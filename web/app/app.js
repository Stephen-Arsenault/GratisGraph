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
let jobDurationSec = 0;          // estimated runtime of the built job
const SHARED_GRID = { W: 100, H: 100, M: 10 };

// inline icons for the object-list row actions
const ICON = {
  eye:    '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"><path d="M1 8s2.6-4.5 7-4.5 7 4.5 7 4.5-2.6 4.5-7 4.5S1 8 1 8z"/><circle cx="8" cy="8" r="1.9"/></svg>',
  eyeOff: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"><path d="M6.4 3.7A6.7 6.7 0 0 1 8 3.5c4.4 0 7 4.5 7 4.5a12.4 12.4 0 0 1-2.1 2.6M4.1 4.8A12.2 12.2 0 0 0 1 8s2.6 4.5 7 4.5a6.5 6.5 0 0 0 2.7-.6"/><path d="M2 2l12 12"/></svg>',
  trash:  '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"><path d="M2.8 4.2h10.4M6.3 4.2V3c0-.4.3-.7.7-.7h2c.4 0 .7.3.7.7v1.2M4.3 4.2l.6 8.2c0 .4.4.8.8.8h4.6c.4 0 .8-.4.8-.8l.6-8.2"/></svg>',
};

// ── motion model: job-duration estimate (ported from gts_optimise.estimate_duration) ──
const _VS_TO_MM_PER_SEC = 0.995, _RAPID_MULTIPLIER = 1.0, _Z_DWELL_SEC = 0.5;
function estimateDurationSec(strokes, speed, unitsPerMm = 100) {
  if (!strokes.length) return 0;
  const v = speed * _VS_TO_MM_PER_SEC, rapid = v * _RAPID_MULTIPLIER;
  const moveT = (mm, vel) => (mm > 0 && vel > 0) ? mm / vel : 0;
  let total = 0, cx = 0, cy = 0;
  for (const s of strokes) {
    const sx = s.start[0], sy = s.start[1];
    total += moveT(Math.hypot(sx - cx, sy - cy) / unitsPerMm, rapid);   // rapid to start
    total += _Z_DWELL_SEC;                                              // Z dip
    let len = 0, ex = sx, ey = sy;
    for (const [dx, dy] of s.deltas) { len += Math.hypot(dx, dy); ex += dx; ey += dy; }
    total += moveT(len / unitsPerMm, v);                               // engrave travel
    cx = ex; cy = ey;
  }
  total += moveT(Math.hypot(cx, cy) / unitsPerMm, rapid);              // return home
  return total;
}
function formatDuration(sec) {
  sec = Math.round(sec);
  const m = Math.floor(sec / 60), s = sec % 60;
  if (m >= 60) { const h = Math.floor(m / 60); return `${h}h\u202f${m % 60}m`; }
  return m ? `${m}m\u202f${s}s` : `${s}s`;
}

// ── orientation / centering / mirroring (strokes are {start,deltas} in device units) ──
// Source content authored Y-up (fonts, DXF/CAD) must be flipped into the Y-down
// machine frame (home top-left, +Y toward the operator) so it engraves upright.
function flipStrokesY(strokes) {
  return strokes.map((s) => ({ start: [s.start[0], -s.start[1]], deltas: s.deltas.map(([dx, dy]) => [dx, -dy]) }));
}
// Transform that lands the strokes' bbox centre on the bed centre (50,50 mm).
// fit=true also scales the longest side to ~90mm so big imports land on-bed.
function centerTransform(strokes, fit = false) {
  const [x0, y0, x1, y1] = strokesBounds(strokes);
  const cx = (x0 + x1) / 2, cy = (y0 + y1) / 2;
  let scale = 1;
  if (fit) { const maxDim = Math.max(x1 - x0, y1 - y0); scale = maxDim > 0 ? 9000 / maxDim : 1; }
  return { tx: 5000 - cx, ty: 5000 - cy, rotation: 0, scale };
}
function mirrorObject(obj, axis) {
  const [minX, minY, maxX, maxY] = strokesBounds(obj.baseStrokes);
  if (axis === 'x') {
    const c2 = minX + maxX;   // mirror X about local centre — world placement unchanged
    obj.baseStrokes = obj.baseStrokes.map((s) => ({ start: [c2 - s.start[0], s.start[1]], deltas: s.deltas.map(([dx, dy]) => [-dx, dy]) }));
  } else {
    const c2 = minY + maxY;
    obj.baseStrokes = obj.baseStrokes.map((s) => ({ start: [s.start[0], c2 - s.start[1]], deltas: s.deltas.map(([dx, dy]) => [dx, -dy]) }));
  }
}
function mirrorSelected(axis) {
  const obj = scene.objects.find((o) => o.id === selectedId);
  if (!obj) { toast('Select an object to mirror.', 'warn'); return; }
  mirrorObject(obj, axis);
  renderEdit(); renderObjectList(); autosave();
}

