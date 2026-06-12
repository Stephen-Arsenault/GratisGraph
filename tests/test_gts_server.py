"""tests/test_gts_server.py — HTTP-level tests for gts_server.py (no hardware).

Starts a real ThreadingHTTPServer on an ephemeral port with a stubbed
connection factory, then exercises it over actual HTTP.
"""
import json
import sys, os, time, threading
import http.client
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from gts_api import M20_PROFILE, build_home_gts
from gts_server import ServerState, make_server

TOKEN  = 'test-token-123'
ORIGIN = 'https://ui.example'


class FakeConn:
    """Stands in for GTSConnection: records calls, never touches USB."""
    def __init__(self):
        self.model, self.model_code, self.config_code = 'M20', 0x0204, 4
        self.boot_ver, self.prog_ver = '1.2', '1.30'
        self.profile = M20_PROFILE
        self.dev = object()          # truthy = "connected"
        self.taps, self.sent = [], []
        self.aborted = False
    def connect(self, timeout_s=5.0, wait=False): return True
    def add_tap(self, fn): self.taps.append(fn)
    def send_gts(self, gts, home_first=True): self.sent.append((gts, home_first))
    def abort(self): self.aborted = True
    def disconnect(self): self.dev = None


@pytest.fixture()
def server():
    fake  = FakeConn()
    state = ServerState(connection_factory=lambda: fake)
    httpd = make_server(port=0, token=TOKEN, allowed_origins=[ORIGIN], state=state)
    port  = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    yield {'port': port, 'fake': fake, 'state': state}
    httpd.shutdown(); httpd.server_close()


def _req(port, method, path, body=None, headers=None):
    c = http.client.HTTPConnection('127.0.0.1', port, timeout=5)
    c.request(method, path, body=body, headers=headers or {})
    r = c.getresponse()
    data = r.read()
    c.close()
    return r.status, dict(r.getheaders()), (json.loads(data) if data else {})


AUTH = {'Authorization': f'Bearer {TOKEN}'}


def test_root_is_unauthenticated(server):
    status, _, body = _req(server['port'], 'GET', '/')
    assert status == 200 and body['server'] == 'gratisgraph'

def test_missing_token_is_401(server):
    status, _, _ = _req(server['port'], 'GET', '/machine')
    assert status == 401

def test_wrong_token_is_401(server):
    status, _, _ = _req(server['port'], 'GET', '/machine',
                        headers={'Authorization': 'Bearer nope'})
    assert status == 401

def test_machine_identity(server):
    status, _, body = _req(server['port'], 'GET', '/machine', headers=AUTH)
    assert status == 200
    assert body['model_code'] == '0x0204'
    assert body['profile']['name'] == 'M20'
    assert body['profile']['envelope_mm'] == [100.0, 100.0]

def test_query_param_token_accepted(server):
    status, _, _ = _req(server['port'], 'GET', f'/machine?token={TOKEN}')
    assert status == 200

def test_job_submission_and_state(server):
    gts = build_home_gts()
    status, _, body = _req(server['port'], 'POST', '/job?home_first=0',
                           body=gts, headers=AUTH)
    assert status == 202
    job_id = body['job_id']
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        _, _, job = _req(server['port'], 'GET', f'/job/{job_id}', headers=AUTH)
        if job['state'] == 'done': break
        time.sleep(0.02)
    assert job['state'] == 'done'
    assert server['fake'].sent == [(gts, False)]   # home_first=0 honoured

def test_job_rejects_non_gts(server):
    status, _, body = _req(server['port'], 'POST', '/job',
                           body=b'G1 X10 Y10', headers=AUTH)
    assert status == 400 and 'magic' in body['error']

def test_abort(server):
    status, _, _ = _req(server['port'], 'POST', '/abort', headers=AUTH)
    assert status == 200 and server['fake'].aborted

