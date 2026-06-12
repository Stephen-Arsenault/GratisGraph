/**
 * optimise.ts — toolpath optimisation. Faithful port of gts_optimise.py.
 *
 * Byte-equality of optimised corpus cases depends on IDENTICAL DECISIONS, so
 * this preserves the Python operation order, comparison operators (<=, and the
 * 2-opt `< best - 0.5` threshold), and float math (Math.hypot == math.hypot)
 * exactly. Ported faithfully, not idiomatically.
 *
 * Strokes here are the same shape as the binary builder's: {start, deltas} in
 * device units.
 */
import type { Stroke } from './gts.ts';

type Point = [number, number];

function dist(a: Point, b: Point): number {
  return Math.hypot(a[0] - b[0], a[1] - b[1]);
}

function endpoint(s: Stroke): Point {
  let ex = s.start[0], ey = s.start[1];
  for (const [dx, dy] of s.deltas) { ex += dx; ey += dy; }
  return [ex, ey];
}

function reverse(s: Stroke): Stroke {
  const [ex, ey] = endpoint(s);
  if (ex === s.start[0] && ey === s.start[1]) return s;   // closed loop: no-op
  const newDeltas: [number, number][] = [];
  for (let i = s.deltas.length - 1; i >= 0; i--) {
    const [dx, dy] = s.deltas[i]!;
    newDeltas.push([-dx, -dy]);
  }
  return { start: [ex, ey], deltas: newDeltas };
}

function heading(s: Stroke): number {
  const [ex, ey] = endpoint(s);
  return Math.atan2(ey - s.start[1], ex - s.start[0]);
}

function isParallel(a: Stroke, b: Stroke, angleTolDeg: number, spacingTol: number): boolean {
  const ha = heading(a), hb = heading(b);
  // Python: abs(degrees(ha - hb)) % 180
  let diff = Math.abs((ha - hb) * 180 / Math.PI) % 180;
  if (diff > angleTolDeg && diff < 180 - angleTolDeg) return false;
  const aEnd = endpoint(a), bEnd = endpoint(b);
  const am: Point = [(a.start[0] + aEnd[0]) / 2, (a.start[1] + aEnd[1]) / 2];
  const bm: Point = [(b.start[0] + bEnd[0]) / 2, (b.start[1] + bEnd[1]) / 2];
  return dist(am, bm) <= spacingTol;
}

function applyReversal(strokes: Stroke[]): Stroke[] {
  if (!strokes.length) return strokes;
  const result: Stroke[] = [];
  let cur: Point = [0, 0];
  for (let s of strokes) {
    const start: Point = [s.start[0], s.start[1]];
    const end = endpoint(s);
    if (dist(cur, end) < dist(cur, start)) s = reverse(s);
    result.push(s);
    cur = endpoint(s);
  }
  return result;
}

function applyMerging(strokes: Stroke[], tolUnits: number): Stroke[] {
  if (strokes.length < 2) return strokes;
  const result: Stroke[] = [strokes[0]!];
  for (let idx = 1; idx < strokes.length; idx++) {
    const stroke = strokes[idx]!;
    const prev = result[result.length - 1]!;
    const prevEnd = endpoint(prev);
    const currStart: Point = [stroke.start[0], stroke.start[1]];
    if (dist(prevEnd, currStart) <= tolUnits) {
      const gapDx = currStart[0] - prevEnd[0];
      const gapDy = currStart[1] - prevEnd[1];
      const bridge: [number, number][] = (gapDx || gapDy) ? [[gapDx, gapDy]] : [];
      result[result.length - 1] = {
        start: [prev.start[0], prev.start[1]],
        deltas: [...prev.deltas, ...bridge, ...stroke.deltas],
      };
    } else {
      result.push(stroke);
    }
  }
  return result;
}

