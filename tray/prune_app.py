#!/usr/bin/env python3
"""
prune_app.py — post-build slimming of the packaged GratisGraph.app.

py2app bundles all of Pillow, including image-format codec dylibs the tray
never uses (it only loads a PNG menu-bar icon and draws simple shapes). This
removes the unused Pillow satellite extensions, then deletes every bundled
dylib that no *remaining* binary still references — computed as a dependency
closure via `otool`, so it can never delete a library something we keep
actually needs (e.g. libtiff is kept by _imaging and drags in liblzma/libjpeg,
which therefore stay too).

Runs automatically after `python setup_app.py py2app` (wired via cmdclass in
setup_app.py), or by hand:

    python3 prune_app.py [path/to/GratisGraph.app]
"""
import glob
import os
import shutil
import subprocess
import sys

# Pillow extensions the tray never exercises. Removing each lets its exclusive
# codec dylibs fall out of the closure. _imaging (core PNG/zlib), _imagingmath
# and _imagingmorph are kept.
REMOVE_EXTENSIONS = ['_avif', '_webp', '_imagingft', '_imagingcms', '_imagingtk']
# Matching format plugins (cosmetic: Pillow tolerates a missing codec, but
# dropping the .py keeps the format genuinely gone and the bundle tidy).
REMOVE_PLUGINS = ['AvifImagePlugin', 'WebPImagePlugin']


def _dir_size(path):
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            fp = os.path.join(root, f)
            if not os.path.islink(fp):
                try:
                    total += os.path.getsize(fp)
                except OSError:
                    pass
    return total


def _find_pil(app_path):
    hits = glob.glob(os.path.join(
        app_path, 'Contents', 'Resources', 'lib', 'python3.*', 'PIL'))
    return hits[0] if hits else None


def _bundled_deps(binary, present):
    """Basenames of this binary's linked dylibs that we actually bundle."""
    try:
        out = subprocess.run(['otool', '-L', binary],
                             capture_output=True, text=True, check=True).stdout
    except Exception:
        return []
    deps = []
    for line in out.splitlines()[1:]:           # line 0 is the binary's own path
        tok = line.strip().split(' ', 1)[0]
        base = os.path.basename(tok)            # matches @loader_path/... and @rpath/...
        if base in present:
            deps.append(base)
    return deps


def _make_sourceless(lib_root):
    """py2app ships both X.py and __pycache__/X.pyc for the loose C-extension
    packages (PIL, pystray, pyobjc, ctypes). The source is dead weight at
    runtime, so move each cached .pyc up to a sourceless X.pyc and drop the .py.
    The stdlib is already byte-only inside python3*.zip, so it's untouched."""
    saved = 0
    for dirpath, _dirs, files in os.walk(lib_root):
        if os.path.basename(dirpath) == '__pycache__':
            continue
        cache = os.path.join(dirpath, '__pycache__')
        if not os.path.isdir(cache):
            continue
        for name in files:
            if not name.endswith('.py'):
                continue
            stem = name[:-3]
            hits = sorted(glob.glob(os.path.join(cache, stem + '.cpython-*.pyc')))
            if not hits:
                continue
            src = os.path.join(dirpath, name)
            saved += os.path.getsize(src)
            shutil.move(hits[0], os.path.join(dirpath, stem + '.pyc'))  # sourceless
            os.remove(src)
            for extra in glob.glob(os.path.join(cache, stem + '.cpython-*.pyc')):
                os.remove(extra)   # drop -OO / duplicate variants
    # sweep now-empty __pycache__ dirs
    for dirpath, dirs, _files in os.walk(lib_root, topdown=False):
        if os.path.basename(dirpath) == '__pycache__' and not os.listdir(dirpath):
            os.rmdir(dirpath)
    return saved


def prune(app_path):
    pil = _find_pil(app_path)
    if not pil:
        print('[prune] no PIL in bundle — nothing to do')
        return
    dylibs_dir = os.path.join(pil, '.dylibs')
    before = _dir_size(app_path)

    # 1. remove the unused satellite extensions + their format plugins
    removed_ext = []
    for ext in REMOVE_EXTENSIONS:
        for so in glob.glob(os.path.join(pil, ext + '.*.so')):
            os.remove(so)
            removed_ext.append(os.path.basename(so))
    for plug in REMOVE_PLUGINS:
        for pat in (os.path.join(pil, plug + '.py'),
                    os.path.join(pil, '__pycache__', plug + '.*')):
            for f in glob.glob(pat):
                os.remove(f)

    # 2. dependency closure: every dylib still reachable from a KEPT binary
    present = set(os.listdir(dylibs_dir)) if os.path.isdir(dylibs_dir) else set()
    required = set()
    stack = glob.glob(os.path.join(pil, '*.so'))        # remaining extensions
    while stack:
        for dep in _bundled_deps(stack.pop(), present):
            if dep not in required:
                required.add(dep)
                stack.append(os.path.join(dylibs_dir, dep))   # walk transitively

    # 3. delete orphaned dylibs (present, but unreachable from anything kept)
    removed_dylibs = []
    for dy in sorted(present - required):
        os.remove(os.path.join(dylibs_dir, dy))
        removed_dylibs.append(dy)

    # 4. drop redundant .py source for loose packages (keep sourceless .pyc)
    src_saved = _make_sourceless(os.path.dirname(pil))

    after = _dir_size(app_path)
    print(f'[prune] removed {len(removed_ext)} PIL extension(s), '
          f'{len(removed_dylibs)} orphan dylib(s), and {src_saved / 1e6:.1f} MB '
          f'of redundant .py source')
    if removed_dylibs:
        print('[prune]   dropped: ' + ', '.join(removed_dylibs))
    print(f'[prune] bundle {before / 1e6:.1f} MB -> {after / 1e6:.1f} MB '
          f'({(before - after) / 1e6:.1f} MB saved)')


if __name__ == '__main__':
    target = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        os.path.dirname(os.path.abspath(__file__)), 'dist', 'GratisGraph.app')
    if not os.path.isdir(target):
        sys.exit(f'app bundle not found: {target}')
    prune(target)
