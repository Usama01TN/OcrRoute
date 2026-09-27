# coding=utf-8
"""
Cluster sync: one **leader** server holds the configuration, any number of **followers** mirror it.

What is synchronised: providers, credentials, routes (with their members), client API keys, panel users, runtime
settings, and the enabled/disabled state of engines. What stays per server: OCR runs, usage and costs, the result
cache, jobs, logs, provider health / circuit breakers, credential usage counters and quota state, "last used" and
"last login" timestamps, and the server's own address, port and sync settings.

Security: the leader serves ``GET /v1/sync/snapshot`` only to callers presenting the shared sync token. Secrets
(credential keys, TOTP seeds) are decrypted with the leader's own key and re-encrypted for transport with a key
derived from the sync token; each follower decrypts them and re-encrypts them with *its* key, so no server ever
stores another server's master key. Serve the leader over HTTPS (reverse proxy or tunnel) when servers talk across
an untrusted network.

Consistency: the follower applies a whole snapshot in one transaction and mirrors it exactly (rows missing on the
leader are removed). A digest (ETag) makes unchanged polls a cheap ``304``. Configuration writes on a follower are
refused while it follows a leader, so no edit is ever silently overwritten.
"""
from __future__ import absolute_import, division, print_function

import base64
import hashlib
import hmac
import json
import threading
import time
from datetime import datetime, timezone

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import DateTime

from ocrroute.db.models import ApiKey, Credential, Engine, Provider, Route, RouteMember, Setting, User
from ocrroute.logsetup import getLogger

log = getLogger(__name__)

SYNC_VERSION = 1
TOKEN_HEADER = 'X-OcrRoute-Sync-Token'
LOCAL_SETTING_PREFIXES = ('sync.',)  # never synchronised: each server's own sync state
# per-server settings that are never synchronised: each server's own public address (Endpoints page)
LOCAL_SETTING_KEYS = frozenset({'public_base_url'})
STATE_KEY = 'sync.state'
#: (table name, model, per-server columns that are never synchronised, secret columns)
SPEC = (
    ('providers', Provider, {'health', 'health_checked_at', 'consecutive_failures', 'circuit_open_until'}, ()),
    ('credentials', Credential, {'last_used_at', 'success_count', 'failure_count', 'exhausted_until'}, ('secret_enc',)),
    ('routes', Route, set(), ()),
    ('route_members', RouteMember, set(), ()),
    ('api_keys', ApiKey, {'last_used_at'}, ()),
    ('users', User, {'last_login_at'}, ('totp_secret_enc',)),
)
#: URL prefixes of configuration the leader owns; writes to them are refused on a follower
MANAGED_PREFIXES = ('/v1/providers', '/v1/credentials', '/v1/routes', '/v1/keys', '/v1/users', '/v1/settings')


# --------------------------------------------------------------------------------------------------------------- #
# helpers                                                                                                         #
# --------------------------------------------------------------------------------------------------------------- #
def transportCipher(token):
    """
    :param token: str  shared sync token
    :return: Fernet  cipher used only for secrets in transit
    """
    raw = hashlib.sha256(b'ocrroute-sync-v1:' + token.encode('utf-8')).digest()
    return Fernet(base64.urlsafe_b64encode(raw))


def tokenMatches(expected, given):
    """
    :return: bool  constant-time comparison; an empty configured token never matches
    """
    return bool(expected) and hmac.compare_digest(expected.encode('utf-8'), (given or '').encode('utf-8'))


def _columns(model, exclude):
    return [c for c in model.__table__.columns if c.name not in exclude]


def _dump(value):
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _load(value, column):
    if value is not None and isinstance(column.type, DateTime) and isinstance(value, str):
        return datetime.fromisoformat(value)
    return value


def _isLocalSetting(key):
    return key.startswith(LOCAL_SETTING_PREFIXES) or key in LOCAL_SETTING_KEYS


# --------------------------------------------------------------------------------------------------------------- #
# leader                                                                                                          #
# --------------------------------------------------------------------------------------------------------------- #
def buildSnapshot(db, secrets, token):
    """
    :param db: Session
    :param secrets: SecretStore  this server's secret store
    :param token: str  sync token (encrypts secrets for transport)
    :return: dict  {version, generated_at, digest, data}
    """
    cipher = transportCipher(token)
    data, view = {}, {}  # view: digest input (secrets as hashes: Fernet output is random, the digest must not be)
    for name, model, exclude, secretCols in SPEC:
        rows, viewRows = [], []
        for obj in db.query(model).order_by(model.id).all():
            row, viewRow = {}, {}
            for col in _columns(model, exclude):
                value = getattr(obj, col.name)
                if col.name in secretCols and value:
                    plain = secrets.decrypt(value)
                    row[col.name] = {'$secret': cipher.encrypt(plain.encode('utf-8')).decode('ascii')}
                    viewRow[col.name] = {'$sha256': hashlib.sha256(plain.encode('utf-8')).hexdigest()}
                else:
                    row[col.name] = viewRow[col.name] = _dump(value)
            rows.append(row)
            viewRows.append(viewRow)
        data[name], view[name] = rows, viewRows
    settings = {s.key: s.value_json for s in db.query(Setting).order_by(Setting.key).all() if not _isLocalSetting(s.key)}
    engines = {e.id: bool(e.enabled) for e in db.query(Engine).order_by(Engine.id).all()}
    data['settings'] = view['settings'] = settings
    data['engines'] = view['engines'] = engines
    digest = hashlib.sha256(json.dumps(view, sort_keys=True, default=str).encode('utf-8')).hexdigest()
    return {'version': SYNC_VERSION, 'generated_at': datetime.now(timezone.utc).isoformat(), 'digest': digest,
            'data': data}


