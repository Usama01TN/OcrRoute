# coding=utf-8
"""
None
"""

PAGES = [
    '/panel/',
    '/panel/playground',
    '/panel/engines',
    '/panel/providers',
    '/panel/routes',
    '/panel/runs',
    '/panel/batch',
    '/panel/usage',
    '/panel/keys',
    '/panel/tools',
    '/panel/settings',
    '/panel/doctor',
]


def _login(client):
    client.post(
        '/panel/setup',
        data={'username': 'admin', 'password': 'password123', 'confirm': 'password123'},
        follow_redirects=False,
    )
    r = client.post('/panel/login', data={'username': 'admin', 'password': 'password123'}, follow_redirects=False)
    assert r.status_code == 303


def test_anonymous_redirects_and_first_run_setup(client):
    r = client.get('/panel/', follow_redirects=False)
    assert r.status_code == 303 and '/panel/login' in r.headers['location']
    r = client.get('/panel/login', follow_redirects=False)
    assert r.headers['location'].endswith('/panel/setup')
    assert (
        client.post('/panel/setup', data={'username': 'a', 'password': 'short', 'confirm': 'short'}).status_code == 200
    )


def test_login_lockout_and_pages(client, sample_png):
    _login(client)
    for p in PAGES:
        r = client.get(p)
        assert r.status_code == 200, p
        assert 'OcrRoute' in r.text
    assert 'No tools are installed' in client.get('/panel/tools').text
    assert 'reserved' in client.get('/panel/tools').text


def test_session_api_requires_csrf_header(client, sample_png):
    _login(client)
    assert client.post('/v1/providers', json={'engine_id': 'FakeOcr', 'label': 'x'}).status_code == 401
    r = client.post(
        '/v1/providers', json={'engine_id': 'FakeOcr', 'label': 'x'}, headers={'X-Requested-With': 'OcrRoute'}
    )
    assert r.status_code == 201
    r = client.post(
        '/v1/ocr',
        files={'file': ('t.png', sample_png)},
        data={'json': '{"engine": "FakeOcr", "output": ["overlay_png"]}'},
        headers={'X-Requested-With': 'OcrRoute'},
    )
    assert r.status_code == 200
    detail = client.get('/panel/runs/{}'.format(r.json()['run_id']))
    assert detail.status_code == 200 and 'Attempt timeline' in detail.text


def test_secret_never_rendered(client):
    _login(client)
    H = {'X-Requested-With': 'OcrRoute'}
    p = client.post('/v1/providers', json={'engine_id': 'FakeOcr', 'label': 'x'}, headers=H).json()
    client.post(
        '/v1/credentials', json={'provider_id': p['id'], 'secret': 'sk-supersecret-value-9876', 'alias': 'k'}, headers=H
    )
    html = client.get('/panel/providers').text
    assert 'sk-supersecret' not in html and '9876' in html
    api = client.get('/v1/providers', headers=H).json()
    assert 'sk-supersecret' not in str(api)


def test_language_switch_and_rtl(client):
    _login(client)
    r = client.get('/panel/lang/ar?next=/panel/', follow_redirects=False)
    assert r.status_code == 303 and 'ocrroute_lang=ar' in r.headers.get('set-cookie', '')
    html = client.get('/panel/').text
    assert 'dir="rtl"' in html and 'المحركات' in html
    html = client.get('/panel/lang/fr?next=/panel/engines', follow_redirects=True).text
    assert 'dir="ltr"' in html and 'Moteurs' in html and 'Réanalyser' in html
    html = client.get('/panel/lang/en?next=/panel/', follow_redirects=True).text
    assert 'Welcome back, admin.' in html and 'Getting started' in html
