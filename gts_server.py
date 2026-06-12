#!/usr/bin/env python3
"""
gts_server.py — Local HTTP bridge between a browser UI and a GTS machine.

The browser does all translation client-side and POSTs finished GTS binary
here; this server only moves bytes to USB and streams telemetry back.

Design notes:
  - Stdlib only (http.server) — nothing to install, small attack surface,
    trivially freezable with PyInstaller.
  - Telemetry is Server-Sent Events, not WebSocket: the stream is one-way,
    EventSource gives browsers auto-reconnect for free, and stdlib can serve
    it. EventSource cannot set headers, so /telemetry also accepts ?token=.

Security model (a localhost daemon that drives motors is a drive-by target):
  - Binds 127.0.0.1 ONLY. Not configurable.
  - Every endpoint except GET / requires the pairing token, printed at startup
    and stored at ~/.gratisgraph/token (chmod 600).
    Send as 'Authorization: Bearer <token>' or '?token=<token>'.
  - Browser origins must be explicitly allowlisted: --allow-origin URL
    (repeatable). No allowlist = no CORS headers = same-machine tools only.

Endpoints:
  GET  /            unauthenticated liveness: {"server":"gratisgraph",...}
  GET  /machine     connect if needed; identity + selected profile
  POST /job         raw .m20 bytes -> 202 {"job_id":...}; ?home_first=0|1
  GET  /job/<id>    job state: queued | running | done | error
  GET  /telemetry   SSE stream of decoded machine packets (JSON per event)
  POST /abort       emergency stop (!1B)

Usage:
  sudo python3 gts_server.py [--port 8020] [--allow-origin https://ui.example]
"""

import argparse
import hmac
import json
import os
import secrets
import sys
import threading
import time
import uuid
import queue as queue_mod
import socketserver
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

from gts_api import GTSConnection, __version__

BIND_HOST    = '127.0.0.1'   # loopback only — deliberately not configurable
DEFAULT_PORT = 8020
TOKEN_PATH   = Path.home() / '.gratisgraph' / 'token'
GTS_MAGIC    = b'!MM88;'
MAX_JOB_SIZE = 8 * 1024 * 1024   # 8 MB — far above any real job
SSE_KEEPALIVE_S = 15.0

# Auth brute-force lockout. Loopback has no per-client identity (everything
# is 127.0.0.1, including a drive-by page in the user's own browser), so a
# per-client blocklist is impossible — the counter and lockout are GLOBAL.
# A paired client holds a valid token and never trips this; only guessing
# does. Required before the token shrinks to a 6-char pairing code.
AUTH_MAX_FAILURES = 10     # consecutive failures before lockout
AUTH_LOCKOUT_S    = 900.0  # 15 min
AUTH_FAIL_DELAY_S = 0.25   # tarpit per failed attempt


def load_or_create_token(path: Path = TOKEN_PATH) -> str:
    if path.exists():
        return path.read_text().strip()
    path.parent.mkdir(parents=True, exist_ok=True)
    token = secrets.token_urlsafe(24)
    path.write_text(token + '\n')
    os.chmod(path, 0o600)
    return token


def _json_safe(item):
    """Decoded packets may contain raw bytes — make them JSON-serialisable."""
    if isinstance(item, dict):
        out = dict(item)
        raw = out.get('raw')
        if isinstance(raw, (bytes, bytearray)):
            out['raw'] = raw.hex(' ')
        return out
    return {'type': 'ascii', 'text': str(item)}


