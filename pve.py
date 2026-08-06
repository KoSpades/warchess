"""PvE — the smart AI holding one seat against a live player.

`python3 game.py --pve` opens a normal game (draft, build, deploy, fight) with
the Right seat driven by the same brain the tournament runs on: `ai.py` judging,
`playtest.py` enumerating. The player gets the full game rather than the
pre-arranged sandbox `--test` gives.

The bot never watches the clock. It is pumped by the server whenever the match
state moves, answers everything its seat owes right then, and stops the moment
the next decision belongs to the player. So the whole thing is synchronous and
holds the game lock — no second thread, and no chance of the bot reading a board
that is halfway through changing.

The draft goes by measured win rate: of the cards on offer it takes the best or
the second best, a coin flip between them. It used to reason from the stat block
instead — health, attack, reach, a penalty for stacking a role — and that scored
r = +0.09 against how the heroes actually perform, which is noise. It rated 蛇帝
its top pick (50.3% in play) and put 狙击手 (63.8%) among its last. A table of
what actually wins beats a theory about what should.
"""

import random

import heroes as H
import matchlog as LOG
import playtest as PT
import winrates as WR
import ai as AI
from topology import LEFT, RIGHT

# The draft: best card on offer or second best, evenly. Never worse than second,
# so it drafts well; never certain, so two games do not open the same way.
SECOND_BEST_CHANCE = 0.5


def draft_scores(m, side):
    """Every card on offer with what it is measured to win. `side` is unused —
    the table says nothing about what a force already holds, and inventing a
    balance rule on top of it would be a theory again."""
    return {k: WR.win_rate(k) for k in m.draft["shown"]}


def squad_blob(m, pool, count):
    """A connected group of `count` squares out of `pool`, grown from as far
    forward as the pool allows. None if the shape will not fit at all.

    Grown with the board's own `neighbours`, which is what `connected` checks
    with — so a blob this finds is a blob the engine accepts."""
    have = set(pool)
    for seed in pool:
        group, frontier = [seed], [seed]
        while frontier and len(group) < count:
            cur = frontier.pop(0)
            for n in m.topology.neighbours(cur):
                if len(group) >= count:
                    break
                if n in have and n not in group:
                    group.append(n)
                    frontier.append(n)
        if len(group) == count:
            return group
    return None


def plan_deployment(m, side, bodies, free):
    """One square per body, in the order `bodies` came in.

    Squads are carved out first, as connected blobs: 哥布林团伙 and 蛇帝 must go
    down as one piece, and a role-sorted layout does not know that — it will run
    a squad off the end of one column and on to the top of the next, which the
    engine refuses. Everything else is then laid out by role in what is left,
    which is the ordinary front-rank-first arrangement."""
    pool = list(free)
    out = [None] * len(bodies)
    squads = {}
    for i, key in enumerate(bodies):
        gang = H.BY_KEY[key].gang
        if gang:
            squads.setdefault(gang, []).append(i)
    for idxs in squads.values():
        blob = squad_blob(m, pool, len(idxs))
        if blob is None:
            return None
        for i, cell in zip(idxs, blob):
            out[i] = cell
            pool.remove(cell)
    rest = [i for i in range(len(bodies)) if out[i] is None]
    cells = AI.deployment(m, side, [bodies[i] for i in rest], pool) or pool
    for i, cell in zip(rest, cells):
        out[i] = cell
    return out


