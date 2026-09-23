# coding=utf-8
"""Executes a Run: input → cache → resolve → attempts (with rotation, breaker, deadline) → persist → artifacts."""
from __future__ import absolute_import, division, print_function

import concurrent.futures as cf
from ocrroute.compat.py23 import raiseFrom
import hashlib
import threading
import time
from pathlib import Path


from ocrroute.db.base import utcnow
from ocrroute.db.models import Artifact, Attempt, Credential, Provider, Run
from ocrroute.db.session import sessionScope
from ocrroute.enginelib import OCRPlugin
from ocrroute.errors import (
    AUTH,
    DEADLINE,
    EMPTY_RESULT,
    ENGINE_MISSING,
    QUOTA,
    RATE_LIMIT,
    TERMINAL,
    TIMEOUT,
    OcrRouteError,
    classify,
)
from ocrroute.logsetup import getLogger, redact
from ocrroute.pipeline import export, postprocess, preprocess
from ocrroute.routing.candidates import Candidate, RequestContext
from ocrroute.routing.consensus import reconcile
from ocrroute.routing.cost import estimateCents
from ocrroute.routing.explain import RoutingTrace
from ocrroute.runtime import cache as cache_mod
from ocrroute.runtime.limits import IN_FLIGHT, WINDOW

log = getLogger(__name__)


