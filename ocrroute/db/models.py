# coding=utf-8
"""SQLAlchemy 2.0 models - one SQLite file, UTC ISO-8601 text timestamps, ULID text ids."""
from __future__ import absolute_import, division, print_function

from sqlalchemy import JSON, Boolean, Float, ForeignKey, Index, Integer, LargeBinary, String, Text
from sqlalchemy.orm import mapped_column, relationship

from ocrroute.db.base import Base, utcnow
from ocrroute.ids import newId


def _id():
    return mapped_column(String(26), primary_key=True, default=newId)


def _ts():
    return mapped_column(String(32), default=utcnow, nullable=False)


class Engine(Base):
    __tablename__ = 'engines'
    id = mapped_column(String(64), primary_key=True)
    name = mapped_column(String(128), default='')
    kind = mapped_column(String(8))
    vendor = mapped_column(String(128), default='')
    module = mapped_column(String(255), default='')
    available = mapped_column(Boolean, default=False)
    import_error = mapped_column(Text, default='')
    install_hint = mapped_column(Text, default='')
    requires_key = mapped_column(Boolean, default=False)
    supports_pdf = mapped_column(Boolean, default=False)
    supports_handwriting = mapped_column(Boolean, default=False)
    supports_tables = mapped_column(Boolean, default=False)
    supports_overlay = mapped_column(Boolean, default=True)
    languages = mapped_column(JSON, default=list)
    option_schema = mapped_column(JSON, default=list)
    cost_model = mapped_column(String(16), default='free')
    unit_price = mapped_column(Float, default=0.0)
    quality_score = mapped_column(Integer, default=50)
    homepage = mapped_column(String(255), default='')
    docs_url = mapped_column(String(255), default='')
    enabled = mapped_column(Boolean, default=True)
    first_seen = _ts()
    last_seen = _ts()


class Provider(Base):
    __tablename__ = 'providers'
    id = _id()
    engine_id = mapped_column(ForeignKey('engines.id'), index=True)
    label = mapped_column(String(128))
    enabled = mapped_column(Boolean, default=True)
    priority = mapped_column(Integer, default=100)
    weight = mapped_column(Integer, default=1)
    endpoint = mapped_column(String(512), default='')
    model = mapped_column(String(128), default='')
    language = mapped_column(String(64), default='')
    timeout = mapped_column(Integer, default=60)
    retries = mapped_column(Integer, default=2)
    options = mapped_column(JSON, default=dict)
    proxy = mapped_column(JSON, default=dict)
    concurrency_limit = mapped_column(Integer, default=4)
    rpm_limit = mapped_column(Integer, default=0)
    rpd_limit = mapped_column(Integer, default=0)
    monthly_budget_cents = mapped_column(Float, default=0.0)
    health = mapped_column(String(16), default='unknown')
    health_checked_at = mapped_column(String(32), default='')
    consecutive_failures = mapped_column(Integer, default=0)
    circuit_open_until = mapped_column(String(32), default='')
    notes = mapped_column(Text, default='')
    created_at = _ts()
    updated_at = _ts()

    engine = relationship('Engine')
    credentials = relationship(
        'Credential', back_populates='provider', cascade='all, delete-orphan', order_by='Credential.order_index'
    )


class Credential(Base):
    __tablename__ = 'credentials'
    id = _id()
    provider_id = mapped_column(ForeignKey('providers.id', ondelete='CASCADE'), index=True)
    alias = mapped_column(String(64), default='')
    secret_enc = mapped_column(LargeBinary)
    enabled = mapped_column(Boolean, default=True)
    order_index = mapped_column(Integer, default=0)
    last_used_at = mapped_column(String(32), default='')
    success_count = mapped_column(Integer, default=0)
    failure_count = mapped_column(Integer, default=0)
    exhausted_until = mapped_column(String(32), default='')
    created_at = _ts()

    provider = relationship('Provider', back_populates='credentials')


class Route(Base):
    __tablename__ = 'routes'
    id = _id()
    name = mapped_column(String(64), unique=True)
    description = mapped_column(Text, default='')
    strategy = mapped_column(String(32), default='priority')
    enabled = mapped_column(Boolean, default=True)
    is_default = mapped_column(Boolean, default=False)
    stop_condition = mapped_column(JSON, default=dict)
    max_attempts = mapped_column(Integer, default=5)
    total_deadline_ms = mapped_column(Integer, default=120000)
    cache_ttl_seconds = mapped_column(Integer, default=3600)
    tool_chain = mapped_column(JSON, default=list)  # reserved (Tools), always empty
    created_at = _ts()
    updated_at = _ts()

    members = relationship(
        'RouteMember', back_populates='route', cascade='all, delete-orphan', order_by='RouteMember.order_index'
    )


