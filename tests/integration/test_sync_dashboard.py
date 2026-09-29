# coding=utf-8
"""Cluster sync configured from the dashboard (API calls the Cluster sync page makes): no .env, live role changes."""
from __future__ import absolute_import, division, print_function

import requests

from tests.integration.test_sync_cluster import _adminKey, _port, _serve, _until, _wait


def test_configure_sync_from_the_dashboard(tmp_path):
    lp, fp = _port(), _port()
    lhome, fhome = tmp_path / 'leader', tmp_path / 'follower'
    lkey, fkey = _adminKey(lhome), _adminKey(fhome)
    leader, follower = _serve(lhome, lp), _serve(fhome, fp)  # no OCRROUTE_SYNC_* variables at all
    try:
        L, F = 'http://127.0.0.1:{}'.format(lp), 'http://127.0.0.1:{}'.format(fp)
        _wait(L, leader)
        _wait(F, follower)
        LH, FH = {'Authorization': 'Bearer ' + lkey}, {'Authorization': 'Bearer ' + fkey}
        assert requests.get(F + '/panel/cluster', allow_redirects=False).status_code in (200, 303)  # page exists
        cfg = requests.get(L + '/v1/sync/config', headers=LH).json()
        assert cfg['role'] == 'off' and cfg['source'] == 'dashboard'
        # leader: pick the role; an empty token is generated
        cfg = requests.put(L + '/v1/sync/config', json={'role': 'leader'}, headers=LH).json()
        assert cfg['role'] == 'leader' and len(cfg['token']) >= 24
        token = cfg['token']
        pid = requests.post(L + '/v1/providers', json={'engine_id': 'Tesseract', 'label': 'from-dashboard-leader'},
                            headers=LH).json()['id']
        # follower: test first (wrong token, then right token), then save
        bad = requests.post(F + '/v1/sync/test', json={'leader_url': L, 'token': 'x' * 30}, headers=FH).json()
        assert not bad['ok'] and 'token' in bad['message']
        good = requests.post(F + '/v1/sync/test', json={'leader_url': L, 'token': token}, headers=FH).json()
        assert good['ok'] and good['counts']['providers'] >= 1
        invalid = requests.put(F + '/v1/sync/config', json={'role': 'follower', 'leader_url': 'not-a-url', 'token': token},
                               headers=FH)
        assert invalid.status_code == 400
        saved = requests.put(F + '/v1/sync/config', json={'role': 'follower', 'leader_url': L, 'token': token,
                                                          'interval_seconds': 1}, headers=FH)
        assert saved.status_code == 200 and saved.json()['role'] == 'follower'  # applied live, no restart
        # after the first sync the follower uses the leader's keys
        _until(lambda: any(p['id'] == pid for p in requests.get(F + '/v1/providers', headers=LH).json().get('items', [])))
        assert requests.post(F + '/v1/providers', json={'engine_id': 'Tesseract', 'label': 'x'}, headers=LH).status_code == 409
        state = requests.get(F + '/v1/sync/config', headers=LH).json()['state']
        assert state['last_error'] is None and state['digest']
        # switch the follower off from the dashboard: edits are allowed again, immediately
        off = requests.put(F + '/v1/sync/config', json={'role': 'off'}, headers=LH).json()
        assert off['role'] == 'off' and off['state'] is None
        assert requests.post(F + '/v1/providers', json={'engine_id': 'Tesseract', 'label': 'local-again'},
                             headers=LH).status_code in (200, 201)
    finally:
        for p in (leader, follower):
            p.terminate()
            p.wait(timeout=10)


def test_env_configuration_makes_the_page_read_only(tmp_path):
    port, home = _port(), tmp_path / 'envserver'
    key = _adminKey(home)
    proc = _serve(home, port, sync_role='leader', sync_token='env-token-0123456789abcdefghij')
    try:
        base = 'http://127.0.0.1:{}'.format(port)
        _wait(base, proc)
        H = {'Authorization': 'Bearer ' + key}
        cfg = requests.get(base + '/v1/sync/config', headers=H).json()
        assert cfg['source'] == 'environment' and cfg['role'] == 'leader'
        r = requests.put(base + '/v1/sync/config', json={'role': 'off'}, headers=H)
        assert r.status_code == 400 and '.env' in r.json()['error_message']
    finally:
        proc.terminate()
        proc.wait(timeout=10)


def test_leader_lists_many_followers_and_flags_temporary_addresses(tmp_path):
    lp = _port()
    lhome = tmp_path / 'leader'
    lkey = _adminKey(lhome)
    leader = _serve(lhome, lp)
    procs = [leader]
    try:
        L = 'http://127.0.0.1:{}'.format(lp)
        _wait(L, leader)
        LH = {'Authorization': 'Bearer ' + lkey}
        token = requests.put(L + '/v1/sync/config', json={'role': 'leader'}, headers=LH).json()['token']
        # a temporary tunnel URL configured as the public URL must be flagged, a LAN address must not
        r = requests.patch(L + '/v1/endpoints', json={'public_base_url': 'https://random-words.trycloudflare.com'}, headers=LH)
        assert r.status_code == 200, r.text
        cfg = requests.get(L + '/v1/sync/config', headers=LH).json()
        flagged = [a for a in cfg['public_addresses'] if not a['stable']]
        assert flagged and flagged[0]['url'].startswith('https://random-words.trycloudflare.com') and cfg['has_public_address']
        assert all(a['stable'] for a in cfg['public_addresses'] if a['kind'] == 'lan')
        # three followers, each configured from its own dashboard API
        names = []
        for i in range(3):
            fp, fhome = _port(), tmp_path / 'follower{}'.format(i)
            fkey = _adminKey(fhome)
            proc = _serve(fhome, fp)
            procs.append(proc)
            F = 'http://127.0.0.1:{}'.format(fp)
            _wait(F, proc)
            r = requests.put(F + '/v1/sync/config', json={'role': 'follower', 'leader_url': L, 'token': token,
                                                          'interval_seconds': 1}, headers={'Authorization': 'Bearer ' + fkey})
            assert r.status_code == 200, r.text
            names.append(F)
        followers = _until(lambda: (lambda f: f if len(f) >= 3 and all(x['up_to_date'] for x in f) else None)(
            requests.get(L + '/v1/sync/config', headers=LH).json()['followers']))
        assert len(followers) == 3 and all(f['version'] and f['last_seen'] for f in followers)
        # a follower that stops syncing stays listed with its last contact, and a change makes the others "behind"
        # until their next poll (they poll every second, so they catch up)
        requests.post(L + '/v1/providers', json={'engine_id': 'Tesseract', 'label': 'change'}, headers=LH)
        _until(lambda: all(f['up_to_date'] for f in requests.get(L + '/v1/sync/config', headers=LH).json()['followers']))
    finally:
        for p in procs:
            p.terminate()
        for p in procs:
            p.wait(timeout=10)
