"""tests/test_gts_translator.py — Tests for gts_translator.py"""
import sys, os
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from gts_translator import translate, GCodeParser, GMove, gcode_to_strokes
from gts_api import UNITS_PER_MM
from wire_format import assert_valid_gts, assert_single_jf, find_opcodes, find_segments, OP_PD


def test_unit_scale_mm():
    gcode = 'G21\nG90\nG0 Z1\nG0 X0 Y0\nG1 Z-1\nG1 X35.4 Y0\nG0 Z1\nM30'
    data = translate(gcode, scale=100.0, wrap=False)
    dx_values = [s[1][4] for s in find_segments(data) if s[1][4] != 0]
    assert 3540 in dx_values

def test_unit_scale_inch():
    gcode = 'G20\nG90\nG0 Z0.1\nG0 X0 Y0\nG1 Z-0.01\nG1 X1 Y0\nG0 Z0.1\nM30'
    data = translate(gcode, units='inch', scale=100.0, wrap=False)
    dx_values = [s[1][4] for s in find_segments(data) if s[1][4] != 0]
    assert 2540 in dx_values

def test_g91_relative():
    gcode = 'G21\nG91\nG0 Z1\nG0 X0 Y0\nG1 Z-1\nG1 X10 Y0\nG0 Z1\nM30'
    data = translate(gcode, scale=100.0, wrap=False)
    dx_values = [s[1][4] for s in find_segments(data) if s[1][4] != 0]
    assert 1000 in dx_values

def test_g92_offset():
    gcode = 'G21\nG90\nG92 X10\nG0 Z1\nG0 X10 Y10\nG1 Z-1\nG1 X20 Y10\nG0 Z1\nM30'
    data = translate(gcode, scale=100.0, wrap=False)
    dx_values = [s[1][4] for s in find_segments(data) if s[1][4] != 0]
    assert 1000 in dx_values

def test_arc_g2():
    gcode = 'G21\nG90\nG0 Z1\nG0 X40 Y20\nG1 Z-1\nG2 X40 Y20 I-20 J0\nG0 Z1\nM30'
    data = translate(gcode, scale=100.0, wrap=False)
    assert len(find_segments(data)) >= 36

def test_multi_stroke_single_jf():
    gcode = ('G21\nG90\n'
             'G0 Z1\nG0 X0 Y0\nG1 Z-1\nG1 X10 Y0\nG0 Z1\n'
             'G0 X20 Y0\nG1 Z-1\nG1 X30 Y0\nG0 Z1\nM30')
    data = translate(gcode, scale=100.0, wrap=False)
    assert_single_jf(data)
    assert len(find_opcodes(data, OP_PD)) == 2

def test_lightburn_m_codes_ignored():
    gcode = 'G21\nG90\nM4\nM8\nG0 X10 Y10\nG1 X20 Y10 S200 F6000\nM9\nG1 S0\nM5\nG0 X0 Y0\nM2'
    data = translate(gcode, scale=100.0, wrap=False)
    assert len(data) > 0

def test_output_is_valid_gts():
    gcode = ('G21\nG90\nG0 Z1\nG0 X10 Y10\n'
             'G1 Z-1\nG1 X50 Y10\nG1 X50 Y50\nG1 X10 Y50\nG1 X10 Y10\nG0 Z1\nM30')
    assert_valid_gts(translate(gcode, scale=100.0, wrap=False))

def test_flip_y_changes_output():
    gcode = 'G21\nG90\nG0 Z1\nG0 X50 Y30\nG1 Z-1\nG1 X50 Y70\nG0 Z1\nM30'
    normal  = translate(gcode, scale=100.0, flip_y=False, wrap=False)
    flipped = translate(gcode, scale=100.0, flip_y=True,  wrap=False)
    assert normal != flipped

def test_gmove_defaults():
    """GMove fields default correctly."""
    m = GMove()
    assert m.rapid == False
    assert m.x is None
    assert m.y is None
    assert m.z is None
    assert m.feed is None


def test_gcode_to_strokes_basic():
    """gcode_to_strokes produces correct stroke tuples from parsed moves."""
    moves = [
        GMove(rapid=True,  x=10.0, y=10.0, z=1.0),
        GMove(rapid=False, x=10.0, y=10.0, z=-1.0),
        GMove(rapid=False, x=20.0, y=10.0, z=-1.0),
        GMove(rapid=True,  x=20.0, y=10.0, z=1.0),
    ]
    strokes = gcode_to_strokes(moves, scale=100.0)
    assert len(strokes) == 1
    start_x, start_y, segs = strokes[0]
    assert start_x == 1000   # 10mm * 100
    assert start_y == 1000
    assert len(segs) == 1
    assert segs[0] == (1000, 0)   # dx=10mm, dy=0


def test_gcode_to_strokes_two_strokes():
    """Two pen-down regions produce two strokes."""
    moves = [
        GMove(rapid=True,  x=0.0,  y=0.0,  z=1.0),
        GMove(rapid=False, x=0.0,  y=0.0,  z=-1.0),
        GMove(rapid=False, x=10.0, y=0.0,  z=-1.0),
        GMove(rapid=True,  x=10.0, y=0.0,  z=1.0),
        GMove(rapid=True,  x=20.0, y=0.0,  z=1.0),
        GMove(rapid=False, x=20.0, y=0.0,  z=-1.0),
        GMove(rapid=False, x=30.0, y=0.0,  z=-1.0),
        GMove(rapid=True,  x=30.0, y=0.0,  z=1.0),
    ]
    strokes = gcode_to_strokes(moves, scale=100.0)
    assert len(strokes) == 2
