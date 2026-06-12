#!/usr/bin/env python3
"""
Hardware test: are stroke-header coordinates absolute or relative?
Two 10mm horizontal pen lines. Stroke 1 starts at (10,20)mm, stroke 2 at (30,40)mm.
Settles Open Question #3. Safe: worst case lands at (60,80), inside envelope.
"""
from gts_api import build_gts, GTSConnection

gts = build_gts([
    (1000, 2000, [(1000, 0)]),   # line A: (10,20) -> (20,20)
    (3000, 4000, [(1000, 0)]),   # line B: (30,40) -> (40,40)
])

conn = GTSConnection()
if not conn.connect(timeout_s=90.0, wait=True):
    raise SystemExit('Machine never appeared on USB. Check power and cable.')
try:
    conn.send_gts(gts, home_first=True)
finally:
    conn.disconnect()
