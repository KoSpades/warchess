"""Hero definitions as data (spec 7.11).

A hero is a stat block plus ability entries plus passive handler classes. No
hero subclasses Entity, and nothing here is referenced by the core loop.
"""

from dataclasses import dataclass, field

import damage as DMG
import events as EV
from entities import Modifier
from topology import other_side


# ---------------------------------------------------------------- abilities


class Ability:
    key = ""
    name = ""
    ap_cost = 0
    use_limit = None  # None = unlimited; an int caps total uses per match
    opening = False   # True = fires once at game start (the opening phase), not on a turn
    targeting = {"kind": "none"}
    blurb = ""

    def build_damage(self, match, actor, params):
        return []

    def side_effects(self, match, actor, params):
        return None


class Sweep(Ability):
    key = "sweep"
    name = "横扫 Sweep"
    ap_cost = 2
    targeting = {"kind": "direction", "options": ["forward", "backward"]}
    blurb = "Own column plus one adjacent column, forward or back. 4 damage to every enemy inside."

    @staticmethod
    def block(match, actor, direction):
        col = actor.cell[0]
        step = match.topology.forward_step(actor.side)
        if direction == "backward":
            step = -step
        cells = list(match.topology.column(col))
        cells += list(match.topology.column(col + step))
        return cells

    def build_damage(self, match, actor, params):
        cells = set(self.block(match, actor, params.get("direction", "forward")))
        out = []
        for e in match.living():
            if e.side != actor.side and (e.cells & cells):
                out.append(
                    DMG.DamageEvent(
                        source=actor, target=e, amount=4, category=DMG.ABILITY
                    )
                )
        return out


class Ray(Ability):
    key = "ray"
    name = "射线 Ray"
    ap_cost = 2
    targeting = {"kind": "none"}
    blurb = "5 damage to every enemy in the caster's row. Travels with it if bounced."

    def build_damage(self, match, actor, params):
        cells = set(match.topology.row(actor.cell[1]))
        return [
            DMG.DamageEvent(source=actor, target=e, amount=5, category=DMG.ABILITY)
            for e in match.living()
            if e.side != actor.side and (e.cells & cells)
        ]


class Inspire(Ability):
    key = "inspire"
    name = "鼓舞 Inspire"
    ap_cost = 3
    targeting = {"kind": "none"}
    blurb = "Every ally gains +1 attack, permanently. Casts stack."

    def side_effects(self, match, actor, params):
        for e in match.living():
            if e.side == actor.side:
                e.add_modifier(Modifier("atk", "add", 1))
        match.log_line(f"{match.label(actor)} inspires the line — +1 attack to all allies.")


class Incite(Ability):
    key = "incite"
    name = "激励 Incite"
    ap_cost = 3
    use_limit = 1
    targeting = {"kind": "none"}
    blurb = "Once per match: every ally gains +1 movement, permanently."

    def side_effects(self, match, actor, params):
        for e in match.living():
            if e.side == actor.side:
                e.add_modifier(Modifier("move", "add", 1))
        match.log_line(f"{match.label(actor)} rallies the line — +1 movement to all allies.")


class Pierce(Ability):
    key = "pierce"
    name = "穿刺 Pierce"
    ap_cost = 3
    targeting = {"kind": "unit"}
    blurb = "8 damage to one chosen enemy, anywhere. Element: wood."

    def build_damage(self, match, actor, params):
        tgt = match.entity(params.get("target"))
        if tgt is None or not tgt.alive or tgt.side == actor.side:
            return []
        return [
            DMG.DamageEvent(
                source=actor, target=tgt, amount=8, category=DMG.ABILITY, element=DMG.WOOD
            )
        ]


