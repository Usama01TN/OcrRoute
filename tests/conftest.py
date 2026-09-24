# coding=utf-8
"""Shared fixtures: isolated OCRROUTE_HOME, a fake engine registered only in tests, an app client with an admin key."""

import io
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))

os.environ['OCRROUTE_JSON_LOGS'] = '1'
os.environ['OCRROUTE_LOG_LEVEL'] = 'ERROR'


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv('OCRROUTE_HOME', str(tmp_path / 'home'))
    from ocrroute.config import resetSettings
    from ocrroute.db import session as db_session
    from ocrroute.runtime import context

    resetSettings()
    db_session.dispose()
    context.resetContext()
    yield tmp_path / 'home'
    db_session.dispose()
    context.resetContext()
    resetSettings()


@pytest.fixture
def sample_png():
    from PIL import Image, ImageDraw

    im = Image.new('RGB', (300, 100), 'white')
    ImageDraw.Draw(im).text((10, 40), 'hello world', fill='black')
    buf = io.BytesIO()
    im.save(buf, format='PNG')
    return buf.getvalue()


@pytest.fixture
def ctx():
    """App context with the fake engines registered (no live paid APIs, no Tesseract dependency)."""
    from ocrroute.runtime.context import buildContext

    c = buildContext()
    from fixtures.fakeocr import FakeAuthFail, FakeEmpty, FakeOcr, FakeSlow, FakeUnavailable

    from ocrroute.db.repo.engines import syncEngines
    from ocrroute.db.session import sessionScope

    for cls, meta in (
        (FakeOcr, {'quality_score': 70}),
        (FakeAuthFail, {'requires_key': True, 'kind': 'api', 'cost_model': 'per_request', 'unit_price': 1.0}),
        (FakeEmpty, {}),
        (FakeSlow, {}),
        (FakeUnavailable, {}),
    ):
        info = c.registry.register(cls, kind=meta.pop('kind', 'local'), **meta)
        if cls is FakeUnavailable:
            info.available = False
            info.import_error = "No module named 'nothing'"
            info.install_hint = 'pip install nothing'
    with sessionScope() as s:
        syncEngines(s, c.registry)
    return c


@pytest.fixture
def client(ctx):
    from fastapi.testclient import TestClient

    from ocrroute.api.app import createApp

    app = createApp(include_panel=True)
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


@pytest.fixture
def admin_headers(client):
    from ocrroute.crypto import hashApiKey, newApiKey
    from ocrroute.db.models import ApiKey
    from ocrroute.db.session import sessionScope

    raw = newApiKey()
    with sessionScope() as s:
        s.add(ApiKey(name='test-admin', key_hash=hashApiKey(raw), key_prefix=raw[:10], scopes=['admin']))
    return {'Authorization': 'Bearer {}'.format(raw)}
