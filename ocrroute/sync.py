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
    return key.startswith(LOCAL_SETTING_PREFIXES)


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
    keep = {}
    skippedProviders = set()
    for name, model, exclude, secretCols in SPEC:
        cols = {c.name: c for c in _columns(model, exclude)}
        count, ids = 0, set()
        for row in data.get(name, []):
            if name == 'providers' and row.get('engine_id') not in knownEngines:
                skippedProviders.add(row['id'])
                skipped.append('provider {} ({}): engine {} is not installed here'.format(
                    row.get('label'), row['id'], row.get('engine_id')))
                continue
            if name in ('credentials', 'route_members') and row.get('provider_id') in skippedProviders:
                continue
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
            ids.add(values['id'])
            count += 1
        db.flush()
        keep[name] = ids
        upserted[name] = count
    # Mirror deletions, children first (route members / credentials / keys before the routes and providers).
    for name, model, _exclude, _secret in reversed(SPEC):
        gone = [obj for obj in db.query(model).all() if obj.id not in keep[name]]
        for obj in gone:
            db.delete(obj)
        removed[name] = len(gone)
        db.flush()
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
        :return: dict  the state after this attempt
        """
        with self.lock:
            self.state['last_attempt_at'] = datetime.now(timezone.utc).isoformat()
            headers = {TOKEN_HEADER: self.settings.sync_token}
            if self.state['digest']:
                headers['If-None-Match'] = '"{}"'.format(self.state['digest'])
            url = self.settings.sync_leader_url.rstrip('/') + '/v1/sync/snapshot'
            try:
                r = self._get(url, headers)
                if r.status_code == 304:
                    self.state.update(last_success_at=self.state['last_attempt_at'], last_error=None,
                                      consecutive_failures=0)
                    return dict(self.state)
                if r.status_code == 401:
                    raise RuntimeError('the leader rejected the sync token (OCRROUTE_SYNC_TOKEN must match)')
                if r.status_code == 404:
                    raise RuntimeError('{} is not a sync leader (set OCRROUTE_SYNC_ROLE=leader there)'.format(
                        self.settings.sync_leader_url))
                r.raise_for_status()
                snapshot = r.json()
                with self.sessionFactory() as db:
                    result = applySnapshot(db, self.secrets, self.settings.sync_token, snapshot)
                self.state.update(digest=snapshot['digest'], last_success_at=self.state['last_attempt_at'],
                                  last_change_at=self.state['last_attempt_at'], last_error=None, last_result=result,
                                  consecutive_failures=0)
                log.info('sync applied', digest=snapshot['digest'][:12], **{k: v for k, v in result['upserted'].items()})
            except Exception as exc:  # noqa: BLE001 - a failed poll must never stop the follower
                self.state['last_error'] = '{}: {}'.format(type(exc).__name__, exc)[:500]
                self.state['consecutive_failures'] += 1
                log.warning('sync failed', error=self.state['last_error'])
            return dict(self.state)

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
    if role == 'follower' and not (settings.sync_leader_url or '').startswith(('http://', 'https://')):
        problems.append('OCRROUTE_SYNC_LEADER_URL must be the leader base URL, e.g. https://ocr-1.example.com')
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
        return SimpleNamespace(sync_role=settings.sync_role, sync_token=settings.sync_token,
                               sync_leader_url=settings.sync_leader_url,
                               sync_interval_seconds=settings.sync_interval_seconds, source=source)
    row = db.get(Setting, CONFIG_KEY)
    data = dict(row.value_json) if row is not None and isinstance(row.value_json, dict) else {}
    return SimpleNamespace(sync_role=data.get('role', 'off'), sync_token=data.get('token', ''),
                           sync_leader_url=data.get('leader_url', ''),
                           sync_interval_seconds=int(data.get('interval_seconds', 30) or 30), source=source)


def saveConfig(db, role, token, leaderUrl, interval):
    row = db.get(Setting, CONFIG_KEY)
    value = {'role': role, 'token': token, 'leader_url': leaderUrl.rstrip('/'), 'interval_seconds': int(interval)}
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
                log.info('cluster sync: serving snapshots to followers', source=config.source)

    def configure(self, role, token, leaderUrl, interval):
        """
        Save the dashboard configuration and apply it immediately.

        :return: list[str]  problems (nothing saved when not empty)
        """
        from types import SimpleNamespace

        if configSource(self.settings) == 'environment':
            return ['Sync is configured by environment variables (.env) on this server; edit them there and restart.']
        candidate = SimpleNamespace(sync_role=role, sync_token=token, sync_leader_url=leaderUrl,
                                    sync_interval_seconds=int(interval), source='dashboard')
        problems = validate(candidate)
        if problems:
            return problems
        with self.sessionFactory() as db:
            saveConfig(db, role, token, leaderUrl, interval)
        self.reload()
        return []

    def stop(self):
        with self.lock:
            if self.follower is not None:
                self.follower.stop()
                self.follower = None


__all__ += ['SyncManager', 'configSource', 'loadConfig', 'testLeader']