class ServerState:
    """Shared state: machine connection, job registry, SSE subscribers."""

    def __init__(self, connection_factory=None, connect_timeout_s: float = 10.0,
                 auth_max_failures: int = AUTH_MAX_FAILURES,
                 auth_lockout_s: float = AUTH_LOCKOUT_S,
                 auth_fail_delay_s: float = AUTH_FAIL_DELAY_S):
        self._factory = connection_factory or (
            lambda: GTSConnection(verbose=False))
        self.connect_timeout_s = connect_timeout_s
        self.conn = None
        self.lock = threading.Lock()          # guards conn + serialises sends
        self.jobs: dict = {}                  # job_id -> state dict
        self._subs: set = set()               # SSE subscriber queues
        self._subs_lock = threading.Lock()
        # auth lockout state (global — see note at AUTH_MAX_FAILURES)
        self.auth_max_failures = auth_max_failures
        self.auth_lockout_s    = auth_lockout_s
        self.auth_fail_delay_s = auth_fail_delay_s
        self._auth_failures    = 0
        self._auth_locked_until = 0.0
        self._auth_lock = threading.Lock()

    # --- auth lockout ---

    def auth_locked_for(self) -> float:
        """Seconds of lockout remaining (0 if not locked)."""
        with self._auth_lock:
            return max(0.0, self._auth_locked_until - time.monotonic())

    def auth_failed(self) -> None:
        """Record a failed attempt; lock token auth after the limit."""
        with self._auth_lock:
            self._auth_failures += 1
            if self._auth_failures >= self.auth_max_failures:
                self._auth_locked_until = time.monotonic() + self.auth_lockout_s
                self._auth_failures = 0
                print(f'[gts_server] AUTH LOCKOUT: {self.auth_max_failures} '
                      f'consecutive bad tokens — token auth locked for '
                      f'{self.auth_lockout_s:.0f}s. Something on this machine '
                      f'is guessing the pairing token.', file=sys.stderr)
        time.sleep(self.auth_fail_delay_s)   # tarpit (outside the lock)

    def auth_succeeded(self) -> None:
        with self._auth_lock:
            self._auth_failures = 0

    # --- connection ---

    def ensure_connected(self):
        """Connect on first use. Returns the live connection or raises."""
        with self.lock:
            if self.conn is not None and self.conn.dev is not None:
                return self.conn
            conn = self._factory()
            if not conn.connect(timeout_s=self.connect_timeout_s):
                raise ConnectionError(
                    'Machine not found on USB. Check power and cable.')
            conn.add_tap(self._broadcast)
            self.conn = conn
            return conn

    def shutdown(self):
        with self.lock:
            if self.conn is not None:
                try:
                    self.conn.disconnect()
                finally:
                    self.conn = None

    # --- telemetry fan-out ---

    def _broadcast(self, item):
        event = _json_safe(item)
        with self._subs_lock:
            for q in list(self._subs):
                try:
                    q.put_nowait(event)
                except queue_mod.Full:
                    pass   # slow consumer: drop rather than block USB thread

    def subscribe(self) -> 'queue_mod.Queue':
        q = queue_mod.Queue(maxsize=512)
        with self._subs_lock:
            self._subs.add(q)
        return q

    def unsubscribe(self, q):
        with self._subs_lock:
            self._subs.discard(q)

    # --- jobs ---

    def submit_job(self, gts: bytes, home_first: bool) -> str:
        job_id = uuid.uuid4().hex[:12]
        job = {'id': job_id, 'state': 'queued', 'bytes': len(gts),
               'home_first': home_first, 'submitted': time.time(),
               'finished': None, 'error': None}
        self.jobs[job_id] = job

        def run():
            try:
                conn = self.ensure_connected()
                job['state'] = 'running'
                with self.lock:            # one job on the wire at a time
                    conn.send_gts(gts, home_first=home_first)
                job['state'] = 'done'
            except Exception as e:
                job['state'] = 'error'
                job['error'] = str(e)
            finally:
                job['finished'] = time.time()

        threading.Thread(target=run, daemon=True,
                         name=f'gts-job-{job_id}').start()
        return job_id

    def abort(self):
        conn = self.ensure_connected()
        conn.abort()


