# coding=utf-8
"""
None
"""

import pytest

from ocrroute.config import Settings
from ocrroute.enginelib import OCRPlugin
from ocrroute.errors import BadInput, UnsupportedInput
from ocrroute.pipeline import export
from ocrroute.pipeline.input import checkUrlAllowed, loadInput, parsePageSpec, sniffMime
from ocrroute.pipeline.postprocess import applyRtlOrder, applyTools, isRtlText, stitchPages
from ocrroute.routing.consensus import reconcile


def result_with(words):
    return OCRPlugin().buildResult([OCRPlugin.makeWord(*w) for w in words])


@pytest.mark.parametrize('host', ['127.0.0.1', '169.254.169.254', '10.1.2.3', '[::1]', 'localhost', '192.168.1.1'])
def test_ssrf_blocked(host, tmp_path):
    s = Settings(home=tmp_path)
    with pytest.raises(BadInput):
        checkUrlAllowed('http://{}/x'.format(host), s)


def test_ssrf_allowlist_and_scheme(tmp_path):
    s = Settings(home=tmp_path, url_allowlist=['example.com'])
    with pytest.raises(BadInput):
        checkUrlAllowed('http://other.com/', s)
    with pytest.raises(BadInput):
        checkUrlAllowed('ftp://example.com/', s)


def test_sniff_and_load(sample_png, tmp_path):
    assert sniffMime(sample_png) == 'image/png'
    assert sniffMime(b'%PDF-1.4') == 'application/pdf'
    s = Settings(home=tmp_path)
    doc = loadInput(settings=s, file_bytes=sample_png, filename='x.png')
    assert doc.width == 300 and doc.sha256
    with pytest.raises(UnsupportedInput):
        loadInput(settings=s, file_bytes=b'not an image at all', filename='x.txt')
    with pytest.raises(BadInput):
        loadInput(settings=s, path='/definitely/missing.png')
    with pytest.raises(BadInput):
        loadInput(settings=s, b64='!!!notbase64')


def test_page_spec():
    assert parsePageSpec('1-3,7', 10) == [0, 1, 2, 6]
    assert parsePageSpec('', 3) == [0, 1, 2]
    assert parsePageSpec('9-', 10) == [8, 9]


def test_all_exporters_and_searchable_pdf(sample_png):
    res = result_with([('Hello', 20, 40, 60, 12), ('World', 90, 40, 60, 12)])
    meta = {'width': 300, 'height': 100, 'image_bytes': sample_png}
    for kind in export.KINDS:
        data, mime, ext = export.write(kind, res, meta)
        assert data and mime and ext, kind
    pdf, _, _ = export.write('pdf', res, meta)
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(pdf)
    assert 'Hello' in doc[0].get_textpage().get_text_range()


def test_rtl_ordering_and_stitching():
    assert isRtlText('مرحبا بالعالم') and not isRtlText('hello')
    res = result_with([('مرحبا', 100, 10, 40, 12), ('بالعالم', 10, 10, 60, 12)])
    applyRtlOrder(res)
    assert res['TextOverlay']['Lines'][0]['LineText'].split()[0] == 'مرحبا'
    merged = stitchPages([result_with([('a', 0, 0, 5, 5)]), result_with([('b', 0, 0, 5, 5)])], [0, 1])
    assert merged['Pages'] == 2 and [ln['Page'] for ln in merged['TextOverlay']['Lines']] == [1, 2]
    assert '\f' in merged['ParsedText']


def test_apply_tools_is_identity_with_empty_chain():
    res = result_with([('x', 0, 0, 5, 5)])
    assert applyTools(res, []) is res and applyTools(res, None) is res


def test_consensus_votes():
    a = result_with([('hello', 0, 0, 50, 10), ('wor1d', 60, 0, 50, 10)])
    b = result_with([('hello', 1, 0, 50, 10), ('world', 61, 0, 50, 10)])
    c = result_with([('hello', 0, 1, 50, 10), ('world', 60, 1, 50, 10)])
    final, votes = reconcile([('A', 50, a), ('B', 60, b), ('C', 55, c)])
    assert final['ParsedText'] == 'hello world' and votes['disagreements'] == 1
