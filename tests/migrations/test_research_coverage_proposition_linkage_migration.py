"""Migration tests for the Proposition Linkage revision (20261007_05).

Mirrors the prior Slice migration tests' discipline: this revision must
add exactly the three Slice 4 tables and leave every already-frozen table
untouched.
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
from migrations.research_coverage_proposition_linkage_table_inventory import (
    RESEARCH_COVERAGE_PROPOSITION_LINKAGE_TABLES,
)
from tests.migrations.helpers import (
    schema_snapshot,
    upgrade_database,
    downgrade_database,
)


FACT_DERIVATION_HEAD = "20261007_04"
PROPOSITION_LINKAGE_HEAD = "20261007_05"


def _database_url(tmp_path, name: str) -> str:
    return f"sqlite:///{(tmp_path / name).as_posix()}"


def test_upgrade_adds_exactly_the_three_tables(tmp_path):
    url = _database_url(tmp_path, "proposition-linkage-fresh.db")
    upgrade_database(url, FACT_DERIVATION_HEAD)

    before_m1 = schema_snapshot(url, M1_TABLES)
    before_pilot = schema_snapshot(url, RESEARCH_BRAIN_PILOT_TABLES)
    before_extraction_unit = schema_snapshot(
        url, RESEARCH_BRAIN_EXTRACTION_UNIT_TABLES
    )
    before_foundation = schema_snapshot(url, RESEARCH_COVERAGE_FOUNDATION_TABLES)
    before_candidates = schema_snapshot(
        url, RESEARCH_COVERAGE_CANDIDATE_FINDINGS_TABLES
    )
    before_derivation = schema_snapshot(url, RESEARCH_COVERAGE_FACT_DERIVATION_TABLES)

    upgrade_database(url, PROPOSITION_LINKAGE_HEAD)

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
    )
    assert (
        schema_snapshot(url, RESEARCH_COVERAGE_FACT_DERIVATION_TABLES)
        == before_derivation
    ), "Proposition Linkage migration must not change any Fact Derivation table's schema"

    proposition_tables = schema_snapshot(
        url, RESEARCH_COVERAGE_PROPOSITION_LINKAGE_TABLES
    )
    assert set(proposition_tables) == RESEARCH_COVERAGE_PROPOSITION_LINKAGE_TABLES


def test_research_proposition_table_shape(tmp_path):
    url = _database_url(tmp_path, "proposition-shape.db")
    upgrade_database(url, PROPOSITION_LINKAGE_HEAD)

    snapshot = schema_snapshot(url, {"research_proposition"})["research_proposition"]
    column_names = {column["name"] for column in snapshot["columns"]}
    assert column_names == {
        "id",
        "created_at",
        "company_id",
        "title",
        "description",
        "created_by_user_id",
    }

    foreign_keys = {
        (tuple(fk["columns"]), fk["referred_table"]) for fk in snapshot["foreign_keys"]
    }
    assert (("company_id",), "company") in foreign_keys
    assert (("created_by_user_id",), "user") in foreign_keys


def test_proposition_stage_type_table_shape(tmp_path):
    url = _database_url(tmp_path, "proposition-stage-type-shape.db")
    upgrade_database(url, PROPOSITION_LINKAGE_HEAD)

    snapshot = schema_snapshot(url, {"proposition_stage_type"})["proposition_stage_type"]
    column_names = {column["name"] for column in snapshot["columns"]}
    assert column_names == {
        "id",
        "created_at",
        "code",
        "name",
        "description",
        "is_active",
    }

    unique_columns = {
        tuple(constraint["columns"]) for constraint in snapshot["unique_constraints"]
    }
    assert ("code",) in unique_columns


def test_proposition_link_table_shape(tmp_path):
    url = _database_url(tmp_path, "proposition-link-shape.db")
    upgrade_database(url, PROPOSITION_LINKAGE_HEAD)

    snapshot = schema_snapshot(url, {"proposition_link"})["proposition_link"]
    column_names = {column["name"] for column in snapshot["columns"]}
    assert column_names == {
        "id",
        "created_at",
        "proposition_id",
        "stage_type_id",
        "fact_id",
        "candidate_finding_id",
        "document_id",
        "as_of_date",
        "period",
        "stage_note",
        "created_by_user_id",
    }

    foreign_keys = {
        (tuple(fk["columns"]), fk["referred_table"]) for fk in snapshot["foreign_keys"]
    }
    assert (("proposition_id",), "research_proposition") in foreign_keys
    assert (("stage_type_id",), "proposition_stage_type") in foreign_keys
    assert (("fact_id",), "extracted_fact") in foreign_keys
    assert (("candidate_finding_id",), "candidate_finding") in foreign_keys
    assert (("document_id",), "document") in foreign_keys


def test_downgrade_removes_exactly_the_three_tables(tmp_path):
    url = _database_url(tmp_path, "proposition-linkage-downgrade.db")
    upgrade_database(url, PROPOSITION_LINKAGE_HEAD)

    before_derivation = schema_snapshot(url, RESEARCH_COVERAGE_FACT_DERIVATION_TABLES)

    downgrade_database(url, FACT_DERIVATION_HEAD)

    engine = sa.create_engine(url)
    try:
        remaining_tables = set(sa.inspect(engine).get_table_names())
    finally:
        engine.dispose()

    assert not (remaining_tables & RESEARCH_COVERAGE_PROPOSITION_LINKAGE_TABLES)

    after_derivation = schema_snapshot(url, RESEARCH_COVERAGE_FACT_DERIVATION_TABLES)
    assert after_derivation == before_derivation
