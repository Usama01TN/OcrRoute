# coding=utf-8
"""The Playground language dropdown in a real browser: per-engine lists, hints, fixed scripts, OCR.Space variants."""
from __future__ import absolute_import, division, print_function

import pytest

from tests.integration.test_cluster_page_browser import _browser, _login
from tests.integration.test_sync_cluster import _port, _serve, _wait

sync_playwright = pytest.importorskip('playwright.sync_api').sync_playwright


def test_language_dropdown_follows_the_engine(tmp_path):
    port = _port()
    proc = _serve(tmp_path / 'home', port)
    base = 'http://127.0.0.1:{}'.format(port)
    errors = []
    try:
        _wait(base, proc)
        with sync_playwright() as p:
            browser = _browser(p)
            page = browser.new_page()
            page.on('pageerror', lambda e: errors.append(str(e)))
            _login(page, base)
            page.goto(base + '/panel/playground')
            page.wait_for_selector('#lang')

            def pick(engine):
                page.select_option('#target', 'engine:' + engine)
                page.wait_for_function("document.querySelector('#lang').options.length > 0 && !document.querySelector('#lang').dataset.loading")
                page.wait_for_timeout(400)

            pick('GeminiOcr')  # vision-language model: auto first, every language as a hint
            opts = page.eval_on_selector_all('#lang option', 'os => os.map(o => o.value)')
            assert opts[0] == 'auto' and page.input_value('#lang') == 'auto' and len(opts) > 100 and 'ar' in opts
            assert 'hint' in page.inner_text('#lang-note')
            pick('TrOcr')  # fixed script: no setting, says what it reads
            assert page.is_disabled('#lang') and 'English' in page.inner_text('#lang-note')
            pick('OcrSpace')  # variants: engine 1 has no auto, engine 2 has it
            assert page.is_visible('#variant')
            page.select_option('#variant', '1')
            page.wait_for_function("!document.querySelector('#lang').options[0] || document.querySelector('#lang').options[0].value !== 'auto'")
            assert page.input_value('#lang') == 'en'
            page.select_option('#variant', '2')
            page.wait_for_function("document.querySelector('#lang').options[0] && document.querySelector('#lang').options[0].value === 'auto'")
            assert page.input_value('#lang') == 'auto'
            pick('BaiduOcr')
            assert page.input_value('#lang') == 'auto' and 'fr' in page.eval_on_selector_all('#lang option', 'os => os.map(o => o.value)')
            assert not errors, errors
            browser.close()
    finally:
        proc.terminate()
        proc.wait(timeout=10)


def test_provider_form_offers_models_and_a_prompt_box(tmp_path):
    port = _port()
    proc = _serve(tmp_path / 'home', port)
    base = 'http://127.0.0.1:{}'.format(port)
    errors = []
    try:
        _wait(base, proc)
        with sync_playwright() as p:
            browser = _browser(p)
            page = browser.new_page()
            page.on('pageerror', lambda e: errors.append(str(e)))
            _login(page, base)
            page.goto(base + '/panel/providers#new=ChatGptOcr')
            page.wait_for_selector('#prov-dlg[open]')
            page.wait_for_function("document.querySelectorAll('#dl-model option').length >= 2")
            assert 'gpt-5-mini' in page.eval_on_selector_all('#dl-model option', 'os => os.map(o => o.value)')
            assert page.get_attribute('[data-opt="model"]', 'placeholder') == 'gpt-5-mini'
            assert page.locator('textarea[data-opt="prompt"]').count() == 1
            assert page.locator('#prov-form input[name="model"]').count() == 0  # one model field: the engine option
            # save a provider with a model and two languages (ChatGPT takes several); reopen: both come back
            page.wait_for_function("document.querySelectorAll('#prov-lang option').length > 1")
            page.fill('#prov-form input[name="label"]', 'gpt-main')
            page.fill('[data-opt="model"]', 'gpt-4o')
            page.select_option('#prov-lang', 'fr')
            page.wait_for_selector('#prov-form .lang-multi:not(.d-none) .lang-add')
            page.select_option('#prov-form .lang-multi .lang-add', 'de')
            page.click('#prov-form button[type="submit"]')
            page.wait_for_selector('td:has-text("gpt-main")', timeout=30000)  # the page reloads after saving
            saved = page.evaluate("async () => (await OcrRoute.api('GET', '/v1/providers')).items.find(p => p.label === 'gpt-main')")
            assert saved and saved['model'] == 'gpt-4o' and saved['options']['model'] == 'gpt-4o' and saved['language'] == 'fr,de', saved
            page.evaluate("id => document.querySelector('.edit[data-id=\"' + id + '\"]').click()", saved['id'])
            page.wait_for_selector('#prov-dlg[open]')
            page.wait_for_function("document.querySelector('[data-opt=\"model\"]') && document.querySelector('[data-opt=\"model\"]').value === 'gpt-4o'")
            page.wait_for_function("document.querySelector('#prov-lang').value === 'fr' && !!document.querySelector('#prov-form .lang-chip[data-c=\"de\"]')")
            page.click('#prov-dlg .cancel, #prov-dlg [value=cancel], #prov-dlg button:has-text("Cancel")', timeout=3000) if page.locator('#prov-dlg button:has-text("Cancel")').count() else None
            page.goto(base + '/panel/engines')  # a hash-only change would not reload the page
            page.goto(base + '/panel/providers#new=OcrSpace')
            page.wait_for_selector('#prov-dlg[open]')
            page.wait_for_timeout(300)
            assert page.locator('[data-opt="model"]').count() == 0 and page.locator('textarea[data-opt="prompt"]').count() == 0
            assert not errors, errors
            browser.close()
    finally:
        proc.terminate()
        proc.wait(timeout=10)
