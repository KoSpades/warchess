"""Per-player view filtering (spec 7.10).

The server holds authoritative state; each client is handed only what its side
is entitled to. During the commitment phase enemy units render from the
snapshot taken at the start of the exchange, so a turn-start fire tick cannot
betray which hero the opponent picked up.
"""

import functools
import os

import heroes as HEROES
import match as M
from topology import LEFT, RIGHT, other_side

# Two kinds of hero art, one folder each: full portraits for the draft cards and
# the pause screen, small sprites for the board tokens. This is the single source
# of truth for where they live — server.py serves exactly these subfolders.
IMAGE_ROOT = os.path.join(os.path.dirname(__file__), "image")
ART_SUBDIR = "full_pic"
SPRITE_SUBDIR = "sprite"
IMAGE_SUBDIRS = (ART_SUBDIR, SPRITE_SUBDIR)


@functools.lru_cache(maxsize=None)
def _art_url(subdir, name_en):
    """URL for image/<subdir>/<name_en>.png (camelCase), or None if absent. Cached:
    art never changes during a session and unit payloads are built on every poll."""
    path = os.path.join(IMAGE_ROOT, subdir, f"{name_en}.png")
    return f"/image/{subdir}/{name_en}.png" if os.path.isfile(path) else None


def hero_image(name_en):
    """Full portrait — draft cards, deployment roster, pause screen."""
    return _art_url(ART_SUBDIR, name_en)


def hero_sprite(name_en):
    """Board token art. Falls back to None, and the token renders as before."""
    return _art_url(SPRITE_SUBDIR, name_en)


def hero_card(h, with_squad=True):
    card = {
        "key": h.key,
        "name": h.name,
        "name_en": h.name_en,
        "hp": h.max_hp,
        "atk": h.atk,
        "move": h.move,
        "max_ap": h.max_ap,
        "attack": h.attack,
        "shots": h.attacks_per_turn,
        "gang": h.gang,
        "blurb": h.blurb,
        "traits": HEROES.describe(h),
        "image": hero_image(h.name_en),
        "sprite": hero_sprite(h.name_en),
    }
    if h.squad and with_squad:
        # A squad card is drawn from its members' cards, not its own numbers.
        card["squad"] = [hero_card(HEROES.BY_KEY[k], with_squad=False) for k in h.squad]
    return card


def roster_payload():
    return [hero_card(h) for h in HEROES.ROSTER]


def codex():
    """All heroes (including the dummy) keyed by key — static per session, so the
    client fetches it once from /api/codex rather than in every state poll."""
    return {k: hero_card(h) for k, h in HEROES.BY_KEY.items()}


def order_slip(m, o):
    """A sealed order, in words. Sent back to its own side so the slip can keep
    showing what you committed while the exchange waits on the other seat — and
    so it survives a page refresh, since the client's draft is gone by then."""
    e = m.entity(o["entity"])
    action = o.get("action") or {}
    key = action.get("key", "none")
    if key == "none":
        name = "Hold"
    elif key == "attack":
        name = "Normal attack"
    else:
        ab = next((a for a in e.abilities if a.key == key.split(":", 1)[1]), None)
        name = ab.name if ab else key

    target = "—"
    shots = [sh for sh in (action.get("shots") or []) if sh]
    if shots:
        # Squares have no names a player can read off the board, so the slip says
        # how wide the net was rather than naming cells nobody can find.
        target = "  /  ".join(
            f"{len(sh)} square{'' if len(sh) == 1 else 's'}" for sh in shots
        )
    if action.get("target") is not None:
        t = m.entity(action["target"])
        target = t.name if t else "—"
    if action.get("cell"):
        target = "a square"
    if action.get("direction"):
        target = action["direction"]
    if action.get("first") is not None and action.get("second") is not None:
        pair = [m.entity(action["first"]), m.entity(action["second"])]
        target = " ⇄ ".join(x.name if x else "—" for x in pair)
    if action.get("amount") is not None:
        target = str(action["amount"])
    if action.get("weapon"):
        w = HEROES.WEAPONS_BY_KEY.get(action["weapon"])
        if w:
            name = f"{name} · {w['name']}"

    dest = tuple(o["destination"]) if o.get("destination") else None
    return {
        "hero": e.name,
        "move": "hold" if (dest is None or (e.cells and dest == e.cell)) else "move",
        "action": name,
        "target": target,
    }


