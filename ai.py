"""A stronger play-testing AI.

The old scorer answers "how much damage does this do right now". That is cheap
and fair, but it is blind to most of what the roster does: moving somebody is
worth nothing to it, so 大力士, 牛头, 渔夫 and 魔术师 score as blank bodies, and
anything that pays now to profit later scores zero on the turn you pay.

This one answers a different question — "what is the board worth once this
resolves" — by projecting the position forward and evaluating it. The projection
is a throwaway dict of the only things a turn changes (where everyone stands,
what health they have left), which is cheap enough to do a few dozen times per
decision; a real `deepcopy` of the match is 0.26 ms and would blow the budget.

Deliberately free of per-hero knowledge. Nothing here names a hero or an ability
key: it reads the same previews the client is sent (who a lane catches, where a
throw lands, what an attack would deal) and prices the result. A tournament run
under an AI that has been taught which heroes are good measures the teacher.
"""

import heroes as H
from topology import other_side

# --- what a board is worth --------------------------------------------------
#
# Health and threat are the bulk of it. The positional terms are deliberately
# small: they break ties between moves that are otherwise equal, and stop the
# thing shuffling on the spot, without drowning out an actual kill.

W_HP = 1.0            # a point of health
W_THREAT = 2.0        # a point of attack, which buys health every turn it lives
W_ALIVE = 6.0         # simply being on the board at all
# The positional terms are summed over every pair, so with four enemies a careless
# weight here reaches the size of a kill and the thing starts shuffling instead of
# fighting. They are tiebreakers between moves that are otherwise equal.
W_REACH = 0.15        # an enemy I could hit from where I end up
W_EXPOSURE = 0.15     # an enemy that could hit me from where I end up
W_HAZARD = 0.6        # ground that will hurt whoever is standing on it
W_DOOMED = 0.25       # how much of a hero is already lost if it can be finished

# --- focus fire: three attempts, all measured, all worse -----------------------
#
# Concentrating fire is obviously right in the abstract — a body off the board is
# the biggest swing there is — and none of these beat the AI that does not do it.
# Written down so a fourth attempt is a different idea and not one of these again.
# Each was played head to head against the identical brain without it, every force
# from both sides.
#
#   1. Reward a board where an enemy stands inside the collective reach of enough
#      of your heroes to kill it — the mirror of W_DOOMED.        47.3% (300)
#   2. Point the approach term at one chosen enemy rather than the nearest, so
#      the side walks at one body and shoots whatever the walk puts in front of
#      it.                                              47.7% +/-1.4% (5,000)
#   3. Choose *which* of the bodies already in reach to hit by what it is worth
#      instead of by how close to death it is — no position given up at all,
#      which is why this one was expected to be free.   48.1% +/-1.4% (5,000)
#
# 2 did not fail through dithering: its target changed on only 14% of turns, so
# the plan held. It fails because the walk is billed in full — crossing the board
# at one enemy means stepping through everyone else's range, and W_EXPOSURE
# charges every step — while the kill that repays it lies beyond a one-ply
# horizon.
#
# 3 is the informative one. Aiming at the most valuable body is *worse* than
# aiming at the frailest, because a kill removes a whole hero and its threat,
# and the frailest is the one a kill is actually available against. Scratching
# the biggest enemy accomplishes nothing if it lives. "Finish what is nearly
# dead" already is the concentration this game rewards.
#
# The honest conclusion: focus fire needs search depth, not another weight. Its
# payoff is several turns out and a one-ply evaluation cannot price it.
W_GAIN = 1.0          # what an ability says it is worth when nothing else can see it

# The pull toward the fight: per square out of position, *per point of the
# hero's own attack*.
#
# It used to be flat, and sized like the tiebreaker the comment above describes.
# But the term it has to argue against — W_EXPOSURE — is summed over every enemy
# that can reach you, so one step forward that brings one more enemy into range
# costs W_EXPOSURE × that enemy's attack (0.45 to 0.75 for most of the roster)
# and paid back a flat 0.15. Closing was therefore never worth it, and a melee
# hero three squares out preferred to stand still — or step backwards — forever.
# Measured on a clean board before this change: at four squares it held, at three
# it retreated, and it only ever fought when the enemy walked into it. Across
# 10,000 tournament games, reach correlated with win rate at r = +0.57: the whole
# short-ranged half of the roster was being scored as bad heroes when what was
# really happening is that nobody was bringing them to the fight.
#
# Scaling by threat makes the two terms commensurate — a square closed is worth
# about as much as one more enemy able to shoot at you — and it says the right
# thing about who should be pushing: a hero with nothing to hit anybody with has
# no reason to walk toward them at all.
W_APPROACH = 0.15


