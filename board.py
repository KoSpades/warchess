"""The board's own state layer (spec 7.5).

Cells carry effects independent of their occupants. An effect belongs to the side
that placed it and, in general, only works against that side's enemies.

Everything an effect can do is declared here rather than in the turn loop, so a
new hazard is a subclass and nothing else changes:

  * `hidden`      — only the side that placed it may see it (潜水者's bombs)
  * `on_enter`    — it goes off when a unit steps onto the square (a mine)
  * `fuse_round`  — it goes off at the start of that round (a timer)

None of these are special-cased anywhere: the board is asked which effects are
due, the movement rule asks each effect it lands on whether it triggers, and the
view asks the board what a given side is allowed to see.
"""

import damage as DMG
import events as EV


class CellEffect:
    """Something sitting on a square. Subclasses set `kind` and whichever of the
    three triggers they use."""

    kind = "effect"
    hidden = False          # True = only its owner's side is told it is there
    fuse_round = None       # set to a round number to go off at its start
    stacks = 1

    def __init__(self, owner_side):
        self.owner_side = owner_side

    def hostile_to(self, entity):
        """Effects work against the other side. Nothing here has ever harmed the
        side that placed it, and the roster reads that way — a hero walking over
        its own ground is safe."""
        return entity is not None and entity.side != self.owner_side

    @property
    def damage(self):
        return 0

    def describe(self):
        return self.kind

    def payload(self):
        """Extra fields for the client, beyond the ones every effect sends."""
        return {}


class BurningTile(CellEffect):
    """火法师's ground. Permanent, stacks additively, burns anyone but its owner
    who *starts a turn* on it — which is why it has no `on_enter`: walking across
    is free, standing there is not."""

    kind = "burning"

    def __init__(self, owner_side, damage_per_stack=2):
        super().__init__(owner_side)
        self.damage_per_stack = damage_per_stack

    @property
    def damage(self):
        return self.stacks * self.damage_per_stack

    def describe(self):
        return f"Burning x{self.stacks} ({self.damage} fire)"


class SmallBomb(CellEffect):
    """潜水者's 小炸弹. A mine: it goes off under the first enemy to step onto it,
    and is spent doing so. Invisible to the side it is laid against."""

    kind = "small_bomb"
    hidden = True
    DAMAGE = 3

    @property
    def damage(self):
        return self.DAMAGE

    def on_enter(self, match, entity):
        """The damage this deals to whoever just arrived, or None. Returning an
        event rather than applying one keeps every mine in an exchange landing in
        the same instant."""
        if not self.hostile_to(entity):
            return None
        return DMG.DamageEvent(source=None, target=entity, amount=self.DAMAGE,
                               category=DMG.ABILITY)

    def describe(self):
        return f"Small bomb ({self.DAMAGE} to the first enemy that steps on it)"


class BigBomb(CellEffect):
    """潜水者's 大炸弹. A timer: it goes off at the start of the round two after
    the one it was laid in, hitting whichever enemy is standing on it then.
    Invisible to the side it is laid against."""

    kind = "big_bomb"
    hidden = True
    DAMAGE = 6
    FUSE = 2

    def __init__(self, owner_side, laid_round):
        super().__init__(owner_side)
        self.fuse_round = laid_round + self.FUSE

    @property
    def damage(self):
        return self.DAMAGE

    def detonate(self, match, cell):
        """Whatever this catches when the fuse runs out. One square only, so it
        hits at most the single unit standing there."""
        occ = match.occupant(cell)
        if occ is None or not self.hostile_to(occ):
            return []
        return [DMG.DamageEvent(source=None, target=occ, amount=self.DAMAGE,
                                category=DMG.ABILITY)]

    def describe(self):
        return f"Big bomb ({self.DAMAGE}, goes off at the start of round {self.fuse_round})"

    def payload(self):
        return {"fuse_round": self.fuse_round}


class Board:
    def __init__(self, topology):
        self.topology = topology
        self.effects = {}  # cell -> list of effects

    def effects_at(self, cell):
        return self.effects.get(cell, [])

    def add_effect(self, cell, effect):
        self.effects.setdefault(cell, []).append(effect)
        return effect

    def remove_effect(self, cell, effect):
        rest = [e for e in self.effects_at(cell) if e is not effect]
        if rest:
            self.effects[cell] = rest
        else:
            self.effects.pop(cell, None)

    def has_kind(self, cell, kind):
        return any(e.kind == kind for e in self.effects_at(cell))

    def add_burning(self, cell, owner_side, stacks=1):
        for eff in self.effects_at(cell):
            if eff.kind == "burning" and eff.owner_side == owner_side:
                eff.stacks += stacks
                return eff
        tile = BurningTile(owner_side)
        tile.stacks = stacks
        return self.add_effect(cell, tile)

    def burning_damage_for(self, cell, entity):
        """Total fire damage a given entity would take starting its turn here.
        A tile never burns its owner's own side."""
        return sum(eff.damage for eff in self.effects_at(cell)
                   if eff.kind == "burning" and eff.hostile_to(entity))

    def due(self, round_number):
        """(cell, effect) for every timer whose round has come."""
        return [(cell, eff)
                for cell, effs in list(self.effects.items())
                for eff in list(effs)
                if eff.fuse_round is not None and eff.fuse_round <= round_number]

    def serialise(self, side=None):
        """What a given side may see. `side=None` is the whole truth, for tests and
        for anything server-side that is not a player's view."""
        out = []
        for cell, effs in self.effects.items():
            for eff in effs:
                if eff.hidden and side is not None and eff.owner_side != side:
                    continue
                out.append(dict(
                    {
                        "cell": list(cell),
                        "kind": eff.kind,
                        "owner": eff.owner_side,
                        "stacks": eff.stacks,
                        "damage": eff.damage,
                        "hidden": eff.hidden,
                        "text": eff.describe(),
                    },
                    **eff.payload(),
                ))
        return out


class Minefield:
    """Global rule: stepping onto a square sets off whatever is buried in it.

    Hung off AFTER_MOVE rather than the walking code, so *every* kind of movement
    trips a mine — a walk, 半人马's charge, 大力士's throw, 魔术师's swap. The hits
    are banked rather than applied, so a whole exchange's worth land together and
    two units can die on two mines in the same instant."""

    def on_after_move(self, match, ctx):
        e = ctx.get("entity")
        if e is None or not e.alive or not e.cells:
            return
        cell = e.cell
        for eff in list(match.board.effects_at(cell)):
            fn = getattr(eff, "on_enter", None)
            if fn is None:
                continue
            ev = fn(match, e)
            if ev is None:
                continue
            match.board.remove_effect(cell, eff)     # a mine is spent going off
            match.pending_hazards.append(ev)
            match.log_line(
                f"{match.label(e)} steps on something — {eff.describe()}.",
                side=eff.owner_side,
            )
