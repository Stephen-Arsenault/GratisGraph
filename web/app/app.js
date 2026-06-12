// GratisGraph web UI — wires the GTS engine to the local server.
// The preview is GTS-DRIVEN: we render the .m20 we built, parsed back, so the
// operator sees exactly what the machine will do.
import { fromGcode, gcodeToOptimisedStrokes, strokesToGgs, ggsToStrokes } from './engine/pipeline.js';
import { buildGts } from './engine/gts.js';
import { parseGts } from './engine/preview.js';
import { optimiseStrokes } from './engine/optimise.js';
import { parseDxf } from './engine/dxf.js';
import { circleStroke, rectStroke, lineStroke } from './engine/shapes.js';
import { ttfTextToStrokes } from './engine/ttf.js';
import { hersheyTextToStrokes } from './engine/hershey.js';
import {
  newScene, makeObject, identityTransform, worldStrokes,
  strokesBounds, flattenScene, quantiseStrokes,
} from './engine/scene.js';

const $ = (id) => document.getElementById(id);
const SERVER = `http://127.0.0.1:8020`;

let scene = newScene();          // the editable layout (source of truth)
let activeTool = 'select';       // 'select' | 'text' — drives the settings card
let selectedId = null;           // selected object id (edit mode)
let mode = 'edit';               // 'edit' | 'prepare'
let m20 = null;                  // built binary (prepare mode)
const SHARED_GRID = { W: 100, H: 100, M: 10 };

// ── toasts ──
function toast(msg, kind = '') {
  const el = document.createElement('div');
  el.className = `toast ${kind}`;
  el.textContent = msg;
  $('toasts').append(el);
  setTimeout(() => el.remove(), 4000);
}

// ── server polling ──
async function pollServer() {
  try {
    const r = await fetch(`${SERVER}/`, { signal: AbortSignal.timeout(2000) });
    const d = await r.json();
    $('serverDot').className = 'dot on';
    $('serverText').textContent = `server ${d.version}`;
    if (d.connected) pollMachine();
    else setMachine(null);
  } catch {
    $('serverDot').className = 'dot';
    $('serverText').textContent = 'server offline';
    setMachine(null);
  }
}
async function pollMachine() {
  const token = $('token').value.trim();
  if (!token) { setMachine('unpaired'); return; }
  try {
    const r = await fetch(`${SERVER}/machine`, { headers: { Authorization: `Bearer ${token}` } });
    if (r.status === 401) { setMachine('rejected'); return; }
    const m = await r.json();
    if (m && m.model) savePaired(token);
    setMachine(m);
  } catch { setMachine(null); }
}
function setMachine(m) {
  const pill = $('machinePill'), dot = $('machineDot'), txt = $('machineText');
  const cta = (m === 'unpaired' || m === 'rejected');
  pill.classList.toggle('cta', cta);
  pill.classList.toggle('bad', m === 'rejected');
  if (m === 'unpaired') { dot.className = 'dot busy'; txt.textContent = 'get your pairing code'; showPairingField(true); }
  else if (m === 'rejected') { dot.className = 'dot busy'; txt.textContent = 'pairing code rejected'; showPairingField(true); }
  else if (m && m.model) { dot.className = 'dot on'; txt.textContent = m.model; showPairingField(false); }
  else { dot.className = 'dot'; txt.textContent = 'no machine'; }
  refreshSendButton();
}
function showPairingHelp() {
  toast('Start the server (or open the GratisGraph tray). It prints a pairing code in the terminal — paste it into the field on the left.', '');
  $('token').focus();
}


// ── scene autosave (survives refresh + browser restart) ──
const AUTOSAVE_KEY = 'gg-scene-v1';
let saveTimer = null;
function sceneToDoc() {
  return {
    format: 'gratisgraph-scene', version: 1, units: 'mm', frame: 'machine',
    settings: {
      speed: +$('speed').value, depth: +$('depth').value,
      optimise: $('optimise').checked, flipy: $('flipy').checked,
    },
    objects: scene.objects.map((o) => ({
      name: o.name, transform: o.transform, reference: o.reference || false,
      baseStrokes: o.baseStrokes.map((s) => ({ start: s.start, deltas: s.deltas })),
    })),
  };
}
function autosave() {
  // debounce: dragging fires continuously
  clearTimeout(saveTimer);
  saveTimer = setTimeout(() => {
    try {
      localStorage.setItem(AUTOSAVE_KEY, JSON.stringify(sceneToDoc()));
    } catch (e) {
      // quota exceeded (very large scene) — warn once, keep working in memory
      toast('Scene too large to auto-save — it will not survive a refresh.', 'warn');
    }
  }, 400);
}
function restoreAutosave() {
  let doc;
  try { doc = JSON.parse(localStorage.getItem(AUTOSAVE_KEY) || 'null'); } catch { return false; }
  if (!doc || !doc.objects || !doc.objects.length) return false;
  if (doc.settings) {
    $('speed').value = doc.settings.speed ?? 25;
    $('depth').value = doc.settings.depth ?? 30;
    $('optimise').checked = doc.settings.optimise !== false;
    $('flipy').checked = !!doc.settings.flipy;
  }
  scene = newScene();
  for (const o of doc.objects) {
    const no = makeObject(o.name || 'object',
      o.baseStrokes.map((s) => ({ start: s.start, deltas: s.deltas })),
      o.transform || identityTransform());
    no.reference = !!o.reference;
    scene.objects.push(no);
  }
  selectedId = scene.objects.length ? scene.objects[scene.objects.length - 1].id : null;
  return true;
}
function clearAutosave() { try { localStorage.removeItem(AUTOSAVE_KEY); } catch {} }

