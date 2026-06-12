#!/usr/bin/env python3
"""
Hardware test: RELATIVE-interpretation multi-stroke emission (the proposed fix).

Same two 10mm lines as hw_header_semantics.py — A at (10,20), B at (30,40) —
but every stroke header is a DELTA from the previous stroke's end, with NO
separate inter-stroke rapid (matching the single-stroke Gravostyle capture
structure, where the header IS the rapid-to-start).

If headers are relative: BOTH lines land at their commanded positions.
If headers are absolute: line B lands at (10,40) — left of commanded.
Either way, one run discriminates.
"""
from gts_api import (
    GTSConnection, _gts_header, _gts_setup, _seg, _cmd, DEFAULT_ZA,
)

def stroke_rel(hdr_dx, hdr_dy, segs):
    body  = _seg(hdr_dx, hdr_dy)            # header = delta rapid to start
    body += _cmd(b'ZA', DEFAULT_ZA)
    body += _cmd(b'PD', 0)
    for dx, dy in segs:
        body += _seg(dx, dy)
    body += _cmd(b'PU', 0) + _cmd(b'PU', 0) + _cmd(b'PU', 1)
    return body

# Line A: home(0,0) -> delta (1000,2000) to start, draw +1000,0 -> end (2000,2000)
# Line B: delta from A end to B start (3000,4000): (1000,2000); draw +1000,0
#         -> end (4000,4000); return-home delta (-4000,-4000)
job  = _gts_header() + _gts_setup()
job += stroke_rel(1000, 2000, [(1000, 0)])
job += stroke_rel(1000, 2000, [(1000, 0)])
job += _seg(-4000, -4000)                   # return to home (delta)
job += _cmd(b'JF', 0)

conn = GTSConnection()
if not conn.connect(timeout_s=90.0, wait=True):
    raise SystemExit('Machine not found.')
try:
    conn.send_gts(job, home_first=True)
finally:
    conn.disconnect()
