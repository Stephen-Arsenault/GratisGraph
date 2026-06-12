"""
tests/wire_format.py — GT Smartstream wire format constants and assertion helpers.

Single authoritative source of truth for the GTS binary protocol.
All test files import from here. When the wire format changes, change it here.
"""

import struct
from typing import List, Tuple

MAGIC           = b'!MM88;'
HEADER_LEN      = 94
BOX_DIM         = 0x0DE4
ACCEL_SENTINEL  = 551
DEFAULT_ZA      = 1          # confirmed from 00013.SPL / 00018.SPL reference captures

OP_DR   = b'DR'
OP_MC   = b'MC'
OP_VS   = b'VS'
OP_VZ   = b'VZ'
OP_PZ   = b'PZ'
OP_DZ   = b'DZ'
OP_ZA   = b'ZA'
OP_PD   = b'PD'
OP_PU   = b'PU'
OP_JF   = b'JF'
OP_NR   = b'NR'   # NOT used — confirmed absent from all captured jobs

PREFIX  = b'\xFF\xFD'


def find_opcodes(data: bytes, opcode: bytes) -> List[int]:
    marker = PREFIX + opcode
    offsets, pos = [], 0
    while True:
        idx = data.find(marker, pos)
        if idx == -1: break
        offsets.append(idx)
        pos = idx + 1
    return offsets



def find_segments(data: bytes, start: int = HEADER_LEN) -> List[Tuple[int, Tuple]]:
    segments, pos = [], start
    while pos + 12 <= len(data):
        if data[pos:pos+2] == PREFIX:
            pos += 6
            continue
        vals = struct.unpack_from('>hhhhhh', data, pos)
        if vals[3] == ACCEL_SENTINEL:
            segments.append((pos, vals))
            pos += 12
        else:
            pos += 1
    return segments


def assert_magic(data: bytes):
    assert data[:6] == MAGIC, f'Expected magic {MAGIC!r}, got {data[:6]!r}'

def assert_header_len(data: bytes):
    assert len(data) >= HEADER_LEN, f'Data too short: {len(data)} bytes'

def assert_setup_commands(data: bytes):
    for op in [OP_DR, OP_MC, OP_VS, OP_VZ, OP_PZ, OP_DZ]:
        assert find_opcodes(data, op), f'Setup command {op!r} not found'

def assert_za_before_pd(data: bytes):
    za_offsets = find_opcodes(data, OP_ZA)
    pd_offsets = find_opcodes(data, OP_PD)
    assert pd_offsets, 'No PD commands found'
    assert za_offsets, 'No ZA commands found'
    for pd_off in pd_offsets:
        preceding_za = [z for z in za_offsets if z < pd_off]
        assert preceding_za, f'PD at {pd_off:#06x} has no preceding ZA'
        assert pd_off - max(preceding_za) <= 18, 'ZA not immediately before PD'

def assert_single_jf(data: bytes):
    jf = find_opcodes(data, OP_JF)
    assert len(jf) == 1, f'Expected exactly 1 JF, found {len(jf)}: {jf}'

def assert_no_nr(data: bytes):
    nr = find_opcodes(data, OP_NR)
    assert not nr, f'NR opcode found at {nr} — NR is not valid in GTS format'

def assert_valid_segments(data: bytes):
    for offset, (pw1, pw2, speed, accel, dx, dy) in find_segments(data):
        assert accel == ACCEL_SENTINEL, \
            f'Segment at {offset:#06x}: accel={accel} expected {ACCEL_SENTINEL}'

def assert_valid_gts(data: bytes):
    """Run all wire format assertions on a GTS binary."""
    assert_magic(data)
    assert_header_len(data)
    assert_setup_commands(data)
    assert_za_before_pd(data)
    assert_single_jf(data)
    assert_no_nr(data)
    assert_valid_segments(data)


def parse_gts_fields(data: bytes) -> dict:
    """
    Parse a GTS binary into a structured dict of meaningful protocol fields.

    Used for regression testing — compare our output against Gravostyle
    reference captures field-by-field, ignoring header metadata that is
    only relevant to the Windows spooler stack.

    Returns a dict with keys:
        setup       — dict of setup command values {opcode: arg}
        strokes     — list of stroke dicts, each with:
                        start   (dx, dy) — stroke header segment
                        za      int      — ZA arg
                        segs    list of (dx, dy) tuples
                        pu      list of PU args
                        ret     (dx, dy) — return segment
        jf_count    int — number of JF commands (must be 1)
    """
    setup = {}
    strokes = []
    jf_count = 0
    pos = 0

    while pos < len(data):
        if data[pos:pos+2] == PREFIX and pos+6 <= len(data):
            op  = data[pos+2:pos+4]
            arg = struct.unpack_from('>h', data, pos+4)[0]
            op_s = op.decode('ascii', errors='replace')

            if op_s in ('DR', 'MC', 'VS', 'VZ', 'PZ', 'DZ'):
                setup[op_s] = arg
                pos += 6
                continue

            if op_s == 'ZA':
                # ZA marks the start of a new stroke — stroke header precedes it
                hdr_pos = pos - 12
                if hdr_pos >= 0:
                    vals = struct.unpack_from('>hhhhhh', data, hdr_pos)
                    if vals[3] == ACCEL_SENTINEL:
                        stroke = {
                            'start': (vals[4], vals[5]),
                            'za':    arg,
                            'segs':  [],
                            'pu':    [],
                            'ret':   None,
                        }
                        strokes.append(stroke)
                pos += 6
                continue

            if op_s == 'PD':
                pos += 6
                # Collect segments until next FF FD
                while pos + 12 <= len(data):
                    if data[pos:pos+2] == PREFIX:
                        break
                    vals = struct.unpack_from('>hhhhhh', data, pos)
                    if vals[3] == ACCEL_SENTINEL and strokes:
                        strokes[-1]['segs'].append((vals[4], vals[5]))
                        pos += 12
                    else:
                        break
                continue

            if op_s == 'PU':
                if strokes:
                    strokes[-1]['pu'].append(arg)
                pos += 6
                continue

            if op_s == 'JF':
                jf_count += 1
                # The segment immediately before JF is the return block
                ret_pos = pos - 12
                if ret_pos >= 0 and strokes:
                    vals = struct.unpack_from('>hhhhhh', data, ret_pos)
                    if vals[3] == ACCEL_SENTINEL:
                        strokes[-1]['ret'] = (vals[4], vals[5])
                pos += 6
                continue

            pos += 6
        else:
            pos += 1

    return {
        'setup':    setup,
        'strokes':  strokes,
        'jf_count': jf_count,
    }