// ── SVG import — flatten via the browser's own SVG engine (handles every path
// command + nested transforms). SVG is natively Y-down, i.e. already the machine
// frame, so no Y-flip. Multi-subpath <path>s are split at pen-up jumps. ──
function svgUnitMm(svg) {
  const vb = (svg.getAttribute('viewBox') || '').split(/[\s,]+/).map(Number).filter((n) => !Number.isNaN(n));
  const wAttr = (svg.getAttribute('width') || '').trim();
  if (vb.length === 4 && vb[2] > 0 && wAttr) {
    const m = /^([\d.]+)\s*(mm|cm|in|px|pt)?$/i.exec(wAttr);
    if (m) {
      const val = parseFloat(m[1]), unit = (m[2] || 'px').toLowerCase();
      const wMm = unit === 'mm' ? val : unit === 'cm' ? val * 10 : unit === 'in' ? val * 25.4 : unit === 'pt' ? val * 25.4 / 72 : val * 25.4 / 96;
      return wMm / vb[2];
    }
  }
  return 25.4 / 96;   // assume px → mm at 96dpi
}
function applyMatrix(m, x, y) { return [m.a * x + m.c * y + m.e, m.b * x + m.d * y + m.f]; }
function pointsToStroke(pts, ctm, mmPerUnit) {
  const u = (x, y) => { const [mx, my] = applyMatrix(ctm, x, y); return [Math.round(mx * mmPerUnit * 100), Math.round(my * mmPerUnit * 100)]; };
  let [sx, sy] = u(pts[0][0], pts[0][1]);
  const deltas = []; let px = sx, py = sy;
  for (let i = 1; i < pts.length; i++) {
    const [qx, qy] = u(pts[i][0], pts[i][1]);
    if (qx !== px || qy !== py) deltas.push([qx - px, qy - py]);
    px = qx; py = qy;
  }
  return deltas.length ? { start: [sx, sy], deltas } : null;
}
function parseSvgToStrokes(text) {
  const warnings = [];
  const parsed = new DOMParser().parseFromString(text, 'image/svg+xml');
  if (parsed.querySelector('parsererror')) throw new Error('Invalid SVG.');
  const srcSvg = parsed.querySelector('svg');
  if (!srcSvg) throw new Error('No <svg> element.');
  const mmPerUnit = svgUnitMm(srcSvg);
  const holder = document.createElement('div');
  holder.setAttribute('style', 'position:absolute;left:-99999px;top:-99999px;width:0;height:0;overflow:hidden;opacity:0');
  const svg = document.importNode(srcSvg, true);
  holder.appendChild(svg);
  document.body.appendChild(holder);
  const strokes = [];
  try {
    for (const el of svg.querySelectorAll('path,line,polyline,polygon,rect,circle,ellipse')) {
      if (el.closest('defs,marker,pattern,clipPath,mask,symbol')) continue;   // skip non-drawn defs
      let total = 0;
      try { total = el.getTotalLength(); } catch { continue; }
      if (!(total > 0)) continue;
      const ctm = el.getCTM();
      if (!ctm) continue;
      const step = Math.max(0.25, total / 3000);
      const jump = step * 4;   // a pen-up move (zero-length) shows as a sample jump
      let cur = [], prev = null;
      const flush = () => { if (cur.length >= 2) { const st = pointsToStroke(cur, ctm, mmPerUnit); if (st) strokes.push(st); } cur = []; };
      for (let L = 0; L <= total + 1e-6; L += step) {
        const pt = el.getPointAtLength(Math.min(L, total));
        const p = [pt.x, pt.y];
        if (prev && Math.hypot(p[0] - prev[0], p[1] - prev[1]) > jump) flush();
        cur.push(p); prev = p;
      }
      flush();
    }
  } finally {
    document.body.removeChild(holder);
  }
  if (!strokes.length) warnings.push('No usable path geometry found in the SVG.');
  return { strokes, warnings };
}

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
let paired = false;
function setMachine(m) {
  const dot = $('machineDot'), txt = $('machineText');
  if (m === 'unpaired') { dot.className = 'dot'; txt.textContent = 'not paired'; paired = false; showPairingField(true); }
  else if (m === 'rejected') { dot.className = 'dot busy'; txt.textContent = 'code rejected'; paired = false; showPairingField(true); }
  else if (m && m.model) { dot.className = 'dot on'; txt.textContent = m.model; paired = true; showPairingField(false); }
  else { dot.className = 'dot'; txt.textContent = 'no machine'; if (!paired) showPairingField(true); }
  refreshSendButton();
}


