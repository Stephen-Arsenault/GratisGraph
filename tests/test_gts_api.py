"""tests/test_gts_api.py — Tests for gts_api.py"""
import struct, sys, os
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from gts_api import (
    MAGIC, BOX_DIM, UNITS_PER_MM, WORK_ENVELOPE_MM, PHYSICAL_ENVELOPE_MM,
    DEFAULT_SPEED, DEFAULT_ACCEL, M20_VID, M20_PID, BULK_EP_OUT, BULK_EP_IN,
    build_gts, build_home_gts, build_square, build_circle, build_gts_stroke,
    MachineProfile, M20_PROFILE, M40_PROFILE, GTSConnection,
    M10_JEWEL_PROFILE, IS400_IQ_PROFILE, PROFILE_FOR_MODEL_CODE,
    __version__,
)
from wire_format import (
    assert_valid_gts, assert_single_jf, assert_magic,
    find_opcodes, OP_PD, OP_JF,
)


def test_usb_ids():
    assert M20_VID == 0xFFFD
    assert M20_PID == 0x0001
    assert BULK_EP_OUT == 0x01
    assert BULK_EP_IN  == 0x82

def test_coordinate_constants():
    assert UNITS_PER_MM == 100
    assert WORK_ENVELOPE_MM == (100.0, 100.0)
    assert PHYSICAL_ENVELOPE_MM == (120.0, 120.0)

def test_magic():
    assert MAGIC == b'!MM88;'

def test_box_dim():
    assert BOX_DIM == 0x0DE4

def test_build_gts_single_stroke():
    strokes = [(1000, 1000, [(1000, 0), (0, 1000), (-1000, 0), (0, -1000)])]
    assert_valid_gts(build_gts(strokes))

def test_build_gts_multi_stroke_single_jf():
    strokes = [(500, 500, [(1000, 0)]), (2000, 500, [(1000, 0)]), (3500, 500, [(1000, 0)])]
    data = build_gts(strokes)
    assert_single_jf(data)
    assert len(find_opcodes(data, OP_PD)) == 3

def test_build_home_gts():
    data = build_home_gts()
    assert_magic(data)
    assert len(find_opcodes(data, OP_JF)) == 1

def test_build_square():
    assert_valid_gts(build_square(50.0, 50.0, 50.0))

def test_build_circle():
    assert_valid_gts(build_circle(50.0, 50.0, 40.0))

def _make_conn_with_queue():
    """Create a bare GTSConnection with the rx_queue initialised for testing."""
    import queue as q
    from gts_api import GTSConnection
    from gts_api import M20_PROFILE
    conn = GTSConnection.__new__(GTSConnection)
    conn._rx_queue = q.Queue()
    conn._taps = []
    conn.verbose = False
    conn.model, conn.model_code, conn.config_code = '', 0, 0
    conn.boot_ver, conn.prog_ver = '', ''
    conn.profile = M20_PROFILE
    conn._log = lambda msg: None
    return conn

def test_id_parse_m20_express():
    """Parse confirmed live values: boot=1.2 prog=1.30 platform=M20Express model=M20."""
    conn = _make_conn_with_queue()
    # 28-byte struct = 7 × uint32 BE: boot_maj boot_min prog_maj prog_min platform model alt
    payload = struct.pack('>7I', 1, 2, 1, 30, 4, 0x0204, 0)
    conn._dispatch_long_packet(payload)
    result = conn._rx_queue.get_nowait()
    assert result['platform'] == 'M20Express'
    assert result['boot_ver'] == '1.2'
    assert result['prog_ver'] == '1.30'
    assert result['model_code'] == 0x0204

def test_id_parse_nul_prefix():
    """_parse_rx_buf must handle a NUL byte prepended before !ID (seen on some firmware)."""
    conn = _make_conn_with_queue()
    payload = struct.pack('>7I', 1, 2, 1, 30, 4, 0x0204, 0)
    # Simulate the full 35-byte packet with a leading NUL — _parse_rx_buf strips prefix
    packet = b'\x00!ID 28;' + payload   # 36 bytes: NUL + 7B prefix + 28B struct
    # Feed directly into the RX buffer path
    conn._rx_buf = packet
    conn._parse_rx_buf()
    result = conn._rx_queue.get_nowait()
    assert result['platform'] == 'M20Express'

