/* Warchess client. Served at /app.js; index.html is the shell around it.
 * Kept as its own file so it can be syntax-checked and driven headlessly —
 * see test_client.js. */
const SIDE = (new URLSearchParams(location.search).get('side') || 'L').toUpperCase().startsWith('R') ? 'R' : 'L';
const DIRS = {forward:'Forward — into the enemy', backward:'Backward — toward your line',
              up:'Up — toward row 1', down:'Down — toward row 5'};
let S = null, armed = null, draft = null, err = "", lastVersion = -1, pendingMoves = [];
// The square aimed at for a square-targeted opening, before Enter confirms it.
let openingPick = null;
// The square aimed at for a mid-resolution cell choice — 男枪's step after a hit,
// 刺客 picking which side of its mark to appear on, 潜水者 burying a charge. Both
// phases work the same way: click a highlighted square, then Enter.
let stepPick = null;
function stepTask(){
  if (!S) return null;
  if (S.phase==='resolved') return (S.followup && S.followup.task) || null;
  if (S.phase==='move_choice') return (S.move_choice && S.move_choice.task) || null;
  return null;
}
// Self-correcting: an aim left over from a task that has moved on is simply not one.
function aimedStep(){
  const t = stepTask();
  return (t && stepPick && has(t.options, stepPick)) ? stepPick : null;
}
function confirmStep(){
  const cell = aimedStep(); if (!cell) return;
  const which = S.phase==='move_choice' ? 'move_choice' : 'followup';
  stepPick = null;
  cmd({cmd: which, cell});
}
function confirmOpening(){
  if (!openingPick) return;
  const cell = openingPick, t = S.opening && S.opening.task;
  openingPick = null;
  if (t && t.targeting.kind==='unit'){
    const u = unitAt(cell);
    if (u) cmd({cmd:'opening', target:u.id});
    return;
  }
  cmd({cmd:'opening', cell});
}
let hoverId = null, inspected = null, revealActive = false, shownReveal = "";
let codex = {};   // all hero cards, fetched once from /api/codex (not per poll)

// Squares have no names a player can read off the board, so anything that has to
// tell them apart says where it is relative to a fixed point instead.
const dirWord = (from, to) => {
  const dc = to[0]-from[0], dr = to[1]-from[1];
  if (Math.abs(dc) >= Math.abs(dr)) return dc > 0 ? 'to the right' : 'to the left';
  return dr > 0 ? 'below' : 'above';
};
const eq = (a,b) => a && b && a[0]===b[0] && a[1]===b[1];
const has = (arr,c) => (arr||[]).some(x => eq(x,c));

async function api(path, body){
  const r = await fetch(path, {method:'POST', headers:{'Content-Type':'application/json', 'ngrok-skip-browser-warning':'1'},
                              body: JSON.stringify(Object.assign({side:SIDE}, body||{}))});
  return r.json();
}
async function cmd(body){
  const res = await api('/api/cmd', body);
  err = res.error || "";
  await poll(true);
}
async function resetMatch(){ await api('/api/reset'); draft=null; armed=null; pendingMoves=[]; inspected=null; hoverId=null; revealActive=false; shownReveal=""; err=""; await poll(true); }

async function loadCodex(){
  try {
    const r = await fetch('/api/codex', {headers:{'ngrok-skip-browser-warning':'1'}});
    codex = (await r.json()).codex || {};
  } catch (e) {}
}
async function poll(force){
  if (document.hidden && !force) return;   // don't poll a backgrounded tab
  const r = await fetch('/api/state?side='+SIDE, {cache:'no-store', headers:{'ngrok-skip-browser-warning':'1'}});
  const next = await r.json();
  // Presence isn't part of the version counter, so compare it explicitly —
  // otherwise a seat stays on "waiting" when the opponent joins.
  const changed = !S || next.version !== lastVersion || next.both_present !== S.both_present;
  S = next; lastVersion = next.version;
  // Drop the pending-move ghost once its order is no longer the live selection
  // (i.e. the exchange resolved and the board updated to the real position).
  if (pendingMoves.length && (S.phase!=='commit' || !S.commit || !S.commit.sealed)) pendingMoves = [];
  // A fresh, fully-resolved exchange: flash the reveal overlay (both heroes +
  // what they did) for a beat so the resolution is readable.
  if (S.reveal && (S.phase==='commit' || S.phase==='gameover')){
    const key = JSON.stringify(S.reveal);
    if (key !== shownReveal){
      shownReveal = key; revealActive = true;
      clearTimeout(window._revealT);
      window._revealT = setTimeout(() => { revealActive = false; render(); }, 3600);
    }
  }
  if (changed || force) { syncDraft(); render(); }
}

function syncDraft(){
  if (S.phase !== 'commit') { draft = null; return; }
  const sel = S.commit && S.commit.selected;
  if (!sel || (S.commit && S.commit.sealed)) { draft = null; return; }
  if (!draft) { draft = blankDraft(sel); return; }
  // A gang keeps its own idea of which goblin is being ordered right now — the
  // server's `selected` only records which one opened the gang's turn.
  if (isGang()){
    draft.gangOrders = draft.gangOrders || [];
    draft.pos = draft.pos || {};
    draft.posIdx = draft.posIdx || 0;
    draft.stage = draft.stage || 'move';
    // Switching away and back leaves a stale id behind — rebuild rather than
    // render a panel for a hero that isn't in this gang.
    if (draft.entity != null && !gangMember(draft.entity)) draft = blankDraft(sel);
    return;
  }
  if (draft.entity !== sel) draft = blankDraft(sel);
}
function blankDraft(entity){
  return {entity, destination:null, held:false, tentative:null, actionKey:null, shots:[], shotIndex:0,
          target:null, direction:null, cell:null, amount:null, weapon:null, pair:[],
          choices:{}, gangOrders:[], pos:{}, posIdx:0, stage:'move'};
}
function resetLeg(){
  // Clear one unit's half-built order, keeping the gang's sealed-so-far list.
  Object.assign(draft, {destination:null, held:false, tentative:null, actionKey:null, shots:[],
                        shotIndex:0, target:null, direction:null, cell:null, amount:null, weapon:null,
                        pair:[], choices:{}});
}

/* ---------------- goblin gang ---------------- */
function gangBlock(){ return (S && S.commit && S.commit.gang) || null; }
function isGang(){ return !!gangBlock(); }
function gangMembers(){ return gangBlock() ? gangBlock().members : []; }
function gangMember(id){ return gangMembers().find(x => x.entity === id) || null; }
function gangOrders(){ return (draft && draft.gangOrders) || []; }
function isOrdered(id){ return gangOrders().some(o => o.entity === id); }
function gangPending(){ return gangMembers().filter(x => !isOrdered(x.entity)); }
/* ---------------- linked bodies (蛇帝) ---------------- */
// A squad whose halves are placed against one another rather than walking about
// independently. The whole body is positioned first and confirmed in one go, and
// only then does each half aim — so an illegal position is never offered at all.
// 哥布林团伙 has no such link (three units that merely share a turn) and keeps the
// per-goblin move-then-attack flow untouched.
function isLinked(){ return isGang() && gangMembers().some(g => g.move_anchor); }
function linkedOrder(){
  return gangMembers().slice().sort(
    (a,b) => ((a.rank==null)?99:a.rank) - ((b.rank==null)?99:b.rank));
}
function linkedAt(i){ return linkedOrder()[i] || null; }
function placedPos(){ return (draft && draft.pos) || {}; }
function placedAll(){ return linkedOrder().every(g => placedPos()[g.entity]); }
// Where the i-th body may be put, given where the earlier ones are going. Both
// halves are moving at once, so neither blocks the other's old square.
function linkedMoves(i){
  const ms = linkedOrder(), g = ms[i];
  if (!g) return [];
  const own = ms.map(x => x.cell).filter(Boolean);
  const taken = ms.filter((x,j) => j!==i && placedPos()[x.entity]).map(x => placedPos()[x.entity]);
  const open = c => {
    if (c[0]<1 || c[1]<1 || c[0]>S.board.cols || c[1]>S.board.rows) return false;
    if (has(taken, c)) return false;
    const u = unitAt(c);
    return !u || has(own, c);
  };
  if (g.move_anchor){
    const a = placedPos()[g.move_anchor.entity] || (gangMember(g.move_anchor.entity)||{}).cell;
    if (!a) return [];
    return [[a[0]+1,a[1]],[a[0]-1,a[1]],[a[0],a[1]+1],[a[0],a[1]-1]].filter(open);
  }
  // A body that walks: the server's own list (holding is always allowed).
  return (g.legal_moves||[]).concat(g.cell?[g.cell]:[]).filter(open);
}
function beginLinkedLeg(){
  const g = linkedAt(draft.posIdx);
  if (!g) return render();
  resetLeg();
  draft.entity = g.entity;
  draft.destination = placedPos()[g.entity];
  err=""; finishMove();
}
function resetLinked(){
  draft.pos = {}; draft.posIdx = 0; draft.stage = 'move'; draft.gangOrders = [];
  draft.entity = null; resetLeg(); err=""; render();
}

// The move list / action menu for whoever is being ordered right now.
function curMoves(){
  if (isLinked()) return draft.stage==='act' ? [] : linkedMoves(draft.posIdx||0);
  if (isGang()){
    const g = gangMember(draft && draft.entity);
    return g ? g.legal_moves : [];
  }
  return (S.commit && S.commit.legal_moves) || [];
}
function curActions(){
  if (isGang()){
    const g = gangMember(draft && draft.entity);
    return g ? g.actions : [];
  }
  return (S.commit && S.commit.actions) || [];
}
// Free picks that ride along with this unit's turn (杂货店爷爷's handout).
function curChoices(){
  if (isGang()){
    const g = gangMember(draft && draft.entity);
    return (g && g.choices) || [];
  }
  return (S.commit && S.commit.choices) || [];
}
function choicesReady(){ return curChoices().every(ch => (draft.choices||{})[ch.key] != null); }
function pickChoice(key, id){ draft.choices = draft.choices || {}; draft.choices[key] = id; err=""; render(); }

// A unit-locked attack names one hero, or several once something has widened it
// (四圣兽 with 白虎). `draft.target` stays the single-target case so nothing else
// had to change.
function unitCount(){
  const act = currentAction();
  return Math.max(1, (act && act.targeting && act.targeting.count) || 1);
}
function namedUnits(){
  if (!draft) return [];
  if (unitCount() > 1) return draft.named || [];
  return draft.target == null ? [] : [draft.target];
}
function nameUnit(id){
  if (unitCount() === 1){
    draft.target = (draft.target === id) ? null : id;   // click again to unpick
    return;
  }
  draft.named = draft.named || [];
  const at = draft.named.indexOf(id);
  if (at >= 0) draft.named.splice(at, 1);
  else if (draft.named.length < unitCount()) draft.named.push(id);
}

