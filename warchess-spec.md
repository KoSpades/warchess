# Warchess — Design Specification

**Status:** working draft. Sections 1–6 are settled rules. Section 7 is the architecture the roster demands. Section 9 lists open questions.

> **On the roster sample:** a large sample of intended hero designs was reviewed to calibrate how flexible the engine must be. No hero from it is specified here and none should be implemented from it. It is used only as evidence about the *shape* of mechanics the engine must support.

---

## 1. Board and setup

- Board is 9 columns × 5 rows = 45 cells. Coordinates are `(col, row)`, `col ∈ 1..9`, `row ∈ 1..5`.
- **Left deployment zone:** columns 1–3 (15 cells). **Right deployment zone:** columns 7–9 (15 cells). Columns 4–6 are neutral at setup.
- Each side places 4 heroes in its own zone, one hero per cell.
- Distance is **L1 (Manhattan)** by default, but see §7.6 — adjacency and distance are engine queries, not arithmetic, because effects rewrite them.

---

## 2. Turn structure

A **match** is a sequence of **rounds**. A round is a sequence of **exchanges**.

### Round

1. Every living hero on both sides is marked *un-acted*.
2. Repeat until every hero on both sides has acted:
   - Each side secretly commits one un-acted hero and its full action.
   - Both commitments are revealed and resolved **simultaneously** (§3).
   - Both heroes are marked *acted*.
3. When one side runs out of un-acted heroes but the other has some left, the remaining heroes act **unopposed**, one at a time, still one per exchange. No simultaneity, so no collisions and no mutual kills.
4. Round ends. Start the next round.

This is the **default** schedule only. Effects routinely skip a unit's turn, grant extra turns, let a unit act twice, insert newly created units into the order, delegate one unit's turn to another, or exempt a unit from taking turns at all. Implement the schedule as a mutable queue (§7.8), never as a `for` loop over four heroes.

### Commitment is hidden

Neither side sees the other's choice of hero, movement, or target before resolution. This is the most load-bearing rule in the design — it is why attacks can miss, why body-blocking works, and why targeting mode matters. Some effects deliberately break it by forcing an opponent to declare early and then binding them to that declaration, so "reveal a commitment ahead of resolution, and lock it" must be a supported operation.

### Match end

A side with zero living heroes loses. If both sides lose their last heroes in the same exchange, the match is a draw. Note that the entity set is dynamic (§7.7): summons, converted enemies, and temporarily-revived units all bear on this condition, and which entity types count toward it must be an explicit flag rather than an assumption.

---

## 3. Exchange resolution

An exchange has **two decision points**, not one. Both sides act privately at each, so nothing leaks.

1. **Commit (private).** Each side picks a hero, picks its destination, and picks its action parameters — the target cells of a cell-locked attack, the direction of an anchored one, the cell for a placement effect. Cell selection is made relative to the hero's *intended* post-move position.
2. **Resolve movement.** Both movements apply simultaneously (see legality below).
3. **Victim pick (private, live).** For any attack whose pattern may contain several enemies, the attacking side now sees who actually ended up inside its cells and **chooses one** to take the damage. Both sides choose independently and simultaneously. Attacks that hit all enemies in their pattern skip this step — there is nothing to choose.
4. **Apply all damage simultaneously**, then resolve deaths. A hero that has taken lethal damage still lands its own attack — **mutual kills are legal and both heroes die**.

Step 3 is why an attack cannot be a pure function of the commitment: the resolver must pause, prompt both clients, and continue.

### Movement legality

A destination is legal only if it is **empty in the pre-movement snapshot** — no ally and no enemy standing there. Occupancy at commit time is what counts, not occupancy after everyone has moved.

Two consequences:

- **Swap is impossible.** A and B cannot exchange cells, because each is standing in the other's destination when legality is checked. (This supersedes the earlier ruling that swaps resolve.)
- **You cannot follow a retreating enemy.** Standing adjacent to a hero denies it that cell for the whole exchange, even if you vacate it. Body-blocking is therefore strong, and with a 1-cell move budget it fires constantly.

