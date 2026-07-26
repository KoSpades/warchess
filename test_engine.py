"""Headless checks on the rules most likely to be implemented wrong.

Robustness rule (hero numbers get retuned constantly): NEVER assert a hardcoded
absolute stat. Assert on the *effect* — damage dealt (`before - after`), whether
a hit landed at all, or a change relative to a captured baseline. Attacks mark
just the target's own cell (1 cell) and fire from close range, so cell-count and
range tweaks can't break these either.
"""

import damage as DMG
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

# 15 — 山神: shields column allies from attacks/abilities, but not tiles, and only allies
m = arena([("mountain_god", (2, 3)), ("dummy", (2, 1))],
          [("dummy", (7, 1)), ("gatekeeper", (7, 2))])
sg, ally, src = unit(m, LEFT, "mountain_god"), unit(m, LEFT, "dummy"), unit(m, RIGHT, "dummy")
hit = lambda cat: DMG.deal(m, DMG.DamageEvent(source=src, target=ally, amount=5, category=cat))

h = ally.hp
hit(DMG.NORMAL_ATTACK)
ok("山神 shields an ally in his column", ally.hp == h, f"took {h - ally.hp}")
h = ally.hp
DMG.deal(m, DMG.DamageEvent(source=src, target=ally, amount=2, category=DMG.TILE))
ok("山神's shield lets tile damage burn through", h - ally.hp == 2, f"took {h - ally.hp}")
ally.set_cell((5, 1))  # step out of 山神's column
h = ally.hp
hit(DMG.NORMAL_ATTACK)
ok("山神 leaves an out-of-column ally exposed", h - ally.hp == 5, f"took {h - ally.hp}")

print("\nlog tail:")
for line in m.log[-5:]:
    print("   ", line["text"])