def unit_payload(m, e, live, viewer=None):
    if live or e.id not in m.snapshot:
        hp, ap, cell, acted, alive = e.hp, e.ap, list(e.cell) if e.cells else None, e.has_acted, e.alive
        status = HEROES.status_of(m, e)
    else:
        s = m.snapshot[e.id]
        hp, ap, cell, acted, alive = s["hp"], s["ap"], s["cell"], s["acted"], s["alive"]
        status = s.get("status", [])
    # Some badges are for their owner's eyes only (诅咒娃娃's mark).
    if viewer is not None and viewer != e.side:
        status = [x for x in status if not x.get("private")]
    return {
        "id": e.id,
        "side": e.side,
        "key": e.key,
        "name": e.name,
        "name_en": e.name_en,
        "gang": e.hero.gang,
        "sprite": hero_sprite(e.name_en),
        "hp": hp,
        "max_hp": e.max_hp,
        "ap": ap,
        "max_ap": e.max_ap,
        "cell": cell,
        "acted": acted,
        "alive": alive,
        "status": status,
        "atk": e.atk,
        "rng": e.rng,
        "grid": e.grid,
        "move": m.move_budget(e),
        "shots": e.hero.attacks_per_turn,
        "attack": e.hero.attack,
        # False = the other side may not aim anything at it (世界树, a bodiless 鬼魂).
        "targetable": e.flags["targetable"],
        # False = it never spends one of your exchanges (世界树, a sealed 土著).
        "acts": e.flags["takes_turns"],
    }


