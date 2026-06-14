"""
setup_app.py — py2app packaging for the GratisGraph menu-bar app.

Builds a native macOS .app that lives ONLY in the menu bar (LSUIElement),
wrapping gts_tray.py / gts_server.py. No Dock icon, no window.

Build:
    cd tray
    python3 -m pip install py2app
    python3 setup_app.py py2app

Output: tray/dist/GratisGraph.app
    Drag to /Applications, then add to System Settings -> General ->
    Login Items to launch at startup.

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

# Top-level repo modules the tray pulls in (their transitive imports — struct,
# usb, http.server, etc. — are discovered automatically by modulegraph).
INCLUDES = [
    'gts_server', 'version', 'gts_api', 'gts_translator',
    'gts_optimise', 'gts_prepare', 'gts_preview', 'usb',
    'usb_helper',
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
    'frameworks': FRAMEWORKS,                       # bundle libusb-1.0 dylib
    'resources': [MENUBAR_PNG],                     # -> Contents/Resources/menubar_G.png
    'extra_scripts': [],
}
if os.path.exists(ICON):
    OPTIONS['iconfile'] = ICON

setup(
    app=APP,
    name='GratisGraph',
    options={'py2app': OPTIONS},
    setup_requires=['py2app'],
)
