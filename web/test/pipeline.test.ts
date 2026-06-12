import { test } from 'node:test';
import assert from 'node:assert/strict';
import { fromGcode, gcodeToOptimisedStrokes, strokesToGgs, ggsToStrokes } from '../src/pipeline.ts';
import { buildGts } from '../src/gts.ts';

test('fromGcode produces a valid GTS header', () => {
  const m20 = fromGcode('G21\nG90\nG0 X10 Y20\nG1 X30 Y20\n', { optimise: false });
  // magic !MM88;
  assert.deepEqual(Array.from(m20.slice(0, 6)), [0x21, 0x4d, 0x4d, 0x38, 0x38, 0x3b]);
});

test('.ggs round-trips strokes losslessly at 0.01mm', () => {
  const strokes = gcodeToOptimisedStrokes('G21\nG90\nG0 X10 Y20\nG1 X30 Y20\n', { optimise: false });
  const ggs = strokesToGgs(strokes, { name: 'test' });
  const back = ggsToStrokes(ggs);
  // building from both must yield identical bytes
  assert.equal(
    Buffer.from(buildGts(strokes)).toString('hex'),
    Buffer.from(buildGts(back)).toString('hex'),
  );
});

test('ggsToStrokes rejects non-ggs', () => {
  assert.throws(() => ggsToStrokes({ format: 'nope' } as never), /stroke file/);
});