class RouteMember(Base):
    __tablename__ = 'route_members'
    id = _id()
    route_id = mapped_column(ForeignKey('routes.id', ondelete='CASCADE'), index=True)
    provider_id = mapped_column(ForeignKey('providers.id', ondelete='CASCADE'), index=True)
    order_index = mapped_column(Integer, default=0)
    weight = mapped_column(Integer, default=1)
    enabled = mapped_column(Boolean, default=True)
    condition = mapped_column(JSON, default=dict)
    option_overrides = mapped_column(JSON, default=dict)

    route = relationship('Route', back_populates='members')
    provider = relationship('Provider')


class ApiKey(Base):
    __tablename__ = 'api_keys'
    id = _id()
    name = mapped_column(String(64))
    key_hash = mapped_column(String(64), unique=True, index=True)
    key_prefix = mapped_column(String(16))
    scopes = mapped_column(JSON, default=lambda: ['ocr:read', 'ocr:write'])
    route_id = mapped_column(ForeignKey('routes.id'), nullable=True)
    rpm_limit = mapped_column(Integer, default=0)
    rpd_limit = mapped_column(Integer, default=0)
    monthly_budget_cents = mapped_column(Float, default=0.0)
    enabled = mapped_column(Boolean, default=True)
    expires_at = mapped_column(String(32), default='')
    last_used_at = mapped_column(String(32), default='')
    created_at = _ts()


class Run(Base):
    __tablename__ = 'runs'
    id = _id()
    api_key_id = mapped_column(String(26), nullable=True, index=True)
    route_id = mapped_column(String(26), nullable=True, index=True)
    route_name = mapped_column(String(64), default='')
    requested_engine = mapped_column(String(64), default='')
    winning_engine = mapped_column(String(64), default='', index=True)
    winning_provider = mapped_column(String(128), default='')
    strategy = mapped_column(String(32), default='')
    input_kind = mapped_column(String(16), default='')
    mime = mapped_column(String(64), default='')
    bytes = mapped_column(Integer, default=0)
    image_sha256 = mapped_column(String(64), default='', index=True)
    page_count = mapped_column(Integer, default=1)
    language = mapped_column(String(64), default='')
    status = mapped_column(String(16), default='queued', index=True)
    winning_attempt_id = mapped_column(String(26), default='')
    attempt_count = mapped_column(Integer, default=0)
    chars = mapped_column(Integer, default=0)
    lines = mapped_column(Integer, default=0)
    words = mapped_column(Integer, default=0)
    mean_confidence = mapped_column(Float, default=0.0)
    duration_ms = mapped_column(Integer, default=0)
    cost_cents = mapped_column(Float, default=0.0)
    cache_hit = mapped_column(Boolean, default=False)
    degraded = mapped_column(Boolean, default=False)
    client_ip = mapped_column(String(64), default='')
    user_agent = mapped_column(String(255), default='')
    idempotency_key = mapped_column(String(128), default='', index=True)
    error_code = mapped_column(String(32), default='')
    error_message = mapped_column(Text, default='')
    result_json = mapped_column(JSON, nullable=True)
    routing_json = mapped_column(JSON, nullable=True)
    metadata_json = mapped_column(JSON, default=dict)
    origin = mapped_column(String(16), default='api')
    created_at = _ts()
    finished_at = mapped_column(String(32), default='')

    attempts = relationship(
        'Attempt', back_populates='run', cascade='all, delete-orphan', order_by='Attempt.order_index'
    )
    artifacts = relationship('Artifact', back_populates='run', cascade='all, delete-orphan')


Index('ix_runs_created_at', Run.created_at)


class Attempt(Base):
    __tablename__ = 'attempts'
    id = _id()
    run_id = mapped_column(ForeignKey('runs.id', ondelete='CASCADE'), index=True)
    provider_id = mapped_column(String(26), default='', index=True)
    provider_label = mapped_column(String(128), default='')
    engine_id = mapped_column(String(64), default='')
    credential_id = mapped_column(String(26), default='')
    order_index = mapped_column(Integer, default=0)
    status = mapped_column(String(16), default='failed')
    duration_ms = mapped_column(Integer, default=0)
    http_status = mapped_column(Integer, default=0)
    error_code = mapped_column(String(32), default='')
    error_type = mapped_column(String(64), default='')
    error_message = mapped_column(Text, default='')
    retries_used = mapped_column(Integer, default=0)
    chars = mapped_column(Integer, default=0)
    cost_cents = mapped_column(Float, default=0.0)
    started_at = _ts()
    finished_at = mapped_column(String(32), default='')

    run = relationship('Run', back_populates='attempts')


Index('ix_attempts_provider_started', Attempt.provider_id, Attempt.started_at)


