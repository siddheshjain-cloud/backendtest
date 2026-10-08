"""Phase 3 Slice C -- Cycle Intelligence (cycle_observation,
cycle_assessment, cycle_assessment_observation, company_exposure)

Revision ID: 20261008_02
Revises: 20261008_01
Create Date: 2026-10-08

Purely additive. Creates exactly the four Slice C tables named in
``migrations/phase3_slice_c_cycle_intelligence_table_inventory.py``'s
``PHASE3_SLICE_C_CYCLE_INTELLIGENCE_TABLES`` set, using the same
``db.metadata`` / ``checkfirst`` approach every prior revision in this
chain uses for its own frozen set -- including the revision-integrity
unique constraints and the partial unique "single genesis per
(cycle_tag, scenario)" index on ``cycle_assessment``, which are declared
directly on the model (see ``app/models/cycle_intelligence.py``) and so
are created by ``create_all`` with no separate DDL needed, unlike Slice
A's retrofit of ``forecast_revision``/``valuation_revision``.

This revision does not touch ``M1_TABLES``, ``RESEARCH_BRAIN_PILOT_TABLES``,
``RESEARCH_BRAIN_EXTRACTION_UNIT_TABLES``, ``RESEARCH_COVERAGE_FOUNDATION_TABLES``,
``RESEARCH_COVERAGE_CANDIDATE_FINDINGS_TABLES``,
``RESEARCH_COVERAGE_FACT_DERIVATION_TABLES``,
``RESEARCH_COVERAGE_PROPOSITION_LINKAGE_TABLES``, or ``PHASE3_SLICE_A_TABLES``
-- it does not alter any table from those eight frozen sets in any way,
and it does not reopen any of the eight prior revisions.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app import db
import app.models  # noqa: F401 - registers the current metadata, including cycle_intelligence
from migrations.phase3_slice_c_cycle_intelligence_table_inventory import (
    PHASE3_SLICE_C_CYCLE_INTELLIGENCE_TABLES,
)


revision: str = "20261008_02"
down_revision: str = "20261008_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()

    db.metadata.create_all(
        bind=bind,
        tables=[
            db.metadata.tables[name]
            for name in sorted(PHASE3_SLICE_C_CYCLE_INTELLIGENCE_TABLES)
        ],
        checkfirst=True,
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    for table in reversed(db.metadata.sorted_tables):
        if table.name not in PHASE3_SLICE_C_CYCLE_INTELLIGENCE_TABLES:
            continue
        if not inspector.has_table(table.name):
            continue
        bind.execute(sa.text(f"DROP TABLE IF EXISTS {table.name}"))
