import { test } from 'node:test';
import assert from 'node:assert/strict';
import { circleStroke, rectStroke, lineStroke } from '../src/shapes.ts';

function endpoint(s: { start: [number, number]; deltas: [number, number][] }): [number, number] {
  let [x, y] = s.start;
  for (const [dx, dy] of s.deltas) { x += dx; y += dy; }
  return [x, y];
}

test('circle d=20 at (50,50): closed, every point on radius', () => {
  const s = circleStroke(20, 50, 50);
  assert.deepEqual(endpoint(s), s.start);
  let [x, y] = s.start;
  for (const [dx, dy] of s.deltas) {
    x += dx; y += dy;
    const r = Math.hypot(x - 5000, y - 5000);
    assert.ok(Math.abs(r - 1000) <= 2, 'on radius: ' + r);
  }
});

test('square 20x20 at (50,50): 4 sides, closed, correct corners', () => {
  const s = rectStroke(20, 20, 50, 50);
  assert.equal(s.deltas.length, 4);
  assert.deepEqual(s.start, [4000, 4000]);
  assert.deepEqual(endpoint(s), [4000, 4000]);
});

test('line 20mm at (50,50): two endpoints, horizontal', () => {
  const s = lineStroke(20, 50, 50);
  assert.deepEqual(s.start, [4000, 5000]);
  assert.deepEqual(endpoint(s), [6000, 5000]);
  assert.equal(s.deltas.length, 1);
});
