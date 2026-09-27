# coding=utf-8
"""
Leader address changes (quick-tunnel restarts): push, failover, plain errors, and impostor rejection, with real servers.
"""
from __future__ import absolute_import, division, print_function

import socket
import threading

import requests

from tests.integration.test_sync_cluster import _adminKey, _port, _serve, _until, _wait

DEAD = 'https://bonus-zinc-chambers-independently.trycloudflare.com'  # the address from the bug report: gone from DNS


class Tunnel(object):
    """A TCP forwarder standing in for a tunnel: it can be killed like a restarting cloudflared."""

    def __init__(self, target):
        self.target, self.port = target, _port()
        self.sock = socket.socket()
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(('127.0.0.1', self.port))
        self.sock.listen(50)
        self.alive = True
        threading.Thread(target=self._accept, daemon=True).start()

    def _pipe(self, a, b):
        try:
            while True:
                data = a.recv(65536)
                if not data:
                    break
                b.sendall(data)
        except OSError:
            pass
        finally:
            for s in (a, b):
                try:
                    s.close()
                except OSError:
                    pass

    def _accept(self):
        while self.alive:
            try:
                client, _ = self.sock.accept()
            except OSError:
                return
            upstream = socket.create_connection(('127.0.0.1', self.target))
            threading.Thread(target=self._pipe, args=(client, upstream), daemon=True).start()
            threading.Thread(target=self._pipe, args=(upstream, client), daemon=True).start()

    def kill(self):
        self.alive = False
        self.sock.close()


def test_leader_address_change_is_pushed_and_verified(tmp_path):
    from ocrroute import sync

    lp, fp, xp = _port(), _port(), _port()
    L, F, X = ('http://127.0.0.1:{}'.format(p) for p in (lp, fp, xp))
    lkey, fkey, xkey = (_adminKey(tmp_path / n) for n in ('leader', 'follower', 'impostor'))
    procs = [_serve(tmp_path / 'leader', lp, sync_watch_seconds=2), _serve(tmp_path / 'follower', fp), _serve(tmp_path / 'impostor', xp)]
    tunnel = Tunnel(lp)
    A = 'http://127.0.0.1:{}'.format(tunnel.port)  # the leader's "quick tunnel" address
    try:
        for base, proc in zip((L, F, X), procs):
            _wait(base, proc)
        LH, FH, XH = ({'Authorization': 'Bearer ' + k} for k in (lkey, fkey, xkey))
        requests.patch(L + '/v1/endpoints', json={'public_base_url': A}, headers=LH).raise_for_status()
        requests.patch(F + '/v1/endpoints', json={'public_base_url': 'http://localhost:{}'.format(fp)}, headers=FH).raise_for_status()
        requests.put(L + '/v1/sync/config', json={'role': 'leader'}, headers=LH).raise_for_status()
        token = requests.post(L + '/v1/sync/nodes', json={'label': 'paris-2'}, headers=LH).json()['token']  # a card token
        requests.put(F + '/v1/sync/config', json={'role': 'follower', 'leader_url': A, 'token': token, 'interval_seconds': 1},
                     headers=FH).raise_for_status()
        # synced through the tunnel; the leader knows how to reach the follower, the follower knows the leader's addresses
        card = _until(lambda: (lambda n: n[0] if n and n[0]['status'] == 'up_to_date' and n[0].get('addresses') else None)(
            requests.get(L + '/v1/sync/nodes', headers=LH).json()['items']))
        assert 'http://localhost:{}'.format(fp) in card['addresses']
        # the tunnel restarts: its old address dies and the leader now has a new one
        tunnel.kill()
        B = 'http://localhost:{}'.format(lp)
        requests.patch(L + '/v1/endpoints', json={'public_base_url': B}, headers=LH).raise_for_status()
        # the leader's watcher notices, pushes B; the follower verifies B (challenge / response) and switches to it
        cfg = _until(lambda: (lambda c: c if c['state'] and c['state'].get('via') == B and c['state']['last_error'] is None else None)(
            requests.get(F + '/v1/sync/config', headers=LH).json()), timeout=60)
        learned = [a for a in cfg['leader_urls'] if a['url'] == B]
        assert learned and learned[0]['learned'] and cfg['leader_urls'][0]['url'] == A  # added after the user's own address
        card = requests.get(L + '/v1/sync/nodes', headers=LH).json()['items'][0]
        assert card['notify_result'].startswith('delivered')
        # the exact address from the bug report (gone from DNS) fails over to a known address of the leader
        requests.put(F + '/v1/sync/config', json={'role': 'follower', 'leader_url': DEAD, 'token': token, 'interval_seconds': 1},
                     headers=LH).raise_for_status()
        cfg = _until(lambda: (lambda c: c if c['state'].get('via') not in (None, DEAD) and c['state']['last_error'] is None else None)(
            requests.get(F + '/v1/sync/config', headers=LH).json()), timeout=60)
        assert cfg['state']['address_health'][DEAD]['error']  # the dead primary shows its problem on its card
        # and its plain-language explanation when nothing else is known
        msg = sync.describeError(Exception("NameResolutionError: Failed to resolve ([Errno 11001] getaddrinfo failed)"), DEAD)
        assert 'no longer exists' in msg and 'quick-tunnel' in msg
        # an impostor leader (another token) is rejected BEFORE the token is sent: it never sees this follower
        xtoken = requests.put(X + '/v1/sync/config', json={'role': 'leader'}, headers=XH).json()['token']
        assert xtoken != token
        ok, _lid, why = sync.verifyLeader(X, token, 'any-server-id')
        assert not ok and 'token not sent' in why
        requests.post(F + '/v1/sync/leader-moved', json={'leader_id': 'evil', 'addresses': [X]}).raise_for_status()
        _until(lambda: requests.get(F + '/v1/sync/config', headers=LH).json()['state']['last_error'] is None)
        assert requests.get(X + '/v1/sync/nodes', headers=XH).json()['items'] == []
        assert all(a['url'] != X for a in requests.get(F + '/v1/sync/config', headers=LH).json()['leader_urls'])
    finally:
        tunnel.kill() if tunnel.alive else None
        for p in procs:
            p.terminate()
        for p in procs:
            p.wait(timeout=10)


