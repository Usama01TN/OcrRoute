# coding=utf-8
"""End-to-end API tests against the fake engines (no live OCR services)."""

import concurrent.futures as cf
import time

from fixtures.fakeocr import CALLS


def _provider(client, H, engine, label, **kw):
    r = client.post('/v1/providers', json={'engine_id': engine, 'label': label, **kw}, headers=H)
    assert r.status_code == 201, r.text
    return r.json()


def _cred(client, H, pid, secret, alias=''):
    r = client.post('/v1/credentials', json={'provider_id': pid, 'secret': secret, 'alias': alias}, headers=H)
    assert r.status_code == 201
    return r.json()


def _ocr(client, H, png, **payload):
    import json

    return client.post(
        '/v1/ocr', files={'file': ('t.png', png, 'image/png')}, data={'json': json.dumps(payload)}, headers=H
    )


def test_health_version_and_auth(client, sample_png):
    assert client.get('/v1/health').json() == {'status': 'ok'}
    assert client.get('/v1/version').json()['name'] == 'OcrRoute'
    assert client.post('/v1/ocr', json={'base64': 'aGk='}).status_code == 401


def test_single_engine_envelope_and_artifacts(client, admin_headers, sample_png):
    r = _ocr(client, admin_headers, sample_png, engine='FakeOcr', output=['text', 'json', 'hocr', 'pdf', 'overlay_png'])
    assert r.status_code == 200
    env = r.json()
    assert env['status'] == 'succeeded' and env['result']['FileParseExitCode'] == 1
    assert env['result']['ParsedText'].startswith('hello world')
    assert env['routing']['winning_engine'] == 'FakeOcr' and env['routing']['attempt_count'] == 1
    assert {a['kind'] for a in env['artifacts']} == {'text', 'json', 'hocr', 'pdf', 'overlay_png'}
    txt = client.get(env['artifacts'][0]['url'], headers=admin_headers)
    assert txt.status_code == 200
    detail = client.get('/v1/runs/{}'.format(env['run_id']), headers=admin_headers).json()
    assert detail['attempts'][0]['status'] == 'succeeded'


def test_fallback_records_failed_attempt_then_succeeds(client, admin_headers, sample_png):
    bad = _provider(client, admin_headers, 'FakeAuthFail', 'bad-first')
    _cred(client, admin_headers, bad['id'], 'wrong', 'k1')
    good = _provider(client, admin_headers, 'FakeOcr', 'good-second')
    r = client.post(
        '/v1/routes',
        json={
            'name': 'chain',
            'strategy': 'priority',
            'is_default': True,
            'members': [{'provider_id': bad['id']}, {'provider_id': good['id']}],
        },
        headers=admin_headers,
    )
    assert r.status_code == 201
    env = _ocr(client, admin_headers, sample_png).json()
    assert env['status'] == 'succeeded' and env['routing']['route'] == 'chain'
    assert [a['status'] for a in env['routing']['attempts']] == ['failed', 'succeeded']
    assert env['routing']['attempts'][0]['error_code'] == 'auth'


def test_credential_rotation_within_provider(client, admin_headers, sample_png):
    p = _provider(client, admin_headers, 'FakeAuthFail', 'rotating')
    _cred(client, admin_headers, p['id'], 'wrong', 'k1')
    _cred(client, admin_headers, p['id'], 'good', 'k2')
    CALLS.clear()
    env = _ocr(client, admin_headers, sample_png, provider_id=p['id'], cache=False).json()
    assert env['status'] == 'succeeded', env
    assert env['routing']['winning_provider'] == 'rotating'
    assert any('rotating key' in x for x in env['routing']['explain'])
    # both credential attempts land on the same provider, no other provider was touched
    assert len(env['routing']['attempts']) == 2 and {a['provider'] for a in env['routing']['attempts']} == {'rotating'}
    provs = client.get('/v1/providers', headers=admin_headers).json()['items']
    creds = {c['alias']: c for c in next(x for x in provs if x['id'] == p['id'])['credentials']}
    assert creds['k1']['exhausted_until'] and creds['k1']['failure_count'] == 1 and creds['k2']['success_count'] == 1


def test_bad_input_fails_fast_with_one_attempt(client, admin_headers):
    r = client.post('/v1/ocr', json={'path': '/nope/missing.png', 'engine': 'FakeOcr'}, headers=admin_headers)
    assert r.status_code == 400 and r.json()['error_code'] == 'bad_input'
    r = client.post('/v1/ocr', json={'url': 'http://169.254.169.254/latest/meta-data'}, headers=admin_headers)
    assert r.status_code == 400 and 'blocked' in r.json()['error_message']


def test_cache_hit_creates_no_attempts(client, admin_headers, sample_png):
    a = _ocr(client, admin_headers, sample_png, engine='FakeOcr').json()
    b = _ocr(client, admin_headers, sample_png, engine='FakeOcr').json()
    assert not a['cached'] and b['cached'] and b['status'] == 'cached'
    assert client.get('/v1/runs/{}'.format(b['run_id']), headers=admin_headers).json()['attempts'] == []
    c = client.post(
        '/v1/ocr',
        files={'file': ('t.png', sample_png)},
        data={'engine': 'FakeOcr'},
        headers=dict(admin_headers, **{'X-OcrRoute-No-Cache': '1'}),
    ).json()
    assert not c['cached']


def test_idempotency_key(client, admin_headers, sample_png):
    h = dict(admin_headers, **{'Idempotency-Key': 'abc-123'})
    a = _ocr(client, h, sample_png, engine='FakeOcr', cache=False).json()
    b = _ocr(client, h, sample_png, engine='FakeOcr', cache=False).json()
    assert a['run_id'] == b['run_id'] and b['cached']


