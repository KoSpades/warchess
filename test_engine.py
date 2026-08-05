"""Headless checks on the rules most likely to be implemented wrong.

Robustness rule (hero numbers get retuned constantly): NEVER assert a hardcoded
absolute stat. Assert on the *effect* — damage dealt (`before - after`), whether
a hit landed at all, or a change relative to a captured baseline. Attacks mark
just the target's own cell (1 cell) and fire from close range, so cell-count and
range tweaks can't break these either.
"""

import collections
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


def turn(m, ls, la, rs, ra, land=None):
    """Commit one exchange and answer any prompts it pauses on. `land` picks the
    square for a movement choice (刺客's blink); the first on offer by default."""
    if ls is not None:
        assert m.select_hero(LEFT, ls) is None
        assert m.commit(LEFT, la) is None
    if rs is not None:
        assert m.select_hero(RIGHT, rs) is None
        assert m.commit(RIGHT, ra) is None
    guard = 0
    while m.phase in ("victim", "move_choice"):
        guard += 1
        assert guard < 20, "resolution loop stuck"
        if m.phase == "move_choice":
            for side in (LEFT, RIGHT):
                pend = m.move_choices[side]
                if pend:
                    opts = pend[0]["options"]
                    pick = land if land is not None and list(land) in opts else opts[0]
                    assert m.choose_move(side, pick) is None
            continue
        for side in (LEFT, RIGHT):
            opts = m.res["options"][side]
            if opts and not m.victims_complete(side):
                m.choose_victim(side, opts[0])


def unit(m, side, key):
    return next(e for e in m.living(side) if e.key == key)


def ok(label, cond, detail=""):
    print(f"{'PASS' if cond else 'FAIL'}  {label}" + (f"   [{detail}]" if detail else ""))


# 0 — the hero data itself is well formed: unknown attack modes, squads naming
# heroes that do not exist, art that can never be found, abilities nobody can pay
# for. Cheap, and it catches these before they reach a game.
import heroes as HEROES
_bad = HEROES.check_roster()
ok("the roster is structurally sound", not _bad, "; ".join(_bad))


# 1 — both step to different cells, both move
m = build()
turn(m, 1, {"destination": [4, 1], "action": {"key": "none"}},
     5, {"destination": [6, 1], "action": {"key": "none"}})
ok("independent moves both apply", m.entity(1).cell == (4, 1) and m.entity(5).cell == (6, 1))

# 2 — both step to the same empty cell: the stronger claim takes it, the other stays
m = build()
m.entity(5).set_cell((5, 1))
a, b = m.entity(1), m.entity(5)                 # 枪兵 19 max hp vs 火法师 17
turn(m, a.id, {"destination": [4, 1], "action": {"key": "none"}},
     b.id, {"destination": [4, 1], "action": {"key": "none"}})
ok("a contested square goes to the bigger frame",
   a.cell == (4, 1) and b.cell == (5, 1), f"{a.name} {a.cell}, {b.name} {b.cell}")

# ...and the ordering is max HP, then current HP, then attack
m = build()
a, b = m.entity(1), m.entity(5)
b.set_cell((5, 1))
a.max_hp = b.max_hp = 20                        # level the first tiebreak
a.hp, b.hp = 12, 18                             # b is in better shape
turn(m, a.id, {"destination": [4, 1], "action": {"key": "none"}},
     b.id, {"destination": [4, 1], "action": {"key": "none"}})
ok("with equal frames the healthier one takes it",
   b.cell == (4, 1) and a.cell == (3, 1), f"{a.cell} {b.cell}")

m = build()
a, b = m.entity(1), m.entity(5)
b.set_cell((5, 1))
a.max_hp = b.max_hp = 20
a.hp = b.hp = 15
a.add_modifier(__import__("entities").Modifier("atk", "add", 5))   # a hits harder
turn(m, a.id, {"destination": [4, 1], "action": {"key": "none"}},
     b.id, {"destination": [4, 1], "action": {"key": "none"}})
ok("with equal frames and health the harder hitter takes it",
   a.cell == (4, 1) and b.cell == (5, 1), f"{a.cell} {b.cell}")

# an exact tie still resolves — somebody gets the square, nobody bounces off it
m = arena([("gatekeeper", (3, 1))], [("gatekeeper", (7, 1))])
x, y = m.living(LEFT)[0], m.living(RIGHT)[0]
x.set_cell((4, 1)); y.set_cell((6, 1))
turn(m, x.id, {"destination": [5, 1], "action": {"key": "none"}},
     y.id, {"destination": [5, 1], "action": {"key": "none"}})
ok("an exact tie is still decided, not shared",
   ((x.cell == (5, 1)) != (y.cell == (5, 1))), f"{x.cell} {y.cell}")

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
ok("two goblins after the same square — one takes it, the other stays",
   (j1.cell == (3, 2)) != (cm.cell == (3, 2)), f"{j1.cell} {cm.cell}")
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
ok("it loses exactly its next turn, then acts again",
   seen == [True, False, False], str(seen))

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
                if o and not m.victims_complete(side):
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

# the body it builds is made of what it drained
m, g, gate, cannon, d = ghost_arena()
possess = lambda t: {"destination": None, "action": {"key": "ability:possess", "target": t.id}}
drained = 0
for _ in range(3):
    before_hp = cannon.hp
    ghost_round(m, g, possess(cannon))
    drained += before_hp - cannon.hp
ok("it counts every point it takes", g.vars.get("harvest") == drained,
   f"harvest {g.vars.get('harvest')}, actually drained {drained}")
squares = m.legal_moves(g)
ghost_round(m, g, {"destination": squares[0], "action": {"key": "none"}})
ok("it takes flesh with exactly that much health",
   g.max_hp == drained and g.hp == drained, f"{g.hp}/{g.max_hp} from {drained} drained")

# a ghost that drained nothing still steps out alive
m, g, gate, cannon, d = ghost_arena()
g.vars["turns_done"] = 3
g.vars["haunting"] = cannon.id
squares = m.legal_moves(g)
ghost_round(m, g, {"destination": squares[0], "action": {"key": "none"}})
ok("one that drained nothing still has a point of life",
   g.alive and g.max_hp == 1, f"{g.hp}/{g.max_hp}")
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

# 42 — 男枪: a three-square arc, a ramp that caps, and a step after it connects
def gunner_arena():
    m = arena([("gunner", (3, 3))], [("dummy", (7, 3)), ("cannoneer", (7, 2)), ("gatekeeper", (7, 4))])
    g = unit(m, LEFT, "gunner")
    d, c, gk = (unit(m, RIGHT, k) for k in ("dummy", "cannoneer", "gatekeeper"))
    return m, g, d, c, gk

def spray(m, g, direction, foe, step=None):
    """Fire, then answer the follow-up if the shot earned one."""
    assert m.select_hero(LEFT, g.id) is None
    err = m.commit(LEFT, {"destination": None, "action": {"key": "attack", "direction": direction}})
    if err:
        return err
    m.select_hero(RIGHT, foe.id)
    m.commit(RIGHT, hold)
    while m.phase == "victim":
        for side in (LEFT, RIGHT):
            o = m.res["options"][side]
            if o and not m.victims_complete(side):
                m.choose_victim(side, o[0])
    if m.phase == "resolved":
        m.choose_followup(LEFT, step)
    # Play the rest of the round out so the gunner comes round again.
    guard = 0
    while m.phase == "commit" and m.unacted(RIGHT) and guard < 12:
        guard += 1
        r = m.unacted(RIGHT)[0]
        m.select_hero(RIGHT, r.id)
        m.commit(RIGHT, hold)
        while m.phase == "victim":
            for side in (LEFT, RIGHT):
                o = m.res["options"][side]
                if o and not m.victims_complete(side):
                    m.choose_victim(side, o[0])
        if m.phase == "resolved":
            m.choose_followup(LEFT, None)
    return None

m, g, d, c, gk = gunner_arena()
d.set_cell((4, 3)); c.set_cell((4, 2)); gk.set_cell((4, 4))     # the whole forward arc
m.select_hero(LEFT, g.id)
menu = {a["key"]: a for a in m.action_menu(g)}
ok("its attack asks for a direction", menu["attack"]["targeting"]["kind"] == "cone")
ok("and each direction offers a three-square arc",
   all(len(x["cells"]) == 3 for x in menu["attack"]["targeting"]["dirs"]
       if x["dir"] in ("forward", "up", "down")),
   str(menu["attack"]["targeting"]["dirs"][0]))

before = {x.name: x.hp for x in (d, c, gk)}
atk0 = g.atk
spray(m, g, "forward", d, step=None)
ok("every enemy in the arc takes the shot",
   all(before[x.name] - x.hp == atk0 for x in (d, c, gk)),
   str({x.name: before[x.name] - x.hp for x in (d, c, gk)}))
ok("a shot that connects raises its damage", g.atk - atk0 == 1, f"{atk0} -> {g.atk}")

# an enemy outside the arc is untouched
m, g, d, c, gk = gunner_arena()
d.set_cell((4, 3)); c.set_cell((5, 3))                          # c is two squares off
before_c = c.hp
spray(m, g, "forward", d, step=None)
ok("an enemy beyond the arc is untouched", c.hp == before_c, f"took {before_c - c.hp}")

# the ramp stops at +2, however many shots land
m, g, d, c, gk = gunner_arena()
d.set_cell((4, 3))
d.max_hp = d.hp = 200
base = g.hero.atk
seen = []
for _ in range(4):
    spray(m, g, "forward", d, step=None)
    seen.append(g.atk - base)
ok("the ramp climbs and then stops at +2", seen == [1, 2, 2, 2], str(seen))

# a shot that hits nothing earns neither ramp nor step
m, g, d, c, gk = gunner_arena()
d.set_cell((7, 3)); c.set_cell((7, 2)); gk.set_cell((7, 4))     # nobody near
atk0 = g.atk
spray(m, g, "forward", d, step=None)
ok("a shot that connects with nothing gives no ramp", g.atk == atk0, f"{atk0} -> {g.atk}")
ok("...and offers no step", m.phase != "resolved", m.phase)

# the step: offered after everything resolves, optional, and only onto free squares
m, g, d, c, gk = gunner_arena()
d.set_cell((4, 3))
m.select_hero(LEFT, g.id)
m.commit(LEFT, {"destination": None, "action": {"key": "attack", "direction": "forward"}})
m.select_hero(RIGHT, d.id); m.commit(RIGHT, hold)
ok("a hit pauses for the step, after the exchange has resolved", m.phase == "resolved", m.phase)
task = m.followups[LEFT][0]
ok("only free neighbours are offered",
   all(m.occupant(tuple(x)) is None for x in task["options"]) and task["optional"],
   str(task["options"]))
ok("a square nobody offered is refused", m.choose_followup(LEFT, [9, 1]) is not None)
where = task["options"][0]
ok("stepping there works", m.choose_followup(LEFT, where) is None)
ok("it moved, and play carries on", g.cell == tuple(where) and m.phase == "commit",
   f"{g.cell}, phase {m.phase}")

# and it can decline
m, g, d, c, gk = gunner_arena()
d.set_cell((4, 3))
m.select_hero(LEFT, g.id)
m.commit(LEFT, {"destination": None, "action": {"key": "attack", "direction": "forward"}})
m.select_hero(RIGHT, d.id); m.commit(RIGHT, hold)
was = g.cell
ok("the step can be declined", m.choose_followup(LEFT, None) is None)
ok("declining leaves it where it stood", g.cell == was and m.phase == "commit", str(g.cell))

# 43 — 魔术师: 转移 resolves with movement, so a swap redirects what was aimed
def magic_arena():
    m = arena([("magician", (3, 2)), ("gatekeeper", (3, 3)), ("cannoneer", (3, 1))],
              [("spearman", (7, 3)), ("dummy", (7, 1))])
    mag = unit(m, LEFT, "magician")
    mag.ap = mag.max_ap
    return (m, mag, unit(m, LEFT, "gatekeeper"), unit(m, LEFT, "cannoneer"),
            unit(m, RIGHT, "spearman"), unit(m, RIGHT, "dummy"))

swap = lambda a, b: {"destination": None,
                     "action": {"key": "ability:transfer", "first": a.id, "second": b.id}}

# the enemy marks the square 门神 stands on; we swap 炮手 into it
m, mag, gate, cannon, foe, d = magic_arena()
foe.set_cell((4, 3))
hp0 = {e.name: e.hp for e in (gate, cannon)}
turn(m, mag.id, swap(gate, cannon),
     foe.id, {"destination": None, "action": {"key": "attack", "shots": [[[3, 3]]]}})
ok("the swapped-in hero takes what was aimed at the square",
   hp0[cannon.name] - cannon.hp == foe.atk, f"炮手 took {hp0[cannon.name] - cannon.hp}")
ok("...and the hero pulled out of it takes nothing",
   gate.hp == hp0[gate.name], f"门神 took {hp0[gate.name] - gate.hp}")
ok("both actually changed places", gate.cell == (3, 1) and cannon.cell == (3, 3),
   f"{gate.cell} {cannon.cell}")

# it can swap two enemies with each other
m, mag, gate, cannon, foe, d = magic_arena()
a, b = foe.cell, d.cell
turn(m, mag.id, swap(foe, d), foe.id, hold)
ok("it can rearrange the enemy's own line", foe.cell == b and d.cell == a,
   f"{foe.cell} {d.cell}")

# a unit-locked attack follows its target through the swap
m, mag, gate, cannon, foe, d = magic_arena()
before = gate.hp
turn(m, mag.id, swap(gate, cannon),
     d.id, {"destination": None, "action": {"key": "attack", "target": gate.id}})
ok("a unit-locked attack still finds its target after the swap",
   before - gate.hp == d.atk, f"took {before - gate.hp}")

# and it moves and casts in the same turn
m, mag, gate, cannon, foe, d = magic_arena()
turn(m, mag.id, dict(swap(gate, cannon), destination=[4, 2]), foe.id, hold)
ok("the magician moves and casts in one turn", mag.cell == (4, 2), str(mag.cell))

m, mag, gate, cannon, foe, d = magic_arena()
m.select_hero(LEFT, mag.id)
ok("swapping a unit with itself is refused",
   m.commit(LEFT, swap(gate, gate)) is not None)

# 44 — 转移 against an aimed attack: no friendly fire, so putting an enemy in the
# marked square makes the shot find nobody at all
m = arena([("magician", (3, 2)), ("gatekeeper", (3, 3))], [("spearman", (7, 3)), ("dummy", (7, 1))])
mag = unit(m, LEFT, "magician"); mag.ap = mag.max_ap
gate = unit(m, LEFT, "gatekeeper")
sp, d = unit(m, RIGHT, "spearman"), unit(m, RIGHT, "dummy")
sp.set_cell((4, 3))                      # 枪兵 marks C3, where 门神 stands
b_gate, b_d = gate.hp, d.hp
turn(m, mag.id, {"destination": None,
                 "action": {"key": "ability:transfer", "first": d.id, "second": gate.id}},
     sp.id, {"destination": None, "action": {"key": "attack", "shots": [[[3, 3]]]}})
ok("swapping an enemy into their own marked square voids the shot",
   d.cell == (3, 3) and d.hp == b_d, f"木桩 at {d.cell}, took {b_d - d.hp}")
ok("...and the hero it replaced walks away clean", gate.hp == b_gate,
   f"门神 took {b_gate - gate.hp}")

# swapping one of your own in instead just changes who takes it
m = arena([("magician", (3, 2)), ("gatekeeper", (3, 3)), ("cannoneer", (3, 1))],
          [("spearman", (7, 3))])
mag = unit(m, LEFT, "magician"); mag.ap = mag.max_ap
gate, can = unit(m, LEFT, "gatekeeper"), unit(m, LEFT, "cannoneer")
sp = unit(m, RIGHT, "spearman"); sp.set_cell((4, 3))
b = can.hp
turn(m, mag.id, {"destination": None,
                 "action": {"key": "ability:transfer", "first": can.id, "second": gate.id}},
     sp.id, {"destination": None, "action": {"key": "attack", "shots": [[[3, 3]]]}})
ok("swapping your own hero in takes the hit for the other",
   can.cell == (3, 3) and b - can.hp == sp.atk, f"炮手 took {b - can.hp}")

# 45 — 转移 loose ends
m = arena([("magician", (3, 2)), ("ghost", (3, 3))], [("dummy", (7, 3))])
mag, gh, d = unit(m, LEFT, "magician"), unit(m, LEFT, "ghost"), unit(m, RIGHT, "dummy")
mag.ap = mag.max_ap
m.select_hero(LEFT, mag.id)
ok("a hero with no square cannot be swapped",
   m.commit(LEFT, {"destination": None,
                   "action": {"key": "ability:transfer", "first": gh.id, "second": d.id}}) is not None)
ok("...and is not even offered",
   gh.id not in m.ability_targeting(mag, mag.abilities[0])["options"])

# it moves first, then swaps from where it ended up
m = arena([("magician", (3, 2)), ("gatekeeper", (3, 3))], [("dummy", (7, 3))])
mag, d = unit(m, LEFT, "magician"), unit(m, RIGHT, "dummy")
mag.ap = mag.max_ap
turn(m, mag.id, {"destination": [4, 2],
                 "action": {"key": "ability:transfer", "first": mag.id, "second": d.id}},
     d.id, hold)
ok("swapping itself uses where its own movement left it",
   mag.cell == (7, 3) and d.cell == (4, 2), f"{mag.cell} {d.cell}")

# two magicians swapping the same pair cancel out, deterministically
m = arena([("magician", (3, 2)), ("gatekeeper", (3, 3))],
          [("magician", (7, 2)), ("dummy", (7, 3))])
ml, gl = unit(m, LEFT, "magician"), unit(m, LEFT, "gatekeeper")
mr, dr = unit(m, RIGHT, "magician"), unit(m, RIGHT, "dummy")
ml.ap = ml.max_ap; mr.ap = mr.max_ap
where = {gl.id: gl.cell, dr.id: dr.cell}
both = {"key": "ability:transfer", "first": gl.id, "second": dr.id}
turn(m, ml.id, {"destination": None, "action": both},
     mr.id, {"destination": None, "action": both})
ok("two magicians undoing each other leaves the board as it was",
   gl.cell == where[gl.id] and dr.cell == where[dr.id], f"{gl.cell} {dr.cell}")

# the sealed slip names both units
m = arena([("magician", (3, 2)), ("gatekeeper", (3, 3))], [("dummy", (7, 3))])
mag, gate, d = unit(m, LEFT, "magician"), unit(m, LEFT, "gatekeeper"), unit(m, RIGHT, "dummy")
mag.ap = mag.max_ap
m.select_hero(LEFT, mag.id)
m.commit(LEFT, {"destination": None,
                "action": {"key": "ability:transfer", "first": gate.id, "second": d.id}})
slip = view.state_for(m, LEFT)["commit"]["orders"][0]
ok("the sealed order says who is being swapped",
   gate.name in slip["target"] and d.name in slip["target"], slip["target"])

# 46 — 剑齿虎: what it mauls cannot run, but can still fight
def tiger_arena(foe="gatekeeper"):
    m = arena([("sabretooth", (3, 3)), ("cannoneer", (3, 1))], [(foe, (7, 3)), ("dummy", (7, 1))])
    tig = unit(m, LEFT, "sabretooth")
    return m, tig, unit(m, RIGHT, foe), unit(m, RIGHT, "dummy")

m, tig, foe, d = tiger_arena()
foe.set_cell((4, 3))
ok("nobody is pinned to begin with", not m.rooted(foe))
turn(m, tig.id, {"destination": None, "action": {"key": "attack", "shots": [[[4, 3]]]}},
     d.id, hold)
ok("a mauled hero is pinned", m.rooted(foe))
ok("it can still be selected and can still fight", m.legal_moves(foe) == [] and foe.alive)
m.select_hero(RIGHT, foe.id)
ok("moving is refused while pinned",
   m.commit(RIGHT, {"destination": [5, 3], "action": {"key": "none"}}) is not None)
ok("but holding and attacking is fine",
   m.commit(RIGHT, {"destination": None,
                    "action": {"key": "attack", "shots": [[[3, 3]]]}}) is None)
ally = unit(m, LEFT, "cannoneer")            # the tiger has already acted this round
m.select_hero(LEFT, ally.id); m.commit(LEFT, hold)
while m.phase == "victim":
    for side in (LEFT, RIGHT):
        o = m.res["options"][side]
        if o and not m.victims_complete(side):
            m.choose_victim(side, o[0])
ok("the pin is spent once that turn is over", not m.rooted(foe))
ok("...and it can move again", m.legal_moves(foe) != [])

# damage that is turned aside pins nobody
m, tig, foe, d = tiger_arena("paladin")
foe.set_cell((4, 3))
foe.vars["aegis_spent"] = True               # its shield is already up: the claw does nothing
before = foe.hp
turn(m, tig.id, {"destination": None, "action": {"key": "attack", "shots": [[[4, 3]]]}},
     d.id, hold)
ok("a blow that is turned aside pins nobody",
   foe.hp == before and not m.rooted(foe), f"took {before - foe.hp}, rooted={m.rooted(foe)}")

# it stops ability movement too — a rooted 半人马 cannot charge
m = arena([("sabretooth", (3, 3)), ("cannoneer", (3, 1))], [("centaur", (7, 3)), ("dummy", (7, 1))])
tig, cen, d = unit(m, LEFT, "sabretooth"), unit(m, RIGHT, "centaur"), unit(m, RIGHT, "dummy")
cen.set_cell((4, 3)); cen.ap = cen.max_ap
turn(m, tig.id, {"destination": None, "action": {"key": "attack", "shots": [[[4, 3]]]}},
     d.id, hold)
ok("a pinned 半人马 is not offered its charge",
   "ability:charge" not in [a["key"] for a in m.action_menu(cen)],
   str([a["key"] for a in m.action_menu(cen)]))
m.select_hero(RIGHT, cen.id)
ok("...and committing one is refused",
   m.commit(RIGHT, {"destination": None,
                    "action": {"key": "ability:charge", "direction": "forward"}}) is not None)

# but 转移 can still move it — that is somebody else doing the moving
m = arena([("sabretooth", (3, 3)), ("magician", (3, 1))], [("gatekeeper", (7, 3)), ("dummy", (7, 1))])
tig, mag = unit(m, LEFT, "sabretooth"), unit(m, LEFT, "magician")
gk, d = unit(m, RIGHT, "gatekeeper"), unit(m, RIGHT, "dummy")
mag.ap = mag.max_ap
gk.set_cell((4, 3))
turn(m, tig.id, {"destination": None, "action": {"key": "attack", "shots": [[[4, 3]]]}},
     d.id, hold)
ok("the pinned hero is still pinned", m.rooted(gk))
was = gk.cell
turn(m, mag.id, {"destination": None,
                 "action": {"key": "ability:transfer", "first": gk.id, "second": d.id}},
     gk.id, hold)
ok("being swapped moves it anyway — that is not its own movement",
   gk.cell != was, f"{was} -> {gk.cell}")

# 47 — 再咒: the doll may lay another curse, but only once the last one sprang
def doll_arena():
    m = arena([("cursed_doll", (3, 3)), ("gatekeeper", (3, 1)), ("cannoneer", (3, 2))],
              [("spearman", (7, 1)), ("dummy", (7, 3))])
    doll = unit(m, LEFT, "cursed_doll")
    gate, can = unit(m, LEFT, "gatekeeper"), unit(m, LEFT, "cannoneer")
    sp, d = unit(m, RIGHT, "spearman"), unit(m, RIGHT, "dummy")
    assert m.opening_choose(LEFT, {"target": gate.id}) is None
    return m, doll, gate, can, sp, d

m, doll, gate, can, sp, d = doll_arena()
doll.ap = doll.max_ap
ok("再咒 is hidden while the first curse still sits unsprung",
   "ability:recurse" not in [a["key"] for a in m.action_menu(doll)],
   str([a["key"] for a in m.action_menu(doll)]))
m.select_hero(LEFT, doll.id)
ok("...and committing it is refused",
   m.commit(LEFT, {"destination": None,
                   "action": {"key": "ability:recurse", "target": can.id}}) is not None)

# spring it
DMG.apply_batch(m, [DMG.DamageEvent(source=sp, target=gate, amount=4,
                                    category=DMG.NORMAL_ATTACK)])
ok("springing the curse frees the doll to lay another",
   "ability:recurse" in [a["key"] for a in m.action_menu(doll)])
ok("...and the mark it was holding is gone", not gate.vars.get("curse_mark"))

before_ap = doll.ap
turn(m, doll.id, {"destination": None,
                  "action": {"key": "ability:recurse", "target": can.id}},
     d.id, hold)
ok("再咒 costs its 2 AP", before_ap - doll.ap == 2 - 1, f"{before_ap} -> {doll.ap} (+1 at turn end)")
ok("the new ally carries the mark", can.vars.get("curse_mark") == doll.id)
ok("and 再咒 is hidden again until this one springs",
   "ability:recurse" not in [a["key"] for a in m.action_menu(doll)])

# the fresh curse bites just like the first
d.vars["frozen_at_round"] = None
DMG.apply_batch(m, [DMG.DamageEvent(source=d, target=can, amount=3,
                                    category=DMG.NORMAL_ATTACK)])
ok("the replacement curse freezes whoever springs it",
   d.vars.get("frozen_at_round") is not None)
ok("and the cycle can begin again",
   "ability:recurse" in [a["key"] for a in m.action_menu(doll)])

# 48 — 大力士: take hold on your turn, throw once the exchange has settled
def strong_arena():
    m = arena([("strongman", (3, 3)), ("cannoneer", (3, 1))],
              [("gatekeeper", (7, 3)), ("dummy", (7, 1))])
    st = unit(m, LEFT, "strongman"); st.ap = st.max_ap
    return m, st, unit(m, RIGHT, "gatekeeper"), unit(m, RIGHT, "dummy")

slam = lambda t: {"destination": None, "action": {"key": "ability:slam", "target": t.id}}
SLAM = HEROES.Slam.DAMAGE       # read, not repeated: this number gets rebalanced
REACH = HEROES.Slam.REACH

def slam_task(m, side=LEFT):
    return next((t for t in m.followups[side] if t["key"] == HEROES.Slam.THROW), None)

# the grab itself does nothing at all
m, st, gk, d = strong_arena()
gk.set_cell((4, 3))                                  # east of the strongman at C3
before, where = gk.hp, gk.cell
turn(m, st.id, slam(gk), d.id, hold)
ok("the grab takes nothing off", gk.hp == before, f"took {before - gk.hp}")
ok("...and moves nobody yet", gk.cell == where, str(gk.cell))
ok("the throw is asked for once the exchange has settled",
   m.phase == "resolved" and slam_task(m) is not None, m.phase)