// ── building objects from files ──
function strokesFromFile(text, kind) {
  const opts = {
    speed: +$('speed').value, pz: +$('depth').value,
    flipY: $('flipy').checked, optimise: false,   // edit mode keeps raw geometry; optimise at Prepare
  };
  if (kind === 'ggs') {
    const doc = JSON.parse(text);
    if (doc.format === 'gratisgraph-scene') return null; // handled by loadScene
    return ggsToStrokes(doc);
  }
  if (kind === 'dxf') {
    const r = parseDxf(text);
    for (const w of r.warnings) toast(w, 'warn');
    return r.strokes;
  }
  return gcodeToOptimisedStrokes(text, opts);
}

function addFile(file) {
  const lower = file.name.toLowerCase();
  const kind = lower.endsWith('.ggs') ? 'ggs' : lower.endsWith('.dxf') ? 'dxf' : 'gcode';
  const name = file.name.replace(/\.[^.]+$/, '');
  const reader = new FileReader();
  reader.onload = () => {
    try {
      const text = reader.result;
      if (kind === 'ggs' && JSON.parse(text).format === 'gratisgraph-scene') {
        loadScene(JSON.parse(text));
      } else {
        const baseStrokes = strokesFromFile(text, kind);
        if (!baseStrokes || !baseStrokes.length) { toast(`${file.name} has no strokes.`, 'warn'); return; }
        const obj = makeObject(name, baseStrokes);
        scene.objects.push(obj);
        selectedId = obj.id;
        toMode('edit');
        autosave();
        toast(`Added ${name}`, 'ok');
      }
    } catch (e) { toast(`Couldn't read ${file.name}: ${e.message}`, 'warn'); }
  };
  reader.readAsText(file);
}

function loadScene(doc) {
  // Rehydrate a saved scene into editable objects.
  scene = newScene();
  for (const o of (doc.objects || [])) {
    const no = makeObject(o.name || 'object',
      o.baseStrokes.map((s) => ({ start: s.start, deltas: s.deltas })),
      o.transform || identityTransform());
    no.reference = !!o.reference;
    scene.objects.push(no);
  }
  selectedId = scene.objects.length ? scene.objects[scene.objects.length - 1].id : null;
  toMode('edit');
  autosave();
  toast('Scene loaded', 'ok');
}

// ── text: bundled fonts fetched once, custom .ttf held in memory ──
const FONT_FILES = {
  serif:  'fonts/Tinos-Regular.ttf',
  sans:   'fonts/Arimo-Regular.ttf',
  script: 'fonts/GreatVibes-Regular.ttf',
  gorton: 'fonts/OpenGorton-Regular.ttf',
  engrave: 'fonts/futural.jhf',
};
const fontCache = new Map();
let customFont = null;   // { name, data: Uint8Array }

async function loadFontData(key) {
  if (key === 'custom') {
    if (!customFont) throw new Error('Choose a .ttf file first.');
    return customFont.data;
  }
  if (fontCache.has(key)) return fontCache.get(key);
  const r = await fetch(FONT_FILES[key]);
  if (!r.ok) throw new Error(`Couldn't load font (${r.status})`);
  const data = key === 'engrave' ? await r.text() : new Uint8Array(await r.arrayBuffer());
  fontCache.set(key, data);
  return data;
}