# --------------------------------------------------------------------------------------------------------------- #
# follower                                                                                                        #
# --------------------------------------------------------------------------------------------------------------- #
def applySnapshot(db, secrets, token, snapshot):
    """
    Mirror a leader snapshot into this server's database, in the caller's transaction.

    :param db: Session
    :param secrets: SecretStore  this server's secret store (re-encrypts incoming secrets)
    :param token: str  sync token (decrypts transported secrets)
    :param snapshot: dict  as produced by ``buildSnapshot``
    :return: dict  {upserted: {table: n}, removed: {table: n}, skipped: [...]}
    """
    if snapshot.get('version') != SYNC_VERSION:
        raise ValueError('unsupported sync snapshot version {!r}'.format(snapshot.get('version')))
    cipher = transportCipher(token)
    data = snapshot['data']
    knownEngines = {e.id for e in db.query(Engine).all()}
    upserted, removed, skipped = {}, {}, []
    # Which leader rows this server will hold (providers of engines not installed here are skipped, with their
    # credentials and route members).
    skippedProviders = set()
    for row in data.get('providers', []):
        if row.get('engine_id') not in knownEngines:
            skippedProviders.add(row['id'])
            skipped.append('provider {} ({}): engine {} is not installed here'.format(
                row.get('label'), row['id'], row.get('engine_id')))
    wanted = {}
    for name, _model, _exclude, _secret in SPEC:
        rows = data.get(name, [])
        if name == 'providers':
            rows = [r for r in rows if r['id'] not in skippedProviders]
        elif name in ('credentials', 'route_members'):
            rows = [r for r in rows if r.get('provider_id') not in skippedProviders]
        wanted[name] = rows
    # 1. Remove local rows the leader does not have, children first, and flush BEFORE inserting: a local row with
    #    the same unique value as a leader row (a panel user "admin" created on both servers, a provider label, a
    #    route name...) would otherwise collide ("UNIQUE constraint failed") while both briefly exist.
    for name, model, _exclude, _secret in reversed(SPEC):
        keep = {r['id'] for r in wanted[name]}
        gone = [obj for obj in db.query(model).all() if obj.id not in keep]
        for obj in gone:
            db.delete(obj)
        removed[name] = len(gone)
        db.flush()
    # 2. Insert / update the leader's rows, parents first.
    for name, model, exclude, secretCols in SPEC:
        cols = {c.name: c for c in _columns(model, exclude)}
        count = 0
        for row in wanted[name]:
            values = {}
            for key, value in row.items():
                col = cols.get(key)
                if col is None:
                    continue  # column unknown to this version: ignore instead of failing
                if key in secretCols and isinstance(value, dict) and '$secret' in value:
                    try:
                        plain = cipher.decrypt(value['$secret'].encode('ascii')).decode('utf-8')
                    except InvalidToken:
                        raise ValueError('cannot decrypt synced secrets: the sync token differs from the leader')
                    value = secrets.encrypt(plain)
                values[key] = _load(value, col)
            obj = db.get(model, values['id'])
            if obj is None:
                db.add(model(**values))
            else:
                for key, value in values.items():
                    setattr(obj, key, value)
            count += 1
        db.flush()
        upserted[name] = count
    incoming = {k: v for k, v in (data.get('settings') or {}).items() if not _isLocalSetting(k)}
    for key, value in incoming.items():
        row = db.get(Setting, key)
        if row is None:
            db.add(Setting(key=key, value_json=value))
        else:
            row.value_json = value
    stale = [s for s in db.query(Setting).all() if not _isLocalSetting(s.key) and s.key not in incoming]
    for s in stale:
        db.delete(s)
    upserted['settings'], removed['settings'] = len(incoming), len(stale)
    for engineId, enabled in (data.get('engines') or {}).items():
        engine = db.get(Engine, engineId)
        if engine is not None and bool(engine.enabled) != enabled:
            engine.enabled = enabled
    return {'upserted': upserted, 'removed': removed, 'skipped': skipped}


class Follower(object):
    """
    Follower class: polls the leader's snapshot and mirrors it.
    """

    def __init__(self, settings, sessionFactory, secrets, http=None):
        """
        :param settings: Settings  sync_leader_url, sync_token, sync_interval_seconds
        :param sessionFactory: callable -> context manager yielding a Session (``sessionScope``)
        :param secrets: SecretStore
        :param http: module with ``get`` (requests; injectable for tests)
        """
        self.settings = settings
        self.sessionFactory = sessionFactory
        self.secrets = secrets
        self.http = http
        self.lock = threading.Lock()
        self.stopEvent = threading.Event()
        self.thread = None
        self.state = {'role': 'follower', 'leader': settings.sync_leader_url, 'digest': None, 'last_attempt_at': None,
                      'last_success_at': None, 'last_change_at': None, 'last_error': None, 'last_result': None,
                      'consecutive_failures': 0}

    def _get(self, url, headers):
        http = self.http
        if http is None:
            import requests as http
        return http.get(url, headers=headers, timeout=30)

    def syncOnce(self):
        """
        One sync attempt: the configured leader address first, then the leader's other known addresses (learned from
        earlier snapshots or pushed by the leader), each verified by challenge / response before our token is sent.

        :return: dict  the state after this attempt
        """
        with self.lock:
            self.state['last_attempt_at'] = datetime.now(timezone.utc).isoformat()
            try:
                with self.sessionFactory() as db:
                    fstate = loadFollowerState(db)
                    myUrls = [a['url'] for a in publicAddresses(self._appSettings(), db)]
            except Exception:  # noqa: BLE001
                fstate, myUrls = {'leader_id': '', 'addresses': [], 'active_url': '', 'pushed': []}, []
            configuredList = [a['url'] for a in (getattr(self.settings, 'sync_leader_urls', None)
                                                 or normaliseLeaders([], self.settings.sync_leader_url or ''))]
            configured = configuredList[0] if configuredList else ''
            now = time.time()
            active = fstate['active_url'] if fstate['active_url'] in configuredList else ''
            if active and active != configured and now - self.state.get('_primary_checked', 0) < PRIMARY_RECHECK_SECONDS:
                ordered = [active] + configuredList          # stay on the working backup for a while
            else:
                ordered = configuredList                     # user's order: the primary first
                self.state['_primary_checked'] = now
            candidates = _cleanUrls(ordered + fstate['pushed'] + fstate['addresses'], limit=20)
            health = self.state.setdefault('address_health', {})
            identity = dict(nodeIdentity(self.state['digest']), urls=_cleanUrls(myUrls))
            failures, done = [], False
            for url in candidates:
                h = health.setdefault(url, {})
                h['tried_at'] = datetime.now(timezone.utc).isoformat()
                if url not in configuredList:  # learned or pushed: prove it is our leader before sending the token
                    ok, _lid, why = verifyLeader(url, self.settings.sync_token, identity.get('id', ''), fstate['leader_id'])
                    if not ok:
                        failures.append('{}: {}'.format(url, why))
                        h['error'] = why
                        continue
                headers = {TOKEN_HEADER: self.settings.sync_token, NODE_HEADER: json.dumps(identity)}
                if self.state['digest']:
                    headers['If-None-Match'] = '"{}"'.format(self.state['digest'])
                try:
                    r = self._get(url + '/v1/sync/snapshot', headers)
                except Exception as exc:  # noqa: BLE001 - network: try the next known address
                    failures.append(describeError(exc, url))
                    h['error'] = failures[-1]
                    continue
                if r.status_code in (502, 503, 504, 530):
                    failures.append(describeStatus(r.status_code, url))
                    h['error'] = failures[-1]
                    continue
                done = True
                h['error'] = None
                h['ok_at'] = h['tried_at']
                try:
                    self._handleAnswer(r, url, configuredList, fstate)
                except Exception as exc:  # noqa: BLE001 - a definitive answer from the leader (token, paused...)
                    h['error'] = str(exc)
                    self._fail(str(exc))
                break
            if not done:
                self._fail(failures[0] if failures else 'no leader address configured')
                if len(failures) > 1:
                    self.state['last_error'] += ' (also tried {} other known address(es) of the leader)'.format(len(failures) - 1)
            return dict(self.state)

    def _appSettings(self):
        try:
            from ocrroute.config import getSettings

            return getSettings()
        except Exception:  # noqa: BLE001
            return self.settings

    def _fail(self, message):
        self.state['consecutive_failures'] += 1
        changed = message != self.state.get('last_error')
        self.state['last_error'] = message[:700]
        now = time.time()
        if changed or now - self.state.get('_logged_at', 0) > LOG_REPEAT_SECONDS:  # once per problem, then a reminder
            log.warning('sync failed', error=self.state['last_error'])
            self.state['_logged_at'] = now

    def _handleAnswer(self, r, url, configuredList, fstate):
        if r.status_code == 401:
            raise RuntimeError('the leader rejected the sync token: copy it again from the leader (Cluster sync page)')
        if r.status_code == 403:
            try:
                why = r.json().get('error_message') or 'refused'
            except ValueError:
                why = 'refused'
            raise RuntimeError(why)
        if r.status_code == 404:
            raise RuntimeError(explainNotLeader(url))
        if r.status_code not in (200, 304):
            raise RuntimeError(describeStatus(r.status_code, url))
        try:
            leader = json.loads(r.headers.get(LEADER_HEADER) or '{}')
        except ValueError:
            leader = {}
        result = None
        if r.status_code == 200:
            snapshot = r.json()
            with self.sessionFactory() as db:
                result = applySnapshot(db, self.secrets, self.settings.sync_token, snapshot)
            self.state.update(digest=snapshot['digest'], last_change_at=self.state['last_attempt_at'], last_result=result)
            log.info('sync applied', digest=snapshot['digest'][:12], **{k: v for k, v in result['upserted'].items()})
        if self.state.get('last_error'):
            log.info('sync working again', via=url)
        self.state.update(last_success_at=self.state['last_attempt_at'], last_error=None, consecutive_failures=0, via=url)
        # remember the leader's addresses; keep the saved leader address current when another one worked
        with self.sessionFactory() as db:
            st = loadFollowerState(db)
            st['leader_id'] = str(leader.get('id') or st['leader_id'])
            st['addresses'] = _cleanUrls(leader.get('addresses') or st['addresses'])
            st['active_url'] = url
            st['pushed'] = [u for u in st['pushed'] if u != url]
            saveFollowerState(db, st)
            if url not in configuredList and getattr(self.settings, 'source', '') == 'dashboard':
                # a verified address learned from the leader: keep it in the list (marked), after the user's own
                row = db.get(Setting, CONFIG_KEY)
                if row is not None and isinstance(row.value_json, dict):
                    leaders = normaliseLeaders(row.value_json.get('leader_urls') or [], row.value_json.get('leader_url', ''))
                    leaders = normaliseLeaders(leaders + [{'url': url, 'label': 'from the leader', 'learned': True}])
                    row.value_json = dict(row.value_json, leader_urls=leaders, leader_url=leaders[0]['url'])
                    self.settings.sync_leader_urls = leaders
                    log.info('leader address added', url=url)
        self.state['leader_addresses'] = _cleanUrls(leader.get('addresses') or [])
        if leader.get('members') is not None:
            self.state['members'] = leader.get('members')
            self.state['primary_name'] = next((m.get('name') for m in leader['members'] if m.get('role') == 'primary'), '')

    def _loop(self):
        while not self.stopEvent.is_set():
            self.syncOnce()
            failures = self.state['consecutive_failures']
            delay = self.settings.sync_interval_seconds * (min(2 ** failures, 8) if failures else 1)  # back off
            self.stopEvent.wait(delay)

    def start(self):
        if self.thread is None:
            self.thread = threading.Thread(target=self._loop, name='ocrroute-sync', daemon=True)
            self.thread.start()

    def stop(self):
        self.stopEvent.set()
        if self.thread is not None:
            self.thread.join(timeout=5)