function unitAt(cell){ return (S.units||[]).find(u => u.alive && u.cell && eq(u.cell, cell)); }
// Whose turn can actually be taken. The engine already excludes heroes that are
// frozen (咒毒) from commit.unacted, so trust that list rather than re-deriving
// it from alive/acted — otherwise we offer picks the server will refuse.
function canAct(u){
  if (!u || !u.alive || u.acted || u.side !== SIDE) return false;
  const un = S.commit && S.commit.unacted;
  return un ? un.includes(u.id) : true;
}
function myUnits(){ return (S.units||[]).filter(u => u.side === SIDE); }
function foeUnits(){ return (S.units||[]).filter(u => u.side !== SIDE); }
function selectedUnit(){
  const id = (draft && draft.entity) || (S.commit && S.commit.selected);
  return id ? (S.units||[]).find(u=>u.id===id) : null;
}
function currentAction(){
  if (!draft || !draft.actionKey || !S.commit) return null;
  return curActions().find(a => a.key === draft.actionKey);
}
// Where a unit will be standing once this turn's movement resolves: its own
// aim if it is the one being ordered, its already-recorded order if it is a
// goblin further up the gang's queue, otherwise where it stands now. Keyed on
// draft.entity, never on S.commit.selected — for a gang those differ, and using
// `selected` drew one goblin's reach around another goblin's destination.
function plannedCell(u){
  if (!u) return null;
  if (S.phase!=='commit' || !draft) return u.cell;
  // A linked body is placed before either half aims, so both know where they are
  // going from the moment they are put down — including the one whose turn to aim
  // has not come round yet.
  if (isLinked() && placedPos()[u.id]) return placedPos()[u.id];
  // u.cell may be null (鬼魂 before it takes flesh) — the aim still counts.
  if (draft.entity === u.id) return draft.destination || draft.tentative || u.cell;
  const o = gangOrders().find(x => x.entity === u.id);
  return (o && o.destination) || u.cell;
}
function originCell(){
  const u = selectedUnit();
  return u ? plannedCell(u) : null;
}
function currentWeapon(){
  const act = currentAction();
  if (!act || act.targeting.kind!=='weapon' || !draft.weapon) return null;
  return (act.weapons||[]).find(w => w.key===draft.weapon);
}
// Effective cell-marking spec — from a plain cells attack, or a cells-mode weapon.
function cellSpec(){
  const act = currentAction(); if (!act) return null;
  const t = act.targeting;
  if (t.kind==='cells') return {count:t.count, range:t.range, shots:t.shots};
  const w = currentWeapon();
  if (w && w.mode==='cells') return {count:w.cells, range:w.range, shots:1};
  return null;
}

/* ---------------- board ---------------- */
function renderBoard(){
  const cols = S.board.cols, rows = S.board.rows;
  const act = currentAction();
  const tgt = act ? act.targeting : null;
  const cspec = cellSpec();
  const origin = originCell();
  const sweepCells = sweepPreview();

  // A planned-but-unresolved move: draw the selected piece as a ghost at its
  // intended square rather than where it currently stands. Covers the tentative
  // (arrow-key) square, the confirmed destination, and a sealed-but-unresolved
  // order — all cleared once the move actually resolves on the board.
  let previews = [];
  const ghostFor = (id, to) => {
    const su = (S.units||[]).find(x=>x.id===id);
    if (su && to && !eq(to, su.cell)) previews.push({u:su, to});
  };
  if (S.phase==='commit' && draft){
    // Both halves of a linked body show where they are going as soon as they are
    // placed — before anything is confirmed and before either has aimed.
    if (isLinked()) for (const g of linkedOrder()) ghostFor(g.entity, placedPos()[g.entity]);
    const su = selectedUnit();
    if (su) ghostFor(su.id, draft.destination || draft.tentative);
    // Goblins already ordered keep their ghost while the rest of the gang is aimed.
    for (const o of gangOrders()) ghostFor(o.entity, o.destination);
    // A picked charge lane shows where the hero will pull up.
    const lane = chargeLane();
    if (lane && lane.landing && su) ghostFor(su.id, lane.landing);
  } else if (pendingMoves.length && S.phase==='commit' && S.commit && S.commit.sealed){
    // Only during the exact sealed-and-waiting window of the order that set them —
    // once the exchange resolves the seal clears, so ghosts never linger.
    for (const p of pendingMoves) ghostFor(p.id, p.to);
  }

  let html = '';
  for (let r=1;r<=rows;r++){
    html += `<div class="boardrow"><div class="board">`;
    for (let c=1;c<=cols;c++){
      const cell=[c,r], cls=['cell'];
      cls.push(c<=3 ? 'regL' : c>=cols-2 ? 'regR' : 'regM');
      let mark='', extra='';
      if (S.phase==='setup' && has(S.zone,cell)) cls.push('zone');
      // A square can carry several effects at once — burning ground you can both
      // see, and a charge only your side knows about.
      const effs = (S.tiles||[]).filter(t=>eq(t.cell,cell));
      const tile = effs.find(t => t.kind==='burning');
      // Charges pile up without limit, so a marker has to say how many as well as
      // what. A bare count would be ambiguous against a big bomb's countdown, so
      // counts always carry ×, and a fuse always reads "in N".
      const smalls = effs.filter(t => t.kind==='small_bomb').length;
      const bigs = effs.filter(t => t.kind==='big_bomb');
      let mineTxt = '';
      if (smalls) mineTxt += '◆' + (smalls>1 ? '×'+smalls : '');
      if (bigs.length){
        const soonest = Math.min(...bigs.map(b => b.fuse_round));
        const left = Math.max(0, soonest - (S.round||0));
        mineTxt += (mineTxt?' ':'') + '◈' + (left ? 'in'+left : 'now')
                 + (bigs.length>1 ? '×'+bigs.length : '');
      }
      if (tile) cls.push('burn');
      if (mineTxt) cls.push('mined');
      if (origin && cspec &&
          Math.abs(origin[0]-c)+Math.abs(origin[1]-r) <= cspec.range) cls.push('inrange');
      const step = stepTask();
      if (step && has(step.options, cell)) cls.push('legal');
      const aimed = stepTask() ? [aimedStep()].filter(Boolean)
        : S.phase==='opening' ? [openingPick].filter(Boolean)
        : !draft ? []
        : isLinked() ? linkedOrder().map(g => placedPos()[g.entity]).filter(Boolean)
        : [draft.destination, draft.tentative].filter(Boolean);
      if (aimed.some(x => eq(x, cell))) cls.push('dest');
      // Just the styling here — `pick` is added below from clickableCell, which is
      // the single answer to "can this square be clicked".
      if (S.phase==='commit' && draft && !draft.destination && !draft.held &&
          has(curMoves(),cell)) cls.push('legal');
      if (has(sweepCells,cell)) cls.push('preview');
      if (draft && draft.shots){
        draft.shots.forEach((sh,i)=>{ if (has(sh,cell)){ cls.push('marked'); mark = draft.shots.length>1?(i+1):'x'; }});
      }
      if (draft && draft.cell && eq(draft.cell,cell)) cls.push('marked');
      if (S.phase==='victim' && S.victim && has(S.victim.cells,cell)) cls.push('marked');
      if (clickableCell(cell)) cls.push('pick');

      let u = unitAt(cell), ghost = false;
      for (const mp of previews){
        if (u && u.id===mp.u.id){ u = null; cls.push('movefrom'); }   // vacate + mark origin
        if (eq(cell, mp.to)){ u = mp.u; ghost = true; }                // ghost at target
      }
      if (u) extra = unitHTML(u, ghost);
      if (S.phase==='setup'){
        const p = (S.setup.placements||[]).find(x=>eq(x.cell,cell));
        if (p) extra = placementHTML(p);
      }
      const dnd = (S.phase==='setup' && !S.setup.ready)
        ? ` ondragover="allowDrop(event)" ondragenter="dragEnterCell(event)"`
          + ` ondragleave="dragLeaveCell(event)" ondrop="dropCell(event,${c},${r})"`
        : '';
      const live = cls.includes('pick');
      html += `<button type="button" class="${cls.join(' ')}" data-mark="${mark}" data-cell="${c},${r}"`
            + ` ${live?'':'tabindex="-1"'} aria-label="${u?u.name:'empty square'}"`
            + (effs.length?` title="${effs.map(t=>t.text).filter(Boolean).join(' · ')}"`:'')
            + ` onclick="onCell(${c},${r})"${dnd}>`
            + `<span class="tick"></span>${extra}`
            + (tile?`<span class="flame">▲${tile.stacks}</span>`:'')
            + (mineTxt?`<span class="bomb">${mineTxt}</span>`:'')
            + `</button>`;
    }
    html += '</div></div>';
  }
  document.getElementById('rows').innerHTML = html;
  applyHover();
}

/* ---- hover a hero to preview its auto-attack reach; click to inspect ---- */
function reachCells(origin, rng){
  if (rng==null) return [];
  const out=[];
  for (let c=1;c<=S.board.cols;c++) for (let r=1;r<=S.board.rows;r++)
    if (Math.abs(origin[0]-c)+Math.abs(origin[1]-r) <= rng) out.push([c,r]);
  return out;
}
function applyHover(){
  document.querySelectorAll('.cell.reachL,.cell.reachR').forEach(el=>el.classList.remove('reachL','reachR'));
  if (hoverId==null) return;
  const u = (S.units||[]).find(x=>x.id===hoverId);
  if (!u || !u.cell) return;
  const cls = u.side==='L' ? 'reachL' : 'reachR';
  for (const [c,r] of reachCells(plannedCell(u), u.rng)){
    const el = document.querySelector(`#rows .cell[data-cell="${c},${r}"]`);
    if (el) el.classList.add(cls);
  }
}
function setHover(id){ hoverId = (id==null ? null : id); applyHover(); }
function inspect(id){ inspected = (inspected === id ? null : id); renderRHS(); }

function unitHTML(u, pending){
  const cls=['unit',u.side];
  if (pending) cls.push('pending');
  if (u.acted) cls.push('acted');
  if (!u.alive) cls.push('dead');
  const curId = draft ? draft.entity : (S.commit && S.commit.selected);
  if (curId===u.id) cls.push('sel');
  const qi = gangOrders().findIndex(o=>o.entity===u.id);
  if (qi>=0) cls.push('queued');
  if (S.phase==='victim' && S.victim && S.victim.needed && S.victim.options.includes(u.id)) cls.push('tgt');
  const act = currentAction();
  if (act && act.targeting.kind==='two_units' && (act.targeting.options||[]).includes(u.id)){
    cls.push('clickable');
    if ((draft.pair||[]).includes(u.id)) cls.push('tgt');
  }
  if (act && act.targeting.kind==='unit' && u.side!==SIDE) cls.push('clickable');
  if (act && act.targeting.kind==='ally' && u.side===SIDE) cls.push('clickable');
  if (draft && namedUnits().includes(u.id)) cls.push('tgt');
  if (S.phase==='commit' && !S.commit.sealed && !S.commit.selected && canAct(u)) cls.push('clickable');
  const pct = Math.max(0,Math.round(100*u.hp/u.max_hp));
  const pips = u.max_ap>0 ? `<span class="pips">${Array.from({length:u.max_ap},(_,i)=>`<i class="${i<u.ap?'on':''}"></i>`).join('')}</span>` : '';
  const st = u.status || [];
  for (const s of st) cls.push('st-'+s.key);
  const badges = st.map(s => `<span class="st" title="${s.label} — ${s.text}">${s.badge}</span>`).join('');
  // Board art sits behind the name and HP; heroes with no sprite render as before.
  const sprite = u.sprite ? `<span class="sp" style="background-image:url('${u.sprite}')"></span>` : '';
  if (u.sprite) cls.push('has-sprite');
  return `<span class="${cls.join(' ')}" onmouseenter="setHover(${u.id})" onmouseleave="setHover(null)" onclick="inspect(${u.id})">`
       + sprite
       + (qi>=0 ? `<span class="qn" title="acts ${qi+1}${'st,nd,rd'.split(',')[qi]||'th'} this gang turn">${qi+1}</span>` : '')
       + badges
       + (u.acted && !pending ? `<span class="done">✓</span>` : '')
       + `<span class="nm">${u.name.slice(0,4)}</span>`
       + `<span class="hpbar"><i class="${pct<40?'hurt':''}" style="width:${pct}%"></i></span>`
       + `<span class="hpn">${u.hp}</span>${pips}</span>`;
}

