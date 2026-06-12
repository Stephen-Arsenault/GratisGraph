import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { ttfTextToStrokes } from '../src/ttf.ts';
import { hersheyTextToStrokes } from '../src/hershey.ts';

const here = dirname(fileURLToPath(import.meta.url));
const fontDir = join(here, '..', 'app', 'fonts');
const tinos = new Uint8Array(readFileSync(join(fontDir, 'Tinos-Regular.ttf')));
const arimo = new Uint8Array(readFileSync(join(fontDir, 'Arimo-Regular.ttf')));
const vibes = new Uint8Array(readFileSync(join(fontDir, 'GreatVibes-Regular.ttf')));
const jhf = readFileSync(join(fontDir, 'futural.jhf'), 'utf8');
const gorton = new Uint8Array(readFileSync(join(fontDir, 'OpenGorton-Regular.ttf')));

function bounds(strokes: { start: [number, number]; deltas: [number, number][] }[]) {
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
  for (const s of strokes) {
    let [x, y] = s.start;
    const v = () => { minX = Math.min(minX, x); maxX = Math.max(maxX, x);
                      minY = Math.min(minY, y); maxY = Math.max(maxY, y); };
    v();
    for (const [dx, dy] of s.deltas) { x += dx; y += dy; v(); }
  }
  return [minX, minY, maxX, maxY] as [number, number, number, number];
}
function closed(s: { start: [number, number]; deltas: [number, number][] }) {
  let [x, y] = s.start;
  for (const [dx, dy] of s.deltas) { x += dx; y += dy; }
  return x === s.start[0] && y === s.start[1];
}

test('Tinos "I" at 10mm: one closed contour, cap height ~7mm, on baseline', () => {
  const r = ttfTextToStrokes(tinos, 'I', 10, 0, 0);
  assert.equal(r.warnings.length, 0);
  assert.ok(r.strokes.length >= 1, 'has strokes');
  assert.ok(r.strokes.every(closed), 'all contours closed');
  const [, minY, , maxY] = bounds(r.strokes);
  const capMm = (maxY - minY) / 100;
  assert.ok(capMm > 6 && capMm < 8, 'cap height ' + capMm + 'mm in 6..8');
  assert.ok(Math.abs(minY) <= 5, 'sits on baseline, minY=' + minY);
});

test('Tinos width accumulates: "III" ~3x "I"', () => {
  const w1 = ttfTextToStrokes(tinos, 'I', 10).widthMm;
  const w3 = ttfTextToStrokes(tinos, 'III', 10).widthMm;
  assert.ok(Math.abs(w3 - 3 * w1) < 0.01, w3 + ' vs ' + 3 * w1);
});

test('space advances without strokes: "a a" wider than "aa"', () => {
  const aa = ttfTextToStrokes(tinos, 'aa', 10);
  const a_a = ttfTextToStrokes(tinos, 'a a', 10);
  assert.equal(a_a.strokes.length, aa.strokes.length);
  assert.ok(a_a.widthMm > aa.widthMm + 1, a_a.widthMm + ' > ' + aa.widthMm);
});

test('Tinos composite glyph "\u00c1" parses to >= 3 contours (A + accent)', () => {
  const r = ttfTextToStrokes(tinos, '\u00c1', 10);
  assert.equal(r.warnings.length, 0);
  assert.ok(r.strokes.length >= 3, r.strokes.length + ' contours');
  assert.ok(r.strokes.every(closed));
});

test('Arimo (variable TTF, default instance) renders "Hello"', () => {
  const r = ttfTextToStrokes(arimo, 'Hello', 10);
  assert.equal(r.warnings.length, 0);
  assert.ok(r.strokes.length >= 7, r.strokes.length + ' contours');
  assert.ok(r.widthMm > 10 && r.widthMm < 40, 'width ' + r.widthMm);
});

test('Great Vibes (script, curve-heavy) renders "ab" closed', () => {
  const r = ttfTextToStrokes(vibes, 'ab', 14);
  assert.equal(r.warnings.length, 0);
  assert.ok(r.strokes.length >= 2);
  assert.ok(r.strokes.every(closed), 'script contours close');
});

test('rejects an OTF/CFF or garbage buffer with a clear error', () => {
  assert.throws(() => ttfTextToStrokes(new Uint8Array([0x4f, 0x54, 0x54, 0x4f]), 'x', 10),
    /not supported|TrueType/i);
});

test('Hershey "I": single-stroke (NOT closed), sits on baseline', () => {
  const r = hersheyTextToStrokes(jhf, 'I', 10, 0, 0);
  assert.equal(r.warnings.length, 0);
  assert.ok(r.strokes.length >= 1);
  assert.ok(r.strokes.some((s) => !closed(s)), 'single-line, not an outline');
  const [, minY, , maxY] = bounds(r.strokes);
  assert.ok(Math.abs(minY) <= 5, 'baseline at 0, minY=' + minY);
  const capMm = (maxY - minY) / 100;
  assert.ok(capMm > 5 && capMm < 8, 'cap ' + capMm + 'mm');
});

test('Hershey width accumulates and text renders', () => {
  const w1 = hersheyTextToStrokes(jhf, 'A', 10).widthMm;
  const w2 = hersheyTextToStrokes(jhf, 'AB', 10).widthMm;
  assert.ok(w2 > w1 + 1, w2 + ' > ' + w1);
  const r = hersheyTextToStrokes(jhf, 'Hello 123', 10);
  assert.equal(r.warnings.length, 0);
  assert.ok(r.strokes.length >= 9);
});

test('Open Gorton (OTF-converted TTF) renders closed contours', () => {
  const r = ttfTextToStrokes(gorton, 'GORTON M20', 10);
  assert.equal(r.warnings.length, 0);
  assert.ok(r.strokes.length >= 10, r.strokes.length + ' contours');
  assert.ok(r.strokes.every(closed), 'converted quadratic contours close');
});
