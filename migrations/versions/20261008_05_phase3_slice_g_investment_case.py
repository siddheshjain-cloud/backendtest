"""Phase 3 Slice G -- Investment View / ResearchRevision (investment_case,
investment_case_forecast, investment_case_valuation,
investment_case_kill_switch, investment_case_gate_result)

Revision ID: 20261008_05
Revises: 20261008_04
Create Date: 2026-10-08

Purely additive. Creates exactly the five Slice G tables named in
``migrations/phase3_slice_g_investment_case_table_inventory.py``'s
``PHASE3_SLICE_G_INVESTMENT_CASE_TABLES`` set, using the same
``db.metadata`` / ``checkfirst`` approach every prior revision in this
chain uses for its own frozen set -- including the revision-integrity
unique constraints and the partial unique "single genesis" indexes on
``investment_case`` (per company) and ``investment_case_gate_result``
(per investment_case_id, gate), which are declared directly on the
model (see ``app/models/investment_case.py``) and so are created by
``create_all`` with no separate DDL needed.

``ResearchRevision`` itself is not touched by this revision -- no new
column, no reverse reference -- consistent with design proposal §7/§19.D's
"no second authoritative research-view table" rule; ``investment_case``
only adds a forward-pointing, nullable-nothing foreign key to it.

This revision does not touch ``M1_TABLES``, ``RESEARCH_BRAIN_PILOT_TABLES``,
``RESEARCH_BRAIN_EXTRACTION_UNIT_TABLES``, ``RESEARCH_COVERAGE_FOUNDATION_TABLES``,
``RESEARCH_COVERAGE_CANDIDATE_FINDINGS_TABLES``,
``RESEARCH_COVERAGE_FACT_DERIVATION_TABLES``,
``RESEARCH_COVERAGE_PROPOSITION_LINKAGE_TABLES``, ``PHASE3_SLICE_A_TABLES``,
``PHASE3_SLICE_C_CYCLE_INTELLIGENCE_TABLES``, or
``PHASE3_SLICE_D_HYPOTHESIS_ASSUMPTION_TABLES`` -- it does not alter any
table from those ten frozen sets in any way, and it does not reopen any
of the ten prior revisions. (Slice E added no schema; Slice F added no
schema.)
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app import db
import app.models  # noqa: F401 - registers the current metadata, including investment_case
from migrations.phase3_slice_g_investment_case_table_inventory import (
    PHASE3_SLICE_G_INVESTMENT_CASE_TABLES,
)


revision: str = "20261008_05"
down_revision: str = "20261008_04"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()

    db.metadata.create_all(
        bind=bind,
        tables=[
            db.metadata.tables[name]
            for name in sorted(PHASE3_SLICE_G_INVESTMENT_CASE_TABLES)
        ],
        checkfirst=True,
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    for table in reversed(db.metadata.sorted_tables):
        if table.name not in PHASE3_SLICE_G_INVESTMENT_CASE_TABLES:
            continue
        if not inspector.has_table(table.name):
            continue
        bind.execute(sa.text(f"DROP TABLE IF EXISTS {table.name}"))