def state_for(m, side):
    foe = other_side(side)
    live_enemy = m.phase != M.COMMIT
    units = []
    for e in m.entities:
        units.append(unit_payload(m, e, live=(e.side == side) or live_enemy, viewer=side))

    out = {
        "version": m.version,
        "phase": m.phase,
        "round": m.round,
        "exchange": m.exchange,
        "you": side,
        "winner": m.winner,
        "mode": m.mode,
        "both_present": m.both_present(),
        "board": {"cols": m.topology.cols, "rows": m.topology.rows},
        "zone": [list(c) for c in m.topology.deployment_zone(side)],
        # Per side: a hidden effect (潜水者's bombs) is only ever sent to the side
        # that laid it, the same way a curse mark is.
        "tiles": m.board.serialise(side),
        # Doors are built into the board before anyone deploys, so both seats see
        # them. Sent with the side that may walk through, for the client to mark.
        # Squares cut out of the board into a sub-map of their own (探险家's 岛屿).
        # Both seats see them: the island is public from the moment it is charted.
        "offboard": [list(c) for c in m.topology.regions],
        # Squares a hero's passive wants drawn (四圣兽's shrines). Both seats see
        # them: where they are follows from the rules, so there is nothing to hide.
        "marks": HEROES.board_marks(m),
        "doors": [{"cells": [list(a), list(b)], "owner": owner}
                  for a, b, owner in m.topology.links],
        "units": units,
        "log": [l for l in m.log if l.get("side") in (None, side)][-60:],
        "reveal": m.last_reveal,
    }

    if m.phase == M.DRAFT:
        d = m.draft
        cards = {c["key"]: c for c in roster_payload()}
        out["draft"] = {
            "picker": d["picker"],
            "your_pick": d["picker"] == side,
            "batch": d["batch"] + 1,
            "batches_total": len(M.DRAFT_BATCHES),
            "shown": [cards[k] for k in d["shown"]],
            "taken": {
                LEFT: [cards[k] for k in m.drafted[LEFT]],
                RIGHT: [cards[k] for k in m.drafted[RIGHT]],
            },
        }
        return out

    if m.phase == M.SETUP:
        # One entry per body to place, so a squad shows up as its members and a
        # duplicated member (two 投矛手) shows up twice.
        cards = codex()
        out["roster"] = [cards[k] for k in m.deploy_bodies(side)]
        out["setup"] = {
            # Bodies, not cards: a squad card puts several units on the board.
            "force_size": m.bodies_needed(side),
            "placements": m.setup_state[side]["placements"],
            "ready": m.setup_state[side]["ready"],
            "opponent_ready": m.setup_state[foe]["ready"],
        }
        return out

    if m.phase == M.BUILD:
        hero, ab = m.build_ability(side)
        out["build"] = {
            "task": None if ab is None else {
                "hero": hero.name, "hero_en": hero.name_en,
                "ability": ab.name, "text": ab.blurb,
                "targeting": m.build_targeting(side, ab),
            },
            "waiting_on_opponent": ab is None and bool(m.build),
        }
        return out

    if m.phase == M.OPENING:
        pend = m.opening["pending"][side]
        task = None
        if pend:
            e = m.entity(pend[0]["entity"])
            ab = next(a for a in e.abilities if a.key == pend[0]["ability_key"])
            # The enriched targeting, not the bare class attribute: the validator
            # uses `ability_targeting`, so anything less and the client cannot know
            # which squares are legal (鸟嘴医生's plague, 潜水者's charge).
            task = {"entity": e.id, "hero": e.name, "hero_en": e.name_en,
                    "ability": ab.name, "text": ab.blurb,
                    "targeting": m.ability_targeting(e, ab)}
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
        if m.commits[side] is not None:
            c["kind"] = m.commits[side]["kind"]
            c["orders"] = [order_slip(m, o) for o in m.orders_of(m.commits[side])]
        eid = m.selected[side]
        if eid is not None and m.commits[side] is None:
            e = m.entity(eid)
            c["legal_moves"] = m.legal_moves(e)
            c["actions"] = m.action_menu(e)
            c["enemies"] = [x.id for x in m.living(foe) if x.flags["targetable"]]
            c["ap"] = e.ap
            c["choices"] = m.turn_choices(e)
            # Picking up a goblin picks up the gang: the client needs every
            # member's own moves and menu, since all of them act this turn.
            if m.gang_of(e):
                c["gang"] = {
                    "key": m.gang_of(e),
                    "members": [
                        {
                            "entity": g.id,
                            "name": g.name,
                            "name_en": g.name_en,
                            "cell": list(g.cell) if g.cells else None,
                            "ap": g.ap,
                            "legal_moves": m.legal_moves(g),
                            "actions": m.action_menu(g),
                            "choices": m.turn_choices(g),
                            # Squads that act in a fixed order say so, and a body
                            # placed against another names it — 哥布林团伙 sends
                            # neither, being three units that merely share a turn.
                            "rank": g.hero.gang_rank,
                            "move_anchor": m.move_anchor_of(g),
                        }
                        for g in m.turn_actors(e)
                    ],
                }
        out["commit"] = c
        return out

    if m.phase == M.RESOLVED:
        pend = m.followups[side]
        out["followup"] = {
            "task": pend[0] if pend else None,
            "waiting_on_opponent": (not pend) and bool(m.followups[foe]),
        }
        return out

    if m.phase == M.INTERRUPT:
        task = m.interrupts[0] if m.interrupts else None
        out["interrupt"] = {
            "task": task if task and task["side"] == side else None,
            "waiting_on_opponent": bool(task) and task["side"] != side,
        }
        return out

    if m.phase == M.MOVE_CHOICE:
        pend = m.move_choices[side]
        out["move_choice"] = {
            "task": pend[0] if pend else None,
            "waiting_on_opponent": (not pend) and bool(m.move_choices[foe]),
        }
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
            "needed": bool(opts) and not m.victims_complete(side),
            "options": [o for o in opts
                        if o not in (m.res["picks"][side] or [])],
            "picked": list(m.res["picks"][side] or []),
            "wanted": m.victims_wanted(side),
            "cells": cells,
            "waiting_on_opponent": bool(m.res)
            and not (bool(opts) and not m.victims_complete(side)),
        }
        return out

    return out
