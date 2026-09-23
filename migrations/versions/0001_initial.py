# coding=utf-8
"""initial schema - every table, including the reserved ``tools`` / ``tool_runs``.

Revision ID: 0001_initial
Revises: None
"""

from alembic import op

from ocrroute.db.base import Base
import ocrroute.db.models  # noqa: F401

revision = '0001_initial'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    Base.metadata.create_all(op.get_bind())


def downgrade():
    Base.metadata.drop_all(op.get_bind())
