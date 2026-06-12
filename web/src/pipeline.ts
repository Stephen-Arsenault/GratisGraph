/**
 * pipeline.ts — high-level engine API for the UI, mirroring Python from_gcode.
 * Also handles the .ggs stroke-file format (docs/STROKE_SCHEMA.md).
 */
import { GCodeParser, gcodeToStrokes, type ParserOptions } from './gcode.ts';
import { optimiseStrokes, type OptimiseOptions } from './optimise.ts';
import { buildGts, type Stroke, type BuildOptions } from './gts.ts';

export interface FromGcodeOptions extends ParserOptions, BuildOptions, OptimiseOptions {
  optimise?: boolean;
}

/** G-code text -> strokes (parse -> optimise). Matches Python from_gcode pre-build. */
export function gcodeToOptimisedStrokes(text: string, opts: FromGcodeOptions = {}): Stroke[] {
  const moves = new GCodeParser(opts).parse(text);
  let strokes = gcodeToStrokes(moves);
  if (opts.optimise !== false) strokes = optimiseStrokes(strokes, opts);
  return strokes;
}

/** G-code text -> .m20 binary. */
export function fromGcode(text: string, opts: FromGcodeOptions = {}): Uint8Array {
  return buildGts(gcodeToOptimisedStrokes(text, opts), opts);
}

// ── .ggs stroke file (docs/STROKE_SCHEMA.md v1) ─────────────────────────────

export interface GgsFile {
  format: 'gratisgraph-strokes';
  version: 1;
  units: 'mm';
  frame: 'machine';
  params?: { speed?: number; depth?: number; lift?: number };
  metadata?: Record<string, unknown>;
  strokes: { start: [number, number]; deltas: [number, number][] }[];
}

/** Serialise strokes (device units) to a .ggs document. Strokes carry mm in the
 * file per schema, so we divide by 100; the engine works in device units. */
export function strokesToGgs(strokes: Stroke[], meta: Record<string, unknown> = {}): GgsFile {
  return {
    format: 'gratisgraph-strokes', version: 1, units: 'mm', frame: 'machine',
    metadata: { created: new Date().toISOString(), ...meta },
    strokes: strokes.map((s) => ({
      start: [s.start[0] / 100, s.start[1] / 100],
      deltas: s.deltas.map(([dx, dy]) => [dx / 100, dy / 100] as [number, number]),
    })),
  };
}

import { mmToUnits } from './round.ts';

/** Parse a .ggs document back to engine strokes (device units). */
export function ggsToStrokes(doc: GgsFile): Stroke[] {
  if (doc.format !== 'gratisgraph-strokes') {
    throw new Error('Not a GratisGraph stroke file (.ggs).');
  }
  return doc.strokes.map((s) => ({
    start: [mmToUnits(s.start[0]), mmToUnits(s.start[1])] as [number, number],
    deltas: s.deltas.map(([dx, dy]) => [mmToUnits(dx), mmToUnits(dy)] as [number, number]),
  }));
}
