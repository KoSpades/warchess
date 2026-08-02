"""Headless play-testing: a greedy AI drives both sides through whole matches.

    python3 playtest.py            # round-robin, every hero against every other
    python3 playtest.py 猛犸 狼人    # one matchup, verbose

Each side fields the hero under test plus a training dummy, so the dummies cancel
out and what is left is the hero. Every pair plays twice, once from each side, so
the first-pick advantage cancels too.

The AI is deliberately simple — one ply, greedy on expected damage — and applies
equally to both sides. That makes the numbers fair *for comparison*, but it will
undersell any hero whose value comes from planning several turns ahead. Those are
called out in the report rather than hidden.
"""

import random
import sys
from itertools import combinations

import heroes as H
import view
from actions import ConeAttack, LineShot
from match import Match
from topology import LEFT, RIGHT, other_side

MAX_ROUNDS = 30
PARTNER = "dummy"          # a neutral body, so the comparison is hero vs hero


# ----------------------------------------------------------------- scoring

def enemies_of(m, e):
    return [x for x in m.living(other_side(e.side)) if x.flags["targetable"] and x.cells]


def nearest(m, e, origin):
    foes = enemies_of(m, e)
    if not foes or origin is None:
        return None
    return min(foes, key=lambda x: m.topology.distance(origin, x.cell))


def kill_bonus(target, damage):
    """Finishing something off is worth more than the raw numbers say."""
    return 6 if target is not None and damage >= target.hp else 0


def attack_options(m, e, a, origin):
    """Every way this attack could be aimed from `origin`, with what it would do.
    Yields (params, score)."""
    t = a["targeting"]
    kind = t["kind"]
    foes = enemies_of(m, e)
    if origin is None or not foes:
        return

    if kind == "cells":
        # Mark the squares the nearest enemies stand on, within reach.
        reach = [f for f in foes if m.topology.distance(origin, f.cell) <= (e.rng or 0)]
        if not reach:
            return
        picked = [f.cell for f in sorted(reach, key=lambda f: f.hp)[:t["count"]]]
        shots = [[list(c) for c in picked] for _ in range(t["shots"])]
        best = min(reach, key=lambda f: f.hp)
        yield {"shots": shots}, e.atk + kill_bonus(best, e.atk)

    elif kind == "unit":
        want = t.get("count", 1)
        if want > 1:
            picked = sorted(foes, key=lambda x: x.hp)[:want]
            if len(picked) == want:
                yield ({"targets": [f.id for f in picked]},
                       e.atk * want + max(kill_bonus(f, e.atk) for f in picked))
        else:
            f = min(foes, key=lambda x: x.hp)
            yield {"target": f.id}, e.atk + kill_bonus(f, e.atk)

    elif kind == "area":
        cells = set(map(tuple, t["cells"]))
        hit = [f for f in foes if f.cells & cells]
        if hit:
            yield {}, e.atk * len(hit) + max((kill_bonus(f, e.atk) for f in hit), default=0)

    elif kind == "cone":
        for d in t["dirs"]:
            cells = set(map(tuple, d["cells"]))
            hit = [f for f in foes if f.cells & cells]
            if hit:
                yield ({"direction": d["dir"]},
                       e.atk * len(hit) + max((kill_bonus(f, e.atk) for f in hit), default=0))

    elif kind == "lane":
        for d in t["dirs"]:
            shot = LineShot.scan(m, e, d, origin)
            if shot:
                target, dist = shot
                dmg = max(0, dist + e.atk)
                yield {"direction": d}, dmg + kill_bonus(target, dmg)

    elif kind == "weapon":
        for w in (a.get("weapons") or []):
            if w["mode"] == "cells":
                reach = [f for f in foes if m.topology.distance(origin, f.cell) <= w["range"]]
                if reach:
                    yield ({"weapon": w["key"], "shots": [[list(reach[0].cell)]]},
                           w["atk"] + kill_bonus(reach[0], w["atk"]))
            elif w["mode"] == "row":
                hit = [f for f in foes if f.cell[1] == origin[1]]
                if hit:
                    yield {"weapon": w["key"]}, w["atk"] * len(hit)
            elif w["mode"] == "surround8":
                hit = [f for f in foes if m.topology.distance(origin, f.cell) <= 2
                       and abs(f.cell[0] - origin[0]) <= 1 and abs(f.cell[1] - origin[1]) <= 1]
                if hit:
                    yield {"weapon": w["key"]}, w["atk"] + kill_bonus(hit[0], w["atk"])


