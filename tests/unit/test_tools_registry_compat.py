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
