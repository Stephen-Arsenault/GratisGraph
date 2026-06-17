#!/usr/bin/env python3
"""
gts_tray.py — system-tray companion for the GratisGraph local server.

Starts gts_server.py in-process and presents a tray menu: pairing code,
machine status, the full component-version support snapshot, and the
pairing/lockout controls. Version info is read live from the server's
diagnostics endpoint — never hardcoded — so a support request always
reflects the user's real setup.

  pip install pystray pillow
  python3 tray/gts_tray.py

The tray <-> server unlock works because they share one process: "Unlock"
is a direct in-process state reset, proof of physical presence. There is
deliberately no HTTP unlock endpoint.
"""
import os
import subprocess
import sys
import threading
import time
import webbrowser

# Make the repo modules importable when run as tray/gts_tray.py
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import gts_server
from version import diagnostics

GRATISGRAPH_URL = 'https://gratisgraph.com'
KOFI_URL = 'https://ko-fi.com/gratisgraph'
# Origins the local bridge trusts for cross-origin calls from the hosted web
# app: the custom domain + the Cloudflare Pages preview host.
DEFAULT_ALLOWED_ORIGINS = [GRATISGRAPH_URL, 'https://gratisgraph.pages.dev']


def _set_clipboard(text: str) -> bool:
    """Copy text to the OS clipboard. macOS pbcopy / Windows clip; best-effort."""
    try:
        if sys.platform == 'darwin':
            subprocess.run(['pbcopy'], input=text.encode('utf-8'), check=True)
        elif sys.platform.startswith('win'):
            subprocess.run(['clip'], input=text.encode('utf-16-le'), check=True)
        else:
            return False
        return True
    except Exception:
        return False


# ── bundle helpers (work both in dev and inside the packaged .app) ────────

def _resource(name: str) -> str:
    """Locate a bundled resource across dev (tray/resources/), the macOS .app
    (Contents/Resources/), and a PyInstaller onefile (sys._MEIPASS)."""
    if getattr(sys, 'frozen', False):
        meipass = getattr(sys, '_MEIPASS', None)
        base = meipass if meipass else os.path.join(
            os.path.dirname(sys.executable), '..', 'Resources')
    else:
        base = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'resources')
    return os.path.normpath(os.path.join(base, name))


def _configure_bundled_libusb() -> None:
    """Make pyusb load the libusb shipped inside the frozen app instead of a
    system copy, so the app is self-contained. Handles the macOS .app (a dylib
    in Contents/Frameworks) and a PyInstaller onefile (libusb-1.0.dll in
    sys._MEIPASS). No-op when running from source."""
    if not getattr(sys, 'frozen', False):
        return
    meipass = getattr(sys, '_MEIPASS', None)
    if meipass:                                    # PyInstaller (Windows / Linux)
        lib = os.path.join(meipass, 'libusb-1.0.dll')
    else:                                          # py2app (.app, macOS)
        lib = os.path.normpath(os.path.join(
            os.path.dirname(sys.executable), '..', 'Frameworks', 'libusb-1.0.0.dylib'))
    if not os.path.exists(lib):
        return
    import ctypes.util as _cu
    _orig = _cu.find_library
    def _patched(name):
        if name in ('usb-1.0', 'libusb-1.0', 'usb'):
            return lib
        return _orig(name)
    _cu.find_library = _patched


def _enable_template_icon() -> None:
    """Make pystray's macOS status-bar image a *template* image so the system
    tints it automatically for light/dark menu bars (matching the favicon's
    prefers-color-scheme behaviour)."""
    try:
        from pystray import _darwin
    except Exception:
        return
    if getattr(_darwin.Icon, '_gg_template_patched', False):
        return
    _orig = _darwin.Icon._assert_image
    def _assert_image(self):
        _orig(self)
        if self._icon_image is not None:
            self._icon_image.setTemplate_(True)
    _darwin.Icon._assert_image = _assert_image
    _darwin.Icon._gg_template_patched = True


