"""Match state machine.

The round is a mutable queue of exchanges rather than a loop over four heroes
(spec 7.8), and resolution is re-entrant: it pauses at the victim-pick step,
waits for both sides to choose independently, then continues.
"""

import itertools
import random
import time

import actions as ACT
import attacks as ATK
import damage as DMG
import events as EV
import heroes as HEROES
from board import Board, Minefield, Vineyard
from entities import (Entity, Modifier, UNTIL_OWNER_NEXT_TURN, UNTIL_ROUND_END,
                      UNTIL_TURN_END)
from topology import LEFT, RIGHT, Topology, other_side


DRAFT = "draft"
# Before anybody is placed: abilities flagged `prebuild=True` reshape the board
# itself (工匠's doors), so both sides deploy knowing what it looks like. There are
# no entities yet, so a build ability is handed its side rather than a hero.
BUILD = "build"
SETUP = "setup"
OPENING = "opening"
COMMIT = "commit"
VICTIM = "victim"
# Everyone has finished moving but no attack has been worked out yet, and an
# ability wants the player to choose where it puts somebody (刺客's 封喉 picking
# which square beside its mark to appear on). The choice cannot be made when the
# order is sealed, because it depends on where the mark ended up.
MOVE_CHOICE = "move_choice"
# A killing blow has landed but nobody has been swept off the board yet, and
# somebody may step in front of it (教皇). Resolution holds here until the Pope's
# side says yes or no, and — if yes — whoever swung has picked what it gains.
INTERRUPT = "interrupt"
# A turn has fully resolved and something wants to act on the outcome before the
# next pair is picked (男枪 stepping after a hit). Only entered if a follow-up
# actually needs a decision.
RESOLVED = "resolved"
GAMEOVER = "gameover"

# The draft plays out in batches: each batch shows a fresh set of never-seen
# champions, and the listed sides each pick one (in order); leftover cards are
# discarded, not handed to anyone. 4x4 = 16 shown, 4 picked per side, so the
# roster must stay at least 16 heroes. Every pick is a choice of 4 then 3 — the
# side picking second alternates, so both sides face the same choice counts.
DRAFT_BATCHES = [
    (4, [LEFT, RIGHT]),
    (4, [RIGHT, LEFT]),
    (4, [RIGHT, LEFT]),
    (4, [LEFT, RIGHT]),
]