function heroByKey(k){ return (S.roster||[]).find(h=>h.key===k); }

function placementHTML(p){
  const hero = heroByKey(p.key) || {name:p.key, hp:''};
  const drag = S.setup.ready ? ''
    : `draggable="true" ondragstart="startDragUnit(event,'${p.key}',${p.cell[0]},${p.cell[1]})"`;
  return `<span class="unit ${SIDE} place ${hero.sprite?'has-sprite':''}" ${drag}>`
       + (hero.sprite ? `<span class="sp" style="background-image:url('${hero.sprite}')"></span>` : '')
       + `<span class="rm">✕</span>`
       + `<span class="nm">${hero.name.slice(0,4)}</span>`
       + `<span class="hpn">${hero.hp} HP</span></span>`;
}

/* ---------------- deployment drag & drop ---------------- */
let dragHero = null;
function setDragChip(e, key){
  // Replace the browser's default drag ghost (a snapshot of the whole card)
  // with a small cell-sized chip carrying just the hero's name.
  const hero = heroByKey(key) || {name:key};
  const chip = document.createElement('div');
  chip.textContent = hero.name;
  chip.style.cssText = 'position:absolute;top:-999px;left:-999px;'
    + 'padding:8px 12px;border-radius:3px;white-space:nowrap;'
    + 'background:#16232a;border:1px solid #57A08E;color:#DCE4E8;'
    + 'font:600 12px system-ui,sans-serif';
  document.body.appendChild(chip);
  e.dataTransfer.setDragImage(chip, 24, 16);
  setTimeout(()=>chip.remove(), 0);
}
function startDragHero(e, key){
  if (S.phase!=='setup' || S.setup.ready) { e.preventDefault(); return; }
  // A squad can owe several copies of the same body, so compare counts.
  const owed = (S.roster||[]).filter(x=>x.key===key).length;
  if (S.setup.placements.filter(p=>p.key===key).length >= owed) { e.preventDefault(); return; }
  dragHero = {key, fromCell:null};
  e.dataTransfer.setData('text/plain', key);
  e.dataTransfer.effectAllowed='move';
  setDragChip(e, key);
  e.currentTarget.classList.add('dragging');
  document.body.classList.add('placing');
}
function startDragUnit(e, key, c, r){
  if (S.phase!=='setup' || S.setup.ready) { e.preventDefault(); return; }
  dragHero = {key, fromCell:[c,r]};
  e.dataTransfer.setData('text/plain', key);
  e.dataTransfer.effectAllowed='move';
  setDragChip(e, key);
  document.body.classList.add('placing');
  e.stopPropagation();
}
function allowDrop(e){ if (dragHero){ e.preventDefault(); e.dataTransfer.dropEffect='move'; } }
function dragEnterCell(e){ if (dragHero){ e.preventDefault(); e.currentTarget.classList.add('drop-ok'); } }
function dragLeaveCell(e){ e.currentTarget.classList.remove('drop-ok'); }
function endDrag(){
  dragHero = null;
  document.body.classList.remove('placing');
  document.querySelectorAll('.drop-ok').forEach(el=>el.classList.remove('drop-ok'));
  document.querySelectorAll('.hero.dragging').forEach(el=>el.classList.remove('dragging'));
}
async function dropCell(e, c, r){
  e.preventDefault();
  const d = dragHero; endDrag();
  if (!d || S.phase!=='setup' || S.setup.ready) return;
  const cell=[c,r];
  if (!has(S.zone, cell)) return;
  const occ = S.setup.placements.find(p=>eq(p.cell,cell));
  if (!d.fromCell){                       // from roster
    if (occ) return;
    await cmd({cmd:'place', hero:d.key, cell});
  } else if (!eq(d.fromCell, cell)) {      // moving a placed hero
    if (!occ){
      await api('/api/cmd', {cmd:'unplace', cell:d.fromCell});
      await cmd({cmd:'place', hero:d.key, cell});
    } else {                               // swap two placed heroes
      const other = occ.key;
      await api('/api/cmd', {cmd:'unplace', cell:d.fromCell});
      await api('/api/cmd', {cmd:'unplace', cell});
      await api('/api/cmd', {cmd:'place', hero:d.key, cell});
      await cmd({cmd:'place', hero:other, cell:d.fromCell});
    }
  }
}

// 狙击手's lanes, scanned client-side from the square being aimed at — the
// server re-scans from the real destination when the order is sealed. Mirrors
// actions.LineShot.scan: first body wins, your own line blocks the shot.
function laneShots(){
  const act = currentAction(), u = selectedUnit();
  if (!act || act.targeting.kind!=='lane' || !u) return [];
  const o = originCell(); if (!o) return [];
  const fwd = SIDE==='L' ? 1 : -1;
  const steps = {forward:[fwd,0], backward:[-fwd,0], up:[0,-1], down:[0,1]};
  const out = [];
  for (const d of (act.targeting.dirs||[])){
    const [dc,dr] = steps[d]; let c=o[0], r=o[1];
    for (let n=1;;n++){
      c += dc; r += dr;
      if (c<1 || r<1 || c>S.board.cols || r>S.board.rows) break;
      const v = unitAt([c,r]);
      if (!v || v.id===u.id) continue;                // its own pre-move body doesn't block
      if (v.side===SIDE) break;                       // your own line blocks it
      out.push({dir:d, target:v, distance:n, damage:Math.max(0, n + (u.atk||0))});
      break;
    }
  }
  return out;
}
function currentLane(){
  return draft && draft.direction ? laneShots().find(l=>l.dir===draft.direction) || null : null;
}
function chargeLane(){
  const act = currentAction();
  if (!act || !draft || !draft.direction) return null;
  return ((act.targeting.choices)||[]).find(x=>x.dir===draft.direction) || null;
}
// The hero's area attack, whether or not it is the action currently armed —
// clicking a covered square is how you choose it.
// The arcs a cone attack could spray, from where the hero will be standing.
function coneArcs(){
  const act = currentAction(), u = selectedUnit();
  if (!act || act.targeting.kind!=='cone' || !u) return [];
  const o = originCell(); if (!o) return [];
  const fwd = SIDE==='L' ? 1 : -1;
  const steps = {forward:[fwd,0], backward:[-fwd,0], up:[0,-1], down:[0,1]};
  return (act.targeting.dirs||[]).map(d => {
    const [dc,dr] = steps[d.dir], perp = dc ? [0,1] : [1,0];
    const cells = [[o[0]+dc, o[1]+dr],
                   [o[0]+dc+perp[0], o[1]+dr+perp[1]],
                   [o[0]+dc-perp[0], o[1]+dr-perp[1]]]
      .filter(([c,r]) => c>=1 && r>=1 && c<=S.board.cols && r<=S.board.rows);
    const hits = cells.filter(c => { const v=unitAt(c); return v && v.side!==SIDE; }).length;
    return {dir:d.dir, cells, hits};
  }).filter(a => a.cells.length);
}
function currentArc(){
  return draft && draft.direction ? coneArcs().find(a=>a.dir===draft.direction) || null : null;
}
// The shapes a shape-targeted ability could use, centred on the square the hero
// will be standing in once it has moved.
const SHAPE_LABEL = {row:'Row — right across the board',
                     column:'Column — top to bottom',
                     surround8:'All around you — the 8 squares'};
function shapeOptions(){
  const act = currentAction(), u = selectedUnit();
  if (!act || act.targeting.kind!=='shape' || !u) return [];
  const o = originCell(); if (!o) return [];
  const inside = ([c,r]) => c>=1 && r>=1 && c<=S.board.cols && r<=S.board.rows;
  return (act.targeting.options||[]).map(which => {
    let cells = [];
    if (which==='row') for (let c=1;c<=S.board.cols;c++) cells.push([c,o[1]]);
    else if (which==='column') for (let r=1;r<=S.board.rows;r++) cells.push([o[0],r]);
    else for (const dc of [-1,0,1]) for (const dr of [-1,0,1])
      if (dc||dr) cells.push([o[0]+dc, o[1]+dr]);
    cells = cells.filter(inside);
    const hits = cells.filter(c => { const v=unitAt(c); return v && v.side!==SIDE; }).length;
    return {dir:which, cells, hits};
  });
}
function currentShape(){
  return draft && draft.direction ? shapeOptions().find(x=>x.dir===draft.direction) || null : null;
}
function areaAttack(){
  return curActions().find(a => a.targeting && a.targeting.kind==='area') || null;
}
// The squares that swing catches, centred on where the hero will stand.
function areaCells(act){
  act = act || currentAction();
  if (!act || act.targeting.kind!=='area') return [];
  const o = originCell(); if (!o) return [];
  const out = [];
  for (const dc of [-1,0,1]) for (const dr of [-1,0,1]){
    if (!dc && !dr) continue;
    const c=o[0]+dc, r=o[1]+dr;
    if (c>=1 && r>=1 && c<=S.board.cols && r<=S.board.rows) out.push([c,r]);
  }
  return out;
}
function sweepPreview(){
  const act = currentAction();
  if (act && act.targeting.kind==='area') return areaCells(act);
  if (act && act.targeting.kind==='cone'){
    const arc = currentArc();
    return arc ? arc.cells : [];
  }
  if (act && act.targeting.kind==='shape'){
    const sh = currentShape();
    return sh ? sh.cells : [];
  }
  if (!act || !draft || !draft.direction || act.targeting.kind!=='direction') return [];
  const shot = currentLane();
  if (shot) return shot.target.cell ? [shot.target.cell] : [];
  const lane = chargeLane();
  if (lane) return lane.victims
    .map(id => (S.units||[]).find(u=>u.id===id))
    .filter(u => u && u.cell).map(u => u.cell);
  const o = originCell(); if (!o) return [];
  let step = SIDE==='L' ? 1 : -1;
  if (draft.direction==='backward') step = -step;
  const out=[];
  for (const col of [o[0], o[0]+step])
    for (let r=1;r<=S.board.rows;r++) if (col>=1 && col<=S.board.cols) out.push([col,r]);
  return out;
}

