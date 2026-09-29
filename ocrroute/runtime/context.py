# coding=utf-8
"""Application-wide singletons wired once at startup and shared by the API, panel, CLI and executor."""
from __future__ import absolute_import, division, print_function

import concurrent.futures as cf

from ocrroute.catalog.registry import getRegistry
from ocrroute.config import getSettings
from ocrroute.crypto import SecretStore, loadOrCreateKey
from ocrroute.db.repo.engines import seedProviders, syncEngines
from ocrroute.db.session import initDb, sessionScope
from ocrroute.logsetup import configureLogging
from ocrroute.routing.breaker import CircuitBreaker
from ocrroute.routing.router import Router
from ocrroute.runtime.executor import Executor


class AppContext(object):
    """
    Application-wide singletons shared by API, panel, CLI and executor.
    """

    def __init__(self, *args, **kwargs):
        """
        :param settings: Settings
        :param registry: EngineRegistry
        :param secrets: SecretStore
        :param breaker: CircuitBreaker
        :param router: Router
        :param executor: Executor
        """
        args = list(args)
        self.__m_settings = kwargs.pop('settings', args.pop(0) if args else None)
        self.__m_registry = kwargs.pop('registry', args.pop(0) if args else None)
        self.__m_secrets = kwargs.pop('secrets', args.pop(0) if args else None)
        self.__m_breaker = kwargs.pop('breaker', args.pop(0) if args else None)
        self.__m_router = kwargs.pop('router', args.pop(0) if args else None)
        self.__m_executor = kwargs.pop('executor', args.pop(0) if args else None)

    def getSettings(self):
        """
        :return: Settings
        """
        return self.__m_settings

    def setSettings(self, settings):
        """
        :param settings: Settings
        """
        self.__m_settings = settings

    def getRegistry(self):
        """
        :return: EngineRegistry
        """
        return self.__m_registry

    def setRegistry(self, registry):
        """
        :param registry: EngineRegistry
        """
        self.__m_registry = registry

    def getSecrets(self):
        """
        :return: SecretStore
        """
        return self.__m_secrets

    def setSecrets(self, secrets):
        """
        :param secrets: SecretStore
        """
        self.__m_secrets = secrets

    def getBreaker(self):
        """
        :return: CircuitBreaker
        """
        return self.__m_breaker

    def setBreaker(self, breaker):
        """
        :param breaker: CircuitBreaker
        """
        self.__m_breaker = breaker

    def getRouter(self):
        """
        :return: Router
        """
        return self.__m_router

    def setRouter(self, router):
        """
        :param router: Router
        """
        self.__m_router = router

    def getExecutor(self):
        """
        :return: Executor
        """
        return self.__m_executor

    def setExecutor(self, executor):
        """
        :param executor: Executor
        """
        self.__m_executor = executor

    def toDict(self):
        """
        :return: dict
        """
        return {
            'settings': self.__m_settings,
            'registry': self.__m_registry,
            'secrets': self.__m_secrets,
            'breaker': self.__m_breaker,
            'router': self.__m_router,
            'executor': self.__m_executor,
        }

    def __repr__(self):
        return 'AppContext({})'.format(', '.join('{}={!r}'.format(k, v) for k, v in self.toDict().items()))

    settings = property(fget=getSettings, fset=setSettings)
    registry = property(fget=getRegistry, fset=setRegistry)
    secrets = property(fget=getSecrets, fset=setSecrets)
    breaker = property(fget=getBreaker, fset=setBreaker)
    router = property(fget=getRouter, fset=setRouter)
    executor = property(fget=getExecutor, fset=setExecutor)


_ctx = None


def buildContext(settings=None, sync=True):
    global _ctx
    settings = settings or getSettings()
    configureLogging(settings.log_level, settings.json_logs)
    initDb(settings.databaseUrl)
    registry = getRegistry()
    if sync:
        with sessionScope() as s:
            syncEngines(s, registry)
            if settings.auto_seed_providers:
                from ocrroute.sync import loadConfig

                if loadConfig(settings, s).sync_role != 'follower':  # followers get providers from the leader
                    seedProviders(s, registry)
    secrets = SecretStore(loadOrCreateKey(settings.secretFile, settings.secret_key))
    breaker = CircuitBreaker(settings.breaker_threshold, settings.breaker_cooldown_seconds)
    router = Router(registry, breaker)
    executor = Executor(
        settings,
        registry,
        router,
        breaker,
        secrets,
        pool=cf.ThreadPoolExecutor(max_workers=settings.worker_threads, thread_name_prefix='ocr'),
    )
    _ctx = AppContext(settings, registry, secrets, breaker, router, executor)
    _reconcileInterrupted()
    return _ctx


def getContext():
    if _ctx is None:
        return buildContext()
    return _ctx


def resetContext():
    global _ctx
    _ctx = None


def _reconcileInterrupted():
    """Runs left 'running' by a previous process crash are marked failed/interrupted."""
    from ocrroute.db.base import utcnow
    from ocrroute.db.models import Job, Run
    from ocrroute.errors import INTERRUPTED

    with sessionScope() as s:
        for run in s.query(Run).filter(Run.status.in_(('running', 'queued'))).all():
            run.status = 'failed'
            run.error_code = INTERRUPTED
            run.error_message = 'Server restarted while the run was in progress'
            run.finished_at = utcnow()
        for job in s.query(Job).filter(Job.status == 'running').all():
            job.status = 'failed'
            job.finished_at = utcnow()
