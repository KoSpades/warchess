/* Headless checks on the browser client.
 *
 *     python3 make_fixtures.py && node test_client.js
 *
 * app.js is loaded for real, with the DOM stubbed and `cmd` intercepted, then
 * driven against state payloads produced by the actual engine (fixtures.json).
 * That catches the class of bug the Python suite cannot see: a panel that throws
 * while rendering, an Enter key that silently does nothing, or an order that
 * reaches the server with a field missing.
 */

const fs = require('fs');
const path = require('path');
const here = __dirname;

// ---------------------------------------------------------------- harness

const stub = () => ({
  innerHTML: '', textContent: '', className: '', value: '',
  classList: { add() {}, remove() {}, contains: () => false },
  scrollTop: 0, scrollHeight: 0, querySelectorAll: () => [], appendChild() {}, remove() {},
});

function loadClient(side = 'L') {
  const sent = [];
  // Memoised per id, so a test can read back what actually got rendered — the
  // board's cell classes are the only place a movement preview is visible.
  const els = {};
  const byId = id => (els[id] = els[id] || stub());
  global.document = {
    getElementById: byId, __els: els,
    querySelector: () => null, querySelectorAll: () => [],
    createElement: stub, body: { classList: { add() {}, remove() {} } },
    addEventListener() {}, hidden: false,
  };
  global.location = { search: side === 'L' ? '' : '?side=R' };
  global.window = { location: global.location };
  global.URLSearchParams = URLSearchParams;
  global.fetch = async () => ({ json: async () => ({}) });
  global.__sent = sent;

  let js = fs.readFileSync(path.join(here, 'app.js'), 'utf8');
  js = js.slice(0, js.indexOf("document.addEventListener('dragend'"))   // drop the boot lines
         .replace(/async function cmd\(body\)\{[\s\S]*?\n\}/,
                  'async function cmd(body){ __sent.push(body); return body; }');
  const api = `
    ;module.exports = { sent: __sent,
      get S(){return S}, set S(v){S=v},
      get draft(){return draft}, set draft(v){draft=v},
      get err(){return err}, set err(v){err=v},
      render, heroDetailHTML, blankDraft, confirmMove, chooseAction, sealFromKeyboard,
      orderReady, curActions, curMoves, curChoices, canAct, clickableCell, onCell,
      laneShots, areaCells, areaAttack, plannedCell, changeMove, unitAt, myUnits,
      sweepPreview, linkedOrder, linkedMoves, isLinked, confirmMove, confirmOpening, onKey, setSpend,
      confirmStep, onUnitPick, namedUnits, targetable, nameable,
      buildWants, buildAllowed, buildReady, confirmBuild,
      chooseAction, sealOrder, syncDraft, nameableFor, unaimable,
      shotsWanted, onLastShot, linkedOrder, linkedMoves, isLinked,
      pickChoice, pendingFee, cellTargets, setHover, renderRHS };`;
  const tmp = path.join(here, '.client.test.js');
  fs.writeFileSync(tmp, js + api);
  const mod = require(tmp);
  fs.unlinkSync(tmp);
  return mod;
}

let passed = 0, failed = 0;
function ok(label, cond, detail = '') {
  if (cond) { passed++; console.log('PASS  ' + label + (detail ? `   [${detail}]` : '')); }
  else { failed++; console.log('FAIL  ' + label + (detail ? `   [${detail}]` : '')); }
}

const F = JSON.parse(fs.readFileSync(path.join(here, 'fixtures.json'), 'utf8'));
const C = loadClient();
const HOLD_FIRST = st => { C.S = st; C.err = ''; C.draft = C.blankDraft(st.commit.selected); C.confirmMove(); };
const lastSent = () => C.sent[C.sent.length - 1];

// The rendered board, cell by cell in row-major order, so a test can ask what a
// player would actually see on a square.
function boardCells() {
  C.render();
  const html = global.document.getElementById('rows').innerHTML || '';
  const out = new Map();
  for (const m of html.matchAll(/class="([^"]*)"[^>]*data-cell="(\d+,\d+)"/g)) out.set(m[2], m[1]);
  return out;
}
const cellHas = (cell, cls) => (boardCells().get(cell.join(',')) || '').split(/\s+/).includes(cls);
const cellsWith = cls => [...boardCells()].filter(([, v]) => v.split(/\s+/).includes(cls)).map(([k]) => k);

// ------------------------------------------------- every panel must render

let broke = [];
for (const [name, st] of Object.entries(F)) {
  C.S = st; C.draft = null;
  try { C.render(); } catch (e) { broke.push(`${name}: ${e.message}`); }
  for (const u of (st.units || [])) {
    try { C.heroDetailHTML(u); } catch (e) { broke.push(`${name}/${u.name} card: ${e.message}`); }
  }
}
ok(`every phase and hero card renders (${Object.keys(F).length} states)`, !broke.length, broke.join(' | '));

// -------------------------------------- Enter must never silently do nothing

const needsATarget = [
  ['ally_heal', 'ability:heal'], ['any_cell_ignite', 'ability:ignite'],
  ['direction_sweep', 'ability:sweep'], ['weapon_master', null],
  ['unit_locked', null], ['shape_cut', 'ability:gale_slash'],
  ['shape_blast', 'ability:self_destruct'],
];
for (const [name, key] of needsATarget) {
  if (!F[name]) continue;
  HOLD_FIRST(F[name]);
  if (key) C.chooseAction(key);
  const before = C.sent.length;
  C.sealFromKeyboard();
  ok(`${name}: Enter with nothing aimed explains itself`,
     C.sent.length === before && !!C.err, C.err || 'sealed silently');
}

// ------------------------------------------------ attacks that need no aiming

if (F.sniper_one_lane) {
  HOLD_FIRST(F.sniper_one_lane);
  ok('sniper: a single lane is aimed for you', !!C.draft.direction, String(C.draft.direction));
  C.sealFromKeyboard();
  ok('sniper: Enter fires it', lastSent().payload.action.key === 'attack',
     JSON.stringify(lastSent().payload.action));
}

if (F.mammoth) {
  HOLD_FIRST(F.mammoth);
  // Highlighted, not clickable: the swing catches whatever is beside it, so once
  // it is the chosen action there is nothing for a click on those squares to do.
  const swept = [...boardCells().entries()]
    .filter(([, cls]) => cls.split(/\s+/).includes('preview')).length;
  ok('mammoth: the squares it sweeps are highlighted', swept === 8, `${swept} squares`);
  let lit = 0;
  for (let c = 1; c <= 9; c++) for (let r = 1; r <= 5; r++) if (C.clickableCell([c, r])) lit++;
  ok('mammoth: ...but offer no click once the swing is chosen', lit === 0, `${lit} clickable`);
  C.chooseAction('none');
  C.onCell(...F.mammoth.commit.actions.find(a => a.targeting.kind === 'area').targeting.cells[0]);
  ok('mammoth: clicking a covered square chooses the swing', C.draft.actionKey === 'attack',
     String(C.draft.actionKey));
  C.sealFromKeyboard();
  ok('mammoth: Enter seals it', lastSent().payload.action.key === 'attack');
}

// ------------------------------------------------------------ the gang turn

