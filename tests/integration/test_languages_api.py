# coding=utf-8
from __future__ import absolute_import, division, print_function


def test_language_endpoints(client, admin_headers):
    all_ = client.get('/v1/languages', headers=admin_headers).json()
    assert all_['languages'][0]['code'] == 'auto' and len(all_['languages']) > 100
    d = client.get('/v1/engines/OcrSpace/languages', headers=admin_headers).json()
    assert d['available'] and d['engine'] == 2 and d['default'] == 'auto' and len(d['languages']) == 25
    assert [v['value'] for v in d['engines']] == [1, 2, 3]
    one = client.get('/v1/engines/OcrSpace/languages?engine=1', headers=admin_headers).json()
    assert one['default'] == 'en' and 'auto' not in [x['code'] for x in one['languages']]
    three = client.get('/v1/engines/OcrSpace/languages?engine=3', headers=admin_headers).json()
    assert [x['code'] for x in three['languages']] == ['auto']
    vlm = client.get('/v1/engines/ClaudeOcr/languages', headers=admin_headers).json()
    assert vlm['hint'] and not vlm['fixed'] and vlm['languages'][0]['code'] == 'auto'  # detects; a choice is a prompt hint
    assert client.get('/v1/engines/NoSuchEngine/languages', headers=admin_headers).status_code == 404


def test_executor_passes_canonical_codes_and_explains_substitutions(client, admin_headers, sample_png, monkeypatch):
    """The engine receives canonical codes (it translates them itself); unsupported ones are replaced and explained."""
    import json

    from fixtures.fakeocr import FakeOcr

    seen = []
    realRun = FakeOcr._run

    def recordingRun(self, image, *args, **kwargs):
        seen.append(self.getLanguage())
        return realRun(self, image, *args, **kwargs)

    monkeypatch.setattr(FakeOcr, 'getLanguages', classmethod(lambda cls, engine=None: ['en', 'fr']))
    monkeypatch.setattr(FakeOcr, '_run', recordingRun)

    def run(lang):
        body = {'engine': 'FakeOcr', 'cache': False}
        if lang is not None:
            body['language'] = lang
        r = client.post('/v1/ocr', files={'file': ('t.png', sample_png)}, headers=admin_headers,
                        data={'json': json.dumps(body)}).json()
        assert r['status'] == 'succeeded', r.get('error_message')
        return [x for x in r['routing']['explain'] if x.startswith('FakeOcr:')]

    assert run('French') == [] and seen[-1] == 'fr'          # any spelling -> canonical code
    assert run(['eng', 'fra']) == [] and seen[-1] == ['en', 'fr']
    note = run('ar')
    assert seen[-1] == 'en' and note and 'Arabic not available' in note[0] and 'used English' in note[0]
    note = run(None)                                          # default "auto", which this engine cannot do
    assert seen[-1] == 'en' and 'automatic detection not available' in note[0]


def test_every_engine_reports_its_language_mode(client, admin_headers):
    """Every engine answers the picker: a list, a hint list, or "fixed" with what it reads; nothing errors."""
    engines = client.get('/v1/engines', headers=admin_headers).json()['items']
    assert len(engines) >= 50
    modes = {'list': 0, 'hint': 0, 'fixed': 0, 'unavailable': 0}
    for e in engines:
        r = client.get('/v1/engines/{}/languages'.format(e['id']), headers=admin_headers)
        assert r.status_code == 200, e['id']
        d = r.json()
        if d.get('available') is False:
            modes['unavailable'] += 1
        elif d['fixed']:
            modes['fixed'] += 1
        elif d.get('hint'):
            modes['hint'] += 1
        else:
            modes['list'] += 1
            assert d['default'] in [x['code'] for x in d['languages']], e['id']
    assert modes['hint'] >= 15 and modes['list'] >= 4 and modes['fixed'] >= 5, modes
    gem = client.get('/v1/engines/GeminiOcr/languages', headers=admin_headers).json()
    assert gem['hint'] and gem['default'] == 'auto' and gem['languages'][0]['code'] == 'auto'
    tro = client.get('/v1/engines/TrOcr/languages', headers=admin_headers).json()
    assert tro['fixed'] and [x['code'] for x in tro['reads']] == ['en']
    baidu = client.get('/v1/engines/BaiduOcr/languages', headers=admin_headers).json()
    assert baidu['default'] == 'auto' and 'fr' in [x['code'] for x in baidu['languages']]


def test_multiple_languages_are_described_for_the_picker(client, admin_headers):
    easy = client.get('/v1/engines/EasyOCR/languages', headers=admin_headers).json()
    assert easy['multiple'] and easy['groups'] and ['ja'] in easy['groups'] and easy['universal'] == ['en']
    space = client.get('/v1/engines/OcrSpace/languages', headers=admin_headers).json()
    assert not space['multiple']
    gem = client.get('/v1/engines/GeminiOcr/languages', headers=admin_headers).json()
    assert gem['multiple'] and gem['groups'] == []