function clickableCell(cell){
  if (S.phase==='setup') return !S.setup.ready && has(S.zone,cell);
  if (S.phase==='opening'){
    const t = S.opening && S.opening.task;
    if (!t) return false;
    if (t.targeting.kind==='unit'){        // name an enemy: click the hero itself
      const u = unitAt(cell);
      return !!(u && u.alive && u.side!==SIDE);
    }
    if (t.targeting.kind!=='any_cell') return false;
    const only = t.targeting.cells;
    return only ? has(only, cell) : true;
  }
  if (S.phase==='resolved'){
    const t = S.followup && S.followup.task;
    if (!t || t.kind==='confirm') return false;
    if (t.kind==='unit'){
      const u = unitAt(cell);
      return !!(u && t.options.includes(u.id));
    }
    return has(t.options, cell);
  }
  if (S.phase==='move_choice'){
    const t = S.move_choice && S.move_choice.task;
    return !!(t && has(t.options, cell));
  }
  if (S.phase!=='commit' || !draft || S.commit.sealed) return false;
  if (isLinked() && draft.stage!=='act'){
    // The squares still to be placed, plus either half itself — clicking a body
    // goes back to positioning it, so it has to stay live once both are down.
    if (has(curMoves(), cell)) return true;
    const u = unitAt(cell);
    return !!(u && linkedOrder().some(g => g.entity === u.id));
  }
  if (!draft.destination && !draft.held) return has(curMoves(),cell);
  const act = currentAction(); if (!act) return false;
  const cs = cellSpec();
  if (cs){
    if (S.mode==='self') return false;   // solo mode auto-aims — no manual marking
    const o = originCell();
    if (!o) return false;          // nowhere to fire from yet
    return Math.abs(o[0]-cell[0])+Math.abs(o[1]-cell[1]) <= cs.range;
  }
  if (act.targeting.kind==='any_cell')
    return act.targeting.cells ? has(act.targeting.cells, cell) : true;
  if (act.targeting.kind==='lane')
    return laneShots().some(l => l.target.cell && eq(l.target.cell, cell));
  if (areaAttack()) return has(areaCells(areaAttack()), cell);
  if (act.targeting.kind==='cone')
    return coneArcs().some(a => has(a.cells, cell));
  if (act.targeting.kind==='shape')
    // A square that more than one shape covers settles nothing, so it is not
    // offered as a click — those choices are made with the buttons.
    return shapeOptions().filter(x => has(x.cells, cell)).length === 1;
  return false;
}

/* ---------------- interaction ---------------- */
function onCell(c,r){
  const cell=[c,r];
  if (S.phase==='setup'){
    if (S.setup.ready) return;
    const placed = S.setup.placements.find(p=>eq(p.cell,cell));
    if (placed) return cmd({cmd:'unplace', cell});
    if (!armed) { err="Pick a hero from the roster first."; return render(); }
    return cmd({cmd:'place', hero:armed, cell}).then(()=>{ armed=null; });
  }
  if (S.phase==='resolved' || S.phase==='move_choice'){
    const t = stepTask();
    if (t && t.kind==='confirm') return;
    if (t && t.kind==='unit'){
      const u = unitAt(cell);
      if (u && t.options.includes(u.id)) return cmd({cmd:'followup', entity:u.id});
      return;
    }
    if (t && has(t.options, cell)){
      stepPick = eq(stepPick||[], cell) ? null : cell;   // click again to unpick
      err=""; return render();
    }
    return;
  }
  if (S.phase==='victim'){
    const u = unitAt(cell);
    if (u && S.victim.needed && S.victim.options.includes(u.id)) return cmd({cmd:'victim', entity:u.id});
    return;
  }
  if (S.phase==='opening'){
    // A square-targeted opening is aimed on the board (潜水者 burying a charge) and
    // confirmed with Enter — nothing is spent on a single click. One that names an
    // ally is made in the panel, and a stray board click must not spend it.
    const t = S.opening && S.opening.task;
    if (t && (t.targeting.kind==='any_cell' || t.targeting.kind==='unit')
        && clickableCell(cell)){
      openingPick = eq(openingPick||[], cell) ? null : cell;   // click again to unpick
      err=""; return render();
    }
    return;
  }
  if (S.phase!=='commit' || S.commit.sealed) return;
  if (!S.commit.selected){
    // Click your own un-acted piece to tentatively pick it (reversible).
    const u = unitAt(cell);
    if (canAct(u)) return cmd({cmd:'select', entity:u.id});
    return;
  }
  if (!draft) return;
  if (isLinked() && draft.stage!=='act'){
    // Placing the body. Only squares that will still be legal once the earlier
    // half lands are offered, so nothing here can be refused later.
    if (has(linkedMoves(draft.posIdx||0), cell)){
      draft.pos[linkedAt(draft.posIdx||0).entity] = cell;
      draft.posIdx = Math.min((draft.posIdx||0)+1, linkedOrder().length);
      err=""; return render();
    }
    const u = unitAt(cell);
    const idx = u ? linkedOrder().findIndex(g => g.entity===u.id) : -1;
    if (idx >= 0){
      // Clicking one of your own halves goes back to placing it, dropping the
      // choices that were made after it.
      draft.posIdx = idx;
      for (let j=idx; j<linkedOrder().length; j++) delete draft.pos[linkedAt(j).entity];
      err=""; return render();
    }
    if (canAct(u)) return cmd({cmd:'select', entity:u.id});
    return;
  }
  if (isGang() && !draft.destination && !draft.held){
    const u = unitAt(cell);
    if (canAct(u)){
      if (gangMember(u.id)){
        if (isOrdered(u.id)) return;        // that goblin already has its orders
        // Clicking a goblin says "this one acts next" — the click order IS the
        // acting order. Clicking the one you are aiming puts it back in the queue.
        draft.entity = (u.id===draft.entity) ? null : u.id;
        resetLeg(); err=""; return render();
      }
      // One of your other heroes: switch the turn to it. The gang's orders were
      // only ever local, so nothing was committed and nothing is lost.
      return cmd({cmd:'select', entity:u.id});
    }
    if (!draft.entity) return;              // nobody is up yet — squares do nothing
    if (has(curMoves(),cell)){ draft.tentative=cell; err=""; return render(); }
    return;
  }
  if (!draft.destination && !draft.held){
    // Nothing is locked until you press Enter. Click another hero to switch, the
    // current one to back out, or a legal square to *tentatively* aim the move —
    // just like the arrow keys. Enter then confirms and locks this hero in.
    const u = unitAt(cell);
    if (canAct(u)){
      return u.id === S.commit.selected ? cmd({cmd:'deselect'}) : cmd({cmd:'select', entity:u.id});
    }
    if (has(curMoves(),cell)){ draft.tentative=cell; err=""; return render(); }
    return;
  }
  const act = currentAction(); if (!act) return;
  const cs = cellSpec();
  if (cs){
    if (S.mode==='self') return;   // solo mode auto-aims — clicks do nothing here
    const o=originCell();
    if (!o){ err="Take flesh first."; return render(); }
    if (Math.abs(o[0]-c)+Math.abs(o[1]-r) > cs.range){ err="Out of range."; return render(); }
    const sh = draft.shots[draft.shotIndex] || (draft.shots[draft.shotIndex]=[]);
    const at = sh.findIndex(x=>eq(x,cell));
    if (at>=0) sh.splice(at,1);
    else if (sh.length < cs.count) sh.push(cell);
    else { err=`Already marked ${cs.count} cells.`; return render(); }
    err=""; return render();
  }
  if (act.targeting.kind==='cone'){
    const arcs = coneArcs();
    // The square directly next to the hero belongs to exactly one arc, so that is
    // the unambiguous way to aim; other covered squares fall through to it.
    const centre = arcs.find(a => eq(a.cells[0], cell));
    const any = centre || arcs.find(a => has(a.cells, cell));
    if (any){ draft.direction = any.dir; err=""; return render(); }
    return;
  }
  if (areaAttack()){
    // Nothing to aim: clicking any square the swing covers simply chooses it,
    // and Enter confirms. Clicking anywhere else leaves the order alone.
    if (has(areaCells(areaAttack()), cell)){ draft.actionKey='attack'; err=""; return render(); }
    return;
  }
  if (act.targeting.kind==='lane'){
    const l = laneShots().find(x => x.target.cell && eq(x.target.cell, cell));
    if (l){ draft.direction = l.dir; err=""; return render(); }
    return;
  }
  if (act.targeting.kind==='shape'){
    // Clicking a square only aims when exactly one shape covers it; where several
    // overlap there is nothing to infer, so the buttons settle it.
    const only = shapeOptions().filter(x => has(x.cells, cell));
    if (only.length===1){ draft.direction = only[0].dir; err=""; return render(); }
    return;
  }
  if (act.targeting.kind==='any_cell'){
    const only = act.targeting.cells;
    if (only && !has(only, cell)){ err="Not a square this can be used on."; return render(); }
    draft.cell=cell; err=""; return render();
  }
  if (act.targeting.kind==='two_units'){
    const tu = unitAt(cell);
    if (!tu || !(act.targeting.options||[]).includes(tu.id)) return;
    const at = (draft.pair||[]).indexOf(tu.id);
    if (at >= 0) draft.pair.splice(at, 1);
    else if (draft.pair.length < 2) draft.pair.push(tu.id);
    else { err = "Two is all it can move."; return render(); }
    err=""; return render();
  }
  if (act.targeting.kind==='unit' || act.targeting.kind==='ally'){
    const tu = unitAt(cell); if (!tu || !tu.alive) return;
    const wantAlly = act.targeting.kind==='ally';
    if (wantAlly ? tu.side===SIDE : tu.side!==SIDE){ nameUnit(tu.id); err=""; return render(); }
  }
}

function onUnitPick(id){
  if (S.phase==='commit' && !S.commit.selected){
    const u = (S.units||[]).find(x=>x.id===id);
    return canAct(u) ? cmd({cmd:'select', entity:id}) : null;
  }
  const act = currentAction();
  if (act && act.targeting.kind==='unit'){ nameUnit(id); err=""; render(); }
}