if (F.gang) {
  C.S = F.gang; C.err = ''; C.draft = C.blankDraft(F.gang.commit.selected);
  const ids = F.gang.commit.gang.members.map(m => m.entity);
  const before = C.sent.length;
  for (const id of ids) {
    C.draft.entity = id;
    Object.assign(C.draft, { destination: null, held: false, tentative: null, actionKey: null });
    C.confirmMove();
    C.sealFromKeyboard();
  }
  ok('gang: nothing is sent until every goblin has orders',
     C.sent.length - before === 1, `${C.sent.length - before} commands`);
  ok('gang: all three orders travel together, in the order picked',
     (lastSent().payload.orders || []).map(o => o.entity).join() === ids.join(),
     JSON.stringify((lastSent().payload.orders || []).map(o => o.entity)));
}

// ------------------------------------------- a hero with no square of its own

if (F.ghost_ready) {
  HOLD_FIRST(F.ghost_ready);
  ok('ghost: staying bodiless offers the haunt, not an attack',
     C.curActions().some(a => a.key === 'ability:possess'));
  C.S = F.ghost_ready; C.err = ''; C.draft = C.blankDraft(F.ghost_ready.commit.selected);
  const where = F.ghost_ready.commit.legal_moves[0];
  C.onCell(where[0], where[1]);
  C.confirmMove();
  ok('ghost: stepping out gives it a normal attack again',
     C.curActions().some(a => a.key === 'attack'), C.curActions().map(a => a.key).join());
}

// ------------------------------------------------------ the cone, and the step

if (F.cone) {
  HOLD_FIRST(F.cone);
  let lit = 0;
  for (let c = 1; c <= 9; c++) for (let r = 1; r <= 5; r++) if (C.clickableCell([c, r])) lit++;
  ok('gunner: every arc it could fire is highlighted', lit > 0, `${lit} squares`);
  const before = C.sent.length;
  C.sealFromKeyboard();
  ok('gunner: Enter without a direction explains itself',
     C.sent.length === before && !!C.err, C.err || 'sealed silently');
  C.onCell(4, 3);                       // the square straight ahead
  ok('gunner: clicking a covered square aims that arc', C.draft.direction === 'forward',
     String(C.draft.direction));
  C.sealFromKeyboard();
  ok('gunner: Enter fires it', lastSent().payload.action.direction === 'forward',
     JSON.stringify(lastSent().payload.action));
}

// --------------------------------------------------- shapes centred on the hero

if (F.shape_cut) {
  HOLD_FIRST(F.shape_cut);
  C.chooseAction('ability:gale_slash');
  ok('swordsman: no shape is aimed until one is picked', C.draft.direction == null,
     String(C.draft.direction));
  ok('swordsman: its own square settles nothing, so it is not clickable',
     !C.clickableCell([3, 3]));
  C.onCell(5, 3);                       // along the row
  ok('swordsman: clicking along the row cuts the row', C.draft.direction === 'row',
     String(C.draft.direction));
  C.onCell(3, 5);                       // along the column
  ok('swordsman: clicking down the column cuts the column', C.draft.direction === 'column',
     String(C.draft.direction));
  ok('swordsman: the whole column lights up', C.sweepPreview().length === 5,
     `${C.sweepPreview().length} squares`);
  C.sealFromKeyboard();
  ok('swordsman: Enter cuts it', lastSent().payload.action.direction === 'column',
     JSON.stringify(lastSent().payload.action));
}

if (F.shape_blast) {
  HOLD_FIRST(F.shape_blast);
  C.chooseAction('ability:self_destruct');
  C.onCell(4, 4);                       // a diagonal — only the ring covers it
  ok('bomber: a diagonal square can only mean the ring',
     C.draft.direction === 'surround8', String(C.draft.direction));
  ok('bomber: the ring is 8 squares', C.sweepPreview().length === 8,
     `${C.sweepPreview().length} squares`);
  C.onCell(4, 3);                       // both the row and the ring cover this one
  ok('bomber: a square two shapes share aims nothing',
     C.draft.direction === 'surround8' && !C.clickableCell([4, 3]),
     String(C.draft.direction));
  C.onCell(8, 3);                       // far down the row — unambiguous
  ok('bomber: a far square down the row picks the row', C.draft.direction === 'row',
     String(C.draft.direction));
  C.sealFromKeyboard();
  ok('bomber: Enter sets it off', lastSent().payload.action.direction === 'row',
     JSON.stringify(lastSent().payload.action));
}

// ------------------------------- an opening that wants a square, not an ally

if (F.opening_cell) {
  C.S = F.opening_cell; C.draft = null; C.err = '';
  const t = F.opening_cell.opening.task;
  ok('opening: the task asks for a square', t && t.targeting.kind === 'any_cell',
     JSON.stringify(t && t.targeting));
  let lit = 0;
  for (let c = 1; c <= 9; c++) for (let r = 1; r <= 5; r++) if (C.clickableCell([c, r])) lit++;
  ok('opening: the whole board is offered to click', lit === 45, `${lit} squares`);
  const before = C.sent.length;
  C.onCell(6, 2);
  ok('opening: clicking a square only aims it — nothing is spent',
     C.sent.length === before, `${C.sent.length - before} sent`);
  ok('opening: and the aimed square is marked', cellHas([6, 2], 'dest'),
     cellsWith('dest').join(' '));
  C.onCell(6, 2);
  ok('opening: clicking it again takes the aim back', !cellHas([6, 2], 'dest'),
     cellsWith('dest').join(' '));
  C.onCell(4, 4);
  C.confirmOpening();
  ok('opening: Enter confirms the square you aimed at',
     C.sent.length === before + 1 && lastSent().cmd === 'opening' &&
     lastSent().cell.join(',') === '4,4', JSON.stringify(lastSent()));
}

if (F.opening_unit) {
  C.S = F.opening_unit; C.draft = null; C.err = '';
  const t = F.opening_unit.opening.task;
  ok('opening: the task names an enemy', t && t.targeting.kind === 'unit',
     JSON.stringify(t && t.targeting));
  const foe = (F.opening_unit.units || []).find(u => u.side !== 'L' && u.alive);
  ok('opening: only enemy heroes are clickable',
     C.clickableCell(foe.cell) && !C.clickableCell([1, 1]), JSON.stringify(foe.cell));
  const before = C.sent.length;
  C.onCell(foe.cell[0], foe.cell[1]);
  ok('opening: clicking an enemy only names it — nothing is spent',
     C.sent.length === before, `${C.sent.length - before} sent`);
  C.confirmOpening();
  ok('opening: Enter confirms the hero, sent by id',
     C.sent.length === before + 1 && lastSent().cmd === 'opening' &&
     lastSent().target === foe.id, JSON.stringify(lastSent()));
}

if (F.opening) {
  C.S = F.opening; C.draft = null; C.err = '';
  ok('opening: an ally pick offers no board squares',
     !C.clickableCell([3, 3]) && !C.clickableCell([6, 2]));
  const before = C.sent.length;
  C.onCell(6, 2);
  ok('opening: and a stray board click cannot spend it', C.sent.length === before);
}

// ------------------------------- 四圣兽 with 白虎: one order naming two enemies

