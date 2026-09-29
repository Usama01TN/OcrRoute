# coding=utf-8
"""
Panel users management: roles, enable/disable, password reset, self-service password change.

Roles
- ``admin``: everything (users, API keys, settings, server control).
- ``operator``: run OCR and manage providers, credentials, routes and engines (``manage`` scope).
- ``viewer``: read-only.
"""
from __future__ import absolute_import, division, print_function

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ocrroute.api.deps import getDb
from ocrroute.api.security import ROLE_SCOPES, requireKey
from ocrroute.crypto import hashPassword, verifyPassword
from ocrroute.db.models import ApiKey, AuditLog, User
from ocrroute.errors import BadInput, Conflict, Forbidden, NotFound

router = APIRouter(prefix='/users', tags=['users'])
admin = requireKey('admin')
ROLES = ('admin', 'operator', 'viewer')


def userToDict(u):
    """
    :param u: User
    :return: dict
    """
    return {'id': u.id, 'username': u.username, 'role': u.role, 'enabled': u.enabled, 'last_login_at': u.last_login_at,
            'created_at': u.created_at, 'scopes': ROLE_SCOPES.get(u.role, [])}


def _audit(db, request, key, action, target, detail=None):
    db.add(AuditLog(actor=key.name, action=action, target_type='user', target_id=target, detail=detail or {},
                    ip=request.client.host if request.client else ''))


def _lastAdminGuard(db, user, becoming_role=None, becoming_enabled=None):
    """Refuse changes that would leave no enabled administrator."""
    if user.role != 'admin' or not user.enabled:
        return
    if (becoming_role not in (None, 'admin')) or becoming_enabled is False:
        others = db.query(User).filter(User.role == 'admin', User.enabled.is_(True), User.id != user.id).count()
        if others == 0:
            raise Conflict('This is the last enabled administrator')


class UserIn(BaseModel):
    username: str = Field(min_length=2, max_length=64, pattern=r'^[A-Za-z0-9._@-]+$')
    password: str = Field(min_length=8)
    role: str = 'operator'


class UserPatch(BaseModel):
    role: str | None = None
    enabled: bool | None = None
    password: str | None = Field(default=None, min_length=8)


class PasswordChange(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8)


@router.get('')
def listUsers(db: Session = Depends(getDb), key: ApiKey = Depends(admin)):
    return {'items': [userToDict(u) for u in db.query(User).order_by(User.username).all()], 'roles': ROLE_SCOPES}


@router.post('', status_code=201)
def createUser(body: UserIn, request: Request, db: Session = Depends(getDb), key: ApiKey = Depends(admin)):
    if body.role not in ROLES:
        raise BadInput('role must be one of {}'.format(', '.join(ROLES)))
    if db.query(User).filter(User.username == body.username).first():
        raise Conflict('username already exists')
    u = User(username=body.username, password_hash=hashPassword(body.password), role=body.role)
    db.add(u)
    db.flush()
    _audit(db, request, key, 'user.create', u.id, {'username': u.username, 'role': u.role})
    db.commit()
    return userToDict(u)


@router.patch('/{uid}')
def patchUser(uid: str, body: UserPatch, request: Request, db: Session = Depends(getDb), key: ApiKey = Depends(admin)):
    u = db.get(User, uid)
    if u is None:
        raise NotFound('user not found')
    if body.role is not None and body.role not in ROLES:
        raise BadInput('role must be one of {}'.format(', '.join(ROLES)))
    _lastAdminGuard(db, u, body.role, body.enabled)
    changes = {}
    if body.role is not None:
        u.role = body.role
        changes['role'] = body.role
    if body.enabled is not None:
        u.enabled = body.enabled
        changes['enabled'] = body.enabled
    if body.password:
        u.password_hash = hashPassword(body.password)
        changes['password'] = 'reset'
    _audit(db, request, key, 'user.update', uid, changes)
    db.commit()
    return userToDict(u)


@router.delete('/{uid}', status_code=204)
def deleteUser(uid: str, request: Request, db: Session = Depends(getDb), key: ApiKey = Depends(admin)):
    from fastapi import Response

    u = db.get(User, uid)
    if u is None:
        raise NotFound('user not found')
    _lastAdminGuard(db, u, becoming_enabled=False)
    if key.name == 'panel:{}'.format(u.username):
        raise Forbidden('You cannot delete your own account while signed in with it')
    db.delete(u)
    _audit(db, request, key, 'user.delete', uid, {'username': u.username})
    db.commit()
    return Response(status_code=204)


@router.get('/me')
def me(request: Request, db: Session = Depends(getDb), key: ApiKey = Depends(requireKey('ocr:read'))):
    username = getattr(request.state, 'panel_user', None)
    if not username:
        return {'kind': 'api_key', 'name': key.name, 'scopes': key.scopes}
    u = db.query(User).filter(User.username == username).first()
    return {'kind': 'user', **(userToDict(u) if u else {'username': username})}


@router.post('/me/password')
def changeMyPassword(body: PasswordChange, request: Request, db: Session = Depends(getDb),
                     key: ApiKey = Depends(requireKey('ocr:read'))):
    username = getattr(request.state, 'panel_user', None)
    if not username:
        raise Forbidden('Only signed-in panel users can change a password here')
    u = db.query(User).filter(User.username == username).first()
    if u is None or not verifyPassword(u.password_hash, body.current_password):
        raise BadInput('Current password is incorrect')
    u.password_hash = hashPassword(body.new_password)
    _audit(db, request, key, 'user.password_change', u.id)
    db.commit()
    return {'ok': True}
