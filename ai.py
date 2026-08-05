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
W_APPROACH = 0.15     # mild pull toward the fight, so nobody dawdles
W_DOOMED = 0.25       # how much of a hero is already lost if it can be finished


def hero_worth(e):
    """What losing this hero would cost its side, ignoring position."""
    return W_ALIVE + W_THREAT * (e.atk or 0)


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
        incoming, nearest = 0, None
        for f, fst in theirs:
            if fst[0] is None:
                continue
            d = m.topology.distance(st[0], fst[0])
            if nearest is None or d < nearest:
                nearest = d
            if f.flags["targetable"] and can_strike(e, st[0], fst[0]):
                total += W_REACH * (e.atk or 0)
            if f.flags["takes_turns"] and can_strike(f, fst[0], st[0]):
                incoming += (f.atk or 0)
        # Standing somewhere the whole enemy line can finish you is not a small
        # penalty on top of the ordinary one — it is losing the hero.
        if e.flags["counts_for_defeat"] and incoming >= st[1] > 0:
            total -= W_DOOMED * (W_HP * st[1] + hero_worth(e))
        else:
            total -= W_EXPOSURE * incoming
        if nearest is not None:
            total -= W_APPROACH * nearest

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
        holder = e
        hp = st[1] - amount
        out[holder.id] = [st[0], max(0, hp), hp > 0]
    return out


# --- choosing an order ------------------------------------------------------

SHORTLIST = 6          # how many candidates get the full treatment
CHEAP_WEIGHT = 0.35    # what the old estimate is worth where a real one is missing


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
                # the old estimate rather than calling it worthless.
                worth += CHEAP_WEIGHT * cheap
        if worth > best[0]:
            best = (worth, {"destination": list(dest) if dest else None,
                            "action": dict(params, key=a["key"])})
    # A *delta*, not the board's absolute worth. The caller compares scores across
    # heroes to decide which one acts this exchange, and every hero is looking at
    # the same board — absolute values differ by almost nothing, so returning them
    # turns that choice into a coin flip.
    return best[0] - base, best[1]