def _addrId(url):
    return 'la_' + hashlib.sha1(url.encode('utf-8')).hexdigest()[:10]


def normaliseLeaders(items, fallback=''):
    """
    :param items: list of {url, label, learned} dicts, or of URL strings (a comma-separated string is accepted too)
    :param fallback: str  a single leader URL (older configurations)
    :return: list[dict]  {id, url, label, learned}, in priority order, without duplicates
    """
    if isinstance(items, str):
        items = [u for u in items.split(',')]
    out, seen = [], set()
    for it in list(items or []) + ([fallback] if fallback else []):
        if isinstance(it, str):
            it = {'url': it}
        url = baseUrl(it.get('url'))
        if not url or url in seen:
            continue
        seen.add(url)
        out.append({'id': _addrId(url), 'url': url, 'label': str(it.get('label') or '')[:40].strip(),
                    'learned': bool(it.get('learned'))})
    return out[:10]


def validate(settings):
    """
    :return: list[str]  configuration problems (empty when the sync configuration is usable)
    """
    problems = []
    role = settings.sync_role
    if role not in ('off', 'leader', 'follower'):
        problems.append('OCRROUTE_SYNC_ROLE must be off, leader or follower (got {!r})'.format(role))
    if role in ('leader', 'follower') and len(settings.sync_token or '') < 24:
        problems.append('OCRROUTE_SYNC_TOKEN must be at least 24 characters (generate one: ocrroute sync token)')
    if role == 'follower' and interval_ok(settings) is False:
        problems.append('the sync interval must be between 1 and 3600 seconds')
    if role == 'follower':
        leaders = getattr(settings, 'sync_leader_urls', None) or normaliseLeaders([], settings.sync_leader_url or '')
        if not leaders:
            problems.append('add at least one leader address, e.g. https://ocr-1.example.com')
        bad = [a['url'] for a in leaders if not a['url'].startswith(('http://', 'https://'))]
        if bad:
            problems.append('leader addresses must start with http:// or https:// ({})'.format(', '.join(bad)))
    return problems


def interval_ok(settings):
    try:
        return 1 <= int(settings.sync_interval_seconds) <= 3600
    except (TypeError, ValueError):
        return False


def newToken():
    """
    :return: str  a random sync token
    """
    import secrets as _s

    return _s.token_urlsafe(32)


def nowMs():
    return int(time.time() * 1000)


__all__ = ['Follower', 'MANAGED_PREFIXES', 'TOKEN_HEADER', 'applySnapshot', 'buildSnapshot', 'newToken',
           'tokenMatches', 'validate']


# --------------------------------------------------------------------------------------------------------------- #
# configuration from the dashboard (stored locally under "sync.config"; .env / environment variables win)        #
# --------------------------------------------------------------------------------------------------------------- #
CONFIG_KEY = 'sync.config'
ENV_KEYS = ('OCRROUTE_SYNC_ROLE', 'OCRROUTE_SYNC_TOKEN', 'OCRROUTE_SYNC_LEADER_URL', 'OCRROUTE_SYNC_INTERVAL_SECONDS')


def configSource(settings):
    """
    :return: str  "environment" when any OCRROUTE_SYNC_* variable (or .env entry) sets sync, else "dashboard"
    """
    import os

    if any(os.environ.get(k) for k in ENV_KEYS):
        return 'environment'
    if settings.sync_role != 'off' or settings.sync_token or settings.sync_leader_url:  # values read from .env
        return 'environment'
    return 'dashboard'


def loadConfig(settings, db):
    """
    :return: SimpleNamespace  sync_role, sync_token, sync_leader_url, sync_interval_seconds, source
    """
    from types import SimpleNamespace

    source = configSource(settings)
    if source == 'environment':
        leaders = normaliseLeaders(settings.sync_leader_url or '')  # comma-separated list in .env
        return SimpleNamespace(cluster_name='cluster', sync_role=settings.sync_role, sync_token=settings.sync_token,
                               sync_leader_url=leaders[0]['url'] if leaders else '', sync_leader_urls=leaders,
                               sync_interval_seconds=settings.sync_interval_seconds, source=source)
    row = db.get(Setting, CONFIG_KEY)
    data = dict(row.value_json) if row is not None and isinstance(row.value_json, dict) else {}
    leaders = normaliseLeaders(data.get('leader_urls') or [], data.get('leader_url', ''))
    return SimpleNamespace(cluster_name=data.get('cluster_name', ''), sync_role=data.get('role', 'off'), sync_token=data.get('token', ''),
                           sync_leader_url=leaders[0]['url'] if leaders else '', sync_leader_urls=leaders,
                           sync_interval_seconds=int(data.get('interval_seconds', 30) or 30), source=source)


