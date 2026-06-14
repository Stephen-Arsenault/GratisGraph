# GratisGraph

**Drive your Gravograph engraver from any computer — no Gravostyle, no Windows, no dongle.**

GratisGraph is an open-source Python driver for Gravograph M20-series contact engraving machines. It talks to the machine directly over raw USB, translating standard G-Code into the machine's native binary protocol (which we call **GTS**, documented from live USB captures and confirmed on hardware). Design in whatever you like — LightBurn, Inkscape, Fusion, anything that exports G-Code — and engrave from macOS, Linux, or Windows.

Confirmed working on the **M20 / M20 Express**, with profiles for the full family sharing the same protocol: **M20 XL, M20 PIX, M20 Jewel / Jewel 2, all M20 IQ variants, and M40 / M40G (incl. IQ)**. The **M10 Jewel** and **IS400 IQ** profiles are included but **unverified**: their parameters are read from the vendor's published INF files and have not yet been tested on live hardware. Drag and rotary machines only — laser machines use a different driver stack and are out of scope.

## Legal

GratisGraph is free software, licensed under the **GNU GPL v3** (see [LICENSE](LICENSE)). Copyright © the GratisGraph contributors.

This project is the result of independent interoperability research conducted on hardware owned by the authors, for the purpose of building an open-source driver. No proprietary source code was used or reproduced, and no proprietary binaries are distributed — only findings. *Gravograph, Gravotech, Gravostyle, and GT Smartstream are trademarks of Gravotech Group. This project is not affiliated with or endorsed by Gravotech.* No warranty: this software moves real motors with real cutters — keep your hands clear and test with a pen first.

## How it works

The pipeline is a clean, layered chain. Each layer only knows about the one below it:

```
G-Code text
   │  gts_translator.py   parse RS-274, expand arcs, split into strokes
   ▼
strokes  (start + relative segments, 0.01mm units)
   │  gts_optimise.py     reverse / merge / TSP-order / snake — pure geometry
   ▼
optimised strokes
   │  gts_api.py          assemble GTS binary (header, setup, opcodes)
   ▼
.m20 binary
   │  gts_api.GTSConnection   raw USB bulk transfer (pyusb), telemetry monitoring
   ▼
machine
```

`gts_prepare.py` sits on top as the friendly front door: it owns coordinate transforms (origin shift, Y-flip for LightBurn), envelope validation, and machine profiles, and wires the layers together. `GTSConnection` auto-detects the connected machine via the `!ID` handshake and selects the right profile.

## The tools

| File | What it does |
|---|---|
| `gts_ctl.py` | CLI entry point. Translate a G-Code file to `.m20`, send it, run the console or calibration. `python3 gts_ctl.py job.gc out.m20 --send` |
| `gts_prepare.py` | Library front door: `from_gcode()`, `from_file()`, `square()`, `circle()`, `home()`. Use this from your own scripts. |
| `gts_api.py` | The hardware layer: protocol constants, GTS binary builders, machine profiles, and `GTSConnection` (USB I/O, identification, job monitoring, macOS kext handling). |
| `gts_translator.py` | RS-274 G-Code parser and stroke extraction. Handles G0–G3, G20/21, G90/91, G92, arc expansion, inch/mm. |
| `gts_optimise.py` | Toolpath optimisation (direction reversal, stroke merging, nearest-neighbour + 2-opt ordering, bidirectional snaking) and job duration estimation. Pure geometry — no USB. |
| `gts_server.py` | Local HTTP bridge for the (upcoming) browser UI: `POST /job` raw `.m20` bytes, live telemetry via SSE, `/abort`. Loopback-only, token-paired, stdlib-only. `sudo python3 gts_server.py` |
| `gts_capture.py` | One-command support tool: captures machine USB traffic and produces a compact, paste-friendly report. Got a family machine we haven't confirmed? Run this and open an issue. `sudo python3 gts_capture.py --duration 30 -o report.txt` |
| `gts_preview.py` | Renders any `.m20` file as an SVG toolpath preview — see exactly what the machine will do before it does it. |
| `console.py` | Interactive REPL: jog, home, send files, sniff raw machine packets. |
| `calibrate.py` | Pen-on-paper calibration suite (squares, lines, circles, border) to verify scale and squareness. |

## Quick start (macOS)

```bash
pip install pyusb
# macOS claims the machine as a printer — run with sudo (or --disable-kext):
sudo python3 gts_ctl.py --console
gts> send myjob.gc
```

Tests: `python3 -m pytest tests/` (add `--hardware` with a machine connected). Byte-level regression tests compare our output against real Gravostyle captures in `tests/regression/`.

## Status & contributing

The protocol is confirmed end-to-end on real hardware: G-Code → GTS binary → USB → engraved part, with byte-identical output to Gravostyle reference captures. The wire format is fully specified in [`docs/GTS_FORMAT.md`](docs/GTS_FORMAT.md); remaining open questions live in [`docs/PROTOCOL_FINDINGS.md`](docs/PROTOCOL_FINDINGS.md) and `TODO.md`. If you own a family machine other than the M20 Express, connection logs and captures are gold — please open an issue.
