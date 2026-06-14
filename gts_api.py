#!/usr/bin/env python3
"""
gts_api.py — Gravograph M20 USB API

Machine constants, GTS binary builder, USB communication layer (GTSConnection),
and macOS kext manager. Import this from test scripts and the translator.

Protocol state:
  - Home = back-left, nearest the gantry (confirmed hardware: marker fiducial)
  - +X = right, +Y = toward the operator (table translates away) — no axis inversion needed
  - USB VID=0xFFFD PID=0x0001, EP OUT=0x01, EP IN=0x82
  - Machine queues jobs and waits for green button — does NOT auto-execute on receipt
  - Machine holds the last job in buffer after execution — pressing green again re-runs it
  - Buffer is cleared by power cycle or homing sequence
  - Machine streams !MX/MY/MZ axis position packets on EP IN during execution
  - !CM 4; arms job for auto-execution after disconnect (~15s delay)
  - !ID 0; only responded to shortly after power-on, not while idle
"""


__version__ = '0.1.0'

__all__ = [
    # Version
    '__version__',
    # Machine profile
    'MachineProfile', 'M20_PROFILE', 'M40_PROFILE',
    'M10_JEWEL_PROFILE', 'IS400_IQ_PROFILE',
    'PROFILE_FOR_MODEL_CODE',
    # Protocol constants
    'M20_VID', 'M20_PID', 'BULK_EP_OUT', 'BULK_EP_IN',
    'MAGIC', 'BOX_DIM', 'UNITS_PER_MM',
    'WORK_ENVELOPE_MM', 'PHYSICAL_ENVELOPE_MM',
    'DEFAULT_SPEED', 'DEFAULT_DEPTH', 'DEFAULT_LIFT',
    'DEFAULT_ACCEL', 'DEFAULT_ZA', 'PEN_W_RAMP',
    # Builders
    'build_gts', 'build_home_gts', 'build_gts_stroke',
    'build_square', 'build_circle', 'build_envelope',
    # Connection
    'GTSConnection',
    # Response tables
    'MACHINE_PLATFORM_TYPES', 'MODEL_CODE_NAMES',
]

import math
import struct
from dataclasses import dataclass
import time
import threading
import queue
import os
import subprocess
import platform
from typing import List, Tuple, Optional

# Protocol Constants
# ---------------------------------------------------------------------------

# GTS binary layer
MAGIC          = b'!MM88;'
METADATA       = 'ot : 1\x00'.encode('utf-16-le')
BOX_DIM        = 0x0DE4     # 3556 units = 35.56 mm — character cell height

DEFAULT_SPEED  = 25         # VS/VZ: vector speed
DEFAULT_PZ     = 30         # PZ: plunge Z (confirmed from 00013.m20 and 00018.m20)
DEFAULT_DZ     = 500        # DZ: drag/engrave depth (confirmed from 00013.SPL / 00018.SPL captures)
DEFAULT_LIFT   = 30         # alias for PZ, kept for callers using DEFAULT_LIFT
DEFAULT_DEPTH  = 500        # alias for DZ (= DEFAULT_DZ), confirmed from 00013.SPL / 00018.SPL
DEFAULT_ACCEL  = 551        # 0x0227 — constant in all observed segment records
PEN_W_RAMP     = 1          # pen_w at stroke start (ramps up)
DEFAULT_ZA     = 1          # ZA: Z approach depth (confirmed from 00013.SPL / 00018.SPL)

# Coordinate system
UNITS_PER_MM   = 100        # 1 unit = 0.01 mm (confirmed from hardware measurement)
                             # Previous incorrect value was 40 (0.025 mm/unit)
WORK_ENVELOPE_MM = (100.0, 100.0)  # Software-defined work area
PHYSICAL_ENVELOPE_MM = (120.0, 120.0)  # Conservative safe limit confirmed from hardware

# USB device identifiers — confirmed from live USB enumeration.
# 0xFFFD is an unregistered vendor ID. It coincidentally matches the GTS
# 0xFFFD opcode prefix; the two are unrelated. Do not change.
M20_VID        = 0xFFFD     # Gravograph USB vendor ID (unregistered)
M20_PID        = 0x0001     # GT Smartstream USB product ID
BULK_EP_OUT    = 0x01       # bulk OUT endpoint (host → machine)
BULK_EP_IN     = 0x82       # bulk IN endpoint (machine → host) — confirmed from live capture

# ASCII envelope commands (Layer 1)
CMD_IDENTIFY   = b'!ID 0;\r\n'    # query machine model
CMD_STOP       = b'!1B 0;\r\n'    # ESC / abort
CMD_TOOL_UP    = b'!TU 0;\r\n'    # retract spindle
CMD_TOOL_UP_4  = b'!TU 4;\r\n'    # retract spindle (confirmed variant)

# Machine platform type codes — from field [4] of the !ID response 28-byte binary struct.
# Confirmed from the machine's !ID response on the wire and cross-checked against
# the firmware updater's identity blobs (Id_*.bin).
# The !ID response is 28 bytes = 7 × int32:
#   [0] boot_ver_major  [1] boot_ver_minor
#   [2] prog_ver_major  [3] prog_ver_minor
#   [4] platform_type   [5] model_code_primary  [6] model_code_alt
#
# IMPORTANT — platform_type=4 and the M20 Express (confirmed from raw_capture.bin):
#   The live !ID response from an M20 Express unit returned:
#     platform_type=4, model_code=0x0204, boot_ver=1.2, prog_ver=1.30
#   platform_type=4 means the machine runs the Sirius-family firmware BASE, but this
#   does NOT make it a "Sirius engraver" — the M20 Express is a full M20-class machine.
#   The firmware platform (uc_sirius) is shared across several product lines.
#   The product identity is carried by model_code (0x200-series = M20 family).
#   Previously this entry was labelled 'Gravograph Sirius (legacy)', which is misleading.
MACHINE_PLATFORM_TYPES = {
    3: ('K2000',     'Gravograph K2000 / M20 standard series', 'Firmware\\uc_k2000\\'),
    4: ('M20Express','Gravograph M20 Express (Sirius FW base)', 'Firmware\\uc_sirius\\'),
}
# Any platform_type other than 3 or 4 is treated as IM4E.
PLATFORM_IM4E = ('IM4E', 'Gravograph IM4E', 'Firmware\\uc_im4e\\')

# Model codes reported in !ID response fields [5]/[6].
# Source: decoded directly from Firmware/ID Machines/*.bin files (11 bytes each,
# format: "!ID 4;\x00\x00[model_code_BE16]\x00"). These are authoritative —
# they are the identity blobs the firmware updater sends to the machine.
# Codes marked (unconfirmed) do not appear in any ID machine bin file and may be
# incorrect or unused.
MODEL_CODE_NAMES = {
    # Legacy platforms (pre-0x200 series)
    0x1C:  'Legacy platform A (unconfirmed)',
    0x1D:  'Legacy platform B (unconfirmed)',
    # M40 family (confirmed from Id_M40.bin, Id_M40G.bin)
    0x0202: 'M40',
    0x0203: 'M40G',
    # M20 family (confirmed from Id_M20.bin through Id_M20_JEWEL_IQ.bin)
    0x0204: 'M20',
    0x0205: 'M20 XL',
    0x0206: 'M20 Jewel',
    0x0207: 'M20 PIX',
    0x0209: 'M20 Jewel 2',
    # IQ variants (confirmed from Id_M20_IQ.bin etc.)
    0x020D: 'M20 IQ',
    0x020E: 'M20 XL IQ',
    0x020F: 'M20 Jewel IQ',
    0x0210: 'M40 IQ',
    0x0211: 'M40G IQ',
    0x0212: 'M10 Jewel',
    # Codes once suspected but not present in any ID bin file:
    0x0201: 'M20 variant (unconfirmed)',
    0x020B: 'M20 submodel (unconfirmed)',
    0x020C: 'M20 submodel (unconfirmed)',
}
# Legacy modern-protocol config codes (observed in some live captures;
# kept for backwards compatibility with old firmware versions).

