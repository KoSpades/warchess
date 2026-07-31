"""Headless checks on the rules most likely to be implemented wrong.

Robustness rule (hero numbers get retuned constantly): NEVER assert a hardcoded
absolute stat. Assert on the *effect* — damage dealt (`before - after`), whether
a hit landed at all, or a change relative to a captured baseline. Attacks mark
just the target's own cell (1 cell) and fire from close range, so cell-count and
range tweaks can't break these either.
"""

import damage as DMG
import view
from heroes import Sweep
from match import Match
from topology import LEFT, RIGHT

L = [("spearman", (3, 1)), ("paladin", (3, 2)), ("robot", (3, 3)), ("gunslinger", (3, 4))]
R = [("fire_mage", (7, 1)), ("thunder_dragon", (7, 2)), ("robot", (7, 3)), ("gunslinger", (7, 4))]


def arena(left, right):
    """Deploy exactly the listed heroes per side (force size = list length)."""
    m = Match()
    m.assign_draft([k for k, _ in left], [k for k, _ in right])
    for k, c in left:
        assert m.place(LEFT, k, c) is None, (k, c)
    for k, c in right:
        assert m.place(RIGHT, k, c) is None, (k, c)
    assert m.lock_force(LEFT) is None
    assert m.lock_force(RIGHT) is None
    return m


def build(left=L, right=R):
    """The standard 4v4 used by the core-rules checks."""
    return arena(left, right)


def turn(m, ls, la, rs, ra):
    """Commit one exchange and answer any victim prompts."""
    if ls is not None:
        assert m.select_hero(LEFT, ls) is None
        assert m.commit(LEFT, la) is None
    if rs is not None:
        assert m.select_hero(RIGHT, rs) is None
        assert m.commit(RIGHT, ra) is None
    guard = 0
    while m.phase == "victim":
        guard += 1
        assert guard < 20, "victim loop stuck"
        for side in (LEFT, RIGHT):
            opts = m.res["options"][side]
            if opts and m.res["picks"][side] is None:
                m.choose_victim(side, opts[0])


def unit(m, side, key):
    return next(e for e in m.living(side) if e.key == key)


def ok(label, cond, detail=""):
    print(f"{'PASS' if cond else 'FAIL'}  {label}" + (f"   [{detail}]" if detail else ""))


# 1 — both step to different cells, both move
m = build()
turn(m, 1, {"destination": [4, 1], "action": {"key": "none"}},
     5, {"destination": [6, 1], "action": {"key": "none"}})
ok("independent moves both apply", m.entity(1).cell == (4, 1) and m.entity(5).cell == (6, 1))

# 2 — both step to the same empty cell, both bounce
m = build()
m.entity(5).set_cell((5, 1))
turn(m, 1, {"destination": [4, 1], "action": {"key": "none"}},
     5, {"destination": [4, 1], "action": {"key": "none"}})
ok("same destination bounces both", m.entity(1).cell == (3, 1) and m.entity(5).cell == (5, 1))

# 3 — swap is impossible: destination must be empty in the snapshot
m = build()
m.entity(5).set_cell((4, 1))
m.select_hero(LEFT, 1)
err = m.commit(LEFT, {"destination": [4, 1], "action": {"key": "none"}})
ok("swap rejected at commit", err is not None, err)

# 4 — bounced attacker re-derives its cells from where it actually stands
m = build()
m.entity(5).set_cell((5, 1))
giant = m.entity(2)
giant.set_cell((4, 3))                       # spearman marks this cell...
turn(m, 1, {"destination": [4, 1],           # ...intending to stand at (4,1), but bounces
            "action": {"key": "attack", "shots": [[[4, 3]]]}},
     5, {"destination": [4, 1], "action": {"key": "none"}})
ok("bounce shifts the pattern with the hero", giant.hp == giant.max_hp,
   f"giant took {giant.max_hp - giant.hp}, spearman at {m.entity(1).cell}")

# 5 — paladin: the sequential second shot is turned aside by the holy shield
m = build()
giant = m.entity(2)                          # paladin at (3,2)
gun = m.entity(8)
gun.set_cell((4, 2))                          # adjacent, well within any range
before = giant.hp
shots = [[[3, 2]], [[3, 2]]]                  # both shots aimed at the paladin
turn(m, 3, {"destination": None, "action": {"key": "none"}},
     8, {"destination": None, "action": {"key": "attack", "shots": shots}})
ok("gunslinger's 2nd shot blocked by holy shield", before - giant.hp == gun.atk,
   f"took {before - giant.hp}, one shot = {gun.atk}")

# 6 — mutual kill: both land in the same instant and both die
m = build()
a, b = m.entity(4), m.entity(8)
a.set_cell((4, 2)); b.set_cell((5, 2))
a.hp = b.hp = 1                               # any positive atk is lethal
turn(m, 4, {"destination": None, "action": {"key": "attack", "shots": [[[5, 2]], [[5, 2]]]}},
     8, {"destination": None, "action": {"key": "attack", "shots": [[[4, 2]], [[4, 2]]]}})
ok("mutual kill removes both", not a.alive and not b.alive)

# 7 — sweep geometry is a 2x5 block
m = build()
sp = m.entity(1)
sp.set_cell((5, 1))
cells = Sweep.block(m, sp, "forward")
ok("sweep is 10 cells across two columns",
   len(cells) == 10 and {c[0] for c in cells} == {5, 6})

# 8 — burning tiles burn enemies only, stack, and fire before the hero acts
m = build()
one = m.board.add_burning((3, 3), RIGHT).damage      # damage from a single stack
m.board.add_burning((3, 3), RIGHT)                   # now two stacks
m.board.add_burning((7, 3), RIGHT)
m.select_hero(LEFT, 3)                     # fire resolves at commit, not select
m.commit(LEFT, {"destination": None, "action": {"key": "none"}})
took = m.entity(3).max_hp - m.entity(3).hp
ok("stacked enemy tile deals two stacks at turn start", took == 2 * one, f"took {took}, stack {one}")
m.select_hero(RIGHT, 7)
m.commit(RIGHT, {"destination": None, "action": {"key": "none"}})
ok("a tile never burns its owner's own side", m.entity(7).hp == m.entity(7).max_hp)

# 9 — AP: none on round one, granted at end of turn
m = build()
sp = m.entity(1)
first = sp.ap
turn(m, 1, {"destination": None, "action": {"key": "none"}},
     5, {"destination": None, "action": {"key": "none"}})
ok("no AP on the opening turn, 1 AP after it", first == 0 and sp.ap == 1)

# 10 — thunderstorm hits every enemy regardless of position, all for the same amount
m = build()
m.entity(6).ap = 3
turn(m, 1, {"destination": None, "action": {"key": "none"}},
     6, {"destination": None, "action": {"key": "ability:thunderstorm"}})
took = [m.entity(i).max_hp - m.entity(i).hp for i in (1, 2, 3, 4)]
ok("thunderstorm hits all four enemies equally", all(t == took[0] and t > 0 for t in took), str(took))

# 11 — unit-locked attack ignores movement entirely
m = build()
tgt, dragon = m.entity(1), m.entity(6)
before = tgt.hp
turn(m, 1, {"destination": [4, 1], "action": {"key": "none"}},
     6, {"destination": None, "action": {"key": "attack", "target": 1}})
ok("unbounded one_chosen lands after the target moves", before - tgt.hp == dragon.atk,
   f"took {before - tgt.hp}, dragon atk {dragon.atk}")

# 12 — a hero killed by fire at turn start (committing it) loses its action
m = build()
rob = m.entity(3)
rob.hp = 1
m.board.add_burning((3, 3), RIGHT)
m.select_hero(LEFT, 3)               # tentative: no fire yet
assert rob.alive, "fire should not fire on select"
m.commit(LEFT, {"destination": None, "action": {"key": "none"}})   # now the turn starts
ok("fire kills when the hero is committed", not rob.alive and m.commits[LEFT]["kind"] == "dead")

# 13 — a plain cell attack lands for the attacker's atk (dummy target: no passives)
m = arena([("cannoneer", (3, 3))], [("dummy", (7, 3))])
cannon, tgt = unit(m, LEFT, "cannoneer"), unit(m, RIGHT, "dummy")
tgt.set_cell((5, 3))                          # bring it close so range tweaks can't break this
before = tgt.hp
turn(m, cannon.id, {"destination": None, "action": {"key": "attack", "shots": [[[5, 3]]]}},
     tgt.id, {"destination": None, "action": {"key": "none"}})
