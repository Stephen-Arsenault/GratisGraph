import { test } from 'node:test';
import assert from 'node:assert/strict';
import { roundHalfEven, mmToUnits } from '../src/round.ts';

test('round-half-to-even matches Python round() on midpoints', () => {
  // Pure .5 midpoints round to even
  assert.equal(roundHalfEven(0.5), 0);
  assert.equal(roundHalfEven(1.5), 2);
  assert.equal(roundHalfEven(2.5), 2);
  assert.equal(roundHalfEven(3.5), 4);
  assert.equal(roundHalfEven(-0.5), 0);
  assert.equal(roundHalfEven(-1.5), -2);
});

test('round-half-to-even matches Python on non-midpoints', () => {
  assert.equal(roundHalfEven(0.4), 0);
  assert.equal(roundHalfEven(0.6), 1);
  assert.equal(roundHalfEven(2.49), 2);
  assert.equal(roundHalfEven(2.51), 3);
});

test('mmToUnits matches Python int(round(mm*100)) — the corpus-critical cases', () => {
  // These are the exact values verified against Python earlier:
  // round(0.105*100)=10, round(0.115*100)=12, round(0.135*100)=14
  assert.equal(mmToUnits(0.105), 10);
  assert.equal(mmToUnits(0.115), 12);
  assert.equal(mmToUnits(0.135), 14);
  assert.equal(mmToUnits(10), 1000);
  assert.equal(mmToUnits(20), 2000);
  // where Math.round would DISAGREE (half-up gives 11):
  assert.notEqual(Math.round(0.105 * 100), mmToUnits(0.105));
});
