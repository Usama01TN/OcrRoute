# coding=utf-8
"""Server cards (per-server tokens) and the duplicate-username sync bug, with real leader / follower processes."""
from __future__ import absolute_import, division, print_function

import requests

from tests.integration.test_sync_cluster import _adminKey, _port, _serve, _until, _wait


def _panelUser(base, key, name, password):
    return requests.post(base + '/v1/users', json={'username': name, 'password': password, 'role': 'admin'},
                         headers={'Authorization': 'Bearer ' + key})


def test_server_cards_crud_and_duplicate_usernames(tmp_path):
    lp, ap, bp = _port(), _port(), _port()
    L, A, B = ('http://127.0.0.1:{}'.format(p) for p in (lp, ap, bp))
    lkey, akey, bkey = (_adminKey(tmp_path / n) for n in ('leader', 'a', 'b'))
    procs = [_serve(tmp_path / 'leader', lp), _serve(tmp_path / 'a', ap), _serve(tmp_path / 'b', bp)]
    try:
        for base, proc in zip((L, A, B), procs):
            _wait(base, proc)
        LH, AH, BH = ({'Authorization': 'Bearer ' + k} for k in (lkey, akey, bkey))
        # the exact case from the bug report: a panel user with the same name on the leader and on a follower
        assert _panelUser(L, lkey, 'usama', 'leader-password-1').status_code in (200, 201)
        assert _panelUser(A, akey, 'usama', 'follower-password').status_code in (200, 201)
        shared = requests.put(L + '/v1/sync/config', json={'role': 'leader'}, headers=LH).json()['token']
        # CREATE: a card with its own token for server A
        created = requests.post(L + '/v1/sync/nodes', json={'label': 'ocr-a', 'notes': 'rack 1'}, headers=LH)
        assert created.status_code == 201
        card, token = created.json()['node'], created.json()['token']
        assert card['own_token'] and card['status'] == 'never' and 'token_hash' not in card
        requests.put(A + '/v1/sync/config', json={'role': 'follower', 'leader_url': L, 'token': token, 'interval_seconds': 1},
                     headers=AH).raise_for_status()
        # B uses the shared token: it appears as a card automatically
        requests.put(B + '/v1/sync/config', json={'role': 'follower', 'leader_url': L, 'token': shared, 'interval_seconds': 1},
                     headers=BH).raise_for_status()
        # READ: both cards up to date; the duplicate username synced without IntegrityError
        nodes = _until(lambda: (lambda n: n if len(n) == 2 and all(x['status'] == 'up_to_date' for x in n) else None)(
            requests.get(L + '/v1/sync/nodes', headers=LH).json()['items']))
        byLabel = {n['label']: n for n in nodes}
        assert byLabel['ocr-a']['own_token'] and not [n for n in nodes if n['label'] != 'ocr-a'][0]['own_token']
        stateA = requests.get(A + '/v1/sync/config', headers=LH).json()['state']
        assert stateA['last_error'] is None, stateA['last_error']
        users = requests.get(A + '/v1/users', headers=LH).json()['items']
        assert [u['username'] for u in users].count('usama') == 1
        # UPDATE: rename + notes, then pause (A is refused with a clear reason), then resume
        key = card['key']
        r = requests.patch(L + '/v1/sync/nodes/' + key, json={'label': 'ocr-a-renamed', 'notes': 'rack 2'}, headers=LH).json()
        assert r['node']['label'] == 'ocr-a-renamed' and r['node']['notes'] == 'rack 2'
        requests.patch(L + '/v1/sync/nodes/' + key, json={'paused': True}, headers=LH).raise_for_status()
        _until(lambda: 'paused' in (requests.get(A + '/v1/sync/config', headers=LH).json()['state']['last_error'] or ''))
        requests.patch(L + '/v1/sync/nodes/' + key, json={'paused': False}, headers=LH).raise_for_status()
        _until(lambda: requests.get(A + '/v1/sync/config', headers=LH).json()['state']['last_error'] is None)
        # UPDATE: rotate A's token: the old one is refused, the new one works
        newTok = requests.patch(L + '/v1/sync/nodes/' + key, json={'regenerate_token': True}, headers=LH).json()['token']
        assert newTok != token
        _until(lambda: 'token' in (requests.get(A + '/v1/sync/config', headers=LH).json()['state']['last_error'] or ''))
        requests.put(A + '/v1/sync/config', json={'role': 'follower', 'leader_url': L, 'token': newTok, 'interval_seconds': 1},
                     headers=LH).raise_for_status()
        _until(lambda: requests.get(A + '/v1/sync/config', headers=LH).json()['state']['last_error'] is None)
        # DELETE: the shared-token server B is removed and stays out, although the shared token still works for others
        bKey = [n for n in requests.get(L + '/v1/sync/nodes', headers=LH).json()['items'] if not n['own_token']][0]['key']
        assert requests.delete(L + '/v1/sync/nodes/' + bKey, headers=LH).status_code == 204
        _until(lambda: 'removed' in (requests.get(B + '/v1/sync/config', headers=LH).json()['state']['last_error'] or ''))
        assert all(n['key'] != bKey for n in requests.get(L + '/v1/sync/nodes', headers=LH).json()['items'])
        # a new card lets B back in with its own token
        back = requests.post(L + '/v1/sync/nodes', json={'label': 'ocr-b'}, headers=LH).json()['token']
        requests.put(B + '/v1/sync/config', json={'role': 'follower', 'leader_url': L, 'token': back, 'interval_seconds': 1},
                     headers=LH).raise_for_status()
        _until(lambda: requests.get(B + '/v1/sync/config', headers=LH).json()['state']['last_error'] is None)
        assert requests.delete(L + '/v1/sync/nodes/does-not-exist', headers=LH).status_code == 404
    finally:
        for p in procs:
            p.terminate()
        for p in procs:
            p.wait(timeout=10)