task = slam_task(m)
ok("...offering every square within reach of where it ended up, and only free ones",
   all(m.topology.distance(tuple(c), st.cell) <= REACH for c in task["options"])
   and all(m.occupant(tuple(c)) in (None, gk) for c in task["options"]),
   str(len(task["options"])))
ok("...including one a full three squares away",
   any(m.topology.distance(tuple(c), st.cell) == REACH for c in task["options"]))
far = max(task["options"], key=lambda c: m.topology.distance(tuple(c), st.cell))
ok("the throw is accepted", m.choose_followup(LEFT, far) is None)
ok("...and lands the enemy where you put it", gk.cell == tuple(far), str(gk.cell))
ok("...for its damage", before - gk.hp == SLAM, f"took {before - gk.hp}")

# one of your own may be seized, and takes nothing for it
m, st, gk, d = strong_arena()
can = unit(m, LEFT, "cannoneer")
can.set_cell((3, 2))                                 # right beside the strongman
hp0 = can.hp
turn(m, st.id, slam(can), d.id, hold)
task = slam_task(m)
ok("it may take hold of one of its own", task is not None)
m.choose_followup(LEFT, task["options"][-1])
ok("...and sets it down unhurt", can.hp == hp0, f"took {hp0 - can.hp}")

# two squares away is out of reach
m, st, gk, d = strong_arena()
gk.set_cell((5, 3))
m.select_hero(LEFT, st.id)
ok("it cannot reach past the squares around it", m.commit(LEFT, slam(gk)) is not None)
ok("...and that hero is not offered",
   gk.id not in m.ability_targeting(st, st.abilities[0])["options"])

# the grip holds wherever the target walks off to
m, st, gk, d = strong_arena()
gk.set_cell((4, 3))
turn(m, st.id, slam(gk), gk.id, {"destination": [5, 3], "action": {"key": "none"}})
ok("walking away does not shake the grip", gk.cell == (5, 3) and slam_task(m) is not None,
   f"{gk.cell} {slam_task(m) is not None}")
task = slam_task(m)
ok("...and the reach is measured from the thrower, not the caught",
   all(m.topology.distance(tuple(c), st.cell) <= REACH for c in task["options"]))
m.choose_followup(LEFT, task["options"][0])
ok("...so it is still thrown", gk.cell == tuple(task["options"][0]), str(gk.cell))

# a death between the grab and the throw ends it
m, st, gk, d = strong_arena()
gk.set_cell((4, 3)); gk.hp = 1
turn(m, st.id, slam(gk),
     d.id, {"destination": None, "action": {"key": "none"}})
DMG.apply_batch(m, [DMG.DamageEvent(source=None, target=gk, amount=9,
                                    category=DMG.ABILITY)])
ok("a target that dies first is simply let go", not gk.alive)

# the throw lands after the exchange, but 教皇 still gets to step in front of it
m = arena([("strongman", (3, 3)), ("cannoneer", (3, 1))],
          [("pope", (7, 3)), ("dummy", (7, 1))])
st = unit(m, LEFT, "strongman"); st.ap = st.max_ap
pope, d = unit(m, RIGHT, "pope"), unit(m, RIGHT, "dummy")
d.set_cell((4, 3)); d.hp = 2                         # the throw would finish it
turn(m, st.id, slam(d), pope.id, hold)
task = slam_task(m)
m.choose_followup(LEFT, task["options"][0])
ok("a killing throw stops the board for a save",
   m.phase == "interrupt"
   and any(t["key"] == "death_save" for t in m.interrupts), m.phase)
m.choose_interrupt(RIGHT, True)
guard = 0
while m.phase == "interrupt" and m.interrupts and guard < 8:
    guard += 1
    t = m.interrupts[0]
    m.choose_interrupt(t["side"], t["options"][0] if t.get("options") else True)
ok("...and 教皇 saves it from a throw as from anything else", d.alive, f"{d.hp}")
ok("...then the board carries on", m.phase == "commit", m.phase)

# without one, it simply falls
m = arena([("strongman", (3, 3)), ("cannoneer", (3, 1))],
          [("gatekeeper", (7, 3)), ("dummy", (7, 1))])
st = unit(m, LEFT, "strongman"); st.ap = st.max_ap
gk, d = unit(m, RIGHT, "gatekeeper"), unit(m, RIGHT, "dummy")
gk.set_cell((4, 3)); gk.hp = 2
turn(m, st.id, slam(gk), d.id, hold)
m.choose_followup(LEFT, slam_task(m)["options"][0])
ok("with nobody to step in, the throw finishes it", not gk.alive)

# 49 — 长老: one blow turned aside, and swifter until it is
def elder_arena():
    m = arena([("elder", (3, 3)), ("gatekeeper", (3, 1))], [("cannoneer", (7, 3)), ("dummy", (7, 1))])
    el = unit(m, LEFT, "elder"); el.ap = el.max_ap
    return m, el, unit(m, LEFT, "gatekeeper"), unit(m, RIGHT, "cannoneer"), unit(m, RIGHT, "dummy")

bless = lambda t: {"destination": None, "action": {"key": "ability:bless", "target": t.id}}

m, el, gate, cannon, d = elder_arena()
move0 = gate.move_allowance
turn(m, el.id, bless(gate), d.id, hold)
ok("the blessed hero carries the ward", gate.vars.get("blessed") == el.id)
ok("...and moves one square further while it holds",
   gate.move_allowance - move0 == 1, f"{move0} -> {gate.move_allowance}")

hp0 = gate.hp
DMG.apply_batch(m, [DMG.DamageEvent(source=cannon, target=gate, amount=6,
                                    category=DMG.NORMAL_ATTACK)])
ok("the first blow that would hurt it is turned aside", gate.hp == hp0, f"took {hp0 - gate.hp}")
ok("the blessing is spent by it", not gate.vars.get("blessed"))
ok("...and the extra movement goes with it",
   gate.move_allowance == move0, f"{gate.move_allowance}")
DMG.apply_batch(m, [DMG.DamageEvent(source=cannon, target=gate, amount=6,
                                    category=DMG.NORMAL_ATTACK)])
ok("the next blow lands in full", hp0 - gate.hp == 6, f"took {hp0 - gate.hp}")

# an ability is warded off just the same
m, el, gate, cannon, d = elder_arena()
turn(m, el.id, bless(gate), d.id, hold)
hp0 = gate.hp
DMG.apply_batch(m, [DMG.DamageEvent(source=cannon, target=gate, amount=8,
                                    category=DMG.ABILITY)])
ok("an ability is turned aside too", gate.hp == hp0 and not gate.vars.get("blessed"))

# burning ground is neither stopped by it nor spends it
m, el, gate, cannon, d = elder_arena()
turn(m, el.id, bless(gate), d.id, hold)
burn = m.board.add_burning(gate.cell, RIGHT).damage
hp0 = gate.hp
m.select_hero(LEFT, gate.id); m.commit(LEFT, hold)
ok("burning ground still bites through a blessing",
   hp0 - gate.hp == burn, f"took {hp0 - gate.hp}, tile deals {burn}")
ok("...and the blessing is still there for a real blow", gate.vars.get("blessed") == el.id)

# one to a hero, but several heroes at once
m, el, gate, cannon, d = elder_arena()
turn(m, el.id, bless(gate), d.id, hold)
m.select_hero(LEFT, el.id)
ok("an already-blessed hero cannot take a second", m.commit(LEFT, bless(gate)) is not None)
ok("...and is not offered", gate.id not in m.ability_targeting(el, el.abilities[0])["options"])
ok("but the elder itself still can be", el.id in m.ability_targeting(el, el.abilities[0])["options"])
m.deselect(LEFT)
turn(m, gate.id, hold, cannon.id, hold)      # close the round out
el.ap = el.max_ap
turn(m, el.id, bless(el), d.id, hold)
ok("two allies can carry blessings at once",
   gate.vars.get("blessed") and el.vars.get("blessed"),
   f"gate={bool(gate.vars.get('blessed'))} elder={bool(el.vars.get('blessed'))}")

# 50 — 剑客: one cut down a whole rank, and everyone caught is easier to kill after
def sword_arena():
    m = arena([("swordsman", (3, 3)), ("gatekeeper", (1, 1))],
              [("dummy", (7, 3)), ("cannoneer", (7, 2)), ("gatekeeper", (7, 1))])
    sw = unit(m, LEFT, "swordsman"); sw.ap = sw.max_ap
    return (m, sw, unit(m, LEFT, "gatekeeper"), unit(m, RIGHT, "dummy"),
            unit(m, RIGHT, "cannoneer"), unit(m, RIGHT, "gatekeeper"))

cut = lambda which: {"destination": None,
                     "action": {"key": "ability:gale_slash", "direction": which}}

# the row runs the whole width of the board, however far away they are standing
m, sw, ally, d, cannon, gate = sword_arena()
gate.set_cell((5, 3))                                 # 30 HP, so it lives to be poked at
d.set_cell((7, 1))                                    # out of the row entirely
hp0 = (gate.hp, cannon.hp, d.hp)
turn(m, sw.id, cut("row"), cannon.id, hold)
ok("the cut catches everyone in the row, right across the board",
   hp0[0] - gate.hp == 5, f"took {hp0[0] - gate.hp}")
ok("...and nobody outside it",
   (cannon.hp, d.hp) == hp0[1:], f"{hp0[1:]} -> {(cannon.hp, d.hp)}")
ok("the first cut lands clean — the mark is what it leaves behind",
   gate.vars.get("vulnerable") == 1, str(gate.vars.get("vulnerable")))

# and that mark makes everything afterwards land harder
hp0 = gate.hp
DMG.apply_batch(m, [DMG.DamageEvent(source=sw, target=gate, amount=3,
                                    category=DMG.NORMAL_ATTACK)])
ok("every later blow lands 1 harder", hp0 - gate.hp == 4, f"took {hp0 - gate.hp}")
hp0 = gate.hp
DMG.apply_batch(m, [DMG.DamageEvent(source=sw, target=gate, amount=2, category=DMG.TILE)])
ok("...burning ground included", hp0 - gate.hp == 3, f"took {hp0 - gate.hp}")
hp0 = cannon.hp
DMG.apply_batch(m, [DMG.DamageEvent(source=sw, target=cannon, amount=3,
                                    category=DMG.NORMAL_ATTACK)])
ok("an unmarked hero takes what it always did", hp0 - cannon.hp == 3, f"took {hp0 - cannon.hp}")

# the column is the other half of the choice
m, sw, ally, d, cannon, gate = sword_arena()
cannon.set_cell((3, 1)); gate.set_cell((3, 5))       # both share the swordsman's column
hp0 = (cannon.hp, gate.hp, d.hp)
turn(m, sw.id, cut("column"), d.id, hold)
ok("cutting the column catches its whole height",
   (hp0[0] - cannon.hp, hp0[1] - gate.hp) == (5, 5),
   f"{hp0[0] - cannon.hp}, {hp0[1] - gate.hp}")
ok("...and leaves the row alone", d.hp == hp0[2], f"took {hp0[2] - d.hp}")
ok("both come away marked",
   (cannon.vars.get("vulnerable"), gate.vars.get("vulnerable")) == (1, 1))

# allies standing in the line are not touched by it
m, sw, ally, d, cannon, gate = sword_arena()
ally.set_cell((5, 3))                                 # in the swordsman's row
hp0 = ally.hp
turn(m, sw.id, cut("row"), cannon.id, hold)
ok("its own line walks through the cut untouched",
   ally.hp == hp0 and not ally.vars.get("vulnerable"), f"took {hp0 - ally.hp}")

# the line is drawn from where it ends up, not from where it set off
m, sw, ally, d, cannon, gate = sword_arena()
d.set_cell((6, 2))                                    # a row the swordsman is not in
hp0 = d.hp
turn(m, sw.id, {"destination": [3, 2],
                "action": {"key": "ability:gale_slash", "direction": "row"}},
     cannon.id, hold)
ok("the cut is drawn from the square it reaches, not the one it left",
   hp0 - d.hp == 5 and sw.cell == (3, 2), f"took {hp0 - d.hp} from {sw.cell}")

# the marks stack, and a second cut is amplified by the first one's mark
m, sw, ally, d, cannon, gate = sword_arena()
gate.set_cell((5, 3))                                 # 30 HP, so it survives both
turn(m, sw.id, cut("row"), cannon.id, hold)
hp0 = gate.hp
r0 = m.round
while m.round == r0:                                  # close the round out
    left = [e.id for e in m.unacted(LEFT)]
    right = [e.id for e in m.unacted(RIGHT)]
    turn(m, left[0] if left else None, hold, right[0] if right else None, hold)
sw.ap = sw.max_ap
turn(m, sw.id, cut("row"), d.id, hold)
ok("a second cut is itself sharpened by the first one's mark",
   hp0 - gate.hp == 6, f"took {hp0 - gate.hp}")
ok("...and the marks stack", gate.vars.get("vulnerable") == 2,
   str(gate.vars.get("vulnerable")))

# a hero the cut kills is not marked on the way down
m, sw, ally, d, cannon, gate = sword_arena()
d.hp = 4
turn(m, sw.id, cut("row"), cannon.id, hold)
ok("nothing is marked once it is dead",
   not d.alive and not d.vars.get("vulnerable"),
   f"alive={d.alive} mark={d.vars.get('vulnerable')}")

# 石像鬼's stone still only chips: the cap sits after the mark, so it wins
m = arena([("swordsman", (3, 3)), ("gatekeeper", (1, 1))], [("gargoyle", (7, 3))])
sw = unit(m, LEFT, "swordsman"); sw.ap = sw.max_ap
garg = unit(m, RIGHT, "gargoyle")
turn(m, sw.id, cut("row"), garg.id, hold)
ok("an ability cuts through stone in full", garg.vars.get("vulnerable") == 1)
hp0 = garg.hp
DMG.apply_batch(m, [DMG.DamageEvent(source=sw, target=garg, amount=6,
                                    category=DMG.NORMAL_ATTACK)])
ok("but a normal attack still only chips it, marked or not",
   hp0 - garg.hp == 1, f"took {hp0 - garg.hp}")

# the line has to be named, and it costs the whole bar
m, sw, ally, d, cannon, gate = sword_arena()
m.select_hero(LEFT, sw.id)
ok("it must be told which line to cut", m.commit(LEFT, cut("diagonal")) is not None)
ok("row and column are the only two offered",
   [c["dir"] for c in m.ability_targeting(sw, sw.abilities[0])["choices"]] == ["row", "column"])
m.deselect(LEFT)
m, sw, ally, d, cannon, gate = sword_arena()
sw.ap = 2
entry = next(a for a in m.action_menu(sw) if a["key"] == "ability:gale_slash")
ok("two AP is not enough for it — the menu says so", not entry["affordable"])
m.select_hero(LEFT, sw.id)
ok("...and committing it anyway is refused", m.commit(LEFT, cut("row")) is not None)
m.deselect(LEFT)
sw.ap = sw.max_ap
before_ap = sw.ap
turn(m, sw.id, cut("row"), cannon.id, hold)
ok("a cut costs the whole bar", before_ap - sw.ap == 3 - 1,
   f"{before_ap} -> {sw.ap} (+1 at turn end)")

# 51 — 炸弹客: spends itself all at once, in one of three shapes
def bomb_arena():
    m = arena([("bomber", (3, 3)), ("gatekeeper", (1, 1))],
              [("gatekeeper", (7, 3)), ("cannoneer", (7, 2)), ("dummy", (7, 1))])
    bo = unit(m, LEFT, "bomber"); bo.ap = bo.max_ap
    return (m, bo, unit(m, LEFT, "gatekeeper"), unit(m, RIGHT, "gatekeeper"),
            unit(m, RIGHT, "cannoneer"), unit(m, RIGHT, "dummy"))

blast = lambda which: {"destination": None,
                       "action": {"key": "ability:self_destruct", "direction": which}}

# the row: 6 to everyone in it, and the bomber is gone
m, bo, ally, gate, cannon, d = bomb_arena()
hp0 = (gate.hp, cannon.hp)
turn(m, bo.id, blast("row"), cannon.id, hold)
ok("the blast takes everyone in the row for 6", hp0[0] - gate.hp == 6, f"took {hp0[0] - gate.hp}")
ok("...and nobody outside it", cannon.hp == hp0[1], f"took {hp0[1] - cannon.hp}")
ok("the bomber spends itself", not bo.alive and bo.hp == 0, f"{bo.hp} hp, alive={bo.alive}")

# the column
m, bo, ally, gate, cannon, d = bomb_arena()
cannon.set_cell((3, 1)); gate.set_cell((3, 5))
hp0 = (cannon.hp, gate.hp, d.hp)
turn(m, bo.id, blast("column"), d.id, hold)
ok("the column catches its whole height",
   (hp0[0] - cannon.hp, hp0[1] - gate.hp) == (6, 6), f"{hp0[0] - cannon.hp}, {hp0[1] - gate.hp}")
ok("...and leaves the rest of the board alone", d.hp == hp0[2])

# the 8 around it — diagonals included, and nothing further out
m, bo, ally, gate, cannon, d = bomb_arena()
gate.set_cell((4, 4)); cannon.set_cell((4, 3)); d.set_cell((5, 3))
hp0 = (gate.hp, cannon.hp, d.hp)
turn(m, bo.id, blast("surround8"), d.id, hold)
ok("the ring catches a diagonal neighbour", hp0[0] - gate.hp == 6, f"took {hp0[0] - gate.hp}")
ok("...and the one straight beside it", hp0[1] - cannon.hp == 6, f"took {hp0[1] - cannon.hp}")
ok("...but not the one two squares out", d.hp == hp0[2], f"took {hp0[2] - d.hp}")

# its own line walks away
m, bo, ally, gate, cannon, d = bomb_arena()
ally.set_cell((4, 3))
hp0 = ally.hp
turn(m, bo.id, blast("row"), cannon.id, hold)
ok("allies standing in the blast are untouched", ally.hp == hp0, f"took {hp0 - ally.hp}")

# the shape is drawn from where it ends up — move 2 is what makes it a threat
m, bo, ally, gate, cannon, d = bomb_arena()
gate.set_cell((6, 4)); cannon.set_cell((5, 2)); d.set_cell((1, 1))
hp0 = (gate.hp, cannon.hp)
turn(m, bo.id, {"destination": [5, 3],
                "action": {"key": "ability:self_destruct", "direction": "surround8"}},
     d.id, hold)
ok("it walks two squares in and blows up there",
   (hp0[0] - gate.hp, hp0[1] - cannon.hp) == (6, 6), f"{hp0[0] - gate.hp}, {hp0[1] - cannon.hp}")

# 体力降至0 is a setting, not a blow: nothing wards it, softens it or is marked by it
m, bo, ally, gate, cannon, d = bomb_arena()
bo.vars["damage_reduction"] = 99
bo.vars["blessed"] = ally.id
turn(m, bo.id, blast("row"), cannon.id, hold)
ok("no ward or reduction keeps the bomber standing", not bo.alive and bo.hp == 0)
ok("...and the blessing is not spent putting it out", bo.vars.get("blessed") == ally.id)

# killed in the same breath, the fuse still burns — simultaneity, same as mutual kills
m, bo, ally, gate, cannon, d = bomb_arena()
gate.set_cell((4, 3)); bo.hp = 3
hp0 = gate.hp
turn(m, bo.id, blast("row"),
     gate.id, {"destination": None, "action": {"key": "attack", "shots": [[[3, 3]]]}})
ok("a bomber cut down in the same instant still goes off",
   hp0 - gate.hp == 6 and not bo.alive, f"took {hp0 - gate.hp}, bomber alive={bo.alive}")

# all three shapes are offered, and it costs the whole bar
m, bo, ally, gate, cannon, d = bomb_arena()
ok("all three shapes are on offer",
   [c["dir"] for c in m.ability_targeting(bo, bo.abilities[0])["choices"]]
   == ["row", "column", "surround8"])
m.select_hero(LEFT, bo.id)
ok("a shape it does not have is refused", m.commit(LEFT, blast("diagonal")) is not None)
m.deselect(LEFT)
m, bo, ally, gate, cannon, d = bomb_arena()
bo.ap = 2
entry = next(a for a in m.action_menu(bo) if a["key"] == "ability:self_destruct")
ok("two AP is not enough to set it off", not entry["affordable"])

# 52 — 蛇帝: one creature on two squares, head leading and tail following
def snake_arena(right=(("gatekeeper", (7, 3)), ("dummy", (7, 1)))):
    m = arena([("snake_head", (3, 3)), ("snake_tail", (3, 4))], list(right))
    hd, tl = unit(m, LEFT, "snake_head"), unit(m, LEFT, "snake_tail")
    return (m, hd, tl) + tuple(unit(m, RIGHT, k) for k, _ in right)

def snake_turn(m, hd, tl, head_order, tail_order, rs=None, ra=None):
    """A gang turn: one order per body, head first."""
    assert m.select_hero(LEFT, hd.id) is None
    assert m.commit(LEFT, {"orders": [dict(head_order, entity=hd.id),
                                      dict(tail_order, entity=tl.id)]}) is None, "head+tail order"
    if rs is not None:
        assert m.select_hero(RIGHT, rs) is None
        assert m.commit(RIGHT, ra) is None
    guard = 0
    while m.phase == "victim":
        guard += 1
        assert guard < 20
        for side in (LEFT, RIGHT):
            opts = m.res["options"][side]
            if opts and not m.victims_complete(side):
                m.choose_victim(side, opts[0])

stay = lambda c: {"destination": list(c), "action": {"key": "none"}}
bite = lambda c, cells: {"destination": list(c), "action": {"key": "attack", "shots": [cells]}}

# picking either half brings both — it is one turn
m, hd, tl, gate, d = snake_arena()
ok("picking the head brings the tail into the same turn",
   sorted(e.id for e in m.turn_actors(hd)) == sorted([hd.id, tl.id]))
ok("...and picking the tail does the same",
   sorted(e.id for e in m.turn_actors(tl)) == sorted([hd.id, tl.id]))

# one HP pool: a blow to the tail wounds the snake
m, hd, tl, gate, d = snake_arena()
ok("both halves start on the same 25", (hd.hp, tl.hp) == (25, 25), f"{hd.hp}/{tl.hp}")
DMG.apply_batch(m, [DMG.DamageEvent(source=gate, target=tl, amount=6,
                                    category=DMG.NORMAL_ATTACK)])
ok("a blow to the tail comes off the shared pool", hd.hp == 19, f"head {hd.hp}")
ok("...and the tail reads the same", tl.hp == 19, f"tail {tl.hp}")
DMG.apply_batch(m, [DMG.DamageEvent(source=gate, target=hd, amount=4,
                                    category=DMG.NORMAL_ATTACK)])
ok("a blow to the head comes off the same pool too", (hd.hp, tl.hp) == (15, 15),
   f"{hd.hp}/{tl.hp}")

# kill it once and the whole snake goes
m, hd, tl, gate, d = snake_arena()
DMG.apply_batch(m, [DMG.DamageEvent(source=gate, target=tl, amount=25,
                                    category=DMG.NORMAL_ATTACK)])
ok("25 through the tail kills the whole snake", not hd.alive and not tl.alive,
   f"head alive={hd.alive} tail alive={tl.alive}")
ok("...and both squares are cleared", not hd.cells and not tl.cells)
m.check_victory()
ok("it counted as one hero, so that is the match", m.phase == "gameover", m.phase)

# the bite leaves venom, which slows the victim's next turn and then is done
m, hd, tl, gate, d = snake_arena()
gate.set_cell((4, 3))                                  # in reach of the head (rng 1)
hp0, move0 = gate.hp, gate.move_allowance
snake_turn(m, hd, tl, bite((3, 3), [[4, 3]]), stay((3, 4)), d.id, hold)
ok("the bite lands", hp0 - gate.hp == 3, f"took {hp0 - gate.hp}")
ok("...and leaves venom", gate.vars.get("rooted_tag") == "venom",
   str(gate.vars.get("rooted_tag")))
ok("...which costs no health of its own", gate.hp == hp0 - 3, f"{gate.hp}")

# it has not taken its turn yet this round, so that turn is the slowed one
ok("the venom is already in force for the turn it has coming",
   m.move_budget(gate) == move0 - 1, f"{move0} -> {m.move_budget(gate)}")
ok("...without touching the stat itself — it is the walk that is short",
   gate.move_allowance == move0, f"{gate.move_allowance}")
before = gate.hp
assert m.select_hero(RIGHT, gate.id) is None
ok("...so its walk is a square shorter when it is picked up",
   m.move_budget(gate) == move0 - 1, f"budget {m.move_budget(gate)}")
m.commit(RIGHT, hold)
for e in m.unacted(LEFT)[:1]:
    m.select_hero(LEFT, e.id); m.commit(LEFT, hold)
ok("venom takes nothing off in health", before == gate.hp, f"took {before - gate.hp}")
ok("...and once that turn is over it is spent", m.move_budget(gate) == move0,
   f"{m.move_budget(gate)}")
ok("...leaving no mark behind", gate.vars.get("rooted_at") is None,
   str(gate.vars.get("rooted_tag")))

# only an unvenomed hero can be given a dose — no refresh
m, hd, tl, gate, d = snake_arena()
gate.set_cell((4, 3))
m.root(gate, squares=1, tag="venom")
stamp0 = gate.vars["rooted_at"]
snake_turn(m, hd, tl, bite((3, 3), [[4, 3]]), stay((3, 4)), d.id, hold)
ok("a hero already envenomed takes no second dose",
   gate.vars.get("rooted_at") == stamp0, str(gate.vars.get("rooted_at")))

# the pincer: head and tail on the same victim, +1 on the tail's blow
m, hd, tl, gate, d = snake_arena()
gate.set_cell((4, 3))
hp0 = gate.hp
snake_turn(m, hd, tl, bite((3, 3), [[4, 3]]), bite((3, 4), [[4, 3]]), d.id, hold)
ok("head 3 + tail 3 + 1 for the pincer", hp0 - gate.hp == 7, f"took {hp0 - gate.hp}")

