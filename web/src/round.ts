/**
 * Round-half-to-even (banker's rounding) — matches Python's built-in round().
 *
 * THE conformance landmine for byte-equality with the Python reference
 * (gts_translator.py uses int(round(mm * scale))). JS Math.round is
 * half-UP and disagrees on exact .5 midpoints, which would silently
 * corrupt golden-corpus byte matching. Use this everywhere mm -> device
 * units conversion happens.
 *
 * Verified against Python: round(0.105*100)=10, round(0.115*100)=12,
 * round(0.125*100)=12, round(0.135*100)=14, round(2.5)=2, round(3.5)=4.
 */
export function roundHalfEven(x: number): number {
  const floor = Math.floor(x);
  const diff = x - floor;
  if (diff < 0.5) return floor;
  if (diff > 0.5) return floor + 1;
  // Exactly halfway (within float tolerance): round to even.
  return floor % 2 === 0 ? floor : floor + 1;
}

/**
 * Convert millimetres to device units (0.01 mm integers) the same way the
 * Python reference does: int(round(mm * scale)). `scale` defaults to
 * UNITS_PER_MM (100). Quantisation happens ONCE, at stroke -> GTS; the
 * optimiser then operates on these integers.
 */
export function mmToUnits(mm: number, scale = 100): number {
  return roundHalfEven(mm * scale);
}