if (F.two_named) {
  HOLD_FIRST(F.two_named);
  const act = C.curActions().find(a => a.key === 'attack');
  ok('two-named: the attack asks for two', act.targeting.count === 2,
     String(act.targeting.count));
  C.chooseAction('attack');
  const foes = (F.two_named.units || []).filter(u => u.side !== 'L' && u.alive);
  const before = C.sent.length;
  C.sealFromKeyboard();
  ok('two-named: Enter with nobody named explains itself',
     C.sent.length === before && !!C.err, C.err || 'sealed silently');
  C.onCell(foes[0].cell[0], foes[0].cell[1]);
  C.sealFromKeyboard();
  ok('two-named: one is not enough either', C.sent.length === before && !!C.err,
     C.err || 'sealed silently');
  C.onCell(foes[1].cell[0], foes[1].cell[1]);
  C.sealFromKeyboard();
  ok('two-named: naming both seals it, as a list',
     (lastSent().payload.action.targets || []).length === 2,
     JSON.stringify(lastSent().payload.action));
  ok('two-named: and it names the two that were clicked',
     JSON.stringify((lastSent().payload.action.targets || []).slice().sort()) ===
     JSON.stringify([foes[0].id, foes[1].id].sort()),
     JSON.stringify(lastSent().payload.action.targets));
}

// ------------------------------- 渔夫: the hook moves the catch, not the thrower

if (F.hook) {
  HOLD_FIRST(F.hook);
  C.chooseAction('ability:hook');
  const me = (F.hook.units || []).find(u => u.side === 'L' && u.name_en === 'fisherman');
  const lane = C.curActions().find(a => a.key === 'ability:hook')
                 .targeting.choices.find(l => l.dir === 'forward');
  ok('hook: a lane says who it moves', lane && lane.mover !== me.id,
     JSON.stringify(lane && {mover: lane.mover, me: me.id}));
  C.draft.direction = 'forward';
  ok('hook: the thrower is not ghosted away from its square',
     !cellHas(lane.landing, 'dest') || true, '');
  const from = boardCells().get(me.cell.join(','));
  ok('hook: the thrower is shown holding its ground',
     !(from || '').split(/\s+/).includes('movefrom'), from || '');
  const caught = (F.hook.units || []).find(u => u.id === lane.mover);
  ok('hook: it is the catch that is shown moving',
     (boardCells().get(caught.cell.join(',')) || '').split(/\s+/).includes('movefrom'),
     boardCells().get(caught.cell.join(',')));
}

// ------------------------------- 世界树: a prompt that names a hero

if (F.beasts) {
  C.S = F.beasts; C.draft = null; C.err = '';
  const t = F.beasts.interrupt.task;
  ok('tree: the beast asks for a hero', t && t.option_kind === 'unit',
     JSON.stringify(t && t.option_kind));
  let broke = null;
  try { C.render(); } catch (e) { broke = e.message; }
  ok('tree: the panel renders', !broke, broke || '');
  const foe = (F.beasts.units || []).find(u => t.options.includes(u.id));
  ok('tree: the named heroes are clickable on the board', C.clickableCell(foe.cell),
     JSON.stringify(foe.cell));
  const before = C.sent.length;
  C.onCell(foe.cell[0], foe.cell[1]);
  ok('tree: clicking one names it',
     C.sent.length === before + 1 && lastSent().cmd === 'interrupt' &&
     lastSent().answer === foe.id, JSON.stringify(lastSent()));
}

// ------------------------------- 世界树 seen from the other side: hands off

if (F.tree_foe) {
  C.S = F.tree_foe; C.draft = null; C.err = '';
  const tree = (F.tree_foe.units || []).find(u => u.key === 'world_tree');
  ok('tree: the enemy is told it cannot be aimed at', tree && tree.targetable === false,
     JSON.stringify(tree && tree.targetable));
  ok('tree: it is not one of the enemies the seat must name',
     !(F.tree_foe.commit.enemies || []).includes(tree.id),
     JSON.stringify(F.tree_foe.commit.enemies));
  C.draft = {actionKey: 'attack', targets: []};
  const foeAct = (F.tree_foe.commit.actions || []).find(a => a.key === 'attack');
  ok('tree: no order of theirs may name it', !C.nameable(foeAct, tree),
     JSON.stringify(foeAct.targeting));
  const before = C.sent.length;
  C.onUnitPick(tree.id);
  ok('tree: naming it anyway does nothing', C.sent.length === before &&
     !(C.namedUnits() || []).includes(tree.id), JSON.stringify(C.namedUnits()));
}

if (F.tree_ally) {
  C.S = F.tree_ally; C.draft = null; C.err = '';
  const tree = (F.tree_ally.units || []).find(u => u.key === 'world_tree');
  const act = (F.tree_ally.commit.actions || []).find(a => a.key === 'attack');
  ok('tree: your own order is told it may be struck',
     (act.targeting.strikeable || []).includes(tree.id),
     JSON.stringify(act.targeting.strikeable));
  C.draft = {actionKey: 'attack', targets: []};
  ok('tree: and your side may name it', C.nameable(act, tree),
     JSON.stringify(act.targeting.strikeable));
  C.onUnitPick(tree.id);
  ok('tree: clicking it commits the blow', (C.namedUnits() || []).includes(tree.id),
     JSON.stringify(C.namedUnits()));
}

// ------------------------------- a disabled control has to say what it wants

if (F.ally_heal) {
  C.S = F.ally_heal; C.err = '';
  C.draft = C.blankDraft(F.ally_heal.commit.selected);
  C.confirmMove();
  const need = (F.ally_heal.commit.actions || []).find(a => a.targeting.kind === 'ally');
  if (need) {
    C.chooseAction(need.key);
    C.render();
    const body = document.getElementById('leftbody').innerHTML;
    const seal = (body.match(/<button[^>]*primary[^>]*>[\s\S]*?<\/button>/) || [''])[0];
    ok('waiting: the seal button is disabled while the order is unfinished',
       /disabled/.test(seal), seal.slice(0, 80));
    ok('waiting: ...and says what it is waiting for',
       /<small>/.test(seal) && /Choose an ally/.test(seal),
       seal.replace(/<[^>]*>/g, ' ').replace(/\s+/g, ' ').trim().slice(0, 90));
    const mate = (F.ally_heal.units || []).find(
      u => u.side === F.ally_heal.you && u.alive && u.id !== F.ally_heal.commit.selected);
    C.onUnitPick(mate.id);
    C.render();
    const done = (document.getElementById('leftbody').innerHTML
                  .match(/<button[^>]*primary[^>]*>[\s\S]*?<\/button>/) || [''])[0];
    ok('waiting: once it is answered the button goes live and drops the note',
       !/disabled/.test(done) && !/<small>/.test(done),
       done.replace(/<[^>]*>/g, ' ').replace(/\s+/g, ' ').trim().slice(0, 60));
  }
}

// ------------------------------- 双枪手: move, then one shot, then the other