def saveConfig(db, role, token, leaderUrl, interval, leaders=None, clusterName=None):
    row = db.get(Setting, CONFIG_KEY)
    leaders = normaliseLeaders(leaders or [], leaderUrl or '')
    previous = row.value_json if row is not None and isinstance(row.value_json, dict) else {}
    value = {'role': role, 'token': token, 'leader_url': leaders[0]['url'] if leaders else '', 'leader_urls': leaders,
             'interval_seconds': int(interval),
             'cluster_name': (clusterName if clusterName is not None else previous.get('cluster_name', '')) if role != 'off' else ''}
    if row is None:
        db.add(Setting(key=CONFIG_KEY, value_json=value))
    else:
        row.value_json = value


def testLeader(url, token, http=None):
    """
    Ask a leader for a snapshot without applying it.

    :return: dict  {ok, message, counts}
    """
    if http is None:
        import requests as http
    try:
        r = http.get(url.rstrip('/') + '/v1/sync/snapshot', headers={TOKEN_HEADER: token}, timeout=15)
    except Exception as exc:  # noqa: BLE001
        return {'ok': False, 'message': 'Cannot reach {}: {}. Check the address, that the leader listens on 0.0.0.0 '
                                        '(not 127.0.0.1) and the firewall.'.format(url, type(exc).__name__), 'counts': {}}
    if r.status_code == 401:
        return {'ok': False, 'message': 'The leader rejected the token: copy the exact token from the leader.', 'counts': {}}
    if r.status_code == 403:
        try:
            why = r.json().get('error_message')
        except ValueError:
            why = None
        return {'ok': False, 'message': why or 'The leader refuses this server.', 'counts': {}}
    if r.status_code == 404:
        return {'ok': False, 'message': '{} answered, but it is not a sync leader: set its role to Leader.'.format(url),
                'counts': {}}
    if r.status_code != 200:
        return {'ok': False, 'message': 'Unexpected answer from the leader: HTTP {}'.format(r.status_code), 'counts': {}}
    data = r.json().get('data', {})
    counts = {k: len(data.get(k) or []) for k in ('providers', 'credentials', 'routes', 'api_keys', 'users')}
    return {'ok': True, 'message': 'Connected: the leader shares its configuration.', 'counts': counts}


class SyncManager(object):
    """
    SyncManager class: the running sync role of this server, reconfigurable live from the dashboard.
    """

    def __init__(self, settings, sessionFactory, secrets):
        self.settings = settings
        self.sessionFactory = sessionFactory
        self.secrets = secrets
        self.config = None
        self.follower = None
        self.lock = threading.Lock()

    @property
    def role(self):
        return self.config.sync_role if self.config is not None else 'off'

    def reload(self):
        """Read the configuration (environment or dashboard) and start / stop the follower to match."""
        with self.sessionFactory() as db:
            config = loadConfig(self.settings, db)
        self._apply(config)
        return config

    def _apply(self, config):
        with self.lock:
            if self.follower is not None:
                self.follower.stop()
                self.follower = None
            if getattr(self, 'watchStop', None) is not None:
                self.watchStop.set()
                self.watchStop = None
            self.config = config
            problems = validate(config)
            if problems:
                if config.sync_role != 'off':
                    log.error('cluster sync disabled: invalid configuration', problems=problems)
                return
            if config.sync_role == 'follower':
                self.follower = Follower(config, self.sessionFactory, self.secrets)
                self.follower.start()
                log.info('cluster sync: following', leader=config.sync_leader_url, source=config.source)
            elif config.sync_role == 'leader':
                self.watchStop = threading.Event()
                self.watcher = threading.Thread(target=_watch, args=(self, self.watchStop), name='ocrroute-sync-watch',
                                                daemon=True)
                self.watcher.start()
                log.info('cluster sync: serving snapshots to followers', source=config.source)

    def configure(self, role, token, leaderUrl, interval, leaders=None, clusterName=None):
        """
        Save the dashboard configuration and apply it immediately.

        :return: list[str]  problems (nothing saved when not empty)
        """
        from types import SimpleNamespace

        if configSource(self.settings) == 'environment':
            return ['Sync is configured by environment variables (.env) on this server; edit them there and restart.']
        leaders = normaliseLeaders(leaders or [], leaderUrl or '')
        candidate = SimpleNamespace(sync_role=role, sync_token=token, sync_leader_url=leaders[0]['url'] if leaders else '',
                                    sync_leader_urls=leaders, sync_interval_seconds=int(interval), source='dashboard')
        problems = validate(candidate)
        if problems:
            return problems
        with self.sessionFactory() as db:
            saveConfig(db, role, token, '', interval, leaders, clusterName)
        self.reload()
        return []

    # ---- the cluster verbs the dashboard uses
    def createCluster(self, name):
        name = (name or '').strip()[:60] or 'cluster'
        return self.configure('leader', newToken(), '', 30, None, name)

    def join(self, code):
        """
        Join with a join code: check the primary is reachable and proves it knows our secret, then follow it.

        :return: tuple(list[str], dict)  problems, join details
        """
        from ocrroute.runtime.endpoints import _serverId

        info = parseJoinCode(code)  # ValueError: shown as is
        if configSource(self.settings) == 'environment':
            return ['Sync is configured by environment variables (.env) on this server; edit them there and restart.'], info
        reasons = []
        for url in info['addresses']:
            ok, lid, why = verifyLeader(url, info['token'], _serverId(), info['leader_id'])
            if ok:
                break
            reasons.append('{}: {}'.format(url, why))
        else:
            return ['Cannot reach the primary server with this join code. ' + ' | '.join(reasons)], info
        with self.sessionFactory() as db:
            saveFollowerState(db, {'leader_id': info['leader_id'], 'addresses': info['addresses'], 'active_url': url, 'pushed': []})
        problems = self.configure('follower', info['token'], '', 15, [{'url': u} for u in info['addresses']], info['cluster'])
        if not problems and self.follower is not None:
            self.follower.syncOnce()
        return problems, info

    def leave(self):
        """Leave the cluster: stop following. The configuration copied so far stays on this server."""
        problems = self.configure('off', '', '', 30, None, '')
        if not problems:
            with self.sessionFactory() as db:
                saveFollowerState(db, {'leader_id': '', 'addresses': [], 'active_url': '', 'pushed': []})
        return problems

    def deleteCluster(self):
        """Primary: dissolve the cluster (members are refused from now on)."""
        problems = self.configure('off', '', '', 30, None, '')
        if not problems:
            with self.sessionFactory() as db:
                saveNodes(db, {'nodes': {}, 'revoked': []})
        return problems

    def stop(self):
        with self.lock:
            if self.follower is not None:
                self.follower.stop()
                self.follower = None
            if getattr(self, 'watchStop', None) is not None:
                self.watchStop.set()


