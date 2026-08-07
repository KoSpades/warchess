"""Real engine states, written out for the browser client's tests to run against.

    python3 make_fixtures.py && node test_client.js

Everything here comes from an actual Match, so the client is always tested
against payloads the server would really send — not hand-written ones that drift.
"""

import json

import board
import heroes as HEROES
import view
from entities import Modifier
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
                # Both keys, so this works whichever kind the opening asks for —
                # an ally (森林之子, 诅咒娃娃) or a square (潜水者).
                m.opening_choose(side, {"target": m.living(side)[0].id, "cell": [1, 1]})
    return m


def unit(m, side, key):
    return next(e for e in m.living(side) if e.key == key)


def stage(m, *pairs):
    """Stand bodies on squares no deployment zone could reach, then make that the
    board both seats can see.

    A seat renders the other side out of the exchange snapshot, which is taken
    when the exchange opens — so a board arranged by hand afterwards is arranged
    for one seat only, and every fixture below is snapped from Left. Without the
    re-snapshot the client is handed enemies still standing on their deployment
    squares while the server's own target lists were worked out from where they
    really are."""
    for e, c in pairs:
        e.set_cell(tuple(c))
    m.take_snapshot()


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
    stage(m, (unit(m, RIGHT, "dummy"), (5, 3)), (unit(m, RIGHT, "cannoneer"), (3, 5)))
    snap("shape_cut", m, select=m.living(LEFT)[0].id, full_ap=[m.living(LEFT)[0]])

    # three shapes rather than two, so the overlapping-square rule gets exercised
    m = arena([("bomber", (3, 3))], [("dummy", (7, 3)), ("cannoneer", (7, 2))])
    stage(m, (unit(m, RIGHT, "dummy"), (4, 4)), (unit(m, RIGHT, "cannoneer"), (6, 3)))
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
    stage(m, (unit(m, RIGHT, "dummy"), (5, 3)))
    snap("centaur_charge", m, select=unit(m, LEFT, "centaur").id,
         full_ap=[unit(m, LEFT, "centaur")])

    # a hero with nobody in reach of the square it stands on, but somebody in
    # reach of a square it can walk to: the menu has to judge an ability from
    # where the hero will be, since movement resolves before the ability does
    m = arena([("strongman", (2, 3)), ("gatekeeper", (1, 1))],
              [("dummy", (7, 3)), ("cannoneer", (8, 3))])
    stage(m, (unit(m, RIGHT, "dummy"), (4, 3)))
    snap("reach_after_walk", m, select=unit(m, LEFT, "strongman").id,
         full_ap=[unit(m, LEFT, "strongman")])

    m = arena([("mammoth", (3, 3))], [("dummy", (7, 3)), ("cannoneer", (7, 2))])
    stage(m, (unit(m, RIGHT, "dummy"), (4, 3)), (unit(m, RIGHT, "cannoneer"), (4, 2)))
    snap("mammoth", m, select=m.living(LEFT)[0].id)

    m = arena([("gunner", (3, 3))], [("dummy", (7, 3)), ("cannoneer", (7, 2))])
    stage(m, (unit(m, RIGHT, "dummy"), (4, 3)), (unit(m, RIGHT, "cannoneer"), (4, 2)))
    snap("cone", m, select=m.living(LEFT)[0].id)

    # a hit pauses for the follow-up step, after everything else has resolved
    m = arena([("gunner", (3, 3))], [("dummy", (7, 3))])
    g, d = m.living(LEFT)[0], m.living(RIGHT)[0]
    stage(m, (d, (4, 3)))
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

    # --- a lane that moves somebody other than the thrower (渔夫's hook) -------
    m = arena([("fisherman", (3, 3)), ("gatekeeper", (3, 1))],
              [("cannoneer", (7, 3)), ("dummy", (7, 1))])
    fm = unit(m, LEFT, "fisherman")
    snap("hook", m, select=fm.id, full_ap=[fm])

    # --- 世界树's beasts: a round-start prompt that names a hero --------------
    # The tree is a clock now, not a thing to strike: the beasts come at round 3,
    # so this plays holds until they do.
    m = Match(); m.assign_draft(["world_tree", "cannoneer"], ["dummy", "gatekeeper"])
    m.place(LEFT, "cannoneer", (3, 3))
    m.place(RIGHT, "dummy", (7, 3)); m.place(RIGHT, "gatekeeper", (7, 1))
    m.lock_force(LEFT); m.lock_force(RIGHT)
    guard = 0
    while m.round < 3 and guard < 60:
        guard += 1
        if m.phase == "interrupt":
            t = m.interrupts[0]
            m.choose_interrupt(t["side"], t["options"][0] if t.get("options") else None)
        elif m.phase == "commit":
            moved = False
            for side in (LEFT, RIGHT):
                if m.commits[side] is None and m.unacted(side):
                    m.select_hero(side, m.unacted(side)[0].id)
                    m.commit(side, HOLD)
                    moved = True
            if not moved:
                break
        else:
            break
    assert m.phase == "interrupt", m.phase
    assert m.interrupts[0].get("beast"), m.interrupts[0]
    out["beasts"] = view.state_for(m, LEFT)

    # --- the enemy seat looking at the tree: it may not aim anything at it ----
    m = Match(); m.assign_draft(["world_tree", "cannoneer"], ["thunder_dragon", "dummy"])
    m.place(LEFT, "cannoneer", (3, 3))
    m.place(RIGHT, "thunder_dragon", (7, 3)); m.place(RIGHT, "dummy", (7, 1))
    m.lock_force(LEFT); m.lock_force(RIGHT)
    td = unit(m, RIGHT, "thunder_dragon")
    m.select_hero(RIGHT, td.id)
    out["tree_foe"] = view.state_for(m, RIGHT)

    # --- the tree's own side, with an order that commits to a hero -----------
    m = Match(); m.assign_draft(["world_tree", "thunder_dragon"], ["dummy", "gatekeeper"])
    m.place(LEFT, "thunder_dragon", (3, 3))
    m.place(RIGHT, "dummy", (7, 3)); m.place(RIGHT, "gatekeeper", (7, 1))
    m.lock_force(LEFT); m.lock_force(RIGHT)
    td = unit(m, LEFT, "thunder_dragon")
    m.select_hero(LEFT, td.id)
    out["tree_ally"] = view.state_for(m, LEFT)

    # --- 探险家's island part-dug: one of each, and one square still bare -------
    m = Match()
    m.assign_draft(["explorer", "cannoneer", "gatekeeper"],
                   ["cannoneer", "dummy", "berserker"])
    m.build_choose(LEFT, {"cells": [[4, 1], [4, 2], [4, 3], [5, 2]]})
    m.build_choose(LEFT, {"cell": [4, 2]})
    for k, c in (("cannoneer", (3, 3)), ("gatekeeper", (3, 1))):
        m.place(LEFT, k, c)
    for k, c in (("cannoneer", (7, 2)), ("dummy", (7, 3)), ("berserker", (7, 4))):
        m.place(RIGHT, k, c)
    m.lock_force(LEFT); m.lock_force(RIGHT)
    ex = next(e for e in m.entities if e.key == "explorer")
    for res, cell in (("dig_grapes", (4, 1)), ("mine_ore", (4, 3))):
        todo, guard = res, 0
        r0 = m.round
        while m.round == r0 and m.phase == "commit" and guard < 20:
            guard += 1
            for side in (LEFT, RIGHT):
                if m.commits[side] is not None:
                    continue
                un = m.unacted(side)
                if not un:
                    continue
                pick = next((e for e in un if e is ex), un[0])
                m.select_hero(side, pick.id)
                if pick is ex and todo:
                    assert m.commit(side, {"destination": None,
                                           "action": {"key": "ability:" + todo,
                                                      "cell": list(cell)}}) is None, todo
                    todo = None
                else:
                    m.commit(side, HOLD)
    out["island_worked"] = view.state_for(m, LEFT)
    out["island_worked_foe"] = view.state_for(m, RIGHT)

    # --- 四圣兽's shrines, some woken and some not ------------------------------
    m = arena([("four_beasts", (2, 3)), ("gatekeeper", (1, 1))],
              [("dummy", (7, 3)), ("berserker", (9, 5))])
    fb = unit(m, LEFT, "four_beasts")
    snap("shrines", m, select=fb.id, full_ap=[fb])

    # --- a vine on the ground, and an enemy side nothing may be named on --------
    m = Match()
    m.assign_draft(["assassin", "elder", "magician"], ["world_tree", "explorer", "gatekeeper"])
    m.build_choose(RIGHT, {"cells": [[8, 1], [8, 2], [8, 3], [7, 2]]})
    m.build_choose(RIGHT, {"cell": [8, 2]})
    for k, c in (("assassin", (3, 3)), ("elder", (3, 4)), ("magician", (3, 5))):
        assert m.place(LEFT, k, c) is None, (k, c)
    assert m.place(RIGHT, "gatekeeper", (9, 3)) is None
    assert m.lock_force(LEFT) is None
    assert m.lock_force(RIGHT) is None
    # Everything the left seat could name is gone: what is left of the right side
    # is a 世界树 and an 探险家 still out on its island, neither of them touchable.
    gk = unit(m, RIGHT, "gatekeeper")
    gk.alive = False
    gk.cells = set()
    m.snapshot = {}          # the loss is public by the time this is drawn
    m.board.add_effect((4, 3), board.GrapeVine(LEFT))
    spent = board.GrapeVine(LEFT); spent.spent = True
    m.board.add_effect((4, 4), spent)
    great = board.GrapeVine(LEFT); great.great = True
    m.board.add_effect((4, 5), great)
    # 世界树 is a clock now, and its 长冬 pins every enemy through round 1 — which
    # correctly hides any ability that carries its own hero (封喉) from the menu.
    # This fixture is about an enemy side nothing may be *named* on, so thaw it.
    for e in m.living(LEFT):
        e.vars["rooted_at"] = None
    asn = unit(m, LEFT, "assassin")
    asn.ap = asn.max_ap
    m.select_hero(LEFT, asn.id)
    out["untouchable_foes"] = view.state_for(m, LEFT)

    # --- an opening pick with a restricted square list (鸟嘴医生's plague) -------
    m = Match()
    m.assign_draft(["plague_doctor", "cannoneer", "gatekeeper"],
                   ["dummy", "dummy", "berserker"])
    for k, c in (("plague_doctor", (3, 3)), ("cannoneer", (3, 1)), ("gatekeeper", (3, 2))):
        m.place(LEFT, k, c)
    for k, c in (("dummy", (7, 2)), ("dummy", (7, 3)), ("berserker", (7, 4))):
        m.place(RIGHT, k, c)
    m.lock_force(LEFT); m.lock_force(RIGHT)
    out["opening_cells"] = view.state_for(m, LEFT)

    # --- 探险家: a build task that wants four squares, then one of those four ---
    def island_match():
        m = Match()
        m.assign_draft(["explorer", "cannoneer", "gatekeeper"],
                       ["cannoneer", "dummy", "berserker"])
        return m

    m = island_match()
    out["island_chart"] = view.state_for(m, LEFT)              # 4 squares, anywhere
    m.build_choose(LEFT, {"cells": [[4, 1], [4, 2], [4, 3], [5, 2]]})
    out["island_landfall"] = view.state_for(m, LEFT)           # 1 square, of those 4

    m.build_choose(LEFT, {"cell": [4, 2]})
    for k, c in (("cannoneer", (3, 3)), ("gatekeeper", (3, 1))):
        m.place(LEFT, k, c)
    for k, c in (("cannoneer", (7, 2)), ("dummy", (7, 3)), ("berserker", (7, 4))):
        m.place(RIGHT, k, c)
    m.lock_force(LEFT); m.lock_force(RIGHT)
    ex = next(e for e in m.entities if e.key == "explorer")
    m.select_hero(LEFT, ex.id)
    out["island_dig"] = view.state_for(m, LEFT)                # digging, and nothing else
    out["island_foe"] = view.state_for(m, RIGHT)               # what the other seat sees

    # --- a shot that wants two victims out of three (猎人 once blooded) --------
    m = arena([("hunter", (2, 3)), ("cannoneer", (1, 1))],
              [("dummy", (7, 2)), ("dummy", (7, 3)), ("dummy", (7, 4))])
    hn = unit(m, LEFT, "hunter")
    hn.vars["first_blood"] = True
    hn.add_modifier(Modifier("targets", "add", 1))
    hn.add_modifier(Modifier("rng", "add", 4))
    foes = m.living(RIGHT)
    m.select_hero(LEFT, hn.id)
    m.commit(LEFT, {"destination": None,
                    "action": {"key": "attack",
                               "shots": [[list(f.cell) for f in foes]]}})
    m.select_hero(RIGHT, foes[0].id); m.commit(RIGHT, HOLD)
    assert m.phase == "victim", m.phase
    out["victim_two"] = view.state_for(m, LEFT)

    # --- a shot that can be fed AP (军火商人) ---------------------------------
    m = arena([("arms_dealer", (3, 3))], [("dummy", (7, 3))])
    ad = unit(m, LEFT, "arms_dealer"); ad.ap = 6
    snap("fuelled", m, select=ad.id)

    # --- the same, with nothing banked and a sale to make ---------------------
    # The fee is collected as the turn opens, before the shot, so it is fuel for
    # that shot — and a dealer down to nothing still has a full slider.
    m = arena([("arms_dealer", (3, 3)), ("gatekeeper", (3, 1))], [("dummy", (7, 3))])
    ad = unit(m, LEFT, "arms_dealer"); ad.ap = 0
    unit(m, LEFT, "gatekeeper").ap = max(w["ap"] for w in HEROES.ARMS)
    snap("fuelled_sale", m, select=ad.id)

    # --- a cells attack with nothing whatever in reach -------------------------
    # Marking nothing really is a hold here: there was never a shot to forget.
    m = arena([("gatekeeper", (1, 1))], [("gatekeeper", (9, 5))])
    snap("no_targets", m, select=unit(m, LEFT, "gatekeeper").id)

    # --- a unit attack that names two heroes rather than one ------------------
    m = arena([("four_beasts", (3, 3))], [("dummy", (7, 3)), ("gatekeeper", (7, 1))])
    fb = unit(m, LEFT, "four_beasts")
    fb.vars["beasts"] = {"tiger"}
    fb.add_modifier(Modifier("targets", "add", 1))
    snap("two_named", m, select=fb.id)

    # --- a killing blow held up while somebody decides -------------------------
    m = Match(); m.assign_draft(["pope", "cannoneer"], ["cannoneer", "dummy"])
    m.place(LEFT, "pope", (2, 2)); m.place(LEFT, "cannoneer", (2, 3))
    m.place(RIGHT, "cannoneer", (8, 2)); m.place(RIGHT, "dummy", (8, 3))
    m.lock_force(LEFT); m.lock_force(RIGHT)
    doomed = unit(m, LEFT, "cannoneer"); doomed.hp = 2
    stage(m, (doomed, (7, 2)))
    m.select_hero(LEFT, unit(m, LEFT, "pope").id); m.commit(LEFT, HOLD)
    m.select_hero(RIGHT, unit(m, RIGHT, "cannoneer").id)
    m.commit(RIGHT, {"destination": None, "action": {"key": "attack", "shots": [[[7, 2]]]}})
    assert m.phase == "interrupt", m.phase
    out["interrupt_save"] = view.state_for(m, LEFT)
    out["interrupt_waiting"] = view.state_for(m, RIGHT)

    # --- infected ground: plain to both seats, unlike a buried charge ---------
    # Built directly: `arena` answers openings for you, and this one needs to land
    # on a square of its own choosing.
    m = Match(); m.assign_draft(["plague_doctor", "gatekeeper"], ["dummy", "dummy"])
    m.place(LEFT, "plague_doctor", (2, 2)); m.place(LEFT, "gatekeeper", (2, 3))
    m.place(RIGHT, "dummy", (8, 3)); m.place(RIGHT, "dummy", (8, 1))
    m.lock_force(LEFT); m.lock_force(RIGHT)
    assert m.opening_choose(LEFT, {"cell": [5, 3]}) is None
    m.board.add_effect((5, 2), board.Infection(LEFT))
    out["infected_owner"] = view.state_for(m, LEFT)
    out["infected_enemy"] = view.state_for(m, RIGHT)

    # --- buried charges: the same board seen from both sides ------------------
    m = arena([("diver", (3, 3))], [("gatekeeper", (7, 3))])
    dv = unit(m, LEFT, "diver")
    m.board.add_effect((5, 3), board.SmallBomb(LEFT))
    m.select_hero(LEFT, dv.id)
    out["mined_owner"] = view.state_for(m, LEFT)
    out["mined_enemy"] = view.state_for(m, RIGHT)

    # --- two halves of one body, positioned together before either aims -------
    m = arena([("snake_head", (2, 2)), ("snake_tail", (2, 3))], [("dummy", (8, 3))])
    snap("linked", m, select=unit(m, LEFT, "snake_head").id)

    # --- the same, with the enemy's door standing where the tail could go -----
    # The client works the tail's squares out for itself (it follows the head,
    # wherever the head is going), so it has to know the two board facts the
    # server checks: another side's door is wall, and an island is off the board.
    m = Match()
    m.assign_draft(["snake_emperor", "cannoneer"], ["artisan", "dummy"])
    m.build_choose(RIGHT, {"cells": [[3, 3], [8, 3]]})   # (3,3) is wall to Left
    for k, c in (("snake_head", (2, 3)), ("snake_tail", (2, 4)), ("cannoneer", (1, 1))):
        m.place(LEFT, k, c)
    for k, c in (("artisan", (8, 3)), ("dummy", (8, 1))):
        m.place(RIGHT, k, c)
    m.lock_force(LEFT); m.lock_force(RIGHT)
    snap("linked_doors", m, select=unit(m, LEFT, "snake_head").id)

    # --- a head walled in, so its tail has nowhere beside it to follow to -----
    # 海妖's song drags a whole line together and can leave 蛇帝's head with every
    # neighbour taken. The tail must still be offered the square it is on, or the
    # seat could never finish the leg.
    m = Match()
    m.assign_draft(["snake_emperor", "cannoneer"],
                   ["gatekeeper", "dummy", "spearman"])
    for k, c in (("snake_head", (2, 3)), ("snake_tail", (2, 4)), ("cannoneer", (1, 1))):
        assert m.place(LEFT, k, c) is None, (k, c)
    for k, c in (("gatekeeper", (7, 1)), ("dummy", (8, 1)), ("spearman", (9, 1))):
        assert m.place(RIGHT, k, c) is None, (k, c)
    m.lock_force(LEFT); m.lock_force(RIGHT)
    # Wall the head into the corner and shake the tail loose of it.
    stage(m, (unit(m, LEFT, "snake_head"), (1, 3)),
          (unit(m, RIGHT, "gatekeeper"), (2, 3)),
          (unit(m, RIGHT, "dummy"), (1, 2)),
          (unit(m, RIGHT, "spearman"), (1, 4)),
          (unit(m, LEFT, "snake_tail"), (2, 5)))
    snap("linked_boxed", m, select=unit(m, LEFT, "snake_head").id)

    # --- a round-start prompt raised by a hero, answered by its own side -------
    # 万磁王 offers a pull before either seat picks a turn: confirm, then who,
    # then where. Nothing else in the fixtures exercises a hero-owned confirm.
    m = Match()
    m.assign_draft(["magneto", "cannoneer"], ["gatekeeper", "dummy"])
    for k, c in (("magneto", (3, 3)), ("cannoneer", (3, 1))):
        assert m.place(LEFT, k, c) is None, (k, c)
    for k, c in (("gatekeeper", (7, 3)), ("dummy", (7, 1))):
        assert m.place(RIGHT, k, c) is None, (k, c)
    m.lock_force(LEFT); m.lock_force(RIGHT)
    assert m.phase == "interrupt", m.phase
    out["magnet_confirm"] = view.state_for(m, LEFT)
    out["magnet_waiting"] = view.state_for(m, RIGHT)
    m.choose_interrupt(LEFT, True)
    out["magnet_who"] = view.state_for(m, LEFT)
    m.choose_interrupt(LEFT, unit(m, RIGHT, "gatekeeper").id)
    out["magnet_where"] = view.state_for(m, LEFT)

    # --- an opening ally pick with a narrower list than "any ally" ------------
    # 血盟卫 may not swear to itself, and the client must not offer it.
    m = Match()
    m.assign_draft(["blood_guard", "cannoneer"], ["dummy", "dummy"])
    for k, c in (("blood_guard", (3, 3)), ("cannoneer", (3, 1))):
        assert m.place(LEFT, k, c) is None, (k, c)
    for k, c in (("dummy", (7, 3)), ("dummy", (7, 1))):
        assert m.place(RIGHT, k, c) is None, (k, c)
    m.lock_force(LEFT); m.lock_force(RIGHT)
    out["opening_ally_some"] = view.state_for(m, LEFT)

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
    stage(m, (gk, (4, 3)))
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

    # --- the board being built, before anyone is placed ------------------------
    m = Match(); m.assign_draft(["artisan", "cannoneer"], ["gatekeeper", "dummy"])
    out["build"] = view.state_for(m, LEFT)
    out["build_waiting"] = view.state_for(m, RIGHT)
    m.build_choose(LEFT, {"cells": [[2, 3], [8, 3]]})
    for k, c in (("artisan", (2, 3)), ("cannoneer", (1, 1))):
        m.place(LEFT, k, c)
    for k, c in (("gatekeeper", (8, 1)), ("dummy", (8, 2))):
        m.place(RIGHT, k, c)
    m.lock_force(LEFT); m.lock_force(RIGHT)
    m.select_hero(LEFT, unit(m, LEFT, "artisan").id)
    out["doors"] = view.state_for(m, LEFT)
    # and the same pair with its last passage walked, so the board can say so
    m.topology.links[0].passages = 0
    out["doors_spent"] = view.state_for(m, LEFT)
    m.topology.links[0].passages = HEROES.RaiseDoors.PASSAGES

    # --- the other phases -----------------------------------------------------
    out["draft"] = view.state_for(Match(), LEFT)
    m = Match(); m.assign_draft(["goblin_gang", "sniper"], ["dummy", "dummy"])
    out["setup"] = view.state_for(m, LEFT)
    m = Match(); m.assign_draft(["forest_child"], ["dummy"])
    m.place(LEFT, "forest_child", (3, 3)); m.place(RIGHT, "dummy", (7, 3))
    m.lock_force(LEFT); m.lock_force(RIGHT)
    out["opening"] = view.state_for(m, LEFT)

    # An opening that names an enemy (占星师's prophecy).
    m = Match(); m.assign_draft(["astrologer"], ["dummy"])
    m.place(LEFT, "astrologer", (3, 3)); m.place(RIGHT, "dummy", (7, 3))
    m.lock_force(LEFT); m.lock_force(RIGHT)
    out["opening_unit"] = view.state_for(m, LEFT)

    # An opening that wants a square rather than an ally (潜水者's buried charge).
    m = Match(); m.assign_draft(["diver"], ["dummy"])
    m.place(LEFT, "diver", (3, 3)); m.place(RIGHT, "dummy", (7, 3))
    m.lock_force(LEFT); m.lock_force(RIGHT)
    out["opening_cell"] = view.state_for(m, LEFT)

    m = arena([("gatekeeper", (3, 1))], [("dummy", (7, 3))])
    m.select_hero(LEFT, m.living(LEFT)[0].id)
    m.commit(LEFT, HOLD)
    out["sealed"] = view.state_for(m, LEFT)

    m = arena([("cannoneer", (3, 3))], [("dummy", (7, 3)), ("gatekeeper", (7, 2))])
    stage(m, (unit(m, RIGHT, "dummy"), (4, 3)), (unit(m, RIGHT, "gatekeeper"), (4, 2)))
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