ok("cell attack lands for the attacker's atk", before - tgt.hp == cannon.atk,
   f"took {before - tgt.hp}, atk {cannon.atk}")

# 14 — 马尔斯: rng rises once an enemy falls, atk once only one remains (spec 7.3)
m = arena([("mars", (3, 3)), ("dummy", (3, 1)), ("gatekeeper", (3, 2))],
          [("dummy", (7, 1)), ("gatekeeper", (7, 2)), ("cannoneer", (7, 3))])
mars = unit(m, LEFT, "mars")
base_atk, base_rng = mars.atk, mars.rng
ok("马尔斯 opens with no bonus at all — enemies still at full strength",
   mars.atk == mars.hero.atk and mars.rng == mars.hero.attack["range"],
   f"{mars.atk}/{mars.rng} vs sheet {mars.hero.atk}/{mars.hero.attack['range']}")
m.living(RIGHT)[0].hp = 0
m.sweep_deaths()
ok("马尔斯 gains rng after the first enemy falls",
   mars.rng == base_rng + 1 and mars.atk == base_atk)
m.living(RIGHT)[0].hp = 0
m.sweep_deaths()
ok("马尔斯 gains atk when one enemy remains",
   mars.rng == base_rng + 1 and mars.atk == base_atk + 1)

# 15 — 山神: allies in his column take 2 less; he isn't covered himself
m = arena([("mountain_god", (2, 3)), ("dummy", (2, 1))],
          [("dummy", (7, 1)), ("gatekeeper", (7, 2))])
sg, ally, src = unit(m, LEFT, "mountain_god"), unit(m, LEFT, "dummy"), unit(m, RIGHT, "dummy")
hit = lambda who: DMG.deal(m, DMG.DamageEvent(source=src, target=who, amount=5, category=DMG.NORMAL_ATTACK))

h = ally.hp
hit(ally)
ok("山神 cuts damage to a column ally by 2", h - ally.hp == 3, f"took {h - ally.hp}")
ally.set_cell((5, 1))  # step out of 山神's column
h = ally.hp
hit(ally)
ok("山神 doesn't cover an out-of-column ally", h - ally.hp == 5, f"took {h - ally.hp}")
h = sg.hp
hit(sg)
ok("山神 does not cover himself", h - sg.hp == 5, f"took {h - sg.hp}")

# 16 — 狼人: 野兽化 transforms permanently, once, and the grid change is live
m = arena([("werewolf", (3, 3))], [("dummy", (7, 3))])
wolf = unit(m, LEFT, "werewolf")
wolf.hp = wolf.max_hp - 6                     # wounded, so the heal has room
base = (wolf.atk, wolf.move_allowance, wolf.grid, wolf.rng, wolf.hp)
wolf.ap = wolf.max_ap
turn(m, wolf.id, {"destination": None, "action": {"key": "ability:beast_form"}},
     unit(m, RIGHT, "dummy").id, {"destination": None, "action": {"key": "none"}})
now = (wolf.atk, wolf.move_allowance, wolf.grid, wolf.rng, wolf.hp)
ok("野兽化 applies +3 atk / +1 move / −1 grid / −2 rng / heal 4",
   [n - b for n, b in zip(now, base)] == [3, 1, -1, -2, 4],
   f"{base} -> {now}")

menu = {a["key"]: a for a in m.action_menu(wolf)}
ok("野兽化 is spent — no longer offered", "ability:beast_form" not in menu, str(list(menu)))
ok("the attack menu reports the beast's smaller net",
   menu["attack"]["targeting"]["count"] == wolf.grid
   and menu["attack"]["targeting"]["range"] == wolf.rng,
   str(menu["attack"]["targeting"]))

wolf.set_cell((6, 3))                          # adjacent to the dummy at (7,3)
tgt = unit(m, RIGHT, "dummy")
tgt.max_hp = tgt.hp = 99                       # survive the beast's swing
err = m.select_hero(LEFT, wolf.id) or m.commit(
    LEFT, {"destination": None,
           "action": {"key": "attack", "shots": [[[7, 3], [7, 2], [7, 4]]]}})
ok("marking more grids than the beast has is rejected", err is not None, str(err))

before = tgt.hp
turn(m, wolf.id, {"destination": None, "action": {"key": "attack", "shots": [[[7, 3]]]}},
     tgt.id, {"destination": None, "action": {"key": "none"}})
ok("the beast's attack lands for its boosted atk", before - tgt.hp == wolf.atk,
   f"took {before - tgt.hp}, atk {wolf.atk}")

# 17 — 蛮王: 背水 catches the lethal blow once, then rage runs out on turn three
m = arena([("barbarian_king", (3, 3))], [("dummy", (7, 3))])
king, foe = unit(m, LEFT, "barbarian_king"), unit(m, RIGHT, "dummy")
base_atk = king.atk
kill = lambda: DMG.apply_batch(m, [DMG.DamageEvent(
    source=foe, target=king, amount=99, category=DMG.NORMAL_ATTACK)])

kill()
ok("背水 turns a lethal hit into 1 HP and rage",
   king.alive and king.hp == 1 and king.atk == base_atk + 3,
   f"hp {king.hp}, atk {king.atk} (was {base_atk})")

kill()
ok("raging 蛮王 takes nothing from any source",
   king.alive and king.hp == 1, f"hp {king.hp}")
m.board.add_burning((3, 3), RIGHT)   # tile damage is a different category — also nothing
DMG.apply_batch(m, [DMG.DamageEvent(source=None, target=king, amount=99,
                                    category=DMG.TILE, element=DMG.FIRE)])
ok("rage blocks tile damage too", king.alive and king.hp == 1, f"hp {king.hp}")

# Two turns of action, then the third turn start kills him before he can act.
hold = {"destination": None, "action": {"key": "none"}}
turn(m, king.id, hold, foe.id, hold)
ok("first raging turn: still standing and able to act", king.alive)
turn(m, king.id, hold, foe.id, hold)
ok("second raging turn: still standing", king.alive)
m.select_hero(LEFT, king.id)
assert king.alive, "he should not burn out until the turn actually starts"
m.commit(LEFT, hold)
ok("third raging turn start burns him out", not king.alive and m.commits[LEFT]["kind"] == "dead",
   str(m.commits[LEFT]))

# 18 — 背水 fires only once: revive him by hand and the next lethal hit sticks
m = arena([("barbarian_king", (3, 3))], [("dummy", (7, 3))])
king, foe = unit(m, LEFT, "barbarian_king"), unit(m, RIGHT, "dummy")
DMG.apply_batch(m, [DMG.DamageEvent(source=foe, target=king, amount=99,
                                    category=DMG.NORMAL_ATTACK)])
king.vars["rage"] = False            # drop the immunity, keep 背水 spent
DMG.apply_batch(m, [DMG.DamageEvent(source=foe, target=king, amount=99,
                                    category=DMG.NORMAL_ATTACK)])
ok("背水 is once per match — the second lethal blow kills", not king.alive)

# 19 — the rage badge reaches both clients, but its countdown can't leak a pick
m = arena([("barbarian_king", (3, 3))], [("dummy", (7, 3))])
king, foe = unit(m, LEFT, "barbarian_king"), unit(m, RIGHT, "dummy")
king.hp = 1                                   # the dummy's next swing is lethal
foe.set_cell((4, 3))
badge = lambda side: next(
    (u["status"] for u in view.state_for(m, side)["units"] if u["id"] == king.id), None)

ok("no status badge before 背水 fires", badge(LEFT) == [])
turn(m, king.id, {"destination": None, "action": {"key": "none"}},
     foe.id, {"destination": None, "action": {"key": "attack", "target": king.id}})
ok("both sides see the rage badge once the exchange resolves",
   badge(LEFT) and badge(RIGHT) and badge(LEFT)[0]["key"] == "rage", str(badge(RIGHT)))

