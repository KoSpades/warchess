"""Committed actions and their resolution.

An action is not a pure function of the commitment: cell-locked attacks need a
live victim pick after movement resolves (spec 3). So a commitment becomes one
or more ActionInstances, each of which is asked for its eligible victims, then
asked to build damage once a victim is known.
"""

import damage as DMG


class ActionInstance:
    label = "action"
    needs_pick = False
    actor = None

    def is_live(self):
        """A unit killed earlier in the same exchange does not fire its later
        sequential attacks. Its simultaneous ones have already landed. Death is
        the only test — a living unit with no square (鬼魂 before it manifests)
        still acts; its actions simply are not positional."""
        return self.actor is None or self.actor.alive

    def eligible_victims(self, match):
        return []

    def build_damage(self, match, victim):
        return []

    def side_effects(self, match):
        return None


class NullAction(ActionInstance):
    label = "hold"


class CellLockedAttack(ActionInstance):
    """X cells chosen within range Y of the intended post-move position. One
    enemy standing in them takes the damage, chosen live by the attacker."""

    needs_pick = True

    def __init__(self, attacker, cells, intended_origin, halve=False, index=0, amount=None):
        self.attacker = attacker
        self.actor = attacker
        self.committed_cells = [tuple(c) for c in cells]
        self.intended_origin = tuple(intended_origin)
        self.halve = halve
        self.index = index
        self.amount = amount  # None = use the attacker's live atk (weapons pass a fixed value)
        self.label = "shot 2" if index else "attack"

    def resolved_cells(self, match):
        """If the attacker was bounced, the pattern is re-derived from where it
        actually stands: being blocked displaces your shot, it does not void it."""
        actual = self.attacker.cell
        dc = actual[0] - self.intended_origin[0]
        dr = actual[1] - self.intended_origin[1]
        out = []
        for c, r in self.committed_cells:
            cell = (c + dc, r + dr)
            if match.topology.in_bounds(cell):
                out.append(cell)
        return out

    def eligible_victims(self, match):
        cells = set(self.resolved_cells(match))
        return [
            e
            for e in match.living()
            if e.side != self.attacker.side
            and e.flags["targetable"]
            and (e.cells & cells)
        ]

    def build_damage(self, match, victim):
        if victim is None or not victim.alive:
            return []
        tags = {"halve"} if self.halve else set()
        return [
            DMG.DamageEvent(
                source=self.attacker,
                target=victim,
                amount=self.attacker.atk if self.amount is None else self.amount,
                category=DMG.NORMAL_ATTACK,
                tags=tags,
            )
        ]


class LineShot(ActionInstance):
    """狙击手: fires down one lane of its own row or column and hits the first enemy
    in it, for the distance between them plus its attack. An ally in the lane blocks
    the shot. Like every cell-locked attack, the lane is re-scanned from where the
    sniper actually ends up, so being bounced re-aims rather than voids the shot."""

    needs_pick = False
    label = "shot"

    def __init__(self, attacker, direction):
        self.attacker = attacker
        self.actor = attacker
        self.direction = direction

    @staticmethod
    def scan(match, actor, direction, origin=None):
        """(target, distance) down this lane, or None if it cannot be fired: no
        enemy in it, or one of your own standing in the way."""
        step = match.topology.direction_step(actor.side, direction)
        origin = tuple(origin) if origin else actor.cell
        if step is None or not origin:
            return None
        for dist, cell in enumerate(match.topology.ray(origin, step), start=1):
            occ = match.occupant(cell)
            # Its own body doesn't block it: when the lane is scanned from a square
            # it is moving to, the sniper is still standing in the old one, and that
            # square will be empty by the time the shot goes off.
            if occ is None or occ is actor:
                continue
            return None if occ.side == actor.side else (occ, dist)
        return None

    @classmethod
    def lanes(cls, match, actor, origin=None):
        """Every lane that can actually be fired, for offering and validation."""
        out = []
        for d in match.topology.DIRECTIONS:
            hit = cls.scan(match, actor, d, origin)
            if hit:
                target, dist = hit
                out.append({"dir": d, "target": target.id,
                            "distance": dist, "damage": max(0, dist + actor.atk)})
        return out

    def eligible_victims(self, match):
        return None

    def build_damage(self, match, victim):
        hit = self.scan(match, self.attacker, self.direction)
        if hit is None:
            match.log_line(
                f"{match.label(self.attacker)} fires down the lane — nobody there."
            )
            return []
        target, dist = hit
        return [
            DMG.DamageEvent(
                source=self.attacker,
                target=target,
                amount=max(0, dist + self.attacker.atk),
                category=DMG.NORMAL_ATTACK,
            )
        ]


class AreaAttack(ActionInstance):
    """Hits every enemy standing in a fixed set of cells for a flat amount — no
    victim pick (e.g. 武器大师's 长枪 sweeping its row)."""

    needs_pick = False

    def __init__(self, attacker, cells, amount):
        self.attacker = attacker
        self.actor = attacker
        self.cells = set(tuple(c) for c in cells)
        self.amount = amount
        self.label = "attack"

    def eligible_victims(self, match):
        return None

    def build_damage(self, match, victim):
        return [
            DMG.DamageEvent(
                source=self.attacker, target=e, amount=self.amount, category=DMG.NORMAL_ATTACK
            )
            for e in match.living()
            if e.side != self.attacker.side and e.flags["targetable"] and (e.cells & self.cells)
        ]


class UnitLockedAttack(ActionInstance):
    """Commits a hero identity. Lands regardless of where either unit moved."""

    needs_pick = False
    label = "attack"

    def __init__(self, attacker, target, index=0):
        self.attacker = attacker
        self.actor = attacker
        self.target = target
        self.index = index

    def eligible_victims(self, match):
        return None

    def build_damage(self, match, victim):
        if self.target is None or not self.target.alive:
            return []
        return [
            DMG.DamageEvent(
                source=self.attacker,
                target=self.target,
                amount=self.attacker.atk,
                category=DMG.NORMAL_ATTACK,
            )
        ]


class AbilityAction(ActionInstance):
    """Wraps an ability's own resolver. Abilities that hit everything in their
    pattern need no victim pick."""

    def __init__(self, actor, ability, params, index=0):
        self.actor = actor
        self.attacker = actor
        self.ability = ability
        self.params = params or {}
        self.index = index
        self.label = ability.name

    @property
    def needs_pick(self):
        return False

    def eligible_victims(self, match):
        return None

    def build_damage(self, match, victim):
        return self.ability.build_damage(match, self.actor, self.params)

    def side_effects(self, match):
        return self.ability.side_effects(match, self.actor, self.params)