function nnOrder(strokes: Stroke[]): Stroke[] {
  if (!strokes.length) return strokes;
  const remaining = [...strokes];
  const ordered: Stroke[] = [];
  let cur: Point = [0, 0];
  while (remaining.length) {
    let bestIdx = 0, bestDist = Infinity, bestRev = false;
    for (let i = 0; i < remaining.length; i++) {
      const s = remaining[i]!;
      const start: Point = [s.start[0], s.start[1]];
      const end = endpoint(s);
      const dStart = dist(cur, start), dEnd = dist(cur, end);
      let d: number, rev: boolean;
      if (dStart <= dEnd) { d = dStart; rev = false; }
      else { d = dEnd; rev = true; }
      if (d < bestDist) { bestDist = d; bestIdx = i; bestRev = rev; }
    }
    let s = remaining.splice(bestIdx, 1)[0]!;
    if (bestRev) s = reverse(s);
    ordered.push(s);
    cur = endpoint(s);
  }
  return ordered;
}

function tourDistance(strokes: Stroke[]): number {
  if (!strokes.length) return 0.0;
  let total = dist([0, 0], [strokes[0]!.start[0], strokes[0]!.start[1]]);
  for (let i = 0; i < strokes.length - 1; i++) {
    total += dist(endpoint(strokes[i]!), [strokes[i + 1]!.start[0], strokes[i + 1]!.start[1]]);
  }
  return total;
}

function twoOpt(strokes: Stroke[]): Stroke[] {
  let improved = true;
  let best = [...strokes];
  let bestDist = tourDistance(best);
  while (improved) {
    improved = false;
    for (let i = 0; i < best.length - 1; i++) {
      for (let j = i + 2; j < best.length; j++) {
        // reverse tour segment best[i+1 .. j], direction-reversing each stroke
        const mid: Stroke[] = [];
        for (let k = j; k >= i + 1; k--) mid.push(reverse(best[k]!));
        const candidate = [...best.slice(0, i + 1), ...mid, ...best.slice(j + 1)];
        const candDist = tourDistance(candidate);
        if (candDist < bestDist - 0.5) {
          best = candidate; bestDist = candDist; improved = true;
        }
      }
    }
  }
  return best;
}

function applyBidirectional(strokes: Stroke[], angleTolDeg: number, spacingTol: number): Stroke[] {
  if (strokes.length < 2) return strokes;
  const result = [...strokes];
  let i = 0;
  while (i < result.length) {
    let runEnd = i + 1;
    while (runEnd < result.length &&
           isParallel(result[runEnd - 1]!, result[runEnd]!, angleTolDeg, spacingTol)) {
      runEnd++;
    }
    if (runEnd - i >= 2) {
      for (let k = i; k < runEnd; k++) {
        if ((k - i) % 2 === 1) result[k] = reverse(result[k]!);
      }
    }
    i = runEnd;
  }
  return result;
}

export interface OptimiseOptions {
  merge?: boolean;
  mergeToleranceMm?: number;
  unitsPerMm?: number;
  reorder?: boolean;
  bidirectional?: boolean;
  angleTolDeg?: number;
  spacingTolMm?: number;
}

export function optimiseStrokes(strokes: Stroke[], opts: OptimiseOptions = {}): Stroke[] {
  if (!strokes.length) return strokes;
  const merge = opts.merge ?? true;
  const mergeToleranceMm = opts.mergeToleranceMm ?? 0.1;
  const unitsPerMm = opts.unitsPerMm ?? 100;
  const reorder = opts.reorder ?? true;
  const bidirectional = opts.bidirectional ?? true;
  const angleTolDeg = opts.angleTolDeg ?? 5.0;
  const spacingTolMm = opts.spacingTolMm ?? 0.5;

  const tolUnits = mergeToleranceMm * unitsPerMm;
  const spacingUnits = spacingTolMm * unitsPerMm;

  let result = applyReversal(strokes);
  if (merge) result = applyMerging(result, tolUnits);
  if (reorder) result = twoOpt(nnOrder(result));
  if (bidirectional) result = applyBidirectional(result, angleTolDeg, spacingUnits);
  return result;
}
