#!/usr/bin/env python3
"""
gts_optimise.py — Toolpath optimisation for GT Smartstream jobs.

Accepts a list of stroke tuples (sx, sy, [(dx, dy), ...]) as produced by
gts_translator.gcode_to_strokes() and returns a reordered, optionally
merged and reversed list that minimises total pen-up travel distance.

Pipeline (applied in order):
    1. Materialise absolute endpoints     — required by all subsequent passes
    2. Per-stroke direction reversal      — greedy, in source order
    3. Stroke merging                     — before ordering to reduce TSP n
    4. TSP ordering                       — nearest-neighbour + 2-opt
    5. Bidirectional passes               — snake sequential parallel strokes

All passes operate on the same stroke tuple format:
    (start_x, start_y, [(dx, dy), ...])
All coordinates in machine units (integer, 0.01mm per unit).
"""

import math
from typing import List, Tuple, Optional

__version__ = '0.1.0'

__all__ = ['optimise_strokes']

Stroke  = Tuple[int, int, list]          # (sx, sy, [(dx,dy),...])
Point   = Tuple[int, int]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _dist(a: Point, b: Point) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _endpoint(stroke: Stroke) -> Point:
    """Absolute end position of a stroke."""
    sx, sy, segs = stroke
    ex = sx + sum(dx for dx, dy in segs)
    ey = sy + sum(dy for dx, dy in segs)
    return (ex, ey)


def _reverse(stroke: Stroke) -> Stroke:
    """
    Reverse a stroke: new start = old end, segments negated and reversed.
    Closed strokes (start == end) are returned unchanged.
    """
    sx, sy, segs = stroke
    end = _endpoint(stroke)
    if end == (sx, sy):          # closed loop — reversing is a no-op
        return stroke
    new_segs = [(-dx, -dy) for dx, dy in reversed(segs)]
    return (end[0], end[1], new_segs)


def _is_parallel(a: Stroke, b: Stroke,
                 angle_tol_deg: float = 5.0,
                 spacing_tol: float = 50.0) -> bool:
    """
    Return True if two strokes are parallel passes:
      - Same dominant axis direction (within angle_tol_deg)
      - Perpendicular separation within spacing_tol units
    Used for bidirectional pass detection.
    """
    def _heading(s: Stroke) -> float:
        sx, sy, segs = s
        ex, ey = _endpoint(s)
        return math.atan2(ey - sy, ex - sx)

    ha = _heading(a)
    hb = _heading(b)
    diff = abs(math.degrees(ha - hb)) % 180
    if diff > angle_tol_deg and diff < 180 - angle_tol_deg:
        return False

    # Check perpendicular separation
    ax, ay = (a[0] + _endpoint(a)[0]) / 2, (a[1] + _endpoint(a)[1]) / 2
    bx, by = (b[0] + _endpoint(b)[0]) / 2, (b[1] + _endpoint(b)[1]) / 2
    return _dist((ax, ay), (bx, by)) <= spacing_tol


# ---------------------------------------------------------------------------
# Pass 1 — per-stroke direction reversal (greedy, source order)
# ---------------------------------------------------------------------------

def _apply_reversal(strokes: List[Stroke]) -> List[Stroke]:
    """
    For each stroke, choose the entry end that is closer to the previous
    stroke's exit point. Operates in source order — this is a greedy
    per-stroke decision, not globally optimal, but it catches most
    mergeable pairs before the TSP runs.
    """
    if not strokes:
        return strokes

    result  = []
    cur_pos: Point = (0, 0)   # tool starts at home

    for stroke in strokes:
        start = (stroke[0], stroke[1])
        end   = _endpoint(stroke)

        d_start = _dist(cur_pos, start)
        d_end   = _dist(cur_pos, end)

        if d_end < d_start:
            stroke = _reverse(stroke)

        result.append(stroke)
        cur_pos = _endpoint(stroke)

    return result


# ---------------------------------------------------------------------------
# Pass 2 — stroke merging
# ---------------------------------------------------------------------------

def _apply_merging(strokes: List[Stroke],
                   tolerance_units: float) -> List[Stroke]:
    """
    Merge consecutive strokes whose junction is within tolerance_units.
    The merged stroke starts at stroke N's start and ends at stroke N+1's end.
    Runs in source order on the (already reversed) stroke list.
    """
    if len(strokes) < 2:
        return strokes

    result = [strokes[0]]

    for stroke in strokes[1:]:
        prev      = result[-1]
        prev_end  = _endpoint(prev)
        curr_start = (stroke[0], stroke[1])

        if _dist(prev_end, curr_start) <= tolerance_units:
            # Merge: bridge the gap with a zero-delta segment if there's
            # a sub-unit rounding gap, then append new stroke's segments
            gap_dx = curr_start[0] - prev_end[0]
            gap_dy = curr_start[1] - prev_end[1]
            bridge = [(gap_dx, gap_dy)] if (gap_dx or gap_dy) else []
            merged = (prev[0], prev[1],
                      prev[2] + bridge + list(stroke[2]))
            result[-1] = merged
        else:
            result.append(stroke)

    return result


