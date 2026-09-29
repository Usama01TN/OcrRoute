# coding=utf-8
"""A key over quota on one server is skipped by every server in the cluster (real primary + members + a gateway)."""
from __future__ import absolute_import, division, print_function

import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from io import BytesIO

import requests

from tests.integration.test_sync_cluster import _adminKey, _port, _serve, _until, _wait

GOOD, BAD = 'sk-good-1111', 'sk-bad-2222'


def _gateway(seen):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, body):
            data = json.dumps(body).encode()
            self.send_response(code)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            self._send(200, {'data': [{'id': 'auto'}]})

        def do_POST(self):
            self.rfile.read(int(self.headers['Content-Length']))
            auth = self.headers.get('Authorization', '')
            seen.append(auth)
            if auth != 'Bearer ' + GOOD:
                return self._send(401, {'error': {'message': 'invalid api key'}})
            self._send(200, {'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': json.dumps(
                [{'text': 'ok', 'box_2d': [0, 0, 500, 1000]}])}, 'finish_reason': 'stop'}]})

    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    server = HTTPServer(('127.0.0.1', port), H)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, port


def _creds(base, headers, pid):
    p = next(x for x in requests.get(base + '/v1/providers', headers=headers).json()['items'] if x['id'] == pid)
    return {c['masked'][-4:]: c for c in p['credentials']}


def test_exhausted_key_is_skipped_cluster_wide(tmp_path):
    from PIL import Image

    seen = []
    gw, gwPort = _gateway(seen)
    pp, ap, bp = _port(), _port(), _port()
    P, A, B = ('http://127.0.0.1:{}'.format(p) for p in (pp, ap, bp))
    pkey = _adminKey(tmp_path / 'primary')
    akey, bkey = _adminKey(tmp_path / 'a'), _adminKey(tmp_path / 'b')
    procs = [_serve(tmp_path / 'primary', pp), _serve(tmp_path / 'a', ap), _serve(tmp_path / 'b', bp)]
    try:
        for base, proc in zip((P, A, B), procs):
            _wait(base, proc)
        PH = {'Authorization': 'Bearer ' + pkey}
        # the primary: an OmniRoute provider with two keys, the bad one first
        pid = requests.post(P + '/v1/providers', json={'engine_id': 'OmniRouteOcr', 'label': 'omni', 'priority': 1,
                                                       'options': {'base': 'http://127.0.0.1:{}'.format(gwPort)}}, headers=PH).json()['id']
        requests.post(P + '/v1/credentials', json={'provider_id': pid, 'secret': BAD, 'order_index': 0}, headers=PH).raise_for_status()
        requests.post(P + '/v1/credentials', json={'provider_id': pid, 'secret': GOOD, 'order_index': 1}, headers=PH).raise_for_status()
        requests.patch(P + '/v1/endpoints', json={'public_base_url': P}, headers=PH).raise_for_status()
        requests.post(P + '/v1/cluster/create', json={'name': 'c'}, headers=PH).raise_for_status()
        items = requests.post(P + '/v1/sync/nodes/bulk', json={'items': [{'label': 'a'}, {'label': 'b'}]}, headers=PH).json()['items']
        for base, key, item in ((A, akey, items[0]), (B, bkey, items[1])):
            requests.post(base + '/v1/cluster/join', json={'code': item['join_code']}, headers={'Authorization': 'Bearer ' + key}).raise_for_status()
        for base in (A, B):  # both members have both keys (the primary's key works there after the sync)
            _until(lambda b=base: len(_creds(b, PH, pid)) == 2)
        buf = BytesIO()
        Image.new('RGB', (200, 100), 'white').save(buf, 'PNG')
        # 1. member A: the bad key fails, A rotates to the good key within the request
        r = requests.post(A + '/v1/ocr', files={'file': ('t.png', buf.getvalue())},
                          data={'json': json.dumps({'engine': 'OmniRouteOcr', 'cache': False})}, headers=PH).json()
        assert r['status'] == 'succeeded', r.get('error_message')
        assert [a['status'] for a in r['routing']['attempts']] == ['failed', 'succeeded']
        assert seen[-2:] == ['Bearer ' + BAD, 'Bearer ' + GOOD]
        assert _creds(A, PH, pid)[BAD[-4:]]['exhausted_until']
        # 2. the primary learns it without using the key itself
        _until(lambda: _creds(P, PH, pid)[BAD[-4:]]['exhausted_until'])
        assert not _creds(P, PH, pid)[GOOD[-4:]]['exhausted_until']
        # 3. member B learns it from the primary
        _until(lambda: _creds(B, PH, pid)[BAD[-4:]]['exhausted_until'])
        # 4. an OCR on the primary (and on B) goes straight to the good key: one attempt, no failed try
        for base in (P, B):
            before = len(seen)
            r = requests.post(base + '/v1/ocr', files={'file': ('t.png', buf.getvalue())},
                              data={'json': json.dumps({'engine': 'OmniRouteOcr', 'cache': False})}, headers=PH).json()
            assert r['status'] == 'succeeded' and [a['status'] for a in r['routing']['attempts']] == ['succeeded']
            assert seen[before:] == ['Bearer ' + GOOD]
    finally:
        for p in procs:
            p.terminate()
        for p in procs:
            p.wait(timeout=10)
        gw.shutdown()