/* ---- keyboard: arrows move the piece, Enter confirms move then registers attack ---- */
function commitActive(){
  return S && S.phase==='commit' && S.commit && !S.commit.sealed && S.commit.selected && draft;
}
function moveInputActive(){
  if (isLinked()) return !!commitActive() && draft.stage!=='act';
  return commitActive() && !draft.destination && !draft.held
      && (!isGang() || !!draft.entity);
}
function confirmMove(){
  if (!moveInputActive()) return;
  if (isLinked()){
    // One Enter settles the whole body; only then does either half aim.
    if (!placedAll()){
      err = `Place ${linkedAt(draft.posIdx||0).name} first.`;
      return render();
    }
    draft.stage = 'act'; draft.posIdx = 0;
    return beginLinkedLeg();
  }
  if (draft.tentative) draft.destination = draft.tentative;
  else draft.held = true;              // Enter with no move = hold position
  draft.tentative = null; err="";
  finishMove();
}
function finishMove(){
  // Arm the normal attack the moment the move is set — from arrow+Enter, a click,
  // or hold — so grids are immediately clickable without picking "Normal attack".
  const atk = curActions().find(a=>a.key==='attack');
  if (atk) chooseAction('attack'); else render();
}
function sealFromKeyboard(){
  const act = currentAction();
  if (!act) return;
  if (!choicesReady()){ err = "Make the free pick first."; return render(); }
  // Normal attack: any marked grids seal it; nothing marked = hold.
  if (act.key==='attack' && act.targeting.kind==='cells'){
    // Solo mode fires a random attack; otherwise no grids marked = hold.
    if (S.mode!=='self' && !(draft.shots||[]).some(s=>s.length>0)) draft.actionKey='none';
    return sealOrder();
  }
  if (act.key==='attack' && act.targeting.kind==='area') return sealOrder();
  if (act.key==='attack' && act.targeting.kind==='cone'){
    if (draft.direction==null){ err = stillNeeded(act); return render(); }
    return sealOrder();
  }
  if (act.key==='attack' && act.targeting.kind==='lane'){
    if (draft.direction==null){
      if (laneShots().length){          // a shot is there to be taken — don't eat it
        err = "Pick a lane to fire down, or choose Hold.";
        return render();
      }
      draft.actionKey='none';           // genuinely nothing to shoot: hold
    }
    return sealOrder();
  }
  if (act.key==='attack' && act.targeting.kind==='unit'){
    // Same trap as the sniper's lane: silently turning an unaimed attack into a
    // hold loses the player's turn without telling them. Hold is a button.
    if (namedUnits().length !== unitCount()){
      if ((S.commit.enemies||[]).length){ err = stillNeeded(act); return render(); }
      draft.actionKey='none';
    }
    return sealOrder();
  }
  // Hold, or an ability whose parameters are set.
  if (act.key==='none' || orderReady()) return sealOrder();
  // Half-chosen: Enter used to do nothing at all, which reads as a frozen UI.
  err = stillNeeded(act);
  render();
}
// What an armed-but-unaimed action is still waiting for. Enter is the one key
// players lean on, so it must always answer.
function stillNeeded(act){
  const t = act.targeting;
  if (t.kind==='ally')      return `Choose an ally for ${act.name} — click one on the board or in the panel.`;
  if (t.kind==='unit')
    return unitCount()>1
      ? `Name ${unitCount()} enemies for ${act.name} — ${namedUnits().length} so far.`
      : `Choose an enemy for ${act.name}.`;
  if (t.kind==='any_cell')  return `Choose a square for ${act.name}.`;
  if (t.kind==='direction') return `Choose a direction for ${act.name}.`;
  if (t.kind==='weapon')    return draft.weapon ? 'Finish aiming this weapon.' : 'Choose a weapon first.';
  if (t.kind==='lane')      return 'Pick a lane to fire down, or choose Hold.';
  if (t.kind==='shape')     return `Pick the shape for ${act.name}.`;
  if (t.kind==='cone')      return 'Pick a direction to spray, or choose Hold.';
  if (t.kind==='two_units')
    return (draft.pair||[]).length ? 'Choose a second unit to swap with.'
                                   : 'Choose the two units to swap — click them on the board.';
  if (t.kind==='magnitude') return 'Choose an amount.';
  return 'That order is not ready yet.';
}
function onKey(e){
  // Deployment: Enter seals once all heroes are placed.
  if (S && S.phase==='setup' && !S.setup.ready){
    if (e.key==='Enter' && S.setup.placements.length===S.setup.force_size){
      e.preventDefault(); cmd({cmd:'lock'});
    }
    return;
  }
  if (S && (S.phase==='resolved' || S.phase==='move_choice')){
    const t = stepTask();
    if (e.key!=='Enter' || !t) return;
    e.preventDefault();
    if (t.kind==='confirm') cmd({cmd:'followup', confirm:true});   // Enter says yes
    else if (aimedStep()) confirmStep();
    else if (t.optional) cmd({cmd:'followup'});     // Enter alone declines
    return;
  }
  // An opening aimed at a square is aimed first and confirmed after, like a move.
  if (S && S.phase==='opening'){
    if (e.key==='Enter' && openingPick){ e.preventDefault(); confirmOpening(); }
    return;
  }
  if (!commitActive()) return;
  const moving = !draft.destination && !draft.held;
  const dirs = {ArrowUp:[0,-1], ArrowDown:[0,1], ArrowLeft:[-1,0], ArrowRight:[1,0]};
  if (moving && (e.key in dirs)){
    e.preventDefault();
    const u = selectedUnit(); if (!u) return;
    const cur = draft.tentative || u.cell;
    const [dx,dy] = dirs[e.key];
    const cand = [cur[0]+dx, cur[1]+dy];
    if (eq(cand, u.cell)){ draft.tentative=null; err=""; return render(); }   // step back to origin
    if (curMoves().some(m=>eq(m,cand))){ draft.tentative=cand; err=""; return render(); }
    return;                                     // off-limits — ignore
  }
  if (e.key==='Enter'){
    e.preventDefault();
    if (moving) confirmMove(); else sealFromKeyboard();
  }
}

function chooseAction(key){
  const act = curActions().find(a=>a.key===key);
  draft.actionKey=key; draft.shots=[]; draft.shotIndex=0; draft.target=null; draft.direction=null; draft.cell=null; draft.amount=null; draft.weapon=null; draft.pair=[]; draft.named=[];
  if (act.targeting.kind==='cells') draft.shots = Array.from({length:act.targeting.shots},()=>[]);
  if (act.targeting.kind==='magnitude') draft.amount = 1;
  if (act.targeting.kind==='lane'){
    // Only one line of fire: aim it, so Enter shoots instead of quietly holding.
    const only = laneShots();
    if (only.length === 1) draft.direction = only[0].dir;
  }
  err=""; render();
}
function changeMove(){
  // A linked body is positioned as a whole, so re-opening the step restarts the
  // placement rather than freeing one half of it.
  if (isLinked()) return resetLinked();
  // Re-opening the movement step drops the action aimed from the old square — a
  // lane or a marked cell chosen from there is meaningless once you move.
  Object.assign(draft, {destination:null, held:false, tentative:null, actionKey:null,
                        shots:[], shotIndex:0, target:null, direction:null,
                        cell:null, amount:null, weapon:null, pair:[]});
  err=""; render();
}
function chooseSelfMove(key){
  // The ability supplies the motion, so the movement step is settled as "hold"
  // without ever asking for a destination that would then be thrown away.
  draft.tentative = null; draft.destination = null; draft.held = true;
  chooseAction(key);
}
function chooseWeapon(key){
  draft.weapon = key;
  const w = currentWeapon();
  draft.shots = (w && w.mode==='cells') ? [[]] : [];
  draft.shotIndex = 0; err=""; render();
}

function orderReady(){
  if (!draft || (!draft.destination && !draft.held)) return false;
  if (!choicesReady()) return false;
  const act = currentAction(); if (!act) return false;
  const t = act.targeting;
  if (t.kind==='none') return true;
  if (t.kind==='cells') return S.mode==='self' || (draft.shots.length===t.shots && draft.shots.every(s=>s.length<=t.count));
  if (t.kind==='unit') return namedUnits().length === unitCount();
  if (t.kind==='ally') return draft.target!=null;
  if (t.kind==='two_units') return (draft.pair||[]).length===2;
  if (t.kind==='magnitude') return draft.amount>=1;
  if (t.kind==='cone') return draft.direction!=null;
  if (t.kind==='area') return true;   // nothing to aim — it catches whatever is beside it
  if (t.kind==='lane') return true;   // no lane picked simply holds
  if (t.kind==='direction') return draft.direction!=null;
  if (t.kind==='shape') return draft.direction!=null;
  if (t.kind==='any_cell') return draft.cell!=null;
  if (t.kind==='weapon') return draft.weapon!=null;
  return false;
}

function sealOrder(){
  const act=currentAction(), t=act.targeting, action={key:draft.actionKey};
  if (t.kind==='cells') action.shots = draft.shots;
  if (t.kind==='unit'){
    const ids = namedUnits();
    if (unitCount() > 1) action.targets = ids; else action.target = ids[0];
  }
  if (t.kind==='ally') action.target = draft.target;
  if (t.kind==='two_units'){ action.first = draft.pair[0]; action.second = draft.pair[1]; }
  if (t.kind==='magnitude') action.amount = draft.amount;
  if (t.kind==='direction' || t.kind==='lane' || t.kind==='cone' || t.kind==='shape') action.direction = draft.direction;
  if (t.kind==='any_cell') action.cell = draft.cell;
  if (t.kind==='weapon'){ action.weapon = draft.weapon; const w=currentWeapon(); if (w && w.mode==='cells') action.shots = draft.shots; }
  if (isLinked()){
    draft.gangOrders.push({entity: draft.entity, destination: draft.destination, action,
                           choices: Object.assign({}, draft.choices)});
    draft.posIdx++;
    if (draft.posIdx < linkedOrder().length){ beginLinkedLeg(); return render(); }
    pendingMoves = gangOrders().filter(o=>o.destination).map(o=>({id:o.entity, to:o.destination}));
    return cmd({cmd:'commit', payload:{orders: gangOrders()}});
  }
  if (isGang()){
    draft.gangOrders.push({entity: draft.entity, destination: draft.destination, action,
                           choices: Object.assign({}, draft.choices)});
    draft.entity = null; resetLeg(); err="";
    if (gangPending().length) return render();     // on to the next goblin
    pendingMoves = gangOrders().filter(o=>o.destination).map(o=>({id:o.entity, to:o.destination}));
    return cmd({cmd:'commit', payload:{orders: gangOrders()}});
  }
  // Remember the intended move so the ghost persists after the slip is sealed,
  // until the exchange actually resolves.
  pendingMoves = draft.destination ? [{id: draft.entity, to: draft.destination}] : [];
  cmd({cmd:'commit', payload:{destination: draft.destination, action, choices: draft.choices||{}}});
}

function pickGoblin(id){
  if (!isGang() || isOrdered(id)) return;
  draft.entity = id; resetLeg(); err=""; render();
}
function dropGang(){
  draft = blankDraft(null);        // keep the object valid until the poll rebuilds it
  cmd({cmd:'deselect'});
}
function resetGang(){
  if (!isGang()) return;
  draft.gangOrders = []; draft.entity = null; resetLeg(); err=""; render();
}

/* ---------------- panels ---------------- */
function render(){
  if (!S) return;
  document.getElementById('badge').textContent = SIDE==='L' ? 'Left force' : 'Right force';
  document.getElementById('badge').className = 'sidebadge side'+SIDE;
  document.getElementById('clock').innerHTML =
      S.phase==='draft' ? 'Draft'
    : S.phase==='setup' ? 'Deployment'
    : S.phase==='opening' ? 'Opening'
    : `Round <b>${S.round}</b> · exchange <b>${S.exchange}</b> · <b>${S.phase}</b>`;
  document.getElementById('modal').innerHTML = '';
  renderBoard();
  renderRHS();
  if (S.phase==='draft') renderDraft();
  else if (S.phase==='setup') renderSetup();
  else if (S.phase==='opening') renderOpening();
  else if (S.phase==='commit') renderCommit();
  else if (S.phase==='resolved') renderFollowup();
  else if (S.phase==='move_choice') renderMoveChoice();
  else if (S.phase==='victim') renderVictim();
  else renderOver();
  if (revealActive) renderReveal();   // sits on top of everything
}

