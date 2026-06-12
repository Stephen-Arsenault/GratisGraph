#!/usr/bin/env python3
"""
generate_fixtures.py — Cross-language conformance fixtures for the TS GTS engine.

Python (gts_api) is the source of truth. This emits, for each strokes-level
case, the input, the expected bytes (hex), and a SHA-256. The TS test suite
hashes its OWN buildGts output and asserts equality against this manifest, so
neither implementation can silently diverge.

  generate    python3 generate_fixtures.py            # (re)write the manifest
  drift guard python3 generate_fixtures.py --check    # fail if manifest is stale

The --check mode is the closed loop: it rebuilds in memory and compares to the
committed manifest. A change to Python build_gts that isn't reflected here
fails CI, forcing a regen — which then fails the TS byte-compare until TS is
updated too. Never regenerate just to silence a failure; a format change goes
through docs/GTS_FORMAT.md first.
"""
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(HERE))))  # repo root

from gts_api import build_gts   # noqa: E402

# Strokes-level cases. Names are stable identifiers the TS suite keys on.
CASES = {
    'single_line':     ([(1000, 2000, [(2000, 0)])], {}),
    'two_lines':       ([(1000, 2000, [(1000, 0)]), (3000, 4000, [(1000, 0)])], {}),
    'square':          ([(1000, 1000, [(2000, 0), (0, 2000), (-2000, 0), (0, -2000)])], {}),
    'speed_40':        ([(1000, 2000, [(2000, 0)])], {'speed': 40}),
    'depth_pz_100':    ([(1000, 2000, [(2000, 0)])], {'pz': 100}),
    'negative_deltas': ([(5000, 5000, [(-1000, -1000), (500, -500)])], {}),
}

MANIFEST = os.path.join(HERE, 'build_gts_fixtures.json')


def build_manifest() -> dict:
    out = {'note': 'Python gts_api.build_gts is the source of truth. '
                   'TS buildGts must match expected_hex and sha256 exactly.',
           'cases': {}}
    for name, (strokes, opts) in CASES.items():
        blob = build_gts(strokes, **opts)
        out['cases'][name] = {
            'strokes': [[sx, sy, moves] for sx, sy, moves in strokes],
            'opts': opts,
            'bytes': len(blob),
            'expected_hex': blob.hex(),
            'sha256': hashlib.sha256(blob).hexdigest(),
        }
    return out


def main():
    manifest = build_manifest()
    if '--check' in sys.argv:
        if not os.path.exists(MANIFEST):
            print('FAIL: manifest missing — run without --check to generate.')
            sys.exit(1)
        committed = json.load(open(MANIFEST))
        # Normalise both through JSON so tuple->list round-tripping matches.
        live = json.loads(json.dumps(manifest['cases']))
        if committed.get('cases') != live:
            print('FAIL: committed fixtures are STALE vs live Python build_gts.\n'
                  'Python output changed. If deliberate: update docs/GTS_FORMAT.md, '
                  'regenerate, and update the TS engine to match.')
            # show which cases drifted
            for name in manifest['cases']:
                a = committed.get('cases', {}).get(name, {}).get('sha256')
                b = live[name]['sha256']
                if a != b:
                    print(f'  drift: {name}  committed={a}  live={b}')
            sys.exit(1)
        print(f'OK: {len(manifest["cases"])} fixtures match live Python build_gts.')
        return
    json.dump(manifest, open(MANIFEST, 'w'), indent=2)
    print(f'wrote {len(manifest["cases"])} fixtures + sha256 -> {MANIFEST}')


if __name__ == '__main__':
    main()
