# GratisGraph Stroke Schema (v1)

The stroke list is the system's central interchange format. It is:

1. **The browser-internal boundary** between input parsers (SVG / DXF /
   G-Code → strokes) and the GTS engine (strokes → optimise → `.m20` binary).
   Parsers are format-dumb; all GTS knowledge lives downstream of this seam.
2. **The save/open file format** for the web editor. A saved project IS a
   stroke file — there is no separate document format.
3. **The Python reference boundary**: `gcode_to_strokes()` output and
   `build_gts()` input are this structure (as tuples, in device units).

## File format

Extension `.ggs` (GratisGraph strokes). UTF-8 JSON:

```json
{
  "format": "gratisgraph-strokes",
  "version": 1,
  "units": "mm",
  "frame": "machine",
  "envelope_mm": [100.0, 100.0],
  "params": { "speed": 25, "depth": 500, "lift": 30 },
  "metadata": { "name": "two lines", "source": "drawing.svg",
                "created": "2026-06-09T21:30:00-07:00" },
  "strokes": [
    { "start": [10.0, 20.0], "deltas": [[10.0, 0.0]] },
    { "start": [30.0, 40.0], "deltas": [[10.0, 0.0]] }
  ]
}
```

- `frame: "machine"` — home (0,0) front-left, +X right, +Y toward the back.
  Coordinate transforms (Y-flip, origin shift, scale) happen in the parser
  layer; a stroke file is ALWAYS already in machine frame.
- `start` is absolute (mm, floats); `deltas` are pen-down segment deltas in
  order. Pen-up rapids are implicit (stroke order = draw order after
  optimisation; the GTS engine emits header deltas per GTS_FORMAT.md §5).
- `params` are GTS setup values (VS / DZ / PZ). Optional; defaults above.
- Unknown top-level or metadata keys MUST be preserved on round-trip and
  ignored otherwise (forward compatibility).

## Conformance: mm → device units rounding

GTS device units are 0.01 mm integers. The reference implementation
(`gts_translator.py`) converts with Python `int(round(mm * scale))` —
**round-half-to-even (banker's rounding)**, e.g. 0.125 mm → 12, 0.135 mm
→ 14 units.

⚠ JavaScript `Math.round()` is round-half-UP and WILL produce different
bytes on exact .005 mm midpoints. A conforming TS implementation must use a
half-to-even rounding function. This is the single most likely source of
golden-corpus byte mismatches; get it right first.

Quantisation happens ONCE, at stroke → GTS translation. Stroke files carry
mm floats; the optimiser operates on device-unit integers after
quantisation (matching the Python pipeline: gcode_to_strokes quantises,
optimise_strokes reorders integers — reordering after quantisation is
byte-stable, reordering before is not).
