import { test } from 'node:test';
import assert from 'node:assert/strict';
import { parseDxf } from '../src/dxf.ts';

const dxf = (entities: string, header = '') => ['0','SECTION','2','HEADER',
  header,'0','ENDSEC','0','SECTION','2','ENTITIES',entities,'0','ENDSEC','0','EOF',''
].filter((l) => l !== '').join('\n') + '\n';

const E = (...lines: (string | number)[]) => lines.join('\n');

function endpoint(s: { start: [number, number]; deltas: [number, number][] }): [number, number] {
  let [x, y] = s.start;
  for (const [dx, dy] of s.deltas) { x += dx; y += dy; }
  return [x, y];
}

test('LINE: 10,10 -> 30,20 mm in device units', () => {
  const r = parseDxf(dxf(E(0,'LINE',10,'10.0',20,'10.0',11,'30.0',21,'20.0')));
  assert.equal(r.strokes.length, 1);
  assert.deepEqual(r.strokes[0]!.start, [1000, 1000]);
  assert.deepEqual(endpoint(r.strokes[0]!), [3000, 2000]);
});

test('$INSUNITS=1 (inches) scales 1in -> 2540 units', () => {
  const r = parseDxf(dxf(E(0,'LINE',10,0,20,0,11,'1.0',21,0), E(9,'$INSUNITS',70,1)));
  assert.deepEqual(endpoint(r.strokes[0]!), [2540, 0]);
});

test('closed LWPOLYLINE square 0,0..20,20 returns to start', () => {
  const r = parseDxf(dxf(E(0,'LWPOLYLINE',90,4,70,1,
    10,0,20,0, 10,20,20,0, 10,20,20,20, 10,0,20,20)));
  assert.equal(r.strokes.length, 1);
  const s = r.strokes[0]!;
  assert.deepEqual(s.start, [0, 0]);
  assert.deepEqual(endpoint(s), [0, 0]);
  assert.equal(s.deltas.length, 4);
});

test('LWPOLYLINE bulge=+1 (CCW per DXF spec) bows BELOW the chord', () => {
  // Positive bulge = CCW sweep (AutoCAD: "negative if the arc goes clockwise").
  // CCW semicircle (0,0)->(20,0) necessarily passes through (10,-10).
  const r = parseDxf(dxf(E(0,'LWPOLYLINE',90,2,70,0,
    10,0,20,0,42,'1.0', 10,20,20,0)));
  const s = r.strokes[0]!;
  assert.deepEqual(s.start, [0, 0]);
  assert.deepEqual(endpoint(s), [2000, 0]);
  let [x, y] = s.start; let minY = 0; let apexX = 0;
  for (const [dx, dy] of s.deltas) { x += dx; y += dy; if (y < minY) { minY = y; apexX = x; } }
  assert.ok(Math.abs(minY + 1000) <= 3, 'apex y ' + minY + ' should be ~-1000');
  assert.ok(Math.abs(apexX - 1000) <= 80, 'apex x ' + apexX + ' within half a chord step of 1000');
});

test('LWPOLYLINE bulge=-1 (CW) bows ABOVE the chord', () => {
  const r = parseDxf(dxf(E(0,'LWPOLYLINE',90,2,70,0,
    10,0,20,0,42,'-1.0', 10,20,20,0)));
  const s = r.strokes[0]!;
  assert.deepEqual(s.start, [0, 0]);
  assert.deepEqual(endpoint(s), [2000, 0]);
  let [x, y] = s.start; let maxY = 0; let apexX = 0;
  for (const [dx, dy] of s.deltas) { x += dx; y += dy; if (y > maxY) { maxY = y; apexX = x; } }
  assert.ok(Math.abs(maxY - 1000) <= 3, 'apex y ' + maxY + ' should be ~1000');
  assert.ok(Math.abs(apexX - 1000) <= 80, 'apex x ' + apexX + ' within half a chord step of 1000');
});

test('ARC 90..180deg r=10 at (50,50): start (50,60), end (40,50)', () => {
  const r = parseDxf(dxf(E(0,'ARC',10,50,20,50,40,10,50,90,51,180)));
  const s = r.strokes[0]!;
  assert.deepEqual(s.start, [5000, 6000]);
  assert.deepEqual(endpoint(s), [4000, 5000]);
});

test('CIRCLE r=5 at (10,10) closes and stays on radius', () => {
  const r = parseDxf(dxf(E(0,'CIRCLE',10,10,20,10,40,5)));
  const s = r.strokes[0]!;
  assert.deepEqual(endpoint(s), s.start);
  let [x, y] = s.start;
  for (const [dx, dy] of s.deltas) {
    x += dx; y += dy;
    const rr = Math.hypot(x - 1000, y - 1000);
    assert.ok(Math.abs(rr - 500) <= 3, 'on radius: ' + rr);
  }
});

test('ELLIPSE a=20 b=10 at origin: bounds reach +-a, +-b within tolerance', () => {
  const r = parseDxf(dxf(E(0,'ELLIPSE',10,0,20,0,11,20,21,0,40,'0.5',41,0,42,'6.283185307')));
  const s = r.strokes[0]!;
  let [x, y] = s.start;
  let minX = x, maxX = x, minY = y, maxY = y;
  for (const [dx, dy] of s.deltas) {
    x += dx; y += dy;
    minX = Math.min(minX, x); maxX = Math.max(maxX, x);
    minY = Math.min(minY, y); maxY = Math.max(maxY, y);
  }
  // sample points lie ON the ellipse; extremes approach +-a,+-b within chord step
  assert.ok(Math.abs(maxX - 2000) <= 5 && Math.abs(minX + 2000) <= 5, 'x extent ' + minX + '..' + maxX);
  assert.ok(Math.abs(maxY - 1000) <= 5 && Math.abs(minY + 1000) <= 5, 'y extent ' + minY + '..' + maxY);
});

test('SPLINE clamped degree-2: endpoints exact, midpoint ~(10,10)', () => {
  const r = parseDxf(dxf(E(0,'SPLINE',71,2,
    40,0,40,0,40,0,40,1,40,1,40,1,
    10,0,20,0, 10,10,20,20, 10,20,20,0)));
  const s = r.strokes[0]!;
  assert.deepEqual(s.start, [0, 0]);
  assert.deepEqual(endpoint(s), [2000, 0]);
  let [x, y] = s.start; let best = 1e9; let bestY = 0;
  for (const [dx, dy] of s.deltas) { x += dx; y += dy; if (Math.abs(x - 1000) < best) { best = Math.abs(x - 1000); bestY = y; } }
  assert.ok(Math.abs(bestY - 1000) <= 12, 'midpoint y ' + bestY + ' should be ~1000');
});

test('unsupported entities skipped with warning, not fatal', () => {
  const r = parseDxf(dxf(E(0,'TEXT',10,0,20,0,1,'hello',0,'LINE',10,0,20,0,11,5,21,5)));
  assert.equal(r.strokes.length, 1);
  assert.ok(r.warnings.some((w) => w.includes('TEXT')), 'TEXT warning present');
});