**Same destination → both bounce.** If both heroes commit to the same empty cell, neither moves. Both still count as having acted.

**A bounced attacker's target cells are re-derived from its original position** — the pattern travels with the hero rather than staying at absolute coordinates. Being blocked displaces your shot instead of voiding it.

## 4. Heroes

### Stats

| Stat | Meaning |
|---|---|
| `max_hp` / `hp` | Health. At 0 the unit dies (but see §7.7 — death is interruptible). |
| `atk` | Damage of the normal attack. |
| `rng` | Range of the normal attack. |
| `attack_type` | Targeting pattern of the normal attack (§5). |
| `move_allowance` | Max cells of movement per action. |
| `footprint` | Cells occupied. **1 for most heroes**, but not all (§7.7). |
| `ap` / `max_ap` | Ability Points, a persistent capped pool. |
| `ap_per_turn` | AP granted at end of turn. Default 1, frequently overridden to 0 or to an external source. |
| `attacks_per_turn` | Default 1. |
| `abilities` | Passive and active abilities. |

Every one of these must be modifiable at runtime, temporarily or permanently, by any source (§7.3).

### Action

Optionally **move** up to `move_allowance`, then optionally either make a normal attack **or** use one active ability. Move and attack are independent — a hero may do both, either, or neither.

### AP

- Persistent pool carried across turns, clamped at `max_ap`, starting at **0**.
- Granted at the **end** of the hero's turn.

Two useful consequences: round one is uniform (no hero has AP on its first turn, so the opening round is always move-plus-normal-attack), and **AP cost reads directly as cooldown length** — cost 1 is every turn, cost 2 every other turn, cost 3 every third. `max_ap` separately controls how long a hero can bank toward something expensive.

**AP generation is not universal.** Designs exist that generate no AP at all, that receive AP from allies, that draw AP from the number of tokens they control, or that steal it from adjacent enemies. Treat the default end-of-turn grant as a rule that any effect can suppress or replace, not as engine behaviour (§7.12).

**Some abilities are once-per-match** rather than AP-gated. Use limits are a separate concept from cost and both must exist.

---

## 5. Targeting

**No friendly fire by default** — allies are not legal targets for enemy-seeking patterns. Individual effects that target allies (heals, buffs, sacrifices) or that target *any* unit regardless of side declare so explicitly in their filter.

### Normal attacks

A normal attack picks **one enemy within `rng`** and damages it for `atk`. This is the near-universal case.

### Ability targeting modes

| Mode | Committed as | Resolution |
|---|---|---|
| Cell-locked ("X cells within Y") | coordinates | one enemy standing there takes damage, or nobody |
| Anchored (surround-8, row line, column line) | one enemy in the pattern | that enemy, or nothing if it left |
| Unit-locked (`one_chosen`) | hero identity | lands regardless of position |
| Global (`all_enemies`) | nothing | every living enemy |

**Cell-locked X is an accuracy stat, not an area stat** — more cells means a wider net for catching a moving enemy, never more damage.

**Anchored attacks fizzle** if the committed target isn't in the pattern at resolution — no retarget. Since the pattern originates from the attacker's post-move position, a movement collision can invalidate an otherwise-good attack.

**The one-enemy-per-attack rule applies to normal attacks only.** Abilities routinely hit every enemy in a row, in a column, in all adjacent cells, or on the whole board. Multi-target damage is normal ability behaviour; the target-count is a property of each ability's targeting spec, not a global invariant.

### Damage attributes

Damage carries an **element** tag (fire, water, thunder, wood, or none) and a **source category**. Both live on the damage event, not on the attacker (§7.2).

Source categories are load-bearing: at least one passive triggers on normal-attack and ability damage but explicitly *not* on tile damage. The minimum set is `NORMAL_ATTACK`, `ABILITY`, `TILE`, with `STATUS` and `REFLECTED` reserved. Any effect that reacts to damage must declare which categories it listens to; none may default to "all" implicitly.

An element tag on its own does nothing. It exists so later designs can key off it.

---

## 6. Data model sketch

