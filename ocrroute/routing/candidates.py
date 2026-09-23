# coding=utf-8
"""
Candidate model shared by strategies, the router and the simulator.
"""
from __future__ import absolute_import, division, print_function

class RequestContext(object):
    """
    Facts about the incoming request that influence routing.
    """

    def __init__(self, *args, **kwargs):
        """
        :param mime: str
        :param width: int
        :param height: int
        :param page_count: int
        :param language: list[str]
        :param handwriting: bool
        :param tables: bool
        :param sensitive: bool
        :param offline: bool
        :param max_cost_cents: float | None
        """
        args = list(args)
        self.__m_mime = kwargs.pop('mime', args.pop(0) if args else 'image/png')
        self.__m_width = kwargs.pop('width', args.pop(0) if args else 0)
        self.__m_height = kwargs.pop('height', args.pop(0) if args else 0)
        self.__m_pageCount = kwargs.pop('page_count', args.pop(0) if args else 1)
        self.__m_language = kwargs.pop('language', args.pop(0) if args else ['en'])
        self.__m_handwriting = kwargs.pop('handwriting', args.pop(0) if args else False)
        self.__m_tables = kwargs.pop('tables', args.pop(0) if args else False)
        self.__m_sensitive = kwargs.pop('sensitive', args.pop(0) if args else False)
        self.__m_offline = kwargs.pop('offline', args.pop(0) if args else False)
        self.__m_maxCostCents = kwargs.pop('max_cost_cents', args.pop(0) if args else None)
        self.sha256 = kwargs.pop('sha256', '')

    def getMime(self):
        """
        :return: str
        """
        return self.__m_mime

    def setMime(self, mime):
        """
        :param mime: str
        """
        self.__m_mime = mime

    def getWidth(self):
        """
        :return: int
        """
        return self.__m_width

    def setWidth(self, width):
        """
        :param width: int
        """
        self.__m_width = width

    def getHeight(self):
        """
        :return: int
        """
        return self.__m_height

    def setHeight(self, height):
        """
        :param height: int
        """
        self.__m_height = height

    def getPageCount(self):
        """
        :return: int
        """
        return self.__m_pageCount

    def setPageCount(self, pageCount):
        """
        :param pageCount: int
        """
        self.__m_pageCount = pageCount

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

    def getHandwriting(self):
        """
        :return: bool
        """
        return self.__m_handwriting

    def setHandwriting(self, handwriting):
        """
        :param handwriting: bool
        """
        self.__m_handwriting = handwriting

    def getTables(self):
        """
        :return: bool
        """
        return self.__m_tables

    def setTables(self, tables):
        """
        :param tables: bool
        """
        self.__m_tables = tables

    def getSensitive(self):
        """
        :return: bool
        """
        return self.__m_sensitive

    def setSensitive(self, sensitive):
        """
        :param sensitive: bool
        """
        self.__m_sensitive = sensitive

    def getOffline(self):
        """
        :return: bool
        """
        return self.__m_offline

    def setOffline(self, offline):
        """
        :param offline: bool
        """
        self.__m_offline = offline

    def getMaxCostCents(self):
        """
        :return: float | None
        """
        return self.__m_maxCostCents

    def setMaxCostCents(self, maxCostCents):
        """
        :param maxCostCents: float | None
        """
        self.__m_maxCostCents = maxCostCents

    def toDict(self):
        """
        :return: dict
        """
        return {
            'mime': self.__m_mime,
            'width': self.__m_width,
            'height': self.__m_height,
            'page_count': self.__m_pageCount,
            'language': self.__m_language,
            'handwriting': self.__m_handwriting,
            'tables': self.__m_tables,
            'sensitive': self.__m_sensitive,
            'offline': self.__m_offline,
            'max_cost_cents': self.__m_maxCostCents,
        }

    def __repr__(self):
        return 'RequestContext({})'.format(', '.join('{}={!r}'.format(k, v) for k, v in self.toDict().items()))

    def isPdf(self):
        """
        :return: bool
        """
        return self.__m_mime == 'application/pdf' or self.__m_pageCount > 1

    def isSmall(self):
        """
        :return: bool - a single small image (<= 1.5 Mpx)
        """
        return 0 < self.__m_width * self.__m_height <= 1500000

    is_pdf = property(fget=isPdf)
    small = property(fget=isSmall)

    mime = property(fget=getMime, fset=setMime)
    width = property(fget=getWidth, fset=setWidth)
    height = property(fget=getHeight, fset=setHeight)
    page_count = property(fget=getPageCount, fset=setPageCount)
    language = property(fget=getLanguage, fset=setLanguage)
    handwriting = property(fget=getHandwriting, fset=setHandwriting)
    tables = property(fget=getTables, fset=setTables)
    sensitive = property(fget=getSensitive, fset=setSensitive)
    offline = property(fget=getOffline, fset=setOffline)
    max_cost_cents = property(fget=getMaxCostCents, fset=setMaxCostCents)


