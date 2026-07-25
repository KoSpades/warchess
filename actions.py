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
        sequential attacks. Its simultaneous ones have already landed."""
        return self.actor is None or (self.actor.alive and self.actor.cells)

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

    def __init__(self, attacker, cells, intended_origin, halve=False, index=0):
        self.attacker = attacker
        self.actor = attacker
        self.committed_cells = [tuple(c) for c in cells]
        self.intended_origin = tuple(intended_origin)
        self.halve = halve
        self.index = index
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
                amount=self.attacker.atk,
                category=DMG.NORMAL_ATTACK,
                tags=tags,
            )
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
