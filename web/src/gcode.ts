/**
 * gcode.ts — RS-274 G-Code parser + stroke extraction.
 * Byte-exact port of gts_translator.py (GCodeParser + gcode_to_strokes).
 *
 * Oracle: the from_gcode golden corpus (tests/golden/). Float math must match
 * Python: both use IEEE-754 doubles; trig (Math.sin/cos/atan2/hypot) matches
 * Python's math.*. Quantisation is mmToUnits (banker's rounding), applied as
 * quantise-then-subtract for deltas — identical to the Python _u() path.
 */
import { mmToUnits } from './round.ts';
import type { Stroke } from './gts.ts';

const UNITS_PER_MM = 100;
const WORK_ENVELOPE_MM: [number, number] = [100.0, 100.0];
const PHYSICAL_ENVELOPE_MM: [number, number] = [120.0, 120.0];

export interface GMove {
  rapid: boolean;
  x: number | null;
  y: number | null;
  z: number | null;
  feed: number | null;
}

export interface ParserOptions {
  units?: 'mm' | 'inch';
  arcSteps?: number;
  origin?: [number, number];
  flipY?: boolean;
  negateX?: boolean;
  envelopeMm?: [number, number];
}

/** Match Python's self._word: letter not preceded by A-Z, value with sign/decimal. */
function word(line: string, letter: string): number | null {
  const re = new RegExp(`(?<![A-Z])${letter}([+-]?\\d+\\.?\\d*)`, 'i');
  const m = line.match(re);
  return m ? parseFloat(m[1]!) : null;
}

function has(line: string, code: string): boolean {
  // Gn not followed by a digit or '.' — mirrors Python's (?![\d.]) / (?!\d)
  return new RegExp(`${code}(?![\\d.])`).test(line);
}
function hasModal(line: string, code: string): boolean {
  return new RegExp(`${code}(?!\\d)`).test(line);
}

export class GCodeParser {
  arcSteps: number;
  originX: number;
  originY: number;
  flipY: boolean;
  negateX: boolean;
  envelopeY: number;
  absolute = true;
  inchMode: boolean;
  x = 0.0; y = 0.0; z = 0.0;
  feed = 600.0;
  g92x = 0.0; g92y = 0.0;
  lastMotion = 'G1';
  warnings: string[] = [];

  constructor(opts: ParserOptions = {}) {
    this.arcSteps = opts.arcSteps ?? 72;
    [this.originX, this.originY] = opts.origin ?? [0.0, 0.0];
    this.flipY = opts.flipY ?? false;
    this.negateX = opts.negateX ?? false;
    this.envelopeY = (opts.envelopeMm ?? WORK_ENVELOPE_MM)[1];
    this.inchMode = opts.units === 'inch';
  }

  private toMm(v: number): number {
    return this.inchMode ? v * 25.4 : v;
  }

  parse(gcodeText: string): GMove[] {
    const moves: GMove[] = [];
    for (const rawLine of gcodeText.split(/\r?\n/)) {
      let line = rawLine.replace(/\(.*?\)/g, '');
      line = (line.split(';')[0] ?? '').trim().toUpperCase();
      if (!line) continue;

      if (hasModal(line, 'G20')) this.inchMode = true;
      if (hasModal(line, 'G21')) this.inchMode = false;
      if (hasModal(line, 'G90')) this.absolute = true;
      if (hasModal(line, 'G91')) this.absolute = false;

      if (hasModal(line, 'G18'))
        throw new Error('G18 (XZ plane arcs) not supported — M20 is 2.5D only.');
      if (hasModal(line, 'G19'))
        throw new Error('G19 (YZ plane arcs) not supported — M20 is 2.5D only.');

      if (hasModal(line, 'G92')) {
        const ox = word(line, 'X'); const oy = word(line, 'Y');
        if (ox !== null) this.g92x = this.toMm(ox);
        if (oy !== null) this.g92y = this.toMm(oy);
        continue;
      }

      if (/M0(?!\d)|M2(?!\d)|M30(?!\d)/.test(line)) {
        moves.push({ rapid: true, x: this.x, y: this.y, z: 1.0, feed: null });
        continue;
      }
      if (hasModal(line, 'M6')) {
        this.warnings.push('M6 tool change ignored — M20 is single-tool');
        continue;
      }

      const f = word(line, 'F');
      if (f !== null) this.feed = this.toMm(f);

      let hasG0 = has(line, 'G0');
      let hasG1 = has(line, 'G1');
      let hasG2 = has(line, 'G2');
      let hasG3 = has(line, 'G3');

      if (hasG0) this.lastMotion = 'G0';
      else if (hasG1) this.lastMotion = 'G1';
      else if (hasG2) this.lastMotion = 'G2';
      else if (hasG3) this.lastMotion = 'G3';

      if (!(hasG0 || hasG1 || hasG2 || hasG3)) {
        const x = word(line, 'X'); const y = word(line, 'Y');
        if (x === null && y === null) continue;
        if (this.lastMotion === 'G0') hasG0 = true;
        else if (this.lastMotion === 'G2') hasG2 = true;
        else if (this.lastMotion === 'G3') hasG3 = true;
        else hasG1 = true;
      }

      const xRaw = word(line, 'X');
      const yRaw = word(line, 'Y');
      const zRaw = word(line, 'Z');
      const iRaw = word(line, 'I');
      const jRaw = word(line, 'J');

      const resolve = (raw: number | null, current: number, g92off: number): number => {
        if (raw === null) return current;
        const v = this.toMm(raw);
        return this.absolute ? v - g92off : current + v;
      };

      const newX = resolve(xRaw, this.x, this.g92x);
      const newY = resolve(yRaw, this.y, this.g92y);
      const newZ = resolve(zRaw, this.z, 0.0);
      const iMm = iRaw !== null ? this.toMm(iRaw) : 0.0;
      const jMm = jRaw !== null ? this.toMm(jRaw) : 0.0;

      if (hasG2 || hasG3) {
        const arcMoves = this.expandArc(this.x, this.y, newX, newY, iMm, jMm, hasG2);
        for (const m of arcMoves) {
          if (m.x !== null) { m.x += this.originX; if (this.negateX) m.x = -m.x; }
          if (m.y !== null) { m.y += this.originY; if (this.flipY) m.y = this.envelopeY - m.y; }
        }
        moves.push(...arcMoves);
      } else {
        let fy = newY + this.originY;
        if (this.flipY) fy = this.envelopeY - fy;
        let fx = newX + this.originX;
        if (this.negateX) fx = -fx;
        moves.push({
          rapid: hasG0,
          x: xRaw !== null ? fx : null,
          y: yRaw !== null ? fy : null,
          z: newZ,
          feed: hasG1 ? this.feed : null,
        });
      }

      this.x = newX; this.y = newY; this.z = newZ;
    }
    return moves;
  }

