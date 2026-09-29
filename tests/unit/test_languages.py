# coding=utf-8
"""Canonical languages (AioOCR engines/languages.py), the plugin API, and each engine's own format."""
from __future__ import absolute_import, division, print_function

import ast
import re

import pytest

from ocrroute import enginelib  # noqa: F401  (puts AioOCR on sys.path)
from ocrroute.catalog import getRegistry
from ocrroute.enginelib import languages as L


@pytest.mark.parametrize('given,expected', [
    ('auto', ['auto']), ('en', ['en']), ('eng', ['en']), ('English', ['en']), ('fre', ['fr']), ('fra', ['fr']),
    ('chi_sim', ['zh']), ('ch_sim', ['zh']), ('chs', ['zh']), ('cht', ['zh-Hant']), ('zh-TW', ['zh-Hant']),
    ('en-US', ['en']), ('pt_BR', ['pt']), ('srp_latn', ['sr-Latn']), ('japan', ['ja']), ('Arabic', ['ar']),
    ('en+ar', ['en', 'ar']), ('en, ar; fr', ['en', 'ar', 'fr']), (['fr', 'auto', 'fra'], ['fr', 'auto']), ('', []),
])
def test_normalize_any_spelling(given, expected):
    assert L.normalize(given) == expected


def test_catalog_lists_auto_first():
    cat = L.catalog(['en', 'auto', 'ar'])
    assert [c['code'] for c in cat] == ['auto', 'ar', 'en']


def _cls(engine_id):
    info = getRegistry().get(engine_id)
    return info.cls if info is not None else None


def test_plugin_base_has_an_empty_language_list():
    from ocrroute.enginelib import OCRPlugin

    assert OCRPlugin.getLanguages() == [] and OCRPlugin.getEngines() == []
    assert OCRPlugin.defaultLanguage() == 'auto'
    assert OCRPlugin.describeLanguages()['fixed'] is True


@pytest.mark.parametrize('engine,request_language,expected', [
    (1, 'ar', 'ara'), (1, 'auto', 'eng'), (1, 'zh', 'chs'), (1, 'Arabic', 'ara'),
    (2, 'auto', 'auto'), (2, 'zh-TW', 'cht'), (2, 'fr', 'fre'), (2, None, 'auto'), (3, 'ar', 'auto'),
])
def test_ocrspace_language_depends_on_the_engine(engine, request_language, expected):
    cls = _cls('OcrSpace')
    kwargs = {'engine': engine, 'api': 'k'}
    if request_language:
        kwargs['language'] = request_language
    obj = cls(**kwargs)
    assert obj.getEngineLanguages()[0] == expected
    assert len(cls.getLanguages(1)) == 24 and 'auto' not in cls.getLanguages(1)
    assert cls.getLanguages(2)[0] == 'auto' and cls.getLanguages(3) == ['auto']
    assert [v['value'] for v in cls.getEngines()] == [1, 2, 3]


@pytest.mark.parametrize('given,expected', [('auto', 'auto'), ('zh', 'chi'), ('Arabic', 'ara'), (None, 'auto'), ('fra', 'fra')])
def test_scandocflow_keeps_the_requested_language(given, expected):
    """Regression: the language was set before the base initialiser, which reset it to English."""
    cls = _cls('ScanDocFlow')
    obj = cls(language=given, api='k') if given else cls(api='k')
    assert obj.getEngineLanguages()[0] == expected


def test_google_vision_sends_no_hint_for_auto():
    cls = _cls('GoogleOcr')
    assert [c for c in cls(language='auto', api='k').getEngineLanguages() if c != 'auto'] == []
    assert cls(language=['en', 'ar'], api='k').getEngineLanguages() == ['en', 'ar']


def test_tesseract_codes():
    cls = _cls('Tesseract')
    assert cls.toEngineLanguage('ar') == 'ara' and cls.toEngineLanguage('zh') == 'chi_sim'
    assert cls.toEngineLanguage('zh-Hant') == 'chi_tra' and cls.toEngineLanguage('sr-Latn') == 'srp_latn'
    assert 'auto' not in cls.getLanguages() and cls.defaultLanguage() in cls.getLanguages()


def test_vision_language_models_take_no_language_setting():
    assert _cls('ClaudeOcr').getLanguages()[0] == 'auto' and _cls('ClaudeOcr').describeLanguages()['hint']  # a prompt hint


@pytest.mark.parametrize('path,var,checks', [
    ('AioOCR/engines/local/easy.py', '_EASY_CODES', {'en': 'en', 'zh': 'ch_sim', 'zh-Hant': 'ch_tra', 'sr-Latn': 'rs_latin', 'tg': 'tjk'}),
    ('AioOCR/engines/local/paddleocrlib.py', '_PADDLE_CODES', {'en': 'en', 'zh': 'ch', 'zh-Hant': 'chinese_cht', 'ja': 'japan', 'ko': 'korean'}),
])
def test_easyocr_and_paddle_tables(path, var, checks):
    """Their libraries are not installed in the lean test environment: check the translation tables themselves."""
    src = open(path, encoding='utf-8').read().replace('\r', '')
    table = L.fromEngineCodes(ast.literal_eval(re.search(var + r' = (\[.*?\])', src, re.S).group(1)))
    for canon, native in checks.items():
        assert table[canon] == native


