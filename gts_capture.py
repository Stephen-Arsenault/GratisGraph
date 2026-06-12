#!/usr/bin/env python3
"""
gts_capture.py — One-command USB capture + summary report for support.

Connect your machine, run this, do whatever you want to capture (press green,
power-cycle, run a job), and paste the resulting report into a GitHub issue:

    sudo python3 gts_capture.py --duration 30 -o report.txt

The report is deliberately INFORMATIVE and MINIMAL: every known packet decodes
to one line, repetitive telemetry collapses to one line per burst with a count
and value range, and raw hex appears ONLY for unrecognised bytes — which are
exactly the interesting part when supporting a new machine model. Total report
size is hard-capped (~8 KB) so it is always cheap to read, paste, and analyse.

All summarisation happens here, on your machine. Decoding reuses the driver's
own parser (via GTSConnection packet taps), so this report shows precisely
what GratisGraph sees — never a second, divergent interpretation.
"""

import argparse
import platform
import sys
import threading
import time

from gts_api import (
    GTSConnection, MODEL_CODE_NAMES, __version__,
)

REPORT_CAP_BYTES   = 8192   # hard cap on the rendered report
UNKNOWN_MAX_ITEMS  = 20     # max unrecognised entries shown
UNKNOWN_MAX_BYTES  = 64     # max hex bytes per unrecognised entry


# ---------------------------------------------------------------------------
# Summariser (pure — no USB; unit-tested in tests/test_gts_capture.py)
# ---------------------------------------------------------------------------

