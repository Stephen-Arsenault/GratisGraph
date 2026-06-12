/**
 * optimise.test.ts — full parse -> strokes -> optimise -> buildGts conformance.
 * The optimiser is the port most likely to drift, so this byte+hash compares
 * the OPTIMISED pipeline against the Python reference. Faithful port = identical
 * decisions = identical bytes.
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { createHash } from 'node:crypto';
import { GCodeParser, gcodeToStrokes } from '../src/gcode.ts';
import { optimiseStrokes, type OptimiseOptions } from '../src/optimise.ts';
import { buildGts } from '../src/gts.ts';

const here = dirname(fileURLToPath(import.meta.url));
const fx = JSON.parse(
  readFileSync(join(here, 'fixtures', 'optimise_fixtures.json'), 'utf8'),
) as {
  cases: Record<string, {
    input: string; optimise_kwargs: Record<string, unknown>;
    strokes: [number, number, [number, number][]][];
    stroke_count: number; bytes: number; expected_hex: string; sha256: string;
  }>;
};

function hex(u: Uint8Array): string {
  return Array.from(u, (b) => b.toString(16).padStart(2, '0')).join('');
}

function optOpts(kw: Record<string, unknown>): OptimiseOptions {
  const o: OptimiseOptions = {};
  if ('merge' in kw) o.merge = kw.merge as boolean;
  if ('reorder' in kw) o.reorder = kw.reorder as boolean;
  if ('bidirectional' in kw) o.bidirectional = kw.bidirectional as boolean;
  return o;
}

for (const [name, c] of Object.entries(fx.cases)) {
  test(`optimiser pipeline byte-exact: ${name}`, () => {
    const text = readFileSync(join(here, 'fixtures', 'inputs', c.input), 'utf8');
    const moves = new GCodeParser().parse(text);
    const strokes = gcodeToStrokes(moves);
    const opt = optimiseStrokes(strokes, optOpts(c.optimise_kwargs));

    assert.equal(opt.length, c.stroke_count, 'stroke count after optimise');
    const got = opt.map((s) => [s.start[0], s.start[1], s.deltas]);
    assert.deepEqual(got, c.strokes, 'optimised strokes differ from Python');

    const blob = buildGts(opt);
    assert.equal(hex(blob), c.expected_hex, 'byte mismatch');
    assert.equal(createHash('sha256').update(blob).digest('hex'), c.sha256, 'sha mismatch');
  });
}