class OcrRequest(object):
    """
    Everything the executor needs for one run.
    """

    def __init__(self, *args, **kwargs):
        """
        :param doc: InputDocument
        :param engine: str
        :param provider_id: str
        :param route: str
        :param language: list[str]
        :param prompt: str
        :param options: dict - engine kwargs
        :param preprocess: dict
        :param output: list[str] - artifact kinds
        :param stop_condition: dict
        :param strategy: str - strategy override
        :param cache: bool
        :param hints: dict - handwriting, tables, sensitive, offline, max_cost_cents
        :param metadata: dict
        :param api_key: ApiKey | None
        :param client_ip: str
        :param user_agent: str
        :param idempotency_key: str
        :param origin: str - api | cli | batch | panel_test | desktop
        :param page_indices: list[int]
        """
        args = list(args)
        self.__m_doc = kwargs.pop('doc', args.pop(0) if args else None)
        self.__m_engine = kwargs.pop('engine', args.pop(0) if args else '')
        self.__m_providerId = kwargs.pop('provider_id', args.pop(0) if args else '')
        self.__m_route = kwargs.pop('route', args.pop(0) if args else '')
        self.__m_language = kwargs.pop('language', args.pop(0) if args else ['en'])
        self.__m_prompt = kwargs.pop('prompt', args.pop(0) if args else '')
        self.__m_options = kwargs.pop('options', args.pop(0) if args else dict())
        self.__m_preprocess = kwargs.pop('preprocess', args.pop(0) if args else dict())
        self.__m_output = kwargs.pop('output', args.pop(0) if args else ['json'])
        self.__m_stopCondition = kwargs.pop('stop_condition', args.pop(0) if args else dict())
        self.__m_strategy = kwargs.pop('strategy', args.pop(0) if args else '')
        self.__m_cache = kwargs.pop('cache', args.pop(0) if args else True)
        self.__m_hints = kwargs.pop('hints', args.pop(0) if args else dict())
        self.__m_metadata = kwargs.pop('metadata', args.pop(0) if args else dict())
        self.__m_apiKey = kwargs.pop('api_key', args.pop(0) if args else None)
        self.__m_clientIp = kwargs.pop('client_ip', args.pop(0) if args else '')
        self.__m_userAgent = kwargs.pop('user_agent', args.pop(0) if args else '')
        self.__m_idempotencyKey = kwargs.pop('idempotency_key', args.pop(0) if args else '')
        self.__m_origin = kwargs.pop('origin', args.pop(0) if args else 'api')
        self.__m_pageIndices = kwargs.pop('page_indices', args.pop(0) if args else list())

    def getDoc(self):
        """
        :return: InputDocument
        """
        return self.__m_doc

    def setDoc(self, doc):
        """
        :param doc: InputDocument
        """
        self.__m_doc = doc

    def getEngine(self):
        """
        :return: str
        """
        return self.__m_engine

    def setEngine(self, engine):
        """
        :param engine: str
        """
        self.__m_engine = engine

    def getProviderId(self):
        """
        :return: str
        """
        return self.__m_providerId

    def setProviderId(self, providerId):
        """
        :param providerId: str
        """
        self.__m_providerId = providerId

    def getRoute(self):
        """
        :return: str
        """
        return self.__m_route

    def setRoute(self, route):
        """
        :param route: str
        """
        self.__m_route = route

    def getLanguage(self):
        """
        :return: list[str]
        """
        return self.__m_language

    def setLanguage(self, language):
        """
        :param language: list[str]
        """
        self.__m_language = language

    def getPrompt(self):
        """
        :return: str
        """
        return self.__m_prompt

    def setPrompt(self, prompt):
        """
        :param prompt: str
        """
        self.__m_prompt = prompt

    def getOptions(self):
        """
        :return: dict
        """
        return self.__m_options

    def setOptions(self, options):
        """
        :param options: dict
        """
        self.__m_options = options

    def getPreprocess(self):
        """
        :return: dict
        """
        return self.__m_preprocess

    def setPreprocess(self, preprocess):
        """
        :param preprocess: dict
        """
        self.__m_preprocess = preprocess

    def getOutput(self):
        """
        :return: list[str]
        """
        return self.__m_output

    def setOutput(self, output):
        """
        :param output: list[str]
        """
        self.__m_output = output

    def getStopCondition(self):
        """
        :return: dict
        """
        return self.__m_stopCondition

    def setStopCondition(self, stopCondition):
        """
        :param stopCondition: dict
        """
        self.__m_stopCondition = stopCondition

    def getStrategy(self):
        """
        :return: str
        """
        return self.__m_strategy

    def setStrategy(self, strategy):
        """
        :param strategy: str
        """
        self.__m_strategy = strategy

    def getCache(self):
        """
        :return: bool
        """
        return self.__m_cache

    def setCache(self, cache):
        """
        :param cache: bool
        """
        self.__m_cache = cache

    def getHints(self):
        """
        :return: dict
        """
        return self.__m_hints

    def setHints(self, hints):
        """
        :param hints: dict
        """
        self.__m_hints = hints

    def getMetadata(self):
        """
        :return: dict
        """
        return self.__m_metadata

    def setMetadata(self, metadata):
        """
        :param metadata: dict
        """
        self.__m_metadata = metadata

    def getApiKey(self):
        """
        :return: ApiKey | None
        """
        return self.__m_apiKey

    def setApiKey(self, apiKey):
        """
        :param apiKey: ApiKey | None
        """
        self.__m_apiKey = apiKey

    def getClientIp(self):
        """
        :return: str
        """
        return self.__m_clientIp

    def setClientIp(self, clientIp):
        """
        :param clientIp: str
        """
        self.__m_clientIp = clientIp

    def getUserAgent(self):
        """
        :return: str
        """
        return self.__m_userAgent

    def setUserAgent(self, userAgent):
        """
        :param userAgent: str
        """
        self.__m_userAgent = userAgent

    def getIdempotencyKey(self):
        """
        :return: str
        """
        return self.__m_idempotencyKey

    def setIdempotencyKey(self, idempotencyKey):
        """
        :param idempotencyKey: str
        """
        self.__m_idempotencyKey = idempotencyKey

    def getOrigin(self):
        """
        :return: str
        """
        return self.__m_origin

    def setOrigin(self, origin):
        """
        :param origin: str
        """
        self.__m_origin = origin

    def getPageIndices(self):
        """
        :return: list[int]
        """
        return self.__m_pageIndices

    def setPageIndices(self, pageIndices):
        """
        :param pageIndices: list[int]
        """
        self.__m_pageIndices = pageIndices

    def toDict(self):
        """
        :return: dict
        """
        return {
            'doc': self.__m_doc,
            'engine': self.__m_engine,
            'provider_id': self.__m_providerId,
            'route': self.__m_route,
            'language': self.__m_language,
            'prompt': self.__m_prompt,
            'options': self.__m_options,
            'preprocess': self.__m_preprocess,
            'output': self.__m_output,
            'stop_condition': self.__m_stopCondition,
            'strategy': self.__m_strategy,
            'cache': self.__m_cache,
            'hints': self.__m_hints,
            'metadata': self.__m_metadata,
            'api_key': self.__m_apiKey,
            'client_ip': self.__m_clientIp,
            'user_agent': self.__m_userAgent,
            'idempotency_key': self.__m_idempotencyKey,
            'origin': self.__m_origin,
            'page_indices': self.__m_pageIndices,
        }

    def __repr__(self):
        return 'OcrRequest({})'.format(', '.join('{}={!r}'.format(k, v) for k, v in self.toDict().items()))

    doc = property(fget=getDoc, fset=setDoc)
    engine = property(fget=getEngine, fset=setEngine)
    provider_id = property(fget=getProviderId, fset=setProviderId)
    route = property(fget=getRoute, fset=setRoute)
    language = property(fget=getLanguage, fset=setLanguage)
    prompt = property(fget=getPrompt, fset=setPrompt)
    options = property(fget=getOptions, fset=setOptions)
    preprocess = property(fget=getPreprocess, fset=setPreprocess)
    output = property(fget=getOutput, fset=setOutput)
    stop_condition = property(fget=getStopCondition, fset=setStopCondition)
    strategy = property(fget=getStrategy, fset=setStrategy)
    cache = property(fget=getCache, fset=setCache)
    hints = property(fget=getHints, fset=setHints)
    metadata = property(fget=getMetadata, fset=setMetadata)
    api_key = property(fget=getApiKey, fset=setApiKey)
    client_ip = property(fget=getClientIp, fset=setClientIp)
    user_agent = property(fget=getUserAgent, fset=setUserAgent)
    idempotency_key = property(fget=getIdempotencyKey, fset=setIdempotencyKey)
    origin = property(fget=getOrigin, fset=setOrigin)
    page_indices = property(fget=getPageIndices, fset=setPageIndices)


