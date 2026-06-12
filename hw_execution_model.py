#!/usr/bin/env python3
"""
Hardware test: connected-workflow execution model.
Sends home + one 20mm line at (10,20). DO NOT touch the green button unless
the machine clearly stops and waits. Settles the queue-vs-execute question.
"""
from gts_api import build_gts, GTSConnection

gts = build_gts([(1000, 2000, [(2000, 0)])])   # single stroke: safe regardless
                                                # of header semantics (from home)
conn = GTSConnection()
if not conn.connect(timeout_s=90.0, wait=True):
    raise SystemExit('Machine not found. Check power and cable.')
try:
    conn.send_gts(gts)        # home_first=True (the decided default)
finally:
    conn.disconnect()
