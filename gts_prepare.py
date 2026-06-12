#!/usr/bin/env python3
"""
gts_prepare.py — Job preparation for GT Smartstream machines.

Single authoritative location for:
  - Coordinate system knowledge (home position, axis directions)
  - Coordinate transforms (origin shift, Y-axis flip for LightBurn files)
  - Work envelope validation
  - Source-agnostic job construction (from G-Code, from file, from shapes)

All callers pass source coordinates in their native system. gts_prepare
applies the correct transforms for the target machine and validates the
result against the work envelope before returning a GTS binary.

Coordinate system (M20, confirmed from hardware):
    Home (0,0) = back-left corner of work area (nearest the gantry)
    +X         = tool moves right
    +Y         = mark moves toward the operator (table translates away)
    Units      = 0.01mm (100 units per mm)

    LightBurn files use Y-up convention — pass flip_y=True.
    flip_y applies: y_machine = envelope_height_mm - y_source

Input format support:
    from_gcode()   G-Code text (RS-274)       — all CAM tools, LightBurn, Inkscape
    from_file()    G-Code file on disk         — auto-detects inch/mm mode
    square()       Programmatic square shape
    circle()       Programmatic circle shape
    home()         Homing GTS binary

    Future:
    from_svg()     SVG path data               — Illustrator, Inkscape, CorelDRAW
    from_dxf()     DXF file                    — AutoCAD, Rhino, CorelDRAW
"""

import re
from typing import Tuple, Optional

from gts_api import (
    MachineProfile, M20_PROFILE,
    UNITS_PER_MM, WORK_ENVELOPE_MM, PHYSICAL_ENVELOPE_MM,
    build_gts, build_home_gts, build_square, build_circle,
    DEFAULT_SPEED, DEFAULT_DEPTH, DEFAULT_LIFT,
)

__version__ = '0.1.0'
from gts_translator import GCodeParser, gcode_to_strokes
from gts_optimise import optimise_strokes

__version__ = '0.1.0'


__all__ = [
    'from_gcode',
    'from_file',
    'square',
    'circle',
    'home',
    'optimise_strokes',
]


# ---------------------------------------------------------------------------
# Job preparation
# ---------------------------------------------------------------------------

def from_gcode(
    gcode_text:   str,
    origin:       Tuple[float, float] = (0.0, 0.0),
    flip_y:       bool                = False,
    scale:        float               = 1.0,
    speed:        int                 = DEFAULT_SPEED,
    depth:        int                 = DEFAULT_DEPTH,
    lift:         int                 = DEFAULT_LIFT,
    arc_steps:    int                 = 72,
    units:        str                 = 'mm',
    profile:      MachineProfile      = M20_PROFILE,
    optimise:     bool                = True,
    merge:        bool                = True,
    merge_tolerance_mm: float         = 0.1,
    envelope_mm:  Optional[Tuple[float, float]] = None,
) -> bytes:
    """
    Translate G-Code text to a validated GTS binary.

    origin             -- shift job origin by (x, y) mm
    flip_y             -- invert Y axis (use for LightBurn files)
    scale              -- uniform scale multiplier
    profile            -- target machine profile (default M20_PROFILE)
    optimise           -- enable toolpath optimisation (default True)
    merge              -- enable stroke merging (default True)
    merge_tolerance_mm -- merge gap threshold in mm (default 0.1)
    """
    # Envelope override: explicit envelope_mm wins over the profile default
    env = envelope_mm if envelope_mm is not None else profile.envelope_mm

    # Apply profile-level adjustments
    effective_scale = scale * profile.coord_scale
    scaled_origin   = (origin[0] * effective_scale, origin[1] * effective_scale)

    parser = GCodeParser(
        units=units,
        arc_steps=arc_steps,
        origin=scaled_origin,
        flip_y=flip_y,
        negate_x=profile.negate_x,
        envelope_mm=env,
    )
    moves = parser.parse(gcode_text)

    strokes = gcode_to_strokes(
        moves,
        scale=float(profile.units_per_mm) * effective_scale,
        envelope_mm=env,
    )

    if not strokes:
        raise ValueError(
            'No engraving strokes found. '
            'Ensure G-Code has G1/G2/G3 moves with Z ≤ 0.'
        )

    if optimise:
        strokes = optimise_strokes(
            strokes,
            merge=merge,
            merge_tolerance_mm=merge_tolerance_mm,
            units_per_mm=profile.units_per_mm,
        )

    return build_gts(strokes, speed=speed, pz=lift, dz=depth)


def from_file(
    path:         str,
    origin:       Tuple[float, float] = (0.0, 0.0),
    flip_y:       bool                = False,
    scale:        float               = 1.0,
    speed:        int                 = DEFAULT_SPEED,
    depth:        int                 = DEFAULT_DEPTH,
    lift:         int                 = DEFAULT_LIFT,
    arc_steps:    int                 = 72,
    profile:      MachineProfile      = M20_PROFILE,
    optimise:     bool                = True,
    merge:        bool                = True,
    merge_tolerance_mm: float         = 0.1,
    envelope_mm:  Optional[Tuple[float, float]] = None,
) -> bytes:
    """
    Load a G-Code file from disk and translate to GTS binary.
    Auto-detects inch/mm mode from G-Code content (G20/G21).
    """
    with open(path, encoding='utf-8') as f:
        gcode = f.read()

    units = 'inch' if re.search(r'\bG20\b', gcode) else 'mm'

    return from_gcode(
        gcode,
        origin=origin,
        flip_y=flip_y,
        scale=scale,
        speed=speed,
        depth=depth,
        lift=lift,
        arc_steps=arc_steps,
        units=units,
        profile=profile,
        optimise=optimise,
        merge=merge,
        merge_tolerance_mm=merge_tolerance_mm,
        envelope_mm=envelope_mm,
    )


def square(
    centre_x_mm:  float          = 50.0,
    centre_y_mm:  float          = 50.0,
    side_mm:      float          = 50.0,
    scale:        float          = 1.0,
    speed:        int            = DEFAULT_SPEED,
    depth:        int            = DEFAULT_DEPTH,
    profile:      MachineProfile = M20_PROFILE,
) -> bytes:
    """Build a GTS binary for a square, with optional scale."""
    return build_square(
        centre_x_mm=centre_x_mm,
        centre_y_mm=centre_y_mm,
        side_mm=side_mm * scale,
        speed=speed,
        dz=depth,
    )


def circle(
    centre_x_mm:  float          = 50.0,
    centre_y_mm:  float          = 50.0,
    radius_mm:    float          = 45.0,
    scale:        float          = 1.0,
    steps:        int            = 72,
    speed:        int            = DEFAULT_SPEED,
    depth:        int            = DEFAULT_DEPTH,
    profile:      MachineProfile = M20_PROFILE,
) -> bytes:
    """Build a GTS binary for a circle, with optional scale."""
    return build_circle(
        centre_x_mm=centre_x_mm,
        centre_y_mm=centre_y_mm,
        radius_mm=radius_mm * scale,
        steps=steps,
        speed=speed,
        dz=depth,
    )


def home(profile: MachineProfile = M20_PROFILE) -> bytes:
    """Build the homing GTS binary."""
    return build_home_gts()