def test_empty_result_falls_through_and_degraded_partial(client, admin_headers, sample_png):
    e = _provider(client, admin_headers, 'FakeEmpty', 'empty')
    ok = _provider(client, admin_headers, 'FakeOcr', 'ok')
    client.post(
        '/v1/routes',
        json={
            'name': 'strict',
            'members': [{'provider_id': e['id']}, {'provider_id': ok['id']}],
            'stop_condition': {'min_chars': 10000},
        },
        headers=admin_headers,
    )
    env = _ocr(client, admin_headers, sample_png, route='strict').json()
    assert env['status'] == 'succeeded' and env['routing']['degraded'] is True
    assert env['routing']['attempts'][0]['error_code'] == 'empty_result'


def test_unavailable_engine_is_skipped_and_hinted(client, admin_headers, sample_png):
    eng = client.get('/v1/engines/FakeUnavailable', headers=admin_headers).json()
    assert eng['available'] is False and eng['install_hint'] == 'pip install nothing'
    r = client.post('/v1/routes/simulate', json={'engine': 'FakeUnavailable'}, headers=admin_headers).json()
    assert r['ok'] is False and 'unavailable' in r['error']


def test_engine_deadline_timeout(client, admin_headers, sample_png):
    p = _provider(client, admin_headers, 'FakeSlow', 'slow', timeout=1)
    client.post(
        '/v1/routes',
        json={'name': 'fast', 'members': [{'provider_id': p['id']}], 'total_deadline_ms': 1500},
        headers=admin_headers,
    )
    t0 = time.time()
    env = _ocr(client, admin_headers, sample_png, route='fast').json()
    assert env['status'] == 'failed' and env['error_code'] in ('timeout', 'deadline')
    assert time.time() - t0 < 6


def test_keys_scopes_limits_and_rotation(client, admin_headers, sample_png):
    k = client.post(
        '/v1/keys', json={'name': 'client', 'rpm_limit': 2, 'scopes': ['ocr:write']}, headers=admin_headers
    ).json()
    assert k['secret'].startswith('ocrr_')
    H = {'Authorization': 'Bearer {}'.format(k['secret'])}
    assert client.get('/v1/keys', headers=H).status_code == 403
    assert _ocr(client, H, sample_png, engine='FakeOcr').status_code == 200
    assert _ocr(client, H, sample_png, engine='FakeOcr').status_code == 200
    r = _ocr(client, H, sample_png, engine='FakeOcr')
    assert r.status_code == 429 and 'Retry-After' in r.headers
    rot = client.post('/v1/keys/{}/rotate'.format(k['id']), headers=admin_headers).json()
    assert client.get('/v1/runs', headers=H).status_code == 401
    client.delete('/v1/keys/{}'.format(k['id']), headers=admin_headers)
    assert client.get('/v1/runs', headers={'Authorization': 'Bearer {}'.format(rot['secret'])}).status_code == 401


def test_tools_reserved_surface(client, admin_headers):
    r = client.get('/v1/tools', headers=admin_headers).json()
    assert r == {'tools': [], 'reserved': True, 'note': r['note']}
    assert client.get('/v1/tools/x', headers=admin_headers).status_code == 404
    assert client.post('/v1/tools/x/run', headers=admin_headers).status_code == 501


def test_batch_job(client, admin_headers, sample_png):
    r = client.post(
        '/v1/batch',
        files=[('files', ('a.png', sample_png)), ('files', ('b.png', sample_png)), ('files', ('bad.txt', b'nope'))],
        data={'body': '{"engine": "FakeOcr", "name": "t"}'},
        headers=admin_headers,
    )
    assert r.status_code == 200
    jid = r.json()['job_id']
    for _ in range(60):
        j = client.get('/v1/jobs/{}'.format(jid), headers=admin_headers).json()
        if j['status'] not in ('queued', 'running'):
            break
        time.sleep(0.2)
    assert j['status'] == 'succeeded' and j['done'] == 2 and j['failed'] == 1


def test_concurrency_no_locked_errors(client, admin_headers, sample_png):
    def one(i):
        import json

        return client.post(
            '/v1/ocr',
            files={'file': ('t{}.png'.format(i), sample_png + bytes([i]))},
            data={'json': json.dumps({'engine': 'FakeOcr', 'cache': False})},
            headers=admin_headers,
        ).status_code

    with cf.ThreadPoolExecutor(max_workers=16) as pool:
        codes = list(pool.map(one, range(50)))
    assert codes.count(200) == 50


def test_stats_usage_settings_audit(client, admin_headers, sample_png):
    _ocr(client, admin_headers, sample_png, engine='FakeOcr')
    s = client.get('/v1/stats/summary', headers=admin_headers).json()
    assert s['summary']['runs'] >= 1
    assert client.get('/v1/usage?group_by=engine', headers=admin_headers).json()['items']
    assert (
        client.patch('/v1/settings', json={'values': {'log_retention_days': 7}}, headers=admin_headers).status_code
        == 200
    )
    assert client.patch('/v1/settings', json={'values': {'port': 1}}, headers=admin_headers).status_code == 400
    assert client.get('/v1/audit', headers=admin_headers).json()['items']
    assert 'ocrroute_runs_total' in client.get('/metrics').text
    assert client.get('/v1/doctor', headers=admin_headers).json()['engines_total'] > 50


def test_ready_and_route_validation(client, admin_headers):
    assert client.get('/v1/ready').json()['status'] == 'ready'
    assert client.post('/v1/routes', json={'name': 'Bad Name!'}, headers=admin_headers).status_code == 422
    assert client.post('/v1/routes', json={'name': 'x', 'strategy': 'nope'}, headers=admin_headers).status_code == 400
