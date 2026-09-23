# coding=utf-8
from __future__ import absolute_import, division, print_function

import json


class FakeServer(object):
    should_exit = False


def test_lifecycle_flags_and_busy():
    from ocrroute.runtime import lifecycle

    srv = FakeServer()
    lifecycle.register(srv, 'serve')
    assert not lifecycle.busy() and lifecycle.canRestart()
    lifecycle.requestRestart(delay=0.01)
    assert lifecycle.busy() and lifecycle.restartRequested()
    import time

    time.sleep(0.1)
    assert srv.should_exit is True
    lifecycle.reset()
    assert lifecycle.busy()  # old server is still draining until the owner registers the next one
    srv2 = FakeServer()
    lifecycle.register(srv2, 'embedded')
    assert not lifecycle.busy() and not lifecycle.restartRequested()
    lifecycle.requestShutdown(delay=0.01)
    time.sleep(0.1)
    assert srv2.should_exit and lifecycle.shutdownRequested()
    lifecycle.reset()
    lifecycle.register(None, 'unknown')


def test_restart_and_shutdown_endpoints_use_lifecycle(client, admin_headers):
    from ocrroute.runtime import lifecycle

    srv = FakeServer()
    lifecycle.register(srv, 'serve')
    try:
        r = client.post('/v1/endpoints/server/restart', headers=admin_headers).json()
        assert r['restarting'] and r['mechanism'] == 'server.should_exit+loop'
        assert client.post('/v1/endpoints/server/restart', headers=admin_headers).status_code == 409  # in progress
        lifecycle.reset()
        lifecycle.register(FakeServer(), 'serve')
        r = client.post('/v1/endpoints/server/shutdown', headers=admin_headers).json()
        assert r['shutting_down']
    finally:
        lifecycle.reset()
        lifecycle.register(None, 'unknown')


def _login(client, user, pw):
    client.post('/panel/setup', data={'username': 'admin', 'password': 'password123', 'confirm': 'password123'}, follow_redirects=False)
    r = client.post('/panel/login', data={'username': user, 'password': pw}, follow_redirects=False)
    return r.status_code


def test_users_roles_enforced(client, sample_png):
    H = {'X-Requested-With': 'OcrRoute'}
    assert _login(client, 'admin', 'password123') == 303
    assert client.post('/v1/users', json={'username': 'ops', 'password': 'operator123', 'role': 'operator'}, headers=H).status_code == 201
    v = client.post('/v1/users', json={'username': 'view', 'password': 'viewer1234', 'role': 'viewer'}, headers=H).json()
    admin_id = next(u['id'] for u in client.get('/v1/users', headers=H).json()['items'] if u['username'] == 'admin')
    assert client.patch('/v1/users/{}'.format(admin_id), json={'role': 'viewer'}, headers=H).status_code == 409  # last admin
    assert client.post('/v1/users/me/password', json={'current_password': 'wrong', 'new_password': 'newpassword1'}, headers=H).status_code == 400
    assert client.post('/v1/users/me/password', json={'current_password': 'password123', 'new_password': 'newpassword1'}, headers=H).json()['ok']
    # operator
    client.post('/panel/logout')
    assert client.post('/panel/login', data={'username': 'ops', 'password': 'operator123'}, follow_redirects=False).status_code == 303
    assert client.post('/v1/providers', json={'engine_id': 'FakeOcr', 'label': 'ops-p'}, headers=H).status_code == 201
    assert client.post('/v1/keys', json={'name': 'x'}, headers=H).status_code == 403
    assert client.get('/v1/users', headers=H).status_code == 403
    # viewer
    client.post('/panel/logout')
    assert client.post('/panel/login', data={'username': 'view', 'password': 'viewer1234'}, follow_redirects=False).status_code == 303
    assert client.get('/v1/runs', headers=H).status_code == 200
    assert client.post('/v1/ocr', files={'file': ('t.png', sample_png)}, data={'json': json.dumps({'engine': 'FakeOcr'})}, headers=H).status_code == 403
    client.post('/panel/logout')
    assert client.post('/panel/login', data={'username': 'admin', 'password': 'newpassword1'}, follow_redirects=False).status_code == 303
    assert client.delete('/v1/users/{}'.format(v['id']), headers=H).status_code == 204


def test_auto_templates_and_new_strategies(client, admin_headers, sample_png):
    cat = client.get('/v1/routes/catalog', headers=admin_headers).json()
    ids = [t['id'] for t in cat['templates']]
    assert 'auto/balanced' in ids and 'auto/best-of-two' in ids and len(ids) >= 12
    assert set(cat['categories']) == {'intelligent', 'deterministic'}
    from ocrroute.routing import strategies as S

    assert {'confidence_first', 'script_aware', 'sticky', 'best_of_two'} <= set(S.names())
    # a fake provider with credentials-less cloud engines around: auto routes must skip credential-less cloud providers
    for name in ('auto/fast', 'auto/private', 'auto/best-of-two', 'auto/consensus'):
        r = client.post('/v1/ocr', files={'file': ('t.png', sample_png)}, data={'json': json.dumps({'route': name, 'cache': False})},
                        headers=admin_headers).json()
        assert r['status'] == 'succeeded', (name, r.get('error_message'), r['routing']['explain'])
        assert r['routing']['route'] == name
    sim = client.post('/v1/routes/simulate', json={'route': 'auto/multilingual', 'language': ['ar']}, headers=admin_headers).json()
    assert sim['ok'] and sim['strategy'] == 'script_aware'
    r = client.post('/v1/routes/simulate', json={'route': 'auto/nope'}, headers=admin_headers)
    assert r.status_code == 404 or r.json().get('ok') is False


def test_script_detection():
    from ocrroute.routing.strategies import scriptOf

    assert scriptOf('ar') == 'arabic' and scriptOf('zh-CN') == 'cjk' and scriptOf('ru') == 'cyrillic' and scriptOf('fr') == 'latin'


def test_tunnel_install_unknown_and_status(client, admin_headers):
    assert client.post('/v1/endpoints/tunnels/nope/install', headers=admin_headers).status_code == 404
    e = client.get('/v1/endpoints', headers=admin_headers).json()
    for t in e['tunnels']:
        assert set(t) >= {'installed', 'authenticated', 'running', 'url', 'install_command', 'needs_auth'}


def test_all_languages_complete():
    from ocrroute import i18n

    codes = [c for c, _ in i18n.languages()]
    assert set(codes) >= {'en', 'fr', 'es', 'de', 'ar', 'it', 'pt', 'ru', 'zh'}
    en = set(i18n.catalogue('en'))
    for c in codes:
        assert set(i18n.catalogue(c)) == en, c