def push_of(e):
    """How hard this hero should be pulled toward the enemy: what it brings when
    it gets there. Nothing to swing with, no reason to close."""
    return max(0, e.atk or 0)


def hero_worth(e):
    """What losing this hero would cost its side, ignoring position."""
    return W_ALIVE + W_THREAT * (e.atk or 0)


# --- where a hero wants to stand --------------------------------------------
#
# A single "walk toward the enemy" pull is wrong for most of the roster: it
# marches 教皇 and 炮手 into the line they outrange, and it lets a 28-health
# 门神 dawdle at the same distance as a 13-health 占星师. What a hero should do
# with its feet follows from its stat block, so that is where the role is read
# from — nothing here knows a hero's name, and a hero added tomorrow is sorted
# by the same rule as the rest.

MODE_REACH = {"cone_locked": 1, "area_locked": 2, "weapon": 2, "line_locked": 8}


def reach_of(rng, mode):
    """How far this attack really carries. A null range means the shape decides:
    a cone is adjacent, a lane is the whole row, 雷霆龙 is the whole board."""
    if rng is not None:
        return max(1, rng)
    return MODE_REACH.get(mode, 8)


def role_of(reach, hp, move):
    """back — outranges the fight and should keep its distance.
    flank — too soft to trade in the line, fast enough to go round it.
    front — heavy and short-ranged: its job is to be the thing being hit.
    mid   — everything else: close to its own range and fight."""
    if reach >= 4:
        return "back"
    if move >= 2 and hp <= 15:
        return "flank"
    if hp >= 18:
        return "front"
    return "mid"


def hero_role(h):
    """The role of a drafted card, before there is an entity to ask."""
    if h.deploys:
        return "fixed"       # 世界树: the board stands it somewhere, not its owner
    return role_of(reach_of(h.attack.get("range"), h.attack.get("mode")),
                   h.max_hp, h.move)


def entity_role(e):
    """The role of a body as it stands now — armour dug up and buffs included."""
    if not e.flags["takes_turns"]:
        return "fixed"
    return role_of(reach_of(e.rng, (e.attack_spec or {}).get("mode")),
                   e.max_hp, max(0, e.move_allowance))


# How close each role wants the nearest enemy to be. `back` is the only one held
# to it from both directions: everyone else is merely pulled forward.
def wanted_distance(e, role):
    if role == "front" or role == "flank":
        return 1
    return reach_of(e.rng, (e.attack_spec or {}).get("mode"))


RANK = {"front": 0, "flank": 1, "mid": 2, "back": 3, "fixed": 4}