class Artifact(Base):
    __tablename__ = 'artifacts'
    id = _id()
    run_id = mapped_column(ForeignKey('runs.id', ondelete='CASCADE'), index=True)
    kind = mapped_column(String(16))
    path = mapped_column(String(512))
    bytes = mapped_column(Integer, default=0)
    sha256 = mapped_column(String(64), default='')
    created_at = _ts()

    run = relationship('Run', back_populates='artifacts')


class CacheEntry(Base):
    __tablename__ = 'cache'
    cache_key = mapped_column(String(64), primary_key=True)
    run_id = mapped_column(String(26), default='')
    result_json = mapped_column(JSON)
    routing_json = mapped_column(JSON, default=dict)
    hits = mapped_column(Integer, default=0)
    bytes = mapped_column(Integer, default=0)
    created_at = _ts()
    expires_at = mapped_column(String(32), index=True)


class UsageDaily(Base):
    __tablename__ = 'usage_daily'
    day = mapped_column(String(10), primary_key=True)
    engine_id = mapped_column(String(64), primary_key=True, default='')
    provider_id = mapped_column(String(26), primary_key=True, default='')
    api_key_id = mapped_column(String(26), primary_key=True, default='')
    runs = mapped_column(Integer, default=0)
    successes = mapped_column(Integer, default=0)
    failures = mapped_column(Integer, default=0)
    pages = mapped_column(Integer, default=0)
    chars = mapped_column(Integer, default=0)
    cost_cents = mapped_column(Float, default=0.0)
    p50_ms = mapped_column(Integer, default=0)
    p95_ms = mapped_column(Integer, default=0)


class Job(Base):
    __tablename__ = 'jobs'
    id = _id()
    name = mapped_column(String(128), default='')
    route_id = mapped_column(String(26), nullable=True)
    api_key_id = mapped_column(String(26), nullable=True)
    status = mapped_column(String(16), default='queued', index=True)
    total = mapped_column(Integer, default=0)
    done = mapped_column(Integer, default=0)
    failed = mapped_column(Integer, default=0)
    options = mapped_column(JSON, default=dict)
    webhook_url = mapped_column(String(512), default='')
    created_at = _ts()
    finished_at = mapped_column(String(32), default='')

    items = relationship('JobItem', back_populates='job', cascade='all, delete-orphan', order_by='JobItem.order_index')


class JobItem(Base):
    __tablename__ = 'job_items'
    id = _id()
    job_id = mapped_column(ForeignKey('jobs.id', ondelete='CASCADE'), index=True)
    source = mapped_column(String(512), default='')
    run_id = mapped_column(String(26), default='')
    status = mapped_column(String(16), default='queued')
    error_message = mapped_column(Text, default='')
    order_index = mapped_column(Integer, default=0)

    job = relationship('Job', back_populates='items')


class Setting(Base):
    __tablename__ = 'settings'
    key = mapped_column(String(64), primary_key=True)
    value_json = mapped_column(JSON)
    updated_at = _ts()


class AuditLog(Base):
    __tablename__ = 'audit_log'
    id = _id()
    actor = mapped_column(String(64), default='')
    action = mapped_column(String(64))
    target_type = mapped_column(String(32), default='')
    target_id = mapped_column(String(64), default='')
    detail = mapped_column(JSON, default=dict)
    ip = mapped_column(String(64), default='')
    created_at = _ts()


class User(Base):
    __tablename__ = 'users'
    id = _id()
    username = mapped_column(String(64), unique=True)
    password_hash = mapped_column(String(255))
    role = mapped_column(String(16), default='admin')
    totp_secret_enc = mapped_column(LargeBinary, nullable=True)
    enabled = mapped_column(Boolean, default=True)
    last_login_at = mapped_column(String(32), default='')
    created_at = _ts()


# ---------------------------------------------------------------- reserved: Tools (§12) - created, never used
class Tool(Base):
    __tablename__ = 'tools'
    id = _id()
    name = mapped_column(String(64), unique=True)
    version = mapped_column(String(32), default='')
    enabled = mapped_column(Boolean, default=False)
    options_json = mapped_column(JSON, default=dict)
    installed_at = _ts()


class ToolRun(Base):
    __tablename__ = 'tool_runs'
    id = _id()
    run_id = mapped_column(String(26), index=True)
    tool_id = mapped_column(ForeignKey('tools.id'), index=True)
    status = mapped_column(String(16), default='queued')
    duration_ms = mapped_column(Integer, default=0)
    error_message = mapped_column(Text, default='')
    output_ref = mapped_column(String(512), default='')
    created_at = _ts()


class SchemaMeta(Base):
    __tablename__ = 'schema_meta'
    key = mapped_column(String(32), primary_key=True)
    value = mapped_column(String(64))