async function addText() {
  const text = $('textValue').value;
  if (!text.trim()) { toast('Type some text first.', 'warn'); return; }
  const key = $('textFont').value;
  const size = Math.min(80, Math.max(1, +$('textSize').value || 10));
  try {
    const data = await loadFontData(key);
    const r = key === 'engrave'
      ? hersheyTextToStrokes(data, text, size, 0, 0)
      : ttfTextToStrokes(data, text, size, 0, 0);
    for (const w of r.warnings) toast(w, 'warn');
    if (!r.strokes.length) { toast('No engraveable strokes in that text.', 'warn'); return; }
    // generate at origin, then translate to bed centre via the transform
    const txUnits = Math.round((50 - r.widthMm / 2) * 100);
    const obj = makeObject(text.length > 18 ? text.slice(0, 18) + '…' : text, r.strokes,
                           { tx: txUnits, ty: 5000 - Math.round(size * 35), rotation: 0, scale: 1 });
    scene.objects.push(obj);
    selectedId = obj.id;
    activeTool = 'select';
    toMode('edit');
    autosave();
  } catch (e) { toast(e.message, 'warn'); }
}

const shapeCounts = { circle: 0, square: 0, line: 0 };
function addShape(kind) {
  let strokes, name;
  if (kind === 'circle') { strokes = [circleStroke(20, 50, 50)]; name = `circle ${++shapeCounts.circle}`; }
  else if (kind === 'square') { strokes = [rectStroke(20, 20, 50, 50)]; name = `square ${++shapeCounts.square}`; }
  else { strokes = [lineStroke(20, 50, 50)]; name = `line ${++shapeCounts.line}`; }
  const obj = makeObject(name, strokes);
  scene.objects.push(obj);
  selectedId = obj.id;
  toMode('edit');
  autosave();
}

function newSceneAction() {
  scene = newScene();
  selectedId = null;
  m20 = null;
  clearAutosave();
  toMode('edit');
}

// ── mode switching ──
function toMode(m) {
  mode = m;
  const editing = (m === 'edit');
  $('modeLabel').textContent = editing ? 'Edit · arrange objects' : 'Prepare · machine preview';
  $('modeLabel').className = `mode-label ${editing ? 'edit' : 'prepare'}`;
  const pb = $('prepareBtn');
  if (editing) {
    pb.textContent = 'Prepare for engraving →';
    pb.classList.remove('primary'); pb.classList.add('ghost');
    pb.disabled = scene.objects.length === 0;
    pb.onclick = prepareForEngraving;
  } else {
    pb.textContent = '← Back to edit';
    pb.classList.remove('ghost'); pb.classList.add('ghost');
    pb.disabled = false;
    pb.onclick = () => toMode('edit');
  }
  renderObjectList();
  if (editing) renderEdit(); else renderPrepare();
  refreshSendButton();
}

function prepareForEngraving() {
  // quantise after transforms: rotate/scale make fractional units; the binary
  // needs integer deltas with no accumulated drift
  const flat = quantiseStrokes(flattenScene(scene));
  if (!flat.length) { toast('Nothing to engrave — add a file first.', 'warn'); return; }
  // optimise + build at the commit point
  const opts = { speed: +$('speed').value, pz: +$('depth').value, optimise: $('optimise').checked };
  let strokes = flat;
  if ($('optimise').checked) strokes = optimiseStrokes(flat, {});
  m20 = buildGts(strokes, opts);
  toMode('prepare');
}

// ── shared bed chrome (grid + labels), returns SVG string ──
function cssVar(name) { return getComputedStyle(document.documentElement).getPropertyValue(name).trim(); }
function bedChrome() {
  const { W, H, M } = SHARED_GRID;
  const cGrid = cssVar('--line'), cFaint = cssVar('--ink-faint');
  $('bed').setAttribute('viewBox', `${-M} ${-M} ${W + 2 * M} ${H + 2 * M}`);
  let svg = `<rect x="0" y="0" width="${W}" height="${H}" fill="none" stroke="${cGrid}" stroke-width="0.18"/>`;
  let minor = `<g stroke="${cGrid}" stroke-width="0.04" opacity="0.45">`;
  let major = `<g stroke="${cGrid}" stroke-width="0.1" opacity="0.85">`;
  for (let i = 1; i < W; i++) { if (i % 10 === 0) major += `<line x1="${i}" y1="0" x2="${i}" y2="${H}"/>`; else minor += `<line x1="${i}" y1="0" x2="${i}" y2="${H}"/>`; }
  for (let j = 1; j < H; j++) { if (j % 10 === 0) major += `<line x1="0" y1="${j}" x2="${W}" y2="${j}"/>`; else minor += `<line x1="0" y1="${j}" x2="${W}" y2="${j}"/>`; }
  svg += minor + `</g>` + major + `</g>`;
  let labels = `<g fill="${cFaint}" font-family="var(--mono)" font-size="2.6">`;
  for (let i = 0; i <= W; i += 10) labels += `<text x="${i}" y="${H + 4.6}" text-anchor="middle">${i}</text>`;
  for (let j = 0; j <= H; j += 10) labels += `<text x="${-2}" y="${H - j + 0.9}" text-anchor="end">${j}</text>`;
  svg += labels + `</g>`;
  return svg;
}