def test_follower_with_several_leader_addresses(tmp_path):
    lp, fp = _port(), _port()
    L, F = 'http://127.0.0.1:{}'.format(lp), 'http://127.0.0.1:{}'.format(fp)
    lkey, fkey = _adminKey(tmp_path / 'leader'), _adminKey(tmp_path / 'follower')
    procs = [_serve(tmp_path / 'leader', lp), _serve(tmp_path / 'follower', fp)]
    try:
        _wait(L, procs[0])
        _wait(F, procs[1])
        LH, FH = {'Authorization': 'Bearer ' + lkey}, {'Authorization': 'Bearer ' + fkey}
        token = requests.put(L + '/v1/sync/config', json={'role': 'leader'}, headers=LH).json()['token']
        refused = requests.put(F + '/v1/sync/config', json={'role': 'follower', 'token': token, 'leader_urls': []}, headers=FH)
        assert refused.status_code == 400  # a follower needs at least one address
        bad = requests.put(F + '/v1/sync/config', json={'role': 'follower', 'token': token,
                                                        'leader_urls': [{'url': 'ftp://nope'}]}, headers=FH)
        assert bad.status_code == 400
        saved = requests.put(F + '/v1/sync/config', headers=FH, json={
            'role': 'follower', 'token': token, 'interval_seconds': 1,
            'leader_urls': [{'url': DEAD, 'label': 'Cloudflare'}, {'url': L + '/', 'label': 'LAN'}, {'url': DEAD}]}).json()
        assert [a['url'] for a in saved['leader_urls']] == [DEAD, L]  # order kept, duplicate and trailing slash removed
        assert [a['label'] for a in saved['leader_urls']] == ['Cloudflare', 'LAN']
        # the dead primary fails, the backup works; each address has its own health
        cfg = _until(lambda: (lambda c: c if c['state'] and c['state'].get('via') == L and c['state']['last_error'] is None else None)(
            requests.get(F + '/v1/sync/config', headers=LH).json()), timeout=60)
        health = cfg['state']['address_health']
        assert health[DEAD]['error'] and 'no longer exists' in health[DEAD]['error']
        assert health[L]['ok_at'] and not health[L]['error']
        assert [a['url'] for a in cfg['leader_urls']] == [DEAD, L]  # the user's list is not rewritten
        # make the working address primary, then remove the dead one: both apply live
        requests.put(F + '/v1/sync/config', headers=LH, json={'role': 'follower', 'token': token, 'interval_seconds': 1,
                                                              'leader_urls': [{'url': L, 'label': 'LAN'}]}).raise_for_status()
        cfg = requests.get(F + '/v1/sync/config', headers=LH).json()
        assert [a['url'] for a in cfg['leader_urls']] == [L] and cfg['leader_url'] == L
    finally:
        for p in procs:
            p.terminate()
        for p in procs:
            p.wait(timeout=10)