# ---------------------------------------------------------------------------
# Pass 3 — TSP ordering (nearest-neighbour + 2-opt)
# ---------------------------------------------------------------------------

def _nn_order(strokes: List[Stroke]) -> List[Stroke]:
    """
    Nearest-neighbour TSP heuristic.
    At each step, pick the unvisited stroke whose nearest endpoint
    (start or end) is closest to the current tool position.
    Entry direction is set by which endpoint was chosen.
    """
    if not strokes:
        return strokes

    remaining = list(strokes)
    ordered   = []
    cur_pos: Point = (0, 0)

    while remaining:
        best_idx  = 0
        best_dist = math.inf
        best_rev  = False

        for i, stroke in enumerate(remaining):
            start = (stroke[0], stroke[1])
            end   = _endpoint(stroke)
            d_start = _dist(cur_pos, start)
            d_end   = _dist(cur_pos, end)

            if d_start <= d_end:
                d, rev = d_start, False
            else:
                d, rev = d_end, True

            if d < best_dist:
                best_dist = d
                best_idx  = i
                best_rev  = rev

        stroke = remaining.pop(best_idx)
        if best_rev:
            stroke = _reverse(stroke)
        ordered.append(stroke)
        cur_pos = _endpoint(stroke)

    return ordered


def _tour_distance(strokes: List[Stroke]) -> float:
    """Total pen-up travel distance for a given stroke order."""
    if not strokes:
        return 0.0
    total = _dist((0, 0), (strokes[0][0], strokes[0][1]))
    for i in range(len(strokes) - 1):
        total += _dist(_endpoint(strokes[i]),
                       (strokes[i+1][0], strokes[i+1][1]))
    return total


def _two_opt(strokes: List[Stroke]) -> List[Stroke]:
    """
    2-opt improvement: try reversing every sub-sequence of the tour.
    Keep any swap that reduces total pen-up travel distance.
    Runs until no improvement is found (convergence).
    O(n²) per pass — fast for practical stroke counts (<500).

    When a tour sub-sequence is reversed, every stroke in it is also
    direction-reversed: each is now entered from its previous exit end.
    """
    improved  = True
    best      = list(strokes)
    best_dist = _tour_distance(best)

    while improved:
        improved = False
        for i in range(len(best) - 1):
            for j in range(i + 2, len(best)):
                # Reverse the tour segment best[i+1 : j+1], reversing the
                # direction of every stroke within it
                candidate = (best[:i+1]
                             + [_reverse(s) for s in reversed(best[i+1:j+1])]
                             + best[j+1:])

                cand_dist = _tour_distance(candidate)
                if cand_dist < best_dist - 0.5:
                    best      = candidate
                    best_dist = cand_dist
                    improved  = True

    return best


def _apply_ordering(strokes: List[Stroke]) -> List[Stroke]:
    ordered = _nn_order(strokes)
    return _two_opt(ordered)


# ---------------------------------------------------------------------------
# Pass 4 — bidirectional passes
# ---------------------------------------------------------------------------

def _apply_bidirectional(strokes: List[Stroke],
                          angle_tol_deg: float = 5.0,
                          spacing_tol: float = 50.0) -> List[Stroke]:
    """
    Detect runs of parallel strokes and snake their directions so the
    tool alternates rather than always returning to the same side.

    A run is a maximal sequence of consecutive strokes that are pairwise
    parallel (same heading ± angle_tol_deg, perpendicular spacing within
    spacing_tol units).
    """
    if len(strokes) < 2:
        return strokes

    result = list(strokes)
    i = 0

    while i < len(result):
        # Find the extent of the current parallel run starting at i
        run_end = i + 1
        while (run_end < len(result) and
               _is_parallel(result[run_end - 1], result[run_end],
                             angle_tol_deg, spacing_tol)):
            run_end += 1

        run_len = run_end - i
        if run_len >= 2:
            # Reverse every other stroke in the run (snake pattern)
            for k in range(i, run_end):
                if (k - i) % 2 == 1:
                    result[k] = _reverse(result[k])

        i = run_end

    return result


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def optimise_strokes(
    strokes:            List[Stroke],
    merge:              bool  = True,
    merge_tolerance_mm: float = 0.1,
    units_per_mm:       int   = 100,
    reorder:            bool  = True,
    bidirectional:      bool  = True,
    angle_tol_deg:      float = 5.0,
    spacing_tol_mm:     float = 0.5,
) -> List[Stroke]:
    """
    Optimise a stroke list for minimum pen-up travel distance.

    Parameters
    ----------
    strokes             List of (sx, sy, [(dx,dy),...]) tuples from
                        gcode_to_strokes(). Coordinates in machine units.
    merge               Enable stroke merging (default True).
    merge_tolerance_mm  Gap in mm within which adjacent stroke endpoints
                        are merged into one stroke (default 0.1mm).
    units_per_mm        Coordinate scale (default 100 = 0.01mm/unit).
    reorder             Enable TSP stroke ordering (default True).
    bidirectional       Enable bidirectional pass snaking (default True).
    angle_tol_deg       Heading tolerance for parallel pass detection.
    spacing_tol_mm      Max perpendicular spacing for parallel pass detection.

    Returns
    -------
    Optimised stroke list in the same format as the input.
    """
    if not strokes:
        return strokes

    tol_units     = merge_tolerance_mm * units_per_mm
    spacing_units = spacing_tol_mm    * units_per_mm

    # 1. Per-stroke direction reversal (greedy, source order)
    result = _apply_reversal(strokes)

    # 2. Stroke merging (before ordering to reduce TSP n)
    if merge:
        result = _apply_merging(result, tol_units)

    # 3. TSP ordering — nearest-neighbour + 2-opt improvement
    if reorder:
        result = _apply_ordering(result)

    # 4. Bidirectional passes
    if bidirectional:
        result = _apply_bidirectional(result, angle_tol_deg, spacing_units)

    return result


