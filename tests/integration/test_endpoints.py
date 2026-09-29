# coding=utf-8
from __future__ import absolute_import, division, print_function

import pathlib

from ocrroute.runtime.endpoints import Tunnel, localAddresses


def test_local_addresses_are_ipv4_without_loopback():
    for ip in localAddresses():
        assert ip.count('.') == 3 and not ip.startswith('127.')


def test_endpoints_snapshot_and_patch(client, admin_headers):
    e = client.get('/v1/endpoints', headers=admin_headers).json()
    assert e['active'][0]['url'].endswith(':{}/v1'.format(e['port']))
    assert [t['name'] for t in e['tunnels']] == ['cloudflare', 'tailscale', 'ngrok']
    assert all('install_command' in t and 'installed' in t for t in e['tunnels'])
    r = client.patch('/v1/endpoints', json={'public_base_url': 'https://ocr.example.com', 'custom_prompt': 'Text only.',
                                            'custom_prompt_enabled': True}, headers=admin_headers).json()
    assert [a['label'] for a in r['active']] == ['Local', 'Public'] and r['active'][1]['url'] == 'https://ocr.example.com/v1'
    assert r['custom_prompt_enabled'] is True
    assert client.patch('/v1/endpoints', json={'public_base_url': 'ftp://nope'}, headers=admin_headers).status_code == 400


def test_tunnel_errors_are_explicit(client, admin_headers):
    r = client.post('/v1/endpoints/tunnels/nope/enable', headers=admin_headers)
    assert r.status_code == 404
    r = client.post('/v1/endpoints/tunnels/cloudflare/enable', headers=admin_headers)
    assert r.status_code in (200, 400)  # 400 "not installed" on machines without cloudflared
    if r.status_code == 400:
        assert 'not installed' in r.json()['error_message']
    assert client.post('/v1/endpoints/tunnels/ngrok/disable', headers=admin_headers).json()['running'] is False


def test_global_prompt_is_injected(client, admin_headers, sample_png):
    """The Custom OCR prompt reaches the engine as the ``prompt`` kwarg when the request has none."""
    import json

    client.patch('/v1/endpoints', json={'custom_prompt': 'GLOBAL-PROMPT', 'custom_prompt_enabled': True}, headers=admin_headers)
    r = client.post('/v1/ocr', files={'file': ('t.png', sample_png)}, data={'json': json.dumps({'engine': 'FakeOcr', 'cache': False})},
                    headers=admin_headers).json()
    assert r['status'] == 'succeeded'
    from fixtures.fakeocr import LAST_PROMPT

    assert LAST_PROMPT.get('prompt') == 'GLOBAL-PROMPT'


def test_tunnel_status_shape_without_binary():
    class Fake(Tunnel):
        name, title, binary = 'fake', 'Fake', 'definitely-not-a-binary-xyz'

        def command(self, port):
            return ['true']

    t = Fake()
    st = t.status()
    assert st['installed'] is False and st['running'] is False and st['url'] == ''
    import pytest

    with pytest.raises(RuntimeError):
        t.start(1234, wait=0.1)


def test_no_em_dash_in_project_files():
    root = pathlib.Path(__file__).resolve().parents[2]
    offenders = []
    for p in root.rglob('*'):
        if not p.is_file() or 'AioOCR' in p.parts or 'vendor' in p.parts or '__pycache__' in p.parts or '.git' in p.parts:
            continue
        if p.suffix in ('.py', '.html', '.md', '.json', '.toml', '.yml', '.css', '.js', '.qss', '.txt') and '\u2014' in p.read_text(encoding='utf-8', errors='ignore'):
            offenders.append(str(p.relative_to(root)))
    assert offenders == []


def test_each_tunnel_keeps_its_own_authenticate():
    """Regression: a default authenticate() once landed inside Ngrok and shadowed the real one."""
    from ocrroute.runtime.endpoints import Cloudflare, Ngrok, Tailscale

    assert Ngrok.authenticate is not Tunnel.authenticate
    assert Tailscale.authenticate is not Tunnel.authenticate
    assert Cloudflare.authenticate is Tunnel.authenticate
    assert 'authtoken is required' in Ngrok().authenticate('')['output']