class Match:
    def __init__(self, force_size=3, mode="pvp"):
        self.mode = mode  # "pvp" or "self" (solo hotseat: normal attacks auto-aim)
        self.topology = Topology()
        self.board = Board(self.topology)
        # Sub-maps built before deployment, by side: {"cells": [...], "stand": cell}.
        self.islands = {}
        # What to do when the interrupt queue drains. None means a resolution is in
        # flight and waiting for it, which is the ordinary case.
        self.interrupt_resume = None
        self.bus = EV.EventBus(self)
        self.global_rules = [DMG.SharedPool(), DMG.AbilityImmunity(), DMG.Blessing(),
                             DMG.OutgoingShift(),
                             DMG.Vulnerability(), DMG.FlatReduction(), DMG.CurseTrap(),
                             DMG.HalvingRule(), DMG.Ledger(),
                             DMG.Verdict(), Minefield(), Vineyard()]
        self.clear_exchange()

        self.entities = []
        self._ids = itertools.count(1)
        self.force_size = force_size

        self.round = 0
        self.exchange = 0
        self.log = []
        self.winner = None
        self.version = 0

        self.setup_state = {
            LEFT: {"placements": [], "ready": False},
            RIGHT: {"placements": [], "ready": False},
        }

        # Last time each seat polled for state — used to tell whether both
        # players have actually opened their tab (the draft waits for both).
        self.present = {LEFT: 0.0, RIGHT: 0.0}

        # Heroes each side owns after the draft; placement is restricted to these.
        self.drafted = {LEFT: [], RIGHT: []}
        # Deployment slots per side, filled in once a force is known.
        self.slots_total = {}
        self.draft = None
        self.build = None
        self.opening = None

        self.phase = SETUP
        # 美梦神's 魔法守护: side -> the id of the caster silencing it. Lazily valid,
        # so the ward dies with its caster without a death hook.
        self.ability_lock = {LEFT: None, RIGHT: None}
        self.snapshot = {}
        self.last_reveal = None

        # Kicks off draft (pvp/self) or auto-setup (test); must come last so all
        # state above exists before an opening ability or first exchange runs.
        if self.mode == "test":
            self._begin_test()
        else:
            self._begin_draft()

    # ------------------------------------------------------------ helpers

    def bump(self):
        self.version += 1

    def label(self, e):
        """Both sides can field the same hero, so log lines must say whose."""
        return ("Left " if e.side == LEFT else "Right ") + e.name

    def log_line(self, text, quiet=False, side=None):
        """`side` scopes a line to one seat — hidden information (诅咒娃娃 choosing
        whom to curse) must not appear in the opponent's field log."""
        self.log.append({"round": self.round, "text": text, "quiet": quiet, "side": side})
        self.log = self.log[-200:]

    def living(self, side=None):
        return [
            e
            for e in self.entities
            if e.alive and (side is None or e.side == side)
        ]

    def bodies(self, side=None):
        """Living units counted once per creature: a body whose health lives on
        another (蛇帝's tail) is not a second thing to heal. Anything that acts on
        "every ally's health" should walk this rather than `living`."""
        return [e for e in self.living(side) if DMG.pool_holder(self, e) is None]

    def entity(self, eid):
        for e in self.entities:
            if e.id == eid:
                return e
        return None

    def enemies_in(self, cells, side):
        """Every enemy standing in these squares, counted once per creature. A hero
        on two squares (蛇帝) is one target, not two — otherwise anything that sweeps
        an area hits it twice for one blow. `cells=None` means the whole board."""
        cells = None if cells is None else {tuple(c) for c in cells}
        seen, out = set(), []
        for e in self.living():
            if e.side == side or not e.flags["targetable"]:
                continue
            if cells is not None and not (e.cells & cells):
                continue
            holder = DMG.pool_holder(self, e) or e
            if holder.id in seen:
                continue
            seen.add(holder.id)
            out.append(e)
        return out

    def strikeable_allies(self, cells, side):
        """Friends in these squares that may be picked as a target on purpose. Only
        世界树 offers itself this way, and no blow ever lands — the striking is what
        counts. Nothing else in the game has friendly fire."""
        cells = {tuple(c) for c in cells}
        return [e for e in self.living(side)
                if (e.cells & cells)
                and any(getattr(p, "struck_by_allies", False) for p in e.passives)]

    def occupant(self, cell):
        for e in self.entities:
            if e.alive and e.flags["blocks_movement"] and e.occupies(cell):
                return e
        return None

    def unacted(self, side):
        return [e for e in self.living(side)
                if not e.has_acted and e.flags["takes_turns"] and not self.frozen(e)]

    def can_cross(self, e, cell, ignore=()):
        """May this unit walk *over* that square? Only a body in the way stops it —
        a square shut against its side (工匠's doors) may be crossed, just not stood
        on, so a door costs the other side somewhere to stand and never a step of
        distance."""
        cell = tuple(cell)
        if not self.topology.in_bounds(cell):
            return False
        occ = self.occupant(cell)
        return occ is None or occ is e or occ in ignore

    def can_enter(self, e, cell, ignore=()):
        """May this unit come to rest on that square? Everything `can_cross` asks,
        and not shut against its side. Every landing goes through here — walked to,
        thrown to, hauled to or swapped into — so a square closed to a side is
        closed however the arrival is arranged.

        `ignore` names units that will have left by the time this resolves.
        """
        if e is not None and self.topology.closed_to(cell, e.side):
            return False
        return self.can_cross(e, cell, ignore)

    def strike_down(self, e, why=None):
        """End a unit outright, with no blow behind it — 蛮王 burning out, 蛇尾 going
        limp when the head falls, 探险家 paying 反动. It is not damage, so nothing
        that turns damage aside touches it and nobody can be interposed; the
        ordinary death path still runs, so a passive that prevents death has its
        say and whatever the unit owed its side is still asked for."""
        e.hp = 0
        if why:
            self.log_line(why)
        self.sweep_deaths()

    def aim(self, e, direction, origin=None):
        """(step, origin) for a hero aiming down a named direction: the unit vector
        from its own side's point of view, and the square it will be aiming from
        once movement has resolved. Both are None if the direction is nonsense or
        the hero has no body to aim with — four lane-walkers used to reason about
        which of the two could be missing, each in its own way."""
        origin = e.acts_from(origin)
        step = self.topology.direction_step(e.side, direction)
        return (step, origin) if (step and origin) else (None, None)

    def place_unit(self, e, cell):
        """Put a unit on a square and tell the board it has moved. Everything that
        shifts somebody without walking them goes through here — 半人马's charge,
        渔夫's haul, 大力士's throw, 男枪's step aside, 鸟嘴医生 walking in. The
        emit is the part that is easy to forget, and forgetting it is how a mine
        under the destination quietly stops going off."""
        cell = tuple(cell)
        frm = e.cell
        e.set_cell(cell)
        self.bus.emit(EV.AFTER_MOVE, {"entity": e, "from": frm, "to": cell})

    def clear_exchange(self):
        """Everything that lives for exactly one exchange, wiped in one place. It
        was written out twice — once when a match begins and once when an exchange
        does — which meant a new field of exchange state had to be remembered in
        both, or it would arrive stale on the first exchange of a match."""
        self.selected = {LEFT: None, RIGHT: None}
        self.commits = {LEFT: None, RIGHT: None}
        self.turn_started = {LEFT: False, RIGHT: False}
        self.res = None
        # Whose attacks connected this exchange, for the turn-resolved hooks.
        self.landed = set()
        self.followups = {LEFT: [], RIGHT: []}
        # Units that died during it. They are gone from the board but may still
        # owe the player a parting decision.
        self.recent_deaths = []
        # Damage from the board itself, banked as it is triggered so a whole
        # exchange's worth lands in one instant (mines under two movers).
        self.pending_hazards = []
        # Squares an ability is waiting on, and the ones already chosen.
        self.move_choices = {LEFT: [], RIGHT: []}
        self.move_picks = {}
        # Decisions owed mid-damage, and the half-applied instant waiting on them.
        self.interrupts = []
        self.instant = None

    def on_map(self, side=None):
        """Living units standing on the board proper. Anything on a sub-map (探险家's
        island, until round four) is out of the game entirely, so no hero skill of
        either side reaches it — not a blessing of its own, nor a curse of theirs.
        A hero with no square at all (鬼魂 between bodies) is still on the map.
        """
        return [e for e in self.living(side)
                if not e.cells or self.topology.region(e.cell) is None]

    # A root that takes every square there is. Held as a number rather than a
    # special case so "all of it" and "one square of it" are the same rule.
    ALL_SQUARES = 99

    def root(self, e, squares=None, tag=None):
        """Take movement out of this unit's next turn — all of it by default
        (剑齿虎's pin), or just `squares` of it (蛇帝's venom leaves it a square
        short). It still fights either way; only its feet are affected.

        Stamped with the exchange it was applied in, so one applied during its own
        turn is served by the turn after, not this one. Two roots on the same turn
        do not add up: the heavier one stands, so a slow can never loosen a pin.

        `tag` names what did it, for the badge and for any rule that asks whether
        this particular thing is already on (蛇帝 gives no second dose). It lives in
        the root's own state, so it is spent by exactly the same turn."""
        n = self.ALL_SQUARES if squares is None else squares
        if e.vars.get("rooted_at") is not None:
            was = e.vars.get("rooted_squares", self.ALL_SQUARES)
            if was >= n:
                n, tag = was, e.vars.get("rooted_tag")
        e.vars["rooted_at"] = (self.round, self.exchange)
        e.vars["rooted_squares"] = n
        e.vars["rooted_tag"] = tag

    def sprint(self, e, squares):
        """Put squares *into* this unit's next turn — the mirror of `root`, and
        stamped the same way, so one granted during the unit's own turn is spent
        by the turn after rather than by the one that granted it (忍者 walks out
        of the lightning). Two grants do not add up: the longer stride stands."""
        if e.vars.get("sprint_at") is not None:
            squares = max(squares, e.vars.get("sprint_squares", 0))
        e.vars["sprint_at"] = (self.round, self.exchange)
        e.vars["sprint_squares"] = squares

    def rooted(self, e):
        """Stopped outright — which is what an ability that carries the hero asks
        about. A slow leaves it walking, just less far, so it is not this."""
        return (e.vars.get("rooted_at") is not None
                and e.vars.get("rooted_squares", self.ALL_SQUARES) >= self.ALL_SQUARES
                ) or self.bound(e)

    def move_budget(self, e):
        """How far this unit may actually walk this turn: its move, less whatever
        has been taken off its feet."""
        n = e.move_allowance
        if e.vars.get("sprint_at") is not None:
            n += e.vars.get("sprint_squares", 0)
        if e.vars.get("rooted_at") is not None:
            n -= e.vars.get("rooted_squares", self.ALL_SQUARES)
        return max(0, n)

    def bound(self, e):
        """Held in place for as long as somebody keeps holding it — 占星师's prophecy,
        which lasts while the seer lives rather than expiring on a turn. Checked
        lazily like the ability lock: a prophecy whose seer has fallen is no
        prophecy."""
        holder = self.entity(e.vars.get("bound_by")) if e.vars.get("bound_by") else None
        return holder is not None and holder.alive

    FREEZE_ROUNDS = 1

    def freeze(self, e, reason="", rounds=None):
        """Take this unit's next turn — or several, if an effect asks for more. The
        current round is untouched, so a unit caught after it has already acted
        loses the round that follows."""
        rounds = self.FREEZE_ROUNDS if rounds is None else rounds
        e.vars["frozen_at_round"] = self.round
        e.vars["frozen_rounds"] = rounds
        # Anything that expires on this unit's next turn must end now instead: its
        # next turn is not coming (美梦神's ward would otherwise silence forever).
        for side, eid in list(self.ability_lock.items()):
            if eid == e.id:
                self.ability_lock[side] = None
                self.log_line(f"{self.label(e)}'s ward fails as she seizes up.", quiet=True)
        turns = "turn" if rounds == 1 else f"{rounds} rounds"
        self.log_line(
            f"{self.label(e)} is struck by {reason} — it loses its next {turns}."
            if reason else f"{self.label(e)} cannot act for its next {turns}."
        )

    def frozen(self, e):
        at = e.vars.get("frozen_at_round")
        span = e.vars.get("frozen_rounds", self.FREEZE_ROUNDS)
        return at is not None and 0 < self.round - at <= span

    def frozen_rounds_left(self, e):
        at = e.vars.get("frozen_at_round")
        span = e.vars.get("frozen_rounds", self.FREEZE_ROUNDS)
        return 0 if at is None else max(0, at + span - self.round + 1)

    def mark_seen(self, side):
        self.present[side] = time.time()

    def both_present(self):
        """True only if both seats have polled within the last few seconds — i.e.
        both players actually have the page open right now. Always true in the
        single-driver modes (solo / test), where one person drives both seats."""
        if self.mode != "pvp":
            return True
        now = time.time()
        return all(now - t < 4.0 for t in self.present.values())

    def random_shots(self, e, dest):
        """Solo mode: pick the cell-locked attack's grids at random within range
        of where the hero will stand, one net per shot."""
        pool = [list(c) for c in self.topology.cells_within(tuple(dest), e.rng)]
        out = []
        for _ in range(e.hero.attacks_per_turn):
            k = min(e.grid, len(pool))
            out.append(random.sample(pool, k) if k else [])
        return out

    # --------------------------------------------------------------- test

    def _begin_test(self):
        """Skip draft/deploy: a 2v2 with the current test champions (padded with
        dummies) on the Left, two dummies on the Right, auto-placed, then straight
        into the opening/first round."""
        self.force_size = 2
        left = list(HEROES.TEST_HEROES)[:2]
        left += ["dummy"] * (2 - len(left))
        right = ["dummy", "dummy"]   # always dummies: no passives to muddy a test
        self.drafted = {LEFT: left, RIGHT: right}
        self.slots_total = {s: sum(HEROES.BY_KEY[k].slots for k in self.drafted[s])
                            for s in (LEFT, RIGHT)}
        self.begin_build()
        if self.phase == BUILD:
            # --test is a playground, not a game: give any builder a sensible pair
            # rather than making the player answer before anything is on the board.
            for s in (LEFT, RIGHT):
                while self.build and self.build["pending"][s]:
                    home = 2 if s == LEFT else 8
                    self.build_choose(s, {"cells": [[home, 3], [5, 3]]})
        for s in (LEFT, RIGHT):
            # Down a column, so a squad's bodies land adjacent to each other.
            col = 2 if s == LEFT else 8
            free = [(col, r) for r in range(1, self.topology.rows + 1)]
            self.setup_state[s]["placements"] = [
                {"key": k, "cell": list(c)} for k, c in zip(self.affordable_bodies(s), free)
            ]
            self.setup_state[s]["ready"] = True
        self.begin()
        # Start the champions under test with full AP so abilities are usable
        # right away (dummies have none anyway).
        under_test = set()
        for k in HEROES.TEST_HEROES:
            hero = HEROES.BY_KEY[k]
            under_test.update(hero.squad or [k])
        for e in self.entities:
            if e.key in under_test:
                e.ap = e.max_ap

    # -------------------------------------------------------------- draft

    def _begin_draft(self):
        self.phase = DRAFT
        self.drafted = {LEFT: [], RIGHT: []}
        self.draft = {"batch": 0, "pick": 0, "order": [], "shown": [], "used": set(), "picker": LEFT}
        self._deal_batch()

    def _deal_batch(self):
        """Reveal a fresh batch of never-seen champions for the current batch's
        pickers to choose from; leftovers are discarded (added to `used`)."""
        d = self.draft
        size, order = DRAFT_BATCHES[d["batch"]]
        pool = [h.key for h in HEROES.ROSTER if h.key not in d["used"]]
        d["shown"] = random.sample(pool, min(size, len(pool)))
        d["used"].update(d["shown"])
        d["order"] = list(order)
        d["pick"] = 0
        d["picker"] = order[0]

    def draft_pick(self, side, hero_key):
        if self.phase != DRAFT or not self.draft:
            return "Not the draft phase."
        d = self.draft
        if side != d["picker"]:
            return "It is not your pick."
        if hero_key not in d["shown"]:
            return "That hero is not on offer."
        self.drafted[side].append(hero_key)
        d["shown"].remove(hero_key)
        self.log_line(
            f"{'Left' if side == LEFT else 'Right'} drafts {HEROES.BY_KEY[hero_key].name}."
        )
        d["pick"] += 1
        if d["pick"] < len(d["order"]):
            d["picker"] = d["order"][d["pick"]]
        elif d["batch"] + 1 < len(DRAFT_BATCHES):
            d["batch"] += 1
            self._deal_batch()
        else:
            self._finish_draft()
        self.bump()
        return None

    def _finish_draft(self):
        self.draft = None
        self.force_size = len(self.drafted[LEFT])
        # One slot per card picked: field what you drafted, unless something you
        # drafted is priced at more than one.
        self.slots_total = {s: len(self.drafted[s]) for s in (LEFT, RIGHT)}
        self.log_line("Draft complete.")
        self.begin_build()

    # ------------------------------------------------------------- build
    # Between the draft and deployment. A hero whose ability is flagged `prebuild`
    # gets to change the board before anyone stands on it.

    def begin_build(self):
        pending = {LEFT: [], RIGHT: []}
        for side in (LEFT, RIGHT):
            for key in self.drafted[side]:
                card = HEROES.BY_KEY[key]
                for body in (card.squad or [key]):
                    for ab in HEROES.BY_KEY[body].abilities:
                        if getattr(ab, "prebuild", False):
                            pending[side].append({"hero": body, "ability_key": ab.key})
        self.build = {"pending": pending}
        if not pending[LEFT] and not pending[RIGHT]:
            self.build = None
            self.open_setup()
            return
        self.phase = BUILD
        self.log_line("The board is being made ready.")
        self.bump()

    def build_targeting(self, side, ab):
        """The build task's targeting, plus any option list only the engine can work
        out (探险家's island squares). Mirrors `ability_targeting` for the turn."""
        t = dict(ab.targeting)
        fn = getattr(ab, "build_cells", None)
        if fn is not None:
            t["cells"] = [list(c) for c in fn(self, side)]
        return t

    def build_ability(self, side):
        """The build task this side owes, as (hero key, ability), or (None, None)."""
        pend = self.build["pending"][side] if self.build else []
        if not pend:
            return None, None
        hero = HEROES.BY_KEY[pend[0]["hero"]]
        return hero, next(a for a in hero.abilities if a.key == pend[0]["ability_key"])

    def build_choose(self, side, params):
        if self.phase != BUILD or not self.build:
            return "Not the building phase."
        hero, ab = self.build_ability(side)
        if ab is None:
            return "Nothing for you to build."
        err = ab.validate_build(self, side, params)
        if err:
            return err
        ab.build_effects(self, side, params)
        self.build["pending"][side].pop(0)
        if not self.build["pending"][LEFT] and not self.build["pending"][RIGHT]:
            self.build = None
            self.open_setup()
        self.bump()
        return None

    def open_setup(self):
        self.phase = SETUP
        self.log_line("Deploy your forces.")

    def assign_draft(self, left_keys, right_keys):
        """Skip the interactive draft and hand each side its heroes directly. Goes to
        deployment, or to the building phase first if anybody drafted reshapes the
        board. Used by headless tests and scripted setups."""
        self.drafted = {LEFT: list(left_keys), RIGHT: list(right_keys)}
        self.force_size = len(left_keys)
        # Told what to field, not asked to choose: room for every card given.
        self.slots_total = {s: sum(HEROES.BY_KEY[k].slots for k in self.drafted[s])
                            for s in (LEFT, RIGHT)}
        self.draft = None
        self.begin_build()

    # -------------------------------------------------------------- setup

    def middle(self):
        return ((self.topology.cols + 1) // 2, (self.topology.rows + 1) // 2)

    def free_near(self, cell):
        """`cell` if nothing holds it, else the closest empty square to it. Both
        sides may draft the same board-placed hero (two 世界树), and they cannot
        both have the middle — the second one stands as near it as it can."""
        cell = tuple(cell)
        if self.occupant(cell) is None and self.topology.region(cell) is None:
            return cell
        free = [c for c in self.topology.all_cells() if self.can_enter(None, c)]
        if not free:
            return cell
        return min(free, key=lambda c: (self.topology.distance(cell, c), c))

    def board_places(self, how):
        """True if anybody drafted a card the board itself puts down this way
        (世界树 at the middle). Lets a build-time choice know what ground is
        already spoken for, before any of it is an entity."""
        return any(HEROES.BY_KEY[k].deploys == how
                   for s in (LEFT, RIGHT) for k in self.drafted[s])

    def deploy_bodies(self, side):
        """Every body the side must put on the board. A normal card contributes
        itself; a squad card (哥布林团伙) contributes its members, duplicates and
        all — so the force is 4 cards but can be 6 units. A card the board places
        itself (世界树) contributes nothing: its owner never positions it."""
        out = []
        for k in self.drafted[side]:
            hero = HEROES.BY_KEY[k]
            if hero.deploys:
                continue
            out.extend(hero.squad or [k])
        return out

    def spawn(self, hero, side, cell):
        """Put a new body on the board mid-match (洛基 arriving, and the board's own
        placements at the start). Returns the entity."""
        e = Entity(next(self._ids), hero, side, tuple(cell) if cell else None)
        self.entities.append(e)
        return e

    def bodies_needed(self, side):
        return len(self.deploy_bodies(side))

    # --- deployment slots ----------------------------------------------------
    # A force is a budget, not a list. Almost every card costs one slot, so
    # "deploy everything you drafted" and "spend your whole budget" are the same
    # sentence and nothing below changes what a normal game feels like. A card
    # priced higher (武器大师 at two) makes them different: it fits, but something
    # else has to stay on the bench, and which one is the player's to decide.

    def card_of(self, body_key):
        """Which drafted card put this body on the board. A squad member names its
        squad; everything else is its own card."""
        for k in (self.drafted[LEFT] + self.drafted[RIGHT]):
            hero = HEROES.BY_KEY[k]
            if body_key == k or (hero.squad and body_key in hero.squad):
                return k
        return body_key

    def slot_budget(self, side):
        """Slots to spend.

        A real draft hands you four cards and four slots, so a force of ordinary
        cards deploys exactly as it always did and a card priced at two costs you
        one of the others. A force handed over directly instead — `assign_draft`,
        which headless tests and `--test` use — is not a draft at all but an
        instruction to field precisely these, so it is given room for all of them."""
        return self.slots_total.get(side, len(self.drafted[side]))

    def placed_cards(self, side):
        """The drafted cards this side has committed to, in draft order. A card the
        board puts down itself (世界树) is committed the moment it is drafted —
        its owner never places it, so there is nothing to wait for."""
        down = {self.card_of(p["key"]) for p in self.setup_state[side]["placements"]}
        return [k for k in self.drafted[side]
                if k in down or HEROES.BY_KEY[k].deploys]

    def slots_used(self, side):
        return sum(HEROES.BY_KEY[k].slots for k in self.placed_cards(side))

    def slots_left(self, side):
        return self.slot_budget(side) - self.slots_used(side)

    def affordable_bodies(self, side):
        """The bodies of a force that actually fits the budget, taking cards in
        draft order and skipping any the remaining slots cannot cover. With every
        card at one slot this is just `deploy_bodies` — which is why nothing that
        drives a match had to learn about slots to keep working."""
        out, left = [], self.slot_budget(side)
        for k in self.drafted[side]:
            hero = HEROES.BY_KEY[k]
            if hero.slots > left:
                continue
            left -= hero.slots
            if hero.deploys:
                continue          # the board stands it up; its owner places nothing
            out.extend(hero.squad or [k])
        return out

    def bench(self, side):
        """Drafted cards with nothing on the board yet that the budget still has
        room for, and those it no longer does."""
        left = self.slots_left(side)
        down = set(self.placed_cards(side))
        rest = [k for k in self.drafted[side] if k not in down]
        return ([k for k in rest if HEROES.BY_KEY[k].slots <= left],
                [k for k in rest if HEROES.BY_KEY[k].slots > left])

    def place(self, side, hero_key, cell):
        cell = tuple(cell)
        st = self.setup_state[side]
        if st["ready"] or self.phase != SETUP:
            return "Force already locked in."
        if cell not in self.topology.deployment_zone(side):
            return "That cell is outside your deployment zone."
        if any(tuple(p["cell"]) == cell for p in st["placements"]):
            return "A hero already holds that cell."
        if hero_key not in HEROES.BY_KEY:
            return "No such hero."
        bodies = self.deploy_bodies(side)
        copies = bodies.count(hero_key)
        if not copies:
            return "That hero was not drafted to you."
        # Squads deploy several identical bodies, so the check is "how many of
        # this body do you still owe", not "is it already down".
        if sum(1 for p in st["placements"] if p["key"] == hero_key) >= copies:
            return "That hero is already deployed."
        # The budget, not the body count: the first body of a card spends its
        # slots, and the rest of a squad rides along on the same one.
        card = self.card_of(hero_key)
        if card not in self.placed_cards(side):
            cost = HEROES.BY_KEY[card].slots
            if cost > self.slots_left(side):
                return (f"{HEROES.BY_KEY[card].name} needs {cost} slots and you have "
                        f"{self.slots_left(side)} left — take something else off first.")
        st["placements"].append({"key": hero_key, "cell": list(cell)})
        self.bump()
        return None

    def unplace(self, side, cell):
        cell = tuple(cell)
        st = self.setup_state[side]
        if st["ready"]:
            return "Force already locked in."
        st["placements"] = [p for p in st["placements"] if tuple(p["cell"]) != cell]
        self.bump()
        return None

    def gang_placement_error(self, side):
        """初始位置：3相邻格 — a squad's bodies must go down as one connected
        blob (each touching at least one other), not scattered across the zone."""
        st = self.setup_state[side]
        for key in self.drafted[side]:
            hero = HEROES.BY_KEY[key]
            if not hero.squad:
                continue
            cells = [tuple(p["cell"]) for p in st["placements"] if p["key"] in hero.squad]
            if not self.topology.connected(cells):
                return f"{hero.name} must deploy on adjacent cells — keep the group connected."
        return None

    def lock_force(self, side):
        st = self.setup_state[side]
        # Every card you have committed to has to be all the way down — a squad
        # with two of its three placed is a half-deployed force, not a choice.
        for k in self.placed_cards(side):
            hero = HEROES.BY_KEY[k]
            if hero.deploys:
                continue
            for body in (hero.squad or [k]):
                want = (hero.squad or [k]).count(body)
                got = sum(1 for p in st["placements"] if p["key"] == body)
                if got < want:
                    return f"Finish deploying {hero.name} — {want - got} still to place."
        # And you field what you can afford. With every card at one slot this is
        # exactly the old rule, "deploy all of them"; it only bites when a card
        # is priced higher and something genuinely has to sit out.
        fits, _ = self.bench(side)
        if fits:
            return (f"You still have room for {HEROES.BY_KEY[fits[0]].name} — "
                    f"{self.slots_left(side)} slot(s) spare.")
        if not st["placements"] and not any(HEROES.BY_KEY[k].deploys
                                            for k in self.drafted[side]):
            return "Deploy your force first."
        err = self.gang_placement_error(side)
        if err:
            return err
        st["ready"] = True
        self.bump()
        if all(s["ready"] for s in self.setup_state.values()):
            self.begin()
        return None

    def begin(self):
        for side in (LEFT, RIGHT):
            for p in self.setup_state[side]["placements"]:
                hero = HEROES.BY_KEY[p["key"]]
                e = Entity(next(self._ids), hero, side, tuple(p["cell"]))
                self.entities.append(e)
        for side in (LEFT, RIGHT):
            for k in self.drafted[side]:
                hero = HEROES.BY_KEY[k]
                if hero.deploys == "centre":
                    self.spawn(hero, side, self.free_near(self.middle()))
                elif hero.deploys == "island":
                    isl = self.islands.get(side)
                    if isl:
                        self.spawn(hero, side, isl["stand"])
        self.log_line("Forces deployed. The board is live.")
        self.bus.emit(EV.MATCH_START, {})
        self.begin_opening()

    # ------------------------------------------------------------ opening
    # Abilities flagged `opening=True` fire once, after deployment but before the
    # first turn. Choice-free ones apply immediately; the rest queue a pick each
    # side resolves independently. Add a hero with an opening ability and it just
    # works — no changes here needed.

    def begin_opening(self):
        pending = {LEFT: [], RIGHT: []}
        for e in self.living():
            for ab in e.abilities:
                if not getattr(ab, "opening", False):
                    continue
                if ab.targeting.get("kind", "none") == "none":
                    self._apply_opening(e, ab, {})
                else:
                    pending[e.side].append({"entity": e.id, "ability_key": ab.key})
        self.opening = {"pending": pending}
        if not pending[LEFT] and not pending[RIGHT]:
            self.opening = None
            self.start_round()
        else:
            self.phase = OPENING
            self.bump()

    def _apply_opening(self, e, ab, params):
        ab.side_effects(self, e, params)
        if ab.use_limit is not None:
            uses = e.vars.setdefault("ability_uses", {})
            uses[ab.key] = uses.get(ab.key, 0) + 1

    def opening_choose(self, side, params):
        if self.phase != OPENING or not self.opening:
            return "Not the opening phase."
        pend = self.opening["pending"][side]
        if not pend:
            return "Nothing to choose."
        e = self.entity(pend[0]["entity"])
        ab = next(a for a in e.abilities if a.key == pend[0]["ability_key"])
        err = self.validate_targeting(e, ab, params)
        if err:
            return err
        self._apply_opening(e, ab, params)
        pend.pop(0)
        self.bump()
        if not self.opening["pending"][LEFT] and not self.opening["pending"][RIGHT]:
            self.opening = None
            self.start_round()
        return None

    def validate_targeting(self, e, ab, params, dest=None):
        """Every targeting kind checked in one place — turn actions and opening
        picks both come through here, so the two can never drift apart. Anything
        beyond targeting belongs in the ability's own `validate`."""
        t = self.ability_targeting(e, ab, tuple(dest) if dest else None)
        kind = t.get("kind", "none")

        if kind == "direction":
            if params.get("direction") not in t.get("options", []):
                return "Choose a direction."

        elif kind == "shape":
            # Which shape centred on the hero to use. The squares depend on where it
            # ends up standing, so only the choice itself is checked here.
            if params.get("direction") not in t.get("options", []):
                return "Choose a shape."

        elif kind == "any_cell":
            cell = params.get("cell")
            if not cell or not self.topology.in_bounds(tuple(cell)):
                return "Choose a cell on the board."
            # A hero only ever reaches squares on its own map: 潜水者 cannot bury a
            # charge on an island, and 探险家 digs nowhere else.
            if e.cells and not self.topology.same_region(tuple(cell), e.cell):
                return "That square is not part of your map."
            legal = t.get("cells")
            if legal is not None and list(cell) not in legal:
                return "Not a square this can be used on."

        elif kind == "two_units":
            a, b = params.get("first"), params.get("second")
            live = {x.id for x in self.living() if x.cells and x.flags["targetable"]}
            if a not in live or b not in live or a == b:
                return "Choose two different units, both on the board."

        elif kind == "any_unit":
            tt = self.entity(params.get("target"))
            if tt is None or not tt.alive or not tt.flags["targetable"]:
                return "Choose anyone still standing."
            allowed = t.get("options")
            if allowed is not None and tt.id not in allowed:
                return "That one is out of reach."

        elif kind in ("ally", "unit"):
            want_ally = kind == "ally"
            tt = self.entity(params.get("target"))
            ok = tt is not None and tt.alive and ((tt.side == e.side) == want_ally)
            # Something the board says cannot be touched (世界树) is no target for
            # anybody's ability, however it is aimed.
            if ok and not want_ally and not tt.flags["targetable"]:
                ok = False
            if not ok:
                return "Choose a living ally." if want_ally else "Choose a living enemy."
            allowed = t.get("options")
            if allowed is not None and tt.id not in allowed:
                return "That one is out of reach."

        elif kind == "cells":
            # The same marking an ordinary attack uses, borrowed by an ability that
            # paints a shape (水法师's 浸水). Range is measured from where the hero
            # will be standing, not where it is now.
            shots = params.get("shots") or []
            want = t.get("shots", 1)
            if len(shots) != want:
                return f"Mark cells for all {want} shot(s)."
            origin = tuple(dest) if dest else e.cell
            for cells in shots:
                cells = [tuple(c) for c in cells]
                # A net is a ceiling, not a quota — the same rule an ordinary
                # cell attack has always had, where a smaller net is the
                # attacker's own choice. An ability whose shape has to be exactly
                # so (水法师 painting four connected squares) says `exact`.
                if t.get("exact"):
                    if len(cells) != t["count"]:
                        return f"Mark exactly {t['count']} squares."
                elif len(cells) > t["count"]:
                    return f"At most {t['count']} squares."
                if len(set(cells)) != len(cells):
                    return "Squares must be distinct."
                for c in cells:
                    if not self.topology.in_bounds(c):
                        return "Square off the board."
                    if origin and self.topology.distance(origin, c) > t["range"]:
                        return "Out of range of where you will be standing."

        elif kind == "magnitude":
            cap = ab.magnitude_cap(e)
            if cap < 1:
                return f"{ab.name} cannot be used right now."
            x = params.get("amount")
            if not isinstance(x, int) or x < 1 or x > cap:
                return f"Choose an amount between 1 and {cap}."

        return None

    # -------------------------------------------------------------- round

    def start_round(self):
        self.round += 1
        for e in self.living():
            e.has_acted = False
        self.bus.emit(EV.ROUND_START, {})
        self.log_line(f"— Round {self.round} —")
        spread = self.board.spread_effects(self)
        if spread:
            self.log_line(f"The infection creeps outward — {spread} more square"
                          f"{'' if spread == 1 else 's'}.")
        self.detonate_due()
        if self.check_victory():
            return
        if self.interrupts:
            # A round-start hook wants an answer before anybody acts (探险家's
            # island arriving with one of every resource).
            self.pause_for_interrupts()
            return
        self.start_exchange()

    def detonate_due(self):
        """Every timer on the board whose round has come, all in one instant — two
        charges going off together kill together. Spent effects are cleared whether
        or not they caught anybody."""
        batch = []
        for cell, eff in self.board.due(self.round):
            fn = getattr(eff, "detonate", None)
            events = fn(self, cell) if fn else []
            self.board.remove_effect(cell, eff)
            self.log_line(f"A buried charge goes up — {eff.describe()}.")
            batch.extend(events)
        if batch:
            DMG.apply_batch(self, batch)

    def end_round(self):
        self.bus.emit(EV.ROUND_END, {})
        for e in self.entities:
            e.expire_modifiers(UNTIL_ROUND_END)
        self.start_round()

    def start_exchange(self):
        if self.phase == GAMEOVER:
            return
        left, right = self.unacted(LEFT), self.unacted(RIGHT)
        if not left and not right:
            self.end_round()
            return

        self.exchange += 1
        self.clear_exchange()
        self.take_snapshot()

        # A side with nobody left to act sits the exchange out; the other side
        # acts unopposed (spec 2).
        for side, pool in ((LEFT, left), (RIGHT, right)):
            if not pool:
                self.commits[side] = {"kind": "absent"}
        self.phase = COMMIT
        self.bump()

    def take_snapshot(self):
        """Opponent views render from this, so a turn-start fire tick during the
        commit phase does not leak which hero you selected (spec 7.10)."""
        self.snapshot = {
            e.id: {
                "hp": e.hp,
                "ap": e.ap,
                "cell": list(e.cell) if e.cells else None,
                "acted": e.has_acted,
                "alive": e.alive,
                # Snapshotted like HP: 蛮王's rage counter ticks at turn start, so
                # a live badge would betray which hero the opponent picked up.
                "status": HEROES.status_of(self, e),
            }
            for e in self.entities
        }

    # ------------------------------------------------------------- commit

    def select_hero(self, side, eid):
        if self.phase != COMMIT:
            return "Not the commitment phase."
        if self.commits[side] is not None:
            return "Order already sealed."
        e = self.entity(eid)
        if e is None or e.side != side or not e.alive:
            return "Not one of your heroes."
        if e.has_acted:
            return "That hero has already acted this round."
        if not e.flags["takes_turns"]:
            # Scenery (世界树) and anything still off the board (探险家's 土著 before
            # its island lands) never spends one of your exchanges.
            return f"{e.name} does not take turns."
        if self.frozen(e):
            return f"{e.name} cannot act for another {self.frozen_rounds_left(e)} round(s)."
        # Tentative: picking a hero is reversible — you can switch to another (or
        # deselect) freely until you commit. The turn only truly starts (and fire
        # resolves) at commit time.
        self.selected[side] = eid
        self.bump()
        return None

    def deselect(self, side):
        if self.phase == COMMIT and self.commits[side] is None:
            self.selected[side] = None
            self.bump()
            return None
        return "Order already sealed."

    def gang_of(self, e):
        """The squad key this unit belongs to, or None for a lone hero."""
        return e.hero.gang if e is not None else None

    def gang_members(self, side, gang):
        """Living members of one gang, in a stable order (deployment order)."""
        return [e for e in self.living(side) if e.hero.gang == gang]

    def turn_actors(self, e):
        """Everyone whose turn it is when this unit is picked up: a gang brings its
        whole crew, anyone else brings only themselves. A frozen goblin is not part
        of the turn — it neither owes an order nor gets to act."""
        gang = self.gang_of(e)
        if not gang:
            return [e]
        return [g for g in self.gang_members(e.side, gang) if not self.frozen(g)]

    def set_ability_lock(self, caster):
        """Silence the enemy side until this caster's next turn begins."""
        foe = other_side(caster.side)
        self.ability_lock[foe] = caster.id
        self.log_line(
            f"{self.label(caster)} wards the field — no enemy abilities until her next turn."
        )

    def ability_locked(self, side):
        """The caster silencing this side, or None. Checked lazily: a ward whose
        caster has fallen is no ward at all."""
        eid = self.ability_lock.get(side)
        if eid is None:
            return None
        caster = self.entity(eid)
        if caster is None or not caster.alive:
            self.ability_lock[side] = None
            return None
        return caster

    def forfeit_turn(self, side):
        self.commits[side] = {"kind": "dead"}
        self.maybe_resolve()

    def expire_owner_modifiers(self, owner):
        """Drop everything this unit hung on the board that lasts \"until my next
        turn\" — wherever it landed. Any hero can use it: attach the modifier with
        source=<the caster> and duration=UNTIL_OWNER_NEXT_TURN."""
        for e in self.entities:
            keep = [m for m in e.modifiers
                    if not (m.duration == UNTIL_OWNER_NEXT_TURN and m.source is owner)]
            if len(keep) != len(e.modifiers):
                e.modifiers = keep

    #: What a hero charges at the start of each of its own turns.
    AP_PER_TURN = 1

    def turn_ap(self, e):
        """The AP this hero will hold once its turn opens — what it has now, plus
        the charge that turn brings. The menu is built before the turn actually
        starts, so without this an ability the hero can pay for the moment it
        acts would be greyed out right up until it acted."""
        if e.has_acted or not e.alive or self.turn_started.get(e.side):
            return e.ap
        return min(e.max_ap, e.ap + self.AP_PER_TURN)

    def run_turn_start(self, e):
        """Board effects resolve before the hero decides anything. Returns False
        if the unit died here — it forfeits its action."""
        # The charge comes at the *start* of a turn, so a hero may spend it on the
        # turn it arrives rather than banking it for the next one.
        before = e.ap
        e.gain_ap(self.AP_PER_TURN)
        if e.ap != before:
            self.log_line(f"{self.label(e)} charges to {e.ap}/{e.max_ap} AP.", quiet=True)
        self.expire_owner_modifiers(e)
        for side, eid in self.ability_lock.items():
            if eid == e.id:            # her next turn has come: the ward lifts
                self.ability_lock[side] = None
                self.log_line(f"{self.label(e)}'s ward fades.", quiet=True)
        self.bus.emit(EV.TURN_START, {"entity": e})
        # One creature, one bite: 蛇帝's two halves take the same turn, and ground
        # under both of them is still one patch of ground under one hero.
        body = DMG.pool_holder(self, e) or e
        stamp = (self.round, self.exchange)
        already = body.vars.get("ground_bite") == stamp
        body.vars["ground_bite"] = stamp
        ground = self.board.turn_start_events(e.cell, e) if (e.cells and not already) else []
        if ground:
            DMG.apply_batch(self, ground)
            for ev in ground:
                self.log_line(
                    f"{self.label(e)} starts its turn on bad ground: "
                    f"{ev.amount}{' ' + ev.element if ev.element else ''}."
                )
        # Anything that can kill at turn start forfeits the turn — a burning tile,
        # or a passive that expires lethally (蛮王's 背水 burnout).
        if not e.alive:
            self.log_line(f"{self.label(e)} is destroyed before it can act.")
            return False
        return True

    def move_anchor_of(self, e):
        """For a unit placed relative to another (蛇帝's tail): which unit, and by
        what rule. The client needs this to work out legal squares while the order
        is still being drafted — a position it can never propose is better than one
        it proposes and has refused."""
        for p in e.passives:
            fn = getattr(p, "move_anchor", None)
            if fn is None:
                continue
            got = fn(self, e)
            if got:
                return got
        return None

    def move_zone(self, e, pending=None):
        """(cells, dictated) for a unit whose square is placed by a passive rather
        than walked to — 蛇帝's tail, which goes wherever the head ends up. Only a
        passive that actually returns a list counts: one that answers None for this
        body (the head carries the same passive as the tail) leaves it walking
        normally. `pending` is the destinations already sealed this turn."""
        for p in e.passives:
            fn = getattr(p, "move_zone", None)
            if fn is None:
                continue
            zone = fn(self, e, pending or {})
            if zone is not None:
                return [list(c) for c in zone], True
        return None, False

    def legal_moves(self, e, pending=None):
        # A rooted unit has nowhere it may put itself. This also covers a bodiless
        # hero's squares to appear on, since those come through here.
        if self.rooted(e):
            return []
        zone, dictated = self.move_zone(e, pending)
        if dictated:
            return zone

        """Every cell reachable within the hero's move allowance, walking one step
        at a time through cells empty in the current snapshot (you cannot move
        through an occupied square). Most heroes move 1, but the budget is the
        `move` stat, so buffs like 激励 just work."""
        if not e.alive:
            return []
        if not e.cells:
            # No square of its own, but it may still have somewhere to appear
            # (鬼魂 taking flesh). Passives publish those squares; stepping onto
            # one *is* the move, so the rest of the turn plays out as normal.
            out = []
            for p in e.passives:
                fn = getattr(p, "manifest_cells", None)
                if fn:
                    out.extend(fn(self, e))
            return [list(c) for c in out]
        start = e.cell
        seen = {start}
        frontier = [start]
        out = []
        for _ in range(self.move_budget(e)):
            nxt = []
            for cell in frontier:
                for n in self.topology.neighbours(cell, e):
                    # Crossed and stood on are different questions: a door may be
                    # walked over by the far side but not stopped on, so it keeps
                    # its place in the walk and stays out of the destinations.
                    if n in seen or not self.can_cross(e, n):
                        continue
                    seen.add(n)
                    nxt.append(n)
                    if self.can_enter(e, n):
                        out.append(list(n))
            frontier = nxt
        return out

    def sole_actions(self, e):
        """Ability keys that are the *only* thing this hero may do right now, or
        None for the ordinary case. A passive answers for its own hero (探险家 while
        its island is still off the board does nothing but dig), so neither holding
        nor attacking is offered."""
        for p in e.passives:
            fn = getattr(p, "sole_actions", None)
            if fn is None:
                continue
            keys = fn(self, e)
            if keys:
                return {"ability:" + k for k in keys}
        return None

    def action_menu(self, e):
        """What this hero could commit to, given the AP its turn will open with —
        `turn_ap`, not `e.ap`, since the charge lands before it acts."""
        if not e.alive:
            return []
        out = [{"key": "none", "name": "Hold", "ap_cost": 0, "targeting": {"kind": "none"}}]
        # A bodiless hero can still plan an attack if it is about to take flesh.
        mode = ATK.mode_for(e) if (e.cells or self.legal_moves(e)) else None
        if mode is not None:
            out.append(mode.menu_entry(self, e))
        uses = e.vars.get("ability_uses", {})
        for ab in e.abilities:
            if getattr(ab, "opening", False) or getattr(ab, "prebuild", False):
                continue  # these fire before the match, not on a turn
            if ab.use_limit is not None and uses.get(ab.key, 0) >= ab.use_limit:
                continue  # a spent limited ability disappears from the menu
            if not ab.available(self, e):
                continue  # not yet, or no longer, part of this hero\'s kit
            if ab.carries_self and self.rooted(e):
                continue  # rooted: it cannot carry itself anywhere
            warder = self.ability_locked(e.side)
            out.append(
                {
                    "key": "ability:" + ab.key,
                    "name": ab.name,
                    "ap_cost": ab.ap_cost,
                    "targeting": self.menu_targeting(e, ab),
                    "self_move": ab.self_move,
                    "affordable": self.turn_ap(e) >= ab.ap_cost and warder is None,
                    "blocked": f"warded by {warder.name}" if warder else None,
                    "text": ab.blurb,
                }
            )
        only = self.sole_actions(e)
        if only is not None:
            out = [x for x in out if x["key"] in only]
        return out

    def commit(self, side, payload):
        if self.phase != COMMIT:
            return "Not the commitment phase."
        if self.commits[side] is not None:
            return "Order already sealed."
        eid = self.selected[side]
        if eid is None:
            return "Choose a hero first."
        e = self.entity(eid)
        if e is None or not e.alive or e.side != side or e.has_acted:
            return "Choose a living, un-acted hero."

        # Committing is the point of no return: the turn starts now and board
        # effects (fire) resolve. A unit killed here forfeits its action; a gang
        # only forfeits if the board wipes out every goblin at once.
        actors = self.turn_actors(e)
        if not self.turn_started[side]:
            self.turn_started[side] = True
            survivors = [a for a in actors if self.run_turn_start(a)]
            if not survivors:
                self.forfeit_turn(side)
                return None
            actors = survivors

        if self.gang_of(e):
            return self._commit_gang(side, e, actors, payload)

        order, err = self._build_order(e, payload)
        if err:
            return err

        self.commits[side] = dict(order, kind="action")
        self.bump()
        self.maybe_resolve()
        return None

    def turn_choices(self, e):
        """Free picks a unit's passives make when its turn begins (杂货店爷爷's
        handout). They are not actions — they ride along with the order, so a
        hero still moves and attacks in the same turn. A choice with no legal
        options simply doesn't appear."""
        out = []
        for p in e.passives:
            fn = getattr(p, "turn_choice", None)
            ch = fn(self, e) if fn else None
            if ch and ch.get("options"):
                out.append(ch)
        return out

    def validate_choices(self, e, payload):
        picks = payload.get("choices") or {}
        for ch in self.turn_choices(e):
            got = picks.get(ch["key"])
            if ch.get("optional") and got in (None, ""):
                continue          # a sale nobody wanted to make
            if got not in ch["options"]:
                return f"{ch['name']}: pick one."
        return None

    def apply_choices(self, e, payload):
        """Settle this unit's free picks. Run as its turn *begins* — before the
        move and the action are judged — because that is when they happen: the
        fee 军火商人 charges for a weapon is AP in its own pocket for the rest of
        the same turn, and its shot may burn it.

        Once per turn, whatever happens next. A commit refused for a bad order
        leaves the picks made, exactly the way it leaves the turn started."""
        if e.vars.get("choices_exchange") == self.exchange:
            return
        picks = (payload or {}).get("choices") or {}
        e.vars["choices_exchange"] = self.exchange
        for ch in self.turn_choices(e):
            target = picks.get(ch["key"])
            if target not in ch["options"]:
                continue
            for p in e.passives:
                fn = getattr(p, "apply_choice", None)
                if fn:
                    fn(self, e, ch["key"], target)

    def _build_order(self, e, payload, pending=None):
        """Validate one unit's move + action. Returns (order, error). `pending` is
        the destinations sealed earlier in the same gang turn, for a unit whose
        legal squares depend on where a comrade is going."""
        dest = payload.get("destination")
        dest = tuple(dest) if dest else e.cell
        # A unit whose square is dictated by another's has to justify standing
        # still too: if the head walks off, the tail cannot simply stay behind.
        _zone, dictated = self.move_zone(e, pending)
        if dest != e.cell or dictated:
            if dest not in [tuple(c) for c in self.legal_moves(e, pending)]:
                # A dictated zone belongs to whichever passive published it, so the
                # refusal names the squares on offer rather than guessing why.
                return None, ("That is not one of the squares this hero may hold."
                              if dictated else "That cell is not an open adjacent square.")

        action = payload.get("action") or {"key": "none"}
        key = action.get("key", "none")
        # Solo mode aims normal attacks for you — random grids within range.
        if self.mode == "self" and key == "attack" and e.hero.attack["mode"] == HEROES.CELL:
            action = dict(action, shots=self.random_shots(e, dest))
        # The free picks are settled first, then the action is judged against
        # what they leave behind. 军火商人 collects its fee as its turn opens, so
        # by the time its shot is priced the fee is its own to feed into it.
        err = self.validate_choices(e, payload)
        if err:
            return None, err
        self.apply_choices(e, payload)
        err = self.validate_action(e, dest, action)
        if err:
            return None, err
        return {
            "entity": e.id,
            "destination": list(dest) if dest else None,   # None: nothing on the board
            "action": action,
            "choices": payload.get("choices") or {},
        }, None

    def _commit_gang(self, side, picked, actors, payload):
        """A gang seals one order per living goblin. The list order IS the acting
        order (团伙回合内，所有存活哥布林按你选择的顺序行动)."""
        raw = payload.get("orders")
        if not isinstance(raw, list) or not raw:
            return "Give every goblin an order."
        living = {a.id: a for a in actors}
        seen = []
        orders = []
        # Destinations sealed so far this turn, so a body that has to stand beside
        # a comrade (蛇帝's tail) is checked against where that comrade is going,
        # not where it currently stands.
        pending = {}
        for item in raw:
            e = self.entity(item.get("entity"))
            if e is None or e.side != side or self.gang_of(e) != self.gang_of(picked):
                return "That goblin cannot act this turn."
            if e.id not in living:
                # Burned down by a tile the instant the turn started: its order is
                # dropped and the rest of the gang carries on. The client cannot
                # have known — fire only resolves at commit.
                continue
            if e.id in seen:
                return "Each goblin acts once."
            seen.append(e.id)
            order, err = self._build_order(e, item, pending)
            if err:
                return f"{e.name}: {err}"
            orders.append(order)
            pending[e.id] = tuple(order["destination"]) if order["destination"] else None
        missing = [a for a in actors if a.id not in seen]
        if missing:
            return f"Still waiting on orders for {', '.join(a.name for a in missing)}."
        # Most squads act in whatever order you like; one whose members declare a
        # rank does not (蛇帝's head leads and the tail follows it).
        ranks = [self.entity(i).hero.gang_rank for i in seen]
        if any(r is not None for r in ranks) and ranks != sorted(r or 0 for r in ranks):
            return "This one acts in a fixed order — the head leads."

        self.commits[side] = {
            "kind": "gang",
            "gang": self.gang_of(picked),
            "entity": orders[0]["entity"],
            "orders": orders,
        }
        self.bump()
        self.maybe_resolve()
        return None

    def menu_targeting(self, e, ab):
        """The ability's targeting for the menu, worked out for every square the
        hero could be standing on when it goes off — its own, and everywhere it
        could walk to. Movement resolves before the ability does, so an option
        list built only from where it stands now offers throws it cannot make and
        hides ones it can. The client picks the entry for the square it is aiming
        from; the server judges the order against the same one."""
        t = self.ability_targeting(e, ab)
        positional = [k for k in ("choices", "options", "cells") if k in t]
        if not positional:
            return t
        at = {}
        for cell in [e.cell] + [tuple(c) for c in self.legal_moves(e)]:
            if cell is None:
                continue
            v = self.ability_targeting(e, ab, cell)
            at[f"{cell[0]},{cell[1]}"] = {k: v[k] for k in positional}
        t["at"] = at
        return t

    def ability_targeting(self, e, ab, origin=None):
        """The ability's targeting, plus any live options only the engine can work
        out — 冲撞's chargeable lanes, with landing square and who gets trampled.

        `origin` is the square the hero will be standing on when the ability goes
        off. Everything positional is measured from there, so an order is offered
        and judged against where the hero *will* be, not where it is leaving."""
        t = dict(ab.targeting)
        if t.get("kind") == "two_units":
            t["options"] = [e.id for e in self.living()
                            if e.cells and e.flags["targetable"]]
        if hasattr(ab, "lanes"):
            t["choices"] = ab.lanes(self, e, origin)
        if hasattr(ab, "shapes"):
            t["choices"] = ab.shapes(self, e, origin)
        if hasattr(ab, "cells"):
            t["cells"] = [list(c) for c in ab.cells(self, e, origin)]
        if hasattr(ab, "throwable"):
            t["options"] = ab.throwable(self, e, origin)
        if hasattr(ab, "blessable"):
            t["options"] = ab.blessable(self, e, origin)
        return t

    def validate_action(self, e, dest, action):
        key = action.get("key", "none")
        if key == "none":
            return None      # holding is always legal, whatever the attack mode
        if key == "attack":
            if not e.cells and dest is None:
                return f"{e.name} has no body to attack with — take flesh first."
            mode = ATK.mode_for(e)
            if mode is None:
                return f"{e.name} has no attack."
            return mode.validate(self, e, dest, action)
        if key.startswith("ability:"):
            abkey = key.split(":", 1)[1]
            ab = next((a for a in e.abilities if a.key == abkey), None)
            if ab is None:
                return "This hero has no such ability."
            if getattr(ab, "opening", False):
                return "That ability only fires at the opening."
            warder = self.ability_locked(e.side)
            if warder is not None:
                return f"{warder.name}'s ward blocks abilities until she acts again."
            if e.ap < ab.ap_cost:
                return f"Needs {ab.ap_cost} AP."
            if ab.use_limit is not None and e.vars.get("ability_uses", {}).get(ab.key, 0) >= ab.use_limit:
                return "That ability is spent for the match."
            if ab.carries_self and self.rooted(e):
                return f"{e.name} cannot move this turn."
            if ab.self_move and e.cells and dest != e.cell:
                return f"{ab.name} does the moving — leave your movement as hold."
            # Where it will be standing when this resolves — movement is applied
            # first, so anything positional must be judged from there.
            return (self.validate_targeting(e, ab, action, dest)
                    or ab.validate(self, e, action, dest or e.cell))
        return "Unknown action."

    # ---- 武器大师 weapon stances -------------------------------------------

    def _validate_weapon(self, e, dest, action, key):
        w = HEROES.WEAPONS_BY_KEY.get(action.get("weapon"))
        if w is None:
            return "Choose a weapon."
        if key != "attack":          # a weapon is always the attack; stance rides along
            return None
        if w["mode"] == "cells":
            cells = (action.get("shots") or [[]])[0] or []
            if len(cells) > w["cells"]:
                return f"At most {w['cells']} cells."
            for c in cells:
                c = tuple(c)
                if not self.topology.in_bounds(c):
                    return "Cell off the board."
                if self.topology.distance(dest, c) > w["range"]:
                    return "Cell out of range of where you will be standing."
            if len(set(tuple(c) for c in cells)) != len(cells):
                return "Cells must be distinct."
        return None

    def surround8(self, cell):
        """The eight squares around this one, diagonals included, on the same map.
        The one definition of "right beside it" — 大力士 asks it what it can seize,
        and 武器大师's spread asks it what it catches."""
        c, r = cell
        return [
            (c + dc, r + dr)
            for dc in (-1, 0, 1) for dr in (-1, 0, 1)
            if (dc or dr) and self.topology.in_bounds((c + dc, r + dr))
            and self.topology.same_region((c + dc, r + dr), cell)
        ]

    def shape_cells(self, origin, shape):
        """The squares a named shape covers around a square. One definition, so an
        area attack and an ability offering a choice of shapes can never disagree
        about what "your row" means."""
        origin = tuple(origin)
        if shape == "board":
            return self.topology.all_cells()
        if shape == "surround8":
            return self.surround8(origin)
        if shape == "row":
            return self.topology.row(origin[1])
        if shape == "column":
            return self.topology.column(origin[0])
        return []

    def attack_shape(self, e, origin=None):
        """The squares an area attack covers from a given square. Named shapes, so a
        future hero can sweep a cross or a row without new plumbing. A swing never
        catches the square it is thrown from."""
        origin = tuple(origin) if origin else e.cell
        if origin is None:
            return []
        shape = e.attack_spec.get("shape", "surround8")
        return [c for c in self.shape_cells(origin, shape) if c != origin]

    def _apply_stance(self, e, w):
        buff = w.get("buff")
        if buff == "guard":
            e.vars["stance_dr"] = 2
            self.log_line(f"{self.label(e)} raises 剑盾 — +2 damage reduction.")
        elif buff == "ward":
            e.vars["ability_immune"] = True
            self.log_line(f"{self.label(e)} draws 太刀 — warded against enemy abilities.")

    def _build_weapon(self, e, action, intended):
        w = HEROES.WEAPONS_BY_KEY.get(action.get("weapon"))
        if w is None:
            return [ACT.NullAction()]
        self._apply_stance(e, w)
        if action.get("key") != "attack":
            return [ACT.NullAction()]
        if w["mode"] == "cells":
            cells = (action.get("shots") or [[]])[0] or []
            return [ACT.CellLockedAttack(e, cells, intended, amount=w["atk"])]
        if e.cell is None:
            # Nothing on the board to swing from: the master was killed on its way
            # (a mine under the square it moved to), or has no body yet. The marked
            # form above is fine without one — it fires at squares it named — but a
            # pattern measured from the hero itself has no centre.
            return [ACT.NullAction()]
        if w["mode"] == "row":
            return [ACT.AreaAttack(e, self.topology.row(e.cell[1]), w["atk"])]
        if w["mode"] == "surround8":
            return [ACT.CellLockedAttack(e, self.surround8(e.cell), e.cell, amount=w["atk"])]
        return [ACT.NullAction()]

    def maybe_resolve(self):
        if all(v is not None for v in self.commits.values()):
            self.resolve()

    # ---------------------------------------------------------- resolution

    def orders_of(self, commit):
        """Every unit order in a commit — one for a lone hero, one per goblin for
        a gang, none for an absent or dead side."""
        if commit["kind"] == "action":
            return [commit]
        if commit["kind"] == "gang":
            return commit["orders"]
        return []

    def resolve(self):
        reveal = {}
        movers = []
        for side in (LEFT, RIGHT):
            c = self.commits[side]
            orders = self.orders_of(c)
            if not orders:
                reveal[side] = None
                continue
            actors = [self.entity(o["entity"]) for o in orders]
            lead = actors[0]
            if c["kind"] == "gang":
                card = HEROES.BY_KEY[c["gang"]]
                reveal[side] = {
                    "key": card.key,
                    "hero": card.name,
                    "hero_en": card.name_en,
                    "action": orders[0]["action"],
                    "crew": [a.name for a in actors],
                    "hits": [],
                }
            else:
                reveal[side] = {
                    "key": lead.key,
                    "hero": lead.name,
                    "hero_en": lead.name_en,
                    "action": c["action"],
                    "hits": [],   # filled during resolution: [{target, amount}, ...]
                }
            for o, a in zip(orders, actors):
                if o["destination"] is None:
                    continue        # nothing on the board to move (鬼魂 while bodiless)
                movers.append((side, a, tuple(o["destination"])))
        self.last_reveal = reveal

        self.apply_movement(movers)
        # An ability may want the player to say where it lands, now that everyone
        # has stopped moving. If anything does, resolution pauses here and picks up
        # in `_resolve_after_moves` once the choices are in.
        if self.collect_move_choices():
            self.phase = MOVE_CHOICE
            self.bump()
            return
        self._resolve_after_moves()

    def collect_move_choices(self):
        """Ask every acting ability whether it needs a square chosen. A choice with
        only one square left is taken silently — there is nothing to decide."""
        self.move_choices = {LEFT: [], RIGHT: []}
        for side in (LEFT, RIGHT):
            for o in self.orders_of(self.commits[side]):
                key = (o.get("action") or {}).get("key", "none")
                if not key.startswith("ability:"):
                    continue
                e = self.entity(o["entity"])
                if e is None or not e.alive:
                    continue
                ab = next((a for a in e.abilities if a.key == key.split(":", 1)[1]), None)
                fn = getattr(ab, "move_choice", None)
                task = fn(self, e, o["action"]) if fn else None
                opts = (task or {}).get("options") or []
                if not opts:
                    continue
                if len(opts) == 1:
                    self.move_picks[e.id] = tuple(opts[0])
                    continue
                self.move_choices[side].append(dict(task, entity=e.id))
        return any(self.move_choices.values())

    def choose_move(self, side, cell):
        """Answer the first movement choice pending for this side."""
        if self.phase != MOVE_CHOICE:
            return "Nothing to decide."
        pend = self.move_choices[side]
        if not pend:
            return "Nothing pending for you."
        task = pend[0]
        if cell is None or list(cell) not in task["options"]:
            return "Not one of the squares on offer."
        self.move_picks[task["entity"]] = tuple(cell)
        pend.pop(0)
        self.bump()
        if not any(self.move_choices.values()):
            self._resolve_after_moves()
        return None

    def _resolve_after_moves(self):
        """Everything from the end of movement to the first blow. Split out so the
        pause for a movement choice can pick up exactly where it left off."""
        self.apply_move_effects()
        # Anything the board did to whoever walked onto it. Applied as one batch
        # once every unit has finished moving, so mines under two movers land in
        # the same instant and mutual kills work as they do everywhere else.
        if self.pending_hazards:
            batch, self.pending_hazards = self.pending_hazards, []
            DMG.apply_batch(self, batch)
            if self.check_victory():
                return

        plan = {LEFT: [], RIGHT: []}
        for side in (LEFT, RIGHT):
            c = self.commits[side]
            for o in self.orders_of(c):
                # Sequential: goblin #1's instances sit at index 0 alongside the
                # enemy's action, #2's after them, and so on.
                plan[side].extend(self.build_instances(self.entity(o["entity"]), o))

        self.res = {
            "plan": plan,
            "index": 0,
            "picks": {LEFT: None, RIGHT: None},
            "options": {LEFT: [], RIGHT: []},
        }
        self.advance_resolution()

    def apply_move_effects(self):
        """Abilities that reposition rather than damage resolve here, in the same
        instant as ordinary movement and before any attack is worked out. That is
        what lets 魔术师's 转移 pull somebody into a square the enemy already
        marked — attacks find their victims from live positions, so whoever ends
        up standing there is the one who is hit."""
        for side in (LEFT, RIGHT):
            for o in self.orders_of(self.commits[side]):
                key = (o.get("action") or {}).get("key", "none")
                if not key.startswith("ability:"):
                    continue
                e = self.entity(o["entity"])
                if e is None or not e.alive:
                    continue
                ab = next((a for a in e.abilities if a.key == key.split(":", 1)[1]), None)
                fn = getattr(ab, "move_effects", None)
                if fn:
                    fn(self, e, o["action"])

    def claim_square(self, units):
        """Who wins a square two or more units reach for at once. The bigger frame
        takes it first, then whoever is in better shape, then the harder hitter —
        all public, so a contest can be read before it is entered. A coin decides
        only if every one of those ties."""
        best = max((u.max_hp, u.hp, u.atk) for u in units)
        tied = [u for u in units if (u.max_hp, u.hp, u.atk) == best]
        return tied[0] if len(tied) == 1 else random.choice(tied)

    def apply_movement(self, movers):
        """Everyone moves in the same instant — one hero a side normally, but a
        gang turn puts several movers on one side. Two units reaching for the same
        square do not both fail: the stronger claim takes it and the others simply
        stay where they are. A destination is only ever a square that was empty
        when orders were sealed, so nobody can swap or chain through anyone."""
        going = [(e, d) for _s, e, d in movers if d != e.cell]
        by_square = {}
        for e, d in going:
            by_square.setdefault(d, []).append(e)

        for d, units in by_square.items():
            winner = units[0] if len(units) == 1 else self.claim_square(units)
            for e in units:
                if e is not winner:
                    self.log_line(
                        f"{self.label(e)} is shouldered aside by "
                        f"{self.label(winner)} — no movement."
                    )
            if self.occupant(d) is not None:
                self.log_line(f"{self.label(winner)} is blocked — no movement.")
                continue
            frm = winner.cell
            winner.set_cell(d)
            self.bus.emit(EV.AFTER_MOVE, {"entity": winner, "from": frm, "to": d})
            if frm is not None:     # a mover with no origin has just taken flesh
                self.log_line(f"{self.label(winner)} moves.")

    def build_instances(self, e, commit):
        action = commit["action"]
        key = action.get("key", "none")
        # None only for a unit with no square at all (鬼魂 while bodiless); its
        # actions are targeted, never positional, so the origin is unused.
        intended = tuple(commit["destination"]) if commit["destination"] else e.cell
        if key == "none":
            return [ACT.NullAction()]     # no weapon drawn, so no stance either
        if key == "attack":
            mode = ATK.mode_for(e)
            spend = int(action.get("spend") or 0)
            if spend and e.attack_spec.get("fuel"):
                e.ap = max(0, e.ap - spend)      # fed into the shot, point for point
                self.log_line(f"{self.label(e)} feeds {spend} into the shot.", quiet=True)
            built = mode.build(self, e, intended, action) if mode else [ACT.NullAction()]
            arms = e.vars.get("arms")
            if arms and arms.get("once") and mode is not None:
                # Spent by being fired, hit or miss — but only once the shot itself
                # has been worked out, since the weapon is what shapes it.
                e.vars["arms"] = dict(arms, attack={"mode": None}, spent=True)
                self.log_line(f"{self.label(e)} has nothing left to fire with.")
            return built
        if key.startswith("ability:"):
            abkey = key.split(":", 1)[1]
            ab = next((a for a in e.abilities if a.key == abkey), None)
            if ab is None:
                # It had this when the order was sealed and does not now: movement
                # resolves first, and a hero can give an ability up by moving
                # (鬼魂 surrenders 附身 the moment it takes flesh). The order simply
                # comes to nothing rather than bringing the exchange down.
                self.log_line(f"{self.label(e)} no longer has that to give.")
                return [ACT.NullAction()]
            e.ap = max(0, e.ap - ab.ap_cost)
            if ab.use_limit is not None:
                uses = e.vars.setdefault("ability_uses", {})
                uses[ab.key] = uses.get(ab.key, 0) + 1
            self.log_line(f"{self.label(e)} spends {ab.ap_cost} AP on {ab.name}.")
            # An ability may resolve as real action instances rather than straight
            # to damage. That is the only way to get a victim picked *after*
            # everyone has moved — the resolution pauses for instances, not for
            # `build_damage` — which is what an ability shaped like an attack
            # needs (忍者's 雷切 marks a net and takes one body out of it).
            own = getattr(ab, "instances", None)
            if own is not None:
                return own(self, e, action, intended)
            return [ACT.AbilityAction(e, ab, action, 0)]
        return [ACT.NullAction()]

    def advance_resolution(self):
        res = self.res
        while True:
            idx = res["index"]
            longest = max(len(res["plan"][LEFT]), len(res["plan"][RIGHT]))
            if idx >= longest:
                self.finish_exchange()
                return

            insts = {}
            for side in (LEFT, RIGHT):
                plan = res["plan"][side]
                inst = plan[idx] if idx < len(plan) else None
                if inst is not None and not inst.is_live():
                    inst = None
                insts[side] = inst

            waiting = False
            for side in (LEFT, RIGHT):
                inst = insts[side]
                if inst is None or not inst.needs_pick:
                    res["options"][side] = []
                    continue
                if self.victims_complete(side):
                    continue
                opts = inst.eligible_victims(self)
                res["options"][side] = [o.id for o in opts]
                want = min(getattr(inst, "max_victims", 1), len(opts))
                if len(opts) == 0:
                    res["picks"][side] = []
                    self.log_line(f"{self.label(inst.attacker)} fires — nobody there.")
                elif len(opts) <= want:
                    # No more enemies in the net than the shot can hit: they all take
                    # it and there is nothing to decide.
                    res["picks"][side] = [o.id for o in opts]
                else:
                    waiting = True

            if waiting:
                self.phase = VICTIM
                self.bump()
                return

            batch = []
            for side in (LEFT, RIGHT):
                inst = insts[side]
                if inst is None:
                    continue
                pick = res["picks"][side]
                # One instance can hit several victims (猎人 after its first kill),
                # so damage is built once per victim and all of it lands together.
                victims = ([self.entity(p) for p in pick]
                           if isinstance(pick, list) else [None])
                for victim in victims:
                    for ev in inst.build_damage(self, victim):
                        batch.append((side, inst, ev))
            # Apply every hit of this instant, but do NOT resolve deaths yet: an
            # ally healing the same target in the same instant has to land first,
            # or a hero saved on the exact turn it drops would die anyway.
            applied = []
            for _s, _i, ev in batch:
                applied.append((ev, DMG.deal(self, ev)))
            self.instant = {"batch": batch, "insts": insts, "applied": applied}
            if self.offer_saves():
                # Somebody can be stepped in front of. Everything after this point
                # waits until that is settled.
                self.phase = INTERRUPT
                self.bump()
                return
            if self.finish_instant():
                return
            continue

    def finish_instant(self):
        """Everything after the blows have landed — logging, side effects, the board's
        own answer, then settling who died. Split out of the loop so a decision taken
        mid-damage (教皇 stepping in front of a killing blow) resumes exactly here.
        Returns True if the match ended."""
        st, self.instant = self.instant, None
        if st is None:
            return False
        batch, insts = st["batch"], st["insts"]
        res = self.res
        if True:
            for side, inst, ev in batch:
                if not ev.cancelled and ev.amount > 0 and inst.actor is not None:
                    self.landed.add(inst.actor.id)
            for side, inst, ev in batch:
                if ev.cancelled:
                    self.log_line(
                        f"{inst.label}: {self.label(ev.target)} blocks it ({ev.cancel_reason})."
                    )
                elif ev.amount > 0:
                    # Record the outcome on the reveal so the pause screen can show
                    # "X damage to Y" for the acting hero.
                    if self.last_reveal and self.last_reveal.get(side):
                        self.last_reveal[side].setdefault("hits", []).append(
                            {"target": ev.target.name, "amount": ev.amount}
                        )
                    src = self.label(ev.source) if ev.source else "The board"
                    self.log_line(
                        f"{src} hits {self.label(ev.target)} for {ev.amount}"
                        + (f" [{ev.element}]" if ev.element else "")
                        + f" — {ev.target.hp}/{ev.target.max_hp} left."
                    )
            for side in (LEFT, RIGHT):
                inst = insts[side]
                if inst is None:
                    continue
                mark = len(self.log)
                inst.side_effects(self)
                self.note_on_reveal(side, inst, mark)
            # A side effect can move somebody (大力士's throw, 半人马's charge), so
            # the board may have caught another victim. Part of this same instant.
            for ev in self.pending_hazards:
                DMG.deal(self, ev)
            self.pending_hazards = []
            # Now the instant is complete — heals included — so settle who died.
            self.sweep_deaths()

            res["index"] += 1
            res["picks"] = {LEFT: None, RIGHT: None}
            res["options"] = {LEFT: [], RIGHT: []}
            return self.check_victory()

    def note_on_reveal(self, side, inst, mark):
        """Put an ability's name — and whatever it announced while resolving — on the
        pause screen, so an ability that deals no damage isn't invisible there."""
        rv = (self.last_reveal or {}).get(side)
        if rv is None:
            return
        ab = getattr(inst, "ability", None)
        if ab is not None and ab.name not in rv.setdefault("abilities", []):
            rv["abilities"].append(ab.name)
        for line in self.log[mark:]:
            if line.get("quiet"):
                continue
            # The panel already says whose side this is.
            text = line["text"]
            for prefix in ("Left ", "Right "):
                if text.startswith(prefix):
                    text = text[len(prefix):]
                    break
            rv.setdefault("notes", []).append(text)

    def victims_wanted(self, side):
        """How many of the enemies in the net this side's current shot may hit."""
        if self.res is None:
            return 0
        plan = self.res["plan"][side]
        idx = self.res["index"]
        inst = plan[idx] if idx < len(plan) else None
        if inst is None or not inst.needs_pick:
            return 0
        return min(getattr(inst, "max_victims", 1), len(self.res["options"][side]))

    def victims_complete(self, side):
        picked = self.res["picks"][side] if self.res else None
        return isinstance(picked, list) and len(picked) >= self.victims_wanted(side)

    def choose_victim(self, side, eid):
        if self.phase != VICTIM or self.res is None:
            return "No target choice pending."
        if eid not in self.res["options"][side]:
            return "Not a legal target."
        picked = self.res["picks"][side]
        picked = list(picked) if isinstance(picked, list) else []
        if eid in picked:
            return "That one is already marked."
        picked.append(eid)
        self.res["picks"][side] = picked
        pending = [
            s
            for s in (LEFT, RIGHT)
            if self.res["options"][s] and not self.victims_complete(s)
        ]
        if not pending:
            self.phase = COMMIT  # transient; advance_resolution moves it on
            self.advance_resolution()
        self.bump()
        return None

    REWARDS = ("atk", "grid", "rng")

    def saves_left(self, e):
        """How many more times this hero may step in front of a killing blow.
        None means it cannot at all; a large number means no cap."""
        for p in e.passives:
            if not getattr(p, "saves_deaths", False):
                continue
            cap = getattr(p, "SAVE_LIMIT", None)
            if cap is None:
                return 1 << 30
            return max(0, cap - e.vars.get("saves_used", 0))
        return None

    def savers(self):
        """Heroes able to step in front of a killing blow, either side's — and that
        have mercies left to spend."""
        out = []
        for e in self.living():
            left = self.saves_left(e)
            if left:
                out.append(e)
        return out

    def offer_saves(self):
        """Queue every decision this instant's damage has raised: a hero about to
        fall while somebody can step in front of it, and anything else that wants
        a say once it knows what actually landed (浪子 buying its way out). Returns
        True if anything is owed."""
        # Anything already queued by the instant itself (世界树's beasts, 洛基
        # arriving) holds the board up just as a save would, so this always ends by
        # asking whether *anything* is owed rather than only about Popes.
        st = self.instant
        if st is None:
            return bool(self.interrupts)
        self._offer_saves(st)
        self._offer_reactions(st)
        return bool(self.interrupts)

    def _offer_reactions(self, st):
        """Anything that reacts to a blow that has just landed. A passive or an
        ability says so by defining `damage_prompt(match, owner, applied)`; the
        answer comes back to its `apply_interrupt`. The same shape as `followup`,
        one beat earlier — while the damage can still be taken back."""
        for e in list(self.living()):
            for p in list(e.passives) + list(e.abilities):
                fn = getattr(p, "damage_prompt", None)
                if fn is None:
                    continue
                got = fn(self, e, st["applied"])
                for task in (got if isinstance(got, list) else [got]):
                    if task:
                        self.interrupts.append(dict(task, entity=e.id))

    def _offer_saves(self, st):
        popes = self.savers()
        if not popes:
            return
        doomed = {}
        for ev, dealt in st["applied"]:
            if dealt <= 0 or ev.source is None:
                continue
            if ev.category not in (DMG.NORMAL_ATTACK, DMG.ABILITY):
                continue
            t = ev.target
            if not t.alive or t.hp > 0:
                continue
            slot = doomed.setdefault(t.id, {"target": t.id, "restore": 0, "source": ev.source.id})
            slot["restore"] += dealt
        # Several heroes can be doomed in the same instant, and one hero may be the
        # only one able to save any of them. Its remaining mercies are counted down
        # as the prompts are raised, or it would be asked more times than it can
        # answer and spend past its own cap.
        budget = {p.id: self.saves_left(p) for p in popes}
        for spec in doomed.values():
            t = self.entity(spec["target"])
            # Nobody steps in front of their own end, but another can step in front
            # of theirs. Its own side answers where it can, otherwise the other.
            others = [p for p in popes if p is not t and budget.get(p.id, 0) > 0]
            if not others:
                continue
            pope = next((p for p in others if p.side == t.side), others[0])
            budget[pope.id] -= 1
            self.interrupts.append({
                "kind": "confirm", "key": "death_save", "side": pope.side,
                "pope": pope.id, "target": spec["target"], "source": spec["source"],
                "restore": spec["restore"],
                "name": "赦免 Absolution",
                "text": f"{self.label(t)} is about to fall to "
                        f"{self.label(self.entity(spec['source']))}. Step in front of it? "
                        f"It takes nothing — and whoever swung is sharpened for good.",
            })

    def choose_interrupt(self, side, answer):
        """Answer the decision holding resolution up. `answer` is truthy/None for a
        confirm, or one of the reward names for a pick."""
        if self.phase != INTERRUPT or not self.interrupts:
            return "Nothing to decide."
        task = self.interrupts[0]
        if task["side"] != side:
            return "Not yours to answer."
        if task["kind"] == "pick" and answer not in task["options"]:
            return "Not one of the things on offer."
        self.interrupts.pop(0)

        if task["key"] == "death_save" and answer:
            t, src = self.entity(task["target"]), self.entity(task["source"])
            pope = self.entity(task["pope"])
            if pope is not None and not self.saves_left(pope):
                answer = None          # out of mercies since this was raised
        if task["key"] == "death_save" and answer:
            if pope is not None:
                pope.vars["saves_used"] = pope.vars.get("saves_used", 0) + 1
            if t is not None and t.alive:
                t.hp = min(t.max_hp, t.hp + task["restore"])
                self.log_line(
                    f"{self.label(task and self.entity(task['pope']))} steps in front of "
                    f"{self.label(t)} — the blow does nothing."
                )
            if src is not None and src.alive:
                # Whoever swung is owed something for being denied.
                self.interrupts.insert(0, {
                    "kind": "pick", "key": "reward", "side": src.side, "source": src.id,
                    "options": list(self.REWARDS),
                    "option_kind": "stat",
                    "name": "补偿 Recompense",
                    "text": f"{self.label(src)} was denied a kill. Choose what it gains, "
                            f"for the rest of the match.",
                })
        elif task["key"] == "beast":
            tgt = self.entity(answer)
            if tgt is not None and tgt.alive:
                self.entity(task["tree"]).vars.setdefault("bitten", []).append(tgt.id)
                self.log_line(f"{task['beast']} takes {self.label(tgt)} for {task['amount']}.")
                DMG.apply_batch(self, [DMG.DamageEvent(
                    source=self.entity(task["tree"]), target=tgt,
                    amount=task["amount"], category=DMG.ABILITY)])
        elif task["key"] == "loki":
            hero = HEROES.BY_KEY[task["hero"]]
            e = self.spawn(hero, side, tuple(answer))
            self.log_line(f"{self.label(e)} steps out of the wreck.")
            self.bus.emit(EV.MATCH_START, {"entity": e})
        elif task["key"] == "dig":
            owner = self.entity(task["explorer"])
            cell = tuple(answer)
            if owner is not None and self.occupant(cell) is None:
                HEROES.apply_resource(self, owner, task["resource"], cell)
            # Whatever just took that square is no longer on offer to the rest.
            for t in self.interrupts:
                if t.get("key") == "dig":
                    t["options"] = [c for c in t["options"] if tuple(c) != cell]
        elif task["key"] == "reward":
            src = self.entity(task["source"])
            if src is not None:
                src.add_modifier(Modifier(answer, "add", 1))
                self.log_line(f"{self.label(src)} is sharpened — +1 {answer}.")
        elif task.get("entity") is not None:
            # Raised by a hero's own passive or ability (浪子 buying its way out of
            # a blow), so it is answered by the same one rather than by a branch
            # here that has to know what every hero wants.
            e = self.entity(task["entity"])
            for p in (list(e.passives) + list(e.abilities)) if e else ():
                fn = getattr(p, "apply_interrupt", None)
                if fn:
                    fn(self, e, task, answer)

        self.bump()
        if self.interrupts:
            return None
        self.phase = COMMIT              # transient; the instant moves it on
        resume, self.interrupt_resume = self.interrupt_resume, None
        if resume is not None:
            # The queue was raised outside an exchange's own instant (探险家's island
            # arriving at the top of a round, 大力士's throw once everything has
            # settled), so there is nothing to finish — pick up where it paused.
            return self.resume_after_interrupts(resume)
        if not self.finish_instant():
            self.advance_resolution()
        return None

    def pause_for_interrupts(self, resume="round_start"):
        """Hold the board on the queue that was just raised, with no resolution
        behind it. The caller must return straight after."""
        self.interrupt_resume = resume
        self.phase = INTERRUPT
        self.bump()

    def resume_after_interrupts(self, what):
        """Pick up whatever was paused. Deaths are settled here rather than where
        the damage landed, so a hero somebody stepped in front of is already back
        above zero by the time anyone counts the fallen."""
        self.instant = None
        self.sweep_deaths()
        if self.check_victory():
            return None
        if what == "followup" and any(self.followups.values()):
            self.phase = RESOLVED        # back to the queue that was interrupted
            self.bump()
            return None
        self.start_exchange()
        return None

    def deal_after_exchange(self, batch, resume="followup"):
        """Land damage that happens once an exchange has already settled (大力士's
        throw). It is still an ability landing on a hero, so 教皇 gets its chance
        exactly as it would mid-resolution — the only difference is that there is
        no instant behind it to finish afterwards.

        Anything the board banked on the way — a mine under the square somebody
        was just put on — lands in the same instant, the way movement hazards do
        during an exchange.

        Returns True if the board is now waiting on a decision."""
        batch = list(batch)
        if self.pending_hazards:
            batch += self.pending_hazards
            self.pending_hazards = []
        if not batch:
            return False
        applied = [(ev, DMG.deal(self, ev)) for ev in batch]
        # `insts` is keyed by side wherever a resolution builds one; there is no
        # instance behind this damage, so it is empty rather than a list.
        self.instant = {"batch": [(ev.target.side, None, ev) for ev in batch],
                        "insts": {}, "applied": applied}
        if self.offer_saves():
            self.pause_for_interrupts(resume)
            return True
        self.instant = None
        self.sweep_deaths()
        return False

    def sweep_deaths(self):
        for e in self.entities:
            if e.alive and e.hp <= 0:
                ctx = {"entity": e, "prevented": False}
                self.bus.emit(EV.BEFORE_DEATH, ctx)
                if ctx["prevented"]:
                    continue
                e.alive = False
                e.cells = set()
                self.recent_deaths.append(e)
                # Anything this hero was granting others lapses with it, and a bar
                # it was propping up shrinks back — losing whatever sat above it.
                for other in self.entities:
                    if other is e:
                        continue
                    if any(m.source is e for m in other.modifiers):
                        other.modifiers = [m for m in other.modifiers if m.source is not e]
                        other.ap = min(other.ap, other.max_ap)
                self.bus.emit(EV.DEATH, {"entity": e})
                self.log_line(f"{self.label(e)} is destroyed.")

    def turn_units(self, side, c):
        """Whose turn just ended. A gang turn ends for the whole gang at once,
        including goblins that died in it and, on a wipe, ones that never acted."""
        if c["kind"] == "gang":
            return [e for e in self.entities if e.side == side and e.hero.gang == c["gang"]]
        eid = c.get("entity") or self.selected[side]
        e = self.entity(eid) if eid else None
        if e is None:
            return []
        gang = self.gang_of(e)
        if gang:
            return [x for x in self.entities if x.side == side and x.hero.gang == gang]
        return [e]

    def finish_exchange(self):
        for side in (LEFT, RIGHT):
            c = self.commits[side]
            if c["kind"] not in ("action", "gang", "dead"):
                continue
            for e in self.turn_units(side, c):
                e.has_acted = True
                # A root spends itself on the turn after it lands: keep one applied
                # during this very exchange, drop one that has now been served.
                if e.vars.get("rooted_at") not in (None, (self.round, self.exchange)):
                    e.vars["rooted_at"] = None
                    e.vars.pop("rooted_squares", None)
                    e.vars.pop("rooted_tag", None)
                # A stride is spent the same way, on the turn after the one that
                # granted it — the turn it was meant to carry.
                if e.vars.get("sprint_at") not in (None, (self.round, self.exchange)):
                    e.vars["sprint_at"] = None
                    e.vars.pop("sprint_squares", None)
                # Turns this unit has finished. Read at menu-build time as well as
                # at commit, so it must not change between the two.
                e.vars["turns_done"] = e.vars.get("turns_done", 0) + 1
                if not e.alive:
                    continue
                self.bus.emit(EV.TURN_END, {"entity": e})
                # "本回合" buffs (哥布林鼓舞) die with the turn that granted them.
                e.expire_modifiers(UNTIL_TURN_END)
        if self.check_victory():
            return
        self.res = None
        if self.run_turn_resolved():
            return          # somebody has a decision to make first
        self.start_exchange()

    def run_turn_resolved(self):
        """The exchange has settled. Tell every unit that acted how it went, then
        collect any follow-up that needs the player to choose something. Returns
        True if we are now waiting on one of those choices.

        A passive reacts by implementing `on_turn_resolved` (no input needed) or
        `followup`/`apply_followup` (a choice), mirroring the `turn_choice` pair
        that fires at the start of a turn.

        A unit that died in this exchange is asked too, wherever on the board it
        fell — a parting shot belongs to whoever just lost the hero (潜水者's last
        charge), and it never got a turn to ask for it on."""
        self.followups = {LEFT: [], RIGHT: []}
        asked = set()

        def collect(side, e, acted):
            if e.id in asked:
                return
            asked.add(e.id)
            ctx = {"entity": e, "landed": e.id in self.landed,
                   "died": not e.alive, "acted": acted}
            if acted:
                self.bus.emit(EV.TURN_RESOLVED, ctx)
            # Abilities are asked as well as passives: an ability whose effect
            # lands after the exchange (大力士's throw) owns that rule itself
            # rather than needing a passive kept alongside it to serve it.
            for p in list(e.passives) + list(e.abilities):
                fn = getattr(p, "followup", None)
                got = fn(self, e, ctx) if fn else None
                # A passive may raise several at once (画师 both blunting and
                # sharpening off one exchange), so a list is as good as one task.
                for task in (got if isinstance(got, list) else [got]):
                    # A `confirm` asks a plain yes/no and so carries no options.
                    if task and (task.get("options") or task.get("kind") == "confirm"):
                        self.followups[side].append(dict(task, entity=e.id))

        for side in (LEFT, RIGHT):
            c = self.commits.get(side)
            acted_ids = set()
            if c and c["kind"] in ("action", "gang", "dead"):
                acted_ids = {e.id for e in self.turn_units(side, c) if e.alive}
            for e in self.living(side):
                collect(side, e, acted=e.id in acted_ids)
        for e in self.recent_deaths:
            collect(e.side, e, acted=False)
        if not any(self.followups.values()):
            return False
        self.phase = RESOLVED
        self.bump()
        return True

    def choose_followup(self, side, choice=None):
        """Answer the first follow-up pending for this side. `choice` of None
        declines it, when the follow-up allows that."""
        if self.phase != RESOLVED:
            return "Nothing to decide."
        pend = self.followups[side]
        if not pend:
            return "Nothing pending for you."
        task = pend[0]
        e = self.entity(task["entity"])
        if choice is not None and task.get("kind") != "confirm":
            # A follow-up asks for a square or for a hero; the options list says which.
            legal = (choice in task["options"] if task.get("kind") == "unit"
                     else list(choice) in task["options"])
            if not legal:
                return ("Not one of the heroes on offer." if task.get("kind") == "unit"
                        else "Not one of the squares on offer.")
        if choice is None and not task.get("optional"):
            return "You must choose one."
        # Not gated on `alive`: a follow-up is only ever offered because a passive
        # asked for it, and a parting shot is asked for precisely by a unit that
        # has just died. Passives that need a body of their own check for one.
        if e is not None:
            for p in list(e.passives) + list(e.abilities):
                fn = getattr(p, "apply_followup", None)
                if fn:
                    fn(self, e, task["key"], choice)
        pend.pop(0)
        # Whatever the board banked while that was applied — a mine under the
        # square somebody was just shoved onto. Settled here rather than by each
        # hero, so the ground answers a move the same way whoever made it.
        if self.pending_hazards and self.phase != INTERRUPT:
            self.deal_after_exchange([])
        self.bump()
        if self.phase == INTERRUPT:
            # The follow-up raised a decision of its own (somebody may be stepped
            # in front of). Whatever is left of this queue waits for that.
            return None
        if not any(self.followups.values()):
            self.phase = COMMIT      # start_exchange sets the real phase
            self.start_exchange()
        return None

    def check_victory(self):
        left = [e for e in self.living(LEFT) if e.flags["counts_for_defeat"]]
        right = [e for e in self.living(RIGHT) if e.flags["counts_for_defeat"]]
        if left and right:
            return False
        self.phase = GAMEOVER
        if not left and not right:
            self.winner = "draw"
            self.log_line("Both forces are wiped out. Draw.")
        else:
            self.winner = LEFT if left else RIGHT
            self.log_line(f"{'Left' if left else 'Right'} wins.")
        self.bump()
        return True