/** pointer event -> machine coords (mm), inverting the viewBox + Y-flip. */
function bedPointMm(ev) {
  const rect = $('bed').getBoundingClientRect();
  const { W, H, M } = SHARED_GRID;
  const x = -M + (ev.clientX - rect.left) * ((W + 2 * M) / rect.width);
  const ySvg = -M + (ev.clientY - rect.top) * ((H + 2 * M) / rect.height);
  return [x, H - ySvg];
}

// machine(mm) -> svg point string, applying the Y-flip (origin front-left)
function pp(x, y) { return `${x / 100},${SHARED_GRID.H - y / 100}`; }
function mmflip(yUnits) { return SHARED_GRID.H - yUnits / 100; }

// ── EDIT render: objects as strokes + draggable bounding boxes ──
function renderEdit() {
  const bed = $('bed');
  const cCut = cssVar('--cut'), cInk = cssVar('--ink'), cBox = cssVar('--ink-faint'), cRef = cssVar('--rapid');
  let svg = bedChrome();
  for (const obj of scene.objects) {
    const ws = worldStrokes(obj);
    const selected = obj.id === selectedId;
    svg += `<g class="obj" data-id="${obj.id}" stroke="${obj.reference ? cRef : cCut}" stroke-width="${selected ? 0.32 : 0.26}" fill="none" stroke-linecap="round" stroke-linejoin="round" opacity="${obj.reference ? 0.6 : (selected ? 1 : 0.85)}">`;
    for (const s of ws) {
      let x = s.start[0], y = s.start[1];
      let pts = pp(x, y);
      for (const [dx, dy] of s.deltas) { x += dx; y += dy; pts += ' ' + pp(x, y); }
      svg += `<polyline points="${pts}"/>`;
    }
    svg += `</g>`;
    // bounding box (draggable handle)
    const [bx0, by0, bx1, by1] = strokesBounds(ws);
    const pad = 100; // 1mm pad in units
    const X = (bx0 - pad) / 100, Y0 = mmflip(by1 + pad), Wd = (bx1 - bx0 + 2 * pad) / 100, Hd = (by1 - by0 + 2 * pad) / 100;
    svg += `<rect class="bbox" data-id="${obj.id}" x="${X}" y="${Y0}" width="${Wd}" height="${Hd}" fill="transparent" stroke="${cBox}" stroke-width="${selected ? 0.25 : 0.15}" stroke-dasharray="1 1" opacity="${selected ? 0.85 : 0.4}" style="cursor:move"/>`;
    if (selected) {
      const midX = X + Wd / 2;
      svg += `<line x1="${midX}" y1="${Y0}" x2="${midX}" y2="${Y0 - 3.2}" stroke="${cBox}" stroke-width="0.12"/>`;
      svg += `<circle class="rot-handle" data-id="${obj.id}" cx="${midX}" cy="${Y0 - 4.4}" r="1.5" fill="${cssVar('--panel')}" stroke="${cCut}" stroke-width="0.25" style="cursor:grab"/>`;
      svg += `<rect class="scale-handle" data-id="${obj.id}" x="${X + Wd - 1.2}" y="${Y0 + Hd - 1.2}" width="2.4" height="2.4" fill="${cssVar('--panel')}" stroke="${cCut}" stroke-width="0.25" style="cursor:nwse-resize"/>`;
    }
  }
  bed.innerHTML = svg + headLayer();
  attachDrag();
  const n = scene.objects.length;
  $('jobInfo').textContent = n ? `${n} object${n === 1 ? '' : 's'}` : '';
  $('bounds').textContent = '';
}

