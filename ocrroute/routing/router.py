# coding=utf-8
"""Resolve a request to an ordered candidate list, with a full explain trace. No OCR happens here."""
from __future__ import absolute_import, division, print_function

import time
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from ocrroute.db.models import Engine, Provider, Route
from ocrroute.db.repo.stats import providerRecentConfidence, providerRecentLatency
from ocrroute.errors import NoCandidate, NotFound
from ocrroute.routing import strategies
from ocrroute.routing.candidates import Candidate, filterCandidates
from ocrroute.routing.cost import estimateCents
from ocrroute.routing.templates import AUTO_TEMPLATES, applyFilter, isAutoRoute
from ocrroute.runtime.limits import IN_FLIGHT, WINDOW


class RouteSpec(object):
    """
    A resolved route: ordered candidates plus the run parameters.
    """

    def __init__(self, *args, **kwargs):
        """
        :param name: str
        :param strategy: str
        :param candidates: list[Candidate]
        :param explain: list[str]
        :param stop_condition: dict
        :param max_attempts: int
        :param total_deadline_ms: int
        :param cache_ttl_seconds: int
        :param route_id: str | None
        :param parallel: int - ensemble fan-out
        :param tool_chain: list - reserved (Tools), always empty
        """
        args = list(args)
        self.__m_name = kwargs.pop('name', args.pop(0) if args else '')
        self.__m_strategy = kwargs.pop('strategy', args.pop(0) if args else 'priority')
        self.__m_candidates = kwargs.pop('candidates', args.pop(0) if args else list())
        self.__m_explain = kwargs.pop('explain', args.pop(0) if args else list())
        self.__m_stopCondition = kwargs.pop('stop_condition', args.pop(0) if args else dict())
        self.__m_maxAttempts = kwargs.pop('max_attempts', args.pop(0) if args else 5)
        self.__m_totalDeadlineMs = kwargs.pop('total_deadline_ms', args.pop(0) if args else 120000)
        self.__m_cacheTtlSeconds = kwargs.pop('cache_ttl_seconds', args.pop(0) if args else 3600)
        self.__m_routeId = kwargs.pop('route_id', args.pop(0) if args else None)
        self.__m_parallel = kwargs.pop('parallel', args.pop(0) if args else 1)
        self.__m_toolChain = kwargs.pop('tool_chain', args.pop(0) if args else list())

    def getName(self):
        """
        :return: str
        """
        return self.__m_name

    def setName(self, name):
        """
        :param name: str
        """
        self.__m_name = name

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

    def getCandidates(self):
        """
        :return: list[Candidate]
        """
        return self.__m_candidates

    def setCandidates(self, candidates):
        """
        :param candidates: list[Candidate]
        """
        self.__m_candidates = candidates

    def getExplain(self):
        """
        :return: list[str]
        """
        return self.__m_explain

    def setExplain(self, explain):
        """
        :param explain: list[str]
        """
        self.__m_explain = explain

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

    def getMaxAttempts(self):
        """
        :return: int
        """
        return self.__m_maxAttempts

    def setMaxAttempts(self, maxAttempts):
        """
        :param maxAttempts: int
        """
        self.__m_maxAttempts = maxAttempts

    def getTotalDeadlineMs(self):
        """
        :return: int
        """
        return self.__m_totalDeadlineMs

    def setTotalDeadlineMs(self, totalDeadlineMs):
        """
        :param totalDeadlineMs: int
        """
        self.__m_totalDeadlineMs = totalDeadlineMs

    def getCacheTtlSeconds(self):
        """
        :return: int
        """
        return self.__m_cacheTtlSeconds

    def setCacheTtlSeconds(self, cacheTtlSeconds):
        """
        :param cacheTtlSeconds: int
        """
        self.__m_cacheTtlSeconds = cacheTtlSeconds

    def getRouteId(self):
        """
        :return: str | None
        """
        return self.__m_routeId

    def setRouteId(self, routeId):
        """
        :param routeId: str | None
        """
        self.__m_routeId = routeId

    def getParallel(self):
        """
        :return: int
        """
        return self.__m_parallel

    def setParallel(self, parallel):
        """
        :param parallel: int
        """
        self.__m_parallel = parallel

    def getToolChain(self):
        """
        :return: list
        """
        return self.__m_toolChain

    def setToolChain(self, toolChain):
        """
        :param toolChain: list
        """
        self.__m_toolChain = toolChain

    def toDict(self):
        """
        :return: dict
        """
        return {
            'name': self.__m_name,
            'strategy': self.__m_strategy,
            'candidates': self.__m_candidates,
            'explain': self.__m_explain,
            'stop_condition': self.__m_stopCondition,
            'max_attempts': self.__m_maxAttempts,
            'total_deadline_ms': self.__m_totalDeadlineMs,
            'cache_ttl_seconds': self.__m_cacheTtlSeconds,
            'route_id': self.__m_routeId,
            'parallel': self.__m_parallel,
            'tool_chain': self.__m_toolChain,
        }

    def __repr__(self):
        return 'RouteSpec({})'.format(', '.join('{}={!r}'.format(k, v) for k, v in self.toDict().items()))

    name = property(fget=getName, fset=setName)
    strategy = property(fget=getStrategy, fset=setStrategy)
    candidates = property(fget=getCandidates, fset=setCandidates)
    explain = property(fget=getExplain, fset=setExplain)
    stop_condition = property(fget=getStopCondition, fset=setStopCondition)
    max_attempts = property(fget=getMaxAttempts, fset=setMaxAttempts)
    total_deadline_ms = property(fget=getTotalDeadlineMs, fset=setTotalDeadlineMs)
    cache_ttl_seconds = property(fget=getCacheTtlSeconds, fset=setCacheTtlSeconds)
    route_id = property(fget=getRouteId, fset=setRouteId)
    parallel = property(fget=getParallel, fset=setParallel)
    tool_chain = property(fget=getToolChain, fset=setToolChain)


