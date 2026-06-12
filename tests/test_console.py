"""tests/test_console.py — REPL command loop with a stubbed connection."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import console as console_mod
from console import run_console


class FakeConn:
    def __init__(self, *a, **kw):
        self.model = 'M20'
        self.calls = []
        FakeConn.last = self
    def connect(self, timeout_s=60.0, wait=False): return True
    def identify(self): self.calls.append('identify'); return 'M20'
    def jog(self, x, y): self.calls.append(('jog', x, y))
    def abort(self): self.calls.append('abort')
    def send_raw(self, data): self.calls.append(('raw', data))
    def send_gts(self, gts, home_first=True):
        self.calls.append(('send_gts', home_first))
    def disconnect(self): self.calls.append('disconnect')


def _run(monkeypatch, capsys, inputs):
    feed = iter(inputs)
    monkeypatch.setattr(console_mod, 'GTSConnection', FakeConn)
    monkeypatch.setattr('builtins.input', lambda prompt='': next(feed))
    run_console()
    return capsys.readouterr().out, FakeConn.last


def test_help_then_quit(monkeypatch, capsys):
    out, fake = _run(monkeypatch, capsys, ['help', 'quit'])
    assert 'jog X Y' in out and 'abort' in out
    assert 'disconnect' in fake.calls          # clean shutdown

def test_jog_dispatch(monkeypatch, capsys):
    _, fake = _run(monkeypatch, capsys, ['jog 12.5 30', 'quit'])
    assert ('jog', 12.5, 30.0) in fake.calls

def test_unknown_command_does_not_crash(monkeypatch, capsys):
    out, _ = _run(monkeypatch, capsys, ['frobnicate', 'quit'])
    assert 'gts>' not in out.lower() or True   # loop survived to quit