seen_before = badge(RIGHT)[0]["text"]
m.select_hero(LEFT, king.id)                  # his turn starts: the counter ticks
m.commit(LEFT, {"destination": None, "action": {"key": "none"}})
ok("the owner sees the countdown tick", badge(LEFT)[0]["text"] != seen_before, badge(LEFT)[0]["text"])
ok("the opponent's view stays on the snapshot mid-commit",
   badge(RIGHT)[0]["text"] == seen_before, badge(RIGHT)[0]["text"])

# 20 — 哥布林团伙: one card, three bodies, and they must land connected
def gang(cells=None, right=("dummy", (8, 3))):
    """A gang vs one very fat dummy parked in javelin range."""
    m = Match()
    m.assign_draft(["goblin_gang"], [right[0]])
    for k, c in (cells or [("goblin_javelin", (2, 2)), ("goblin_javelin", (2, 3)),
                           ("goblin_commander", (2, 4))]):
        assert m.place(LEFT, k, c) is None, (k, c)
    assert m.place(RIGHT, right[0], right[1]) is None
    assert m.lock_force(LEFT) is None
    assert m.lock_force(RIGHT) is None
    j1, j2, cm = (m.entity(i) for i in (1, 2, 3))
    d = m.living(RIGHT)[0]
    d.max_hp = d.hp = 200          # a wall, so nothing dies mid-test
    d.set_cell((5, 3))
    return m, j1, j2, cm, d

m = Match()
m.assign_draft(["goblin_gang"], ["dummy"])
ok("one gang card asks for three bodies", m.bodies_needed(LEFT) == 3, str(m.deploy_bodies(LEFT)))
m.place(LEFT, "goblin_javelin", (2, 2)); m.place(LEFT, "goblin_javelin", (2, 3))
ok("a third copy of a two-copy body is refused",
   m.place(LEFT, "goblin_javelin", (2, 4)) is not None)
m.place(LEFT, "goblin_commander", (1, 5))       # off on its own
err = m.lock_force(LEFT)
ok("a scattered gang cannot lock in", err is not None, err)
m.unplace(LEFT, (1, 5)); m.place(LEFT, "goblin_commander", (2, 4))
ok("a connected gang locks in fine", m.lock_force(LEFT) is None)

# 21 — the whole gang acts on one turn, in the order you give
m, j1, j2, cm, d = gang()
cm.ap = cm.max_ap
before = d.hp
turn(m, j1.id, {"orders": [
        {"entity": cm.id, "destination": None, "action": {"key": "ability:goblin_rally"}},
        {"entity": j1.id, "destination": None, "action": {"key": "attack", "shots": [[[5, 3]]]}},
        {"entity": j2.id, "destination": None, "action": {"key": "attack", "shots": [[[5, 3]]]}}]},
     d.id, {"destination": None, "action": {"key": "none"}})
rallied = before - d.hp
ok("both javelins throw in one gang turn, rallied", rallied == 2 * (j1.atk + 2),
   f"dealt {rallied}, javelin atk {j1.atk}")
ok("the gang costs exactly one turn", m.round == 2, f"round {m.round}, exchange {m.exchange}")
ok("鼓舞 expires with the turn that granted it",
   all(g.atk == g.hero.atk for g in (j1, j2, cm)), str([g.atk for g in (j1, j2, cm)]))
ok("only 指挥 banks AP", cm.ap == 1 and j1.max_ap == 0, f"cmdr {cm.ap}/{cm.max_ap}, javelin max {j1.max_ap}")

# 22 — order matters: rally after the throws helps nobody
m, j1, j2, cm, d = gang()
cm.ap = cm.max_ap
before = d.hp
turn(m, j1.id, {"orders": [
        {"entity": j1.id, "destination": None, "action": {"key": "attack", "shots": [[[5, 3]]]}},
        {"entity": j2.id, "destination": None, "action": {"key": "attack", "shots": [[[5, 3]]]}},
        {"entity": cm.id, "destination": None, "action": {"key": "ability:goblin_rally"}}]},
     d.id, {"destination": None, "action": {"key": "none"}})
ok("rallying last buffs nobody", before - d.hp == 2 * j1.atk, f"dealt {before - d.hp}")

# 23 — a goblin killed by the enemy's simultaneous action never gets its throw off
m, j1, j2, cm, d = gang()
j2.hp = 1
before = d.hp
turn(m, j1.id, {"orders": [
        {"entity": j1.id, "destination": None, "action": {"key": "attack", "shots": [[[5, 3]]]}},
        {"entity": j2.id, "destination": None, "action": {"key": "attack", "shots": [[[5, 3]]]}},
        {"entity": cm.id, "destination": None, "action": {"key": "none"}}]},
     d.id, {"destination": None, "action": {"key": "attack", "target": j2.id}})
ok("a goblin killed at index 0 loses its later action",
   not j2.alive and before - d.hp == j1.atk, f"dealt {before - d.hp}, one javelin {j1.atk}")

# 24 — orders must cover every living goblin, exactly once
m, j1, j2, cm, d = gang()
m.select_hero(LEFT, j1.id)
one = {"entity": j1.id, "destination": None, "action": {"key": "none"}}
ok("a partial order set is refused", m.commit(LEFT, {"orders": [one]}) is not None)
ok("ordering the same goblin twice is refused", m.commit(LEFT, {"orders": [one, one, one]}) is not None)

# 25 — fire at turn start: one goblin drops, its order is dropped with it
m, j1, j2, cm, d = gang()
j1.hp = 1
m.board.add_burning(j1.cell, RIGHT)
before = d.hp
turn(m, j2.id, {"orders": [
        {"entity": j1.id, "destination": None, "action": {"key": "attack", "shots": [[[5, 3]]]}},
        {"entity": j2.id, "destination": None, "action": {"key": "attack", "shots": [[[5, 3]]]}},
        {"entity": cm.id, "destination": None, "action": {"key": "none"}}]},
     d.id, {"destination": None, "action": {"key": "none"}})
ok("the burned goblin's order is dropped, the gang still acts",
   not j1.alive and before - d.hp == j2.atk, f"dealt {before - d.hp}")

# 26 — two goblins reaching for one square collide, like any two units would
m, j1, j2, cm, d = gang([("goblin_javelin", (2, 2)), ("goblin_javelin", (2, 3)),
                         ("goblin_commander", (3, 3))])
turn(m, j1.id, {"orders": [
        {"entity": j1.id, "destination": [3, 2], "action": {"key": "none"}},
        {"entity": cm.id, "destination": [3, 2], "action": {"key": "none"}},
        {"entity": j2.id, "destination": [1, 3], "action": {"key": "none"}}]},
     d.id, {"destination": None, "action": {"key": "none"}})
ok("two goblins after the same square both bounce",
   j1.cell == (2, 2) and cm.cell == (3, 3), f"{j1.cell} {cm.cell}")
ok("the uncontested goblin still moves", j2.cell == (1, 3), str(j2.cell))

# 27 — the client gets what it needs: every goblin's own moves and menu, and a
# reveal that names the gang rather than one goblin
m, j1, j2, cm, d = gang()
cm.ap = cm.max_ap
m.select_hero(LEFT, j1.id)
gb = view.state_for(m, LEFT)["commit"].get("gang")
ok("the commit payload carries the whole gang",
   gb and len(gb["members"]) == 3 and all(x["legal_moves"] and x["actions"] for x in gb["members"]),
   str(gb and [x["name"] for x in gb["members"]]))
ok("only 指挥 is offered 鼓舞",
   [len([a for a in x["actions"] if a["key"].startswith("ability:")]) for x in gb["members"]] == [0, 0, 1])
turn(m, j1.id, {"orders": [
        {"entity": cm.id, "destination": None, "action": {"key": "ability:goblin_rally"}},
        {"entity": j1.id, "destination": None, "action": {"key": "attack", "shots": [[[5, 3]]]}},
        {"entity": j2.id, "destination": None, "action": {"key": "attack", "shots": [[[5, 3]]]}}]},
     d.id, {"destination": None, "action": {"key": "none"}})
rv = m.last_reveal[LEFT]
ok("the pause screen shows the gang and its acting order",
   rv["key"] == "goblin_gang" and rv["crew"] == ["指挥", "投矛手", "投矛手"] and len(rv["hits"]) == 2,
   f"{rv['hero']} {rv.get('crew')} hits={rv['hits']}")