// ── scene autosave (survives refresh + browser restart) ──
const AUTOSAVE_KEY = 'gg-scene-v1';
let saveTimer = null;
function sceneToDoc() {
  return {
    format: 'gratisgraph-scene', version: 1, units: 'mm', frame: 'machine',
    settings: {
      speed: +$('speed').value, depth: +$('depth').value,
      optimise: $('optimise').checked,
    },
    objects: scene.objects.map((o) => ({
      name: o.name, transform: o.transform, reference: o.reference || false,
      source: o.source || null,
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
  }
  scene = newScene();
  for (const o of doc.objects) {
    const no = makeObject(o.name || 'object',
      o.baseStrokes.map((s) => ({ start: s.start, deltas: s.deltas })),
      o.transform || identityTransform());
    no.reference = !!o.reference;
    if (o.source) no.source = o.source;
    scene.objects.push(no);
  }
  selectedId = scene.objects.length ? scene.objects[scene.objects.length - 1].id : null;
  return true;
}
function clearAutosave() { try { localStorage.removeItem(AUTOSAVE_KEY); } catch {} }

// ── building objects from files ──
function strokesFromFile(text, kind) {
  if (kind === 'ggs') {
    const doc = JSON.parse(text);
    if (doc.format === 'gratisgraph-scene') return null; // handled by loadScene
    return ggsToStrokes(doc);
  }
  if (kind === 'dxf') {
    const r = parseDxf(text);
    for (const w of r.warnings) toast(w, 'warn');
    return flipStrokesY(r.strokes);            // DXF is Y-up → flip into the Y-down machine frame
  }
  if (kind === 'svg') {
    const r = parseSvgToStrokes(text);          // SVG is already Y-down — no flip
    for (const w of r.warnings) toast(w, 'warn');
    return r.strokes;
  }
  // G-code: orientation left as authored (its Y convention is ambiguous)
  return gcodeToOptimisedStrokes(text, { speed: +$('speed').value, pz: +$('depth').value, flipY: false, optimise: false });
}

function addFile(file) {
  const lower = file.name.toLowerCase();
  const kind = lower.endsWith('.ggs') ? 'ggs' : lower.endsWith('.dxf') ? 'dxf' : lower.endsWith('.svg') ? 'svg' : 'gcode';
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
        const obj = makeObject(name, baseStrokes, centerTransform(baseStrokes, true));   // scale-to-fit + centre
        obj.source = { kind: 'import', format: kind };
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
    if (o.source) no.source = o.source;
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
    // fonts are authored Y-up; flip into the Y-down machine frame, then centre on the bed
    const strokes = flipStrokesY(r.strokes);
    const obj = makeObject(text.length > 18 ? text.slice(0, 18) + '…' : text, strokes, centerTransform(strokes));
    obj.source = { kind: 'text', text, font: key, size };
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
  obj.source = { kind: 'shape', shape: kind };
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
  jobDurationSec = estimateDurationSec(strokes, +$('speed').value);
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
  for (let i = 0; i <= W; i += 10) labels += `<text x="${i}" y="${-2.4}" text-anchor="middle">${i}</text>`;
  for (let j = 0; j <= H; j += 10) labels += `<text x="${-2}" y="${j + 0.9}" text-anchor="end">${j}</text>`;
  svg += labels + `</g>`;
  return svg;
}

/** pointer event -> machine coords (mm), inverting the viewBox + Y-flip. */
function bedPointMm(ev) {
  const rect = $('bed').getBoundingClientRect();
  const { W, H, M } = SHARED_GRID;
  const x = -M + (ev.clientX - rect.left) * ((W + 2 * M) / rect.width);
  const ySvg = -M + (ev.clientY - rect.top) * ((H + 2 * M) / rect.height);
  return [x, ySvg];
}

// machine(units) -> svg point string. Origin (0,0) = top-left (home / back-left
// of the bed), +X right, +Y down (toward the operator) — matches the operator's view.
function pp(x, y) { return `${x / 100},${y / 100}`; }
function mmflip(yUnits) { return yUnits / 100; }

// ── EDIT render: objects as strokes + draggable bounding boxes ──
function renderEdit() {
  const bed = $('bed');
  const cCut = cssVar('--cut'), cInk = cssVar('--ink'), cBox = cssVar('--ink-faint'), cRef = cssVar('--rapid'), cCtrl = cssVar('--ctrl');
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
    const X = (bx0 - pad) / 100, Y0 = mmflip(by0 - pad), Wd = (bx1 - bx0 + 2 * pad) / 100, Hd = (by1 - by0 + 2 * pad) / 100;
    svg += `<rect class="bbox" data-id="${obj.id}" x="${X}" y="${Y0}" width="${Wd}" height="${Hd}" fill="transparent" stroke="${cBox}" stroke-width="${selected ? 0.25 : 0.15}" stroke-dasharray="1 1" opacity="${selected ? 0.85 : 0.4}" style="cursor:move"/>`;
    if (selected) {
      const midX = X + Wd / 2;
      svg += `<line x1="${midX}" y1="${Y0}" x2="${midX}" y2="${Y0 - 3.2}" stroke="${cCtrl}" stroke-width="0.18"/>`;
      svg += `<circle class="rot-handle" data-id="${obj.id}" cx="${midX}" cy="${Y0 - 4.4}" r="1.5" fill="${cssVar('--panel')}" stroke="${cCtrl}" stroke-width="0.35" style="cursor:grab"/>`;
      svg += `<rect class="scale-handle" data-id="${obj.id}" x="${X + Wd - 1.2}" y="${Y0 + Hd - 1.2}" width="2.4" height="2.4" fill="${cssVar('--panel')}" stroke="${cCtrl}" stroke-width="0.35" style="cursor:nwse-resize"/>`;
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
  let g = `<g>`;
  g += `<g stroke="${cRapid}" stroke-width="0.25" stroke-dasharray="1 1" fill="none" opacity="0.7">`;
  for (const [a, b] of job.rapids) g += `<line x1="${a[0]}" y1="${a[1]}" x2="${b[0]}" y2="${b[1]}"/>`;
  g += `</g><g stroke="${cCut}" stroke-width="0.3" fill="none" stroke-linecap="round" stroke-linejoin="round">`;
  for (const s of job.strokes) g += `<polyline points="${s.points.map((p) => `${p[0]},${p[1]}`).join(' ')}"/>`;
  g += `</g><circle cx="0" cy="0" r="1.2" fill="${cCut}"/></g>`;
  bed.innerHTML = svg + g + headLayer();
  const [x0, y0, x1, y1] = job.bounds;
  $('jobInfo').textContent = `${job.strokes.length} stroke${job.strokes.length === 1 ? '' : 's'} · ≈ ${formatDuration(jobDurationSec)} · ${m20.length} bytes`;
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

    const name = document.createElement('span');
    name.className = 'obj-name';
    name.textContent = obj.name;
    name.title = obj.name;
    name.onclick = () => {
      selectedId = obj.id; activeTool = 'select';
      if (mode === 'edit') renderEdit();
      renderObjectList();
    };

    const acts = document.createElement('span');
    acts.className = 'obj-actions';

    const eye = document.createElement('button');
    eye.type = 'button';
    eye.className = 'obj-icon' + (obj.reference ? ' on' : '');
    eye.title = obj.reference
      ? 'Reference only — won’t be engraved (click to engrave)'
      : 'Will be engraved (click to make reference only)';
    eye.innerHTML = obj.reference ? ICON.eyeOff : ICON.eye;
    eye.onclick = (e) => {
      e.stopPropagation();
      obj.reference = !obj.reference;
      renderEdit(); renderObjectList(); autosave();
    };

    const trash = document.createElement('button');
    trash.type = 'button';
    trash.className = 'obj-icon danger-icon';
    trash.title = 'Delete object';
    trash.innerHTML = ICON.trash;
    trash.onclick = (e) => { e.stopPropagation(); requestDelete(obj.id); };

    acts.append(eye, trash);
    li.append(name, acts);
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
    $('objType').textContent = objectTypeLabel(obj);
    const isText = !!obj.source && obj.source.kind === 'text';
    $('objTextEdit').style.display = isText ? '' : 'none';
    if (isText) {
      $('objTextValue').value = obj.source.text;
      $('objTextFont').value = obj.source.font;
    }
    $('objRot').value = Math.round((obj.transform.rotation * 180 / Math.PI) * 10) / 10;
    $('objScale').value = Math.round(obj.transform.scale * 1000) / 1000;
    const [bx0, by0, bx1, by1] = strokesBounds(worldStrokes(obj));
    $('objW').value = Math.round((bx1 - bx0) / 10) / 10;
    $('objH').value = Math.round((by1 - by0) / 10) / 10;
    $('objX').value = Math.round(bx0 / 10) / 10;
    $('objY').value = Math.round(by0 / 10) / 10;
    $('objRef').checked = !!obj.reference;
  }
}

function deleteObject(id) {
  if (!id) return;
  scene.objects = scene.objects.filter((o) => o.id !== id);
  if (selectedId === id) selectedId = scene.objects.length ? scene.objects[scene.objects.length - 1].id : null;
  autosave();
  toMode('edit');
}

// ── object type + text re-editing ──
function objectTypeLabel(obj) {
  const s = obj.source;
  if (!s) return 'Strokes';
  if (s.kind === 'text') return 'Text';
  if (s.kind === 'shape') return ({ circle: 'Circle', square: 'Square', line: 'Line' })[s.shape] || 'Shape';
  if (s.kind === 'import') return ({ gcode: 'G-Code', dxf: 'DXF', ggs: 'GGS', svg: 'SVG' })[s.format] || 'Import';
  return 'Strokes';
}

let pendingCustomFor = null;   // object id awaiting a custom-font load (edit flow)

// Regenerate a text object's strokes from its source (string/font/size),
// keeping its lower-left corner anchored so it doesn't jump when edited.
async function regenTextObject(obj) {
  const src = obj.source;
  const size = Math.min(80, Math.max(1, src.size || 10));
  const before = strokesBounds(worldStrokes(obj));
  let r;
  try {
    const data = await loadFontData(src.font);
    r = src.font === 'engrave'
      ? hersheyTextToStrokes(data, src.text, size, 0, 0)
      : ttfTextToStrokes(data, src.text, size, 0, 0);
  } catch (e) { toast(e.message, 'warn'); return false; }
  for (const w of r.warnings) toast(w, 'warn');
  if (!r.strokes.length) { toast('No engraveable strokes in that text.', 'warn'); return false; }
  obj.baseStrokes = flipStrokesY(r.strokes);   // keep upright in the Y-down machine frame
  const after = strokesBounds(worldStrokes(obj));
  obj.transform = { ...obj.transform, tx: obj.transform.tx + (before[0] - after[0]), ty: obj.transform.ty + (before[1] - after[1]) };
  return true;
}

async function applyTextEdit() {
  const obj = scene.objects.find((o) => o.id === selectedId);
  if (!obj || !obj.source || obj.source.kind !== 'text') return;
  const newText = $('objTextValue').value;
  if (!newText.trim()) { toast('Text can’t be empty.', 'warn'); $('objTextValue').value = obj.source.text; return; }
  if (newText === obj.source.text) return;
  const prev = obj.source.text;
  obj.source = { ...obj.source, text: newText };
  if (!(await regenTextObject(obj))) { obj.source = { ...obj.source, text: prev }; $('objTextValue').value = prev; return; }
  obj.name = newText.length > 18 ? newText.slice(0, 18) + '…' : newText;
  renderEdit(); renderObjectList(); autosave();
}

async function applyFontEdit() {
  const obj = scene.objects.find((o) => o.id === selectedId);
  if (!obj || !obj.source || obj.source.kind !== 'text') return;
  const f = $('objTextFont').value;
  if (f === obj.source.font) return;
  if (f === 'custom' && !customFont) { pendingCustomFor = obj.id; $('objTextFont').value = obj.source.font; $('fontFile').click(); return; }
  const prev = obj.source.font;
  obj.source = { ...obj.source, font: f };
  if (!(await regenTextObject(obj))) { obj.source = { ...obj.source, font: prev }; $('objTextFont').value = prev; return; }
  renderEdit(); renderObjectList(); autosave();
}

// delete is gated by a confirm dialog (Enter or the Delete button confirms)
let pendingDeleteId = null;
function requestDelete(id) {
  const obj = scene.objects.find((o) => o.id === id);
  if (!obj) return;
  pendingDeleteId = id;
  $('confirmMsg').textContent = `“${obj.name}” will be removed from the scene.`;
  $('confirmModal').style.display = '';
  $('confirmOk').focus();
}
function closeConfirm() { $('confirmModal').style.display = 'none'; pendingDeleteId = null; }
function performDelete() {
  const id = pendingDeleteId;
  closeConfirm();
  deleteObject(id);
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
      ev.stopPropagation();   // don't let this reach the bed-background deselect handler
      if (reselect) { renderEdit(); renderObjectList(); }
      const startT = { ...obj.transform };
      // convert pointer movement (px) to mm via the SVG's current scale
      const rect = bed.getBoundingClientRect();
      const { W, M } = SHARED_GRID;
      const mmPerPx = (W + 2 * M) / rect.width;   // viewBox units per screen px
      const sx = ev.clientX, sy = ev.clientY;
      const onMove = (e) => {
        let dxmm = (e.clientX - sx) * mmPerPx;
        let dymm = (e.clientY - sy) * mmPerPx;    // screen down = machine +Y (origin top-left)
        let dux = dxmm * 100, duy = dymm * 100;   // mm -> device units
        if (!e.shiftKey) { dux = Math.round(dux / 100) * 100; duy = Math.round(duy / 100) * 100; } // snap 1mm unless Shift
        obj.transform = { ...startT, tx: startT.tx + dux, ty: startT.ty + duy };
        renderEdit(); updateObjPanel();
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
        s = Math.min(50, Math.max(0.001, s));
        obj.transform = { ...obj.transform, scale: s };
        renderEdit(); updateObjPanel();
      };
      const onUp = () => { window.removeEventListener('pointermove', onMove); window.removeEventListener('pointerup', onUp); autosave(); };
      window.addEventListener('pointermove', onMove);
      window.addEventListener('pointerup', onUp);
    });
  });
}

