# coding=utf-8
"""Scheduled housekeeping: cache expiry, rollups, retention, backups, PRAGMA optimize."""
from __future__ import absolute_import, division, print_function

import shutil
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from time import time

from sqlalchemy import text

from ocrroute.db.models import Artifact, Run
from ocrroute.db.repo.usage import rollupDay
from ocrroute.db.session import getEngine, sessionScope
from ocrroute.logsetup import getLogger
from ocrroute.runtime import cache as cache_mod

log = getLogger(__name__)


def runOnce(settings):
    out = {'cache_purged': 0, 'days_rolled': 0, 'runs_deleted': 0, 'artifacts_pruned': 0}
    with sessionScope() as s:
        out['cache_purged'] = cache_mod.purgeExpired(s)
        cutoff = (datetime.now(timezone.utc) - timedelta(days=settings.log_retention_days)).date().isoformat()
        days = {
            r[0]
            for r in s.execute(
                text('select substr(created_at,1,10) from runs where created_at < :c'), {'c': cutoff}
            ).all()
        }
        for d in sorted(days):
            rollupDay(s, d)
            out['days_rolled'] += 1
        old = s.query(Run).filter(Run.created_at < cutoff).all()
        for run in old:
            for a in run.artifacts:
                try:
                    Path(a.path).unlink(missing_ok=True)
                    out['artifacts_pruned'] += 1
                except OSError:
                    pass
            s.delete(run)
            out['runs_deleted'] += 1
        live_paths = {a.path for a in s.query(Artifact).all()}
    root = settings.artifactRoot
    if root.exists():
        for p in root.rglob('*'):
            if p.is_file() and str(p) not in live_paths:
                p.unlink(missing_ok=True)
                out['artifacts_pruned'] += 1
        for d in sorted((d for d in root.rglob('*') if d.is_dir()), key=lambda x: -len(x.parts)):
            try:
                d.rmdir()
            except OSError:
                pass
    with getEngine().begin() as conn:
        conn.execute(text('PRAGMA optimize'))
    return out


def backup(settings, dest=None):
    src = settings.home / 'ocrroute.db'
    dest = dest or settings.home / 'backups' / 'ocrroute-{}.db'.format(
        datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    )
    dest.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(src) as source, sqlite3.connect(dest) as target:
        source.backup(target)
    return dest


def restore(settings, src):
    from ocrroute.db.session import dispose

    dispose()
    shutil.copy2(src, settings.home / 'ocrroute.db')
    for suffix in ('-wal', '-shm'):
        p = settings.home / 'ocrroute.db{}'.format(suffix)
        p.unlink(missing_ok=True)


def integrity(settings):
    with sqlite3.connect(settings.home / 'ocrroute.db') as c:
        return str(c.execute('PRAGMA integrity_check').fetchone()[0])


def vacuum(settings):
    with sqlite3.connect(settings.home / 'ocrroute.db') as c:
        c.execute('VACUUM')


class Scheduler(threading.Thread):
    def __init__(self, settings, interval_s=3600.0):
        super(Scheduler, self).__init__(daemon=True, name='ocrroute-maintenance')
        self.__m_settings = settings
        self.__m_interval = interval_s
        self.__m_stop = threading.Event()
        self.__m_lastResult = {}
        self.__m_lastRun = ''

    def rescanEngines(self):
        """
        Detect engines added, removed or repaired on disk and refresh the catalogue and providers.

        :return: bool  True when the catalogue changed
        """
        from ocrroute.db.repo.engines import seedProviders, syncEngines
        from ocrroute.runtime.context import getContext

        ctx = getContext()
        if not ctx.registry.rescanIfChanged():
            return False
        with sessionScope() as s:
            syncEngines(s, ctx.registry)
            if self.__m_settings.auto_seed_providers:
                seedProviders(s, ctx.registry)
        log.info('engines rescanned', available=len(ctx.registry.available()))
        return True

    def run(self):
        rescanEvery = max(0, int(getattr(self.__m_settings, 'engine_rescan_minutes', 0))) * 60
        nextRescan = time() + rescanEvery if rescanEvery else None
        tick = min(self.__m_interval, 60.0)
        elapsed = 0.0
        while not self.__m_stop.wait(tick):
            elapsed += tick
            if nextRescan and time() >= nextRescan:
                nextRescan = time() + rescanEvery
                try:
                    self.rescanEngines()
                except Exception as exc:  # noqa: BLE001
                    log.warning('engine rescan failed', error=str(exc))
            if elapsed < self.__m_interval:
                continue
            elapsed = 0.0
            try:
                self.__m_lastResult = runOnce(self.__m_settings)
                self.__m_lastRun = datetime.now(timezone.utc).isoformat()
                log.info('maintenance done', **self.__m_lastResult)
            except Exception as exc:  # noqa: BLE001
                log.warning('maintenance failed', error=str(exc))

    def stop(self):
        self.__m_stop.set()

    def getSettings(self):
        """
        :return: any
        """
        return self.__m_settings

    def setSettings(self, settings):
        """
        :param settings: any
        """
        self.__m_settings = settings

    def getInterval(self):
        """
        :return: any
        """
        return self.__m_interval

    def setInterval(self, interval):
        """
        :param interval: any
        """
        self.__m_interval = interval

    def getLastResult(self):
        """
        :return: any
        """
        return self.__m_lastResult

    def setLastResult(self, lastResult):
        """
        :param lastResult: any
        """
        self.__m_lastResult = lastResult

    def getLastRun(self):
        """
        :return: any
        """
        return self.__m_lastRun

    def setLastRun(self, lastRun):
        """
        :param lastRun: any
        """
        self.__m_lastRun = lastRun

    settings = property(fget=getSettings, fset=setSettings)
    interval = property(fget=getInterval, fset=setInterval)
    last_result = property(fget=getLastResult, fset=setLastResult)
    last_run = property(fget=getLastRun, fset=setLastRun)