  private expandArc(
    x0: number, y0: number, x1: number, y1: number,
    i: number, j: number, cw: boolean,
  ): GMove[] {
    const cx = x0 + i, cy = y0 + j;
    const r0 = Math.hypot(x0 - cx, y0 - cy);
    const ang0 = Math.atan2(y0 - cy, x0 - cx);
    let ang1 = Math.atan2(y1 - cy, x1 - cx);

    if (cw) { if (ang1 >= ang0) ang1 -= 2 * Math.PI; }
    else { if (ang1 <= ang0) ang1 += 2 * Math.PI; }

    const span = Math.abs(ang1 - ang0);
    const steps = Math.max(3, Math.ceil(span / (2 * Math.PI) * this.arcSteps));
    const result: GMove[] = [];
    for (let k = 1; k <= steps; k++) {
      const t = k / steps;
      const ang = ang0 + (ang1 - ang0) * t;
      result.push({
        rapid: false,
        x: cx + r0 * Math.cos(ang),
        y: cy + r0 * Math.sin(ang),
        z: null,
        feed: this.feed,
      });
    }
    return result;
  }
}

export interface StrokesOptions {
  scale?: number;
  zThreshold?: number;
  envelopeMm?: [number, number];
}

/** Port of gcode_to_strokes. Quantise-then-subtract for deltas (matches Python _u()). */
export function gcodeToStrokes(moves: GMove[], opts: StrokesOptions = {}): Stroke[] {
  const scale = opts.scale ?? UNITS_PER_MM;
  const zThreshold = opts.zThreshold ?? 0.0;
  const envelopeMm = opts.envelopeMm ?? WORK_ENVELOPE_MM;
  const u = (mm: number): number => mmToUnits(mm, scale);

  const [envX, envY] = envelopeMm;
  const [physX, physY] = PHYSICAL_ENVELOPE_MM;
  const allX = moves.filter((m) => m.x !== null).map((m) => m.x as number);
  const allY = moves.filter((m) => m.y !== null).map((m) => m.y as number);
  if (allX.length && allY.length) {
    const maxX = Math.max(...allX), maxY = Math.max(...allY);
    if (maxX > physX || maxY > physY) {
      throw new Error(
        `Job exceeds work envelope (${envX.toFixed(0)}x${envY.toFixed(0)}mm). ` +
        `Adjust your job origin. Home (0,0) is the front-left corner.`,
      );
    }
  }

  const strokes: Stroke[] = [];
  let curX = 0.0, curY = 0.0;
  let penDown = false;
  let sx = 0, sy = 0;
  let segBuf: [number, number][] = [];

  const flush = () => {
    if (segBuf.length) {
      strokes.push({ start: [sx, sy], deltas: segBuf });
      segBuf = [];
    }
    penDown = false;
  };

  for (const mv of moves) {
    const tx = mv.x !== null ? mv.x : curX;
    const ty = mv.y !== null ? mv.y : curY;
    const tz = mv.z !== null ? mv.z : 0.0;
    const cutting = !mv.rapid && tz <= zThreshold;

    if (!cutting) {
      flush();
    } else {
      if (!penDown) { sx = u(curX); sy = u(curY); penDown = true; }
      const dx = u(tx) - u(curX);
      const dy = u(ty) - u(curY);
      if (dx !== 0 || dy !== 0) segBuf.push([dx, dy]);
    }
    curX = tx; curY = ty;
  }
  flush();
  return strokes;
}