function dismissReveal(){ revealActive = false; clearTimeout(window._revealT); render(); }

function revealHits(o){
  const bits = [];
  for (const a of (o.abilities||[])) bits.push(`<div class="rv-ability">${a}</div>`);
  for (const h of (o.hits||[])) bits.push(`<div class="rv-hit"><b>${h.amount}</b> damage → ${h.target}</div>`);
  for (const n of (o.notes||[]).slice(0,3)) bits.push(`<div class="rv-note">${n}</div>`);
  if (bits.length) return bits.join('');
  return revealHitsPlain(o);
}
function revealHitsPlain(o){
  const hits = o.hits || [];
  if (!hits.length) return `<div class="rv-hit rv-none">no damage</div>`;
  return hits.map(h => `<div class="rv-hit"><b>${h.amount}</b> damage → ${h.target}</div>`).join('');
}
function revealSide(side){
  const o = S.reveal[side];
  if (!o) return `<div class="rv-side"><div class="rv-img"></div><div class="rv-name">—</div></div>`;
  const cx = codex[o.key] || {};
  const bg = cx.image ? ` style="background-image:url('${cx.image}')"` : '';
  return `<div class="rv-side side${side}">
    <div class="rv-img ${cx.image?'has-img':''}"${bg}></div>
    <div class="rv-name">${o.hero}</div>
    ${o.crew ? `<div class="rv-crew">${o.crew.join(' → ')}</div>` : ''}
    <div class="rv-order">${revealHits(o)}</div>
  </div>`;
}
function renderReveal(){
  document.getElementById('modal').innerHTML = `
    <div class="modal-backdrop reveal-back" onclick="dismissReveal()">
      <div class="reveal-box" onclick="event.stopPropagation()">
        <div class="modal-h">Exchange resolved</div>
        <div class="rv-row">${revealSide('L')}<div class="rv-vs">VS</div>${revealSide('R')}</div>
        <div class="rv-hint">click to continue</div>
      </div>
    </div>`;
}

function heroCard(hero, clickable, onclick){
  const bg = hero.image ? ` style="background-image:url('${hero.image}')"` : '';
  return `<div class="hero draft-card ${hero.image?'has-img':''} ${clickable?'pickable':''}"${bg} ${clickable?`onclick="${onclick}"`:''}>
    <div class="dc-body">
      <div class="dc-name">${hero.name}</div>
      <div class="dc-desc">${hero.blurb||''}</div>
    </div>
  </div>`;
}

function renderDraft(){
  document.getElementById('leftheading').textContent = 'Draft';
  document.getElementById('leftbody').innerHTML = `<p class="note">Hero draft in progress — make your pick in the panel.</p>`;

  if (!S.both_present){
    document.getElementById('modal').innerHTML = `
      <div class="modal-backdrop"><div class="modal">
        <div class="modal-h">Waiting for the other player</div>
        <div class="modal-sub">The draft begins once both seats are open.</div>
      </div></div>`;
    return;
  }

  const d = S.draft;
  const whose = d.picker==='L' ? 'Left' : 'Right';
  const cards = (d.shown||[]).map(h => heroCard(h, d.your_pick, `draftPick('${h.key}')`)).join('');
  const takenCol = (arr) => arr.length
    ? arr.map(x=>`<div class="hero" style="padding:6px 9px;margin-bottom:5px">
        <span class="cn">${x.name}</span> <span class="en">${x.name_en}</span></div>`).join('')
    : `<p class="note">—</p>`;

  const sub = d.your_pick ? '' : `<div class="modal-sub"><b class="side${d.picker}">${whose}</b> is choosing…</div>`;

  document.getElementById('modal').innerHTML = `
    <div class="modal-backdrop">
      <div class="modal modal-wide">
        <div class="modal-h">Draft · batch ${d.batch} of ${d.batches_total}</div>
        ${sub}
        <div class="offer-grid">${cards}</div>
        ${d.your_pick ? '' : `<div class="modal-wait">Waiting for ${whose} to choose</div>`}
        <div class="modal-taken">
          <div><div class="eyebrow sideL" style="margin-bottom:6px">Left${SIDE==='L'?' · you':''} (${d.taken.L.length}/4)</div>${takenCol(d.taken.L)}</div>
          <div><div class="eyebrow sideR" style="margin-bottom:6px">Right${SIDE==='R'?' · you':''} (${d.taken.R.length}/4)</div>${takenCol(d.taken.R)}</div>
        </div>
        <p class="err" style="text-align:center;margin-top:10px">${err}</p>
      </div>
    </div>`;
}
function draftPick(k){ cmd({cmd:'draft', hero:k}); }

function renderOpening(){
  document.getElementById('leftheading').textContent = 'Opening';
  const o = S.opening;
  let h = '';
  if (o.task){
    const t = o.task;
    h += `<p class="note"><b>${t.hero}</b> · ${t.ability}</p>`;
    if (t.text) h += `<p class="note">${t.text}</p>`;
    if (t.targeting.kind==='ally'){
      h += `<div class="step">Choose an ally</div>`;
      for (const u of myUnits().filter(u=>u.alive)){
        h += `<button class="btn" onclick="cmd({cmd:'opening', target:${u.id}})">
                ${u.name} <span class="cost">${u.hp}/${u.max_hp} HP</span></button>`;
      }
      h += `<p class="note">Hover a hero on the board to see its reach; the choice is made here.</p>`;
    } else if (t.targeting.kind==='unit'){
      h += `<div class="step">Name an enemy</div>`;
      h += `<p class="note">Click an enemy hero on the board, then <b>Enter</b> to
            confirm. Click it again to change your mind.</p>`;
      h += openingPick && unitAt(openingPick)
        ? `<button class="btn primary" onclick="confirmOpening()">Name ${unitAt(openingPick).name} — <b>Enter</b></button>`
        : `<p class="note">Nobody named yet.</p>`;
    } else if (t.targeting.kind==='any_cell'){
      h += `<div class="step">Choose a square</div>`;
      h += `<p class="note">Click a square on the board to aim, then <b>Enter</b> to
            confirm. Click it again to change your mind — nothing is spent until you
            confirm.</p>`;
      h += openingPick
        ? `<button class="btn primary" onclick="confirmOpening()">Confirm this square — <b>Enter</b></button>`
        : `<p class="note">Nothing aimed yet.</p>`;
    }
  } else {
    h += `<div class="waiting">Waiting for the other seat</div>`;
  }
  h += `<p class="err">${err}</p>`;
  document.getElementById('leftbody').innerHTML = h;
}

function renderSetup(){
  document.getElementById('leftheading').textContent = 'Deployment';
  const st = S.setup;
  // The roster lists one entry per *body*, so a squad shows up as its members and
  // 投矛手 appears twice — mark copies used by count, not by key.
  const placedCount = {}, shownCount = {};
  for (const p of st.placements) placedCount[p.key] = (placedCount[p.key]||0)+1;
  let h = '';
  if (st.ready){
    h += `<div class="waiting">Deployment sealed<br>Waiting for the other seat</div>`;
  } else {
    const left = st.force_size - st.placements.length;
    h += `<p class="note">Drag each hero onto a square in your shaded zone. <b>${left}</b> left to place.</p>`;
    h += `<p class="dzHint">Drag a placed hero to move it · drop onto another to swap · click it to remove.</p>`;
    for (const hero of S.roster){
      shownCount[hero.key] = (shownCount[hero.key]||0)+1;
      const on = shownCount[hero.key] <= (placedCount[hero.key]||0);
      const drag = on ? '' : `draggable="true" ondragstart="startDragHero(event,'${hero.key}')" ondragend="endDrag()"`;
      h += `<div class="hero ${on?'used':'pickable'} ${armed===hero.key?'armed':''}" ${drag} onclick="armHero('${hero.key}')">
        <div class="top"><span class="cn">${hero.name}</span><span class="en">${hero.name_en}</span>${on?'':'<span class="grip">⠿</span>'}</div>
        <div class="stats">HP ${hero.hp} · ATK ${hero.atk} · MOVE ${hero.move} · AP ${hero.max_ap}
          · ${hero.attack.mode==='cell_locked'?`${hero.attack.cells} cells @ ${hero.attack.range}`
              :hero.attack.mode==='area_locked'?'every enemy in the 8 around it'
              :hero.attack.mode==='cone_locked'?'a three-square arc, direction of your choice'
              :hero.attack.mode==='line_locked'?'first enemy in row/column'
              :hero.attack.mode==='weapon'?'weapon of choice':'any one enemy'}
          ${hero.shots>1?` · ${hero.shots} shots`:''}</div>
        ${hero.traits.map(t=>`<div class="trait"><b>${t.name}${t.ap_cost?` (${t.ap_cost} AP)`:''}</b> — ${t.text}</div>`).join('')}
      </div>`;
    }
    h += st.placements.length===st.force_size
      ? `<p class="note">All heroes placed — press <b>Enter</b> to seal your deployment.</p>`
      : `<p class="note">Place all ${st.force_size} heroes, then press <b>Enter</b> to seal.</p>`;
  }
  h += `<p class="err">${err}</p>`;
  document.getElementById('leftbody').innerHTML = h;
}
function armHero(k){ armed = (armed===k?null:k); err=""; render(); }