class CaptureSummary:
    """
    Collects decoded packets (from GTSConnection taps) and renders a compact
    plain-text report. Thread-safe add(); deterministic arrival-order output.
    """

    def __init__(self,
                 cap_bytes: int = REPORT_CAP_BYTES,
                 unknown_max_items: int = UNKNOWN_MAX_ITEMS,
                 unknown_max_bytes: int = UNKNOWN_MAX_BYTES):
        self.cap_bytes = cap_bytes
        self.unknown_max_items = unknown_max_items
        self.unknown_max_bytes = unknown_max_bytes
        self._lock = threading.Lock()
        self._t0 = time.monotonic()
        self._items = []          # (t_rel, item) in arrival order
        self.tx_log = []          # (t_rel, description) of bytes we sent
        self.bytes_rx = 0

    # --- collection ---

    def add(self, item):
        with self._lock:
            self._items.append((time.monotonic() - self._t0, item))
            raw = item.get('raw') if isinstance(item, dict) else None
            if isinstance(raw, (bytes, bytearray)):
                self.bytes_rx += len(raw)

    def add_tx(self, description: str):
        with self._lock:
            self.tx_log.append((time.monotonic() - self._t0, description))

    # --- classification ---

    @staticmethod
    def _key(item):
        """Grouping key: consecutive items with the same key collapse."""
        if isinstance(item, dict):
            t = item.get('type')
            if t == 'motor':
                return ('motor', item.get('axis'))
            if t == 'ack':
                return ('ack', item.get('cmd'), item.get('value'))
            if t == 'long':
                return ('long', id(item))          # never collapse identity
            return (t, item.get('text'))
        return ('ascii', str(item))

    @staticmethod
    def _is_unknown(item) -> bool:
        s = str(item)
        return isinstance(item, str) and (
            s.startswith('[BIN]') or s.startswith('[USB ERROR]'))

    # --- rendering helpers ---

    @staticmethod
    def _motor_run_line(axis, t_rel, values):
        lo, hi = min(values), max(values)
        n = len(values)
        if n == 1:
            return f'+{t_rel:7.2f}s  !M{axis}    value {values[0]}'
        if lo == hi:
            return f'+{t_rel:7.2f}s  !M{axis}    ×{n}  value {lo}'
        trend = ''
        if all(b >= a for a, b in zip(values, values[1:])):
            trend = ' monotonic\u2191'
        elif all(b <= a for a, b in zip(values, values[1:])):
            trend = ' monotonic\u2193'
        return (f'+{t_rel:7.2f}s  !M{axis}    \u00d7{n}  '
                f'values {lo}\u2013{hi}{trend}')

    def _timeline_lines(self):
        lines, i = [], 0
        items = self._items
        while i < len(items):
            t_rel, item = items[i]
            key = self._key(item)

            # gather the consecutive run sharing this key
            j = i + 1
            while j < len(items) and self._key(items[j][1]) == key:
                j += 1
            run = [it for _, it in items[i:j]]
            n = len(run)

            if key[0] == 'motor':
                lines.append(self._motor_run_line(
                    key[1], t_rel, [it['value'] for it in run]))
            elif key[0] == 'ack':
                cmd, value = key[1], key[2]
                v = f'value=0x{value:08X}' if value is not None else '(no payload)'
                sfx = f'  \u00d7{n}' if n > 1 else ''
                lines.append(f'+{t_rel:7.2f}s  {cmd.strip():7s}{v}{sfx}')
            elif key[0] == 'long':
                it = run[0]
                lines.append(
                    f'+{t_rel:7.2f}s  !ID     platform={it.get("platform_type")} '
                    f'model=0x{it.get("model_code", 0):04X} '
                    f'boot={it.get("boot_ver")} prog={it.get("prog_ver")}')
            elif self._is_unknown(run[0]):
                pass   # rendered in the unrecognised section, not the timeline
            else:
                text = str(run[0]).strip()
                sfx = f'  \u00d7{n}' if n > 1 else ''
                lines.append(f'+{t_rel:7.2f}s  ascii   {text[:80]!r}{sfx}')
            i = j
        return lines

    def _unknown_lines(self, trailing_buf: bytes = b''):
        out, shown = [], 0
        for t_rel, item in self._items:
            if not self._is_unknown(item):
                continue
            if shown >= self.unknown_max_items:
                out.append(f'(further unrecognised entries omitted)')
                break
            text = str(item)
            if text.startswith('[BIN] '):
                hexpart = text[len('[BIN] '):]
                nbytes = (len(hexpart) + 1) // 3
                hexpart = ' '.join(hexpart.split()[:self.unknown_max_bytes])
                out.append(f'+{t_rel:7.2f}s  UNRECOGNISED ({nbytes}B): {hexpart}')
            else:
                out.append(f'+{t_rel:7.2f}s  {text[:120]}')
            shown += 1
        if trailing_buf:
            out.append(f'unparsed trailing buffer ({len(trailing_buf)}B): '
                       + trailing_buf[:self.unknown_max_bytes].hex(' '))
        return out or ['(none)']

    # --- report ---

    def render(self, machine: dict = None, duration_s: float = None,
               trailing_buf: bytes = b'') -> str:
        m = machine or {}
        head = [
            'GratisGraph capture report',
            '==========================',
            f'time:      {time.strftime("%Y-%m-%dT%H:%M:%S%z")}',
            f'version:   {__version__}',
            f'os:        {platform.system()} {platform.release()} '
            f'{platform.machine()}   python {platform.python_version()}',
            f'device:    VID 0x{m.get("vid", 0):04X}  PID 0x{m.get("pid", 0):04X}',
            f'identity:  platform={m.get("platform", "?")}  '
            f'model=0x{m.get("model_code", 0):04X} '
            f'({MODEL_CODE_NAMES.get(m.get("model_code", 0), "unknown")})  '
            f'boot={m.get("boot_ver") or "?"}  prog={m.get("prog_ver") or "?"}',
            f'profile:   {m.get("profile", "?")}',
            f'duration:  {duration_s:.1f}s   packets: {len(self._items)}   '
            f'bytes_rx: {self.bytes_rx}' if duration_s is not None else
            f'packets: {len(self._items)}   bytes_rx: {self.bytes_rx}',
            '',
            'tx (sent by this tool)',
            '----------------------',
            *([f'+{t:7.2f}s  {d}' for t, d in self.tx_log] or ['(nothing sent)']),
            '',
            'timeline (compressed)',
            '---------------------',
        ]
        timeline = self._timeline_lines() or ['(no packets received)']
        tail = [
            '',
            'unrecognised',
            '------------',
            *self._unknown_lines(trailing_buf),
        ]

        # Hard cap: if over budget, drop the MIDDLE of the timeline.
        def assemble(tl):
            return '\n'.join(head + tl + tail) + '\n'
        report = assemble(timeline)
        if len(report.encode()) > self.cap_bytes:
            keep = max(4, len(timeline) // 4)
            dropped = len(timeline) - 2 * keep
            while dropped > 0:
                tl = (timeline[:keep]
                      + [f'... {dropped} timeline lines elided (size cap) ...']
                      + timeline[-keep:])
                report = assemble(tl)
                if len(report.encode()) <= self.cap_bytes or keep <= 2:
                    break
                keep -= 1
                dropped = len(timeline) - 2 * keep
        return report


# ---------------------------------------------------------------------------
# Capture session (USB)
# ---------------------------------------------------------------------------

def run_capture(duration_s: float, output: str = None, send_file: str = None,
                raw_path: str = None, disable_kext: bool = False) -> str:
    summary = CaptureSummary()
    raw_chunks = []

    conn = GTSConnection(verbose=False, disable_kext=disable_kext)
    conn.add_tap(summary.add)
    if raw_path:
        conn.add_raw_tap(raw_chunks.append)

    print(f'Connecting (waiting up to 60s — power the machine on now)...')
    if not conn.connect(timeout_s=60.0, wait=True):
        raise SystemExit('Machine not found on USB. Check power and cable.')
    summary.add_tx('!ID 0; (identity query, sent during connect)')

    try:
        if send_file:
            with open(send_file, 'rb') as f:
                gts = f.read()
            summary.add_tx(f'GTS job from {send_file} ({len(gts)} bytes)')
            conn.send_raw(gts)

        print(f'Capturing for {duration_s:.0f}s — interact with the machine '
              f'now (green button, jobs, power-cycle). Ctrl-C to stop early.')
        try:
            time.sleep(duration_s)
        except KeyboardInterrupt:
            print('\nStopped early.')

        machine = {
            'vid': conn.vid, 'pid': conn.pid,
            'platform': conn.config_code, 'model_code': conn.model_code,
            'boot_ver': conn.boot_ver, 'prog_ver': conn.prog_ver,
            'profile': conn.profile.name,
        }
        trailing = bytes(conn._rx_buf)
    finally:
        conn.disconnect()

    report = summary.render(machine=machine, duration_s=duration_s,
                            trailing_buf=trailing)

    if raw_path:
        with open(raw_path, 'wb') as f:
            for chunk in raw_chunks:
                f.write(chunk)

    if output:
        with open(output, 'w') as f:
            f.write(report)
        print(f'Report written \u2192 {output}'
              f'  ({len(report.encode())} bytes)')
    else:
        print(report)
    return report


def main():
    ap = argparse.ArgumentParser(
        description='Capture machine USB traffic and produce a compact, '
                    'paste-friendly support report.')
    ap.add_argument('--duration', type=float, default=30.0,
                    help='Seconds to capture (default 30)')
    ap.add_argument('-o', '--output', default=None,
                    help='Write report to file (default: print to stdout)')
    ap.add_argument('--send', default=None, metavar='FILE',
                    help='Send a pre-built .m20 file at capture start')
    ap.add_argument('--disable-kext', action='store_true', default=False)
    ap.add_argument('--raw', nargs='?', const='capture.raw.bin', default=None,
                    help=argparse.SUPPRESS)   # undocumented, by design
    args = ap.parse_args()

    run_capture(duration_s=args.duration, output=args.output,
                send_file=args.send, raw_path=args.raw,
                disable_kext=args.disable_kext)


if __name__ == '__main__':
    main()