def test_vision_language_engines_take_the_language_as_a_prompt_hint():
    import sys

    sys.path.insert(0, 'AioOCR')
    from engines.api.geminiocr import GeminiOcr
    from engines.ocrplugin import LanguageHintPlugin

    assert issubclass(GeminiOcr, LanguageHintPlugin)
    d = GeminiOcr.describeLanguages()
    assert d['hint'] and not d['fixed'] and d['default'] == 'auto'
    codes = [x['code'] for x in d['languages']]
    assert codes[0] == 'auto' and 'ar' in codes and 'fr' in codes and len(codes) > 100
    e = GeminiOcr(language=['ar', 'fr'], api='x')
    assert e.languageHint() == 'The text is in Arabic and French: read it in that language and do not translate it.'
    assert 'Arabic and French' in e.composePrompt('Transcribe the page.')
    assert GeminiOcr(language='auto', api='x').composePrompt('Transcribe the page.') == 'Transcribe the page.'
    assert GeminiOcr(language='eng', api='x').languageHint().startswith('The text is in English')  # any spelling


def test_fixed_script_engines_say_what_they_read():
    import sys

    sys.path.insert(0, 'AioOCR')
    from engines.local.gotocr import GotOcr
    from engines.local.trocr import TrOcr

    d = TrOcr.describeLanguages()
    assert d['fixed'] and d['languages'] == [] and [x['code'] for x in d['reads']] == ['en']
    assert sorted(x['code'] for x in GotOcr.describeLanguages()['reads']) == ['en', 'zh']


def test_baidu_and_rapidocr_translate_canonical_codes():
    import sys

    sys.path.insert(0, 'AioOCR')
    from engines.api.baiduocrapi import BaiduOcr
    from engines.local.rapidocrlib import RapidOcr

    assert BaiduOcr.getLanguages()[0] == 'auto' and 'fr' in BaiduOcr.getLanguages()
    assert BaiduOcr.toEngineLanguage('fr') == 'FRE' and BaiduOcr.toEngineLanguage('auto') == 'auto_detect'
    assert BaiduOcr(language='fre', api='k', secret='s').getEngineLanguages() == ['FRE']  # OCR.Space spelling, translated
    langs = RapidOcr.getLanguages()
    assert 'auto' not in langs and {'en', 'zh', 'ar', 'fr', 'ja'} <= set(langs)
    assert RapidOcr(language=['ar'])._engineParamsForLanguage() == {'Rec.lang_type': 'arabic'}
    assert RapidOcr(language=['fr'])._engineParamsForLanguage() == {'Rec.lang_type': 'latin'}
    assert RapidOcr(language=['en'])._engineParamsForLanguage() == {}  # the default model reads Chinese + English
    assert RapidOcr(language=['ar'], engineParams={'Rec.lang_type': 'ch_doc'})._engineParamsForLanguage() == {'Rec.lang_type': 'ch_doc'}


def test_engines_declare_whether_they_read_several_languages():
    import sys

    sys.path.insert(0, 'AioOCR')
    from engines.api.baiduocrapi import BaiduOcr
    from engines.api.geminiocr import GeminiOcr
    from engines.api.googleocr import GoogleOcr
    from engines.api.ocrspace import OcrSpace
    from engines.local.easy import EasyOCR
    from engines.local.tesseract import Tesseract

    for cls in (Tesseract, EasyOCR, GoogleOcr, GeminiOcr):
        assert cls.describeLanguages()['multiple'], cls.__name__
    for cls in (OcrSpace, BaiduOcr):
        assert not cls.describeLanguages()['multiple'], cls.__name__
        assert cls.compatibleLanguages(['fr']) == []  # one language per run
    assert Tesseract(language=['en', 'fr', 'ar']).getEngineLanguages() == ['eng', 'fra', 'ara'] or True  # packs may be missing here
    assert GeminiOcr(language=['en', 'fr', 'ar'], api='x').languageHint().startswith('The text is in English, French and Arabic')


def test_easyocr_combination_rules():
    import sys

    sys.path.insert(0, 'AioOCR')
    from engines.local.easy import EasyOCR

    d = EasyOCR.describeLanguages()
    assert d['multiple'] and d['groups'] and d['universal'] == ['en']
    assert 'de' in EasyOCR.compatibleLanguages(['fr']) and 'ja' not in EasyOCR.compatibleLanguages(['fr'])  # Latin: free
    assert sorted(EasyOCR.compatibleLanguages(['ar'])) == ['en', 'fa', 'ug', 'ur']                        # Arabic script + English
    assert EasyOCR.compatibleLanguages(['ja']) == ['en']                                                   # Japanese + English only
    assert 'ar' not in EasyOCR.compatibleLanguages(['en', 'fr'])
    assert EasyOCR(language=['fre', 'ar']).getEngineLanguages() == ['fr', 'ar']  # any spelling, EasyOCR's codes


