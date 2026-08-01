"""Hero definitions as data (spec 7.11).

A hero is a stat block plus ability entries plus passive handler classes. No
hero subclasses Entity, and nothing here is referenced by the core loop.
"""

from dataclasses import dataclass, field

import damage as DMG
import events as EV
from entities import Modifier, UNTIL_OWNER_NEXT_TURN, UNTIL_TURN_END
from topology import other_side


# ---------------------------------------------------------------- abilities


class Ability:
    key = ""
    name = ""
    ap_cost = 0
    use_limit = None  # None = unlimited; an int caps total uses per match
    opening = False   # True = fires once at game start (the opening phase), not on a turn
    # True = the ability moves the hero itself (半人马's 冲撞), so the turn's normal
    # movement is not used: the commit must hold position.
    self_move = False
    targeting = {"kind": "none"}
    blurb = ""

    def build_damage(self, match, actor, params):
        return []

    def side_effects(self, match, actor, params):
        return None

    def validate(self, match, actor, params):
        """Legality beyond the generic targeting checks — e.g. whether a chosen
        direction can actually be charged. None means fine."""
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


class Charge(Ability):
    key = "charge"
    name = "冲撞 Charge"
    ap_cost = 2
    self_move = True
    targeting = {"kind": "direction", "options": ["forward", "backward", "up", "down"]}
    blurb = ("Charge 3 squares down one lane, dealing 3 to each enemy in the two squares "
             "crossed on the way (at most two). If the third square is taken or off the "
             "board it still tramples, but holds its ground. Takes the place of your move.")
    DAMAGE = 3
    DISTANCE = 3

    @classmethod
    def path(cls, match, actor, direction):
        """(landing cell or None when it can't land, [enemies in the crossed squares]).
        The run is always exactly DISTANCE squares: the ones in between are trampled,
        the last one is where it ends up — if anybody is standing there, or it is off
        the board, the charge still lands its damage but the centaur doesn't move.
        Whole thing is None only if the direction itself is nonsense."""
        d = match.topology.direction_step(actor.side, direction)
        if d is None or not actor.cells:
            return None
        c0, r0 = actor.cell
        lane = [(c0 + d[0] * i, r0 + d[1] * i) for i in range(1, cls.DISTANCE + 1)]
        victims = []
        for cell in lane[:-1]:
            if not match.topology.in_bounds(cell):
                continue
            occ = match.occupant(cell)
            if occ is not None and occ.side != actor.side:
                victims.append(occ)   # allies in the way are simply ridden past
        end = lane[-1]
        blocked = not match.topology.in_bounds(end) or match.occupant(end) is not None
        return (None if blocked else end), victims

    def lanes(self, match, actor):
        """Every lane worth charging, for the client to offer and preview. A lane
        that would neither move nor trample anyone is left out."""
        out = []
        for d in self.targeting["options"]:
            p = self.path(match, actor, d)
            if p is None:
                continue
            landing, victims = p
            if landing is None and not victims:
                continue   # nowhere to go and nobody to hit
            out.append({"dir": d,
                        "landing": list(landing) if landing else None,
                        "victims": [v.id for v in victims],
                        "damage": self.DAMAGE})
        return out

    def validate(self, match, actor, params):
        p = self.path(match, actor, params.get("direction"))
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
            f"{match.label(actor)} charges {match.cell_name(frm)} → {match.cell_name(landing)}."
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
                if match.occupant(c) is None]
        if not free:
            return None
        return {
            "key": "reposition",
            "name": "散弹枪 Reposition",
            "text": "The shot connected — step one square now, or hold your ground.",
            "kind": "cell",
            "optional": True,
            "options": [list(c) for c in free],
        }

    def apply_followup(self, match, owner, key, choice):
        if key != "reposition" or not choice:
            return
        cell = tuple(choice)
        if match.occupant(cell) is not None or not owner.alive:
            return
        frm = owner.cell
        owner.set_cell(cell)
        match.bus.emit(EV.AFTER_MOVE, {"entity": owner, "from": frm, "to": cell})
        match.log_line(
            f"{match.label(owner)} racks the slide and slides "
            f"{match.cell_name(frm)} → {match.cell_name(cell)}."
        )

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
                if match.occupant(c) is None]

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
            f"{match.label(owner)} tears loose and takes flesh at "
            f"{match.cell_name(ctx['to'])} with {owner.max_hp} health — "
            f"every point of it drained."
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

    def validate(self, match, actor, params):
        a, b = self.pair(match, params)
        for x in (a, b):
            if x is None or not x.alive or not x.cells:
                return "Choose two units that are on the board."
        if a is b:
            return "Choose two different units."
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

    def blessable(self, match, actor):
        return [e.id for e in match.living(actor.side) if not e.vars.get("blessed")]

    def validate(self, match, actor, params):
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
    """大力士's 摔击. Seize an adjacent enemy and hurl it to the square directly
    opposite through you — reflected through your own cell. The landing square has
    to be clear, so the throw is as much about geometry as about the damage."""

    key = "slam"
    name = "摔击 Slam"
    ap_cost = 1
    targeting = {"kind": "unit"}
    blurb = ("Seize an enemy in any of the 8 squares around you and hurl it to the "
             "square directly opposite, dealing 3. The landing square must be empty.")
    DAMAGE = 3

    @staticmethod
    def landing(match, actor, target):
        """Where the victim ends up: reflected through the strongman. None if it is
        not adjacent, or the far square is off the board or already taken."""
        if not actor.cells or target is None or not target.cells:
            return None
        (ac, ar), (tc, tr) = actor.cell, target.cell
        dc, dr = tc - ac, tr - ar
        if max(abs(dc), abs(dr)) != 1:      # the 8 around it, diagonals included
            return None
        cell = (ac - dc, ar - dr)
        if not match.topology.in_bounds(cell) or match.occupant(cell) is not None:
            return None
        return cell

    def throwable(self, match, actor):
        """Every enemy it could actually seize right now, for the client to offer."""
        return [e.id for e in match.living(other_side(actor.side))
                if e.flags["targetable"] and self.landing(match, actor, e)]

    def validate(self, match, actor, params):
        tgt = match.entity(params.get("target"))
        if tgt is None or not tgt.alive or tgt.side == actor.side:
            return "Choose a living enemy."
        if not actor.cells:
            return f"{actor.name} has nothing to throw with."
        (ac, ar), (tc, tr) = actor.cell, tgt.cell
        if max(abs(tc - ac), abs(tr - ar)) != 1:
            return "It can only seize somebody right beside it."
        if self.landing(match, actor, tgt) is None:
            return "Nowhere to throw it — the square behind is taken or off the board."
        return None

    def build_damage(self, match, actor, params):
        tgt = match.entity(params.get("target"))
        if tgt is None or not tgt.alive or tgt.side == actor.side:
            return []
        # Remembered now, while both are still where the order was given: the throw
        # itself happens after the damage, once everything has landed.
        actor.vars["slam_target"] = tgt.id
        return [DMG.DamageEvent(source=actor, target=tgt, amount=self.DAMAGE,
                                category=DMG.ABILITY)]

    def side_effects(self, match, actor, params):
        tgt = match.entity(actor.vars.pop("slam_target", None))
        if tgt is None or not tgt.alive or not actor.alive:
            return
        cell = self.landing(match, actor, tgt)
        if cell is None:
            match.log_line(f"{match.label(actor)} loses its grip — nowhere to throw.")
            return
        frm = tgt.cell
        tgt.set_cell(cell)
        match.bus.emit(EV.AFTER_MOVE, {"entity": tgt, "from": frm, "to": cell})
        match.log_line(
            f"{match.label(actor)} hurls {match.label(tgt)} "
            f"{match.cell_name(frm)} → {match.cell_name(cell)}."
        )


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
        cells = set(self.cells_of(match, actor, which))
        return [e for e in match.living()
                if e.side != actor.side and (e.cells & cells)]

    def shapes(self, match, actor):
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

    def validate(self, match, actor, params):
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
        for e in match.living():
            if e.side == actor.side:
                continue
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
        cells = set(match.topology.row(actor.cell[1]))
        return [
            DMG.DamageEvent(source=actor, target=e, amount=6, category=DMG.ABILITY)
            for e in match.living()
            if e.side != actor.side and (e.cells & cells)
        ]


