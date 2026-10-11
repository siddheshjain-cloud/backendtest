"""Migration tests for the Research Coverage Foundation revision (20261007_02).

Mirrors ``test_research_brain_extraction_unit_migration.py``'s discipline:
this revision must add exactly the six Slice 1 tables and leave every
already-frozen table -- ``M1_TABLES``, ``RESEARCH_BRAIN_PILOT_TABLES``, and
``RESEARCH_BRAIN_EXTRACTION_UNIT_TABLES`` -- byte-for-byte unchanged.
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
from tests.migrations.helpers import (
    schema_snapshot,
    upgrade_database,
    downgrade_database,
)


EXTRACTION_UNIT_HEAD = "20261007_01"
COVERAGE_FOUNDATION_HEAD = "20261007_02"


def _database_url(tmp_path, name: str) -> str:
    return f"sqlite:///{(tmp_path / name).as_posix()}"


def test_upgrade_adds_exactly_the_six_tables(tmp_path):
    url = _database_url(tmp_path, "coverage-foundation-fresh.db")
    upgrade_database(url, EXTRACTION_UNIT_HEAD)

    before_m1 = schema_snapshot(url, M1_TABLES)
    before_pilot = schema_snapshot(url, RESEARCH_BRAIN_PILOT_TABLES)
    before_extraction_unit = schema_snapshot(
        url, RESEARCH_BRAIN_EXTRACTION_UNIT_TABLES
    )

    upgrade_database(url, COVERAGE_FOUNDATION_HEAD)

    after_m1 = schema_snapshot(url, M1_TABLES)
    assert after_m1 == before_m1, (
        "Coverage Foundation migration must not change any frozen "
        "Milestone 1 table's schema"
    )

    after_pilot = schema_snapshot(url, RESEARCH_BRAIN_PILOT_TABLES)
    assert after_pilot == before_pilot, (
        "Coverage Foundation migration must not change any Research Brain "
        "Pilot table's schema"
    )

    after_extraction_unit = schema_snapshot(
        url, RESEARCH_BRAIN_EXTRACTION_UNIT_TABLES
    )
    assert after_extraction_unit == before_extraction_unit, (
        "Coverage Foundation migration must not change the extraction_unit "
        "table's schema"
    )

    coverage_tables = schema_snapshot(url, RESEARCH_COVERAGE_FOUNDATION_TABLES)
    assert set(coverage_tables) == RESEARCH_COVERAGE_FOUNDATION_TABLES


def test_research_dimension_table_shape(tmp_path):
    url = _database_url(tmp_path, "coverage-foundation-dimension-shape.db")
    upgrade_database(url, COVERAGE_FOUNDATION_HEAD)

    snapshot = schema_snapshot(url, {"research_dimension"})["research_dimension"]
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


def test_coverage_document_subtype_table_shape(tmp_path):
    url = _database_url(tmp_path, "coverage-foundation-subtype-shape.db")
    upgrade_database(url, COVERAGE_FOUNDATION_HEAD)

    snapshot = schema_snapshot(url, {"coverage_document_subtype"})[
        "coverage_document_subtype"
    ]
    column_names = {column["name"] for column in snapshot["columns"]}
    assert column_names == {
        "id",
        "created_at",
        "document_id",
        "subtype_code",
        "assigned_by_user_id",
    }

    foreign_keys = {
        (tuple(fk["columns"]), fk["referred_table"]) for fk in snapshot["foreign_keys"]
    }
    assert (("document_id",), "document") in foreign_keys
    assert (("assigned_by_user_id",), "user") in foreign_keys


def test_coverage_profile_table_shape(tmp_path):
    url = _database_url(tmp_path, "coverage-foundation-profile-shape.db")
    upgrade_database(url, COVERAGE_FOUNDATION_HEAD)

    snapshot = schema_snapshot(url, {"coverage_profile"})["coverage_profile"]
    column_names = {column["name"] for column in snapshot["columns"]}
    assert column_names == {
        "id",
        "created_at",
        "document_type_code",
        "supersedes_profile_id",
        "effective_from",
        "created_by_user_id",
    }

    foreign_keys = {
        (tuple(fk["columns"]), fk["referred_table"]) for fk in snapshot["foreign_keys"]
    }
    assert (("supersedes_profile_id",), "coverage_profile") in foreign_keys
    assert (("created_by_user_id",), "user") in foreign_keys


def test_coverage_profile_dimension_table_shape(tmp_path):
    url = _database_url(tmp_path, "coverage-foundation-profile-dimension-shape.db")
    upgrade_database(url, COVERAGE_FOUNDATION_HEAD)

    snapshot = schema_snapshot(url, {"coverage_profile_dimension"})[
        "coverage_profile_dimension"
    ]
    column_names = {column["name"] for column in snapshot["columns"]}
    assert column_names == {
        "id",
        "created_at",
        "coverage_profile_id",
        "research_dimension_id",
        "is_required",
        "notes",
    }

    foreign_keys = {
        (tuple(fk["columns"]), fk["referred_table"]) for fk in snapshot["foreign_keys"]
    }
    assert (("coverage_profile_id",), "coverage_profile") in foreign_keys
    assert (("research_dimension_id",), "research_dimension") in foreign_keys


def test_coverage_review_pass_table_shape(tmp_path):
    url = _database_url(tmp_path, "coverage-foundation-review-pass-shape.db")
    upgrade_database(url, COVERAGE_FOUNDATION_HEAD)

    snapshot = schema_snapshot(url, {"coverage_review_pass"})["coverage_review_pass"]
    column_names = {column["name"] for column in snapshot["columns"]}
    assert column_names == {
        "id",
        "created_at",
        "document_id",
        "extraction_run_id",
        "research_dimension_id",
        "coverage_profile_id",
        "units_considered_count",
        "units_considered_min_seq",
        "units_considered_max_seq",
        "has_material_content",
        "performed_by_user_id",
        "notes",
    }

    foreign_keys = {
        (tuple(fk["columns"]), fk["referred_table"]) for fk in snapshot["foreign_keys"]
    }
    assert (("document_id",), "document") in foreign_keys
    assert (("extraction_run_id",), "extraction_run") in foreign_keys
    assert (("research_dimension_id",), "research_dimension") in foreign_keys
    assert (("coverage_profile_id",), "coverage_profile") in foreign_keys
    assert (("performed_by_user_id",), "user") in foreign_keys


def test_coverage_record_table_shape(tmp_path):
    url = _database_url(tmp_path, "coverage-foundation-record-shape.db")
    upgrade_database(url, COVERAGE_FOUNDATION_HEAD)

    snapshot = schema_snapshot(url, {"coverage_record"})["coverage_record"]
    column_names = {column["name"] for column in snapshot["columns"]}
    assert column_names == {
        "id",
        "created_at",
        "document_id",
        "extraction_run_id",
        "research_dimension_id",
        "state",
        "review_pass_id",
    }

    foreign_keys = {
        (tuple(fk["columns"]), fk["referred_table"]) for fk in snapshot["foreign_keys"]
    }
    assert (("document_id",), "document") in foreign_keys
    assert (("extraction_run_id",), "extraction_run") in foreign_keys
    assert (("research_dimension_id",), "research_dimension") in foreign_keys
    assert (("review_pass_id",), "coverage_review_pass") in foreign_keys

    unique_columns = {
        tuple(sorted(constraint["columns"]))
        for constraint in snapshot["unique_constraints"]
    }
    assert (
        tuple(
            sorted(
                ["document_id", "extraction_run_id", "research_dimension_id"]
            )
        )
        in unique_columns
    ), "at most one CoverageRecord per (document, run, dimension) must be DB-enforced"


def test_downgrade_removes_exactly_the_six_tables(tmp_path):
    url = _database_url(tmp_path, "coverage-foundation-downgrade.db")
    upgrade_database(url, COVERAGE_FOUNDATION_HEAD)

    before_m1 = schema_snapshot(url, M1_TABLES)
    before_pilot = schema_snapshot(url, RESEARCH_BRAIN_PILOT_TABLES)
    before_extraction_unit = schema_snapshot(
        url, RESEARCH_BRAIN_EXTRACTION_UNIT_TABLES
    )

    downgrade_database(url, EXTRACTION_UNIT_HEAD)

    engine = sa.create_engine(url)
    try:
        remaining_tables = set(sa.inspect(engine).get_table_names())
    finally:
        engine.dispose()

    assert not (remaining_tables & RESEARCH_COVERAGE_FOUNDATION_TABLES)

    after_m1 = schema_snapshot(url, M1_TABLES)
    assert after_m1 == before_m1
    after_pilot = schema_snapshot(url, RESEARCH_BRAIN_PILOT_TABLES)
    assert after_pilot == before_pilot
    after_extraction_unit = schema_snapshot(
        url, RESEARCH_BRAIN_EXTRACTION_UNIT_TABLES
    )
    assert after_extraction_unit == before_extraction_unit