// ── PREPARE render: GTS-driven, parsed back from the built binary ──
function renderPrepare() {
  if (!m20) return;
  const job = parseGts(m20);
  const bed = $('bed');
  const cCut = cssVar('--cut'), cRapid = cssVar('--rapid'), cInk = cssVar('--ink');
  let svg = bedChrome();
  let g = `<g transform="translate(0,${SHARED_GRID.H}) scale(1,-1)">`;
  g += `<g stroke="${cRapid}" stroke-width="0.25" stroke-dasharray="1 1" fill="none" opacity="0.7">`;
  for (const [a, b] of job.rapids) g += `<line x1="${a[0]}" y1="${a[1]}" x2="${b[0]}" y2="${b[1]}"/>`;
  g += `</g><g stroke="${cCut}" stroke-width="0.3" fill="none" stroke-linecap="round" stroke-linejoin="round">`;
  for (const s of job.strokes) g += `<polyline points="${s.points.map((p) => `${p[0]},${p[1]}`).join(' ')}"/>`;
  g += `</g><circle cx="0" cy="0" r="1.2" fill="${cCut}"/></g>`;
  bed.innerHTML = svg + g + headLayer();
  const [x0, y0, x1, y1] = job.bounds;
  $('jobInfo').textContent = `${job.strokes.length} stroke${job.strokes.length === 1 ? '' : 's'} · ${m20.length} bytes`;
  $('bounds').textContent = `${(x1 - x0).toFixed(1)} × ${(y1 - y0).toFixed(1)} mm`;
}

// ── object list (left rail) ──
function renderObjectList() {
  const ul = $('objectList');
  if (!ul) return;
  ul.innerHTML = '';
  for (const obj of scene.objects) {
    const li = document.createElement('li');
    li.className = 'obj-item' + (obj.id === selectedId ? ' sel' : '') + (obj.reference ? ' ref' : '');
    li.textContent = obj.name;
    li.onclick = () => { selectedId = obj.id; activeTool = 'select'; if (mode === 'edit') renderEdit(); renderObjectList(); $('delObjBtn').disabled = false; };
    ul.append(li);
  }
  $('delObjBtn').disabled = !selectedId || scene.objects.length === 0;
  updateObjPanel();
}

function updateObjPanel() {
  // contextual settings card: text composer > object settings > job settings
  const obj = scene.objects.find((o) => o.id === selectedId);
  const showText = activeTool === 'text' && mode === 'edit';
  const showObj = !showText && !!obj && mode === 'edit';
  $('textSettings').style.display = showText ? '' : 'none';
  $('objSettings').style.display = showObj ? '' : 'none';
  $('jobSettings').style.display = (!showText && !showObj) ? '' : 'none';
  $('toolText').classList.toggle('active', activeTool === 'text');
  $('objectsEmpty').style.display = scene.objects.length ? 'none' : '';
  if (showObj) {
    $('objRot').value = Math.round((obj.transform.rotation * 180 / Math.PI) * 10) / 10;
    $('objScale').value = Math.round(obj.transform.scale * 1000) / 1000;
    const [bx0, by0, bx1, by1] = strokesBounds(worldStrokes(obj));
    $('objW').value = Math.round((bx1 - bx0) / 10) / 10;
    $('objH').value = Math.round((by1 - by0) / 10) / 10;
    $('objRef').checked = !!obj.reference;
  }
}

function deleteSelected() {
  if (!selectedId) return;
  scene.objects = scene.objects.filter((o) => o.id !== selectedId);
  selectedId = scene.objects.length ? scene.objects[scene.objects.length - 1].id : null;
  autosave();
  toMode('edit');
}

