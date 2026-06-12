#!/usr/bin/env python3
"""
gts_preview.py — Render a Gravograph M20 GTS file as an SVG toolpath preview.

Parses the binary GTS format (confirmed from 00013.SPL / 00018.SPL reference captures)
and emits an SVG showing:
  - The 100x100mm software work envelope (dashed border)
  - Each pen-down stroke as a coloured polyline
  - Rapid (pen-up) moves as thin dashed lines
  - Stroke start markers

Usage:
  python3 gts_preview.py input.m20              # writes input.svg
  python3 gts_preview.py input.m20 output.svg   # explicit output path
  python3 gts_preview.py input.m20 --show       # open in browser after writing

Stroke headers are RELATIVE rapid deltas from the current tool position
(hardware-confirmed 2026-06-09); the parser integrates them. Single-stroke
files are unaffected (tool starts at home: delta == absolute).
"""

import struct, sys, argparse, os

__version__ = '0.1.0'


# ---------------------------------------------------------------------------
# GTS parser
# ---------------------------------------------------------------------------

ACCEL_SENTINEL = 551   # 0x0227 — constant in all observed segment records


def parse_gts(data: bytes):
    """
    Parse binary GTS data into a list of strokes.

    Returns:
        strokes: list of dicts, each with:
            'start': (x_mm, y_mm)   — rapid-to position (pen up)
            'points': [(x_mm, y_mm), ...]  — pen-down path including start point
        rapid_moves: list of (from_mm, to_mm) tuples for pen-up rapid moves
        bounds: (min_x, min_y, max_x, max_y) in mm
    """
    strokes = []
    tool_x, tool_y = 0.0, 0.0  # track tool position for rapid lines
    rapid_moves = []

    pos = 0
    while pos < len(data) - 5:
        pd_idx = data.find(b'\xff\xfdPD', pos)
        if pd_idx == -1:
            break

        # Stroke header is 18 bytes before PD:
        #   12 bytes raw struct (pw1 pw2 speed accel hx hy)
        #   6 bytes FF FD ZA arg
        hdr_pos = pd_idx - 18
        if hdr_pos < 0:
            pos = pd_idx + 6
            continue

        _, _, _, acc, hx, hy = struct.unpack_from('>hhhhhh', data, hdr_pos)
        if acc != ACCEL_SENTINEL:
            pos = pd_idx + 6
            continue

        # Stroke header is a RELATIVE rapid delta from the current tool
        # position (confirmed on hardware 2026-06-09, telemetry-verified).
        # First stroke: tool at home, so delta == absolute.
        start_x = tool_x + hx / 100.0
        start_y = tool_y + hy / 100.0

        # Record the rapid move from current tool position to stroke start
        if strokes or (tool_x != 0 or tool_y != 0):
            rapid_moves.append(((tool_x, tool_y), (start_x, start_y)))

        # Collect pen-down segments after PD until next FF FD opcode
        seg_pos = pd_idx + 6
        points = [(start_x, start_y)]
        cx, cy = start_x, start_y

        while seg_pos + 12 <= len(data):
            if data[seg_pos:seg_pos+2] == b'\xff\xfd':
                break
            _, _, _, seg_acc, dx, dy = struct.unpack_from('>hhhhhh', data, seg_pos)
            if seg_acc != ACCEL_SENTINEL:
                break
            cx += dx / 100.0
            cy += dy / 100.0
            points.append((cx, cy))
            seg_pos += 12

        strokes.append({'start': (start_x, start_y), 'points': points})
        tool_x, tool_y = cx, cy
        pos = pd_idx + 6

    # Compute bounds from all pen-down points
    all_pts = [p for s in strokes for p in s['points']]
    if all_pts:
        min_x = min(p[0] for p in all_pts)
        max_x = max(p[0] for p in all_pts)
        min_y = min(p[1] for p in all_pts)
        max_y = max(p[1] for p in all_pts)
    else:
        min_x = min_y = max_x = max_y = 0.0

    return strokes, rapid_moves, (min_x, min_y, max_x, max_y)


