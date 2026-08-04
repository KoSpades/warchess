"""Hero definitions as data (spec 7.11).

A hero is a stat block plus ability entries plus passive handler classes. No
hero subclasses Entity, and nothing here is referenced by the core loop.
"""

from dataclasses import dataclass, field

import board as BOARD
import damage as DMG
import events as EV
from entities import (Modifier, UNTIL_OWNER_NEXT_TURN, UNTIL_ROUND_END,
                      UNTIL_TURN_END)
from topology import LEFT, other_side


# ---------------------------------------------------------------- abilities


class Ability:
    key = ""
    name = ""
    ap_cost = 0
    use_limit = None  # None = unlimited; an int caps total uses per match
    opening = False   # True = fires once at game start (the opening phase), not on a turn
    # True = the ability carries the hero itself (半人马's 冲撞, 刺客's 封喉). A hero
    # whose feet are stopped cannot use one.
    carries_self = False
    # True = it carries the hero *instead of* its ordinary move, so the commit must
    # hold position. Implies `carries_self`; an ability that merely moves you as
    # well as your walk sets only that.
    self_move = False
    targeting = {"kind": "none"}
    blurb = ""

    def build_damage(self, match, actor, params):
        return []

    def side_effects(self, match, actor, params):
        return None

    def validate(self, match, actor, params, origin=None):
        """Legality beyond the generic targeting checks — e.g. whether a chosen
        direction can actually be charged. None means fine.

        `origin` is the square the hero will be standing on when this resolves,
        which is not where it is standing now: movement is applied first. Anything
        whose legality depends on position must measure from there, or it will
        refuse orders that turn out to be legal and allow ones that do not."""
        return None

    def available(self, match, actor):
        """False hides the ability from the menu entirely — for one that unlocks
        partway through a match, or that a hero can trade away."""
        return True

    def magnitude_cap(self, actor):
        """Largest amount a `magnitude` ability accepts. The cost is the ability's
        business, not the validator's — 0 means it cannot be used at all."""
        return max(0, actor.hp - 1)


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
        cells = self.block(match, actor, params.get("direction", "forward"))
        return [
            DMG.DamageEvent(source=actor, target=e, amount=4, category=DMG.ABILITY)
            for e in match.enemies_in(cells, actor.side)
        ]


class Charge(Ability):
    key = "charge"
    name = "冲撞 Charge"
    ap_cost = 2
    carries_self = True          # but not instead of its walk: it does both
    targeting = {"kind": "direction", "options": ["forward", "backward", "up", "down"]}
    blurb = ("Charge 3 squares down one lane, dealing 3 to each enemy in the two squares "
             "crossed on the way (at most two). If the third square is taken or off the "
             "board it still tramples, but holds its ground. You may move first — the "
             "run starts from wherever your move ends.")
    DAMAGE = 3
    DISTANCE = 3

    @classmethod
    def path(cls, match, actor, direction, origin=None):
        """(landing cell or None when it can't land, [enemies in the crossed squares]).
        The run is always exactly DISTANCE squares: the ones in between are trampled,
        the last one is where it ends up — if anybody is standing there, or it is off
        the board, the charge still lands its damage but the centaur doesn't move.
        Whole thing is None only if the direction itself is nonsense.

        Run from `origin` — where the centaur will be standing when it puts its head
        down, which is the end of its walk, not the start."""
        d = match.topology.direction_step(actor.side, direction)
        origin = tuple(origin) if origin else (actor.cell if actor.cells else None)
        if d is None or origin is None:
            return None
        c0, r0 = origin
        lane = [(c0 + d[0] * i, r0 + d[1] * i) for i in range(1, cls.DISTANCE + 1)]
        victims = []
        for cell in lane[:-1]:
            if not match.topology.in_bounds(cell):
                continue
            if not match.topology.same_region(cell, origin):
                continue          # a sub-map in the lane is simply run over
            occ = match.occupant(cell)
            if occ is not None and occ.side != actor.side and occ.flags["targetable"]:
                victims.append(occ)   # allies, and things that cannot be touched,
                                      # are simply ridden past
        end = lane[-1]
        blocked = (not match.topology.same_region(end, origin)
                   or not match.can_enter(actor, end))
        return (None if blocked else end), victims

    def lanes(self, match, actor, origin=None):
        """Every lane worth charging, for the client to offer and preview. A lane
        that would neither move nor trample anyone is left out."""
        out = []
        for d in self.targeting["options"]:
            p = self.path(match, actor, d, origin)
            if p is None:
                continue
            landing, victims = p
            if landing is None and not victims:
                continue   # nowhere to go and nobody to hit
            n = len(victims)
            out.append({"dir": d,
                        "landing": list(landing) if landing else None,
                        # The charge carries the charger, so it is the one that
                        # ends up on the landing square.
                        "mover": actor.id,
                        "victims": [v.id for v in victims],
                        "damage": self.DAMAGE,
                        "where": "charges through" if landing else "holds ground",
                        "label": (f"tramples {n} {'enemy' if n == 1 else 'enemies'} "
                                  f"for {self.DAMAGE} each" if n else "nobody in the way")
                        + ("" if landing else " · the third square is taken")})
        return out

    def validate(self, match, actor, params, origin=None):
        p = self.path(match, actor, params.get("direction"), origin)
        if p is None:
            return "Choose a direction."
        if p[0] is None and not p[1]:
            return "Nothing to trample that way, and nowhere to land."
        return None

    def build_damage(self, match, actor, params):
        p = self.path(match, actor, params.get("direction"))
        if p is None:
            actor.vars.pop("charge_plan", None)
            return []
        landing, victims = p
        # Stashed on the actor, never on self: one Ability instance is shared by
        # every entity of that hero, on both sides.
        actor.vars["charge_plan"] = {"landing": landing}
        return [
            DMG.DamageEvent(source=actor, target=v, amount=self.DAMAGE, category=DMG.ABILITY)
            for v in victims
        ]

    def side_effects(self, match, actor, params):
        plan = actor.vars.pop("charge_plan", None)
        if plan is None or not actor.alive:
            return
        landing = plan["landing"]
        if landing is None:
            match.log_line(f"{match.label(actor)} tramples through but holds its ground.")
            return
        frm = actor.cell
        actor.set_cell(landing)
        match.bus.emit(EV.AFTER_MOVE, {"entity": actor, "from": frm, "to": landing})
        match.log_line(
            f"{match.label(actor)} charges through the line."
        )


class Possess(Ability):
    """鬼魂's haunt. Pure debuff plumbing: 2 damage, then two ordinary modifiers on
    the victim that expire when the ghost's own next turn begins."""

    key = "possess"
    name = "附身 Possess"
    ap_cost = 0
    targeting = {"kind": "unit"}
    blurb = ("Sink into one enemy: 2 damage, and until your next turn everything it "
             "deals is 1 weaker and its reach is 1 shorter.")
    DAMAGE = 2
    RNG_FLOOR = 1

    def available(self, match, actor):
        return not actor.vars.get("manifested")

    def build_damage(self, match, actor, params):
        tgt = match.entity(params.get("target"))
        if tgt is None or not tgt.alive or tgt.side == actor.side:
            return []
        return [DMG.DamageEvent(source=actor, target=tgt, amount=self.DAMAGE,
                                category=DMG.ABILITY)]

    def side_effects(self, match, actor, params):
        tgt = match.entity(params.get("target"))
        if tgt is None or not tgt.alive or tgt.side == actor.side:
            return
        actor.vars["haunting"] = tgt.id
        tgt.add_modifier(Modifier("damage_dealt", "add", -1, source=actor,
                                  duration=UNTIL_OWNER_NEXT_TURN))
        # A whole-board attacker has no reach to shorten, and nobody is pushed
        # below the floor — the same rule 大雾 follows.
        if tgt.rng is not None and tgt.rng > self.RNG_FLOOR:
            tgt.add_modifier(Modifier("rng", "add", -1, source=actor,
                                      duration=UNTIL_OWNER_NEXT_TURN))
        match.log_line(
            f"{match.label(actor)} sinks into {match.label(tgt)} — its blows land "
            f"1 weaker and its reach shortens until the ghost stirs again."
        )


class Pounce:
    """剑齿虎 pins what it mauls: anything its normal attack draws blood from cannot
    move on its next turn. It can still fight from where it stands."""

    describe = ("Anything its normal attack hits cannot move on its next turn — it "
                "may still attack and cast from where it stands.")

    # Late in the pipeline, so it only fires on damage that actually got through.
    @EV.hook(priority=60)
    def on_after_damage(self, match, owner, ev):
        if ev.source is not owner or ev.category != DMG.NORMAL_ATTACK or ev.amount <= 0:
            return
        if ev.target.side == owner.side:
            return
        match.root(ev.target)
        match.log_line(f"{match.label(ev.target)} is pinned — no movement on its next turn.")


class Shotgun:
    """男枪's 散弹枪. Two halves, both hung off the turn-resolved hook: a hit ramps
    its damage (to a cap), and a hit lets it reposition once the exchange has
    settled — the follow-up asks where."""

    describe = ("Its attack sprays a three-cell arc in a chosen direction. Every shot "
                "that connects raises its damage by 1 — at most +2 — and lets it step "
                "one square once the exchange is over.")
    RAMP_CAP = 2

    def on_turn_resolved(self, match, owner, ctx):
        if ctx.get("entity") is not owner or not ctx.get("landed"):
            return
        ramp = owner.vars.get("spread_ramp", 0)
        if ramp >= self.RAMP_CAP:
            return
        owner.vars["spread_ramp"] = ramp + 1
        owner.add_modifier(Modifier("atk", "add", 1, source=self))
        match.log_line(
            f"{match.label(owner)} finds its range — Atk now {owner.atk}"
            + (" (as far as it goes)." if ramp + 1 == self.RAMP_CAP else ".")
        )

    def followup(self, match, owner, ctx):
        if ctx.get("entity") is not owner or not ctx.get("landed"):
            return None
        if not owner.alive or not owner.cells or match.rooted(owner):
            return None
        free = [c for c in match.topology.neighbours(owner.cell)
                if match.can_enter(owner, c)]
        if not free:
            return None
        return {
            "key": "reposition",
            "name": "散弹枪 Reposition",
            "text": "The shot connected — step one square now, or hold your ground.",
            "kind": "cell",
            "optional": True,
            "anchor": list(owner.cell),
            "options": [list(c) for c in free],
        }

    def apply_followup(self, match, owner, key, choice):
        if key != "reposition" or not choice:
            return
        cell = tuple(choice)
        if not owner.alive or not match.can_enter(owner, cell):
            return
        frm = owner.cell
        owner.set_cell(cell)
        match.bus.emit(EV.AFTER_MOVE, {"entity": owner, "from": frm, "to": cell})
        match.log_line(f"{match.label(owner)} racks the slide and steps aside.")

    def status(self, match, owner):
        ramp = owner.vars.get("spread_ramp", 0)
        if not ramp:
            return None
        return {
            "key": "ramp", "badge": f"+{ramp}", "label": "散弹枪 DIALLED IN",
            "text": f"Every shot that connected has raised its damage — +{ramp}"
                    + (", as far as it goes." if ramp >= self.RAMP_CAP else "."),
        }


class GhostForm:
    """Bodiless until it takes flesh. Nothing new in the engine: it turns off the
    entity flags that already exist and gives up its square. Stepping into the
    world is its *movement* — the squares beside whoever it is haunting — so the
    turn it appears is an ordinary turn: it walks out of the host and acts."""

    describe = ("Bodiless: holds no square and nothing can touch it, but it cannot hold "
                "the field either. From its 4th turn it may step into an empty square "
                "beside the hero it haunts — giving up 附身 for good — and then take "
                "that turn as normal. The body it builds has as much health as it has "
                "dealt damage, so the longer it haunts the stronger it steps out.")
    FROM_TURN = 4

    def on_match_start(self, match, owner, ctx):
        if owner.vars.get("manifested"):
            return
        owner.cells = set()
        owner.flags.update(blocks_movement=False, counts_for_defeat=False,
                           targetable=False)

    # Everything it takes while haunting is what it will be made of.
    @EV.hook(priority=60)
    def on_after_damage(self, match, owner, ev):
        if ev.source is owner and ev.amount > 0 and not owner.vars.get("manifested"):
            owner.vars["harvest"] = owner.vars.get("harvest", 0) + ev.amount

    @classmethod
    def ready(cls, owner):
        """True once this is its FROM_TURN'th turn — it has finished the three before."""
        return owner.vars.get("turns_done", 0) >= cls.FROM_TURN - 1

    def manifest_cells(self, match, owner):
        """Where a bodiless hero may step into being. `match.legal_moves` asks every
        passive for these, so this is the whole of its movement while incorporeal."""
        if owner.vars.get("manifested") or not self.ready(owner):
            return []
        host = match.entity(owner.vars.get("haunting"))
        if host is None or not host.alive or not host.cells:
            return []
        return [c for c in match.topology.neighbours(host.cell)
                if match.can_enter(owner, c)]

    def on_after_move(self, match, owner, ctx):
        """It only ever "moves" from nowhere once — that is the moment it takes flesh."""
        if ctx.get("entity") is not owner or owner.vars.get("manifested"):
            return
        if ctx.get("from") is not None:
            return
        owner.vars["manifested"] = True
        owner.abilities = [a for a in owner.abilities if a.key != Possess.key]
        owner.flags.update(blocks_movement=True, counts_for_defeat=True, targetable=True)
        # The body it builds is made of what it drained: as much health as it has
        # dealt damage. Never less than 1, so it can never step out already dead.
        owner.max_hp = max(1, owner.vars.get("harvest", 0))
        owner.hp = owner.max_hp
        match.expire_owner_modifiers(owner)      # the haunt it was holding lifts
        match.log_line(
            f"{match.label(owner)} tears loose and takes flesh with "
            f"{owner.max_hp} health — every point of it drained."
        )

    def status(self, match, owner):
        if owner.vars.get("manifested"):
            return None
        host = match.entity(owner.vars.get("haunting"))
        left = max(0, self.FROM_TURN - 1 - owner.vars.get("turns_done", 0))
        return {
            "key": "ghost", "badge": "魂", "label": "鬼魂 BODILESS",
            "text": ("Cannot be touched, and holds no ground."
                     + (f" Haunting {host.name}." if host and host.alive else "")
                     + f" Drained {max(1, owner.vars.get('harvest', 0))} — the health it"
                       " would take flesh with."
                     + (f" Can do so in {left} more turn{'' if left == 1 else 's'}."
                        if left else " It may do so now.")),
        }


class CursePoison(Ability):
    key = "curse_poison"
    name = "咒毒 Curse Poison"
    ap_cost = 0
    use_limit = 1
    opening = True
    targeting = {"kind": "ally"}
    blurb = ("At game start, mark one ally (itself included). The first attack or "
             "ability that damages the marked hero costs its source its next turn. "
             "Once it springs, 再咒 can lay another.")

    def side_effects(self, match, actor, params):
        tgt = match.entity(params.get("target"))
        if tgt is None or not tgt.alive or tgt.side != actor.side:
            return
        actor.vars["curse_live"] = True
        tgt.vars["curse_mark"] = actor.id
        # Scoped to its own seat: the enemy must not know which hero is baited.
        match.log_line(
            f"{match.label(actor)} curses {match.label(tgt)} — whoever draws its blood "
            f"loses two rounds.",
            side=actor.side,
        )


class Transfer(Ability):
    """魔术师's 转移. Resolves with movement rather than with damage, so a unit
    pulled into a marked square is the one that takes the hit — attacks find
    their victims from live positions."""

    key = "transfer"
    name = "转移 Transfer"
    ap_cost = 2
    targeting = {"kind": "two_units"}
    blurb = ("Swap any two units on the board, anywhere. It happens as everyone "
             "moves, so a hero dragged into a marked square takes what was aimed "
             "at whoever stood there.")

    @staticmethod
    def pair(match, params):
        a = match.entity(params.get("first"))
        b = match.entity(params.get("second"))
        return a, b

    def validate(self, match, actor, params, origin=None):
        a, b = self.pair(match, params)
        for x in (a, b):
            if x is None or not x.alive or not x.cells:
                return "Choose two units that are on the board."
        if a is b:
            return "Choose two different units."
        # Each has to be able to stand where the other is. A square shut against
        # one of them (工匠's doors) is no place to be swapped into either.
        if not match.can_enter(a, b.cell, ignore=(b,)) \
                or not match.can_enter(b, a.cell, ignore=(a,)):
            return "One of them cannot stand where the other is."
        return None

    def move_effects(self, match, actor, params):
        a, b = self.pair(match, params)
        if a is None or b is None or a is b or not a.cells or not b.cells:
            return
        ca, cb = a.cell, b.cell
        a.set_cell(cb)
        b.set_cell(ca)
        match.bus.emit(EV.AFTER_MOVE, {"entity": a, "from": ca, "to": cb})
        match.bus.emit(EV.AFTER_MOVE, {"entity": b, "from": cb, "to": ca})
        match.log_line(
            f"{match.label(actor)} works the switch — {match.label(a)} and "
            f"{match.label(b)} trade places."
        )


