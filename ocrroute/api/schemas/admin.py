# coding=utf-8
"""
None
"""
from __future__ import absolute_import, division, print_function

from typing import Any

from pydantic import BaseModel, Field


class ProviderIn(BaseModel):
    engine_id: str
    label: str
    enabled: bool = True
    priority: int = 100
    weight: int = 1
    endpoint: str = ''
    model: str = ''
    language: str = ''
    timeout: int = 60
    retries: int = 2
    options: dict[str, Any] = Field(default_factory=dict)
    proxy: dict[str, Any] = Field(default_factory=dict)
    concurrency_limit: int = 4
    rpm_limit: int = 0
    rpd_limit: int = 0
    monthly_budget_cents: float = 0.0
    notes: str = ''


class ProviderPatch(BaseModel):
    label: str | None = None
    enabled: bool | None = None
    priority: int | None = None
    weight: int | None = None
    endpoint: str | None = None
    model: str | None = None
    language: str | None = None
    timeout: int | None = None
    retries: int | None = None
    options: dict[str, Any] | None = None
    proxy: dict[str, Any] | None = None
    concurrency_limit: int | None = None
    rpm_limit: int | None = None
    rpd_limit: int | None = None
    monthly_budget_cents: float | None = None
    notes: str | None = None


class CredentialIn(BaseModel):
    provider_id: str
    secret: str = Field(min_length=1)
    alias: str = ''
    enabled: bool = True
    order_index: int = 0


class RouteMemberIn(BaseModel):
    provider_id: str
    order_index: int = 0
    weight: int = 1
    enabled: bool = True
    condition: dict[str, Any] = Field(default_factory=dict)
    option_overrides: dict[str, Any] = Field(default_factory=dict)


class RouteIn(BaseModel):
    name: str = Field(pattern=r'^[a-z0-9][a-z0-9_-]{0,63}$')
    description: str = ''
    strategy: str = 'priority'
    enabled: bool = True
    is_default: bool = False
    stop_condition: dict[str, Any] = Field(default_factory=dict)
    max_attempts: int = 5
    total_deadline_ms: int = 120000
    cache_ttl_seconds: int = 3600
    members: list[RouteMemberIn] = Field(default_factory=list)


class RoutePatch(BaseModel):
    description: str | None = None
    strategy: str | None = None
    enabled: bool | None = None
    is_default: bool | None = None
    stop_condition: dict[str, Any] | None = None
    max_attempts: int | None = None
    total_deadline_ms: int | None = None
    cache_ttl_seconds: int | None = None
    members: list[RouteMemberIn] | None = None


class SimulateIn(BaseModel):
    route: str = ''
    engine: str = ''
    strategy: str = ''
    mime: str = 'image/png'
    width: int = 1200
    height: int = 800
    page_count: int = 1
    language: list[str] = ['en']
    handwriting: bool = False
    tables: bool = False
    sensitive: bool = False
    offline: bool = False
    max_cost_cents: float | None = None


class ApiKeyIn(BaseModel):
    name: str
    scopes: list[str] = ['ocr:read', 'ocr:write']
    route_id: str | None = None
    rpm_limit: int = 0
    rpd_limit: int = 0
    monthly_budget_cents: float = 0.0
    expires_at: str = ''


class ApiKeyPatch(BaseModel):
    name: str | None = None
    scopes: list[str] | None = None
    route_id: str | None = None
    rpm_limit: int | None = None
    rpd_limit: int | None = None
    monthly_budget_cents: float | None = None
    enabled: bool | None = None
    expires_at: str | None = None


class SettingsPatch(BaseModel):
    values: dict[str, Any]


class BatchIn(BaseModel):
    name: str = ''
    sources: list[str] = Field(default_factory=list, description='URLs or server-local paths')
    route: str = ''
    engine: str = ''
    language: list[str] = ['en']
    output: list[str] = ['json', 'text']
    options: dict[str, Any] = Field(default_factory=dict)
    webhook_url: str = ''