# 28 — 杂货店爷爷: hands an ally 1 AP when his turn starts, free, and banks none
m = arena([("shopkeeper", (3, 1)), ("tide_goddess", (3, 2)), ("gatekeeper", (3, 3))],
          [("dummy", (7, 3))])
gramps, tide, gate = (unit(m, LEFT, k) for k in ("shopkeeper", "tide_goddess", "gatekeeper"))
d = unit(m, RIGHT, "dummy")
m.select_hero(LEFT, gramps.id)
ch = view.state_for(m, LEFT)["commit"]["choices"]
ok("the handout is offered as a free pick", len(ch) == 1 and ch[0]["key"] == "handout", str(ch))
ok("only allies who can bank AP are offered",
   ch[0]["options"] == [tide.id], f"offered {ch[0]['options']}, gatekeeper max_ap {gate.max_ap}")
ok("sealing without the pick is refused",
   m.commit(LEFT, {"destination": None, "action": {"key": "none"}}) is not None)
ok("handing it to an enemy is refused",
   m.commit(LEFT, {"destination": None, "action": {"key": "none"},
                   "choices": {"handout": d.id}}) is not None)

d.set_cell((5, 1))
before_ap, before_hp = tide.ap, d.hp
turn(m, gramps.id, {"destination": [4, 1], "action": {"key": "attack", "shots": [[[5, 1]]]},
                    "choices": {"handout": tide.id}},
     d.id, {"destination": None, "action": {"key": "none"}})
ok("the ally gains 1 AP", tide.ap - before_ap == 1, f"{before_ap} -> {tide.ap}")
ok("the handout costs him nothing — he still moved and hit",
   gramps.cell == (4, 1) and before_hp - d.hp == gramps.atk,
   f"at {gramps.cell}, dealt {before_hp - d.hp}")
ok("he never charges his own bar", gramps.ap == 0 and gramps.max_ap == 0,
   f"{gramps.ap}/{gramps.max_ap}")

# ...and when nobody can use it, the pick disappears rather than blocking the turn
m = arena([("shopkeeper", (3, 1)), ("gatekeeper", (3, 3))], [("dummy", (7, 3))])
gramps = unit(m, LEFT, "shopkeeper")
m.select_hero(LEFT, gramps.id)
ok("no eligible ally, no prompt", view.state_for(m, LEFT)["commit"]["choices"] == [])
ok("and the turn seals normally",
   m.commit(LEFT, {"destination": None, "action": {"key": "none"}}) is None)

# 29 — 雾女: 大雾 shortens every enemy's reach once, and never below 1
m = arena([("mist_lady", (3, 3))],
          [("cannoneer", (7, 1)), ("berserker", (7, 2)), ("thunder_dragon", (7, 3)), ("dummy", (7, 4))])
fog = unit(m, LEFT, "mist_lady")
cannon, zerk, dragon, d = (unit(m, RIGHT, k) for k in
                           ("cannoneer", "berserker", "thunder_dragon", "dummy"))
zerk.hp = zerk.max_hp                     # keep 狂战士 out of its wounded state
before = {e.key: e.rng for e in (cannon, zerk, dragon, d)}
fog.ap = fog.max_ap
turn(m, fog.id, {"destination": None, "action": {"key": "ability:great_fog"}},
     d.id, {"destination": None, "action": {"key": "none"}})

ok("大雾 takes 1 range off a long-ranged enemy", before["cannoneer"] - cannon.rng == 1,
   f"{before['cannoneer']} -> {cannon.rng}")
ok("大雾 leaves a whole-board attacker alone", dragon.rng is None and d.rng is None)
ok("大雾 never pushes anyone below 1",
   all(e.rng >= 1 for e in (cannon, zerk)), str([cannon.rng, zerk.rng]))
ok("大雾 is repeatable — still on the menu",
   "ability:great_fog" in {a["key"] for a in m.action_menu(fog)})

# a second fog next round stacks another step off, and the floor still holds
m2 = arena([("mist_lady", (3, 3))], [("cannoneer", (7, 3)), ("berserker", (7, 1))])
fog2 = unit(m2, LEFT, "mist_lady")
cannon2, zerk2 = unit(m2, RIGHT, "cannoneer"), unit(m2, RIGHT, "berserker")
zerk2.hp = zerk2.max_hp                        # keep 狂战士 out of its wounded +1 rng state
rolls, floored = [], []
for _ in range(2):
    fog2.ap = fog2.max_ap                      # she'd otherwise need 3 turns between casts
    turn(m2, fog2.id, {"destination": None, "action": {"key": "ability:great_fog"}},
         cannon2.id, {"destination": None, "action": {"key": "none"}})
    # she is the whole left force, so the round only closes once 狂战士 has acted too
    turn(m2, None, None, zerk2.id, {"destination": None, "action": {"key": "none"}})
    rolls.append(cannon2.rng)
    floored.append(zerk2.rng)
ok("a second fog stacks another step off", rolls[0] - rolls[1] == 1, str(rolls))
ok("but a hero already on the floor stays there through both",
   floored == [1, 1], f"berserker {floored}")

# a range-1 enemy is untouched rather than dropped to 0
m = arena([("mist_lady", (3, 3))], [("goblin_commander", (7, 3)), ("dummy", (7, 4))])
fog, cmdr, d = unit(m, LEFT, "mist_lady"), unit(m, RIGHT, "goblin_commander"), unit(m, RIGHT, "dummy")
base = cmdr.rng
fog.ap = fog.max_ap
turn(m, fog.id, {"destination": None, "action": {"key": "ability:great_fog"}},
     d.id, {"destination": None, "action": {"key": "none"}})   # 指挥 is a gang: let the dummy act
ok("an enemy already at range 1 shrugs the fog off", cmdr.rng == base == 1, f"{base} -> {cmdr.rng}")

# 30 — 半人马: 冲撞 runs a fixed 3 squares, trampling the two it crosses
def charge_arena(left, right):
    m = arena(left, right)
    cen = unit(m, LEFT, "centaur")
    cen.ap = cen.max_ap
    return m, cen

def lanes_for(m, cen):
    m.select_hero(LEFT, cen.id)
    ability = next(a for a in m.action_menu(cen) if a["key"] == "ability:charge")
    return {c["dir"]: c for c in ability["targeting"]["choices"]}

# C3 · enemies at D3 and E3 · F3 open -> lands on F3, both trampled
m, cen = charge_arena([("centaur", (3, 3))], [("dummy", (7, 3)), ("gatekeeper", (8, 3))])
d, g = unit(m, RIGHT, "dummy"), unit(m, RIGHT, "gatekeeper")
d.set_cell((4, 3)); g.set_cell((5, 3))
lane = lanes_for(m, cen)["forward"]
ok("the lane reports its landing square and both victims",
   lane["landing"] == [6, 3] and len(lane["victims"]) == 2, str(lane))
ok("a charge cannot be combined with a normal move",
   m.commit(LEFT, {"destination": [4, 2],
                   "action": {"key": "ability:charge", "direction": "forward"}}) is not None)
hp0 = (d.hp, g.hp)
turn(m, cen.id, {"destination": None, "action": {"key": "ability:charge", "direction": "forward"}},
     d.id, {"destination": None, "action": {"key": "none"}})
ok("it ends three squares along", cen.cell == (6, 3), str(cen.cell))
ok("both crossed enemies take the same hit",
   hp0[0] - d.hp == hp0[1] - g.hp > 0, f"{hp0[0]-d.hp} and {hp0[1]-g.hp}")

# third square taken: damage still lands, the centaur stays put
m, cen = charge_arena([("centaur", (3, 3))], [("dummy", (7, 3)), ("gatekeeper", (8, 3))])
d, g = unit(m, RIGHT, "dummy"), unit(m, RIGHT, "gatekeeper")
d.set_cell((4, 3)); g.set_cell((6, 3))        # crossed: D3 enemy, E3 empty; F3 blocked
lane = lanes_for(m, cen)["forward"]
ok("a blocked landing is reported as such", lane["landing"] is None, str(lane))
hp0, start = d.hp, cen.cell
turn(m, cen.id, {"destination": None, "action": {"key": "ability:charge", "direction": "forward"}},
     d.id, {"destination": None, "action": {"key": "none"}})
