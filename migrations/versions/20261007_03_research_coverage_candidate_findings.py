"""add Research Coverage Candidate Findings tables (candidate_finding, candidate_finding_decision)

Revision ID: 20261007_03
Revises: 20261007_02
Create Date: 2026-10-07

Purely additive. Creates exactly the two Slice 2 "Candidate Findings"
tables named in
``migrations/research_coverage_candidate_findings_table_inventory.py``'s
``RESEARCH_COVERAGE_CANDIDATE_FINDINGS_TABLES`` set, using the same
``db.metadata`` / ``checkfirst`` approach the frozen ``20260904_02``,
``20261006_01``, ``20261007_01``, and ``20261007_02`` revisions already use
for their own frozen sets. This revision does not touch ``M1_TABLES``,
``RESEARCH_BRAIN_PILOT_TABLES``, ``RESEARCH_BRAIN_EXTRACTION_UNIT_TABLES``,
or ``RESEARCH_COVERAGE_FOUNDATION_TABLES`` -- it does not alter `document`,
`company`, `ticker`, `extraction_run`, `extraction_unit`, `evidence`,
`extracted_fact`, `fact_evidence`, `research_dimension`,
`coverage_document_subtype`, `coverage_profile`,
`coverage_profile_dimension`, `coverage_review_pass`, or `coverage_record`
in any way, and it does not reopen any of the four prior revisions.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app import db
import app.models  # noqa: F401 - registers the current metadata, including research_coverage
from migrations.research_coverage_candidate_findings_table_inventory import (
    RESEARCH_COVERAGE_CANDIDATE_FINDINGS_TABLES,
)


revision: str = "20261007_03"
down_revision: str = "20261007_02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()

    # Create exactly the frozen RESEARCH_COVERAGE_CANDIDATE_FINDINGS_TABLES
    # set, not every table SQLAlchemy happens to know about -- mirrors the
    # prior four revisions' own discipline so this migration cannot
    # silently absorb an unrelated table added to app.models after this
    # revision was written.
    db.metadata.create_all(
        bind=bind,
        tables=[
            db.metadata.tables[name]
            for name in sorted(RESEARCH_COVERAGE_CANDIDATE_FINDINGS_TABLES)
        ],
        checkfirst=True,
    )


def downgrade() -> None:
    """Drop exactly the two Candidate Findings tables."""

    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # Reverse SQLAlchemy's dependency-sorted order so candidate_finding_decision
    # (which FKs candidate_finding) drops before candidate_finding itself.
    candidate_tables = [
        table
        for table in reversed(db.metadata.sorted_tables)
        if table.name in RESEARCH_COVERAGE_CANDIDATE_FINDINGS_TABLES
    ]

    for table in candidate_tables:
        if not inspector.has_table(table.name):
            continue
        bind.execute(sa.text(f"DROP TABLE IF EXISTS {table.name}"))
