"""tests/test_tray.py — TrayController logic (headless, no pystray/display)."""
import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), 'tray'))

import gts_server
from gts_tray import TrayController


def _ctrl(tmp_token='tok-abc'):
    state = gts_server.ServerState(connection_factory=lambda: None,
                                   auth_max_failures=3, auth_lockout_s=300)
    return TrayController(state, tmp_token, port=8020), state


def test_pairing_code_shown():
    c, _ = _ctrl('pair-123')
    assert c.pairing_code() == 'pair-123'


def test_machine_status_not_found():
    c, _ = _ctrl()
    assert c.machine_status() == 'Machine: not found'


def test_machine_status_connected():
    c, state = _ctrl()
    class FakeConn:
        dev = object(); model = 'M20'; model_code = 0x0204
    state.conn = FakeConn()
    s = c.machine_status()
    assert 'M20' in s and '0x0204' in s


def test_version_lines_cover_all_components():
    c, _ = _ctrl()
    lines = c.version_lines()
    joined = '\n'.join(lines)
    for comp in ('gts_api', 'gts_translator', 'gts_optimise',
                 'gts_prepare', 'gts_preview', 'gts_server'):
        assert comp in joined, f'{comp} missing from version report'
    assert 'python:' in joined and 'os:' in joined


def test_support_snapshot_is_one_paste():
    c, _ = _ctrl()
    snap = c.support_snapshot()
    assert 'GratisGraph support snapshot' in snap
    assert 'gts_api=' in snap and 'python=' in snap


def test_lockout_reporting_and_unlock():
    c, state = _ctrl()
    assert not c.is_locked()
    # trip the lockout
    for _ in range(3):
        state.auth_failed()
    assert c.is_locked()
    assert 'Locked out' in c.lockout_label()
    # unlock clears it (proof of physical presence)
    c.unlock()
    assert not c.is_locked()


def test_regenerate_token_changes_and_persists(tmp_path, monkeypatch):
    monkeypatch.setattr(gts_server, 'TOKEN_PATH', tmp_path / 'token')
    c, _ = _ctrl('original')
    new = c.regenerate_token()
    assert new != 'original' and len(new) > 10
    assert c.pairing_code() == new
    assert (tmp_path / 'token').read_text().strip() == new