__all__ += ['SyncManager', 'configSource', 'loadConfig', 'testLeader']


# --------------------------------------------------------------------------------------------------------------- #
# many servers behind tunnels: who follows this leader, and which of the leader's addresses to hand out          #
# --------------------------------------------------------------------------------------------------------------- #
NODE_HEADER = 'X-OcrRoute-Sync-Node'      # JSON: {name, version, digest} sent by followers with every poll
UNSTABLE_HOSTS = ('trycloudflare.com', 'ngrok-free.app', 'ngrok-free.dev', 'ngrok.io')  # new random URL per restart


def nodeIdentity(digest=None):
    """
    :return: dict  what a follower tells the leader about itself
    """
    import socket

    from ocrroute.runtime.endpoints import _serverId
    from ocrroute.version import __version__

    # id: this server's stable identifier (also on its Endpoints page). Two followers on one host, or behind one
    # NAT address, would otherwise be listed as a single follower on the leader.
    return {'id': _serverId(), 'name': socket.gethostname(), 'version': __version__, 'digest': digest or ''}


def classifyAddress(url):
    """
    :param url: str
    :return: dict  {url, stable, note}  stable=False for tunnel URLs that change on every restart
    """
    from urllib.parse import urlparse

    host = (urlparse(url).hostname or '').lower()
    if any(host == h or host.endswith('.' + h) for h in UNSTABLE_HOSTS):
        return {'url': url, 'stable': False,
                'note': 'temporary: this URL changes every time the tunnel restarts; use a named tunnel or a static domain for the leader'}
    if isLoopback(url):
        return {'url': url, 'stable': True,
                'note': 'this computer only: other servers cannot use it (use a tunnel, a public URL or a LAN address)'}
    if host.endswith('.ts.net'):
        return {'url': url, 'stable': True, 'note': 'Tailscale: private to your machines, stable name, HTTPS'}
    if url.startswith('https://'):
        return {'url': url, 'stable': True, 'note': 'HTTPS'}
    return {'url': url, 'stable': True, 'note': 'local network only (no HTTPS): fine on a LAN, never over the internet'}


def publicAddresses(settings, db):
    """
    Addresses followers can use to reach this server, best first: running tunnels, the manual public URL, then LAN.

    :return: list[dict]  {url, label, kind, stable, note}
    """
    out = []
    try:
        from ocrroute.api.routers.endpoints import readSettings
        from ocrroute.runtime.endpoints import getEndpointManager, localAddresses

        manual = baseUrl(readSettings(db).get('public_base_url', ''))
        for t in getEndpointManager(settings).snapshot(manual).get('tunnels', []):
            if t.get('running') and t.get('url'):
                out.append(dict(classifyAddress(str(t['url']).rstrip('/')), label=t.get('title') or t.get('name'), kind='tunnel'))
        if manual:
            out.append(dict(classifyAddress(manual.rstrip('/')), label='Public URL', kind='public'))
        for ip in localAddresses():
            out.append(dict(classifyAddress('http://{}:{}'.format(ip, settings.port)), label='LAN', kind='lan'))
    except Exception as exc:  # noqa: BLE001 - the page must render even if a tunnel probe fails
        log.warning('public addresses unavailable', error=str(exc))
    return out


def _recordFollower(manager, ip, header, currentDigest):
    try:
        info = json.loads(header) if header else {}
    except ValueError:
        info = {}
    name = str(info.get('name') or ip)
    key = str(info.get('id') or '{}@{}'.format(name, ip))
    now = datetime.now(timezone.utc).isoformat()
    entry = manager.followers.get(key) or {'first_seen': now}
    entry.update(id=key, name=name, ip=ip, version=str(info.get('version') or ''), last_seen=now,
                 digest=str(info.get('digest') or ''), up_to_date=bool(info.get('digest')) and info.get('digest') == currentDigest)
    manager.followers[key] = entry


SyncManager.followers = {}


def recordFollower(manager, ip, header, currentDigest):
    """
    Remember a follower that just polled the leader (kept in memory: an overview, not an audit log).

    :param manager: SyncManager
    :param ip: str  remote address of the poll
    :param header: str  the follower's ``X-OcrRoute-Sync-Node`` JSON
    :param currentDigest: str  the leader's current snapshot digest
    """
    if manager.followers is SyncManager.followers:  # per-instance dict
        manager.followers = {}
    _recordFollower(manager, ip, header, currentDigest)


__all__ += ['NODE_HEADER', 'classifyAddress', 'nodeIdentity', 'publicAddresses', 'recordFollower']


# --------------------------------------------------------------------------------------------------------------- #
# server cards on the leader: one card per follower, each with its own revocable token                            #
# --------------------------------------------------------------------------------------------------------------- #
NODES_KEY = 'sync.nodes'          # leader-local (the "sync." prefix is never synchronised)
OFFLINE_AFTER_SECONDS = 300


class SyncRefused(Exception):
    """A poll the leader refuses; ``status`` is the HTTP status to answer."""

    def __init__(self, status, message):
        Exception.__init__(self, message)
        self.status = status


def _encText(secrets, value):
    """Encrypt for JSON storage (the secret store returns bytes; Fernet output is ASCII)."""
    if secrets is None:
        return ''
    enc = secrets.encrypt(value)
    return enc.decode('ascii') if isinstance(enc, bytes) else str(enc)


def _decText(secrets, value):
    try:
        out = secrets.decrypt(value)
    except (TypeError, AttributeError):
        out = secrets.decrypt(value.encode('ascii'))
    return out.decode('utf-8') if isinstance(out, bytes) else out


def hashToken(token):
    return hashlib.sha256(('ocrroute-node:' + (token or '')).encode('utf-8')).hexdigest()


def loadNodes(db):
    row = db.get(Setting, NODES_KEY)
    data = row.value_json if row is not None and isinstance(row.value_json, dict) else {}
    return {'nodes': dict(data.get('nodes') or {}), 'revoked': list(data.get('revoked') or []),
            'revoked_tokens': list(data.get('revoked_tokens') or [])}


def saveNodes(db, reg):
    row = db.get(Setting, NODES_KEY)
    value = {'nodes': reg['nodes'], 'revoked': reg['revoked'], 'revoked_tokens': reg.get('revoked_tokens', [])[-500:]}
    if row is None:
        db.add(Setting(key=NODES_KEY, value_json=value))
    else:
        row.value_json = dict(value)  # a new object, so SQLAlchemy sees the change


def _newKey():
    import secrets as _s

    return 'srv_' + _s.token_hex(6)


def createNode(db, label, notes='', secrets=None, address=''):
    """
    :return: tuple(dict, str)  the card and its token (shown once: only a hash is stored)
    """
    reg = loadNodes(db)
    token = newToken()
    key = _newKey()
    reg['nodes'][key] = {'key': key, 'label': (label or 'Server').strip()[:80], 'notes': (notes or '')[:500],
                         'token_hash': hashToken(token), 'token_enc': _encText(secrets, token),
                         'paused': False, 'created_at': datetime.now(timezone.utc).isoformat(),
                         'server_id': '', 'name': '', 'ip': '', 'version': '', 'last_seen': '', 'first_seen': '', 'digest': '',
                         'manual_address': (_cleanUrls([address]) or [''])[0], 'addresses': _cleanUrls([address])}
    saveNodes(db, reg)
    return reg['nodes'][key], token


