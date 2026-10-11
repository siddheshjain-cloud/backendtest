"""Migration tests for the Fact Derivation revision (20261007_04).

Mirrors the prior Coverage Foundation / Candidate Findings migration
tests' discipline: this revision must add exactly the two Slice 3 tables
and leave every already-frozen table untouched.
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
from migrations.research_coverage_fact_derivation_table_inventory import (
    RESEARCH_COVERAGE_FACT_DERIVATION_TABLES,
)
from tests.migrations.helpers import (
    schema_snapshot,
    upgrade_database,
    downgrade_database,
)


CANDIDATE_FINDINGS_HEAD = "20261007_03"
FACT_DERIVATION_HEAD = "20261007_04"


def _database_url(tmp_path, name: str) -> str:
    return f"sqlite:///{(tmp_path / name).as_posix()}"


def test_upgrade_adds_exactly_the_two_tables(tmp_path):
    url = _database_url(tmp_path, "fact-derivation-fresh.db")
    upgrade_database(url, CANDIDATE_FINDINGS_HEAD)

    before_m1 = schema_snapshot(url, M1_TABLES)
    before_pilot = schema_snapshot(url, RESEARCH_BRAIN_PILOT_TABLES)
    before_extraction_unit = schema_snapshot(
        url, RESEARCH_BRAIN_EXTRACTION_UNIT_TABLES
    )
    before_foundation = schema_snapshot(url, RESEARCH_COVERAGE_FOUNDATION_TABLES)
    before_candidates = schema_snapshot(
        url, RESEARCH_COVERAGE_CANDIDATE_FINDINGS_TABLES
    )

    upgrade_database(url, FACT_DERIVATION_HEAD)

    assert schema_snapshot(url, M1_TABLES) == before_m1
    assert schema_snapshot(url, RESEARCH_BRAIN_PILOT_TABLES) == before_pilot
    assert (
        schema_snapshot(url, RESEARCH_BRAIN_EXTRACTION_UNIT_TABLES)
        == before_extraction_unit
    )
    assert (
        schema_snapshot(url, RESEARCH_COVERAGE_FOUNDATION_TABLES)
        == before_foundation
    )
    assert (
        schema_snapshot(url, RESEARCH_COVERAGE_CANDIDATE_FINDINGS_TABLES)
        == before_candidates
    ), "Fact Derivation migration must not change any Candidate Findings table's schema"

    derivation_tables = schema_snapshot(url, RESEARCH_COVERAGE_FACT_DERIVATION_TABLES)
    assert set(derivation_tables) == RESEARCH_COVERAGE_FACT_DERIVATION_TABLES


def test_fact_derivation_table_shape(tmp_path):
    url = _database_url(tmp_path, "fact-derivation-shape.db")
    upgrade_database(url, FACT_DERIVATION_HEAD)

    snapshot = schema_snapshot(url, {"fact_derivation"})["fact_derivation"]
    column_names = {column["name"] for column in snapshot["columns"]}
    assert column_names == {
        "id",
        "created_at",
        "derived_fact_id",
        "formula_description",
        "created_by_user_id",
    }

    foreign_keys = {
        (tuple(fk["columns"]), fk["referred_table"]) for fk in snapshot["foreign_keys"]
    }
    assert (("derived_fact_id",), "extracted_fact") in foreign_keys
    assert (("created_by_user_id",), "user") in foreign_keys

    unique_columns = {
        tuple(constraint["columns"]) for constraint in snapshot["unique_constraints"]
    }
    assert ("derived_fact_id",) in unique_columns, (
        "at most one FactDerivation per Fact must be DB-enforced"
    )


def test_fact_derivation_input_table_shape(tmp_path):
    url = _database_url(tmp_path, "fact-derivation-input-shape.db")
    upgrade_database(url, FACT_DERIVATION_HEAD)

    snapshot = schema_snapshot(url, {"fact_derivation_input"})["fact_derivation_input"]
    column_names = {column["name"] for column in snapshot["columns"]}
    assert column_names == {
        "id",
        "created_at",
        "fact_derivation_id",
        "input_fact_id",
        "input_evidence_id",
        "role_label",
    }

    foreign_keys = {
        (tuple(fk["columns"]), fk["referred_table"]) for fk in snapshot["foreign_keys"]
    }
    assert (("fact_derivation_id",), "fact_derivation") in foreign_keys
    assert (("input_fact_id",), "extracted_fact") in foreign_keys
    assert (("input_evidence_id",), "evidence") in foreign_keys


def test_downgrade_removes_exactly_the_two_tables(tmp_path):
    url = _database_url(tmp_path, "fact-derivation-downgrade.db")
    upgrade_database(url, FACT_DERIVATION_HEAD)

    before_candidates = schema_snapshot(
        url, RESEARCH_COVERAGE_CANDIDATE_FINDINGS_TABLES
    )

    downgrade_database(url, CANDIDATE_FINDINGS_HEAD)

    engine = sa.create_engine(url)
    try:
        remaining_tables = set(sa.inspect(engine).get_table_names())
    finally:
        engine.dispose()

    assert not (remaining_tables & RESEARCH_COVERAGE_FACT_DERIVATION_TABLES)

    after_candidates = schema_snapshot(
        url, RESEARCH_COVERAGE_CANDIDATE_FINDINGS_TABLES
    )
    assert after_candidates == before_candidates