function renderCommit(){
  document.getElementById('leftheading').textContent = 'Order slip';
  const c = S.commit;
  let h = '';
  if (c.sealed){
    // Keep showing what you actually committed — the longest wait in the game is
    // the worst moment to forget your own order.
    const orders = c.orders || [];
    const f = (k,v)=>`<div class="fld"><div class="k">${k}</div><div class="v ${v==='—'?'blank':''}">${v}</div></div>`;
    h += `<div class="slip sealed ${SIDE} flip">
            <div class="stamp">Sealed</div>
            ${orders.length ? orders.map((o,i)=>`
              <div class="hdr"><span>${orders.length>1?`${i+1} · `:''}${o.hero}</span><span>R${S.round}/E${S.exchange}</span></div>
              ${f('Move', o.move)}${f('Action', o.action)}${f('Target', o.target)}`).join('')
             : `<div class="hdr"><span>${c.kind==='dead'?'Destroyed before it could act':'No order'}</span><span>R${S.round}/E${S.exchange}</span></div>`}
          </div>`;
    h += `<div class="waiting">${c.opponent_sealed?'Resolving':'Waiting for the other seat'}</div>`;
    document.getElementById('leftbody').innerHTML = h; return;
  }
  if (!c.selected){
    h += `<p class="note">Pick the hero to move.</p>`;
    for (const u of myUnits().filter(canAct)){
      h += `<div class="hero pickable" onclick="onUnitPick(${u.id})">
        <div class="top"><span class="cn">${u.name}</span><span class="en">${u.name_en}</span></div>
        <div class="stats">HP ${u.hp}/${u.max_hp} · AP ${u.ap}/${u.max_ap}</div></div>`;
    }
    // Anyone alive, un-acted and still not offered is being held out by an effect
    // (咒毒's freeze) — say so rather than silently dropping them from the list.
    for (const u of myUnits().filter(u=>u.alive && !u.acted && !canAct(u))){
      const why = (u.status||[]).map(s=>s.label).join(' · ') || 'cannot act';
      h += `<div class="hero used"><div class="top"><span class="cn">${u.name}</span>
        <span class="en">${u.name_en||''}</span></div><div class="stats">${why}</div></div>`;
    }
    const done = myUnits().filter(u=>u.alive && u.acted).length;
    if (done) h += `<p class="note">${done} of your heroes have already acted this round.</p>`;
    h += `<p class="err">${err}</p>`;
    document.getElementById('leftbody').innerHTML = h; return;
  }

  if (!draft){ document.getElementById('leftbody').innerHTML = h; return; }
  const u = selectedUnit(), act = currentAction();
  if (isLinked()){
    h += linkedHeaderHTML();
    if (draft.stage!=='act'){                 // still positioning the whole body
      h += `<p class="err">${err}</p>`;
      document.getElementById('leftbody').innerHTML = h; return;
    }
  } else if (isGang()){
    h += gangHeaderHTML();
    if (!draft.entity){                       // nobody up: choose who acts next
      h += `<p class="err">${err}</p>`;
      document.getElementById('leftbody').innerHTML = h; return;
    }
  }
  const bodiless = !u.cell;
  const canAppear = bodiless && (curMoves()||[]).length > 0;
  h += `<div class="step">1 · ${u.name}${bodiless?(canAppear?' · take flesh':''):' · move'}</div>`;
  if (!draft.destination && !draft.held){
    h += canAppear
      ? `<p class="note">Click a highlighted square beside its host to <b>take flesh</b> there — then it acts as normal. <b>Enter</b> alone stays a ghost.</p>`
      : bodiless
      ? `<p class="note">${u.name} has no body to move — press <b>Enter</b> to go on to its action.</p>`
      : `<p class="note">Arrows or click to aim · <b>Enter</b> locks it in (Enter alone holds).</p>
          <button class="btn primary" onclick="confirmMove()">${draft.tentative?(bodiless?'Take flesh here':'Confirm move'):(bodiless?'Stay a ghost':'Confirm — hold position')}</button>`;
    for (const a of curActions().filter(a=>a.self_move)){
      const dis = a.affordable===false ? 'disabled' : '';
      h += `<button class="btn" ${dis} onclick="chooseSelfMove('${a.key}')">
              ${a.name}<span class="cost">${a.ap_cost} AP</span>
              <small>Moves ${u.name} itself — pick this instead of a move.</small></button>`;
    }
  } else {
    const staying = draft.held || (draft.destination && u.cell && eq(draft.destination, u.cell));
    h += `<button class="btn on" onclick="changeMove()">
            ${staying?'Holding position':'Moving'} <small>Click to change</small></button>`;
  }
  if (draft.destination || draft.held){
    for (const ch of curChoices()){
      const got = (draft.choices||{})[ch.key];
      h += `<div class="step">Free · ${ch.name}</div><p class="note">${ch.text}</p>`;
      for (const id of ch.options){
        const a = (S.units||[]).find(x=>x.id===id) || {};
        h += `<button class="btn ${got===id?'on':''}" onclick="pickChoice('${ch.key}',${id})">
               ${a.name||('#'+id)} <small>AP ${a.ap}/${a.max_ap}</small></button>`;
      }
    }
    h += `<div class="step">2 · Action</div>`;
    for (const a of curActions()){
      const dis = a.affordable===false ? 'disabled' : '';
      h += `<button class="btn ${draft.actionKey===a.key?'on':''}" ${dis} onclick="chooseAction('${a.key}')">
             ${a.name}${a.ap_cost?`<span class="cost">${a.ap_cost} AP</span>`:''}
             ${a.blocked?`<small class="blocked">${a.blocked}</small>`:(a.text?`<small>${a.text}</small>`:'')}</button>`;
    }
    if (act) h += targetingHTML(act);
    const last = isGang() && gangPending().length===1;
    const label = !isGang() ? 'Seal order'
                : last ? (isLinked() ? 'Seal both attacks' : 'Seal the gang’s orders')
                : (isLinked() ? `Lock in ${u.name}’s attack` : 'Lock this goblin in');
    h += `<button class="btn primary" ${orderReady()?'':'disabled'} onclick="sealFromKeyboard()">${label} — <b>Enter</b></button>`;
  }
  h += `<p class="err">${err}</p>`;
  document.getElementById('leftbody').innerHTML = h;
}

function gangActionLabel(m, action){
  if (!action || action.key==='none') return 'holds';
  if (action.key==='attack') return 'attacks';
  const a = ((m && m.actions)||[]).find(x=>x.key===action.key);
  return a ? a.name : action.key;
}
function linkedHeaderHTML(){
  const ms = linkedOrder(), pos = placedPos();
  const rows = ms.map(mm => `<div class="gang-row"><b>${mm.name}</b>
      <small>${pos[mm.entity] ? 'placed' : 'not placed yet'}</small></div>`).join('');
  if (draft.stage!=='act'){
    const g = linkedAt(draft.posIdx||0);
    let h = `<div class="step">1 · Position</div>`;
    h += g
      ? `<p class="note">Click a highlighted square to place <b>${g.name}</b>.${
           g.move_anchor ? ' It can only go beside the head.'
                         : ' The tail then goes beside wherever it lands.'}</p>`
      : `<p class="note">Both halves placed — <b>Enter</b> confirms, then each one aims.</p>`;
    h += `<div class="gang-done">${rows}</div>`;
    if (placedAll())
      h += `<button class="btn primary" onclick="confirmMove()">Confirm position</button>`;
    if (Object.keys(pos).length)
      h += `<button class="btn" onclick="resetLinked()">Start the position again</button>`;
    h += `<button class="btn" onclick="dropGang()">← Order a different hero instead</button>`;
    return h;
  }
  let h = `<div class="step">Position · settled</div><div class="gang-done">${rows}</div>`;
  h += `<button class="btn" onclick="resetLinked()">Change position</button>`;
  h += `<div class="step">Attacks · ${gangOrders().length}/${ms.length}</div>`;
  return h;
}

function gangHeaderHTML(){
  const done = gangOrders(), pending = gangPending(), all = gangMembers();
  let h = `<div class="step">Gang turn · ${done.length}/${all.length} ordered</div>`;
  h += `<p class="note">Every living goblin acts this turn, in the order you pick them.
        Click a goblin on the board or below to give it its orders — nothing is sent until all ${all.length} are set.</p>`;
  if (done.length){
    h += `<div class="gang-done">` + done.map((o,i)=>{
      const m = gangMember(o.entity) || {};
      return `<div class="gang-row"><span class="n">${i+1}</span>
        <b>${m.name||''}</b><small>${o.destination?'moves':'holds'} · ${gangActionLabel(m,o.action)}</small></div>`;
    }).join('') + `</div>`;
    h += `<button class="btn" onclick="resetGang()">Clear the gang’s orders</button>`;
  }
  h += `<button class="btn" onclick="dropGang()">← Order a different hero instead</button>`;
  for (const m of pending){
    h += `<button class="btn ${draft.entity===m.entity?'on':''}" onclick="pickGoblin(${m.entity})">
            ${m.name} <small>AP ${m.ap}</small></button>`;
  }
  return h;
}

