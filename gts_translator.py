#!/usr/bin/env python3
"""
gts_translator.py — G-Code to GT Smartstream binary translator.
"""

import math
import sys
import re
from dataclasses import dataclass
from typing import List, Tuple, Optional

from gts_api import (

    UNITS_PER_MM, WORK_ENVELOPE_MM, PHYSICAL_ENVELOPE_MM,
    DEFAULT_SPEED, DEFAULT_DEPTH, DEFAULT_LIFT,
    build_gts, build_envelope,
)

__version__ = '0.1.0'


@dataclass
class GMove:
    """A single decoded G-Code move."""
    rapid:   bool           = False
    x:       Optional[float] = None
    y:       Optional[float] = None
    z:       Optional[float] = None
    feed:    Optional[float] = None


class GCodeParser:
    """
    RS-274 G-Code parser.

    Supported: G0, G1, G2, G3, G4, G17, G20, G21, G90, G91, G92,
               M0, M2, M30, M3, M4, M5, M6, F, S, T, X, Y, Z, I, J, K
    """

    def __init__(self, units: str = 'mm', arc_steps: int = 72,
                 origin: Tuple[float, float] = (0.0, 0.0),
                 flip_y:    bool = False,
                 negate_x:  bool = False,
                 envelope_mm: Tuple[float, float] = WORK_ENVELOPE_MM):
        self.arc_steps   = arc_steps
        self.origin_x, self.origin_y = origin
        self.flip_y      = flip_y
        self.negate_x    = negate_x
        self.envelope_y  = envelope_mm[1]
        self.absolute    = True
        self.inch_mode   = (units == 'inch')
        self.x = self.y = self.z = 0.0
        self.feed        = 600.0
        self._g92_x = self._g92_y = 0.0
        self._last_motion = 'G1'
        self.warnings: List[str] = []

    def _to_mm(self, v: float) -> float:
        return v * 25.4 if self.inch_mode else v

    def _word(self, line: str, letter: str) -> Optional[float]:
        m = re.search(rf'(?<![A-Z]){letter}([+-]?\d+\.?\d*)', line, re.IGNORECASE)
        return float(m.group(1)) if m else None

    def parse(self, gcode_text: str) -> List[GMove]:
        moves = []
        for raw_line in gcode_text.splitlines():
            line = re.sub(r'\(.*?\)', '', raw_line)
            line = line.split(';')[0].strip().upper()
            if not line:
                continue

            if re.search(r'G20(?!\d)', line): self.inch_mode = True
            if re.search(r'G21(?!\d)', line): self.inch_mode = False
            if re.search(r'G90(?!\d)', line): self.absolute  = True
            if re.search(r'G91(?!\d)', line): self.absolute  = False
            if re.search(r'G17(?!\d)', line): pass
            if re.search(r'G4(?!\d)',  line): pass

            if re.search(r'G18(?!\d)', line):
                raise ValueError('G18 (XZ plane arcs) not supported — M20 is 2.5D only.')
            if re.search(r'G19(?!\d)', line):
                raise ValueError('G19 (YZ plane arcs) not supported — M20 is 2.5D only.')

            if re.search(r'G92(?!\d)', line):
                ox = self._word(line, 'X')
                oy = self._word(line, 'Y')
                if ox is not None: self._g92_x = self._to_mm(ox)
                if oy is not None: self._g92_y = self._to_mm(oy)
                continue

            if re.search(r'M0(?!\d)|M2(?!\d)|M30(?!\d)', line):
                moves.append(GMove(rapid=True, x=self.x, y=self.y, z=1.0))
                continue
            if re.search(r'M6(?!\d)', line):
                self.warnings.append('M6 tool change ignored — M20 is single-tool')
                continue
            if re.search(r'M3(?!\d)|M4(?!\d)|M5(?!\d)', line):
                pass

            f = self._word(line, 'F')
            if f is not None:
                self.feed = self._to_mm(f)

            has_g0 = bool(re.search(r'G0(?![\d.])', line))
            has_g1 = bool(re.search(r'G1(?![\d.])', line))
            has_g2 = bool(re.search(r'G2(?![\d.])', line))
            has_g3 = bool(re.search(r'G3(?![\d.])', line))

            if has_g0: self._last_motion = 'G0'
            elif has_g1: self._last_motion = 'G1'
            elif has_g2: self._last_motion = 'G2'
            elif has_g3: self._last_motion = 'G3'

            if not any([has_g0, has_g1, has_g2, has_g3]):
                x = self._word(line, 'X')
                y = self._word(line, 'Y')
                if x is None and y is None:
                    continue
                if   self._last_motion == 'G0': has_g0 = True
                elif self._last_motion == 'G2': has_g2 = True
                elif self._last_motion == 'G3': has_g3 = True
                else:                            has_g1 = True

            x_raw = self._word(line, 'X')
            y_raw = self._word(line, 'Y')
            z_raw = self._word(line, 'Z')
            i_raw = self._word(line, 'I')
            j_raw = self._word(line, 'J')

            def resolve(raw, current, g92_off):
                if raw is None:
                    return current
                v = self._to_mm(raw)
                if self.absolute:
                    return v - g92_off
                else:
                    return current + v

            new_x = resolve(x_raw, self.x, self._g92_x)
            new_y = resolve(y_raw, self.y, self._g92_y)
            new_z = resolve(z_raw, self.z, 0.0)
            i_mm  = self._to_mm(i_raw) if i_raw is not None else 0.0
            j_mm  = self._to_mm(j_raw) if j_raw is not None else 0.0

            if has_g2 or has_g3:
                arc_moves = self._expand_arc(
                    self.x, self.y, new_x, new_y,
                    i_mm, j_mm, cw=has_g2)
                for m in arc_moves:
                    if m.x is not None:
                        m.x += self.origin_x
                        if self.negate_x: m.x = -m.x
                    if m.y is not None:
                        m.y += self.origin_y
                        if self.flip_y: m.y = self.envelope_y - m.y
                moves.extend(arc_moves)
            else:
                fy = new_y + self.origin_y
                if self.flip_y: fy = self.envelope_y - fy
                fx = new_x + self.origin_x
                if self.negate_x: fx = -fx
                moves.append(GMove(
                    rapid=(has_g0),
                    x=fx if new_x is not None else None,
                    y=fy if new_y is not None else None,
                    z=new_z,
                    feed=self.feed if has_g1 else None
                ))

            self.x, self.y, self.z = new_x, new_y, new_z

        return moves

    def _expand_arc(self, x0, y0, x1, y1, i, j, cw: bool) -> List[GMove]:
        cx, cy = x0 + i, y0 + j
        r0     = math.hypot(x0 - cx, y0 - cy)
        ang0   = math.atan2(y0 - cy, x0 - cx)
        ang1   = math.atan2(y1 - cy, x1 - cx)

        if cw:
            if ang1 >= ang0: ang1 -= 2 * math.pi
        else:
            if ang1 <= ang0: ang1 += 2 * math.pi

        span  = abs(ang1 - ang0)
        steps = max(3, int(math.ceil(span / (2 * math.pi) * self.arc_steps)))
        result = []
        for k in range(1, steps + 1):
            t   = k / steps
            ang = ang0 + (ang1 - ang0) * t
            result.append(GMove(
                rapid=False,
                x=cx + r0 * math.cos(ang),
                y=cy + r0 * math.sin(ang),
                z=None,
                feed=self.feed
            ))
        return result


