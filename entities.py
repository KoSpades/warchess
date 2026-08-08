"""Entities and the modifier stack (spec 7.3, 7.7).

No code reads a raw stat field. Everything goes through `stat()` so that a
passive granting +1 move, terrain slowing a unit, and a temporary debuff are all
the same mechanism. None of the first six heroes uses a modifier, so the stack
is currently scaffolding — but it is the scaffolding the roster will need first.
"""

from dataclasses import dataclass, field

PERMANENT = "permanent"
UNTIL_ROUND_END = "until_round_end"
UNTIL_OWNER_NEXT_TURN = "until_owner_next_turn"
# Expires when the turn it was granted in ends — 哥布林鼓舞's "本回合". For a gang
# turn that means the whole gang's turn, since they share one.
UNTIL_TURN_END = "until_turn_end"


@dataclass
class Modifier:
    stat: str
    op: str  # "add" | "set" | "mul"
    value: float
    source: object = None
    duration: str = PERMANENT


class Entity:
    """Heroes, summons, and tokens are all Entities. The flags keep variations
    (units that never take turns, occupy cells without blocking them, or do not
    count toward defeat) out of the core loop."""

    def __init__(self, eid, hero_def, side, cell):
        self.id = eid
        self.hero = hero_def
        self.key = hero_def.key
        self.name = hero_def.name
        self.name_en = hero_def.name_en
        self.side = side  # mutable: units can change allegiance
        self.cells = {cell} if cell else set()

        self.max_hp = hero_def.max_hp
        self.hp = hero_def.max_hp
        # Almost every hero opens the match empty and charges one as its first turn
        # begins; a card may start with some already banked (长老).
        self.ap = min(hero_def.max_ap, getattr(hero_def, "start_ap", 0))
        self.modifiers = []
        self.alive = True
        self.has_acted = False
        self.state_tag = "base"
        self.vars = {}  # per-hero scratch state (e.g. immunity flags)

        self.flags = {
            "takes_turns": True,
            "blocks_movement": True,
            "counts_for_defeat": True,
            "targetable": True,
        }
        self.passives = [p() for p in hero_def.passives]
        self.abilities = list(hero_def.abilities)

    # --- position ------------------------------------------------------
    # Positions are sets from day one so multi-cell units need a change to the
    # movement rules only, not to every call site (spec 7.7).

    @property
    def cell(self):
        """None once the unit is off the board. Callers that can run after a
        death must check `alive` rather than assume a position exists."""
        return next(iter(self.cells), None)

    def acts_from(self, origin=None):
        """The square this unit will be standing on when whatever it committed to
        goes off: `origin` where the caller knows the destination, otherwise where
        it stands now, and None for a unit with no body at all. Movement resolves
        before abilities do, so anything positional has to ask this rather than
        read `cell` — five places used to spell the same fallback out."""
        return tuple(origin) if origin else self.cell

    def set_cell(self, cell):
        self.cells = {cell}

    def occupies(self, cell):
        return cell in self.cells

    # --- stats ---------------------------------------------------------

    @property
    def attack_spec(self):
        """What this unit attacks with: a weapon it has been handed (军火商人's
        arsenal) if it holds one, otherwise its own. Everything that asks about a
        hero's attack — its mode, its reach, its net — reads this."""
        arms = self.vars.get("arms")
        if arms is not None:
            return arms.get("attack") or {}
        return self.hero.attack or {}

    def base_stat(self, name):
        # Attack range lives inside the `attack` dict rather than as a top-level
        # field, but it must flow through the modifier stack like any other stat
        # (马尔斯 raises his range as the enemy thins). None for unit-locked
        # attacks, which have no finite range.
        spec = self.attack_spec
        arms = self.vars.get("arms")
        if name == "atk" and arms and not arms.get("spent"):
            # A weapon carries its own number, and the hero's buffs still ride on it.
            # A spent one carries nothing — the hero reads as its own again.
            return arms.get("atk", 0)
        if name == "rng":
            return spec.get("range")
        if name == "targets":
            # How many enemies one cell-locked attack may hit out of those standing
            # in its net. Same story as rng and grid: it lives in the `attack` dict
            # but must ride the modifier stack (猎人 widens it on its first kill).
            return spec.get("targets", 1)
        if name == "grid":
            # How many cells a cell-locked attack may mark. Same story as rng:
            # it lives in the `attack` dict but must be modifiable (狼人 trades
            # grids for raw attack when it turns). None for other attack modes.
            return spec.get("cells")
        return getattr(self.hero, name, 0)

    def stat(self, name):
        value = self.base_stat(name)
        if value is None:
            return None
        for m in self.modifiers:
            if m.stat != name:
                continue
            if m.op == "add":
                value += m.value
            elif m.op == "mul":
                value *= m.value
            elif m.op == "set":
                value = m.value
        return int(value)

    def add_modifier(self, mod):
        self.modifiers.append(mod)

    def expire_modifiers(self, when):
        self.modifiers = [m for m in self.modifiers if m.duration != when]

    # --- convenience ---------------------------------------------------

    def worn_down(self, name):
        """A stat after everything stacked on it, but never worn below its floor.

        Effects may blunt a hero; they may not take it off the board. Reach and
        attack are pushed down by several things at once (大雾 then 野兽化, 画师's
        brush, 鬼魂's haunt) and a hero left at 0 could never threaten anything
        again — a worse outcome than any one debuff intended.

        The floor is 1, or the hero's own base where that is deliberately lower:
        狙击手 attacks at 0 by design, its shot being measured by distance instead,
        and nothing here should quietly hand it a point it was never given.

        `None` is not a small number — it means the stat does not apply at all, as
        for a unit-locked attack with no finite reach — so it passes straight
        through."""
        value = self.stat(name)
        if value is None:
            return None
        base = self.base_stat(name)
        return max(min(1, 1 if base is None else base), value)

    @property
    def atk(self):
        return self.worn_down("atk")

    @property
    def rng(self):
        return self.worn_down("rng")

    @property
    def grid(self):
        return self.worn_down("grid")

    @property
    def targets(self):
        return self.worn_down("targets") or 1

    @property
    def move_allowance(self):
        return self.stat("move")

    @property
    def max_ap(self):
        return self.stat("max_ap")

    def gain_ap(self, n):
        self.ap = max(0, min(self.max_ap, self.ap + n))

    def __repr__(self):
        return f"<{self.name_en} {self.side}#{self.id} hp={self.hp}>"