def ability_options(m, e, a, origin):
    """Every way this ability could be used, scored by the damage it would do —
    read straight off the ability itself — plus a flat value for utility so the
    AI still bothers with heals and buffs."""
    ab = next((x for x in e.abilities if "ability:" + x.key == a["key"]), None)
    if ab is None:
        return
    t = a["targeting"]
    kind = t["kind"]
    foes = enemies_of(m, e)
    allies = [x for x in m.living(e.side) if x is not e]

    def damage_of(params):
        try:
            evs = ab.build_damage(m, e, params)
        except Exception:
            return None
        return sum(ev.amount for ev in evs), evs

    if kind == "none":
        got = damage_of({})
        if got and got[0]:
            best = max((kill_bonus(ev.target, ev.amount) for ev in got[1]), default=0)
            yield {}, got[0] + best
        else:
            yield {}, 4          # a buff or a ward: worth using, not worth forcing

    elif kind == "unit":
        for f in foes:
            got = damage_of({"target": f.id})
            dmg = got[0] if got else 0
            yield {"target": f.id}, dmg + kill_bonus(f, dmg)

    elif kind == "ally":
        hurt = [x for x in allies + [e] if x.hp < x.max_hp]
        if hurt:
            worst = min(hurt, key=lambda x: x.hp / max(1, x.max_hp))
            yield {"target": worst.id}, min(6, worst.max_hp - worst.hp)
        else:
            yield {"target": e.id}, 1

    elif kind == "any_cell":
        cells = t.get("cells")
        if cells:
            yield {"cell": list(cells[0])}, 5
        else:
            f = nearest(m, e, origin)
            if f:
                yield {"cell": list(f.cell)}, 3

    elif kind in ("direction", "cone", "shape"):
        opts = t.get("options") or [d["dir"] if isinstance(d, dict) else d
                                    for d in t.get("dirs", [])]
        for d in opts:
            got = damage_of({"direction": d})
            yield {"direction": d}, (got[0] if got else 0)

    elif kind == "two_units":
        # Two honest uses: drag the enemy's weakest into our line, and pull our
        # own most wounded out of theirs. Modest scores — it is a tempo play.
        ours = [x for x in m.living(e.side) if x.cells]
        if foes and ours:
            weakest_foe = min(foes, key=lambda x: x.hp)
            hurt = min(ours, key=lambda x: x.hp / max(1, x.max_hp))
            if hurt is not weakest_foe:
                yield {"first": hurt.id, "second": weakest_foe.id}, 4
            far = max(foes, key=lambda x: m.topology.distance(origin, x.cell)) if origin else None
            near = min(foes, key=lambda x: m.topology.distance(origin, x.cell)) if origin else None
            if far is not None and near is not None and far is not near:
                yield {"first": near.id, "second": far.id}, 3

    elif kind == "magnitude":
        cap = ab.magnitude_cap(e)
        if cap >= 1:
            yield {"amount": max(1, cap // 2)}, 4


def best_order(m, e, pending=None):
    """The order this hero should give, by a one-ply greedy score. `pending` is the
    destinations already chosen by comrades in the same gang turn, so a body placed
    relative to another (蛇帝's tail) is scored against where that one is going."""
    menu = m.action_menu(e)
    zone, dictated = m.move_zone(e, pending)
    dests = [tuple(c) for c in zone] if dictated \
        else [None] + [tuple(c) for c in m.legal_moves(e, pending)]
    best = (float("-inf"), {"destination": None, "action": {"key": "none"}})

    for dest in dests:
        origin = dest or e.cell
        # Closing on the enemy is worth a little, so melee heroes advance.
        f = nearest(m, e, origin)
        approach = -0.30 * m.topology.distance(origin, f.cell) if (f and origin) else 0.0
        for a in menu:
            if a.get("affordable") is False:
                continue
            if a["key"] == "none":
                cand = [({}, 0.0)]
            elif a["key"] == "attack":
                cand = list(attack_options(m, e, a, origin))
            else:
                cand = list(ability_options(m, e, a, origin))
            for params, score in cand:
                total = score + approach
                if total > best[0]:
                    action = dict(params, key=a["key"])
                    best = (total, {"destination": list(dest) if dest else None,
                                    "action": action})
    return best


# ------------------------------------------------------------------- play

def free_picks(m, e, payload):
    """Answer any pick that rides along with the turn (杂货店爷爷's handout)."""
    choices = {}
    for ch in m.turn_choices(e):
        if ch["options"]:
            choices[ch["key"]] = ch["options"][0]
    if choices:
        payload["choices"] = choices
    return payload


def take_turn(m, side):
    pool = m.unacted(side)
    if not pool:
        return
    scored = []
    for e in pool:
        score, payload = best_order(m, e)
        scored.append((score, e, payload))
    score, e, payload = max(scored, key=lambda x: x[0])
    if m.select_hero(side, e.id) is not None:
        return
    actors = m.turn_actors(e)
    if m.gang_of(e) and len(actors) > 1:
        # Ranked squads act in a fixed order (蛇帝: head, then tail); unranked ones
        # keep deployment order. Each body is scored knowing where the earlier ones
        # are going, so two of them never claim the same square.
        actors = sorted(actors, key=lambda g: (g.hero.gang_rank is None, g.hero.gang_rank or 0))
        orders, pending = [], {}
        for g in actors:
            _, p = best_order(m, g, pending)
            pending[g.id] = tuple(p["destination"]) if p["destination"] else g.cell
            orders.append(free_picks(m, g, dict(p, entity=g.id)))
        err = m.commit(side, {"orders": orders})
    else:
        err = m.commit(side, free_picks(m, e, payload))
    if err:                                   # never leave a side stuck
        m.commit(side, {"destination": None, "action": {"key": "none"}})


def play(left_key, right_key, seed=0, verbose=False):
    """One hero a side, each with a training dummy. Returns 'L', 'R' or 'draw'."""
    return play_teams([left_key, PARTNER], [right_key, PARTNER], seed, verbose)


def play_teams(left, right, seed=0, verbose=False):
    """One match between two whole forces. Returns 'L', 'R' or 'draw'."""
    random.seed(seed)
    m = Match()
    m.assign_draft(list(left), list(right))
    for side, col in ((LEFT, 2), (RIGHT, 8)):
        # Down one column, and spilling into the next if a squad brings extra bodies.
        free = [(c, r) for c in (col, col - 1 if side == LEFT else col + 1)
                for r in range(1, m.topology.rows + 1)]
        m.setup_state[side]["placements"] = [
            {"key": k, "cell": list(c)} for k, c in zip(m.deploy_bodies(side), free)
        ]
        m.setup_state[side]["ready"] = True
    m.begin()

    guard = 0
    while m.phase != "gameover" and m.round <= MAX_ROUNDS:
        guard += 1
        if guard > 4000:
            break
        if m.phase == "opening":
            for side in (LEFT, RIGHT):
                if m.opening is None or m.phase != "opening":
                    break        # the last pick ends the phase
                pend = m.opening["pending"][side]
                if pend:
                    e = m.entity(pend[0]["entity"])
                    ab = next(a for a in e.abilities if a.key == pend[0]["ability_key"])
                    t = ab.targeting["kind"]
                    if t == "ally":
                        m.opening_choose(side, {"target": e.id})
                    elif t == "any_cell":
                        m.opening_choose(side, {"cell": [5, 3]})
                    elif t == "unit":
                        # Name the frailest enemy: the likeliest to fall first.
                        foes = [x for x in m.living(other_side(side)) if x.cells]
                        m.opening_choose(
                            side, {"target": min(foes, key=lambda x: x.hp).id if foes else e.id})
                    else:
                        m.opening_choose(side, {})
        elif m.phase == "victim":
            if m.res is None:
                break
            for side in (LEFT, RIGHT):
                # answering one side can finish the whole exchange
                if m.res is None or m.phase != "victim":
                    break
                opts = m.res["options"][side]
                # A shot may want several victims (猎人 once blooded), so keep
                # naming until it is satisfied rather than stopping after one.
                while opts and not m.victims_complete(side) and m.phase == "victim":
                    picked = m.res["picks"][side] or []
                    rest = [i for i in opts if i not in picked]
                    if not rest:
                        break
                    m.choose_victim(side, min(rest, key=lambda i: m.entity(i).hp))
        elif m.phase == "move_choice":
            for side in (LEFT, RIGHT):
                if m.phase != "move_choice":
                    break
                if m.move_choices[side]:
                    task = m.move_choices[side][0]
                    e = m.entity(task["entity"])
                    # Appear on the side of the mark furthest from the rest of its
                    # friends — a crude stand-in for not landing in a crossfire.
                    foes = enemies_of(m, e) if e else []
                    pick = task["options"][0]
                    if foes:
                        pick = max(task["options"],
                                   key=lambda c: min(m.topology.distance(tuple(c), f.cell)
                                                     for f in foes if f.cells))
                    m.choose_move(side, pick)
        elif m.phase == "resolved":
            for side in (LEFT, RIGHT):
                if m.phase != "resolved":
                    break
                if m.followups[side]:
                    task = m.followups[side][0]
                    # Toward the enemy: a step after a shot, or ground worth mining.
                    # Keyed off the enemy rather than the acting unit, because a
                    # parting shot comes from a hero that no longer has a square.
                    pick = None
                    if task.get("kind") == "unit":
                        opts = [m.entity(i) for i in task["options"]]
                        opts = [o for o in opts if o is not None and o.alive]
                        if opts:
                            pick = min(opts, key=lambda o: o.hp).id
                    else:
                        foes = [x for x in m.living(other_side(side)) if x.cells]
                        if foes and task["options"]:
                            pick = min(task["options"],
                                       key=lambda c: min(m.topology.distance(tuple(c), f.cell)
                                                         for f in foes))
                    m.choose_followup(side, pick)
        elif m.phase == "commit":
            for side in (LEFT, RIGHT):
                if m.commits[side] is None:
                    take_turn(m, side)
        else:
            break

    if verbose:
        for line in m.log[-25:]:
            print("   ", line["text"])
    if m.winner in (LEFT, RIGHT):
        return m.winner
    return "draw"


# ---------------------------------------------------------------- reports

def playable():
    """Every hero a side can field on its own — squad members are deployed by
    their card, not drafted."""
    return [h.key for h in H.ROSTER]


def random_tournament(n=3000, team_size=2, seed=0):
    """Random forces on both sides, over and over. A hero's rate is then measured
    across many different partners and many different opponents, so neither a
    lucky pairing nor a fixed partner can flatter it."""
    rng = random.Random(seed)
    pool = playable()
    rec = {k: {"games": 0, "score": 0.0, "survived": 0} for k in pool}
    draws = 0
    for i in range(n):
        left = rng.sample(pool, team_size)
        right = rng.sample(pool, team_size)
        res = play_teams(left, right, seed=i)
        if res == "draw":
            draws += 1
        for team, side in ((left, LEFT), (right, RIGHT)):
            got = 0.5 if res == "draw" else (1.0 if res == side else 0.0)
            for k in team:
                rec[k]["games"] += 1
                rec[k]["score"] += got
    return rec, draws


def round_robin(keys, reps=1):
    wins = {k: 0.0 for k in keys}
    games = {k: 0 for k in keys}
    for a, b in combinations(keys, 2):
        for i in range(reps):
            for left, right in ((a, b), (b, a)):
                res = play(left, right, seed=i)
                games[left] += 1
                games[right] += 1
                if res == "draw":
                    wins[left] += 0.5
                    wins[right] += 0.5
                elif res == LEFT:
                    wins[left] += 1
                else:
                    wins[right] += 1
    return [(k, wins[k] / max(1, games[k]), games[k]) for k in keys]


if __name__ == "__main__":
    args = sys.argv[1:]
    by_name = {h.name: h.key for h in H.ROSTER}
    if len(args) == 2:
        a, b = (by_name.get(x, x) for x in args)
        print(f"{a} vs {b}")
        for i in range(3):
            print("  game", i, "->", play(a, b, seed=i, verbose=(i == 0)))
    else:
        keys = playable()
        table = sorted(round_robin(keys), key=lambda r: -r[1])
        name = {h.key: h.name for h in H.ROSTER}
        print(f"{'hero':<14}{'win rate':>10}{'games':>8}")
        for k, rate, n in table:
            print(f"{name[k]:<14}{rate:>9.1%}{n:>8}")