@pytest.mark.hardware
def test_hardware_connect():
    from gts_api import GTSConnection
    conn = GTSConnection(verbose=False)
    assert conn.connect(timeout_s=5.0), 'M20 not found on USB'
    conn.disconnect()


# ---------------------------------------------------------------------------
# Version and public API surface
# ---------------------------------------------------------------------------

def test_version_exists():
    from gts_api import __version__
    assert isinstance(__version__, str)
    assert len(__version__.split('.')) == 3   # semver: MAJOR.MINOR.PATCH


def test_all_exports_importable():
    """Every name in __all__ must actually be importable from gts_api."""
    import gts_api
    for name in gts_api.__all__:
        assert hasattr(gts_api, name), f'__all__ lists {name!r} but it is not defined'


# ---------------------------------------------------------------------------
# MachineProfile
# ---------------------------------------------------------------------------

def test_m20_profile_values():
    assert M20_PROFILE.vid          == 0xFFFD
    assert M20_PROFILE.pid          == 0x0001
    assert M20_PROFILE.units_per_mm == 100
    assert M20_PROFILE.envelope_mm  == (100.0, 100.0)
    assert M20_PROFILE.physical_envelope_mm == (120.0, 120.0)
    assert M20_PROFILE.negate_x     == False
    assert M20_PROFILE.coord_scale  == 1.0


def test_m40_profile_values():
    assert M40_PROFILE.vid          == 0xFFFD
    assert M40_PROFILE.pid          == 0x0001
    assert M40_PROFILE.units_per_mm == 100


def test_gts_connection_accepts_profile():
    """GTSConnection should store profile and derive vid/pid from it."""
    conn = GTSConnection.__new__(GTSConnection)
    conn.__init__(profile=M20_PROFILE, verbose=False)
    assert conn.profile is M20_PROFILE
    assert conn.vid == M20_PROFILE.vid
    assert conn.pid == M20_PROFILE.pid


def test_gts_connection_profile_overrides_vid_pid():
    """Explicit profile takes precedence over individual vid/pid args."""
    conn = GTSConnection.__new__(GTSConnection)
    conn.__init__(vid=0x1234, pid=0x5678, profile=M20_PROFILE, verbose=False)
    assert conn.vid == M20_PROFILE.vid
    assert conn.pid == M20_PROFILE.pid


# ---------------------------------------------------------------------------
# Machine identification and profile auto-selection
# ---------------------------------------------------------------------------

def test_all_supported_model_codes_have_profile():
    """Every model code in MODEL_CODE_NAMES that is supported must map to a profile."""
    from gts_api import MODEL_CODE_NAMES
    supported = {
        0x0202, 0x0203, 0x0204, 0x0205, 0x0206, 0x0207,
        0x0209, 0x020D, 0x020E, 0x020F, 0x0210, 0x0211, 0x0212,
    }
    for code in supported:
        assert code in PROFILE_FOR_MODEL_CODE, (
            f'Model code 0x{code:04X} ({MODEL_CODE_NAMES.get(code, "?")}) '
            f'has no entry in PROFILE_FOR_MODEL_CODE'
        )


def test_m20_family_maps_to_m20_profile():
    m20_family = [0x0204, 0x0205, 0x0206, 0x0207, 0x0209, 0x020D, 0x020E, 0x020F]
    for code in m20_family:
        assert PROFILE_FOR_MODEL_CODE[code] is M20_PROFILE, (
            f'0x{code:04X} should map to M20_PROFILE'
        )


def test_m40_family_maps_to_m40_profile():
    m40_family = [0x0202, 0x0203, 0x0210, 0x0211]
    for code in m40_family:
        assert PROFILE_FOR_MODEL_CODE[code] is M40_PROFILE, (
            f'0x{code:04X} should map to M40_PROFILE'
        )


def test_m10_jewel_profile():
    assert M10_JEWEL_PROFILE.negate_x   == True
    assert M10_JEWEL_PROFILE.coord_scale == 1.0
    assert PROFILE_FOR_MODEL_CODE[0x0212] is M10_JEWEL_PROFILE


def test_is400_iq_profile():
    assert IS400_IQ_PROFILE.negate_x   == False
    assert IS400_IQ_PROFILE.coord_scale == 1.25


