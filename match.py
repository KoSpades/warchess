"""Match state machine.

The round is a mutable queue of exchanges rather than a loop over four heroes
(spec 7.8), and resolution is re-entrant: it pauses at the victim-pick step,
waits for both sides to choose independently, then continues.
"""

import itertools
import random
import time

import actions as ACT
import damage as DMG
import events as EV
import heroes as HEROES
from board import Board
from entities import Entity, UNTIL_TURN_END
from topology import LEFT, RIGHT, Topology, other_side

COLUMN_LETTERS = "ABCDEFGHI"

DRAFT = "draft"
SETUP = "setup"
OPENING = "opening"
COMMIT = "commit"
VICTIM = "victim"
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
        self.bus = EV.EventBus(self)
        self.global_rules = [DMG.AbilityImmunity(), DMG.FlatReduction(), DMG.HalvingRule()]

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
        self.draft = None
        self.opening = None

        self.phase = SETUP
        self.selected = {LEFT: None, RIGHT: None}
        self.commits = {LEFT: None, RIGHT: None}
        self.turn_started = {LEFT: False, RIGHT: False}
        self.snapshot = {}
        self.res = None
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

    def cell_name(self, cell):
        c, r = cell
        return f"{COLUMN_LETTERS[c - 1]}{r}"

    def log_line(self, text, quiet=False):
        self.log.append({"round": self.round, "text": text, "quiet": quiet})
        self.log = self.log[-200:]

    def living(self, side=None):
        return [
            e
            for e in self.entities
            if e.alive and (side is None or e.side == side)
        ]

    def entity(self, eid):
        for e in self.entities:
            if e.id == eid:
                return e
        return None

    def occupant(self, cell):
        for e in self.entities:
            if e.alive and e.flags["blocks_movement"] and e.occupies(cell):
                return e
        return None

    def unacted(self, side):
        return [e for e in self.living(side) if not e.has_acted and e.flags["takes_turns"]]

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
        for s in (LEFT, RIGHT):
            # Down a column, so a squad's bodies land adjacent to each other.
            col = 2 if s == LEFT else 8
            free = [(col, r) for r in range(1, self.topology.rows + 1)]
            self.setup_state[s]["placements"] = [
                {"key": k, "cell": list(c)} for k, c in zip(self.deploy_bodies(s), free)
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
        self.phase = SETUP
        self.log_line("Draft complete — deploy your forces.")

    def assign_draft(self, left_keys, right_keys):
        """Skip the interactive draft and hand each side its heroes directly,
        then enter deployment. Used by headless tests and scripted setups."""
        self.drafted = {LEFT: list(left_keys), RIGHT: list(right_keys)}
        self.force_size = len(left_keys)
        self.draft = None
        self.phase = SETUP

    # -------------------------------------------------------------- setup

    def deploy_bodies(self, side):
        """Every body the side must put on the board. A normal card contributes
        itself; a squad card (哥布林团伙) contributes its members, duplicates and
        all — so the force is 4 cards but can be 6 units."""
        out = []
        for k in self.drafted[side]:
            hero = HEROES.BY_KEY[k]
            out.extend(hero.squad or [k])
        return out

    def bodies_needed(self, side):
        return len(self.deploy_bodies(side))

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
        if len(st["placements"]) >= len(bodies):
            return f"Your force is full at {len(bodies)}."
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
            if len(cells) < 2:
                continue
            seen = {cells[0]}
            frontier = [cells[0]]
            pool = set(cells)
            while frontier:
                cur = frontier.pop()
                for n in self.topology.neighbours(cur):
                    if n in pool and n not in seen:
                        seen.add(n)
                        frontier.append(n)
            if len(seen) != len(cells):
                return f"{hero.name} must deploy on adjacent cells — keep the group connected."
        return None

    def lock_force(self, side):
        st = self.setup_state[side]
        need = self.bodies_needed(side)
        if len(st["placements"]) != need:
            return f"Deploy {need} heroes first."
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
        err = self._validate_targeting(e, ab.targeting, params)
        if err:
            return err
        self._apply_opening(e, ab, params)
        pend.pop(0)
        self.bump()
        if not self.opening["pending"][LEFT] and not self.opening["pending"][RIGHT]:
            self.opening = None
            self.start_round()
        return None

    def _validate_targeting(self, e, t, params):
        """Shared targeting checks for opening picks (and any future non-turn
        choice). Mirrors the ability cases in validate_action."""
        kind = t.get("kind", "none")
        if kind == "ally":
            tt = self.entity(params.get("target"))
            if tt is None or not tt.alive or tt.side != e.side:
                return "Choose a living ally."
        elif kind == "any_cell":
            cell = params.get("cell")
            if not cell or not self.topology.in_bounds(tuple(cell)):
                return "Choose a cell on the board."
        elif kind == "magnitude":
            x = params.get("amount")
            cap = min(e.hp, e.max_hp - 1)
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
        self.start_exchange()

    def end_round(self):
        self.bus.emit(EV.ROUND_END, {})
        self.start_round()

    def start_exchange(self):
        if self.phase == GAMEOVER:
            return
        left, right = self.unacted(LEFT), self.unacted(RIGHT)
        if not left and not right:
            self.end_round()
            return

        self.exchange += 1
        self.selected = {LEFT: None, RIGHT: None}
        self.commits = {LEFT: None, RIGHT: None}
        self.turn_started = {LEFT: False, RIGHT: False}
        self.res = None
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
        """Everyone whose turn it is when this unit is picked up: a gang brings
        its whole living crew, anyone else brings only themselves."""
        gang = self.gang_of(e)
        return self.gang_members(e.side, gang) if gang else [e]

    def forfeit_turn(self, side):
        self.commits[side] = {"kind": "dead"}
        self.maybe_resolve()

    def run_turn_start(self, e):
        """Board effects resolve before the hero decides anything. Returns False
        if the unit died here — it forfeits its action."""
        self.bus.emit(EV.TURN_START, {"entity": e})
        burn = self.board.burning_damage_for(e.cell, e)
        if burn:
            ev = DMG.DamageEvent(
                source=None,
                target=e,
                amount=burn,
                category=DMG.TILE,
                element=DMG.FIRE,
            )
            DMG.apply_batch(self, [ev])
            self.log_line(f"{self.label(e)} starts its turn burning: {burn} fire.")
        # Anything that can kill at turn start forfeits the turn — a burning tile,
        # or a passive that expires lethally (蛮王's 背水 burnout).
        if not e.alive:
            self.log_line(f"{self.label(e)} is destroyed before it can act.")
            return False
        return True

    def legal_moves(self, e):
        """Every cell reachable within the hero's move allowance, walking one step
        at a time through cells empty in the current snapshot (you cannot move
        through an occupied square). Most heroes move 1, but the budget is the
        `move` stat, so buffs like 激励 just work."""
        if not e.alive or not e.cells:
            return []
        start = e.cell
        seen = {start}
        frontier = [start]
        out = []
        for _ in range(max(0, e.move_allowance)):
            nxt = []
            for cell in frontier:
                for n in self.topology.neighbours(cell, e):
                    if n in seen or self.occupant(n) is not None:
                        continue
                    seen.add(n)
                    nxt.append(n)
                    out.append(list(n))
            frontier = nxt
        return out

    def action_menu(self, e):
        """What this hero could commit to, given its AP right now."""
        if not e.alive:
            return []
        out = [{"key": "none", "name": "Hold", "ap_cost": 0, "targeting": {"kind": "none"}}]
        spec = e.hero.attack
        if spec["mode"] == HEROES.CELL:
            out.append(
                {
                    "key": "attack",
                    "name": "Normal attack",
                    "ap_cost": 0,
                    "targeting": {
                        "kind": "cells",
                        "count": e.grid,
                        "range": e.rng,
                        "shots": e.hero.attacks_per_turn,
                    },
                }
            )
        elif spec["mode"] == HEROES.WEAPON:
            out.append(
                {
                    "key": "attack",
                    "name": "Weapon",
                    "ap_cost": 0,
                    "targeting": {"kind": "weapon"},
                    "weapons": e.hero.weapons,
                }
            )
        else:
            out.append(
                {
                    "key": "attack",
                    "name": "Normal attack",
                    "ap_cost": 0,
                    "targeting": {"kind": "unit", "range": spec.get("range")},
                }
            )
        uses = e.vars.get("ability_uses", {})
        for ab in e.abilities:
            if getattr(ab, "opening", False):
                continue  # opening abilities fire once at game start, not on a turn
            if ab.use_limit is not None and uses.get(ab.key, 0) >= ab.use_limit:
                continue  # a spent limited ability disappears from the menu
            out.append(
                {
                    "key": "ability:" + ab.key,
                    "name": ab.name,
                    "ap_cost": ab.ap_cost,
                    "targeting": self.ability_targeting(e, ab),
                    "self_move": ab.self_move,
                    "affordable": e.ap >= ab.ap_cost,
                    "text": ab.blurb,
                }
            )
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

        self.apply_choices(e, order)
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
            if picks.get(ch["key"]) not in ch["options"]:
                return f"{ch['name']}: pick one of your allies."
        return None

    def apply_choices(self, e, order):
        """Run at seal time — the moment the turn actually begins."""
        picks = order.get("choices") or {}
        for ch in self.turn_choices(e):
            target = picks.get(ch["key"])
            if target not in ch["options"]:
                continue
            for p in e.passives:
                fn = getattr(p, "apply_choice", None)
                if fn:
                    fn(self, e, ch["key"], target)

    def _build_order(self, e, payload):
        """Validate one unit's move + action. Returns (order, error)."""
        dest = payload.get("destination")
        dest = tuple(dest) if dest else e.cell
        if dest != e.cell:
            if dest not in [tuple(c) for c in self.legal_moves(e)]:
                return None, "That cell is not an open adjacent square."

        action = payload.get("action") or {"key": "none"}
        key = action.get("key", "none")
        # Solo mode aims normal attacks for you — random grids within range.
        if self.mode == "self" and key == "attack" and e.hero.attack["mode"] == HEROES.CELL:
            action = dict(action, shots=self.random_shots(e, dest))
        err = self.validate_action(e, dest, action) or self.validate_choices(e, payload)
        if err:
            return None, err
        return {
            "entity": e.id,
            "destination": list(dest),
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
            order, err = self._build_order(e, item)
            if err:
                return f"{e.name}: {err}"
            orders.append(order)
        missing = [a for a in actors if a.id not in seen]
        if missing:
            return f"Still waiting on orders for {', '.join(a.name for a in missing)}."

        for o in orders:
            self.apply_choices(self.entity(o["entity"]), o)
        self.commits[side] = {
            "kind": "gang",
            "gang": self.gang_of(picked),
            "entity": orders[0]["entity"],
            "orders": orders,
        }
        self.bump()
        self.maybe_resolve()
        return None

    def ability_targeting(self, e, ab):
        """The ability's targeting, plus any live options only the engine can work
        out — 冲撞's chargeable lanes, with landing square and who gets trampled."""
        t = dict(ab.targeting)
        if hasattr(ab, "lanes"):
            t["choices"] = ab.lanes(self, e)
        return t

    def validate_action(self, e, dest, action):
        key = action.get("key", "none")
        if e.hero.attack.get("mode") == HEROES.WEAPON:
            return self._validate_weapon(e, dest, action, key)
        if key == "none":
            return None
        if key == "attack":
            spec = e.hero.attack
            if spec["mode"] == HEROES.CELL:
                shots = action.get("shots") or []
                if len(shots) != e.hero.attacks_per_turn:
                    return f"Mark cells for all {e.hero.attacks_per_turn} shot(s)."
                for cells in shots:
                    # Any number of cells up to the max — a smaller net is the
                    # attacker's choice; an empty net simply hits nothing.
                    if len(cells) > e.grid:
                        return f"At most {e.grid} cells per shot."
                    for c in cells:
                        c = tuple(c)
                        if not self.topology.in_bounds(c):
                            return "Cell off the board."
                        if self.topology.distance(dest, c) > e.rng:
                            return "Cell out of range of where you will be standing."
                    if len(set(tuple(c) for c in cells)) != len(cells):
                        return "Cells must be distinct."
            else:
                t = self.entity(action.get("target"))
                if t is None or not t.alive or t.side == e.side:
                    return "Choose a living enemy."
            return None
        if key.startswith("ability:"):
            abkey = key.split(":", 1)[1]
            ab = next((a for a in e.abilities if a.key == abkey), None)
            if ab is None:
                return "This hero has no such ability."
            if getattr(ab, "opening", False):
                return "That ability only fires at the opening."
            if e.ap < ab.ap_cost:
                return f"Needs {ab.ap_cost} AP."
            if ab.use_limit is not None and e.vars.get("ability_uses", {}).get(ab.key, 0) >= ab.use_limit:
                return "That ability is spent for the match."
            if ab.self_move and e.cells and dest != e.cell:
                return f"{ab.name} does the moving — leave your movement as hold."
            t = ab.targeting
            if t["kind"] == "direction" and action.get("direction") not in t["options"]:
                return "Choose a direction."
            if t["kind"] == "any_cell":
                cell = action.get("cell")
                if not cell or not self.topology.in_bounds(tuple(cell)):
                    return "Choose a cell on the board."
            if t["kind"] == "ally":
                tt = self.entity(action.get("target"))
                if tt is None or not tt.alive or tt.side != e.side:
                    return "Choose a living ally."
            if t["kind"] == "unit":
                tt = self.entity(action.get("target"))
                if tt is None or not tt.alive or tt.side == e.side:
                    return "Choose a living enemy."
            if t["kind"] == "magnitude":
                x = action.get("amount")
                cap = min(e.hp, e.max_hp - 1)
                if not isinstance(x, int) or x < 1 or x > cap:
                    return f"Choose an amount between 1 and {cap}."
            return ab.validate(self, e, action)
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

    def _surround8(self, cell):
        c, r = cell
        return [
            (c + dc, r + dr)
            for dc in (-1, 0, 1) for dr in (-1, 0, 1)
            if (dc or dr) and self.topology.in_bounds((c + dc, r + dr))
        ]

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
        if w["mode"] == "row":
            return [ACT.AreaAttack(e, self.topology.row(e.cell[1]), w["atk"])]
        if w["mode"] == "surround8":
            return [ACT.CellLockedAttack(e, self._surround8(e.cell), e.cell, amount=w["atk"])]
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
                movers.append((side, a, tuple(o["destination"])))
        self.last_reveal = reveal

        self.apply_movement(movers)

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

    def apply_movement(self, movers):
        """Everyone moves in the same instant — one hero a side normally, but a
        gang turn puts several movers on one side. Two units reaching for the
        same square collide and neither goes (whichever sides they are on), and
        a square that is still occupied when a mover arrives blocks it, so units
        can never swap or chain through each other."""
        going = [(e, d) for _s, e, d in movers if d != e.cell]
        contested = {
            d for i, (_e, d) in enumerate(going)
            if any(d2 == d for j, (_e2, d2) in enumerate(going) if j != i)
        }
        for e, d in going:
            if d in contested:
                self.log_line(f"{self.label(e)} is blocked at {self.cell_name(d)} — no movement.")
                continue
            if self.occupant(d) is not None:
                self.log_line(f"{self.label(e)} is blocked at {self.cell_name(d)} — no movement.")
                continue
            frm = e.cell
            e.set_cell(d)
            self.bus.emit(EV.AFTER_MOVE, {"entity": e, "from": frm, "to": d})
            self.log_line(f"{self.label(e)} moves {self.cell_name(frm)} → {self.cell_name(d)}.")

    def build_instances(self, e, commit):
        action = commit["action"]
        key = action.get("key", "none")
        intended = tuple(commit["destination"])
        if e.hero.attack.get("mode") == HEROES.WEAPON:
            return self._build_weapon(e, action, intended)
        if key == "none":
            return [ACT.NullAction()]
        if key == "attack":
            spec = e.hero.attack
            if spec["mode"] == HEROES.CELL:
                out = []
                for i, cells in enumerate(action["shots"]):
                    halve = (
                        e.hero.halve_from_index is not None
                        and i >= e.hero.halve_from_index
                    )
                    out.append(ACT.CellLockedAttack(e, cells, intended, halve, i))
                return out
            return [ACT.UnitLockedAttack(e, self.entity(action.get("target")))]
        if key.startswith("ability:"):
            abkey = key.split(":", 1)[1]
            ab = next(a for a in e.abilities if a.key == abkey)
            e.ap = max(0, e.ap - ab.ap_cost)
            if ab.use_limit is not None:
                uses = e.vars.setdefault("ability_uses", {})
                uses[ab.key] = uses.get(ab.key, 0) + 1
            self.log_line(f"{self.label(e)} spends {ab.ap_cost} AP on {ab.name}.")
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
                if res["picks"][side] is not None:
                    continue
                opts = inst.eligible_victims(self)
                res["options"][side] = [o.id for o in opts]
                if len(opts) == 0:
                    res["picks"][side] = "none"
                    cells = ", ".join(self.cell_name(c) for c in inst.resolved_cells(self))
                    self.log_line(f"{self.label(inst.attacker)} fires at {cells} — nobody there.")
                elif len(opts) == 1:
                    res["picks"][side] = opts[0].id
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
                victim = self.entity(pick) if isinstance(pick, int) else None
                events = inst.build_damage(self, victim)
                for ev in events:
                    batch.append((side, inst, ev))
            dealt = DMG.apply_batch(self, [ev for _s, _i, ev in batch])
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
                if insts[side] is not None:
                    insts[side].side_effects(self)

            res["index"] += 1
            res["picks"] = {LEFT: None, RIGHT: None}
            res["options"] = {LEFT: [], RIGHT: []}
            if self.check_victory():
                return

    def choose_victim(self, side, eid):
        if self.phase != VICTIM or self.res is None:
            return "No target choice pending."
        if eid not in self.res["options"][side]:
            return "Not a legal target."
        self.res["picks"][side] = eid
        pending = [
            s
            for s in (LEFT, RIGHT)
            if self.res["options"][s] and self.res["picks"][s] is None
        ]
        if not pending:
            self.phase = COMMIT  # transient; advance_resolution moves it on
            self.advance_resolution()
        self.bump()
        return None

    def sweep_deaths(self):
        for e in self.entities:
            if e.alive and e.hp <= 0:
                ctx = {"entity": e, "prevented": False}
                self.bus.emit(EV.BEFORE_DEATH, ctx)
                if ctx["prevented"]:
                    continue
                e.alive = False
                e.cells = set()
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
                if not e.alive:
                    continue
                self.bus.emit(EV.TURN_END, {"entity": e})
                # "本回合" buffs (哥布林鼓舞) die with the turn that granted them.
                e.expire_modifiers(UNTIL_TURN_END)
                before = e.ap
                e.gain_ap(1)
                if e.ap != before:
                    self.log_line(f"{self.label(e)} charges to {e.ap}/{e.max_ap} AP.", quiet=True)
        if self.check_victory():
            return
        self.res = None
        self.start_exchange()

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