class Thunderstorm(Ability):
    key = "thunderstorm"
    name = "雷暴 Thunderstorm"
    ap_cost = 3
    targeting = {"kind": "none"}
    blurb = "3 damage to every living enemy. No targeting, no counterplay."

    def build_damage(self, match, actor, params):
        return [
            DMG.DamageEvent(
                source=actor,
                target=e,
                amount=3,
                category=DMG.ABILITY,
                element=DMG.THUNDER,
            )
            for e in match.living()
            if e.side != actor.side
        ]


class Ignite(Ability):
    key = "ignite"
    name = "点燃 Ignite"
    ap_cost = 1
    targeting = {"kind": "any_cell"}
    blurb = "Permanently sets one cell alight, anywhere. Enemies starting a turn there take 2 fire. Stacks."

    def side_effects(self, match, actor, params):
        cell = tuple(params["cell"])
        tile = match.board.add_burning(cell, actor.side)
        match.log_line(
            f"{match.label(actor)} ignites {match.cell_name(cell)} "
            f"(now x{tile.stacks}, {tile.damage} fire)."
        )


class Heal(Ability):
    key = "heal"
    name = "治疗 Heal"
    ap_cost = 2
    targeting = {"kind": "ally"}
    blurb = "Restore 6 HP to one ally — herself included."

    def side_effects(self, match, actor, params):
        tgt = match.entity(params.get("target"))
        if tgt is None or not tgt.alive or tgt.side != actor.side:
            return
        healed = DMG.heal(match, tgt, 6, source=actor)
        if healed:
            match.log_line(f"{match.label(actor)} heals {match.label(tgt)} for {healed}.")


class BloodRite(Ability):
    key = "blood_rite"
    name = "血祭 Blood Rite"
    ap_cost = 1
    use_limit = 1
    targeting = {"kind": "magnitude"}
    blurb = "Once per match: sacrifice up to your current HP in max HP for that much permanent attack."

    def side_effects(self, match, actor, params):
        # Never enough to self-kill: leave at least 1 max HP; current HP clamps
        # down to the new max.
        x = max(1, min(int(params.get("amount") or 0), actor.hp, actor.max_hp - 1))
        actor.max_hp -= x
        if actor.hp > actor.max_hp:
            actor.hp = actor.max_hp
        actor.add_modifier(Modifier("atk", "add", x))
        match.log_line(
            f"{match.label(actor)} sacrifices {x} max HP for +{x} attack "
            f"(now {actor.atk} atk, {actor.hp}/{actor.max_hp} HP)."
        )


class AncientGuard(Ability):
    key = "ancient_guard"
    name = "远古守护 Ancient Guard"
    ap_cost = 0
    use_limit = 1
    opening = True
    targeting = {"kind": "ally"}
    blurb = "At game start, grant one ally (yourself included) a permanent −1 to all damage taken."

    def side_effects(self, match, actor, params):
        tgt = match.entity(params.get("target"))
        if tgt is None or not tgt.alive or tgt.side != actor.side:
            return
        tgt.vars["damage_reduction"] = tgt.vars.get("damage_reduction", 0) + 1
        match.log_line(f"{match.label(actor)} guards {match.label(tgt)} — −1 to all damage taken.")


# ---------------------------------------------------------------- passives


class RockSkin:
    """First damage of the round from an attack or ability lands in full; every
    later instance of those categories is blocked for the rest of the round.
    Tile damage neither triggers it nor is blocked by it."""

    TRIGGERS = (DMG.NORMAL_ATTACK, DMG.ABILITY)
    describe = "After taking attack or ability damage, immune to all further attack and ability damage this round."

    @EV.hook(priority=30)
    def on_before_damage(self, match, owner, ev):
        if ev.target is not owner or ev.cancelled:
            return
        if ev.category not in self.TRIGGERS:
            return
        if owner.vars.get("rockskin_spent"):
            ev.cancel("stone immunity")

    @EV.hook(priority=60)
    def on_after_damage(self, match, owner, ev):
        if ev.target is owner and ev.category in self.TRIGGERS:
            if not owner.vars.get("rockskin_spent"):
                owner.vars["rockskin_spent"] = True
                match.log_line(f"{match.label(owner)} hardens — immune for the rest of round {match.round}.")

    def on_round_start(self, match, owner, ctx):
        owner.vars["rockskin_spent"] = False


