# tests/regression/

This directory contains regression tests that validate GratisGraph's GTS binary
output against real captures produced by Gravostyle / Gravotech Quick software.

## Why this exists

The GTS binary format was documented from USB captures of a live M20
machine running Gravostyle. The reference `.SPL` files in this directory are
those captures — unmodified bytes that were confirmed to produce correct motion
on hardware.

The regression tests parse both the reference capture and our generated output
field-by-field (setup commands, stroke positions, segment records, terminal
opcodes) and assert they are identical. Header metadata bytes that are only
relevant to the Windows spooler stack are excluded from comparison.

If a change to `gts_api.py` or `gts_translator.py` causes our output to diverge
from the reference, these tests will catch it immediately.

## Reference files

| File | Description | Confirmed |
|------|-------------|-----------|
| `00013.SPL` | Single horizontal line: start (0, 16.56mm), dx=35.40mm | ✓ Hardware |
| `00018.SPL` | Small curved stroke: start (16.74mm, 19.30mm), arc segments | ✓ Hardware |

## Adding new references

When a new Gravostyle capture is confirmed working on hardware, copy the `.SPL`
file into this directory and add a corresponding test in `test_regression.py`.
Name the file descriptively if possible (e.g. `circle_30mm.spl`).
