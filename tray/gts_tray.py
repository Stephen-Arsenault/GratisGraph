#!/usr/bin/env python3
"""
gts_tray.py — system-tray companion for the GratisGraph local server.

Starts gts_server.py in-process and presents a tray menu: pairing code,
machine status, the full component-version support snapshot, and the
pairing/lockout controls. Version info is read live from the server's
diagnostics endpoint — never hardcoded — so a support request always
reflects the user's real setup.

  pip install pystray pillow requests
  python3 tray/gts_tray.py

The tray <-> server unlock works because they share one process: "Unlock"
is a direct in-process state reset, proof of physical presence. There is
deliberately no HTTP unlock endpoint.
"""
import os
import sys
import threading
import webbrowser

# Make the repo modules importable when run as tray/gts_tray.py
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import gts_server
from version import diagnostics


# ── Tray logic (headless-testable; no pystray here) ─────────────────────────

class TrayController:
    """All tray behaviour as plain methods, independent of the GUI toolkit so
    it can be unit-tested without a display."""

    def __init__(self, state: 'gts_server.ServerState', token: str, port: int):
        self.state = state
        self.token = token
        self.port = port

    # --- data the menu shows ---

    def pairing_code(self) -> str:
        return self.token

    def machine_status(self) -> str:
        conn = self.state.conn
        if conn is not None and getattr(conn, 'dev', None) is not None:
            model = getattr(conn, 'model', '') or 'connected'
            code = getattr(conn, 'model_code', 0)
            return f'Machine: {model} (0x{code:04X})' if code else f'Machine: {model}'
        return 'Machine: not found'

    def version_lines(self) -> list:
        d = diagnostics()
        lines = [f'{name}: {ver}' for name, ver in d['components'].items()]
        lines.append(f'python: {d["python"]}')
        lines.append(f'os: {d["os"]} {d["machine"]}')
        return lines

    def support_snapshot(self) -> str:
        """One-paste support string."""
        d = diagnostics()
        comp = '  '.join(f'{k}={v}' for k, v in d['components'].items())
        return (f'GratisGraph support snapshot\n'
                f'  {comp}\n'
                f'  python={d["python"]}  os={d["os"]} {d["machine"]}\n'
                f'  {self.machine_status()}')

    def is_locked(self) -> bool:
        return self.state.auth_locked_for() > 0

    def lockout_label(self) -> str:
        secs = int(self.state.auth_locked_for())
        return f'\u26a0 Locked out ({secs // 60}m {secs % 60}s)'

    # --- actions ---

    def regenerate_token(self) -> str:
        import secrets
        self.token = secrets.token_urlsafe(24)
        gts_server.TOKEN_PATH.write_text(self.token + '\n')
        os.chmod(gts_server.TOKEN_PATH, 0o600)
        return self.token

    def unlock(self) -> None:
        """Clear auth lockout + failure counter. In-process; proof of presence."""
        with self.state._auth_lock:
            self.state._auth_locked_until = 0.0
            self.state._auth_failures = 0

    def open_ui(self, url: str = '') -> str:
        target = url or f'http://127.0.0.1:{self.port}'
        webbrowser.open(target)
        return target


# ── pystray rendering (imported lazily so logic stays testable) ─────────────

def _make_icon_image():
    from PIL import Image, ImageDraw
    img = Image.new('RGB', (64, 64), '#1a1a1a')
    d = ImageDraw.Draw(img)
    d.rectangle([16, 16, 48, 48], outline='#1a73e8', width=4)
    d.line([24, 40, 40, 24], fill='#e8710a', width=3)   # a little "engrave" stroke
    return img


def run(port: int = gts_server.DEFAULT_PORT, allowed_origins=None):
    import pystray
    from pystray import MenuItem as Item, Menu

    token = gts_server.load_or_create_token()
    state = gts_server.ServerState()
    httpd = gts_server.make_server(port=port, token=token,
                                   allowed_origins=allowed_origins or [], state=state)
    ctrl = TrayController(state, token, port)

    threading.Thread(target=httpd.serve_forever, daemon=True,
                     name='gts-server').start()

    def versions_submenu():
        return Menu(*[Item(line, None, enabled=False) for line in ctrl.version_lines()])

    def build_menu():
        items = [
            Item(lambda _: f'Pairing code: {ctrl.pairing_code()}',
                 lambda: _copy(ctrl.pairing_code())),
            Item(lambda _: ctrl.machine_status(), None, enabled=False),
            Item('Versions', versions_submenu()),
            Item('Copy support snapshot', lambda: _copy(ctrl.support_snapshot())),
            Menu.SEPARATOR,
            Item('Regenerate pairing code', lambda: ctrl.regenerate_token()),
            Item(lambda _: ctrl.lockout_label(),
                 lambda: ctrl.unlock(),
                 visible=lambda _: ctrl.is_locked()),
            Item('Open web UI', lambda: ctrl.open_ui()),
            Menu.SEPARATOR,
            Item('Quit', lambda icon: (httpd.shutdown(), icon.stop())),
        ]
        return Menu(*items)

    def _copy(text):
        try:
            icon.notify(text[:200], 'GratisGraph')
        except Exception:
            pass

    icon = pystray.Icon('gratisgraph', _make_icon_image(),
                        'GratisGraph', menu=build_menu())
    print(f'GratisGraph tray running. Server http://127.0.0.1:{port}')
    print(f'Pairing code: {token}')
    icon.run()


if __name__ == '__main__':
    run()