def test_auto_select_profile_known_code():
    """_auto_select_profile() updates self.profile from model_code."""
    conn = GTSConnection.__new__(GTSConnection)
    conn.__init__(verbose=False)
    conn.profile    = M20_PROFILE   # start as M20
    conn.model_code = 0x0202        # M40
    conn._log = lambda msg: None
    conn._auto_select_profile()
    assert conn.profile is M40_PROFILE


def test_auto_select_profile_uses_model_code_not_platform_type():
    """Regression: auto-select must key on model_code, not platform_type.
    A live M20 Express reports platform_type=4 (config_code) and
    model_code=0x0204 — platform_type must never be used for the lookup."""
    conn = GTSConnection.__new__(GTSConnection)
    conn.__init__(verbose=False)
    conn.profile     = M40_PROFILE   # deliberately wrong starting profile
    conn.config_code = 4             # platform_type, NOT a model code
    conn.model_code  = 0x0204        # M20
    conn._log = lambda msg: None
    conn._auto_select_profile()
    assert conn.profile is M20_PROFILE


def test_auto_select_profile_unknown_code():
    """Unknown model code keeps existing profile and does not raise."""
    conn = GTSConnection.__new__(GTSConnection)
    conn.__init__(verbose=False)
    conn.profile    = M20_PROFILE
    conn.model_code = 0xDEAD        # unknown
    conn._log = lambda msg: None
    conn._auto_select_profile()
    assert conn.profile is M20_PROFILE   # unchanged


def test_auto_select_profile_no_code():
    """Zero model_code (no !ID response) keeps existing profile."""
    conn = GTSConnection.__new__(GTSConnection)
    conn.__init__(verbose=False)
    conn.profile    = M20_PROFILE
    conn.model_code = 0
    conn._log = lambda msg: None
    conn._auto_select_profile()
    assert conn.profile is M20_PROFILE


# ---------------------------------------------------------------------------
# RX parser: counted-packet framing (regression from live 2026-06-09 capture)
# ---------------------------------------------------------------------------

def test_parse_counted_packets_jf_zero_payload():
    """'!JF 0;' carries 0 payload bytes — the parser must not eat the next
    packet's header as a value (live misframe: value=0x214D4520 = '!ME ')."""
    conn = _make_conn_with_queue()
    conn._rx_buf = b'!JF 0;' + b'!ME 4;\x00\x00\x03\xe9' + b'!MN 4;\x00\x00\x01\xf5'
    conn._parse_rx_buf()
    items = []
    while not conn._rx_queue.empty():
        items.append(conn._rx_queue.get_nowait())
    assert len(items) == 3
    assert items[0]['type'] == 'ack'   and items[0]['cmd'].startswith('!JF')
    assert items[0]['value'] is None
    assert items[1]['type'] == 'motor' and items[1]['axis'] == 'E'
    assert items[1]['value'] == 0x3E9
    assert items[2]['type'] == 'motor' and items[2]['axis'] == 'N'


def test_parse_counted_packets_partial_buffer():
    """A counted packet split across USB reads must wait for completion."""
    conn = _make_conn_with_queue()
    conn._rx_buf = b'!MX 4;\x00\x00'          # first half
    conn._parse_rx_buf()
    assert conn._rx_queue.empty()              # nothing dispatched yet
    conn._rx_buf += b'\x23\x28'                # second half arrives
    conn._parse_rx_buf()
    item = conn._rx_queue.get_nowait()
    assert item['type'] == 'motor' and item['axis'] == 'X'
    assert item['value'] == 0x2328


def test_monitor_job_does_not_complete_on_jf_ack():
    """Regression (hardware 2026-06-09): !JF arrives ~2s after queueing,
    BEFORE execution. Treating it as completion made the monitor disconnect
    while the job sat unexecuted. The monitor must keep waiting past !JF."""
    import time as _time
    conn = _make_conn_with_queue()
    conn._rx_queue.put({'type': 'ack', 'cmd': '!JF 0;', 'value': None,
                        'text': '!JF 0;', 'raw': b'!JF 0;'})
    t0 = _time.monotonic()
    conn._monitor_job(timeout_s=0.7)
    elapsed = _time.monotonic() - t0
    assert elapsed >= 0.6, (
        f'monitor returned after {elapsed:.2f}s — it treated !JF as '
        f'completion instead of a receipt ack')