function targetingHTML(act){
  const t = act.targeting;
  if (t.kind==='none') return '';
  if (t.kind==='weapon'){
    let h = `<div class="step">2 · Weapon</div>
      <p class="note">Pick a weapon — it sets this turn's attack and stance.</p>`;
    for (const w of (act.weapons||[])){
      h += `<button class="btn ${draft.weapon===w.key?'on':''}" onclick="chooseWeapon('${w.key}')">
              ${w.name}<small>${w.text}</small></button>`;
    }
    const w = currentWeapon();
    if (w){
      h += `<div class="step">3 · Target</div>`;
      if (w.mode==='cells'){
        const n = (draft.shots[0]||[]).length;
        h += `<p class="note">Mark up to ${w.cells} cells within ${w.range}, then <b>Enter</b>. You pick the enemy hit after everyone moves.</p>
              <button class="btn on">Cells <span class="cost">${n}/${w.cells}</span></button>`;
      } else if (w.mode==='row'){
        h += `<p class="note">Hits every enemy in your row — press <b>Enter</b> to swing.</p>`;
      } else if (w.mode==='surround8'){
        h += `<p class="note">Strikes one enemy among the 8 cells around you (picked after moves) — press <b>Enter</b>.</p>`;
      }
    }
    return h;
  }
  let h = `<div class="step">3 · Target</div>`;
  if (t.kind==='cells'){
    if (S.mode==='self'){
      return `<div class="step">3 · Target</div><p class="note">Solo mode aims for you — grids are chosen at random within range. Press <b>Enter</b> to fire.</p>`;
    }
    h += `<p class="note">Mark up to ${t.count} cells within ${t.range}, then <b>Enter</b> to register (no marks = hold). You pick which enemy is hit after everyone moves.</p>`;
    for (let i=0;i<t.shots;i++){
      const n=(draft.shots[i]||[]).length;
      h += `<button class="btn ${draft.shotIndex===i?'on':''}" onclick="draft.shotIndex=${i};render()">
              ${t.shots>1?`Shot ${i+1}`:'Cells'} <span class="cost">${n}/${t.count}</span>
              ${t.shots>1&&i===1?'<small>Half damage</small>':''}</button>`;
    }
  } else if (t.kind==='cone'){
    const arcs = coneArcs();
    let h = `<div class="step">3 · Spread</div>
      <p class="note">Sprays the three squares in an arc that way. Click one on the board, or pick here.</p>`;
    for (const a of arcs){
      h += `<button class="btn ${draft.direction===a.dir?'on':''}" onclick="draft.direction='${a.dir}';err='';render()">
              ${DIRS[a.dir]||a.dir}<span class="cost">${a.hits} in reach</span></button>`;
    }
    return h;
  } else if (t.kind==='area'){
    const hit = areaCells(act).filter(c => {
      const v = unitAt(c); return v && v.side!==SIDE;
    }).length;
    return `<div class="step">3 · Sweep</div>
      <p class="note">Catches every enemy standing beside it — no aiming.
      ${hit?`<b>${hit}</b> in reach right now.`:'Nothing in reach from there.'}
      Press <b>Enter</b> to swing.</p>`;
  } else if (t.kind==='two_units'){
    const picked = (draft.pair||[]).map(id => (S.units||[]).find(u=>u.id===id)).filter(Boolean);
    let h = `<div class="step">3 · Swap</div>
      <p class="note">Click two units on the board — either side, anywhere. They trade places as everyone moves, so a hero dragged into a marked square takes what was aimed there.</p>`;
    for (let i = 0; i < 2; i++){
      const u = picked[i];
      h += `<button class="btn ${u?'on':''}" ${u?`onclick="draft.pair.splice(${i},1);render()"`:''}>
              ${u ? u.name : `— pick unit ${i+1} —`}</button>`;
    }
    return h;
  } else if (t.kind==='unit'){
    const want = unitCount(), got = namedUnits();
    h += `<p class="note">${want>1?`Name ${want} enemies`:'Choose one enemy'}${
            got.length<want?' — click on the board':''}. ${
            want>1?'Both take the full blow.':'This lands wherever it moves.'} ${
            got.length}/${want} named.</p>`;
    for (const u of foeUnits().filter(u=>u.alive)){
      h += `<button class="btn ${got.includes(u.id)?'on':''}" onclick="nameUnit(${u.id});err='';render()">
              ${u.name}</button>`;
    }
  } else if (t.kind==='lane'){
    const shots = laneShots();
    h += `<div class="step">3 · Lane</div>`;
    h += shots.length
      ? `<p class="note">Hits the first enemy down that lane — damage is how far away it is.${
           draft.direction ? '' : ' <b>Pick one</b> (or click it on the board).'}</p>`
      : `<p class="note">No shot from here: no enemy in this row or column, or your own line is in the way. <b>Enter</b> holds.</p>`;
    for (const l of shots){
      h += `<button class="btn ${draft.direction===l.dir?'on':''}" onclick="draft.direction='${l.dir}';err='';render()">
              ${DIRS[l.dir]||l.dir}<span class="cost">${l.damage} dmg</span>
              <small>${l.target.name} · ${l.distance} squares away</small></button>`;
    }
  } else if (t.kind==='shape'){
    h += `<div class="step">3 · Shape</div>`;
    h += `<p class="note">Centred on wherever you end up standing.${
             draft.direction ? '' : ' <b>Pick one</b> (or click a square on the board).'}</p>`;
    for (const sh of shapeOptions()){
      h += `<button class="btn ${draft.direction===sh.dir?'on':''}" onclick="draft.direction='${sh.dir}';err='';render()">
              ${SHAPE_LABEL[sh.dir]||sh.dir}
              <span class="cost">${sh.hits} caught</span></button>`;
    }
  } else if (t.kind==='direction' && t.choices){
    h += `<div class="step">3 · Lane</div>`;
    h += t.choices.length
      ? `<p class="note">Three squares down the lane, trampling whatever is in the two you cross.</p>`
      : `<p class="note">No lane worth charging from here — nothing to trample and nowhere to land.</p>`;
    for (const ch of t.choices){
      const n = ch.victims.length;
      const where = ch.landing ? 'charges through' : 'holds ground';
      h += `<button class="btn ${draft.direction===ch.dir?'on':''}" onclick="draft.direction='${ch.dir}';err='';render()">
              ${DIRS[ch.dir]||ch.dir}<span class="cost">${where}</span>
              <small>${n?`tramples ${n} ${n===1?'enemy':'enemies'} for ${ch.damage} each`:'nobody in the way'}${ch.landing?'':' · third square is taken'}</small></button>`;
    }
  } else if (t.kind==='direction'){
    h += `<p class="note">Choose which neighbouring column joins your own.</p>`;
    for (const d of t.options){
      h += `<button class="btn ${draft.direction===d?'on':''}" onclick="draft.direction='${d}';err='';render()">
              ${d==='forward'?'Forward — toward the enemy':'Backward — toward your own line'}</button>`;
    }
  } else if (t.kind==='any_cell'){
    h += `<p class="note">Click any cell on the board to set it alight.</p>`;
    if (draft.cell) h += `<button class="btn on">That square is marked to burn</button>`;
  } else if (t.kind==='ally'){
    h += `<p class="note">Choose an ally to heal (yourself included). <b>Enter</b> registers.</p>`;
    for (const u of myUnits().filter(u=>u.alive)){
      h += `<button class="btn ${draft.target===u.id?'on':''}" onclick="draft.target=${u.id};err='';render()">
              ${u.name} <span class="cost">${u.hp}/${u.max_hp} HP</span></button>`;
    }
  } else if (t.kind==='magnitude'){
    const su = selectedUnit();
    const maxX = Math.max(1, Math.min(su.hp-1, su.max_hp-1));
    if (draft.amount==null || draft.amount>maxX) draft.amount = Math.min(draft.amount||1, maxX);
    h += `<p class="note">Sacrifice max HP for the same amount of permanent attack, then <b>Enter</b>.</p>
          <input type="range" min="1" max="${maxX}" value="${draft.amount}" style="width:100%"
                 oninput="magUpdate(this.value)">
          <p class="note">Sacrifice <b id="magv">${draft.amount}</b> → <span id="magres">+${draft.amount} atk, ${su.hp-draft.amount}/${su.max_hp-draft.amount} HP</span></p>`;
  }
  return h;
}
function magUpdate(v){
  draft.amount = +v;
  const su = selectedUnit();
  document.getElementById('magv').textContent = v;
  document.getElementById('magres').textContent = `+${v} atk, ${su.hp - (+v)}/${su.max_hp - (+v)} HP`;
}

function renderMoveChoice(){
  // Everyone has stopped moving, nothing has been struck yet, and an ability wants
  // to be told where it puts somebody (刺客 choosing which side of its mark).
  document.getElementById('leftheading').textContent = 'Where do you appear?';
  const t = S.move_choice && S.move_choice.task;
  let h = '';
  if (t){
    h += `<div class="step">${t.name}</div><p class="note">${t.text}</p>`;
    h += `<p class="note">Click one of the highlighted squares, then <b>Enter</b>.</p>`;
    h += aimedStep()
      ? `<button class="btn primary" onclick="confirmStep()">Appear here — <b>Enter</b></button>`
      : `<p class="note">Nothing aimed yet.</p>`;
  } else {
    h += `<div class="waiting">Waiting for the other seat</div>`;
  }
  h += `<p class="err">${err}</p>`;
  document.getElementById('leftbody').innerHTML = h;
}

function renderFollowup(){
  // The exchange is over and something wants a decision before the next pair is
  // picked (男枪 stepping after a hit).
  document.getElementById('leftheading').textContent = 'After the exchange';
  const f = S.followup, t = f && f.task;
  let h = '';
  if (t){
    h += `<div class="step">${t.name}</div><p class="note">${t.text}</p>`;
    if (t.kind==='confirm'){
      h += `<button class="btn primary" onclick="cmd({cmd:'followup', confirm:true})">Yes — <b>Enter</b></button>`;
      h += `<button class="btn" onclick="cmd({cmd:'followup'})">No, save it</button>`;
      h += `<p class="err">${err}</p>`;
      document.getElementById('leftbody').innerHTML = h; return;
    }
    if (t.kind==='unit'){
      // Naming a hero is a single click — there is no square to confirm.
      h += `<p class="note">Click one of the highlighted enemies.</p>`;
      for (const id of t.options){
        const u = (S.units||[]).find(x=>x.id===id) || {};
        h += `<button class="btn" onclick="cmd({cmd:'followup', entity:${id}})">
                ${u.name||('#'+id)}</button>`;
      }
      h += `<p class="err">${err}</p>`;
      document.getElementById('leftbody').innerHTML = h; return;
    }
    h += `<p class="note">Click one of the highlighted squares, then <b>Enter</b>.</p>`;
    h += aimedStep()
      ? `<button class="btn primary" onclick="confirmStep()">Confirm — <b>Enter</b></button>`
      : `<p class="note">Nothing aimed yet.</p>`;
    if (t.optional)
      h += `<button class="btn" onclick="stepPick=null;cmd({cmd:'followup'})">Stay put — <b>Enter</b> alone</button>`;
  } else {
    h += `<div class="waiting">Waiting for the other seat</div>`;
  }
  h += `<p class="err">${err}</p>`;
  document.getElementById('leftbody').innerHTML = h;
}

function renderVictim(){
  document.getElementById('leftheading').textContent = 'Target choice';
  let h='';
  if (S.victim.needed){
    h += `<p class="note">More than one enemy is standing in your marked cells. Choose which one takes the hit.</p>`;
    for (const id of S.victim.options){
      const u=(S.units||[]).find(x=>x.id===id);
      h += `<button class="btn" onclick="cmd({cmd:'victim', entity:${id}})">
              ${u.name} <small>${u.name_en}</small>
              <span class="cost">${u.hp} HP</span></button>`;
    }
  } else {
    h += `<div class="waiting">Waiting for the other seat</div>`;
  }
  h += `<p class="err">${err}</p>`;
  document.getElementById('leftbody').innerHTML=h;
}

function renderOver(){
  document.getElementById('leftheading').textContent = 'Result';
  const w = S.winner;
  const cls = w==='draw' ? 'draw' : (w===SIDE ? 'win' : 'lose');
  const txt = w==='draw' ? 'Mutual destruction' : (w===SIDE ? 'Your force holds the field' : 'Your force is destroyed');
  document.getElementById('leftbody').innerHTML =
    `<div class="banner ${cls}">${txt}</div><button class="btn primary" onclick="resetMatch()">New match</button>`;
}

function heroDetailHTML(u){
  const h = codex[u.key] || {};
  const a = u.attack || {};   // stats come from the unit itself — robust if codex hasn't loaded
  const pct = Math.max(0, Math.round(100*u.hp/u.max_hp));
  const tile = (k,v) => `<div class="hd-tile"><div class="k">${k}</div><div class="v">${v}</div></div>`;
  let tiles = tile('Attack', u.atk) + tile('Move', u.move);
  if (a.mode==='cell_locked') tiles += tile('Grids', u.grid ?? a.cells) + tile('Range', u.rng);
  else if (a.mode==='line_locked') tiles += tile('Reach', 'row & column') + tile('Damage', 'distance');
  else if (a.mode==='area_locked') tiles += tile('Reach', 'all 8 around') + tile('Hits', 'everyone');
  else if (a.mode==='cone_locked') tiles += tile('Reach', '3-square arc') + tile('Hits', 'everyone in it');
  else if (a.mode==='weapon') tiles += tile('Weapon', 'varies');
  else tiles += tile('Reach', 'any enemy');
  const status = (u.status||[]).map(s =>
    `<div class="hd-status"><b>${s.badge} ${s.label}</b>${s.text}</div>`).join('');
  return `<div class="hd">
    <div class="hd-name">${u.name}<span class="en">${u.name_en}</span></div>
    <div class="hd-hp side${u.side}">
      <b>${u.hp}/${u.max_hp}</b>
      <div class="bar"><i class="${pct<40?'hurt':''}" style="width:${pct}%"></i></div>
    </div>
    ${status}
    <div class="hd-grid">${tiles}</div>
    ${(h.traits||[]).map(t=>`<div class="hd-trait"><b>${t.name}${t.ap_cost?` · ${t.ap_cost} AP`:''}</b>${t.text}</div>`).join('')}
  </div>`;
}
function renderRHS(){
  const head=document.getElementById('rhshead'), body=document.getElementById('rhsbody');
  if (inspected!=null){
    const u=(S.units||[]).find(x=>x.id===inspected);
    if (u){ head.textContent='Hero'; body.className='body'; body.innerHTML=heroDetailHTML(u); return; }
    inspected=null;
  }
  head.textContent='Field log'; body.className='body log';
  body.innerHTML=(S.log||[]).map(l=>{
    const isRound = l.text.startsWith('—');
    return `<div class="${isRound?'rd':(l.quiet?'quiet':'')}">${l.text}</div>`;
  }).join('');
  body.scrollTop = body.scrollHeight;
}

document.addEventListener('dragend', endDrag);
document.addEventListener('keydown', onKey);
loadCodex().then(() => poll(true));
setInterval(poll, 1200);
document.addEventListener('visibilitychange', () => { if (!document.hidden) poll(true); });
