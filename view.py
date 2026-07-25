"""Per-player view filtering (spec 7.10).

The server holds authoritative state; each client is handed only what its side
is entitled to. During the commitment phase enemy units render from the
snapshot taken at the start of the exchange, so a turn-start fire tick cannot
betray which hero the opponent picked up.
"""

import heroes as HEROES
import match as M
from topology import LEFT, RIGHT, other_side


def hero_card(h):
    return {
        "key": h.key,
        "name": h.name,
        "name_en": h.name_en,
        "hp": h.max_hp,
        "atk": h.atk,
        "move": h.move,
        "max_ap": h.max_ap,
        "attack": h.attack,
        "shots": h.attacks_per_turn,
        "blurb": h.blurb,
        "traits": HEROES.describe(h),
    }


def roster_payload():
    return [hero_card(h) for h in HEROES.ROSTER]


def unit_payload(m, e, live):
    if live or e.id not in m.snapshot:
        hp, ap, cell, acted, alive = e.hp, e.ap, list(e.cell) if e.cells else None, e.has_acted, e.alive
    else:
        s = m.snapshot[e.id]
        hp, ap, cell, acted, alive = s["hp"], s["ap"], s["cell"], s["acted"], s["alive"]
    return {
        "id": e.id,
        "side": e.side,
        "key": e.key,
        "name": e.name,
        "name_en": e.name_en,
        "hp": hp,
        "max_hp": e.max_hp,
        "ap": ap,
        "max_ap": e.max_ap,
        "cell": cell,
        "acted": acted,
        "alive": alive,
        "atk": e.atk,
        "rng": e.rng,
        "shots": e.hero.attacks_per_turn,
        "attack": e.hero.attack,
    }


def state_for(m, side):
    foe = other_side(side)
    live_enemy = m.phase != M.COMMIT
    units = []
    for e in m.entities:
        units.append(unit_payload(m, e, live=(e.side == side) or live_enemy))

    out = {
        "version": m.version,
        "phase": m.phase,
        "round": m.round,
        "exchange": m.exchange,
        "you": side,
        "winner": m.winner,
        "mode": m.mode,
        "both_present": m.both_present(),
        "codex": {k: hero_card(h) for k, h in HEROES.BY_KEY.items()},
        "board": {"cols": m.topology.cols, "rows": m.topology.rows},
        "zone": [list(c) for c in m.topology.deployment_zone(side)],
        "tiles": m.board.serialise(),
        "units": units,
        "log": m.log[-60:],
        "reveal": m.last_reveal,
    }

    if m.phase == M.DRAFT:
        d = m.draft
        cards = {c["key"]: c for c in roster_payload()}
        out["draft"] = {
            "picker": d["picker"],
            "your_pick": d["picker"] == side,
            "step": d["step"],
            "total": len(d["order"]),
            "pair": [cards[k] for k in (d["pair"] or [])],
            "taken": {
                LEFT: [cards[k] for k in m.drafted[LEFT]],
                RIGHT: [cards[k] for k in m.drafted[RIGHT]],
            },
        }
        return out

    if m.phase == M.SETUP:
        drafted = set(m.drafted[side])
        out["roster"] = [c for c in roster_payload() if c["key"] in drafted]
        out["setup"] = {
            "force_size": m.force_size,
            "placements": m.setup_state[side]["placements"],
            "ready": m.setup_state[side]["ready"],
            "opponent_ready": m.setup_state[foe]["ready"],
        }
        return out

    if m.phase == M.OPENING:
        pend = m.opening["pending"][side]
        task = None
        if pend:
            e = m.entity(pend[0]["entity"])
            ab = next(a for a in e.abilities if a.key == pend[0]["ability_key"])
            task = {"entity": e.id, "hero": e.name, "hero_en": e.name_en,
                    "ability": ab.name, "text": ab.blurb, "targeting": ab.targeting}
        out["opening"] = {
            "task": task,
            "waiting_on_opponent": (not pend) and bool(m.opening["pending"][foe]),
        }
        return out

    if m.phase == M.COMMIT:
        c = {"sealed": m.commits[side] is not None,
             "opponent_sealed": m.commits[foe] is not None,
             "selected": m.selected[side],
             "unacted": [e.id for e in m.unacted(side)]}
        eid = m.selected[side]
        if eid is not None and m.commits[side] is None:
            e = m.entity(eid)
            c["legal_moves"] = m.legal_moves(e)
            c["actions"] = m.action_menu(e)
            c["enemies"] = [x.id for x in m.living(foe)]
            c["ap"] = e.ap
        out["commit"] = c
        return out

    if m.phase == M.VICTIM:
        opts = m.res["options"][side] if m.res else []
        inst = None
        if m.res:
            plan = m.res["plan"][side]
            idx = m.res["index"]
            inst = plan[idx] if idx < len(plan) else None
        cells = []
        if inst is not None and hasattr(inst, "resolved_cells"):
            cells = [list(c) for c in inst.resolved_cells(m)]
        out["victim"] = {
            "needed": bool(opts) and m.res["picks"][side] is None,
            "options": opts,
            "cells": cells,
            "waiting_on_opponent": bool(m.res)
            and not (bool(opts) and m.res["picks"][side] is None),
        }
        return out

    return out