class SelfRepair:
    describe = "Heals 4 at the end of its own turn."

    def on_turn_end(self, match, owner, ctx):
        if ctx.get("entity") is owner and owner.alive:
            healed = DMG.heal(match, owner, 4, source=owner)
            if healed:
                match.log_line(f"{match.label(owner)} self-repairs {healed}.")


class KeenEdge:
    describe = "At the end of its own turn, Atk +2 (up to a maximum of 10)."
    CAP = 10

    def on_turn_end(self, match, owner, ctx):
        if ctx.get("entity") is not owner or not owner.alive:
            return
        gain = min(2, self.CAP - owner.atk)
        if gain <= 0:
            return
        owner.add_modifier(Modifier("atk", "add", gain))
        match.log_line(f"{match.label(owner)} sharpens — Atk now {owner.atk}.")


class Regrowth:
    describe = "At the start of its own turn, every ally recovers 1 HP."

    def on_turn_start(self, match, owner, ctx):
        if ctx.get("entity") is not owner or not owner.alive:
            return
        total = sum(
            DMG.heal(match, e, 1, source=owner)
            for e in match.living() if e.side == owner.side
        )
        if total:
            match.log_line(f"{match.label(owner)}'s grove restores the line (+1 to each ally).")


class BattleFury:
    """Below a health threshold, hits harder and reaches further. Conditional on
    current HP, so it is recomputed (spec 7.3) whenever HP can change."""

    describe = "While at 11 HP or below, Atk +2 and Rng +1."
    THRESHOLD = 11

    def _recompute(self, match, owner):
        owner.modifiers = [m for m in owner.modifiers if m.source is not self]
        if owner.alive and owner.hp <= self.THRESHOLD:
            owner.add_modifier(Modifier("atk", "add", 2, source=self))
            owner.add_modifier(Modifier("rng", "add", 1, source=self))

    def on_after_damage(self, match, owner, ev):
        self._recompute(match, owner)

    def on_heal(self, match, owner, ctx):
        self._recompute(match, owner)

    def on_round_start(self, match, owner, ctx):
        self._recompute(match, owner)

    def on_turn_start(self, match, owner, ctx):
        self._recompute(match, owner)

    def on_match_start(self, match, owner, ctx):
        self._recompute(match, owner)


class TwinGuns:
    describe = "Two normal attacks per turn, resolved in sequence. The second deals half damage, rounded down."


class Warlord:
    """马尔斯 grows into the endgame. Once the enemy has lost a hero his reach
    extends by 1; once they are down to their last hero his attack gains 1.

    The bonuses are conditional on the enemy count, so rather than a static
    modifier they are recomputed (spec 7.3) whenever that count can change — a
    death, or a round boundary. Enemy count only ever changes on a death in the
    current roster, but recomputing on round_start/match_start too keeps it
    correct if a future design revives or spawns units."""

    describe = "Rng +1 once an enemy hero is destroyed; Atk +1 while only one enemy remains."

    def _recompute(self, match, owner):
        if not owner.alive:
            return
        foe = other_side(owner.side)
        living = [e for e in match.living(foe) if e.flags["counts_for_defeat"]]
        deployed = [e for e in match.entities
                    if e.side == foe and e.flags["counts_for_defeat"]]
        owner.modifiers = [m for m in owner.modifiers if m.source is not self]
        if len(living) < len(deployed):
            owner.add_modifier(Modifier("rng", "add", 1, source=self))
        if len(living) == 1:
            owner.add_modifier(Modifier("atk", "add", 1, source=self))

    def on_match_start(self, match, owner, ctx):
        self._recompute(match, owner)

    def on_round_start(self, match, owner, ctx):
        self._recompute(match, owner)

    def on_death(self, match, owner, ctx):
        self._recompute(match, owner)


