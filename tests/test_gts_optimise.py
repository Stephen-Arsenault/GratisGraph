"""tests/test_gts_optimise.py — Tests for gts_optimise.py"""
import sys, os, math
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from gts_optimise import (
    optimise_strokes,
    _reverse, _endpoint, _dist, _tour_distance,
    _apply_reversal, _apply_merging, _apply_ordering, _apply_bidirectional,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _stroke(sx, sy, *deltas):
    """Convenience: build a stroke tuple from absolute start + delta pairs."""
    return (sx, sy, list(deltas))

def _travel(strokes):
    """Total pen-up travel for a stroke list starting from home."""
    return _tour_distance(strokes)


# ---------------------------------------------------------------------------
# _endpoint
# ---------------------------------------------------------------------------

def test_endpoint_single_segment():
    s = _stroke(100, 200, (300, 0))
    assert _endpoint(s) == (400, 200)

def test_endpoint_multi_segment():
    s = _stroke(0, 0, (100, 0), (0, 100), (-100, 0), (0, -100))
    assert _endpoint(s) == (0, 0)   # closed loop

def test_endpoint_zero_segments():
    s = (50, 50, [])
    assert _endpoint(s) == (50, 50)


# ---------------------------------------------------------------------------
# _reverse
# ---------------------------------------------------------------------------

def test_reverse_open_stroke():
    s = _stroke(0, 0, (100, 0), (0, 100))
    r = _reverse(s)
    assert r[0] == 100   # new start = old end x
    assert r[1] == 100   # new start = old end y
    assert r[2] == [(0, -100), (-100, 0)]

def test_reverse_closed_loop_unchanged():
    s = _stroke(0, 0, (100, 0), (0, 100), (-100, 0), (0, -100))
    r = _reverse(s)
    assert r == s        # closed loop — should be identical

def test_reverse_twice_is_identity():
    s = _stroke(10, 20, (50, 0), (0, 30))
    assert _reverse(_reverse(s)) == s


# ---------------------------------------------------------------------------
# Pass 1 — direction reversal
# ---------------------------------------------------------------------------

def test_reversal_chooses_closer_end():
    # Tool at home (0,0). Stroke starts at (1000,0), ends at (0,0).
    # End is closer to home, so stroke should be reversed.
    s = _stroke(1000, 0, (-1000, 0))
    result = _apply_reversal([s])
    assert result[0][0] == 0   # reversed: now starts at (0,0)
    assert result[0][1] == 0

def test_reversal_no_flip_when_start_closer():
    # Tool at home. Stroke starts at (100,0) — closer than end at (1000,0).
    s = _stroke(100, 0, (900, 0))
    result = _apply_reversal([s])
    assert result[0][0] == 100  # unchanged


# ---------------------------------------------------------------------------
# Pass 2 — stroke merging
# ---------------------------------------------------------------------------

def test_merge_adjacent_strokes():
    # Two strokes with endpoints touching exactly
    a = _stroke(0, 0, (1000, 0))      # ends at (1000, 0)
    b = _stroke(1000, 0, (0, 1000))   # starts at (1000, 0)
    result = _apply_merging([a, b], tolerance_units=0)
    assert len(result) == 1
    assert result[0][0] == 0    # merged start = a's start
    assert result[0][1] == 0
    assert len(result[0][2]) == 2

def test_merge_within_tolerance():
    a = _stroke(0, 0, (1000, 0))       # ends at (1000, 0)
    b = _stroke(1001, 0, (0, 1000))    # starts 1 unit away
    result = _apply_merging([a, b], tolerance_units=5)
    assert len(result) == 1

def test_no_merge_beyond_tolerance():
    a = _stroke(0, 0, (1000, 0))
    b = _stroke(1100, 0, (0, 1000))   # 100 units gap
    result = _apply_merging([a, b], tolerance_units=5)
    assert len(result) == 2

def test_merge_preserves_non_adjacent():
    a = _stroke(0,    0, (500, 0))
    b = _stroke(500,  0, (500, 0))    # touches a
    c = _stroke(2000, 0, (500, 0))    # far from b
    result = _apply_merging([a, b, c], tolerance_units=0)
    assert len(result) == 2   # a+b merged, c separate


# ---------------------------------------------------------------------------
# Pass 3 — TSP ordering
# ---------------------------------------------------------------------------

def test_ordering_reduces_travel():
    # Four strokes laid out in a Z pattern — source order is suboptimal
    strokes = [
        _stroke(0,    0,    (100, 0)),   # home area
        _stroke(5000, 5000, (100, 0)),   # far away
        _stroke(200,  0,    (100, 0)),   # near first
        _stroke(5200, 5000, (100, 0)),   # near second
    ]
    original_travel = _travel(strokes)
    optimised = _apply_ordering(strokes)
    assert _travel(optimised) <= original_travel

def test_ordering_visits_all_strokes():
    strokes = [_stroke(i * 100, 0, (50, 0)) for i in range(10)]
    result = _apply_ordering(strokes)
    assert len(result) == len(strokes)

def test_ordering_single_stroke_unchanged():
    s = [_stroke(500, 500, (100, 0))]
    assert _apply_ordering(s) == s


# ---------------------------------------------------------------------------
# Pass 4 — bidirectional passes
# ---------------------------------------------------------------------------

def test_bidirectional_alternates_direction():
    # Three parallel horizontal strokes at y=0, y=100, y=200
    # All going left-to-right (dx=+1000)
    strokes = [
        _stroke(0, 0,   (1000, 0)),
        _stroke(0, 100, (1000, 0)),
        _stroke(0, 200, (1000, 0)),
    ]
    result = _apply_bidirectional(strokes, spacing_tol=150)
    # Even-indexed strokes should go forward, odd-indexed reversed
    assert result[0][2][0][0] > 0    # stroke 0: positive dx (forward)
    assert result[1][2][0][0] < 0    # stroke 1: negative dx (reversed)
    assert result[2][2][0][0] > 0    # stroke 2: positive dx (forward)

def test_bidirectional_non_parallel_unchanged():
    # Strokes pointing in different directions — should not be snaked
    strokes = [
        _stroke(0,    0, (1000, 0)),    # horizontal
        _stroke(0, 1000, (0, -1000)),   # vertical
    ]
    result = _apply_bidirectional(strokes)
    assert result[0][2] == strokes[0][2]
    assert result[1][2] == strokes[1][2]


# ---------------------------------------------------------------------------
# Full pipeline
# ---------------------------------------------------------------------------

def test_optimise_reduces_travel():
    # 5 strokes scattered — optimisation should reduce total travel
    strokes = [
        _stroke(5000, 5000, (200, 0)),
        _stroke(100,  100,  (200, 0)),
        _stroke(4800, 4800, (200, 0)),
        _stroke(300,  300,  (200, 0)),
        _stroke(4600, 4600, (200, 0)),
    ]
    original = _travel(strokes)
    result   = optimise_strokes(strokes, merge=False, bidirectional=False)
    assert _travel(result) <= original

def test_optimise_preserves_stroke_count_when_no_merge():
    strokes = [_stroke(i * 500, 0, (200, 0)) for i in range(8)]
    result  = optimise_strokes(strokes, merge=False)
    assert len(result) == 8

def test_optimise_empty_input():
    assert optimise_strokes([]) == []

def test_optimise_single_stroke_unchanged():
    s = [_stroke(500, 500, (100, 0), (0, 100))]
    result = optimise_strokes(s)
    assert len(result) == 1

def test_optimise_merge_disabled():
    # Adjacent strokes should NOT be merged when merge=False
    a = _stroke(0,    0, (1000, 0))
    b = _stroke(1000, 0, (0, 1000))
    result = optimise_strokes([a, b], merge=False)
    assert len(result) == 2

def test_optimise_produces_valid_gts():
    """Full pipeline: G-Code → optimise → GTS binary must be wire-format valid."""
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
    from gts_prepare import from_gcode
    from wire_format import assert_valid_gts

    gcode = (
        'G21\nG90\n'
        'G0 Z1\nG0 X10 Y10\nG1 Z-1\nG1 X20 Y10\nG0 Z1\n'
        'G0 X40 Y40\nG1 Z-1\nG1 X50 Y40\nG0 Z1\n'
        'G0 X10 Y30\nG1 Z-1\nG1 X20 Y30\nG0 Z1\nM30\n'
    )
    data = from_gcode(gcode, optimise=True)
    assert_valid_gts(data)


# ---------------------------------------------------------------------------
# Duration estimation
# ---------------------------------------------------------------------------

def test_estimate_duration_returns_none_if_velocity_unset(monkeypatch):
    """Returns None only if the velocity constant is explicitly unset."""
    import gts_optimise
    monkeypatch.setattr(gts_optimise, '_VS_TO_MM_PER_SEC', None)
    from gts_optimise import estimate_duration
    assert estimate_duration([_stroke(0, 0, (5000, 0))]) is None


def test_estimate_duration_is_positive_float():
    """With characterised constants, duration is a positive float."""
    from gts_optimise import estimate_duration
    duration = estimate_duration([_stroke(0, 0, (5000, 0))], speed=25)  # 50mm from home
    assert isinstance(duration, float)
    assert duration > 0.0


def test_estimate_duration_longer_job_takes_more_time():
    """Longer engraving travel must estimate longer duration."""
    from gts_optimise import estimate_duration
    short = [_stroke(0, 0, (1000, 0))]    # 10mm
    long  = [_stroke(0, 0, (5000, 0))]    # 50mm
    assert estimate_duration(long, speed=25) > estimate_duration(short, speed=25)


def test_estimate_duration_scales_inversely_with_speed():
    """Constant-velocity model: lower VS engraves the same line more slowly."""
    from gts_optimise import estimate_duration
    line = [_stroke(0, 0, (9000, 0))]     # 90mm
    assert estimate_duration(line, speed=10) > estimate_duration(line, speed=40)


def test_estimate_duration_empty_strokes():
    from gts_optimise import estimate_duration
    assert estimate_duration([]) == 0.0