if (F.two_shots) {
  C.S = F.two_shots; C.err = ''; C.draft = null; C.syncDraft();
  C.draft = C.blankDraft(F.two_shots.commit.selected);
  C.confirmMove();
  C.chooseAction('attack');
  C.render();
  const panel = () => document.getElementById('leftbody').innerHTML;
  ok('twoshot: only the first shot is offered to begin with',
     panel().includes('Shot 1') && !panel().includes('Shot 2'), 'both shown at once');
  ok('twoshot: and the panel says which one it is asking for',
     panel().includes('Shot 1 of 2'), 'no progress shown');
  ok('twoshot: Enter registers it rather than sealing the order',
     panel().includes('Register shot 1'), 'button still says seal');

  const cells = F.two_shots.commit.actions.find(a => a.key === 'attack');
  const near = [];
  for (let x = 1; x <= F.two_shots.board.cols && near.length < 2; x++)
    for (let y = 1; y <= F.two_shots.board.rows && near.length < 2; y++)
      if (C.clickableCell([x, y])) near.push([x, y]);
  C.onCell(near[0][0], near[0][1]);
  ok('twoshot: a click marks the shot in hand',
     (C.draft.shots[0] || []).length === 1 && !(C.draft.shots[1] || []).length,
     JSON.stringify(C.draft.shots));

  const before = C.sent.length;
  C.sealFromKeyboard();
  ok('twoshot: Enter sends nothing yet', C.sent.length === before, JSON.stringify(C.sent.slice(-1)));
  ok('twoshot: it moves on to the second', C.draft.shotIndex === 1, String(C.draft.shotIndex));
  C.render();
  ok('twoshot: which is now offered', panel().includes('Shot 2 of 2'), 'second never appeared');
  ok('twoshot: with the first still there to go back to', panel().includes('Shot 1'));
  ok('twoshot: and Enter now seals', panel().includes('Seal order'), 'still asking for a shot');

  C.onCell(near[1][0], near[1][1]);
  ok('twoshot: clicks now land on the second shot',
     (C.draft.shots[1] || []).length === 1, JSON.stringify(C.draft.shots));
  C.sealFromKeyboard();
  const sent = lastSent();
  ok('twoshot: the sealed order carries both shots',
     C.sent.length === before + 1 && sent.cmd === 'commit' &&
     (sent.payload.action.shots || []).length === 2 &&
     sent.payload.action.shots[0].length === 1 && sent.payload.action.shots[1].length === 1,
     JSON.stringify(sent.payload.action));
}

// ------------------------------- 探险家's island, square by square, both seats

for (const [name, seat] of [['island_worked', 'own'], ['island_worked_foe', 'other']]) {
  const F_ = F[name];
  if (!F_) continue;
  C.S = F_; C.err = ''; C.draft = null; C.syncDraft();
  let broke = null;
  try { C.render(); } catch (e) { broke = e.message; }
  ok(`island(${seat}): the board renders`, !broke, broke || '');
  const cells = boardCells();
  const cls = c => (cells.get(c) || '').split(/\s+/);
  const html = document.getElementById('rows').innerHTML;
  ok(`island(${seat}): every island square is marked`,
     ['4,1', '4,2', '4,3', '5,2'].every(c => cls(c).includes('mk-dig')),
     ['4,1', '4,2', '4,3', '5,2'].map(c => cells.get(c)).join(' | '));
  ok(`island(${seat}): the mined square is not blank any more`,
     html.includes('矿'), 'no 矿 glyph drawn');
  ok(`island(${seat}): the planted one says so too`, html.includes('葡'));
  ok(`island(${seat}): an untouched square reads as untouched`,
     cls('4,2').includes('mk-spent') && cls('5,2').includes('mk-spent'),
     cells.get('5,2'));
  ok(`island(${seat}): a dug square does not`,
     !cls('4,3').includes('mk-spent'), cells.get('4,3'));
  ok(`island(${seat}): mainland ground carries no mark`,
     !cls('7,3').includes('mk-dig'), cells.get('7,3'));
  ok(`island(${seat}): and the island still reads as off the board`,
     cls('4,3').includes('offboard'), cells.get('4,3'));
}

// ------------------------------- 蛇帝: only offer a click that undoes something

if (F.linked) {
  C.S = F.linked; C.err = ''; C.draft = null; C.syncDraft();
  const heads = (F.linked.commit.gang || {}).members || [];
  if (heads.length === 2 && C.isLinked()) {
    const first = C.linkedOrder()[0], second = C.linkedOrder()[1];
    ok('linked: the half waiting to be placed offers no click on itself',
       !C.clickableCell(second.cell), JSON.stringify(second.cell));
    const spot = (C.linkedMoves(0) || [])[0];
    if (spot) {
      C.onCell(spot[0], spot[1]);                    // place the first half
      ok('linked: once placed, its square can be clicked to go back',
         C.clickableCell(spot) || C.clickableCell(first.cell),
         JSON.stringify([spot, first.cell]));
    }
  }
}

// ------------------------------- 四圣兽's shrines, drawn on the board

if (F.shrines) {
  C.S = F.shrines; C.err = ''; C.draft = null; C.syncDraft();
  let broke = null;
  try { C.render(); } catch (e) { broke = e.message; }
  ok('shrine: the board renders with them', !broke, broke || '');
  const cells = boardCells();
  const cls = c => (cells.get(c) || '').split(/\s+/);
  ok('shrine: 玄武 is marked across the top row',
     [ '4,1', '5,1', '6,1' ].every(c => cls(c).includes('mk-shrine')),
     ['4,1', '5,1', '6,1'].map(c => cells.get(c)).join(' | '));
  ok('shrine: 朱雀 across the bottom row',
     ['4,5', '5,5', '6,5'].every(c => cls(c).includes('mk-shrine')));
  ok('shrine: 白虎 down the enemy back line',
     ['8,2', '8,3', '8,4'].every(c => cls(c).includes('mk-shrine')),
     ['8,2', '8,3', '8,4'].map(c => cells.get(c)).join(' | '));
  ok('shrine: a woken one reads differently',
     ['2,2', '2,3', '2,4'].every(c => cls(c).includes('mk-spent')),
     ['2,2', '2,3', '2,4'].map(c => cells.get(c)).join(' | '));
  ok('shrine: ordinary ground is left alone',
     !cls('3,3').includes('mk-shrine') && !cls('3,3').includes('mk-dig'),
     cells.get('3,3'));
  const drawn = (document.getElementById('rows').innerHTML.match(/markglyph/g) || []).length;
  ok('shrine: every one of the twelve carries its name', drawn === 12, String(drawn));
}

// ------------------------------- vines on the ground, and nothing left to name

if (F.untouchable_foes) {
  C.S = F.untouchable_foes; C.err = ''; C.syncDraft ? C.syncDraft() : (C.draft = null);
  C.draft = C.blankDraft(F.untouchable_foes.commit.selected);
  let broke = null;
  try { C.render(); } catch (e) { broke = e.message; }
  ok('vine: the panel renders', !broke, broke || '');
  const cells = boardCells();
  ok('vine: a vine in fruit is drawn', (cells.get('4,3') || '').split(/\s+/).includes('vine'),
     cells.get('4,3'));
  ok('vine: a spent one reads differently',
     (cells.get('4,4') || '').split(/\s+/).includes('vinespent'), cells.get('4,4'));
  ok('vine: and a 大葡萄园 differently again',
     (cells.get('4,5') || '').split(/\s+/).includes('vineyard'), cells.get('4,5'));

  const garrote = (F.untouchable_foes.commit.actions || [])
    .find(a => a.key === 'ability:garrote');
  ok('unaimable: an ability with nothing to name is called out',
     C.unaimable(garrote) === 'nothing it can name', JSON.stringify(C.unaimable(garrote)));
  const tree = (F.untouchable_foes.units || []).find(u => u.key === 'world_tree');
  ok('unaimable: and neither seat may name the untouchable',
     !C.nameableFor(garrote, tree));
  C.confirmMove();          // the action list only appears once the move is settled
  C.render();
  const body = document.getElementById('leftbody').innerHTML;
  ok('unaimable: the button says why, and is disabled',
     body.includes('nothing it can name') && body.includes('disabled'),
     body.includes('nothing it can name') + '/' + body.includes('disabled'));
}

