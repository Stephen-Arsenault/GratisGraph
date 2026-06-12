"""tests/test_gts_prepare.py — Tests for gts_prepare.py"""
import sys, os
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from gts_api import M20_PROFILE, M40_PROFILE, M10_JEWEL_PROFILE, IS400_IQ_PROFILE, UNITS_PER_MM
from gts_prepare import from_gcode, from_file, square, circle, home
from wire_format import assert_valid_gts, assert_single_jf


SIMPLE_SQUARE = (
    'G21\nG90\n'
    'G0 Z1\nG0 X10 Y10\n'
    'G1 Z-1\nG1 X30 Y10\nG1 X30 Y30\nG1 X10 Y30\nG1 X10 Y10\n'
    'G0 Z1\nM30\n'
)


def test_from_gcode_returns_valid_gts():
    data = from_gcode(SIMPLE_SQUARE)
    assert_valid_gts(data)


def test_from_gcode_respects_profile_envelope():
    """Job within M20 envelope should pass; shifted beyond physical envelope should fail."""
    from_gcode(SIMPLE_SQUARE, profile=M20_PROFILE)  # should not raise
    with pytest.raises(ValueError, match='envelope'):
        # Shift origin so job extends to ~130mm — beyond physical envelope of 120mm
        from_gcode(SIMPLE_SQUARE, profile=M20_PROFILE, origin=(105.0, 105.0))


def test_from_gcode_flip_y_changes_output():
    normal  = from_gcode(SIMPLE_SQUARE, flip_y=False)
    flipped = from_gcode(SIMPLE_SQUARE, flip_y=True)
    assert normal != flipped


def test_from_gcode_scale():
    normal = from_gcode(SIMPLE_SQUARE, scale=1.0)
    scaled = from_gcode(SIMPLE_SQUARE, scale=0.5)
    # Scaled job should be smaller — fewer or smaller segment deltas
    assert len(scaled) <= len(normal)


def test_from_gcode_accepts_m40_profile():
    """M40 profile should produce valid GTS without error."""
    data = from_gcode(SIMPLE_SQUARE, profile=M40_PROFILE)
    assert_valid_gts(data)


def test_from_file_detects_mm(tmp_path):
    f = tmp_path / 'test.gc'
    f.write_text(SIMPLE_SQUARE, encoding='utf-8')
    data = from_file(str(f))
    assert_valid_gts(data)


def test_from_file_detects_inch(tmp_path):
    inch_gcode = 'G20\nG90\nG0 Z0.1\nG0 X0.5 Y0.5\nG1 Z-0.01\nG1 X1.0 Y0.5\nG0 Z0.1\nM30\n'
    f = tmp_path / 'inch.gc'
    f.write_text(inch_gcode, encoding='utf-8')
    data = from_file(str(f))
    assert_valid_gts(data)


def test_square_returns_valid_gts():
    assert_valid_gts(square(50.0, 50.0, 20.0))


def test_square_scale():
    s1 = square(50.0, 50.0, 20.0, scale=1.0)
    s2 = square(50.0, 50.0, 20.0, scale=0.5)
    assert s1 != s2


def test_circle_returns_valid_gts():
    assert_valid_gts(circle(50.0, 50.0, 20.0))


def test_home_returns_valid_bytes():
    data = home()
    assert data[:6] == b'!MM88;'
    assert len(data) > 0


def test_all_exports_importable():
    import gts_prepare
    for name in gts_prepare.__all__:
        assert hasattr(gts_prepare, name), f'__all__ lists {name!r} but it is not defined'


def test_m10_jewel_negates_x():
    """M10 Jewel profile should negate X — stroke header dx should be negated."""
    import struct
    from wire_format import find_segments, ACCEL_SENTINEL
    # Asymmetric X start so negation is detectable
    gcode = 'G21\nG90\nG0 Z1\nG0 X20 Y10\nG1 Z-1\nG1 X30 Y10\nG0 Z1\nM30\n'
    normal  = from_gcode(gcode, profile=M20_PROFILE)
    negated = from_gcode(gcode, profile=M10_JEWEL_PROFILE)
    # Extract stroke header dx from both (first segment record = stroke header)
    segs_normal  = find_segments(normal)
    segs_negated = find_segments(negated)
    dx_normal  = segs_normal[0][1][4]   # stroke header dx
    dx_negated = segs_negated[0][1][4]
    assert dx_negated == -dx_normal, (
        f'negate_x should flip stroke header dx: normal={dx_normal}, negated={dx_negated}'
    )


def test_is400_iq_scales_coordinates():
    """IS400 IQ profile applies coord_scale=1.25 — output differs from M20."""
    m20_out    = from_gcode(SIMPLE_SQUARE, profile=M20_PROFILE)
    is400_out  = from_gcode(SIMPLE_SQUARE, profile=IS400_IQ_PROFILE)
    assert m20_out != is400_out
