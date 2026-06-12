/**
 * ttf.ts — TrueType font parser + text-to-strokes, from scratch, zero deps.
 *
 * Parses the sfnt container (head/maxp/cmap/loca/glyf/hhea/hmtx), expands
 * TrueType quadratic outlines (simple + composite glyphs incl. 2x2
 * transforms), flattens to polylines at chord tolerance, lays out text
 * left-to-right by advance width. Output: device-unit strokes.
 *
 * Scope v1: glyf outlines only — no CFF/.otf, no kerning, no hinting, no
 * variable axes (a variable TTF's default instance parses fine: glyf holds
 * the default outlines). No Python oracle — geometric tests on real fonts.
 */
import { mmToUnits } from './round.ts';
import type { Stroke } from './gts.ts';

const CHORD_TOL_MM = 0.02;

export interface TtfText { strokes: Stroke[]; widthMm: number; warnings: string[] }
interface GlyphPoint { x: number; y: number; on: boolean }
type Contour = GlyphPoint[];

export interface Font {
  view: DataView; unitsPerEm: number; numGlyphs: number;
  locaLong: boolean; loca: number; glyf: number;
  cmapSub: number; cmapFmt: number; advances: Uint16Array;
}

function u16(v: DataView, o: number) { return v.getUint16(o, false); }
function s16(v: DataView, o: number) { return v.getInt16(o, false); }
function u32(v: DataView, o: number) { return v.getUint32(o, false); }

export function parseFont(data: Uint8Array): Font {
  const view = new DataView(data.buffer, data.byteOffset, data.byteLength);
  const magic = u32(view, 0);
  if (magic !== 0x00010000 && magic !== 0x74727565) {
    throw new Error('Not a TrueType font (.ttf). OpenType CFF (.otf) is not supported.');
  }
  const numTables = u16(view, 4);
  const tables: Record<string, number> = {};
  for (let i = 0; i < numTables; i++) {
    const off = 12 + i * 16;
    const tag = String.fromCharCode(view.getUint8(off), view.getUint8(off + 1),
                                    view.getUint8(off + 2), view.getUint8(off + 3));
    tables[tag] = u32(view, off + 8);
  }
  for (const t of ['head', 'maxp', 'cmap', 'loca', 'glyf', 'hhea', 'hmtx']) {
    if (!(t in tables)) throw new Error('Font missing required table: ' + t);
  }
  const head = tables['head']!, maxp = tables['maxp']!, hhea = tables['hhea']!;
  const unitsPerEm = u16(view, head + 18);
  const locaLong = s16(view, head + 50) === 1;
  const numGlyphs = u16(view, maxp + 4);
  const numHMetrics = u16(view, hhea + 34);

  const advances = new Uint16Array(numGlyphs);
  const hmtx = tables['hmtx']!;
  let last = 0;
  for (let g = 0; g < numGlyphs; g++) {
    if (g < numHMetrics) last = u16(view, hmtx + g * 4);
    advances[g] = last;
  }

  // cmap subtable preference: (3,10) fmt12 > (3,1) fmt4 > unicode platform 0
  const cmap = tables['cmap']!;
  const nSub = u16(view, cmap + 2);
  let best = -1, bestScore = -1, bestFmt = 0;
  for (let i = 0; i < nSub; i++) {
    const plat = u16(view, cmap + 4 + i * 8);
    const enc = u16(view, cmap + 6 + i * 8);
    const off = cmap + u32(view, cmap + 8 + i * 8);
    const fmt = u16(view, off);
    let score = -1;
    if (plat === 3 && enc === 10 && fmt === 12) score = 3;
    else if (plat === 3 && enc === 1 && fmt === 4) score = 2;
    else if (plat === 0 && (fmt === 4 || fmt === 12)) score = 1;
    if (score > bestScore) { bestScore = score; best = off; bestFmt = fmt; }
  }
  if (best === -1) throw new Error('No usable Unicode cmap subtable in font.');
  return { view, unitsPerEm, numGlyphs, locaLong,
           loca: tables['loca']!, glyf: tables['glyf']!,
           cmapSub: best, cmapFmt: bestFmt, advances };
}