def updateNode(db, key, label=None, notes=None, paused=None, regenerate=False, secrets=None, address=None):
    """
    :return: tuple(dict, str | None)  the card, and the new token when ``regenerate``
    """
    reg = loadNodes(db)
    node = reg['nodes'].get(key)
    if node is None:
        raise KeyError(key)
    if label is not None:
        node['label'] = label.strip()[:80] or node['label']
    if notes is not None:
        node['notes'] = notes[:500]
    if paused is not None:
        node['paused'] = bool(paused)
    if address is not None:
        manual = (_cleanUrls([address]) or [''])[0]
        if address.strip() and not manual:
            raise ValueError('the address must start with http:// or https://')
        node['manual_address'] = manual
        node['addresses'] = _cleanUrls([manual] + [a for a in node.get('addresses') or [] if a != node.get('manual_address')])
    token = None
    if regenerate:
        token = newToken()
        node['token_hash'] = hashToken(token)
        node['token_enc'] = _encText(secrets, token)
    saveNodes(db, reg)
    return node, token


def deleteNode(db, key):
    """Remove a card and revoke that server: its own token stops working, and if it used the shared token its server
    id is refused too (create a new card to let it back in)."""
    reg = loadNodes(db)
    node = reg['nodes'].pop(key, None)
    if node is None:
        raise KeyError(key)
    if node.get('server_id') and node['server_id'] not in reg['revoked']:
        reg['revoked'].append(node['server_id'])
    if node.get('token_hash'):  # so the removed server is told why, instead of "invalid token"
        reg.setdefault('revoked_tokens', []).append(node['token_hash'])
    saveNodes(db, reg)
    return node


def authenticatePoll(db, sharedToken, presented, info):
    """
    Decide whether a follower's poll is allowed, and on which card it lands.

    :param presented: str  token the follower sent
    :param info: dict  the follower's identity header (id, name, version, digest)
    :return: str  card key
    :raises SyncRefused: 401 unknown token, 403 paused or removed
    """
    reg = loadNodes(db)
    serverId = str(info.get('id') or '')
    hashed = hashToken(presented)
    for key, node in reg['nodes'].items():
        if node.get('token_hash') and hmac.compare_digest(node['token_hash'], hashed):
            if node.get('paused'):
                raise SyncRefused(403, 'The leader paused sync for this server ("{}").'.format(node['label']))
            if serverId in reg['revoked']:  # a new card's token lets a removed server back in
                reg['revoked'].remove(serverId)
                saveNodes(db, reg)
            return key
    if hashed in reg.get('revoked_tokens', []):
        raise SyncRefused(403, 'The primary removed this server from the cluster. To come back, ask for a new join code '
                               'and join again.')
    if tokenMatches(sharedToken, presented):
        if serverId and serverId in reg['revoked']:
            raise SyncRefused(403, 'The primary removed this server from the cluster. To come back, ask for a new join code '
                                   'and join again.')
        for key, node in reg['nodes'].items():
            if serverId and node.get('server_id') == serverId and not node.get('token_hash'):
                if node.get('paused'):
                    raise SyncRefused(403, 'The leader paused sync for this server ("{}").'.format(node['label']))
                return key
        key = _newKey()  # a server using the shared token appears as a card automatically
        reg['nodes'][key] = {'key': key, 'label': str(info.get('name') or 'Server')[:80], 'notes': '', 'token_hash': '',
                             'paused': False, 'created_at': datetime.now(timezone.utc).isoformat(), 'server_id': serverId,
                             'name': '', 'ip': '', 'version': '', 'last_seen': '', 'first_seen': '', 'digest': ''}
        saveNodes(db, reg)
        return key
    raise SyncRefused(401, 'invalid sync token')


def recordPoll(db, key, info, ip, currentDigest):
    reg = loadNodes(db)
    node = reg['nodes'].get(key)
    if node is None:
        return
    now = datetime.now(timezone.utc).isoformat()
    node.update(server_id=str(info.get('id') or node.get('server_id') or ''), name=str(info.get('name') or ''),
                ip=ip, version=str(info.get('version') or ''), last_seen=now, first_seen=node.get('first_seen') or now,
                digest=str(info.get('digest') or ''))
    # where the leader can notify it: what the follower reports, plus an address typed on its card
    node['addresses'] = _cleanUrls((info.get('urls') or []) + [node.get('manual_address') or ''] + (node.get('addresses') or []))
    node['up_to_date'] = bool(node['digest']) and node['digest'] == currentDigest
    saveNodes(db, reg)


def nodeStatus(node, now=None):
    """
    :return: str  never | paused | offline | behind | up_to_date
    """
    if node.get('paused'):
        return 'paused'
    if not node.get('last_seen'):
        return 'never'
    now = now or datetime.now(timezone.utc)
    if (now - datetime.fromisoformat(node['last_seen'])).total_seconds() > OFFLINE_AFTER_SECONDS:
        return 'offline'
    return 'up_to_date' if node.get('up_to_date') else 'behind'


def listNodes(db):
    out = []
    for node in loadNodes(db)['nodes'].values():
        view = {k: v for k, v in node.items() if k not in ('token_hash', 'token_enc')}
        view['own_token'] = bool(node.get('token_hash'))
        view['status'] = nodeStatus(node)
        out.append(view)
    return sorted(out, key=lambda n: (n['label'] or '').lower())


__all__ += ['SyncRefused', 'authenticatePoll', 'createNode', 'deleteNode', 'listNodes', 'recordPoll', 'updateNode']


# --------------------------------------------------------------------------------------------------------------- #
# resilient addressing: plain-language errors, learned leader addresses with failover, and address pushes        #
# --------------------------------------------------------------------------------------------------------------- #
LEADER_HEADER = 'X-OcrRoute-Leader'       # JSON {id, addresses} on every snapshot answer (200 and 304)
FOLLOWER_STATE_KEY = 'sync.follower'      # follower-local: {leader_id, addresses, active_url}
LOG_REPEAT_SECONDS = 600
PRIMARY_RECHECK_SECONDS = 600   # a follower on a backup address retries the primary this often


def describeError(exc, url):
    """
    :param exc: Exception  raised while calling the leader
    :param url: str
    :return: str  what went wrong, and what to do, in plain language
    """
    from urllib.parse import urlparse

    host = urlparse(url).hostname or url
    text = '{}: {}'.format(type(exc).__name__, exc)
    temporary = any(host == h or host.endswith('.' + h) for h in UNSTABLE_HOSTS)
    if 'NameResolution' in text or 'getaddrinfo' in text or 'Name or service not known' in text \
            or 'nodename nor servname' in text or 'Temporary failure in name resolution' in text:
        if temporary:
            return ('The leader address {} no longer exists: Cloudflare quick-tunnel and free ngrok URLs get a new '
                    'random name every time the tunnel restarts. Open the leader\'s Cluster sync page, copy its current '
                    'address and paste it here (or click "Notify followers" there). For good, give the leader a stable '
                    'address: Tailscale, a Cloudflare named tunnel or an ngrok static domain.').format(host)
        return 'Cannot find {} (DNS): check the leader address and this server\'s internet connection.'.format(host)
    if 'Timeout' in text or 'timed out' in text:
        return 'The leader at {} did not answer in time: it may be down, overloaded or blocked by a firewall.'.format(host)
    if 'Connection refused' in text or 'ConnectionRefused' in text or 'actively refused' in text or 'WinError 10061' in text:
        return ('Nothing answers at {}: the leader is stopped, the port is wrong, or it only listens on 127.0.0.1 '
                '(behind a tunnel that is fine; on a LAN set OCRROUTE_HOST=0.0.0.0).').format(url)
    if 'SSL' in text or 'CERTIFICATE' in text.upper():
        return 'HTTPS certificate problem with {}: check the address (https://...) and the tunnel.'.format(host)
    return text[:400]


