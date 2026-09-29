# coding=utf-8
"""The cluster model end to end with real servers: create, add servers (join codes), join, members, remove, leave."""
from __future__ import absolute_import, division, print_function

import requests

from tests.integration.test_sync_cluster import _adminKey, _port, _serve, _until, _wait


def test_create_join_remove_leave(tmp_path):
    ports = [_port() for _ in range(3)]
    P, A, B = ('http://127.0.0.1:{}'.format(p) for p in ports)
    keys = [_adminKey(tmp_path / n) for n in ('primary', 'a', 'b')]
    procs = [_serve(tmp_path / n, p) for n, p in zip(('primary', 'a', 'b'), ports)]
    try:
        for base, proc in zip((P, A, B), procs):
            _wait(base, proc)
        PH, AH, BH = ({'Authorization': 'Bearer ' + k} for k in keys)
        requests.patch(P + '/v1/endpoints', json={'public_base_url': P}, headers=PH).raise_for_status()
        assert requests.get(A + '/v1/cluster', headers=AH).json()['mode'] == 'standalone'
        cl = requests.post(P + '/v1/cluster/create', json={'name': 'production'}, headers=PH).json()
        assert cl['mode'] == 'primary' and cl['cluster_name'] == 'production' and cl['members'][0]['role'] == 'primary'
        items = requests.post(P + '/v1/sync/nodes/bulk', json={'items': [{'label': 'ocr-a'}, {'label': 'ocr-b'}]}, headers=PH).json()['items']
        codes = {i['node']['label']: i['join_code'] for i in items}
        assert all(c.startswith('OCRJ1-') for c in codes.values())
        # clear errors: a damaged code, a foreign string
        bad = requests.post(A + '/v1/cluster/join', json={'code': codes['ocr-a'][:-4] + 'abcd'}, headers=AH)
        assert bad.status_code == 400 and 'damaged' in bad.json()['error_message']
        assert 'not an OcrRoute join code' in requests.post(A + '/v1/cluster/join', json={'code': 'hello'}, headers=AH).json()['error_message']
        # A joins with its code (a line break from copy / paste is tolerated)
        code = codes['ocr-a']
        joined = requests.post(A + '/v1/cluster/join', json={'code': code[:30] + '\n' + code[30:]}, headers=AH)
        assert joined.status_code == 200, joined.text
        assert joined.json()['mode'] == 'member' and joined.json()['cluster_name'] == 'production'
        # after joining, A uses the primary's keys; it lists the members and says where it is connected
        m = _until(lambda: (lambda c: c if c.get('members') and c['state']['last_success_at'] else None)(
            requests.get(A + '/v1/cluster', headers=PH).json()))
        assert m['state']['last_error'] is None and m['state']['via'] == P
        assert any(x['role'] == 'primary' for x in m['members'])
        # B joins too; the primary shows both members up to date
        requests.post(B + '/v1/cluster/join', json={'code': codes['ocr-b']}, headers=BH).raise_for_status()
        members = _until(lambda: (lambda ms: ms if len([x for x in ms if x['role'] == 'member' and x['status'] == 'up_to_date']) == 2 else None)(
            requests.get(P + '/v1/cluster', headers=PH).json()['members']))
        assert len(members) == 3
        # remove B on the primary: B is refused with a clear reason
        bKey = [x['key'] for x in members if x['label'] == 'ocr-b'][0]
        assert requests.delete(P + '/v1/sync/nodes/' + bKey, headers=PH).status_code == 204
        _until(lambda: 'removed' in (requests.get(B + '/v1/cluster', headers=PH).json()['state']['last_error'] or ''))
        # a member's configuration is read-only; leaving makes it standalone and editable again
        assert requests.post(A + '/v1/providers', json={'engine_id': 'Tesseract', 'label': 'x'}, headers=PH).status_code == 409
        left = requests.post(A + '/v1/cluster/leave', headers=PH).json()
        assert left['mode'] == 'standalone'
        assert requests.post(A + '/v1/providers', json={'engine_id': 'Tesseract', 'label': 'local'}, headers=PH).status_code in (200, 201)
        # a code whose primary cannot be reached is refused with the reason
        requests.post(P + '/v1/cluster/delete', headers=PH).raise_for_status()
        gone = requests.post(A + '/v1/cluster/join', json={'code': codes['ocr-a']}, headers=PH)
        assert gone.status_code == 400 and 'Cannot reach the primary' in gone.json()['error_message']
    finally:
        for p in procs:
            p.terminate()
        for p in procs:
            p.wait(timeout=10)


def test_self_address_is_explained_and_watcher_survives_reconfiguration(tmp_path):
    import threading
    import time

    port = _port()
    base = 'http://127.0.0.1:{}'.format(port)
    key = _adminKey(tmp_path / 's')
    proc = _serve(tmp_path / 's', port, sync_watch_seconds=1)
    try:
        _wait(base, proc)
        H = {'Authorization': 'Bearer ' + key}
        # the report's configuration: this server following "http://127.0.0.1:20256/panel", i.e. itself via a panel link
        requests.put(base + '/v1/sync/config', headers=H, json={
            'role': 'follower', 'token': 't' * 32, 'interval_seconds': 1, 'leader_urls': [{'url': base + '/panel'}]}).raise_for_status()
        cfg = requests.get(base + '/v1/sync/config', headers=H).json()
        assert cfg['leader_urls'][0]['url'] == base  # "/panel" stripped
        err = _until(lambda: requests.get(base + '/v1/sync/config', headers=H).json()['state']['last_error'])
        assert 'this server itself' in err
        # leader on / off / on quickly: the old address watcher must stop cleanly (it crashed with AttributeError)
        for role in ('leader', 'off', 'leader', 'off', 'leader'):
            requests.put(base + '/v1/sync/config', json={'role': role}, headers=H).raise_for_status()
            time.sleep(0.3)
        time.sleep(2.5)
        assert proc.poll() is None
    finally:
        proc.terminate()
        _, stderr = proc.communicate(timeout=15)
        text = stderr.decode('utf-8', 'replace')
        assert 'Exception in thread ocrroute-sync-watch' not in text and 'AttributeError' not in text, text[-1500:]
    del threading
