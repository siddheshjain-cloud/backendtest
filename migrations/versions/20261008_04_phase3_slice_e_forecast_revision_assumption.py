"""Phase 3 Slice E -- Scenario/Forecast Engine (forecast_revision_assumption)

Revision ID: 20261008_04
Revises: 20261008_03
Create Date: 2026-10-08

Purely additive. Creates exactly the one Slice E table named in
``migrations/phase3_slice_e_forecast_revision_assumption_table_inventory.py``'s
``PHASE3_SLICE_E_FORECAST_REVISION_ASSUMPTION_TABLES`` set, using the
same ``db.metadata`` / ``checkfirst`` approach every prior revision in
this chain uses for its own frozen set.

No column/index change to ``forecast_revision`` here -- Slice A's
migration already added ``scenario``/``origin`` and the partial-unique-
index pair; Slice E's correction is confined to
``ResearchCommandService`` actually using them
(``_build_forecast_revision_values`` now accepts ``scenario``,
``_current_forecast_revision_locked`` now scopes by
``(company_id, scenario)``, and
``_is_forecast_revision_number_unique_violation`` now recognizes the
two index names Slice A's migration actually created instead of the
pre-Slice-A constraint name it still checked) -- a service-layer fix,
not a schema change, so it has no migration of its own.

This revision does not touch ``M1_TABLES``, ``RESEARCH_BRAIN_PILOT_TABLES``,
``RESEARCH_BRAIN_EXTRACTION_UNIT_TABLES``, ``RESEARCH_COVERAGE_FOUNDATION_TABLES``,
``RESEARCH_COVERAGE_CANDIDATE_FINDINGS_TABLES``,
``RESEARCH_COVERAGE_FACT_DERIVATION_TABLES``,
``RESEARCH_COVERAGE_PROPOSITION_LINKAGE_TABLES``, ``PHASE3_SLICE_A_TABLES``,
``PHASE3_SLICE_C_CYCLE_INTELLIGENCE_TABLES``, or
``PHASE3_SLICE_D_HYPOTHESIS_ASSUMPTION_TABLES`` -- it does not alter any
table from those ten frozen sets in any way, and it does not reopen any
of the ten prior revisions.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app import db
import app.models  # noqa: F401 - registers the current metadata, including forecast_revision_assumption
from migrations.phase3_slice_e_forecast_revision_assumption_table_inventory import (
    PHASE3_SLICE_E_FORECAST_REVISION_ASSUMPTION_TABLES,
)


revision: str = "20261008_04"
down_revision: str = "20261008_03"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()

    db.metadata.create_all(
        bind=bind,
        tables=[
            db.metadata.tables[name]
            for name in sorted(PHASE3_SLICE_E_FORECAST_REVISION_ASSUMPTION_TABLES)
        ],
        checkfirst=True,
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    for table in reversed(db.metadata.sorted_tables):
        if table.name not in PHASE3_SLICE_E_FORECAST_REVISION_ASSUMPTION_TABLES:
            continue
        if not inspector.has_table(table.name):
            continue
        bind.execute(sa.text(f"DROP TABLE IF EXISTS {table.name}"))