def describeStatus(code, url):
    from urllib.parse import urlparse

    host = urlparse(url).hostname or url
    if code in (502, 503, 504, 530):
        return ('The address {} answers, but no leader runs behind it (HTTP {}): the tunnel is up and OcrRoute on the '
                'leader is stopped, or the tunnel points to the wrong port.').format(host, code)
    return 'Unexpected answer from the leader at {}: HTTP {}.'.format(host, code)


def loadFollowerState(db):
    row = db.get(Setting, FOLLOWER_STATE_KEY)
    data = row.value_json if row is not None and isinstance(row.value_json, dict) else {}
    return {'leader_id': data.get('leader_id', ''), 'addresses': list(data.get('addresses') or []),
            'active_url': data.get('active_url', ''), 'pushed': list(data.get('pushed') or [])}


def saveFollowerState(db, state):
    row = db.get(Setting, FOLLOWER_STATE_KEY)
    value = {k: state.get(k) for k in ('leader_id', 'addresses', 'active_url', 'pushed')}
    if row is None:
        db.add(Setting(key=FOLLOWER_STATE_KEY, value_json=value))
    else:
        row.value_json = dict(value)


def baseUrl(url):
    """
    The OcrRoute base URL inside anything a user may paste: ``http://host:20256/panel/cluster`` or ``.../v1/docs`` become
    ``http://host:20256``; a reverse-proxy prefix is kept (``https://example.com/ocr/panel`` -> ``https://example.com/ocr``).
    """
    from urllib.parse import urlsplit, urlunsplit

    u = str(url or '').strip()
    if not u:
        return ''
    parts = urlsplit(u)
    segs = [x for x in parts.path.split('/') if x]
    for i, seg in enumerate(segs):
        if seg in ('panel', 'v1'):
            segs = segs[:i]
            break
    path = '/' + '/'.join(segs) if segs else ''
    return urlunsplit((parts.scheme, parts.netloc, path, '', '')).rstrip('/')


def isLoopback(url):
    from urllib.parse import urlsplit

    host = (urlsplit(url).hostname or '').lower()
    return host in ('localhost', '::1') or host.startswith('127.')


def _cleanUrls(urls, limit=10):
    out = []
    for u in urls or []:
        u = baseUrl(u)
        if u.startswith(('http://', 'https://')) and u not in out and len(u) < 300:
            out.append(u)
    return out[:limit]


def proofFor(token, nonce, leaderId):
    return hmac.new(token.encode('utf-8'), '{}|{}'.format(nonce, leaderId).encode('utf-8'), hashlib.sha256).hexdigest()


def tokenForServer(db, secrets, sharedToken, serverId, tokenHash=''):
    """
    :param tokenHash: str  ``hashToken(token)`` sent by the follower: identifies its card before it is bound to a
                      server id (a server joining for the first time). It is the one-way hash already stored on the
                      card, so it reveals nothing usable.
    :return: str | None  the token a given follower uses (its card's own token, else the shared token)
    """
    if tokenHash:
        for node in loadNodes(db)['nodes'].values():
            if node.get('token_hash') and hmac.compare_digest(node['token_hash'], tokenHash) and node.get('token_enc'):
                try:
                    return _decText(secrets, node['token_enc'])
                except Exception:  # noqa: BLE001
                    return None
        if sharedToken and hmac.compare_digest(hashToken(sharedToken), tokenHash):
            return sharedToken
    for node in loadNodes(db)['nodes'].values():
        if serverId and node.get('server_id') == serverId:
            if node.get('token_enc'):
                try:
                    return _decText(secrets, node['token_enc'])
                except Exception:  # noqa: BLE001
                    return None
            if not node.get('token_hash'):
                return sharedToken or None
            return None  # card created before 0.7.1 stored only a hash: rotate its token to enable address proofs
    return sharedToken or None


def leaderAddresses(settings, db):
    """:return: list[str]  this leader's addresses, best first (tunnels, public URL, then LAN)"""
    ranked = sorted(publicAddresses(settings, db), key=lambda a: (isLoopback(a['url']), not a['stable'],
                                                                  {'tunnel': 0, 'public': 1}.get(a['kind'], 2)))
    return _cleanUrls([a['url'] for a in ranked])


def verifyLeader(url, token, serverId, expectedLeaderId='', http=None):
    """
    Ask ``url`` to prove it knows ``token`` BEFORE sending the token there (challenge / response).

    :return: tuple(bool, str, str)  (ok, leader id, reason)
    """
    import secrets as _s

    if http is None:
        import requests as http
    nonce = _s.token_hex(16)
    try:
        r = http.post(url.rstrip('/') + '/v1/sync/hello',
                      json={'server_id': serverId, 'nonce': nonce, 'token_hash': hashToken(token)}, timeout=15)
    except Exception as exc:  # noqa: BLE001
        return False, '', describeError(exc, url)
    if r.status_code != 200:
        return False, '', describeStatus(r.status_code, url) if r.status_code >= 500 else 'not a leader for this server'
    try:
        data = r.json()
    except ValueError:
        return False, '', 'not an OcrRoute leader'
    leaderId = str(data.get('leader_id') or '')
    if not hmac.compare_digest(str(data.get('proof') or ''), proofFor(token, nonce, leaderId)):
        return False, leaderId, 'this server does not know our token: not our leader (token not sent)'
    if expectedLeaderId and leaderId != expectedLeaderId:
        return False, leaderId, 'a different leader answered (id {}, expected {})'.format(leaderId, expectedLeaderId)
    return True, leaderId, ''


__all__ += ['normaliseLeaders', 'describeError', 'leaderAddresses', 'proofFor', 'tokenForServer', 'verifyLeader']


def notifyFollowers(settings, sessionFactory, http=None):
    """
    Push this leader's current addresses to every follower that reported how to reach it (its tunnel / public URL).
    A follower never trusts the push blindly: it verifies the new address by challenge / response before using it.

    :return: dict  {card key: result}
    """
    if http is None:
        import requests as http
    from ocrroute.runtime.endpoints import _serverId

    with sessionFactory() as db:
        addresses = leaderAddresses(settings, db)
        nodes = [dict(n) for n in loadNodes(db)['nodes'].values()]
    body = {'leader_id': _serverId(), 'addresses': addresses}
    results = {}
    for node in nodes:
        outcome = 'no address reported yet'
        for url in node.get('addresses') or []:
            try:
                r = http.post(url.rstrip('/') + '/v1/sync/leader-moved', json=body, timeout=10)
                outcome = 'delivered via {}'.format(url) if r.status_code == 200 else 'HTTP {} from {}'.format(r.status_code, url)
                if r.status_code == 200:
                    break
            except Exception as exc:  # noqa: BLE001
                outcome = describeError(exc, url)
        results[node['key']] = outcome
    with sessionFactory() as db:
        reg = loadNodes(db)
        now = datetime.now(timezone.utc).isoformat()
        for key, outcome in results.items():
            if key in reg['nodes']:
                reg['nodes'][key].update(notified_at=now, notify_result=outcome)
        saveNodes(db, reg)
    log.info('leader addresses pushed to followers', addresses=addresses, followers=len(results))
    return results