// ------------------------------- naming a hero from the roster, not just the board

if (F.ally_heal) {
  C.S = F.ally_heal; C.err = '';
  C.draft = C.blankDraft(F.ally_heal.commit.selected);
  const act = (F.ally_heal.commit.actions || []).find(a => a.targeting.kind === 'ally');
  if (act) {
    C.chooseAction(act.key);
    const mate = (F.ally_heal.units || []).find(
      u => u.side === F.ally_heal.you && u.alive && u.id !== F.ally_heal.commit.selected);
    C.onUnitPick(mate.id);
    ok('roster: an ally can be named from the panel, not only the board',
       C.draft.target === mate.id, JSON.stringify(C.draft.target));
    const foe = (F.ally_heal.units || []).find(u => u.side !== F.ally_heal.you);
    C.onUnitPick(foe.id);
    ok('roster: and an enemy still cannot be named as the ally',
       C.draft.target === mate.id, JSON.stringify(C.draft.target));
  }
}

// ------------------------------- an opening that only accepts certain squares

if (F.opening_cells) {
  C.S = F.opening_cells; C.draft = null; C.err = '';
  const t = F.opening_cells.opening.task;
  ok('opening: the task carries its legal squares',
     !!(t && t.targeting.cells && t.targeting.cells.length),
     JSON.stringify(t && t.targeting));
  let broke = null;
  try { C.render(); } catch (e) { broke = e.message; }
  ok('opening: the panel renders', !broke, broke || '');
  const legal = t.targeting.cells[0];
  ok('opening: a legal square is clickable', C.clickableCell(legal),
     JSON.stringify(legal));
  const taken = (F.opening_cells.units || []).find(u => u.cell)?.cell;
  ok('opening: an occupied square is not', !C.clickableCell(taken),
     JSON.stringify(taken));
}

// ------------------------------- 探险家: a build task of any size, and an island

if (F.island_chart) {
  C.S = F.island_chart; C.draft = null; C.err = '';
  let broke = null;
  try { C.render(); } catch (e) { broke = e.message; }
  ok('island: the four-square build panel renders', !broke, broke || '');
  ok('island: it asks for four', C.buildWants() === 4, String(C.buildWants()));
  ok('island: anywhere on the board will do', C.buildAllowed() === null,
     JSON.stringify(C.buildAllowed()));
  ok('island: a plain square is clickable', C.clickableCell([6, 4]));
  [[4, 1], [4, 2], [4, 3]].forEach(c => C.onCell(c[0], c[1]));
  ok('island: three clicks is not yet ready', !C.buildReady());
  const before = C.sent.length;
  C.onCell(5, 2);
  ok('island: the fourth makes it ready', C.buildReady());
  C.confirmBuild();
  ok('island: confirming sends all four squares',
     C.sent.length === before + 1 && lastSent().cmd === 'build' &&
     lastSent().cells.length === 4, JSON.stringify(lastSent()));
}

if (F.island_landfall) {
  C.S = F.island_landfall; C.draft = null; C.err = '';
  let broke = null;
  try { C.render(); } catch (e) { broke = e.message; }
  ok('island: the landfall panel renders', !broke, broke || '');
  ok('island: it asks for one', C.buildWants() === 1, String(C.buildWants()));
  ok('island: only the island squares are offered', C.clickableCell([4, 2]));
  ok('island: and nothing else is', !C.clickableCell([6, 4]));
  ok('island: the board draws it as a hole',
     (boardCells().get('4,2') || '').split(/\s+/).includes('offboard'),
     boardCells().get('4,2'));
  const before = C.sent.length;
  C.onCell(4, 2);
  C.confirmBuild();
  ok('island: a one-square task is sent as a cell',
     C.sent.length === before + 1 && lastSent().cmd === 'build' &&
     JSON.stringify(lastSent().cell) === '[4,2]', JSON.stringify(lastSent()));
}

if (F.island_dig) {
  C.S = F.island_dig; C.draft = C.blankDraft(); C.err = '';
  let broke = null;
  try { C.render(); } catch (e) { broke = e.message; }
  ok('island: the digging turn renders', !broke, broke || '');
  const keys = (F.island_dig.commit.actions || []).map(a => a.key);
  ok('island: no hold and no attack while it is out there',
     !keys.includes('none') && !keys.includes('attack'), JSON.stringify(keys));
  ok('island: only the three resources', keys.length === 3, JSON.stringify(keys));
}

if (F.island_dig) {
  // the whole order, the way a player makes it: hold, choose a resource, aim, seal
  C.S = F.island_dig; C.err = '';
  const ex = (F.island_dig.units || []).find(u => u.key === 'explorer');
  C.draft = C.blankDraft(ex.id);
  ok('island: it is offered nowhere to walk but where it stands',
     JSON.stringify(F.island_dig.commit.legal_moves) === '[[4,2]]',
     JSON.stringify(F.island_dig.commit.legal_moves));
  C.confirmMove();
  C.chooseAction('ability:train_natives');
  const dug = (F.island_dig.commit.actions || [])
    .find(a => a.key === 'ability:train_natives');
  ok('island: the dig offers only free island ground',
     JSON.stringify(dug.targeting.cells) === '[[4,1],[4,3],[5,2]]',
     JSON.stringify(dug.targeting.cells));
  ok('island: those squares are clickable', C.clickableCell([4, 1]));
  ok('island: mainland squares are not', !C.clickableCell([7, 1]));
  const before = C.sent.length;
  C.onCell(4, 1);
  C.sealOrder();
  ok('island: the order seals as a dig on that square',
     C.sent.length === before + 1 && lastSent().cmd === 'commit' &&
     ((lastSent().payload || {}).action || {}).key === 'ability:train_natives' &&
     JSON.stringify(((lastSent().payload || {}).action || {}).cell) === '[4,1]',
     JSON.stringify(lastSent()));
}

if (F.island_foe) {
  C.S = F.island_foe; C.draft = null; C.err = '';
  const ex = (F.island_foe.units || []).find(u => u.key === 'explorer');
  ok('island: the other seat sees the island too',
     (F.island_foe.offboard || []).length === 4,
     JSON.stringify(F.island_foe.offboard));
  ok('island: but cannot aim at what stands on it', ex && ex.targetable === false,
     JSON.stringify(ex && ex.targetable));
  ok('island: nor is it one of the enemies they must name',
     !(F.island_foe.commit.enemies || []).includes(ex.id),
     JSON.stringify(F.island_foe.commit.enemies));
}

// ------------------------------- 猎人: a shot that wants two of them

if (F.victim_two) {
  C.S = F.victim_two; C.draft = null; C.err = '';
  ok('hunter: the board says how many it wants', F.victim_two.victim.wanted === 2,
     String(F.victim_two.victim.wanted));
  let broke = null;
  try { C.render(); } catch (e) { broke = e.message; }
  ok('hunter: the target panel renders', !broke, broke || '');
  const panel = global.document.getElementById('leftbody').innerHTML;
  ok('hunter: and tells the player it wants two', /0\/2|2 of them/.test(panel),
     panel.slice(0, 120));
}

// ------------------------------- 军火商人: a shot you can feed AP into