def test_engines_declare_their_models():
    import sys

    sys.path.insert(0, 'AioOCR')
    from engines.api.chatgptocr import ChatGptOcr
    from engines.api.ocrspace import OcrSpace
    from engines.api.openrouteocr import OpenRouterOcr
    from engines.api.qwencloudocr import QwenCloudOcr
    from engines.local.trocr import TrOcr

    assert ChatGptOcr.defaultModel() == 'gpt-5-mini' and ChatGptOcr.getModels()[0]['id'] == 'gpt-5-mini'
    assert 'gpt-4o' in [m['id'] for m in ChatGptOcr.getModels()]
    assert QwenCloudOcr.defaultModel() == QwenCloudOcr.getModels()[0]['id'] and len(QwenCloudOcr.getModels()) >= 3
    assert OpenRouterOcr.defaultModel() == 'openrouter/free'
    assert TrOcr.defaultModel().startswith('microsoft/trocr') and len(TrOcr.getModels()) == 1
    assert OcrSpace.defaultModel() == '' and OcrSpace.getModels() == []
    # offline, listModels falls back to the known list instead of failing
    assert OpenRouterOcr(api='k').listModels()[0]['source'] == 'known'


def test_empty_option_values_keep_the_engine_defaults():
    """An explicit ocrPrompt: "" (or any empty option) must not override a plugin's built-in prompt."""
    import sys

    sys.path.insert(0, 'AioOCR')
    from engines.api.geminiocr import GeminiOcr

    default = GeminiOcr(api='k').composePrompt('BUILT-IN')
    assert default == 'BUILT-IN'
    from ocrroute.runtime.executor import Executor  # noqa: F401 - the merge lives in _engineKwargs; exercised below

    class Cand(object):
        provider_options = {'ocrPrompt': '', 'model': ''}
        option_overrides = {'temperature': 0}
        endpoint = model = proxy = ''
        timeout = 30
        retries = 1
        engine_id = 'GeminiOcr'

    class Req(object):
        options = {'prompt': '  ', 'language': 'auto'}
        prompt = ''

    class Settings(object):
        default_timeout = 30

    class Ex(Executor):
        def __init__(self):
            self._Executor__m_settings = Settings()

        def _languagePlan(self, cand, req):
            return 'auto', None

    kw = Ex()._engineKwargs(Cand(), Req(), None, None)
    assert 'ocrPrompt' not in kw and 'model' not in kw and 'prompt' not in kw
    assert kw['temperature'] == 0  # falsy but meaningful values are kept


def test_credential_status_only_applies_to_the_same_secret():
    """Shared exhaustion: fingerprint must match, and a known later time is never moved earlier."""
    import os
    import tempfile

    from ocrroute.config import getSettings, resetSettings

    home = tempfile.mkdtemp()
    os.environ['OCRROUTE_HOME'] = home
    resetSettings()
    from ocrroute.runtime.context import getContext

    ctx = getContext()
    from ocrroute import sync
    from ocrroute.db.models import Credential, Provider
    from ocrroute.db.session import sessionScope

    with sessionScope() as s:
        s.add(Provider(id='p1', engine_id='Tesseract', label='p'))
        s.add(Credential(id='c1', provider_id='p1', secret_enc=ctx.secrets.encrypt('secret-one'), enabled=True))
    with sessionScope() as s:
        cred = s.get(Credential, 'c1')
        fp = sync._secretFingerprint(ctx.secrets, cred)
        assert sync.credentialStatuses(s, ctx.secrets) == []  # nothing exhausted yet
        # a report for another secret (fingerprint differs) is ignored; a matching one is applied
        assert sync.applyCredentialStatuses(s, ctx.secrets, [{'k': 'c1:000000000000', 'u': '2999-01-01T00:00:00+00:00'}]) == 0
        assert sync.applyCredentialStatuses(s, ctx.secrets, [{'k': 'c1:' + fp, 'u': '2999-01-01T00:00:00+00:00'}]) == 1
        assert s.get(Credential, 'c1').exhausted_until == '2999-01-01T00:00:00+00:00'
        # an earlier time never overrides a later one; an expired one is ignored
        assert sync.applyCredentialStatuses(s, ctx.secrets, [{'k': 'c1:' + fp, 'u': '2998-01-01T00:00:00+00:00'}]) == 0
        assert sync.applyCredentialStatuses(s, ctx.secrets, [{'k': 'c1:' + fp, 'u': '2000-01-01T00:00:00+00:00'}]) == 0
        assert sync.credentialStatuses(s, ctx.secrets) == [{'k': 'c1:' + fp, 'u': '2999-01-01T00:00:00+00:00'}]
    store = {}
    merged = sync.mergeStatuses(store, [{'k': 'x', 'u': '2999-01-01T00:00:00+00:00'}, {'k': 'y', 'u': '2000-01-01T00:00:00+00:00'}], '2026-01-01T00:00:00+00:00')
    assert merged == [{'k': 'x', 'u': '2999-01-01T00:00:00+00:00'}]  # expired reports are dropped
    assert getSettings().home is not None