def gcode_to_strokes(moves: List[GMove],
                     scale: float = float(UNITS_PER_MM),
                     z_threshold: float = 0.0,
                     envelope_mm: Tuple[float, float] = WORK_ENVELOPE_MM,
                     ) -> List[Tuple[int, int, list]]:
    """
    Convert a list of GMoves into GTS stroke tuples.

    Returns: [(abs_x, abs_y, [(dX, dY), ...]), ...]
    All coordinates in machine units (scale * mm).
    """
    def _u(mm: float) -> int:
        return int(round(mm * scale))

    env_x_mm, env_y_mm = envelope_mm
    phys_x_mm, phys_y_mm = PHYSICAL_ENVELOPE_MM
    all_x = [m.x for m in moves if m.x is not None]
    all_y = [m.y for m in moves if m.y is not None]
    if all_x and all_y:
        min_x, max_x = min(all_x), max(all_x)
        min_y, max_y = min(all_y), max(all_y)
        if (max_x > phys_x_mm or max_y > phys_y_mm):
            raise ValueError(
                f'Job exceeds work envelope ({env_x_mm:.0f}\u00d7{env_y_mm:.0f}mm).\n'
                f'  X range: {min_x:.2f} to {max_x:.2f}mm '
                f'(envelope: 0.0 to {env_x_mm:.1f}mm)\n'
                f'  Y range: {min_y:.2f} to {max_y:.2f}mm '
                f'(envelope: 0.0 to {env_y_mm:.1f}mm)\n'
                f'  Adjust your job origin. Home (0,0) is the back-left corner (nearest the gantry).'
            )

    strokes   = []
    cur_x = cur_y = 0.0
    pen_down  = False
    sx = sy   = 0
    seg_buf: List[Tuple[int, int]] = []

    def flush():
        nonlocal seg_buf, pen_down
        if seg_buf:
            strokes.append((sx, sy, list(seg_buf)))
            seg_buf.clear()
        pen_down = False

    for mv in moves:
        tx = mv.x if mv.x is not None else cur_x
        ty = mv.y if mv.y is not None else cur_y
        tz = mv.z if mv.z is not None else 0.0

        cutting = (not mv.rapid) and (tz <= z_threshold)

        if not cutting:
            flush()
        else:
            if not pen_down:
                sx, sy = _u(cur_x), _u(cur_y)
                pen_down = True
            dx = _u(tx) - _u(cur_x)
            dy = _u(ty) - _u(cur_y)
            if dx != 0 or dy != 0:
                seg_buf.append((dx, dy))

        cur_x, cur_y = tx, ty

    flush()
    return strokes


