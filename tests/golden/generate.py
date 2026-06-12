#!/usr/bin/env python3
"""
tests/golden/generate.py — Golden corpus generator for cross-implementation
conformance (the oracle for the TypeScript translator port).

Each case = (G-Code input, from_gcode kwargs) -> committed .m20 output.
manifest.json records inputs, kwargs, byte size and SHA-256 of every output.
A conforming implementation, given the same input and parameters, must
produce byte-identical output. The corpus includes the exact two-line
geometry hardware-verified on 2026-06-09 ('two_lines_hw_proven').

Regenerate ONLY after a deliberate format change — never to silence a
failing test:    python3 tests/golden/generate.py
Drift guard:     tests/golden/test_golden.py (run by the normal suite)
"""
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))

from gts_prepare import from_gcode   # noqa: E402

# name -> (input file, from_gcode kwargs). Names are stable identifiers the
# TS suite keys on: add cases freely; never rename or repurpose existing ones.
CASES = {
    'line_single':         ('line_single.gc',         {}),
    'line_single_raw':     ('line_single.gc',         {'optimise': False}),
    'two_lines_hw_proven': ('two_lines_hw_proven.gc', {'optimise': False}),
    'square_20mm':         ('square_20mm.gc',         {}),
    'circle_arcs':         ('circle_arcs.gc',         {}),
    'mixed_arcs':          ('mixed_arcs.gc',          {}),
    'inches':              ('inches.gc',              {'units': 'inch'}),
    'relative_mode':       ('relative_mode.gc',       {}),
    'scattered_optimised': ('scattered_strokes.gc',   {}),
    'scattered_raw':       ('scattered_strokes.gc',   {'optimise': False}),
    'scattered_no_merge':  ('scattered_strokes.gc',   {'merge': False}),
    'flip_y':              ('two_lines_hw_proven.gc', {'flip_y': True,
                                                       'optimise': False}),
    'origin_offset':       ('line_single.gc',         {'origin': (5.0, 5.0),
                                                       'optimise': False}),
    'scaled_2x':           ('line_single.gc',         {'scale': 2.0,
                                                       'optimise': False}),
    'speed_40':            ('line_single.gc',         {'speed': 40,
                                                       'optimise': False}),
}


def build_all() -> dict:
    """Build every case in memory. Returns {name: bytes}."""
    out = {}
    for name, (infile, kwargs) in CASES.items():
        with open(os.path.join(HERE, 'inputs', infile)) as f:
            out[name] = from_gcode(f.read(), **kwargs)
    return out


def main():
    outputs_dir = os.path.join(HERE, 'outputs')
    os.makedirs(outputs_dir, exist_ok=True)
    manifest = {'format': 'GTS',
                'normative_spec': 'docs/GTS_FORMAT.md',
                'cases': {}}
    for name, blob in sorted(build_all().items()):
        with open(os.path.join(outputs_dir, f'{name}.m20'), 'wb') as f:
            f.write(blob)
        infile, kwargs = CASES[name]
        manifest['cases'][name] = {
            'input':  f'inputs/{infile}',
            'kwargs': kwargs,
            'output': f'outputs/{name}.m20',
            'bytes':  len(blob),
            'sha256': hashlib.sha256(blob).hexdigest(),
        }
        print(f'{name:22s} {len(blob):5d}B  {manifest["cases"][name]["sha256"][:16]}…')
    with open(os.path.join(HERE, 'manifest.json'), 'w') as f:
        json.dump(manifest, f, indent=2, sort_keys=True)
        f.write('\n')
    print(f'\n{len(CASES)} cases -> manifest.json')


if __name__ == '__main__':
    main()
