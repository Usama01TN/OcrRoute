# coding=utf-8
"""
None
"""

from alembic import context
from sqlalchemy import engine_from_config, pool

from ocrroute.config import getSettings
from ocrroute.db.base import Base
import ocrroute.db.models  # noqa: F401 - register models

config = context.config
config.set_main_option('sqlalchemy.url', getSettings().databaseUrl)
target_metadata = Base.metadata


def run_migrations_offline():
    context.configure(
        url=config.get_main_option('sqlalchemy.url'),
        target_metadata=target_metadata,
        literal_binds=True,
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}), prefix='sqlalchemy.', poolclass=pool.NullPool
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, render_as_batch=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
