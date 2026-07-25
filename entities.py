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


@dataclass
class Modifier:
    stat: str
    op: str  # "add" | "set" | "mul"
    value: float
    source: object = None
    duration: str = PERMANENT
    expires_round: int = None


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
        self.ap = 0
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

    def set_cell(self, cell):
        self.cells = {cell}

    def occupies(self, cell):
        return cell in self.cells

    # --- stats ---------------------------------------------------------

    def base_stat(self, name):
        # Attack range lives inside the `attack` dict rather than as a top-level
        # field, but it must flow through the modifier stack like any other stat
        # (马尔斯 raises his range as the enemy thins). None for unit-locked
        # attacks, which have no finite range.
        if name == "rng":
            return self.hero.attack.get("range")
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

    def expire_modifiers(self, when, round_number=None):
        self.modifiers = [
            m
            for m in self.modifiers
            if not (
                m.duration == when
                and (m.expires_round is None or m.expires_round <= round_number)
            )
        ]

    # --- convenience ---------------------------------------------------

    @property
    def atk(self):
        return self.stat("atk")

    @property
    def rng(self):
        return self.stat("rng")

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