class RunOutcome(object):
    """
    The response envelope of one run.
    """

    def __init__(self, *args, **kwargs):
        """
        :param run_id: str
        :param status: str
        :param result: dict - unified OCR result, verbatim
        :param routing: dict
        :param usage: dict
        :param artifacts: list[dict]
        :param cached: bool
        :param error_code: str
        :param error_message: str
        :param metadata: dict
        :param http_status: int
        """
        args = list(args)
        self.__m_runId = kwargs.pop('run_id', args.pop(0) if args else '')
        self.__m_status = kwargs.pop('status', args.pop(0) if args else '')
        self.__m_result = kwargs.pop('result', args.pop(0) if args else dict())
        self.__m_routing = kwargs.pop('routing', args.pop(0) if args else dict())
        self.__m_usage = kwargs.pop('usage', args.pop(0) if args else dict())
        self.__m_artifacts = kwargs.pop('artifacts', args.pop(0) if args else list())
        self.__m_cached = kwargs.pop('cached', args.pop(0) if args else False)
        self.__m_errorCode = kwargs.pop('error_code', args.pop(0) if args else '')
        self.__m_errorMessage = kwargs.pop('error_message', args.pop(0) if args else '')
        self.__m_metadata = kwargs.pop('metadata', args.pop(0) if args else dict())
        self.__m_httpStatus = kwargs.pop('http_status', args.pop(0) if args else 200)

    def getRunId(self):
        """
        :return: str
        """
        return self.__m_runId

    def setRunId(self, runId):
        """
        :param runId: str
        """
        self.__m_runId = runId

    def getStatus(self):
        """
        :return: str
        """
        return self.__m_status

    def setStatus(self, status):
        """
        :param status: str
        """
        self.__m_status = status

    def getResult(self):
        """
        :return: dict
        """
        return self.__m_result

    def setResult(self, result):
        """
        :param result: dict
        """
        self.__m_result = result

    def getRouting(self):
        """
        :return: dict
        """
        return self.__m_routing

    def setRouting(self, routing):
        """
        :param routing: dict
        """
        self.__m_routing = routing

    def getUsage(self):
        """
        :return: dict
        """
        return self.__m_usage

    def setUsage(self, usage):
        """
        :param usage: dict
        """
        self.__m_usage = usage

    def getArtifacts(self):
        """
        :return: list[dict]
        """
        return self.__m_artifacts

    def setArtifacts(self, artifacts):
        """
        :param artifacts: list[dict]
        """
        self.__m_artifacts = artifacts

    def getCached(self):
        """
        :return: bool
        """
        return self.__m_cached

    def setCached(self, cached):
        """
        :param cached: bool
        """
        self.__m_cached = cached

    def getErrorCode(self):
        """
        :return: str
        """
        return self.__m_errorCode

    def setErrorCode(self, errorCode):
        """
        :param errorCode: str
        """
        self.__m_errorCode = errorCode

    def getErrorMessage(self):
        """
        :return: str
        """
        return self.__m_errorMessage

    def setErrorMessage(self, errorMessage):
        """
        :param errorMessage: str
        """
        self.__m_errorMessage = errorMessage

    def getMetadata(self):
        """
        :return: dict
        """
        return self.__m_metadata

    def setMetadata(self, metadata):
        """
        :param metadata: dict
        """
        self.__m_metadata = metadata

    def getHttpStatus(self):
        """
        :return: int
        """
        return self.__m_httpStatus

    def setHttpStatus(self, httpStatus):
        """
        :param httpStatus: int
        """
        self.__m_httpStatus = httpStatus

    def __repr__(self):
        return 'RunOutcome({})'.format(', '.join('{}={!r}'.format(k, v) for k, v in self.toDict().items()))

    def toDict(self):
        """
        :return: dict - the JSON envelope
        """
        d = {
            'run_id': self.__m_runId,
            'status': self.__m_status,
            'cached': self.__m_cached,
            'result': self.__m_result,
            'routing': self.__m_routing,
            'usage': self.__m_usage,
            'artifacts': self.__m_artifacts,
            'metadata': self.__m_metadata,
        }
        if self.__m_errorCode:
            d['error_code'] = self.__m_errorCode
            d['error_message'] = self.__m_errorMessage
        return d

    run_id = property(fget=getRunId, fset=setRunId)
    status = property(fget=getStatus, fset=setStatus)
    result = property(fget=getResult, fset=setResult)
    routing = property(fget=getRouting, fset=setRouting)
    usage = property(fget=getUsage, fset=setUsage)
    artifacts = property(fget=getArtifacts, fset=setArtifacts)
    cached = property(fget=getCached, fset=setCached)
    error_code = property(fget=getErrorCode, fset=setErrorCode)
    error_message = property(fget=getErrorMessage, fset=setErrorMessage)
    metadata = property(fget=getMetadata, fset=setMetadata)
    http_status = property(fget=getHttpStatus, fset=setHttpStatus)


