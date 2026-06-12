#!/usr/bin/env python3
"""
console.py — Interactive REPL for GT Smartstream machines.

Commands:
    id              Query machine identity
    jog X Y         Home, then rapid pen-up to (X, Y)mm from home
    home            Send home GTS
    send FILE       Translate and send a G-Code file
    raw CMD         Send raw ASCII bytes to machine
    listen [N]      Print decoded packets for N seconds (default 10)
    dump [N]        Print raw packet hex for N seconds (default 5)
    up              Tool up
    abort           Emergency stop
    help            Show commands
    quit            Disconnect and exit
"""

import sys
import time
from typing import Optional

from gts_api import (
    GTSConnection, M20_VID, M20_PID,
    build_home_gts, MACHINE_PLATFORM_TYPES, MODEL_CODE_NAMES,
)
from gts_prepare import from_file


def run_console(vid: int = M20_VID, pid: int = M20_PID,
                disable_kext: bool = False,
                skip_home: bool = False):
    """Open an interactive terminal for direct machine control."""

    print('GT Smartstream Interactive Console')
    conn = GTSConnection(vid=vid, pid=pid, verbose=True,
                         disable_kext=disable_kext)
    skip_home_flag = skip_home

    try:
        connected = conn.connect(timeout_s=60.0)
        if not connected:
            print('Timed out waiting for machine. Check USB cable and power.')
            return
    except KeyboardInterrupt:
        print('\nCancelled.')
        return
    except RuntimeError as e:
        print(f'Error: {e}')
        return

    print(f'\nConnected. Model: {conn.model or "M20"}')
    print('Type "help" for commands.\n')

    while True:
        try:
            line = input('gts> ').strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not line:
            continue

        parts = line.split()
        cmd   = parts[0].lower()

        if cmd in ('quit', 'exit', 'q'):
            break

        elif cmd == 'help':
            print('  jog X Y       home            send FILE')
            print('  id            up              abort')
            print('  raw TEXT      listen [N]      dump [N]')
            print('  home-on       home-off        quit')

        elif cmd == 'id':
            model = conn.identify()
            print(f'  Model: {model or "(no response)"}')

        elif cmd == 'up':
            conn.tool_up()

        elif cmd == 'abort':
            conn.abort()

        elif cmd == 'home':
            gts = build_home_gts()
            conn.send_raw(gts)
            print(f'  Home GTS sent ({len(gts)} bytes).')

        elif cmd == 'home-on':
            skip_home_flag = False
            print('  Home prefix enabled — two green presses per job.')

        elif cmd == 'home-off':
            skip_home_flag = True
            print('  Home prefix disabled — one green press per job.')

        elif cmd == 'jog' and len(parts) == 3:
            try:
                jx, jy = float(parts[1]), float(parts[2])
                conn.jog(jx, jy)
            except ValueError:
                print('  Usage: jog X Y  (mm)')

        elif cmd == 'send' and len(parts) == 2:
            try:
                gts = from_file(parts[1])
                conn.send_gts(gts, home_first=not skip_home_flag)
            except FileNotFoundError:
                print(f'  File not found: {parts[1]}')
            except Exception as e:
                print(f'  Error: {e}')

        elif cmd == 'raw' and len(parts) >= 2:
            payload = ' '.join(parts[1:])
            conn.send_ascii(payload)
            deadline = time.monotonic() + 2.0
            count = 0
            while time.monotonic() < deadline:
                r = conn.recv(timeout_ms=100)
                if r is not None:
                    count += 1
                    if isinstance(r, dict):
                        print(f'  [{r["type"]:5s}] {r["text"]}')
                    else:
                        print(f'  [ascii] {repr(r)}')
            if count == 0:
                print('  (no response in 2s)')

        elif cmd == 'listen':
            duration = float(parts[1]) if len(parts) == 2 else 10.0
            print(f'  Listening {duration:.0f}s (Ctrl-C to stop)...')
            deadline = time.monotonic() + duration
            count = 0
            try:
                while time.monotonic() < deadline:
                    r = conn.recv(timeout_ms=200)
                    if r is not None:
                        count += 1
                        if isinstance(r, dict):
                            print(f'  [{r["type"]:5s}] {r["text"]}')
                        else:
                            print(f'  [ascii] {repr(r)}')
            except KeyboardInterrupt:
                pass
            print(f'  {count} packets received.')

        elif cmd == 'dump':
            duration = float(parts[1]) if len(parts) == 2 else 5.0
            print(f'  Dumping {duration:.0f}s (Ctrl-C to stop)...')
            deadline = time.monotonic() + duration
            count = 0
            try:
                while time.monotonic() < deadline:
                    r = conn.recv(timeout_ms=200)
                    if r is not None:
                        count += 1
                        if isinstance(r, dict):
                            raw = r.get('raw', b'')
                            hex_str = ' '.join(f'{b:02X}' for b in raw)
                            print(f'  [{r["type"]:5s}] {r["text"]}')
                            if hex_str:
                                print(f'           raw: {hex_str}')
                        else:
                            print(f'  [ascii] {repr(r)}')
            except KeyboardInterrupt:
                pass
            print(f'  {count} packets received.')

        else:
            print(f'  Unknown command: {repr(line)}. Type "help".')

    conn.disconnect()
