/**
 * dxf.ts — DXF → strokes parser, from scratch, zero dependencies.
 *
 * DXF is a flat stream of (group code, value) line pairs. We tokenize pairs,
 * walk the ENTITIES section, and flatten the engraving-relevant entities to
 * polyline strokes: LINE, LWPOLYLINE (incl. bulge arcs), POLYLINE/VERTEX,
 * ARC, CIRCLE, ELLIPSE, SPLINE (de Boor). Everything else is skipped with a
 * warning. Output strokes are in device units (0.01mm), quantise-then-
 * subtract like gcode.ts so deltas stay consistent with the engine.
 *
 * Coordinates: DXF is Y-up. This parser emits Y-up as-is; the app flips Y into
 * the machine's Y-down frame (home top-left) at import time.
 * Units: $INSUNITS 4=mm, 1=inch, 0/absent=unitless (assumed mm). A caller-
 * supplied scale multiplies on top for the "import scale" affordance.
 *
 * No Python oracle exists for this module; tests are geometric assertions.
 */
import { mmToUnits } from './round.ts';
import type { Stroke } from './gts.ts';

const CHORD_TOL_MM = 0.02;   // max chord deviation when flattening curves
const MAX_ARC_SEGS = 256;

export interface DxfResult {
  strokes: Stroke[];
  warnings: string[];
}

export interface DxfOptions {
  scale?: number;            // extra user scale on top of unit conversion
}

type Pair = { code: number; value: string };

function tokenize(text: string): Pair[] {
  const lines = text.split(/\r\n|\r|\n/);
  const pairs: Pair[] = [];
  for (let i = 0; i + 1 < lines.length; i += 2) {
    const code = parseInt(lines[i]!.trim(), 10);
    if (Number.isNaN(code)) continue;
    pairs.push({ code, value: lines[i + 1]!.trim() });
  }
  return pairs;
}

/** Flatten an arc (center, radius, start/end angle rad, CCW) to points. */
function arcPoints(cx: number, cy: number, r: number,
                   a0: number, a1: number, includeStart: boolean): [number, number][] {
  let span = a1 - a0;
  if (span <= 0) span += 2 * Math.PI;
  // segments for chord tolerance: sagitta s = r(1-cos(θ/2)) <= tol
  const maxStep = r > CHORD_TOL_MM ? 2 * Math.acos(Math.max(0, 1 - CHORD_TOL_MM / r)) : span;
  const n = Math.min(MAX_ARC_SEGS, Math.max(2, Math.ceil(span / maxStep)));
  const pts: [number, number][] = [];
  for (let k = includeStart ? 0 : 1; k <= n; k++) {
    const a = a0 + (span * k) / n;
    pts.push([cx + r * Math.cos(a), cy + r * Math.sin(a)]);
  }
  return pts;
}

/** Bulge arc between two LWPOLYLINE vertices. bulge = tan(θ/4), signed CCW. */
function bulgeArc(x0: number, y0: number, x1: number, y1: number,
                  bulge: number): [number, number][] {
  const theta = 4 * Math.atan(bulge);            // included angle, signed
  const dx = x1 - x0, dy = y1 - y0;
  const chord = Math.hypot(dx, dy);
  if (chord < 1e-12 || Math.abs(theta) < 1e-12) return [[x1, y1]];
  const r = chord / (2 * Math.sin(Math.abs(theta) / 2));
  // center is perpendicular to the chord midpoint
  const mx = (x0 + x1) / 2, my = (y0 + y1) / 2;
  const h = r * Math.cos(theta / 2) * (bulge < 0 ? -1 : 1);
  const ux = -dy / chord, uy = dx / chord;       // unit perpendicular (left of chord)
  const cx = mx + ux * h, cy = my + uy * h;
  let a0 = Math.atan2(y0 - cy, x0 - cx);
  let a1 = Math.atan2(y1 - cy, x1 - cx);
  if (bulge > 0) {                                // CCW
    if (a1 <= a0) a1 += 2 * Math.PI;
    return arcPoints(cx, cy, r, a0, a1, false);
  } else {                                        // CW: walk reversed CCW arc
    if (a0 <= a1) a0 += 2 * Math.PI;
    const rev = arcPoints(cx, cy, r, a1, a0, true);
    rev.reverse();
    rev.shift();                                  // drop start point (== current)
    rev.push([x1, y1]);
    return rev.length ? rev : [[x1, y1]];
  }
}