class Candidate(object):
    """
    One provider (or engine default) that may serve a request.
    """

    def __init__(self, *args, **kwargs):
        """
        :param provider_id: str - provider row id (empty for engine defaults)
        :param provider_label: str
        :param engine_id: str
        :param kind: str - api | local
        :param order_index: int
        :param priority: int
        :param weight: int
        :param enabled: bool
        :param engine_available: bool
        :param engine_enabled: bool
        :param health: str
        :param circuit_open: bool
        :param over_limit: str - rpm | rpd | budget | empty
        :param in_flight: int
        :param recent_uses: int
        :param recent_latency_ms: float
        :param est_cost_cents: float
        :param quality_score: int
        :param supports_pdf: bool
        :param supports_handwriting: bool
        :param supports_tables: bool
        :param languages: list[str]
        :param condition: dict - member condition
        :param option_overrides: dict
        :param provider_options: dict
        :param endpoint: str
        :param model: str
        :param language: str
        :param timeout: int
        :param retries: int
        :param proxy: dict
        :param credential_ids: list[str]
        """
        args = list(args)
        self.__m_providerId = kwargs.pop('provider_id', args.pop(0) if args else '')
        self.__m_providerLabel = kwargs.pop('provider_label', args.pop(0) if args else '')
        self.__m_engineId = kwargs.pop('engine_id', args.pop(0) if args else '')
        self.__m_kind = kwargs.pop('kind', args.pop(0) if args else 'local')
        self.__m_orderIndex = kwargs.pop('order_index', args.pop(0) if args else 0)
        self.__m_priority = kwargs.pop('priority', args.pop(0) if args else 100)
        self.__m_weight = kwargs.pop('weight', args.pop(0) if args else 1)
        self.__m_enabled = kwargs.pop('enabled', args.pop(0) if args else True)
        self.__m_engineAvailable = kwargs.pop('engine_available', args.pop(0) if args else True)
        self.__m_engineEnabled = kwargs.pop('engine_enabled', args.pop(0) if args else True)
        self.__m_health = kwargs.pop('health', args.pop(0) if args else 'unknown')
        self.__m_circuitOpen = kwargs.pop('circuit_open', args.pop(0) if args else False)
        self.__m_overLimit = kwargs.pop('over_limit', args.pop(0) if args else '')
        self.__m_inFlight = kwargs.pop('in_flight', args.pop(0) if args else 0)
        self.__m_recentUses = kwargs.pop('recent_uses', args.pop(0) if args else 0)
        self.__m_recentLatencyMs = kwargs.pop('recent_latency_ms', args.pop(0) if args else 0.0)
        self.recent_confidence = kwargs.pop('recent_confidence', 0.0)
        self.__m_estCostCents = kwargs.pop('est_cost_cents', args.pop(0) if args else 0.0)
        self.__m_qualityScore = kwargs.pop('quality_score', args.pop(0) if args else 50)
        self.__m_supportsPdf = kwargs.pop('supports_pdf', args.pop(0) if args else False)
        self.__m_supportsHandwriting = kwargs.pop('supports_handwriting', args.pop(0) if args else False)
        self.__m_supportsTables = kwargs.pop('supports_tables', args.pop(0) if args else False)
        self.__m_languages = kwargs.pop('languages', args.pop(0) if args else list())
        self.__m_condition = kwargs.pop('condition', args.pop(0) if args else dict())
        self.__m_optionOverrides = kwargs.pop('option_overrides', args.pop(0) if args else dict())
        self.__m_providerOptions = kwargs.pop('provider_options', args.pop(0) if args else dict())
        self.__m_endpoint = kwargs.pop('endpoint', args.pop(0) if args else '')
        self.__m_model = kwargs.pop('model', args.pop(0) if args else '')
        self.__m_language = kwargs.pop('language', args.pop(0) if args else '')
        self.__m_timeout = kwargs.pop('timeout', args.pop(0) if args else 60)
        self.__m_retries = kwargs.pop('retries', args.pop(0) if args else 2)
        self.__m_proxy = kwargs.pop('proxy', args.pop(0) if args else dict())
        self.__m_credentialIds = kwargs.pop('credential_ids', args.pop(0) if args else list())

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

    def getProviderLabel(self):
        """
        :return: str
        """
        return self.__m_providerLabel

    def setProviderLabel(self, providerLabel):
        """
        :param providerLabel: str
        """
        self.__m_providerLabel = providerLabel

    def getEngineId(self):
        """
        :return: str
        """
        return self.__m_engineId

    def setEngineId(self, engineId):
        """
        :param engineId: str
        """
        self.__m_engineId = engineId

    def getKind(self):
        """
        :return: str
        """
        return self.__m_kind

    def setKind(self, kind):
        """
        :param kind: str
        """
        self.__m_kind = kind

    def getOrderIndex(self):
        """
        :return: int
        """
        return self.__m_orderIndex

    def setOrderIndex(self, orderIndex):
        """
        :param orderIndex: int
        """
        self.__m_orderIndex = orderIndex

    def getPriority(self):
        """
        :return: int
        """
        return self.__m_priority

    def setPriority(self, priority):
        """
        :param priority: int
        """
        self.__m_priority = priority

    def getWeight(self):
        """
        :return: int
        """
        return self.__m_weight

    def setWeight(self, weight):
        """
        :param weight: int
        """
        self.__m_weight = weight

    def getEnabled(self):
        """
        :return: bool
        """
        return self.__m_enabled

    def setEnabled(self, enabled):
        """
        :param enabled: bool
        """
        self.__m_enabled = enabled

    def getEngineAvailable(self):
        """
        :return: bool
        """
        return self.__m_engineAvailable

    def setEngineAvailable(self, engineAvailable):
        """
        :param engineAvailable: bool
        """
        self.__m_engineAvailable = engineAvailable

    def getEngineEnabled(self):
        """
        :return: bool
        """
        return self.__m_engineEnabled

    def setEngineEnabled(self, engineEnabled):
        """
        :param engineEnabled: bool
        """
        self.__m_engineEnabled = engineEnabled

    def getHealth(self):
        """
        :return: str
        """
        return self.__m_health

    def setHealth(self, health):
        """
        :param health: str
        """
        self.__m_health = health

    def getCircuitOpen(self):
        """
        :return: bool
        """
        return self.__m_circuitOpen

    def setCircuitOpen(self, circuitOpen):
        """
        :param circuitOpen: bool
        """
        self.__m_circuitOpen = circuitOpen

    def getOverLimit(self):
        """
        :return: str
        """
        return self.__m_overLimit

    def setOverLimit(self, overLimit):
        """
        :param overLimit: str
        """
        self.__m_overLimit = overLimit

    def getInFlight(self):
        """
        :return: int
        """
        return self.__m_inFlight

    def setInFlight(self, inFlight):
        """
        :param inFlight: int
        """
        self.__m_inFlight = inFlight

    def getRecentUses(self):
        """
        :return: int
        """
        return self.__m_recentUses

    def setRecentUses(self, recentUses):
        """
        :param recentUses: int
        """
        self.__m_recentUses = recentUses

    def getRecentLatencyMs(self):
        """
        :return: float
        """
        return self.__m_recentLatencyMs

    def setRecentLatencyMs(self, recentLatencyMs):
        """
        :param recentLatencyMs: float
        """
        self.__m_recentLatencyMs = recentLatencyMs

    def getEstCostCents(self):
        """
        :return: float
        """
        return self.__m_estCostCents

    def setEstCostCents(self, estCostCents):
        """
        :param estCostCents: float
        """
        self.__m_estCostCents = estCostCents

    def getQualityScore(self):
        """
        :return: int
        """
        return self.__m_qualityScore

    def setQualityScore(self, qualityScore):
        """
        :param qualityScore: int
        """
        self.__m_qualityScore = qualityScore

    def getSupportsPdf(self):
        """
        :return: bool
        """
        return self.__m_supportsPdf

    def setSupportsPdf(self, supportsPdf):
        """
        :param supportsPdf: bool
        """
        self.__m_supportsPdf = supportsPdf

    def getSupportsHandwriting(self):
        """
        :return: bool
        """
        return self.__m_supportsHandwriting

    def setSupportsHandwriting(self, supportsHandwriting):
        """
        :param supportsHandwriting: bool
        """
        self.__m_supportsHandwriting = supportsHandwriting

    def getSupportsTables(self):
        """
        :return: bool
        """
        return self.__m_supportsTables

    def setSupportsTables(self, supportsTables):
        """
        :param supportsTables: bool
        """
        self.__m_supportsTables = supportsTables

    def getLanguages(self):
        """
        :return: list[str]
        """
        return self.__m_languages

    def setLanguages(self, languages):
        """
        :param languages: list[str]
        """
        self.__m_languages = languages

    def getCondition(self):
        """
        :return: dict
        """
        return self.__m_condition

    def setCondition(self, condition):
        """
        :param condition: dict
        """
        self.__m_condition = condition

    def getOptionOverrides(self):
        """
        :return: dict
        """
        return self.__m_optionOverrides

    def setOptionOverrides(self, optionOverrides):
        """
        :param optionOverrides: dict
        """
        self.__m_optionOverrides = optionOverrides

    def getProviderOptions(self):
        """
        :return: dict
        """
        return self.__m_providerOptions

    def setProviderOptions(self, providerOptions):
        """
        :param providerOptions: dict
        """
        self.__m_providerOptions = providerOptions

    def getEndpoint(self):
        """
        :return: str
        """
        return self.__m_endpoint

    def setEndpoint(self, endpoint):
        """
        :param endpoint: str
        """
        self.__m_endpoint = endpoint

    def getModel(self):
        """
        :return: str
        """
        return self.__m_model

    def setModel(self, model):
        """
        :param model: str
        """
        self.__m_model = model

    def getLanguage(self):
        """
        :return: str
        """
        return self.__m_language

    def setLanguage(self, language):
        """
        :param language: str
        """
        self.__m_language = language

    def getTimeout(self):
        """
        :return: int
        """
        return self.__m_timeout

    def setTimeout(self, timeout):
        """
        :param timeout: int
        """
        self.__m_timeout = timeout

    def getRetries(self):
        """
        :return: int
        """
        return self.__m_retries

    def setRetries(self, retries):
        """
        :param retries: int
        """
        self.__m_retries = retries

    def getProxy(self):
        """
        :return: dict
        """
        return self.__m_proxy

    def setProxy(self, proxy):
        """
        :param proxy: dict
        """
        self.__m_proxy = proxy

    def getCredentialIds(self):
        """
        :return: list[str]
        """
        return self.__m_credentialIds

    def setCredentialIds(self, credentialIds):
        """
        :param credentialIds: list[str]
        """
        self.__m_credentialIds = credentialIds

    def toDict(self):
        """
        :return: dict
        """
        return {
            'provider_id': self.__m_providerId,
            'provider_label': self.__m_providerLabel,
            'engine_id': self.__m_engineId,
            'kind': self.__m_kind,
            'order_index': self.__m_orderIndex,
            'priority': self.__m_priority,
            'weight': self.__m_weight,
            'enabled': self.__m_enabled,
            'engine_available': self.__m_engineAvailable,
            'engine_enabled': self.__m_engineEnabled,
            'health': self.__m_health,
            'circuit_open': self.__m_circuitOpen,
            'over_limit': self.__m_overLimit,
            'in_flight': self.__m_inFlight,
            'recent_uses': self.__m_recentUses,
            'recent_latency_ms': self.__m_recentLatencyMs,
            'recent_confidence': self.recent_confidence,
            'est_cost_cents': self.__m_estCostCents,
            'quality_score': self.__m_qualityScore,
            'supports_pdf': self.__m_supportsPdf,
            'supports_handwriting': self.__m_supportsHandwriting,
            'supports_tables': self.__m_supportsTables,
            'languages': self.__m_languages,
            'condition': self.__m_condition,
            'option_overrides': self.__m_optionOverrides,
            'provider_options': self.__m_providerOptions,
            'endpoint': self.__m_endpoint,
            'model': self.__m_model,
            'language': self.__m_language,
            'timeout': self.__m_timeout,
            'retries': self.__m_retries,
            'proxy': self.__m_proxy,
            'credential_ids': self.__m_credentialIds,
        }

    def __repr__(self):
        return 'Candidate({})'.format(', '.join('{}={!r}'.format(k, v) for k, v in self.toDict().items()))

    provider_id = property(fget=getProviderId, fset=setProviderId)
    provider_label = property(fget=getProviderLabel, fset=setProviderLabel)
    engine_id = property(fget=getEngineId, fset=setEngineId)
    kind = property(fget=getKind, fset=setKind)
    order_index = property(fget=getOrderIndex, fset=setOrderIndex)
    priority = property(fget=getPriority, fset=setPriority)
    weight = property(fget=getWeight, fset=setWeight)
    enabled = property(fget=getEnabled, fset=setEnabled)
    engine_available = property(fget=getEngineAvailable, fset=setEngineAvailable)
    engine_enabled = property(fget=getEngineEnabled, fset=setEngineEnabled)
    health = property(fget=getHealth, fset=setHealth)
    circuit_open = property(fget=getCircuitOpen, fset=setCircuitOpen)
    over_limit = property(fget=getOverLimit, fset=setOverLimit)
    in_flight = property(fget=getInFlight, fset=setInFlight)
    recent_uses = property(fget=getRecentUses, fset=setRecentUses)
    recent_latency_ms = property(fget=getRecentLatencyMs, fset=setRecentLatencyMs)
    est_cost_cents = property(fget=getEstCostCents, fset=setEstCostCents)
    quality_score = property(fget=getQualityScore, fset=setQualityScore)
    supports_pdf = property(fget=getSupportsPdf, fset=setSupportsPdf)
    supports_handwriting = property(fget=getSupportsHandwriting, fset=setSupportsHandwriting)
    supports_tables = property(fget=getSupportsTables, fset=setSupportsTables)
    languages = property(fget=getLanguages, fset=setLanguages)
    condition = property(fget=getCondition, fset=setCondition)
    option_overrides = property(fget=getOptionOverrides, fset=setOptionOverrides)
    provider_options = property(fget=getProviderOptions, fset=setProviderOptions)
    endpoint = property(fget=getEndpoint, fset=setEndpoint)
    model = property(fget=getModel, fset=setModel)
    language = property(fget=getLanguage, fset=setLanguage)
    timeout = property(fget=getTimeout, fset=setTimeout)
    retries = property(fget=getRetries, fset=setRetries)
    proxy = property(fget=getProxy, fset=setProxy)
    credential_ids = property(fget=getCredentialIds, fset=setCredentialIds)