# on different victims there is no bonus
m, hd, tl, gate, d = snake_arena()
gate.set_cell((4, 3)); d.set_cell((5, 4))
hp0 = (gate.hp, d.hp)
snake_turn(m, hd, tl, bite((3, 3), [[4, 3]]), bite((3, 4), [[5, 4]]), gate.id, hold)
ok("two different victims each take a plain 3",
   (hp0[0] - gate.hp, hp0[1] - d.hp) == (3, 3),
   f"{hp0[0] - gate.hp}, {hp0[1] - d.hp}")

# the tail reaches 3 squares where the head reaches 1
m, hd, tl, gate, d = snake_arena()
ok("the head reaches 1 and marks 2", (hd.rng, hd.grid) == (1, 2), f"{hd.rng}/{hd.grid}")
ok("the tail reaches 3 and marks 3", (tl.rng, tl.grid) == (3, 3), f"{tl.rng}/{tl.grid}")

# the tail goes where the head goes — it must end up beside it
m, hd, tl, gate, d = snake_arena()
zone = [tuple(c) for c in m.legal_moves(tl, {hd.id: (4, 3)})]
ok("the tail may take any square beside the head's destination",
   sorted(zone) == sorted([(4, 2), (4, 4), (5, 3), (3, 3)]), str(sorted(zone)))
ok("...including the square the head is vacating", (3, 3) in zone)
m.select_hero(LEFT, hd.id)
ok("a tail left behind is refused",
   m.commit(LEFT, {"orders": [dict(bite((4, 3), [[5, 3]]), entity=hd.id),
                              dict(stay((3, 4)), entity=tl.id)]}) is not None)
ok("...but following the head is fine",
   m.commit(LEFT, {"orders": [dict(bite((4, 3), [[5, 3]]), entity=hd.id),
                              dict(stay((4, 4)), entity=tl.id)]}) is None)

# the head leads: the tail cannot be ordered first
m, hd, tl, gate, d = snake_arena()
m.select_hero(LEFT, hd.id)
ok("the tail cannot act before the head",
   m.commit(LEFT, {"orders": [dict(stay((3, 4)), entity=tl.id),
                              dict(stay((3, 3)), entity=hd.id)]}) is not None)

# the venom is not damage, so nothing that turns damage aside touches it
m, hd, tl, pal, d = snake_arena(right=(("paladin", (7, 3)), ("dummy", (7, 1))))
pal.set_cell((4, 3))
snake_turn(m, hd, tl, bite((3, 3), [[4, 3]]), stay((3, 4)), d.id, hold)
ok("the bite trips the holy shield", pal.vars.get("aegis_spent") is True)
ok("...and the venom still takes hold behind it",
   pal.vars.get("rooted_tag") == "venom", str(pal.vars.get("rooted_tag")))

# 53 — 潜水者: mines the ground, and only your side can see them
import board as BOARD

def diver_arena(bomb_at=(5, 3)):
    m = arena([("diver", (3, 3)), ("gatekeeper", (3, 1))],
              [("cannoneer", (7, 3)), ("dummy", (7, 1))])
    dv = unit(m, LEFT, "diver")
    assert m.opening_choose(LEFT, {"cell": list(bomb_at)}) is None
    return m, dv, unit(m, LEFT, "gatekeeper"), unit(m, RIGHT, "cannoneer"), unit(m, RIGHT, "dummy")

m, dv, ally, cannon, d = diver_arena()
ok("the opening charge is buried where you asked",
   m.board.has_kind((5, 3), "big_bomb"), str(m.board.serialise()))
ok("...and it is set for two rounds from now",
   m.board.effects_at((5, 3))[0].fuse_round == 2,
   str(m.board.effects_at((5, 3))[0].fuse_round))

# only the side that laid it is told about it
ok("your own side sees the charge",
   any(t["kind"] == "big_bomb" for t in m.board.serialise(LEFT)))
ok("the enemy is told nothing",
   not any(t["kind"] == "big_bomb" for t in m.board.serialise(RIGHT)),
   str(m.board.serialise(RIGHT)))
ok("...and the whole truth is still available server-side",
   any(t["kind"] == "big_bomb" for t in m.board.serialise()))

# the fuse: it goes off at the start of round 2, on whoever is standing there
m, dv, ally, cannon, d = diver_arena()
cannon.set_cell((5, 3))
hp0 = cannon.hp
r0 = m.round
while m.round == r0:
    left = [e.id for e in m.unacted(LEFT)]
    right = [e.id for e in m.unacted(RIGHT)]
    turn(m, left[0] if left else None, hold, right[0] if right else None, hold)
ok("the charge goes off at the start of round 2", m.round == 2, str(m.round))
ok("...for 6, on whoever is standing on it", hp0 - cannon.hp == 6, f"took {hp0 - cannon.hp}")
ok("...and it is spent", not m.board.has_kind((5, 3), "big_bomb"))

# it only catches an enemy — your own hero standing there is fine
m, dv, ally, cannon, d = diver_arena()
ally.set_cell((5, 3))
hp0 = ally.hp
r0 = m.round
while m.round == r0:
    left = [e.id for e in m.unacted(LEFT)]
    right = [e.id for e in m.unacted(RIGHT)]
    turn(m, left[0] if left else None, hold, right[0] if right else None, hold)
ok("your own hero walks away from your own charge", ally.hp == hp0, f"took {hp0 - ally.hp}")

# the small bomb: offered only on a turn it actually moved
m, dv, ally, cannon, d = diver_arena()
m.select_hero(LEFT, dv.id); m.commit(LEFT, hold)
m.select_hero(RIGHT, d.id); m.commit(RIGHT, hold)
ok("no charge is offered on a turn it held still", not m.followups[LEFT],
   str(m.followups[LEFT]))

m, dv, ally, cannon, d = diver_arena()
turn(m, dv.id, {"destination": [4, 3], "action": {"key": "none"}}, d.id, hold)
task = (m.followups[LEFT] or [None])[0]
ok("moving offers a small charge beside it", task and task["key"] == "small_bomb",
   str(task))
ok("...and only into empty squares next to where it ended up",
   task and all(m.topology.distance((4, 3), tuple(c)) == 1 for c in task["options"]),
   str(task and task["options"]))
m.choose_followup(LEFT, task["options"][0])
laid = tuple(task["options"][0])
ok("choosing one buries it", m.board.has_kind(laid, "small_bomb"))

# an enemy stepping onto it sets it off; an ally does not
m = arena([("diver", (3, 3))], [("gatekeeper", (7, 3))])
dv, gk = unit(m, LEFT, "diver"), unit(m, RIGHT, "gatekeeper")
m.opening_choose(LEFT, {"cell": [1, 1]})
gk.set_cell((6, 3))
m.board.add_effect((5, 3), BOARD.SmallBomb(LEFT))
hp0 = gk.hp
turn(m, dv.id, hold, gk.id, {"destination": [5, 3], "action": {"key": "none"}})
ok("an enemy stepping on a small bomb takes 3", hp0 - gk.hp == 3, f"took {hp0 - gk.hp}")
ok("...and the bomb is spent", not m.board.has_kind((5, 3), "small_bomb"))

m = arena([("diver", (3, 3)), ("gatekeeper", (3, 1))], [("dummy", (7, 3))])
dv, ally, d = unit(m, LEFT, "diver"), unit(m, LEFT, "gatekeeper"), unit(m, RIGHT, "dummy")
m.opening_choose(LEFT, {"cell": [1, 1]})
m.board.add_effect((4, 1), BOARD.SmallBomb(LEFT))
hp0 = ally.hp
turn(m, ally.id, {"destination": [4, 1], "action": {"key": "none"}}, d.id, hold)
ok("your own hero walks over your own mine safely", ally.hp == hp0, f"took {hp0 - ally.hp}")
ok("...and does not set it off", m.board.has_kind((4, 1), "small_bomb"))

# a thrown hero trips a mine too — the trigger is movement, not walking
m = arena([("strongman", (3, 3)), ("diver", (3, 1))], [("gatekeeper", (7, 3))])
st, dv2, gk = unit(m, LEFT, "strongman"), unit(m, LEFT, "diver"), unit(m, RIGHT, "gatekeeper")
m.opening_choose(LEFT, {"cell": [1, 1]})
gk.set_cell((4, 3))
st.ap = st.max_ap
m.board.add_effect((2, 3), BOARD.SmallBomb(LEFT))
hp0 = gk.hp
turn(m, st.id, {"destination": None, "action": {"key": "ability:slam", "target": gk.id}},
     gk.id, hold)
throw = next(t for t in m.followups[LEFT] if t["key"] == HEROES.Slam.THROW)
assert [2, 3] in throw["options"], throw["options"]
m.choose_followup(LEFT, [2, 3])
slam, mine = HEROES.Slam.DAMAGE, BOARD.SmallBomb.DAMAGE
ok("a hero hurled onto a mine sets it off", hp0 - gk.hp == slam + mine,
   f"took {hp0 - gk.hp} ({slam} slam + {mine} mine)")

# the parting charge: it dies, and its side still gets to place one
m, dv, ally, cannon, d = diver_arena()
dv.hp = 2
turn(m, ally.id, hold,
     cannon.id, {"destination": None, "action": {"key": "attack", "shots": [[[3, 3]]]}})
ok("the diver is destroyed", not dv.alive, f"{dv.hp} hp")
task = (m.followups[LEFT] or [None])[0]
ok("its side is still asked for one last charge",
   task and task["key"] == "last_charge", str(task))
ok("...and it may go anywhere on the board", task and len(task["options"]) == 45,
   str(task and len(task["options"])))
m.choose_followup(LEFT, [8, 5])
ok("the last charge is buried", m.board.has_kind((8, 5), "big_bomb"))
ok("...and it is offered only once", not any(
   f["key"] == "last_charge" for f in m.followups[LEFT]))

# charges pile up on one square — no cap on either kind
m = arena([("diver", (3, 3))], [("gatekeeper", (7, 3))])
dv, gk = unit(m, LEFT, "diver"), unit(m, RIGHT, "gatekeeper")
m.opening_choose(LEFT, {"cell": [1, 1]})
gk.set_cell((6, 3))
for _ in range(3):
    m.board.add_effect((5, 3), BOARD.SmallBomb(LEFT))
ok("three small charges sit on one square",
   len(m.board.effects_at((5, 3))) == 3, str(len(m.board.effects_at((5, 3)))))
hp0 = gk.hp
turn(m, dv.id, hold, gk.id, {"destination": [5, 3], "action": {"key": "none"}})
ok("stepping there sets off every one of them", hp0 - gk.hp == 9, f"took {hp0 - gk.hp}")
ok("...and the square is swept clean", not m.board.effects_at((5, 3)))

# big charges stack too, and each pays out in full
m = arena([("diver", (3, 3))], [("gatekeeper", (7, 3))])
dv, gk = unit(m, LEFT, "diver"), unit(m, RIGHT, "gatekeeper")
m.opening_choose(LEFT, {"cell": [1, 1]})
gk.set_cell((5, 3))
laid = m.round
m.board.add_effect((5, 3), BOARD.BigBomb(LEFT, laid))
m.board.add_effect((5, 3), BOARD.BigBomb(LEFT, laid))  # same fuse, same square
m.board.add_effect((5, 3), BOARD.SmallBomb(LEFT))      # a mine can share it too
ok("two big charges and a mine share one square",
   len(m.board.effects_at((5, 3))) == 3, str(len(m.board.effects_at((5, 3)))))
hp0 = gk.hp
while m.round < laid + BOARD.BigBomb.FUSE:
    left = [e.id for e in m.unacted(LEFT)]
    right = [e.id for e in m.unacted(RIGHT)]
    turn(m, left[0] if left else None, hold, right[0] if right else None, hold)
ok("both big charges go off together for 12", hp0 - gk.hp == 12, f"took {hp0 - gk.hp}")
ok("...and the mine is untouched — nobody stepped onto it",
   m.board.has_kind((5, 3), "small_bomb"), str(m.board.serialise()))

# a small bomb may be dropped onto a square that already holds one
m, dv, ally, cannon, d = diver_arena()
m.board.add_effect((4, 3), BOARD.SmallBomb(LEFT))
turn(m, dv.id, {"destination": [4, 2], "action": {"key": "none"}}, d.id, hold)
task = (m.followups[LEFT] or [None])[0]
ok("a square already holding a charge is still offered",
   task and [4, 3] in task["options"], str(task and task["options"]))
m.choose_followup(LEFT, [4, 3])
ok("...and the second one piles on top",
   len(m.board.effects_at((4, 3))) == 2, str(len(m.board.effects_at((4, 3)))))

# 54 — 刺客: names a hero, appears beside it, and cuts
def assassin_arena(right=(("gatekeeper", (7, 3)), ("dummy", (7, 1)))):
    m = arena([("assassin", (1, 3)), ("cannoneer", (1, 1))], list(right))
    asn = unit(m, LEFT, "assassin"); asn.ap = asn.max_ap
    return (m, asn, unit(m, LEFT, "cannoneer")) + tuple(unit(m, RIGHT, k) for k, _ in right)

kill = lambda t: {"destination": None, "action": {"key": "ability:garrote", "target": t.id}}

# it reaches the far side of the board and strikes for its full attack
m, asn, ally, gk, d = assassin_arena()
hp0 = gk.hp
turn(m, asn.id, kill(gk), d.id, hold)
ok("it crosses the whole board in one step",
   m.topology.distance(asn.cell, gk.cell) == 1, f"{asn.cell} vs {gk.cell}")
ok("...and cuts for its full attack", hp0 - gk.hp == 5, f"took {hp0 - gk.hp}")

# only the 4 orthogonals count as beside it
m, asn, ally, gk, d = assassin_arena()
turn(m, asn.id, kill(gk), d.id, hold)
ok("it appears on an orthogonal square, never a diagonal",
   abs(asn.cell[0] - gk.cell[0]) + abs(asn.cell[1] - gk.cell[1]) == 1, str(asn.cell))

# the mark cannot walk away — the blink resolves after everyone has moved
m, asn, ally, gk, d = assassin_arena()
hp0 = gk.hp
turn(m, asn.id, kill(gk), gk.id, {"destination": [6, 3], "action": {"key": "none"}})
ok("a mark that runs is followed", gk.cell == (6, 3) and
   m.topology.distance(asn.cell, gk.cell) == 1, f"{asn.cell} chasing {gk.cell}")
ok("...and cut anyway", hp0 - gk.hp == 5, f"took {hp0 - gk.hp}")

# hemmed in on all four sides: nowhere to appear, and no strike
m = arena([("assassin", (1, 3)), ("cannoneer", (1, 1))],
          [("gatekeeper", (9, 1)), ("dummy", (8, 1)), ("berserker", (9, 2))])
asn = unit(m, LEFT, "assassin"); asn.ap = asn.max_ap
gk = unit(m, RIGHT, "gatekeeper")
# A corner has only two neighbours, and both are held by its own side.
ok("the mark is boxed in", all(m.occupant(c) is not None
                               for c in m.topology.neighbours(gk.cell)), str(gk.cell))
was, hp0 = asn.cell, gk.hp
turn(m, asn.id, kill(gk), unit(m, RIGHT, "dummy").id, hold)
ok("a mark hemmed in on all four sides cannot be reached", asn.cell == was, str(asn.cell))
ok("...and takes nothing", gk.hp == hp0, f"took {hp0 - gk.hp}")

# it is a 普通攻击, so the stone shrugs it off
m, asn, ally, garg, d = assassin_arena(right=(("gargoyle", (7, 3)), ("dummy", (7, 1))))
hp0 = garg.hp
turn(m, asn.id, kill(garg), d.id, hold)
ok("stone chips for 1 even from an assassin", hp0 - garg.hp == 1, f"took {hp0 - garg.hp}")

# 封喉 carries the hero, so the turn's own movement is not used
m, asn, ally, gk, d = assassin_arena()
m.select_hero(LEFT, asn.id)
ok("it cannot also walk somewhere",
   m.commit(LEFT, {"destination": [2, 3],
                   "action": {"key": "ability:garrote", "target": gk.id}}) is not None)
m.deselect(LEFT)

# blinking onto a mine sets it off, exactly as walking there would
m = arena([("assassin", (1, 3)), ("diver", (1, 1))], [("gatekeeper", (7, 3)), ("dummy", (7, 1))])
asn, gk = unit(m, LEFT, "assassin"), unit(m, RIGHT, "gatekeeper")
m.opening_choose(LEFT, {"cell": [1, 5]})
asn.ap = asn.max_ap
m.board.add_effect((6, 3), BOARD.SmallBomb(RIGHT))     # the enemy's mine
hp0 = asn.hp
turn(m, asn.id, kill(gk), unit(m, RIGHT, "dummy").id, hold, land=[6, 3])
ok("it appears on the square you picked", asn.cell == (6, 3), str(asn.cell))
ok("...and the mine there goes off under it", hp0 - asn.hp == 3, f"took {hp0 - asn.hp}")

# the landing square is yours to choose, mid-resolution
m, asn, ally, gk, d = assassin_arena()
m.select_hero(LEFT, asn.id); m.commit(LEFT, kill(gk))
m.select_hero(RIGHT, d.id); m.commit(RIGHT, hold)
ok("resolution pauses to ask where it appears", m.phase == "move_choice", m.phase)
task = m.move_choices[LEFT][0]
ok("every free square beside the mark is offered",
   sorted(map(tuple, task["options"])) == sorted(m.topology.neighbours(gk.cell)),
   str(task["options"]))
ok("the other seat is not asked anything", not m.move_choices[RIGHT])
ok("a square that was not offered is refused", m.choose_move(LEFT, [1, 1]) is not None)
ok("choosing one resumes the exchange", m.choose_move(LEFT, task["options"][-1]) is None
   and m.phase != "move_choice", m.phase)
ok("...and it stands exactly where you put it",
   list(asn.cell) == task["options"][-1], f"{asn.cell} vs {task['options'][-1]}")

# only one way in: taken silently, with nothing to decide
m = arena([("assassin", (1, 3)), ("cannoneer", (1, 1))],
          [("gatekeeper", (9, 1)), ("dummy", (8, 1))])
asn, gk = unit(m, LEFT, "assassin"), unit(m, RIGHT, "gatekeeper")
asn.ap = asn.max_ap
free = [c for c in m.topology.neighbours(gk.cell) if m.occupant(c) is None]
ok("the mark has exactly one open side", len(free) == 1, str(free))
m.select_hero(LEFT, asn.id); m.commit(LEFT, kill(gk))
m.select_hero(RIGHT, unit(m, RIGHT, "dummy").id); m.commit(RIGHT, hold)
ok("no prompt when there is nothing to choose between",
   m.phase != "move_choice" and asn.cell == free[0], f"{m.phase} {asn.cell}")

# AP: it costs the whole bar
m, asn, ally, gk, d = assassin_arena()
asn.ap = 1
entry = next(a for a in m.action_menu(asn) if a["key"] == "ability:garrote")
ok("one AP is not enough", not entry["affordable"])

# 55 — 猎人: one kill and it sees further, and takes two at a time
from entities import Modifier
def hunter_arena(right=(("dummy", (7, 3)), ("dummy", (7, 1)), ("gatekeeper", (7, 2)))):
    m = arena([("hunter", (3, 3)), ("cannoneer", (3, 1))], list(right))
    return (m, unit(m, LEFT, "hunter")) + tuple(m.living(RIGHT))

m, hn, d1, d2, gk = hunter_arena()
rng0, tgt0 = hn.rng, hn.targets
ok("it starts with its own reach and one target at a time", (rng0, tgt0) == (2, 1),
   f"{rng0}/{tgt0}")

# a kill opens its eye
d1.set_cell((4, 3)); d1.hp = 3
turn(m, hn.id, {"destination": None, "action": {"key": "attack", "shots": [[[4, 3]]]}},
     gk.id, hold)
ok("the kill lands", not d1.alive, f"{d1.hp} hp")
ok("...and its reach lengthens by 4", hn.rng - rng0 == 4, f"{rng0} -> {hn.rng}")
ok("...and its net now catches two", hn.targets - tgt0 == 1, f"{tgt0} -> {hn.targets}")
ok("...and it says so on the card",
   any(s["key"] == "blooded" for s in HEROES.status_of(m, hn)),
   str([s["key"] for s in HEROES.status_of(m, hn)]))

# a second kill changes nothing more
rng1, tgt1 = hn.rng, hn.targets
r0 = m.round
while m.round == r0:                       # the hunter has already acted this round
    left = [e.id for e in m.unacted(LEFT)]
    right = [e.id for e in m.unacted(RIGHT)]
    turn(m, left[0] if left else None, hold, right[0] if right else None, hold)
d2.set_cell((4, 4)); d2.hp = 2
turn(m, hn.id, {"destination": None, "action": {"key": "attack", "shots": [[[4, 4]]]}},
     gk.id, hold)
ok("a second kill adds nothing", (hn.rng, hn.targets) == (rng1, tgt1),
   f"{hn.rng}/{hn.targets}")

# before the kill, two enemies in the net means picking one
m, hn, d1, d2, gk = hunter_arena()
d1.set_cell((4, 3)); d2.set_cell((4, 2))
hp0 = (d1.hp, d2.hp)
m.select_hero(LEFT, hn.id)
m.commit(LEFT, {"destination": None, "action": {"key": "attack", "shots": [[[4, 3], [4, 2]]]}})
m.select_hero(RIGHT, gk.id); m.commit(RIGHT, hold)
ok("two in the net, one shot: it has to choose", m.phase == "victim", m.phase)
ok("...and it wants exactly one", m.victims_wanted(LEFT) == 1, str(m.victims_wanted(LEFT)))
m.choose_victim(LEFT, d1.id)
ok("only the one chosen is hit",
   (hp0[0] - d1.hp, hp0[1] - d2.hp) == (3, 0), f"{hp0[0]-d1.hp}, {hp0[1]-d2.hp}")

# once blooded, both in the net are hit
m, hn, d1, d2, gk = hunter_arena()
hn.vars["first_blood"] = True
hn.add_modifier(Modifier("targets", "add", 1))
d1.set_cell((4, 3)); d2.set_cell((4, 2))
hp0 = (d1.hp, d2.hp)
turn(m, hn.id,
     {"destination": None, "action": {"key": "attack", "shots": [[[4, 3], [4, 2]]]}},
     gk.id, hold)
ok("both enemies in the net take the shot",
   (hp0[0] - d1.hp, hp0[1] - d2.hp) == (3, 3), f"{hp0[0]-d1.hp}, {hp0[1]-d2.hp}")

# three in the net, two shots: it picks which two
m, hn, d1, d2, gk = hunter_arena()
hn.add_modifier(Modifier("targets", "add", 1))
d1.set_cell((4, 3)); d2.set_cell((4, 2)); gk.set_cell((4, 4))
hp0 = (d1.hp, d2.hp, gk.hp)
m.select_hero(LEFT, hn.id)
m.commit(LEFT, {"destination": None,
                "action": {"key": "attack", "shots": [[[4, 3], [4, 2], [4, 4]]]}})
m.select_hero(RIGHT, gk.id); m.commit(RIGHT, hold)
ok("three in the net means a choice again", m.phase == "victim", m.phase)
ok("...and it wants two of them", m.victims_wanted(LEFT) == 2, str(m.victims_wanted(LEFT)))
ok("one pick is not enough", m.choose_victim(LEFT, d1.id) is None and m.phase == "victim",
   m.phase)
ok("the same one twice is refused", m.choose_victim(LEFT, d1.id) is not None)
m.choose_victim(LEFT, gk.id)
ok("the two it chose are hit and the third is spared",
   (hp0[0] - d1.hp, hp0[2] - gk.hp, hp0[1] - d2.hp) == (3, 3, 0),
   f"{hp0[0]-d1.hp}, {hp0[2]-gk.hp}, {hp0[1]-d2.hp}")

# 56 — 占星师: names who dies next, and being named gets worse each time it is right
def seer_arena():
    # A fourth body on the right so the side never runs dry mid-exchange: these
    # checks kill units directly, outside the flow that would sit a side out.
    m = arena([("astrologer", (3, 3)), ("gatekeeper", (3, 1))],
              [("dummy", (7, 3)), ("dummy", (7, 1)), ("cannoneer", (7, 2)),
               ("berserker", (8, 3))])
    seer = unit(m, LEFT, "astrologer")
    d1, d2, cannon = m.living(RIGHT)[:3]
    return m, seer, unit(m, LEFT, "gatekeeper"), d1, d2, cannon

def settle(m):
    """Play exchanges until something needs answering, or the round turns over."""
    guard = 0
    while m.phase == "commit" and guard < 12:
        guard += 1
        left = [e.id for e in m.unacted(LEFT)]
        right = [e.id for e in m.unacted(RIGHT)]
        if not left and not right:
            break
        turn(m, left[0] if left else None, hold, right[0] if right else None, hold)

# 0 stars — 疑云: the named hero just takes 1 more from everything
m, seer, ally, d1, d2, cannon = seer_arena()
ok("it must name somebody before the first exchange", m.phase == "opening", m.phase)
assert m.opening_choose(LEFT, {"target": d1.id}) is None
ok("the named hero carries the mark", d1.vars.get("vulnerable") == 1,
   str(d1.vars.get("vulnerable")))
ok("...but is not held at 0 stars", not m.rooted(d1))
ok("...and loses no life at 0 stars", d1.hp == d1.max_hp, f"{d1.hp}/{d1.max_hp}")
ok("the reading is on show to both seats",
   any(s["key"] == "stars" and not s.get("private")
       for s in HEROES.status_of(m, seer)),
   str([s["key"] for s in HEROES.status_of(m, seer)]))

# calling it right earns a star, and a fresh prophecy is offered
DMG.apply_batch(m, [DMG.DamageEvent(source=cannon, target=d1, amount=99,
                                    category=DMG.NORMAL_ATTACK)])
ok("the named hero falls", not d1.alive)
ok("...and a star is read", seer.vars.get("stars") == 1, str(seer.vars.get("stars")))
turn(m, ally.id, hold, cannon.id, hold)
task = (m.followups[LEFT] or [None])[0]
ok("a fresh prophecy is offered once the board settles",
   task and task["key"] == "prophecy" and task["kind"] == "unit", str(task))
ok("...offering the enemies still standing, and not the one that fell",
   d1.id not in task["options"] and {d2.id, cannon.id} <= set(task["options"]),
   str(task["options"]))
ok("a hero not on offer is refused", m.choose_followup(LEFT, 999) is not None)

# 1 star — 凶兆: marked and held
m.choose_followup(LEFT, d2.id)
ok("the second name carries the mark too", d2.vars.get("vulnerable") == 1)
ok("...and at 1 star is held where it stands", m.rooted(d2))
ok("...but still loses no life", d2.hp == d2.max_hp, f"{d2.hp}/{d2.max_hp}")
ok("a held hero is offered nowhere to walk", m.legal_moves(d2) == [])

