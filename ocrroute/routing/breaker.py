# coding=utf-8
"""Per-provider circuit breaker with escalating cooldown and a half-open probe."""
from __future__ import absolute_import, division, print_function

import threading
import time


class _State(object):
    """
    Mutable breaker state of one provider.
    """

    def __init__(self):
        self.failures = 0
        self.openUntil = 0.0
        self.trips = 0
        self.half_open_inflight = False
        self.lock = threading.Lock()


class CircuitBreaker(object):
    def __init__(self, threshold=5, cooldown=120.0, max_cooldown=3600.0):
        self.__m_threshold = threshold
        self.__m_cooldown = cooldown
        self.__m_maxCooldown = max_cooldown
        self.__m_states = {}
        self.__m_lock = threading.Lock()

    def _state(self, key):
        with self.__m_lock:
            return self.__m_states.setdefault(key, _State())

    def isOpen(self, key, now=None):
        st = self._state(key)
        now = now or time.time()
        with st.lock:
            if st.openUntil <= now:
                return False
            return True

    def allow(self, key, now=None):
        """True when a request may proceed (closed, or half-open probe slot available)."""
        st = self._state(key)
        now = now or time.time()
        with st.lock:
            if st.openUntil <= now:
                if st.openUntil and not st.half_open_inflight and st.failures >= self.__m_threshold:
                    st.half_open_inflight = True  # one probe
                return True
            return False

    def recordSuccess(self, key):
        st = self._state(key)
        with st.lock:
            st.failures = 0
            st.openUntil = 0.0
            st.trips = 0
            st.half_open_inflight = False

    def recordFailure(self, key, now=None):
        """Returns True when this failure opened (or re-opened) the circuit."""
        st = self._state(key)
        now = now or time.time()
        with st.lock:
            st.failures += 1
            st.half_open_inflight = False
            if st.failures >= self.__m_threshold:
                st.trips += 1
                delay = min(self.__m_maxCooldown, self.__m_cooldown * (2 ** (st.trips - 1)))
                st.openUntil = now + delay
                return True
            return False

    def reset(self, key):
        with self.__m_lock:
            self.__m_states.pop(key, None)

    def openUntil(self, key):
        return self._state(key).openUntil

    def snapshot(self):
        with self.__m_lock:
            return {
                k: {'failures': v.failures, 'open_until': v.openUntil, 'trips': v.trips}
                for k, v in self.__m_states.items()
            }

    def getThreshold(self):
        """
        :return: any
        """
        return self.__m_threshold

    def setThreshold(self, threshold):
        """
        :param threshold: any
        """
        self.__m_threshold = threshold

    def getCooldown(self):
        """
        :return: any
        """
        return self.__m_cooldown

    def setCooldown(self, cooldown):
        """
        :param cooldown: any
        """
        self.__m_cooldown = cooldown

    def getMaxCooldown(self):
        """
        :return: any
        """
        return self.__m_maxCooldown

    def setMaxCooldown(self, maxCooldown):
        """
        :param maxCooldown: any
        """
        self.__m_maxCooldown = maxCooldown

    threshold = property(fget=getThreshold, fset=setThreshold)
    cooldown = property(fget=getCooldown, fset=setCooldown)
    max_cooldown = property(fget=getMaxCooldown, fset=setMaxCooldown)