export function glyphForChar(f: Font, code: number): number {
  const v = f.view, t = f.cmapSub;
  if (f.cmapFmt === 4) {
    const segX2 = u16(v, t + 6);
    const endBase = t + 14, startBase = endBase + segX2 + 2;
    const deltaBase = startBase + segX2, rangeBase = deltaBase + segX2;
    for (let i = 0; i < segX2 / 2; i++) {
      if (u16(v, endBase + i * 2) >= code) {
        const start = u16(v, startBase + i * 2);
        if (start > code) return 0;
        const delta = s16(v, deltaBase + i * 2);
        const ro = u16(v, rangeBase + i * 2);
        if (ro === 0) return (code + delta) & 0xffff;
        const addr = rangeBase + i * 2 + ro + 2 * (code - start);
        const gid = u16(v, addr);
        return gid === 0 ? 0 : (gid + delta) & 0xffff;
      }
    }
    return 0;
  }
  // format 12: sequential map groups
  const nGroups = u32(v, t + 12);
  for (let i = 0; i < nGroups; i++) {
    const g = t + 16 + i * 12;
    const s = u32(v, g), e = u32(v, g + 4);
    if (code >= s && code <= e) return u32(v, g + 8) + (code - s);
    if (code < s) return 0;
  }
  return 0;
}

function glyphOffset(f: Font, gid: number): [number, number] {
  const v = f.view;
  if (f.locaLong) return [u32(v, f.loca + gid * 4), u32(v, f.loca + gid * 4 + 4)];
  return [u16(v, f.loca + gid * 2) * 2, u16(v, f.loca + gid * 2 + 2) * 2];
}

/** Parse a glyph (recursing into composites) -> contours in font units. */
function glyphContours(f: Font, gid: number, depth = 0): Contour[] {
  if (depth > 6) return [];                       // composite recursion guard
  const [o0, o1] = glyphOffset(f, gid);
  if (o1 <= o0) return [];                        // empty glyph (e.g. space)
  const v = f.view;
  const g = f.glyf + o0;
  const nc = s16(v, g);

  if (nc >= 0) {
    // ── simple glyph ──
    const endPts: number[] = [];
    for (let i = 0; i < nc; i++) endPts.push(u16(v, g + 10 + i * 2));
    const nPts = nc === 0 ? 0 : endPts[nc - 1]! + 1;
    let p = g + 10 + nc * 2;
    p += 2 + u16(v, p);                           // skip instructions

    const flags: number[] = [];
    while (flags.length < nPts) {
      const fl = v.getUint8(p++);
      flags.push(fl);
      if (fl & 8) { let rep = v.getUint8(p++); while (rep--) flags.push(fl); }
    }
    const xs: number[] = []; let x = 0;
    for (let i = 0; i < nPts; i++) {
      const fl = flags[i]!;
      if (fl & 2) { const d = v.getUint8(p++); x += (fl & 16) ? d : -d; }
      else if (!(fl & 16)) { x += s16(v, p); p += 2; }
      xs.push(x);
    }
    const ys: number[] = []; let y = 0;
    for (let i = 0; i < nPts; i++) {
      const fl = flags[i]!;
      if (fl & 4) { const d = v.getUint8(p++); y += (fl & 32) ? d : -d; }
      else if (!(fl & 32)) { y += s16(v, p); p += 2; }
      ys.push(y);
    }
    const contours: Contour[] = [];
    let start = 0;
    for (let c = 0; c < nc; c++) {
      const end = endPts[c]!;
      const ct: Contour = [];
      for (let i = start; i <= end; i++) {
        ct.push({ x: xs[i]!, y: ys[i]!, on: (flags[i]! & 1) === 1 });
      }
      if (ct.length >= 2) contours.push(ct);
      start = end + 1;
    }
    return contours;
  }

  // ── composite glyph ──
  const out: Contour[] = [];
  let p = g + 10;
  while (true) {
    const flags = u16(v, p), childGid = u16(v, p + 2);
    p += 4;
    let dx = 0, dy = 0;
    if (flags & 0x02) {                            // ARGS_ARE_XY_VALUES
      if (flags & 0x01) { dx = s16(v, p); dy = s16(v, p + 2); p += 4; }
      else { dx = (v.getUint8(p) << 24) >> 24; dy = (v.getUint8(p + 1) << 24) >> 24; p += 2; }
    } else {
      p += (flags & 0x01) ? 4 : 2;                 // point matching: unsupported, offset 0
    }
    let a = 1, b = 0, c = 0, d = 1;
    const f2 = (o: number) => s16(v, o) / 16384;
    if (flags & 0x08) { a = d = f2(p); p += 2; }                       // WE_HAVE_A_SCALE
    else if (flags & 0x40) { a = f2(p); d = f2(p + 2); p += 4; }       // X_AND_Y_SCALE
    else if (flags & 0x80) { a = f2(p); b = f2(p + 2); c = f2(p + 4); d = f2(p + 6); p += 8; }
    for (const ct of glyphContours(f, childGid, depth + 1)) {
      out.push(ct.map((pt) => ({
        x: a * pt.x + c * pt.y + dx,
        y: b * pt.x + d * pt.y + dy,
        on: pt.on,
      })));
    }
    if (!(flags & 0x20)) break;                    // MORE_COMPONENTS
  }
  return out;
}

