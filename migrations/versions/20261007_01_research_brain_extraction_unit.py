"""add Research Brain Pilot ExtractionUnit layer (extraction_unit table, evidence.source_extraction_unit_id)

Revision ID: 20261007_01
Revises: 20261006_01
Create Date: 2026-10-07

Purely additive. Creates the one `extraction_unit` table named in
``migrations/research_brain_extraction_unit_table_inventory.py``'s
``RESEARCH_BRAIN_EXTRACTION_UNIT_TABLES`` set, and adds exactly one new
nullable column, `evidence.source_extraction_unit_id`. Does not touch
`M1_TABLES`, `RESEARCH_BRAIN_PILOT_TABLES`, or either of the two migrations
before this one (`20260904_02`, `20261006_01`).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app import db
import app.models  # noqa: F401 - registers the current metadata, including ExtractionUnit
from migrations.research_brain_extraction_unit_table_inventory import (
    RESEARCH_BRAIN_EXTRACTION_UNIT_TABLES,
)


revision: str = "20261007_01"
down_revision: str = "20261006_01"
branch_labels = None
depends_on = None


def _evidence_columns(bind) -> set[str]:
    inspector = sa.inspect(bind)
    return {column["name"] for column in inspector.get_columns("evidence")}


def upgrade() -> None:
    bind = op.get_bind()

    # Create exactly the frozen RESEARCH_BRAIN_EXTRACTION_UNIT_TABLES set --
    # mirrors the discipline both prior revisions already use so this
    # migration cannot silently absorb an unrelated table added to
    # app.models after this revision was written.
    db.metadata.create_all(
        bind=bind,
        tables=[
            db.metadata.tables[name]
            for name in sorted(RESEARCH_BRAIN_EXTRACTION_UNIT_TABLES)
        ],
        checkfirst=True,
    )

    if "source_extraction_unit_id" in _evidence_columns(bind):
        return

    if bind.dialect.name == "sqlite":
        bind.execute(
            sa.text(
                "ALTER TABLE evidence ADD COLUMN source_extraction_unit_id "
                "VARCHAR(36) REFERENCES extraction_unit(id)"
            )
        )
        return

    op.add_column(
        "evidence",
        sa.Column(
            "source_extraction_unit_id",
            sa.String(36),
            sa.ForeignKey("extraction_unit.id"),
            nullable=True,
        ),
    )


def downgrade() -> None:
    """Drop the one new table and the one new column."""

    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "source_extraction_unit_id" in _evidence_columns(bind):
        # SQLite refuses a plain ALTER TABLE ... DROP COLUMN when that
        # column is named in a foreign key clause within the table's own
        # definition. batch_alter_table does the standard Alembic-recommended
        # recreate-table dance SQLite requires for this; it is a transparent
        # passthrough to a plain drop_column on dialects that support it
        # directly, so this one path is correct on every dialect.
        with op.batch_alter_table("evidence") as batch_op:
            batch_op.drop_column("source_extraction_unit_id")

    for table_name in RESEARCH_BRAIN_EXTRACTION_UNIT_TABLES:
        if not inspector.has_table(table_name):
            continue
        bind.execute(sa.text(f"DROP TABLE IF EXISTS {table_name}"))