def _enable_left_click_menu() -> None:
    """On Windows, open the context menu on a LEFT click too. pystray's win32
    backend shows the menu only on right-click (WM_RBUTTONUP); this mirrors that
    same popup logic onto WM_LBUTTONUP so either button works. No-op off
    Windows (macOS/Linux already open the menu on a normal click)."""
    if not sys.platform.startswith('win'):
        return
    try:
        import ctypes
        from ctypes import wintypes
        from pystray import _win32
        win32 = _win32.win32
    except Exception:
        return
    if getattr(_win32.Icon, '_gg_leftclick_patched', False):
        return

    def _on_notify(self, wparam, lparam):
        if self._menu_handle and lparam in (win32.WM_LBUTTONUP, win32.WM_RBUTTONUP):
            # TrackPopupMenuEx misbehaves unless our systray window is foreground
            win32.SetForegroundWindow(self._hwnd)
            point = wintypes.POINT()
            win32.GetCursorPos(ctypes.byref(point))
            hmenu, descriptors = self._menu_handle
            index = win32.TrackPopupMenuEx(
                hmenu,
                win32.TPM_RIGHTALIGN | win32.TPM_BOTTOMALIGN | win32.TPM_RETURNCMD,
                point.x, point.y, self._menu_hwnd, None)
            if index > 0:
                descriptors[index - 1](self)
        elif lparam == win32.WM_LBUTTONUP:
            self()   # no menu registered — fall back to the default action

    _win32.Icon._on_notify = _on_notify
    _win32.Icon._gg_leftclick_patched = True


# Start at login via SMAppService (macOS 13+, packaged .app only). Degrades to
# a no-op when run from source, on macOS < 13, or if the framework is missing.
_LOGIN_ENABLED = 1   # SMAppServiceStatusEnabled


def _login_item_service():
    """The SMAppService for this app, or None when unavailable."""
    if sys.platform != 'darwin' or not getattr(sys, 'frozen', False):
        return None
    try:
        from ServiceManagement import SMAppService
        return SMAppService.mainAppService()
    except Exception:
        return None


def _login_item_status():
    svc = _login_item_service()
    if svc is None:
        return None
    try:
        return int(svc.status())
    except Exception:
        return None


def _login_item_set(enable):
    """Register/unregister the app as a login item. True on success."""
    svc = _login_item_service()
    if svc is None:
        return False
    try:
        ok, _err = (svc.registerAndReturnError_(None) if enable
                    else svc.unregisterAndReturnError_(None))
        return bool(ok)
    except Exception:
        return False


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

    # --- privileged USB helper (scoped sudoers; see usb_helper.py) ---

    def usb_helper_installed(self) -> bool:
        try:
            import usb_helper
            return usb_helper.is_installed()
        except Exception:
            return False

    def usb_helper_status(self) -> str:
        if sys.platform != 'darwin':
            return 'USB helper: n/a'
        return 'USB helper: installed' if self.usb_helper_installed() else 'USB helper: not installed'

    def install_usb_helper(self) -> bool:
        import usb_helper
        return usb_helper.install()

    # --- start at login (SMAppService; packaged .app, macOS 13+) ---

    def login_item_available(self) -> bool:
        return _login_item_status() is not None

    def login_item_enabled(self) -> bool:
        return _login_item_status() == _LOGIN_ENABLED

    def set_login_item(self, enable: bool) -> bool:
        return _login_item_set(enable)


# ── pystray rendering (imported lazily so logic stays testable) ─────────────

