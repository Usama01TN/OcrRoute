# coding=utf-8
"""Secret store: Fernet encryption for credentials, hashing for API keys and passwords."""
from __future__ import absolute_import, division, print_function

import base64
from ocrroute.compat.py23 import raiseFrom
import hashlib
import hmac
import os
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from cryptography.fernet import Fernet, InvalidToken

_ph = PasswordHasher()


def loadOrCreateKey(secretFile, provided=''):
    if provided:
        raw = hashlib.sha256(provided.encode()).digest()
        return base64.urlsafe_b64encode(raw)
    if secretFile.exists():
        return secretFile.read_bytes().strip()
    key = Fernet.generate_key()
    secretFile.parent.mkdir(parents=True, exist_ok=True)
    secretFile.write_bytes(key)
    try:
        os.chmod(secretFile, 0o600)
    except OSError:
        pass
    return key


class SecretStore(object):
    def __init__(self, key):
        self.__m_fernet = Fernet(key)
        self.__m_key = key

    def encrypt(self, plaintext):
        return self.__m_fernet.encrypt(plaintext.encode())

    def decrypt(self, token):
        try:
            return self.__m_fernet.decrypt(bytes(token)).decode()
        except InvalidToken as exc:  # pragma: no cover
            raiseFrom(ValueError('Secret cannot be decrypted with the current OCRROUTE_SECRET_KEY'), exc)
    @property
    def sessionSecret(self):
        return hashlib.sha256(b'session:' + self.__m_key).hexdigest()


def mask(secret, keep=4):
    if not secret:
        return ''
    return '…' + secret[-keep:] if len(secret) > keep else '***'


def newApiKey():
    return 'ocrr_' + secrets.token_urlsafe(24).replace('-', 'x').replace('_', 'y')[:32]


def hashApiKey(key):
    return hashlib.sha256(key.encode()).hexdigest()


def keysEqual(a, b):
    return hmac.compare_digest(a, b)


def hashPassword(password):
    return _ph.hash(password)


def verifyPassword(hashed, password):
    try:
        return _ph.verify(hashed, password)
    except VerifyMismatchError:
        return False
    except Exception:
        return False