def test_cors_preflight_allowed_origin(server):
    status, headers, _ = _req(server['port'], 'OPTIONS', '/job',
                              headers={'Origin': ORIGIN})
    assert status == 204
    assert headers.get('Access-Control-Allow-Origin') == ORIGIN

def test_cors_preflight_unknown_origin_is_403(server):
    status, _, _ = _req(server['port'], 'OPTIONS', '/job',
                        headers={'Origin': 'https://evil.example'})
    assert status == 403

def test_no_cors_headers_without_allowlisted_origin(server):
    _, headers, _ = _req(server['port'], 'GET', '/machine', headers=AUTH)
    assert 'Access-Control-Allow-Origin' not in headers

def test_telemetry_sse_stream(server):
    c = http.client.HTTPConnection('127.0.0.1', server['port'], timeout=5)
    c.request('GET', f'/telemetry?token={TOKEN}')
    r = c.getresponse()
    assert r.status == 200
    assert r.getheader('Content-Type') == 'text/event-stream'
    # Wait until the SSE handler thread has actually subscribed — waiting on
    # the connection tap is not enough (subscribe() happens after it), and
    # broadcasting before subscription drops the event → read timeout (flake).
    deadline = time.monotonic() + 2
    while not server['state']._subs and time.monotonic() < deadline:
        time.sleep(0.01)
    assert server['state']._subs, 'SSE handler never subscribed'
    server['state']._broadcast({'type': 'motor', 'axis': 'X',
                                'value': 1234, 'raw': b'\x21\x4d'})
    line = b''
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        line = r.fp.readline()
        if line.startswith(b'data: '): break
    event = json.loads(line[len(b'data: '):])
    assert event['axis'] == 'X' and event['value'] == 1234
    assert event['raw'] == '21 4d'   # bytes made JSON-safe
    c.close()


# ---------------------------------------------------------------------------
# Auth lockout (global — loopback has no per-client identity to blocklist)
# ---------------------------------------------------------------------------

@pytest.fixture()
def lockout_server():
    fake  = FakeConn()
    state = ServerState(connection_factory=lambda: fake,
                        auth_max_failures=3,      # small for test speed
                        auth_lockout_s=0.5,
                        auth_fail_delay_s=0.0)    # no tarpit in tests
    httpd = make_server(port=0, token=TOKEN, allowed_origins=[ORIGIN], state=state)
    port  = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    yield {'port': port, 'state': state}
    httpd.shutdown(); httpd.server_close()


def test_lockout_after_max_failures(lockout_server):
    port = lockout_server['port']
    bad = {'Authorization': 'Bearer wrong'}
    for _ in range(3):
        status, _, _ = _req(port, 'GET', '/machine', headers=bad)
        assert status == 401
    # Locked: even the CORRECT token is refused with 429 + Retry-After
    status, headers, body = _req(port, 'GET', '/machine', headers=AUTH)
    assert status == 429
    assert int(headers['Retry-After']) >= 1
    # Lockout expires -> correct token works again
    time.sleep(0.6)
    status, _, _ = _req(port, 'GET', '/machine', headers=AUTH)
    assert status == 200


def test_success_resets_failure_counter(lockout_server):
    port = lockout_server['port']
    bad = {'Authorization': 'Bearer wrong'}
    for _ in range(2):                       # 2 of 3 strikes
        _req(port, 'GET', '/machine', headers=bad)
    status, _, _ = _req(port, 'GET', '/machine', headers=AUTH)
    assert status == 200                     # success resets the counter
    for _ in range(2):                       # 2 more strikes: still not locked
        status, _, _ = _req(port, 'GET', '/machine', headers=bad)
        assert status == 401                 # 401, not 429


def test_root_liveness_unaffected_by_lockout(lockout_server):
    port = lockout_server['port']
    for _ in range(3):
        _req(port, 'GET', '/machine', headers={'Authorization': 'Bearer wrong'})
    status, _, body = _req(port, 'GET', '/')
    assert status == 200 and body['server'] == 'gratisgraph'
