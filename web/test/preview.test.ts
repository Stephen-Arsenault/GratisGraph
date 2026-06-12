/**
 * preview.test.ts — parseGts conformance + round-trip.
 * (1) TS parseGts must match Python parse_gts on every golden .m20.
 * (2) Round-trip: buildGts(strokes) parsed back recovers the absolute starts.
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { parseGts } from '../src/preview.ts';
import { buildGts, type Stroke } from '../src/gts.ts';

const here = dirname(fileURLToPath(import.meta.url));
const fx = JSON.parse(
  readFileSync(join(here, 'fixtures', 'preview_fixtures.json'), 'utf8'),
) as {
  cases: Record<string, {
    file: string;
    strokes: { start: [number, number]; points: [number, number][] }[];
    rapids: [[number, number], [number, number]][];
    bounds: [number, number, number, number];
  }>;
};

// Float compare with tolerance (mm at 0.01 resolution; values are exact /100 divisions)
function close(a: number, b: number): boolean { return Math.abs(a - b) < 1e-9; }

for (const [name, c] of Object.entries(fx.cases)) {
  test(`parseGts matches Python: ${name}`, () => {
    const data = new Uint8Array(readFileSync(join(here, 'fixtures', 'm20', `${name}.m20`)));
    const got = parseGts(data);
    assert.equal(got.strokes.length, c.strokes.length, 'stroke count');
    for (let i = 0; i < c.strokes.length; i++) {
      const a = got.strokes[i]!, b = c.strokes[i]!;
      assert.ok(close(a.start[0], b.start[0]) && close(a.start[1], b.start[1]), `start ${i}`);
      assert.equal(a.points.length, b.points.length, `points len ${i}`);
      for (let k = 0; k < b.points.length; k++) {
        assert.ok(close(a.points[k]![0], b.points[k]![0]) && close(a.points[k]![1], b.points[k]![1]),
          `point ${i},${k}`);
      }
    }
    assert.equal(got.rapids.length, c.rapids.length, 'rapid count');
    for (let i = 0; i < 4; i++) assert.ok(close(got.bounds[i]!, c.bounds[i]!), `bound ${i}`);
  });
}

test('round-trip: buildGts -> parseGts recovers absolute stroke starts', () => {
  const strokes: Stroke[] = [
    { start: [1000, 2000], deltas: [[1000, 0]] },   // (10,20)->(20,20)
    { start: [3000, 4000], deltas: [[1000, 0]] },   // (30,40)->(40,40)
  ];
  const parsed = parseGts(buildGts(strokes));
  assert.equal(parsed.strokes.length, 2);
  assert.ok(close(parsed.strokes[0]!.start[0], 10) && close(parsed.strokes[0]!.start[1], 20));
  assert.ok(close(parsed.strokes[1]!.start[0], 30) && close(parsed.strokes[1]!.start[1], 40));
  assert.ok(close(parsed.strokes[1]!.points.at(-1)![0], 40));
});