def translate(gcode_text: str,
              units:       str   = 'mm',
              speed:       int   = DEFAULT_SPEED,
              depth:       int   = DEFAULT_DEPTH,
              lift:        int   = DEFAULT_LIFT,
              scale:       float = float(UNITS_PER_MM),
              arc_steps:   int   = 72,
              origin:      Tuple[float, float] = (0.0, 0.0),
              flip_y:      bool  = False,
              negate_x:    bool  = False,
              wrap:        bool  = True,
              dry_run:     bool  = False,
              envelope_mm: Tuple[float, float] = WORK_ENVELOPE_MM) -> bytes:
    """
    Full pipeline: G-Code text → GTS binary bytes.

    wrap=True  → returns ASCII envelope (!PS !OR !MM) + GTS binary (for Windows spooler path only)
    wrap=False → returns raw GTS binary only — use this for direct USB and .m20 files
    dry_run    → prints decoded moves, returns b''
    """
    parser = GCodeParser(units=units, arc_steps=arc_steps, origin=origin,
                         flip_y=flip_y, negate_x=negate_x,
                         envelope_mm=envelope_mm)
    moves  = parser.parse(gcode_text)

    if parser.warnings:
        for w in parser.warnings:
            print(f'[WARN] {w}', file=sys.stderr)

    if dry_run:
        print(f'Decoded {len(moves)} G-Code moves '
              f'(scale={scale} units/mm = {1/scale:.4f} mm/unit):')
        for i, m in enumerate(moves):
            kind = 'RAPID' if m.rapid else 'CUT  '
            x = f'{m.x:.3f}' if m.x is not None else '—'
            y = f'{m.y:.3f}' if m.y is not None else '—'
            z = f'{m.z:.3f}' if m.z is not None else '0.000'
            print(f'  [{i:4d}] {kind}  X={x:8}  Y={y:8}  Z={z}  F={m.feed}')
        return b''

    strokes = gcode_to_strokes(moves, scale=scale, envelope_mm=envelope_mm)

    if not strokes:
        raise ValueError(
            'No engraving strokes found. '
            'Ensure G-Code has G1/G2/G3 moves with Z \u2264 0.'
        )

    gts = build_gts(strokes, speed=speed, pz=lift, dz=depth)
    return build_envelope(gts) if wrap else gts