ok("blocked charge still tramples", hp0 - d.hp > 0, f"dealt {hp0 - d.hp}")
ok("blocked charge does not move the centaur", cen.cell == start, str(cen.cell))
ok("the enemy standing on the third square is untouched", g.hp == g.max_hp,
   f"took {g.max_hp - g.hp}")

# allies are ridden past: neither damaged nor blocking
m, cen = charge_arena([("centaur", (3, 3)), ("gatekeeper", (3, 2))], [("dummy", (7, 3))])
ally, d = unit(m, LEFT, "gatekeeper"), unit(m, RIGHT, "dummy")
ally.set_cell((4, 3)); d.set_cell((5, 3))
turn(m, cen.id, {"destination": None, "action": {"key": "ability:charge", "direction": "forward"}},
     d.id, {"destination": None, "action": {"key": "none"}})
ok("an ally in the lane is ridden past unharmed and does not block",
   cen.cell == (6, 3) and ally.hp == ally.max_hp, f"{cen.cell}, ally took {ally.max_hp - ally.hp}")

# a lane that would neither move nor hit anyone is not offered
m, cen = charge_arena([("centaur", (1, 1))], [("dummy", (7, 3))])
lanes = lanes_for(m, cen)
ok("a lane off the board with nobody to hit is not offered", "up" not in lanes, str(list(lanes)))
ok("that lane is refused if asked for anyway",
   m.commit(LEFT, {"destination": None,
                   "action": {"key": "ability:charge", "direction": "up"}}) is not None)

# 31 — the charge reads the board *after* movement, so a landing square can be
# taken or freed by the same exchange it was aimed in
def charge_vs_move(gk_from, gk_to):
    m, cen = charge_arena([("centaur", (3, 3))], [("dummy", (7, 3)), ("gatekeeper", (8, 3))])
    d, g = unit(m, RIGHT, "dummy"), unit(m, RIGHT, "gatekeeper")
    d.set_cell((4, 3))                     # sits in a crossed square
    g.set_cell(gk_from)
    predicted = lanes_for(m, cen)["forward"]["landing"]
    hp0 = d.hp
    turn(m, cen.id, {"destination": None, "action": {"key": "ability:charge", "direction": "forward"}},
         g.id, {"destination": list(gk_to), "action": {"key": "none"}})
    return predicted, cen.cell, hp0 - d.hp

predicted, ended, dealt = charge_vs_move((6, 2), (6, 3))    # enemy steps into the landing square
ok("an enemy moving into the landing square stops the charge dead",
   predicted == [6, 3] and ended == (3, 3), f"previewed {predicted}, ended {ended}")
ok("...and the trample still lands anyway", dealt > 0, f"dealt {dealt}")

predicted, ended, dealt = charge_vs_move((6, 3), (6, 4))    # enemy steps out of it
ok("an enemy vacating the landing square lets the charge through",
   predicted is None and ended == (6, 3), f"previewed {predicted}, ended {ended}")
ok("...and that trample lands too", dealt > 0, f"dealt {dealt}")

# 32 — 石像鬼: weapons chip it for 1, magic and fire go straight through
m = arena([("gargoyle", (3, 3)), ("dummy", (3, 1))], [("cannoneer", (7, 3)), ("imp", (7, 1))])
gar, ally, cannon, imp = (unit(m, s, k) for s, k in
                          ((LEFT, "gargoyle"), (LEFT, "dummy"), (RIGHT, "cannoneer"), (RIGHT, "imp")))
hit = lambda who, amount, cat: DMG.deal(
    m, DMG.DamageEvent(source=cannon, target=who, amount=amount, category=cat))

h = gar.hp
hit(gar, cannon.atk, DMG.NORMAL_ATTACK)
ok("a normal attack chips the stone for 1", h - gar.hp == 1, f"took {h - gar.hp} from atk {cannon.atk}")
h = gar.hp
hit(gar, 99, DMG.NORMAL_ATTACK)
ok("no normal attack does better, however hard", h - gar.hp == 1, f"took {h - gar.hp} from 99")

# an ability lands in full — compare against a dummy taking the same instant
gar.hp, ally.hp = gar.max_hp, ally.max_hp
imp.ap = imp.max_ap
imp.set_cell((7, 3))      # 射线 sears the caster's row: stone and dummy both stand in it
ally.set_cell((5, 3))
turn(m, gar.id, {"destination": None, "action": {"key": "none"}},
     imp.id, {"destination": None, "action": {"key": "ability:ray"}})
took_stone, took_flesh = gar.max_hp - gar.hp, ally.max_hp - ally.hp
ok("an ability hits the stone as hard as it hits flesh",
   took_stone == took_flesh > 1, f"stone {took_stone}, dummy {took_flesh}")

# burning ground also ignores the hide
m = arena([("gargoyle", (3, 3))], [("dummy", (7, 3))])
gar, d = unit(m, LEFT, "gargoyle"), unit(m, RIGHT, "dummy")
burn = m.board.add_burning((3, 3), RIGHT).damage
m.select_hero(LEFT, gar.id)
m.commit(LEFT, {"destination": None, "action": {"key": "none"}})
ok("burning ground ignores the stone hide", gar.max_hp - gar.hp == burn,
   f"took {gar.max_hp - gar.hp}, tile deals {burn}")

# 33 — 美梦神: 魔法守护 silences the enemy's abilities until she stirs again
def ward_arena():
    m = arena([("dream_goddess", (3, 3)), ("gatekeeper", (3, 1))],
              [("thunder_dragon", (7, 3)), ("dummy", (7, 1))])
    god, gate = unit(m, LEFT, "dream_goddess"), unit(m, LEFT, "gatekeeper")
    dragon, d = unit(m, RIGHT, "thunder_dragon"), unit(m, RIGHT, "dummy")
    god.ap, dragon.ap = god.max_ap, dragon.max_ap
    return m, god, gate, dragon, d

storm = {"key": "ability:thunderstorm"}
hold = {"destination": None, "action": {"key": "none"}}

# an ability already sealed in the same exchange still resolves
m, god, gate, dragon, d = ward_arena()
hp0 = gate.hp
turn(m, god.id, {"destination": None, "action": {"key": "ability:magic_ward"}},
     dragon.id, {"destination": None, "action": storm})
ok("an ability sealed in the same exchange still lands", hp0 - gate.hp > 0, f"took {hp0 - gate.hp}")

# from the next exchange the enemy cannot commit one
dragon.ap = dragon.max_ap
m.select_hero(RIGHT, d.id)
menu = {a["key"]: a for a in m.action_menu(dragon)}
ok("warded abilities are shown as unavailable, with the reason",
   menu["ability:thunderstorm"]["affordable"] is False and menu["ability:thunderstorm"]["blocked"],
   str(menu["ability:thunderstorm"]["blocked"]))
m.select_hero(RIGHT, dragon.id)
ok("committing a warded ability is refused",
   m.commit(RIGHT, {"destination": None, "action": storm}) is not None)
ok("a normal attack is still allowed while warded",
   m.commit(RIGHT, {"destination": None, "action": {"key": "attack", "target": gate.id}}) is None)

# the ward lifts when she takes her next turn
m, god, gate, dragon, d = ward_arena()
turn(m, god.id, {"destination": None, "action": {"key": "ability:magic_ward"}}, d.id, hold)
ok("the ward is up while it is the enemy's turn to act",
   m.ability_locked(RIGHT) is god, str(m.ability_locked(RIGHT)))
turn(m, gate.id, hold, dragon.id, hold)         # rest of round 1
turn(m, god.id, hold, dragon.id, hold)          # her next turn: the ward lifts
ok("the ward lifts once she acts again", m.ability_locked(RIGHT) is None)

# killing her lifts it immediately
m, god, gate, dragon, d = ward_arena()
turn(m, god.id, {"destination": None, "action": {"key": "ability:magic_ward"}}, d.id, hold)
ok("ward up before she falls", m.ability_locked(RIGHT) is god)
god.hp = 0
m.sweep_deaths()
ok("the ward dies with her", m.ability_locked(RIGHT) is None and not god.alive)

# and it shows on her token while it holds
m, god, gate, dragon, d = ward_arena()
turn(m, god.id, {"destination": None, "action": {"key": "ability:magic_ward"}}, d.id, hold)
badge = next(u["status"] for u in view.state_for(m, LEFT)["units"] if u["id"] == god.id)
ok("the ward shows as a badge on her token", badge and badge[0]["key"] == "ward", str(badge))

