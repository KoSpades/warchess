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


def build(left=L, right=R):
    m = Match()
    m.assign_draft([k for k, _ in left], [k for k, _ in right])
    for k, c in left:
        assert m.place(LEFT, k, c) is None
    for k, c in right:
        assert m.place(RIGHT, k, c) is None
    assert m.lock_force(LEFT) is None
    assert m.lock_force(RIGHT) is None
    return m


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


def turn(m, ls, la, rs, ra, prefer=None):
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
                want = (prefer or {}).get(side)
                m.choose_victim(side, want if want in opts else opts[0])


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
ok("马尔斯 opens at its base rng/atk", mars.atk == base_atk and mars.rng == base_rng)
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

print("\nlog tail:")
for line in m.log[-5:]:
    print("   ", line["text"])