# Machine → Host binary packet constants (confirmed from live USB capture)
STATUS_MOTOR_NEUTRAL = b'!MN'   # all axes idle; value = heartbeat counter
STATUS_MOTOR_X       = b'!MX'   # X axis; value = 0 at home, >0 = in-motion counter
STATUS_MOTOR_Y       = b'!MY'   # Y axis
STATUS_MOTOR_Z       = b'!MZ'   # Z axis absolute position
STATUS_MOTOR_E       = b'!ME'   # encoder / aux axis (observed in live capture)
STATUS_PACKET_LEN    = 10       # TYPE B: [6B ASCII "!MZ 4;"][4B BE uint32 value]
                                 # NOTE: on macOS without --disable-kext the
                                 # AppleUSBPrinter kext truncates these to 1 byte
                                 # (just the axis letter, e.g. 0x5A='Z'). The boot
                                 # broadcast arrives intact in the eager-capture window.
LONG_HEADER_LEN      = 28       # TYPE A binary struct: 7 × int32 big-endian
                                 # Confirmed raw bytes at capture offset 0x058FE6:
                                 # 00 00 00 01 00 00 00 02 00 00 00 01 00 00 00 1E
                                 # 00 00 00 04 00 00 02 04 00 00 00 00
                                 # → boot=1.2, prog=1.30, platform=4, model=0x0204



# ---------------------------------------------------------------------------
# GTS Binary Layer
# ---------------------------------------------------------------------------
# Wire format confirmed from 00013.m20 and 00018.m20 (real captured jobs):
#
#   Header (94 bytes)
#   Setup commands: DR MC VS VZ PZ DZ  (each 6 bytes: FF FD + opcode + uint16 BE)
#   Per stroke:
#     stroke_hdr (12 bytes raw struct: pw1 pw2 speed accel dx dy)  ← rapid to start
#     ZA N       (6 bytes: FF FD ZA uint16)                        ← Z approach
#     PD 0       (6 bytes: FF FD PD 0000)                          ← pen down
#     N × segment (12 bytes raw struct: pw1 pw2 speed accel dx dy) ← NO FF FD prefix
#     PU 0       (6 bytes)
#     PU 0       (6 bytes)
#     PU 1       (6 bytes: home flag)
#   Return block (12 bytes raw struct: move back to job origin)
#   JF 0        (6 bytes)
#
# CRITICAL: segments do NOT use FF FD NR framing and do NOT repeat VS/VZ/PZ/DZ.
# Earlier implementation had both of these wrong, causing Z to drop then
# immediately lift with no XY motion.



# ---------------------------------------------------------------------------
# Machine profile
# ---------------------------------------------------------------------------

@dataclass
class MachineProfile:
    """
    All machine-specific parameters in one place.
    Pass a profile to GTSConnection and gts_prepare functions to target
    a specific machine. Adding a new machine is a one-line change here.
    """
    name:              str
    vid:               int
    pid:               int
    units_per_mm:      int
    envelope_mm:       Tuple[float, float]
    physical_envelope_mm: Tuple[float, float]
    negate_x:          bool  = False   # M10 Jewel: XyzMul=-400
    coord_scale:       float = 1.0     # IS400 IQ: XyzMul=500 → scale × 1.25


# Confirmed from live hardware (M20 Express, USB sniffer captures)
M20_PROFILE = MachineProfile(
    name                 = 'M20',
    vid                  = 0xFFFD,
    pid                  = 0x0001,
    units_per_mm         = 100,
    envelope_mm          = (100.0, 100.0),
    physical_envelope_mm = (120.0, 120.0),
)

# Same GTS format as M20, confirmed from GT Smartstream INF/GPD files
M40_PROFILE = MachineProfile(
    name                 = 'M40',
    vid                  = 0xFFFD,
    pid                  = 0x0001,
    units_per_mm         = 100,
    envelope_mm          = (100.0, 100.0),
    physical_envelope_mm = (120.0, 120.0),
)

# M10 Jewel: XyzMul=-400 in GT Smartstream INF — negate X coordinates
M10_JEWEL_PROFILE = MachineProfile(
    name                 = 'M10 Jewel',
    vid                  = 0xFFFD,
    pid                  = 0x0001,
    units_per_mm         = 100,
    envelope_mm          = (100.0, 100.0),
    physical_envelope_mm = (120.0, 120.0),
    negate_x             = True,
)

# IS400 IQ: XyzMul=500 in GT Smartstream INF — coordinate scale × 1.25
IS400_IQ_PROFILE = MachineProfile(
    name                 = 'IS400 IQ',
    vid                  = 0xFFFD,
    pid                  = 0x0001,
    units_per_mm         = 100,
    envelope_mm          = (100.0, 100.0),
    physical_envelope_mm = (120.0, 120.0),
    coord_scale          = 1.25,
)

# Maps !ID model_code → MachineProfile.
# All M20/M40 family members share the same GTS format and coordinate scale.
# Conditional machines (M10 Jewel, IS400 IQ) have profile-level adjustments.
# Unknown codes fall back to M20_PROFILE with a warning.
PROFILE_FOR_MODEL_CODE: dict = {
    # M20 family
    0x0204: M20_PROFILE,       # M20 / M20 Express — live confirmed
    0x0205: M20_PROFILE,       # M20 XL
    0x0206: M20_PROFILE,       # M20 Jewel
    0x0207: M20_PROFILE,       # M20 PIX
    0x0209: M20_PROFILE,       # M20 Jewel 2
    0x020D: M20_PROFILE,       # M20 IQ
    0x020E: M20_PROFILE,       # M20 XL IQ
    0x020F: M20_PROFILE,       # M20 Jewel IQ
    # M40 family
    0x0202: M40_PROFILE,       # M40
    0x0203: M40_PROFILE,       # M40G
    0x0210: M40_PROFILE,       # M40 IQ
    0x0211: M40_PROFILE,       # M40G IQ
    # Conditional — adjusted profiles
    0x0212: M10_JEWEL_PROFILE, # M10 Jewel (negate_x)
    # IS400 IQ has no confirmed model code from !ID — mapped by name at runtime
}


def _cmd(opcode: bytes, arg: int) -> bytes:
    """Encode a 6-byte FF FD opcode command."""
    return b'\xFF\xFD' + opcode + struct.pack('>H', arg & 0xFFFF)


def _seg(dx: int, dy: int,
         speed: int = DEFAULT_SPEED,
         accel: int = DEFAULT_ACCEL,
         pw:    int = PEN_W_RAMP) -> bytes:
    """
    Encode a 12-byte raw segment or stroke-header record.
    Used for both the stroke header (rapid to start) and each motion segment.
    NO FF FD prefix — confirmed byte-for-byte against 00013.m20.
    """
    return struct.pack('>hhhhhh', pw, pw, speed, accel, dx, dy)


def _gts_header() -> bytes:
    """Build the 94-byte GTS job header."""
    hdr = bytearray(94)
    hdr[0:6] = MAGIC
    hdr[0x0A:0x0A + len(METADATA)] = METADATA
    hdr[0x24] = 0x02; hdr[0x25] = 0x04
    struct.pack_into('>H', hdr, 0x30, BOX_DIM)
    struct.pack_into('>H', hdr, 0x34, BOX_DIM)
    hdr[0x3D] = 0x02; hdr[0x5D] = 0x04
    return bytes(hdr)


def _gts_setup(speed: int = DEFAULT_SPEED,
               pz:    int = DEFAULT_PZ,
               dz:    int = DEFAULT_DZ) -> bytes:
    """Build the setup command block. Values confirmed from 00013.m20."""
    return (
        _cmd(b'DR', 0)    +
        _cmd(b'MC', 0)    +  # MC: spindle (0=off/diamond drag, 1=rotary on)
        _cmd(b'VS', speed) +
        _cmd(b'VZ', speed) +   # VZ = VS (same value, confirmed)
        _cmd(b'PZ', pz)   +
        _cmd(b'DZ', dz)
    )