# 2 stars — 大祸: a quarter of its maximum torn out at once
DMG.apply_batch(m, [DMG.DamageEvent(source=cannon, target=d2, amount=99,
                                    category=DMG.NORMAL_ATTACK)])
ok("a second right call reads a second star", seer.vars.get("stars") == 2,
   str(seer.vars.get("stars")))
settle(m)
task = (m.followups[LEFT] or [None])[0]
if task is None:
    m.select_hero(LEFT, seer.id); m.commit(LEFT, hold)
    m.select_hero(RIGHT, cannon.id); m.commit(RIGHT, hold)
    task = (m.followups[LEFT] or [None])[0]
hp0, quarter = cannon.hp, cannon.max_hp // 4
ok("the last enemy is named", task and cannon.id in task["options"], str(task))
m.choose_followup(LEFT, cannon.id)
ok("at 2 stars a quarter of its maximum is torn out",
   hp0 - cannon.hp == quarter, f"took {hp0 - cannon.hp}, quarter is {quarter}")
ok("...and it is marked and held as well",
   cannon.vars.get("vulnerable") == 1 and m.rooted(cannon))

# the mark is the ordinary 增伤, so every later blow lands harder
hp0 = cannon.hp
DMG.apply_batch(m, [DMG.DamageEvent(source=seer, target=cannon, amount=3,
                                    category=DMG.NORMAL_ATTACK)])
ok("the prophecy's mark works like any other 增伤", hp0 - cannon.hp == 4,
   f"took {hp0 - cannon.hp}")

# the loss is a loss, not a blow: nothing wards it and it never kills
m, seer, ally, d1, d2, cannon = seer_arena()
seer.vars["stars"] = 2
cannon.vars["damage_reduction"] = 99
cannon.hp = 1
assert m.opening_choose(LEFT, {"target": cannon.id}) is None
ok("no guard keeps the life in", cannon.hp == 1 and cannon.alive, f"{cannon.hp} hp")
ok("...and a quarter is never the whole — it cannot kill", cannon.alive)

# a wrong call costs nothing
m, seer, ally, d1, d2, cannon = seer_arena()
assert m.opening_choose(LEFT, {"target": d1.id}) is None
DMG.apply_batch(m, [DMG.DamageEvent(source=ally, target=d2, amount=99,
                                    category=DMG.NORMAL_ATTACK)])
ok("the wrong hero dying reads no star", seer.vars.get("stars", 0) == 0,
   str(seer.vars.get("stars", 0)))
ok("...but a fresh reading is called for", seer.vars.get("reading_due") is True)
ok("...and the old name is still standing until one is made",
   seer.vars.get("prediction") == d1.id, str(seer.vars.get("prediction")))

# the prophecy lifts if the seer falls
m, seer, ally, d1, d2, cannon = seer_arena()
seer.vars["stars"] = 1
assert m.opening_choose(LEFT, {"target": d1.id}) is None
ok("the named hero is held while the seer watches", m.rooted(d1))
DMG.apply_batch(m, [DMG.DamageEvent(source=cannon, target=seer, amount=99,
                                    category=DMG.NORMAL_ATTACK)])
ok("kill the seer and the hold lifts", not seer.alive and not m.rooted(d1))
ok("...though the mark it already left stays", d1.vars.get("vulnerable") == 1)

# 57 — 四圣兽: four squares, four blessings, each taken once and kept
def beast_arena(start=(1, 1)):
    m = arena([("four_beasts", start), ("cannoneer", (1, 5))],
              [("gatekeeper", (7, 3)), ("dummy", (7, 1)), ("berserker", (9, 5))])
    fb = unit(m, LEFT, "four_beasts")
    return (m, fb, unit(m, LEFT, "cannoneer")) + tuple(m.living(RIGHT))

def close_round(m):
    """Play the rest of the round out so everyone is fresh again."""
    r0 = m.round
    while m.round == r0:
        left = [e.id for e in m.unacted(LEFT)]
        right = [e.id for e in m.unacted(RIGHT)]
        turn(m, left[0] if left else None, hold, right[0] if right else None, hold)

m, fb, ally, gk, d, bers = beast_arena()
cells = HEROES.FourBeasts.shrines(m, fb)
def shrine_of(where, beast):
    return sorted(c for c, b in where.items() if b == beast)

W = HEROES.FourBeasts.WIDTH
ok("every shrine is a run of squares, not one",
   sorted(collections.Counter(cells.values()).values()) == [W] * 4,
   str(collections.Counter(cells.values())))
ok("玄武 lies across the middle of the top row",
   shrine_of(cells, "turtle") == [(4, 1), (5, 1), (6, 1)], str(shrine_of(cells, "turtle")))
ok("朱雀 across the middle of the bottom row",
   shrine_of(cells, "phoenix") == [(4, 5), (5, 5), (6, 5)], str(shrine_of(cells, "phoenix")))
ok("青龙 down the middle of your own back line",
   shrine_of(cells, "dragon") == [(2, 2), (2, 3), (2, 4)], str(shrine_of(cells, "dragon")))
ok("白虎 down the middle of theirs",
   shrine_of(cells, "tiger") == [(8, 2), (8, 3), (8, 4)], str(shrine_of(cells, "tiger")))
ok("...and no square serves two beasts", len(cells) == 4 * W, str(len(cells)))

# 青龙 — deployed straight onto it, so it counts without moving
m, fb, ally, gk, d, bers = beast_arena(start=(2, 3))
ok("standing on it from the start wakes 青龙", "dragon" in fb.vars.get("beasts", set()),
   str(fb.vars.get("beasts")))
fb.hp = 5
turn(m, fb.id, {"destination": None, "action": {"key": "attack", "target": gk.id}}, d.id, hold)
ok("...and it mends 1 at the start of its turn", fb.hp == 6, f"{fb.hp} hp")

# 玄武 — heal 3, +3 max, and a point off everything
m, fb, ally, gk, d, bers = beast_arena()
fb.set_cell((5, 2)); fb.hp = 8
mx0 = fb.max_hp
turn(m, fb.id, {"destination": [5, 1], "action": {"key": "attack", "target": gk.id}},
     d.id, hold)
ok("stepping onto it wakes 玄武", "turtle" in fb.vars["beasts"])
ok("...for +3 maximum health", fb.max_hp - mx0 == 3, f"{mx0} -> {fb.max_hp}")
ok("...and 3 healed", fb.hp == 11, f"{fb.hp} hp")
hp0 = fb.hp
DMG.apply_batch(m, [DMG.DamageEvent(source=gk, target=fb, amount=5,
                                    category=DMG.NORMAL_ATTACK)])
ok("...and a point off every hit", hp0 - fb.hp == 4, f"took {hp0 - fb.hp}")

# 朱雀 — what it strikes burns
m, fb, ally, gk, d, bers = beast_arena()
fb.set_cell((5, 4))
turn(m, fb.id, {"destination": [5, 5], "action": {"key": "attack", "target": gk.id}},
     d.id, hold)
ok("stepping onto it wakes 朱雀", "phoenix" in fb.vars["beasts"])
ok("...and the ground under its target is alight",
   m.board.has_kind(gk.cell, "burning"), str(m.board.serialise()))
ok("...owned by its own side, so it burns the enemy",
   m.board.burning_damage_for(gk.cell, gk) > 0)

# 白虎 — +2 attack, and two enemies at a time
m, fb, ally, gk, d, bers = beast_arena()
fb.set_cell((8, 2))
atk0, tgt0 = fb.atk, fb.targets
turn(m, fb.id, {"destination": [8, 3], "action": {"key": "attack", "target": gk.id}},
     d.id, hold)
ok("stepping onto it wakes 白虎", "tiger" in fb.vars["beasts"])
ok("...for +2 attack", fb.atk - atk0 == 2, f"{atk0} -> {fb.atk}")
ok("...and it now names two", fb.targets - tgt0 == 1, f"{tgt0} -> {fb.targets}")
close_round(m)
m.select_hero(LEFT, fb.id)
ok("naming only one is refused now",
   m.commit(LEFT, {"destination": None,
                   "action": {"key": "attack", "target": gk.id}}) is not None)
ok("the same hero twice is refused",
   m.commit(LEFT, {"destination": None,
                   "action": {"key": "attack", "targets": [gk.id, gk.id]}}) is not None)
hp0 = (gk.hp, bers.hp)
m.deselect(LEFT)
turn(m, fb.id, {"destination": None,
                "action": {"key": "attack", "targets": [gk.id, bers.id]}}, d.id, hold)
ok("both named enemies take the full attack",
   (hp0[0] - gk.hp, hp0[1] - bers.hp) == (fb.atk, fb.atk),
   f"{hp0[0]-gk.hp}, {hp0[1]-bers.hp} at atk {fb.atk}")

# each square gives its blessing once only
m, fb, ally, gk, d, bers = beast_arena(start=(2, 3))
mx0 = fb.max_hp
fb.set_cell((5, 2))
turn(m, fb.id, {"destination": [5, 1], "action": {"key": "attack", "target": gk.id}},
     d.id, hold)
after = fb.max_hp
close_round(m)
fb.set_cell((5, 2))
turn(m, fb.id, {"destination": [5, 1], "action": {"key": "attack", "target": gk.id}},
     d.id, hold)
ok("walking back onto a square gives nothing more", fb.max_hp == after,
   f"{mx0} -> {after} -> {fb.max_hp}")

# the two side-relative squares swap with the side
m2 = arena([("gatekeeper", (1, 1))], [("four_beasts", (9, 1))])
fb2 = unit(m2, RIGHT, "four_beasts")
right_cells = HEROES.FourBeasts.shrines(m2, fb2)
ok("the Right hero's 青龙 is down its own back line",
   shrine_of(right_cells, "dragon") == [(8, 2), (8, 3), (8, 4)],
   str(shrine_of(right_cells, "dragon")))
ok("...and its 白虎 down the Left's",
   shrine_of(right_cells, "tiger") == [(2, 2), (2, 3), (2, 4)],
   str(shrine_of(right_cells, "tiger")))
ok("...while 玄武 and 朱雀 stay where they are",
   shrine_of(right_cells, "turtle") + shrine_of(right_cells, "phoenix")
   == [(4, 1), (5, 1), (6, 1), (4, 5), (5, 5), (6, 5)], str(right_cells))

# one shrine is one blessing, however much of it you walk over
m_w, fb_w, *_ = beast_arena(start=(2, 2))
ok("a shrine wakes its beast from any of its squares",
   "dragon" in fb_w.vars.get("beasts", set()), str(fb_w.vars.get("beasts")))
hp_w, mods_w = fb_w.max_hp, len(fb_w.modifiers)
for step in ((2, 3), (2, 4)):
    fb_w.set_cell(step)
    fb_w.passives[0].on_after_move(m_w, fb_w, {"entity": fb_w})
ok("...and walking the rest of it grants nothing further",
   fb_w.vars["beasts"] == {"dragon"} and fb_w.max_hp == hp_w
   and len(fb_w.modifiers) == mods_w,
   f"{fb_w.vars['beasts']} {fb_w.max_hp} {len(fb_w.modifiers)}")

# 58 — regressions found hunting through the newest heroes

# 蛇帝: mending either half mends the one creature
m, hd, tl, gate, d = snake_arena()
DMG.apply_batch(m, [DMG.DamageEvent(source=gate, target=hd, amount=10,
                                    category=DMG.NORMAL_ATTACK)])
ok("both halves read the wound", (hd.hp, tl.hp) == (15, 15), f"{hd.hp}/{tl.hp}")
got = DMG.heal(m, tl, 6, source=None)
ok("mending the tail mends the snake", got == 6 and hd.hp == 21,
   f"healed {got}, head {hd.hp}")
ok("...and both halves read it", tl.hp == 21, f"tail {tl.hp}")
got = DMG.heal(m, hd, 100, source=None)
ok("and it can never be mended past its whole", hd.hp == hd.max_hp == 25,
   f"{hd.hp}/{hd.max_hp}")

# 占星师: two enemies falling in one instant are both measured against the reading
m, seer, ally, d1, d2, cannon = seer_arena()
assert m.opening_choose(LEFT, {"target": d2.id}) is None
DMG.apply_batch(m, [
    DMG.DamageEvent(source=ally, target=d1, amount=99, category=DMG.NORMAL_ATTACK),
    DMG.DamageEvent(source=ally, target=d2, amount=99, category=DMG.NORMAL_ATTACK)])
ok("both fall", not d1.alive and not d2.alive)
ok("the named one still earns its star, whichever is swept first",
   seer.vars.get("stars") == 1, str(seer.vars.get("stars")))

# 占星师: a new reading lets the last one go
m, seer, ally, d1, d2, cannon = seer_arena()
seer.vars["stars"] = 1
assert m.opening_choose(LEFT, {"target": cannon.id}) is None
ok("the first name is held", m.rooted(cannon))
HEROES.read_the_omen(m, seer, d2)
ok("naming somebody else releases the first", not m.rooted(cannon))
ok("...and holds the new one instead", m.rooted(d2))
ok("...though the mark it already carried stays",
   cannon.vars.get("vulnerable") == 1, str(cannon.vars.get("vulnerable")))

# 白虎 awake but only one enemy left: it must still be able to attack
m = arena([("four_beasts", (3, 3)), ("cannoneer", (1, 1))], [("gatekeeper", (7, 3))])
fb = unit(m, LEFT, "four_beasts"); gk = unit(m, RIGHT, "gatekeeper")
fb.vars["beasts"] = {"tiger"}
fb.add_modifier(Modifier("targets", "add", 1))
ok("it wants two when two could be named", fb.targets == 2, str(fb.targets))
entry = next(a for a in m.action_menu(fb) if a["key"] == "attack")
ok("...but asks for only one when one enemy is left",
   entry["targeting"]["count"] == 1, str(entry["targeting"]["count"]))
hp0 = gk.hp
turn(m, fb.id, {"destination": None, "action": {"key": "attack", "targets": [gk.id]}},
     gk.id, hold)
ok("...and the last enemy can still be struck", hp0 - gk.hp == fb.atk,
   f"took {hp0 - gk.hp}")

# 妖精 mends every ally once — 蛇帝 is one creature, not two
m = arena([("fairy", (1, 1)), ("snake_head", (2, 2)), ("snake_tail", (2, 3))],
          [("dummy", (8, 3))])
fay = unit(m, LEFT, "fairy"); hd2 = unit(m, LEFT, "snake_head")
DMG.apply_batch(m, [DMG.DamageEvent(source=None, target=hd2, amount=10,
                                    category=DMG.NORMAL_ATTACK)])
before = hd2.hp
m.bus.emit("turn_start", {"entity": fay})
ok("a two-bodied hero is mended once, not once per body", hd2.hp - before == 1,
   f"healed {hd2.hp - before}")
ok("...and a one-bodied ally still gets its point",
   [e for e in m.bodies(LEFT)] and fay in m.bodies(LEFT))
ok("the tail is not counted as a body of its own", hd2 in m.bodies(LEFT)
   and unit(m, LEFT, "snake_tail") not in m.bodies(LEFT))

# a held hero says it is held, not merely pinned for a turn
m, seer, ally, d1, d2, cannon = seer_arena()
seer.vars["stars"] = 1
assert m.opening_choose(LEFT, {"target": d1.id}) is None
keys = [x["key"] for x in HEROES.status_of(m, d1)]
ok("a bound hero wears the bound badge", "bound" in keys, str(keys))
ok("...and not the one-turn pin", "rooted" not in keys, str(keys))

# reach can never go negative, however many things shorten it
m = arena([("werewolf", (2, 2)), ("mist_lady", (1, 1))], [("dummy", (8, 3))])
w = unit(m, LEFT, "werewolf")
for _ in range(6):
    w.add_modifier(Modifier("rng", "add", -1))
ok("reach is worn down to 1 and no further", w.rng == 1, str(w.rng))
ok("...and a worn-down hero can still swing", 
   m.action_menu(w) and any(a["key"] == "attack" for a in m.action_menu(w)))
for _ in range(6):
    w.add_modifier(Modifier("grid", "add", -1))
ok("...and so is the net it throws", w.grid == 1, str(w.grid))

# 59 — 画师: blunts the hand that strikes it, sharpens itself on what it strikes
def painter_arena():
    m = arena([("painter", (3, 3)), ("gatekeeper", (3, 1))],
              [("cannoneer", (7, 3)), ("dummy", (7, 1))])
    return (m, unit(m, LEFT, "painter"), unit(m, LEFT, "gatekeeper"),
            unit(m, RIGHT, "cannoneer"), unit(m, RIGHT, "dummy"))

def only_task(m, side, key):
    return next((t for t in m.followups[side] if t["key"] == key), None)

# being hit offers a blunt; it is a yes/no, not a square
m, pt, ally, cannon, d = painter_arena()
cannon.set_cell((4, 3))
atk0 = cannon.atk
turn(m, pt.id, hold,
     cannon.id, {"destination": None, "action": {"key": "attack", "shots": [[[3, 3]]]}})
task = only_task(m, LEFT, "paint_blunt")
ok("taking a blow offers a blunt", task is not None and task["kind"] == "confirm", str(task))
ok("...and it asks nothing of the board", not task.get("options"))
ok("saying no leaves the attacker alone",
   m.choose_followup(LEFT, None) is None and cannon.atk == atk0, f"{atk0} -> {cannon.atk}")
ok("...and spends no charge", pt.vars.get("blunts_used", 0) == 0)

# saying yes takes a point off whoever struck
m, pt, ally, cannon, d = painter_arena()
cannon.set_cell((4, 3))
atk0 = cannon.atk
turn(m, pt.id, hold,
     cannon.id, {"destination": None, "action": {"key": "attack", "shots": [[[3, 3]]]}})
m.choose_followup(LEFT, True)
ok("saying yes paints the attacker thinner", atk0 - cannon.atk == 1, f"{atk0} -> {cannon.atk}")
ok("...and spends a charge", pt.vars.get("blunts_used") == 1)

# landing a blow offers a stroke of its own
m, pt, ally, cannon, d = painter_arena()
cannon.set_cell((4, 3))
mine0 = pt.atk
turn(m, pt.id, {"destination": None, "action": {"key": "attack", "shots": [[[4, 3]]]}},
     d.id, hold)
task = only_task(m, LEFT, "paint_sharpen")
ok("landing a blow offers a stroke", task is not None and task["kind"] == "confirm", str(task))
m.choose_followup(LEFT, True)
ok("...and taking it sharpens the brush", pt.atk - mine0 == 1, f"{mine0} -> {pt.atk}")

# both counters run out at three, and run out apart
m, pt, ally, cannon, d = painter_arena()
pt.vars["blunts_used"] = 3
pt.vars["blunt_who"] = cannon.id
pt.vars["sharpen_due"] = True
tasks = pt.passives[0].followup(m, pt, {"entity": pt})
ok("a spent blunt is not offered again",
   not any(t["key"] == "paint_blunt" for t in tasks), str([t["key"] for t in tasks]))
ok("...while the other counter is untouched",
   any(t["key"] == "paint_sharpen" for t in tasks), str([t["key"] for t in tasks]))

# the same enemy can be worn down repeatedly, and attack never goes below zero
m, pt, ally, cannon, d = painter_arena()
atk0 = cannon.atk
for _ in range(3):
    pt.vars["blunt_who"] = cannon.id
    pt.passives[0].apply_followup(m, pt, "paint_blunt", True)
ok("the same hero can be painted thinner three times",
   atk0 - cannon.atk == 3, f"{atk0} -> {cannon.atk}")
ok("...and that is all the charges it has", pt.vars["blunts_used"] == 3)
for _ in range(9):
    cannon.add_modifier(Modifier("atk", "add", -1))
ok("attack is worn down to 1 and no further", cannon.atk == 1, str(cannon.atk))
# 狙击手 swings at 0 by design — its shot is measured by distance — and nothing
# should quietly hand it a point it was never given.
sniper = unit(arena([("sniper", (3, 3))], [("dummy", (7, 3))]), LEFT, "sniper")
ok("a hero built to swing at nothing keeps its zero", sniper.atk == 0, str(sniper.atk))
for _ in range(4):
    sniper.add_modifier(Modifier("atk", "add", -1))
ok("...and cannot be pushed below it either", sniper.atk == 0, str(sniper.atk))

# nothing to blunt when the board itself does the damage
m, pt, ally, cannon, d = painter_arena()
burn = m.board.add_burning(pt.cell, RIGHT).damage
m.select_hero(LEFT, pt.id); m.commit(LEFT, hold)
m.select_hero(RIGHT, d.id); m.commit(RIGHT, hold)
ok("burning ground offers no hand to blunt", not only_task(m, LEFT, "paint_blunt"),
   str(m.followups[LEFT]))

# 60 — 鸟嘴医生: walks in last, and the board rots outward from where it stands
def doctor_arena():
    m = arena([("plague_doctor", (2, 2)), ("gatekeeper", (2, 3))],
              [("cannoneer", (8, 2)), ("dummy", (8, 3))])
    return (m, unit(m, LEFT, "plague_doctor"), unit(m, LEFT, "gatekeeper"),
            unit(m, RIGHT, "cannoneer"), unit(m, RIGHT, "dummy"))

m, doc, ally, cannon, d = doctor_arena()
ok("it is not on the board when the forces lock", not doc.cells, str(doc.cells))
ok("...and nothing can touch it there", not doc.flags["targetable"])
ok("the board waits on it before the first exchange", m.phase == "opening", m.phase)
task = m.opening["pending"][LEFT][0]
ok("it is asked for a square, not an ally",
   doc.abilities[0].targeting["kind"] == "any_cell", str(task))
free = doc.abilities[0].cells(m, doc)
ok("every empty square is on offer, the enemy's own back line included",
   (9, 1) in free and ally.cell not in free, f"{len(free)} squares")

assert m.opening_choose(LEFT, {"cell": [5, 3]}) is None
ok("it stands where it chose", doc.cell == (5, 3), str(doc.cell))
ok("...and that square is infected", m.board.has_kind((5, 3), "infection"))
ok("...and it blocks and can be hit like anything else",
   doc.flags["targetable"] and doc.flags["blocks_movement"])

# the ground bites whoever starts a turn on it — either side
m, doc, ally, cannon, d = doctor_arena()
assert m.opening_choose(LEFT, {"cell": [5, 3]}) is None
cannon.set_cell((5, 1)); ally.set_cell((5, 5))
m.board.add_effect((5, 1), BOARD.Infection(LEFT))
m.board.add_effect((5, 5), BOARD.Infection(LEFT))
hp0 = (cannon.hp, ally.hp, doc.hp)
turn(m, ally.id, hold, cannon.id, hold)
ok("an enemy starting on infected ground loses 2", hp0[0] - cannon.hp == 2,
   f"took {hp0[0] - cannon.hp}")
ok("...and so does one of your own", hp0[1] - ally.hp == 2, f"took {hp0[1] - ally.hp}")

# the doctor alone walks it safely
m, doc, ally, cannon, d = doctor_arena()
assert m.opening_choose(LEFT, {"cell": [5, 3]}) is None
hp0 = doc.hp
turn(m, doc.id, hold, cannon.id, hold)
ok("the doctor takes nothing from its own plague", doc.hp == hp0, f"took {hp0 - doc.hp}")

# nothing softens it
m, doc, ally, cannon, d = doctor_arena()
assert m.opening_choose(LEFT, {"cell": [5, 3]}) is None
cannon.set_cell((5, 1))
m.board.add_effect((5, 1), BOARD.Infection(LEFT))
cannon.vars["damage_reduction"] = 99
cannon.vars["blessed"] = doc.id
hp0 = cannon.hp
turn(m, ally.id, hold, cannon.id, hold)
ok("no guard blunts infected ground", hp0 - cannon.hp == 2, f"took {hp0 - cannon.hp}")
ok("...and no blessing is spent turning it aside", cannon.vars.get("blessed") == doc.id)

# it creeps one square a round, and all at once rather than cascading
m, doc, ally, cannon, d = doctor_arena()
assert m.opening_choose(LEFT, {"cell": [5, 3]}) is None
def infected(m):
    return len([t for t in m.board.serialise() if t["kind"] == "infection"])
ok("for the whole first round it is just the one square", infected(m) == 1
   and m.round == 1, f"{infected(m)} at round {m.round}")
def next_round(m):
    r0 = m.round
    while m.round == r0:
        left = [e.id for e in m.unacted(LEFT)]
        right = [e.id for e in m.unacted(RIGHT)]
        turn(m, left[0] if left else None, hold, right[0] if right else None, hold)
next_round(m)
ok("round 2 is when it first creeps — the square and its four neighbours",
   infected(m) == 5, f"{infected(m)} at round {m.round}")
next_round(m)
ok("...then one ring further every round after", infected(m) == 13,
   f"{infected(m)} at round {m.round}")
ok("...one ring at a time, never a cascade to the board's edge", infected(m) < 25,
   str(infected(m)))

# 61 — 教皇: nothing dies while it stands, and every mercy sharpens the hand
def pope_arena():
    m = arena([("pope", (2, 2)), ("cannoneer", (2, 3))],
              [("cannoneer", (8, 2)), ("dummy", (8, 3))])
    return (m, unit(m, LEFT, "pope"), unit(m, LEFT, "cannoneer"),
            unit(m, RIGHT, "cannoneer"), unit(m, RIGHT, "dummy"))

def strike(target_cell):
    return {"destination": None, "action": {"key": "attack", "shots": [[list(target_cell)]]}}

# a lethal blow stops the board and asks
m, pope, ally, cannon, d = pope_arena()
ally.set_cell((7, 2)); ally.hp = 2
m.select_hero(LEFT, pope.id); m.commit(LEFT, hold)
m.select_hero(RIGHT, cannon.id); m.commit(RIGHT, strike((7, 2)))
ok("a killing blow holds the board up", m.phase == "interrupt", m.phase)
task = m.interrupts[0]
ok("...and it is the Pope's side that is asked",
   task["side"] == LEFT and task["kind"] == "confirm", str(task["side"]))
ok("...and the hero is still standing while it is asked", ally.alive and ally.hp <= 0,
   f"alive={ally.alive} hp={ally.hp}")
ok("the other seat cannot answer for it", m.choose_interrupt(RIGHT, True) is not None)

