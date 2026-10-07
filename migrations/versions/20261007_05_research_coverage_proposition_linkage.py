"""add Research Coverage Proposition Linkage tables (research_proposition, proposition_stage_type, proposition_link)

Revision ID: 20261007_05
Revises: 20261007_04
Create Date: 2026-10-07

Purely additive. Creates exactly the three Slice 4 "Proposition Linkage"
tables named in
``migrations/research_coverage_proposition_linkage_table_inventory.py``'s
``RESEARCH_COVERAGE_PROPOSITION_LINKAGE_TABLES`` set, using the same
``db.metadata`` / ``checkfirst`` approach every prior revision in this
chain already uses for its own frozen set. This revision does not touch
``M1_TABLES``, ``RESEARCH_BRAIN_PILOT_TABLES``,
``RESEARCH_BRAIN_EXTRACTION_UNIT_TABLES``,
``RESEARCH_COVERAGE_FOUNDATION_TABLES``,
``RESEARCH_COVERAGE_CANDIDATE_FINDINGS_TABLES``, or
``RESEARCH_COVERAGE_FACT_DERIVATION_TABLES`` -- it does not alter any
table from those six frozen sets in any way, and it does not reopen any
of the six prior revisions.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app import db
import app.models  # noqa: F401 - registers the current metadata, including research_coverage
from migrations.research_coverage_proposition_linkage_table_inventory import (
    RESEARCH_COVERAGE_PROPOSITION_LINKAGE_TABLES,
)


revision: str = "20261007_05"
down_revision: str = "20261007_04"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()

    db.metadata.create_all(
        bind=bind,
        tables=[
            db.metadata.tables[name]
            for name in sorted(RESEARCH_COVERAGE_PROPOSITION_LINKAGE_TABLES)
        ],
        checkfirst=True,
    )


def downgrade() -> None:
    """Drop exactly the three Proposition Linkage tables."""

    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # Reverse SQLAlchemy's dependency-sorted order so proposition_link
    # (which FKs research_proposition and proposition_stage_type) drops
    # before either of those.
    proposition_tables = [
        table
        for table in reversed(db.metadata.sorted_tables)
        if table.name in RESEARCH_COVERAGE_PROPOSITION_LINKAGE_TABLES
    ]

    for table in proposition_tables:
        if not inspector.has_table(table.name):
            continue
        bind.execute(sa.text(f"DROP TABLE IF EXISTS {table.name}"))
