"""tests/test_gts_preview.py — parse_gts + render_svg (no hardware)."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from gts_api import build_gts, build_square
from gts_preview import parse_gts, render_svg

REGRESSION = os.path.join(os.path.dirname(__file__), 'regression', '00013.SPL')


def test_parse_single_stroke_line():
    gts = build_gts([(1000, 2000, [(2000, 0)])])     # 20mm line at (10,20)
    strokes, rapids, bounds = parse_gts(gts)
    assert len(strokes) == 1
    assert strokes[0]['start'] == (10.0, 20.0)
    assert strokes[0]['points'][-1] == (30.0, 20.0)

def test_parse_square_geometry_closes():
    gts = build_square(centre_x_mm=20, centre_y_mm=20, side_mm=20)
    strokes, _, bounds = parse_gts(gts)
    assert len(strokes) == 1
    pts = strokes[0]['points']
    assert pts[0] == pts[-1]                          # closed loop
    assert bounds == (10.0, 10.0, 30.0, 30.0)         # corners of the square

def test_parse_gravostyle_regression_capture():
    """Real Gravostyle output: horizontal line, start (0, 16.56), Δx 35.40mm."""
    with open(REGRESSION, 'rb') as f:
        strokes, _, _ = parse_gts(f.read())
    assert len(strokes) == 1
    sx, sy = strokes[0]['start']
    ex, ey = strokes[0]['points'][-1]
    assert (sx, sy) == (0.0, 16.56)
    assert abs((ex - sx) - 35.40) < 0.01 and ey == sy

def test_render_svg_contains_toolpath():
    gts = build_square(centre_x_mm=20, centre_y_mm=20, side_mm=20)
    strokes, rapids, _ = parse_gts(gts)
    svg = render_svg(strokes, rapids)
    assert svg.lstrip().startswith('<') and '<svg' in svg
    assert 'polyline' in svg or 'path' in svg


def test_parse_multistroke_relative_headers_round_trip():
    """Hardware-confirmed 2026-06-09: headers are relative deltas. A
    build_gts multi-stroke job must round-trip through parse_gts to the
    original ABSOLUTE stroke starts."""
    gts = build_gts([(1000, 2000, [(1000, 0)]),      # A: (10,20) -> (20,20)
                     (3000, 4000, [(1000, 0)])])     # B: (30,40) -> (40,40)
    strokes, rapids, bounds = parse_gts(gts)
    assert [s['start'] for s in strokes] == [(10.0, 20.0), (30.0, 40.0)]
    assert strokes[1]['points'][-1] == (40.0, 40.0)
    assert bounds == (10.0, 20.0, 40.0, 40.0)
    # One pen-up rapid between the strokes: A end (20,20) -> B start (30,40)
    assert ((20.0, 20.0), (30.0, 40.0)) in rapids