// ── dragging objects by their bounding box ──
function attachDrag() {
  const bed = $('bed');
  bed.querySelectorAll('.bbox, .obj').forEach((el) => {
    el.addEventListener('pointerdown', (ev) => {
      const id = el.getAttribute('data-id');
      const reselect = selectedId !== id || activeTool !== 'select';
      selectedId = id;
      activeTool = 'select';
      const obj = scene.objects.find((o) => o.id === id);
      if (!obj) return;
      ev.preventDefault();
      if (reselect) { renderEdit(); renderObjectList(); }
      const startT = { ...obj.transform };
      // convert pointer movement (px) to mm via the SVG's current scale
      const rect = bed.getBoundingClientRect();
      const { W, M } = SHARED_GRID;
      const mmPerPx = (W + 2 * M) / rect.width;   // viewBox units per screen px
      const sx = ev.clientX, sy = ev.clientY;
      const onMove = (e) => {
        let dxmm = (e.clientX - sx) * mmPerPx;
        let dymm = -(e.clientY - sy) * mmPerPx;   // screen down = machine -Y
        let dux = dxmm * 100, duy = dymm * 100;   // mm -> device units
        if (!e.shiftKey) { dux = Math.round(dux / 100) * 100; duy = Math.round(duy / 100) * 100; } // snap 1mm unless Shift
        obj.transform = { ...startT, tx: startT.tx + dux, ty: startT.ty + duy };
        renderEdit();
      };
      const onUp = () => { window.removeEventListener('pointermove', onMove); window.removeEventListener('pointerup', onUp); autosave(); };
      window.addEventListener('pointermove', onMove);
      window.addEventListener('pointerup', onUp);
    });
  });

  // rotate gizmo: drag around the object's world center; 15° snap, Shift = free
  bed.querySelectorAll('.rot-handle').forEach((el) => {
    el.addEventListener('pointerdown', (ev) => {
      ev.preventDefault(); ev.stopPropagation();
      const obj = scene.objects.find((o) => o.id === el.getAttribute('data-id'));
      if (!obj) return;
      const [bx0, by0, bx1, by1] = strokesBounds(worldStrokes(obj));
      const cx = (bx0 + bx1) / 200, cy = (by0 + by1) / 200;   // units -> mm
      const [px0, py0] = bedPointMm(ev);
      const startAng = Math.atan2(py0 - cy, px0 - cx);
      const startRot = obj.transform.rotation;
      const onMove = (e) => {
        const [px, py] = bedPointMm(e);
        let rot = startRot + Math.atan2(py - cy, px - cx) - startAng;
        if (!e.shiftKey) rot = Math.round(rot / (Math.PI / 12)) * (Math.PI / 12);
        obj.transform = { ...obj.transform, rotation: rot };
        renderEdit(); updateObjPanel();
      };
      const onUp = () => { window.removeEventListener('pointermove', onMove); window.removeEventListener('pointerup', onUp); autosave(); };
      window.addEventListener('pointermove', onMove);
      window.addEventListener('pointerup', onUp);
    });
  });

  // scale gizmo: distance ratio from world center; 0.05 steps, Shift = free
  bed.querySelectorAll('.scale-handle').forEach((el) => {
    el.addEventListener('pointerdown', (ev) => {
      ev.preventDefault(); ev.stopPropagation();
      const obj = scene.objects.find((o) => o.id === el.getAttribute('data-id'));
      if (!obj) return;
      const [bx0, by0, bx1, by1] = strokesBounds(worldStrokes(obj));
      const cx = (bx0 + bx1) / 200, cy = (by0 + by1) / 200;
      const [px0, py0] = bedPointMm(ev);
      const startDist = Math.max(0.5, Math.hypot(px0 - cx, py0 - cy));
      const startScale = obj.transform.scale;
      const onMove = (e) => {
        const [px, py] = bedPointMm(e);
        let s = startScale * (Math.hypot(px - cx, py - cy) / startDist);
        if (!e.shiftKey) s = Math.round(s / 0.05) * 0.05;
        s = Math.min(50, Math.max(0.05, s));
        obj.transform = { ...obj.transform, scale: s };
        renderEdit(); updateObjPanel();
      };
      const onUp = () => { window.removeEventListener('pointermove', onMove); window.removeEventListener('pointerup', onUp); autosave(); };
      window.addEventListener('pointermove', onMove);
      window.addEventListener('pointerup', onUp);
    });
  });
}

function refreshSendButton() {
  const token = $('token').value.trim();
  const ready = mode === 'prepare' && m20 && token && $('machineDot').className.includes('on');
  $('sendBtn').disabled = !ready;
  $('saveBtn').disabled = scene.objects.length === 0;
}

let sse = null;
async function sendJob() {
  const token = $('token').value.trim();
  $('sendBtn').disabled = true;
  $('jobState').textContent = 'sending…';
  try {
    const r = await fetch(`${SERVER}/job`, {
      method: 'POST',
      headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/octet-stream' },
      body: m20,
    });
    if (!r.ok) throw new Error((await r.json()).error || r.statusText);
    const { job_id } = await r.json();
    toast('Job queued — press the green button to run.', 'ok');
    $('jobState').textContent = `queued (${job_id}) · press green`;
    ensureTelemetry(token);
  } catch (e) {
    toast(`Send failed: ${e.message}`, 'warn');
    $('jobState').textContent = 'idle';
  }
  refreshSendButton();
}

