/**
 * scene.ts — the editable layout model that sits between imported files and
 * the GTS pipeline.
 *
 * A Scene is an ordered list of SceneObjects. Each object holds its IMMUTABLE
 * imported geometry (baseStrokes, device units) plus a transform. The world
 * geometry (what you see / what gets engraved) is baseStrokes pushed through
 * applyTransform.
 *
 * The transform is a full {tx, ty, rotation, scale} from day one so that
 * rotate/scale (planned) drop in without touching the data model or the
 * pipeline — only the UI gizmos and applyTransform's already-present math.
 * Dragging writes tx/ty today; rotation/scale are wired to 0/1 and ready.
 */
import type { Stroke } from './gts.ts';
import { roundHalfEven } from './round.ts';

export interface Transform {
  tx: number;      // device units
  ty: number;
  rotation: number; // radians, about the object's center (planned UI; math live)
  scale: number;    // uniform (planned UI; math live)
}

export interface SceneObject {
  id: string;
  name: string;
  baseStrokes: Stroke[];   // immutable imported geometry, device units
  transform: Transform;
  reference?: boolean;     // display in edit view, but exclude from engraving
}

export interface Scene {
  version: 1;
  objects: SceneObject[];
}

export function identityTransform(): Transform {
  return { tx: 0, ty: 0, rotation: 0, scale: 1 };
}

export function newScene(): Scene {
  return { version: 1, objects: [] };
}

let _seq = 0;
export function makeObject(name: string, baseStrokes: Stroke[],
                           transform: Transform = identityTransform()): SceneObject {
  return { id: `obj_${++_seq}_${Date.now().toString(36)}`, name, baseStrokes, transform };
}

/** Absolute (device-unit) center of an object's BASE strokes, pre-transform. */
function baseCenter(strokes: Stroke[]): [number, number] {
  const b = strokesBounds(strokes);
  return [(b[0] + b[2]) / 2, (b[1] + b[3]) / 2];
}

/**
 * Apply a transform to a single stroke, returning a new stroke in world space.
 * Order: scale and rotate about the object's base center, then translate.
 * Today rotation=0 & scale=1 so this reduces to a pure translate, but the full
 * affine is here and unit-tested so rotate/scale need no pipeline changes.
 */
export function applyTransform(stroke: Stroke, t: Transform, center: [number, number]): Stroke {
  const [cx, cy] = center;
  const cos = Math.cos(t.rotation), sin = Math.sin(t.rotation);
  const xf = (x: number, y: number): [number, number] => {
    // about center: scale, rotate, then translate
    let dx = (x - cx) * t.scale, dy = (y - cy) * t.scale;
    const rx = dx * cos - dy * sin, ry = dx * sin + dy * cos;
    return [cx + rx + t.tx, cy + ry + t.ty];
  };
  // start is absolute; deltas are relative — transform start, and rotate/scale
  // the deltas (translation doesn't affect deltas).
  const [sx, sy] = xf(stroke.start[0], stroke.start[1]);
  const dxf = (dx: number, dy: number): [number, number] => {
    const sdx = dx * t.scale, sdy = dy * t.scale;
    return [sdx * cos - sdy * sin, sdx * sin + sdy * cos];
  };
  return {
    start: [sx, sy],
    deltas: stroke.deltas.map(([dx, dy]) => dxf(dx, dy)),
  };
}

/** An object's strokes in world space (transform applied). */
export function worldStrokes(obj: SceneObject): Stroke[] {
  const center = baseCenter(obj.baseStrokes);
  return obj.baseStrokes.map((s) => applyTransform(s, obj.transform, center));
}

/** Bounding box [minX, minY, maxX, maxY] of a stroke list (device units). */
export function strokesBounds(strokes: Stroke[]): [number, number, number, number] {
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
  for (const s of strokes) {
    let x = s.start[0], y = s.start[1];
    const visit = (px: number, py: number) => {
      if (px < minX) minX = px; if (py < minY) minY = py;
      if (px > maxX) maxX = px; if (py > maxY) maxY = py;
    };
    visit(x, y);
    for (const [dx, dy] of s.deltas) { x += dx; y += dy; visit(x, y); }
  }
  if (!isFinite(minX)) return [0, 0, 0, 0];
  return [minX, minY, maxX, maxY];
}

/**
 * Quantise strokes to integer device units WITHOUT drift: round the
 * accumulated absolute positions (half-even, matching the engine), then
 * re-derive deltas from the rounded points. Per-delta rounding would
 * accumulate error over long paths; quantise-then-subtract cannot.
 * Required after rotate/scale transforms, which produce fractional units.
 */
export function quantiseStrokes(strokes: Stroke[]): Stroke[] {
  return strokes.map((s) => {
    const sx = roundHalfEven(s.start[0]), sy = roundHalfEven(s.start[1]);
    let ax = s.start[0], ay = s.start[1];   // exact accumulator
    let px = sx, py = sy;                    // previous rounded point
    const deltas: [number, number][] = [];
    for (const [dx, dy] of s.deltas) {
      ax += dx; ay += dy;
      const qx = roundHalfEven(ax), qy = roundHalfEven(ay);
      const ddx = qx - px, ddy = qy - py;
      if (ddx !== 0 || ddy !== 0) deltas.push([ddx, ddy]);
      px = qx; py = qy;
    }
    return { start: [sx, sy] as [number, number], deltas };
  }).filter((s) => s.deltas.length > 0);
}

/** Flatten the whole scene to a single world-space stroke list for the pipeline. */
export function flattenScene(scene: Scene): Stroke[] {
  return scene.objects.filter((o) => !o.reference).flatMap(worldStrokes);
}
