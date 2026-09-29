# coding=utf-8
"""
None
"""
from __future__ import absolute_import, division, print_function

from ocrroute.db.base import utcnow
from ocrroute.db.models import Engine


def syncEngines(session, registry):
    """Upsert the discovered catalogue into the ``engines`` table. Returns the number of rows touched."""
    now = utcnow()
    count = 0
    for info in registry.all():
        row = session.get(Engine, info.id)
        if row is None:
            row = Engine(id=info.id, kind=info.kind, first_seen=now)
            session.add(row)
        row.name = info.name
        row.kind = info.kind
        row.vendor = info.vendor
        row.module = info.module
        row.available = info.available
        row.import_error = info.import_error
        row.install_hint = info.install_hint
        row.requires_key = info.requires_key
        row.supports_pdf = info.supports_pdf
        row.supports_handwriting = info.supports_handwriting
        row.supports_tables = info.supports_tables
        row.supports_overlay = info.supports_overlay
        row.languages = list(info.languages)
        row.option_schema = [o.toDict() for o in info.options]
        row.cost_model = info.cost_model
        row.unit_price = info.unit_price
        row.quality_score = info.quality_score
        row.homepage = info.homepage
        row.docs_url = info.docs_url
        row.last_seen = now
        count += 1
    return count


def seedProviders(session, registry):
    """
    Create one enabled provider per **available** engine that has none yet, so every detected OCR provider is
    usable from routes, the panel and the desktop without manual setup. API engines still need a credential
    before they can succeed; they are seeded so the operator only has to paste a key.

    :param session: Session
    :param registry: EngineRegistry
    :return: list[str]  labels of the providers created
    """
    from ocrroute.db.models import Provider

    existing = set(row[0] for row in session.query(Provider.engine_id).all())
    created = []
    for info in registry.available():
        if info.getId() in existing:
            continue
        label = '{}-{}'.format(info.getId().lower(), 'local' if info.getKind() == 'local' else 'cloud')
        session.add(Provider(engine_id=info.getId(), label=label, enabled=True,
                             priority=50 if info.getKind() == 'local' else 100,
                             notes='Created automatically from the AioOCR catalogue.'))
        created.append(label)
    return created


seed_providers = seedProviders