class Router(object):
    def __init__(self, registry, breaker):
        self.__m_registry = registry
        self.__m_breaker = breaker
        self.__m_rrState = {}

    # ------------------------------------------------------------------ candidates
    def _candidate(self, session, p, member, ctx, latency, now):
        eng = session.get(Engine, p.engine_id)
        info = self.__m_registry.get(p.engine_id)
        key = 'provider:{}'.format(p.id)
        month = datetime.now(timezone.utc).strftime('%Y-%m')
        over = WINDOW.check(key, p.rpm_limit, p.rpd_limit, now)
        if not over and p.monthly_budget_cents and eng is not None:
            from ocrroute.db.repo.usage import monthCostForProvider

            if monthCostForProvider(session, p.id, month) >= p.monthly_budget_cents:
                over = 'budget'
        est = estimateCents(eng.cost_model if eng else 'free', eng.unit_price if eng else 0.0, pages=ctx.page_count)
        return Candidate(
            provider_id=p.id,
            provider_label=p.label,
            engine_id=p.engine_id,
            kind=(eng.kind if eng else (info.kind if info else 'api')),
            order_index=member.order_index if member else 0,
            priority=p.priority,
            weight=member.weight if member else p.weight,
            enabled=p.enabled and (member.enabled if member else True),
            engine_available=bool(info and info.available),
            engine_enabled=(eng.enabled if eng else True),
            health=p.health,
            circuit_open=not self.__m_breaker.allow(key, now),
            over_limit=over,
            in_flight=IN_FLIGHT.get(key),
            recent_latency_ms=latency.get(p.id, 0.0),
            recent_confidence=(getattr(self, '_confidence', None) or {}).get(p.label, 0.0),
            est_cost_cents=est,
            quality_score=eng.quality_score if eng else 50,
            supports_pdf=bool(eng and eng.supports_pdf),
            supports_handwriting=bool(eng and eng.supports_handwriting),
            supports_tables=bool(eng and eng.supports_tables),
            languages=list(eng.languages) if eng else [],
            condition=dict(member.condition) if member else {},
            option_overrides=dict(member.option_overrides) if member else {},
            provider_options=dict(p.options or {}),
            endpoint=p.endpoint,
            model=p.model,
            language=p.language,
            timeout=p.timeout,
            retries=p.retries,
            proxy=dict(p.proxy or {}),
            credential_ids=[c.id for c in p.credentials if c.enabled],
        )

    def _routeCandidates(self, session, route, ctx):
        now = time.time()
        latency = providerRecentLatency(session)
        self._confidence = providerRecentConfidence(session)
        out = []
        for m in route.members:
            p = session.get(Provider, m.provider_id)
            if p is None:
                continue
            out.append(self._candidate(session, p, m, ctx, latency, now))
        return out

    # ------------------------------------------------------------------ resolution
    def resolve(
        self,
        session,
        ctx,
        engine='',
        provider_id='',
        route_name='',
        api_key=None,
        strategy_override='',
        stop_override=None,
    ):
        explain = []
        if engine or provider_id:
            spec = self._single(session, ctx, engine, provider_id, explain)
        else:
            route = None if isAutoRoute(route_name) else self._pickRoute(session, route_name, api_key, explain)
            if isAutoRoute(route_name):
                spec = self._templateRoute(session, ctx, route_name, explain)
            elif route is None:
                spec = self._autoRoute(session, ctx, explain)
            else:
                spec = RouteSpec(
                    name=route.name,
                    strategy=route.strategy,
                    route_id=route.id,
                    candidates=self._routeCandidates(session, route, ctx),
                    stop_condition=dict(route.stop_condition or {}),
                    max_attempts=route.max_attempts,
                    total_deadline_ms=route.total_deadline_ms,
                    cache_ttl_seconds=route.cache_ttl_seconds,
                    tool_chain=list(route.tool_chain or []),
                )
                explain.append('route={} strategy={}'.format(route.name, route.strategy))
        if strategy_override:
            spec.strategy = strategy_override
            explain.append('strategy overridden → {}'.format(strategy_override))
        if stop_override:
            spec.stop_condition.update(stop_override)
        kept, skipped = filterCandidates(spec.candidates, ctx)
        explain.extend(skipped)
        if not kept:
            raise NoCandidate(
                'No engine can serve this request: ' + ('; '.join(skipped) if skipped else 'the route has no members')
            )
        state = self.__m_rrState.setdefault(spec.name, {})
        ordered = strategies.get(spec.strategy)(kept, ctx, state)
        explain.extend(state.pop('explain', []))
        spec.parallel = int(state.pop('parallel', 1))
        spec.pick = state.pop('pick', '')
        spec.candidates = ordered[: max(1, spec.max_attempts)] if spec.parallel == 1 else ordered
        explain.append('order: ' + ' → '.join('{}/{}'.format(c.engine_id, c.provider_label) for c in spec.candidates))
        spec.explain = explain
        return spec

    def _single(self, session, ctx, engine, provider_id, explain):
        now = time.time()
        latency = providerRecentLatency(session)
        if provider_id:
            p = session.get(Provider, provider_id)
            if p is None:
                raise NotFound("Provider '{}' not found".format(provider_id))
            explain.append('explicit provider {}'.format(p.label))
            return RouteSpec(
                name='provider:{}'.format(p.label),
                strategy='priority',
                candidates=[self._candidate(session, p, None, ctx, latency, now)],
                max_attempts=1,
            )
        info = self.__m_registry.get(engine)
        if info is None:
            raise NotFound("Engine '{}' not found".format(engine))
        providers = (
            session.execute(
                select(Provider)
                .options(selectinload(Provider.credentials))
                .where(Provider.engine_id == engine, Provider.enabled.is_(True))
                .order_by(Provider.priority)
            )
            .scalars()
            .all()
        )
        if providers:
            explain.append('explicit engine {}: {} provider(s)'.format(engine, len(providers)))
            cands = [self._candidate(session, p, None, ctx, latency, now) for p in providers]
        else:
            explain.append('explicit engine {}: no provider configured, using engine defaults'.format(engine))
            eng = session.get(Engine, engine)
            cands = [
                Candidate(
                    provider_id='',
                    provider_label='(defaults)',
                    engine_id=engine,
                    kind=info.kind,
                    engine_available=info.available,
                    engine_enabled=(eng.enabled if eng else True),
                    quality_score=info.quality_score,
                    supports_pdf=info.supports_pdf,
                    supports_handwriting=info.supports_handwriting,
                    supports_tables=info.supports_tables,
                    languages=list(info.languages),
                    circuit_open=not self.__m_breaker.allow('engine:{}'.format(engine), now),
                )
            ]
        return RouteSpec(
            name='engine:{}'.format(engine), strategy='priority', candidates=cands, max_attempts=len(cands)
        )

    def _pickRoute(self, session, route_name, api_key, explain):
        q = select(Route).options(selectinload(Route.members))
        if route_name:
            route = session.execute(q.where((Route.name == route_name) | (Route.id == route_name))).scalar_one_or_none()
            if route is None:
                raise NotFound("Route '{}' not found".format(route_name))
            if not route.enabled:
                raise NoCandidate("Route '{}' is disabled".format(route_name))
            return route
        if api_key is not None and api_key.route_id:
            route = session.execute(q.where(Route.id == api_key.route_id)).scalar_one_or_none()
            if route is not None and route.enabled:
                explain.append("route pinned by API key '{}'".format(api_key.name))
                return route
        route = session.execute(q.where(Route.is_default.is_(True), Route.enabled.is_(True))).scalar_one_or_none()
        if route is not None:
            explain.append('default route')
        return route

    def _templateRoute(self, session, ctx, name, explain):
        """
        Build a RouteSpec from a built-in ``auto/*`` template over every enabled provider.

        :param name: str  template id
        :return: RouteSpec
        """
        tpl = AUTO_TEMPLATES[name]
        for k, v in tpl.get('hints', {}).items():
            setattr(ctx, k, v)
        base = self._autoRoute(session, ctx, explain)
        cands = applyFilter(base.candidates, tpl.get('filter', {}))
        explain.append('template {} ({}) → {} candidate(s)'.format(name, tpl['strategy'], len(cands)))
        return RouteSpec(name=name, strategy=tpl['strategy'], candidates=cands, max_attempts=max(3, min(10, len(cands))),
                         cache_ttl_seconds=3600)

    def _autoRoute(self, session, ctx, explain):
        """Built-in ``auto`` route: every enabled provider, plus available local engines without a provider."""
        now = time.time()
        latency = providerRecentLatency(session)
        self._confidence = providerRecentConfidence(session)
        explain.append('no route configured → built-in auto route')
        cands = []
        providers = (
            session.execute(
                select(Provider).options(selectinload(Provider.credentials)).where(Provider.enabled.is_(True))
            )
            .scalars()
            .all()
        )
        seen = set()
        for p in providers:
            info = self.registry.get(p.engine_id)
            if info is not None and info.requires_key and not any(c.enabled for c in p.credentials):
                explain.append('skipped {}/{} (needs a credential)'.format(p.engine_id, p.label))
                seen.add(p.engine_id)
                continue
            cands.append(self._candidate(session, p, None, ctx, latency, now))
            seen.add(p.engine_id)
        for info in self.__m_registry.available():
            if info.kind == 'local' and info.id not in seen and not info.requires_key:
                eng = session.get(Engine, info.id)
                if eng is not None and not eng.enabled:
                    continue
                cands.append(
                    Candidate(
                        provider_id='',
                        provider_label='(defaults)',
                        engine_id=info.id,
                        kind='local',
                        order_index=len(cands),
                        quality_score=info.quality_score,
                        supports_pdf=info.supports_pdf,
                        supports_handwriting=info.supports_handwriting,
                        supports_tables=info.supports_tables,
                        languages=list(info.languages),
                        circuit_open=not self.__m_breaker.allow('engine:{}'.format(info.id), now),
                    )
                )
        rankHealth = {'healthy': 0, 'unknown': 2, 'degraded': 4, 'down': 5}
        classic = ('Tesseract', 'RapidOcr', 'PaddleOcr', 'EasyOCR', 'OpenOcr', 'OcrSpace', 'GoogleOcr')

        def rank(c):
            r = rankHealth.get(c.health, 2)
            if r == 2:  # cold start: lightweight engines first, deep-learning stacks (often missing deps) last
                info = self.registry.get(c.engine_id)
                if c.engine_id in classic:
                    r = 1
                elif info is not None and getattr(info, 'heavy', False):
                    r = 3
            return (r, 0 if getattr(c, 'recent_confidence', 0) else 1, c.order_index)

        cands.sort(key=rank)
        for i, c in enumerate(cands):  # known-good providers win tie-breaks in every strategy
            c.order_index = i
        return RouteSpec(name='auto', strategy='auto', candidates=cands, max_attempts=min(8, max(4, len(cands))))

    def simulate(self, session, ctx, **kwargs):
        try:
            spec = self.resolve(session, ctx, **kwargs)
        except NoCandidate as exc:
            return {'ok': False, 'error': exc.message, 'explain': []}
        return {
            'ok': True,
            'route': spec.name,
            'strategy': spec.strategy,
            'explain': spec.explain,
            'candidates': [
                {
                    'engine': c.engine_id,
                    'provider': c.provider_label,
                    'provider_id': c.provider_id,
                    'kind': c.kind,
                    'est_cost_cents': c.est_cost_cents,
                    'quality': c.quality_score,
                    'health': c.health,
                }
                for c in spec.candidates
            ],
        }

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

    registry = property(fget=getRegistry, fset=setRegistry)
    breaker = property(fget=getBreaker, fset=setBreaker)