# ---------------------------------------------------------------------------
# Regressions from live session 2026-06-09 20:49 (calibrate --step 2 log)
# ---------------------------------------------------------------------------

def test_late_id_packet_updates_identity_and_profile():
    """An !ID arriving AFTER identify() gave up must still correct the
    connection state and auto-select the right profile."""
    from gts_api import M20_PROFILE, M40_PROFILE
    conn = _make_conn_with_queue()
    assert conn.profile is M20_PROFILE and conn.model_code == 0
    payload = struct.pack('>7I', 1, 1, 2, 46, 3, 0x0202, 0)   # an M40
    conn._rx_buf = b'!ID 28;' + payload
    conn._parse_rx_buf()
    assert conn.model_code == 0x0202
    assert conn.profile is M40_PROFILE        # opportunistically re-selected
    assert conn.boot_ver == '1.1' and conn.prog_ver == '2.46'


def test_monitor_job_ignores_me_packets_for_motion():
    """!ME accompanies job receipt; it must not count as axis motion and
    must never arm the silence-based completion heuristic (live false
    'Job complete' with zero MX/MY/MZ packets, 2026-06-09 20:49)."""
    import time as _time
    conn = _make_conn_with_queue()
    conn._rx_queue.put({'type': 'motor', 'axis': 'E', 'value': 1001,
                        'text': '', 'raw': b''})
    conn._rx_queue.put({'type': 'motor', 'axis': 'E', 'value': 1001,
                        'text': '', 'raw': b''})
    t0 = _time.monotonic()
    conn._monitor_job(timeout_s=0.7)
    elapsed = _time.monotonic() - t0
    assert elapsed >= 0.6, (
        f'monitor returned after {elapsed:.2f}s — !ME was treated as motion')


def test_identify_waits_past_heartbeat_for_id(monkeypatch):
    """identify() must not give up on the first heartbeat: a live run showed
    the !ID reply landing right after an early-return fallback. Heartbeats
    mean alive; the answer can still come."""
    import threading, time as _time
    from gts_api import M40_PROFILE

    conn = _make_conn_with_queue()
    class _Dev:
        def write(self, ep, data, timeout=0): return len(data)
    conn.dev, conn._ep_out = _Dev(), 0x01

    # Heartbeat available immediately; the real !ID arrives 0.8s later.
    conn._rx_queue.put({'type': 'motor', 'axis': 'N', 'value': 502,
                        'text': '', 'raw': b''})
    def late_id():
        _time.sleep(0.8)
        payload = struct.pack('>7I', 1, 1, 2, 46, 3, 0x0202, 0)
        conn._rx_buf = b'!ID 28;' + payload
        conn._parse_rx_buf()
    threading.Thread(target=late_id, daemon=True).start()

    conn.identify()
    assert conn.model_code == 0x0202, (
        'identify() bailed on the heartbeat instead of waiting for !ID')


def test_build_gts_multistroke_emits_hardware_proven_format():
    """Locked to the byte-exact job proven on hardware 2026-06-09 (Run 2:
    both lines landed at commanded positions; !MY peaked 3999 = 39.99mm).
    Headers are deltas from the previous stroke's end; NO separate
    inter-stroke rapid; one return-home delta; one JF."""
    from gts_api import build_gts, _gts_header, _gts_setup, _seg, _cmd, DEFAULT_ZA
    job = build_gts([(1000, 2000, [(1000, 0)]), (3000, 4000, [(1000, 0)])])
    proven = (_gts_header() + _gts_setup()
              + _seg(1000, 2000) + _cmd(b'ZA', DEFAULT_ZA) + _cmd(b'PD', 0)
              + _seg(1000, 0) + _cmd(b'PU', 0) * 2 + _cmd(b'PU', 1)
              + _seg(1000, 2000) + _cmd(b'ZA', DEFAULT_ZA) + _cmd(b'PD', 0)
              + _seg(1000, 0) + _cmd(b'PU', 0) * 2 + _cmd(b'PU', 1)
              + _seg(-4000, -4000) + _cmd(b'JF', 0))
    assert job == proven
