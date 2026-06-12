#!/usr/bin/env python3
"""calibrate.py — Pen-on-paper calibration suite for GT Smartstream machines."""
import argparse, sys
from typing import Tuple
from gts_api import GTSConnection, M20_VID, M20_PID, DEFAULT_SPEED, WORK_ENVELOPE_MM
from gts_prepare import from_gcode


def _sq(cx, cy, s):
    h=s/2
    return f"G21\nG90\nG0 Z1\nG0 X{cx-h:.2f} Y{cy-h:.2f}\nG1 Z-1 F600\nG1 X{cx+h:.2f} Y{cy-h:.2f}\nG1 X{cx+h:.2f} Y{cy+h:.2f}\nG1 X{cx-h:.2f} Y{cy+h:.2f}\nG1 X{cx-h:.2f} Y{cy-h:.2f}\nG0 Z1\nM30"

def _lx(cx, cy, l):
    h=l/2
    return f"G21\nG90\nG0 Z1\nG0 X{cx-h:.2f} Y{cy:.2f}\nG1 Z-1 F600\nG1 X{cx+h:.2f} Y{cy:.2f}\nG0 Z1\nM30"

def _ly(cx, cy, l):
    h=l/2
    return f"G21\nG90\nG0 Z1\nG0 X{cx:.2f} Y{cy-h:.2f}\nG1 Z-1 F600\nG1 X{cx:.2f} Y{cy+h:.2f}\nG0 Z1\nM30"

def _circ(cx, cy, r):
    return f"G21\nG90\nG0 Z1\nG0 X{cx+r:.2f} Y{cy:.2f}\nG1 Z-1 F600\nG2 X{cx+r:.2f} Y{cy:.2f} I{-r:.2f} J0\nG0 Z1\nM30"

def _border(margin, envelope):
    x1,y1=margin,margin; x2,y2=envelope[0]-margin,envelope[1]-margin
    return f"G21\nG90\nG0 Z1\nG0 X{x1} Y{y1}\nG1 Z-1 F600\nG1 X{x2} Y{y1}\nG1 X{x2} Y{y2}\nG1 X{x1} Y{y2}\nG1 X{x1} Y{y1}\nG0 Z1\nM30"


def build_steps(scale=1.0, envelope=WORK_ENVELOPE_MM):
    cx, cy = 50.0, 50.0
    return [
        {'name': f'{10*scale:.0f}×{10*scale:.0f}mm square',
         'description': f'Scale + squareness check. Should measure {10*scale:.1f}×{10*scale:.1f}mm.',
         'gcode': _sq(cx, cy, 10*scale)},
        {'name': f'{30*scale:.0f}mm horizontal line',
         'description': f'X axis scale. Should measure {30*scale:.1f}mm.',
         'gcode': _lx(cx, cy, 30*scale)},
        {'name': f'{30*scale:.0f}mm vertical line',
         'description': f'Y axis scale. Should measure {30*scale:.1f}mm.',
         'gcode': _ly(cx, cy, 30*scale)},
        {'name': f'Circle r={15*scale:.0f}mm',
         'description': f'Arc interpolation. A perfect circle = both axes match.',
         'gcode': _circ(cx, cy, 15*scale)},
        {'name': f'Border',
         'description': f'Work envelope extents. Drawn 1mm inside boundary.',
         'gcode': _border(1*scale, envelope)},
    ]


def run_calibrate(step=0, scale=1.0, dry_run=False, speed=DEFAULT_SPEED,
                  envelope_mm=WORK_ENVELOPE_MM, vid=M20_VID, pid=M20_PID,
                  flip_y=False):
    steps = build_steps(scale=scale, envelope=envelope_mm)
    if step != 0:
        if not 1 <= step <= len(steps): print(f'Invalid step {step}.'); return
        run_steps, nums = [steps[step-1]], [step]
    else:
        run_steps, nums = steps, list(range(1, len(steps)+1))

    print(f'GT Smartstream Calibration — {len(run_steps)} step(s)  scale={scale:.2f}')
    print(f'Envelope: {envelope_mm[0]:.0f}×{envelope_mm[1]:.0f}mm  Centre: (50,50)mm  flip_y={flip_y}')

    if dry_run:
        for n, s in zip(nums, run_steps):
            print(f'Step {n}: {s["name"]}  — {s["description"]}')
        return

    conn = GTSConnection(vid=vid, pid=pid, verbose=True)
    try:
        if not conn.connect(timeout_s=60.0): print('Timed out.'); return
    except KeyboardInterrupt: print('\nCancelled.'); return

    print(f'Connected. Model: {conn.model or "unknown"}\n')
    for i, (n, s) in enumerate(zip(nums, run_steps)):
        print(f'─── Step {n}/{len(steps)}: {s["name"]} ───')
        print(f'    {s["description"]}\n')
        if i > 0:
            try: input('    Press Enter for next step (Ctrl-C to stop)... ')
            except KeyboardInterrupt: print('\nStopped.'); break
        try:
            gts = from_gcode(s['gcode'], speed=speed, flip_y=flip_y, envelope_mm=envelope_mm)
            conn.send_gts(gts, home_first=True)
        except ValueError as e: print(f'    [ERROR] {e}')
        except KeyboardInterrupt: print('\nAborted.'); conn.abort(); break
    conn.disconnect()


def main():
    ap = argparse.ArgumentParser(description='GT Smartstream calibration')
    ap.add_argument('--step',     type=int,   default=0)
    ap.add_argument('--scale',    type=float, default=1.0)
    ap.add_argument('--dry-run',  action='store_true')
    ap.add_argument('--speed',    type=int,   default=DEFAULT_SPEED)
    ap.add_argument('--flip-y',   action='store_true', default=False)
    ap.add_argument('--envelope', default='100,100')
    args = ap.parse_args()
    ex, ey = (float(v) for v in args.envelope.split(','))
    run_calibrate(step=args.step, scale=args.scale, dry_run=args.dry_run,
                  speed=args.speed, envelope_mm=(ex,ey), flip_y=args.flip_y)

if __name__ == '__main__':
    main()
