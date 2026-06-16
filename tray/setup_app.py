"""
setup_app.py — py2app packaging for the GratisGraph menu-bar app.

Builds a native macOS .app that lives ONLY in the menu bar (LSUIElement),
wrapping gts_tray.py / gts_server.py. No Dock icon, no window.

Build:
    cd tray
    python3 -m pip install py2app
    python3 setup_app.py py2app

Output: tray/dist/GratisGraph.app
    Drag to /Applications. Use the tray's "Start at login" toggle to launch at
    startup (or add it to System Settings -> General -> Login Items).

Notes:
  - pyusb loads libusb-1.0 via ctypes from the system (Homebrew) path at
    runtime, so this bundle runs on THIS machine as-is. Bundling libusb for
    distribution to other Macs (plus code-signing / notarisation) is a
    separate step, not needed for local use.
  - The menu-bar glyph is drawn at runtime by gts_tray._make_icon_image(),
    so no .icns is required for an LSUIElement agent.
"""
import os
import sys

from setuptools import setup

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
# Make the repo's top-level modules AND the tray's own modules importable so
# py2app's dependency graph can find and freeze them into the bundle.
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

APP = ['gts_tray.py']

# libusb-1.0: resolve the symlink to the real dylib so py2app copies the actual
# binary into Contents/Frameworks (the app then loads it via _configure_bundled_libusb).
_LIBUSB = os.path.realpath('/opt/homebrew/lib/libusb-1.0.0.dylib')
if not os.path.exists(_LIBUSB):
    _LIBUSB = os.path.realpath('/usr/local/lib/libusb-1.0.0.dylib')
FRAMEWORKS = [_LIBUSB] if os.path.exists(_LIBUSB) else []

ICON = os.path.join(HERE, 'resources', 'GratisGraph.icns')
MENUBAR_PNG = os.path.join(HERE, 'resources', 'menubar_G.png')

# The tray only moves bytes: gts_server -> gts_api over USB. It deliberately
# does NOT include the translation/optimisation/preview stack (that lives in
# the browser) — those modules are what dragged numpy/etc. into the bundle.
INCLUDES = [
    'gts_server', 'version', 'gts_api', 'usb', 'usb_helper',
    'ServiceManagement',   # SMAppService for the "Start at login" toggle (lazy import)
]

# Modules nothing in the tray's runtime graph uses; py2app's modulegraph pulls
# them in speculatively. Excluding them (and the native extensions + dylibs they
# drag in) is the bulk of the size win. All verified safe by `--selftest`, which
# boots the full app (importing gts_server -> hmac/secrets/uuid) and exercises
# PIL + pystray. NB: exclude _hashlib (the OpenSSL ext), NOT hashlib (the pure
# module hmac/uuid import) — hmac falls back to Python 3.14's HACL* builtins.
EXCLUDES = [
    # heavy third-party packages (numpy via Pillow's optional array path, etc.)
    'numpy', 'cryptography', 'cffi', 'pygments', 'Pygments',
    'setuptools', 'pkg_resources', '_distutils_hack',
    # build / test / doc tooling, never needed at runtime
    'tkinter', 'test', 'distutils', 'lib2to3', 'pydoc_data',
    'unittest', 'doctest', 'pydoc',
    '_testcapi', '_testinternalcapi', '_testbuffer', '_testmultiphase',
    '_testclinic', '_xxtestfuzz', '_ctypes_test',
    # the translation / optimise / preview stack lives in the browser
    'gts_translator', 'gts_optimise', 'gts_prepare', 'gts_preview',
    # OpenSSL: nothing here speaks TLS. Drops libcrypto (~4.3M) + libssl (~0.8M).
    'ssl', '_ssl', '_hashlib',
    # compression formats we never use (zipfile/tarfile import these lazily in
    # try/except, so dropping them is safe). Drops libzstd / liblzma / libbz2.
    'bz2', '_bz2', 'lzma', '_lzma', 'zstd', '_zstd', 'compression.zstd',
    # decimal -> libmpdec
    'decimal', '_decimal',
    # CJK text codecs (the wire protocol is ASCII)
    '_codecs_jp', '_codecs_kr', '_codecs_cn', '_codecs_tw', '_codecs_hk',
    '_codecs_iso2022',
    # other stdlib the tray never touches. (xml/pyexpat/_elementtree are NOT
    # excluded: pystray's macOS backend imports xml and fails without it.)
    'sqlite3', '_sqlite3',
]

PLIST = {
    'CFBundleName': 'GratisGraph',
    'CFBundleDisplayName': 'GratisGraph',
    'CFBundleIdentifier': 'com.stephenarsenault.gratisgraph',
    'CFBundleVersion': '0.1.0',
    'CFBundleShortVersionString': '0.1.0',
    'LSUIElement': True,            # menu-bar agent: no Dock icon, no window
    'LSMinimumSystemVersion': '11.0',
    'NSHumanReadableCopyright': 'GPL-3.0-or-later',
}

OPTIONS = {
    'argv_emulation': False,        # menu-bar apps must not use Carbon argv
    'plist': PLIST,
    'packages': ['pystray', 'PIL'],
    'includes': INCLUDES,
    'excludes': EXCLUDES,
    'frameworks': FRAMEWORKS,                       # bundle libusb-1.0 dylib
    'resources': [MENUBAR_PNG],                     # -> Contents/Resources/menubar_G.png
    'extra_scripts': [],
}
if os.path.exists(ICON):
    OPTIONS['iconfile'] = ICON

# Run prune_app after py2app finishes, so EVERY build is slimmed automatically
# (py2app re-copies all of Pillow each build, so the prune can't be a one-off).
try:
    from py2app.build_app import py2app as _py2app_command

    class _Py2appThenPrune(_py2app_command):
        def run(self):
            super().run()
            try:
                sys.path.insert(0, HERE)
                import prune_app
                dist = getattr(self, 'dist_dir', None) or os.path.join(HERE, 'dist')
                prune_app.prune(os.path.join(dist, 'GratisGraph.app'))
            except Exception as e:
                print(f'[setup_app] post-build PIL prune skipped: {e}')

    CMDCLASS = {'py2app': _Py2appThenPrune}
except Exception:
    CMDCLASS = {}

setup(
    app=APP,
    name='GratisGraph',
    options={'py2app': OPTIONS},
    setup_requires=['py2app'],
    cmdclass=CMDCLASS,
)
