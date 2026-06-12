/**
 * preview.ts — parse a GTS binary back into strokes (the inverse of buildGts).
 * Port of gts_preview.py parse_gts. Drives the GTS-driven preview: we render
 * what the MACHINE will do, decoded from the actual .m20 bytes, never from the
 * source file.
 *
 * Stroke headers are RELATIVE deltas (hardware-confirmed); the parser
 * integrates them to absolute mm positions, matching the Python reference.
 */
const ACCEL_SENTINEL = 551;   // 0x0227 — segment-record marker

export interface ParsedStroke {
  start: [number, number];          // mm
  points: [number, number][];       // mm, pen-down path incl. start
}
export interface ParsedJob {
  strokes: ParsedStroke[];
  rapids: [[number, number], [number, number]][];   // (from_mm, to_mm) pen-up
  bounds: [number, number, number, number];          // minX, minY, maxX, maxY mm
}

function s16BE(view: DataView, off: number): number {
  return view.getInt16(off, false);
}

/** Find the next occurrence of bytes [0xFF,0xFD,c0,c1] at or after `from`. */
function findOpcode(data: Uint8Array, c0: number, c1: number, from: number): number {
  for (let i = from; i <= data.length - 4; i++) {
    if (data[i] === 0xff && data[i + 1] === 0xfd && data[i + 2] === c0 && data[i + 3] === c1) {
      return i;
    }
  }
  return -1;
}

export function parseGts(data: Uint8Array): ParsedJob {
  const view = new DataView(data.buffer, data.byteOffset, data.byteLength);
  const strokes: ParsedStroke[] = [];
  const rapids: [[number, number], [number, number]][] = [];
  let toolX = 0.0, toolY = 0.0;

  const P = 'P'.charCodeAt(0), D = 'D'.charCodeAt(0);
  let pos = 0;
  while (pos < data.length - 5) {
    const pdIdx = findOpcode(data, P, D, pos);
    if (pdIdx === -1) break;

    // Header is 18 bytes before PD: 12-byte seg record + 6-byte FF FD ZA.
    const hdrPos = pdIdx - 18;
    if (hdrPos < 0) { pos = pdIdx + 6; continue; }

    const acc = s16BE(view, hdrPos + 6);   // accel field of the header record
    if (acc !== ACCEL_SENTINEL) { pos = pdIdx + 6; continue; }
    const hx = s16BE(view, hdrPos + 8);
    const hy = s16BE(view, hdrPos + 10);

    const startX = toolX + hx / 100.0;
    const startY = toolY + hy / 100.0;

    if (strokes.length || toolX !== 0 || toolY !== 0) {
      rapids.push([[toolX, toolY], [startX, startY]]);
    }

    let segPos = pdIdx + 6;
    const points: [number, number][] = [[startX, startY]];
    let cx = startX, cy = startY;
    while (segPos + 12 <= data.length) {
      if (data[segPos] === 0xff && data[segPos + 1] === 0xfd) break;
      const segAcc = s16BE(view, segPos + 6);
      if (segAcc !== ACCEL_SENTINEL) break;
      const dx = s16BE(view, segPos + 8);
      const dy = s16BE(view, segPos + 10);
      cx += dx / 100.0;
      cy += dy / 100.0;
      points.push([cx, cy]);
      segPos += 12;
    }

    strokes.push({ start: [startX, startY], points });
    toolX = cx; toolY = cy;
    pos = pdIdx + 6;
  }

  const allPts = strokes.flatMap((s) => s.points);
  let bounds: [number, number, number, number] = [0, 0, 0, 0];
  if (allPts.length) {
    bounds = [
      Math.min(...allPts.map((p) => p[0])),
      Math.min(...allPts.map((p) => p[1])),
      Math.max(...allPts.map((p) => p[0])),
      Math.max(...allPts.map((p) => p[1])),
    ];
  }
  return { strokes, rapids, bounds };
}