class Executor(object):
    def __init__(self, settings, registry, router, breaker, secrets, pool=None):
        self.__m_settings = settings
        self.__m_registry = registry
        self.__m_router = router
        self.__m_breaker = breaker
        self.__m_secrets = secrets
        self.__m_pool = pool or cf.ThreadPoolExecutor(max_workers=settings.worker_threads, thread_name_prefix='ocr')
        self.__m_singleFlight = {}
        self.__m_sfLock = threading.Lock()
        self.__m_events = []  # SSE subscribers (callables)

    # ------------------------------------------------------------------ public
    def execute(self, req):
        started = time.time()
        doc = req.doc
        ctx = RequestContext(
            mime=doc.mime,
            sha256=doc.sha256,
            width=doc.width,
            height=doc.height,
            page_count=doc.page_count,
            language=list(req.language) or ['en'],
            handwriting=bool(req.hints.get('handwriting')),
            tables=bool(req.hints.get('tables') or req.options.get('isTable')),
            sensitive=bool(req.hints.get('sensitive')) or self.__m_settings.privacy_mode,
            offline=bool(req.hints.get('offline')),
            max_cost_cents=req.hints.get('max_cost_cents'),
        )
        target = req.engine or req.provider_id or req.route or 'default'
        opts_for_key = {
            'options': req.options,
            'language': req.language,
            'prompt': req.prompt,
            'preprocess': req.preprocess,
            'pages': req.page_indices,
        }
        ckey = cache_mod.cache_key(doc.sha256, target, opts_for_key)

        # idempotency
        if req.idempotency_key:
            with sessionScope() as s:
                prev = (
                    s.query(Run)
                    .filter(Run.idempotency_key == req.idempotency_key)
                    .order_by(Run.created_at.desc())
                    .first()
                )
                if prev is not None and prev.result_json is not None:
                    return self._outcomeFromRun(prev, cached=True)

        # cache
        if req.cache and not self.__m_settings.privacy_mode:
            with sessionScope() as s:
                hit = cache_mod.get(s, ckey)
                if hit is not None:
                    run = self._persistCached(s, req, doc, hit.result_json, hit.routing_json, started)
                    return self._outcomeFromRun(run, cached=True)

        # single-flight for identical in-flight requests
        waiter = None
        with self.__m_sfLock:
            if ckey in self.__m_singleFlight:
                waiter = self.__m_singleFlight[ckey]
            else:
                self.__m_singleFlight[ckey] = threading.Event()
        if waiter is not None:
            waiter.wait(timeout=self.__m_settings.total_deadline_ms / 1000)
            with sessionScope() as s:
                hit = cache_mod.get(s, ckey)
                if hit is not None:
                    run = self._persistCached(s, req, doc, hit.result_json, hit.routing_json, started)
                    return self._outcomeFromRun(run, cached=True)
            # fall through and run ourselves
            with self.__m_sfLock:
                self.__m_singleFlight.setdefault(ckey, threading.Event())
        try:
            return self._executeUncached(req, ctx, ckey, started)
        finally:
            with self.__m_sfLock:
                ev = self.__m_singleFlight.pop(ckey, None)
            if ev is not None:
                ev.set()

    # ------------------------------------------------------------------ core
    def _executeUncached(self, req, ctx, ckey, started):
        doc = req.doc
        trace = RoutingTrace()
        run_id = ''
        with sessionScope() as s:
            run = Run(
                api_key_id=req.api_key.id if req.api_key else None,
                requested_engine=req.engine,
                input_kind=doc.kind,
                mime=doc.mime,
                bytes=len(doc.data),
                image_sha256=doc.sha256,
                page_count=doc.page_count,
                language=','.join(req.language),
                status='running',
                client_ip=req.client_ip,
                user_agent=req.user_agent[:255],
                idempotency_key=req.idempotency_key,
                metadata_json=req.metadata,
                origin=req.origin,
            )
            s.add(run)
            s.flush()
            run_id = run.id
            try:
                spec = self.__m_router.resolve(
                    s,
                    ctx,
                    engine=req.engine,
                    provider_id=req.provider_id,
                    route_name=req.route,
                    api_key=req.api_key,
                    strategy_override=req.strategy,
                    stop_override=req.stop_condition,
                )
            except OcrRouteError as exc:
                run.status = 'failed'
                run.error_code = exc.code
                run.error_message = exc.message
                run.finished_at = utcnow()
                run.duration_ms = int((time.time() - started) * 1000)
                result = OCRPlugin().errorResult(exc)
                result['ErrorMessage'] = exc.message
                run.result_json = result
                trace.explain.append(exc.message)
                run.routing_json = trace.toDict()
                self._emit(run)
                return RunOutcome(
                    run_id,
                    'failed',
                    result,
                    trace.toDict(),
                    self._usage(result, run),
                    [],
                    error_code=exc.code,
                    error_message=exc.message,
                    metadata=req.metadata,
                    http_status=exc.status,
                )
            run.route_id = spec.route_id
            run.route_name = spec.name
            run.strategy = spec.strategy
        trace.route, trace.strategy, trace.explain = spec.name, spec.strategy, list(spec.explain)

        deadline = started + spec.total_deadline_ms / 1000.0
        pre_bytes_pages, transform = self._preparePages(doc, req)
        best = None
        final = None
        winner = None
        error_code, error_message = '', ''

        if spec.parallel > 1:
            final, winner, error_code, error_message = self._runParallel(
                run_id, spec, req, pre_bytes_pages, transform, deadline, trace
            )
        else:
            for order, cand in enumerate(spec.candidates):
                if time.time() >= deadline:
                    error_code, error_message = DEADLINE, 'Route deadline exceeded before all candidates were tried'
                    trace.explain.append(error_message)
                    break
                result, code, msg = self._attempt(run_id, order, cand, req, pre_bytes_pages, transform, deadline, trace)
                if result is not None and result.get('FileParseExitCode') != -1:
                    if self._stopOk(result, spec.stop_condition):
                        final, winner = result, cand
                        break
                    trace.explain.append('{}: result below stop condition, kept as fallback'.format(cand.engine_id))
                    if best is None or len(result.get('ParsedText', '')) > len(best[0].get('ParsedText', '')):
                        best = (result, cand)
                    continue
                error_code, error_message = code, msg
                if code in TERMINAL:
                    trace.explain.append('terminal input error → not trying other engines')
                    break
        degraded = False
        if final is None and best is not None:
            final, winner = best
            degraded = True
            trace.explain.append('all candidates below stop condition → returning best partial result')
        if final is None:
            final = OCRPlugin.emptyResult()
            final['FileParseExitCode'] = -1
            final['ErrorMessage'] = error_message or 'All engines failed'
            final['ErrorDetails'] = error_code or EMPTY_RESULT
        else:
            final = postprocess.normaliseWhitespace(final)
            final = postprocess.applyRtlOrder(final)
            final = postprocess.applyTools(final, spec.tool_chain)
        trace.degraded = degraded
        if winner is not None:
            trace.winning_engine, trace.winning_provider = winner.engine_id, winner.provider_label
            trace.options_applied = dict(winner.provider_options, **dict(winner.option_overrides, **req.options))
        status = 'succeeded' if final.get('FileParseExitCode') != -1 else 'failed'
        duration = int((time.time() - started) * 1000)

        artifacts = []
        with sessionScope() as s:
            run = s.get(Run, run_id)
            assert run is not None
            run.status = status
            run.finished_at = utcnow()
            run.duration_ms = duration
            run.attempt_count = len(trace.attempts)
            run.degraded = degraded
            run.winning_engine = trace.winning_engine
            run.winning_provider = trace.winning_provider
            st = postprocess.stats(final)
            run.chars, run.lines, run.words, run.mean_confidence = (
                st['chars'],
                st['lines'],
                st['words'],
                st['mean_confidence'],
            )
            run.cost_cents = sum(a.get('cost_cents', 0.0) for a in trace.attempts)
            if status == 'failed':
                run.error_code = error_code or EMPTY_RESULT
                run.error_message = redact(error_message)[:2000]
            run.result_json = final
            run.routing_json = trace.toDict()
            if status == 'succeeded':
                artifacts = self._writeArtifacts(s, run, final, req, doc, pre_bytes_pages)
                if req.cache and not self.__m_settings.privacy_mode and spec.cache_ttl_seconds > 0:
                    cache_mod.put(s, ckey, run_id, final, trace.toDict(), spec.cache_ttl_seconds)
            self._emit(run)
        outcome = RunOutcome(
            run_id,
            status,
            final,
            trace.toDict(),
            self._usage(final, run),
            artifacts,
            error_code=(error_code or EMPTY_RESULT) if status == 'failed' else '',
            error_message=error_message if status == 'failed' else '',
            metadata=req.metadata,
        )
        if status == 'failed':
            from ocrroute.errors import HTTP_STATUS

            outcome.http_status = HTTP_STATUS.get(outcome.error_code, 502)
        return outcome

    # ------------------------------------------------------------------ attempts
    def _preparePages(self, doc, req):
        pages = doc.pages if doc.pages else [doc.data]
        spec = preprocess.PreprocessSpec.fromDict(req.preprocess)
        if not spec.active:
            return pages, preprocess.Transform()
        out = []
        transform = preprocess.Transform()
        for p in pages:
            b, transform = preprocess.apply(p, spec)
            out.append(b)
        return out, transform

    def _engineKwargs(self, cand, req, secret, secrets):
        kw = {}
        kw.update(cand.provider_options)
        kw.update(cand.option_overrides)
        kw.update(req.options)
        lang = req.language if req.language else ([cand.language] if cand.language else ['en'])
        kw.setdefault('language', lang if len(lang) > 1 else lang[0])
        if cand.language and not req.language:
            kw['language'] = cand.language
        if cand.endpoint:
            kw['endpoint'] = cand.endpoint
        if cand.model:
            kw['model'] = cand.model
        prompt = req.prompt or self._globalPrompt()
        if prompt:
            kw['prompt'] = prompt
        kw['timeout'] = cand.timeout or self.__m_settings.default_timeout
        kw['retries'] = max(1, cand.retries)
        if cand.proxy:
            kw['proxy'] = cand.proxy
        if secret:
            kw['api'] = secret
        if secrets:
            kw['apiList'] = secrets
        return kw

    def _loadCredentials(self, cand):
        if not cand.credential_ids:
            return []
        now = utcnow()
        with sessionScope() as s:
            rows = (
                s.query(Credential)
                .filter(Credential.id.in_(cand.credential_ids))
                .order_by(Credential.order_index)
                .all()
            )
            out = []
            for c in rows:
                if c.enabled and (not c.exhausted_until or c.exhausted_until <= now):
                    try:
                        out.append((c.id, self.__m_secrets.decrypt(c.secret_enc)))
                    except ValueError as exc:
                        log.warning('credential undecryptable', credential=c.id, error=str(exc))
            return out

    def _attempt(
        self,
        run_id,
        order,
        cand,
        req,
        pages,
        transform,
        deadline,
        trace,
    ):
        key = 'provider:{}'.format(cand.provider_id) if cand.provider_id else 'engine:{}'.format(cand.engine_id)
        creds = self._loadCredentials(cand)
        cred_cycle = creds or [('', '')]
        last_code, last_msg = '', ''
        for ci, (cred_id, secret) in enumerate(cred_cycle):
            t0 = time.time()
            att = {
                'order': order,
                'engine': cand.engine_id,
                'provider': cand.provider_label,
                'credential': cred_id[-4:] if cred_id else '',
            }
            if not IN_FLIGHT.acquire(key, 0):
                pass
            try:
                remaining = deadline - time.time()
                if remaining <= 0:
                    raise TimeoutError('deadline')
                result = self._runEngine(cand, req, pages, transform, secret, [s for _, s in creds], remaining)
                code = '' if result.get('FileParseExitCode') != -1 else classify(result.get('ErrorMessage', '')).code
                if not code and not result.get('ParsedText', '').strip() and not result['TextOverlay']['Lines']:
                    code = EMPTY_RESULT
                    result['FileParseExitCode'] = -1
                    result['ErrorMessage'] = 'Engine returned no text'
                if code:
                    raise _EngineFailed(code, str(result.get('ErrorMessage', ''))[:800])
            except _EngineFailed as exc:
                last_code, last_msg = exc.code, exc.msg
                status = 'failed'
                if exc.code == ENGINE_MISSING:
                    self._demoteEngine(cand.engine_id, exc.msg)
            except TimeoutError:
                last_code, last_msg, status = TIMEOUT, '{} exceeded its time budget'.format(cand.engine_id), 'timeout'
            except ImportError as exc:
                last_code, last_msg, status = ENGINE_MISSING, str(exc)[:800], 'failed'
                self._demoteEngine(cand.engine_id, last_msg)
            except Exception as exc:  # noqa: BLE001
                cl = classify('{}: {}'.format(type(exc).__name__, exc))
                last_code, last_msg, status = cl.code, '{}: {}'.format(type(exc).__name__, exc)[:800], 'failed'
                if cl.code == ENGINE_MISSING:
                    self._demoteEngine(cand.engine_id, last_msg)
            else:
                status = 'succeeded'
            finally:
                IN_FLIGHT.release(key)
            dur = int((time.time() - t0) * 1000)
            att.update({'status': status, 'duration_ms': dur})
            WINDOW.hit(key)
            with sessionScope() as s:
                eng_row = None
                from ocrroute.db.models import Engine as EngineRow

                eng_row = s.get(EngineRow, cand.engine_id)
                cost = 0.0
                if status == 'succeeded' and eng_row is not None:
                    cost = estimateCents(
                        eng_row.cost_model,
                        eng_row.unit_price,
                        pages=len(pages),
                        chars=len(result.get('ParsedText', '')),
                    )
                a = Attempt(
                    run_id=run_id,
                    provider_id=cand.provider_id,
                    provider_label=cand.provider_label,
                    engine_id=cand.engine_id,
                    credential_id=cred_id,
                    order_index=order,
                    status=status,
                    duration_ms=dur,
                    error_code=last_code if status != 'succeeded' else '',
                    error_message=redact(last_msg) if status != 'succeeded' else '',
                    chars=len(result.get('ParsedText', '')) if status == 'succeeded' else 0,
                    cost_cents=cost,
                    finished_at=utcnow(),
                )
                s.add(a)
                att['cost_cents'] = cost
                if status != 'succeeded':
                    att['error_code'] = last_code
                    att['error_message'] = redact(last_msg)[:300]
                self._updateProviderHealth(s, cand, status == 'succeeded', key)
                if cred_id:
                    c = s.get(Credential, cred_id)
                    if c is not None:
                        c.last_used_at = utcnow()
                        if status == 'succeeded':
                            c.success_count += 1
                        else:
                            c.failure_count += 1
                            if last_code in (AUTH, QUOTA, RATE_LIMIT):
                                from datetime import datetime, timedelta, timezone

                                c.exhausted_until = (
                                    (
                                        datetime.now(timezone.utc)
                                        + timedelta(minutes=60 if last_code != RATE_LIMIT else 2)
                                    )
                                    .replace(microsecond=0)
                                    .isoformat()
                                )
            trace.attempts.append(att)
            if status == 'succeeded':
                self.__m_breaker.recordSuccess(key)
                return result, '', ''
            if last_code in TERMINAL:
                return None, last_code, last_msg
            if last_code in (AUTH, QUOTA, RATE_LIMIT) and ci < len(cred_cycle) - 1:
                trace.explain.append(
                    '{}: credential …{} {} → rotating key'.format(cand.engine_id, cred_id[-4:], last_code)
                )
                continue
            break
        if self.__m_breaker.recordFailure(key):
            trace.explain.append(
                '{}/{}: circuit opened after repeated failures'.format(cand.engine_id, cand.provider_label)
            )
        return None, last_code, last_msg

    def _runEngine(self, cand, req, pages, transform, secret, secrets, budget_s):
        kwargs = self._engineKwargs(cand, req, secret, secrets)
        results = []
        per_page = max(1.0, budget_s / max(1, len(pages)))
        for page in pages:
            fut = self.__m_pool.submit(self._invoke, cand.engine_id, page, kwargs)
            try:
                res = fut.result(timeout=per_page + 1.0)
            except cf.TimeoutError as exc:
                fut.cancel()
                raiseFrom(TimeoutError('engine timeout'), exc)
            res = OCRPlugin.normalizeResult(res) if res.get('FileParseExitCode') != -1 else res
            if res.get('FileParseExitCode') == -1:
                return res
            results.append(transform.restore(res))
        indices = req.page_indices or list(range(len(pages)))
        return postprocess.stitchPages(results, indices)

    def _invoke(self, engine_id, image, kwargs):
        obj = self.__m_registry.instantiate(engine_id, image=image, **kwargs)
        res = obj.parse()
        if not isinstance(res, dict):
            raise RuntimeError('{} returned {}, expected dict'.format(engine_id, type(res).__name__))
        if res.get('FileParseExitCode') == -1 and not res.get('ErrorMessage'):
            res['ErrorMessage'] = obj.getLastError() or 'unknown engine error'
        return res

    def _runParallel(
        self,
        run_id,
        spec,
        req,
        pages,
        transform,
        deadline,
        trace,
    ):
        k = min(spec.parallel, len(spec.candidates))
        chosen = spec.candidates[:k]
        trace.explain.append('ensemble: running {} engines in parallel'.format(k))
        futures = {
            self.__m_pool.submit(self._attempt, run_id, i, c, req, pages, transform, deadline, trace): c
            for i, c in enumerate(chosen)
        }
        good = []
        code = msg = ''
        for fut in cf.as_completed(futures, timeout=max(1.0, deadline - time.time())):
            c = futures[fut]
            result, code_i, msg_i = fut.result()
            if result is not None:
                good.append((c.engine_id, c.quality_score, result))
            else:
                code, msg = code_i, msg_i
        if not good:  # every parallel member failed: fall back sequentially through the remaining candidates
            trace.explain.append('ensemble: all {} parallel members failed, falling back sequentially'.format(k))
            for j, cand in enumerate(spec.candidates[k:k + 8], start=k):
                if time.time() >= deadline:
                    break
                result, code_j, msg_j = self._attempt(run_id, j, cand, req, pages, transform, deadline, trace)
                if result is not None:
                    return result, cand, '', ''
                code, msg = code_j or code, msg_j or msg
                if code in TERMINAL:
                    break
            return None, None, code or EMPTY_RESULT, msg or 'all ensemble members failed'
        if getattr(spec, 'pick', '') == 'best':
            from ocrroute.pipeline import postprocess as _pp

            best = max(good, key=lambda t: (_pp.stats(t[2])['mean_confidence'], len(t[2].get('ParsedText', ''))))
            final, votes = best[2], {'engines': [e for e, _, _ in good], 'picked': best[0], 'mode': 'best_of_two'}
        else:
            final, votes = reconcile(good)
        trace.votes = votes
        winner = max(chosen, key=lambda c: c.quality_score)
        winner = Candidate(
            **dict(winner.toDict(), engine_id='+'.join(e for e, _, _ in good), provider_label='ensemble')
        )
        return final, winner, '', ''

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _stopOk(result, cond):
        if not cond:
            return True
        text = result.get('ParsedText', '')
        if cond.get('min_chars') and len(text.strip()) < int(cond['min_chars']):
            return False
        if cond.get('require_overlay') and not result.get('TextOverlay', {}).get('Lines'):
            return False
        if cond.get('min_mean_confidence'):
            st = postprocess.stats(result)
            if st['mean_confidence'] and st['mean_confidence'] < float(cond['min_mean_confidence']):
                return False
        return True

    def _globalPrompt(self):
        """
        :return: str  the operator's global OCR prompt (Endpoints > Custom OCR prompt) when enabled, else ''
        """
        try:
            from ocrroute.db.models import Setting

            with sessionScope() as s:
                enabled = s.get(Setting, 'custom_prompt_enabled')
                text = s.get(Setting, 'custom_prompt')
                if enabled is not None and enabled.value_json and text is not None:
                    return str(text.value_json or '')
        except Exception:  # noqa: BLE001
            pass
        return ''

    def _demoteEngine(self, engine_id, error):
        """An engine whose module imported but whose runtime dependency is missing is marked unavailable."""
        info = self.__m_registry.get(engine_id)
        if info is None or not info.available:
            return
        from ocrroute.catalog.registry import _install_hint
        from ocrroute.db.models import Engine as EngineRow

        info.available = False
        info.import_error = redact(error)[:500]
        info.install_hint = _install_hint(error, info.kind)
        with sessionScope() as s:
            row = s.get(EngineRow, engine_id)
            if row is not None:
                row.available = False
                row.import_error = info.import_error
                row.install_hint = info.install_hint
        log.warning('engine demoted', engine=engine_id, hint=info.install_hint)

    def _updateProviderHealth(self, s, cand, ok, key):
        if not cand.provider_id:
            return
        p = s.get(Provider, cand.provider_id)
        if p is None:
            return
        p.health_checked_at = utcnow()
        if ok:
            p.consecutive_failures = 0
            p.health = 'healthy'
            p.circuit_open_until = ''
        else:
            p.consecutive_failures += 1
            p.health = 'degraded' if p.consecutive_failures < self.__m_settings.breaker_threshold else 'down'
            until = self.__m_breaker.openUntil(key)
            if until:
                from datetime import datetime, timezone

                p.circuit_open_until = datetime.fromtimestamp(until, tz=timezone.utc).replace(microsecond=0).isoformat()

    def _writeArtifacts(self, s, run, result, req, doc, pages):
        kinds = [k for k in dict.fromkeys(req.output) if k in export.KINDS]
        if not kinds:
            return []
        day = utcnow()[:10].replace('-', '/')
        folder = self.__m_settings.artifactRoot / day / run.id
        folder.mkdir(parents=True, exist_ok=True)
        meta = {
            'run_id': run.id,
            'width': doc.width,
            'height': doc.height,
            'image_bytes': pages[0] if pages else b'',
            'page_images': pages,
            'engine': run.winning_engine,
            'created_at': run.created_at,
        }
        out = []
        for kind in kinds:
            try:
                data, mime, ext = export.write(kind, result, meta)
            except Exception as exc:  # noqa: BLE001
                log.warning('artifact failed', kind=kind, error=str(exc))
                continue
            path = folder / '{}{}'.format(kind, ext)
            path.write_bytes(data)
            s.add(
                Artifact(
                    run_id=run.id, kind=kind, path=str(path), bytes=len(data), sha256=hashlib.sha256(data).hexdigest()
                )
            )
            out.append(
                {'kind': kind, 'bytes': len(data), 'mime': mime, 'url': '/v1/runs/{}/artifacts/{}'.format(run.id, kind)}
            )
        if self.__m_settings.store_inputs and not self.__m_settings.privacy_mode:
            (
                self.__m_settings.home / 'uploads' / '{}{}'.format(run.id, Path(doc.filename).suffix or '.bin')
            ).write_bytes(doc.data)
        return out

    def _persistCached(self, s, req, doc, result, routing, started):
        st = postprocess.stats(result)
        run = Run(
            api_key_id=req.api_key.id if req.api_key else None,
            requested_engine=req.engine,
            route_name=(routing or {}).get('route', ''),
            winning_engine=(routing or {}).get('winning_engine', ''),
            winning_provider=(routing or {}).get('winning_provider', ''),
            input_kind=doc.kind,
            mime=doc.mime,
            bytes=len(doc.data),
            image_sha256=doc.sha256,
            page_count=doc.page_count,
            language=','.join(req.language),
            status='cached',
            cache_hit=True,
            chars=st['chars'],
            lines=st['lines'],
            words=st['words'],
            duration_ms=int((time.time() - started) * 1000),
            result_json=result,
            routing_json=routing,
            metadata_json=req.metadata,
            origin=req.origin,
            client_ip=req.client_ip,
            user_agent=req.user_agent[:255],
            idempotency_key=req.idempotency_key,
            finished_at=utcnow(),
        )
        s.add(run)
        s.flush()
        self._emit(run)
        return run

    @staticmethod
    def _usage(result, run):
        st = postprocess.stats(result)
        return {
            'pages': run.page_count,
            'chars': st['chars'],
            'lines': st['lines'],
            'words': st['words'],
            'mean_confidence': st['mean_confidence'],
            'duration_ms': run.duration_ms,
            'cost_cents': round(float(run.cost_cents or 0.0), 4),
        }

    def _outcomeFromRun(self, run, cached=False):
        result = run.result_json or OCRPlugin.emptyResult()
        artifacts = [
            {'kind': a.kind, 'bytes': a.bytes, 'url': '/v1/runs/{}/artifacts/{}'.format(run.id, a.kind)}
            for a in run.artifacts
        ]
        return RunOutcome(
            run.id,
            run.status,
            result,
            run.routing_json or {},
            self._usage(result, run),
            artifacts,
            cached=cached,
            error_code=run.error_code,
            error_message=run.error_message,
            metadata=run.metadata_json or {},
        )

    def _emit(self, run):
        payload = {
            'id': run.id,
            'status': run.status,
            'route': run.route_name,
            'engine': run.winning_engine,
            'duration_ms': run.duration_ms,
            'chars': run.chars,
            'created_at': run.created_at,
            'error_code': run.error_code,
        }
        for cb in list(self.__m_events):
            try:
                cb(payload)
            except Exception:  # noqa: BLE001
                pass

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

    def getRegistry(self):
        """
        :return: any
        """
        return self.__m_registry

    def setRegistry(self, registry):
        """
        :param registry: any
        """
        self.__m_registry = registry

    def getRouter(self):
        """
        :return: any
        """
        return self.__m_router

    def setRouter(self, router):
        """
        :param router: any
        """
        self.__m_router = router

    def getBreaker(self):
        """
        :return: any
        """
        return self.__m_breaker

    def setBreaker(self, breaker):
        """
        :param breaker: any
        """
        self.__m_breaker = breaker

    def getSecrets(self):
        """
        :return: any
        """
        return self.__m_secrets

    def setSecrets(self, secrets):
        """
        :param secrets: any
        """
        self.__m_secrets = secrets

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

    def getEvents(self):
        """
        :return: any
        """
        return self.__m_events

    def setEvents(self, events):
        """
        :param events: any
        """
        self.__m_events = events

    settings = property(fget=getSettings, fset=setSettings)
    registry = property(fget=getRegistry, fset=setRegistry)
    router = property(fget=getRouter, fset=setRouter)
    breaker = property(fget=getBreaker, fset=setBreaker)
    secrets = property(fget=getSecrets, fset=setSecrets)
    pool = property(fget=getPool, fset=setPool)
    events = property(fget=getEvents, fset=setEvents)


class _EngineFailed(Exception):
    def __init__(self, code, msg):
        super(_EngineFailed, self).__init__(msg)
        self.code = code
        self.msg = msg
