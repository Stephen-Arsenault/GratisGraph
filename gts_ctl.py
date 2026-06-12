#!/usr/bin/env python3
"""gts_ctl.py — Command-line interface for GT Smartstream machines."""
import argparse, sys, os, subprocess

def main():
    ap = argparse.ArgumentParser(prog='gts_ctl', description='GT Smartstream machine control')
    ap.add_argument('input',   nargs='?', help='Input G-Code file')
    ap.add_argument('output',  nargs='?', help='Output .m20 file')
    ap.add_argument('--units',      default='mm', choices=['mm', 'inch'])
    ap.add_argument('--speed',      type=int,   default=None)
    ap.add_argument('--depth',      type=int,   default=None)
    ap.add_argument('--lift',       type=int,   default=None)
    ap.add_argument('--scale',      type=float, default=1.0)
    ap.add_argument('--arc-steps',  type=int,   default=72)
    ap.add_argument('--origin',     default='0,0')
    ap.add_argument('--flip-y',     action='store_true', default=False)
    ap.add_argument('--envelope',   default='100,100')
    ap.add_argument('--send',       action='store_true')
    ap.add_argument('--console',    action='store_true')
    ap.add_argument('--calibrate',  action='store_true')
    ap.add_argument('--test',       action='store_true')
    ap.add_argument('--dry-run',    action='store_true')
    ap.add_argument('--no-home',    action='store_true', default=False)
    ap.add_argument('--step',       type=int, default=0)
    ap.add_argument('--profile',    default='m20',
                    choices=['m20','m40','m10-jewel','is400-iq'],
                    help='Target machine profile (default: m20)')
    ap.add_argument('--vid',        type=lambda x: int(x,0), default=None)
    ap.add_argument('--pid',        type=lambda x: int(x,0), default=None)
    ap.add_argument('--verbose',    action='store_true', default=False)
    ap.add_argument('--disable-kext', action='store_true', default=False)
    args = ap.parse_args()

    ox, oy = (float(v) for v in args.origin.split(','))
    ex, ey = (float(v) for v in args.envelope.split(','))
    origin, envelope = (ox, oy), (ex, ey)

    # Resolve --profile to a MachineProfile (used by translate and send paths)
    from gts_api import (M20_PROFILE, M40_PROFILE, M10_JEWEL_PROFILE,
                         IS400_IQ_PROFILE)
    profile = {'m20': M20_PROFILE, 'm40': M40_PROFILE,
               'm10-jewel': M10_JEWEL_PROFILE,
               'is400-iq': IS400_IQ_PROFILE}[args.profile]

    if args.test:
        here = os.path.dirname(os.path.abspath(__file__))
        result = subprocess.run([sys.executable, '-m', 'pytest', 'tests/', '-v'], cwd=here)
        sys.exit(result.returncode)

    if args.console:
        from console import run_console
        kw = {}
        if args.vid: kw['vid'] = args.vid
        if args.pid: kw['pid'] = args.pid
        run_console(**kw); return

    if args.calibrate:
        from calibrate import run_calibrate
        from gts_api import DEFAULT_SPEED
        kw = dict(step=args.step, scale=args.scale, dry_run=args.dry_run,
                  flip_y=args.flip_y, envelope_mm=envelope)
        if args.speed: kw['speed'] = args.speed
        if args.vid:   kw['vid']   = args.vid
        if args.pid:   kw['pid']   = args.pid
        run_calibrate(**kw); return

    if not args.input:
        ap.print_help(); sys.exit(0)

    from gts_prepare import from_file
    from gts_api import DEFAULT_SPEED, DEFAULT_DEPTH, DEFAULT_LIFT, GTSConnection

    speed = args.speed or DEFAULT_SPEED
    depth = args.depth or DEFAULT_DEPTH
    lift  = args.lift  or DEFAULT_LIFT

    if args.dry_run:
        from gts_translator import translate
        gts = translate(open(args.input, encoding='utf-8').read(), units=args.units, speed=speed,
                        depth=depth, lift=lift, scale=100.0*args.scale,
                        arc_steps=args.arc_steps, origin=origin,
                        flip_y=args.flip_y, envelope_mm=envelope,
                        dry_run=True, wrap=False)
        return

    gts = from_file(args.input, origin=origin, flip_y=args.flip_y, scale=args.scale,
                    speed=speed, depth=depth, lift=lift,
                    arc_steps=args.arc_steps, envelope_mm=envelope,
                    profile=profile)

    if args.output:
        open(args.output, 'wb').write(gts)
        print(f'Written {len(gts)} bytes → {args.output}')

    if args.send:
        from gts_api import GTSConnection
        if args.vid or args.pid:
            # Explicit --vid/--pid wins; profile still set for coordinate handling
            conn = GTSConnection(vid=args.vid or profile.vid,
                                 pid=args.pid or profile.pid,
                                 verbose=args.verbose, disable_kext=args.disable_kext)
            conn.profile = profile
        else:
            conn = GTSConnection(profile=profile, verbose=args.verbose,
                                 disable_kext=args.disable_kext)
        try:
            conn.connect()
            conn.send_gts(gts, home_first=not args.no_home)
        finally:
            conn.disconnect()
        return

    if not args.output:
        print('No action specified. Use --send or provide output path.')

if __name__ == '__main__':
    main()
