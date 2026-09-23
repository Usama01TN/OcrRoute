# coding=utf-8
"""Panel sessions: signed cookie, argon2 passwords, login lockout, CSRF double-submit."""
from __future__ import absolute_import, division, print_function

import secrets
import threading
import time

from itsdangerous import BadSignature, URLSafeTimedSerializer

from ocrroute.crypto import hashPassword, verifyPassword
from ocrroute.db.base import utcnow
from ocrroute.db.models import User
from ocrroute.db.session import sessionScope

_attempts = {}
_lock = threading.Lock()
LOCKOUT_AFTER = 8
LOCKOUT_WINDOW = 600.0


class SessionManager(object):
    def __init__(self, settings, secret):
        self.__m_settings = settings
        self.__m_serializer = URLSafeTimedSerializer(secret, salt='ocrroute-panel')

    def issue(self, username, role):
        return self.__m_serializer.dumps({'u': username, 'r': role, 'c': secrets.token_urlsafe(16)})

    def load(self, token):
        if not token:
            return None
        try:
            return self.__m_serializer.loads(token, max_age=self.__m_settings.session_max_age)
        except BadSignature:
            return None

    def csrfFor(self, session):
        return session.get('c', '')

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

    def getSerializer(self):
        """
        :return: any
        """
        return self.__m_serializer

    def setSerializer(self, serializer):
        """
        :param serializer: any
        """
        self.__m_serializer = serializer

    settings = property(fget=getSettings, fset=setSettings)
    serializer = property(fget=getSerializer, fset=setSerializer)


def tooManyAttempts(ip):
    now = time.time()
    with _lock:
        lst = [t for t in _attempts.get(ip, []) if t > now - LOCKOUT_WINDOW]
        _attempts[ip] = lst
        return len(lst) >= LOCKOUT_AFTER


def recordAttempt(ip):
    with _lock:
        _attempts.setdefault(ip, []).append(time.time())


def authenticate(username, password):
    with sessionScope() as s:
        user = s.query(User).filter(User.username == username, User.enabled.is_(True)).one_or_none()
        if user is None or not verifyPassword(user.password_hash, password):
            return None
        user.last_login_at = utcnow()
        return user


def createUser(username, password, role='admin'):
    with sessionScope() as s:
        u = User(username=username, password_hash=hashPassword(password), role=role)
        s.add(u)
        s.flush()
        return u


def userCount():
    with sessionScope() as s:
        return s.query(User).count()


def currentSession(request):
    return getattr(request.state, 'panel_session', None)
