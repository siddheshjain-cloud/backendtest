"""Migration tests for the Candidate Findings revision (20261007_03).

Mirrors ``test_research_coverage_foundation_migration.py``'s discipline:
this revision must add exactly the two Slice 2 tables and leave every
already-frozen table -- ``M1_TABLES``, ``RESEARCH_BRAIN_PILOT_TABLES``,
``RESEARCH_BRAIN_EXTRACTION_UNIT_TABLES``, and
``RESEARCH_COVERAGE_FOUNDATION_TABLES`` -- byte-for-byte unchanged.
"""

from __future__ import annotations

import sqlalchemy as sa

from app import db
import app.models  # noqa: F401 - registers the current metadata
from migrations.m1_table_inventory import M1_TABLES
from migrations.research_brain_pilot_table_inventory import (
    RESEARCH_BRAIN_PILOT_TABLES,
)
from migrations.research_brain_extraction_unit_table_inventory import (
    RESEARCH_BRAIN_EXTRACTION_UNIT_TABLES,
)
from migrations.research_coverage_foundation_table_inventory import (
    RESEARCH_COVERAGE_FOUNDATION_TABLES,
)
from migrations.research_coverage_candidate_findings_table_inventory import (
    RESEARCH_COVERAGE_CANDIDATE_FINDINGS_TABLES,
)
from tests.migrations.helpers import (
    schema_snapshot,
    upgrade_database,
    downgrade_database,
)


COVERAGE_FOUNDATION_HEAD = "20261007_02"
CANDIDATE_FINDINGS_HEAD = "20261007_03"


def _database_url(tmp_path, name: str) -> str:
    return f"sqlite:///{(tmp_path / name).as_posix()}"


def test_upgrade_adds_exactly_the_two_tables(tmp_path):
    url = _database_url(tmp_path, "candidate-findings-fresh.db")
    upgrade_database(url, COVERAGE_FOUNDATION_HEAD)

    before_m1 = schema_snapshot(url, M1_TABLES)
    before_pilot = schema_snapshot(url, RESEARCH_BRAIN_PILOT_TABLES)
    before_extraction_unit = schema_snapshot(
        url, RESEARCH_BRAIN_EXTRACTION_UNIT_TABLES
    )
    before_foundation = schema_snapshot(url, RESEARCH_COVERAGE_FOUNDATION_TABLES)

    upgrade_database(url, CANDIDATE_FINDINGS_HEAD)

    after_m1 = schema_snapshot(url, M1_TABLES)
    assert after_m1 == before_m1

    after_pilot = schema_snapshot(url, RESEARCH_BRAIN_PILOT_TABLES)
    assert after_pilot == before_pilot

    after_extraction_unit = schema_snapshot(
        url, RESEARCH_BRAIN_EXTRACTION_UNIT_TABLES
    )
    assert after_extraction_unit == before_extraction_unit

    after_foundation = schema_snapshot(url, RESEARCH_COVERAGE_FOUNDATION_TABLES)
    assert after_foundation == before_foundation, (
        "Candidate Findings migration must not change any Coverage "
        "Foundation table's schema"
    )

    candidate_tables = schema_snapshot(
        url, RESEARCH_COVERAGE_CANDIDATE_FINDINGS_TABLES
    )
    assert set(candidate_tables) == RESEARCH_COVERAGE_CANDIDATE_FINDINGS_TABLES


def test_candidate_finding_table_shape(tmp_path):
    url = _database_url(tmp_path, "candidate-findings-shape.db")
    upgrade_database(url, CANDIDATE_FINDINGS_HEAD)

    snapshot = schema_snapshot(url, {"candidate_finding"})["candidate_finding"]
    column_names = {column["name"] for column in snapshot["columns"]}
    assert column_names == {
        "id",
        "created_at",
        "document_id",
        "research_dimension_id",
        "source_extraction_unit_id",
        "review_pass_id",
        "raw_quote",
        "proposed_fact_type",
        "proposed_value",
        "proposed_value_type",
        "proposed_unit",
        "proposed_period",
        "proposed_as_of_date",
        "created_by_user_id",
    }

    foreign_keys = {
        (tuple(fk["columns"]), fk["referred_table"]) for fk in snapshot["foreign_keys"]
    }
    assert (("document_id",), "document") in foreign_keys
    assert (("research_dimension_id",), "research_dimension") in foreign_keys
    assert (
        ("source_extraction_unit_id",),
        "extraction_unit",
    ) in foreign_keys
    assert (("review_pass_id",), "coverage_review_pass") in foreign_keys
    assert (("created_by_user_id",), "user") in foreign_keys


def test_candidate_finding_decision_table_shape(tmp_path):
    url = _database_url(tmp_path, "candidate-findings-decision-shape.db")
    upgrade_database(url, CANDIDATE_FINDINGS_HEAD)

    snapshot = schema_snapshot(url, {"candidate_finding_decision"})[
        "candidate_finding_decision"
    ]
    column_names = {column["name"] for column in snapshot["columns"]}
    assert column_names == {
        "id",
        "created_at",
        "candidate_finding_id",
        "decision",
        "promoted_to_fact_id",
        "duplicate_of_candidate_id",
        "reason",
        "created_by_user_id",
    }

    foreign_keys = {
        (tuple(fk["columns"]), fk["referred_table"]) for fk in snapshot["foreign_keys"]
    }
    assert (
        ("candidate_finding_id",),
        "candidate_finding",
    ) in foreign_keys
    assert (("promoted_to_fact_id",), "extracted_fact") in foreign_keys
    assert (
        ("duplicate_of_candidate_id",),
        "candidate_finding",
    ) in foreign_keys

    unique_columns = {
        tuple(constraint["columns"]) for constraint in snapshot["unique_constraints"]
    }
    assert ("candidate_finding_id",) in unique_columns, (
        "at most one decision per candidate finding must be DB-enforced"
    )


def test_downgrade_removes_exactly_the_two_tables(tmp_path):
    url = _database_url(tmp_path, "candidate-findings-downgrade.db")
    upgrade_database(url, CANDIDATE_FINDINGS_HEAD)

    before_foundation = schema_snapshot(url, RESEARCH_COVERAGE_FOUNDATION_TABLES)

    downgrade_database(url, COVERAGE_FOUNDATION_HEAD)

    engine = sa.create_engine(url)
    try:
        remaining_tables = set(sa.inspect(engine).get_table_names())
    finally:
        engine.dispose()

    assert not (remaining_tables & RESEARCH_COVERAGE_CANDIDATE_FINDINGS_TABLES)

    after_foundation = schema_snapshot(url, RESEARCH_COVERAGE_FOUNDATION_TABLES)
    assert after_foundation == before_foundation
