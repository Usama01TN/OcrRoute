# coding=utf-8
from __future__ import absolute_import, division, print_function

from ocrroute import i18n
from ocrroute.compat import py23


def test_catalogues_complete_and_translate():
    codes = [c for c, _ in i18n.languages()]
    assert codes[0] == 'en' and set(codes) >= {'ar', 'de', 'es', 'fr'}
    en = i18n.catalogue('en')
    for code in codes:
        cat = i18n.catalogue(code)
        assert set(cat) == set(en), code  # every language covers the whole vocabulary
    assert i18n.translate('Run OCR', 'fr') == "Lancer l'OCR"
    assert i18n.translate('Run OCR', 'en') == 'Run OCR'
    assert i18n.translate('{n} min ago', 'de', n=5) == 'vor 5 Min.'
    assert i18n.translate('not in catalogue', 'ar') == 'not in catalogue'  # graceful fallback


def test_language_detection_and_rtl():
    assert i18n.normalise('fr-FR') == 'fr' and i18n.normalise('xx') == 'en' and i18n.normalise(None) == 'en'
    assert i18n.isRtl('ar') and not i18n.isRtl('fr')
    assert i18n.pickFromHeader('es-ES,es;q=0.9,en;q=0.8') == 'es'
    assert i18n.pickFromHeader('') == 'en'


def test_relative_time():
    from datetime import datetime, timedelta, timezone

    now = datetime(2026, 9, 22, 12, 0, tzinfo=timezone.utc)
    assert i18n.relativeTime((now - timedelta(seconds=10)).isoformat(), 'en', now) == 'just now'
    assert i18n.relativeTime((now - timedelta(minutes=3)).isoformat(), 'fr', now) == 'il y a 3 min'
    assert i18n.relativeTime((now - timedelta(hours=5)).isoformat(), 'en', now) == '5 h ago'
    assert i18n.relativeTime('', 'es', now) == 'nunca'


def test_py23_helpers():
    assert py23.PY3 and not py23.PY2
    assert py23.ensureText(b'abc') == 'abc' and py23.ensureBytes('abc') == b'abc'
    assert py23.mergeDicts({'a': 1}, {'b': 2}, None, {'a': 3}) == {'a': 3, 'b': 2}
    import pytest

    with pytest.raises(ValueError) as exc:
        try:
            raise KeyError('inner')
        except KeyError as inner:
            py23.raiseFrom(ValueError('outer'), inner)
    assert isinstance(exc.value.__cause__, KeyError)


def test_engine_rediscovery_and_change_detection(ctx):
    reg = ctx.registry
    before = len(reg.all())
    assert reg.changed() is False
    assert reg.rescanIfChanged() is False  # nothing changed on disk → no rescan
    reg.discover(force=True)  # runs AioOCR._discoverOcrPlugins() again
    assert len(reg.all()) >= before
    assert 'Tesseract' in [e.id for e in reg.all()]


def test_providers_are_seeded_for_every_available_engine(ctx):
    from ocrroute.db.models import Provider
    from ocrroute.db.session import sessionScope

    with sessionScope() as s:
        seeded = {p.engine_id for p in s.query(Provider).all()}
    available = {e.id for e in ctx.registry.available()}
    assert available <= seeded | {'FakeOcr', 'FakeAuthFail', 'FakeEmpty', 'FakeSlow'}  # test fakes register after seeding
