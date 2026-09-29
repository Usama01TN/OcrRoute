# coding=utf-8
"""Engine/session factory with SQLite pragmas and a retry-on-busy context manager."""
from __future__ import absolute_import, division, print_function

import threading
import time
from contextlib import contextmanager

from sqlalchemy import create_engine, event, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from ocrroute.config import getSettings
from ocrroute.db.base import Base

_engine = None
_factory = None
_lock = threading.Lock()


def _configureSqlite(dbapi_conn, _record):
    cur = dbapi_conn.cursor()
    cur.execute('PRAGMA journal_mode=WAL')
    cur.execute('PRAGMA foreign_keys=ON')
    cur.execute('PRAGMA busy_timeout=5000')
    cur.execute('PRAGMA synchronous=NORMAL')
    cur.close()


def getEngine(url=None):
    global _engine, _factory
    with _lock:
        if _engine is None:
            url = url or getSettings().databaseUrl
            kwargs = {'future': True}
            if url.startswith('sqlite'):
                kwargs['connect_args'] = {'check_same_thread': False, 'timeout': 5}
            _engine = create_engine(url, **kwargs)
            if url.startswith('sqlite'):
                event.listen(_engine, 'connect', _configureSqlite)
            _factory = sessionmaker(bind=_engine, expire_on_commit=False)
        return _engine


def getSession():
    getEngine()
    assert _factory is not None
    return _factory()


@contextmanager
def sessionScope(retries=5):
    """Commit on success, rollback on error, retry when SQLite reports the database is locked."""
    delay = 0.05
    for attempt in range(retries):
        session = getSession()
        try:
            yield session
            session.commit()
            return
        except OperationalError as exc:
            session.rollback()
            if 'locked' in str(exc).lower() and attempt < retries - 1:
                time.sleep(delay)
                delay *= 2
                continue
            raise
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()


def initDb(url=None):
    engine = getEngine(url)
    Base.metadata.create_all(engine)
    from ocrroute.version import __version__

    with engine.begin() as conn:
        conn.execute(
            text("INSERT OR REPLACE INTO schema_meta(key, value) VALUES ('app_version', :v)"), {'v': __version__}
        )
        conn.execute(text("INSERT OR IGNORE INTO schema_meta(key, value) VALUES ('alembic_version', '0001_initial')"))


def dispose():
    global _engine, _factory
    with _lock:
        if _engine is not None:
            _engine.dispose()
        _engine = None
        _factory = None