# ---------------------------------------------------------------------------
# Duration estimation
# ---------------------------------------------------------------------------

# Motion constants - characterised Session 10 from EP 0x82 axis telemetry.
# Method: 90mm isolated X strokes at VS=10/25/40 via data/capture_motion.py.
# Telemetry reports ABSOLUTE position at 100 units/mm (0.01mm), confirmed
# against the known 90mm of travel (Open Question #2 resolved). See TELEMETRY.md.
#
#   _VS_TO_MM_PER_SEC : VS10 cruise = 995 units/s = 9.95 mm/s (five consecutive
#                       constant-velocity samples); VS25 confirms at 0.994/VS.
#                       Velocity is linear in VS.
#   _RAPID_MULTIPLIER : pen-up return moves ran at roughly engrave speed - rapids
#                       are NOT dramatically faster on this machine (provisional).
#   Acceleration      : NOT modelled. Telemetry (~0.6s/packet) shows the machine
#                       reaches cruise within one sample interval; at VS10 position
#                       is dead-linear from the first moving sample. Ramps finish
#                       in <~0.5s (<~0.2s/stroke), so a constant-velocity model fits.
_VS_TO_MM_PER_SEC = 0.995   # mm/sec per VS unit (telemetry-measured)
_RAPID_MULTIPLIER = 1.0     # rapid speed / engrave speed (~1 measured; provisional)
# Per-stroke Z dip (ZA approach + PU lift). Not separable from telemetry - Z is
# sampled too coarsely and ZA is partly operator-gated by the PD green press.
# Rough fixed estimate; tune against a stopwatch on a multi-stroke job if needed.
_Z_DWELL_SEC = 0.5          # estimated seconds of Z-dip overhead per stroke


def estimate_duration(
    strokes:            List[Stroke],
    speed:              int   = 25,
    units_per_mm:       int   = 100,
) -> Optional[float]:
    """
    Estimate job duration in seconds using a constant-velocity model.

    Motion constants were characterised from EP 0x82 axis telemetry (Session 10);
    see the constants block above and TELEMETRY.md. Acceleration is not modelled:
    the machine reaches cruise within one telemetry sample (ramp <~0.5s), so a
    constant-velocity model is accurate to within ~0.2s per stroke.

    Accounts for:
      - Engrave travel at speed * _VS_TO_MM_PER_SEC
      - Pen-up rapid moves (rapid speed = engrave speed * _RAPID_MULTIPLIER)
      - A fixed per-stroke Z-dip overhead (_Z_DWELL_SEC)
    """
    if not strokes:
        return 0.0
    if _VS_TO_MM_PER_SEC is None:
        return None   # velocity constant explicitly unset (re-characterisation)

    v_mm_per_sec     = speed * _VS_TO_MM_PER_SEC
    rapid_mm_per_sec = v_mm_per_sec * _RAPID_MULTIPLIER

    def _move_time(dist_mm: float, vel: float) -> float:
        return dist_mm / vel if (dist_mm > 0 and vel > 0) else 0.0

    total = 0.0
    cur_x, cur_y = 0, 0

    for sx, sy, segs in strokes:
        # Pen-up rapid to stroke start
        rapid_dist = math.hypot(sx - cur_x, sy - cur_y) / units_per_mm
        total += _move_time(rapid_dist, rapid_mm_per_sec)

        # Z dip (approach + lift): fixed per-stroke overhead
        total += _Z_DWELL_SEC

        # Pen-down engraving travel (continuous at engrave speed)
        stroke_mm = sum(math.hypot(dx, dy) for dx, dy in segs) / units_per_mm
        total += _move_time(stroke_mm, v_mm_per_sec)

        # Advance tool position to stroke end
        cur_x = sx + sum(dx for dx, dy in segs)
        cur_y = sy + sum(dy for dx, dy in segs)

    # Final return to home
    home_dist = math.hypot(cur_x, cur_y) / units_per_mm
    total += _move_time(home_dist, rapid_mm_per_sec)

    return total
