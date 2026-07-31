"""Attack modes, each declared once.

A hero's `attack` dict names a mode; the mode owns all three things the engine
needs to know about it — how it appears in the action menu, what makes a
committed order legal, and what action instances it resolves into. Adding a new
way of attacking is a class here plus a key in heroes.py, and nothing in the
turn loop changes.

Modes are stateless: every method takes the match and the acting entity, so one
instance per mode is shared by both sides.
"""

import actions as ACT
import heroes as HEROES

MODES = {}


def register(cls):
    MODES[cls.key] = cls()
    return cls


def mode_for(e):
    """The mode a unit attacks with, or None if it cannot attack at all — which
    is also the answer for a unit with no square to attack from."""
    return MODES.get((e.hero.attack or {}).get("mode"))


class AttackMode:
    key = None
    name = "Normal attack"

    def targeting(self, match, e):
        """What the client must collect before the order can be sealed."""
        return {"kind": "none"}

    def menu_entry(self, match, e):
        entry = {"key": "attack", "name": self.name, "ap_cost": 0,
                 "targeting": self.targeting(match, e)}
        self.decorate(match, e, entry)
        return entry

    def decorate(self, match, e, entry):
        """Hook for a mode that needs more than targeting in its menu entry."""

    def validate(self, match, e, dest, action):
        """None if this committed order is legal, else the reason it is not."""
        return None

    def build(self, match, e, dest, action):
        """The action instances this order resolves into, after movement."""
        return [ACT.NullAction()]


@register
class CellLocked(AttackMode):
    """Mark up to `grid` squares within `rng`; one enemy standing in them is hit,
    chosen live once everyone has moved."""

    key = HEROES.CELL

    def targeting(self, match, e):
        return {"kind": "cells", "count": e.grid, "range": e.rng,
                "shots": e.hero.attacks_per_turn}

    def validate(self, match, e, dest, action):
        shots = action.get("shots") or []
        if len(shots) != e.hero.attacks_per_turn:
            return f"Mark cells for all {e.hero.attacks_per_turn} shot(s)."
        for cells in shots:
            # Any number of cells up to the max — a smaller net is the attacker's
            # choice; an empty net simply hits nothing.
            if len(cells) > e.grid:
                return f"At most {e.grid} cells per shot."
            for c in cells:
                c = tuple(c)
                if not match.topology.in_bounds(c):
                    return "Cell off the board."
                if match.topology.distance(dest, c) > e.rng:
                    return "Cell out of range of where you will be standing."
            if len(set(tuple(c) for c in cells)) != len(cells):
                return "Cells must be distinct."
        return None

    def build(self, match, e, dest, action):
        out = []
        for i, cells in enumerate(action["shots"]):
            halve = (e.hero.halve_from_index is not None
                     and i >= e.hero.halve_from_index)
            out.append(ACT.CellLockedAttack(e, cells, dest, halve, i))
        return out


@register
class UnitLocked(AttackMode):
    """Commits to a hero identity: it lands wherever that hero ends up."""

    key = HEROES.UNIT

    def targeting(self, match, e):
        return {"kind": "unit", "range": e.hero.attack.get("range")}

    def validate(self, match, e, dest, action):
        t = match.entity(action.get("target"))
        if t is None or not t.alive or t.side == e.side:
            return "Choose a living enemy."
        return None

    def build(self, match, e, dest, action):
        return [ACT.UnitLockedAttack(e, match.entity(action.get("target")))]


@register
class LineLocked(AttackMode):
    """Fires down one lane of its own row or column at the first enemy in it."""

    key = HEROES.LINE

    def targeting(self, match, e):
        # No precomputed choices: the lane depends on where this hero ends up, so
        # the client scans from the square being aimed at and the commit is
        # validated against the real destination.
        return {"kind": "lane", "dirs": list(match.topology.DIRECTIONS)}

    def validate(self, match, e, dest, action):
        d = action.get("direction")
        if d not in match.topology.DIRECTIONS:
            return "Choose a lane to fire down."
        if ACT.LineShot.scan(match, e, d, dest) is None:
            return "No shot down that lane — nobody there, or your own line is in the way."
        return None

    def build(self, match, e, dest, action):
        return [ACT.LineShot(e, action.get("direction"))]


@register
class AreaLocked(AttackMode):
    """Catches every enemy inside a shape centred on the hero. Nothing to aim."""

    key = HEROES.AREA

    def targeting(self, match, e):
        return {"kind": "area",
                "shape": e.hero.attack.get("shape", "surround8"),
                "cells": [list(c) for c in match.attack_shape(e)]}

    def build(self, match, e, dest, action):
        # Built after movement resolves, so the shape is centred on the square it
        # actually ended up standing in.
        return [ACT.AreaAttack(e, match.attack_shape(e), e.atk)]


@register
class ConeLocked(AttackMode):
    """Aim a direction; the three-cell arc one step that way is sprayed."""

    key = HEROES.CONE

    def targeting(self, match, e):
        # Every direction, with the squares each would cover, so the client can
        # preview an arc before it is chosen.
        return {"kind": "cone",
                "dirs": [{"dir": d,
                          "cells": [list(c) for c in ACT.ConeAttack.cells(match, e, d)]}
                         for d in match.topology.DIRECTIONS]}

    def validate(self, match, e, dest, action):
        d = action.get("direction")
        if d not in match.topology.DIRECTIONS:
            return "Choose a direction to fire in."
        if not ACT.ConeAttack.cells(match, e, d, dest):
            return "That way is off the board."
        return None

    def build(self, match, e, dest, action):
        return [ACT.ConeAttack(e, action.get("direction"))]


@register
class WeaponChoice(AttackMode):
    """武器大师: the attack — and the stance riding with it — is chosen each turn."""

    key = HEROES.WEAPON
    name = "Weapon"

    def targeting(self, match, e):
        return {"kind": "weapon"}

    def decorate(self, match, e, entry):
        entry["weapons"] = e.hero.weapons

    def validate(self, match, e, dest, action):
        return match._validate_weapon(e, dest, action, action.get("key", "none"))

    def build(self, match, e, dest, action):
        return match._build_weapon(e, action, dest)