let sseToken = null;
function ensureTelemetry(token) {
  if (sse && sseToken === token) return;        // already streaming with this token
  if (sse) { sse.close(); sse = null; }
  sseToken = token;
  sse = new EventSource(`${SERVER}/telemetry?token=${encodeURIComponent(token)}`);
  sse.onmessage = (ev) => {
    let p; try { p = JSON.parse(ev.data); } catch { return; }
    if (p.type !== 'motor') return;
    const mm = (p.value / 100).toFixed(2);
    if (p.axis === 'X') { $('tx').textContent = mm; moveHead(p.value / 100, null); }
    if (p.axis === 'Y') { $('ty').textContent = mm; moveHead(null, p.value / 100); }
    if (p.axis === 'Z') { $('tz').textContent = mm; }
    if (p.axis === 'E') { $('jobState').textContent = 'running'; }
  };
  sse.onerror = () => { /* keepalive gaps fire this; let EventSource auto-reconnect */ };
}
let hx = 0, hy = 0, headVisible = false;
// A persistent red head dot, rendered in machine coords with the Y-flip so it
// lands at the live (X,Y). Re-emitted on every render so it survives mode
// switches; moveHead() updates it in place during motion.
function headLayer() {
  const y = SHARED_GRID.H - hy;   // flip machine Y into SVG space
  return `<g id="headLayer" style="display:${headVisible ? '' : 'none'}">` +
         `<circle id="headDot" cx="${hx}" cy="${y}" r="1.4" fill="#ff2d2d" stroke="#fff" stroke-width="0.25"/>` +
         `<circle cx="${hx}" cy="${y}" r="2.6" fill="none" stroke="#ff2d2d" stroke-width="0.2" opacity="0.5"/></g>`;
}
function moveHead(x, y) {
  if (x !== null) hx = x; if (y !== null) hy = y;
  headVisible = true;
  const layer = document.getElementById('headLayer');
  const dot = document.getElementById('headDot');
  if (!layer || !dot) return;
  layer.style.display = '';
  const svgY = SHARED_GRID.H - hy;
  layer.querySelectorAll('circle').forEach((c) => { c.setAttribute('cx', hx); c.setAttribute('cy', svgY); });
}