# 34 — 诅咒娃娃: 咒毒 marks an ally at game start; whoever draws its blood loses
# the next two rounds
def curse_arena():
    m = Match()
    m.assign_draft(["cursed_doll", "gatekeeper"], ["cannoneer", "dummy"])
    for k, c in (("cursed_doll", (3, 3)), ("gatekeeper", (3, 1))):
        assert m.place(LEFT, k, c) is None
    for k, c in (("cannoneer", (7, 3)), ("dummy", (7, 1))):
        assert m.place(RIGHT, k, c) is None
    assert m.lock_force(LEFT) is None
    assert m.lock_force(RIGHT) is None
    doll, gate = unit(m, LEFT, "cursed_doll"), unit(m, LEFT, "gatekeeper")
    cannon, d = unit(m, RIGHT, "cannoneer"), unit(m, RIGHT, "dummy")
    assert m.phase == "opening", m.phase
    assert m.opening_choose(LEFT, {"target": gate.id}) is None   # mark the gatekeeper
    return m, doll, gate, cannon, d

hold = {"destination": None, "action": {"key": "none"}}

m, doll, gate, cannon, d = curse_arena()
ok("the mark is placed in the opening phase", gate.vars.get("curse_mark") == doll.id)
badges_of = lambda viewer, eid: [s["key"] for u in view.state_for(m, viewer)["units"]
                                 if u["id"] == eid for s in u["status"]]
ok("the marked hero wears a badge for its own side", "cursed" in badges_of(LEFT, gate.id))
ok("but the enemy cannot see who is baited", "cursed" not in badges_of(RIGHT, gate.id),
   str(badges_of(RIGHT, gate.id)))
ok("nor read it in their field log",
   not any("curses" in l["text"] for l in view.state_for(m, RIGHT)["log"]) and
   any("curses" in l["text"] for l in view.state_for(m, LEFT)["log"]))

# a hit on the marked hero freezes its source for the next two rounds
cannon.set_cell((4, 1))
turn(m, gate.id, hold, cannon.id, {"destination": None, "action": {"key": "attack", "shots": [[[3, 1]]]}})
ok("striking the marked hero curses the attacker — from the next round, not this one",
   cannon.vars.get("frozen_at_round") == m.round and not m.frozen(cannon),
   f"cursed in round {m.round}, still free for the rest of it")
ok("the mark is spent after one trigger", not gate.vars.get("curse_mark"))
ok("the curse does not touch anyone else", not m.frozen(d))

start_round = m.round
seen = []
for _ in range(3):
    while m.round == start_round + len(seen):
        # play out the round with whoever can still act
        left = [e.id for e in m.unacted(LEFT)]
        right = [e.id for e in m.unacted(RIGHT)]
        turn(m, left[0] if left else None, hold, right[0] if right else None, hold)
    seen.append(m.frozen(cannon))
ok("frozen for exactly the next two rounds, then free again",
   seen[:2] == [True, True] and seen[2] is False, str(seen))

# it cannot be picked up while frozen
m, doll, gate, cannon, d = curse_arena()
cannon.set_cell((4, 1))
turn(m, gate.id, hold, cannon.id, {"destination": None, "action": {"key": "attack", "shots": [[[3, 1]]]}})
turn(m, doll.id, hold, d.id, hold)              # closes round 1
ok("a frozen hero is not offered a turn", cannon.id not in [e.id for e in m.unacted(RIGHT)],
   f"unacted: {[e.key for e in m.unacted(RIGHT)]}")
ok("and selecting it is refused", m.select_hero(RIGHT, cannon.id) is not None)
ok("it wears the frozen badge",
   any(s["key"] == "frozen" for u in view.state_for(m, RIGHT)["units"]
       if u["id"] == cannon.id for s in u["status"]))

# tile damage is not an attack or an ability, so it never springs the trap
m, doll, gate, cannon, d = curse_arena()
m.board.add_burning(gate.cell, RIGHT)
m.select_hero(LEFT, gate.id)
m.commit(LEFT, hold)
ok("burning ground does not spring the curse",
   gate.hp < gate.max_hp and gate.vars.get("curse_mark") == doll.id,
   f"burned for {gate.max_hp - gate.hp}, mark still {gate.vars.get('curse_mark')}")

# 35 — 狙击手: shoots the first enemy down its row or column, for the distance
from actions import LineShot

def sniper_arena(right):
    m = arena([("sniper", (3, 3)), ("gatekeeper", (3, 1))], right)
    sn = unit(m, LEFT, "sniper")
    return m, sn

hold = {"destination": None, "action": {"key": "none"}}
shot = lambda d: {"destination": None, "action": {"key": "attack", "direction": d}}

# a lone enemy straight ahead: damage is how far away it is
m, sn = sniper_arena([("dummy", (7, 3)), ("cannoneer", (7, 1))])
d = unit(m, RIGHT, "dummy")
lanes = {l["dir"]: l for l in LineShot.lanes(m, sn)}
ok("only lanes with an enemy in them are offered", list(lanes) == ["forward"], str(list(lanes)))
ok("the lane reports its target and the distance",
   lanes["forward"]["target"] == d.id and lanes["forward"]["distance"] == 4, str(lanes["forward"]))
before = d.hp
turn(m, sn.id, shot("forward"), d.id, hold)
ok("the shot deals the distance (plus its atk)", before - d.hp == 4 + sn.atk,
   f"dealt {before - d.hp} over 4 squares, atk {sn.atk}")

# stepping closer makes it hit for less — the whole point of the hero
m, sn = sniper_arena([("dummy", (7, 3)), ("cannoneer", (7, 1))])
d = unit(m, RIGHT, "dummy")
d.set_cell((5, 3))
before = d.hp
turn(m, sn.id, shot("forward"), d.id, hold)
ok("a nearer target takes less", before - d.hp == 2 + sn.atk, f"dealt {before - d.hp} over 2 squares")

# only the FIRST enemy in the lane is hit
m, sn = sniper_arena([("dummy", (7, 3)), ("cannoneer", (8, 3))])
near, far = unit(m, RIGHT, "dummy"), unit(m, RIGHT, "cannoneer")
b_near, b_far = near.hp, far.hp
turn(m, sn.id, shot("forward"), near.id, hold)
ok("only the first enemy in the lane is hit",
   b_near - near.hp > 0 and far.hp == b_far, f"near {b_near-near.hp}, far {b_far-far.hp}")

# an ally in the lane blocks the shot entirely
m = arena([("sniper", (3, 3)), ("gatekeeper", (2, 3))], [("dummy", (7, 3))])
sn, d = unit(m, LEFT, "sniper"), unit(m, RIGHT, "dummy")
unit(m, LEFT, "gatekeeper").set_cell((5, 3))       # our own body between them
ok("an ally in the lane blocks the shot", LineShot.scan(m, sn, "forward") is None)
ok("and committing it is refused",
   (m.select_hero(LEFT, sn.id) or m.commit(LEFT, shot("forward"))) is not None)

# the lane is re-scanned from where it actually ends up
m, sn = sniper_arena([("dummy", (7, 3)), ("cannoneer", (7, 1))])
d = unit(m, RIGHT, "dummy")
before = d.hp
turn(m, sn.id, {"destination": [4, 3], "action": {"key": "attack", "direction": "forward"}},
     d.id, hold)
ok("moving closer first shortens the shot it fires",
   sn.cell == (4, 3) and before - d.hp == 3 + sn.atk, f"at {sn.cell}, dealt {before - d.hp}")

# it fires along its column too
m, sn = sniper_arena([("dummy", (7, 5)), ("cannoneer", (7, 1))])
d = unit(m, RIGHT, "dummy")
d.set_cell((3, 5))
lanes = {l["dir"]: l for l in LineShot.lanes(m, sn)}
ok("a lane down its own column works the same", "down" in lanes and lanes["down"]["distance"] == 2,
   str(lanes))

# 36 — regressions found in the character audit
hold = {"destination": None, "action": {"key": "none"}}