# stepping in front: the blow does nothing at all
ok("saying yes is accepted", m.choose_interrupt(LEFT, True) is None)
ok("the hero takes nothing from it", ally.alive and ally.hp == 2, f"{ally.hp} hp")
ok("...and now the attacker's own seat is owed a choice",
   m.phase == "interrupt" and m.interrupts[0]["side"] == RIGHT
   and m.interrupts[0]["kind"] == "pick", str(m.interrupts[:1]))
atk0, rng0 = cannon.atk, cannon.rng
ok("something not on offer is refused", m.choose_interrupt(RIGHT, "hp") is not None)
m.choose_interrupt(RIGHT, "rng")
ok("the denied attacker keeps its reward", cannon.rng - rng0 == 1, f"{rng0} -> {cannon.rng}")
ok("...and the board carries on", m.phase != "interrupt", m.phase)

# declining lets the hero fall
m, pope, ally, cannon, d = pope_arena()
ally.set_cell((7, 2)); ally.hp = 2
atk0 = cannon.atk
m.select_hero(LEFT, pope.id); m.commit(LEFT, hold)
m.select_hero(RIGHT, cannon.id); m.commit(RIGHT, strike((7, 2)))
m.choose_interrupt(LEFT, None)
ok("saying no lets it fall", not ally.alive, f"alive={ally.alive}")
ok("...and nobody is sharpened for a mercy not given", cannon.atk == atk0,
   f"{atk0} -> {cannon.atk}")

# it cannot step in front of its own end
m, pope, ally, cannon, d = pope_arena()
pope.set_cell((7, 2)); pope.hp = 2
m.select_hero(LEFT, ally.id); m.commit(LEFT, hold)
m.select_hero(RIGHT, cannon.id); m.commit(RIGHT, strike((7, 2)))
ok("the Pope cannot save itself", not pope.alive and m.phase != "interrupt",
   f"alive={pope.alive} phase={m.phase}")

# mercy reaches across the board — sparing an enemy feeds your own hero
m, pope, ally, cannon, d = pope_arena()
d.set_cell((3, 2)); d.hp = 1
mine0 = ally.atk
m.select_hero(LEFT, ally.id); m.commit(LEFT, strike((3, 2)))
m.select_hero(RIGHT, cannon.id); m.commit(RIGHT, hold)
ok("an enemy about to fall is offered too", m.phase == "interrupt", m.phase)
ok("...and the Pope's own seat answers", m.interrupts[0]["side"] == LEFT)
m.choose_interrupt(LEFT, True)
ok("the enemy is spared", d.alive and d.hp == 1, f"alive={d.alive} hp={d.hp}")
ok("...and your own hero is the one owed a reward",
   m.interrupts[0]["side"] == LEFT and m.interrupts[0]["key"] == "reward", str(m.interrupts[:1]))
m.choose_interrupt(LEFT, "atk")
ok("...which it duly collects", ally.atk - mine0 == 1, f"{mine0} -> {ally.atk}")

# seven mercies and no more
m, pope, ally, cannon, d = pope_arena()
ok("it starts with seven to give",
   next(b for b in HEROES.status_of(m, pope) if b["key"] == "absolution")["badge"] == "赦7",
   str([b["badge"] for b in HEROES.status_of(m, pope)]))
pope.vars["saves_used"] = 6
ally.set_cell((7, 2)); ally.hp = 2
m.select_hero(LEFT, pope.id); m.commit(LEFT, hold)
m.select_hero(RIGHT, cannon.id); m.commit(RIGHT, strike((7, 2)))
ok("the seventh is still offered", m.phase == "interrupt", m.phase)
m.choose_interrupt(LEFT, True)
m.choose_interrupt(RIGHT, "atk")
ok("...and spends the last of them", pope.vars["saves_used"] == 7,
   str(pope.vars["saves_used"]))
ok("...which the card says plainly",
   next(b for b in HEROES.status_of(m, pope) if b["key"] == "absolution")["badge"] == "赦0")
ok("a spent Pope is no longer counted among those who can step in", not m.savers())

# the eighth killing blow simply lands
m, pope, ally, cannon, d = pope_arena()
pope.vars["saves_used"] = 7
ally.set_cell((7, 2)); ally.hp = 2
m.select_hero(LEFT, pope.id); m.commit(LEFT, hold)
m.select_hero(RIGHT, cannon.id); m.commit(RIGHT, strike((7, 2)))
ok("with none left the board does not even pause", m.phase != "interrupt", m.phase)
ok("...and the hero falls", not ally.alive)

# declining does not spend one
m, pope, ally, cannon, d = pope_arena()
ally.set_cell((7, 2)); ally.hp = 2
m.select_hero(LEFT, pope.id); m.commit(LEFT, hold)
m.select_hero(RIGHT, cannon.id); m.commit(RIGHT, strike((7, 2)))
m.choose_interrupt(LEFT, None)
ok("a mercy refused is a mercy kept", pope.vars.get("saves_used", 0) == 0,
   str(pope.vars.get("saves_used", 0)))

# ground and poison are not blows: nobody steps in front of them
m, pope, ally, cannon, d = pope_arena()
ally.set_cell((7, 2)); ally.hp = 1
m.board.add_burning((7, 2), RIGHT)
m.select_hero(LEFT, ally.id); m.commit(LEFT, hold)
m.select_hero(RIGHT, cannon.id); m.commit(RIGHT, hold)
ok("burning ground is nobody's blow to stay", not ally.alive and m.phase != "interrupt",
   f"alive={ally.alive} phase={m.phase}")

# 62 — 水法师: four squares that touch, and half of what the water takes
def wm_arena():
    m = arena([("water_mage", (3, 3)), ("cannoneer", (3, 1))],
              [("dummy", (7, 2)), ("dummy", (7, 3)), ("gatekeeper", (7, 4))])
    wm = unit(m, LEFT, "water_mage"); wm.ap = wm.max_ap
    return (m, wm, unit(m, LEFT, "cannoneer")) + tuple(m.living(RIGHT))

soak = lambda cells: {"destination": None,
                      "action": {"key": "ability:soak", "shots": [[list(c) for c in cells]]}}

# an L of four squares, catching three enemies
m, wm, ally, d1, d2, gk = wm_arena()
wm.hp = 5
hp0 = (d1.hp, d2.hp, gk.hp)
turn(m, wm.id, soak([(7, 2), (7, 3), (7, 4), (6, 3)]), gk.id, hold)
ok("every enemy in the shape is drenched",
   (hp0[0] - d1.hp, hp0[1] - d2.hp, hp0[2] - gk.hp) == (3, 3, 3),
   f"{hp0[0]-d1.hp}, {hp0[1]-d2.hp}, {hp0[2]-gk.hp}")