def _gts_stroke_body(start_x:  int,
                     start_y:  int,
                     segments: bytes,
                     next_x:   int = 0,
                     next_y:   int = 0,
                     za:       int = DEFAULT_ZA,
                     speed:    int = DEFAULT_SPEED) -> bytes:
    """
    One pen-down stroke block followed by a rapid to the next stroke start.
    Used for intermediate strokes in a multi-stroke job.

      stroke_hdr(rapid to start) + ZA + PD + segments + PU×3 + rapid_to_next

    next_x/next_y are the absolute start of the following stroke. The rapid
    delta is (next_x - end_x, next_y - end_y), keeping the tool positioned
    for the next stroke without returning to home. Pass (0, 0) to return home.
    """
    total_dx = total_dy = 0
    seg_pos = 0
    while seg_pos + 12 <= len(segments):
        vals = struct.unpack_from('>hhhhhh', segments, seg_pos)
        if vals[3] == DEFAULT_ACCEL:
            total_dx += vals[4]
            total_dy += vals[5]
            seg_pos += 12
        else:
            break
    end_x = start_x + total_dx
    end_y = start_y + total_dy
    rapid_dx = next_x - end_x
    rapid_dy = next_y - end_y
    return (
        _seg(start_x, start_y, speed=speed) +
        _cmd(b'ZA', za)  +
        _cmd(b'PD', 0)   +
        segments         +
        _cmd(b'PU', 0)   +
        _cmd(b'PU', 0)   +
        _cmd(b'PU', 1)   +
        _seg(rapid_dx, rapid_dy, speed=speed)
    )


def _gts_stroke(start_x:  int,
                start_y:  int,
                segments: bytes,
                za:       int = DEFAULT_ZA,
                speed:    int = DEFAULT_SPEED) -> bytes:
    """
    Final stroke of a job: stroke_hdr + ZA + PD + segs + PU×3 + return-home + JF.
    The return rapid is computed from the segment deltas by _gts_stroke_body
    (next_x/next_y default to 0,0 = home).
    """
    return (
        _gts_stroke_body(start_x, start_y, segments, za=za, speed=speed) +
        _cmd(b'JF', 0)
    )

def build_gts_stroke(start_x_mm: float, start_y_mm: float,
                     moves_mm:   list,
                     speed:      int = DEFAULT_SPEED,
                     pz:         int = DEFAULT_PZ,
                     dz:         int = DEFAULT_DZ,
                     za:         int = DEFAULT_ZA) -> bytes:
    """
    Build a complete single-stroke GTS job.

    start_x_mm, start_y_mm  — stroke start position in mm from home
    moves_mm                — list of (dx_mm, dy_mm) relative moves

    The tool rapids (pen up) to (start_x_mm, start_y_mm), lowers Z once,
    draws all segments continuously, raises Z once, then returns to home.
    """
    sx   = int(round(start_x_mm * UNITS_PER_MM))
    sy   = int(round(start_y_mm * UNITS_PER_MM))
    segs = b''.join(_seg(int(round(dx * UNITS_PER_MM)),
                         int(round(dy * UNITS_PER_MM)), speed=speed)
                    for dx, dy in moves_mm)
    body = _gts_stroke(sx, sy, segs, za=za, speed=speed)
    return _gts_header() + _gts_setup(speed=speed, pz=pz, dz=dz) + body


def build_square(centre_x_mm: float = 50.0,
                 centre_y_mm: float = 50.0,
                 side_mm:     float = 100.0,
                 speed:       int   = DEFAULT_SPEED,
                 pz:          int   = DEFAULT_PZ,
                 dz:          int   = DEFAULT_DZ) -> bytes:
    """
    Build a GTS job that draws a closed square.
    Z is lowered once at the start and raised once at the end.
    Confirmed working on hardware (100×100mm, 90×90mm, 50×50mm).
    """
    half = side_mm / 2.0
    sx   = int(round((centre_x_mm - half) * UNITS_PER_MM))
    sy   = int(round((centre_y_mm - half) * UNITS_PER_MM))
    s    = int(round(side_mm * UNITS_PER_MM))
    segs = (_seg(+s, 0, speed=speed) + _seg(0, +s, speed=speed) +
            _seg(-s, 0, speed=speed) + _seg(0, -s, speed=speed))
    body = _gts_stroke(sx, sy, segs, speed=speed)
    return _gts_header() + _gts_setup(speed=speed, pz=pz, dz=dz) + body


def build_circle(centre_x_mm: float = 50.0,
                 centre_y_mm: float = 50.0,
                 radius_mm:   float = 45.0,
                 steps:       int   = 72,
                 speed:       int   = DEFAULT_SPEED,
                 pz:          int   = DEFAULT_PZ,
                 dz:          int   = DEFAULT_DZ) -> bytes:
    """
    Build a GTS job that approximates a circle with linear segments.
    Starts at the rightmost point (3 o'clock), traverses counter-clockwise.
    72 steps = 5° per segment. Confirmed working on hardware.
    """
    cx = int(round(centre_x_mm * UNITS_PER_MM))
    cy = int(round(centre_y_mm * UNITS_PER_MM))
    r  = int(round(radius_mm   * UNITS_PER_MM))
    sx, sy = cx + r, cy
    segs = b''
    px, py = sx, sy
    for i in range(1, steps + 1):
        angle = 2 * math.pi * i / steps
        nx = cx + int(round(r * math.cos(angle)))
        ny = cy + int(round(r * math.sin(angle)))
        segs += _seg(nx - px, ny - py, speed=speed)
        px, py = nx, ny
    body = _gts_stroke(sx, sy, segs, speed=speed)
    return _gts_header() + _gts_setup(speed=speed, pz=pz, dz=dz) + body


def build_gts(strokes: list,
              speed:   int = DEFAULT_SPEED,
              pz:      int = DEFAULT_PZ,
              dz:      int = DEFAULT_DZ) -> bytes:
    """
    Assemble a multi-stroke GTS job.

    strokes — list of (start_x, start_y, [(dx, dy), ...]) tuples
              all coordinates in machine units (not mm), starts ABSOLUTE
              from home. This matches the output of gcode_to_strokes().

    Wire format (CONFIRMED ON HARDWARE 2026-06-09, telemetry-verified to
    0.01mm): every coordinate on the wire is a RELATIVE delta. Each stroke's
    header is the pen-up rapid delta from the previous stroke's END to this
    stroke's start (first stroke: from home, so delta == absolute). There is
    NO separate inter-stroke rapid record — the header IS the rapid. The job
    ends with a return-to-home delta and a single JF.

    The previous emission (absolute headers + separate inter-stroke rapid)
    double-offset every stroke after the first: a stroke commanded to start
    at (30,40)mm landed at (60,80)mm — !MY telemetry peaked at 7999 (79.99mm)
    vs 3999 (39.99mm) with this format.
    """
    if not strokes:
        raise ValueError('build_gts: strokes list is empty — no job to build')

    all_body = b''
    cur_x, cur_y = 0, 0                       # tool position (units), from home

    for start_x, start_y, moves in strokes:
        # Header: rapid delta from current position to this stroke's start
        all_body += _seg(start_x - cur_x, start_y - cur_y, speed=speed)
        all_body += _cmd(b'ZA', DEFAULT_ZA)
        all_body += _cmd(b'PD', 0)
        for dx, dy in moves:
            all_body += _seg(dx, dy, speed=speed)
        all_body += _cmd(b'PU', 0) + _cmd(b'PU', 0) + _cmd(b'PU', 1)
        cur_x = start_x + sum(dx for dx, _ in moves)
        cur_y = start_y + sum(dy for _, dy in moves)

    # Return to home (delta) + single job finish
    all_body += _seg(-cur_x, -cur_y, speed=speed)
    all_body += _cmd(b'JF', 0)

    return _gts_header() + _gts_setup(speed=speed, pz=pz, dz=dz) + all_body


def build_home_gts() -> bytes:
    """
    Build a minimal GTS job that homes the machine.
    Contains only: header + DR + MC + PU(1) + JF — no strokes, no motion.
    When queued, the machine beeps and homes all axes when green is pressed.
    """
    return (_gts_header()
            + _cmd(b'DR', 0)
            + _cmd(b'MC', 0)  # MC: spindle (0=off/diamond drag, 1=rotary on)
            + _cmd(b'PU', 1)
            + _cmd(b'JF', 0))