def deployment(m, side, bodies, free):
    """Which square each body starts on. `free` is the side's zone sorted front
    rank first; the answer is one cell per body, in the same order.

    Filling the front rank in draft order puts 占星师 in front of 门神 as often
    as not, and a back-line hero that starts inside the enemy's first charge is
    usually dead before it has fired twice. Heavies take the front rank, the
    soft long-ranged bodies take the back, and the flankers take a wing."""
    if len(bodies) > len(free):
        return None
    rows = [c[1] for c in free]
    middle = (min(rows) + max(rows)) / 2.0
    pool = list(free)
    out = [None] * len(bodies)
    for i in sorted(range(len(bodies)),
                    key=lambda j: (RANK[hero_role(H.BY_KEY[bodies[j]])], j)):
        if hero_role(H.BY_KEY[bodies[i]]) == "flank" and len(pool) > 1:
            # A wing, and forward: going round the line means starting beside it.
            front = pool[:max(1, len(pool) // 2)]
            pick = max(front, key=lambda c: abs(c[1] - middle))
        else:
            pick = pool[0]
        pool.remove(pick)
        out[i] = pick
    return out


def project(m):
    """The mutable part of the board: id -> [cell, hp, alive]. Everything else a
    move cannot change inside one turn, so it is read from the entity."""
    return {e.id: [e.cell, e.hp, True] for e in m.living()}


def evaluate(m, proj, side):
    """What this projected board is worth to `side`. Positive is good for it."""
    total = 0.0
    mine, theirs = [], []
    for eid, st in proj.items():
        e = m.entity(eid)
        if e is None or not st[2]:
            continue
        (mine if e.side == side else theirs).append((e, st))

    for group, sign in ((mine, 1.0), (theirs, -1.0)):
        for e, st in group:
            if not e.flags["counts_for_defeat"]:
                continue          # scenery is not material
            total += sign * (W_HP * st[1] + hero_worth(e))


    # Who can reach whom once the dust settles — counting the step they take
    # first. A hero two squares from a range-1 enemy that moves 1 is in danger
    # now, not later, and an evaluation that only sees who is already in range
    # cannot tell a safe square from a fatal one.
    def can_strike(a, a_cell, b_cell):
        if a_cell is None or b_cell is None:
            return False
        if a.rng is None:
            return True                      # 雷霆龙 reaches the whole board
        return m.topology.distance(a_cell, b_cell) <= a.rng + max(0, a.move_allowance)

    for e, st in mine:
        if st[0] is None or not e.flags["takes_turns"]:
            continue
        role = entity_role(e)
        incoming, nearest = 0, None
        for f, fst in theirs:
            if fst[0] is None:
                continue
            d = m.topology.distance(st[0], fst[0])
            if nearest is None or d < nearest:
                nearest = d
            if f.flags["targetable"] and can_strike(e, st[0], fst[0]):
                # Being able to reach the 13-health astrologer is not the same
                # as being able to reach the 28-health gatekeeper. Price it by
                # how much of the target the blow would actually take off, and
                # the soft bodies at the back become the prize they should be.
                share = 1.0
                if fst[1] > 0:
                    share = min(e.atk or 0, fst[1]) / float(fst[1])
                    if role == "flank":
                        share *= 2.0     # its whole job is getting back there
                total += W_REACH * (e.atk or 0) * share
            if f.flags["takes_turns"] and can_strike(f, fst[0], st[0]):
                incoming += (f.atk or 0)
        # Standing somewhere the whole enemy line can finish you is not a small
        # penalty on top of the ordinary one — it is losing the hero.
        if e.flags["counts_for_defeat"] and incoming >= st[1] > 0:
            total -= W_DOOMED * (W_HP * st[1] + hero_worth(e))
        else:
            total -= W_EXPOSURE * incoming
        if nearest is not None:
            want = wanted_distance(e, role)
            # Held to its distance from both sides if it outranges the fight,
            # otherwise merely pulled toward it. Walking a heavy at the softest
            # enemy rather than the closest one was tried and measured at 50.2%
            # against this in melee-only drafts: on a nine-wide board a move-1
            # body cannot reach the back rank whoever it aims at.
            if role == "back":
                # Held to its distance from both sides, and flat: a hero that
                # already outranges the fight has no closing to be talked into,
                # and scaling this only makes it fuss over its exact spacing
                # instead of shooting. Measured: threat-scaling here cost it.
                # Measured against whatever is *closest* — that is a rule about
                # not being caught, so pointing it at a distant focus would walk
                # the one hero that outranges the fight straight into it.
                total -= W_APPROACH * abs(nearest - want)
            else:
                total -= W_APPROACH * max(0, nearest - want) * push_of(e)

    # Ground that is going to hurt somebody, counted for whoever is standing on it.
    if m.board.effects:
        for e, st in mine + theirs:
            if st[0] is None:
                continue
            hurt = sum(eff.turn_damage(e) for eff in m.board.effects_at(st[0]))
            if hurt:
                total += (-W_HAZARD if e.side == side else W_HAZARD) * hurt
    return total


def _victims_in(m, e, cells, cap):
    return m.enemies_in([tuple(c) for c in cells], e.side)[:max(1, cap or 1)]


def predict(m, e, a, params, dest):
    """(hits, moves) for this order — what it would take off, and who it would
    shift. Read off the same previews the client is sent, so it stays true for a
    hero this file has never heard of. Returns None when the effect cannot be
    worked out, and the caller falls back to the cheap estimate."""
    t = a["targeting"]
    kind = t.get("kind")
    key = a["key"]
    origin = tuple(dest) if dest else e.cell
    hits, moves = [], []

    if key == "attack":
        atk = (e.atk or 0) + int(params.get("spend") or 0)
        if kind == "cells":
            cells = [c for shot in params.get("shots", []) for c in shot]
            for v in _victims_in(m, e, cells, e.targets):
                hits.append((v, atk))
            return hits, moves
        if kind == "unit":
            ids = params.get("targets") or ([params["target"]] if params.get("target") else [])
            for i in ids:
                v = m.entity(i)
                if v is not None and v.side != e.side:
                    hits.append((v, atk))
            return hits, moves
        if kind == "area" and origin:
            for v in m.enemies_in(m.attack_shape(e, origin), e.side):
                hits.append((v, atk))
            return hits, moves
        if kind == "cone" and origin:
            from actions import ConeAttack
            cells = ConeAttack.cells(m, e, params.get("direction"), origin)
            for v in m.enemies_in(cells, e.side):
                hits.append((v, atk))
            return hits, moves
        if kind == "lane":
            from actions import LineShot
            shot = LineShot.scan(m, e, params.get("direction"), origin)
            if shot:
                hits.append((shot[0], max(0, shot[1] + (e.atk or 0))))
            return hits, moves
        return None

    if not key.startswith("ability:"):
        return hits, moves

    ab = next((x for x in e.abilities if "ability:" + x.key == key), None)
    if ab is None:
        return None
    # What it takes off, straight from the ability itself.
    try:
        for ev in ab.build_damage(m, e, params):
            if ev.target is not None:
                hits.append((ev.target, ev.amount))
    except Exception:
        return None
    # And who it shifts, from the lane preview the client is offered.
    for ch in (t.get("choices") or []):
        if ch.get("dir") != params.get("direction"):
            continue
        landing = ch.get("landing")
        who = m.entity(ch.get("mover")) if ch.get("mover") is not None else None
        if who is not None and landing:
            moves.append((who, tuple(landing)))
        for vid in ch.get("victims") or []:
            v = m.entity(vid)
            if v is not None and ch.get("damage") and not any(h[0] is v for h in hits):
                hits.append((v, ch["damage"]))
    return hits, moves


def apply_effects(m, proj, hits, moves, mover=None, dest=None):
    """Fold a predicted turn into a projection. `hits` is [(entity, amount)],
    `moves` is [(entity, cell)] for anybody shifted by it."""
    out = dict(proj)
    for e, cell in moves:
        st = out.get(e.id)
        if st is not None:
            out[e.id] = [tuple(cell), st[1], st[2]]
    if mover is not None and dest is not None:
        st = out.get(mover.id)
        if st is not None:
            out[mover.id] = [tuple(dest), st[1], st[2]]
    for e, amount in hits:
        st = out.get(e.id)
        if st is None or amount <= 0:
            continue
        hp = st[1] - amount
        out[e.id] = [st[0], max(0, hp), hp > 0]
    return out


# --- choosing an order ------------------------------------------------------

CHEAP_WEIGHT = 0.35    # what the old estimate is worth where a real one is missing


def declared_gain(m, e, a, params):
    """What an ability says it is worth, for the ones whose whole effect is
    invisible from outside — armour beaten out of ore, a stance taken, ground
    prepared. `build_damage` and the client's previews tell this file what a
    turn takes off and what it shifts; nothing tells it what a turn *builds*, so
    an ability may answer for itself with `gain(match, owner, params)` in the
    same units as the evaluation (health, roughly). Optional everywhere."""
    key = a["key"]
    if not key.startswith("ability:"):
        return 0.0
    ab = next((x for x in e.abilities if "ability:" + x.key == key), None)
    fn = getattr(ab, "gain", None)
    if fn is None:
        return 0.0
    try:
        return float(fn(m, e, params) or 0.0)
    except Exception:
        return 0.0


def best_order(m, e, pending, enumerate_candidates):
    """The order this hero should give. `enumerate_candidates` yields
    (dest, action, params, cheap_score) — the same generators the old AI uses, so
    the two are choosing from the same moves and only the judging differs.

    Every candidate is costed cheaply, then a shortlist is judged properly by
    what the board is worth afterwards. The shortlist always keeps the best of
    each *kind* of action, or a hero whose whole job is worth nothing to the
    cheap estimate — throwing somebody, laying ground — would be filtered out
    before anything looked at it properly."""
    cands = list(enumerate_candidates(m, e, pending))
    if not cands:
        return 0.0, {"destination": None, "action": {"key": "none"}}

    # Best candidate per (action, square). Keeping only the best of each *action*
    # would collapse the square it is made from, and where a hero ends up is half
    # of what is being judged — the whole point is to compare positions, so every
    # square a move could be made from has to survive into the judging.
    by_move = {}
    for c in cands:
        k = (c[1]["key"], c[0])
        if k not in by_move or c[3] > by_move[k][3]:
            by_move[k] = c
    shortlist = list(by_move.values())

    base_proj = project(m)
    base = evaluate(m, base_proj, e.side)
    best = (float("-inf"), {"destination": None, "action": {"key": "none"}})
    for dest, a, params, cheap in shortlist:
        got = predict(m, e, a, params, dest)
        if got is None:
            worth = base + CHEAP_WEIGHT * cheap
        else:
            hits, moves = got
            proj = apply_effects(m, base_proj, hits, moves, mover=e, dest=dest or e.cell)
            worth = evaluate(m, proj, e.side)
            if not hits and not moves:
                # Nothing this file can see — a ward, a buff, a charge laid. Trust
                # the old estimate rather than calling it worthless, and let the
                # ability speak for itself if it has anything to say.
                worth += CHEAP_WEIGHT * cheap
                worth += W_GAIN * declared_gain(m, e, a, params)
        if worth > best[0]:
            best = (worth, {"destination": list(dest) if dest else None,
                            "action": dict(params, key=a["key"])})
    # A *delta*, not the board's absolute worth. The caller compares scores across
    # heroes to decide which one acts this exchange, and every hero is looking at
    # the same board — absolute values differ by almost nothing, so returning them
    # turns that choice into a coin flip.
    return best[0] - base, best[1]