class Handler(BaseHTTPRequestHandler):
    # Injected at server construction:
    state: ServerState = None
    token: str = ''
    allowed_origins: list = []
    server_version = 'GratisGraph'
    protocol_version = 'HTTP/1.1'

    # ----- plumbing -----

    def log_message(self, fmt, *args):       # quiet by default
        if getattr(self, 'verbose', False):
            super().log_message(fmt, *args)

    def _origin_allowed(self) -> bool:
        origin = self.headers.get('Origin')
        return origin is not None and origin in self.allowed_origins

    def _cors_headers(self):
        if self._origin_allowed():
            self.send_header('Access-Control-Allow-Origin',
                             self.headers['Origin'])
            self.send_header('Vary', 'Origin')

    def _send_json(self, code: int, payload: dict):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self._cors_headers()
        self.end_headers()
        self.wfile.write(body)

    def _authed(self) -> bool:
        supplied = ''
        auth = self.headers.get('Authorization', '')
        if auth.startswith('Bearer '):
            supplied = auth[7:].strip()
        else:
            qs = parse_qs(urlparse(self.path).query)
            supplied = (qs.get('token') or [''])[0]
        return bool(supplied) and hmac.compare_digest(supplied, self.token)

    def _require_auth(self) -> bool:
        remaining = self.state.auth_locked_for()
        if remaining > 0:
            self.send_response(429)
            self.send_header('Retry-After', str(int(remaining) + 1))
            body = json.dumps({'error': 'token auth locked after repeated '
                               'failures', 'retry_after_s': int(remaining) + 1}
                              ).encode()
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self._cors_headers()
            self.end_headers()
            self.wfile.write(body)
            return False
        if self._authed():
            self.state.auth_succeeded()
            return True
        self.state.auth_failed()
        self._send_json(401, {'error': 'missing or invalid pairing token'})
        return False

    # ----- methods -----

    def do_OPTIONS(self):
        if not self._origin_allowed():
            self._send_json(403, {'error': 'origin not allowlisted'})
            return
        self.send_response(204)
        self._cors_headers()
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers',
                         'Authorization, Content-Type')
        self.send_header('Access-Control-Max-Age', '600')
        self.send_header('Content-Length', '0')
        self.end_headers()

    def do_GET(self):
        path = urlparse(self.path).path
        if path == '/':
            from version import diagnostics
            payload = {
                'server': 'gratisgraph', 'version': __version__,
                'connected': (self.state.conn is not None
                              and self.state.conn.dev is not None),
                'diagnostics': diagnostics(),
            }
            self._send_json(200, payload)
            return
        if not self._require_auth():
            return
        if path == '/machine':
            self._handle_machine()
        elif path.startswith('/job/'):
            job = self.state.jobs.get(path[len('/job/'):])
            if job is None:
                self._send_json(404, {'error': 'unknown job id'})
            else:
                self._send_json(200, job)
        elif path == '/telemetry':
            self._handle_telemetry()
        else:
            self._send_json(404, {'error': 'not found'})

    def do_POST(self):
        path = urlparse(self.path).path
        if not self._require_auth():
            return
        if path == '/job':
            self._handle_job()
        elif path == '/abort':
            try:
                self.state.abort()
                self._send_json(200, {'aborted': True})
            except Exception as e:
                self._send_json(503, {'error': str(e)})
        else:
            self._send_json(404, {'error': 'not found'})

    # ----- handlers -----

    def _handle_machine(self):
        try:
            conn = self.state.ensure_connected()
        except Exception as e:
            self._send_json(503, {'error': str(e)})
            return
        p = conn.profile
        self._send_json(200, {
            'model':       conn.model,
            'model_code':  f'0x{conn.model_code:04X}',
            'platform':    conn.config_code,
            'boot_ver':    conn.boot_ver,
            'prog_ver':    conn.prog_ver,
            'profile': {
                'name':         p.name,
                'units_per_mm': p.units_per_mm,
                'envelope_mm':  list(p.envelope_mm),
                'negate_x':     p.negate_x,
                'coord_scale':  p.coord_scale,
            },
        })

    def _handle_job(self):
        length = int(self.headers.get('Content-Length') or 0)
        if length <= 0 or length > MAX_JOB_SIZE:
            self._send_json(400, {'error': f'body required, max {MAX_JOB_SIZE} bytes'})
            return
        gts = self.rfile.read(length)
        if not gts.startswith(GTS_MAGIC):
            self._send_json(400, {'error': 'not a GTS binary (missing !MM88; magic)'})
            return
        qs = parse_qs(urlparse(self.path).query)
        home_first = (qs.get('home_first') or ['1'])[0] not in ('0', 'false')
        job_id = self.state.submit_job(gts, home_first)
        self._send_json(202, {'job_id': job_id, 'bytes': len(gts),
                              'home_first': home_first})

    def _handle_telemetry(self):
        try:
            self.state.ensure_connected()
        except Exception as e:
            self._send_json(503, {'error': str(e)})
            return
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.send_header('Cache-Control', 'no-cache')
        self._cors_headers()
        self.end_headers()
        sub = self.state.subscribe()
        try:
            while True:
                try:
                    event = sub.get(timeout=SSE_KEEPALIVE_S)
                    self.wfile.write(
                        f'data: {json.dumps(event)}\n\n'.encode())
                except queue_mod.Empty:
                    self.wfile.write(b': keepalive\n\n')
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass   # client went away
        finally:
            self.state.unsubscribe(sub)


class _LoopbackHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def server_bind(self):
        # Skip HTTPServer.server_bind's getfqdn() reverse-DNS lookup, which
        # can stall ~30s on some networks. We only ever bind loopback.
        socketserver.TCPServer.server_bind(self)
        self.server_name = BIND_HOST
        self.server_port = self.server_address[1]


def make_server(port: int = DEFAULT_PORT,
                token: str = '',
                allowed_origins=None,
                state: ServerState = None) -> ThreadingHTTPServer:
    """Build (but don't run) the server. Test entry point."""
    handler = type('BoundHandler', (Handler,), {
        'state': state or ServerState(),
        'token': token,
        'allowed_origins': list(allowed_origins or []),
    })
    return _LoopbackHTTPServer((BIND_HOST, port), handler)


def main():
    ap = argparse.ArgumentParser(description='GratisGraph local machine server')
    ap.add_argument('--port', type=int, default=DEFAULT_PORT)
    ap.add_argument('--allow-origin', action='append', default=[],
                    help='Browser origin to allowlist for CORS (repeatable)')
    ap.add_argument('--connect-timeout', type=float, default=10.0,
                    help='Seconds to wait for the machine per connect attempt')
    ap.add_argument('--verbose', action='store_true')
    args = ap.parse_args()

    token = load_or_create_token()
    state = ServerState(connect_timeout_s=args.connect_timeout)
    httpd = make_server(port=args.port, token=token,
                        allowed_origins=args.allow_origin, state=state)
    if args.verbose:
        httpd.RequestHandlerClass.verbose = True

    print(f'GratisGraph server  http://{BIND_HOST}:{args.port}')
    print(f'Pairing token       {token}')
    print(f'Token file          {TOKEN_PATH}')
    print(f'Allowed origins     {args.allow_origin or "(none — same-machine only)"}')
    print('Ctrl-C to stop.')
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print('\nShutting down...')
    finally:
        state.shutdown()
        httpd.server_close()


if __name__ == '__main__':
    main()
