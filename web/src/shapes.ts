/**
 * shapes.ts — primitive shape generators (circle, rect, line) as strokes.
 *
 * Shapes are SceneObjects like any imported file: generated once as
 * baseStrokes, then positioned/rotated/scaled via the object transform.
 * Circle segment count is fixed high (128) rather than tolerance-derived so
 * that scaling up via the transform keeps chord error negligible
 * (sagitta at r=10mm/128 segs = 0.003mm; even 5x scale stays at 0.015mm).
 */
import { mmToUnits } from './round.ts';
import type { Stroke } from './gts.ts';

const CIRCLE_SEGS = 128;

function toStroke(pointsMm: [number, number][]): Stroke {
  const sx = mmToUnits(pointsMm[0]![0]), sy = mmToUnits(pointsMm[0]![1]);
  const deltas: [number, number][] = [];
  let px = sx, py = sy;
  for (let i = 1; i < pointsMm.length; i++) {
    const qx = mmToUnits(pointsMm[i]![0]), qy = mmToUnits(pointsMm[i]![1]);
    if (qx !== px || qy !== py) deltas.push([qx - px, qy - py]);
    px = qx; py = qy;
  }
  return { start: [sx, sy], deltas };
}

/** Closed circle, diameter dMm, centered (cxMm, cyMm). */
export function circleStroke(dMm: number, cxMm: number, cyMm: number): Stroke {
  const r = dMm / 2;
  const pts: [number, number][] = [];
  for (let k = 0; k <= CIRCLE_SEGS; k++) {
    const a = (2 * Math.PI * k) / CIRCLE_SEGS;
    pts.push([cxMm + r * Math.cos(a), cyMm + r * Math.sin(a)]);
  }
  return toStroke(pts);
}

/** Closed rectangle wMm x hMm centered (cxMm, cyMm). Square: w == h. */
export function rectStroke(wMm: number, hMm: number, cxMm: number, cyMm: number): Stroke {
  const x0 = cxMm - wMm / 2, x1 = cxMm + wMm / 2;
  const y0 = cyMm - hMm / 2, y1 = cyMm + hMm / 2;
  return toStroke([[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]);
}

/** Horizontal line of lengthMm centered (cxMm, cyMm). Rotate via transform. */
export function lineStroke(lengthMm: number, cxMm: number, cyMm: number): Stroke {
  return toStroke([[cxMm - lengthMm / 2, cyMm], [cxMm + lengthMm / 2, cyMm]]);
}