ok("...and the mage keeps half of it", wm.hp == 5 + 9 // 2, f"{wm.hp} hp")

# the four must touch
m, wm, ally, d1, d2, gk = wm_arena()
m.select_hero(LEFT, wm.id)
ok("four scattered squares are refused",
   m.commit(LEFT, soak([(7, 2), (7, 4), (5, 2), (5, 4)])) is not None)
ok("...and so are three", m.commit(LEFT, soak([(7, 2), (7, 3), (7, 4)])) is not None)
ok("...and the same square twice",
   m.commit(LEFT, soak([(7, 2), (7, 2), (7, 3), (7, 4)])) is not None)
ok("but a straight line of four is one shape",
   m.commit(LEFT, soak([(7, 1), (7, 2), (7, 3), (7, 4)])) is None)

# range is measured from where it ends up
m, wm, ally, d1, d2, gk = wm_arena()
m.select_hero(LEFT, wm.id)
far = [(9, 2), (9, 3), (9, 4), (8, 3)]      # 7 away from C3, 6 from D3
ok("a square beyond 6 is out of reach", m.commit(LEFT, soak(far)) is not None)
m.deselect(LEFT)
m.select_hero(LEFT, wm.id)
ok("...but stepping one closer brings it in",
   m.commit(LEFT, dict(soak(far), destination=[4, 3])) is None)

# the mending follows what actually landed, not what was aimed
m, wm, ally, d1, d2, gk = wm_arena()
wm.hp = 5
gk.vars["damage_reduction"] = 99          # this one shrugs the water off entirely
hp0 = (d1.hp, gk.hp)
turn(m, wm.id, soak([(7, 2), (7, 3), (7, 4), (6, 3)]), gk.id, hold)
ok("a hero that shrugs it off takes nothing", gk.hp == hp0[1], f"took {hp0[1] - gk.hp}")
ok("...and feeds the mage nothing either", wm.hp == 5 + 6 // 2, f"{wm.hp} hp")

# nothing caught, nothing mended
m, wm, ally, d1, d2, gk = wm_arena()
wm.hp = 5
turn(m, wm.id, soak([(5, 1), (5, 2), (5, 3), (5, 4)]), gk.id, hold)
ok("an empty shape mends nothing", wm.hp == 5, f"{wm.hp} hp")
ok("...and still costs the AP", wm.ap == 0 + 1, f"{wm.ap} AP (+1 at turn end)")

# it is water, and it only touches theirs
m, wm, ally, d1, d2, gk = wm_arena()
ally.set_cell((6, 3))
hp0 = ally.hp
turn(m, wm.id, soak([(7, 2), (7, 3), (7, 4), (6, 3)]), gk.id, hold)
ok("its own line stands in the water untouched", ally.hp == hp0, f"took {hp0 - ally.hp}")
ok("the blow is tagged water",
   any("water" in (l["text"] or "") for l in m.log[-12:]),
   str([l["text"] for l in m.log[-4:]]))

# 63 — 法官: marked, then answered for whatever its next turn does
def judge_arena():
    m = arena([("judge", (3, 3)), ("cannoneer", (3, 1))],
              [("cannoneer", (7, 3)), ("dummy", (7, 1))])
    jd = unit(m, LEFT, "judge"); jd.ap = jd.max_ap
    return (m, jd, unit(m, LEFT, "cannoneer"),
            unit(m, RIGHT, "cannoneer"), unit(m, RIGHT, "dummy"))

mark = lambda key, t: {"destination": None,
                       "action": {"key": f"ability:{key}", "target": t.id}}
shoot = lambda c: {"destination": None, "action": {"key": "attack", "shots": [[list(c)]]}}

# 罚恶: what it deals comes back on it
m, jd, ally, foe, d = judge_arena()
turn(m, jd.id, mark("condemn", foe), d.id, hold)
ok("the mark is laid", foe.vars.get("judged", {}).get("kind") == "punish",
   str(foe.vars.get("judged")))
ok("...and it is on show", any(b["key"] == "judged" for b in HEROES.status_of(m, foe)))
hp0, foe0 = ally.hp, foe.hp
turn(m, ally.id, hold, foe.id, shoot(ally.cell))
ok("it deals its damage as normal", hp0 - ally.hp == foe.atk, f"{hp0 - ally.hp}")
ok("...and takes exactly that back at the end of its turn",
   foe0 - foe.hp == foe.atk, f"took {foe0 - foe.hp}")
ok("...and the verdict is spent", not foe.vars.get("judged"))

# 赏善: what it deals is mended
m, jd, ally, foe, d = judge_arena()
ally.hp = 5
turn(m, jd.id, mark("commend", ally), d.id, hold)
turn(m, ally.id, shoot(foe.cell), foe.id, hold)
ok("a rewarded hero is mended for what it dealt", ally.hp == 5 + ally.atk,
   f"{ally.hp} hp")

# a turn that deals nothing earns nothing, and spends the verdict either way
m, jd, ally, foe, d = judge_arena()
foe0 = foe.hp
turn(m, jd.id, mark("condemn", foe), d.id, hold)
turn(m, ally.id, hold, foe.id, hold)
ok("a quiet turn is answered with nothing", foe.hp == foe0, f"took {foe0 - foe.hp}")
ok("...but the verdict is still spent", not foe.vars.get("judged"))

# it is the NEXT turn that counts, not one already under way
m, jd, ally, foe, d = judge_arena()
foe0, hp0 = foe.hp, ally.hp
m.select_hero(LEFT, jd.id); m.commit(LEFT, mark("condemn", foe))
m.select_hero(RIGHT, foe.id); m.commit(RIGHT, shoot(ally.cell))
ok("a hero acting in the same breath is not judged on that turn",
   foe.hp == foe0 and hp0 - ally.hp == foe.atk, f"foe took {foe0 - foe.hp}")
ok("...and still carries the mark for its next one", foe.vars.get("judged") is not None)

# one verdict at a time — a new one replaces whatever was there
m, jd, ally, foe, d = judge_arena()
turn(m, jd.id, mark("condemn", foe), d.id, hold)
r0 = m.round
while m.round == r0:
    left = [e.id for e in m.unacted(LEFT)]
    right = [e.id for e in m.unacted(RIGHT)]
    turn(m, left[0] if left else None, hold, right[0] if right else None, hold)
jd.ap = jd.max_ap
turn(m, jd.id, mark("commend", foe), d.id, hold)
ok("the second verdict replaces the first",
   foe.vars["judged"]["kind"] == "reward", str(foe.vars["judged"]))

# either side can be marked
m, jd, ally, foe, d = judge_arena()
m.select_hero(LEFT, jd.id)
ok("one of your own can be marked", m.commit(LEFT, mark("commend", ally)) is None)
m.deselect(LEFT)
ok("...and so can one of theirs",
   HEROES.Commend().targeting["kind"] == "any_unit")

# 64 — 工匠: two squares built into the board before anyone stands on it
def artisan_arena(doors=((2, 3), (8, 3))):
    m = Match()
    m.assign_draft(["artisan", "cannoneer"], ["gatekeeper", "dummy"])
    assert m.phase == "build", m.phase
    assert m.build_choose(LEFT, {"cells": [list(c) for c in doors]}) is None
    for k, c in (("artisan", (2, 3)), ("cannoneer", (1, 1))):
        assert m.place(LEFT, k, c) is None, (k, c)
    for k, c in (("gatekeeper", (8, 1)), ("dummy", (8, 2))):
        assert m.place(RIGHT, k, c) is None, (k, c)
    assert m.lock_force(LEFT) is None
    assert m.lock_force(RIGHT) is None
    return (m, unit(m, LEFT, "artisan"), unit(m, LEFT, "cannoneer"),
            unit(m, RIGHT, "gatekeeper"), unit(m, RIGHT, "dummy"))

# the board is built before anybody is placed
m2 = Match(); m2.assign_draft(["artisan", "cannoneer"], ["gatekeeper", "dummy"])
ok("the board is made ready before deployment", m2.phase == "build", m2.phase)
ok("...and nobody can be placed until it is",
   m2.place(LEFT, "artisan", (2, 2)) is not None)
ok("two of the same square is refused",
   m2.build_choose(LEFT, {"cells": [[2, 3], [2, 3]]}) is not None)
ok("one square is not a door", m2.build_choose(LEFT, {"cells": [[2, 3]]}) is not None)
ok("a square off the board is refused",
   m2.build_choose(LEFT, {"cells": [[2, 3], [99, 9]]}) is not None)
m2.build_choose(LEFT, {"cells": [[2, 3], [8, 3]]})
ok("once built, deployment opens", m2.phase == "setup", m2.phase)

# a door is one step for its own side
m, art, ally, gk, d = artisan_arena()
ok("the far door is a neighbour of the near one for its builder",
   (8, 3) in [tuple(c) for c in m.legal_moves(art)], str(m.legal_moves(art)))
ok("...so a hero standing on it crosses the whole board in one step",
   m.topology.distance((2, 3), (8, 3)) == 6, "the board still measures 6")

# and a plain square for everyone else
m, art, ally, gk, d = artisan_arena()
gk.set_cell((8, 3))
ok("the enemy is offered no such step",
   (2, 3) not in [tuple(c) for c in m.legal_moves(gk)], str(m.legal_moves(gk)))

# walking through it really moves the hero
m, art, ally, gk, d = artisan_arena()
turn(m, art.id, {"destination": [8, 3], "action": {"key": "none"}}, d.id, hold)
ok("it steps through and comes out the other side", art.cell == (8, 3), str(art.cell))

# the board itself is unchanged: nothing else treats them as neighbours
m, art, ally, gk, d = artisan_arena()
ok("the board's own neighbours are untouched",
   sorted(m.topology.neighbours((2, 3))) == sorted([(1, 3), (3, 3), (2, 2), (2, 4)]),
   str(m.topology.neighbours((2, 3))))

# both seats are told where the doors are
m, art, ally, gk, d = artisan_arena()
for side, who in ((LEFT, "its builder"), (RIGHT, "the other side")):
    doors = view.state_for(m, side)["doors"]
    ok(f"{who} is shown the doors",
       len(doors) == 1 and doors[0]["owner"] == LEFT
       and sorted(map(tuple, doors[0]["cells"])) == [(2, 3), (8, 3)], str(doors))

# 65 — 军火商人: opens everyone's purse, then sells them what to do with it
def dealer_arena():
    m = arena([("arms_dealer", (2, 2)), ("gatekeeper", (2, 3))],
              [("cannoneer", (8, 2)), ("dummy", (8, 3))])
    return (m, unit(m, LEFT, "arms_dealer"), unit(m, LEFT, "gatekeeper"),
            unit(m, RIGHT, "cannoneer"), unit(m, RIGHT, "dummy"))

buy = lambda what: {"destination": None, "action": {"key": "none"},
                    "choices": {"armory": what}}

# the bar is open for the whole side, and closes when it falls
m, ad, ally, cannon, d = dealer_arena()
ok("its own side banks without limit", ally.max_ap == 99, str(ally.max_ap))
ok("...and so does it", ad.max_ap == 99, str(ad.max_ap))
ok("the enemy's purse is untouched", cannon.max_ap == HEROES.BY_KEY["cannoneer"].max_ap,
   str(cannon.max_ap))
ally.ap = 40
DMG.apply_batch(m, [DMG.DamageEvent(source=cannon, target=ad, amount=99,
                                    category=DMG.NORMAL_ATTACK)])
ok("the bar closes when the dealer falls",
   ally.max_ap == HEROES.BY_KEY["gatekeeper"].max_ap, str(ally.max_ap))
ok("...and everything banked above the old ceiling is gone",
   ally.ap == ally.max_ap, f"{ally.ap}/{ally.max_ap}")

# a sale: the buyer pays, the dealer pockets it, the weapon is theirs
m, ad, ally, cannon, d = dealer_arena()
ally.ap = 6
turn(m, ad.id, buy(f"cannon:{ally.id}"), d.id, hold)
ok("the buyer is charged the listed price", ally.ap == 2, f"{ally.ap} AP")
ok("...and the dealer pockets it", ad.ap == 4 + 1, f"{ad.ap} AP (+1 at turn end)")
ok("the weapon replaces its attack", ally.atk == 4 and ally.grid == 3 and ally.rng == 5,
   f"atk {ally.atk} grid {ally.grid} rng {ally.rng}")
ok("...and it says so on the card",
   any(b["key"] == "armed" for b in HEROES.status_of(m, ally)))

# a hero's own buffs still ride on the weapon
m, ad, ally, cannon, d = dealer_arena()
ally.ap = 2
ally.add_modifier(Modifier("atk", "add", 5))
turn(m, ad.id, buy(f"rifle:{ally.id}"), d.id, hold)
ok("the weapon sets the base and buffs still ride on it", ally.atk == 3 + 5,
   str(ally.atk))

# it does not sell to itself
m, ad, ally, cannon, d = dealer_arena()
ad.ap = 9
wares = ad.passives[0].turn_choice(m, ad)["options"]
ok("it is not on its own shelf", not any(w.endswith(f":{ad.id}") for w in wares),
   str(wares))

# fuel: every point spent is a point of damage
m, ad, ally, cannon, d = dealer_arena()
ad.ap = 4
cannon.set_cell((5, 2))
hp0 = cannon.hp
turn(m, ad.id, {"destination": None,
                "action": {"key": "attack", "shots": [[[5, 2]]], "spend": 3}},
     d.id, hold)
ok("a fed shot hits for its attack plus the fuel", hp0 - cannon.hp == 1 + 3,
   f"took {hp0 - cannon.hp}")
ok("...and the fuel is gone", ad.ap == 1 + 1, f"{ad.ap} AP (+1 at turn end)")
m.select_hero(LEFT, ad.id)
ok("it cannot spend what it does not have",
   m.commit(LEFT, {"destination": None,
                   "action": {"key": "attack", "shots": [[[5, 2]]], "spend": 99}}) is not None)
m.deselect(LEFT)

# an ordinary hero's shot takes no fuel
m, ad, ally, cannon, d = dealer_arena()
m.select_hero(LEFT, ally.id)
ok("a hero without the trade cannot feed its shot",
   m.commit(LEFT, {"destination": None,
                   "action": {"key": "attack", "shots": [[[2, 2]]], "spend": 1}}) is not None)
m.deselect(LEFT)

# the nuke: everything at once, and then nothing ever again
m, ad, ally, cannon, d = dealer_arena()
ally.ap = 5
turn(m, ad.id, buy(f"nuke:{ally.id}"), d.id, hold)
ok("the warhead is fitted", ally.vars["arms"]["key"] == "nuke")
hp0 = (cannon.hp, d.hp)
r0 = m.round
while m.round == r0:
    left = [e.id for e in m.unacted(LEFT)]
    right = [e.id for e in m.unacted(RIGHT)]
    turn(m, left[0] if left else None, hold, right[0] if right else None, hold)
turn(m, ally.id, {"destination": None, "action": {"key": "attack"}}, cannon.id, hold)
ok("it catches every enemy on the board",
   (hp0[0] - cannon.hp, hp0[1] - d.hp) == (6, 6),
   f"{hp0[0]-cannon.hp}, {hp0[1]-d.hp}")
ok("...and that hero never attacks again",
   not any(a["key"] == "attack" for a in m.action_menu(ally)),
   str([a["key"] for a in m.action_menu(ally)]))

# but it can be re-armed
m2, ad2, ally2, c2, d2 = dealer_arena()
ally2.vars["arms"] = dict(HEROES.ARMS_BY_KEY["nuke"], attack={"mode": None}, spent=True)
ally2.ap = 2
turn(m2, ad2.id, buy(f"rifle:{ally2.id}"), d2.id, hold)
ok("a spent hero can be handed something new",
   any(a["key"] == "attack" for a in m2.action_menu(ally2)) and ally2.atk == 3,
   f"atk {ally2.atk}")

# 66 — regressions found hunting through the newest heroes

# 蛇帝 is one creature on two squares: nothing that sweeps an area hits it twice
def snake_target_arena(left):
    m = arena(list(left), [("snake_head", (7, 2)), ("snake_tail", (7, 3))])
    return m, unit(m, RIGHT, "snake_head"), unit(m, RIGHT, "snake_tail")

m, hd3, tl3 = snake_target_arena([("thunder_dragon", (3, 2)), ("gatekeeper", (3, 3))])
td = unit(m, LEFT, "thunder_dragon")
ok("an ability that hits every enemy counts the snake once",
   len(td.abilities[0].build_damage(m, td, {})) == 1,
   str(len(td.abilities[0].build_damage(m, td, {}))))

def snake_holds(m, hd, tl):
    """The snake's own order: both halves stand where they are."""
    return {"orders": [{"entity": hd.id, "destination": list(hd.cell),
                        "action": {"key": "none"}},
                       {"entity": tl.id, "destination": list(tl.cell),
                        "action": {"key": "none"}}]}

m, hd3, tl3 = snake_target_arena([("swordsman", (3, 2)), ("gatekeeper", (3, 3))])
sw = unit(m, LEFT, "swordsman"); sw.ap = sw.max_ap
tl3.set_cell((6, 2))                       # both halves in the same row, still joined
hp0 = hd3.hp
m.select_hero(LEFT, sw.id)
assert m.commit(LEFT, {"destination": None,
                       "action": {"key": "ability:gale_slash", "direction": "row"}}) is None
m.select_hero(RIGHT, hd3.id)
assert m.commit(RIGHT, snake_holds(m, hd3, tl3)) is None
ok("a cut down a row catches the snake once, not once per half",
   hp0 - hd3.hp == 5, f"took {hp0 - hd3.hp}")

m, hd3, tl3 = snake_target_arena([("arms_dealer", (3, 2)), ("gatekeeper", (3, 3))])
gk3 = unit(m, LEFT, "gatekeeper")
gk3.vars["arms"] = dict(HEROES.ARMS_BY_KEY["nuke"])
hp0 = hd3.hp
m.select_hero(LEFT, gk3.id); m.commit(LEFT, {"destination": None, "action": {"key": "attack"}})
m.select_hero(RIGHT, hd3.id)
m.commit(RIGHT, {"orders": [{"entity": hd3.id, "destination": list(hd3.cell),
                             "action": {"key": "none"}},
                            {"entity": tl3.id, "destination": list(tl3.cell),
                             "action": {"key": "none"}}]})
ok("a board-wide blow catches it once", hp0 - hd3.hp == 6, f"took {hp0 - hd3.hp}")

# ...and neither does the ground under both of its squares
m = arena([("plague_doctor", (2, 2)), ("gatekeeper", (2, 3))],
          [("snake_head", (7, 2)), ("snake_tail", (7, 3))])
assert m.opening_choose(LEFT, {"cell": [1, 5]}) is None
hd4, tl4 = unit(m, RIGHT, "snake_head"), unit(m, RIGHT, "snake_tail")
m.board.add_effect(hd4.cell, BOARD.Infection(LEFT))
m.board.add_effect(tl4.cell, BOARD.Infection(LEFT))
hp0 = hd4.hp
m.select_hero(LEFT, unit(m, LEFT, "gatekeeper").id); m.commit(LEFT, hold)
m.select_hero(RIGHT, hd4.id)
m.commit(RIGHT, {"orders": [{"entity": hd4.id, "destination": list(hd4.cell),
                             "action": {"key": "none"}},
                            {"entity": tl4.id, "destination": list(tl4.cell),
                             "action": {"key": "none"}}]})
ok("bad ground under both halves bites the creature once", hp0 - hd4.hp == 2,
   f"took {hp0 - hd4.hp}")

# a one-bodied hero is unaffected by any of that
m = arena([("fire_mage", (2, 2)), ("gatekeeper", (2, 3))],
          [("cannoneer", (8, 2)), ("dummy", (8, 3))])
c5 = unit(m, RIGHT, "cannoneer")
burn = m.board.add_burning(c5.cell, LEFT).damage
hp0 = c5.hp
m.select_hero(LEFT, unit(m, LEFT, "gatekeeper").id); m.commit(LEFT, hold)
m.select_hero(RIGHT, c5.id); m.commit(RIGHT, hold)
ok("an ordinary hero still takes its ground tick", hp0 - c5.hp == burn,
   f"took {hp0 - c5.hp}")

# 教皇: one Pope may step in front of another's end, but never its own
m = arena([("pope", (2, 2)), ("pope", (2, 3))],
          [("cannoneer", (8, 2)), ("dummy", (8, 3))])
pa, pb = [e for e in m.living(LEFT) if e.key == "pope"]
c6 = unit(m, RIGHT, "cannoneer")
pb.set_cell((7, 2)); pb.hp = 2
m.select_hero(LEFT, pa.id); m.commit(LEFT, hold)
m.select_hero(RIGHT, c6.id)
m.commit(RIGHT, {"destination": None, "action": {"key": "attack", "shots": [[[7, 2]]]}})
ok("a second Pope's end is offered to the first", m.phase == "interrupt", m.phase)
m.choose_interrupt(LEFT, True)
ok("...and it can be stepped in front of", pb.alive and pb.hp == 2, f"{pb.hp} hp")
ok("...spending the other's mercy, not its own",
   pa.vars.get("saves_used") == 1 and not pb.vars.get("saves_used"),
   f"{pa.vars.get('saves_used')}/{pb.vars.get('saves_used')}")

# 军火商人: a spent warhead leaves the hero its own attack number back
m = arena([("arms_dealer", (2, 2)), ("gatekeeper", (2, 3))], [("dummy", (8, 3))])
gk7 = unit(m, LEFT, "gatekeeper")
own = gk7.atk
gk7.vars["arms"] = dict(HEROES.ARMS_BY_KEY["nuke"], attack={"mode": None}, spent=True)
ok("a spent hero does not keep claiming the warhead's number", gk7.atk == own,
   f"{gk7.atk} vs its own {own}")
ok("...and genuinely has no attack",
   not any(a["key"] == "attack" for a in m.action_menu(gk7)))

# 67 — 雪女: the whole board at once, and the cold with it
def snow_arena():
    m = arena([("snow_woman", (3, 3)), ("gatekeeper", (3, 1))],
              [("thunder_dragon", (7, 3)), ("cannoneer", (7, 1)), ("dummy", (7, 2))])
    sw = unit(m, LEFT, "snow_woman"); sw.ap = sw.max_ap
    return (m, sw, unit(m, LEFT, "gatekeeper"), unit(m, RIGHT, "thunder_dragon"),
            unit(m, RIGHT, "cannoneer"), unit(m, RIGHT, "dummy"))

fall = {"destination": None, "action": {"key": "ability:avalanche"}}

m, sw, ally, td, cannon, d = snow_arena()
td.ap = 3
hp0 = (td.hp, cannon.hp, d.hp, ally.hp)
turn(m, sw.id, fall, d.id, hold)
ok("it buries every enemy on the board, wherever they stand",
   (hp0[0] - td.hp, hp0[1] - cannon.hp, hp0[2] - d.hp) == (6, 6, 6),
   f"{hp0[0]-td.hp}, {hp0[1]-cannon.hp}, {hp0[2]-d.hp}")
ok("...and leaves your own line alone", ally.hp == hp0[3], f"took {hp0[3] - ally.hp}")
ok("the cold takes a point of AP", td.ap == 2, str(td.ap))
ok("...and cannot take one from an empty bar", cannon.ap == 0, str(cannon.ap))
ok("it costs the whole bar", sw.ap == 0 + 1, f"{sw.ap} AP (+1 at turn end)")

# it is water, and it counts one creature once
m = arena([("snow_woman", (3, 3)), ("gatekeeper", (3, 1))],
          [("snake_head", (7, 2)), ("snake_tail", (7, 3))])
sw2 = unit(m, LEFT, "snow_woman"); sw2.ap = sw2.max_ap
hd5 = unit(m, RIGHT, "snake_head")
ok("a two-bodied hero is buried once, not twice",
   len(sw2.abilities[0].build_damage(m, sw2, {})) == 1,
   str(len(sw2.abilities[0].build_damage(m, sw2, {}))))
ok("...and the blow is water",
   sw2.abilities[0].build_damage(m, sw2, {})[0].element == DMG.WATER)

# 68 — 世界树: stands in the middle, and your own heroes cut it down
def tree_arena():
    m = Match()
    m.assign_draft(["world_tree", "cannoneer", "gatekeeper"],
                   ["cannoneer", "dummy", "berserker"])
    for k, c in (("cannoneer", (3, 2)), ("gatekeeper", (3, 3))):
        assert m.place(LEFT, k, c) is None, (k, c)
    for k, c in (("cannoneer", (7, 2)), ("dummy", (7, 3)), ("berserker", (7, 4))):
        assert m.place(RIGHT, k, c) is None, (k, c)
    assert m.lock_force(LEFT) is None
    assert m.lock_force(RIGHT) is None
    tree = next(e for e in m.entities if e.key == "world_tree")
    return m, tree, unit(m, LEFT, "cannoneer"), unit(m, LEFT, "gatekeeper")

chop = {"destination": None, "action": {"key": "attack", "shots": [[[5, 3]]]}}

def answer_all(m, pick=None):
    guard = 0
    while m.phase == "interrupt" and m.interrupts and guard < 12:
        guard += 1
        t = m.interrupts[0]
        m.choose_interrupt(t["side"], pick(t) if pick else t["options"][0])

def chop_once(m, tree, auto=True):
    """One exchange in which whoever can reach the tree strikes it. `auto` answers
    whatever the strike raises; pass False to inspect it instead."""
    L = [e for e in m.unacted(LEFT)]
    R = [e for e in m.unacted(RIGHT)]
    if L:
        m.select_hero(LEFT, L[0].id)
        reach = m.topology.distance(L[0].cell, (5, 3)) <= (L[0].rng or 0)
        m.commit(LEFT, chop if reach else hold)
    if R and m.phase == "commit":
        m.select_hero(RIGHT, R[0].id); m.commit(RIGHT, hold)
    if auto:
        answer_all(m)

m, tree, can, gk = tree_arena()
ok("it stands in the middle without being placed", tree.cell == (5, 3), str(tree.cell))
ok("...taking no square of your deployment zone",
   sorted(m.deploy_bodies(LEFT)) == ["cannoneer", "gatekeeper"], str(m.deploy_bodies(LEFT)))
ok("it never takes a turn", not tree.flags["takes_turns"])
ok("...is not a life to lose", not tree.flags["counts_for_defeat"])
ok("...and blocks the middle", tree.flags["blocks_movement"]
   and m.occupant((5, 3)) is tree)
ok("the enemy cannot lay a finger on it",
   not m.enemies_in([(5, 3)], RIGHT), str(m.enemies_in([(5, 3)], RIGHT)))
ok("...but your own may strike it",
   [e.id for e in m.strikeable_allies([(5, 3)], LEFT)] == [tree.id])

# first strike — 长冬
foe = unit(m, RIGHT, "cannoneer")
mv0 = foe.move_allowance
chop_once(m, tree)
ok("the first strike counts", tree.vars.get("struck") == 1, str(tree.vars.get("struck")))
ok("...and takes nothing off the tree", tree.hp == tree.max_hp, f"{tree.hp}")
ok("长冬 slows every enemy by a square", foe.move_allowance == mv0 - 1,
   f"{mv0} -> {foe.move_allowance}")
r0 = m.round
while m.round == r0:
    L = [e.id for e in m.unacted(LEFT)]; R = [e.id for e in m.unacted(RIGHT)]
    turn(m, L[0] if L else None, hold, R[0] if R else None, hold)
ok("...and thaws when the round turns over", foe.move_allowance == mv0,
   f"{foe.move_allowance}")

# third strike — the beasts
m, tree, can, gk = tree_arena()
while tree.vars.get("struck", 0) < 3 and m.phase == "commit":
    chop_once(m, tree, auto=False)
    if m.interrupts: break
ok("the third strike looses all three beasts",
   [t.get("amount") for t in m.interrupts] == [1, 2, 3],
   str([t.get("beast") for t in m.interrupts]))
ok("...and each names an enemy", m.interrupts[0]["option_kind"] == "unit")
victim = m.entity(m.interrupts[0]["options"][0])
hp0 = victim.hp
answer_all(m, pick=lambda t: victim.id)
ok("the same hero may be named by all three", hp0 - victim.hp == 1 + 2 + 3,
   f"took {hp0 - victim.hp}")

# fifth strike — it falls, and 洛基 walks out
while tree.alive and m.phase == "commit":
    chop_once(m, tree, auto=False)
    if m.interrupts: break
ok("the fifth strike brings it down", not tree.alive and tree.vars["struck"] == 5,
   f"alive={tree.alive} struck={tree.vars.get('struck')}")
ok("...and the middle is free again", m.occupant((5, 3)) is None)
ok("the ground under what the beasts bit is alight",
   m.board.has_kind(victim.cell, "burning"), str(m.board.serialise()))
ok("洛基 is asked for a square", m.interrupts and m.interrupts[0]["key"] == "loki",
   str(m.interrupts[:1]))
answer_all(m, pick=lambda t: [1, 5])
loki = next((e for e in m.living(LEFT) if e.key == "loki"), None)
ok("洛基 walks out where you asked", loki is not None and loki.cell == (1, 5),
   str(loki and loki.cell))
ok("...at his own stats", loki and (loki.max_hp, loki.atk, loki.rng, loki.grid,
                                    loki.move_allowance) == (10, 5, 2, 2, 2),
   f"{loki.max_hp}/{loki.atk}/{loki.rng}/{loki.grid}/{loki.move_allowance}")
ok("...and he takes turns like anything else",
   loki.flags["takes_turns"] and loki.flags["counts_for_defeat"])

# 69 — 渔夫: hauls whatever it can reach out of their line and into yours
def fisher_arena():
    m = arena([("fisherman", (3, 3)), ("gatekeeper", (3, 1))],
              [("cannoneer", (7, 3)), ("dummy", (7, 1))])
    fm = unit(m, LEFT, "fisherman"); fm.ap = fm.max_ap
    return m, fm, unit(m, LEFT, "gatekeeper"), unit(m, RIGHT, "cannoneer"), unit(m, RIGHT, "dummy")

cast = lambda d: {"destination": None, "action": {"key": "ability:hook", "direction": d}}

# straight down the lane
m, fm, ally, cannon, d = fisher_arena()
hp0 = cannon.hp
turn(m, fm.id, cast("forward"), d.id, hold)
ok("it hauls the catch in beside you", cannon.cell == (4, 3), str(cannon.cell))
ok("...and the haul is no blow", cannon.hp == hp0, f"took {hp0 - cannon.hp}")

# it reaches as far as the lane runs
m, fm, ally, cannon, d = fisher_arena()
cannon.set_cell((9, 3))
turn(m, fm.id, cast("forward"), d.id, hold)
ok("the lane runs to the board's edge", cannon.cell == (4, 3), str(cannon.cell))

# diagonals count among the eight
m, fm, ally, cannon, d = fisher_arena()
cannon.set_cell((5, 5))                  # on the forward-and-down lane from C3
turn(m, fm.id, cast("fwd_down"), d.id, hold)
ok("a corner lane throws just as well", cannon.cell == (4, 4), str(cannon.cell))

# one of your own in the way spoils it
m, fm, ally, cannon, d = fisher_arena()
ally.set_cell((5, 3))
m.select_hero(LEFT, fm.id)
ok("one of your own down the lane spoils the throw",
   m.commit(LEFT, cast("forward")) is not None)
ok("...and that lane is not offered",
   "forward" not in [l["dir"] for l in fm.abilities[0].lanes(m, fm)],
   str([l["dir"] for l in fm.abilities[0].lanes(m, fm)]))
m.deselect(LEFT)

# no room to haul it in to
m, fm, ally, cannon, d = fisher_arena()
ally.set_cell((4, 3))
m.select_hero(LEFT, fm.id)
ok("a taken square leaves nowhere to haul it", m.commit(LEFT, cast("forward")) is not None)
m.deselect(LEFT)

# an empty lane catches nothing
m, fm, ally, cannon, d = fisher_arena()
m.select_hero(LEFT, fm.id)
ok("an empty lane catches nothing", m.commit(LEFT, cast("up")) is not None)
m.deselect(LEFT)

# the haul happens with movement, so it can drag somebody into a marked square
m, fm, ally, cannon, d = fisher_arena()
hp0 = cannon.hp
m.select_hero(LEFT, fm.id); m.commit(LEFT, cast("forward"))
m.select_hero(RIGHT, d.id); m.commit(RIGHT, hold)
ok("it lands where the hook put it", cannon.cell == (4, 3))
ok("...having taken nothing on the way", cannon.hp == hp0)

# 70 — nothing the board calls untouchable is a target, however it is aimed
def untouchable_arena(foe_key, at=(6, 3)):
    m = Match()
    m.assign_draft(["world_tree", "gatekeeper"], [foe_key, "dummy"])
    assert m.place(LEFT, "gatekeeper", (3, 3)) is None
    assert m.place(RIGHT, foe_key, (7, 3)) is None
    assert m.place(RIGHT, "dummy", (7, 1)) is None
    assert m.lock_force(LEFT) is None
    assert m.lock_force(RIGHT) is None
    tree = next(e for e in m.entities if e.key == "world_tree")
    foe = unit(m, RIGHT, foe_key)
    foe.ap = foe.max_ap
    # Stand it right next to the tree, so nothing is ever refused for range.
    foe.set_cell(at)
    m.select_hero(RIGHT, foe.id)
    return m, tree, foe

def refused(foe_key, key, at=(6, 3), **params):
    m, tree, foe = untouchable_arena(foe_key, at)
    a = {"key": key}
    for k, v in params.items():
        a[k] = tree.id if v == "TREE" else (foe.id if v == "SELF" else v)
    if a.get("targets") == "TREE_LIST":
        a["targets"] = [tree.id]
    return m.commit(RIGHT, {"destination": None, "action": a})

for who, key in (("druid", "ability:pierce"), ("assassin", "ability:garrote"),
                 ("ghost", "ability:possess"), ("strongman", "ability:slam")):
    why = refused(who, key, target="TREE")
    ok(f"{who} cannot name the tree with an ability", why is not None, repr(why))

ok("nor can 魔术师 shuffle it out of the middle",
   refused("magician", "ability:transfer", first="TREE", second="SELF") is not None)
ok("nor can a unit-locked attack commit to it",
   refused("thunder_dragon", "attack", targets="TREE_LIST") is not None)

m, tree, cen = untouchable_arena("centaur", (7, 3))
lane = cen.abilities[0].path(m, cen, "forward")
ok("nor does a charge trample it on the way past",
   lane is None or tree.id not in [v.id for v in lane[1]])

# the same gate must not close on the side that is *meant* to strike it
m, tree, _ = untouchable_arena("dummy")
friend = unit(m, LEFT, "gatekeeper")
friend.set_cell((4, 3))
ok("your own may still take an axe to it",
   [e.id for e in m.strikeable_allies([(5, 3)], LEFT)] == [tree.id])
m.select_hero(LEFT, friend.id)
ok("...and the strike is accepted",
   m.commit(LEFT, {"destination": None,
                   "action": {"key": "attack", "shots": [[[5, 3]]]}}) is None)

# and the bodiless 鬼魂 is covered by the same rule
m2 = Match()
m2.assign_draft(["ghost", "dummy"], ["druid", "dummy"])
for side, ks in ((LEFT, (("ghost", (3, 2)), ("dummy", (3, 3)))),
                 (RIGHT, (("druid", (7, 2)), ("dummy", (7, 3))))):
    for k, c in ks:
        assert m2.place(side, k, c) is None, (k, c)
assert m2.lock_force(LEFT) is None
assert m2.lock_force(RIGHT) is None
gh = unit(m2, LEFT, "ghost")
dr = unit(m2, RIGHT, "druid")
dr.ap = dr.max_ap
dr.set_cell((4, 2))
m2.select_hero(RIGHT, dr.id)
gh.flags["targetable"] = False          # as it is between manifestations
ok("...and a hero with no body to hit is off limits by the same rule",
   m2.commit(RIGHT, {"destination": None,
                     "action": {"key": "ability:pierce", "target": gh.id}}) is not None)

# 71 — 探险家: three rounds on a board of its own, then whatever it dug up
def island_arena(T=((4, 1), (4, 2), (4, 3), (5, 2)), stand=(4, 2), foes=None):
    m = Match()
    m.assign_draft(["explorer", "cannoneer", "gatekeeper"],
                   ["cannoneer", "dummy", "berserker"])
    assert m.build_choose(LEFT, {"cells": [list(c) for c in T]}) is None
    assert m.build_choose(LEFT, {"cell": list(stand)}) is None
    for k, c in (("cannoneer", (3, 3)), ("gatekeeper", (3, 1))):
        assert m.place(LEFT, k, c) is None, (k, c)
    for k, c in (("cannoneer", (7, 2)), ("dummy", (7, 3)), ("berserker", (7, 4))):
        assert m.place(RIGHT, k, c) is None, (k, c)
    assert m.lock_force(LEFT) is None
    assert m.lock_force(RIGHT) is None
    return m, next(e for e in m.entities if e.key == "explorer")

def island_round(m, ex, ability, cell):
    """One whole round: the explorer digs, everybody else holds."""
    r0, guard = m.round, 0
    todo = ability
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
                assert m.commit(side, {"destination": None, "action": {
                    "key": "ability:" + todo, "cell": list(cell)}}) is None
                todo = None
            else:
                m.commit(side, hold)

def island_run(ability, cells=((4, 1), (4, 3), (5, 2))):
    m, ex = island_arena()
    for cell in cells:
        island_round(m, ex, ability, cell)
    return m, ex

# --- the T, and the ground it takes out of the board
ok("four squares in a line are not a T", not HEROES.t_tetromino([(1, 1), (2, 1), (3, 1), (4, 1)]))
ok("a square block is not a T", not HEROES.t_tetromino([(1, 1), (2, 1), (1, 2), (2, 2)]))
ok("a bar with a stem off its middle is", HEROES.t_tetromino([(1, 1), (2, 1), (3, 1), (2, 2)]))
ok("...and so is the same shape turned on its side",
   HEROES.t_tetromino([(4, 1), (4, 2), (4, 3), (5, 2)]))
ok("a stem off the end is not", not HEROES.t_tetromino([(1, 1), (2, 1), (3, 1), (1, 2)]))

m, ex = island_arena()
isle = [(4, 1), (4, 2), (4, 3), (5, 2)]
ok("the island leaves the board", not [c for c in isle if c in m.topology.all_cells()],
   str([c for c in isle if c in m.topology.all_cells()]))
ok("...taking those squares out of the deployment zone",
   not [c for c in isle if c in m.topology.deployment_zone(LEFT)])
ok("...and it is no neighbour of the mainland",
   (4, 2) not in m.topology.neighbours((3, 2)), str(m.topology.neighbours((3, 2))))
ok("...while its own squares still touch each other",
   sorted(m.topology.neighbours((4, 2))) == sorted([(4, 1), (4, 3), (5, 2)]),
   str(m.topology.neighbours((4, 2))))
ok("nothing on the mainland is at any reach of it",
   m.topology.distance((3, 3), (4, 3)) > m.topology.cols, str(m.topology.distance((3, 3), (4, 3))))
ok("the explorer takes no square of the deployment zone",
   sorted(m.deploy_bodies(LEFT)) == ["cannoneer", "gatekeeper"], str(m.deploy_bodies(LEFT)))
ok("it stands where it said it would", ex.cell == (4, 2), str(ex.cell))
ok("...where the enemy cannot lay a finger on it", not ex.flags["targetable"])
ok("...and does not walk at all while it is out there",
   m.legal_moves(ex) == [[4, 2]], str(m.legal_moves(ex)))
ok("digging is the only thing it may do",
   sorted(a["key"] for a in m.action_menu(ex))
   == ["ability:dig_grapes", "ability:mine_ore", "ability:train_natives"],
   str([a["key"] for a in m.action_menu(ex)]))
ok("...and it may only dig its own free ground",
   sorted(map(tuple, m.action_menu(ex)[0]["targeting"]["cells"])) == [(4, 1), (4, 3), (5, 2)],
   str(m.action_menu(ex)[0]["targeting"]["cells"]))

# the world tree gets the middle first
m2 = Match()
m2.assign_draft(["explorer", "world_tree", "gatekeeper"], ["dummy", "dummy", "berserker"])
ok("an island may not be charted over 世界树",
   m2.build_choose(LEFT, {"cells": [[4, 3], [5, 3], [6, 3], [5, 2]]}) is not None,
   repr(m2.build_choose(LEFT, {"cells": [[4, 3], [5, 3], [6, 3], [5, 2]]})))
ok("...but the same T one square over is fine",
   m2.build_choose(LEFT, {"cells": [[1, 1], [1, 2], [1, 3], [2, 2]]}) is None)

# --- 完全矿物装甲
m, ex = island_run("mine_ore")
ok("三矿物 arrives armoured", (ex.hp, ex.max_hp) == (18, 18), f"{ex.hp}/{ex.max_hp}")
ok("...with the health as well as the ceiling", ex.hp == ex.max_hp, f"{ex.hp}")
ok("...and sharper for it", (ex.atk, ex.rng) == (5, 4), f"atk {ex.atk} rng {ex.rng}")
ok("the island is back on the board", m.topology.region((4, 2)) is None)
ok("...and the explorer can be reached at last", ex.flags["targetable"])
ok("...and does ordinary things again",
   "none" in [a["key"] for a in m.action_menu(ex)], str([a["key"] for a in m.action_menu(ex)]))

# --- 反动
m, ex = island_run("train_natives")
natives = [e for e in m.living(LEFT) if e.key == "native"]
ok("三奴隶 raises three of them", len(natives) == 3, str(len(natives)))
ok("...each the stronger for the revolt",
   [(e.hp, e.max_hp) for e in natives] == [(7, 7)] * 3, str([(e.hp, e.max_hp) for e in natives]))
ok("...and they take their own turns now", all(e.flags["takes_turns"] for e in natives))
ok("...and can be reached", all(e.flags["targetable"] for e in natives))
ok("the explorer pays for it with its life", not ex.alive)
ok("...but the side is not beaten while its 土著 stand",
   m.phase != "gameover" and [e for e in m.living(LEFT) if e.flags["counts_for_defeat"]],
   m.phase)

# the 土著's blow is measured against what it hits
foe = unit(m, RIGHT, "berserker")
n = natives[0]
n.set_cell((6, 4)); n.ap = n.max_ap
hp0, expect = foe.hp, foe.max_hp // 6
m.select_hero(LEFT, n.id)
m.commit(LEFT, {"destination": None, "action": {"key": "attack", "shots": [[list(foe.cell)]]}})
r = m.unacted(RIGHT)
if r:
    m.select_hero(RIGHT, r[0].id); m.commit(RIGHT, hold)
ok("a 土著 takes a sixth of whatever it hits", hp0 - foe.hp == expect,
   f"{hp0}->{foe.hp}, wanted {expect} of {foe.max_hp}")
ok("...which is not its own stat line", expect != n.atk, f"{expect} vs atk {n.atk}")

# --- 大葡萄园
m, ex = island_run("dig_grapes")
vines = [(c, e) for c, effs in m.board.effects.items() for e in effs if e.kind == "vineyard"]
ok("三葡萄 leaves three vines", len(vines) == 3, str(len(vines)))
ok("...every one of them a 大葡萄园", all(e.great for _, e in vines))
gk = unit(m, LEFT, "gatekeeper")
gk.hp = 5; gk.set_cell((4, 4))
m.select_hero(LEFT, gk.id)
m.commit(LEFT, {"destination": [4, 3], "action": {"key": "none"}})
r = m.unacted(RIGHT)
m.select_hero(RIGHT, r[0].id); m.commit(RIGHT, hold)
ok("the first friend to reach a vine mends 4", gk.hp == 9, str(gk.hp))
ok("...and that vine is spent",
   [e.spent for c, e in vines if c == (4, 3)] == [True])
before, r0, guard = gk.hp, m.round, 0
while m.round == r0 and guard < 20:
    guard += 1
    for side in (LEFT, RIGHT):
        if m.commits[side] is not None:
            continue
        un = m.unacted(side)
        if un:
            m.select_hero(side, un[0].id); m.commit(side, hold)
ok("...but the vineyard still mends 2 when the round turns over", gk.hp == before + 2,
   f"{before} -> {gk.hp}")

# --- 全为不同
m, ex = island_arena()
for ability, cell in (("dig_grapes", (4, 1)), ("train_natives", (4, 3)), ("mine_ore", (5, 2))):
    island_round(m, ex, ability, cell)
ok("一样一种 stops the board for three more", m.phase == "interrupt", m.phase)
ok("...one task per resource",
   [t["resource"] for t in m.interrupts] == ["grape", "slave", "mineral"],
   str([t.get("resource") for t in m.interrupts]))
ok("...offered anywhere on the mainland", (8, 1) in [tuple(c) for c in m.interrupts[0]["options"]])
ok("...and never on the enemy's head",
   tuple(unit(m, RIGHT, "dummy").cell) not in [tuple(c) for c in m.interrupts[0]["options"]])
taken = (6, 1)
m.choose_interrupt(LEFT, list(taken))
ok("a square just used is withdrawn from the rest",
   all(tuple(c) != taken for t in m.interrupts for c in t["options"]))
m.choose_interrupt(LEFT, [6, 2])
m.choose_interrupt(LEFT, [6, 3])
ok("the round picks up where it left off", m.phase == "commit", m.phase)
ok("...with a second vine on the mainland",
   sorted(c for c, effs in m.board.effects.items()
          for e in effs if e.kind == "vineyard") == [(4, 1), (6, 1)],
   str(sorted(c for c, effs in m.board.effects.items() for e in effs if e.kind == "vineyard")))
ok("...a second 土著 with it",
   sorted(e.cell for e in m.living(LEFT) if e.key == "native") == [(4, 3), (6, 2)],
   str(sorted(e.cell for e in m.living(LEFT) if e.key == "native")))
ok("...and two plates of armour on the explorer",
   (ex.max_hp, ex.atk, ex.rng) == (9, 4, 3), f"{ex.max_hp}/{ex.atk}/{ex.rng}")

# --- nothing on the mainland can reach in, and 工匠 cannot open onto it
m3 = Match()
m3.assign_draft(["explorer", "artisan", "gatekeeper"], ["dummy", "dummy", "berserker"])
assert m3.build_choose(LEFT, {"cells": [[4, 1], [4, 2], [4, 3], [5, 2]]}) is None
assert m3.build_choose(LEFT, {"cell": [4, 2]}) is None
_h, door = m3.build_ability(LEFT)
ok("工匠 is never offered an island square",
   not [c for c in ([4, 1], [4, 2], [4, 3], [5, 2])
        if c in m3.build_targeting(LEFT, door)["cells"]],
   str(m3.build_targeting(LEFT, door)["cells"])[:60])
ok("...and cannot open one by hand",
   m3.build_choose(LEFT, {"cells": [[2, 3], [4, 2]]}) is not None,
   repr(m3.build_choose(LEFT, {"cells": [[2, 3], [4, 2]]})))
ok("...though a mainland pair is fine",
   m3.build_choose(LEFT, {"cells": [[2, 3], [6, 3]]}) is None)

# the other order: the door goes up first, and the island may not swallow it
m4 = Match()
m4.assign_draft(["artisan", "explorer", "gatekeeper"], ["dummy", "dummy", "berserker"])
assert m4.build_choose(LEFT, {"cells": [[2, 3], [5, 3]]}) is None
ok("an island may not be charted over a door",
   m4.build_choose(LEFT, {"cells": [[5, 2], [5, 3], [5, 4], [6, 3]]}) is not None,
   repr(m4.build_choose(LEFT, {"cells": [[5, 2], [5, 3], [5, 4], [6, 3]]})))
ok("...but anywhere clear of it is fine",
   m4.build_choose(LEFT, {"cells": [[8, 1], [8, 2], [8, 3], [7, 2]]}) is None)

# 潜水者 cannot bury a charge out there either
m5 = Match()
m5.assign_draft(["explorer", "cannoneer", "gatekeeper"], ["diver", "dummy", "berserker"])
assert m5.build_choose(LEFT, {"cells": [[4, 1], [4, 2], [4, 3], [5, 2]]}) is None
assert m5.build_choose(LEFT, {"cell": [4, 2]}) is None
for k, c in (("cannoneer", (3, 3)), ("gatekeeper", (3, 1))):
    assert m5.place(LEFT, k, c) is None
for k, c in (("diver", (7, 2)), ("dummy", (7, 3)), ("berserker", (7, 4))):
    assert m5.place(RIGHT, k, c) is None
assert m5.lock_force(LEFT) is None
assert m5.lock_force(RIGHT) is None
ok("潜水者 cannot bury a charge on an island",
   m5.opening_choose(RIGHT, {"cell": [4, 1]}) is not None,
   repr(m5.opening_choose(RIGHT, {"cell": [4, 1]})))
ok("...but its own ground is fine", m5.opening_choose(RIGHT, {"cell": [6, 2]}) is None)
ex5 = next(e for e in m5.entities if e.key == "explorer")
ok("the explorer digs its own island", m5.validate_targeting(ex5, ex5.abilities[2], {"cell": [4, 1]}) is None)
ok("...and nowhere else", m5.validate_targeting(ex5, ex5.abilities[2], {"cell": [6, 3]}) is not None)
ok("an island is part of nobody's row", (4, 2) not in m5.topology.row(2), str(m5.topology.row(2)))
ok("...nor of any column", (4, 2) not in m5.topology.column(4), str(m5.topology.column(4)))
ok("...nor of the whole board", not [c for c in ((4, 1), (4, 2)) if c in m5.shape_cells((3, 3), "board")])
ok("...nor of what surrounds the square beside it",
   (4, 2) not in m5.surround8((3, 2)), str(sorted(m5.surround8((3, 2)))))

# --- the island is out of the game: no skill of either side reaches it
m6 = Match()
m6.assign_draft(["explorer", "elder", "arms_dealer"], ["mist_lady", "world_tree", "berserker"])
assert m6.build_choose(LEFT, {"cells": [[4, 1], [4, 2], [4, 3], [5, 2]]}) is None
assert m6.build_choose(LEFT, {"cell": [4, 2]}) is None
for k, c in (("elder", (3, 3)), ("arms_dealer", (3, 1))):
    assert m6.place(LEFT, k, c) is None, (k, c)
for k, c in (("mist_lady", (7, 2)), ("berserker", (7, 4))):
    assert m6.place(RIGHT, k, c) is None, (k, c)
assert m6.lock_force(LEFT) is None
assert m6.lock_force(RIGHT) is None
ex6 = next(e for e in m6.entities if e.key == "explorer")
tree6 = next(e for e in m6.entities if e.key == "world_tree")
elder6, ml6 = unit(m6, LEFT, "elder"), unit(m6, RIGHT, "mist_lady")
ad6 = unit(m6, LEFT, "arms_dealer")
mv6, rng6 = ex6.move_allowance, ex6.rng
shore6 = elder6.move_allowance       # 长冬 comes from the right, so it bites the left
tree6.passives[0]._long_winter(m6, tree6)
ok("长冬 does not reach an island", ex6.move_allowance == mv6, str(ex6.move_allowance))
ml6.abilities[0].side_effects(m6, ml6, {})
ok("...nor does 大雾", ex6.rng == rng6, str(ex6.rng))
ok("...nor a blessing of its own side",
   ex6.id not in elder6.abilities[0].blessable(m6, elder6))
ok("...nor 军火商人's open bar", ex6.max_ap == 0, str(ex6.max_ap))
ok("...and it is sold nothing either",
   not [o for o in ad6.passives[0].turn_choice(m6, ad6)["options"]
        if o["value"].split(":")[1] == str(ex6.id)])
ok("but the mainland feels every bit of it",
   elder6.move_allowance == shore6 - 1, f"{shore6} -> {elder6.move_allowance}")

# --- what takes no turn is never selectable, and never spends an exchange
m7 = Match()
m7.assign_draft(["world_tree", "cannoneer", "gatekeeper"], ["dummy", "dummy", "berserker"])
for k, c in (("cannoneer", (3, 3)), ("gatekeeper", (3, 1))):
    assert m7.place(LEFT, k, c) is None
for k, c in (("dummy", (7, 2)), ("dummy", (7, 3)), ("berserker", (7, 4))):
    assert m7.place(RIGHT, k, c) is None
assert m7.lock_force(LEFT) is None
assert m7.lock_force(RIGHT) is None
tree7 = next(e for e in m7.entities if e.key == "world_tree")
ok("世界树 cannot be picked for a turn", m7.select_hero(LEFT, tree7.id) is not None,
   repr(m7.select_hero(LEFT, tree7.id)))
m8, ex8 = island_arena()
island_round(m8, ex8, "train_natives", (4, 1))
nat8 = next(e for e in m8.living(LEFT) if e.key == "native")
ok("...and neither can a 土著 still out at sea",
   m8.select_hero(LEFT, nat8.id) is not None, repr(m8.select_hero(LEFT, nat8.id)))

# --- digging is the whole turn: it cannot walk onto what it just put down
m9, ex9 = island_arena()
m9.select_hero(LEFT, ex9.id)
ok("the explorer cannot walk while its island is off the board",
   m9.commit(LEFT, {"destination": [4, 1],
                    "action": {"key": "ability:train_natives", "cell": [4, 1]}}) is not None)
ok("...but standing still and digging is fine",
   m9.commit(LEFT, {"destination": [4, 2],
                    "action": {"key": "ability:train_natives", "cell": [4, 1]}}) is None)
for e in m9.unacted(RIGHT)[:1]:
    m9.select_hero(RIGHT, e.id); m9.commit(RIGHT, hold)
bodies9 = [e.cell for e in m9.living(LEFT) if e.cells]
ok("...and nothing ends up sharing a square",
   len(bodies9) == len(set(bodies9)), str(sorted(bodies9)))

# --- two explorers keep two separate islands
m10 = Match()
m10.assign_draft(["explorer", "cannoneer", "gatekeeper"], ["explorer", "dummy", "berserker"])
assert m10.build_choose(LEFT, {"cells": [[3, 1], [3, 2], [3, 3], [4, 2]]}) is None
ok("one island cannot be charted on top of another",
   m10.build_choose(RIGHT, {"cells": [[3, 1], [3, 2], [3, 3], [4, 2]]}) is not None)
assert m10.build_choose(RIGHT, {"cells": [[7, 1], [7, 2], [7, 3], [6, 2]]}) is None
assert m10.build_choose(LEFT, {"cell": [3, 2]}) is None
assert m10.build_choose(RIGHT, {"cell": [7, 2]}) is None
for k, c in (("cannoneer", (2, 3)), ("gatekeeper", (2, 1))):
    assert m10.place(LEFT, k, c) is None
for k, c in (("dummy", (8, 3)), ("berserker", (8, 4))):
    assert m10.place(RIGHT, k, c) is None
assert m10.lock_force(LEFT) is None
assert m10.lock_force(RIGHT) is None
exL = next(e for e in m10.entities if e.key == "explorer" and e.side == LEFT)
ok("the two islands are different maps",
   m10.topology.region((3, 2)) != m10.topology.region((7, 2)))
ok("...and neither explorer can dig the other's",
   m10.validate_targeting(exL, exL.abilities[2], {"cell": [7, 1]}) is not None)

# --- an attack passes clean over the island
m, ex = island_arena(T=((5, 1), (5, 2), (5, 3), (6, 2)), stand=(5, 2))
sn = m.spawn(HEROES.BY_KEY["sniper"], LEFT, (2, 2))   # row 2 is clear of its own
sn.ap = sn.max_ap
tgt = unit(m, RIGHT, "cannoneer")
tgt.set_cell((7, 2))
lanes = [l["dir"] for l in LineShot.lanes(m, sn)]
ok("a lane fires straight through an island", "forward" in lanes, str(lanes))
hit = LineShot.scan(m, sn, "forward")
ok("...and finds what is on the far side of it", hit and hit[0] is tgt,
   str(hit and hit[0].key))

# 72 — the six-hero board: 世界树, 军火商人, 探险家, 蛇帝, 武器大师, 鸟嘴医生
def six_arena():
    m = Match()
    m.assign_draft(["world_tree", "explorer", "arms_dealer"],
                   ["snake_emperor", "weapon_master", "plague_doctor"])
    assert m.build_choose(LEFT, {"cells": [[2, 1], [2, 2], [2, 3], [3, 2]]}) is None
    assert m.build_choose(LEFT, {"cell": [2, 2]}) is None
    assert m.place(LEFT, "arms_dealer", (1, 3)) is None
    for k, c in (("snake_head", (7, 3)), ("snake_tail", (8, 3)),
                 ("weapon_master", (7, 2)), ("plague_doctor", (7, 4))):
        assert m.place(RIGHT, k, c) is None, (k, c)
    assert m.lock_force(LEFT) is None
    assert m.lock_force(RIGHT) is None
    guard = 0
    while m.phase == "opening" and guard < 12:
        guard += 1
        for side in (LEFT, RIGHT):
            if m.opening is None or m.phase != "opening":
                break
            pend = m.opening["pending"][side]
            if pend:
                e = m.entity(pend[0]["entity"])
                ab = next(a for a in e.abilities if a.key == pend[0]["ability_key"])
                t = m.ability_targeting(e, ab)
                cells = t.get("cells") or [list(c) for c in m.topology.all_cells()]
                m.opening_choose(side, {"cell": list(cells[-1]), "target": e.id})
    return m

m6a = six_arena()
def who(k, side=None):
    return next(e for e in m6a.entities
                if e.key == k and (side is None or e.side == side))
tree6a, ex6a, ad6a = who("world_tree"), who("explorer"), who("arms_dealer")
hd6a, pd6a = who("snake_head"), who("plague_doctor")

ok("the board takes all six", m6a.phase == "commit", m6a.phase)
ok("...with 世界树 in the middle and the island off the board",
   tree6a.cell == (5, 3) and m6a.topology.region((2, 2)) is not None)

# 军火商人 opens a bar for the people who can use one
ok("军火商人 gives 世界树 no AP bar", tree6a.max_ap == 0, str(tree6a.max_ap))
ok("...nor anything still out at sea", ex6a.max_ap == 0, str(ex6a.max_ap))
ok("...but keeps its own", ad6a.max_ap == 99, str(ad6a.max_ap))
ok("...and sells to neither of them",
   not [o for o in ad6a.passives[0].turn_choice(m6a, ad6a)["options"]
        if o["value"].split(":")[1] in (str(tree6a.id), str(ex6a.id))])
# and it corrects itself once the island lands
ex6a.vars["dug"] = ["mineral"] * 3
m6a.round = Explorer_JOIN = 4
ex6a.passives[0].on_round_start(m6a, ex6a, {})
ad6a.passives[0]._open_the_bar(m6a, ad6a)
ok("the bar opens for 探险家 the moment its island lands", ex6a.max_ap == 99,
   str(ex6a.max_ap))
ok("...and 世界树 still gets none", tree6a.max_ap == 0, str(tree6a.max_ap))

# a nuke over the whole board counts a two-bodied hero once and skips what it cannot touch
m6b = six_arena()
ad6b = next(e for e in m6b.entities if e.key == "arms_dealer")
ad6b.vars["arms"] = HEROES.ARMS_BY_KEY["nuke"]
mode6 = ATK_MODE = None
import attacks as _ATK
mode6 = _ATK.mode_for(ad6b)
insts6 = mode6.build(m6b, ad6b, ad6b.cell, {"key": "attack"})
insts6 = insts6 if isinstance(insts6, list) else [insts6]
nuked = [ev.target for i in insts6 for ev in i.build_damage(m6b, None)]
keys6 = [e.key for e in nuked]
ok("核爆 counts 蛇帝 once, not once a body",
   keys6.count("snake_head") + keys6.count("snake_tail") == 1, str(keys6))
ok("...spares an island it cannot reach", "explorer" not in keys6, str(keys6))
ok("...and its own 世界树", "world_tree" not in keys6, str(keys6))

# 鸟嘴医生 may walk in anywhere on the board — but not onto an island
m6c = six_arena()
pd6c = next(e for e in m6c.entities if e.key == "plague_doctor")
plague = pd6c.abilities[0]
ok("鸟嘴医生 is never offered an island square to open on",
   not [c for c in plague.cells(m6c, pd6c) if m6c.topology.region(c) is not None])
for _ in range(9):
    m6c.board.spread_effects(m6c)
    m6c.round += 1
crept = [c for c, effs in m6c.board.effects.items()
         for e in effs if e.kind == "infection"]
ok("...and the plague never creeps onto one either",
   not [c for c in crept if m6c.topology.region(c) is not None], str(len(crept)))

# ground under a two-bodied hero is one patch of ground
m6d = six_arena()
hd6d = next(e for e in m6d.entities if e.key == "snake_head")
tl6d = next(e for e in m6d.entities if e.key == "snake_tail")
m6d.board.add_effect(hd6d.cell, BOARD.Infection(LEFT, 1))
m6d.board.add_effect(tl6d.cell, BOARD.Infection(LEFT, 1))
pool0 = hd6d.hp
m6d.select_hero(RIGHT, hd6d.id)
m6d.commit(RIGHT, hold)
for e in m6d.unacted(LEFT)[:1]:
    m6d.select_hero(LEFT, e.id); m6d.commit(LEFT, hold)
ok("infected ground under both halves bites 蛇帝 once",
   pool0 - hd6d.hp == BOARD.Infection.DAMAGE, f"{pool0} -> {hd6d.hp}")

# 洛基 comes out of the wreck onto the board, never onto an island
m6e = six_arena()
tree6e = next(e for e in m6e.entities if e.key == "world_tree")
tree6e.vars["struck"] = 4
tree6e.passives[0].on_struck(m6e, tree6e,
                             next(e for e in m6e.entities if e.key == "arms_dealer"))
lok = [t for t in m6e.interrupts if t.get("key") == "loki"]
ok("felling 世界树 calls 洛基 up", bool(lok))
ok("...but never onto an island",
   not [c for c in lok[0]["options"] if m6e.topology.region(tuple(c)) is not None])

# an opening pick must tell the client the same squares the validator will accept
m6f = six_arena()
pd6f = next(e for e in m6f.entities if e.key == "plague_doctor")
m6g = Match()
m6g.assign_draft(["plague_doctor", "cannoneer", "gatekeeper"],
                 ["dummy", "dummy", "berserker"])
for k, c in (("plague_doctor", (3, 3)), ("cannoneer", (3, 1)), ("gatekeeper", (3, 2))):
    assert m6g.place(LEFT, k, c) is None, (k, c)
for k, c in (("dummy", (7, 2)), ("dummy", (7, 3)), ("berserker", (7, 4))):
    assert m6g.place(RIGHT, k, c) is None
assert m6g.lock_force(LEFT) is None
assert m6g.lock_force(RIGHT) is None
task6 = view.state_for(m6g, LEFT)["opening"]["task"]
ok("an opening pick ships the squares it will actually accept",
   bool(task6 and task6["targeting"].get("cells")),
   str(task6 and task6["targeting"]))
ok("...and one of them is genuinely legal",
   m6g.opening_choose(LEFT, {"cell": task6["targeting"]["cells"][0]}) is None)

# 73 — things the random-match hunt turned up
# A squad commits as a squad however little of it is left.
m73 = Match()
m73.assign_draft(["goblin_gang", "cannoneer"], ["dummy", "berserker"])
for k, c in (("goblin_javelin", (3, 1)), ("goblin_javelin", (3, 2)),
             ("goblin_commander", (3, 3)), ("cannoneer", (2, 5))):
    assert m73.place(LEFT, k, c) is None, (k, c)
for k, c in (("dummy", (7, 3)), ("berserker", (7, 4))):
    assert m73.place(RIGHT, k, c) is None
assert m73.lock_force(LEFT) is None
assert m73.lock_force(RIGHT) is None
crew = [e for e in m73.living(LEFT) if e.hero.gang == "goblin_gang"]
for g in crew[:-1]:
    g.alive = False
    g.cells = set()
lone = crew[-1]
assert m73.select_hero(LEFT, lone.id) is None
ok("a lone goblin is still a squad turn, not a plain one",
   m73.commit(LEFT, {"destination": None, "action": {"key": "none"}}) is not None,
   repr(m73.commit(LEFT, {"destination": None, "action": {"key": "none"}})))
ok("...and the squad shape is what it takes",
   m73.commit(LEFT, {"orders": [{"entity": lone.id, "destination": None,
                                 "action": {"key": "none"}}]}) is None)

# Two islands can squeeze a deployment zone, but never shut it
def _t_shapes(topo):
    out = set()
    for c in range(1, topo.cols + 1):
        for r in range(1, topo.rows + 1):
            for bar, stems in ((((c - 1, r), (c, r), (c + 1, r)), ((c, r - 1), (c, r + 1))),
                               (((c, r - 1), (c, r), (c, r + 1)), ((c - 1, r), (c + 1, r)))):
                for st in stems:
                    g = tuple(sorted(set(bar + (st,))))
                    if len(g) == 4 and all(topo.in_bounds(x) for x in g):
                        out.add(g)
    return sorted(out)

import itertools as _it
_topo = m73.topology
_zone = set(_topo.deployment_zone(LEFT))
_shapes = _t_shapes(_topo)
_tightest, _blobless = 99, 0
for _a, _b in _it.combinations(_shapes, 2):
    if set(_a) & set(_b):
        continue
    _free = sorted(_zone - set(_a) - set(_b))
    _tightest = min(_tightest, len(_free))
    if len(_free) >= 3 and not any(_topo.connected(list(t))
                                   for t in _it.combinations(_free, 3)):
        _blobless += 1
_biggest = max(len(HEROES.BY_KEY[k].squad or [k])
               + len(HEROES.BY_KEY[j].squad or [j])
               + len(HEROES.BY_KEY[i].squad or [i])
               for i, j, k in [("spearman", "snake_emperor", "goblin_gang")])
ok("two islands never leave a zone too small to deploy into",
   _tightest >= _biggest, f"{_tightest} squares vs {_biggest} bodies")
ok("...and never leave 哥布林团伙 without a blob to stand in", _blobless == 0,
   str(_blobless))

# 74 — 武器大师 killed on its way in, holding a weapon measured from itself
m74 = Match()
m74.assign_draft(["weapon_master", "cannoneer"], ["diver", "berserker"])
for k, c in (("weapon_master", (3, 3)), ("cannoneer", (3, 1))):
    assert m74.place(LEFT, k, c) is None, (k, c)
for k, c in (("diver", (7, 3)), ("berserker", (7, 4))):
    assert m74.place(RIGHT, k, c) is None
assert m74.lock_force(LEFT) is None
assert m74.lock_force(RIGHT) is None
while m74.phase == "opening":
    for side in (LEFT, RIGHT):
        if m74.opening is None or m74.phase != "opening":
            break
        pend = m74.opening["pending"][side]
        if pend:
            e = m74.entity(pend[0]["entity"])
            ab = next(a for a in e.abilities if a.key == pend[0]["ability_key"])
            cells = m74.ability_targeting(e, ab).get("cells") or [[9, 5]]
            m74.opening_choose(side, {"cell": list(cells[0]), "target": e.id})
wm74 = unit(m74, LEFT, "weapon_master")
wm74.hp = 1
surround = next(w for w in HEROES.WEAPONS if w["mode"] == "surround8")
m74.board.add_effect((4, 3), BOARD.SmallBomb(RIGHT))   # enough to finish it off
m74.board.add_effect((4, 3), BOARD.SmallBomb(RIGHT))
m74.select_hero(LEFT, wm74.id)
err74 = m74.commit(LEFT, {"destination": [4, 3],
                          "action": {"key": "attack", "weapon": surround["key"]}})
ok("a weapon aimed from the hero itself is a legal order", err74 is None, repr(err74))
for e in m74.unacted(RIGHT)[:1]:
    m74.select_hero(RIGHT, e.id); m74.commit(RIGHT, hold)
ok("...and dying on the way in costs the swing rather than the match",
   not wm74.alive and m74.phase != "gameover", f"{wm74.alive}/{m74.phase}")

# 75 — anything positional is judged from where the hero will be, not where it is
def aim_arena(hero, hero_cell, mates, foes):
    m = Match()
    L = [hero] + [k for k, _ in mates]
    R = [k for k, _ in foes]
    while len(L) < 3:
        L.append("gatekeeper" if "gatekeeper" not in L else "paladin")
    while len(R) < 3:
        R.append("berserker" if "berserker" not in R else "paladin")
    m.assign_draft(L, R)
    assert m.place(LEFT, hero, hero_cell) is None
    for k, c in mates:
        assert m.place(LEFT, k, c) is None, (k, c)
    spare = [(1, 5), (2, 5), (1, 4)]
    for k in L[1 + len(mates):]:
        assert m.place(LEFT, k, spare.pop()) is None, k
    for k, c in foes:
        assert m.place(RIGHT, k, c) is None, (k, c)
    spare = [(9, 5), (8, 5), (9, 4)]
    for k in R[len(foes):]:
        assert m.place(RIGHT, k, spare.pop()) is None, k
    assert m.lock_force(LEFT) is None
    assert m.lock_force(RIGHT) is None
    h = unit(m, LEFT, hero)
    h.ap = h.max_ap
    return m, h

# 渔夫: a lane blocked by your own now, clear from the square you are walking to
m75, fm75 = aim_arena("fisherman", (2, 2), [("cannoneer", (3, 2))], [("dummy", (7, 3))])
hook75 = fm75.abilities[0]
ok("a lane its own body blocks is not offered from where it stands",
   not hook75.lanes(m75, fm75), str(hook75.lanes(m75, fm75)))
ok("...but is offered from the square it is walking to",
   [l["dir"] for l in hook75.lanes(m75, fm75, origin=(2, 3))] == ["forward"],
   str(hook75.lanes(m75, fm75, origin=(2, 3))))
m75.select_hero(LEFT, fm75.id)
ok("...and the throw is accepted",
   m75.commit(LEFT, {"destination": [2, 3],
                     "action": {"key": "ability:hook", "direction": "forward"}}) is None)

# the previewed landing square is the one the catch really ends up on
m76, fm76 = aim_arena("fisherman", (3, 3), [], [("cannoneer", (7, 3))])
hook76 = fm76.abilities[0]
here = hook76.lanes(m76, fm76)[0]["landing"]
there = hook76.lanes(m76, fm76, origin=(4, 3))[0]["landing"]
ok("the haul lands beside wherever the thrower ends up", here != there,
   f"{here} vs {there}")
m76.select_hero(LEFT, fm76.id)
assert m76.commit(LEFT, {"destination": [4, 3],
                         "action": {"key": "ability:hook", "direction": "forward"}}) is None
for e in m76.unacted(RIGHT)[:1]:
    m76.select_hero(RIGHT, e.id); m76.commit(RIGHT, hold)
ok("...and that is what the preview promised",
   list(unit(m76, RIGHT, "cannoneer").cell) == list(there),
   f"{unit(m76, RIGHT, 'cannoneer').cell} vs {there}")

# 大力士: step in beside somebody and throw them in the same turn
m77, st77 = aim_arena("strongman", (3, 3), [], [("dummy", (7, 3))])
unit(m77, RIGHT, "dummy").set_cell((5, 3))      # two squares away: out of reach
slam77 = st77.abilities[0]
ok("nobody is within reach from where it stands", not slam77.throwable(m77, st77))
ok("...but the enemy is, from one square closer",
   [unit(m77, RIGHT, "dummy").id] == slam77.throwable(m77, st77, origin=(4, 3)))
m77.select_hero(LEFT, st77.id)
ok("...so stepping in and throwing is one legal turn",
   m77.commit(LEFT, {"destination": [4, 3],
                     "action": {"key": "ability:slam",
                                "target": unit(m77, RIGHT, "dummy").id}}) is None)
for e in m77.unacted(RIGHT)[:1]:
    m77.select_hero(RIGHT, e.id); m77.commit(RIGHT, hold)
t77 = next(t for t in m77.followups[LEFT] if t["key"] == HEROES.Slam.THROW)
ok("...and the throw is measured from the square it stepped into",
   all(m77.topology.distance(tuple(c), (4, 3)) <= HEROES.Slam.REACH
       for c in t77["options"]),
   f"from {st77.cell}")

# the menu carries an option list for every square it could throw from
m78, fm78 = aim_arena("fisherman", (2, 2), [("cannoneer", (3, 2))], [("dummy", (7, 3))])
m78.select_hero(LEFT, fm78.id)
menu78 = next(a for a in view.state_for(m78, LEFT)["commit"]["actions"]
              if a["key"] == "ability:hook")["targeting"]
ok("the menu ships a lane list per square the hero could reach",
   set(menu78["at"]) == {"2,2", "1,2", "2,1", "2,3"}, str(sorted(menu78["at"])))
ok("...empty where the throw is blocked", not menu78["at"]["2,2"]["choices"])
ok("...and live where it is not",
   [c["dir"] for c in menu78["at"]["2,3"]["choices"]] == ["forward"])

# 76 — 工匠's doors are wall to the other side, however they try to arrive
def door_arena(cells, owner=LEFT, left=(), right=()):
    m = Match()
    L = [k for k, _ in left]
    R = [k for k, _ in right]
    owned = L if owner == LEFT else R
    if "artisan" not in owned:
        owned.insert(0, "artisan")
    while len(L) < 3:
        L.append(next(k for k in ("cannoneer", "gatekeeper", "paladin") if k not in L))
    while len(R) < 3:
        R.append(next(k for k in ("berserker", "magician", "paladin") if k not in R))
    m.assign_draft(L, R)
    assert m.build_choose(owner, {"cells": [list(c) for c in cells]}) is None
    spare = {LEFT: [(1, 1), (1, 2), (1, 4), (1, 5), (2, 5)],
             RIGHT: [(9, 1), (9, 2), (9, 4), (9, 5), (8, 5)]}
    for side, keys, fixed in ((LEFT, L, left), (RIGHT, R, right)):
        placed = set()
        for k, c in fixed:
            assert m.place(side, k, c) is None, (k, c)
            placed.add(k)
        for k in keys:
            if k in placed:
                continue
            assert m.place(side, k, spare[side].pop()) is None, (k, side)
    assert m.lock_force(LEFT) is None
    assert m.lock_force(RIGHT) is None
    return m

# a door square is simply not part of the other side's zone
m76 = Match()
m76.assign_draft(["artisan", "cannoneer", "gatekeeper"], ["berserker", "magician", "paladin"])
assert m76.build_choose(LEFT, {"cells": [[8, 3], [5, 3]]}) is None
ok("a door shuts its square against the other side",
   m76.topology.closed_to((8, 3), RIGHT) and m76.topology.closed_to((5, 3), RIGHT))
ok("...and never against its own", not m76.topology.closed_to((8, 3), LEFT))
ok("...so it leaves their deployment zone",
   (8, 3) not in m76.topology.deployment_zone(RIGHT))
ok("...while theirs is untouched",
   len(m76.topology.deployment_zone(LEFT)) == 15,
   str(len(m76.topology.deployment_zone(LEFT))))
ok("...and they cannot deploy onto it",
   m76.place(RIGHT, "berserker", (8, 3)) is not None,
   repr(m76.place(RIGHT, "berserker", (8, 3))))

# walking, both ways round
m77b = door_arena([(5, 3), (2, 3)], LEFT,
                  left=[("artisan", (2, 3))], right=[("berserker", (7, 3))])
art77 = unit(m77b, LEFT, "artisan")
ber77 = unit(m77b, RIGHT, "berserker")
ber77.set_cell((6, 3))          # right up against the door, once the board is live
ok("the enemy cannot walk onto a door square",
   [5, 3] not in m77b.legal_moves(ber77), str(m77b.legal_moves(ber77)))
ok("...and with one square of movement cannot reach past it",
   [4, 3] not in m77b.legal_moves(ber77), str(m77b.legal_moves(ber77)))
ber77.add_modifier(Modifier("move", "add", 1))
ok("...crossing it with a move of 2 to the square beyond",
   [4, 3] in m77b.legal_moves(ber77) and [5, 3] not in m77b.legal_moves(ber77),
   str(m77b.legal_moves(ber77)))
ok("...while a body in the way still stops the walk dead",
   not m77b.can_cross(ber77, art77.cell) and m77b.can_cross(ber77, (5, 3)))
ok("its own may still step between the pair",
   [5, 3] in m77b.legal_moves(art77), str(m77b.legal_moves(art77)))

# thrown, hauled and swapped are all arrivals
m78b = door_arena([(5, 3), (8, 3)], RIGHT,
                  left=[("strongman", (3, 3)), ("gatekeeper", (1, 2))],
                  right=[("artisan", (8, 3)), ("fisherman", (9, 5)),
                         ("magician", (9, 1))])
st78 = unit(m78b, LEFT, "strongman")
gk78 = unit(m78b, LEFT, "gatekeeper")
fm78b = unit(m78b, RIGHT, "fisherman")
st78.set_cell((6, 3)); gk78.set_cell((7, 3))
throw78 = HEROES.Slam().squares(m78b, st78, gk78)
ok("nobody may be hurled onto a square shut against them",
   (5, 3) not in throw78 and (8, 3) not in throw78, str(sorted(throw78)))
ok("...though the rest of the ground is still fair", bool(throw78))
fm78b.set_cell((4, 3))
ok("...nor hauled onto one", HEROES.Hook.cast(m78b, fm78b, "backward") is None,
   str(HEROES.Hook.cast(m78b, fm78b, "backward")))
mg78 = unit(m78b, RIGHT, "magician")
ar78 = unit(m78b, RIGHT, "artisan")
mg78.ap = mg78.max_ap
ok("...nor swapped into one",
   mg78.abilities[0].validate(m78b, mg78, {"first": ar78.id, "second": gk78.id})
   is not None,
   repr(mg78.abilities[0].validate(m78b, mg78, {"first": ar78.id, "second": gk78.id})))

# the square is still ordinary ground for the side that built it
m79b = door_arena([(5, 3), (2, 3)], LEFT,
                  left=[("artisan", (2, 3)), ("gatekeeper", (1, 4))],
                  right=[("fisherman", (7, 3))])
gk79 = unit(m79b, LEFT, "gatekeeper")
fm79 = unit(m79b, RIGHT, "fisherman")
gk79.set_cell((6, 3)); fm79.set_cell((4, 3))
ok("the owner's own may still be put on its door by anybody",
   HEROES.Hook.cast(m79b, fm79, "backward") == (gk79, (5, 3)),
   str(HEROES.Hook.cast(m79b, fm79, "backward")))
ok("...and a door is shut only to the far side, never to both",
   m79b.topology.closed_to((5, 3), RIGHT)
   and not m79b.topology.closed_to((5, 3), LEFT))

# 77 — the island reads square by square, to both seats, until it lands
m80, ex80 = island_arena()
def isle_marks(m, side):
    return {tuple(mk["cell"]): mk for mk in view.state_for(m, side)["marks"]}

for seat in (LEFT, RIGHT):
    got = isle_marks(m80, seat)
    ok(f"every island square is accounted for to {'its own' if seat == LEFT else 'the other'} seat",
       sorted(got) == [(4, 1), (4, 2), (4, 3), (5, 2)], str(sorted(got)))
    ok("...and an undug one says so",
       all(mk["key"] == "bare" and mk["spent"] for mk in got.values()))

island_round(m80, ex80, "mine_ore", (4, 1))
for seat in (LEFT, RIGHT):
    got = isle_marks(m80, seat)
    ok(f"ore leaves a record even though it leaves no ground, seen by {seat}",
       got[(4, 1)]["key"] == "mineral" and not got[(4, 1)]["spent"],
       str(got[(4, 1)]))
ok("...and the square itself is still bare",
   m80.occupant((4, 1)) is None and not m80.board.effects_at((4, 1)),
   str(m80.board.effects_at((4, 1))))
ok("...while the squares beside it are still untouched",
   isle_marks(m80, LEFT)[(4, 3)]["key"] == "bare")

island_round(m80, ex80, "dig_grapes", (4, 3))
# The third dig ends the round, and the island lands the moment it does — so read
# the record while that round is still running.
m80.select_hero(LEFT, ex80.id)
assert m80.commit(LEFT, {"destination": None,
                         "action": {"key": "ability:train_natives",
                                    "cell": [5, 2]}}) is None
foe80 = m80.unacted(RIGHT)[0]
m80.select_hero(RIGHT, foe80.id); m80.commit(RIGHT, hold)
got = isle_marks(m80, LEFT)
ok("each resource is told apart from the others",
   [got[c]["key"] for c in ((4, 1), (4, 3), (5, 2))] == ["mineral", "grape", "slave"],
   str([got[c]["key"] for c in ((4, 1), (4, 3), (5, 2))]))
ok("...each with its own glyph",
   len({got[c]["glyph"] for c in ((4, 1), (4, 3), (5, 2))}) == 3,
   str([got[c]["glyph"] for c in ((4, 1), (4, 3), (5, 2))]))
guard80 = 0
while m80.round < 4 and m80.phase == "commit" and guard80 < 20:
    guard80 += 1
    for side in (LEFT, RIGHT):
        if m80.commits[side] is not None:
            continue
        un = m80.unacted(side)
        if un:
            m80.select_hero(side, un[0].id); m80.commit(side, hold)
ok("the island has landed by now", m80.round >= 4 and m80.topology.region((4, 1)) is None,
   f"round {m80.round}")
ok("...so the record stops — the vines and 土著 speak for themselves",
   not [mk for mk in view.state_for(m80, LEFT)["marks"] if mk["kind"] == "dig"],
   str(view.state_for(m80, LEFT)["marks"]))

# 78 — 半人马 charges as well as walking, and runs from where the walk ended
m81, cen81 = aim_arena("centaur", (3, 3), [], [("dummy", (7, 3))])
foe81 = unit(m81, RIGHT, "dummy")
foe81.set_cell((6, 3))
ch81 = cen81.abilities[0]
ok("冲撞 no longer takes the place of the move", not ch81.self_move)
ok("...though it still carries the hero, so a root stops it", ch81.carries_self)
ok("nothing worth charging from where it stands", not ch81.lanes(m81, cen81),
   str(ch81.lanes(m81, cen81)))
ok("...but a lane opens from one square forward",
   [l["dir"] for l in ch81.lanes(m81, cen81, origin=(4, 3))] == ["forward", "backward"],
   str([l["dir"] for l in ch81.lanes(m81, cen81, origin=(4, 3))]))
m81.select_hero(LEFT, cen81.id)
ok("walking and then charging is one legal turn",
   m81.commit(LEFT, {"destination": [4, 3],
                     "action": {"key": "ability:charge",
                                "direction": "forward"}}) is None)
hp81 = foe81.hp
for e in m81.unacted(RIGHT)[:1]:
    m81.select_hero(RIGHT, e.id); m81.commit(RIGHT, hold)
ok("...and it covers its walk and its run together",
   cen81.cell == (7, 3), str(cen81.cell))
ok("...trampling what it passes over on the way",
   hp81 - foe81.hp == HEROES.Charge.DAMAGE, f"{hp81} -> {foe81.hp}")

# a root still stops the whole thing
m82, cen82 = aim_arena("centaur", (3, 3), [], [("dummy", (7, 3))])
unit(m82, RIGHT, "dummy").set_cell((6, 3))
m82.root(cen82)
ok("a rooted 半人马 is offered no charge",
   not [a for a in m82.action_menu(cen82) if a["key"] == "ability:charge"],
   str([a["key"] for a in m82.action_menu(cen82)]))
m82.select_hero(LEFT, cen82.id)
ok("...and is refused one if it asks anyway",
   m82.commit(LEFT, {"destination": None,
                     "action": {"key": "ability:charge",
                                "direction": "forward"}}) is not None)

# 刺客's blink still replaces its walk — only the centaur changed
ok("刺客's 封喉 still stands in for the move", HEROES.Garrote.self_move)
ok("...and still counts as carrying itself", HEROES.Garrote.carries_self)

# 79 — venom that lands during its victim's own turn bites on the turn after
m83, hd83, tl83, gate83, d83 = snake_arena()
gate83.set_cell((4, 3))
move83 = gate83.move_allowance
# the gatekeeper commits in the very exchange the snake bites it
snake_turn(m83, hd83, tl83, bite((3, 3), [[4, 3]]), stay((3, 4)), gate83.id, hold)
ok("the bite lands while its victim is taking its turn",
   gate83.vars.get("rooted_tag") == "venom", str(gate83.vars.get("rooted_tag")))
ok("...and that turn does not spend it", m83.move_budget(gate83) == move83 - 1,
   f"{move83} -> {m83.move_budget(gate83)}")

# close the round out, exactly the way the block above does
r83 = m83.round
while m83.round == r83:
    left83 = [e.id for e in m83.unacted(LEFT)]
    right83 = [e.id for e in m83.unacted(RIGHT)]
    if left83:
        snake_turn(m83, hd83, tl83, stay(hd83.cell), stay(tl83.cell),
                   right83[0] if right83 else None, hold)
    else:
        turn(m83, None, None, right83[0] if right83 else None, hold)
ok("...it is still slowed when its next turn comes round",
   m83.move_budget(gate83) == move83 - 1, f"{m83.move_budget(gate83)}")

# and that turn spends it — both sides have to commit for the exchange to resolve
snake_turn(m83, hd83, tl83, stay(hd83.cell), stay(tl83.cell), gate83.id, hold)
ok("...and only then is it spent", m83.move_budget(gate83) == move83,
   f"{m83.move_budget(gate83)}")
ok("...with nothing left on the sheet", gate83.vars.get("rooted_at") is None,
   str(gate83.vars.get("rooted_at")))

# a slow never loosens a pin, whichever lands first
m84, hd84, tl84, gate84, d84 = snake_arena()
m84.root(gate84)                                   # 剑齿虎's pin: every square
m84.root(gate84, squares=1, tag="venom")           # venom on top of it
ok("a slow landing on a pin leaves the pin standing", m84.rooted(gate84))
ok("...and the walk is still nothing", m84.move_budget(gate84) == 0,
   str(m84.move_budget(gate84)))
m85, hd85, tl85, gate85, d85 = snake_arena()
m85.root(gate85, squares=1, tag="venom")
m85.root(gate85)                                   # the pin lands second
ok("...and a pin landing on a slow overrides it", m85.rooted(gate85))

# 80 — 牛头: shove whatever its horns reached, once the exchange has settled
def gore_arena(foe="dummy"):
    m = arena([("minotaur", (3, 3)), ("cannoneer", (3, 1)), ("gatekeeper", (3, 2))],
              [(foe, (7, 3)), ("berserker", (7, 4)), ("paladin", (7, 5))])
    return m, unit(m, LEFT, "minotaur"), unit(m, RIGHT, foe)

def gore_tasks(m):
    return [t for t in m.followups[LEFT] if t["key"].startswith(HEROES.Gore.KEY + ":")]

def gore_turn(m, mi, at, foe, foe_order=None):
    m.select_hero(LEFT, mi.id)
    assert m.commit(LEFT, {"destination": None,
                           "action": {"key": "attack", "shots": [[list(at)]]}}) is None
    r = m.unacted(RIGHT)[0]
    m.select_hero(RIGHT, r.id)
    m.commit(RIGHT, foe_order or hold)
    guard = 0
    while m.phase == "victim" and guard < 6:
        guard += 1
        m.choose_victim(LEFT, foe.id)

m, mi, foe = gore_arena()
foe.set_cell((4, 3))
hp0 = foe.hp
gore_turn(m, mi, (4, 3), foe)
ok("a hit earns a shove", len(gore_tasks(m)) == 1, str(len(gore_tasks(m))))
task = gore_tasks(m)[0]
ok("...offered around the enemy, and only where it could stand",
   sorted(map(tuple, task["options"]))
   == sorted(c for c in m.topology.neighbours(foe.cell) if m.can_enter(foe, c)),
   str(task["options"]))
ok("...four squares, never the diagonals",
   all(abs(c[0] - foe.cell[0]) + abs(c[1] - foe.cell[1]) == 1 for c in task["options"]),
   str(task["options"]))
ok("...and it is a choice, not a duty", task["optional"] is True)
ok("the blow itself still landed", hp0 - foe.hp == mi.atk, f"took {hp0 - foe.hp}")
m.choose_followup(LEFT, task["options"][0])
ok("shoving puts it where you said", foe.cell == tuple(task["options"][0]), str(foe.cell))

# declining leaves it exactly where it stood
m, mi, foe = gore_arena()
foe.set_cell((4, 3))
gore_turn(m, mi, (4, 3), foe)
ok("the shove can be declined", m.choose_followup(LEFT, None) is None)
ok("...and nobody moves", foe.cell == (4, 3), str(foe.cell))

# a swing that reaches nobody earns nothing
m, mi, foe = gore_arena()
gore_turn(m, mi, (4, 3), foe)
ok("a swing that catches nobody offers no shove", not gore_tasks(m))

# a blow turned aside still counts as reaching
m, mi, foe = gore_arena()
foe.set_cell((4, 3))
foe.vars["blessed"] = mi.id                 # the next blow is turned aside entirely
hp0 = foe.hp
gore_turn(m, mi, (4, 3), foe)
ok("a blessing turns the blow aside", foe.hp == hp0, f"took {hp0 - foe.hp}")
ok("...but the horns still reached it", len(gore_tasks(m)) == 1,
   str(len(gore_tasks(m))))

# the marked square is where the blow lands, so walking out of it dodges entirely
m, mi, foe = gore_arena()
foe.set_cell((4, 3))
hp0 = foe.hp
gore_turn(m, mi, (4, 3), foe,
          foe_order={"destination": [5, 3], "action": {"key": "none"}})
ok("an enemy that walks out of the marked square is not struck", foe.hp == hp0,
   f"took {hp0 - foe.hp}")
ok("...and so is not shoved either", not gore_tasks(m))

# and one that walks into it is caught, and shoved from there
m, mi, foe = gore_arena()
foe.set_cell((5, 3))
gore_turn(m, mi, (4, 3), foe,
          foe_order={"destination": [4, 3], "action": {"key": "none"}})
ok("an enemy that walks into it is caught", foe.cell == (4, 3), str(foe.cell))
ok("...and the squares offered are the ones around where it finished",
   sorted(map(tuple, gore_tasks(m)[0]["options"]))
   == sorted(c for c in m.topology.neighbours(foe.cell) if m.can_enter(foe, c)),
   str(gore_tasks(m)[0]["options"]))

# a shoved hero trips whatever is buried where it lands
m = arena([("minotaur", (3, 3)), ("diver", (3, 1)), ("gatekeeper", (3, 2))],
          [("gatekeeper", (7, 3)), ("berserker", (7, 4))])
mi, gk = unit(m, LEFT, "minotaur"), unit(m, RIGHT, "gatekeeper")
m.opening_choose(LEFT, {"cell": [1, 1]})
gk.set_cell((4, 3))
m.board.add_effect((4, 2), BOARD.SmallBomb(LEFT))
hp0 = gk.hp
gore_turn(m, mi, (4, 3), gk)
m.choose_followup(LEFT, [4, 2])
ok("a shoved hero sets off what is buried where it lands",
   hp0 - gk.hp == mi.atk + BOARD.SmallBomb.DAMAGE,
   f"took {hp0 - gk.hp} ({mi.atk} horns + {BOARD.SmallBomb.DAMAGE} mine)")

# 81 — 教皇 never spends past its cap, however many fall at once
m = arena([("pope", (3, 3)), ("cannoneer", (3, 1)), ("gatekeeper", (3, 2))],
          [("mammoth", (7, 3)), ("dummy", (7, 1))])
pope = unit(m, LEFT, "pope")
can, gk = unit(m, LEFT, "cannoneer"), unit(m, LEFT, "gatekeeper")
mam = unit(m, RIGHT, "mammoth")
POPE_CAP = HEROES.Absolution.SAVE_LIMIT
ok("教皇 starts with every mercy", m.saves_left(pope) == POPE_CAP,
   str(m.saves_left(pope)))
pope.vars["saves_used"] = POPE_CAP - 1                # one left
can.hp = gk.hp = 1                                    # two about to fall together
# Applied but not swept, which is exactly the moment a save is offered in.
evs = [DMG.DamageEvent(source=mam, target=can, amount=5, category=DMG.ABILITY),
       DMG.DamageEvent(source=mam, target=gk, amount=5, category=DMG.ABILITY)]
applied = [(ev, DMG.deal(m, ev)) for ev in evs]
m.instant = {"batch": [], "insts": [], "applied": applied}
m.offer_saves()
asked = [t for t in m.interrupts if t["key"] == "death_save"]
ok("with one mercy left it is asked once, not twice", len(asked) == 1,
   str(len(asked)))
ok("...and the cap is what stops it", m.saves_left(pope) == 1, str(m.saves_left(pope)))
pope.vars["saves_used"] = POPE_CAP
ok("spent out, it is no longer among the savers", pope not in m.savers())
m.interrupts = []
m.offer_saves()
ok("...and is asked for nothing more",
   not [t for t in m.interrupts if t["key"] == "death_save"], str(m.interrupts))

# 狙击手's lane cannot touch what the board says is untouchable
m = Match()
m.assign_draft(["sniper", "cannoneer", "gatekeeper"], ["world_tree", "dummy", "berserker"])
for k, c in (("sniper", (1, 3)), ("cannoneer", (1, 1)), ("gatekeeper", (1, 2))):
    assert m.place(LEFT, k, c) is None, k
for k, c in (("dummy", (9, 1)), ("berserker", (9, 2))):
    assert m.place(RIGHT, k, c) is None, k
assert m.lock_force(LEFT) is None
assert m.lock_force(RIGHT) is None
sn = unit(m, LEFT, "sniper")
tree = next(e for e in m.entities if e.key == "world_tree")
ok("a lane shot does not find 世界树", LineShot.scan(m, sn, "forward") is None,
   str(LineShot.scan(m, sn, "forward")))
unit(m, RIGHT, "dummy").set_cell((8, 3))
ok("...and does not reach past it either", LineShot.scan(m, sn, "forward") is None,
   str(LineShot.scan(m, sn, "forward")))
ok("...so no lane down that row is offered",
   "forward" not in [l["dir"] for l in LineShot.lanes(m, sn)],
   str([l["dir"] for l in LineShot.lanes(m, sn)]))

# 82 — 浪子: whichever arm is worth more, and one way out of anything
def wand_arena(foe="berserker"):
    m = arena([("wanderer", (3, 3)), ("cannoneer", (3, 1)), ("gatekeeper", (3, 2))],
              [(foe, (7, 3)), ("dummy", (7, 1)), ("paladin", (7, 5))])
    return m, unit(m, LEFT, "wanderer"), unit(m, RIGHT, foe)

def blow(m, src, tgt, cat=None):
    hp0 = tgt.hp
    DMG.apply_batch(m, [DMG.DamageEvent(source=src, target=tgt, amount=src.atk,
                                        category=cat or DMG.NORMAL_ATTACK)])
    return hp0 - tgt.hp

# against a stronger arm
m, w, ber = wand_arena("berserker")
ok("the test needs the enemy to hit harder", ber.atk > w.atk, f"{w.atk} vs {ber.atk}")
ok("swinging at a stronger hero lands for their arm", blow(m, w, ber) == ber.atk,
   f"{blow(m, w, ber)} vs {ber.atk}")
m, w, ber = wand_arena("berserker")
ok("being swung at by one lands for its own", blow(m, ber, w) == w.atk,
   f"vs atk {w.atk}")

# against a weaker arm it keeps its own on the way out, takes theirs on the way in
m, w, d = wand_arena("dummy")
ok("the test needs the enemy to hit softer", d.atk < w.atk, f"{w.atk} vs {d.atk}")
m, w, d = wand_arena("dummy")
ok("swinging at a weaker hero still lands for its own", blow(m, w, d) == w.atk,
   f"vs atk {w.atk}")
m, w, d = wand_arena("dummy")
ok("...and a weaker hero's blow lands for theirs", blow(m, d, w) == d.atk,
   f"vs atk {d.atk}")

# an ability is nobody's arm
m, w, ber = wand_arena("berserker")
hp0 = w.hp
DMG.apply_batch(m, [DMG.DamageEvent(source=ber, target=w, amount=9,
                                    category=DMG.ABILITY)])
ok("an ability passes untouched", hp0 - w.hp == 9, f"took {hp0 - w.hp}")

# the passive follows the live number, not the printed one
m, w, ber = wand_arena("berserker")
w.add_modifier(Modifier("atk", "add", 5))            # now the stronger of the two
ok("it trades on what each is worth right now", blow(m, w, ber) == w.atk,
   f"{w.atk} vs {ber.atk}")

# the reprieve
m, w, ber = wand_arena("berserker")
hp0, atk0 = w.hp, w.atk
paused = m.deal_after_exchange([DMG.DamageEvent(source=ber, target=w, amount=9,
                                                category=DMG.ABILITY)])
task = [t for t in m.interrupts if t["key"] == HEROES.Reprieve.KEY]
ok("a blow that lands offers the way out", paused and len(task) == 1, str(len(task)))
ok("...and it is asked of its own side", task[0]["side"] == LEFT)
m.choose_interrupt(LEFT, True)
ok("taking it gives the damage back", w.hp == hp0, f"{w.hp}/{hp0}")
ok("...and costs a point of Atk for good", w.atk == atk0 - 1, f"{atk0} -> {w.atk}")
m.deal_after_exchange([DMG.DamageEvent(source=ber, target=w, amount=3,
                                       category=DMG.ABILITY)])
ok("...and is never offered again",
   not [t for t in m.interrupts if t["key"] == HEROES.Reprieve.KEY])

# declining leaves the blow standing
m, w, ber = wand_arena("berserker")
hp0 = w.hp
m.deal_after_exchange([DMG.DamageEvent(source=ber, target=w, amount=4,
                                       category=DMG.ABILITY)])
m.choose_interrupt(LEFT, None)
ok("declining leaves the damage where it fell", w.hp == hp0 - 4, f"{w.hp}/{hp0}")
ok("...and keeps the mercy for later", not w.vars.get("reprieve_used"))

# it works on the blow that would have ended it
m, w, ber = wand_arena("berserker")
w.hp = 2
m.deal_after_exchange([DMG.DamageEvent(source=ber, target=w, amount=9,
                                       category=DMG.ABILITY)])
ok("a killing blow raises it too",
   [t for t in m.interrupts if t["key"] == HEROES.Reprieve.KEY] != [])
m.choose_interrupt(LEFT, True)
guard = 0
while m.phase == "interrupt" and m.interrupts and guard < 6:
    guard += 1
    t = m.interrupts[0]
    m.choose_interrupt(t["side"], t["options"][0] if t.get("options") else True)
ok("...and it survives", w.alive and w.hp == 2, f"{w.alive} {w.hp}")

# the Atk it gave up feeds back into what blows against it are worth
m, w, ber = wand_arena("berserker")
w.vars["reprieve_used"] = True
w.add_modifier(Modifier("atk", "add", -1))
ok("a lowered arm means it takes less from a stronger hero",
   blow(m, ber, w) == w.atk, f"{blow(m, ber, w)} vs atk {w.atk}")

print("\nlog tail:")
for line in m.log[-5:]:
    print("   ", line["text"])
