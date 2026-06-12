"""
tests/regression/test_regression.py

Validates GratisGraph's GTS binary output against real captures produced by
Gravostyle / Gravotech Quick software on confirmed hardware.

Each test:
  1. Loads a reference .SPL capture from this directory
  2. Generates the equivalent GTS binary using our code
  3. Compares field-by-field (setup commands, stroke positions, segments,
     terminal opcodes) — header metadata is excluded

If our output diverges from the reference, a change somewhere in the pipeline
(gts_api.py or gts_translator.py) has broken protocol compatibility.
"""

import os
import sys
import struct

# Allow imports from project root and tests/
HERE        = os.path.dirname(os.path.abspath(__file__))
TESTS_DIR   = os.path.dirname(HERE)
PROJECT_DIR = os.path.dirname(TESTS_DIR)
sys.path.insert(0, PROJECT_DIR)
sys.path.insert(0, TESTS_DIR)

from gts_api import build_gts_stroke
from wire_format import parse_gts_fields, ACCEL_SENTINEL


def load_ref(filename: str) -> bytes:
    path = os.path.join(HERE, filename)
    with open(path, 'rb') as f:
        return f.read()


# ---------------------------------------------------------------------------
# 00013.SPL — single horizontal line
#
# Confirmed from hardware decode:
#   Setup:  DR=0 MC=0 VS=25 VZ=25 PZ=30 DZ=500
#   Stroke: start (dx=0, dy=1656) = (0mm, 16.56mm) from home
#   Segment: dx=+3540 dy=0  (35.40mm horizontal line)
#   Return: dx=-3540 dy=-1656 (back to home)
# ---------------------------------------------------------------------------

def test_reference_00013_setup_commands():
    """Setup commands must exactly match the Gravostyle reference capture."""
    ref   = parse_gts_fields(load_ref('00013.SPL'))
    ours  = parse_gts_fields(build_gts_stroke(
        start_x_mm=0.0,
        start_y_mm=16.56,
        moves_mm=[(35.4, 0.0)],
    ))
    assert ours['setup'] == ref['setup'], (
        f'Setup command mismatch:\n  ours: {ours["setup"]}\n  ref:  {ref["setup"]}'
    )


def test_reference_00013_stroke_start():
    """Stroke header position must exactly match the reference."""
    ref  = parse_gts_fields(load_ref('00013.SPL'))
    ours = parse_gts_fields(build_gts_stroke(
        start_x_mm=0.0,
        start_y_mm=16.56,
        moves_mm=[(35.4, 0.0)],
    ))
    assert len(ours['strokes']) == 1
    assert ours['strokes'][0]['start'] == ref['strokes'][0]['start'], (
        f'Stroke start mismatch:\n  ours: {ours["strokes"][0]["start"]}\n'
        f'  ref:  {ref["strokes"][0]["start"]}'
    )


def test_reference_00013_segments():
    """Pen-down segments must exactly match the reference."""
    ref  = parse_gts_fields(load_ref('00013.SPL'))
    ours = parse_gts_fields(build_gts_stroke(
        start_x_mm=0.0,
        start_y_mm=16.56,
        moves_mm=[(35.4, 0.0)],
    ))
    assert ours['strokes'][0]['segs'] == ref['strokes'][0]['segs'], (
        f'Segment mismatch:\n  ours: {ours["strokes"][0]["segs"]}\n'
        f'  ref:  {ref["strokes"][0]["segs"]}'
    )


def test_reference_00013_return_and_jf():
    """Return block and JF must exactly match the reference."""
    ref  = parse_gts_fields(load_ref('00013.SPL'))
    ours = parse_gts_fields(build_gts_stroke(
        start_x_mm=0.0,
        start_y_mm=16.56,
        moves_mm=[(35.4, 0.0)],
    ))
    assert ours['strokes'][0]['ret'] == ref['strokes'][0]['ret'], (
        f'Return block mismatch:\n  ours: {ours["strokes"][0]["ret"]}\n'
        f'  ref:  {ref["strokes"][0]["ret"]}'
    )
    assert ours['jf_count'] == ref['jf_count'] == 1


def test_reference_00013_za_and_pu():
    """ZA value and PU sequence must match the reference."""
    ref  = parse_gts_fields(load_ref('00013.SPL'))
    ours = parse_gts_fields(build_gts_stroke(
        start_x_mm=0.0,
        start_y_mm=16.56,
        moves_mm=[(35.4, 0.0)],
    ))
    assert ours['strokes'][0]['za'] == ref['strokes'][0]['za'], (
        f'ZA mismatch: ours={ours["strokes"][0]["za"]} ref={ref["strokes"][0]["za"]}'
    )
    assert ours['strokes'][0]['pu'] == ref['strokes'][0]['pu'], (
        f'PU sequence mismatch:\n  ours: {ours["strokes"][0]["pu"]}\n'
        f'  ref:  {ref["strokes"][0]["pu"]}'
    )