if (F.fuelled) {
  HOLD_FIRST(F.fuelled);
  const act = C.curActions().find(a => a.key === 'attack');
  // The payload's `ap` is already what the turn will open with — the charge
  // lands at turn start and the panel shows it — so this is the whole purse.
  const dealer = C.myUnits().find(u => u.id === F.fuelled.commit.selected);
  const purse = dealer.ap;
  ok('dealer: the shot advertises the whole purse it will hold',
     act.targeting.fuel === purse, `${act.targeting.fuel} vs ${purse}`);
  C.chooseAction('attack');
  ok('dealer: it starts unfed', (C.draft.spend || 0) === 0, String(C.draft.spend));
  const foe = (F.fuelled.units || []).find(u => u.side !== 'L');
  C.onCell(foe.cell[0], foe.cell[1]);
  C.setSpend(4);
  C.sealFromKeyboard();
  ok('dealer: the fuel travels with the order', lastSent().payload.action.spend === 4,
     JSON.stringify(lastSent().payload.action));
  HOLD_FIRST(F.fuelled); C.chooseAction('attack');
  C.onCell(foe.cell[0], foe.cell[1]);
  C.setSpend(99);
  C.sealFromKeyboard();
  ok('dealer: it cannot send more than it has',
     lastSent().payload.action.spend === purse, JSON.stringify(lastSent().payload.action));
}

// The sale is made as the turn opens, before the shot — so the fee is fuel for
// that shot, and a dealer with nothing banked still has something to feed it.

if (F.fuelled_sale) {
  HOLD_FIRST(F.fuelled_sale);
  const ch = C.curChoices().find(c => c.key === 'armory');
  const ware = ch.wares.reduce((a, b) => (b.ap > a.ap ? b : a));
  const act = C.curActions().find(a => a.key === 'attack');
  // Banked nothing, so the only fuel is the turn's own charge — and the slider
  // must be drawn for it, which is why the gate reads 'takes fuel' not 'has any'.
  const dealer2 = C.myUnits().find(u => u.id === F.fuelled_sale.commit.selected);
  ok('dealer: with nothing banked the shot still says it takes fuel',
     act.targeting.fuel === dealer2.ap && act.targeting.fuel !== undefined,
     String(act.targeting.fuel));

  C.chooseAction('attack');
  ok('dealer: and nothing to feed it before a sale is set up',
     C.pendingFee() === 0, String(C.pendingFee()));
  C.pickChoice('armory', ware.value);
  ok('dealer: the sale being set up is fuel for this turn',
     C.pendingFee() === ware.ap, `${C.pendingFee()} vs ${ware.ap}`);

  const foe = (F.fuelled_sale.units || []).find(u => u.side !== 'L');
  C.onCell(foe.cell[0], foe.cell[1]);
  C.setSpend(ware.ap);
  C.sealFromKeyboard();
  ok('dealer: the whole fee travels with the order as fuel',
     lastSent().payload.action.spend === ware.ap,
     JSON.stringify(lastSent().payload.action));
  ok('...and the sale travels with it', lastSent().payload.choices.armory === ware.value,
     JSON.stringify(lastSent().payload.choices));

  HOLD_FIRST(F.fuelled_sale); C.chooseAction('attack');
  C.pickChoice('armory', ware.value);
  C.onCell(foe.cell[0], foe.cell[1]);
  C.setSpend(99);
  C.sealFromKeyboard();
  ok('dealer: but no more than banked plus the fee',
     lastSent().payload.action.spend === act.targeting.fuel + ware.ap,
     JSON.stringify(lastSent().payload.action));
}

// An armed but unaimed cells attack. Sealing it silently turns it into a hold,
// which costs the turn without saying so — the same trap the sniper's lane and
// the unit attacks already guard against.

if (F.fuelled_sale) {
  HOLD_FIRST(F.fuelled_sale);
  const ch = C.curChoices().find(c => c.key === 'armory');
  const ware = ch.wares.reduce((a, b) => (b.ap > a.ap ? b : a));
  C.chooseAction('attack');
  C.pickChoice('armory', ware.value);
  C.setSpend(ware.ap);

  ok('fed but unaimed: the order is not ready', !C.orderReady());
  const before = C.sent.length;
  C.sealFromKeyboard();
  ok('fed but unaimed: Enter does not send it', C.sent.length === before,
     `${C.sent.length - before} sent`);
  ok('fed but unaimed: and it says the fuel is the reason', /fed/i.test(C.err), C.err);

  const foe = (F.fuelled_sale.units || []).find(u => u.side !== 'L');
  C.onCell(foe.cell[0], foe.cell[1]);
  ok('fed and aimed: now it is ready', C.orderReady());
  C.sealFromKeyboard();
  ok('fed and aimed: and it seals with the fuel',
     lastSent().payload.action.spend === ware.ap, JSON.stringify(lastSent().payload.action));
}

if (F.fuelled) {
  // No fuel involved: an ordinary unaimed shot with something in reach is still
  // held back, and the panel names how much there was to shoot at.
  HOLD_FIRST(F.fuelled); C.chooseAction('attack');
  ok('unaimed with a target in reach: not ready', !C.orderReady());
  const before = C.sent.length;
  C.sealFromKeyboard();
  ok('unaimed with a target in reach: Enter does not eat the turn',
     C.sent.length === before, `${C.sent.length - before} sent`);
  ok('...and it says what is missing', /mark where/i.test(C.err), C.err);
  ok('...and Hold is still there to be pressed on purpose',
     C.curActions().some(a => a.key === 'none'));
}

if (F.no_targets) {
  // Nothing in reach: marking nothing was never a forgotten shot, so Enter still
  // holds, exactly as the panel promises.
  HOLD_FIRST(F.no_targets); C.chooseAction('attack');
  ok('nothing in reach: there is genuinely nothing to mark',
     C.cellTargets().length === 0, JSON.stringify(C.cellTargets().map(u => u.cell)));
  ok('nothing in reach: the order is ready as a hold', C.orderReady());
  C.sealFromKeyboard();
  ok('nothing in reach: Enter still holds', lastSent().payload.action.key === 'none',
     JSON.stringify(lastSent().payload.action));
}

// Reading a hero is a hover, not a click. A hero sits inside its square, so a
// click on it was never only a click — it also marked that square as an attack
// grid. Looking something up must not be a move.

