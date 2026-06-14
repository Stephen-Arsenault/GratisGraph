# GratisGraph Tray

System-tray companion for the local GratisGraph server (`gts_server.py`).
Surfaces the pairing code, machine status, and — for support — the exact
version of every pipeline component, read live from the server's diagnostics
endpoint (never hardcoded).

This wraps the current Python server. If a native server ships later, the
tray is expected to be revisited; nothing here is load-bearing for the engine.

## Run

    pip install pystray pillow
    python3 tray/gts_tray.py            # starts the server + tray together

## Menu

- Pairing code (click to copy)
- Machine: connected / model, or "not found"
- Versions ▸  every component + OS/Python (the support snapshot)
- Regenerate pairing code
- Unlock (shown only during auth lockout — proof of physical presence)
- Open web UI
- Quit
