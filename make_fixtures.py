"""Real engine states, written out for the browser client's tests to run against.

    python3 make_fixtures.py && node test_client.js

Everything here comes from an actual Match, so the client is always tested
against payloads the server would really send — not hand-written ones that drift.
"""

import json

import view
from match import Match
from topology import LEFT, RIGHT

HOLD = {"destination": None, "action": {"key": "none"}}


def arena(left, right):
    m = Match()
    m.assign_draft([k for k, _ in left], [k for k, _ in right])
    for k, c in left:
        assert m.place(LEFT, k, c) is None, (k, c)
    for k, c in right:
        assert m.place(RIGHT, k, c) is None, (k, c)
    assert m.lock_force(LEFT) is None
    assert m.lock_force(RIGHT) is None
    guard = 0
    while m.phase == "opening":          # answer any opening pick, whoever owns it
        guard += 1
        assert guard < 10, "opening never finished"
        for side in (LEFT, RIGHT):
            if m.opening and m.opening["pending"][side]:
                m.opening_choose(side, {"target": m.living(side)[0].id})
    return m


def unit(m, side, key):
    return next(e for e in m.living(side) if e.key == key)


def build():
    out = {}

    def snap(name, m, side=LEFT, select=None, full_ap=()):
        for e in full_ap:
            e.ap = e.max_ap
        if select is not None:
            assert m.select_hero(side, select) is None
        out[name] = view.state_for(m, side)

    # --- one per targeting kind, so every branch of the panel is covered ------
    m = arena([("tide_goddess", (3, 3)), ("gatekeeper", (3, 1))], [("dummy", (7, 3))])
    snap("ally_heal", m, select=unit(m, LEFT, "tide_goddess").id,
         full_ap=[unit(m, LEFT, "tide_goddess")])

    m = arena([("fire_mage", (3, 3))], [("dummy", (7, 3))])
    snap("any_cell_ignite", m, select=m.living(LEFT)[0].id, full_ap=[m.living(LEFT)[0]])

    m = arena([("spearman", (3, 3))], [("dummy", (7, 3))])
    snap("direction_sweep", m, select=m.living(LEFT)[0].id, full_ap=[m.living(LEFT)[0]])

    m = arena([("swordsman", (3, 3))], [("dummy", (7, 3)), ("cannoneer", (7, 2))])
    unit(m, RIGHT, "dummy").set_cell((5, 3))
    unit(m, RIGHT, "cannoneer").set_cell((3, 5))
    snap("shape_cut", m, select=m.living(LEFT)[0].id, full_ap=[m.living(LEFT)[0]])

    # three shapes rather than two, so the overlapping-square rule gets exercised
    m = arena([("bomber", (3, 3))], [("dummy", (7, 3)), ("cannoneer", (7, 2))])
    unit(m, RIGHT, "dummy").set_cell((4, 4))
    unit(m, RIGHT, "cannoneer").set_cell((6, 3))
    snap("shape_blast", m, select=m.living(LEFT)[0].id, full_ap=[m.living(LEFT)[0]])

    m = arena([("blood_mage", (3, 3))], [("dummy", (7, 3))])
    snap("magnitude", m, select=m.living(LEFT)[0].id, full_ap=[m.living(LEFT)[0]])

    m = arena([("weapon_master", (3, 3))], [("dummy", (7, 3))])
    snap("weapon_master", m, select=m.living(LEFT)[0].id)

    m = arena([("thunder_dragon", (3, 3))], [("dummy", (7, 3))])
    snap("unit_locked", m, select=m.living(LEFT)[0].id)

    m = arena([("gunslinger", (3, 3))], [("dummy", (7, 3))])
    snap("two_shots", m, select=m.living(LEFT)[0].id)

    m = arena([("sniper", (3, 3))], [("dummy", (7, 3))])
    snap("sniper_one_lane", m, select=m.living(LEFT)[0].id)

    m = arena([("centaur", (3, 3)), ("gatekeeper", (3, 1))], [("dummy", (7, 3)), ("cannoneer", (8, 3))])
    unit(m, RIGHT, "dummy").set_cell((5, 3))
    snap("centaur_charge", m, select=unit(m, LEFT, "centaur").id,
         full_ap=[unit(m, LEFT, "centaur")])

    m = arena([("mammoth", (3, 3))], [("dummy", (7, 3)), ("cannoneer", (7, 2))])
    unit(m, RIGHT, "dummy").set_cell((4, 3))
    unit(m, RIGHT, "cannoneer").set_cell((4, 2))
    snap("mammoth", m, select=m.living(LEFT)[0].id)

    m = arena([("gunner", (3, 3))], [("dummy", (7, 3)), ("cannoneer", (7, 2))])
    unit(m, RIGHT, "dummy").set_cell((4, 3))
    unit(m, RIGHT, "cannoneer").set_cell((4, 2))
    snap("cone", m, select=m.living(LEFT)[0].id)

    # a hit pauses for the follow-up step, after everything else has resolved
    m = arena([("gunner", (3, 3))], [("dummy", (7, 3))])
    g, d = m.living(LEFT)[0], m.living(RIGHT)[0]
    d.set_cell((4, 3))
    m.select_hero(LEFT, g.id)
    m.commit(LEFT, {"destination": None, "action": {"key": "attack", "direction": "forward"}})
    m.select_hero(RIGHT, d.id)
    m.commit(RIGHT, HOLD)
    assert m.phase == "resolved", m.phase
    out["followup"] = view.state_for(m, LEFT)

    m = arena([("magician", (3, 2)), ("gatekeeper", (3, 3))], [("spearman", (7, 3))])
    mg = unit(m, LEFT, "magician")
    snap("two_units", m, select=mg.id, full_ap=[mg])

    m = arena([("shopkeeper", (3, 1)), ("tide_goddess", (3, 2))], [("dummy", (7, 3))])
    snap("free_pick", m, select=unit(m, LEFT, "shopkeeper").id)

    # --- a gang turn: three bodies, one order ---------------------------------
    m = arena([("goblin_javelin", (2, 1)), ("goblin_javelin", (2, 2)),
               ("goblin_commander", (2, 3))], [("dummy", (8, 3))])
    snap("gang", m, select=m.living(LEFT)[0].id,
         full_ap=[unit(m, LEFT, "goblin_commander")])

    # --- two halves of one body, positioned together before either aims -------
    m = arena([("snake_head", (2, 2)), ("snake_tail", (2, 3))], [("dummy", (8, 3))])
    snap("linked", m, select=unit(m, LEFT, "snake_head").id)

    # --- a hero with no square of its own, ready to take one ------------------
    m = arena([("ghost", (3, 3)), ("gatekeeper", (3, 1))], [("cannoneer", (7, 3)), ("dummy", (7, 1))])
    g = unit(m, LEFT, "ghost")
    g.vars["turns_done"] = 3
    g.vars["haunting"] = unit(m, RIGHT, "cannoneer").id
    snap("ghost_ready", m, select=g.id)

    # --- a hero pinned in place, but still able to fight ---------------------
    m = arena([("sabretooth", (3, 3)), ("cannoneer", (3, 1))],
              [("gatekeeper", (7, 3)), ("dummy", (7, 1))])
    tig, gk, dm = unit(m, LEFT, "sabretooth"), unit(m, RIGHT, "gatekeeper"), unit(m, RIGHT, "dummy")
    gk.set_cell((4, 3))
    m.select_hero(LEFT, tig.id)
    m.commit(LEFT, {"destination": None, "action": {"key": "attack", "shots": [[[4, 3]]]}})
    m.select_hero(RIGHT, dm.id)          # the pinned one keeps its turn for the next exchange
    m.commit(RIGHT, HOLD)
    assert m.rooted(gk), "the fixture needs a pinned hero"
    snap("rooted", m, side=RIGHT, select=gk.id)

    # --- a hero held out of the turn by an effect -----------------------------
    m = arena([("gatekeeper", (3, 1)), ("cannoneer", (3, 3))], [("dummy", (7, 3))])
    m.freeze(unit(m, LEFT, "gatekeeper"))
    m.start_round()
    snap("frozen", m)

    # --- the other phases -----------------------------------------------------
    out["draft"] = view.state_for(Match(), LEFT)
    m = Match(); m.assign_draft(["goblin_gang", "sniper"], ["dummy", "dummy"])
    out["setup"] = view.state_for(m, LEFT)
    m = Match(); m.assign_draft(["forest_child"], ["dummy"])
    m.place(LEFT, "forest_child", (3, 3)); m.place(RIGHT, "dummy", (7, 3))
    m.lock_force(LEFT); m.lock_force(RIGHT)
    out["opening"] = view.state_for(m, LEFT)

    m = arena([("gatekeeper", (3, 1))], [("dummy", (7, 3))])
    m.select_hero(LEFT, m.living(LEFT)[0].id)
    m.commit(LEFT, HOLD)
    out["sealed"] = view.state_for(m, LEFT)

    m = arena([("cannoneer", (3, 3))], [("dummy", (7, 3)), ("gatekeeper", (7, 2))])
    unit(m, RIGHT, "dummy").set_cell((4, 3))
    unit(m, RIGHT, "gatekeeper").set_cell((4, 2))
    c = m.living(LEFT)[0]
    m.select_hero(LEFT, c.id)
    m.commit(LEFT, {"destination": None, "action": {"key": "attack", "shots": [[[4, 3], [4, 2]]]}})
    m.select_hero(RIGHT, unit(m, RIGHT, "dummy").id)
    m.commit(RIGHT, HOLD)
    out["victim"] = view.state_for(m, LEFT)

    m = arena([("gatekeeper", (3, 3))], [("dummy", (7, 3))])
    m.living(RIGHT)[0].hp = 0
    m.sweep_deaths(); m.check_victory()
    out["gameover"] = view.state_for(m, LEFT)
    return out


if __name__ == "__main__":
    states = build()
    with open("fixtures.json", "w") as fh:
        json.dump(states, fh)
    print(f"wrote fixtures.json — {len(states)} states: {', '.join(states)}")