/** Flatten one quadratic Bezier into `pts` (excluding p0). */
function flattenQuad(pts: [number, number][], x0: number, y0: number,
                     cx: number, cy: number, x1: number, y1: number, tol: number): void {
  const dev = Math.hypot(cx - (x0 + x1) / 2, cy - (y0 + y1) / 2);
  const n = Math.max(1, Math.min(32, Math.ceil(Math.sqrt(dev / (4 * tol))) * 2));
  for (let k = 1; k <= n; k++) {
    const t = k / n, mt = 1 - t;
    pts.push([mt * mt * x0 + 2 * mt * t * cx + t * t * x1,
              mt * mt * y0 + 2 * mt * t * cy + t * t * y1]);
  }
}

/** Expand a TrueType contour (on/off points) to a closed polyline. */
function contourToPolyline(ct: Contour, tol: number): [number, number][] {
  // Phase 1: insert implied on-curve midpoints between consecutive
  // off-curve controls, so the walk only ever sees on,(off,on) patterns.
  const exp: GlyphPoint[] = [];
  const n = ct.length;
  for (let i = 0; i < n; i++) {
    const p = ct[i]!, q = ct[(i + 1) % n]!;
    exp.push(p);
    if (!p.on && !q.on) exp.push({ x: (p.x + q.x) / 2, y: (p.y + q.y) / 2, on: true });
  }
  // rotate to start on-curve (one must exist after phase 1 for any real contour)
  let s0 = exp.findIndex((p) => p.on);
  if (s0 === -1) return [];
  const m = exp.length;
  const at = (i: number) => exp[(s0 + i) % m]!;

  // Phase 2: walk on -> on (line) and on -> off -> on (quadratic).
  const out: [number, number][] = [[at(0).x, at(0).y]];
  let i = 0;
  while (i < m) {
    const nxt = at(i + 1);
    if (nxt.on) {
      out.push([nxt.x, nxt.y]);
      i += 1;
    } else {
      const end2 = at(i + 2);   // guaranteed on-curve by phase 1
      const prev = out[out.length - 1]!;
      flattenQuad(out, prev[0], prev[1], nxt.x, nxt.y, end2.x, end2.y, tol);
      i += 2;
    }
  }
  // ensure closed
  const first = out[0]!, lastP = out[out.length - 1]!;
  if (first[0] !== lastP[0] || first[1] !== lastP[1]) out.push([first[0], first[1]]);
  return out;
}

function toStroke(pointsMm: [number, number][]): Stroke | null {
  if (pointsMm.length < 2) return null;
  const sx = mmToUnits(pointsMm[0]![0]), sy = mmToUnits(pointsMm[0]![1]);
  const deltas: [number, number][] = [];
  let px = sx, py = sy;
  for (let i = 1; i < pointsMm.length; i++) {
    const qx = mmToUnits(pointsMm[i]![0]), qy = mmToUnits(pointsMm[i]![1]);
    if (qx !== px || qy !== py) deltas.push([qx - px, qy - py]);
    px = qx; py = qy;
  }
  return deltas.length ? { start: [sx, sy], deltas } : null;
}

/**
 * Lay out text as outline strokes. size = em height in mm; baseline at yMm;
 * text starts at xMm. TTF y-up == machine y-up, no flip needed.
 */
export function ttfTextToStrokes(data: Uint8Array, text: string, sizeMm: number,
                                 xMm = 0, yMm = 0): TtfText {
  const f = parseFont(data);
  const scale = sizeMm / f.unitsPerEm;            // mm per font unit
  const tol = CHORD_TOL_MM / scale;               // chord tolerance in font units
  const warnings: string[] = [];
  const strokes: Stroke[] = [];
  let penX = xMm;

  for (const ch of text) {
    const code = ch.codePointAt(0)!;
    const gid = glyphForChar(f, code);
    if (gid === 0 && code !== 0x20) {
      warnings.push(`No glyph for "${ch}" — skipped`);
      penX += (f.unitsPerEm / 3) * scale;
      continue;
    }
    if (gid !== 0) {
      for (const ct of glyphContours(f, gid)) {
        const poly = contourToPolyline(ct, tol)
          .map(([gx, gy]) => [penX + gx * scale, yMm + gy * scale] as [number, number]);
        const s = toStroke(poly);
        if (s) strokes.push(s);
      }
    }
    penX += (f.advances[gid] ?? f.unitsPerEm / 3) * scale;
  }
  return { strokes, widthMm: penX - xMm, warnings };
}
