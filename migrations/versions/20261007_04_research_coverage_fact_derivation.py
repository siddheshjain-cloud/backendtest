"""add Research Coverage Fact Derivation tables (fact_derivation, fact_derivation_input)

Revision ID: 20261007_04
Revises: 20261007_03
Create Date: 2026-10-07

Purely additive. Creates exactly the two Slice 3 "Fact Derivation" tables
named in
``migrations/research_coverage_fact_derivation_table_inventory.py``'s
``RESEARCH_COVERAGE_FACT_DERIVATION_TABLES`` set, using the same
``db.metadata`` / ``checkfirst`` approach every prior revision in this
chain already uses for its own frozen set. This revision does not touch
``M1_TABLES``, ``RESEARCH_BRAIN_PILOT_TABLES``,
``RESEARCH_BRAIN_EXTRACTION_UNIT_TABLES``,
``RESEARCH_COVERAGE_FOUNDATION_TABLES``, or
``RESEARCH_COVERAGE_CANDIDATE_FINDINGS_TABLES`` -- it does not alter
`document`, `company`, `ticker`, `extraction_run`, `extraction_unit`,
`evidence`, `extracted_fact`, `fact_evidence`, or any Slice 1-2 table in
any way, and it does not reopen any of the five prior revisions.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app import db
import app.models  # noqa: F401 - registers the current metadata, including research_coverage
from migrations.research_coverage_fact_derivation_table_inventory import (
    RESEARCH_COVERAGE_FACT_DERIVATION_TABLES,
)


revision: str = "20261007_04"
down_revision: str = "20261007_03"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()

    db.metadata.create_all(
        bind=bind,
        tables=[
            db.metadata.tables[name]
            for name in sorted(RESEARCH_COVERAGE_FACT_DERIVATION_TABLES)
        ],
        checkfirst=True,
    )


def downgrade() -> None:
    """Drop exactly the two Fact Derivation tables."""

    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # Reverse SQLAlchemy's dependency-sorted order so fact_derivation_input
    # (which FKs fact_derivation) drops before fact_derivation itself.
    derivation_tables = [
        table
        for table in reversed(db.metadata.sorted_tables)
        if table.name in RESEARCH_COVERAGE_FACT_DERIVATION_TABLES
    ]

    for table in derivation_tables:
        if not inspector.has_table(table.name):
            continue
        bind.execute(sa.text(f"DROP TABLE IF EXISTS {table.name}"))
