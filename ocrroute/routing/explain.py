# coding=utf-8
"""
Routing trace returned beside the OCR result.
"""
from __future__ import absolute_import, division, print_function

class RoutingTrace(object):
    """
    Accumulates the routing decisions and attempts of one run.
    """

    def __init__(self, *args, **kwargs):
        """
        :param route: str
        :param strategy: str
        :param explain: list[str]
        :param attempts: list[dict]
        :param winning_engine: str
        :param winning_provider: str
        :param degraded: bool
        :param options_applied: dict
        :param warnings: list[str]
        :param votes: dict | None - ensemble vote detail
        """
        args = list(args)
        self.__m_route = kwargs.pop('route', args.pop(0) if args else '')
        self.__m_strategy = kwargs.pop('strategy', args.pop(0) if args else '')
        self.__m_explain = kwargs.pop('explain', args.pop(0) if args else list())
        self.__m_attempts = kwargs.pop('attempts', args.pop(0) if args else list())
        self.__m_winningEngine = kwargs.pop('winning_engine', args.pop(0) if args else '')
        self.__m_winningProvider = kwargs.pop('winning_provider', args.pop(0) if args else '')
        self.__m_degraded = kwargs.pop('degraded', args.pop(0) if args else False)
        self.__m_optionsApplied = kwargs.pop('options_applied', args.pop(0) if args else dict())
        self.__m_warnings = kwargs.pop('warnings', args.pop(0) if args else list())
        self.__m_votes = kwargs.pop('votes', args.pop(0) if args else None)

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

    def getAttempts(self):
        """
        :return: list[dict]
        """
        return self.__m_attempts

    def setAttempts(self, attempts):
        """
        :param attempts: list[dict]
        """
        self.__m_attempts = attempts

    def getWinningEngine(self):
        """
        :return: str
        """
        return self.__m_winningEngine

    def setWinningEngine(self, winningEngine):
        """
        :param winningEngine: str
        """
        self.__m_winningEngine = winningEngine

    def getWinningProvider(self):
        """
        :return: str
        """
        return self.__m_winningProvider

    def setWinningProvider(self, winningProvider):
        """
        :param winningProvider: str
        """
        self.__m_winningProvider = winningProvider

    def getDegraded(self):
        """
        :return: bool
        """
        return self.__m_degraded

    def setDegraded(self, degraded):
        """
        :param degraded: bool
        """
        self.__m_degraded = degraded

    def getOptionsApplied(self):
        """
        :return: dict
        """
        return self.__m_optionsApplied

    def setOptionsApplied(self, optionsApplied):
        """
        :param optionsApplied: dict
        """
        self.__m_optionsApplied = optionsApplied

    def getWarnings(self):
        """
        :return: list[str]
        """
        return self.__m_warnings

    def setWarnings(self, warnings):
        """
        :param warnings: list[str]
        """
        self.__m_warnings = warnings

    def getVotes(self):
        """
        :return: dict | None
        """
        return self.__m_votes

    def setVotes(self, votes):
        """
        :param votes: dict | None
        """
        self.__m_votes = votes

    def __repr__(self):
        return 'RoutingTrace({})'.format(', '.join('{}={!r}'.format(k, v) for k, v in self.toDict().items()))

    def toDict(self):
        """
        :return: dict - the ``routing`` block of the response envelope
        """
        d = {
            'route': self.__m_route,
            'strategy': self.__m_strategy,
            'winning_engine': self.__m_winningEngine,
            'winning_provider': self.__m_winningProvider,
            'attempt_count': len(self.__m_attempts),
            'degraded': self.__m_degraded,
            'explain': self.__m_explain,
            'attempts': self.__m_attempts,
            'options_applied': self.__m_optionsApplied,
            'warnings': self.__m_warnings,
        }
        if self.__m_votes is not None:
            d['votes'] = self.__m_votes
        return d

    route = property(fget=getRoute, fset=setRoute)
    strategy = property(fget=getStrategy, fset=setStrategy)
    explain = property(fget=getExplain, fset=setExplain)
    attempts = property(fget=getAttempts, fset=setAttempts)
    winning_engine = property(fget=getWinningEngine, fset=setWinningEngine)
    winning_provider = property(fget=getWinningProvider, fset=setWinningProvider)
    degraded = property(fget=getDegraded, fset=setDegraded)
    options_applied = property(fget=getOptionsApplied, fset=setOptionsApplied)
    warnings = property(fget=getWarnings, fset=setWarnings)
    votes = property(fget=getVotes, fset=setVotes)
