"""Phase 3 Slice A -- contracts (financial_metric_definition; scenario/origin
on forecast_revision and valuation_revision)

Revision ID: 20261008_01
Revises: 20261007_05
Create Date: 2026-10-08

Two kinds of change, handled differently:

1. ``financial_metric_definition`` (new table) -- created via
   ``db.metadata.create_all`` against the frozen table list in
   ``migrations/phase3_slice_a_table_inventory.py``, the same convention
   every prior revision in this chain uses for its own net-new tables.

2. ``scenario``/``origin`` on ``forecast_revision``/``valuation_revision``
   (columns and indexes on two *already-existing* tables) -- these cannot
   use the create_all convention, which only creates whole tables. Written
   as explicit, historical DDL instead (the standard Alembic approach for
   altering an existing table), via ``op.batch_alter_table`` so SQLite (which
   cannot drop an inline table-level UNIQUE constraint without a full table
   rebuild) and Postgres (which can ALTER directly) both work correctly
   through the same code path. The old composite ``UniqueConstraint`` is
   replaced by two partial unique indexes -- see ``app/models/forecast.py``'s
   module docstring for why a single composite constraint with a nullable
   ``scenario`` column would not have enforced the legacy single-stream
   invariant.

This revision does not touch ``M1_TABLES``, ``RESEARCH_BRAIN_PILOT_TABLES``,
``RESEARCH_BRAIN_EXTRACTION_UNIT_TABLES``, ``RESEARCH_COVERAGE_FOUNDATION_TABLES``,
``RESEARCH_COVERAGE_CANDIDATE_FINDINGS_TABLES``,
``RESEARCH_COVERAGE_FACT_DERIVATION_TABLES``, or
``RESEARCH_COVERAGE_PROPOSITION_LINKAGE_TABLES`` -- it does not alter any
table from those seven frozen sets in any way, and it does not reopen any
of the seven prior revisions. ``forecast_revision``/``valuation_revision``
are M1 tables whose *shape* this revision extends (additively, with zero
existing rows as of Slice A -- verified directly against the live database
before this was written); no M1 *behavior* (immutability enforcement,
existing columns, existing constraints) is removed or weakened.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app import db
import app.models  # noqa: F401 - registers the current metadata
from migrations.phase3_slice_a_table_inventory import PHASE3_SLICE_A_TABLES


revision: str = "20261008_01"
down_revision: str = "20261007_05"
branch_labels = None
depends_on = None


def _extend_revision_table(
    table_name: str,
    legacy_constraint_name: str,
    legacy_index_name: str,
    legacy_index_columns: list[str],
    scenario_index_name: str,
    scenario_index_columns: list[str],
) -> None:
    """Add ``scenario``/``origin`` and replace the composite unique
    constraint with the two partial unique indexes, defensively.

    Written to be safe to run twice and safe to run against a table that
    already has some of this shape -- this repo's migration tests replay
    every revision from scratch against the *live* ``db.metadata``, so an
    earlier revision's ``create_all`` for this same table can already
    reflect today's (post-Slice-A) ORM model rather than its true
    historical, pre-Slice-A shape. A real production database upgrading
    from revision 20261007_05 does not have this property -- it has the
    actual pre-Slice-A shape on disk -- and this function handles both
    cases identically by checking actual reflected state rather than
    assuming either shape.
    """

    bind = op.get_bind()
    inspector = sa.inspect(bind)

    existing_columns = {c["name"] for c in inspector.get_columns(table_name)}
    existing_constraint_names = {
        c["name"] for c in inspector.get_unique_constraints(table_name)
    }
    existing_index_names = {
        i["name"] for i in inspector.get_indexes(table_name)
    }

    needs_scenario = "scenario" not in existing_columns
    needs_origin = "origin" not in existing_columns
    needs_constraint_drop = legacy_constraint_name in existing_constraint_names

    if needs_scenario or needs_origin or needs_constraint_drop:
        with op.batch_alter_table(table_name, recreate="auto") as batch_op:
            if needs_scenario:
                batch_op.add_column(
                    sa.Column("scenario", sa.String(), nullable=True)
                )
            if needs_origin:
                batch_op.add_column(
                    sa.Column(
                        "origin",
                        sa.String(),
                        nullable=False,
                        server_default="HUMAN_AUTHORED",
                    )
                )
            if needs_constraint_drop:
                batch_op.drop_constraint(
                    legacy_constraint_name, type_="unique"
                )

    if legacy_index_name not in existing_index_names:
        op.create_index(
            legacy_index_name,
            table_name,
            legacy_index_columns,
            unique=True,
            sqlite_where=sa.text("scenario IS NULL"),
            postgresql_where=sa.text("scenario IS NULL"),
        )
    if scenario_index_name not in existing_index_names:
        op.create_index(
            scenario_index_name,
            table_name,
            scenario_index_columns,
            unique=True,
            sqlite_where=sa.text("scenario IS NOT NULL"),
            postgresql_where=sa.text("scenario IS NOT NULL"),
        )


def upgrade() -> None:
    bind = op.get_bind()

    db.metadata.create_all(
        bind=bind,
        tables=[
            db.metadata.tables[name] for name in sorted(PHASE3_SLICE_A_TABLES)
        ],
        checkfirst=True,
    )

    _extend_revision_table(
        "forecast_revision",
        legacy_constraint_name="uq_forecast_revision_company_number",
        legacy_index_name="uq_forecast_revision_company_number_legacy",
        legacy_index_columns=["company_id", "revision_number"],
        scenario_index_name="uq_forecast_revision_company_scenario_number",
        scenario_index_columns=["company_id", "scenario", "revision_number"],
    )

    _extend_revision_table(
        "valuation_revision",
        legacy_constraint_name="uq_valuation_revision_company_method_number",
        legacy_index_name="uq_valuation_revision_company_method_number_legacy",
        legacy_index_columns=["company_id", "valuation_method", "revision_number"],
        scenario_index_name="uq_valuation_revision_company_method_scenario_number",
        scenario_index_columns=[
            "company_id",
            "valuation_method",
            "scenario",
            "revision_number",
        ],
    )


def downgrade() -> None:
    op.drop_index(
        "uq_valuation_revision_company_method_scenario_number",
        table_name="valuation_revision",
    )
    op.drop_index(
        "uq_valuation_revision_company_method_number_legacy",
        table_name="valuation_revision",
    )

    with op.batch_alter_table("valuation_revision", recreate="auto") as batch_op:
        batch_op.drop_column("origin")
        batch_op.drop_column("scenario")
        batch_op.create_unique_constraint(
            "uq_valuation_revision_company_method_number",
            ["company_id", "valuation_method", "revision_number"],
        )

    op.drop_index(
        "uq_forecast_revision_company_scenario_number",
        table_name="forecast_revision",
    )
    op.drop_index(
        "uq_forecast_revision_company_number_legacy",
        table_name="forecast_revision",
    )

    with op.batch_alter_table("forecast_revision", recreate="auto") as batch_op:
        batch_op.drop_column("origin")
        batch_op.drop_column("scenario")
        batch_op.create_unique_constraint(
            "uq_forecast_revision_company_number",
            ["company_id", "revision_number"],
        )

    bind = op.get_bind()
    inspector = sa.inspect(bind)
    for table in reversed(db.metadata.sorted_tables):
        if table.name not in PHASE3_SLICE_A_TABLES:
            continue
        if not inspector.has_table(table.name):
            continue
        bind.execute(sa.text(f"DROP TABLE IF EXISTS {table.name}"))