/** De Boor evaluation of a clamped NURBS curve at parameter u. */
function deBoor(degree: number, knots: number[], ctrl: [number, number][],
                weights: number[] | null, u: number): [number, number] {
  const n = ctrl.length - 1;
  let k = -1;
  for (let i = degree; i <= n; i++) {
    if (u >= knots[i]! && u <= knots[i + 1]!) { k = i; break; }
  }
  if (k === -1) k = u <= knots[degree]! ? degree : n;
  const d: [number, number, number][] = [];
  for (let j = 0; j <= degree; j++) {
    const idx = k - degree + j;
    const w = weights ? weights[idx]! : 1;
    d.push([ctrl[idx]![0] * w, ctrl[idx]![1] * w, w]);
  }
  for (let r = 1; r <= degree; r++) {
    for (let j = degree; j >= r; j--) {
      const i = k - degree + j;
      const denom = knots[i + degree - r + 1]! - knots[i]!;
      const alpha = denom > 1e-12 ? (u - knots[i]!) / denom : 0;
      d[j] = [
        (1 - alpha) * d[j - 1]![0] + alpha * d[j]![0],
        (1 - alpha) * d[j - 1]![1] + alpha * d[j]![1],
        (1 - alpha) * d[j - 1]![2] + alpha * d[j]![2],
      ];
    }
  }
  const [px, py, pw] = d[degree]!;
  return pw > 1e-12 ? [px / pw, py / pw] : [px, py];
}

// $INSUNITS → mm multiplier (engraving-relevant subset)
const INSUNITS_TO_MM: Record<number, number> = {
  0: 1, 1: 25.4, 2: 304.8, 4: 1, 5: 10, 6: 1000, 8: 0.0254, 9: 0.0254 * 1000,
};