class MountainGuard:
    """山神 shelters his line. Allied units sharing his column take no attack or
    ability damage; he himself is not covered, and tile damage (fire) burns
    through — mirroring how 岩石巨人's immunity leaves tiles alone."""

    TRIGGERS = (DMG.NORMAL_ATTACK, DMG.ABILITY)
    describe = "Allied units in his column take no attack or ability damage. He is not covered; fire still burns."

    @EV.hook(priority=20)
    def on_before_damage(self, match, owner, ev):
        if ev.cancelled or ev.category not in self.TRIGGERS:
            return
        tgt = ev.target
        if tgt is owner or tgt.side != owner.side:
            return
        if not owner.alive or not owner.cells or not tgt.cells:
            return
        column = set(match.topology.column(owner.cell[0]))
        if tgt.cells & column:
            ev.cancel("shielded by 山神")


# ---------------------------------------------------------------- hero defs


@dataclass
class HeroDef:
    key: str
    name: str
    name_en: str
    max_hp: int
    atk: int
    move: int
    max_ap: int
    attack: dict
    attacks_per_turn: int = 1
    halve_from_index: int = None
    abilities: list = field(default_factory=list)
    passives: list = field(default_factory=list)
    weapons: list = None  # 武器大师 only: per-turn choosable attacks (see WEAPONS)
    blurb: str = ""


CELL = "cell_locked"
UNIT = "unit_locked"
WEAPON = "weapon"  # 武器大师: the attack is chosen each turn from `weapons`

# 武器大师's arsenal. `mode` drives targeting: "cells" = mark cells within range
# (single victim); "surround8" = the 8 cells around you (single victim); "row" =
# every enemy in your row. `buff` grants a stance until the master's next turn.
WEAPONS = [
    {"key": "sword_shield", "name": "剑盾 Sword & Shield", "atk": 2, "mode": "cells",
     "cells": 3, "range": 2, "buff": "guard",
     "text": "3 cells @2, one target. +2 damage reduction until your next turn."},
    {"key": "odachi", "name": "太刀 Odachi", "atk": 2, "mode": "cells",
     "cells": 4, "range": 2, "buff": "ward",
     "text": "4 cells @2, one target. Immune to enemy abilities until your next turn."},
    {"key": "spear", "name": "长枪 Spear", "atk": 3, "mode": "row",
     "text": "3 damage to every enemy in your row."},
    {"key": "hammer", "name": "大锤 Warhammer", "atk": 5, "mode": "surround8",
     "text": "5 damage to one enemy among the 8 cells around you."},
    {"key": "bow", "name": "弓箭 Bow", "atk": 3, "mode": "cells",
     "cells": 5, "range": 8, "text": "5 cells @8, one target."},
]
WEAPONS_BY_KEY = {w["key"]: w for w in WEAPONS}


class WeaponMaster:
    describe = "Each turn, choose a weapon: it sets that turn's attack and stance."

    def on_turn_start(self, match, owner, ctx):
        # A new turn — last turn's stance expires "before this turn begins".
        if ctx.get("entity") is owner:
            owner.vars["stance_dr"] = 0
            owner.vars["ability_immune"] = False

