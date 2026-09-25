# coding=utf-8
"""OmniRouteOcr end to end through OcrRoute, against an in-process stand-in for the OmniRoute gateway."""
from __future__ import absolute_import, division, print_function

import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from io import BytesIO


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
            self._send(200, {'data': [{'id': 'auto'}]}) if self.path == '/v1/models' else self._send(404, {})

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            seen.append({'model': body.get('model'), 'auth': self.headers.get('Authorization', ''),
                         'image': 'image_url' in json.dumps(body)})
            if self.headers.get('Authorization') != 'Bearer sk-omni-test':
                return self._send(401, {'error': {'message': 'invalid api key'}})
            lines = [{'text': 'OcrRoute Invoice 2026', 'box_2d': [100, 50, 300, 950]}]
            self._send(200, {'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': json.dumps(lines)},
                                          'finish_reason': 'stop'}]})

    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    server = HTTPServer(('127.0.0.1', port), H)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, port


def test_omniroute_catalog_entry(client, admin_headers):
    e = client.get('/v1/engines/OmniRouteOcr', headers=admin_headers).json()
    assert e['available'] and e['name'] == 'OmniRoute (local AI gateway)' and e['vendor'] == 'OmniRoute'
    assert e['requires_key'] and e['supports_handwriting'] and e['supports_tables'] and e['kind'] == 'api'


def test_omniroute_ocr_end_to_end(client, admin_headers):
    from PIL import Image

    seen = []
    server, port = _gateway(seen)
    try:
        buf = BytesIO()
        Image.new('RGB', (800, 200), 'white').save(buf, 'PNG')
        pid = client.post('/v1/providers', json={'engine_id': 'OmniRouteOcr', 'label': 'omni-test', 'priority': 1,
                                                 'options': {'base': 'http://127.0.0.1:{}'.format(port)}},
                          headers=admin_headers).json()['id']
        client.post('/v1/credentials', json={'provider_id': pid, 'secret': 'sk-omni-test'}, headers=admin_headers)
        r = client.post('/v1/ocr', files={'file': ('t.png', buf.getvalue())},
                        data={'json': json.dumps({'engine': 'OmniRouteOcr', 'cache': False})}, headers=admin_headers).json()
        assert r['status'] == 'succeeded', r.get('error_message')
        assert r['result']['ParsedText'].strip() == 'OcrRoute Invoice 2026'
        word = r['result']['TextOverlay']['Lines'][0]['Words'][0]
        assert (word['Left'], word['Top'], round(word['Width']), word['Height']) == (40.0, 20.0, 720, 40.0)  # 0-1000 grid -> px
        assert seen[-1] == {'model': 'auto', 'auth': 'Bearer sk-omni-test', 'image': True}
        sim = client.post('/v1/routes/simulate', json={'route': 'auto/private'}, headers=admin_headers).json()
        assert not any(c.get('engine') == 'OmniRouteOcr' for c in sim.get('candidates', []))  # images leave the machine
    finally:
        server.shutdown()
