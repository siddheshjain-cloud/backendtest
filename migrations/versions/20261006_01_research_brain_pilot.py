"""add Research Brain Pilot tables (extraction_run, evidence, extracted_fact, fact_evidence)

Revision ID: 20261006_01
Revises: 20260904_02
Create Date: 2026-10-06

Purely additive. Creates exactly the four Research Brain Pilot tables named
in ``migrations/research_brain_pilot_table_inventory.py``'s
``RESEARCH_BRAIN_PILOT_TABLES`` set, using the same ``db.metadata`` /
``checkfirst`` approach the frozen ``20260904_02`` revision already uses for
its own ``M1_TABLES`` set. This revision does not touch ``M1_TABLES``, does
not touch any of the 20 frozen Milestone 1 tables, and does not reopen
``20260904_02``.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app import db
import app.models  # noqa: F401 - registers the current metadata, including research_brain
from migrations.research_brain_pilot_table_inventory import (
    RESEARCH_BRAIN_PILOT_TABLES,
)


revision: str = "20261006_01"
down_revision: str = "20260904_02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()

    # Create exactly the frozen RESEARCH_BRAIN_PILOT_TABLES set, not every
    # table SQLAlchemy happens to know about -- mirrors 20260904_02's own
    # M1_TABLES discipline so this migration cannot silently absorb an
    # unrelated table added to app.models after this revision was written.
    db.metadata.create_all(
        bind=bind,
        tables=[
            db.metadata.tables[name] for name in sorted(RESEARCH_BRAIN_PILOT_TABLES)
        ],
        checkfirst=True,
    )


def downgrade() -> None:
    """Drop exactly the four Research Brain Pilot tables."""

    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # Reverse SQLAlchemy's dependency-sorted order so child tables (evidence,
    # extracted_fact, fact_evidence) drop before extraction_run/extracted_fact
    # parents they reference.
    pilot_tables = [
        table
        for table in reversed(db.metadata.sorted_tables)
        if table.name in RESEARCH_BRAIN_PILOT_TABLES
    ]

    for table in pilot_tables:
        if not inspector.has_table(table.name):
            continue
        bind.execute(sa.text(f"DROP TABLE IF EXISTS {table.name}"))