```
Match
  board: Board                 # cells + cell effects + topology
  entities: [Entity]           # dynamic; not just the 8 starting heroes
  turn_queue: TurnQueue        # mutable, §7.8
  scheduler: EffectScheduler   # delayed/expiring effects, §7.4
  event_bus: EventBus          # global, §7.1
  round_number: int

Entity                         # heroes, summons, tokens, hazards, parts of multi-part units
  id
  side: SideRef                # mutable — units change allegiance
  cells: Set[Coord]
  base_stats: Stats
  modifiers: [Modifier]        # all stat reads go through the stack, §7.3
  ap: int
  abilities: [Ability]
  state_tag: str               # for multi-stage units, §7.7
  flags: {takes_turns, blocks_movement, counts_for_defeat, targetable, ...}
  has_acted: bool

Ability
  kind: PASSIVE | ACTIVE
  ap_cost: int
  use_limit: int | UNLIMITED
  targeting: TargetingSpec
  effects: [Effect]            # damage, heal, move, buff, spawn, schedule...
  handlers: {event -> fn}      # PASSIVE

TargetingSpec
  mode: NORMAL | CELL_LOCKED | ANCHORED | UNIT_LOCKED | GLOBAL | SELF | ALLY | ANY_CELL
  shape: SURROUND_8 | ROW | COLUMN | WITHIN_RANGE | ADJACENT_CLUSTER | GLOBAL
  range: int | STAT_REF("rng")
  num_cells: int
  target_count: int | ALL
  filter: ENEMY | ALLY | ANY | SELF

CommittedAction
  entity_id
  destination / path
  action: NONE | NORMAL_ATTACK | ABILITY(id)
  target: EntityId | [Coord] | NONE
  choices: {}                  # weapon selection, direction, X value, etc.
```

`choices` matters: several designs require the player to parameterise an action beyond picking a target — selecting a weapon for the turn, a direction, or a magnitude to spend. The committed action must be able to carry arbitrary per-ability parameters.

---

## 7. Architecture

The roster sample makes clear that the engine's job is **not** to implement hero behaviour. It is to provide a substrate general enough that hero behaviour is authored as data plus small handlers. Everything below is a requirement derived from mechanics the roster actually contains.

### 7.1 Everything is an event, and the bus is global

The mechanics that break naive designs are the ones that fire during *someone else's* action: auras that modify other units' stats, retaliation on being hit, triggers on any unit's death, effects that fire when an enemy targets multiple allies. So the event bus is global and every meaningful state change is published on it.

Minimum event set: `match_start`, `placement_start`, `placement_end`, `round_start`, `round_end`, `turn_start`, `turn_end`, `before_move`, `after_move`, `attack_declared`, `before_damage`, `after_damage`, `heal`, `before_death`, `death`, `stat_changed`, `ap_changed`, `entity_spawned`, `cell_entered`, `cell_left`, `ability_used`.

`cell_entered` in particular is load-bearing: mines, traps, hazard tiles, and movement-triggered effects all hang off it, and it must fire during movement resolution, not only at the end of a move.

### 7.2 Damage is an object flowing through a pipeline

Never `target.hp -= n`. Construct a `DamageEvent {source, target, amount, element, category, tags}` and pass it through an ordered pipeline where handlers may modify or cancel it. The roster requires at minimum: flat reduction, flat increase, shields absorbing one instance, immunity for a duration, immunity to all further damage this round, damage swapping between attacker and defender, damage redirection to another unit, and full prevention at the cost of a stat.

Two requirements that fall out:

- **Ordering must be explicit.** Additive modifiers, multiplicative modifiers, shields, and cancellation cannot be applied in registration order; assign each handler a phase.
- **Source and total must be recorded.** Effects reference "the source of that damage" and "the total damage this unit dealt during its turn," so damage events are logged per turn and queryable.

Healing follows the same pipeline shape and should share it.

### 7.3 Modifiers carry duration and provenance

