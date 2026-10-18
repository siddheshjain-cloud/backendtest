"""add company.public_research_status (anonymous public-access proposal §2.2)

Revision ID: dce47b38cde9
Revises: 1848bf398f95
Create Date: 2026-10-19

Purely additive: one new, non-nullable, defaulted column on the existing
``company`` table. No other table is touched, and no existing row's
visibility changes as a result of this migration alone -- every existing
company row backfills to ``NOT_PUBLIC`` via the column's own
``server_default``, which is the fail-closed state (nothing becomes
anonymously visible by just running this migration; a separate,
explicit admin action is required per company, per the owner-approved
public-access proposal).

Idempotent and uses ``batch_alter_table`` for SQLite ALTER TABLE support,
mirroring the existing pattern in
``migrations/versions/20261008_01_phase3_slice_a_contracts.py``.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision: str = "dce47b38cde9"
down_revision: str = "1848bf398f95"
branch_labels = None
depends_on = None

_TABLE_NAME = "company"
_COLUMN_NAME = "public_research_status"
_ENUM_NAME = "company_public_research_status"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_columns = {
        column["name"] for column in inspector.get_columns(_TABLE_NAME)
    }
    if _COLUMN_NAME in existing_columns:
        return

    with op.batch_alter_table(_TABLE_NAME, recreate="auto") as batch_op:
        batch_op.add_column(
            sa.Column(
                _COLUMN_NAME,
                sa.Enum(
                    "NOT_PUBLIC",
                    "PUBLISHED",
                    name=_ENUM_NAME,
                    native_enum=False,
                ),
                nullable=False,
                server_default="NOT_PUBLIC",
            )
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_columns = {
        column["name"] for column in inspector.get_columns(_TABLE_NAME)
    }
    if _COLUMN_NAME not in existing_columns:
        return

    with op.batch_alter_table(_TABLE_NAME, recreate="auto") as batch_op:
        batch_op.drop_column(_COLUMN_NAME)
