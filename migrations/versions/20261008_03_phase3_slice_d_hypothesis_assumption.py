"""Phase 3 Slice D -- Investment Hypothesis & Assumption Engine
(investment_hypothesis, investment_hypothesis_citation, forecast_assumption)

Revision ID: 20261008_03
Revises: 20261008_02
Create Date: 2026-10-08

Purely additive. Creates exactly the three Slice D tables named in
``migrations/phase3_slice_d_hypothesis_assumption_table_inventory.py``'s
``PHASE3_SLICE_D_HYPOTHESIS_ASSUMPTION_TABLES`` set, using the same
``db.metadata`` / ``checkfirst`` approach every prior revision in this
chain uses for its own frozen set -- including the revision-integrity
unique constraints and the partial unique "single genesis per company"
index on ``investment_hypothesis``, which are declared directly on the
model (see ``app/models/investment_hypothesis.py``) and so are created
by ``create_all`` with no separate DDL needed.

``origin`` is added directly in this table's own initial definition (not
retrofitted) -- this is the first revision to create
``investment_hypothesis``, so §9a's promotion-contract column is simply
part of its original shape, unlike Slice A's retrofit of the two tables
that already existed before the convention was introduced.

This revision does not touch ``M1_TABLES``, ``RESEARCH_BRAIN_PILOT_TABLES``,
``RESEARCH_BRAIN_EXTRACTION_UNIT_TABLES``, ``RESEARCH_COVERAGE_FOUNDATION_TABLES``,
``RESEARCH_COVERAGE_CANDIDATE_FINDINGS_TABLES``,
``RESEARCH_COVERAGE_FACT_DERIVATION_TABLES``,
``RESEARCH_COVERAGE_PROPOSITION_LINKAGE_TABLES``, ``PHASE3_SLICE_A_TABLES``, or
``PHASE3_SLICE_C_CYCLE_INTELLIGENCE_TABLES`` -- it does not alter any table
from those nine frozen sets in any way, and it does not reopen any of the
nine prior revisions.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app import db
import app.models  # noqa: F401 - registers the current metadata, including investment_hypothesis
from migrations.phase3_slice_d_hypothesis_assumption_table_inventory import (
    PHASE3_SLICE_D_HYPOTHESIS_ASSUMPTION_TABLES,
)


revision: str = "20261008_03"
down_revision: str = "20261008_02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()

    db.metadata.create_all(
        bind=bind,
        tables=[
            db.metadata.tables[name]
            for name in sorted(PHASE3_SLICE_D_HYPOTHESIS_ASSUMPTION_TABLES)
        ],
        checkfirst=True,
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    for table in reversed(db.metadata.sorted_tables):
        if table.name not in PHASE3_SLICE_D_HYPOTHESIS_ASSUMPTION_TABLES:
            continue
        if not inspector.has_table(table.name):
            continue
        bind.execute(sa.text(f"DROP TABLE IF EXISTS {table.name}"))
