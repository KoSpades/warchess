"""Headless checks on the rules most likely to be implemented wrong."""

import damage as DMG
from heroes import Sweep
from match import Match
from topology import LEFT, RIGHT

L = [("spearman", (3, 1)), ("rock_giant", (3, 2)), ("robot", (3, 3)), ("gunslinger", (3, 4))]
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


def ok(label, cond, detail=""):
    print(f"{'PASS' if cond else 'FAIL'}  {label}" + (f"   [{detail}]" if detail else ""))


def arena(left, right):
    """Deploy exactly the listed heroes per side — force size is the list length,
    so a new hero's test needs only the units it actually exercises. Sides must
    be the same length."""
    m = Match()
    m.assign_draft([k for k, _ in left], [k for k, _ in right])
    for k, c in left:
        assert m.place(LEFT, k, c) is None, (k, c)
    for k, c in right:
        assert m.place(RIGHT, k, c) is None, (k, c)
    assert m.lock_force(LEFT) is None
    assert m.lock_force(RIGHT) is None
    return m


def unit(m, side, key):
    """The one living hero of `key` on `side` — clearer than guessing entity ids."""
    return next(e for e in m.living(side) if e.key == key)


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
giant.set_cell((4, 3))
turn(m, 1, {"destination": [4, 1],
            "action": {"key": "attack", "shots": [[[4, 2], [4, 3], [5, 2]]]}},
     5, {"destination": [4, 1], "action": {"key": "none"}})
ok("bounce shifts the pattern with the hero", giant.hp == 26,
   f"giant {giant.hp}hp, spearman at {m.entity(1).cell}")

# 5 — rock giant: sequential second shot is blocked
m = build()
giant = m.entity(2)
shots = [[[3, 2], [4, 2], [5, 2], [6, 2]], [[3, 2], [4, 2], [5, 2], [6, 2]]]
turn(m, 3, {"destination": None, "action": {"key": "none"}},
     8, {"destination": None, "action": {"key": "attack", "shots": shots}})
ok("gunslinger's 2nd shot blocked by stone immunity", giant.hp == 22, f"hp {giant.hp}")

# 6 — mutual kill
m = build()
a, b = m.entity(4), m.entity(8)
a.set_cell((4, 2))
b.set_cell((5, 2))
a.hp = b.hp = 3
turn(m, 4, {"destination": None, "action": {"key": "attack",
     "shots": [[[5, 2], [4, 3], [4, 4], [4, 5]], [[5, 2], [4, 3], [4, 4], [4, 5]]]}},
     8, {"destination": None, "action": {"key": "attack",
     "shots": [[[4, 2], [5, 3], [5, 4], [5, 5]], [[4, 2], [5, 3], [5, 4], [5, 5]]]}})
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
m.board.add_burning((3, 3), RIGHT)
m.board.add_burning((3, 3), RIGHT)
m.board.add_burning((7, 3), RIGHT)
m.select_hero(LEFT, 3)
took = m.entity(3).max_hp - m.entity(3).hp
ok("stacked enemy tile deals 4 at turn start", took == 4, f"took {took}")
m.commit(LEFT, {"destination": None, "action": {"key": "none"}})
m.select_hero(RIGHT, 7)
ok("a tile never burns its owner's own side", m.entity(7).hp == m.entity(7).max_hp)

# 9 — AP: none on round one, granted at end of turn
m = build()
sp = m.entity(1)
first = sp.ap
turn(m, 1, {"destination": None, "action": {"key": "none"}},
     5, {"destination": None, "action": {"key": "none"}})
ok("no AP on the opening turn, 1 AP after it", first == 0 and sp.ap == 1)

# 10 — thunderstorm hits every enemy regardless of position
m = build()
m.entity(6).ap = 3
turn(m, 1, {"destination": None, "action": {"key": "none"}},
     6, {"destination": None, "action": {"key": "ability:thunderstorm"}})
took = [m.entity(i).max_hp - m.entity(i).hp for i in (1, 2, 3, 4)]
ok("thunderstorm hits all four enemies for 3", all(t == 3 for t in took), str(took))

# 11 — unit-locked attack ignores movement entirely
m = build()
turn(m, 1, {"destination": [4, 1], "action": {"key": "none"}},
     6, {"destination": None, "action": {"key": "attack", "target": 1}})
ok("unbounded one_chosen lands after the target moves", m.entity(1).hp == 18)

# 12 — a hero killed by fire at turn start loses its action
m = build()
rob = m.entity(3)
rob.hp = 3
m.board.add_burning((3, 3), RIGHT)
m.board.add_burning((3, 3), RIGHT)
m.select_hero(LEFT, 3)
ok("fire kills before the hero acts", not rob.alive and m.commits[LEFT]["kind"] == "dead")

# 13 — 炮手: a plain long-range single shot lands for its atk (spearman: no regen)
m = arena([("cannoneer", (3, 3))], [("spearman", (7, 3))])
tgt = unit(m, RIGHT, "spearman")
turn(m, unit(m, LEFT, "cannoneer").id,
     {"destination": None, "action": {"key": "attack", "shots": [[[7, 3], [7, 2], [7, 4], [6, 3]]]}},
     tgt.id, {"destination": None, "action": {"key": "none"}})
ok("cannoneer's long shot lands for 3", tgt.hp == 16, f"hp {tgt.hp}")

# 14 — 马尔斯: rng rises once an enemy falls, atk once only one remains (spec 7.3)
m = arena([("mars", (3, 3)), ("spearman", (3, 1)), ("robot", (3, 2))],
          [("robot", (7, 1)), ("gunslinger", (7, 2)), ("fire_mage", (7, 3))])
mars = unit(m, LEFT, "mars")
ok("马尔斯 opens at base rng/atk", mars.rng == 2 and mars.atk == 4)
unit(m, RIGHT, "robot").hp = 0
m.sweep_deaths()
ok("马尔斯 gains rng after the first enemy falls", mars.rng == 3 and mars.atk == 4)
unit(m, RIGHT, "gunslinger").hp = 0
m.sweep_deaths()
ok("马尔斯 gains atk when one enemy remains", mars.rng == 3 and mars.atk == 5)

# 15 — 山神: shields column allies from attacks/abilities, but not tiles, and only allies
m = arena([("mountain_god", (2, 3)), ("robot", (2, 1))],
          [("gunslinger", (7, 1)), ("fire_mage", (7, 2))])
ally, src = unit(m, LEFT, "robot"), unit(m, RIGHT, "gunslinger")
h = ally.hp
DMG.deal(m, DMG.DamageEvent(source=src, target=ally, amount=5, category=DMG.NORMAL_ATTACK))
ok("山神 shields an ally in his column", ally.hp == h, f"hp {ally.hp}")
DMG.deal(m, DMG.DamageEvent(source=src, target=ally, amount=2, category=DMG.TILE))
ok("山神's shield lets tile damage burn through", ally.hp == h - 2, f"hp {ally.hp}")
ally.set_cell((5, 1))  # step out of 山神's column
h = ally.hp
DMG.deal(m, DMG.DamageEvent(source=src, target=ally, amount=5, category=DMG.NORMAL_ATTACK))
ok("山神 leaves an out-of-column ally exposed", ally.hp == h - 5, f"hp {ally.hp}")

print("\nlog tail:")
for line in m.log[-5:]:
    print("   ", line["text"])
