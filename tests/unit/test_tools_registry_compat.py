# coding=utf-8
"""
None
"""

from ocrroute.catalog.registry import EngineRegistry
from ocrroute.compat import OcrBase
from ocrroute.tools import ToolPlugin, getToolRegistry


def test_tools_registry_is_empty_and_contract_abstract():
    reg = getToolRegistry()
    assert reg.list() == [] and reg.get('anything') is None
    import pytest

    with pytest.raises(TypeError):
        ToolPlugin()  # abstract


def test_registry_discovers_all_engines_and_introspects_options():
    reg = EngineRegistry()
    engines = reg.all()
    ids = {e.id for e in engines}
    assert {'Tesseract', 'OcrSpace', 'GeminiOcr', 'RapidOcr'} <= ids
    assert len(engines) >= 51
    for e in engines:
        if not e.available:
            assert e.import_error and e.install_hint, e.id
    tess = reg.get('Tesseract')
    assert any(o.name == 'minConfidence' for o in tess.options)
    assert tess.kind == 'local' and not tess.requires_key
    assert reg.get('OcrSpace').requires_key


def test_compat_shim_lists_engines_and_returns_empty_result_for_unknown():
    b = OcrBase()
    assert 'Tesseract' in b.engines()
    res = b.parse(NoSuchEngine=[{'image': 'x'}])
    assert res['FileParseExitCode'] in (1, -1) and 'ParsedText' in res


def test_engine_install_plans(monkeypatch):
    import sys

    from ocrroute.runtime import engineinstall as ei

    plan = ei.planFor('AioOCR.engines.local.easy')
    assert plan['extra'] == 'easyocr' and plan['framework'] == 'PyTorch' and plan['installable']
    assert ei.planFor('engines.api.mistralocr')['packages'] == ['mistralai>=1.0']
    assert ei.planFor('engines.local.tesseract') is None  # nothing to install
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    frozen = ei.planFor('engines.local.suryaocr')
    assert not frozen['installable'] and 'stand-alone executable' in frozen['hint'] and 'ocrroute[surya]' in frozen['hint']
    assert ei.install('engines.local.suryaocr')['ok'] is False  # pip never runs inside a frozen bundle


def test_engine_install_explains_pep668(monkeypatch):
    import subprocess

    from ocrroute.runtime import engineinstall as ei

    class R(object):
        returncode = 1
        stdout = ''
        stderr = 'error: externally-managed-environment\n× This environment is externally managed'

    monkeypatch.setattr(subprocess, 'run', lambda *a, **k: R())
    r = ei.install('engines.local.easy')
    assert r['ok'] is False and r['externally_managed'] is True
    assert 'virtual environment' in r['output'] and 'ocrroute[easyocr]' in r['output']


def test_import_warnings_are_captured_not_printed():
    from ocrroute import enginelib

    assert isinstance(enginelib.IMPORT_WARNINGS, list)  # AioOCR's prints land here, not on stderr
