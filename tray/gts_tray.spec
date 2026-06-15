# -*- mode: python ; coding: utf-8 -*-
"""
gts_tray.spec — PyInstaller build for the Windows GratisGraph tray.

The Windows analog of tray/setup_app.py (which is macOS/py2app only). Produces a
single self-contained GratisGraph.exe.

    pip install pyinstaller pystray pillow pyusb libusb-package
    pyinstaller tray/gts_tray.spec        # run from the repo root

NOTES / TODO — these need a Windows machine to validate (untested from macOS):
  * USB driver claim (the real work item): the M20 enumerates as a USB Printer
    bound to usbprint.sys. Raw libusb needs that VID:PID (0xFFFD:0x0001) rebound
    to WinUSB via Zadig or libusbK. Packaging does NOT solve this — it's the
    Windows analog of the macOS kext detach, and there is no Windows usb_helper
    yet.
  * libusb DLL: taken from the `libusb-package` wheel and bundled into the
    onefile; pyusb finds it via sys._MEIPASS (gts_tray._configure_bundled_libusb).
  * If the exe fails to start, the ssl/_hashlib excludes below are the first
    thing to remove (hmac relies on Python 3.14's HACL* builtins; verified on
    macOS, not yet on Windows). Flip `console=True` to see startup errors.
"""
import os

from PyInstaller.utils.hooks import collect_submodules

SPEC_DIR = SPECPATH                       # tray/  (injected by PyInstaller)
ROOT = os.path.dirname(SPEC_DIR)          # repo root
RES = os.path.join(SPEC_DIR, 'resources')

# --- bundle the libusb-1.0 DLL from the libusb-package wheel ----------------
binaries = []
try:
    import libusb_package
    _base = os.path.dirname(libusb_package.__file__)
    _dll = None
    for _r, _d, _files in os.walk(_base):
        if 'libusb-1.0.dll' in _files:
            _dll = os.path.join(_r, 'libusb-1.0.dll')
            break
    if _dll:
        binaries.append((_dll, '.'))      # -> _MEIPASS/libusb-1.0.dll
        print(f'[spec] bundling libusb DLL: {_dll}')
    else:
        print('[spec] WARNING: libusb-1.0.dll not found inside libusb_package')
except Exception as e:
    print(f'[spec] WARNING: libusb-package not available ({e}); '
          f'the exe will need a system libusb-1.0.dll')

# --- data files (the tray icon pystray loads at runtime) --------------------
datas = []
_png = os.path.join(RES, 'menubar_G.png')
if os.path.exists(_png):
    datas.append((_png, '.'))             # -> _MEIPASS/menubar_G.png

ICON = os.path.join(RES, 'GratisGraph.ico')

# --- modules ----------------------------------------------------------------
hiddenimports = [
    'gts_server', 'gts_api', 'version',
    'pystray._win32',                     # pystray loads its backend by string
] + collect_submodules('usb.backend')     # pyusb backends are imported dynamically

# Mirrors setup_app.py's excludes: nothing in the tray's graph uses these, and
# dropping them trims the onefile. (PyInstaller's modulegraph is generally less
# greedy than py2app's, so the win is smaller, but the intent is identical.)
excludes = [
    'numpy', 'cryptography', 'cffi', 'pygments', 'setuptools', 'pkg_resources',
    'tkinter', 'test', 'unittest', 'doctest', 'pydoc', 'distutils', 'lib2to3',
    'gts_translator', 'gts_optimise', 'gts_prepare', 'gts_preview',
    'ssl', '_ssl', '_hashlib',            # no TLS; hmac uses HACL* builtins
    'bz2', 'lzma', 'sqlite3', 'decimal', 'xmlrpc',
    'PIL.ImageQt', 'PIL.ImageTk',         # GUI-toolkit bridges we never use
]

a = Analysis(
    ['gts_tray.py'],
    pathex=[SPEC_DIR, ROOT],              # tray/ for gts_tray+usb_helper, root for gts_server
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='GratisGraph',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,                            # UPX trips antivirus heuristics; skip it
    runtime_tmpdir=None,
    console=False,                        # tray app: no console window (set True to debug)
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,                     # build host arch (x64 on windows-latest)
    codesign_identity=None,
    entitlements_file=None,
    icon=ICON if os.path.exists(ICON) else None,
)