// ── .ggs save + report ──
function saveScene() {
  if (!scene.objects.length) return;
  const doc = {
    format: 'gratisgraph-scene', version: 1, units: 'mm', frame: 'machine',
    metadata: { created: new Date().toISOString() },
    objects: scene.objects.map((o) => ({
      name: o.name, transform: o.transform, reference: o.reference || false,
      baseStrokes: o.baseStrokes.map((s) => ({ start: s.start, deltas: s.deltas })),
    })),
  };
  const blob = new Blob([JSON.stringify(doc, null, 2)], { type: 'application/json' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob); a.download = 'scene.ggs'; a.click();
  toast('Saved scene', 'ok');
}
function reportMachine() {
  // Opens the support flow: pre-fills a GitHub issue with the diagnostics the
  // server exposes. (Capture itself is the gts_capture CLI.)
  window.open('https://github.com/Stephen-Arsenault/GratisGraph/issues/new?title=Machine+report&labels=machine-report', '_blank');
  toast('Run gts_capture.py and attach the report to the issue.');
}

// ── wiring ──


// ── pairing persistence + field visibility ──
function savePaired(token) {
  try { localStorage.setItem('gg-token', token); } catch {}
}
function loadPaired() {
  try { return localStorage.getItem('gg-token') || ''; } catch { return ''; }
}
function showPairingField(show) {
  const wrap = $('pairWrap'), done = $('pairDone');
  if (!wrap || !done) return;
  wrap.style.display = show ? '' : 'none';
  done.style.display = show ? 'none' : '';
}
function unpair() {
  try { localStorage.removeItem('gg-token'); } catch {}
  $('token').value = '';
  showPairingField(true);
  pollMachine();
}

// ── theme ──
function applyTheme(t) {
  document.documentElement.setAttribute('data-theme', t);
  $('themeIcon').textContent = t === 'light' ? '\u25d1' : '\u25d0';
  $('themeLabel').textContent = t === 'light' ? 'dark' : 'light';
  try { localStorage.setItem('gg-theme', t); } catch {}
}
function initTheme() {
  let t = 'dark';
  try { t = localStorage.getItem('gg-theme') || 'dark'; } catch {}
  applyTheme(t);
  $('themeToggle').onclick = () => {
    const cur = document.documentElement.getAttribute('data-theme') || 'dark';
    applyTheme(cur === 'light' ? 'dark' : 'light');
    if (mode === 'edit') renderEdit(); else renderPrepare();
  };
}

function init() {
  const file = $('file');
  $('toolImport').onclick = () => file.click();
  file.onchange = () => { for (const f of file.files) addFile(f); file.value = ''; };
  // the whole window is the drop target
  window.addEventListener('dragover', (ev) => ev.preventDefault());
  window.addEventListener('drop', (ev) => {
    ev.preventDefault();
    if (ev.dataTransfer?.files?.length) for (const f of ev.dataTransfer.files) addFile(f);
  });
  $('toolText').onclick = () => {
    activeTool = activeTool === 'text' ? 'select' : 'text';
    if (activeTool === 'text') { selectedId = null; if (mode === 'edit') renderEdit(); }
    renderObjectList();
    if (activeTool === 'text') $('textValue').focus();
  };

  for (const id of ['speed', 'depth', 'optimise', 'flipy']) {
    $(id).addEventListener('change', autosave);
  }
  $('objRot').addEventListener('change', () => {
    const obj = scene.objects.find((o) => o.id === selectedId);
    if (!obj) return;
    obj.transform = { ...obj.transform, rotation: (+$('objRot').value || 0) * Math.PI / 180 };
    renderEdit(); autosave();
  });
  $('objScale').addEventListener('change', () => {
    const obj = scene.objects.find((o) => o.id === selectedId);
    if (!obj) return;
    const s = Math.min(50, Math.max(0.05, +$('objScale').value || 1));
    $('objScale').value = s;
    obj.transform = { ...obj.transform, scale: s };
    renderEdit(); autosave();
  });
  const scaleToDimension = (which) => {
    const obj = scene.objects.find((o) => o.id === selectedId);
    if (!obj) return;
    const [bx0, by0, bx1, by1] = strokesBounds(worldStrokes(obj));
    const currentMm = (which === 'w' ? bx1 - bx0 : by1 - by0) / 100;
    const target = +$(which === 'w' ? 'objW' : 'objH').value;
    if (!(currentMm > 0) || !(target > 0)) { updateObjPanel(); return; }
    const s = Math.min(50, Math.max(0.05, obj.transform.scale * (target / currentMm)));
    obj.transform = { ...obj.transform, scale: s };
    renderEdit(); renderObjectList(); autosave();
  };
  $('objW').addEventListener('change', () => scaleToDimension('w'));
  $('objH').addEventListener('change', () => scaleToDimension('h'));
  $('objRef').addEventListener('change', () => {
    const obj = scene.objects.find((o) => o.id === selectedId);
    if (!obj) return;
    obj.reference = $('objRef').checked;
    renderEdit(); renderObjectList(); autosave();
  });
  window.addEventListener('keydown', (e) => {
    const tag = (document.activeElement || {}).tagName || '';
    if ((e.key === 'Delete' || e.key === 'Backspace') && mode === 'edit'
        && selectedId && !/INPUT|TEXTAREA/.test(tag)) {
      e.preventDefault();
      deleteSelected();
    }
  });
  $('addTextBtn').onclick = addText;
  $('textValue').addEventListener('keydown', (e) => { if (e.key === 'Enter') addText(); });
  $('textFont').addEventListener('change', () => {
    if ($('textFont').value === 'custom' && !customFont) $('fontFile').click();
  });
  $('fontFile').addEventListener('change', () => {
    const f = $('fontFile').files[0];
    if (!f) return;
    const rd = new FileReader();
    rd.onload = () => {
      customFont = { name: f.name, data: new Uint8Array(rd.result) };
      const opt = $('textFont').querySelector('option[value="custom"]');
      opt.textContent = `Custom: ${f.name}`;
      toast(`Loaded ${f.name} — parsed locally, never uploaded.`, 'ok');
    };
    rd.readAsArrayBuffer(f);
  });
  $('toolCircle').onclick = () => addShape('circle');
  $('toolSquare').onclick = () => addShape('square');
  $('toolLine').onclick = () => addShape('line');
  $('newSceneBtn').onclick = newSceneAction;
  $('delObjBtn').onclick = deleteSelected;
  // prepareBtn click handler is set in toMode() since it toggles
  $('token').addEventListener('input', () => { pollMachine(); refreshSendButton(); });
  $('trueScale').addEventListener('change', (e) => {
    $('bed').classList.toggle('true-scale', e.target.checked);
    document.querySelector('.bed-wrap').classList.toggle('true-scale', e.target.checked);
  });
  $('machinePill').addEventListener('click', () => {
    if ($('machinePill').classList.contains('cta')) showPairingHelp();
  });
  $('sendBtn').onclick = sendJob;
  $('saveBtn').onclick = saveScene;
  $('reportBtn').onclick = reportMachine;
  // legal & attribution modal
  const legal = $('legalModal');
  const openLegal = () => { legal.style.display = ''; };
  const closeLegal = () => { legal.style.display = 'none'; };
  $('legalBtn').onclick = openLegal;
  $('legalClose').onclick = closeLegal;
  legal.addEventListener('click', (e) => { if (e.target === legal) closeLegal(); });
  window.addEventListener('keydown', (e) => { if (e.key === 'Escape' && legal.style.display !== 'none') closeLegal(); });

  initTheme();
  const saved = loadPaired();
  if (saved) $('token').value = saved;
  $('unpairBtn') && ($('unpairBtn').onclick = unpair);
  const restored = restoreAutosave();
  pollServer();
  setInterval(pollServer, 4000);
  toMode('edit');
  if (restored) toast('Restored your previous scene', 'ok');
}
init();
