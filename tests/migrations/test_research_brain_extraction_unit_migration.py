"""Migration tests for the ExtractionUnit layer revision (20261007_01).

Mirrors ``test_research_brain_pilot_migration.py``'s discipline: this
revision must add exactly one table and exactly one nullable column, and
leave every already-frozen table -- both ``M1_TABLES`` and the three other
Research Brain Pilot tables -- byte-for-byte unchanged.
"""

from __future__ import annotations

import sqlalchemy as sa

from app import db
import app.models  # noqa: F401 - registers the current metadata
from migrations.m1_table_inventory import M1_TABLES
from migrations.research_brain_extraction_unit_table_inventory import (
    RESEARCH_BRAIN_EXTRACTION_UNIT_TABLES,
)
from tests.migrations.helpers import (
    schema_snapshot,
    upgrade_database,
    downgrade_database,
)


RESEARCH_BRAIN_PILOT_HEAD = "20261006_01"
EXTRACTION_UNIT_HEAD = "20261007_01"


def _database_url(tmp_path, name: str) -> str:
    return f"sqlite:///{(tmp_path / name).as_posix()}"


def test_upgrade_adds_the_one_table_and_one_column(tmp_path):
    url = _database_url(tmp_path, "extraction-unit-fresh.db")
    upgrade_database(url, RESEARCH_BRAIN_PILOT_HEAD)

    before_m1 = schema_snapshot(url, M1_TABLES)
    before_other_pilot_tables = schema_snapshot(
        url, {"extraction_run", "extracted_fact", "fact_evidence"}
    )

    upgrade_database(url, EXTRACTION_UNIT_HEAD)

    after_m1 = schema_snapshot(url, M1_TABLES)
    assert after_m1 == before_m1, (
        "ExtractionUnit migration must not change any frozen Milestone 1 "
        "table's schema"
    )

    after_other_pilot_tables = schema_snapshot(
        url, {"extraction_run", "extracted_fact", "fact_evidence"}
    )
    assert after_other_pilot_tables == before_other_pilot_tables, (
        "ExtractionUnit migration must not change extraction_run, "
        "extracted_fact, or fact_evidence"
    )

    unit_tables = schema_snapshot(url, RESEARCH_BRAIN_EXTRACTION_UNIT_TABLES)
    assert set(unit_tables) == RESEARCH_BRAIN_EXTRACTION_UNIT_TABLES

    evidence_snapshot = schema_snapshot(url, {"evidence"})["evidence"]
    evidence_columns = {column["name"] for column in evidence_snapshot["columns"]}
    assert "source_extraction_unit_id" in evidence_columns
    source_column = next(
        column
        for column in evidence_snapshot["columns"]
        if column["name"] == "source_extraction_unit_id"
    )
    assert source_column["nullable"] is True


def test_extraction_unit_table_shape_after_upgrade(tmp_path):
    url = _database_url(tmp_path, "extraction-unit-shape.db")
    upgrade_database(url, EXTRACTION_UNIT_HEAD)

    snapshot = schema_snapshot(url, {"extraction_unit"})["extraction_unit"]

    column_names = {column["name"] for column in snapshot["columns"]}
    assert column_names == {
        "id",
        "created_at",
        "extraction_run_id",
        "document_id",
        "unit_type",
        "sequence_number",
        "locator",
        "content_text",
        "created_by_user_id",
    }

    unique_constraints = {
        constraint["name"]: sorted(constraint["columns"])
        for constraint in snapshot["unique_constraints"]
    }
    assert unique_constraints["uq_extraction_unit_run_type_sequence"] == [
        "extraction_run_id",
        "sequence_number",
        "unit_type",
    ]

    foreign_keys = {
        (tuple(fk["columns"]), fk["referred_table"]) for fk in snapshot["foreign_keys"]
    }
    assert (("extraction_run_id",), "extraction_run") in foreign_keys
    assert (("document_id",), "document") in foreign_keys


def test_evidence_foreign_key_to_extraction_unit_after_upgrade(tmp_path):
    url = _database_url(tmp_path, "extraction-unit-evidence-fk.db")
    upgrade_database(url, EXTRACTION_UNIT_HEAD)

    snapshot = schema_snapshot(url, {"evidence"})["evidence"]
    foreign_keys = {
        (tuple(fk["columns"]), fk["referred_table"]) for fk in snapshot["foreign_keys"]
    }
    assert (("source_extraction_unit_id",), "extraction_unit") in foreign_keys


def test_downgrade_removes_the_table_and_column(tmp_path):
    url = _database_url(tmp_path, "extraction-unit-downgrade.db")
    upgrade_database(url, EXTRACTION_UNIT_HEAD)

    before_m1 = schema_snapshot(url, M1_TABLES)
    before_other_pilot_tables = schema_snapshot(
        url, {"extraction_run", "extracted_fact", "fact_evidence"}
    )

    downgrade_database(url, RESEARCH_BRAIN_PILOT_HEAD)

    engine = sa.create_engine(url)
    try:
        remaining_tables = set(sa.inspect(engine).get_table_names())
        evidence_columns = {
            column["name"]
            for column in sa.inspect(engine).get_columns("evidence")
        }
    finally:
        engine.dispose()

    assert not (remaining_tables & RESEARCH_BRAIN_EXTRACTION_UNIT_TABLES)
    assert "source_extraction_unit_id" not in evidence_columns

    after_m1 = schema_snapshot(url, M1_TABLES)
    assert after_m1 == before_m1
    after_other_pilot_tables = schema_snapshot(
        url, {"extraction_run", "extracted_fact", "fact_evidence"}
    )
    assert after_other_pilot_tables == before_other_pilot_tables