def build_envelope(gts_payload: bytes,
                   plate_size: int = 20,
                   orientation: int = 28) -> bytes:
    """
    Wrap a GTS binary payload in a Layer 1 ASCII command envelope.

    IMPORTANT: This envelope is NOT sent over raw USB to the machine.
    When communicating directly over the bulk USB endpoint, only the raw
    GTS binary is written (see send_job, which uses wrap=False). The ASCII
    envelope commands (!PS, !OR, !MM) are consumed by the Windows Language
    Monitor (GT_SmartstreamMon.dll) inside the print spooler stack and are
    stripped before the bytes reach the USB wire — they never arrive at the
    machine firmware.

    This function exists only for producing .m20 files that can be fed into
    the Windows driver pipeline, or for reference/documentation purposes.

    !PS and !OR arguments (20 and 28) match values seen on the Windows print
    path; they may be plate-size and orientation codes used only by the
    spooler. Their exact semantics over raw USB are unconfirmed.
    """
    header = (
        f'!PS {plate_size};\r\n'
        f'!OR {orientation};\r\n'
        f'!MM {len(gts_payload)};\r\n'
    ).encode('ascii')
    return header + gts_payload


# ---------------------------------------------------------------------------
# macOS Kext Manager
# ---------------------------------------------------------------------------

