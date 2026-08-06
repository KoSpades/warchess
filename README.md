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

### One player, against the AI

```
python game.py --pve
```

You hold the Left seat and the AI holds the Right. See *Playing the AI* below.

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
python test_engine.py     # engine rules
python make_fixtures.py && node test_client.js    # the browser client
```

The engine suite covers the rules that are easiest to get wrong — bouncing, swap
prevention, mutual kills, sequential vs simultaneous damage, tile ownership, AP
timing — and then one section per hero. The client suite replays real engine
states (written out by `make_fixtures.py`, never hand-authored) through the
browser code, so the two can never drift apart.

`python game.py --test` skips draft and deployment and puts the heroes named in
`TEST_HEROES` straight on the board, padded with dummies. Every new hero goes in
that list so it can be played the moment it exists.

## Playing the AI

`python game.py --pve` gives you a whole game — draft, build, deployment, fight
— against the same brain the tournament runs on. You hold the Left seat; the AI
holds the Right and answers the moment a decision is its own, so there is only
one link to open and nothing to wait for.

Every PvE game is written to `logs/`, one JSON Lines file per game. The record
that matters is `turn`: the board before an order, the order actually played,
and how the AI ranked every hero that could have acted — for its own moves *and*
for yours. So a game leaves a series of positions labelled with both the AI's
answer and a human's, which is what a change to `ai.py` can be judged against.
`matchlog.py`'s docstring lists the record types; the `seed` in the `start`
record plus the command stream replays a game exactly.

## Known issue: the Robot stalemate

The Robot heals 4 at the end of its own turn, so anything that cannot out-damage
that regeneration cannot kill it. If both sides' last surviving hero is a Robot,
the match cannot end. Bot play still shows a meaningful share of matches ending
in a draw rather than a result. Any fix works — cap the heal, suppress it in a
round the Robot took damage, or add a round limit — but it needs one before
serious play.

## Layout

Every module sits at the repository root; there is no package directory.

| file | role |
|---|---|
| `topology.py` | every adjacency and distance query |
| `events.py` | global event bus, priority-ordered hooks |
| `damage.py` | damage pipeline, source categories, simultaneous batches, and the global rules that read a var and name no hero (blessing, poison, 增伤, curse) |
| `entities.py` | modifier-stack stat reads, positions as cell sets |
| `board.py` | cell-effect layer |
| `attacks.py` | attack modes — how a hero's normal attack is aimed and resolved |
| `actions.py` | commitments becoming resolvable instances |
| `heroes.py` | the whole roster, as data: stat blocks, abilities, passives |
| `match.py` | setup, exchanges, re-entrant resolution |
| `view.py` | per-player filtering |
| `server.py` | HTTP API |
| `index.html` + `app.js` | client |
| `playtest.py` | headless bot play and balance tournaments |
| `ai.py` | the judging brain — what a board is worth once a move resolves |
| `pve.py` | the AI holding one seat against a live player, and its draft |
| `matchlog.py` | the per-game record `--pve` writes to `logs/` |
| `make_fixtures.py` | real engine states written out for `test_client.js` |

Adding a hero should mean editing `heroes.py` only. If it doesn't, the engine
is missing a primitive — that's the signal to add one rather than special-case
the hero. Recent heroes have earned their primitives that way: a choice of
shapes centred on the caster (`shape` targeting), a status that ticks at round
end (`damage.py`'s poison), and a body placed relative to another rather than
walking (`move_zone` / `move_anchor`). Each is generic, names no hero, and the
next design that needs it gets it for free.

A new targeting kind must be added to `TARGETING_KINDS` in `heroes.py` **and**
handled in `app.js`; `check_roster()` catches only the half Python can see.
