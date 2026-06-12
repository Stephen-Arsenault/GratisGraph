/**
 * hershey.ts — Hershey single-stroke font (.jhf) parser.
 *
 * The engraving-native font format: single-line glyphs (no outlines), the
 * right choice for small text on a drag engraver. JHF is the 1986 usenet
 * ASCII encoding of the 1960s NBS Hershey data: per glyph, a 5-char id,
 * 3-char vertex count, then coordinate pairs encoded as chars relative to
 * 'R'; the pair " R" means pen-up. Y is down in Hershey; we flip. Glyphs
 * map to ASCII 32.. in file order. Baseline sits at Hershey y=+9.
 */
import { mmToUnits } from './round.ts';
import type { Stroke } from './gts.ts';

interface HGlyph { left: number; right: number; paths: [number, number][][] }

export function parseJhf(text: string): HGlyph[] {
  // glyph records can wrap across lines: total chars = 8 + 2*nverts
  const lines = text.split(/\r?\n/).filter((l) => l.length > 0);
  const glyphs: HGlyph[] = [];
  let i = 0;
  while (i < lines.length) {
    let rec = lines[i++]!;
    const nv = parseInt(rec.slice(5, 8), 10);
    const need = 8 + nv * 2;
    while (rec.length < need && i < lines.length) rec += lines[i++]!;
    if (rec.length < need || Number.isNaN(nv)) continue;
    const cv = (c: string) => c.charCodeAt(0) - 82;   // 'R'
    const left = cv(rec[8]!), right = cv(rec[9]!);
    const paths: [number, number][][] = [];
    let cur: [number, number][] = [];
    for (let k = 1; k < nv; k++) {
      const a = rec[8 + k * 2]!, b = rec[9 + k * 2]!;
      if (a === ' ' && b === 'R') {
        if (cur.length >= 2) paths.push(cur);
        cur = [];
      } else {
        cur.push([cv(a), cv(b)]);
      }
    }
    if (cur.length >= 2) paths.push(cur);
    glyphs.push({ left, right, paths });
  }
  return glyphs;
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

/** size = em-ish height in mm (32 Hershey units), to visually match TTF sizing. */
export function hersheyTextToStrokes(jhfText: string, text: string, sizeMm: number,
                                     xMm = 0, yMm = 0): { strokes: Stroke[]; widthMm: number; warnings: string[] } {
  const glyphs = parseJhf(jhfText);
  const scale = sizeMm / 32;
  const warnings: string[] = [];
  const strokes: Stroke[] = [];
  let penX = xMm;
  for (const ch of text) {
    const idx = ch.codePointAt(0)! - 32;
    if (idx < 0 || idx >= glyphs.length) {
      warnings.push(`No Hershey glyph for "${ch}" — skipped`);
      penX += 16 * scale;
      continue;
    }
    const g = glyphs[idx]!;
    for (const path of g.paths) {
      const poly = path.map(([hx, hy]) =>
        [penX + (hx - g.left) * scale, yMm + (9 - hy) * scale] as [number, number]);
      const s = toStroke(poly);
      if (s) strokes.push(s);
    }
    penX += (g.right - g.left) * scale;
  }
  return { strokes, widthMm: penX - xMm, warnings };
}
