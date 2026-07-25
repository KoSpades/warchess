# Warchess

Two-player tactical game on a 9x5 board with hidden simultaneous commitment.
Python 3.9+, standard library only — no installs.

## Run

### Both players at one machine

```
python game.py
```

Open both seats in separate tabs: `?side=L` and `?side=R` on
`http://127.0.0.1:8000`.

### Two players, anywhere

Run this on whichever laptop is hosting:

```
python game.py --share
```

It opens a public link (via ngrok) and prints both seats. The host keeps the
`?side=L` link and sends the `?side=R` link to the other player, who can be on
any network — no shared Wi-Fi needed. Requires ngrok: `brew install ngrok`, then
`ngrok config add-authtoken <token>` once (free account at ngrok.com).

There is no password. Anyone with the link can open either seat, and the URL is
the only thing deciding which side you are. The link changes each time you
restart `--share`.

`--port 9000` to change port, `--no-browser` to suppress the auto-open.

Commitment is hidden, so the two seats genuinely need separate views: the server
holds authoritative state and filters what each side is allowed to see.

## Playing

**Draft.** Two heroes are offered at a time and one side chooses; the other hero
goes to the opponent. Picks alternate (Left, Right, Left, Right) until each side
owns 4 distinct heroes. Both seats see every offer and pick.

**Deploy.** Place your 4 drafted heroes in your shaded 3x5 zone, seal.

**Each exchange.** Both sides privately pick one hero that hasn't acted, choose
its move and action, and seal the order. Both orders resolve at once.

Picking a hero *starts its turn* — board effects like fire resolve immediately
and the choice is final.

**Target choice.** If your marked cells end up containing more than one enemy,
you choose which takes the hit, after movement resolves. Both sides choose
independently.

Four exchanges make a round; then everyone refreshes and AP has ticked up.

## Tests

```
python test_engine.py
```

13 checks on the rules that are easiest to get wrong: bouncing, swap
prevention, mutual kills, sequential vs simultaneous damage, tile ownership,
AP timing.

## Known issue: the Robot stalemate

The Robot heals 4 at the end of its own turn and attacks for 3, so it cannot
kill another Robot. Neither can the Spearman (4, exactly cancelled), the Rock
Giant (2), the Fire Mage (2), or the Thunder Dragon (1). Only the Gunslinger
out-damages the regeneration, at 4 + 2 = 6.

If both sides' last surviving hero is a Robot, the match cannot end. In 500
bot-played matches this accounted for every game that failed to terminate.
Any fix works — cap the heal, suppress it in a round the Robot took damage, or
add a round limit — but it needs one before serious play.

## Layout

| file | role |
|---|---|
| `warchess/topology.py` | every adjacency and distance query |
| `warchess/events.py` | global event bus, priority-ordered hooks |
| `warchess/damage.py` | damage pipeline, source categories, simultaneous batches |
| `warchess/entities.py` | modifier-stack stat reads, positions as cell sets |
| `warchess/board.py` | cell-effect layer |
| `warchess/actions.py` | commitments becoming resolvable instances |
| `warchess/heroes.py` | the six heroes, as data |
| `warchess/match.py` | setup, exchanges, re-entrant resolution |
| `warchess/view.py` | per-player filtering |
| `warchess/server.py` | HTTP API |
| `warchess/static/index.html` | client |

Adding a hero should mean editing `heroes.py` only. If it doesn't, the engine
is missing a primitive — that's the signal to add one rather than special-case
the hero.