class Bot:
    """One seat, driven by the AI. Give it the side it holds and, optionally, a
    `MatchLog` to write its reasoning into."""

    def __init__(self, side=RIGHT, log=None):
        self.side = side
        self.log = log

    # ---------------------------------------------------------- what it owes

    def owed(self, m):
        """True if the match is waiting on this seat right now. Checked rather
        than inferred from the phase, because most phases wait on one side at a
        time and the bot must not answer for the player."""
        s = self.side
        if m.phase == "draft":
            return bool(m.draft) and m.draft["picker"] == s
        if m.phase == "build":
            return bool(m.build) and bool(m.build["pending"][s])
        if m.phase == "setup":
            return not m.setup_state[s]["ready"]
        if m.phase == "opening":
            return bool(m.opening) and bool(m.opening["pending"][s])
        if m.phase == "commit":
            return m.commits[s] is None
        if m.phase == "victim":
            # A side with nothing in the net owes nothing, and its `picks` stay
            # None — so `victims_complete` alone would say it is still waiting.
            return (m.res is not None and bool(m.res["options"][s])
                    and not m.victims_complete(s))
        if m.phase == "interrupt":
            return bool(m.interrupts) and m.interrupts[0]["side"] == s
        if m.phase == "move_choice":
            return bool(m.move_choices[s])
        if m.phase == "resolved":
            return bool(m.followups[s])
        return False

    def pump(self, m, limit=400):
        """Answer everything this seat owes, until the next decision is the
        player's. Stops on no progress as well as on nothing owed: a task the
        bot cannot answer must hand the game back, not spin the server."""
        stuck = 0
        for _ in range(limit):
            if not self.owed(m):
                return
            before = (m.phase, m.version, m.round, m.exchange)
            self.act(m)
            if (m.phase, m.version, m.round, m.exchange) == before:
                stuck += 1
                if stuck >= 2:
                    # The player is now waiting on a seat that will never answer.
                    # Say so on the console as well as in the log — a silent
                    # hand-back looks exactly like a hung game from the browser.
                    print(f"[pve] the bot is stuck in {m.phase} "
                          f"(round {m.round}, exchange {m.exchange})")
                    if self.log:
                        self.log.write("stuck", side=self.side, phase=m.phase,
                                       round=m.round, exchange=m.exchange)
                    return
            else:
                stuck = 0

    def act(self, m):
        if m.phase == "draft":
            return self.draft(m)
        if m.phase == "setup":
            return self.deploy(m)
        # Everything else is the same crank a headless match turns, narrowed to
        # this seat, so the bot in a live game and the bot in a tournament are
        # the same code making the same choices.
        return PT.step(m, (self.side,), observer=self._observe)

    # ------------------------------------------------------------- decisions

    def draft(self, m):
        """The best card on offer, or the second best — a coin flip between them.

        Always one of the top two, so the bot drafts as well as the table knows
        how; never the same one twice, so the same opening hand does not produce
        the same force every game."""
        scores = draft_scores(m, self.side)
        # Shuffled first, so cards the table cannot separate — and at ±4.6 points
        # of noise there are many — are not always broken the same way.
        shown = list(m.draft["shown"])
        random.shuffle(shown)
        ranked = sorted(shown, key=lambda k: -scores[k])
        second = len(ranked) > 1 and random.random() < SECOND_BEST_CHANCE
        pick = ranked[1] if second else ranked[0]
        err = m.draft_pick(self.side, pick)
        if self.log:
            self.log.write("draft", side=self.side, hero=pick, by="bot",
                           took="second" if second else "best",
                           batch=m.draft["batch"] + 1 if m.draft else None,
                           offered=scores, error=err)
        return err

    def deploy(self, m):
        """Place the whole force and lock it in one go — the player never sees a
        half-deployed enemy, and there is nothing to wait for."""
        free = sorted((c for c in m.topology.deployment_zone(self.side)
                       if c in m.topology.all_cells()),
                      key=lambda c: (-c[0] if self.side == LEFT else c[0], c[1]))
        bodies = m.affordable_bodies(self.side)
        if len(bodies) > len(free):
            # Nothing sensible to do; leave the seat unlocked rather than field
            # a short force, and let the pump's no-progress guard stop.
            return "no room to deploy"
        cells = plan_deployment(m, self.side, bodies, free) or free
        st = m.setup_state[self.side]
        st["placements"] = [{"key": k, "cell": list(c)}
                            for k, c in zip(bodies, cells)]
        err = m.lock_force(self.side)
        if err:
            # Never leave the seat unlocked: the player is waiting on it, and a
            # force that will not lock is a frozen game rather than a bad one.
            # Front rank first, straight down the zone, is the plainest layout
            # there is; anything the engine still refuses is worth shouting about.
            st["placements"] = [{"key": k, "cell": list(c)}
                                for k, c in zip(bodies, free)]
            err = m.lock_force(self.side)
            if err:
                print(f"[pve] the bot cannot deploy: {err}")
        if self.log:
            self.log.write("deploy", side=self.side, by="bot",
                           placements=st["placements"], error=err)
        return err

    def _observe(self, m, side, scored, chosen):
        if self.log:
            self.log.write("turn", **turn_record(m, side, scored, chosen, by="bot"))


# --- the record a committed order leaves ------------------------------------

def turn_record(m, side, scored, chosen, by, order=None):
    """One `turn` line: the board before the order, who acts, and how the AI
    ranked every hero that could have. `order` overrides what the bot would have
    played — that is how a player's actual move is logged beside the AI's."""
    ranked = sorted(
        ({"entity": e.id, "key": e.key, "score": round(sc, 3), "order": p}
         for sc, e, p in scored),
        key=lambda r: -r["score"],
    )
    best = ranked[0] if ranked else None
    return {
        "side": side,
        "by": by,
        "entity": chosen.id if chosen is not None else None,
        "key": chosen.key if chosen is not None else None,
        "order": order if order is not None else (best or {}).get("order"),
        "ranked": ranked,
        # The AI's read of the position, always from the bot's seat, so the
        # numbers across a whole game are one series and not two.
        "eval": round(AI.evaluate(m, AI.project(m), RIGHT), 3),
        "before": LOG.snapshot(m),
    }


def log_player_commit(m, side, payload, log):
    """Called just before a player's order is applied. Scores the position the
    same way the bot would have, then records what the player actually did —
    so every game leaves a set of positions labelled with both the AI's answer
    and a human's."""
    if log is None:
        return
    chosen = m.entity(m.selected[side])
    scored = []
    try:
        for e in m.unacted(side):
            sc, p = PT.best_order(m, e)
            scored.append((sc, e, p))
    except Exception:
        scored = []
    log.write("turn", **turn_record(m, side, scored, chosen, by="player",
                                    order=payload))
