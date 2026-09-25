# coding=utf-8
"""
Cluster sync end to end: a real leader and a real follower (separate processes, homes, databases and master keys).
"""
from __future__ import absolute_import, division, print_function

import json
import os
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from io import BytesIO

import requests

TOKEN = 'test-sync-token-0123456789abcdef'


def _port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


def _serve(home, port, **env):
    e = dict(os.environ, OCRROUTE_HOME=str(home), OCRROUTE_LOG_LEVEL='WARNING', PYTHONPATH=os.getcwd(),
             **{'OCRROUTE_' + k.upper(): str(v) for k, v in env.items()})
    return subprocess.Popen([sys.executable, '-m', 'ocrroute', 'serve', '--port', str(port)], env=e,
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


def _adminKey(home):
    e = dict(os.environ, OCRROUTE_HOME=str(home), PYTHONPATH=os.getcwd())
    out = subprocess.run([sys.executable, '-m', 'ocrroute', 'key', 'create', '--name', 'boot', '--scope', 'admin'],
                         env=e, capture_output=True, text=True, timeout=120)
    return out.stdout.strip().splitlines()[-1]


def _wait(base, proc):
    for _ in range(240):
        if proc.poll() is not None:
            raise RuntimeError('server exited: ' + proc.stderr.read().decode()[-1500:])
        try:
            if requests.get(base + '/v1/health', timeout=1).ok:
                return
        except requests.RequestException:
            pass
        time.sleep(0.25)
    raise RuntimeError('server did not start: ' + base)


def _until(fn, timeout=40):
    end = time.time() + timeout
    last = None
    while time.time() < end:
        try:
            last = fn()
            if last:
                return last
        except Exception as exc:  # noqa: BLE001
            last = exc
        time.sleep(0.5)
    raise AssertionError('condition not met; last value: {!r}'.format(last))


def _gateway():
    """Stand-in OmniRoute gateway that only accepts the credential created on the leader."""

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers['Content-Length']))
            ok = self.headers.get('Authorization') == 'Bearer sk-shared-secret'
            body = {'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': json.dumps(
                [{'text': 'synced secret works', 'box_2d': [0, 0, 500, 1000]}])}, 'finish_reason': 'stop'}]} if ok \
                else {'error': {'message': 'bad key'}}
            data = json.dumps(body).encode()
            self.send_response(200 if ok else 401)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    port = _port()
    server = HTTPServer(('127.0.0.1', port), H)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, port


def test_leader_to_follower_sync(tmp_path):
    from PIL import Image

    gw, gwPort = _gateway()
    lp, fp = _port(), _port()
    leaderHome, followerHome, strangerHome = tmp_path / 'leader', tmp_path / 'follower', tmp_path / 'stranger'
    leaderKey = _adminKey(leaderHome)
    _adminKey(followerHome)  # a local key on the follower: mirroring replaces it with the leader's keys
    leader = _serve(leaderHome, lp, sync_role='leader', sync_token=TOKEN)
    procs = [leader]
    try:
        L = 'http://127.0.0.1:{}'.format(lp)
        _wait(L, leader)
        H = {'Authorization': 'Bearer ' + leaderKey}
        # ---- configuration on the leader
        omni = requests.post(L + '/v1/providers', json={'engine_id': 'OmniRouteOcr', 'label': 'shared-omni', 'priority': 1,
                                                        'options': {'base': 'http://127.0.0.1:{}'.format(gwPort)}},
                             headers=H).json()['id']
        requests.post(L + '/v1/credentials', json={'provider_id': omni, 'secret': 'sk-shared-secret'}, headers=H).raise_for_status()
        route = requests.post(L + '/v1/routes', json={'name': 'shared-route', 'members': [{'provider_id': omni}]},
                              headers=H).json()['id']
        client = requests.post(L + '/v1/keys', json={'name': 'shared-client', 'scopes': ['admin']}, headers=H).json()
        clientKey = client.get('key') or client.get('raw') or client.get('secret')
        assert clientKey, client
        # ---- a follower with the right token, and one with a wrong token
        follower = _serve(followerHome, fp, sync_role='follower', sync_token=TOKEN, sync_leader_url=L,
                          sync_interval_seconds=1)
        sp = _port()
        stranger = _serve(strangerHome, sp, sync_role='follower', sync_token='wrong-token-0123456789abcdef',
                          sync_leader_url=L, sync_interval_seconds=1)
        procs += [follower, stranger]
        F, S = 'http://127.0.0.1:{}'.format(fp), 'http://127.0.0.1:{}'.format(sp)
        _wait(F, follower)
        _wait(S, stranger)
        FH = {'Authorization': 'Bearer ' + clientKey}  # a key created on the LEADER authenticates on the follower
        provs = _until(lambda: [p for p in requests.get(F + '/v1/providers', headers=FH, timeout=5).json()['items']
                                if p['label'] == 'shared-omni'])
        assert provs[0]['id'] == omni
        # the credential was re-encrypted with the follower's own key and still works
        buf = BytesIO()
        Image.new('RGB', (400, 100), 'white').save(buf, 'PNG')
        r = requests.post(F + '/v1/ocr', files={'file': ('t.png', buf.getvalue())},
                          data={'json': json.dumps({'route': 'shared-route', 'cache': False})}, headers=FH, timeout=60).json()
        assert r['status'] == 'succeeded', r.get('error_message')
        assert r['result']['ParsedText'].strip() == 'synced secret works'
        # the follower never stores the leader's master key, and its own secret file differs
        assert (leaderHome / 'secret.key').read_bytes() != (followerHome / 'secret.key').read_bytes() \
            if (leaderHome / 'secret.key').exists() else True
        # configuration edits on a follower are refused (they would be overwritten)
        blocked = requests.post(F + '/v1/providers', json={'engine_id': 'Tesseract', 'label': 'local-edit'}, headers=FH)
        assert blocked.status_code == 409 and 'leader' in blocked.json()['error_message']
        # ---- a change on the leader propagates, including deletions
        requests.delete(L + '/v1/routes/' + route, headers=H).raise_for_status()
        _until(lambda: all(x['name'] != 'shared-route' for x in requests.get(F + '/v1/routes', headers=FH).json()['items']))
        status = requests.get(F + '/v1/sync/status', headers=FH).json()
        assert status['role'] == 'follower' and status['state']['last_error'] is None and status['state']['digest']
        # ---- the wrong token gets nothing, and says why
        stranger_status = _until(lambda: (lambda st: st if st.get('state', {}).get('last_error') else None)(
            requests.get(S + '/v1/sync/status', headers={'Authorization': 'Bearer ' + _adminKeyCache(strangerHome)}).json()))
        assert 'sync token' in stranger_status['state']['last_error']
        assert requests.get(L + '/v1/sync/snapshot', headers={'X-OcrRoute-Sync-Token': 'nope'}).status_code == 401
    finally:
        for p in procs:
            p.terminate()
        for p in procs:
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                p.kill()
        gw.shutdown()


_KEYS = {}


def _adminKeyCache(home):
    if home not in _KEYS:
        _KEYS[home] = _adminKey(home)
    return _KEYS[home]
