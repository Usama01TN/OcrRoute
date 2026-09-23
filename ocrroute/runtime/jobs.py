# coding=utf-8
"""In-process batch job runner (bounded worker threads, persisted to SQLite, resumable per item)."""
from __future__ import absolute_import, division, print_function

import concurrent.futures as cf
import threading

from ocrroute.db.base import utcnow
from ocrroute.db.models import Job, JobItem
from ocrroute.db.session import sessionScope
from ocrroute.logsetup import getLogger
from ocrroute.pipeline.input import loadInput
from ocrroute.runtime.executor import OcrRequest
from ocrroute.runtime.webhooks import deliver

log = getLogger(__name__)


class JobRunner(object):
    def __init__(self, executor, concurrency=2):
        self.__m_executor = executor
        self.__m_pool = cf.ThreadPoolExecutor(max_workers=concurrency, thread_name_prefix='job')
        self.__m_cancel = set()
        self.__m_lock = threading.Lock()
        self.__m_listeners = []
        self.__m_uploads = {}

    def create(self, name, sources, route, engine, language, output, options, webhook_url, api_key_id, uploaded=None):
        with sessionScope() as s:
            job = Job(
                name=name or 'batch {}'.format(utcnow()),
                status='queued',
                total=len(sources) + len(uploaded or []),
                options={'route': route, 'engine': engine, 'language': language, 'output': output, 'options': options},
                webhook_url=webhook_url,
                api_key_id=api_key_id,
            )
            s.add(job)
            s.flush()
            for i, src in enumerate(sources):
                s.add(JobItem(job_id=job.id, source=src, order_index=i))
            job_id = job.id
        if uploaded:
            with sessionScope() as s:
                base = len(sources)
                for i, (fname, data) in enumerate(uploaded):
                    item = JobItem(job_id=job_id, source='upload:{}'.format(fname), order_index=base + i)
                    s.add(item)
                    s.flush()
                    self.__m_uploads[item.id] = data
        self.__m_pool.submit(self._runJob, job_id)
        return job_id

    def cancel(self, job_id):
        with self.__m_lock:
            self.__m_cancel.add(job_id)

    def _emit(self, payload):
        for cb in list(self.__m_listeners):
            try:
                cb(payload)
            except Exception:  # noqa: BLE001
                pass

    def _runJob(self, job_id):
        with sessionScope() as s:
            job = s.get(Job, job_id)
            if job is None:
                return
            job.status = 'running'
            opts = dict(job.options)
            items = [(it.id, it.source) for it in job.items if it.status in ('queued', 'failed')]
            key_id = job.api_key_id
        for item_id, source in items:
            with self.__m_lock:
                if job_id in self.__m_cancel:
                    break
            self._runItem(job_id, item_id, source, opts, key_id)
        with sessionScope() as s:
            job = s.get(Job, job_id)
            assert job is not None
            cancelled = job_id in self.__m_cancel
            job.status = 'cancelled' if cancelled else ('failed' if job.failed and job.done == 0 else 'succeeded')
            job.finished_at = utcnow()
            payload = {
                'job_id': job.id,
                'status': job.status,
                'total': job.total,
                'done': job.done,
                'failed': job.failed,
            }
            hook = job.webhook_url
        self._emit(payload)
        if hook:
            deliver(hook, {'event': 'job.finished', **payload})

    def _runItem(self, job_id, item_id, source, opts, key_id):
        settings = self.__m_executor.settings
        try:
            if source.startswith('upload:'):
                data = self.__m_uploads.pop(item_id, None)
                if data is None:
                    raise RuntimeError('upload payload lost (server restarted)')
                doc = loadInput(settings=settings, file_bytes=data, filename=source[7:])
            elif source.startswith(('http://', 'https://')):
                doc = loadInput(settings=settings, url=source)
            else:
                doc = loadInput(settings=settings, path=source)
            req = OcrRequest(
                doc=doc,
                route=opts.get('route', ''),
                engine=opts.get('engine', ''),
                language=list(opts.get('language') or ['en']),
                output=list(opts.get('output') or ['json']),
                options=dict(opts.get('options') or {}),
                origin='batch',
                metadata={'job_id': job_id},
            )
            outcome = self.__m_executor.execute(req)
            ok = outcome.status in ('succeeded', 'cached')
            err = '' if ok else '{}: {}'.format(outcome.error_code, outcome.error_message)
            run_id = outcome.run_id
        except Exception as exc:  # noqa: BLE001
            ok, err, run_id = False, '{}: {}'.format(type(exc).__name__, exc)[:500], ''
        with sessionScope() as s:
            item = s.get(JobItem, item_id)
            job = s.get(Job, job_id)
            if item is not None:
                item.status = 'succeeded' if ok else 'failed'
                item.error_message = err
                item.run_id = run_id
            if job is not None:
                if ok:
                    job.done += 1
                else:
                    job.failed += 1
                payload = {
                    'job_id': job.id,
                    'status': job.status,
                    'total': job.total,
                    'done': job.done,
                    'failed': job.failed,
                    'item': item_id,
                }
        self._emit(payload)

    def getExecutor(self):
        """
        :return: any
        """
        return self.__m_executor

    def setExecutor(self, executor):
        """
        :param executor: any
        """
        self.__m_executor = executor

    def getPool(self):
        """
        :return: any
        """
        return self.__m_pool

    def setPool(self, pool):
        """
        :param pool: any
        """
        self.__m_pool = pool

    def getListeners(self):
        """
        :return: any
        """
        return self.__m_listeners

    def setListeners(self, listeners):
        """
        :param listeners: any
        """
        self.__m_listeners = listeners

    executor = property(fget=getExecutor, fset=setExecutor)
    pool = property(fget=getPool, fset=setPool)
    listeners = property(fget=getListeners, fset=setListeners)