class Inspire(Ability):
    key = "inspire"
    name = "鼓舞 Inspire"
    ap_cost = 2
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
    ap_cost = 2
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
        total = sum(
            DMG.heal(match, e, 1, source=owner)
            for e in match.living() if e.side == owner.side
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

    # Earliest slot in the pipeline, so every rule after it — wards, guards, 增伤,
    # the lot — reads the head as the thing being hit.
    @EV.hook(priority=5)
    def on_before_damage(self, match, owner, ev):
        if ev.target is not owner or owner.key != SNAKE_TAIL:
            return
        head = _other_half(match, owner, SNAKE_HEAD)
        if head is not None and head.alive:
            ev.target = head

    @EV.hook(priority=95)
    def on_after_damage(self, match, owner, ev):
        self._mirror(match, owner)

    def on_match_start(self, match, owner, ctx):
        if owner.key == SNAKE_TAIL:
            # One hero, not two: the tail is a body on the board, never a life to take.
            owner.flags["counts_for_defeat"] = False
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
        free = {owner.id, head.id}
        return [c for c in match.topology.neighbours(dest)
                if match.occupant(c) is None or match.occupant(c).id in free]


class VenomFangs:
    """蛇帝's head. Anything its bite draws blood from carries venom for two rounds.
    A hero already poisoned cannot take a second dose — no stacking and no
    refreshing — so a fresh victim is always worth more than the same one twice.

    It also notes what it bit this turn, which is what the tail closes on."""

    describe = ("Its bite poisons for 2 rounds — 1 damage at each round's end. Only a "
                "hero not already poisoned can be poisoned.")
    ROUNDS = 2

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
        if ev.target.vars.get("poison_rounds", 0) > 0:
            return      # 只有未中毒的才能中毒 — no second dose, no refresh
        ev.target.vars["poison_rounds"] = self.ROUNDS
        ev.target.vars["poison_source"] = owner.id
        match.log_line(
            f"{match.label(ev.target)} is poisoned — 1 at the end of each of the "
            f"next {self.ROUNDS} rounds."
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
        max_hp=30,
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
        atk=2,
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

for _m in SQUAD_MEMBERS:
    BY_KEY[_m.key] = _m

# A punching-bag for --test mode: one-enemy attack (you pick the target), no
# ability. Not in ROSTER, so it can never be drafted in a real game.
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
TEST_HEROES = ["bomber", "snake_emperor"]



# Every targeting kind the engine emits and the client knows how to collect. A
# new one must be added here *and* handled in app.js — check_roster catches the
# half of that which Python can see.
TARGETING_KINDS = {
    "none", "ally", "unit", "two_units", "any_cell", "direction",
    "magnitude", "weapon", "cells", "lane", "area", "cone", "shape",
}


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
        if mode not in ATK.MODES:
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
    if entity.vars.get("poison_rounds"):
        n = entity.vars["poison_rounds"]
        out.append({
            "key": "poisoned", "badge": "毒", "label": "中毒 POISONED",
            "text": f"Loses 1 at the end of each of the next {n} round"
                    f"{'' if n == 1 else 's'}. It cannot be poisoned again until "
                    f"this wears off.",
        })
    if entity.vars.get("vulnerable"):
        n = entity.vars["vulnerable"]
        out.append({
            "key": "vulnerable", "badge": f"伤+{n}", "label": "增伤 EXPOSED",
            "text": f"Takes {n} more from every hit — attacks, abilities and "
                    f"burning ground alike. It does not wear off.",
        })
    if match.rooted(entity):
        out.append({
            "key": "rooted", "badge": "钉", "label": "定身 PINNED",
            "text": "Cannot move on its next turn — it can still attack and cast.",
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