class KextManager:
    """
    Temporarily unloads the macOS AppleUSBPrinter kernel extension for the
    duration of an M20 session.

    When macOS loads AppleUSBPrinter it:
      1. Claims the USB Printer Class interface exclusively.
      2. Continuously polls the device with ZLP status requests (4B 00 00),
         which appear as OUT DATA1 packets and consume bandwidth.
      3. Consumes the device's boot broadcast before libusb can see it.

    Unloading the kext before connecting gives libusb exclusive access.
    The kext is reloaded automatically on disconnect (or program exit).

    Usage:
        mgr = KextManager()
        mgr.unload()    # before connect
        ...             # do work
        mgr.reload()    # after disconnect

    Requires sudo. On macOS 11+ kextunload still works for non-SIP kexts.
    AppleUSBPrinter is not SIP-protected.
    """

    KEXT_ID = 'com.apple.driver.AppleUSBPrinter'
    KEXTUNLOAD = '/sbin/kextunload'
    KEXTLOAD = '/sbin/kextload'

    def __init__(self, verbose: bool = True):
        self.verbose       = verbose
        self._was_unloaded = False  # true if WE unloaded it (so we reload)

    def _log(self, msg: str) -> None:
        if self.verbose:
            print(f'[kext]  {msg}', flush=True)

    def _is_macos(self) -> bool:
        return platform.system() == 'Darwin'

    def _kext_loaded(self) -> bool:
        """Return True if AppleUSBPrinter is currently loaded."""
        try:
            result = subprocess.run(
                ['kextstat', '-b', self.KEXT_ID],
                capture_output=True, text=True, timeout=5
            )
            return self.KEXT_ID in result.stdout
        except Exception:
            return False

    def _sudo_prefix(self) -> list:
        """How to elevate the kext command.

        - Already root (the classic `sudo python3 ...` workflow): no prefix.
        - Otherwise: `sudo -n` (non-interactive). With the one-time USB helper
          installed (tray/usb_helper.py), the scoped sudoers rule lets these
          two exact commands run password-free. Without it, `-n` fails fast
          instead of hanging a GUI app on an impossible password prompt.
        """
        if hasattr(os, 'geteuid') and os.geteuid() == 0:
            return []
        return ['sudo', '-n']

    def _needs_helper(self, stderr: str) -> bool:
        s = (stderr or '').lower()
        return 'password is required' in s or 'a terminal is required' in s

    def unload(self) -> bool:
        """
        Unload AppleUSBPrinter. Returns True on success.
        No-op on non-macOS platforms or if kext is already unloaded.
        """
        if not self._is_macos():
            return True

        if not self._kext_loaded():
            self._log('AppleUSBPrinter not loaded — nothing to unload.')
            return True

        self._log(f'Unloading {self.KEXT_ID} ...')
        try:
            result = subprocess.run(
                self._sudo_prefix() + [self.KEXTUNLOAD, '-b', self.KEXT_ID],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0:
                self._was_unloaded = True
                self._log('Unloaded successfully.')
                return True
            else:
                if self._needs_helper(result.stderr):
                    self._log('Not authorised to unload the printer driver. '
                              'Run with sudo, or install the one-time helper: '
                              'python3 tray/usb_helper.py install')
                else:
                    self._log(
                        f'kextunload failed (exit {result.returncode}): '
                        f'{result.stderr.strip()}'
                    )
                self._log('Continuing without kext unload — OUT traffic may be noisy.')
                return False
        except subprocess.TimeoutExpired:
            self._log('kextunload timed out.')
            return False
        except FileNotFoundError:
            self._log('sudo/kextunload not found — skipping.')
            return False

    def reload(self) -> bool:
        """
        Reload AppleUSBPrinter if we were the ones who unloaded it.
        Returns True on success or if reload was not needed.
        """
        if not self._is_macos() or not self._was_unloaded:
            return True

        self._log(f'Reloading {self.KEXT_ID} ...')
        try:
            result = subprocess.run(
                self._sudo_prefix() + [self.KEXTLOAD, '-b', self.KEXT_ID],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0:
                self._was_unloaded = False
                self._log('Reloaded successfully.')
                return True
            else:
                self._log(
                    f'kextload failed (exit {result.returncode}): '
                    f'{result.stderr.strip()}'
                )
                return False
        except subprocess.TimeoutExpired:
            self._log('kextload timed out.')
            return False
        except FileNotFoundError:
            self._log('sudo/kextload not found — skipping.')
            return False


# ---------------------------------------------------------------------------
# USB Communication Layer
# ---------------------------------------------------------------------------

class GTSConnection:
    """
    Manages bidirectional USB communication with an M20 (or compatible) machine.

    Handles the full job-send protocol:
      1. Identify machine (!ID 0;)
      2. Send GTS binary to machine
      3. Monitor responses from machine throughout

    Requires pyusb: pip install pyusb
    On Linux: add udev rule or run as root.
    On macOS: may need to detach kernel driver.
    """

    READ_TIMEOUT_MS  = 2000    # normal read timeout
    SHORT_TIMEOUT_MS = 200     # used when polling for optional responses
    READ_CHUNK_SIZE  = 512     # bytes per USB read

    def __init__(self, vid: int = M20_VID, pid: int = M20_PID,
                 verbose: bool = True, disable_kext: bool = False,
                 profile: 'MachineProfile | None' = None):
        # Profile takes precedence over individual vid/pid if supplied
        if profile is not None:
            vid = profile.vid
            pid = profile.pid
        self.profile = profile or M20_PROFILE
        self.vid     = vid
        self.pid     = pid
        self.verbose = verbose
        self.dev     = None
        self._rx_thread  = None
        self._rx_queue: queue.Queue = queue.Queue()
        self._rx_stop    = threading.Event()
        self._rx_buf     = b''   # accumulate partial responses
        self.model       = ''    # short model string e.g. 'M20' (set by identify)
        self.config_code = 0     # platform_type from !ID response (3/4)
        self.model_code  = 0     # model_code from !ID response (e.g. 0x0204)
        self.boot_ver    = ''    # e.g. '1.2'  (set by identify)
        self.prog_ver    = ''    # e.g. '1.30' (set by identify)
        self._taps: list = []    # observers called with every decoded packet
        self._raw_taps: list = [] # observers called with every raw RX chunk
        self._ep_out: int = BULK_EP_OUT   # bulk OUT endpoint (discovered at connect)
        self._ep_in:  int = BULK_EP_IN    # bulk IN endpoint  (discovered at connect, may stay -1)
        self._kext_mgr   = KextManager(verbose=verbose) if disable_kext else None

    # --- Kernel driver detach + interface claim ---

    _MACOS_DETACH_ERROR = (
        '\n'
        '[ERROR] macOS denied access to the M20 USB interface (errno 13).\n'
        '\n'
        'The macOS USB Printer driver has claimed the device exclusively.\n'
        'Two options to fix this:\n'
        '\n'
        '  Option 1 — Run with sudo (simplest for testing):\n'
        '       sudo python3 m20.py --console\n'
        '\n'
        '  Option 2 — Unload the printer driver first, then run normally:\n'
        '       sudo kextunload -b com.apple.driver.AppleUSBPrinter\n'
        '       python3 m20.py --console\n'
        '     Or pass --disable-kext to do this automatically (requires sudo):\n'
        '       sudo python3 m20.py --console --disable-kext\n'
        '\n'
        '  Option 3 — Install the one-time USB helper (no sudo afterwards):\n'
        '       python3 tray/usb_helper.py install\n'
        '     Adds a password-free sudoers rule for ONLY kextunload/kextload\n'
        '     of the printer driver, so the app (and menu-bar app) can run\n'
        '     unprivileged.\n'
        '\n'
        'The driver reloads automatically on USB reconnect.\n'
    )

    def _detach_and_claim(self, usb_util) -> None:
        """
        Detach any kernel driver from interface 0 and claim it for libusb.

        On macOS the USB Printer class driver (AppleUSBPrinter / DriverKit)
        holds an exclusive IOKit claim on the interface.  libusb can only
        detach it when running as root or after the kext has been unloaded.

        If detach fails with errno 13 (LIBUSB_ERROR_ACCESS) we raise a
        RuntimeError with a clear actionable message rather than silently
        continuing with an unclaimed interface — which would cause all
        subsequent writes to succeed at the libusb layer but be silently
        discarded before reaching the USB wire.
        """
        import usb.core as _uc
        try:
            if self.dev.is_kernel_driver_active(0):
                self.dev.detach_kernel_driver(0)
                # Verify detach actually worked — on some macOS configurations
                # detach_kernel_driver() returns without raising but the driver
                # remains attached.
                if self.dev.is_kernel_driver_active(0):
                    raise RuntimeError(self._MACOS_DETACH_ERROR)
        except _uc.USBError as e:
            if getattr(e, 'errno', None) == 13 or 'access' in str(e).lower():
                raise RuntimeError(self._MACOS_DETACH_ERROR) from e
            raise  # unexpected USB error — propagate unchanged

        self.dev.set_configuration(1)
        usb_util.claim_interface(self.dev, 0)

    # --- Endpoint discovery ---


    def _discover_endpoints(self, usb_mod=None) -> None:
        """
        Walk the device descriptor to find actual bulk endpoint addresses.
        Updates self._ep_out and self._ep_in.
        Logs a full endpoint map for diagnostics.
        The M20 may be OUT-only (no IN endpoint); that is handled gracefully.
        """
        cfg  = self.dev.get_active_configuration()
        intf = cfg[(0, 0)]
        found_out = None
        found_in  = None

        self._log('Endpoint map:')
        for ep in intf:
            attr      = ep.bmAttributes & 0x03
            ep_type   = {0:'Control', 1:'Isochronous', 2:'Bulk', 3:'Interrupt'}.get(attr, '?')
            direction = 'IN' if (ep.bEndpointAddress & 0x80) else 'OUT'
            self._log(f'  EP 0x{ep.bEndpointAddress:02X}  {ep_type:12s}  {direction}'
                      f'  maxPacket={ep.wMaxPacketSize}')
            if attr == 2:  # Bulk
                if direction == 'OUT' and found_out is None:
                    found_out = ep.bEndpointAddress
                elif direction == 'IN' and found_in is None:
                    found_in  = ep.bEndpointAddress

        if found_out is None:
            raise RuntimeError('No bulk OUT endpoint found on device.')

        self._ep_out = found_out
        self._ep_in  = found_in if found_in is not None else -1

        summary = f'Using  OUT=0x{self._ep_out:02X}'
        summary += f'  IN=0x{self._ep_in:02X}' if self._ep_in >= 0 else '  IN=none (TX-only device)'
        self._log(summary)

    # --- Device waiting ---

    def connect(self, timeout_s: float = 5.0, wait: bool = False) -> bool:
        """
        Open the USB connection to the machine.

        timeout_s  — how long to poll for the device (default 5s).
                     Pass a larger value (e.g. 60.0) when the machine may
                     need to be powered on.
        wait       — if True, print a "Power on the M20 now" prompt while
                     polling. Implied when timeout_s > 10.0.

        Returns True if connected and identified, False if timed out.
        Raises RuntimeError on permission errors (run with sudo on macOS).
        """
        try:
            import usb.core as _uc
            import usb.util as _uu
        except ImportError:
            raise RuntimeError('pyusb not installed. Run: pip install pyusb')

        prompting = wait or timeout_s > 10.0

        # Unload AppleUSBPrinter so macOS does not claim the interface first.
        if self._kext_mgr is not None:
            self._kext_mgr.unload()

        if prompting:
            print(f'Waiting for M20 (VID=0x{self.vid:04X} PID=0x{self.pid:04X})...')
            print('Power on the M20 now. (Ctrl-C to cancel)')

        deadline = time.monotonic() + timeout_s
        dots = 0

        while True:
            dev = _uc.find(idVendor=self.vid, idProduct=self.pid)
            if dev is not None:
                if prompting:
                    print()
                self.dev = dev
                break

            if time.monotonic() >= deadline:
                if prompting:
                    print()
                self._log(f'[WARN] M20 not found after {timeout_s:.0f}s.')
                return False

            time.sleep(0.05)
            if prompting:
                elapsed = timeout_s - (deadline - time.monotonic())
                if int(elapsed) > dots:
                    dots = int(elapsed)
                    print('.', end='', flush=True)

        # Claim the interface
        try:
            self._detach_and_claim(_uu)
        except RuntimeError:
            raise
        except Exception as e:
            self._log(f'[WARN] Interface setup: {e}')

        # Discover endpoints
        self._discover_endpoints(None)

        # Eager boot broadcast capture (500ms window)
        if self._ep_in >= 0:
            self._log('Capturing boot broadcast (500ms window)...')
            capture_end = time.monotonic() + 0.5
            raw_capture = b''
            while time.monotonic() < capture_end:
                try:
                    chunk = self.dev.read(self._ep_in, 512, timeout=50)
                    raw_capture += bytes(chunk)
                except Exception as e:
                    if not any(w in str(e).lower() for w in ('timeout', 'timed out')):
                        break
            if raw_capture:
                self._log(f'Captured {len(raw_capture)} bytes during boot window')
                self._rx_buf = raw_capture
                for t in list(self._raw_taps):
                    try: t(raw_capture)
                    except Exception: pass
            else:
                self._log('No boot broadcast received in 500ms window')

        # Start background reader thread
        self._rx_stop.clear()
        self._rx_thread = threading.Thread(
            target=self._reader_thread, daemon=True, name='m20-rx')
        self._rx_thread.start()

        # Parse any captured boot bytes
        if self._rx_buf:
            self._parse_rx_buf()

        # Identify machine
        self._log(f'Connected: {self.dev.manufacturer or "?"} '
                  f'{self.dev.product or "?"} '
                  f'(VID=0x{self.vid:04X} PID=0x{self.pid:04X})')
        self.identify()
        self._auto_select_profile()
        return True


    def _auto_select_profile(self) -> None:
        """
        Select the correct MachineProfile based on the model code returned
        by !ID. Called automatically after identify() in connect().

        Updates self.profile in-place. If the model code is not in
        PROFILE_FOR_MODEL_CODE, keeps the current profile and logs a warning.

        Keyed on self.model_code (e.g. 0x0204), NOT self.config_code —
        config_code holds the !ID platform_type (3/4), which is a firmware
        family identifier, not a product identifier.
        """
        if self.model_code and self.model_code in PROFILE_FOR_MODEL_CODE:
            detected = PROFILE_FOR_MODEL_CODE[self.model_code]
            if detected is not self.profile:
                self._log(
                    f'Auto-selected profile: {detected.name} '
                    f'(model code 0x{self.model_code:04X})'
                )
                self.profile = detected
        elif self.model_code:
            self._log(
                f'[WARN] Unknown model code 0x{self.model_code:04X} — '
                f'keeping profile: {self.profile.name}. '
                f'Please report this to the GratisGraph project.'
            )

    def disconnect(self):
        """Release the USB interface and stop the background reader."""
        self._rx_stop.set()
        if self._rx_thread and self._rx_thread.is_alive():
            self._rx_thread.join(timeout=1.0)

        if self.dev is not None:
            # Do NOT call usb.util.release_interface() — this allows
            # AppleUSBPrinter to reattach before the process exits.
            # Hold the claim until the process terminates naturally.
            self.dev = None
        self._log('Disconnected.')

        # Reload AppleUSBPrinter now that we are done with the device.
        if self._kext_mgr is not None:
            self._kext_mgr.reload()

    def __enter__(self):
        if not self.connect():
            raise RuntimeError(
                f'M20 not found (VID=0x{self.vid:04X} PID=0x{self.pid:04X}). '
                f'Check power and USB cable.'
            )
        return self

    def __exit__(self, *_):
        self.disconnect()

    # --- Packet taps (observers) ---

    def add_raw_tap(self, fn) -> None:
        """Register fn(chunk: bytes) for every raw RX read, pre-parsing.
        Used by gts_capture --raw. Must never block."""
        self._raw_taps.append(fn)

    def add_tap(self, fn) -> None:
        """
        Register fn(item) to be called with every decoded RX packet, in
        addition to normal queue delivery. Used by gts_server (SSE telemetry)
        and gts_capture. Taps must never block; exceptions are swallowed.
        """
        self._taps.append(fn)

    def remove_tap(self, fn) -> None:
        try:
            self._taps.remove(fn)
        except ValueError:
            pass

    def _emit(self, item) -> None:
        """Deliver a decoded packet to the RX queue and all taps."""
        self._rx_queue.put(item)
        for tap in list(self._taps):
            try:
                tap(item)
            except Exception:
                pass

    # --- Core send/receive ---

    def send_raw(self, data: bytes):
        """Write raw bytes to the bulk OUT endpoint."""
        if self.dev is None:
            raise RuntimeError('Not connected.')
        self.dev.write(self._ep_out, data, timeout=5000)
        if self.verbose:
            decoded = data[:60].decode('ascii', errors='replace')
            preview = ''.join(c if 32 <= ord(c) < 127 else '·'
                              for c in decoded)
            self._log(f'TX ({len(data)} bytes): {preview}{"…" if len(data)>60 else ""}')

    def send_ascii(self, cmd: str):
        """Send an ASCII envelope command (adds \\r\\n if missing)."""
        if not cmd.endswith('\n'):
            cmd += '\r\n'
        self.send_raw(cmd.encode('ascii'))

    def recv(self, timeout_ms: int = READ_TIMEOUT_MS):
        """
        Return the next complete response line from the machine, or None on timeout.
        Lines are newline or semicolon terminated in the Gravograph protocol.
        """
        try:
            line = self._rx_queue.get(timeout=timeout_ms / 1000.0)
            return line
        except queue.Empty:
            return None

    def recv_all(self, timeout_ms: int = SHORT_TIMEOUT_MS) -> List[str]:
        """Drain all pending responses within timeout_ms."""
        lines = []
        deadline = time.monotonic() + timeout_ms / 1000.0
        while time.monotonic() < deadline:
            try:
                lines.append(self._rx_queue.get(timeout=0.02))
            except queue.Empty:
                break
        return lines

    # --- Protocol operations ---

    def identify(self) -> str:
        """
        Identify the connected machine. Returns a short model string e.g. 'M20Express'.

        Two-phase strategy:

          Phase 1 (0.5s window) — listen for the machine's spontaneous connect
            broadcast. The M20 sends a TYPE A identification packet automatically
            on USB attach, before any host command. We catch it here.

          Phase 2 — active query. If nothing arrived spontaneously, send !ID 0;
            and wait up to 3s for the TYPE A response.

        Model is encoded in the model_code field of the TYPE A struct (offset +20):
          0x0204 = M20 Express (confirmed live)
          0x0200 = M20 standard
          0x0304 = M40, etc.
        Platform type (offset +16) describes the firmware base, not the product line:
          3 = K2000/M20 standard firmware
          4 = Sirius firmware base (used by M20 Express)
        """
        def _extract(item):
            platform_type = item.get('platform_type', item.get('config', 0))
            plat_entry    = MACHINE_PLATFORM_TYPES.get(platform_type, PLATFORM_IM4E)
            short_name, desc, _ = plat_entry
            model_code    = item.get('model_code', 0)
            model_name    = MODEL_CODE_NAMES.get(model_code, f'0x{model_code:04X}')
            self._log(f'Machine identified: {desc}  model={model_name}  '
                      f'boot={item.get("boot_ver","?")}  prog={item.get("prog_ver","?")}')
            self.model       = short_name
            self.config_code = platform_type
            self.model_code  = model_code
            self.boot_ver    = item.get('boot_ver', '')
            self.prog_ver    = item.get('prog_ver', '')
            return short_name

        # Phase 1: spontaneous broadcast
        held    = []
        found   = None
        p1_end  = time.monotonic() + 0.5
        while time.monotonic() < p1_end:
            item = self.recv(timeout_ms=100)
            if item is None:
                break
            if isinstance(item, dict) and item.get('type') == 'long' and found is None:
                found = item
                break
            held.append(item)
        for h in held:
            self._rx_queue.put(h)
        if found is not None:
            self._log('Identified from spontaneous connect broadcast.')
            return _extract(found)

        # Phase 2: send !ID 0; once and wait up to 3s.
        # The previous multi-command wake-up sequence added 6+ seconds of dead
        # wait on every connect. USB captures confirmed the machine is
        # present and communicating at the transport layer but is not currently
        # responding to !ID 0; with a TYPE A packet — that is an open protocol
        # question. Keep this path lean: one attempt, short wait, then proceed.
        self._log('Sending !ID 0; ...')
        try:
            self.dev.write(self._ep_out, b'!ID 0;\r\n', timeout=2000)
        except Exception as e:
            self._log(f'TX error sending !ID 0;: {e}')

        # Heartbeats mean the machine is ALIVE — they are NOT evidence that
        # !ID will go unanswered. A live run (2026-06-09 20:49) showed the
        # !ID reply arriving moments after a boot-window heartbeat had made
        # an earlier build give up; on a non-M20 that would mis-profile.
        # Wait the full window; only fall back when it truly expires.
        heartbeat_seen = False
        p2_end = time.monotonic() + 3.0
        while time.monotonic() < p2_end:
            item = self.recv(timeout_ms=300)
            if item is None:
                continue
            if isinstance(item, dict) and item.get('type') == 'long':
                return _extract(item)
            if isinstance(item, dict) and item.get('type') == 'motor':
                if not heartbeat_seen:
                    heartbeat_seen = True
                    self._log('Heartbeat received — machine alive; '
                              'still waiting for !ID...')
                continue
            if isinstance(item, str) and not item.startswith('[USB'):
                self._log(f'RX ASCII during identify: {item.strip()!r}')
                continue

        # Window expired with no !ID — machine present (interface claimed,
        # writes live) but not answering right now. Proceed with the default
        # profile; model_code stays 0 (= assumed, not confirmed). If an !ID
        # arrives later, _dispatch_long_packet updates identity and profile
        # opportunistically.
        if heartbeat_seen:
            self._log('Machine alive (heartbeats) but no !ID reply in 3s.')
        else:
            self._log('No response to !ID 0; — machine present, not answering.')
        self._log('Proceeding with default profile '
                  f'({self.profile.name}); a late !ID will still be applied.')
        self.model = 'M20'
        return 'M20'


    def send_gts(self, gts: bytes, home_first: bool = True):
        """
        Send a pre-built GTS binary to the machine.

        Execution model (confirmed on hardware 2026-06-09): jobs QUEUE on
        receipt (one beep each) and do NOT auto-execute while connected.
        A single green press executes the entire queue in order — with
        home_first=True that is home, then the job, on ONE press.

        home_first=True is a DELIBERATE default (decided 2026-06-09): every
        job gets a known position reference. Opt out explicitly with
        home_first=False / --no-home.
        """
        if home_first:
            home = build_home_gts()
            self._log(f'Queuing home GTS ({len(home)} bytes)...')
            self.send_raw(home)
        self._log(f'Sending {len(gts)} bytes...')
        self.send_raw(gts)
        self._log('Jobs queued. ONE green press runs the full sequence.')
        self._monitor_job()

    def send_job(self, gcode_text: str,
                 units:     str   = 'mm',
                 speed:     int   = DEFAULT_SPEED,
                 depth:     int   = DEFAULT_DEPTH,
                 lift:      int   = DEFAULT_LIFT,
                 scale:     float = float(UNITS_PER_MM),
                 arc_steps: int   = 72,
                 origin:    Tuple[float, float] = (0.0, 0.0),
                 flip_y:    bool  = False,
                 skip_home: bool  = False):
        """
        Translate G-Code text and send it in one step.

        Convenience wrapper — bypasses gts_prepare's toolpath optimisation.
        Prefer gts_prepare.from_gcode() + send_gts() for production jobs.
        """
        from gts_translator import translate
        self._log('Translating G-Code...')
        gts = translate(
            gcode_text,
            units=units, speed=speed, depth=depth, lift=lift,
            scale=scale, arc_steps=arc_steps, origin=origin,
            flip_y=flip_y, wrap=False
        )
        self.send_gts(gts, home_first=not skip_home)

    def tool_up(self):
        """Retract the spindle."""
        self.send_raw(CMD_TOOL_UP)

    def abort(self):
        """Emergency stop."""
        self._log('[ABORT] Sending emergency stop (!1B 0;)')
        self.send_raw(CMD_STOP)
        responses = self.recv_all(timeout_ms=1000)
        for r in responses:
            self._log(f'  Machine: {repr(r)}')

    def jog(self, x_mm: float, y_mm: float,
             scale: float = float(UNITS_PER_MM)):
        """
        Home the machine, then rapid pen-up to (x_mm, y_mm) from home.

        Stroke header coordinates are interpreted relative to the current
        tool position, so this method always homes first — making the
        subsequent move effectively absolute from home.

        Uses the disconnect-then-execute trigger (!CM 4;), so the jog runs
        without a green button press.
        """
        xu = int(round(x_mm * scale))
        yu = int(round(y_mm * scale))

        # Build a minimal GTS: header + setup + single PU stroke + JF
        hdr = _gts_header()


        # Stroke header: abs coords = target position (xu, yu).
        # No segments follow — the machine positions to these coordinates
        # pen-up and the stroke block ends immediately at JF.
        stroke_hdr = struct.pack('>hhhhhh',
                                 PEN_W_RAMP, PEN_W_RAMP,
                                 DEFAULT_SPEED, DEFAULT_ACCEL,
                                 xu, yu)

        spl = (hdr
               + _cmd(b'DR', 0)
               + _cmd(b'MC', 0)  # MC: spindle (0=off/diamond drag, 1=rotary on)
               + _cmd(b'VS', DEFAULT_SPEED)
               + _cmd(b'VZ', DEFAULT_SPEED)
               + _cmd(b'PZ', DEFAULT_LIFT)
               + _cmd(b'DZ', DEFAULT_DEPTH)
               + stroke_hdr
               + _cmd(b'PU', 0)     # pen stays up
               + _cmd(b'PU', 1)
               # JF ends the job. Tool stays at the target position.
               + _cmd(b'JF', 0))

        home_spl = build_home_gts()
        self._log(f'Homing first...')
        self.send_raw(home_spl)
        time.sleep(0.5)   # wait for home to complete before jog
        self._log(f'Jog to ({x_mm:.1f}, {y_mm:.1f})mm')
        self.send_raw(spl)
        self.send_raw(b'!CM 4;\x00\x00\x00\x01')

    # --- Background reader ---

    def _reader_thread(self):
        """
        Continuously reads from bulk IN endpoint (EP 0x82) and dispatches packets.

        The M20 sends two packet types (confirmed from live USB capture):

          TYPE A (35 bytes): ASCII prefix "!ID 28;" then 28-byte binary struct.
            The machine echoes the command+byte-count before sending the payload.

          TYPE B (10 bytes): [6B ASCII "!MZ 4;"][4B BE uint32 value] — continuous status.
            macOS: AppleUSBPrinter kext may truncate these in some configurations.
            The boot broadcast in the 500ms eager-capture window arrives intact.

        All parsed packets are placed on self._rx_queue as dicts (motor/long) or
        plain strings (ASCII fallback).

        If self._ep_in < 0, the device has no IN endpoint and this thread
        exits immediately.
        """
        if self._ep_in < 0:
            self._log('No IN endpoint — running in TX-only mode. '
                      'Machine responses will not be captured.')
            return

        while not self._rx_stop.is_set():
            try:
                chunk = self.dev.read(
                    self._ep_in,
                    self.READ_CHUNK_SIZE,
                    timeout=self.SHORT_TIMEOUT_MS
                )
                raw = bytes(chunk)
                for t in list(self._raw_taps):
                    try: t(raw)
                    except Exception: pass
                self._rx_buf += raw
                self._parse_rx_buf()
            except Exception as e:
                err = str(e)
                # Timeout is normal — the machine is just quiet
                if 'timeout' in err.lower() or 'timed out' in err.lower():
                    continue
                # Pipe error can occur during disconnect
                if 'pipe' in err.lower() or 'no data' in err.lower():
                    continue
                # Anything else is worth noting
                if not self._rx_stop.is_set():
                    self._emit(f'[USB ERROR] {err}')

    def _parse_rx_buf(self):
        """
        Parse the RX buffer. The M20 sends two packet types (confirmed live):

          TYPE B (10 bytes): [6B ASCII "!MZ 4;"][4B BE uint32 value]
            Fixed framing — no delimiter. Detected by buf[0]=='!' buf[1]=='M' buf[5]==';'.
            Also handles !TU, !GA, and other !XX 4; response packets with the
            same 10-byte layout (buf[0]=='!' buf[5]==';').

          TYPE A (35 bytes): [7B ASCII "!ID 28;" NUL-terminated][28B binary struct]
            Wire delivery: the machine sends "!ID 28;" (7 bytes) immediately
            followed by the 28-byte binary struct as a single USB bulk transfer.
            No newline separator. Detection: buf[0:3] == b'!ID' and len >= 35.

          ASCII fallback: newline-terminated plain text.

        Parse order: TYPE B/response packets first, then TYPE A, then ASCII.
        """
        # Strip leading NUL bytes — some firmware versions prepend one
        while self._rx_buf and self._rx_buf[0] == 0x00:
            self._rx_buf = self._rx_buf[1:]

        while len(self._rx_buf) > 0:
            # --- Counted packets: "!XX N;" + N payload bytes ---
            # The digit(s) before ';' are the payload byte count (confirmed
            # live: "!ID 28;"+28B, "!MX 4;"+4B, "!JF 0;"+0B). Covers motor
            # status (!MX/MY/MZ/MN/ME), !ID identity, and command acks.
            if self._rx_buf[0] == 0x21 and self._rx_buf[3:4] == b' ':
                semi = self._rx_buf.find(b';', 4, 9)
                count_bytes = self._rx_buf[4:semi] if semi > 0 else b''
                if semi > 0 and count_bytes.isdigit():
                    count = int(count_bytes)
                    total = semi + 1 + count
                    if len(self._rx_buf) < total:
                        break   # wait for the rest of the packet
                    packet = self._rx_buf[:total]
                    self._rx_buf = self._rx_buf[total:]

                    if packet[0:3] == b'!ID' and count == LONG_HEADER_LEN:
                        self._dispatch_long_packet(packet[semi+1:])
                    elif packet[1] == 0x4D and count == 4:   # 'M' — motor status
                        self._dispatch_status_packet(packet)
                    else:
                        cmd_str = packet[:semi+1].decode('ascii', errors='replace')
                        value   = (struct.unpack_from('>I', packet, semi+1)[0]
                                   if count == 4 else None)
                        msg = (f'{cmd_str}  value=0x{value:08X}' if value is not None
                               else f'{cmd_str}  payload={packet[semi+1:].hex(" ")}'
                               if count else cmd_str)
                        self._emit({'type': 'ack', 'cmd': cmd_str,
                                    'value': value, 'text': msg,
                                    'raw': packet})
                        self._log(f'RX: {msg}')
                    continue

            # --- ASCII fallback: newline-terminated plain text ---
            nl = self._rx_buf.find(b'\n')
            if nl >= 0:
                raw_line = self._rx_buf[:nl].replace(b'\r', b'')
                self._rx_buf = self._rx_buf[nl+1:]
                if raw_line:
                    decoded = (raw_line.decode('ascii', errors='replace')
                               if all(0x20 <= b < 0x7F for b in raw_line)
                               else f'[BIN] {raw_line.hex(" ")}')
                    self._emit(decoded)
                    self._interpret_response(decoded)
                continue

            # --- TYPE A via legacy \n path (old firmware fallback) ---
            # If we reach here with \x00\x00\x00\x01 at the front, it's a
            # bare binary struct without the !ID prefix (old behaviour).
            if (len(self._rx_buf) >= LONG_HEADER_LEN
                    and self._rx_buf[:4] == b'\x00\x00\x00\x01'):
                packet = self._rx_buf[:LONG_HEADER_LEN]
                self._rx_buf = self._rx_buf[LONG_HEADER_LEN:]
                self._dispatch_long_packet(packet)
                continue

            break

    def _dispatch_status_packet(self, packet: bytes):
        """
        Decode and dispatch a 10-byte TYPE B motor status packet.

        Format (confirmed from live USB capture, raw_capture.bin):
          [6B ASCII: '!MX 4;'][4B BE uint32: value]

        value semantics:
          0           Axis is at home / idle.
          non-zero    Axis position or motion counter value.
                      Unit mapping (value → mm) not yet characterised.

        Job completion sequence:
          During job:  !MX/MY/MZ packets with value > 0 (counter running)
          Job done:    !MX/MY/MZ with value = 0, then !MN with value = 0

        Known axes: !MX X, !MY Y, !MZ Z, !ME Encoder/aux, !MN Neutral (all home)
        """
        cmd   = packet[0:3]    # b'!MX', b'!MY', b'!MZ', b'!ME', b'!MN'
        value = struct.unpack_from('>I', packet, 6)[0]   # bytes 6–9

        axis_map = {
            STATUS_MOTOR_NEUTRAL: ('N', 'Neutral'),
            STATUS_MOTOR_X:       ('X', 'X axis'),
            STATUS_MOTOR_Y:       ('Y', 'Y axis'),
            STATUS_MOTOR_Z:       ('Z', 'Z axis'),
            STATUS_MOTOR_E:       ('E', 'Encoder/aux'),
        }
        axis_char, axis_name = axis_map.get(
            cmd, ('?', f'Unknown({cmd.decode("ascii", errors="replace")})'))

        if value == 0:
            msg = f'!M{axis_char}  [{axis_name} at HOME / counter=0]'
        else:
            msg = f'!M{axis_char}  counter={value}  [{axis_name} active]'

        status = {
            'type':  'motor',
            'axis':  axis_char,
            'value': value,
            'raw':   packet,
            'text':  msg,
        }
        self._emit(status)
        self._log(f'RX: {msg}')

    def _dispatch_long_packet(self, packet: bytes):
        """
        Decode a TYPE A long packet (28 bytes fixed = 7 × int32, big-endian).

        Layout confirmed from hardware (M20 Express):
          +0   boot_ver_major   — bootloader version major
          +4   boot_ver_minor   — bootloader version minor
          +8   prog_ver_major   — program firmware version major
          +12  prog_ver_minor   — program firmware version minor
          +16  platform_type    — 3=K2000/M20 standard, 4=M20 Express (Sirius FW base)
          +20  model_code       — primary model code (see MODEL_CODE_NAMES)
          +24  model_code_alt   — secondary/alternate model code

        Live values confirmed from hardware (M20 Express):
          → boot=1.2, prog=1.30, platform_type=4, model_code=0x0204

        Wire framing confirmed from hardware:
          35-byte bulk transfer: b'!ID 28;' (7 bytes) + 28-byte binary struct.
          _parse_rx_buf detects b'!ID' at buf[0:3] and slices buf[7:35].

        The packet is always 28 bytes. Field[3] is firmware version data, not a length.
        """
        if len(packet) < LONG_HEADER_LEN:
            return
        fields        = struct.unpack_from('>7I', packet, 0)
        platform_type = fields[4]
        model_code    = fields[5]
        model_alt     = fields[6]
        boot_ver      = f'{fields[0]}.{fields[1]}'
        prog_ver      = f'{fields[2]}.{fields[3]}'

        plat_entry = MACHINE_PLATFORM_TYPES.get(platform_type, PLATFORM_IM4E)
        model_name = MODEL_CODE_NAMES.get(model_code, f'0x{model_code:04X}')

        msg = (f'!ID  platform={plat_entry[0]}  model={model_name}(0x{model_code:04X})'
               f'  boot={boot_ver}  prog={prog_ver}')
        status = {
            'type':           'long',
            'platform_type':  platform_type,
            'model_code':     model_code,
            'model_code_alt': model_alt,
            'boot_ver':       boot_ver,
            'prog_ver':       prog_ver,
            'platform':       plat_entry[0],
            'config':         platform_type,   # legacy alias kept for callers using ['config']
            'text':           msg,
        }
        self._emit(status)
        self._log(f'RX: {msg}')

        # Opportunistic identity update: a late !ID (e.g. answered after
        # identify()'s window expired — observed live 2026-06-09) still
        # corrects connection state and profile selection.
        if model_code and model_code != self.model_code:
            self.model       = plat_entry[0]
            self.config_code = platform_type
            self.model_code  = model_code
            self.boot_ver    = boot_ver
            self.prog_ver    = prog_ver
            self._auto_select_profile()

    def _interpret_response(self, line: str):
        """Log a plain ASCII response line. (Annotation tables for legacy
        ASCII model/status strings were removed 2026-06-09 — never observed
        on the USB wire; the live protocol is binary via !ID.)"""
        self._log(f'RX: {repr(line)}')

    def _monitor_job(self, timeout_s: float = 120.0):
        """
        Monitor machine responses until the job is complete.

        Uses binary motor status protocol (confirmed live):
          Running:  !MX/MY/MZ packets with value > 0
          Complete: all active axes report 0, then !MN heartbeat
        Fallback: 5s silence after last non-heartbeat packet.
        """
        deadline         = time.monotonic() + timeout_s
        last_motion_time = time.monotonic()
        silence_timeout  = 5.0
        axes_seen_moving: set = set()
        axes_at_home:     set = set()
        jf_acks          = 0

        self._log('Monitoring job — watching for axis motion...')

        while time.monotonic() < deadline:
            item = self.recv(timeout_ms=500)
            if item is None:
                silent_for = time.monotonic() - last_motion_time
                if silent_for >= silence_timeout and axes_seen_moving:
                    self._log(f'Job complete ({silent_for:.1f}s silence after motion).')
                    return
                continue

            if isinstance(item, dict):
                if item['type'] == 'ack' and item.get('cmd', '').startswith('!JF'):
                    # !JF is a job RECEIPT acknowledgement — emitted ~2s after
                    # a job is queued, BEFORE any execution (confirmed on
                    # hardware 2026-06-09). It is NOT a completion signal;
                    # completion is detected from axis telemetry below.
                    jf_acks += 1
                    self._log(f'Machine acknowledged job receipt '
                              f'(!JF #{jf_acks}). Waiting for execution...')
                    continue
                if item['type'] == 'motor':
                    axis, value = item['axis'], item['value']
                    if axis == 'E':
                        # !ME accompanies job receipt and is not axis motion;
                        # counting it as motion falsely armed the silence-based
                        # completion heuristic (live 2026-06-09).
                        continue
                    if axis == 'N':
                        if axes_seen_moving and axes_at_home >= axes_seen_moving:
                            self._log('Job complete (all axes returned home).')
                            return
                        continue
                    last_motion_time = time.monotonic()
                    if value > 0:
                        axes_seen_moving.add(axis)
                        axes_at_home.discard(axis)
                    elif axis in axes_seen_moving:
                        axes_at_home.add(axis)
                        self._log(f'  Axis {axis} at home (pos=0.00mm).')
                elif item['type'] == 'long':
                    suffix = item.get('suffix', '').upper()
                    if 'ERROR' in suffix or 'STOP' in suffix:
                        self._log(f'[WARN] Job interrupted: {suffix}')
                        return
                    last_motion_time = time.monotonic()
            else:
                upper = str(item).strip().upper()
                if upper in ('READY', 'OK'):
                    self._log('Job complete (READY/OK).')
                    return
                if upper in ('ERROR', 'STOP'):
                    self._log(f'[WARN] Job interrupted: {upper}')
                    return
                last_motion_time = time.monotonic()

        self._log('[WARN] Job monitoring timed out.')

    def _log(self, msg: str):
        if self.verbose:
            ts = time.strftime('%H:%M:%S')
            print(f'[{ts}] {msg}')


# ---------------------------------------------------------------------------
# Interactive Console
# ---------------------------------------------------------------------------