function openSetup() { $('setupModal').style.display = ''; }

function refreshSendButton() {
  const token = $('token').value.trim();
  const serverOnline = $('serverDot').className.includes('on');
  const send = $('sendBtn');
  if (!serverOnline) {
    // No local app running (first-time visitor): the primary button becomes the
    // install CTA, and clicking it opens the "get the app" guide.
    send.disabled = false;
    send.textContent = 'Install the app to send';
  } else {
    send.textContent = 'Send to machine';
    const ready = mode === 'prepare' && m20 && token && $('machineDot').className.includes('on');
    send.disabled = !ready;
  }
  $('saveBtn').disabled = scene.objects.length === 0;
}

let sse = null;
async function sendJob() {
  if (!$('serverDot').className.includes('on')) { openSetup(); return; }   // no local app yet
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
async function ensureTelemetry(token) {
  if (sse && sseToken === token) return;        // already streaming with this token
  if (sse) { sse.close(); sse = null; }
  sseToken = token;
  connectTelemetry(token);
}

// EventSource can't send an Authorization header, and we never put the durable
// pairing token in a URL (it'd leak into history/Referer). So mint a single-use,
// short-lived ticket over an authenticated POST and pass THAT in the query.
async function connectTelemetry(token) {
  if (sseToken !== token) return;               // superseded by a newer token
  let ticket;
  try {
    const r = await fetch(`${SERVER}/telemetry/ticket`, {
      method: 'POST', headers: { Authorization: `Bearer ${token}` },
    });
    if (!r.ok) throw new Error(r.statusText);
    ticket = (await r.json()).ticket;
  } catch {
    if (sseToken === token) setTimeout(() => connectTelemetry(token), 3000);
    return;
  }
  if (sseToken !== token) return;
  const es = new EventSource(`${SERVER}/telemetry?ticket=${encodeURIComponent(ticket)}`);
  sse = es;
  es.onmessage = (ev) => {
    let p; try { p = JSON.parse(ev.data); } catch { return; }
    if (p.type !== 'motor') return;
    const mm = (p.value / 100).toFixed(2);
    if (p.axis === 'X') { $('tx').textContent = mm; moveHead(p.value / 100, null); }
    if (p.axis === 'Y') { $('ty').textContent = mm; moveHead(null, p.value / 100); }
    if (p.axis === 'Z') { $('tz').textContent = mm; }
    if (p.axis === 'E') { $('jobState').textContent = 'running'; }
  };
  es.onerror = () => {
    // The ticket is single-use, so EventSource's built-in retry would replay a
    // spent ticket. Take over: close and re-mint a fresh ticket.
    es.close();
    if (sse === es) sse = null;
    if (sseToken === token) setTimeout(() => connectTelemetry(token), 2000);
  };
}
let hx = 0, hy = 0, headVisible = false;
// A persistent red head dot, rendered in machine coords with the Y-flip so it
// lands at the live (X,Y). Re-emitted on every render so it survives mode
// switches; moveHead() updates it in place during motion.
function headLayer() {
  const y = hy;   // machine Y maps directly to SVG (origin top-left, +Y toward front = down)
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
  const svgY = hy;
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
      source: o.source || null,
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

  for (const id of ['speed', 'depth', 'optimise']) {
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
    const s = Math.min(50, Math.max(0.001, +$('objScale').value || 1));
    $('objScale').value = s;
    obj.transform = { ...obj.transform, scale: s };
    renderEdit(); autosave();
  });
  const setObjPos = (axis) => {
    const obj = scene.objects.find((o) => o.id === selectedId);
    if (!obj) return;
    const [bx0, by0] = strokesBounds(worldStrokes(obj));   // current world min corner (units)
    const curMm = (axis === 'x' ? bx0 : by0) / 100;
    const target = +$(axis === 'x' ? 'objX' : 'objY').value;
    if (!Number.isFinite(target)) { updateObjPanel(); return; }
    // tx/ty translate in world space, so shifting by (target - current) lands the
    // bounding-box corner exactly on the requested coordinate.
    const deltaUnits = Math.round((target - curMm) * 100);
    const key = axis === 'x' ? 'tx' : 'ty';
    obj.transform = { ...obj.transform, [key]: obj.transform[key] + deltaUnits };
    renderEdit(); updateObjPanel(); autosave();
  };
  $('objX').addEventListener('change', () => setObjPos('x'));
  $('objY').addEventListener('change', () => setObjPos('y'));
  const scaleToDimension = (which) => {
    const obj = scene.objects.find((o) => o.id === selectedId);
    if (!obj) return;
    const [bx0, by0, bx1, by1] = strokesBounds(worldStrokes(obj));
    const currentMm = (which === 'w' ? bx1 - bx0 : by1 - by0) / 100;
    const target = +$(which === 'w' ? 'objW' : 'objH').value;
    if (!(currentMm > 0) || !(target > 0)) { updateObjPanel(); return; }
    const s = Math.min(50, Math.max(0.001, obj.transform.scale * (target / currentMm)));
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
        && selectedId && !/INPUT|TEXTAREA/.test(tag)
        && $('confirmModal').style.display === 'none') {
      e.preventDefault();
      requestDelete(selectedId);
    }
  });
  $('addTextBtn').onclick = addText;
  $('textValue').addEventListener('keydown', (e) => { if (e.key === 'Enter') addText(); });
  $('textFont').addEventListener('change', () => {
    if ($('textFont').value === 'custom' && !customFont) $('fontFile').click();
  });
  // re-edit an existing text object's string / font from the Object panel
  $('objTextValue').addEventListener('change', applyTextEdit);
  $('objTextValue').addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); e.target.blur(); } });
  $('objTextFont').addEventListener('change', applyFontEdit);
  $('fontFile').addEventListener('change', () => {
    const f = $('fontFile').files[0];
    if (!f) return;
    const rd = new FileReader();
    rd.onload = () => {
      customFont = { name: f.name, data: new Uint8Array(rd.result) };
      for (const selId of ['textFont', 'objTextFont']) {
        const opt = $(selId).querySelector('option[value="custom"]');
        if (opt) opt.textContent = `Custom: ${f.name}`;
      }
      toast(`Loaded ${f.name} — parsed locally, never uploaded.`, 'ok');
      if (pendingCustomFor) {
        const obj = scene.objects.find((o) => o.id === pendingCustomFor);
        pendingCustomFor = null;
        if (obj && obj.source && obj.source.kind === 'text') {
          obj.source = { ...obj.source, font: 'custom' };
          regenTextObject(obj).then((ok) => { if (ok) { $('objTextFont').value = 'custom'; renderEdit(); renderObjectList(); autosave(); } });
        }
      }
    };
    rd.readAsArrayBuffer(f);
  });
  $('toolCircle').onclick = () => addShape('circle');
  $('toolSquare').onclick = () => addShape('square');
  $('toolLine').onclick = () => addShape('line');
  $('toolMirrorH').onclick = () => mirrorSelected('x');
  $('toolMirrorV').onclick = () => mirrorSelected('y');
  $('newSceneBtn').onclick = newSceneAction;
  $('delObjBtn').onclick = () => requestDelete(selectedId);
  // prepareBtn click handler is set in toMode() since it toggles
  $('token').addEventListener('input', () => { pollMachine(); refreshSendButton(); });
  $('trueScale').addEventListener('change', (e) => {
    $('bed').classList.toggle('true-scale', e.target.checked);
    document.querySelector('.bed-wrap').classList.toggle('true-scale', e.target.checked);
  });
  // click empty bed → deselect, so “nothing selected” is always reachable
  $('bed').addEventListener('pointerdown', (ev) => {
    if (mode !== 'edit') return;
    if (ev.target.closest('.obj, .bbox, .rot-handle, .scale-handle')) return;
    if (selectedId !== null || activeTool === 'text') {
      selectedId = null; activeTool = 'select';
      renderEdit(); renderObjectList();
    }
  });
  $('sendBtn').onclick = sendJob;
  $('saveBtn').onclick = saveScene;
  $('reportBtn').onclick = reportMachine;
  // modals (legal & attribution, about, pairing) — shared open/close + backdrop + Escape
  const wireModal = (id, openBtnId) => {
    const m = $(id);
    if (openBtnId) $(openBtnId).onclick = () => { m.style.display = ''; };
    m.querySelector('.modal-close').onclick = () => { m.style.display = 'none'; };
    m.addEventListener('click', (e) => { if (e.target === m) m.style.display = 'none'; });
  };
  wireModal('legalModal', 'legalBtn');
  wireModal('aboutModal', 'aboutBtn');
  wireModal('pairModal', 'pairHelpBtn');
  wireModal('setupModal', null);   // opened from the Send CTA + the About modal
  $('aboutGetApp').onclick = (e) => { e.preventDefault(); $('aboutModal').style.display = 'none'; openSetup(); };
  window.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape') return;
    if ($('confirmModal').style.display !== 'none') return;   // the confirm dialog owns Escape while open
    let closedAny = false;
    for (const id of ['legalModal', 'aboutModal', 'pairModal', 'setupModal']) {
      const m = $(id); if (m && m.style.display !== 'none') { m.style.display = 'none'; closedAny = true; }
    }
    if (closedAny) return;
    // nothing open → Escape backs out of the current selection
    const tag = (document.activeElement || {}).tagName || '';
    if (/INPUT|TEXTAREA|SELECT/.test(tag)) return;
    if (mode === 'edit' && (selectedId !== null || activeTool === 'text')) {
      selectedId = null; activeTool = 'select';
      renderEdit(); renderObjectList();
    }
  });

  // delete-confirmation dialog: Enter confirms, Escape/Cancel/backdrop dismisses
  $('confirmOk').onclick = performDelete;
  $('confirmCancel').onclick = closeConfirm;
  $('confirmClose').onclick = closeConfirm;
  $('confirmModal').addEventListener('click', (e) => { if (e.target === $('confirmModal')) closeConfirm(); });
  window.addEventListener('keydown', (e) => {
    if ($('confirmModal').style.display === 'none') return;
    if (e.key === 'Enter') { e.preventDefault(); performDelete(); }
    else if (e.key === 'Escape') { e.preventDefault(); closeConfirm(); }
  });

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
