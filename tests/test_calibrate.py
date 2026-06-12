"""tests/test_calibrate.py — calibration pattern builders (no hardware)."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest
from calibrate import build_steps
from gts_api import WORK_ENVELOPE_MM
from gts_prepare import from_gcode
from gts_preview import parse_gts


def test_default_suite_has_five_steps():
    steps = build_steps()
    assert len(steps) == 5
    assert all({'name', 'description', 'gcode'} <= set(s) for s in steps)

@pytest.mark.parametrize('scale', [1.0, 2.0, 0.5])
def test_every_step_translates_and_fits_envelope(scale):
    for step in build_steps(scale=scale):
        gts = from_gcode(step['gcode'])           # raises if out of envelope
        strokes, _, bounds = parse_gts(gts)
        assert strokes, step['name']
        lo_x, lo_y, hi_x, hi_y = bounds
        assert 0 <= lo_x and 0 <= lo_y
        assert hi_x <= WORK_ENVELOPE_MM[0] and hi_y <= WORK_ENVELOPE_MM[1]

def test_scale_doubles_square():
    s1 = parse_gts(from_gcode(build_steps(1.0)[0]['gcode']))[2]
    s2 = parse_gts(from_gcode(build_steps(2.0)[0]['gcode']))[2]
    assert abs((s2[2] - s2[0]) - 2 * (s1[2] - s1[0])) < 0.05   # width doubled

def test_border_margin_tracks_scale():
    """Border is drawn margin=1*scale mm inside the envelope edge."""
    for scale in (1.0, 2.0):
        bounds = parse_gts(from_gcode(build_steps(scale)[4]['gcode']))[2]
        assert abs(bounds[0] - 1.0 * scale) < 0.05
        assert abs(WORK_ENVELOPE_MM[0] - bounds[2] - 1.0 * scale) < 0.05