export function parseDxf(text: string, opts: DxfOptions = {}): DxfResult {
  const pairs = tokenize(text);
  const warnings: string[] = [];
  let unitScale = 1;

  // header: $INSUNITS
  for (let i = 0; i < pairs.length - 1; i++) {
    if (pairs[i]!.code === 9 && pairs[i]!.value === '$INSUNITS') {
      const v = parseInt(pairs[i + 1]!.value, 10);
      if (v in INSUNITS_TO_MM) unitScale = INSUNITS_TO_MM[v]!;
      else warnings.push(`Unknown $INSUNITS ${v}; assuming mm`);
      break;
    }
  }
  const scale = unitScale * (opts.scale ?? 1);
  const u = (mm: number) => mmToUnits(mm * scale);

  // collect entity records from the ENTITIES section
  const polysMm: [number, number][][] = [];   // flattened paths in raw dxf units
  let i = 0;
  let inEntities = false;
  const skipped = new Map<string, number>();

  const readEntity = (start: number): [Record<number, string[]>, number] => {
    // gather codes until the next 0. Stop at the FIRST code-0 at or after start:
    // a zero-code entity (e.g. a bare SEQEND) leaves `start` pointing straight at
    // the next entity's 0, and we must not consume it.
    const rec: Record<number, string[]> = {};
    let j = start;
    while (j < pairs.length && pairs[j]!.code !== 0) {
      const p = pairs[j]!;
      (rec[p.code] ||= []).push(p.value);
      j++;
    }
    return [rec, j];
  };

  while (i < pairs.length) {
    const p = pairs[i]!;
    if (p.code === 2 && pairs[i - 1]?.code === 0 && pairs[i - 1]?.value === 'SECTION') {
      inEntities = p.value === 'ENTITIES';
      i++; continue;
    }
    if (!inEntities || p.code !== 0) { i++; continue; }
    const type = p.value;

    if (type === 'LINE') {
      const [r, j] = readEntity(i + 1);
      const x0 = +r[10]![0]!, y0 = +r[20]![0]!, x1 = +r[11]![0]!, y1 = +r[21]![0]!;
      polysMm.push([[x0, y0], [x1, y1]]);
      i = j;
    } else if (type === 'LWPOLYLINE') {
      const [r, j] = readEntity(i + 1);
      const xs = (r[10] || []).map(Number), ys = (r[20] || []).map(Number);
      const closed = ((+(r[70]?.[0] ?? 0)) & 1) === 1;
      // bulges align positionally with vertices; DXF emits 42 only when nonzero,
      // so re-scan pairs in order to align bulge → vertex index.
      const bulges = new Array(xs.length).fill(0);
      let vi = -1;
      for (let k = i + 1; k < j; k++) {
        if (pairs[k]!.code === 10) vi++;
        else if (pairs[k]!.code === 42 && vi >= 0) bulges[vi] = +pairs[k]!.value;
      }
      if (xs.length >= 2) {
        const path: [number, number][] = [[xs[0]!, ys[0]!]];
        const segs = closed ? xs.length : xs.length - 1;
        for (let s = 0; s < segs; s++) {
          const a = s, b = (s + 1) % xs.length;
          if (bulges[a]) path.push(...bulgeArc(xs[a]!, ys[a]!, xs[b]!, ys[b]!, bulges[a]));
          else path.push([xs[b]!, ys[b]!]);
        }
        polysMm.push(path);
      }
      i = j;
    } else if (type === 'POLYLINE') {
      // legacy: POLYLINE header, VERTEX entities, SEQEND
      const [hdr, j0] = readEntity(i + 1);
      const closed = ((+(hdr[70]?.[0] ?? 0)) & 1) === 1;
      const verts: [number, number][] = [];
      const vbulges: number[] = [];
      let j = j0;
      while (j < pairs.length && pairs[j]!.code === 0 && pairs[j]!.value === 'VERTEX') {
        const [vr, jn] = readEntity(j + 1);
        verts.push([+(vr[10]?.[0] ?? 0), +(vr[20]?.[0] ?? 0)]);
        vbulges.push(+(vr[42]?.[0] ?? 0));
        j = jn;
      }
      if (j < pairs.length && pairs[j]!.value === 'SEQEND') { const [, jn] = readEntity(j + 1); j = jn; }
      if (verts.length >= 2) {
        const path: [number, number][] = [verts[0]!];
        const segs = closed ? verts.length : verts.length - 1;
        for (let s = 0; s < segs; s++) {
          const a = s, b = (s + 1) % verts.length;
          if (vbulges[a]) path.push(...bulgeArc(verts[a]![0], verts[a]![1], verts[b]![0], verts[b]![1], vbulges[a]!));
          else path.push(verts[b]!);
        }
        polysMm.push(path);
      }
      i = j;
    } else if (type === 'ARC') {
      const [r, j] = readEntity(i + 1);
      const cx = +r[10]![0]!, cy = +r[20]![0]!, rad = +r[40]![0]!;
      const a0 = (+r[50]![0]!) * Math.PI / 180, a1 = (+r[51]![0]!) * Math.PI / 180;
      polysMm.push(arcPoints(cx, cy, rad, a0, a1, true));
      i = j;
    } else if (type === 'CIRCLE') {
      const [r, j] = readEntity(i + 1);
      const cx = +r[10]![0]!, cy = +r[20]![0]!, rad = +r[40]![0]!;
      polysMm.push(arcPoints(cx, cy, rad, 0, 2 * Math.PI, true));
      i = j;
    } else if (type === 'ELLIPSE') {
      const [r, j] = readEntity(i + 1);
      const cx = +r[10]![0]!, cy = +r[20]![0]!;
      const mx = +r[11]![0]!, my = +r[21]![0]!;       // major axis vector
      const ratio = +r[40]![0]!;
      const p0 = +(r[41]?.[0] ?? 0), p1 = +(r[42]?.[0] ?? (2 * Math.PI));
      const a = Math.hypot(mx, my), b = a * ratio;
      const rot = Math.atan2(my, mx);
      const cosR = Math.cos(rot), sinR = Math.sin(rot);
      let span = p1 - p0; if (span <= 0) span += 2 * Math.PI;
      const maxStep = a > CHORD_TOL_MM ? 2 * Math.acos(Math.max(0, 1 - CHORD_TOL_MM / a)) : span;
      const n = Math.min(MAX_ARC_SEGS, Math.max(8, Math.ceil(span / maxStep)));
      const path: [number, number][] = [];
      for (let k = 0; k <= n; k++) {
        const t = p0 + (span * k) / n;
        const ex = a * Math.cos(t), ey = b * Math.sin(t);
        path.push([cx + ex * cosR - ey * sinR, cy + ex * sinR + ey * cosR]);
      }
      polysMm.push(path);
      i = j;
    } else if (type === 'SPLINE') {
      const [r, j] = readEntity(i + 1);
      const degree = +(r[71]?.[0] ?? 3);
      const knots = (r[40] || []).map(Number);
      const ctrl: [number, number][] = (r[10] || []).map((x, idx) => [+x, +(r[20]?.[idx] ?? 0)]);
      const weights = r[41] ? r[41].map(Number) : null;
      if (ctrl.length >= 2 && knots.length >= ctrl.length + degree + 1) {
        const u0 = knots[degree]!, u1 = knots[knots.length - 1 - degree]!;
        const n = Math.min(MAX_ARC_SEGS, Math.max(16, ctrl.length * 8));
        const path: [number, number][] = [];
        for (let k = 0; k <= n; k++) {
          path.push(deBoor(degree, knots, ctrl, weights, u0 + ((u1 - u0) * k) / n));
        }
        polysMm.push(path);
      } else if (r[11]) {
        // fallback: fit points only
        const fit: [number, number][] = (r[11] || []).map((x, idx) => [+x, +(r[21]?.[idx] ?? 0)]);
        if (fit.length >= 2) polysMm.push(fit);
      } else {
        warnings.push('SPLINE with insufficient data skipped');
      }
      i = j;
    } else if (type === 'ENDSEC' || type === 'EOF' || type === 'SECTION') {
      i++;
    } else {
      skipped.set(type, (skipped.get(type) || 0) + 1);
      const [, j] = readEntity(i + 1);
      i = j;
    }
  }

  for (const [t, n] of skipped) {
    if (!['SEQEND', 'VERTEX'].includes(t)) warnings.push(`${n}× ${t} skipped (unsupported)`);
  }

  // flattened mm paths → device-unit strokes (quantise-then-subtract)
  const strokes: Stroke[] = [];
  for (const path of polysMm) {
    if (path.length < 2) continue;
    const sx = u(path[0]![0]), sy = u(path[0]![1]);
    const deltas: [number, number][] = [];
    let px = sx, py = sy;
    for (let k = 1; k < path.length; k++) {
      const qx = u(path[k]![0]), qy = u(path[k]![1]);
      const dx = qx - px, dy = qy - py;
      if (dx !== 0 || dy !== 0) deltas.push([dx, dy]);
      px = qx; py = qy;
    }
    if (deltas.length) strokes.push({ start: [sx, sy], deltas });
  }
  return { strokes, warnings };
}
