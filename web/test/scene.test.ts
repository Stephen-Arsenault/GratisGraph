import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  newScene, makeObject, identityTransform, applyTransform,
  worldStrokes, strokesBounds, flattenScene, quantiseStrokes,
} from '../src/scene.ts';
import type { Stroke } from '../src/gts.ts';

const line: Stroke = { start: [1000, 1000], deltas: [[1000, 0]] };   // (10,10)->(20,10)

test('identity transform is a no-op', () => {
  const out = applyTransform(line, identityTransform(), [1500, 1000]);
  assert.deepEqual(out.start, [1000, 1000]);
  assert.deepEqual(out.deltas, [[1000, 0]]);
});

test('translate moves start, leaves deltas', () => {
  const t = { tx: 500, ty: -200, rotation: 0, scale: 1 };
  const out = applyTransform(line, t, [1500, 1000]);
  assert.deepEqual(out.start, [1500, 800]);
  assert.deepEqual(out.deltas, [[1000, 0]]);
});

test('scale 2x about center doubles length, scales deltas', () => {
  const t = { tx: 0, ty: 0, rotation: 0, scale: 2 };
  const out = applyTransform(line, t, [1500, 1000]);   // center of the line
  // start was 500 left of center -> 1000 left of center after 2x
  assert.deepEqual(out.start, [500, 1000]);
  assert.deepEqual(out.deltas, [[2000, 0]]);
});

test('rotate 90deg about center turns horizontal into vertical', () => {
  const t = { tx: 0, ty: 0, rotation: Math.PI / 2, scale: 1 };
  const out = applyTransform(line, t, [1500, 1000]);
  // delta (1000,0) rotated 90deg -> (0,1000)
  assert.equal(Math.round(out.deltas[0]![0]), 0);
  assert.equal(Math.round(out.deltas[0]![1]), 1000);
});

test('worldStrokes applies the object transform', () => {
  const obj = makeObject('o', [line], { tx: 100, ty: 100, rotation: 0, scale: 1 });
  const ws = worldStrokes(obj);
  assert.deepEqual(ws[0]!.start, [1100, 1100]);
});

test('strokesBounds covers the path', () => {
  assert.deepEqual(strokesBounds([line]), [1000, 1000, 2000, 1000]);
});

test('flattenScene concatenates all objects world strokes', () => {
  const scene = newScene();
  scene.objects.push(makeObject('a', [line]));
  scene.objects.push(makeObject('b', [line], { tx: 5000, ty: 0, rotation: 0, scale: 1 }));
  const flat = flattenScene(scene);
  assert.equal(flat.length, 2);
  assert.deepEqual(flat[1]!.start, [6000, 1000]);
});

test('quantiseStrokes: rotated path has integer deltas and zero drift', () => {
  // 100-segment unit staircase rotated 30deg -> every delta fractional
  const deltas: [number, number][] = [];
  for (let i = 0; i < 100; i++) deltas.push([100, 0], [0, 100]);
  const obj = makeObject('r', [{ start: [1000, 1000], deltas }],
    { tx: 0, ty: 0, rotation: Math.PI / 6, scale: 1 });
  const ws = worldStrokes(obj);
  const q = quantiseStrokes(ws)[0]!;
  // all integer
  assert.ok(Number.isInteger(q.start[0]) && Number.isInteger(q.start[1]));
  for (const [dx, dy] of q.deltas) {
    assert.ok(Number.isInteger(dx) && Number.isInteger(dy), 'integer deltas');
  }
  // zero drift: quantised endpoint == rounded exact endpoint (within the
  // half-unit that rounding the endpoint itself allows)
  let ex = ws[0]!.start[0], ey = ws[0]!.start[1];
  for (const [dx, dy] of ws[0]!.deltas) { ex += dx; ey += dy; }
  let qx = q.start[0], qy = q.start[1];
  for (const [dx, dy] of q.deltas) { qx += dx; qy += dy; }
  assert.ok(Math.abs(qx - ex) <= 0.5 && Math.abs(qy - ey) <= 0.5,
    'endpoint drift ' + (qx - ex) + ',' + (qy - ey));
});

test('quantiseStrokes: identity-transformed integer strokes pass through', () => {
  const s = { start: [1000, 2000] as [number, number], deltas: [[500, 0]] as [number, number][] };
  const q = quantiseStrokes([s]);
  assert.deepEqual(q, [s]);
});

test('reference objects are displayed-only: excluded from flatten', () => {
  const scene = newScene();
  scene.objects.push(makeObject('cut', [line]));
  const ref = makeObject('guide', [line], { tx: 5000, ty: 0, rotation: 0, scale: 1 });
  ref.reference = true;
  scene.objects.push(ref);
  const flat = flattenScene(scene);
  assert.equal(flat.length, 1);
  assert.deepEqual(flat[0]!.start, [1000, 1000]);   // only the cut object
});
