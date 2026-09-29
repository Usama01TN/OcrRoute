# coding=utf-8
"""Sliding-window rate limits and monthly budgets, in-memory with SQLite-derived seeds."""
from __future__ import absolute_import, division, print_function

import threading
import time
from collections import deque


class SlidingWindow(object):
    def __init__(self):
        self.__m_events = {}
        self.__m_lock = threading.Lock()

    MAX_WINDOW = 86400.0  # events older than the largest supported window are pruned on write

    def hit(self, key, now=None):
        now = now or time.time()
        with self.__m_lock:
            q = self.__m_events.setdefault(key, deque())
            q.append(now)
            while q and q[0] < now - self.MAX_WINDOW:
                q.popleft()

    def count(self, key, window_seconds, now=None):
        now = now or time.time()
        with self.__m_lock:
            q = self.__m_events.get(key)
            if not q:
                return 0
            cutoff = now - window_seconds
            return sum(1 for t in reversed(q) if t >= cutoff)

    def check(self, key, rpm=0, rpd=0, now=None):
        """Return '' when allowed else 'rpm' / 'rpd'."""
        if rpm and self.count(key, 60, now) >= rpm:
            return 'rpm'
        if rpd and self.count(key, 86400, now) >= rpd:
            return 'rpd'
        return ''

    def retryAfter(self, key, rpm, now=None):
        now = now or time.time()
        with self.__m_lock:
            q = self.__m_events.get(key)
            if not q or not rpm:
                return 1
            return max(1, int(q[0] + 60 - now) + 1)


class InFlight(object):
    def __init__(self):
        self.__m_n = {}
        self.__m_lock = threading.Lock()

    def acquire(self, key, limit):
        with self.__m_lock:
            if limit and self.__m_n.get(key, 0) >= limit:
                return False
            self.__m_n[key] = self.__m_n.get(key, 0) + 1
            return True

    def release(self, key):
        with self.__m_lock:
            self.__m_n[key] = max(0, self.__m_n.get(key, 0) - 1)

    def get(self, key):
        with self.__m_lock:
            return self.__m_n.get(key, 0)


WINDOW = SlidingWindow()
IN_FLIGHT = InFlight()