# ---------------------------------------------------------------------------
# SVG renderer
# ---------------------------------------------------------------------------

# Stroke colours — cycle through these for multi-stroke jobs
STROKE_COLOURS = [
    '#1a73e8', '#e8710a', '#188038', '#a142f4',
    '#d93025', '#007b83', '#e52592', '#b5630f',
    '#137333', '#5f6368',
]


def render_svg(strokes, rapid_moves,
               envelope_mm=(100.0, 100.0),
               show_rapids=True,
               show_markers=True,
               px_per_mm=5.6,
               margin_px=40) -> str:
    """Render parsed GTS data to an SVG string."""

    env_w, env_h = envelope_mm
    svg_w = env_w * px_per_mm + 2 * margin_px
    svg_h = env_h * px_per_mm + 2 * margin_px

    def to_px(x_mm, y_mm):
        """Convert mm coordinates to SVG px (Y axis flipped — home at bottom-left)."""
        px = margin_px + x_mm * px_per_mm
        py = margin_px + (env_h - y_mm) * px_per_mm
        return px, py

    def pts(points):
        return ' '.join(f'{to_px(x,y)[0]:.1f},{to_px(x,y)[1]:.1f}' for x,y in points)

    lines = []
    lines.append(f'<svg xmlns="http://www.w3.org/2000/svg" '
                 f'width="{svg_w:.0f}" height="{svg_h:.0f}" '
                 f'viewBox="0 0 {svg_w:.0f} {svg_h:.0f}">')

    # Background
    lines.append(f'<rect width="{svg_w:.0f}" height="{svg_h:.0f}" fill="#1a1a2e"/>')

    # Grid lines (10mm spacing)
    lines.append('<g stroke="#2a2a4a" stroke-width="0.5">')
    for x in range(0, int(env_w)+1, 10):
        x1, y1 = to_px(x, 0)
        x2, y2 = to_px(x, env_h)
        lines.append(f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}"/>')
    for y in range(0, int(env_h)+1, 10):
        x1, y1 = to_px(0, y)
        x2, y2 = to_px(env_w, y)
        lines.append(f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}"/>')
    lines.append('</g>')

    # Work envelope border
    ex1, ey1 = to_px(0, 0)
    ex2, ey2 = to_px(env_w, env_h)
    lines.append(f'<rect x="{min(ex1,ex2):.1f}" y="{min(ey1,ey2):.1f}" '
                 f'width="{abs(ex2-ex1):.1f}" height="{abs(ey2-ey1):.1f}" '
                 f'fill="none" stroke="#444488" stroke-width="1" stroke-dasharray="6,3"/>')

    # Home marker
    hx, hy = to_px(0, 0)
    lines.append(f'<circle cx="{hx:.1f}" cy="{hy:.1f}" r="4" '
                 f'fill="none" stroke="#666699" stroke-width="1"/>')
    lines.append(f'<text x="{hx+6:.0f}" y="{hy-4:.0f}" '
                 f'font-family="monospace" font-size="9" fill="#666699">home</text>')

    # Axis labels
    for x in range(0, int(env_w)+1, 20):
        px, py = to_px(x, 0)
        lines.append(f'<text x="{px:.0f}" y="{py+12:.0f}" '
                     f'text-anchor="middle" font-family="monospace" font-size="8" '
                     f'fill="#444466">{x}</text>')
    for y in range(0, int(env_h)+1, 20):
        if y == 0: continue
        px, py = to_px(0, y)
        lines.append(f'<text x="{px-4:.0f}" y="{py+3:.0f}" '
                     f'text-anchor="end" font-family="monospace" font-size="8" '
                     f'fill="#444466">{y}</text>')

    # Rapid moves (pen-up paths)
    if show_rapids and rapid_moves:
        lines.append('<g stroke="#334455" stroke-width="0.8" stroke-dasharray="3,3" fill="none">')
        for (fx, fy), (tx, ty) in rapid_moves:
            x1, y1 = to_px(fx, fy)
            x2, y2 = to_px(tx, ty)
            lines.append(f'<line x1="{x1:.1f}" y1="{y1:.1f}" '
                         f'x2="{x2:.1f}" y2="{y2:.1f}"/>')
        lines.append('</g>')

    # Pen-down strokes
    for i, stroke in enumerate(strokes):
        colour = STROKE_COLOURS[i % len(STROKE_COLOURS)]
        points_str = pts(stroke['points'])
        lines.append(f'<polyline points="{points_str}" '
                     f'fill="none" stroke="{colour}" stroke-width="1.5" '
                     f'stroke-linecap="round" stroke-linejoin="round"/>')

        # Stroke start marker
        if show_markers:
            sx, sy = to_px(*stroke['start'])
            lines.append(f'<circle cx="{sx:.1f}" cy="{sy:.1f}" r="2.5" '
                         f'fill="{colour}" opacity="0.7"/>')

    # Stats overlay
    min_x = min(p[0] for s in strokes for p in s['points']) if strokes else 0
    max_x = max(p[0] for s in strokes for p in s['points']) if strokes else 0
    min_y = min(p[1] for s in strokes for p in s['points']) if strokes else 0
    max_y = max(p[1] for s in strokes for p in s['points']) if strokes else 0
    total_segs = sum(len(s['points'])-1 for s in strokes)
    cx_mm = (min_x + max_x) / 2
    cy_mm = (min_y + max_y) / 2

    info = [
        f'strokes: {len(strokes)}',
        f'segments: {total_segs}',
        f'width: {max_x-min_x:.1f}mm',
        f'height: {max_y-min_y:.1f}mm',
        f'centre: ({cx_mm:.1f}, {cy_mm:.1f})mm',
    ]
    for j, line in enumerate(info):
        lines.append(f'<text x="{margin_px+4}" y="{margin_px + 14 + j*14}" '
                     f'font-family="monospace" font-size="10" fill="#8888aa">{line}</text>')

    lines.append('</svg>')
    return '\n'.join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description='Render M20 GTS file as SVG preview')
    ap.add_argument('input',          help='Input .m20 file')
    ap.add_argument('output',  nargs='?', help='Output .svg file (default: input.svg)')
    ap.add_argument('--no-rapids',    action='store_true', help='Hide rapid (pen-up) moves')
    ap.add_argument('--no-markers',   action='store_true', help='Hide stroke start markers')
    ap.add_argument('--scale',        type=float, default=5.6,
                    help='Pixels per mm (default 5.6 → 560px for 100mm bed)')
    ap.add_argument('--show',         action='store_true', help='Open SVG in browser after writing')
    args = ap.parse_args()

    if not os.path.exists(args.input):
        sys.exit(f'File not found: {args.input}')

    output = args.output or os.path.splitext(args.input)[0] + '.svg'

    with open(args.input, 'rb') as f:
        data = f.read()

    print(f'Parsing {args.input} ({len(data)} bytes)...')
    strokes, rapids, bounds = parse_gts(data)

    if not strokes:
        sys.exit('No pen-down strokes found in GTS file.')

    min_x, min_y, max_x, max_y = bounds
    print(f'  Strokes:  {len(strokes)}')
    print(f'  Segments: {sum(len(s["points"])-1 for s in strokes)}')
    print(f'  Bounds:   X{min_x:.1f}–{max_x:.1f}mm  Y{min_y:.1f}–{max_y:.1f}mm')
    print(f'  Centre:   ({(min_x+max_x)/2:.1f}, {(min_y+max_y)/2:.1f})mm')

    svg = render_svg(
        strokes, rapids,
        show_rapids=not args.no_rapids,
        show_markers=not args.no_markers,
        px_per_mm=args.scale,
    )

    with open(output, 'w') as f:
        f.write(svg)

    print(f'Written → {output}')

    if args.show:
        import subprocess, platform
        if platform.system() == 'Darwin':
            subprocess.run(['open', output])
        elif platform.system() == 'Linux':
            subprocess.run(['xdg-open', output])
        else:
            subprocess.run(['start', output], shell=True)


if __name__ == '__main__':
    main()
