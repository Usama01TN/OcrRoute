# coding=utf-8
"""The Cluster page in a real headless browser (primary and member flows). Skipped without Playwright's Chromium."""
from __future__ import absolute_import, division, print_function

import pytest
import requests

from tests.integration.test_sync_cluster import _adminKey, _port, _serve, _wait

sync_playwright = pytest.importorskip('playwright.sync_api').sync_playwright


def _login(page, base):
    page.goto(base + '/panel/setup')
    page.fill('#u', 'admin')
    page.fill('#p', 'password123')
    page.fill('#c', 'password123')
    page.click('button[type=submit]')
    page.fill('#u', 'admin')
    page.fill('#p', 'password123')
    page.click('button[type=submit]')


def _browser(p):
    try:
        return p.chromium.launch()
    except Exception as exc:  # noqa: BLE001
        pytest.skip('no headless Chromium: {}'.format(exc))


def test_primary_flow_in_a_browser(tmp_path):
    port = _port()
    proc = _serve(tmp_path / 'home', port)
    base = 'http://127.0.0.1:{}'.format(port)
    errors, dialogs = [], []
    try:
        _wait(base, proc)
        with sync_playwright() as p:
            browser = _browser(p)
            page = browser.new_page()
            page.on('pageerror', lambda e: errors.append(str(e)))
            page.on('dialog', lambda d: (dialogs.append(d.message), d.dismiss()))
            _login(page, base)
            page.goto(base + '/panel/cluster')
            page.wait_for_selector('#view-standalone:not(.d-none)')
            page.fill('#cl-name', 'production')
            page.click('#create')
            page.wait_for_selector('#view-primary:not(.d-none)')
            assert 'production' in page.inner_text('#view-primary .cl-title')
            # add three servers at once: one join code each
            page.click('#add-servers')
            for i, name in enumerate(('ocr-paris', 'ocr-tunis', 'ocr-sfax')):
                if i:
                    page.click('#add-row')
                page.locator('.add-row .a-label').nth(i).fill(name)
            assert '(3)' in page.inner_text('#add-create')
            page.click('#add-create')
            page.wait_for_selector('#codes-box:not(.d-none)')
            codes = page.locator('#codes-list .code-value')
            assert codes.count() == 3 and all(codes.nth(i).input_value().startswith('OCRJ1-') for i in range(3))
            page.click('#copy-all')
            page.click('#codes-close')
            page.wait_for_function("document.querySelectorAll('.member-card').length === 3")
            assert page.inner_text('#member-count') == '4'  # the primary + three servers
            # EDIT inside the card
            page.locator('.member-card', has_text='ocr-paris').locator('.m-edit').click()
            page.fill('.member-card .e-label', 'ocr-paris-2')
            page.fill('.member-card .e-notes', 'rack 9')
            page.click('.member-card .e-save')
            page.wait_for_selector('.member-card:has-text("ocr-paris-2")')
            assert 'rack 9' in page.locator('.member-card', has_text='ocr-paris-2').inner_text()
            # SHOW / HIDE the join code, NEW join code, PAUSE
            card = page.locator('.member-card', has_text='ocr-tunis')
            card.locator('.m-code').click()
            page.wait_for_selector('.member-card:has-text("ocr-tunis") .code-value')
            first = page.locator('.member-card', has_text='ocr-tunis').locator('.code-value').input_value()
            page.locator('.member-card', has_text='ocr-tunis').locator('.m-rotate').click()
            page.locator('.member-card', has_text='ocr-tunis').locator('.m-rotate-yes').click()
            page.wait_for_function("(() => { const c = [...document.querySelectorAll('.member-card')].find(x => x.textContent.includes('ocr-tunis')); const v = c && c.querySelector('.code-value'); return v && v.value !== %r; })()" % first)
            page.locator('.member-card', has_text='ocr-tunis').locator('.m-pause').click()
            page.wait_for_function("[...document.querySelectorAll('.member-card')].some(c => c.textContent.includes('ocr-tunis') && c.querySelector('.m-status').textContent.toLowerCase().includes('paused'))")
            # REMOVE with the in-card confirmation (Cancel first)
            page.locator('.member-card', has_text='ocr-sfax').locator('.m-remove').click()
            page.locator('.member-card', has_text='ocr-sfax').locator('.m-remove-no').click()
            assert page.locator('.member-card').count() == 3
            page.locator('.member-card', has_text='ocr-sfax').locator('.m-remove').click()
            page.locator('.member-card', has_text='ocr-sfax').locator('.m-remove-yes').click()
            page.wait_for_function("document.querySelectorAll('.member-card').length === 2")
            page.click('#notify')
            page.wait_for_timeout(800)
            # DELETE the cluster: back to standalone
            page.click('#delete-cluster')
            page.click('#delete-yes')
            page.wait_for_selector('#view-standalone:not(.d-none)')
            assert not errors, errors
            assert not dialogs, dialogs
            browser.close()
    finally:
        proc.terminate()
        proc.wait(timeout=10)


def test_member_flow_in_a_browser(tmp_path):
    pp, mp = _port(), _port()
    P, M = 'http://127.0.0.1:{}'.format(pp), 'http://127.0.0.1:{}'.format(mp)
    pkey = _adminKey(tmp_path / 'primary')
    procs = [_serve(tmp_path / 'primary', pp), _serve(tmp_path / 'member', mp)]
    errors, dialogs = [], []
    try:
        _wait(P, procs[0])
        _wait(M, procs[1])
        PH = {'Authorization': 'Bearer ' + pkey}
        # the primary has a panel user "admin" too, so the member's panel session stays valid after joining
        requests.post(P + '/v1/users', json={'username': 'admin', 'password': 'password123', 'role': 'admin'}, headers=PH).raise_for_status()
        requests.patch(P + '/v1/endpoints', json={'public_base_url': P}, headers=PH).raise_for_status()
        requests.post(P + '/v1/cluster/create', json={'name': 'production'}, headers=PH).raise_for_status()
        code = requests.post(P + '/v1/sync/nodes/bulk', json={'items': [{'label': 'ocr-member'}]}, headers=PH).json()['items'][0]['join_code']
        with sync_playwright() as p:
            browser = _browser(p)
            page = browser.new_page()
            page.on('pageerror', lambda e: errors.append(str(e)))
            page.on('dialog', lambda d: (dialogs.append(d.message), d.dismiss()))
            _login(page, M)
            page.goto(M + '/panel/cluster')
            page.wait_for_selector('#view-standalone:not(.d-none)')
            page.fill('#join-code', 'not a code')
            page.click('#join')
            page.wait_for_function("document.querySelector('#join-result').textContent.includes('join code')")
            page.fill('#join-code', code)
            page.click('#join')
            page.wait_for_selector('#view-member:not(.d-none)', timeout=60000)
            assert 'production' in page.inner_text('#view-member .cl-title')
            page.click('#sync-now')
            page.wait_for_function("document.querySelector('#status').textContent.includes('%s')" % P, timeout=60000)
            page.wait_for_function("document.querySelectorAll('#members-m .card').length >= 2", timeout=60000)
            # LEAVE with confirmation (Cancel first)
            page.click('#leave')
            page.click('#leave-no')
            assert page.is_visible('#view-member')
            page.click('#leave')
            page.click('#leave-yes')
            page.wait_for_selector('#view-standalone:not(.d-none)')
            assert not errors, errors
            assert not dialogs, dialogs
            browser.close()
    finally:
        for proc in procs:
            proc.terminate()
        for proc in procs:
            proc.wait(timeout=10)