# 武器大师 must be able to hold, and to move without swinging
m = arena([("weapon_master", (3, 3))], [("dummy", (7, 3))])
wm, d = unit(m, LEFT, "weapon_master"), unit(m, RIGHT, "dummy")
m.select_hero(LEFT, wm.id)
ok("武器大师 can hold position", m.commit(LEFT, hold) is None)
m.select_hero(RIGHT, d.id); m.commit(RIGHT, hold)
ok("holding draws no weapon, so no stance", not wm.vars.get("stance_dr"),
   f"stance_dr {wm.vars.get('stance_dr')}")
m.select_hero(LEFT, wm.id)
ok("武器大师 can move without attacking",
   m.commit(LEFT, {"destination": [4, 3], "action": {"key": "none"}}) is None)

# a frozen goblin leaves the gang's turn entirely
m = arena([("goblin_javelin", (2, 1)), ("goblin_javelin", (2, 2)), ("goblin_commander", (2, 3))],
          [("dummy", (8, 3))])
j1, j2 = [e for e in m.living(LEFT) if e.key == "goblin_javelin"]
cm, d = unit(m, LEFT, "goblin_commander"), unit(m, RIGHT, "dummy")
m.freeze(j2)
m.start_round()
ok("a frozen goblin is not one of the turn's actors",
   [e.id for e in m.turn_actors(j1)] == [j1.id, cm.id], str([e.name for e in m.turn_actors(j1)]))
ok("the gang commits with the survivors' orders alone",
   (m.select_hero(LEFT, j1.id) or m.commit(LEFT, {"orders": [
       {"entity": j1.id, "destination": None, "action": {"key": "none"}},
       {"entity": cm.id, "destination": None, "action": {"key": "none"}}]})) is None)
ok("and the frozen goblin stays frozen through it", m.frozen(j2))

# freezing 美梦神 must lift her ward, or the enemy is silenced for ever
m = arena([("dream_goddess", (3, 3)), ("gatekeeper", (3, 1))], [("thunder_dragon", (7, 3))])
god = unit(m, LEFT, "dream_goddess")
m.set_ability_lock(god)
ok("the ward is up", m.ability_locked(RIGHT) is god)
m.freeze(god)
ok("freezing the warder lifts the ward", m.ability_locked(RIGHT) is None)

# a frozen hero is not offered to its own client
m = arena([("gatekeeper", (3, 1)), ("cannoneer", (3, 3))], [("dummy", (7, 3))])
gate, cannon = unit(m, LEFT, "gatekeeper"), unit(m, LEFT, "cannoneer")
m.freeze(gate)
m.start_round()
st = view.state_for(m, LEFT)
ok("a frozen hero is kept out of commit.unacted", st["commit"]["unacted"] == [cannon.id],
   str(st["commit"]["unacted"]))
ok("and it carries a badge saying why",
   any(s["key"] == "frozen" for u in st["units"] if u["id"] == gate.id for s in u["status"]))
ok("selecting it is refused", m.select_hero(LEFT, gate.id) is not None)

# damage that is fully turned aside must not spring 咒毒
m = arena([("cursed_doll", (3, 3)), ("paladin", (3, 1))], [("cannoneer", (7, 1)), ("dummy", (7, 3))])
pal = unit(m, LEFT, "paladin")
assert m.opening_choose(LEFT, {"target": pal.id}) is None
a, b = unit(m, RIGHT, "cannoneer"), unit(m, RIGHT, "dummy")
DMG.apply_batch(m, [DMG.DamageEvent(source=a, target=pal, amount=3, category=DMG.NORMAL_ATTACK)])
ok("a landed hit springs the curse", a.vars.get("frozen_at_round") is not None)
pal.vars["curse_mark"] = 1                       # re-arm and try a blocked hit
DMG.apply_batch(m, [DMG.DamageEvent(source=b, target=pal, amount=5, category=DMG.NORMAL_ATTACK)])
ok("a hit the holy shield turns aside does not spring it",
   b.vars.get("frozen_at_round") is None and pal.vars.get("curse_mark"),
   f"frozen={b.vars.get('frozen_at_round')}, mark={pal.vars.get('curse_mark')}")

# 37 — the sniper's lane is scanned from where it will stand, and its own pre-move
# body must not block it
m = arena([("sniper", (3, 3))], [("dummy", (7, 3))])
sn, d = unit(m, LEFT, "sniper"), unit(m, RIGHT, "dummy")
d.set_cell((1, 3))                               # enemy behind it; it steps forward and shoots back
ok("its own body does not block the lane it fires down",
   LineShot.scan(m, sn, "backward", (4, 3)) is not None,
   str(LineShot.scan(m, sn, "backward", (4, 3))))
before = d.hp
turn(m, sn.id, {"destination": [4, 3], "action": {"key": "attack", "direction": "backward"}},
     d.id, hold)
ok("so it can move away from a target and still shoot it",
   sn.cell == (4, 3) and before - d.hp == 3 + sn.atk, f"at {sn.cell}, dealt {before - d.hp}")

# a target that steps out of the lane in the same exchange is simply missed
m = arena([("sniper", (3, 3))], [("dummy", (7, 3))])
sn, d = unit(m, LEFT, "sniper"), unit(m, RIGHT, "dummy")
before = d.hp
turn(m, sn.id, {"destination": None, "action": {"key": "attack", "direction": "forward"}},
     d.id, {"destination": [7, 2], "action": {"key": "none"}})
ok("a target that steps out of the row is missed, not followed",
   d.cell == (7, 2) and d.hp == before, f"at {d.cell}, took {before - d.hp}")
ok("and the miss is reported in the open",
   any("nobody there" in l["text"] and not l["quiet"] for l in m.log), str(m.log[-3:]))

# 38 — 血祭 spends current HP as well as maximum
m = arena([("blood_mage", (3, 3))], [("dummy", (7, 3))])
bm, d = unit(m, LEFT, "blood_mage"), unit(m, RIGHT, "dummy")
bm.ap = bm.max_ap
bm.hp = bm.max_hp - 6                            # wounded: a clamp alone would hide the cost
hp0, max0, atk0 = bm.hp, bm.max_hp, bm.atk
turn(m, bm.id, {"destination": None, "action": {"key": "ability:blood_rite", "amount": 4}},
     d.id, hold)
ok("血祭 takes the sacrifice out of current HP too", hp0 - bm.hp == 4, f"{hp0} -> {bm.hp}")
ok("...and out of maximum HP", max0 - bm.max_hp == 4, f"{max0} -> {bm.max_hp}")
ok("...and pays it back as attack", bm.atk - atk0 == 4, f"{atk0} -> {bm.atk}")

m = arena([("blood_mage", (3, 3))], [("dummy", (7, 3))])
bm = unit(m, LEFT, "blood_mage")
bm.ap = bm.max_ap
bm.hp = 3
m.select_hero(LEFT, bm.id)
ok("血祭 can never be spent down to death",
   m.commit(LEFT, {"destination": None, "action": {"key": "ability:blood_rite", "amount": 3}}) is not None)
ok("but it may be spent to within a point of it",
   m.commit(LEFT, {"destination": None, "action": {"key": "ability:blood_rite", "amount": 2}}) is None)
d = unit(m, RIGHT, "dummy")
m.select_hero(RIGHT, d.id); m.commit(RIGHT, hold)      # let the exchange resolve
ok("leaving the caster alive on 1", bm.alive and bm.hp == 1, f"hp {bm.hp}")

# 39 — a heal in the same instant saves a hero that would otherwise fall
m = arena([("tide_goddess", (3, 1)), ("gatekeeper", (3, 3))], [("cannoneer", (7, 3))])
tide, gate = unit(m, LEFT, "tide_goddess"), unit(m, LEFT, "gatekeeper")
cannon = unit(m, RIGHT, "cannoneer")
tide.ap = tide.max_ap
cannon.set_cell((4, 3))
gate.hp = cannon.atk                             # exactly lethal this instant
turn(m, tide.id, {"destination": None, "action": {"key": "ability:heal", "target": gate.id}},
     cannon.id, {"destination": None, "action": {"key": "attack", "shots": [[[3, 3]]]}})
ok("a hero healed in the instant it drops survives", gate.alive, f"hp {gate.hp}")
ok("and ends on the healed total, not on zero", gate.hp > 0, f"hp {gate.hp}")