class Bless(Ability):
    """长老's 祝福. A one-shot ward that also quickens the one who carries it. Only
    one to a hero — an already-blessed ally cannot take a second."""

    key = "bless"
    name = "祝福 Blessing"
    ap_cost = 2
    targeting = {"kind": "ally"}
    blurb = ("Ward one ally — itself included — against the next attack or ability "
             "that would hurt it, and give it +1 movement until that happens. One "
             "blessing to a hero.")

    def blessable(self, match, actor, origin=None):
        return [e.id for e in match.on_map(actor.side) if not e.vars.get("blessed")]

    def validate(self, match, actor, params, origin=None):
        tgt = match.entity(params.get("target"))
        if tgt is not None and tgt.vars.get("blessed"):
            return f"{tgt.name} already carries a blessing."
        return None

    def side_effects(self, match, actor, params):
        tgt = match.entity(params.get("target"))
        if tgt is None or not tgt.alive or tgt.side != actor.side:
            return
        if tgt.vars.get("blessed"):
            return
        tgt.vars["blessed"] = actor.id
        tgt.add_modifier(Modifier("move", "add", 1, source="blessing"))
        match.log_line(
            f"{match.label(actor)} blesses {match.label(tgt)} — one blow turned "
            f"aside, and swifter until it is."
        )


class Slam(Ability):
    """大力士's 摔击. Two halves, a whole exchange apart. On its turn it takes hold
    of somebody beside it and nothing else happens — no damage, no movement, and
    the grip does not stop them acting. Once the exchange has settled it throws,
    and only then does it choose where: any empty square within 3 of wherever it
    ended up standing.

    Either side can be seized. An enemy takes the fall; one of your own is simply
    put somewhere better, which is what makes the grip worth having when there is
    nobody to hurt.

    Nothing between the grab and the throw shakes it loose — the target may walk
    the length of the board and is still thrown from wherever it got to. Only a
    death ends it, either its own or the strongman's."""

    key = "slam"
    name = "摔击 Slam"
    ap_cost = 1
    targeting = {"kind": "any_unit"}
    blurb = ("Take hold of anyone in the 8 squares around you — either side. Nothing "
             "happens until the exchange has settled; then you throw them to any "
             "empty square within 3 of where you ended up. An enemy takes 3.")
    DAMAGE = 3
    REACH = 3
    THROW = "slam_throw"

    def throwable(self, match, actor, origin=None):
        """Everyone it could take hold of, for the client to offer — from wherever
        it will be standing when it grabs, not where it is leaving."""
        origin = tuple(origin) if origin else (actor.cell if actor.cells else None)
        if origin is None:
            return []
        beside = match.surround8(origin)
        return [e.id for e in match.living()
                if e is not actor and e.cells and e.flags["targetable"]
                and e.cell in beside]

    def validate(self, match, actor, params, origin=None):
        tgt = match.entity(params.get("target"))
        if tgt is None or not tgt.alive:
            return "Choose anyone still standing."
        if not actor.cells and origin is None:
            return f"{actor.name} has nothing to grab with."
        if tgt.id not in self.throwable(match, actor, origin):
            return "It can only take hold of somebody right beside it."
        return None

    def build_damage(self, match, actor, params):
        return []               # the grab is not a blow — nothing lands yet

    def side_effects(self, match, actor, params):
        tgt = match.entity(params.get("target"))
        if tgt is None or not tgt.alive:
            return
        actor.vars["slam_grab"] = tgt.id
        match.log_line(f"{match.label(actor)} takes hold of {match.label(tgt)}.")

    # --- the throw, once the exchange has settled -------------------------

    def squares(self, match, actor, target):
        """Where it could put them: empty ground within reach of where it ended up,
        judged for the one being thrown — a square shut against their side is no
        landing for them."""
        if not actor.cells:
            return []
        return [c for c in match.topology.cells_within(actor.cell, self.REACH)
                if match.can_enter(target, c, ignore=(target,))]

    def followup(self, match, owner, ctx):
        tgt = match.entity(owner.vars.get("slam_grab"))
        if tgt is None or not tgt.alive or not owner.alive:
            owner.vars.pop("slam_grab", None)   # a death ends it, either one's
            return None
        cells = self.squares(match, owner, tgt)
        if not cells:
            owner.vars.pop("slam_grab", None)
            match.log_line(f"{match.label(owner)} has nowhere to put "
                           f"{match.label(tgt)}, and lets go.")
            return None
        return {
            "key": self.THROW,
            "name": self.name,
            "text": (f"You have hold of {tgt.name}. Throw it to any empty square "
                     f"within {self.REACH}"
                     + (f" — it takes {self.DAMAGE}." if tgt.side != owner.side
                        else ", putting it somewhere better.")),
            "kind": "cell",
            "anchor": list(owner.cell),
            "options": [list(c) for c in cells],
        }

    def apply_followup(self, match, owner, key, choice):
        if key != self.THROW or not choice:
            return
        tgt = match.entity(owner.vars.pop("slam_grab", None))
        cell = tuple(choice)
        if tgt is None or not tgt.alive or not match.can_enter(tgt, cell, ignore=(tgt,)):
            return
        frm = tgt.cell
        tgt.set_cell(cell)
        match.bus.emit(EV.AFTER_MOVE, {"entity": tgt, "from": frm, "to": cell})
        if tgt.side == owner.side:
            match.log_line(f"{match.label(owner)} sets {match.label(tgt)} down "
                           f"where it is wanted.")
            hurt = []
        else:
            match.log_line(f"{match.label(owner)} hurls {match.label(tgt)} clean "
                           f"over its shoulder.")
            hurt = [DMG.DamageEvent(source=owner, target=tgt, amount=self.DAMAGE,
                                    category=DMG.ABILITY)]
        # Through the same door as any other ability damage, so 教皇 may step in
        # front of it — landing after the exchange does not put it out of reach of
        # a save. Called even for an ally, because the ground it was put on may
        # have banked something of its own.
        match.deal_after_exchange(hurt)


class ShapeAbility(Ability):
    """An ability that offers a choice of shapes centred on the caster — your row,
    your column, the squares around you. Which one is sealed with the order, but
    the shape itself is drawn at resolution from the square the hero actually
    reached, so a step sideways swings it somewhere else entirely.

    Subclasses list their options in `targeting` and set DAMAGE; everything about
    enumerating and previewing them is the same for all of them."""

    DAMAGE = 0

    @staticmethod
    def cells_of(match, actor, which):
        """The squares one option covers, from where the hero is standing now."""
        return match.shape_cells(actor.cell, which) if actor.cells else []

    def victims(self, match, actor, which):
        return match.enemies_in(self.cells_of(match, actor, which), actor.side)

    def shapes(self, match, actor, origin=None):
        """Every option with the squares it covers, so the client can show one
        before it is chosen. A preview only — the real one is drawn at resolution."""
        out = []
        for which in self.targeting["options"]:
            cells = self.cells_of(match, actor, which)
            if not cells:
                continue
            out.append({"dir": which,
                        "cells": [list(c) for c in cells],
                        "victims": [v.id for v in self.victims(match, actor, which)],
                        "damage": self.DAMAGE})
        return out


class GaleSlash(ShapeAbility):
    """剑客's 狂风绝息斩. One cut down the whole row or the whole column it stands in.

    The mark it leaves is the generic `vulnerable` stack damage.py already reads:
    nothing here knows how it is applied later, only that it goes on."""

    key = "gale_slash"
    name = "狂风绝息斩 Gale Slash"
    ap_cost = 3
    targeting = {"kind": "shape", "options": ["row", "column"]}
    blurb = ("5 damage to every enemy in your whole row, or your whole column — "
             "you choose. Everyone caught takes 1 more from everything for the "
             "rest of the match, and the marks stack.")
    DAMAGE = 5
    MARK = 1

    def build_damage(self, match, actor, params):
        hit = self.victims(match, actor, params.get("direction"))
        # Noted while the line is still the one that was cut. The marks go on
        # afterwards, so this swing lands a clean 5 on everybody in it — only the
        # blows that come after are the harder ones.
        actor.vars["gale_marked"] = [e.id for e in hit]
        return [DMG.DamageEvent(source=actor, target=e, amount=self.DAMAGE,
                                category=DMG.ABILITY)
                for e in hit]

    def side_effects(self, match, actor, params):
        marked = []
        for eid in actor.vars.pop("gale_marked", []):
            e = match.entity(eid)
            # Side effects run before the dead are swept, so a hero the cut just
            # killed is still flagged alive — no point marking a corpse.
            if e is None or not e.alive or e.hp <= 0:
                continue
            e.vars["vulnerable"] = e.vars.get("vulnerable", 0) + self.MARK
            marked.append(e)
        if marked:
            match.log_line(
                f"{match.label(actor)} leaves the wind in the wound — "
                + ", ".join(f"{match.label(e)} +{e.vars['vulnerable']}" for e in marked)
                + " to everything from here on."
            )


class SelfDestruct(ShapeAbility):
    """炸弹客's 自爆. The hero itself is the cost: its health goes to nothing and it
    takes one shape of the board with it.

    体力降至0 is a setting, not a blow — it is not a DamageEvent, so no ward, no
    reduction and no 增伤 mark touches it, and nothing can soften it into survival.
    The blast and the death land in the same instant: the engine sweeps the dead
    only once every hit of an exchange has been applied, so killing the bomber in
    the same breath does not put the fuse out."""

    key = "self_destruct"
    name = "自爆 Self-Destruct"
    ap_cost = 3
    targeting = {"kind": "shape", "options": ["row", "column", "surround8"]}
    blurb = ("Drop your own health to nothing. 6 damage to every enemy in one shape "
             "you choose: your row, your column, or the 8 squares around you.")
    DAMAGE = 6

    def build_damage(self, match, actor, params):
        return [DMG.DamageEvent(source=actor, target=e, amount=self.DAMAGE,
                                category=DMG.ABILITY)
                for e in self.victims(match, actor, params.get("direction"))]

    def side_effects(self, match, actor, params):
        if not actor.alive:
            return
        actor.hp = 0
        match.log_line(f"{match.label(actor)} goes up with it — nothing left.")


class LayBigBomb(Ability):
    """潜水者's opening charge. Any square on the board — it is invisible to the
    other side, so there is nothing to give away by burying it under their feet."""

    key = "big_bomb"
    name = "大炸弹 Big Bomb"
    ap_cost = 0
    use_limit = 1
    opening = True
    targeting = {"kind": "any_cell"}
    blurb = ("Before the first exchange, bury a charge in any square. It goes off at "
             "the start of the round two later for 6 — and only your side can see it.")

    def side_effects(self, match, actor, params):
        plant_big_bomb(match, actor, tuple(params["cell"]))


def plant_big_bomb(match, actor, cell):
    """Shared by the opening charge and the one laid as 潜水者 goes down."""
    bomb = BOARD.BigBomb(actor.side, match.round)
    match.board.add_effect(cell, bomb)
    match.log_line(
        f"{match.label(actor)} buries a charge — it goes off at the start of "
        f"round {bomb.fuse_round}.",
        side=actor.side,
    )


class BombLayer:
    """潜水者. Two habits, both of them choices offered once the exchange has
    settled, the way 男枪's step is: a small charge dropped beside it whenever it
    moved, and one last big one as it dies.

    Neither needs new machinery — the board owns the bombs and the follow-up pair
    owns the asking."""

    describe = ("Lays a small bomb in an empty square beside it at the end of any turn "
                "it moved — 3 damage to the first enemy that steps on it. Buries a big "
                "charge before the first exchange, and another as it dies. Only your "
                "side can see them.")
    SMALL = "small_bomb"
    LAST = "last_charge"

    def on_turn_start(self, match, owner, ctx):
        if ctx.get("entity") is owner:
            owner.vars["moved_this_turn"] = False

    def on_after_move(self, match, owner, ctx):
        if ctx.get("entity") is owner and ctx.get("from") is not None:
            owner.vars["moved_this_turn"] = True

    def followup(self, match, owner, ctx):
        if ctx.get("entity") is not owner:
            return None
        if ctx.get("died"):
            # It never gets another turn, so this is the only moment to ask.
            if owner.vars.get("last_charge_spent"):
                return None
            return {
                "key": self.LAST,
                "name": "大炸弹 Last Charge",
                "text": "It goes down laying one more. Choose any square — it blows "
                        "two rounds from now, for 6.",
                "kind": "cell",
                "optional": True,
                "options": [list(c) for c in match.topology.all_cells()],
            }
        if not ctx.get("acted") or not owner.alive or not owner.cells:
            return None
        if not owner.vars.get("moved_this_turn"):
            return None
        # 空格 means empty of units — a square that already holds charges is still
        # fair game, and they pile up.
        free = [c for c in match.topology.neighbours(owner.cell)
                if match.occupant(c) is None]
        if not free:
            return None
        return {
            "key": self.SMALL,
            "name": "小炸弹 Small Bomb",
            "text": "It moved this turn — drop a small charge in a square beside it, "
                    "or keep it. 3 damage to the first enemy that steps there.",
            "kind": "cell",
            "optional": True,
            "anchor": list(owner.cell),
            "options": [list(c) for c in free],
        }

    def apply_followup(self, match, owner, key, choice):
        if not choice:
            return
        cell = tuple(choice)
        if key == self.LAST:
            owner.vars["last_charge_spent"] = True
            plant_big_bomb(match, owner, cell)
        elif key == self.SMALL:
            if match.occupant(cell) is not None:
                return
            match.board.add_effect(cell, BOARD.SmallBomb(owner.side))
            match.log_line(
                f"{match.label(owner)} drops a small charge.",
                side=owner.side,
            )


class Garrote(Ability):
    """刺客's 封喉. The order names a hero, not a square — the blink resolves once
    everybody has finished moving, so the mark cannot walk away from it. Same
    promise 雷霆龙's and 雾女's unit-locked attacks make.

    Which square it appears on is the player's to pick, but it cannot be picked
    when the order is sealed — the mark may not be standing where it was. So the
    choice is offered during resolution, once everybody has stopped moving and the
    free squares beside the mark are actually known."""

    key = "garrote"
    name = "封喉 Garrote"
    ap_cost = 2
    self_move = True             # its blink replaces the walk
    carries_self = True
    targeting = {"kind": "unit"}
    blurb = ("Blink to a square beside any one enemy, anywhere on the board, and cut "
             "it. It resolves after everyone has moved, so the mark cannot dodge — but "
             "if all four squares around it are taken there is nowhere to appear.")

    @staticmethod
    def landings(match, actor, params):
        """Every square it could appear on — the free orthogonals of the mark, read
        from wherever the mark actually ended up."""
        tgt = match.entity(params.get("target"))
        if tgt is None or not tgt.alive or not tgt.cells:
            return []
        return [c for c in match.topology.neighbours(tgt.cell)
                if match.can_enter(actor, c)]

    def move_choice(self, match, actor, params):
        """Offered mid-resolution: movement is over, so these squares are real."""
        if not actor.alive or not actor.cells:
            return None
        free = self.landings(match, actor, params)
        if not free:
            return None
        tgt = match.entity(params.get("target"))
        return {
            "key": self.key,
            "name": self.name,
            "text": f"Choose where to appear beside {tgt.name}.",
            "kind": "cell",
            "anchor": list(tgt.cell),
            "options": [list(c) for c in free],
        }

    def validate(self, match, actor, params, origin=None):
        t = match.entity(params.get("target"))
        if t is None or not t.alive or t.side == actor.side:
            return "Choose a living enemy."
        return None

    def move_effects(self, match, actor, params):
        """With movement, after every ordinary move has landed — which is exactly
        what lets it follow a mark that ran."""
        tgt = match.entity(params.get("target"))
        actor.vars["garrote_target"] = None
        if tgt is None or not tgt.alive or not actor.alive or not actor.cells:
            return
        free = self.landings(match, actor, params)
        cell = match.move_picks.get(actor.id)
        if cell not in free:
            # No pick to honour: either the mark is hemmed in, or the one square
            # on offer was taken silently because there was nothing to decide.
            cell = free[0] if len(free) == 1 else None
        if cell is None:
            match.log_line(
                f"{match.label(actor)} finds no way in — {match.label(tgt)} is "
                f"hemmed in on every side."
            )
            return
        frm = actor.cell
        actor.set_cell(cell)
        actor.vars["garrote_target"] = tgt.id
        # A real move, so anything watching movement sees it — a mine under the
        # square it picks goes off exactly as it would for a hero that walked there.
        match.bus.emit(EV.AFTER_MOVE, {"entity": actor, "from": frm, "to": cell})
        match.log_line(
            f"{match.label(actor)} is gone, and standing over {match.label(tgt)}."
        )

    def build_damage(self, match, actor, params):
        tgt = match.entity(actor.vars.pop("garrote_target", None))
        if tgt is None or not tgt.alive or not actor.alive:
            return []      # nowhere to appear, so no throat to cut
        return [DMG.DamageEvent(source=actor, target=tgt, amount=actor.atk,
                                category=DMG.NORMAL_ATTACK)]


