/**
 * translator.test.ts — full parse -> gcodeToStrokes -> buildGts conformance.
 * Oracle: Python reference (no optimiser), from the golden corpus inputs.
 * Validates strokes structure AND final bytes/hash, byte-for-byte.
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { createHash } from 'node:crypto';
import { GCodeParser, gcodeToStrokes, type ParserOptions } from '../src/gcode.ts';
import { buildGts } from '../src/gts.ts';

const here = dirname(fileURLToPath(import.meta.url));
const fx = JSON.parse(
  readFileSync(join(here, 'fixtures', 'translator_fixtures.json'), 'utf8'),
) as {
  cases: Record<string, {
    input: string; units: string; parser_kwargs: Record<string, unknown>;
    strokes: [number, number, [number, number][]][];
    stroke_count: number; bytes: number; expected_hex: string; sha256: string;
  }>;
};

function hex(u: Uint8Array): string {
  return Array.from(u, (b) => b.toString(16).padStart(2, '0')).join('');
}

function parserOpts(units: string, kw: Record<string, unknown>): ParserOptions {
  const o: ParserOptions = { units: units as 'mm' | 'inch' };
  if ('origin' in kw) o.origin = kw.origin as [number, number];
  if ('flip_y' in kw) o.flipY = kw.flip_y as boolean;
  if ('negate_x' in kw) o.negateX = kw.negate_x as boolean;
  return o;
}

for (const [name, c] of Object.entries(fx.cases)) {
  test(`translator pipeline byte-exact: ${name}`, () => {
    const text = readFileSync(join(here, 'fixtures', 'inputs', c.input), 'utf8');
    const parser = new GCodeParser(parserOpts(c.units, c.parser_kwargs));
    const moves = parser.parse(text);
    const strokes = gcodeToStrokes(moves);

    // strokes structure must match exactly
    assert.equal(strokes.length, c.stroke_count, 'stroke count');
    const gotStrokes = strokes.map((s) => [s.start[0], s.start[1], s.deltas]);
    assert.deepEqual(gotStrokes, c.strokes, 'strokes differ from Python');

    // and the final binary must be byte- and hash-identical
    const blob = buildGts(strokes);
    assert.equal(hex(blob), c.expected_hex, 'byte mismatch');
    assert.equal(createHash('sha256').update(blob).digest('hex'), c.sha256, 'sha mismatch');
  });
}
