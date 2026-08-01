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
      sweepPreview, linkedOrder, linkedMoves, isLinked, confirmMove };`;
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
  let lit = 0;
  for (let c = 1; c <= 9; c++) for (let r = 1; r <= 5; r++) if (C.clickableCell([c, r])) lit++;
  ok('mammoth: the squares it sweeps are highlighted', lit === 8, `${lit} squares`);
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
  ok('clicking one takes the step',
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