A modifier is `{stat, op, value, source, duration, expiry_anchor}`. Durations observed in the roster include: permanent, until the source dies, until the start of the modifier-holder's next turn, until the start of the *applier's* next turn, this round only, N rounds, and while a positional condition holds. Conditional modifiers that recompute continuously ("+1 per adjacent ally," "while HP ≤ threshold") must be evaluated at read time rather than applied once.

Caps exist (stacking bonuses that stop at a ceiling), so modifiers need clamping, and permanent stat *loss* is also used, so effects can trade one stat for another.

### 7.4 A scheduler for delayed effects

Numerous effects resolve on a future turn rather than now: damage that lands two turns later, a buff that arrives next turn and reverses the turn after, a bomb that detonates after three rounds, a revived unit that dies on a fixed schedule, a unit that dies a set number of rounds after a trigger. The engine needs a scheduled-effect queue keyed to timing anchors, with each entry able to be cancelled if its source is removed.

### 7.5 The board has its own state layer

Cells carry effects independent of their occupants: persistent burning tiles that damage whoever starts a turn there and stack when applied repeatedly, hidden traps, mines that trigger on entry, delayed explosives, zone markers drawn across a set of cells whose *geometric arrangement* changes their effect, and territory ownership per cell. So `Board` is not `Entity[45]`; it is cells with their own effect lists, lifetimes, owners, and visibility.

### 7.6 Adjacency and topology are queries, not arithmetic

Do not compute neighbours as `±1` anywhere outside a single topology module. The roster contains: units for which diagonals count as adjacent for both movement and attack, paired cells made adjacent by a placed object, board area being permanently removed mid-match, an entire sub-map being attached to the main board partway through, forced movement and teleportation, and rows a given side simply cannot enter.

Every adjacency, distance, line-of-fire, and legal-destination check must route through `topology.neighbours(cell, entity)` / `topology.distance(a, b, entity)` / `topology.can_enter(entity, cell)`. Making this a query from day one is cheap; retrofitting it is close to a rewrite.

### 7.7 The entity set is dynamic, and so is allegiance

Units are created mid-match by many designs, take turns, have stats, and can be attacked. Existing units change side. Units have multiple stages with wholly different stats and abilities. Some units occupy cells without blocking them, some occupy several cells and act in parts, some never take a turn at all. Death is interruptible — several effects replace a lethal damage instance with something else, so `before_death` must be cancellable and `hp ≤ 0` must not itself remove the unit.

Therefore: no code should assume four heroes per side, one cell per unit, static teams, immutable stat blocks, or that reaching zero HP means removal. The `flags` set on `Entity` is what keeps these variations out of the core loop.

**Footprint is no longer safely deferrable to "later."** Multi-cell units exist in more than one form, including one that occupies cells without blocking them. Keep positions as `Set[Coord]` from the first commit; §9 still holds the unresolved orientation and partial-overlap questions.

### 7.8 The turn queue is mutable

Skipped turns, doubled turns, an extra move appended after a turn, one unit spending its turn to grant another unit a turn, units that never take turns, and multi-part units whose parts act in sequence within one turn slot. Model the round as a queue with operations `skip`, `insert_after`, `append_extra`, `delegate`, and `remove`, and let effects manipulate it.

### 7.9 Reactions are the hardest problem in the design

Many effects are **optional** and trigger during another player's resolution: "when you would take lethal damage, you *may* prevent it at a cost," "when an enemy ability targets several of your units, you *may* exempt one." Hidden simultaneous commitment means there is no natural place for the defender to make that choice — pausing to ask leaks information about what is being resolved, and the exchange is supposed to resolve atomically.

Two workable designs, and this needs deciding before the resolver is written:

- **Pre-committed reaction policy.** Along with an action, a side commits its reaction choices for the coming exchange ("if my unit would die, spend the charge"). Preserves hidden information and atomic resolution; less expressive, and the player commits blind.
- **Resolution pause with masked prompt.** The resolver halts at the trigger and prompts, showing only the minimum needed to decide. Fully expressive; leaks some information and makes resolution re-entrant, which complicates any AI or replay system considerably.

The first is far cheaper and fits the game's existing commit-blind philosophy. It should be the default unless a specific design proves it insufficient.