def _make_icon_image():
    """The GratisGraph 'G' for the menu bar (rendered from the favicon by
    make_icon.py). Falls back to a drawn glyph if the asset is missing."""
    from PIL import Image
    try:
        return Image.open(_resource('menubar_G.png')).convert('RGBA')
    except Exception:
        from PIL import ImageDraw
        img = Image.new('RGBA', (64, 64), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.ellipse([8, 8, 56, 56], outline=(0, 0, 0, 255), width=8)
        d.rectangle([32, 30, 58, 36], fill=(0, 0, 0, 255))
        return img


def run(port: int = gts_server.DEFAULT_PORT, allowed_origins=None):
    import pystray
    from pystray import MenuItem as Item, Menu

    _configure_bundled_libusb()
    if sys.platform == 'darwin':
        _enable_template_icon()
    elif sys.platform.startswith('win'):
        _enable_left_click_menu()

    token = gts_server.load_or_create_token()
    state = gts_server.ServerState()
    origins = DEFAULT_ALLOWED_ORIGINS if allowed_origins is None else allowed_origins
    httpd = gts_server.make_server(port=port, token=token,
                                   allowed_origins=origins, state=state)
    ctrl = TrayController(state, token, port)

    threading.Thread(target=httpd.serve_forever, daemon=True,
                     name='gts-server').start()

    def build_menu():
        items = [
            Item('Copy Pairing Code', lambda: _copy(ctrl.pairing_code())),
            Item(lambda _: ctrl.machine_status(), None, enabled=False),
            Item(lambda _: ctrl.usb_helper_status(), None, enabled=False,
                 visible=lambda _: sys.platform == 'darwin'),
            Item('Copy Support Info', lambda: _copy(ctrl.support_snapshot())),
            Menu.SEPARATOR,
            Item('Install USB helper…', lambda: _install_helper(),
                 visible=lambda _: sys.platform == 'darwin' and not ctrl.usb_helper_installed()),
            Item('Regenerate pairing code', lambda: ctrl.regenerate_token()),
            Item(lambda _: ctrl.lockout_label(),
                 lambda: ctrl.unlock(),
                 visible=lambda _: ctrl.is_locked()),
            Item('Open GratisGraph', lambda: ctrl.open_ui(GRATISGRAPH_URL)),
            Item('Donate…', lambda: webbrowser.open(KOFI_URL)),
            Item('Start at login', lambda: _toggle_login(),
                 checked=lambda _: ctrl.login_item_enabled(),
                 visible=lambda _: ctrl.login_item_available()),
            Menu.SEPARATOR,
            Item('Quit', lambda icon: (httpd.shutdown(), icon.stop())),
        ]
        return Menu(*items)

    def _copy(text):
        _set_clipboard(text)   # silent: a clipboard copy is self-evident on paste

    def _toggle_login():
        ctrl.set_login_item(not ctrl.login_item_enabled())
        try:
            icon.update_menu()
        except Exception:
            pass

    def _install_helper():
        try:
            ctrl.install_usb_helper()
        except Exception:
            pass
        try:
            icon.update_menu()   # the 'USB helper: ...' status line reflects the result
        except Exception:
            pass

    icon = pystray.Icon('gratisgraph', _make_icon_image(),
                        'GratisGraph', menu=build_menu())
    print(f'GratisGraph tray running. Server http://127.0.0.1:{port}')
    print(f'Pairing code: {token}')
    print(f'Allowed origins: {origins}')

    def _autoconnect_loop():
        # The tray owns the machine connection: connect on launch and keep it
        # live, so the status line reflects the real machine without waiting
        # for a browser to hit /machine first.
        while True:
            try:
                c = ctrl.state.conn
                if c is None or getattr(c, 'dev', None) is None:
                    ctrl.state.ensure_connected()
                    try:
                        icon.update_menu()
                    except Exception:
                        pass
            except Exception:
                pass
            time.sleep(4)

    threading.Thread(target=_autoconnect_loop, daemon=True,
                     name='gts-autoconnect').start()
    icon.run()


if __name__ == '__main__':
    if '--selftest' in sys.argv:
        # Verify the bundle is self-contained: libusb loads from Frameworks, the
        # menu-bar asset is present, and the USB helper module imports + reports.
        _configure_bundled_libusb()
        import usb.backend.libusb1 as _l
        be = _l.get_backend()
        png = _resource('menubar_G.png')
        ok_png = os.path.exists(png)
        try:
            import usb_helper
            helper = f'OK ({"installed" if usb_helper.is_installed() else "not installed"})'
            ok_helper = True
        except Exception as e:
            helper, ok_helper = f'IMPORT FAILED: {e}', False
        try:
            _make_icon_image()
            import pystray  # noqa: F401 — verify GUI deps survived py2app excludes
            gui, ok_gui = 'OK', True
        except Exception as e:
            gui, ok_gui = f'FAILED: {e}', False
        try:
            import hmac, secrets, uuid    # the bits gts_server needs without OpenSSL
            hmac.compare_digest('x', 'x'); secrets.token_urlsafe(8); uuid.uuid4()
            crypto, ok_crypto = 'OK', True
        except Exception as e:
            crypto, ok_crypto = f'FAILED: {e}', False
        if sys.platform == 'darwin':
            try:
                from ServiceManagement import SMAppService  # noqa: F401 — login-item toggle
                login, ok_login = 'OK', True
            except Exception as e:
                login, ok_login = f'FAILED: {e}', False
        else:
            login, ok_login = 'n/a', True
        print('libusb backend:', 'OK' if be else 'FAILED')
        print('menubar icon  :', 'OK' if ok_png else 'MISSING', png)
        print('usb_helper    :', helper)
        print('gui PIL+pystray:', gui)
        print('crypto (no OpenSSL):', crypto)
        print('login item (SMAppService):', login)
        sys.exit(0 if (be and ok_png and ok_helper and ok_gui and ok_crypto and ok_login) else 1)
    run()