def receivePush(manager, body):
    """
    Follower side of a push: remember the proposed addresses and try them now (each is verified before use).

    :return: dict
    """
    if manager.role != 'follower' or manager.follower is None:
        return {'accepted': False, 'reason': 'not a follower'}
    urls = _cleanUrls(body.get('addresses') or [])
    if not urls:
        return {'accepted': False, 'reason': 'no address'}
    now = time.time()
    if now - getattr(manager, '_lastPush', 0) < 5:
        return {'accepted': True, 'queued': False}
    manager._lastPush = now
    with manager.sessionFactory() as db:
        st = loadFollowerState(db)
        st['pushed'] = _cleanUrls(urls + st['pushed'])
        saveFollowerState(db, st)
    threading.Thread(target=manager.follower.syncOnce, name='ocrroute-sync-push', daemon=True).start()
    return {'accepted': True, 'queued': True}


def _watch(manager, stop):
    """Leader: push the new addresses to followers whenever this server's own address changes (tunnel restart...).

    :param stop: threading.Event  owned by this thread (the manager replaces its own on every reconfiguration)
    """
    import os

    interval = max(2, int(os.environ.get('OCRROUTE_SYNC_WATCH_SECONDS', '20') or 20))
    previous = None
    while not stop.is_set():
        try:
            from ocrroute.config import getSettings

            with manager.sessionFactory() as db:
                current = leaderAddresses(getSettings(), db)
            if previous is not None and current and current != previous:
                log.info('leader address changed', old=previous, new=current)
                notifyFollowers(getSettings(), manager.sessionFactory)
            previous = current
        except Exception as exc:  # noqa: BLE001 - the watcher must never stop
            log.warning('leader address watch failed', error=str(exc))
        stop.wait(interval)


__all__ += ['notifyFollowers', 'receivePush']


# --------------------------------------------------------------------------------------------------------------- #
# cluster model (like Proxmox / Docker Swarm): create a cluster on the primary, join others with a join code     #
# --------------------------------------------------------------------------------------------------------------- #
JOIN_PREFIX = 'OCRJ1-'


def makeJoinCode(clusterName, leaderId, leaderName, addresses, token):
    """
    :return: str  one string with everything a server needs to join: the primary's addresses, its id (checked by
             challenge / response before the secret is used) and this server's own secret
    """
    payload = {'v': 1, 'c': clusterName, 'id': leaderId, 'n': leaderName, 'a': _cleanUrls(addresses), 't': token}
    raw = json.dumps(payload, separators=(',', ':')).encode('utf-8')
    check = hashlib.sha256(raw).hexdigest()[:6]
    return JOIN_PREFIX + base64.urlsafe_b64encode(raw).decode('ascii').rstrip('=') + '.' + check


def parseJoinCode(code):
    """
    :return: dict  {cluster, leader_id, leader_name, addresses, token}
    :raises ValueError: with a message a user can act on
    """
    code = ''.join((code or '').split())  # tolerate line breaks from copy / paste
    if not code.startswith(JOIN_PREFIX):
        raise ValueError('This is not an OcrRoute join code (it starts with {}). Copy it again from the primary server.'
                         .format(JOIN_PREFIX))
    body, _, check = code[len(JOIN_PREFIX):].partition('.')
    try:
        raw = base64.urlsafe_b64decode(body + '=' * (-len(body) % 4))
        data = json.loads(raw.decode('utf-8'))
    except Exception:  # noqa: BLE001
        raise ValueError('The join code is damaged (incomplete copy?). Copy it again from the primary server.')
    if hashlib.sha256(raw).hexdigest()[:6] != check:
        raise ValueError('The join code is damaged (incomplete copy?). Copy it again from the primary server.')
    if data.get('v') != 1 or not data.get('t') or not _cleanUrls(data.get('a')):
        raise ValueError('This join code has no usable address or secret. Create a new one on the primary server.')
    return {'cluster': str(data.get('c') or 'cluster'), 'leader_id': str(data.get('id') or ''),
            'leader_name': str(data.get('n') or ''), 'addresses': _cleanUrls(data.get('a')), 'token': str(data['t'])}


def joinCodeForNode(db, settings, secrets, key):
    """:return: str  the (re-derivable) join code of a member card"""
    import socket

    from ocrroute.runtime.endpoints import _serverId

    config = loadConfig(settings, db)
    node = loadNodes(db)['nodes'].get(key)
    if node is None:
        raise KeyError(key)
    if not node.get('token_enc'):
        raise ValueError('this server was added with the shared token: use "New join code" to give it its own')
    token = _decText(secrets, node['token_enc'])
    return makeJoinCode(getattr(config, 'cluster_name', '') or 'cluster', _serverId(), socket.gethostname(),
                        leaderAddresses(settings, db), token)


def clusterMembers(db, settings):
    """:return: list[dict]  the primary first, then every member card (shared with members in the leader header)"""
    import socket

    from ocrroute.runtime.endpoints import _serverId
    from ocrroute.version import __version__

    primary = {'key': 'primary', 'name': socket.gethostname(), 'label': socket.gethostname(), 'role': 'primary',
               'status': 'up_to_date', 'version': __version__, 'server_id': _serverId(), 'last_seen': datetime.now(timezone.utc).isoformat()}
    members = [dict(n, role='member') for n in listNodes(db)]
    return [primary] + members


__all__ += ['clusterMembers', 'joinCodeForNode', 'makeJoinCode', 'parseJoinCode']


def explainNotLeader(url, http=None):
    """
    :return: str  why ``url`` does not serve snapshots, in plain language (asks its public identity endpoint)
    """
    if http is None:
        import requests as http
    from ocrroute.runtime.endpoints import _serverId

    try:
        who = http.get(url.rstrip('/') + '/v1/sync/identity', timeout=10).json()
    except Exception:  # noqa: BLE001
        return ('{} answers, but it is not an OcrRoute primary (wrong address or path?). Use the primary\'s address as '
                'shown in its join code or on its Cluster page.').format(url)
    if who.get('server_id') == _serverId():
        return ('{} is this server itself, not the primary. Join with a join code created on the primary, or use the '
                'primary\'s address (tunnel, public URL or LAN).').format(url)
    mode = who.get('mode')
    if mode == 'member':
        return '{} is a member of a cluster, not the primary: use the primary\'s address.'.format(url)
    return ('{} is not the primary of a cluster (it is {}). Create a cluster on it first, then join with a join code.'
            .format(url, 'standalone' if mode == 'standalone' else mode or 'not in a cluster'))


__all__ += ['baseUrl', 'explainNotLeader', 'isLoopback']