### 7.10 Game state is per-player, not global

Hidden traps and objects visible only to their owner, units whose true identity is concealed until a reveal condition, and secret placement all mean the server holds authoritative state and each client receives a **filtered view**. Build the view filter early. Adding it after the UI reads global state is painful, and any hidden-information mechanic is unimplementable without it.

### 7.11 Heroes are declarative data

Hero definitions should be data — stat block plus ability entries plus handler references — not subclasses. Two reasons beyond the usual: hero definitions need to be *inspectable and mutable at runtime* (at least one design edits a numeric field in another hero's definition mid-match), and the roster is large enough that anything requiring a code change per hero will not scale. Aim for a small library of composable effect primitives (`Damage`, `Heal`, `Move`, `ForceMove`, `ApplyModifier`, `Spawn`, `Schedule`, `SetCellEffect`, `SkipTurn`, `GrantAP`, `Transform`) that hero data assembles.

### 7.12 Resource generation is a rule, not engine behaviour

End-of-turn AP grant, movement allowance, attack count, and even whether a unit takes a turn are all defaults that specific designs override. Implement each as a value produced by the modifier stack and consumed by the loop — never as a literal in the loop.

---

## 8. Implementation order

1. Board, entities, placement, and the round/exchange loop with movement only. Get collision and swap right — they are the subtlest bugs.
2. Topology module (§7.6) and per-player view filtering (§7.10). Both are cheap now and expensive later.
3. Snapshot-based simultaneous damage through the damage pipeline (§7.2), including mutual kills.
4. Event bus and modifier stack with durations (§7.3).
5. Normal attacks, then the ability targeting modes one at a time; anchored last.
6. AP, active abilities, use limits, and the fizzle path.
7. Scheduler, cell effects, dynamic entities.
8. Reaction model (§7.9).

Build two or three deliberately awkward test heroes early — one with a reaction, one that spawns a unit, one that alters topology — rather than a batch of simple ones. Simple heroes validate nothing.

---

## 9. Open questions

**Blocking:**

1. **Reaction model** (§7.9): pre-committed policy or resolution pause. Determines whether the resolver is atomic or re-entrant. Not yet needed for the first six heroes, none of which has a reaction.

**Settled since the last revision:** cell-locked tie-break (attacker picks live, §3); movement is a 1-step L1 move into a snapshot-empty cell; swaps are impossible; bounced attackers re-derive their cells from their original position; damage carries a source category.

**Non-blocking:**

2. **Does a fizzled active refund its AP?** Leaning consume.
3. **Does a unit that takes no action still gain AP?** If yes, "pass to charge" is a legitimate strategy.
4. **`one_chosen` range.** Currently unlimited, which is why 雷霆龙's normal attack reaches the whole board.
5. **Placement.** Hidden and simultaneous?
6. **Multi-cell units.** Orientation, anchor cell for patterns, partial overlap, and occupying-vs-blocking.
7. **Move budgets above 1.** All six current heroes move 1. Once a hero moves 2+, "1-step into an empty cell" must generalise: is it a sequence of steps each requiring an empty cell, and is the *path* checked against the snapshot or updated as the hero walks?

---

## 10. Implemented heroes

Authored as data. `cell_locked(X, Y)` = choose X free cells within L1 distance Y of the post-move position; one enemy among them takes the damage, chosen live by the attacker.

### 1. 枪兵 Spearman
`HP 19 · Atk 4 · cell_locked(3, 2) · move 1 · max_ap 3`

**横扫 Sweep — 2 AP.** Own column plus one adjacent column, forward or backward, chosen at commit: a 2×5 block of 10 cells. Deals 4 to **every enemy** inside it; allies unaffected. No victim pick and no fizzle — the direction is the only commitment, and the block travels with the spearman if he is bounced.

### 2. 岩石巨人 Rock Giant
`HP 26 · Atk 2 · cell_locked(3, 2) · move 1 · max_ap 0`

**(P)** The first damage of the round from a `NORMAL_ATTACK` or `ABILITY` source lands in full; for the remainder of that round he is immune to all further damage of those categories. Resets at round start. `TILE` damage neither triggers the passive nor is blocked by it. `max_ap 0` means he never uses actives.

### 3. 机器人 Robot
`HP 24 · Atk 3 · cell_locked(3, 2) · move 1 · max_ap 0`

**(P) Self-repair.** Heals 4 at the end of **his own turn** — once per round, not once per exchange. Capped at max HP.

### 4. 雷霆龙 Thunder Dragon
`HP 17 · Atk 1 · one_chosen (unbounded) · move 1 · max_ap 6`

**雷暴 Thunderstorm — 3 AP.** 3 damage to every living enemy, element `thunder`. No targeting, no counterplay. `max_ap 6` lets him bank two charges.

### 5. 火法师 Fire Mage
`HP 17 · Atk 2 · cell_locked(4, 8) · move 1 · max_ap 3`

**点燃 Ignite — 1 AP.** Sets any one cell on the board alight, permanently, at unlimited range. An **enemy** starting its turn on a burning cell takes 2 `fire` damage as `TILE`. Repeated ignitions of the same cell stack additively. Allies and the mage himself are unaffected. Damage is checked at the very start of a hero's turn, before it moves or acts — a hero killed there loses its committed action entirely, and its opponent that exchange acts unopposed. Moving *across* a burning cell is safe.

### 6. 双枪手 Gunslinger
`HP 16 · Atk 4 · cell_locked(4, 7) · move 1 · max_ap 0`

**(P)** Two normal attacks per turn, resolved **sequentially**, not simultaneously — the first fully resolves, including death, before the second begins. Both cell-sets are committed up front; each gets its own live victim pick. The second attack deals half damage, rounded down, applied after all other modifiers. Both may target the same enemy.

### Rulings made during implementation

These were needed to make the code run and are not yet confirmed. Each is flagged in the source.

1. **Multi-attack interleaving.** A hero with two attacks resolves its first simultaneously with the opposing action, then its second against the updated board. So two hits arriving in the same instant both land on 岩石巨人, but the gunslinger's second shot is blocked by the immunity its first triggered. This is what makes "sequential, not simultaneous" mean something.
2. **岩石巨人's immunity does not block tile damage**, only fails to be triggered by it. The alternative leaves a fire tile shielding him for free.
3. **Selecting a hero is irreversible** — that is when its turn starts and fire resolves, so there is no undo.
4. **Enemy units render from a snapshot during the commit phase.** Without this a turn-start fire tick reveals which hero the opponent picked up, which breaks hidden commitment. Not previously in the spec.
5. **A unit killed mid-exchange forfeits its later sequential attacks** but keeps the simultaneous one.

**Smaller assumptions:** heals cap at max HP · burning cells are visible to both players · a hero killed at turn start still counts as having acted · 岩石巨人's immunity clears at round start · a hero may deliberately pass · both sides may field the same hero.

### The Robot stalemate — needs a fix

机器人 heals 4 at the end of its own turn and attacks for 3. It therefore cannot kill another 机器人. Nor can 枪兵 (4 damage, exactly cancelled), 岩石巨人 (2), 火法师 (2), or 雷霆龙 (1). Only 双枪手 out-damages the regeneration, at 4 + 2 = 6.

If both sides' last hero is a 机器人, the match is mathematically unwinnable. Across 500 bot-played matches this was the cause of **every** game that failed to terminate — 100% of non-ending matches ended with only Robots alive.

More generally, per-turn regeneration on the same scale as per-turn damage is a structural hazard on a roster this small. Options: cap the heal, suppress it in any round the unit took damage, cap it as a fraction of max HP, or impose a round limit that resolves on total HP.

### Balance observations from a first playthrough

- **Round one has no ability variance at all.** Nobody has AP, so four exchanges pass as plain normal attacks. 火法师 and 雷霆龙 spend that round as weak archers.
- **Burning tiles only accumulate.** Permanent, stacking, unlimited range, and nothing in the roster removes them. A mage that survives ten rounds has fenced off the board.