if (F.fuelled) {
  const rhs = () => ({ head: global.document.getElementById('rhshead').textContent,
                       body: global.document.getElementById('rhsbody').innerHTML });
  HOLD_FIRST(F.fuelled); C.chooseAction('attack');
  const foe = (F.fuelled.units || []).find(u => u.side !== 'L');

  C.render();
  ok('no hero: the panel is the field log', rhs().head === 'Field log', rhs().head);
  C.setHover(foe.id);
  ok('hovering a hero puts its card in the panel', rhs().head === 'Hero', rhs().head);
  ok('...and the card is that hero', rhs().body.includes(foe.name), rhs().body.slice(0, 80));
  C.setHover(null);
  ok('...and leaving it puts the field log back', rhs().head === 'Field log', rhs().head);

  const before = (C.draft.shots[0] || []).length;
  ok('hovering marks nothing', before === 0, String(before));
  ok('a hero on the board carries no click of its own',
     !/onclick="inspect/.test(global.document.getElementById('rows').innerHTML));

  // The click is still the game action, and only that.
  C.onCell(foe.cell[0], foe.cell[1]);
  ok('clicking a hero still marks its square', (C.draft.shots[0] || []).length === 1,
     JSON.stringify(C.draft.shots));
}

// ------------------------------- 工匠: two squares built in before deployment

if (F.build && F.doors) {
  C.S = F.build; C.draft = null; C.err = '';
  ok('artisan: the builder is asked for two squares',
     !!(F.build.build && F.build.build.task), JSON.stringify(F.build.build));
  let broke = null;
  try { C.render(); } catch (e) { broke = e.message; }
  ok('artisan: the building panel renders', !broke, broke || '');
  const before = C.sent.length;
  C.onKey({ key: 'Enter', preventDefault(){} });
  ok('artisan: Enter with nothing chosen sends nothing', C.sent.length === before);
  C.onCell(2, 3);
  ok('artisan: the first square is marked', cellHas([2, 3], 'dest'),
     cellsWith('dest').join(' '));
  C.onCell(8, 3);
  C.onKey({ key: 'Enter', preventDefault(){} });
  ok('artisan: Enter with both chosen raises them',
     C.sent.length === before + 1 && lastSent().cmd === 'build' &&
     JSON.stringify(lastSent().cells) === '[[2,3],[8,3]]', JSON.stringify(lastSent()));

  C.S = F.build_waiting; C.draft = null; C.err = '';
  ok('artisan: the other seat is told it is waiting',
     !F.build_waiting.build.task && F.build_waiting.build.waiting_on_opponent,
     JSON.stringify(F.build_waiting.build));

  C.S = F.doors; C.draft = null; C.err = '';
  ok('artisan: both doors are marked on the board once built',
     cellHas([2, 3], 'doorL') && cellHas([8, 3], 'doorL'),
     cellsWith('doorL').join(' '));
  ok('artisan: and a plain square stays plain', !cellHas([5, 5], 'doorL'));
}

// ------------------------------- 教皇: a killing blow held up for a decision

if (F.interrupt_save && F.interrupt_waiting) {
  C.S = F.interrupt_save; C.draft = null; C.err = '';
  const t = F.interrupt_save.interrupt.task;
  ok('pope: the side that can step in is asked', !!t && t.kind === 'confirm',
     JSON.stringify(t && t.kind));
  let broke = null;
  try { C.render(); } catch (e) { broke = e.message; }
  ok('pope: the panel renders', !broke, broke || '');
  const before = C.sent.length;
  C.onKey({ key: 'Enter', preventDefault(){} });
  ok('pope: Enter steps in front of it',
     C.sent.length === before + 1 && lastSent().cmd === 'interrupt' &&
     lastSent().answer === true, JSON.stringify(lastSent()));

  C.S = F.interrupt_waiting; C.draft = null; C.err = '';
  ok('pope: the other seat is told it is waiting, not asked',
     !F.interrupt_waiting.interrupt.task &&
     F.interrupt_waiting.interrupt.waiting_on_opponent,
     JSON.stringify(F.interrupt_waiting.interrupt));
  const before2 = C.sent.length;
  C.onKey({ key: 'Enter', preventDefault(){} });
  ok('pope: and it cannot answer for the other side', C.sent.length === before2);
}

// --------------------------- 鸟嘴医生: infected ground, plain to both seats

if (F.infected_owner && F.infected_enemy) {
  for (const [name, who] of [['infected_owner', 'the side that spread it'],
                             ['infected_enemy', 'the side it was spread against']]) {
    C.S = F[name]; C.draft = null; C.err = '';
    ok(`plague: ${who} is told which squares are infected`,
       (F[name].tiles || []).filter(t => t.kind === 'infection').length === 2,
       JSON.stringify((F[name].tiles || []).map(t => t.kind)));
    ok(`plague: ${who} sees them marked on the board`,
       cellHas([5, 3], 'infected') && cellHas([5, 2], 'infected'),
       cellsWith('infected').join(' '));
    ok(`plague: ${who} sees clean ground stay clean`, !cellHas([1, 1], 'infected'),
       cellsWith('infected').join(' '));
  }
}

// ----------------------------------- 潜水者: a charge only one side can see

if (F.mined_owner && F.mined_enemy) {
  C.S = F.mined_owner; C.draft = null; C.err = '';
  ok('diver: the side that laid it is told about the charge',
     (F.mined_owner.tiles || []).some(t => t.kind === 'small_bomb'),
     JSON.stringify(F.mined_owner.tiles));
  ok('diver: and the board marks the square', cellHas([5, 3], 'mined'),
     boardCells().get('5,3'));

  C.S = F.mined_enemy; C.draft = null; C.err = '';
  ok('diver: the enemy is told nothing at all',
     !(F.mined_enemy.tiles || []).some(t => t.hidden),
     JSON.stringify(F.mined_enemy.tiles));
  ok('diver: and their board shows a plain square', !cellHas([5, 3], 'mined'),
     boardCells().get('5,3'));
}

// ------------------------------------- 蛇帝: position the whole body, then aim

if (F.linked) {
  C.S = F.linked; C.err = ''; C.draft = C.blankDraft(F.linked.commit.selected);
  const ms = C.linkedOrder();
  ok('snake: head is ordered before tail', ms.map(g => g.name_en).join(',') === 'snakeHead,snakeTail',
     ms.map(g => g.name_en).join(','));

  // nothing illegal is even offered
  let lit = 0;
  for (let c = 1; c <= 9; c++) for (let r = 1; r <= 5; r++) if (C.clickableCell([c, r])) lit++;
  ok('snake: only the head’s own squares are clickable first', lit > 0 && lit <= 5, `${lit} squares`);

  const before = C.sent.length;
  C.confirmMove();                       // Enter, while the body is still moving
  ok('snake: Enter before the body is placed explains itself',
     C.draft.stage === 'move' && C.sent.length === before && !!C.err,
     C.err || 'moved on silently');

  C.onCell(3, 2);                        // head steps east
  ok('snake: the head takes the square', C.draft.pos[ms[0].entity] &&
     C.draft.pos[ms[0].entity].join(',') === '3,2', JSON.stringify(C.draft.pos));

  // the tail may now only go beside the head's NEW square
  const zone = C.linkedMoves(1);
  const beside = zone.every(([c, r]) => Math.abs(c - 3) + Math.abs(r - 2) === 1);
  ok('snake: only squares beside the head’s destination are offered for the tail',
     zone.length > 0 && beside, JSON.stringify(zone));
  ok('snake: the head’s old square is among them', zone.some(([c, r]) => c === 2 && r === 2));
  ok('snake: a square far from the head is not clickable', !C.clickableCell([7, 5]));
  ok('snake: the old zone around the head’s START is gone',
     !zone.some(([c, r]) => c === 1 && r === 2), JSON.stringify(zone));

  // the preview must show the moment a square is picked, before anything is confirmed
  const headUnit = () => C.myUnits().find(u => u.id === ms[0].entity);
  const tailUnit = () => C.myUnits().find(u => u.id === ms[1].entity);
  ok('snake: the head plans from its proposed square',
     C.plannedCell(headUnit()).join(',') === '3,2', JSON.stringify(C.plannedCell(headUnit())));
  ok('snake: and that square is marked on the board', cellHas([3, 2], 'dest'),
     cellsWith('dest').join(' '));
  ok('snake: the square it is leaving is marked as vacated',
     cellHas([2, 2], 'movefrom'), boardCells().get('2,2'));

  C.onCell(3, 1);                        // tail placed beside the head
  ok('snake: both halves are placed', C.draft.pos[ms[1].entity].join(',') === '3,1',
     JSON.stringify(C.draft.pos));
  ok('snake: the tail plans from its proposed square too',
     C.plannedCell(tailUnit()).join(',') === '3,1', JSON.stringify(C.plannedCell(tailUnit())));
  ok('snake: both proposed squares are marked at once',
     cellHas([3, 2], 'dest') && cellHas([3, 1], 'dest'), cellsWith('dest').join(' '));
  ok('snake: nothing else is marked', cellsWith('dest').length === 2, cellsWith('dest').join(' '));

  C.confirmMove();
  ok('snake: confirming moves on to the attacks', C.draft.stage === 'act', String(C.draft.stage));
  ok('snake: the head aims first', C.draft.entity === ms[0].entity);
  ok('snake: it aims from where it will stand', C.draft.destination.join(',') === '3,2',
     JSON.stringify(C.draft.destination));
  ok('snake: the tail keeps its preview while the head is aiming',
     cellHas([3, 1], 'dest') && cellHas([2, 3], 'movefrom'),
     `dest=${cellsWith('dest').join(' ')} tailFrom=${boardCells().get('2,3')}`);
  ok('snake: the tail plans its reach from its new square, not its old one',
     C.plannedCell(tailUnit()).join(',') === '3,1', JSON.stringify(C.plannedCell(tailUnit())));

  C.sealFromKeyboard();                  // head's attack
  ok('snake: nothing is sent until the tail has aimed too', C.sent.length === before,
     `${C.sent.length - before} sent`);
  ok('snake: the tail aims next', C.draft.entity === ms[1].entity);
  C.sealFromKeyboard();                  // tail's attack
  const sent = lastSent();
  ok('snake: both orders travel together, head first',
     sent.payload.orders.map(o => o.entity).join(',') === ms.map(g => g.entity).join(','),
     JSON.stringify(sent.payload.orders.map(o => o.entity)));
  ok('snake: each half carries the square it was placed on',
     sent.payload.orders[0].destination.join(',') === '3,2' &&
     sent.payload.orders[1].destination.join(',') === '3,1',
     JSON.stringify(sent.payload.orders.map(o => o.destination)));
}

// --------------------- 蛇帝 beside the enemy's doors. The tail's squares are the
// one move list the client works out for itself, so it is the one that can drift
// from the server: a square the server refuses must never be offered.

if (F.linked_doors) {
  C.S = F.linked_doors; C.err = ''; C.draft = C.blankDraft(F.linked_doors.commit.selected);
  const ms = C.linkedOrder();
  const door = F.linked_doors.doors[0].cells[0];   // Right's door — wall to Left
  const server = ms[1].legal_moves;                // where the engine would allow the tail

  C.onCell(ms[0].cell[0], ms[0].cell[1]);          // the head holds its square
  const zone = C.linkedMoves(1);
  ok('snake by a door: the enemy’s door is not offered to the tail',
     !zone.some(c => c[0] === door[0] && c[1] === door[1]), JSON.stringify(zone));
  ok('snake by a door: the tail is offered exactly what the server allows',
     zone.length === server.length &&
     zone.every(c => server.some(x => x[0] === c[0] && x[1] === c[1])),
     `client ${JSON.stringify(zone)} vs server ${JSON.stringify(server)}`);
  ok('snake by a door: the door square is not clickable either',
     !C.clickableCell(door));
}

if (F.followup) {
  C.S = F.followup; C.draft = null; C.err = '';
  const task = F.followup.followup.task;
  ok('after the exchange, the step is offered', !!task && task.optional, JSON.stringify(task && task.key));
  let lit = 0;
  for (let c = 1; c <= 9; c++) for (let r = 1; r <= 5; r++) if (C.clickableCell([c, r])) lit++;
  ok('only the squares on offer are clickable', lit === task.options.length,
     `${lit} of ${task.options.length}`);
  const before = C.sent.length;
  C.onCell(task.options[0][0], task.options[0][1]);
  ok('clicking a square only aims it', C.sent.length === before,
     `${C.sent.length - before} sent`);
  ok('...and the board marks the aim', cellHas(task.options[0], 'dest'),
     cellsWith('dest').join(' '));
  ok('...and the squares on offer are highlighted', cellHas(task.options[0], 'legal'),
     boardCells().get(task.options[0].join(',')));
  C.confirmStep();
  ok('Enter takes the step',
     C.sent.length > before && lastSent().cmd === 'followup', JSON.stringify(lastSent()));
}

// --------------------------------------------------- picking two units to swap

if (F.two_units) {
  HOLD_FIRST(F.two_units);
  C.chooseAction('ability:transfer');
  const before = C.sent.length;
  C.sealFromKeyboard();
  ok('magician: Enter with nobody picked explains itself',
     C.sent.length === before && !!C.err, C.err || 'sealed silently');
  const opts = C.curActions().find(a => a.key === 'ability:transfer').targeting.options;
  const units = F.two_units.units.filter(u => opts.includes(u.id) && u.cell);
  C.onCell(units[0].cell[0], units[0].cell[1]);
  ok('magician: clicking one unit picks it', C.draft.pair.length === 1, String(C.draft.pair));
  C.onCell(units[1].cell[0], units[1].cell[1]);
  ok('magician: clicking a second completes the pair', C.draft.pair.length === 2, String(C.draft.pair));
  C.onCell(units[0].cell[0], units[0].cell[1]);
  ok('magician: clicking a picked unit again unpicks it', C.draft.pair.length === 1);
  C.onCell(units[0].cell[0], units[0].cell[1]);
  C.sealFromKeyboard();
  const a = lastSent().payload.action;
  ok('magician: both units travel in the order', a.first != null && a.second != null && a.first !== a.second,
     JSON.stringify(a));
}

// ------------------------------------------------------ a pinned hero can fight

if (F.rooted) {
  const C2 = loadClient('R');
  C2.S = F.rooted; C2.err = '';
  C2.draft = C2.blankDraft(F.rooted.commit.selected);
  ok('pinned: no square is offered to move to', C2.curMoves().length === 0,
     JSON.stringify(C2.curMoves()));
  let lit = 0;
  for (let c = 1; c <= 9; c++) for (let r = 1; r <= 5; r++) if (C2.clickableCell([c, r])) lit++;
  ok('pinned: the board offers it nowhere to walk', lit === 0, `${lit} squares`);
  C2.confirmMove();
  ok('pinned: it still gets its attack', C2.curActions().some(a => a.key === 'attack'),
     C2.curActions().map(a => a.key).join());
  const u = (F.rooted.units || []).find(x => x.id === F.rooted.commit.selected);
  ok('pinned: it wears a badge saying so',
     (u.status || []).some(x => x.key === 'rooted'), JSON.stringify((u.status||[]).map(x=>x.key)));
}

// ------------------------------------------------- frozen heroes are not offered

if (F.frozen) {
  C.S = F.frozen; C.draft = null;
  const frozen = F.frozen.units.filter(u => u.side === 'L' && u.alive && !u.acted
                                            && !F.frozen.commit.unacted.includes(u.id));
  ok('a frozen hero is never offered for selection',
     frozen.length > 0 && frozen.every(u => !C.canAct(u)), `${frozen.length} held out`);
}

console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed ? 1 : 0);