def filterCandidates(cands, ctx):
    """
    Drop candidates that cannot serve this request.

    :param cands: list[Candidate]
    :param ctx: RequestContext
    :return: tuple[list[Candidate], list[str]] - (kept, explain lines)
    """
    kept = []
    explain = []
    for c in cands:
        why = _rejectReason(c, ctx)
        if why:
            explain.append('skipped {}/{} ({})'.format(c.engine_id, c.provider_label, why))
        else:
            kept.append(c)
    return kept, explain


def _rejectReason(c, ctx):
    """
    :param c: Candidate
    :param ctx: RequestContext
    :return: str - empty when the candidate is acceptable
    """
    if not c.enabled:
        return 'member disabled'
    if not c.engine_enabled:
        return 'engine disabled'
    if not c.engine_available:
        return 'engine unavailable'
    if c.circuit_open:
        return 'circuit open'
    if c.over_limit:
        return 'over {} limit'.format(c.over_limit)
    if ctx.offline and c.kind == 'api':
        return 'offline mode'
    cond = c.condition or {}
    if cond.get('language_in') and not set(ctx.language) & set(cond['language_in']):
        return 'language condition'
    if cond.get('mime_in') and ctx.mime not in cond['mime_in']:
        return 'mime condition'
    if cond.get('min_pages') and ctx.page_count < int(cond['min_pages']):
        return 'min_pages condition'
    if cond.get('max_pages') and ctx.page_count > int(cond['max_pages']):
        return 'max_pages condition'
    if cond.get('max_cost_cents') is not None and c.est_cost_cents > float(cond['max_cost_cents']):
        return 'max_cost condition'
    if ctx.max_cost_cents is not None and c.est_cost_cents > ctx.max_cost_cents:
        return 'request max_cost'
    return ''
