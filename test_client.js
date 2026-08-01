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
  global.document = {
    getElementById: stub, querySelector: () => null, querySelectorAll: () => [],
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
      sweepPreview };`;
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
  ['unit_locked', null], ['line_cut', 'ability:gale_slash'],
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

// ------------------------------------------------------- the row-or-column cut

if (F.line_cut) {
  HOLD_FIRST(F.line_cut);
  C.chooseAction('ability:gale_slash');
  ok('swordsman: neither line is aimed until one is picked', C.draft.direction == null,
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