# the reorder must not resurrect anyone: without the heal it still dies
m = arena([("tide_goddess", (3, 1)), ("gatekeeper", (3, 3))], [("cannoneer", (7, 3))])
tide, gate = unit(m, LEFT, "tide_goddess"), unit(m, LEFT, "gatekeeper")
cannon = unit(m, RIGHT, "cannoneer")
cannon.set_cell((4, 3))
gate.hp = cannon.atk
turn(m, tide.id, hold,
     cannon.id, {"destination": None, "action": {"key": "attack", "shots": [[[3, 3]]]}})
ok("an unhealed hero taking the same blow still dies", not gate.alive)

# 40 — 鬼魂: bodiless, haunting, and the choice to take flesh
def ghost_arena():
    m = arena([("ghost", (3, 3)), ("gatekeeper", (3, 1))], [("cannoneer", (7, 3)), ("dummy", (7, 1))])
    return (m, unit(m, LEFT, "ghost"), unit(m, LEFT, "gatekeeper"),
            unit(m, RIGHT, "cannoneer"), unit(m, RIGHT, "dummy"))

def ghost_round(m, g, action):
    """The ghost acts, then the round is played out so it gets another turn."""
    r0 = m.round
    assert m.select_hero(LEFT, g.id) is None
    err = m.commit(LEFT, action)
    guard = 0
    while m.round == r0 and m.phase in ("commit", "victim") and guard < 12:
        guard += 1
        while m.phase == "victim":
            for side in (LEFT, RIGHT):
                o = m.res["options"][side]
                if o and m.res["picks"][side] is None:
                    m.choose_victim(side, o[0])
        l = next((x for x in m.unacted(LEFT)), None)
        r = next((x for x in m.unacted(RIGHT)), None)
        if not l and not r:
            break
        if l: m.select_hero(LEFT, l.id); m.commit(LEFT, hold)
        if r: m.select_hero(RIGHT, r.id); m.commit(RIGHT, hold)
    return err

m, g, gate, cannon, d = ghost_arena()
ok("鬼魂 starts with no square at all", g.cell is None and g.alive)
ok("nothing can target it, and it holds no ground",
   not g.flags["targetable"] and not g.flags["blocks_movement"], str(g.flags))
ok("a bodiless ghost cannot hold the field", not g.flags["counts_for_defeat"])
ok("and it is offered no attack, only the haunt",
   [a["key"] for a in m.action_menu(g)] == ["none", "ability:possess"],
   str([a["key"] for a in m.action_menu(g)]))

hp0, rng0, atk0 = cannon.hp, cannon.rng, cannon.atk
possess = lambda t: {"destination": None, "action": {"key": "ability:possess", "target": t.id}}
ghost_round(m, g, possess(cannon))
ok("附身 deals its damage", hp0 - cannon.hp == 2, f"took {hp0 - cannon.hp}")
ok("...shortens the victim's reach", rng0 - cannon.rng == 1, f"{rng0} -> {cannon.rng}")
before = gate.hp
DMG.apply_batch(m, [DMG.DamageEvent(source=cannon, target=gate, amount=atk0,
                                    category=DMG.NORMAL_ATTACK)])
ok("...and weakens everything it deals by 1", atk0 - (before - gate.hp) == 1,
   f"a {atk0} blow landed for {before - gate.hp}")

# the haunt lifts when the ghost's own next turn comes round
ghost_round(m, g, possess(d))
ok("the old haunt lifts at the ghost's next turn",
   cannon.stat("damage_dealt") == 0 and cannon.rng == rng0, f"shift {cannon.stat('damage_dealt')}, rng {cannon.rng}")
ok("and the new victim carries it instead", d.stat("damage_dealt") == -1)

ok("it cannot take flesh before the fourth turn", m.legal_moves(g) == [],
   f"after {g.vars.get('turns_done')} turns: {m.legal_moves(g)}")
ghost_round(m, g, possess(cannon))
squares = m.legal_moves(g)
ok("on the fourth turn it may step out beside its host", bool(squares),
   f"after {g.vars.get('turns_done')} turns: {squares}")
ok("only into free squares beside the hero it haunts",
   all(abs(c[0] - cannon.cell[0]) + abs(c[1] - cannon.cell[1]) == 1 for c in squares),
   f"{squares} around {cannon.cell}")
ok("and until it does, it still has no attack",
   m.commit(LEFT, {"destination": None, "action": {"key": "attack", "shots": [[[7, 3]]]}}) is not None)

# it walks out of the host AND takes that turn normally — move plus attack
cannon.set_cell((7, 3))
where = [c for c in squares if abs(c[0] - cannon.cell[0]) + abs(c[1] - cannon.cell[1]) == 1][0]
hp_before = cannon.hp
ghost_round(m, g, {"destination": where,
                   "action": {"key": "attack", "shots": [[list(cannon.cell)]]}})
ok("it takes flesh where it chose", g.cell == tuple(where), str(g.cell))
ok("and attacks in the same turn it appears", hp_before - cannon.hp == g.atk,
   f"dealt {hp_before - cannon.hp}, atk {g.atk}")
ok("it becomes an ordinary unit",
   g.flags["targetable"] and g.flags["counts_for_defeat"] and g.flags["blocks_movement"])
ok("附身 is given up for good", "possess" not in [a.key for a in g.abilities],
   str([a.key for a in g.abilities]))
ok("the haunt it was holding lifts with it", cannon.stat("damage_dealt") == 0)
before = g.hp
DMG.apply_batch(m, [DMG.DamageEvent(source=cannon, target=g, amount=3, category=DMG.NORMAL_ATTACK)])
ok("and it can finally be hurt", before - g.hp == 3, f"took {before - g.hp}")
# 41 — 猛犸: one swing catches every enemy standing beside it, diagonals included
m = arena([("mammoth", (3, 3)), ("gatekeeper", (3, 1))],
          [("dummy", (7, 1)), ("cannoneer", (7, 2)), ("gatekeeper", (7, 3)), ("berserker", (7, 4))])
mam, ally = unit(m, LEFT, "mammoth"), unit(m, LEFT, "gatekeeper")
d, cannon, gk, zerk = (unit(m, RIGHT, k) for k in ("dummy", "cannoneer", "gatekeeper", "berserker"))
d.set_cell((4, 3))        # orthogonal
cannon.set_cell((4, 2))   # diagonal
gk.set_cell((2, 4))       # diagonal, the other side
zerk.set_cell((5, 3))     # two squares away — out of reach
ally.set_cell((3, 2))     # our own, standing right beside it
before = {e.key: e.hp for e in (d, cannon, gk, zerk, ally)}

m.select_hero(LEFT, mam.id)
menu = {a["key"]: a for a in m.action_menu(mam)}
ok("its attack needs no aiming", menu["attack"]["targeting"]["kind"] == "area",
   str(menu["attack"]["targeting"]["kind"]))
ok("and it covers the 8 squares around it",
   len(menu["attack"]["targeting"]["cells"]) == 8, str(menu["attack"]["targeting"]["cells"]))

turn(m, mam.id, {"destination": None, "action": {"key": "attack"}},
     zerk.id, {"destination": None, "action": {"key": "none"}})
ok("every adjacent enemy is hit, diagonals included",
   all(before[e.key] - e.hp == mam.atk for e in (d, cannon, gk)),
   str([(e.key, before[e.key] - e.hp) for e in (d, cannon, gk)]))
ok("an enemy two squares off is untouched", zerk.hp == before["berserker"],
   f"took {before['berserker'] - zerk.hp}")
ok("its own side is never caught in the swing", ally.hp == before["gatekeeper"],
   f"ally took {before['gatekeeper'] - ally.hp}")

# the shape follows it: it is centred where the mammoth actually ends up
m = arena([("mammoth", (3, 3))], [("dummy", (7, 3))])
mam, d = unit(m, LEFT, "mammoth"), unit(m, RIGHT, "dummy")
d.set_cell((5, 3))        # out of reach from C3, adjacent once it steps to D3
before = d.hp
turn(m, mam.id, {"destination": [4, 3], "action": {"key": "attack"}},
     d.id, {"destination": None, "action": {"key": "none"}})
ok("the swing is centred where it ends up, not where it started",
   mam.cell == (4, 3) and before - d.hp == mam.atk, f"at {mam.cell}, dealt {before - d.hp}")

print("\nlog tail:")
for line in m.log[-5:]:
    print("   ", line["text"])
