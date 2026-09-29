# coding=utf-8
"""Language pickers in a real headless browser: the Playground and Batch pages. Skipped without Playwright's Chromium."""
from __future__ import absolute_import, division, print_function

import json

import pytest

from tests.integration.test_cluster_page_browser import _browser, _login
from tests.integration.test_sync_cluster import _port, _serve, _wait

sync_playwright = pytest.importorskip('playwright.sync_api').sync_playwright


def test_playground_and_batch_language_pickers(tmp_path):
    port = _port()
    proc = _serve(tmp_path / 'home', port)
    base = 'http://127.0.0.1:{}'.format(port)
    errors, sent = [], []
    try:
        _wait(base, proc)
        with sync_playwright() as p:
            browser = _browser(p)
            page = browser.new_page()
            page.on('pageerror', lambda e: errors.append(str(e)))
            _login(page, base)

            def capture(route):  # what the Playground really sends, answered with a minimal result
                body = route.request.post_data_buffer or b''
                text = body.decode('utf-8', 'replace')
                start = text.find('{"language"')
                sent.append(json.loads(text[start:text.index('\r\n', start)]) if start >= 0 else {})
                route.fulfill(status=200, content_type='application/json', body=json.dumps({
                    'status': 'failed', 'error_message': 'intercepted by the test', 'routing': {'explain': []}}))

            page.route('**/v1/ocr', capture)
            page.goto(base + '/panel/playground')
            page.wait_for_function("document.querySelectorAll('#lang option').length > 100")
            assert page.input_value('#lang') == 'auto' and page.is_hidden('#variant-wrap')  # default route
            # Tesseract: only its installed languages, no auto
            page.select_option('#target', 'engine:Tesseract')
            page.wait_for_function("![...document.querySelectorAll('#lang option')].some(o => o.value === 'auto')")
            assert page.input_value('#lang') == 'en'
            # OCR.Space: variants appear; engine 2 = auto + 24; engine 1 = 24 without auto; engine 3 = auto only
            page.select_option('#target', 'engine:OcrSpace')
            page.wait_for_selector('#variant-wrap:not(.d-none)')
            page.wait_for_function("document.querySelectorAll('#lang option').length === 25")
            assert page.input_value('#variant') == '2' and page.input_value('#lang') == 'auto'
            page.select_option('#variant', '1')
            page.wait_for_function("document.querySelectorAll('#lang option').length === 24")
            assert page.input_value('#lang') == 'en'
            page.select_option('#lang', 'ar')
            page.select_option('#variant', '2')  # the user's choice is kept when the new variant supports it
            page.wait_for_function("document.querySelectorAll('#lang option').length === 25")
            assert page.input_value('#lang') == 'ar'
            page.select_option('#variant', '3')
            page.wait_for_function("document.querySelectorAll('#lang option').length === 1")
            assert page.input_value('#lang') == 'auto'
            page.select_option('#variant', '1')
            page.wait_for_function("document.querySelectorAll('#lang option').length === 24")
            page.select_option('#lang', 'ar')
            # what is sent: the canonical language and the variant as the engine option
            page.set_input_files('#file', files=[{'name': 't.png', 'mimeType': 'image/png', 'buffer': _png()}])
            page.click('#run')
            page.wait_for_timeout(1500)
            assert sent, 'no OCR request was sent'
            assert sent[-1]['language'] == 'ar' and sent[-1]['engine'] == 'OcrSpace' and sent[-1]['options']['engine'] == 1
            # a vision-language model: auto, and every language as a prompt hint
            page.select_option('#target', 'engine:ClaudeOcr')
            page.wait_for_function("!document.querySelector('#lang').disabled && document.querySelectorAll('#lang option').length > 100")
            assert page.input_value('#lang') == 'auto' and page.is_hidden('#variant-wrap')
            assert 'hint' in page.inner_text('#lang-note')
            # several languages: the "Also:" adder appears; the request carries the list (an "auto" first steps aside)
            page.wait_for_selector('.lang-multi:not(.d-none) .lang-add')
            page.select_option('.lang-multi .lang-add', 'fr')
            page.wait_for_selector('.lang-chip[data-c="fr"]')
            page.select_option('.lang-multi .lang-add', 'ar')
            page.wait_for_selector('.lang-chip[data-c="ar"]')
            page.click('#run')
            page.wait_for_timeout(1500)
            assert sent[-1]['language'] == ['fr', 'ar'], sent[-1]
            page.click('.lang-chip[data-c="fr"] .lang-x')
            page.wait_for_selector('.lang-chip[data-c="fr"]', state='detached')
            # model picker: shown for Claude with its known models; a typed model is sent as options.model
            page.wait_for_selector('#model-wrap:not(.d-none)')
            page.wait_for_function("document.querySelectorAll('#model-list option').length >= 2")
            assert 'claude-sonnet-4-6' in page.eval_on_selector_all('#model-list option', 'os => os.map(o => o.value)')
            assert page.get_attribute('#model', 'placeholder') == 'claude-sonnet-4-6'
            page.fill('#model', 'claude-opus-4-1')
            assert page.is_visible('#prompt-wrap')
            page.click('#run')
            page.wait_for_timeout(1500)
            assert sent[-1].get('options', {}).get('model') == 'claude-opus-4-1', sent[-1]
            # a single-language engine has no adder
            page.select_option('#target', 'engine:OcrSpace')
            page.wait_for_selector('#variant-wrap:not(.d-none)')  # OCR.Space's own list has loaded
            page.wait_for_function("document.querySelector('.lang-multi').classList.contains('d-none')")
            page.wait_for_function("document.querySelector('#model-wrap').classList.contains('d-none')")  # no model setting
            page.wait_for_function("document.querySelector('#prompt-wrap').classList.contains('d-none')")  # no prompt either
            # a fixed script: no setting, and what it reads
            page.select_option('#target', 'engine:TrOcr')
            page.wait_for_function("document.querySelector('#lang').disabled")
            assert 'English' in page.inner_text('#lang-note')
            # Batch: every language, auto preselected
            page.goto(base + '/panel/batch')
            page.wait_for_function("document.querySelectorAll('#lang option').length > 100")
            assert page.input_value('#lang') == 'auto'
            assert not errors, errors
            browser.close()
    finally:
        proc.terminate()
        proc.wait(timeout=10)


def _png():
    from io import BytesIO

    from PIL import Image

    buf = BytesIO()
    Image.new('RGB', (40, 20), 'white').save(buf, 'PNG')
    return buf.getvalue()
