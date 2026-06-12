#!/usr/bin/env python3
"""
version.py — central version registry for support diagnostics.

Collects each pipeline component's __version__ plus environment info so a
support request can be matched to a precise setup. The tray app and the
server's diagnostic endpoint both read from here; nothing hardcodes versions.
"""
import platform
import sys


def component_versions() -> dict:
    """Per-module versions. Imported lazily so a broken module degrades to
    'unavailable' rather than taking the whole report down."""
    out = {}
    for name in ('gts_api', 'gts_translator', 'gts_optimise',
                 'gts_prepare', 'gts_preview', 'gts_server'):
        try:
            mod = __import__(name)
            out[name] = getattr(mod, '__version__', 'unversioned')
        except Exception as e:
            out[name] = f'unavailable ({type(e).__name__})'
    return out


def diagnostics() -> dict:
    """Full support snapshot: components + environment."""
    return {
        'components': component_versions(),
        'python': platform.python_version(),
        'os': f'{platform.system()} {platform.release()}',
        'machine': platform.machine(),
    }


if __name__ == '__main__':
    import json
    json.dump(diagnostics(), sys.stdout, indent=2)
    print()
