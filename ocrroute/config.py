# coding=utf-8
"""Runtime settings. Every value can be overridden with an ``OCRROUTE_*`` env var."""
from __future__ import absolute_import, division, print_function

import os
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

FORBIDDEN_PORTS = {20128}  # never collide with other gateways on the same host


def defaultHome() -> Path:
    return Path(os.environ.get('OCRROUTE_HOME', Path.home() / '.ocrroute')).expanduser()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix='OCRROUTE_', env_file='.env', extra='ignore')

    home: Path = Field(default_factory=defaultHome)
    host: str = '127.0.0.1'
    port: int = 20256
    panel_port: int | None = None  # split-port mode when set (default 20257 via CLI)
    base_url: str = ''  # public URL for links in webhooks/exports; derived when empty

    secret_key: str = ''  # Fernet/session key material; generated into home/secret.key when empty
    session_cookie: str = 'ocrroute_session'
    session_max_age: int = 60 * 60 * 12

    db_url: str = ''  # sqlite:///<home>/ocrroute.db when empty
    artifacts_dir: Path | None = None
    store_inputs: bool = False
    privacy_mode: bool = False
    log_retention_days: int = 30
    cache_ttl_seconds: int = 3600
    cache_max_entries: int = 5000

    max_upload_bytes: int = 25 * 1024 * 1024
    max_pixels: int = 60000000
    max_pages: int = 200
    url_allowlist: list[str] = []
    url_denylist: list[str] = []
    allow_private_urls: bool = False

    worker_threads: int = 8
    queue_size: int = 200
    default_timeout: int = 60
    total_deadline_ms: int = 120000
    breaker_threshold: int = 5
    breaker_cooldown_seconds: int = 120

    # cluster sync (see ocrroute/sync.py): off | leader | follower. Local to each server, never synchronised.
    sync_role: str = 'off'
    sync_token: str = ''  # shared secret, same on the leader and every follower (>= 24 chars)
    sync_leader_url: str = ''  # followers: the leader's base URL, e.g. https://ocr-1.example.com
    sync_interval_seconds: int = 30
    auto_seed_providers: bool = True  # create a provider row for every available engine automatically
    engine_rescan_minutes: int = 10  # 0 disables periodic detection of new/repaired engines
    default_language: str = "en"  # UI language: en | fr | es | de | ar

    cors_origins: list[str] = []
    log_level: str = 'INFO'
    json_logs: bool = False

    @property
    def databaseUrl(self) -> str:
        return self.db_url or 'sqlite:///{}'.format(self.home / 'ocrroute.db')

    @property
    def artifactRoot(self) -> Path:
        return self.artifacts_dir or (self.home / 'artifacts')

    @property
    def secretFile(self) -> Path:
        return self.home / 'secret.key'

    def ensureDirs(self) -> None:
        self.home.mkdir(parents=True, exist_ok=True)
        self.artifactRoot.mkdir(parents=True, exist_ok=True)
        (self.home / 'uploads').mkdir(parents=True, exist_ok=True)
        (self.home / 'backups').mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def getSettings() -> Settings:
    s = Settings()
    s.ensureDirs()
    return s


def resetSettings() -> None:
    getSettings.cache_clear()