class Soak(Ability):
    """水法师's 浸水. Paint four squares that touch — a line, an L, a block, whatever
    the board offers — and everything of theirs standing in them is drenched. Half
    of what the water takes, the mage keeps.

    The marking is the ordinary cell-marking every attack uses; the only thing this
    adds is that the four must be one piece."""

    key = "soak"
    name = "浸水 Soak"
    ap_cost = 2
    targeting = {"kind": "cells", "count": 4, "range": 6, "shots": 1}
    blurb = ("Mark four connected squares within 6. Every enemy in them takes 3 water "
             "damage, and the mage mends for half of everything that actually landed.")
    DAMAGE = 3

    def validate(self, match, actor, params, origin=None):
        cells = (params.get("shots") or [[]])[0]
        if not match.topology.connected(cells):
            return "The four squares must touch — one shape, not scattered."
        return None

    def build_damage(self, match, actor, params):
        cells = (params.get("shots") or [[]])[0]
        events = [
            DMG.DamageEvent(source=actor, target=e, amount=self.DAMAGE,
                            category=DMG.ABILITY, element=DMG.WATER)
            for e in match.enemies_in(cells, actor.side)
        ]
        # Kept so the mending can read what actually landed once the pipeline has
        # had its say — guards, marks and caps all count against it.
        actor.vars["soak_events"] = events
        return events

    def side_effects(self, match, actor, params):
        events = actor.vars.pop("soak_events", [])
        took = sum(ev.dealt for ev in events)
        if not took or not actor.alive:
            return
        healed = DMG.heal(match, actor, took // 2, source=actor)
        if healed:
            match.log_line(
                f"{match.label(actor)} draws the water back — {took} taken, "
                f"{healed} mended."
            )


def pass_judgement(match, judge, target, kind):
    """Lay a verdict on somebody. One at a time — a new one replaces whatever was
    there, whichever kind it was."""
    if target is None or not target.alive:
        return
    target.vars["judged"] = {"kind": kind, "judge": judge.id, "exchange": match.exchange}
    word = "rewarded" if kind == "reward" else "condemned"
    match.log_line(
        f"{match.label(judge)} marks {match.label(target)} — it will be {word} at the "
        f"end of its next turn, by whatever it does with it."
    )


class Commend(Ability):
    """法官's 赏善. Anyone may be marked, either side's — rewarding one of theirs is
    a poor idea, but the bench does not check colours."""

    key = "commend"
    name = "赏善 Commend"
    ap_cost = 2
    targeting = {"kind": "any_unit"}
    judges = "reward"
    blurb = ("Mark anyone. At the end of its next turn it is mended for everything it "
             "dealt during that turn.")

    def side_effects(self, match, actor, params):
        pass_judgement(match, actor, match.entity(params.get("target")), "reward")


class Condemn(Ability):
    """法官's 罚恶. The mirror of 赏善, and cheaper — cruelty usually is."""

    key = "condemn"
    name = "罚恶 Condemn"
    ap_cost = 1
    targeting = {"kind": "any_unit"}
    judges = "punish"
    blurb = ("Mark anyone. At the end of its next turn it takes everything it dealt "
             "during that turn, back on itself.")

    def side_effects(self, match, actor, params):
        pass_judgement(match, actor, match.entity(params.get("target")), "punish")


class RaiseDoors(Ability):
    """工匠's doors. Chosen before anybody is placed, so both sides deploy knowing
    where they are — that is the whole point of building first rather than after.

    The two squares become neighbours for its own side and nobody else. Adjacency is
    the topology's business, and it has always taken the unit as an argument for
    exactly this reason, so no rule about movement had to change."""

    key = "raise_doors"
    name = "立门 Raise Doors"
    ap_cost = 0
    prebuild = True
    targeting = {"kind": "two_cells"}
    blurb = ("Before anyone deploys, pick two squares anywhere on the board. Your side "
             "may step between them as though they touched. Both sides can see them.")

    def build_cells(self, match, side):
        """Only the board proper. An island is not somewhere a door can open onto,
        and `all_cells` already leaves one out, so this needs no rule of its own."""
        return match.topology.all_cells()

    def validate_build(self, match, side, params):
        cells = [tuple(c) for c in (params.get("cells") or [])]
        if len(cells) != 2:
            return "Pick two squares."
        if cells[0] == cells[1]:
            return "Pick two different squares."
        for c in cells:
            if not match.topology.in_bounds(c):
                return "That square is not on the board."
            if match.topology.region(c) is not None:
                return "A door cannot open onto an island."
        return None

    def build_effects(self, match, side, params):
        a, b = (tuple(c) for c in params["cells"])
        match.topology.link(a, b, side)
        match.log_line(f"{'Left' if side == LEFT else 'Right'} 工匠 raises a pair of doors.")


class Avalanche(Ability):
    """雪女's 大雪崩. Everything of theirs, wherever it stands, and the cold takes a
    point of their strength with it."""

    key = "avalanche"
    name = "大雪崩 Great Avalanche"
    ap_cost = 4
    targeting = {"kind": "none"}
    blurb = ("6 water damage to every enemy on the board, and every one of them loses "
             "a point of AP.")
    DAMAGE = 6

    def build_damage(self, match, actor, params):
        return [
            DMG.DamageEvent(source=actor, target=e, amount=self.DAMAGE,
                            category=DMG.ABILITY, element=DMG.WATER)
            for e in match.enemies_in(None, actor.side)
        ]

    def side_effects(self, match, actor, params):
        chilled = [e for e in match.enemies_in(None, actor.side) if e.ap > 0]
        for e in chilled:
            e.ap = max(0, e.ap - 1)
        if chilled:
            match.log_line(
                f"{match.label(actor)}'s cold settles — "
                + ", ".join(f"{match.label(e)} to {e.ap}" for e in chilled) + " AP."
            )


class Hook(Ability):
    """渔夫's hook. Thrown down one of the eight lanes, it catches the first thing it
    reaches and hauls it in to the square beside you. It is a haul, not a blow: the
    catch takes no damage, it simply ends up somewhere it did not choose.

    Three ways to waste it, all of them the thrower's misjudgement: the lane is
    empty, the first thing down it is one of your own, or the square you would haul
    it into is already taken."""

    key = "hook"
    name = "钩爪 Hook"
    ap_cost = 2
    targeting = {"kind": "direction",
                 "options": ["forward", "backward", "up", "down",
                             "fwd_up", "fwd_down", "back_up", "back_down"]}
    blurb = ("Throw down any of the eight lanes. The first enemy it reaches is hauled "
             "in to the square beside you. It fails if the lane is empty, if one of "
             "your own is first, or if that square is taken.")

    @staticmethod
    def cast(match, actor, name, origin=None):
        """(catch, landing) for this lane, or None if the throw would come to
        nothing. The landing square is the one beside the thrower down that lane.

        Thrown from `origin` — where the fisherman will be standing when the hook
        goes out, which is its destination, not the square it is leaving. Its own
        body never blocks the lane: by the time the line is drawn it has left."""
        step = match.topology.direction_step(actor.side, name)
        origin = tuple(origin) if origin else actor.cell
        if step is None or origin is None:
            return None
        landing = (origin[0] + step[0], origin[1] + step[1])
        if not match.topology.same_region(landing, origin):
            return None              # the square beside you is not on your map
        if match.occupant(landing) not in (None, actor) and landing != origin:
            pass                     # checked per catch below, once one is found
        for cell in match.topology.lane(origin, step):
            occ = match.occupant(cell)
            if occ is None or occ is actor:
                continue             # its own square is vacated by the time it throws
            if occ.side == actor.side or not occ.flags["targetable"]:
                return None          # one of your own is in the way
            if not match.can_enter(occ, landing, ignore=(actor, occ)):
                return None          # nowhere to haul it to
            return occ, landing
        return None

    def lanes(self, match, actor, origin=None):
        """Every throw worth making, for the client to offer and preview."""
        out = []
        for name in match.topology.DIRECTIONS8:
            got = self.cast(match, actor, name, origin)
            if got is None:
                continue
            catch, landing = got
            out.append({"dir": name, "landing": list(landing),
                        # The hook moves the catch, never the thrower.
                        "mover": catch.id,
                        "victims": [catch.id], "damage": 0,
                        "where": "hauls it in",
                        "label": f"catches {catch.name} and drags it beside you"})
        return out

    def validate(self, match, actor, params, origin=None):
        if params.get("direction") not in match.topology.DIRECTIONS8:
            return "Choose a lane to throw down."
        if self.cast(match, actor, params["direction"], origin) is None:
            return "Nothing to catch that way — an empty lane, one of your own, or no room to haul it in."
        return None

    def move_effects(self, match, actor, params):
        """Hauled in with the movement, so whoever is dragged into a marked square
        is the one that takes what was aimed at it — the same rule 魔术师's swap
        plays by."""
        got = self.cast(match, actor, params.get("direction"))
        if got is None:
            match.log_line(f"{match.label(actor)} throws and comes up empty.")
            return
        catch, landing = got
        if not match.can_enter(catch, landing, ignore=(catch,)):
            match.log_line(f"{match.label(actor)} has nowhere to haul it in to.")
            return
        frm = catch.cell
        catch.set_cell(landing)
        match.bus.emit(EV.AFTER_MOVE, {"entity": catch, "from": frm, "to": landing})
        match.log_line(f"{match.label(actor)} hauls {match.label(catch)} in.")


class Recurse(Ability):
    """诅咒娃娃's 再咒. Only available once the mark it laid has actually been
    sprung — a curse still sitting on the board cannot be moved."""

    key = "recurse"
    name = "再咒 Curse Again"
    ap_cost = 2
    targeting = {"kind": "ally"}
    blurb = ("Lay the curse on another ally, once the last one has been sprung. "
             "The next hero to draw that ally's blood loses its next turn.")

    def available(self, match, actor):
        return not actor.vars.get("curse_live")

    def validate(self, match, actor, params, origin=None):
        if actor.vars.get("curse_live"):
            return "The curse it already laid has not been sprung yet."
        return None

    def side_effects(self, match, actor, params):
        tgt = match.entity(params.get("target"))
        if tgt is None or not tgt.alive or tgt.side != actor.side:
            return
        actor.vars["curse_live"] = True
        tgt.vars["curse_mark"] = actor.id
        match.log_line(
            f"{match.label(actor)} curses {match.label(tgt)} again — whoever draws "
            f"its blood loses a turn.",
            side=actor.side,
        )


class MagicWard(Ability):
    key = "magic_ward"
    name = "魔法守护 Magic Ward"
    ap_cost = 2
    targeting = {"kind": "none"}
    blurb = ("Until her next turn begins, no enemy hero can use an active ability. "
             "Orders already sealed this exchange still resolve, and the ward lifts "
             "early if she falls.")

    def side_effects(self, match, actor, params):
        match.set_ability_lock(actor)


class DreamWard:
    """Display only — the lock itself lives on the match, since it silences a whole
    side rather than attaching to any one unit."""

    describe = ("While her ward is up, enemy heroes cannot use active abilities — "
                "until her next turn, or until she is destroyed.")

    def status(self, match, owner):
        if match.ability_lock.get(other_side(owner.side)) != owner.id:
            return None
        return {
            "key": "ward",
            "badge": "守",
            "label": "魔法守护 WARD",
            "text": "Enemy heroes cannot use active abilities until her next turn.",
        }


class GreatFog(Ability):
    key = "great_fog"
    name = "大雾 Great Fog"
    ap_cost = 3
    targeting = {"kind": "none"}
    blurb = ("Every enemy loses 1 attack range, permanently. Casts stack, but never "
             "below 1, and heroes who strike any enemy anywhere are unaffected.")
    FLOOR = 1

    def side_effects(self, match, actor, params):
        fogged = []
        for e in match.on_map(other_side(actor.side)):
            # No finite range to shorten (雷霆龙 reaches the whole board), and never
            # push anyone below the floor — a range-1 attacker just shrugs it off.
            if e.rng is None or e.rng <= self.FLOOR:
                continue
            e.add_modifier(Modifier("rng", "add", -1, source=self))
            fogged.append(e)
        if fogged:
            match.log_line(
                f"{match.label(actor)} rolls in the fog — "
                + ", ".join(f"{match.label(e)} to {e.rng}" for e in fogged)
                + " range."
            )
        else:
            match.log_line(f"{match.label(actor)} rolls in the fog, but nobody's reach can shorten.")


class Ray(Ability):
    key = "ray"
    name = "射线 Ray"
    ap_cost = 2
    targeting = {"kind": "none"}
    blurb = "6 damage to every enemy in the caster's row. Travels with it if bounced."

    def build_damage(self, match, actor, params):
        return [
            DMG.DamageEvent(source=actor, target=e, amount=6, category=DMG.ABILITY)
            for e in match.enemies_in(match.topology.row(actor.cell[1]), actor.side)
        ]


class RallyTheLine(Ability):
    """One permanent point of something to everyone still on the board, on your own
    side. 鼓舞 and 激励 are the same ability pointed at different stats, so they say
    so rather than each carrying its own copy of the loop."""

    ap_cost = 2
    targeting = {"kind": "none"}
    STAT = None
    VERB = "rallies"

    def side_effects(self, match, actor, params):
        for e in match.on_map(actor.side):
            e.add_modifier(Modifier(self.STAT, "add", 1))
        match.log_line(f"{match.label(actor)} {self.VERB} the line — "
                       f"+1 {self.STAT} to all allies.")


class Inspire(RallyTheLine):
    key = "inspire"
    name = "鼓舞 Inspire"
    STAT = "atk"
    VERB = "inspires"
    blurb = "Every ally gains +1 attack, permanently. Casts stack."


class Incite(RallyTheLine):
    key = "incite"
    name = "激励 Incite"
    use_limit = 1
    STAT = "move"
    blurb = "Once per match: every ally gains +1 movement, permanently."


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
    ap_cost = 2
    targeting = {"kind": "none"}
    blurb = "2 damage to every living enemy. No targeting, no counterplay."

    def build_damage(self, match, actor, params):
        return [
            DMG.DamageEvent(
                source=actor,
                target=e,
                amount=2,
                category=DMG.ABILITY,
                element=DMG.THUNDER,
            )
            for e in match.enemies_in(None, actor.side)
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
            f"{match.label(actor)} sets the ground alight "
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
    blurb = ("Once per match: spend health — current and maximum alike — for that "
             "much permanent attack. Never enough to kill yourself.")

    def magnitude_cap(self, actor):
        # Never enough to self-kill: one point of each is always left standing.
        return max(0, min(actor.hp - 1, actor.max_hp - 1))

    def side_effects(self, match, actor, params):
        # The sacrifice is real blood, not just a smaller frame: it costs current
        # HP as well as max.
        x = max(0, min(int(params.get("amount") or 0), self.magnitude_cap(actor)))
        if x <= 0:
            match.log_line(f"{match.label(actor)} has no blood left to spare.")
            return
        actor.max_hp -= x
        actor.hp = max(1, min(actor.hp - x, actor.max_hp))
        actor.add_modifier(Modifier("atk", "add", x))
        match.log_line(
            f"{match.label(actor)} sacrifices {x} max HP for +{x} attack "
            f"(now {actor.atk} atk, {actor.hp}/{actor.max_hp} HP)."
        )


class GoblinRally(Ability):
    key = "goblin_rally"
    name = "哥布林鼓舞 Goblin Rally"
    ap_cost = 2
    targeting = {"kind": "none"}
    blurb = ("Every living goblin in the gang gets +2 attack for the rest of this "
             "gang turn — so cast it before the javelins throw.")

    def side_effects(self, match, actor, params):
        crew = [e for e in match.living(actor.side) if e.hero.gang == actor.hero.gang]
        for e in crew:
            e.add_modifier(Modifier("atk", "add", 2, source=self, duration=UNTIL_TURN_END))
        match.log_line(
            f"{match.label(actor)} whips up the gang — +2 attack to {len(crew)} goblin(s) this turn."
        )


class BeastForm(Ability):
    key = "beast_form"
    name = "野兽化 Beast Form"
    ap_cost = 3
    use_limit = 1
    targeting = {"kind": "none"}
    blurb = ("Once per match: turn beast, permanently. Heal 4, Atk +3, Move +1 — "
             "but one grid less and 2 less range.")

    def side_effects(self, match, actor, params):
        healed = DMG.heal(match, actor, 4, source=actor)
        for stat, delta in (("atk", 3), ("move", 1), ("grid", -1), ("rng", -2)):
            actor.add_modifier(Modifier(stat, "add", delta))
        actor.vars["beast_form"] = True
        match.log_line(
            f"{match.label(actor)} turns beast — heals {healed}, "
            f"Atk {actor.atk}, Move {actor.move_allowance}, "
            f"{actor.grid} grids @ {actor.rng}."
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


class DivineAegis:
    """First damage of the round from an attack or ability lands in full; every
    later instance of those categories is turned aside for the rest of the round.
    Tile damage neither triggers it nor is blocked by it."""

    TRIGGERS = (DMG.NORMAL_ATTACK, DMG.ABILITY)
    describe = "After taking attack or ability damage, immune to all further attack and ability damage this round."

    @EV.hook(priority=30)
    def on_before_damage(self, match, owner, ev):
        if ev.target is not owner or ev.cancelled:
            return
        if ev.category not in self.TRIGGERS:
            return
        if owner.vars.get("aegis_spent"):
            ev.cancel("holy shield")

    @EV.hook(priority=60)
    def on_after_damage(self, match, owner, ev):
        if ev.target is owner and ev.category in self.TRIGGERS:
            if not owner.vars.get("aegis_spent"):
                owner.vars["aegis_spent"] = True
                match.log_line(f"{match.label(owner)} raises a holy shield — immune for the rest of round {match.round}.")

    def status(self, match, owner):
        if not owner.vars.get("aegis_spent"):
            return None
        return {
            "key": "aegis",
            "badge": "盾",
            "label": "神圣壁垒 SHIELD",
            "text": f"Immune to attack and ability damage for the rest of round {match.round}. "
                    "Tile damage still lands.",
        }

    def on_round_start(self, match, owner, ctx):
        owner.vars["aegis_spent"] = False


class SelfRepair:
    describe = "Heals 4 at the end of its own turn."

    def on_turn_end(self, match, owner, ctx):
        if ctx.get("entity") is owner and owner.alive:
            healed = DMG.heal(match, owner, 4, source=owner)
            if healed:
                match.log_line(f"{match.label(owner)} self-repairs {healed}.")


class KeenEdge:
    describe = "At the end of its own turn, Atk +1 (up to a maximum of 10)."
    CAP = 10

    def on_turn_end(self, match, owner, ctx):
        if ctx.get("entity") is not owner or not owner.alive:
            return
        gain = min(1, self.CAP - owner.atk)
        if gain <= 0:
            return
        owner.add_modifier(Modifier("atk", "add", gain))
        match.log_line(f"{match.label(owner)} sharpens — Atk now {owner.atk}.")


class Regrowth:
    describe = "At the start of its own turn, every ally recovers 1 HP."

    def on_turn_start(self, match, owner, ctx):
        if ctx.get("entity") is not owner or not owner.alive:
            return
        # One creature, one mend: 蛇帝's two halves share a pool and must not be
        # healed once each.
        total = sum(
            DMG.heal(match, e, 1, source=owner)
            for e in match.bodies(owner.side)
        )
        if total:
            match.log_line(f"{match.label(owner)}'s blessing restores the line (+1 to each ally).")


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


class LastStand:
    """背水: once per match, the blow that would kill 蛮王 leaves him at 1 HP and
    sends him berserk instead. Rage makes him untouchable and stronger, but it
    burns him out — he drops dead at the start of his third turn in it, so the
    rage buys exactly two turns of action."""

    describe = ("Once: a lethal hit leaves him at 1 HP and enrages him instead — "
                "immune to all damage and Atk +3, but he burns out at the start of "
                "his third turn enraged (two turns of action).")
    RAGE_TURNS = 3
    RAGE_ATK = 3

    # Earliest slot in the pipeline: rage beats reductions, wards, everything.
    @EV.hook(priority=10)
    def on_before_damage(self, match, owner, ev):
        if ev.target is owner and not ev.cancelled and owner.vars.get("rage"):
            ev.cancel("背水 rage")

    def on_before_death(self, match, owner, ctx):
        if ctx["entity"] is not owner or ctx.get("prevented"):
            return
        if owner.vars.get("last_stand_spent"):
            return  # already used once — and the burnout death must not be blocked
        ctx["prevented"] = True
        owner.vars["last_stand_spent"] = True
        owner.vars["rage"] = True
        owner.vars["rage_turns"] = 0
        owner.hp = 1
        owner.add_modifier(Modifier("atk", "add", self.RAGE_ATK))
        match.log_line(
            f"{match.label(owner)} refuses to fall — 背水! 1 HP, immune to all damage, "
            f"Atk now {owner.atk}. Two turns before the rage burns him out."
        )

    def status(self, match, owner):
        if not owner.vars.get("rage"):
            return None
        left = max(0, self.RAGE_TURNS - 1 - owner.vars.get("rage_turns", 0))
        tail = (f"{left} more turn{'' if left == 1 else 's'} of action"
                if left else "burns out at the start of its next turn")
        return {
            "key": "rage",
            "badge": "怒",
            "label": "背水 RAGE",
            "text": f"Immune to all damage · Atk +{self.RAGE_ATK} · {tail}",
        }

    def on_turn_start(self, match, owner, ctx):
        if ctx.get("entity") is not owner or not owner.vars.get("rage"):
            return
        turns = owner.vars.get("rage_turns", 0) + 1
        owner.vars["rage_turns"] = turns
        if turns < self.RAGE_TURNS:
            left = self.RAGE_TURNS - 1 - turns
            match.log_line(
                f"{match.label(owner)} rages on — {left} turn{'' if left == 1 else 's'} left."
            )
            return
        # Burnout. The rage flag drops first so nothing shields the death, and
        # sweep_deaths runs the normal death path (背水 is spent, so it stands).
        owner.vars["rage"] = False
        owner.hp = 0
        match.log_line(f"{match.label(owner)} burns out — the rage takes him.")
        match.sweep_deaths()


class Almsgiving:
    """杂货店爷爷 never charges his own bar (max AP 0) but hands a point out to
    somebody else every time his turn comes round. The pick is a `turn_choice`:
    it rides along with his order instead of costing his action."""

    describe = ("Gains no AP himself. When his turn begins, one ally you choose gains "
                "1 AP — free, and he still moves and attacks as normal.")
    CHOICE = "handout"

    def turn_choice(self, match, owner):
        opts = [
            e.id for e in match.living(owner.side)
            if e is not owner and e.max_ap > 0 and e.ap < e.max_ap
        ]
        return {
            "key": self.CHOICE,
            "name": "接济 Handout",
            "text": "Give one ally 1 AP. Free — it does not use his action.",
            "kind": "ally",
            "options": opts,
        }

    def apply_choice(self, match, owner, key, target_id):
        if key != self.CHOICE:
            return
        tgt = match.entity(target_id)
        if tgt is None or not tgt.alive or tgt.side != owner.side:
            return
        before = tgt.ap
        tgt.gain_ap(1)
        if tgt.ap != before:
            match.log_line(
                f"{match.label(owner)} slips {match.label(tgt)} a little something — "
                f"{tgt.ap}/{tgt.max_ap} AP."
            )


SNAKE_HEAD = "snake_head"
SNAKE_TAIL = "snake_tail"


def _other_half(match, owner, key):
    """The other body of a two-bodied hero, or None once it is gone."""
    return next((e for e in match.living(owner.side)
                 if e.hero.gang == owner.hero.gang and e.key == key), None)


class SerpentBody:
    """蛇帝 is one creature standing on two squares. Whichever half is struck, the
    wound goes to the same 25 points: the tail hands on everything dealt to it and
    the head holds the pool. When the head falls the tail goes with it — there is
    no half a snake — and only the head counts toward defeat, so the pair is one
    hero on the board and one hero in the victory check."""

    describe = ("Head and tail are one 25 HP body across two adjacent squares. A blow "
                "to either wounds the whole snake, and both fall together.")

    @EV.hook(priority=95)
    def on_after_damage(self, match, owner, ev):
        self._mirror(match, owner)

    def on_match_start(self, match, owner, ctx):
        if owner.key == SNAKE_TAIL:
            # One hero, not two: the tail is a body on the board, never a life to take,
            # and every wound and mend it receives belongs to the head.
            owner.flags["counts_for_defeat"] = False
            head = _other_half(match, owner, SNAKE_HEAD)
            if head is not None:
                owner.vars["pool_holder"] = head.id
        self._mirror(match, owner)

    def on_turn_start(self, match, owner, ctx):
        self._mirror(match, owner)

    def on_round_start(self, match, owner, ctx):
        self._mirror(match, owner)

    def on_heal(self, match, owner, ctx):
        self._mirror(match, owner)

    @staticmethod
    def _mirror(match, owner):
        """The tail shows the head's health, so reading either square tells you the
        snake's real state."""
        if owner.key != SNAKE_TAIL:
            return
        head = _other_half(match, owner, SNAKE_HEAD)
        if head is not None:
            owner.max_hp, owner.hp = head.max_hp, head.hp

    def on_death(self, match, owner, ctx):
        """Hung off the tail rather than the head: a dying unit is already marked
        dead before DEATH is emitted, so its own passives never hear it."""
        if owner.key != SNAKE_TAIL or not owner.alive:
            return
        dead = ctx.get("entity")
        if dead is None or dead.key != SNAKE_HEAD or dead.side != owner.side:
            return
        if dead.hero.gang != owner.hero.gang:
            return
        owner.hp = 0
        match.log_line(f"{match.label(owner)} goes limp as the head falls.")
        match.sweep_deaths()

    def move_anchor(self, match, owner):
        """Who this body is placed against, so the client can offer only the squares
        that will actually be legal instead of refusing them after the fact."""
        if owner.key != SNAKE_TAIL:
            return None
        head = _other_half(match, owner, SNAKE_HEAD)
        return None if head is None else {"entity": head.id, "rule": "neighbours"}

    def move_zone(self, match, owner, pending):
        """The tail does not walk — the body follows the head. It may be put on any
        empty square orthogonally beside wherever the head is going, which includes
        the square the head is leaving and the one the tail is already on, since
        both halves are moving at once."""
        if owner.key != SNAKE_TAIL:
            return None
        head = _other_half(match, owner, SNAKE_HEAD)
        if head is None or not head.cells:
            return None
        dest = pending.get(head.id) or head.cell
        # Neither half blocks the other: both are moving in the same instant.
        return [c for c in match.topology.neighbours(dest)
                if match.can_enter(owner, c, ignore=(head,))]


class VenomFangs:
    """蛇帝's head. Anything its bite draws blood from is slowed by a square on its
    next turn, and then the venom is spent. A hero already carrying a dose cannot
    take a second — no stacking and no refreshing — so a fresh victim is always
    worth more than the same one twice.

    It also notes what it bit this turn, which is what the tail closes on."""

    describe = ("Its bite leaves venom: the hero it wounds moves one square less on "
                "its next turn, and then it wears off. Only a hero not already "
                "carrying a dose can be given one.")
    SQUARES = 1
    TAG = "venom"

    def on_turn_start(self, match, owner, ctx):
        if ctx.get("entity") is owner:
            owner.vars["bit_this_turn"] = set()

    @EV.hook(priority=60)
    def on_after_damage(self, match, owner, ev):
        if ev.source is not owner or ev.category != DMG.NORMAL_ATTACK or ev.amount <= 0:
            return
        if ev.target.side == owner.side:
            return
        owner.vars.setdefault("bit_this_turn", set()).add(ev.target.id)
        if ev.target.vars.get("rooted_tag") == self.TAG:
            return      # 只有未中毒的才能中毒 — no second dose, no refresh
        match.root(ev.target, squares=self.SQUARES, tag=self.TAG)
        match.log_line(
            f"{match.label(ev.target)} is envenomed — a square slower on its next turn."
        )


class PincerStrike:
    """蛇帝's tail. Where the head has already drawn blood this turn, the tail's blow
    lands 1 harder — the pincer closing on one victim. The head always acts first,
    so the tail is always the half that completes it."""

    describe = ("Strikes from 3 squares away. Anything the head already bit this turn "
                "takes 1 more from the tail.")
    BONUS = 1

    @EV.hook(priority=15)
    def on_before_damage(self, match, owner, ev):
        if ev.source is not owner or ev.cancelled or ev.amount <= 0:
            return
        head = _other_half(match, owner, SNAKE_HEAD)
        if head is None or ev.target.id not in head.vars.get("bit_this_turn", set()):
            return
        ev.amount += self.BONUS


def read_the_omen(match, seer, target):
    """Lay the prophecy on one enemy. What it does grows with the stars already
    read: a mark it can never shake, then its feet, then a quarter of its life.
    The mark and the binding are the engine's own — a `vulnerable` stack and a
    hold — so nothing here is special-cased anywhere else."""
    if target is None or not target.alive or target.side == seer.side:
        return
    stars = seer.vars.get("stars", 0)
    # The last reading lifts: whoever it named is let go, mark and all but the 增伤,
    # which is permanent like every other one.
    old = match.entity(seer.vars.get("prediction")) if seer.vars.get("prediction") else None
    if old is not None and old.vars.get("bound_by") == seer.id:
        old.vars["bound_by"] = None
    seer.vars["prediction"] = target.id
    seer.vars["reading_due"] = False
    target.vars["vulnerable"] = target.vars.get("vulnerable", 0) + 1
    parts = ["1 增伤"]
    if stars >= 1:
        target.vars["bound_by"] = seer.id
        parts.append("held where it stands")
    if stars >= 2:
        # 血量失去 — a loss, not a blow: no ward, guard or mark touches it, and it
        # can never be the thing that kills (a quarter is never the whole).
        lost = target.max_hp // 4
        if lost:
            target.hp = max(1, target.hp - lost)
            parts.append(f"{lost} life torn out of it")
    name = StarSign.OMENS[min(stars, 2)]
    match.log_line(
        f"{match.label(seer)} reads {name} over {match.label(target)} — "
        + ", ".join(parts) + "."
    )


class Prophecy(Ability):
    """占星师's opening reading. Every later one comes free when an enemy falls."""

    key = "prophecy"
    name = "预言 Prophecy"
    ap_cost = 0
    use_limit = 1
    opening = True
    targeting = {"kind": "unit"}
    blurb = ("Before the first exchange, name the enemy you expect to fall first. "
             "Name it right and you read a star; every star makes the next prophecy "
             "worse for whoever it lands on.")

    def side_effects(self, match, actor, params):
        read_the_omen(match, actor, match.entity(params.get("target")))


class StarSign:
    """占星师. Names the enemy it expects to fall next. If that one does fall, it
    reads another star and the next prophecy bites harder. A wrong call costs
    nothing — the stars only ever go up."""

    describe = ("Names the enemy it expects to fall next, at the start and again "
                "whenever an enemy dies. Right calls earn stars: 0 — the named hero "
                "takes 1 more from everything. 1 — it is also held where it stands. "
                "2+ — and a quarter of its maximum life is torn out at once.")
    OMENS = ["疑云 Doubt", "凶兆 Ill Omen", "大祸 Catastrophe"]

    def on_death(self, match, owner, ctx):
        dead = ctx.get("entity")
        if dead is None or dead.side == owner.side or not owner.alive:
            return
        if owner.vars.get("prediction") == dead.id:
            owner.vars["stars"] = owner.vars.get("stars", 0) + 1
            match.log_line(
                f"{match.label(owner)} called it — {match.label(dead)} falls, and "
                f"a {owner.vars['stars']}{'st' if owner.vars['stars'] == 1 else 'th'} "
                f"star is read."
            )
        # Any enemy falling calls for a new reading, but the old one is left standing
        # until that reading is made: two heroes dying in the same instant are both
        # measured against it, whichever order the board sweeps them in.
        owner.vars["reading_due"] = True

    def followup(self, match, owner, ctx):
        """Offered the moment the board settles with no prophecy standing."""
        if ctx.get("entity") is not owner or not owner.alive:
            return None
        if owner.vars.get("prediction") and not owner.vars.get("reading_due"):
            return None
        foes = [e for e in match.living(other_side(owner.side))
                if e.flags["counts_for_defeat"] and e.flags["targetable"]]
        if not foes:
            return None
        stars = owner.vars.get("stars", 0)
        return {
            "key": "prophecy",
            "name": self.OMENS[min(stars, 2)],
            "text": f"{stars} star{'' if stars == 1 else 's'} read. Name the enemy you "
                    f"expect to fall next.",
            "kind": "unit",
            "options": [e.id for e in foes],
        }

    def apply_followup(self, match, owner, key, choice):
        if key == "prophecy" and choice is not None:
            read_the_omen(match, owner, match.entity(choice))

    def status(self, match, owner):
        stars = owner.vars.get("stars", 0)
        tgt = match.entity(owner.vars.get("prediction")) if owner.vars.get("prediction") else None
        return {
            "key": "stars", "badge": f"星{stars}", "label": "星标记 STARS",
            "text": (f"{stars} star{'' if stars == 1 else 's'} read — next prophecy is "
                     f"{self.OMENS[min(stars, 2)]}."
                     + (f" Watching {tgt.name}." if tgt is not None and tgt.alive
                        else " Nobody named.")),
        }


class TakeThePlague(Ability):
    """鸟嘴医生 puts itself on the board once everyone else is down and in view. The
    opening phase is exactly that moment — after both forces are locked and visible,
    before a single exchange — so this needs no timing of its own."""

    key = "take_the_plague"
    name = "瘟疫 Plague"
    ap_cost = 0
    use_limit = 1
    opening = True
    targeting = {"kind": "any_cell"}
    blurb = ("Once both forces are down and in view, step onto any empty square on "
             "the board. It is infected the moment you arrive.")

    def cells(self, match, actor, origin=None):
        """Any square it may stand on — it may open inside the enemy formation, but
        not on ground shut against it."""
        return [c for c in match.topology.all_cells() if match.can_enter(actor, c)]

    def side_effects(self, match, actor, params):
        cell = tuple(params["cell"])
        if not match.can_enter(actor, cell):
            # Somebody took it while the order was sealed: walk in somewhere else.
            cell = next(iter(self.cells(match, actor)), None)
            if cell is None:
                return
        actor.set_cell(cell)
        actor.flags.update(blocks_movement=True, targetable=True)
        match.board.add_effect(cell, BOARD.Infection(actor.side, match.round))
        match.bus.emit(EV.AFTER_MOVE, {"entity": actor, "from": None, "to": cell})
        match.log_line(
            f"{match.label(actor)} walks in where it likes, and the ground it stands "
            f"on turns bad."
        )


class Absolution:
    """教皇. Nothing dies to a blow or a spell while it stands, if it does not want
    that death — but mercy is never free: whoever was denied the kill is sharpened
    for the rest of the match, and picks its own reward.

    Either side's heroes can be spared. Sparing one of theirs feeds *your* attacker,
    so the mercy is also a way to grow your own force.

    The engine owns the whole thing — a killing blow pauses resolution and asks. All
    this passive does is say that its owner is the kind of hero who can be asked."""

    describe = ("When any hero is about to fall to a normal attack or an active "
                "ability, it may step in front: that hero takes nothing at all, and "
                "whoever swung permanently gains one of +1 attack, +1 grid or +1 "
                "range, chosen by whoever controls it. Seven times a match, and it "
                "cannot save itself.")
    saves_deaths = True
    SAVE_LIMIT = 7

    def status(self, match, owner):
        left = self.SAVE_LIMIT - owner.vars.get("saves_used", 0)
        return {
            "key": "absolution", "badge": f"赦{left}", "label": "赦免 ABSOLUTION",
            "text": (f"{left} of {self.SAVE_LIMIT} mercies left — it can still step in "
                     f"front of that many killing blows."
                     if left else "Every mercy spent. Nothing is safe now."),
        }


class PlagueBearer:
    """鸟嘴医生. It is not deployed with the rest of the force — it waits until both
    sides are set out in the open, then puts itself wherever it pleases and leaves
    the plague under its own feet.

    The plague is ground, not an attack: everything the board owns lives in
    board.py, and nothing here knows how it spreads."""

    describe = ("Deploys after both forces are down and visible, on any empty square. "
                "The square it lands on is infected: anyone starting a turn on infected "
                "ground loses 2, either side's, and nothing softens it. Infected ground "
                "creeps to the four squares beside it at the start of every round. It "
                "alone is immune.")

    def on_match_start(self, match, owner, ctx):
        # Off the board until it chooses its square, like a hero with no body yet.
        owner.vars["plague_immune"] = True
        owner.cells = set()
        owner.flags.update(blocks_movement=False, targetable=False)

    def status(self, match, owner):
        # No badge once it is down: its own immunity is permanent and never in
        # question, so saying so every turn is noise. The only thing worth a note is
        # the moment before it has picked its square, when it is nowhere at all.
        if owner.cells:
            return None
        return {
            "key": "waiting", "badge": "疫", "label": "瘟疫 UNPLACED",
            "text": "Not on the board yet — it walks in once both forces are set.",
        }


class PaintStroke:
    """画师. Every blow it takes is a chance to blunt the hand that struck it, and
    every blow it lands is one more stroke of its own. Both are offered once the
    exchange has settled — taking one is a choice, because a charge spent on a
    training dummy is one you cannot spend on the hero that matters.

    Three of each, tracked apart. Both are ordinary modifiers on the stack, so
    nothing in the damage code knows this hero exists."""

    describe = ("When it is damaged, it may take 1 attack off whoever struck it. When "
                "it deals damage, it may add 1 to its own. Three of each per match, "
                "and it is asked each time.")
    LIMIT = 3
    BLUNT = "paint_blunt"
    SHARPEN = "paint_sharpen"

    # Late, so only a blow that really landed counts either way.
    @EV.hook(priority=65)
    def on_after_damage(self, match, owner, ev):
        if ev.amount <= 0:
            return
        if ev.target is owner and ev.source is not None and ev.source.side != owner.side:
            if owner.vars.get("blunts_used", 0) < self.LIMIT:
                owner.vars["blunt_who"] = ev.source.id
        if ev.source is owner and ev.target.side != owner.side:
            if owner.vars.get("sharpens_used", 0) < self.LIMIT:
                owner.vars["sharpen_due"] = True

    def followup(self, match, owner, ctx):
        if ctx.get("entity") is not owner or not owner.alive:
            return None
        out = []
        who = match.entity(owner.vars.get("blunt_who")) if owner.vars.get("blunt_who") else None
        if who is not None and who.alive and owner.vars.get("blunts_used", 0) < self.LIMIT:
            left = self.LIMIT - owner.vars.get("blunts_used", 0)
            out.append({
                "key": self.BLUNT,
                "name": "褪色 Fade",
                "text": f"{who.name} drew your blood. Take 1 off its attack? "
                        f"({left} left, and it is now {who.atk}.)",
                "kind": "confirm",
                "optional": True,
            })
        if owner.vars.get("sharpen_due") and owner.vars.get("sharpens_used", 0) < self.LIMIT:
            left = self.LIMIT - owner.vars.get("sharpens_used", 0)
            out.append({
                "key": self.SHARPEN,
                "name": "落笔 Stroke",
                "text": f"Your blow landed. Add 1 to your own attack? "
                        f"({left} left, and it is now {owner.atk}.)",
                "kind": "confirm",
                "optional": True,
            })
        return out

    def apply_followup(self, match, owner, key, choice):
        if key == self.BLUNT:
            who = match.entity(owner.vars.get("blunt_who")) if owner.vars.get("blunt_who") else None
            owner.vars["blunt_who"] = None          # the moment passes either way
            if choice and who is not None and who.alive:
                owner.vars["blunts_used"] = owner.vars.get("blunts_used", 0) + 1
                who.add_modifier(Modifier("atk", "add", -1, source=self))
                match.log_line(
                    f"{match.label(owner)} paints {match.label(who)} thinner — "
                    f"Atk now {who.atk}."
                )
        elif key == self.SHARPEN:
            owner.vars["sharpen_due"] = False
            if choice:
                owner.vars["sharpens_used"] = owner.vars.get("sharpens_used", 0) + 1
                owner.add_modifier(Modifier("atk", "add", 1, source=self))
                match.log_line(
                    f"{match.label(owner)} lays down another stroke — Atk now {owner.atk}."
                )

    def status(self, match, owner):
        used = (owner.vars.get("blunts_used", 0), owner.vars.get("sharpens_used", 0))
        if not any(used):
            return None
        return {
            "key": "brush", "badge": f"画{used[0]}/{used[1]}", "label": "画师 BRUSHWORK",
            "text": f"{used[0]} of {self.LIMIT} enemies painted thinner · "
                    f"{used[1]} of {self.LIMIT} strokes on itself.",
        }




class ArmsDealer:
    """军火商人. While it is standing, its whole side can bank AP without limit; once
    a round it charges one of them for a weapon and pockets the fee.

    Nothing here is new machinery. The open bar is a `set` modifier on the stack,
    granted by the dealer itself — so it lapses the moment the dealer falls, and any
    savings above a hero's own ceiling go with it. A weapon is a stat block a unit
    carries instead of its own, which the engine already reads through one hook."""

    describe = ("While it stands, every one of your heroes can bank AP without limit. "
                "Once a round, on its own turn, it charges one of them for a weapon "
                "and keeps the fee. Its own shot can be fed AP: every point spent is "
                "a point of damage. It does not sell to itself.")
    OPEN_BAR = 99
    CHOICE = "armory"

    def _open_the_bar(self, match, owner):
        """Who has a bar and who does not, settled fresh each round rather than only
        added to. Scenery never gets one (世界树), nor does anything off the board
        (探险家 before its island lands) — and both of those can change under us."""
        if not owner.alive:
            return
        welcome = {e.id for e in match.on_map(owner.side) if e.flags["takes_turns"]}
        for e in match.living(owner.side):
            mine = [m for m in e.modifiers if m.source is owner and m.stat == "max_ap"]
            if e.id in welcome and not mine:
                e.add_modifier(Modifier("max_ap", "set", self.OPEN_BAR, source=owner))
            elif e.id not in welcome and mine:
                e.modifiers = [m for m in e.modifiers if m not in mine]
                e.ap = min(e.ap, e.max_ap)

    def on_match_start(self, match, owner, ctx):
        self._open_the_bar(match, owner)

    def on_round_start(self, match, owner, ctx):
        self._open_the_bar(match, owner)

    def turn_choice(self, match, owner):
        """One sale a round, riding along with its own turn — it still moves and
        shoots. Every affordable pairing of hero and weapon is offered."""
        opts = []
        for e in match.on_map(owner.side):
            if e is owner or not e.flags["counts_for_defeat"]:
                continue      # it does not sell to itself
            for w in ARMS:
                if e.ap >= w["ap"]:
                    opts.append({"value": f"{w['key']}:{e.id}",
                                 "label": f"{e.name} ← {w['name']}",
                                 "note": f"{w['ap']} AP · {w['text']}"})
        return {
            "key": self.CHOICE,
            "name": "武器库 Armory",
            "text": "Charge one of your heroes for a weapon. The fee is yours to keep.",
            "kind": "armory",
            "optional": True,
            "options": [o["value"] for o in opts],
            "wares": opts,
        }

    def apply_choice(self, match, owner, key, value):
        if key != self.CHOICE or not value:
            return
        wkey, _, eid = str(value).partition(":")
        buyer, w = match.entity(int(eid)) if eid.isdigit() else None, ARMS_BY_KEY.get(wkey)
        if buyer is None or w is None or not buyer.alive or buyer.side != owner.side:
            return
        if buyer is owner or buyer.ap < w["ap"]:
            return
        buyer.ap -= w["ap"]
        owner.gain_ap(w["ap"])
        buyer.vars["arms"] = dict(w)
        match.log_line(
            f"{match.label(owner)} sells {match.label(buyer)} a {w['name']} for "
            f"{w['ap']} AP."
        )

    def status(self, match, owner):
        return {
            "key": "open_bar", "badge": "军", "label": "军火商人 OPEN BAR",
            "text": "Every one of your heroes can bank AP without limit while it "
                    "stands. Its own shot takes AP as fuel, a point for a point.",
        }


class WorldTree:
    """世界树. It stands in the middle of the board and never acts. Its own side may
    strike it with an ordinary attack — no blow lands, because the tree has nothing
    to wound; what counts is how many times it has been struck.

    Nothing here is friendly fire. The tree simply offers itself as a target to its
    own side, and the attack resolves into a tally instead of damage."""

    describe = ("Stands in the middle of the board and never takes a turn. Your own "
                "heroes may attack it. First strike: 长冬 — every enemy is a square "
                "slower for the rest of the round. Third: 魔狼, 巨蟒 and 邪龙 take "
                "three enemies for 1, 2 and 3, the same hero allowed more than once. "
                "Fifth: it falls, 洛基 rises on a square of your choosing, and the "
                "ground under everything the beasts bit catches fire.")
    struck_by_allies = True
    BEASTS = [("魔狼 Fenrir", 1), ("巨蟒 Jörmungandr", 2), ("邪龙 Níðhöggr", 3)]

    @EV.hook(priority=10)
    def on_match_start(self, match, owner, ctx):
        if ctx.get("entity") not in (None, owner):
            return
        # Scenery, not a life: it blocks the middle, the enemy cannot touch it, and
        # losing it never loses the game. Settled early, because these say what the
        # hero *is* — anything that reads them must not race the answer.
        owner.flags.update(takes_turns=False, counts_for_defeat=False,
                           blocks_movement=True, targetable=False)

    def on_struck(self, match, owner, attacker):
        n = owner.vars.get("struck", 0) + 1
        owner.vars["struck"] = n
        match.log_line(f"{match.label(attacker)} strikes 世界树 — {n} now.")
        if n == 1:
            self._long_winter(match, owner)
        elif n == 3:
            self._loose_the_beasts(match, owner)
        elif n == 5:
            self._fell(match, owner)

    def _long_winter(self, match, owner):
        foes = [e for e in match.on_map(other_side(owner.side)) if e.move_allowance > 0]
        for e in foes:
            e.add_modifier(Modifier("move", "add", -1, source=self,
                                    duration=UNTIL_ROUND_END))
        match.log_line(
            f"长冬 settles — every enemy is a square slower for the rest of round "
            f"{match.round}."
        )

    def _loose_the_beasts(self, match, owner):
        foes = [e.id for e in match.living(other_side(owner.side))
                if e.flags["targetable"]]
        if not foes:
            return
        for name, amount in self.BEASTS:
            match.interrupts.append({
                "kind": "pick", "key": "beast", "side": owner.side,
                "tree": owner.id, "beast": name, "amount": amount,
                "options": list(foes),
                "option_kind": "unit",
                "name": name,
                "text": f"{name} is loose. Name the hero it takes for {amount}. "
                        f"The same one may be named more than once.",
            })

    def _fell(self, match, owner):
        match.log_line("世界树 comes down.")
        # The ground under everything the beasts bit catches, once per square.
        lit = set()
        for eid in owner.vars.get("bitten", []):
            e = match.entity(eid)
            if e is None or not e.alive or not e.cells or e.cell in lit:
                continue
            lit.add(e.cell)
            match.board.add_burning(e.cell, owner.side)
        if lit:
            match.log_line(f"The wreck takes light — {len(lit)} square(s) burning.")
        owner.alive = False
        owner.cells = set()
        free = [list(c) for c in match.topology.all_cells()
                if match.can_enter(owner, c)]
        match.interrupts.append({
            "kind": "pick", "key": "loki", "side": owner.side, "hero": "loki",
            "options": free,
            "option_kind": "cell",
            "name": "洛基降临 Loki Arrives",
            "text": "The tree is down. Choose any empty square for 洛基 to step into.",
        })

    def status(self, match, owner):
        n = owner.vars.get("struck", 0)
        nxt = {0: "长冬 at the first strike", 1: "the beasts at the third",
               2: "the beasts at the third", 3: "it falls at the fifth",
               4: "it falls at the fifth"}.get(n, "")
        return {
            "key": "world_tree", "badge": f"树{n}", "label": "世界树 STRUCK",
            "text": f"Struck {n} time{'' if n == 1 else 's'}."
                    + (f" Next: {nxt}." if nxt else ""),
        }


class FourBeasts:
    """四圣兽. Four squares carry a blessing, each given once and kept for good. The
    squares are read off the topology rather than written down, so a board of
    another shape still has all four in the right places.

    Two are fixed to the board (the middle of the top and bottom rows) and two are
    side-relative (the middle of your own back line, and of theirs) — so the Tiger
    is always the hardest of the four to reach.

    Each shrine is three squares across the middle of its edge, not one. Reaching
    any of the three wakes that beast; the other two do nothing more, because a
    shrine is one blessing however much of it you walk over."""

    describe = ("Four shrines bless it, once each and for good — three squares apiece, "
                "across the middle of their edge. 青龙, your own back line: heal 1 at "
                "the start of every turn. 玄武, the top row: heal 3, +3 maximum "
                "health, and −1 to all damage taken. 朱雀, the bottom row: its attacks "
                "set the ground under the target alight. 白虎, the enemy back line: "
                "+2 attack, and it strikes two enemies instead of one.")
    NAMES = {"dragon": "青龙 Dragon", "turtle": "玄武 Turtle",
             "phoenix": "朱雀 Phoenix", "tiger": "白虎 Tiger"}
    WIDTH = 3            # squares per shrine, centred on its edge

    @staticmethod
    def _span(centre, limit, width):
        """`width` squares centred on `centre`, clipped to 1..limit — so a board
        too short for the full span still gets as much of it as fits."""
        half = width // 2
        lo = max(1, min(centre - half, limit - width + 1))
        return list(range(lo, min(limit, lo + width - 1) + 1))

    @classmethod
    def shrines(cls, match, owner):
        """cell -> beast, from this hero's point of view. Each shrine is a run of
        squares across the middle of its own edge: the top and bottom ones run along
        their row, the two back lines run down their column."""
        t = match.topology
        mid_row, mid_col = (t.rows + 1) // 2, (t.cols + 1) // 2
        own = sorted({c for c, _ in t.deployment_zone(owner.side)})
        foe = sorted({c for c, _ in t.deployment_zone(other_side(owner.side))})
        cols = cls._span(mid_col, t.cols, cls.WIDTH)
        rows = cls._span(mid_row, t.rows, cls.WIDTH)
        out = {}
        for c in cols:
            out[(c, 1)] = "turtle"
            out[(c, t.rows)] = "phoenix"
        for r in rows:
            out[(own[len(own) // 2], r)] = "dragon"
            out[(foe[len(foe) // 2], r)] = "tiger"
        return out

    def held(self, owner):
        return owner.vars.setdefault("beasts", set())

    def marks(self, match, owner):
        """The shrines, for the board to draw. Twelve squares is too many to expect
        anyone to work out from the hero's description, and a shrine already woken
        has to read differently from one still worth walking to."""
        held = self.held(owner)
        return [{"cell": list(cell), "kind": "shrine", "key": beast,
                 "name": self.NAMES[beast], "glyph": self.NAMES[beast][0],
                 "owner": owner.side, "spent": beast in held}
                for cell, beast in self.shrines(match, owner).items()]

    # Standing on any one square of a shrine is enough, so a hero deployed straight
    # onto its own back line has the Dragon from the first exchange.
    def on_match_start(self, match, owner, ctx):
        self._touch(match, owner)

    def on_after_move(self, match, owner, ctx):
        if ctx.get("entity") is owner:
            self._touch(match, owner)

    def _touch(self, match, owner):
        if not owner.alive or not owner.cells:
            return
        beast = self.shrines(match, owner).get(owner.cell)
        if beast is None or beast in self.held(owner):
            return
        self.held(owner).add(beast)
        getattr(self, "_" + beast)(match, owner)

    def _dragon(self, match, owner):
        match.log_line(f"{match.label(owner)} wakes 青龙 — it mends 1 every turn from here.")

    def _turtle(self, match, owner):
        owner.max_hp += 3
        healed = DMG.heal(match, owner, 3, source=owner)
        owner.vars["damage_reduction"] = owner.vars.get("damage_reduction", 0) + 1
        match.log_line(
            f"{match.label(owner)} wakes 玄武 — {owner.hp}/{owner.max_hp} health "
            f"(+{healed}) and 1 less from every hit."
        )

    def _phoenix(self, match, owner):
        match.log_line(f"{match.label(owner)} wakes 朱雀 — what it strikes now burns.")

    def _tiger(self, match, owner):
        owner.add_modifier(Modifier("atk", "add", 2, source=self))
        owner.add_modifier(Modifier("targets", "add", 1, source=self))
        match.log_line(
            f"{match.label(owner)} wakes 白虎 — Atk {owner.atk}, and it takes "
            f"{owner.targets} at a time."
        )

    def on_turn_start(self, match, owner, ctx):
        if ctx.get("entity") is not owner or "dragon" not in self.held(owner):
            return
        if DMG.heal(match, owner, 1, source=owner):
            match.log_line(f"{match.label(owner)} mends — 青龙 keeps it whole.", quiet=True)

    # Late, so only a blow that actually landed lights the ground.
    @EV.hook(priority=60)
    def on_after_damage(self, match, owner, ev):
        if ev.source is not owner or ev.category != DMG.NORMAL_ATTACK or ev.amount <= 0:
            return
        if "phoenix" not in self.held(owner) or ev.target.side == owner.side:
            return
        if not ev.target.cells:
            return
        tile = match.board.add_burning(ev.target.cell, owner.side)
        match.log_line(
            f"朱雀's fire takes the ground under {match.label(ev.target)} "
            f"(now x{tile.stacks}, {tile.damage} fire)."
        )

    def status(self, match, owner):
        got = self.held(owner)
        if not got:
            return None
        return {
            "key": "beasts", "badge": f"圣{len(got)}", "label": "四圣兽 AWAKENED",
            "text": " · ".join(self.NAMES[b] for b in
                               ("dragon", "turtle", "phoenix", "tiger") if b in got),
        }


class FirstBlood:
    """猎人. The first kill it lands opens its eye: its reach lengthens and its net
    catches two enemies instead of one, for the rest of the match. Once only — a
    second kill changes nothing.

    Both halves are ordinary modifiers on the stack, so nothing in the attack code
    knows about this hero: `rng` and `targets` are read live like any other stat."""

    describe = ("The first time it kills an enemy: +4 attack range, and its normal "
                "attack hits two enemies in its net instead of one. Once per match.")
    RNG = 4
    EXTRA_TARGETS = 1

    # After the flat cuts and caps, so it only counts a blow that really landed.
    @EV.hook(priority=70)
    def on_after_damage(self, match, owner, ev):
        if ev.source is not owner or owner.vars.get("first_blood"):
            return
        if ev.target.side == owner.side or ev.target.hp > 0:
            return      # runs before the dead are swept, so hp 0 is the killing blow
        owner.vars["first_blood"] = True
        owner.add_modifier(Modifier("rng", "add", self.RNG, source=self))
        owner.add_modifier(Modifier("targets", "add", self.EXTRA_TARGETS, source=self))
        match.log_line(
            f"{match.label(owner)} takes its first kill — reach now {owner.rng}, "
            f"and its net catches {owner.targets} at once."
        )

    def status(self, match, owner):
        if not owner.vars.get("first_blood"):
            return None
        return {
            "key": "blooded", "badge": "猎", "label": "首杀 BLOODED",
            "text": f"Reach {owner.rng}, and its attack hits {owner.targets} enemies "
                    f"in its net instead of one.",
        }


class GangTactics:
    """Display-only: the ordering rule itself lives in the turn loop (a gang
    commits one order per living member and they resolve in the chosen order)."""

    describe = ("Gang turn: every living goblin acts, in an order you choose — "
                "the whole gang costs one turn.")


class StoneHide:
    """石像鬼 is carved stone: blades and bullets chip it for 1 no matter how hard
    they hit. Abilities and burning ground are unaffected — the way through it is
    magic, not muscle."""

    describe = ("Any normal attack deals at most 1 damage to it. Abilities and burning "
                "ground land in full.")
    CAP = 1

    # Sits after the flat reductions (40) so it overrides them rather than stacking
    # with them, and before the gunslinger's halving (90).
    @EV.hook(priority=70)
    def on_before_damage(self, match, owner, ev):
        if ev.target is not owner or ev.cancelled:
            return
        if ev.category == DMG.NORMAL_ATTACK:
            # A cap, not a flat value: something that already softened the blow to
            # nothing must not be undone by the stone.
            ev.amount = min(ev.amount, self.CAP)


class MountainGuard:
    """山神 shelters his line. Allied units sharing his column take 2 less from
    every hit (all damage, fire included). He himself is not covered. Positional —
    re-checked at damage time, so it follows units as they move."""

    describe = "Allied units in his column take 2 less from every hit. He is not covered."

    @EV.hook(priority=20)
    def on_before_damage(self, match, owner, ev):
        if ev.cancelled or ev.amount <= 0:
            return
        tgt = ev.target
        if tgt is owner or tgt.side != owner.side:
            return
        if not owner.alive or not owner.cells or not tgt.cells:
            return
        column = set(match.topology.column(owner.cell[0]))
        if tgt.cells & column:
            ev.amount = max(0, ev.amount - 2)


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
    # A draft card that deploys several bodies (哥布林团伙): the member hero keys,
    # duplicates included. Squad cards never become entities themselves.
    squad: list = None
    # Set on the members: which squad card they belong to. Members sharing a gang
    # key on the same side act together in one turn.
    gang: str = None
    # Where this body falls in its squad's turn. None everywhere means the player
    # picks the order (哥布林团伙); ranked members must act in ascending order
    # (蛇帝's head leads, the tail follows).
    gang_rank: int = None
    # A hero that is not deployed by its owner but stands somewhere the board
    # decides (世界树 at the middle). It takes a draft slot and no zone square.
    deploys: str = None
    blurb: str = ""


CELL = "cell_locked"
UNIT = "unit_locked"
LINE = "line_locked"  # 狙击手: the first enemy down one lane of your row or column
AREA = "area_locked"  # 猛犸: every enemy inside a shape centred on the hero
CONE = "cone_locked"  # 男枪: the three-cell arc one step away, in a chosen direction
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

# 军火商人's stock. Each is an ordinary attack spec — a mode, a net, a reach and a
# number — so a hero holding one attacks exactly as a hero built that way would.
ARMS = [
    {"key": "rifle", "name": "步枪 Rifle", "ap": 2, "atk": 3,
     "attack": {"mode": CELL, "cells": 2, "range": 2},
     "text": "3 damage · 2 squares at range 2."},
    {"key": "cannon", "name": "重炮 Heavy Cannon", "ap": 4, "atk": 4,
     "attack": {"mode": CELL, "cells": 3, "range": 5},
     "text": "4 damage · 3 squares at range 5."},
    {"key": "nuke", "name": "核爆 Nuke", "ap": 5, "atk": 6, "once": True,
     "attack": {"mode": AREA, "shape": "board", "range": None},
     "text": "6 damage to every enemy on the board. Firing it costs that hero its "
             "attack for good."},
]
ARMS_BY_KEY = {w["key"]: w for w in ARMS}


class WeaponMaster:
    describe = "Each turn, choose a weapon: it sets that turn's attack and stance."

    def on_turn_start(self, match, owner, ctx):
        # A new turn — last turn's stance expires "before this turn begins".
        if ctx.get("entity") is owner:
            owner.vars["stance_dr"] = 0
            owner.vars["ability_immune"] = False

# --------------------------------------------------------- 探险家's 岛屿
#
# The island is not a hero rule: it is a piece of board. `Topology.regions` cuts
# four squares out of the map into a sub-map of their own — nothing there is a
# neighbour of anything here, no distance reaches it, and `all_cells` does not
# list it, so no ability can name a square on it without asking for it by name.
# Joining it back on at round four is one call and the board is whole again.


def t_tetromino(cells):
    """True if these four squares form a T: a straight bar of three with the
    fourth attached to the middle of the bar. All four rotations count."""
    pool = {tuple(c) for c in cells}
    if len(pool) != 4:
        return False
    for stem in pool:
        bar = sorted(pool - {stem})
        cols = [c for c, _ in bar]
        rows = [r for _, r in bar]
        if len(set(rows)) == 1 and cols == list(range(cols[0], cols[0] + 3)):
            middle = (cols[1], rows[0])
        elif len(set(cols)) == 1 and rows == list(range(rows[0], rows[0] + 3)):
            middle = (cols[0], rows[1])
        else:
            continue
        if abs(stem[0] - middle[0]) + abs(stem[1] - middle[1]) == 1:
            return True
    return False


def island_region(side):
    return f"island:{side}"


class ChooseIsland(Ability):
    """Chosen before anybody deploys, like 工匠's doors, so both sides lay their
    forces out already knowing which four squares are gone."""

    key = "choose_island"
    name = "勘定岛屿 Chart the Island"
    ap_cost = 0
    prebuild = True
    targeting = {"kind": "cells", "count": 4}
    blurb = ("Before anyone deploys, mark four squares in a T. They leave the board "
             "entirely until round four — nothing can enter them, and nothing on "
             "them can be touched. Both sides see it.")

    def validate_build(self, match, side, params):
        cells = [tuple(c) for c in (params.get("cells") or [])]
        if len(cells) != 4:
            return "Mark four squares."
        if len(set(cells)) != 4:
            return "Mark four different squares."
        for c in cells:
            if not match.topology.in_bounds(c):
                return "That square is not on the board."
            if match.topology.region(c) is not None:
                return "That square is already part of an island."
            if any(c in (a, b) for a, b, _ in match.topology.links):
                return "A door already opens onto that square."
        mid = ((match.topology.cols + 1) // 2, (match.topology.rows + 1) // 2)
        if mid in cells and match.board_places("centre"):
            return "世界树 stands in the middle — the island cannot take that square."
        if not t_tetromino(cells):
            return "The four squares must form a T."
        return None

    def build_effects(self, match, side, params):
        cells = [tuple(c) for c in params["cells"]]
        match.topology.detach(cells, island_region(side))
        match.islands[side] = {"cells": cells, "stand": cells[0],
                               "region": island_region(side)}
        match.log_line(
            f"{'Left' if side == LEFT else 'Right'} 探险家 charts an island — four "
            f"squares leave the board."
        )


class MakeLandfall(Ability):
    """The second half of the same decision: which of the four the explorer itself
    starts on. Split into its own build task so each is a plain pick."""

    key = "make_landfall"
    name = "登岛 Make Landfall"
    ap_cost = 0
    prebuild = True
    targeting = {"kind": "any_cell"}
    blurb = "Choose which square of your island you start on. The other three are what you dig."

    def build_cells(self, match, side):
        isl = match.islands.get(side) or {}
        return list(isl.get("cells") or [])

    def validate_build(self, match, side, params):
        cell = params.get("cell")
        if not cell:
            return "Choose a square."
        if tuple(cell) not in self.build_cells(match, side):
            return "Choose one of your island's four squares."
        return None

    def build_effects(self, match, side, params):
        match.islands[side]["stand"] = tuple(params["cell"])


class Dig(Ability):
    """One resource, into one empty square of the island. Every round the island is
    still detached, this is the only thing the explorer may do."""

    ap_cost = 0
    resource = None
    targeting = {"kind": "any_cell"}

    def available(self, match, actor):
        return bool(Explorer.free_cells(match, actor))

    def cells(self, match, actor, origin=None):
        return Explorer.free_cells(match, actor)

    def validate(self, match, actor, params, origin=None):
        cell = tuple(params.get("cell") or ())
        if cell not in Explorer.free_cells(match, actor):
            return "That square is not free island ground."
        return None

    def side_effects(self, match, actor, params):
        cell = tuple(params["cell"])
        actor.vars.setdefault("dug", []).append(self.resource)
        # Which square went to what, so the island can be read at a glance by both
        # seats. Ore leaves nothing behind on its own, and an unrecorded square is
        # indistinguishable from one nobody has touched.
        actor.vars.setdefault("worked", {})[cell] = self.resource
        apply_resource(match, actor, self.resource, cell)


class DigGrapes(Dig):
    key = "dig_grapes"
    name = "葡萄 Grapes"
    resource = "grape"
    blurb = ("Plant a vine. The first friendly hero to step onto it mends 4. The vine "
             "stays where it is for the rest of the match.")


class TrainNatives(Dig):
    key = "train_natives"
    name = "奴隶 Slaves"
    resource = "slave"
    blurb = ("Train a 土著 on that square: a hero of your own whose blow is worth "
             "a sixth of whatever it lands on.")


class MineOre(Dig):
    key = "mine_ore"
    name = "矿物 Ore"
    resource = "mineral"
    blurb = ("Dig ore and beat it into armour: +3 max HP (and the health with it), "
             "+1 attack, +1 range. The square is left as bare as it was found.")


def apply_resource(match, owner, resource, cell):
    """What a dug resource does, wherever it is dug. The island and the three
    squares 全为不同 grants on the main map go through exactly this."""
    if resource == "grape":
        match.board.add_effect(cell, BOARD.GrapeVine(owner.side))
        match.log_line(f"{match.label(owner)} plants a vine.", side=owner.side)
    elif resource == "slave":
        e = match.spawn(BY_KEY["native"], owner.side, cell)
        if match.topology.region(cell) is not None:
            # Trained on ground that is not yet part of the board: it can neither
            # act nor be touched until the island arrives.
            e.flags.update(takes_turns=False, targetable=False)
        match.bus.emit(EV.MATCH_START, {"entity": e})
        match.log_line(f"{match.label(owner)} trains a 土著.", side=owner.side)
    elif resource == "mineral":
        owner.max_hp += MineOre.PLATE
        owner.hp += MineOre.PLATE
        owner.add_modifier(Modifier("atk", "add", 1))
        owner.add_modifier(Modifier("rng", "add", 1))
        match.log_line(f"{match.label(owner)} beats out another plate of armour.",
                       side=owner.side)


MineOre.PLATE = 3


class Explorer:
    """探险家. Spends the first three rounds on four squares that are not part of the
    board, digging one resource a round, and arrives at the top of round four with
    whatever it found. Three of a kind, or one of each, is worth more."""

    describe = ("Charts four squares in a T before anyone deploys; they leave the "
                "board until round four and it stands on them alone, untouchable. "
                "Each of the first three rounds it digs one resource into an empty "
                "island square — a vine, a 土著, or a plate of armour — and does "
                "nothing else. At the top of round four the island is joined back "
                "on. Three of one resource, or one of each, pays a bonus.")

    JOIN_ROUND = 4
    GREAT_PLATE = 6      # 完全矿物装甲, on top of the three ordinary plates
    REVOLT_HP = 2        # 反动, on each 土著 — and the explorer with them

    # --- island bookkeeping, shared with the dig abilities ----------------

    @staticmethod
    def sealed_cells(match, owner):
        """The island's squares while it is still off the board, else nothing."""
        cells = owner.vars.get("island_cells") or []
        return [c for c in cells if match.topology.region(c) is not None]

    @staticmethod
    def free_cells(match, owner):
        """Island ground with nothing standing or planted on it."""
        return [c for c in Explorer.sealed_cells(match, owner)
                if match.occupant(c) is None
                and not match.board.has_kind(c, BOARD.GrapeVine.kind)]

    # --- the match ---------------------------------------------------------

    @EV.hook(priority=10)
    def on_match_start(self, match, owner, ctx):
        if ctx.get("entity") not in (None, owner):
            return
        isl = match.islands.get(owner.side) or {}
        owner.vars["island_cells"] = [tuple(c) for c in (isl.get("cells") or [])]
        owner.vars.setdefault("dug", [])
        # Off the board is off the board: for three rounds nothing can reach it.
        owner.flags["targetable"] = False

    def move_zone(self, match, owner, pending):
        """While the island is off the board the explorer does not walk: the whole
        turn is one spadeful, and its square is dictated to be the one it is on.
        Without this it could commit to a square and dig the same one, and arrive
        on top of what it had just put there."""
        if not self.sealed_cells(match, owner) or not owner.cells:
            return None
        return [owner.cell]

    GLYPHS = {"grape": "葡", "slave": "奴", "mineral": "矿", None: "·"}
    WORKED = {"grape": "葡萄树 a vine planted here",
              "slave": "土著 trained here",
              "mineral": "矿物 dug out here — the ground keeps nothing",
              None: "not dug yet"}

    def marks(self, match, owner):
        """What each island square was used for, while the island is still off the
        board. Both seats see it: the island is public from the moment it is
        charted, and ore leaves nothing on the ground to show for itself. Once the
        island lands, the vines and the 土著 speak for themselves and this stops."""
        cells = self.sealed_cells(match, owner)
        if not cells:
            return []
        worked = owner.vars.get("worked") or {}
        out = []
        for cell in cells:
            res = worked.get(tuple(cell))
            out.append({"cell": list(cell), "kind": "dig", "key": res or "bare",
                        "name": self.WORKED[res], "glyph": self.GLYPHS[res],
                        "owner": owner.side, "spent": res is None})
        return out

    def sole_actions(self, match, owner):
        """While the island is still its own map there is nothing else to do: no
        hold, no attack — the whole turn is one spadeful. If there were somehow no
        ground left to dig, the ordinary menu comes back rather than none at all."""
        if not self.sealed_cells(match, owner):
            return None
        keys = [ab.key for ab in owner.abilities
                if isinstance(ab, Dig) and ab.available(match, owner)]
        return keys or None

    def on_round_start(self, match, owner, ctx):
        if match.round < self.JOIN_ROUND or owner.vars.get("joined"):
            return
        self._join(match, owner)

    def _join(self, match, owner):
        owner.vars["joined"] = True
        region = island_region(owner.side)
        cells = owner.vars.get("island_cells") or []
        match.topology.rejoin(region)
        owner.flags["targetable"] = True
        for e in match.living(owner.side):
            if e.cells and e.cell in cells:
                e.flags.update(takes_turns=True, targetable=True)
        match.log_line("The island runs aground against the mainland.")
        self._bonus(match, owner)

    def _bonus(self, match, owner):
        dug = owner.vars.get("dug") or []
        if len(dug) < 3:
            return
        kinds = set(dug)
        if len(kinds) == 1:
            self._all_same(match, owner, dug[0])
        elif len(kinds) == 3:
            self._all_different(match, owner)

    def _all_same(self, match, owner, resource):
        if resource == "grape":
            # 大葡萄园. Set on the vines themselves rather than on the explorer, so
            # the vineyard outlives whatever happens to the hero that planted it.
            for effs in match.board.effects.values():
                for eff in effs:
                    if eff.kind == BOARD.GrapeVine.kind and eff.owner_side == owner.side:
                        eff.great = True
            match.log_line("大葡萄园 — every vine mends its own at the end of a round.")
        elif resource == "mineral":
            owner.max_hp += self.GREAT_PLATE
            owner.hp += self.GREAT_PLATE
            match.log_line(f"完全矿物装甲 — {match.label(owner)} is sealed in ore.")
        elif resource == "slave":
            natives = [e for e in match.living(owner.side) if e.key == "native"]
            for e in natives:
                e.max_hp += self.REVOLT_HP
                e.hp += self.REVOLT_HP
            match.log_line(
                f"反动 — the 土著 turn on {match.label(owner)}, and are the stronger "
                f"for it."
            )
            # The cost is not damage and nothing can be interposed against it.
            owner.hp = 0
            match.sweep_deaths()

    def _all_different(self, match, owner):
        free = [list(c) for c in match.topology.all_cells()
                if match.can_enter(owner, c)
                and not match.board.has_kind(c, BOARD.GrapeVine.kind)]
        if not free:
            return
        match.log_line("全为不同 — the whole mainland is there to be worked.")
        for res in ("grape", "slave", "mineral"):
            match.interrupts.append({
                "kind": "pick", "key": "dig", "side": owner.side,
                "explorer": owner.id, "resource": res,
                "options": [list(c) for c in free],
                "option_kind": "cell",
                "name": RESOURCE_NAMES[res],
                "text": f"全为不同: choose a square anywhere on the board to "
                        f"{RESOURCE_VERBS[res]}.",
            })


RESOURCE_NAMES = {"grape": "葡萄 Grapes", "slave": "奴隶 Slaves", "mineral": "矿物 Ore"}
RESOURCE_VERBS = {"grape": "plant a vine on", "slave": "train a 土著 on",
                  "mineral": "dig for ore on"}


class NativeStrike:
    """土著 fight with what they are given, so their blow is measured against
    whatever it lands on rather than by any arm of their own."""

    describe = "Its normal attack deals a sixth of the target's maximum health, rounded down."
    SHARE = 6

    def on_before_damage(self, match, owner, ev):
        if ev.source is not owner or ev.cancelled:
            return
        if ev.category != DMG.NORMAL_ATTACK:
            return
        ev.amount = ev.target.max_hp // self.SHARE


ROSTER = [
    HeroDef(
        key="spearman",
        name="枪兵",
        name_en="lancer",
        max_hp=18,
        atk=4,
        move=1,
        max_ap=3,
        attack={"mode": CELL, "cells": 3, "range": 2},
        abilities=[Sweep()],
        blurb="A wall of reach. Sweep hits a 2x5 block and cannot miss.",
    ),
    HeroDef(
        key="paladin",
        name="圣骑士",
        name_en="paladin",
        max_hp=22,
        atk=3,
        move=1,
        max_ap=0,
        attack={"mode": CELL, "cells": 3, "range": 2},
        passives=[DivineAegis],
        blurb="Takes one blow per round; a holy shield turns aside the rest.",
    ),
    HeroDef(
        key="robot",
        name="机器人",
        name_en="robot",
        max_hp=21,
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
        name_en="thunderDragon",
        max_hp=17,
        atk=2,
        move=1,
        max_ap=6,
        attack={"mode": UNIT, "range": None},
        abilities=[Thunderstorm()],
        blurb="Reaches the whole board. Banks three storms.",
    ),
    HeroDef(
        key="fire_mage",
        name="火法师",
        name_en="fireMage",
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
        name_en="gunslinger",
        max_hp=15,
        atk=4,
        move=1,
        max_ap=0,
        attack={"mode": CELL, "cells": 3, "range": 4},
        attacks_per_turn=2,
        halve_from_index=1,
        passives=[TwinGuns],
        blurb="Two nets a turn. The second shot is half strength.",
    ),
    HeroDef(
        key="mars",
        name="马尔斯",
        name_en="mars",
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
        name_en="cannoneer",
        max_hp=16,
        atk=4,
        move=1,
        max_ap=0,
        attack={"mode": CELL, "cells": 4, "range": 7},
        blurb="A wide net from the back line. One long-range shot a turn.",
    ),
    HeroDef(
        key="mountain_god",
        name="山神",
        name_en="mountainGod",
        max_hp=19,
        atk=2,
        move=1,
        max_ap=0,
        attack={"mode": CELL, "cells": 3, "range": 2},
        passives=[MountainGuard],
        blurb="Allies sharing his column take 2 less from every hit.",
    ),
    HeroDef(
        key="tide_goddess",
        name="潮汐女神",
        name_en="tideGoddess",
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
        name_en="bloodMage",
        max_hp=16,
        atk=3,
        move=1,
        max_ap=2,
        attack={"mode": CELL, "cells": 3, "range": 2},
        abilities=[BloodRite()],
        blurb="Once, trades life for lasting power.",
    ),
    HeroDef(
        key="forest_child",
        name="森林之子",
        name_en="forestChild",
        max_hp=17,
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
        name_en="imp",
        max_hp=16,
        atk=2,
        move=1,
        max_ap=3,
        attack={"mode": CELL, "cells": 4, "range": 4},
        abilities=[Ray()],
        blurb="Sears its entire row for 5 — no aim, no escape sideways.",
    ),
    HeroDef(
        key="woodcutter",
        name="樵夫",
        name_en="woodcutter",
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
        name_en="victoryGoddess",
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
        name_en="druid",
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
        name_en="weaponMaster",
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
        name_en="berserker",
        max_hp=22,
        atk=4,
        move=1,
        max_ap=0,
        attack={"mode": CELL, "cells": 2, "range": 2},
        passives=[BattleFury],
        blurb="Wounded and dangerous — +2 attack and +1 range at 11 HP or below.",
    ),
    HeroDef(
        key="fairy",
        name="妖精",
        name_en="fairy",
        max_hp=14,
        atk=2,
        move=1,
        max_ap=0,
        attack={"mode": CELL, "cells": 3, "range": 5},
        passives=[Regrowth],
        blurb="Mends the army — every ally recovers 1 HP at the start of its turn.",
    ),
    HeroDef(
        key="gatekeeper",
        name="门神",
        name_en="gatekeeper",
        max_hp=29,
        atk=3,
        move=1,
        max_ap=0,
        attack={"mode": CELL, "cells": 2, "range": 2},
        blurb="A wall of a hero — 30 HP and a solid swing.",
    ),
    HeroDef(
        key="wind_rider",
        name="御风使",
        name_en="windRider",
        max_hp=19,
        atk=4,
        move=2,
        max_ap=0,
        attack={"mode": CELL, "cells": 3, "range": 2},
        blurb="Covers ground fast — moves 2 cells a turn.",
    ),
    HeroDef(
        key="werewolf",
        name="狼人",
        name_en="werewolf",
        max_hp=19,
        atk=3,
        move=1,
        max_ap=3,
        attack={"mode": CELL, "cells": 3, "range": 3},
        abilities=[BeastForm()],
        blurb="Banks one transformation — heals and hits far harder, but must close in.",
    ),
    HeroDef(
        key="barbarian_king",
        name="蛮王",
        name_en="barbarianKing",
        max_hp=12,
        atk=3,
        move=1,
        max_ap=0,
        attack={"mode": CELL, "cells": 3, "range": 2},
        passives=[LastStand],
        blurb="Frail, but the killing blow only enrages him — two untouchable turns, then dust.",
    ),
    HeroDef(
        key="elder",
        name="长老",
        name_en="elder",
        max_hp=13,
        atk=3,
        move=1,
        max_ap=4,
        attack={"mode": CELL, "cells": 2, "range": 3},
        abilities=[Bless()],
        blurb="Wards one hero at a time against a single blow, and quickens them while it holds.",
    ),
    HeroDef(
        key="strongman",
        name="大力士",
        name_en="strongman",
        max_hp=20,
        atk=3,
        move=1,
        max_ap=2,
        attack={"mode": CELL, "cells": 2, "range": 2},
        abilities=[Slam()],
        blurb="Grabs whoever comes close and throws them clean over its shoulder.",
    ),
    HeroDef(
        key="swordsman",
        name="剑客",
        name_en="swordsman",
        max_hp=17,
        atk=4,
        move=1,
        max_ap=3,
        attack={"mode": CELL, "cells": 2, "range": 2},
        abilities=[GaleSlash()],
        blurb="Cuts a whole rank open and leaves everyone in it easier to kill.",
    ),
    HeroDef(
        key="bomber",
        name="炸弹客",
        name_en="bomber",
        max_hp=15,
        atk=3,
        move=2,
        max_ap=3,
        attack={"mode": CELL, "cells": 2, "range": 2},
        abilities=[SelfDestruct()],
        blurb="Walks in fast and spends itself all at once — a whole rank, or everything around it.",
    ),
    HeroDef(
        key="sabretooth",
        name="剑齿虎",
        name_en="sabretooth",
        max_hp=22,
        atk=5,
        move=1,
        max_ap=0,
        attack={"mode": CELL, "cells": 2, "range": 1},
        passives=[Pounce],
        blurb="Hits harder than anything else on the board, and whatever it mauls cannot run.",
    ),
    HeroDef(
        key="magician",
        name="魔术师",
        name_en="magician",
        max_hp=15,
        atk=2,
        move=1,
        max_ap=2,
        attack={"mode": CELL, "cells": 2, "range": 5},
        abilities=[Transfer()],
        blurb="Trades two heroes' places mid-exchange — walk one out of danger, or drag one into it.",
    ),
    HeroDef(
        key="gunner",
        name="男枪",
        name_en="gunner",
        max_hp=18,
        atk=4,
        move=1,
        max_ap=0,
        attack={"mode": CONE},
        passives=[Shotgun],
        blurb="Sprays a three-cell arc, and every shot that bites makes the next one hurt more.",
    ),
    HeroDef(
        key="mammoth",
        name="猛犸",
        name_en="mammoth",
        max_hp=18,
        atk=3,
        move=1,
        max_ap=0,
        attack={"mode": AREA, "shape": "surround8", "range": None},
        blurb="Swings all the way round — every enemy standing beside it takes the hit.",
    ),
    HeroDef(
        key="ghost",
        name="鬼魂",
        name_en="ghost",
        max_hp=6,
        atk=4,
        move=2,
        max_ap=0,
        attack={"mode": CELL, "cells": 1, "range": 1},
        abilities=[Possess()],
        passives=[GhostForm],
        blurb="Untouchable but powerless, riding an enemy — and it steps into the world made of what it drained.",
    ),
    HeroDef(
        key="sniper",
        name="狙击手",
        name_en="sniper",
        max_hp=13,
        atk=0,
        move=1,
        max_ap=0,
        attack={"mode": LINE, "range": None},
        blurb="Shoots the first enemy down its row or column — the further away, the harder it hits.",
    ),
    HeroDef(
        key="cursed_doll",
        name="诅咒娃娃",
        name_en="cursedDoll",
        max_hp=15,
        atk=4,
        move=1,
                max_ap=2,
        attack={"mode": CELL, "cells": 2, "range": 3},
        abilities=[CursePoison(), Recurse()],
        blurb="Lays a curse on one of your own — striking them costs a turn, and it can lay another once sprung.",
    ),
    HeroDef(
        key="dream_goddess",
        name="美梦神",
        name_en="dreamGoddess",
        max_hp=15,
        atk=2,
        move=1,
        max_ap=2,
        attack={"mode": CELL, "cells": 3, "range": 5},
        abilities=[MagicWard()],
        passives=[DreamWard],
        blurb="Puts the enemy's magic to sleep — no abilities from them until she stirs again.",
    ),
    HeroDef(
        key="gargoyle",
        name="石像鬼",
        name_en="gargoyle",
        max_hp=12,
        atk=4,
        move=1,
        max_ap=0,
        attack={"mode": CELL, "cells": 3, "range": 1},
        passives=[StoneHide],
        blurb="Carved stone — weapons chip it for 1 at a time. Only magic really hurts it.",
    ),
    HeroDef(
        key="centaur",
        name="半人马",
        name_en="centaur",
        max_hp=22,
        atk=3,
        move=1,
        max_ap=4,
        attack={"mode": CELL, "cells": 2, "range": 2},
        abilities=[Charge()],
        blurb="Rides straight through the enemy line, trampling everyone in the lane.",
    ),
    HeroDef(
        key="mist_lady",
        name="雾女",
        name_en="mistLady",
        max_hp=14,
        atk=2,
        move=1,
        max_ap=3,
        attack={"mode": UNIT, "range": None},
        abilities=[GreatFog()],
        blurb="Picks off any enemy anywhere for almost nothing — and every fog takes a step of reach from the whole enemy force.",
    ),
    HeroDef(
        key="shopkeeper",
        name="杂货店爷爷",
        name_en="shopkeeper",
        max_hp=17,
        atk=3,
        move=1,
        max_ap=0,
        attack={"mode": CELL, "cells": 3, "range": 2},
        passives=[Almsgiving],
        blurb="Keeps nothing for himself — every turn, an ally leaves with an extra point.",
    ),
    HeroDef(
        key="arms_dealer",
        name="军火商人",
        name_en="armsDealer",
        max_hp=15,
        atk=1,
        move=1,
        max_ap=99,
        attack={"mode": CELL, "cells": 2, "range": 5, "fuel": True},
        passives=[ArmsDealer],
        blurb="Opens everyone's purse, then sells them what to do with it.",
    ),
    HeroDef(
        key="artisan",
        name="工匠",
        name_en="artisan",
        max_hp=17,
        atk=3,
        move=1,
        max_ap=0,
        attack={"mode": CELL, "cells": 2, "range": 2},
        abilities=[RaiseDoors()],
        blurb="Builds two doors into the board before a single hero is placed.",
    ),
    HeroDef(
        key="judge",
        name="法官",
        name_en="judge",
        max_hp=16,
        atk=3,
        move=1,
        max_ap=4,
        attack={"mode": CELL, "cells": 2, "range": 3},
        abilities=[Commend(), Condemn()],
        blurb="Marks a hero and lets its own next turn decide what it has earned.",
    ),
    HeroDef(
        key="world_tree",
        name="世界树",
        name_en="worldTree",
        max_hp=1,
        atk=0,
        move=0,
        max_ap=0,
        attack={"mode": None},
        passives=[WorldTree],
        deploys="centre",
        blurb="Stands in the middle and never moves. Cut it down yourself, and see what comes out.",
    ),
    HeroDef(
        key="fisherman",
        name="渔夫",
        name_en="fisherman",
        max_hp=16,
        atk=3,
        move=1,
        max_ap=2,
        attack={"mode": CELL, "cells": 2, "range": 2},
        abilities=[Hook()],
        blurb="Drags whatever it can reach out of their line and into yours.",
    ),
    HeroDef(
        key="snow_woman",
        name="雪女",
        name_en="snowWoman",
        max_hp=16,
        atk=2,
        move=1,
        max_ap=4,
        attack={"mode": CELL, "cells": 2, "range": 5},
        abilities=[Avalanche()],
        blurb="Buries the whole board at once, and the cold takes their strength with it.",
    ),
    HeroDef(
        key="water_mage",
        name="水法师",
        name_en="waterMage",
        max_hp=15,
        atk=2,
        move=1,
        max_ap=2,
        attack={"mode": CELL, "cells": 3, "range": 4},
        abilities=[Soak()],
        blurb="Drenches four squares at a stroke, and keeps half of what the water takes.",
    ),
    HeroDef(
        key="pope",
        name="教皇",
        name_en="pope",
        max_hp=13,
        atk=2,
        move=1,
        max_ap=0,
        attack={"mode": CELL, "cells": 2, "range": 4},
        passives=[Absolution],
        blurb="Nothing dies while it stands — but every mercy sharpens the hand it stayed.",
    ),
    HeroDef(
        key="plague_doctor",
        name="鸟嘴医生",
        name_en="plagueDoctor",
        max_hp=14,
        atk=2,
        move=1,
        max_ap=0,
        attack={"mode": CELL, "cells": 2, "range": 2},
        abilities=[TakeThePlague()],
        passives=[PlagueBearer],
        blurb="Arrives last, stands where it likes, and the board rots outward from there.",
    ),
    HeroDef(
        key="painter",
        name="画师",
        name_en="painter",
        max_hp=18,
        atk=1,
        move=1,
        max_ap=0,
        attack={"mode": CELL, "cells": 2, "range": 2},
        passives=[PaintStroke],
        blurb="Starts as the weakest thing on the board and paints the difference away.",
    ),
    HeroDef(
        key="four_beasts",
        name="四圣兽",
        name_en="fourBeasts",
        max_hp=12,
        atk=2,
        move=2,
        max_ap=0,
        attack={"mode": UNIT, "range": None},
        passives=[FourBeasts],
        blurb="Four squares on the board wake something in it. Reaching all four takes the whole match.",
    ),
    HeroDef(
        key="astrologer",
        name="占星师",
        name_en="astrologer",
        max_hp=14,
        atk=2,
        move=1,
        max_ap=0,
        attack={"mode": CELL, "cells": 3, "range": 4},
        abilities=[Prophecy()],
        passives=[StarSign],
        blurb="Names who dies next. The more often it is right, the worse being named becomes.",
    ),
    HeroDef(
        key="hunter",
        name="猎人",
        name_en="hunter",
        max_hp=14,
        atk=3,
        move=1,
        max_ap=0,
        attack={"mode": CELL, "cells": 3, "range": 2},
        passives=[FirstBlood],
        blurb="One kill is all it needs — then it sees further and takes two at a time.",
    ),
    HeroDef(
        key="assassin",
        name="刺客",
        name_en="assassin",
        max_hp=14,
        atk=5,
        move=1,
        max_ap=2,
        attack={"mode": CELL, "cells": 2, "range": 1},
        abilities=[Garrote()],
        blurb="Nowhere on the board is out of reach, and what it reaches it cuts.",
    ),
    HeroDef(
        key="diver",
        name="潜水者",
        name_en="diver",
        max_hp=13,
        atk=2,
        move=2,
        max_ap=0,
        attack={"mode": CELL, "cells": 2, "range": 2},
        abilities=[LayBigBomb()],
        passives=[BombLayer],
        blurb="Mines the ground as it goes, and leaves one last charge behind when it falls.",
    ),
    HeroDef(
        key="snake_emperor",
        name="蛇帝",
        name_en="snakeEmperor",
        # Card-level numbers describe the whole snake; the two bodies below carry
        # the real stats. The 25 HP is a single pool that both halves share.
        max_hp=25,
        atk=3,
        move=1,
        max_ap=0,
        attack={"mode": CELL, "cells": 2, "range": 1},
        squad=[SNAKE_HEAD, SNAKE_TAIL],
        passives=[SerpentBody],
        blurb="One creature on two squares — a poisoning bite up close, and a tail that reaches.",
    ),
    HeroDef(
        key="goblin_gang",
        name="哥布林团伙",
        name_en="goblinGang",
        # Card-level numbers are the gang's totals, shown only if a client has
        # nothing better to draw; the real stats live on the members below.
        max_hp=21,
        atk=2,
        move=1,
        max_ap=2,
        attack={"mode": CELL, "cells": 2, "range": 4},
        squad=["goblin_javelin", "goblin_javelin", "goblin_commander"],
        passives=[GangTactics],
        blurb="Three bodies for one slot — two javelins and a commander, all acting on one turn.",
    ),
    HeroDef(
        key="explorer",
        name="探险家",
        name_en="explorer",
        max_hp=3,
        atk=2,
        move=1,
        max_ap=0,
        attack={"mode": CELL, "cells": 2, "range": 1},
        abilities=[ChooseIsland(), MakeLandfall(),
                   DigGrapes(), TrainNatives(), MineOre()],
        passives=[Explorer],
        deploys="island",
        blurb="Spends three rounds on an island of its own making, then brings back what it dug up.",
    ),
]

BY_KEY = {h.key: h for h in ROSTER}

# Squad bodies — 哥布林团伙's crew and 蛇帝's two halves. Not in ROSTER: they are
# never drafted directly, only deployed by their card, so they live in BY_KEY
# alongside the dummy.
SQUAD_MEMBERS = [
    HeroDef(
        key="goblin_javelin",
        name="投矛手",
        name_en="goblinJavelin",
        max_hp=8,
        atk=2,
        move=1,
        max_ap=0,
        attack={"mode": CELL, "cells": 2, "range": 4},
        passives=[GangTactics],
        gang="goblin_gang",
        blurb="Throws from four cells away. Fragile up close.",
    ),
    HeroDef(
        key="goblin_commander",
        name="指挥",
        name_en="goblinCommander",
        max_hp=5,
        atk=1,
        move=1,
        max_ap=2,
        attack={"mode": CELL, "cells": 3, "range": 1},
        abilities=[GoblinRally()],
        passives=[GangTactics],
        gang="goblin_gang",
        blurb="Barely a fighter — but he makes the whole gang hit harder.",
    ),
    HeroDef(
        key=SNAKE_HEAD,
        name="蛇首",
        name_en="snakeHead",
        max_hp=25,          # the pool for the whole snake; the tail mirrors it
        atk=3,
        move=1,
        max_ap=0,
        attack={"mode": CELL, "cells": 2, "range": 1},
        passives=[SerpentBody, VenomFangs],
        gang="snake_emperor",
        gang_rank=0,        # the head takes the turn; the tail follows it
        blurb="Bites at arm's length and leaves venom in the wound.",
    ),
    HeroDef(
        key=SNAKE_TAIL,
        name="蛇尾",
        name_en="snakeTail",
        max_hp=25,          # display only — every blow is passed to the head
        atk=3,
        move=1,
        max_ap=0,
        attack={"mode": CELL, "cells": 3, "range": 3},
        passives=[SerpentBody, PincerStrike],
        gang="snake_emperor",
        gang_rank=1,
        blurb="Lashes out three squares, and hits harder where the head has already bitten.",
    ),
]

SQUAD_MEMBERS += [
    HeroDef(
        key="loki",
        name="洛基",
        name_en="loki",
        max_hp=10,
        atk=5,
        move=2,
        max_ap=0,
        attack={"mode": CELL, "cells": 2, "range": 2},
        blurb="Walks out of the wreck of the world tree.",
    ),
]

for _m in SQUAD_MEMBERS:
    BY_KEY[_m.key] = _m

# A punching-bag for --test mode: one-enemy attack (you pick the target), no
# ability. Not in ROSTER, so it can never be drafted in a real game.
NATIVE = HeroDef(
    key="native",
    name="土著",
    name_en="native",
    max_hp=5,
    atk=1,           # decorative: NativeStrike rewrites the blow against its target
    move=1,
    max_ap=0,
    attack={"mode": CELL, "cells": 2, "range": 1},
    passives=[NativeStrike],
    blurb="Trained by 探险家. Its blow is a sixth of whatever it lands on.",
)
SQUAD_MEMBERS.append(NATIVE)
BY_KEY[NATIVE.key] = NATIVE


DUMMY = HeroDef(
    key="dummy",
    name="木桩",
    name_en="dummy",
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
# Whoever is newest goes here — --test always deploys the hero just added, so it
# can be played immediately. Up to two; the rest of the side is padded with dummies.
TEST_HEROES = ["explorer", "world_tree"]



# Every targeting kind the engine emits and the client knows how to collect. A
# new one must be added here *and* handled in app.js — check_roster catches the
# half of that which Python can see.
TARGETING_KINDS = {
    "none", "ally", "unit", "two_units", "any_cell", "direction",
    "magnitude", "weapon", "cells", "lane", "area", "cone", "shape",
    "any_unit", "two_cells",
}


def board_marks(match):
    """Squares a living hero's passive wants shown on the board (四圣兽's shrines).
    General on purpose: a passive publishes its own marks and the view asks for all
    of them without knowing whose they are, so a future hero with special ground
    needs no new plumbing."""
    out = []
    for e in match.living():
        for p in e.passives:
            fn = getattr(p, "marks", None)
            if fn is not None:
                out.extend(fn(match, e))
    return out


def check_roster():
    """Structural checks on the hero data itself. Cheap enough to run at import in
    tests, and it catches the mistakes that are otherwise found only in play: a
    misspelled attack mode, a squad naming a body that does not exist, two heroes
    sharing a key, art that will never be found because name_en does not match.
    Returns a list of complaints; empty means the roster is well formed."""
    import attacks as ATK

    bad = []
    seen = {}
    every = list(ROSTER) + list(SQUAD_MEMBERS) + [DUMMY]

    for h in every:
        where = f"{h.name}({h.key})"
        if h.key in seen:
            bad.append(f"{where}: duplicate key, also used by {seen[h.key]}")
        seen[h.key] = h.name

        mode = (h.attack or {}).get("mode")
        # None is a real answer: 世界树 never attacks anything.
        if mode is not None and mode not in ATK.MODES:
            bad.append(f"{where}: unknown attack mode {mode!r}")
        if mode == CELL:
            for field in ("cells", "range"):
                if h.attack.get(field) is None:
                    bad.append(f"{where}: cell-locked attack needs a {field!r}")
        if mode == WEAPON and not h.weapons:
            bad.append(f"{where}: weapon attack but no weapons listed")

        if not h.name_en or not h.name_en[0].islower() or " " in h.name_en:
            bad.append(f"{where}: name_en {h.name_en!r} should be lowerCamelCase — art is looked up by it")
        for n in (h.max_hp, h.atk, h.move, h.max_ap):
            if not isinstance(n, int) or n < 0:
                bad.append(f"{where}: stats must be non-negative whole numbers, got {n!r}")
        if h.max_hp < 1:
            bad.append(f"{where}: a hero needs at least 1 HP")

        for k in (h.squad or []):
            if k not in BY_KEY:
                bad.append(f"{where}: squad names {k!r}, which is not a hero")
        if h.gang and h.gang not in BY_KEY:
            bad.append(f"{where}: belongs to gang {h.gang!r}, which is not a hero")

        keys = [ab.key for ab in h.abilities]
        if len(keys) != len(set(keys)):
            bad.append(f"{where}: two abilities share a key {keys}")
        for ab in h.abilities:
            if not ab.key or not ab.name:
                bad.append(f"{where}: ability {ab!r} needs a key and a name")
            if ab.ap_cost > h.max_ap and not ab.opening:
                bad.append(f"{where}: {ab.name} costs {ab.ap_cost} AP but the hero maxes at {h.max_ap}")
            if ab.targeting.get("kind") == "direction" and not ab.targeting.get("options"):
                bad.append(f"{where}: {ab.name} is direction-targeted but lists no options")
            kind = ab.targeting.get("kind")
            if kind not in TARGETING_KINDS:
                bad.append(f"{where}: {ab.name} uses targeting {kind!r}, which nothing collects")

    for k in TEST_HEROES:
        if k not in BY_KEY:
            bad.append(f"TEST_HEROES names {k!r}, which is not a hero")
    return bad


def status_of(match, entity):
    """Live badges for the board and the hero card. Most come from a passive's
    optional `status(match, owner)`; the match-level ones (诅咒娃娃's mark, and the
    freeze it inflicts) are collected here because they sit on units that have no
    passive of their own."""
    out = []
    if entity.vars.get("curse_mark"):
        out.append({
            "key": "cursed", "badge": "咒", "label": "咒毒 MARKED",
            "private": True,      # only the side that laid the curse may see it
            "text": "The first attack or ability that damages this hero costs its "
                    "source the next two rounds.",
        })
    if entity.vars.get("blessed"):
        out.append({
            "key": "blessed", "badge": "佑", "label": "祝福 BLESSED",
            "text": "The next attack or ability that would hurt it is turned aside. "
                    "+1 movement until then. Burning ground still bites.",
        })
    if entity.vars.get("arms"):
        w = entity.vars["arms"]
        spent = w.get("spent")
        out.append({
            "key": "armed", "badge": "械", "label": f"{w['name']}",
            "text": ("Its own attack is gone — the warhead is spent." if spent
                     else w["text"]),
        })
    if entity.vars.get("judged"):
        mark = entity.vars["judged"]
        good = mark["kind"] == "reward"
        out.append({
            "key": "judged", "badge": "赏" if good else "罚",
            "label": "赏善 COMMENDED" if good else "罚恶 CONDEMNED",
            "text": ("At the end of its next turn it is mended for everything it "
                     "dealt during that turn." if good else
                     "At the end of its next turn it takes everything it dealt "
                     "during that turn, back on itself."),
        })
    if entity.vars.get("vulnerable"):
        n = entity.vars["vulnerable"]
        out.append({
            "key": "vulnerable", "badge": f"伤+{n}", "label": "增伤 EXPOSED",
            "text": f"Takes {n} more from every hit — attacks, abilities and "
                    f"burning ground alike. It does not wear off.",
        })
    if match.bound(entity):
        out.append({
            "key": "bound", "badge": "缚", "label": "束缚 BOUND",
            "text": "Held where it stands for as long as whoever holds it lives — "
                    "it can still attack and cast.",
        })
    elif match.rooted(entity):
        out.append({
            "key": "rooted", "badge": "钉", "label": "定身 PINNED",
            "text": "Cannot move on its next turn — it can still attack and cast.",
        })
    elif entity.vars.get("rooted_at") is not None:
        n = entity.vars.get("rooted_squares", 0)
        venom = entity.vars.get("rooted_tag") == "venom"
        out.append({
            "key": "poisoned" if venom else "slowed",
            "badge": "毒" if venom else "慢",
            "label": "中毒 ENVENOMED" if venom else "减速 SLOWED",
            "text": (f"Moves {n} square{'' if n == 1 else 's'} less on its next turn, "
                     f"and then it wears off."
                     + (" It cannot be given a second dose until this one is spent."
                        if venom else "")),
        })
    if match.frozen(entity):
        left = match.frozen_rounds_left(entity)
        out.append({
            "key": "frozen", "badge": "禁", "label": "无法行动 FROZEN",
            "text": f"Cursed — cannot take a turn for {left} more round"
                    f"{'' if left == 1 else 's'}.",
        })
    for p in entity.passives:
        fn = getattr(p, "status", None)
        s = fn(match, entity) if fn else None
        if s:
            out.append(s)
    return out


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