ROSTER = [
    HeroDef(
        key="spearman",
        name="枪兵",
        name_en="Spearman",
        max_hp=19,
        atk=4,
        move=1,
        max_ap=3,
        attack={"mode": CELL, "cells": 3, "range": 2},
        abilities=[Sweep()],
        blurb="A wall of reach. Sweep hits a 2x5 block and cannot miss.",
    ),
    HeroDef(
        key="rock_giant",
        name="岩石巨人",
        name_en="Rock Giant",
        max_hp=26,
        atk=2,
        move=1,
        max_ap=0,
        attack={"mode": CELL, "cells": 3, "range": 2},
        passives=[RockSkin],
        blurb="Takes one hit per round and shrugs off the rest.",
    ),
    HeroDef(
        key="robot",
        name="机器人",
        name_en="Robot",
        max_hp=22,
        atk=2,
        move=1,
        max_ap=0,
        attack={"mode": CELL, "cells": 3, "range": 2},
        passives=[SelfRepair],
        blurb="Grinds down anything that cannot out-damage 4 a round.",
    ),
    HeroDef(
        key="thunder_dragon",
        name="雷霆龙",
        name_en="Thunder Dragon",
        max_hp=17,
        atk=1,
        move=1,
        max_ap=6,
        attack={"mode": UNIT, "range": None},
        abilities=[Thunderstorm()],
        blurb="Reaches the whole board. Banks two storms.",
    ),
    HeroDef(
        key="fire_mage",
        name="火法师",
        name_en="Fire Mage",
        max_hp=17,
        atk=2,
        move=1,
        max_ap=3,
        attack={"mode": CELL, "cells": 4, "range": 8},
        abilities=[Ignite()],
        blurb="Permanently poisons ground. The board only gets worse.",
    ),
    HeroDef(
        key="gunslinger",
        name="双枪手",
        name_en="Gunslinger",
        max_hp=16,
        atk=4,
        move=1,
        max_ap=0,
        attack={"mode": CELL, "cells": 4, "range": 7},
        attacks_per_turn=2,
        halve_from_index=1,
        passives=[TwinGuns],
        blurb="Two nets a turn. The second shot is half strength.",
    ),
    HeroDef(
        key="mars",
        name="马尔斯",
        name_en="Mars",
        max_hp=18,
        atk=4,
        move=1,
        max_ap=0,
        attack={"mode": CELL, "cells": 3, "range": 2},
        passives=[Warlord],
        blurb="Sharpens as the enemy thins — longer reach, then a harder hit.",
    ),
    HeroDef(
        key="cannoneer",
        name="炮手",
        name_en="Cannoneer",
        max_hp=16,
        atk=3,
        move=1,
        max_ap=0,
        attack={"mode": CELL, "cells": 4, "range": 7},
        blurb="A wide net from the back line. One long-range shot a turn.",
    ),
    HeroDef(
        key="mountain_god",
        name="山神",
        name_en="Mountain God",
        max_hp=25,
        atk=2,
        move=1,
        max_ap=0,
        attack={"mode": CELL, "cells": 3, "range": 2},
        passives=[MountainGuard],
        blurb="Allies in his column cannot be struck by attacks or abilities.",
    ),
    HeroDef(
        key="tide_goddess",
        name="潮汐女神",
        name_en="Tide Goddess",
        max_hp=14,
        atk=2,
        move=1,
        max_ap=4,
        attack={"mode": CELL, "cells": 3, "range": 3},
        abilities=[Heal()],
        blurb="Keeps the line standing — a 6-point heal every other turn.",
    ),
    HeroDef(
        key="blood_mage",
        name="血魔法师",
        name_en="Blood Mage",
        max_hp=20,
        atk=4,
        move=1,
        max_ap=1,
        attack={"mode": CELL, "cells": 3, "range": 2},
        abilities=[BloodRite()],
        blurb="Once, trades life for lasting power.",
    ),
    HeroDef(
        key="forest_child",
        name="森林之子",
        name_en="Forest Child",
        max_hp=18,
        atk=4,
        move=1,
        max_ap=0,
        attack={"mode": CELL, "cells": 3, "range": 3},
        abilities=[AncientGuard()],
        blurb="Blesses one ally with lasting protection before the first move.",
    ),
    HeroDef(
        key="imp",
        name="小鬼",
        name_en="Imp",
        max_hp=16,
        atk=1,
        move=1,
        max_ap=3,
        attack={"mode": CELL, "cells": 4, "range": 4},
        abilities=[Ray()],
        blurb="Sears its entire row for 5 — no aim, no escape sideways.",
    ),
    HeroDef(
        key="woodcutter",
        name="樵夫",
        name_en="Woodcutter",
        max_hp=20,
        atk=2,
        move=1,
        max_ap=0,
        attack={"mode": CELL, "cells": 2, "range": 2},
        passives=[KeenEdge],
        blurb="Hits harder every turn it survives — +2 attack, up to 10.",
    ),
    HeroDef(
        key="victory_goddess",
        name="胜利女神",
        name_en="Victory Goddess",
        max_hp=14,
        atk=1,
        move=1,
        max_ap=6,
        attack={"mode": CELL, "cells": 3, "range": 5},
        abilities=[Inspire(), Incite()],
        blurb="Lifts the whole army — sharper blades, and once, longer strides.",
    ),
    HeroDef(
        key="druid",
        name="德鲁伊",
        name_en="Druid",
        max_hp=17,
        atk=1,
        move=1,
        max_ap=6,
        attack={"mode": CELL, "cells": 3, "range": 5},
        abilities=[Pierce()],
        blurb="A single wooden spike — 8 damage to any one enemy.",
    ),
    HeroDef(
        key="weapon_master",
        name="武器大师",
        name_en="Weapon Master",
        max_hp=23,
        atk=2,
        move=1,
        max_ap=0,
        attack={"mode": WEAPON},
        passives=[WeaponMaster],
        weapons=WEAPONS,
        blurb="Swaps weapons every turn — five attacks, two stances.",
    ),
    HeroDef(
        key="berserker",
        name="狂战士",
        name_en="Berserker",
        max_hp=22,
        atk=4,
        move=1,
        max_ap=0,
        attack={"mode": CELL, "cells": 2, "range": 2},
        passives=[BattleFury],
        blurb="Wounded and dangerous — +2 attack and +1 range at 11 HP or below.",
    ),
    HeroDef(
        key="tree_spirit",
        name="树灵",
        name_en="Tree Spirit",
        max_hp=14,
        atk=1,
        move=1,
        max_ap=0,
        attack={"mode": CELL, "cells": 3, "range": 5},
        passives=[Regrowth],
        blurb="Regrows the army — every ally recovers 1 HP at the start of its turn.",
    ),
    HeroDef(
        key="gatekeeper",
        name="门神",
        name_en="Gatekeeper",
        max_hp=32,
        atk=3,
        move=1,
        max_ap=0,
        attack={"mode": CELL, "cells": 2, "range": 2},
        blurb="A wall of a hero — 32 HP and a solid swing.",
    ),
    HeroDef(
        key="wind_rider",
        name="御风使",
        name_en="Wind Rider",
        max_hp=20,
        atk=4,
        move=2,
        max_ap=0,
        attack={"mode": CELL, "cells": 3, "range": 2},
        blurb="Covers ground fast — moves 2 cells a turn.",
    ),
]

BY_KEY = {h.key: h for h in ROSTER}

# A punching-bag for --test mode: one-enemy attack (you pick the target), no
# ability. Not in ROSTER, so it can never be drafted in a real game.
DUMMY = HeroDef(
    key="dummy",
    name="木桩",
    name_en="Dummy",
    max_hp=10,
    atk=2,
    move=1,
    max_ap=0,
    attack={"mode": UNIT, "range": None},
    blurb="A training dummy — strikes one chosen enemy for 2.",
)
BY_KEY[DUMMY.key] = DUMMY

# The champions --test puts under your control (the current batch). Update this
# whenever you add heroes; --test fills the rest of your side with dummies.
TEST_HEROES = ["wind_rider"]


def describe(hero):
    lines = []
    for ab in hero.abilities:
        lines.append(
            {
                "kind": "active",
                "name": ab.name,
                "ap_cost": ab.ap_cost,
                "text": ab.blurb,
            }
        )
    for p in hero.passives:
        lines.append(
            {"kind": "passive", "name": "Passive", "ap_cost": 0, "text": p.describe}
        )
    return lines
