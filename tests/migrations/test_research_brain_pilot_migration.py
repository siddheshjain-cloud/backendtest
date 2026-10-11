"""Migration tests for the additive Research Brain Pilot revision (20261006_01).

Mirrors ``test_m1_additive_migration.py``'s discipline, scoped to this
much simpler revision: it adds exactly four tables and must leave every
frozen Milestone 1 (``M1_TABLES``) table's schema byte-for-byte unchanged.
"""

from __future__ import annotations

import sqlalchemy as sa

from app import db
import app.models  # noqa: F401 - registers the current metadata
from migrations.m1_table_inventory import M1_TABLES
from migrations.research_brain_pilot_table_inventory import (
    RESEARCH_BRAIN_PILOT_TABLES,
)
from tests.migrations.helpers import (
    schema_snapshot,
    upgrade_database,
    downgrade_database,
)


M1_HEAD = "20260904_02"
RESEARCH_BRAIN_PILOT_HEAD = "20261006_01"


def _database_url(tmp_path, name: str) -> str:
    return f"sqlite:///{(tmp_path / name).as_posix()}"


def test_upgrade_adds_exactly_the_four_pilot_tables(tmp_path):
    url = _database_url(tmp_path, "pilot-fresh.db")
    upgrade_database(url, M1_HEAD)

    before = schema_snapshot(url, M1_TABLES)

    upgrade_database(url, RESEARCH_BRAIN_PILOT_HEAD)

    after_m1 = schema_snapshot(url, M1_TABLES)
    assert after_m1 == before, (
        "Research Brain Pilot migration must not change any frozen "
        "Milestone 1 table's schema"
    )

    pilot_tables = schema_snapshot(url, RESEARCH_BRAIN_PILOT_TABLES)
    assert set(pilot_tables) == RESEARCH_BRAIN_PILOT_TABLES


def test_extracted_fact_table_shape_after_upgrade(tmp_path):
    url = _database_url(tmp_path, "pilot-fact-shape.db")
    upgrade_database(url, RESEARCH_BRAIN_PILOT_HEAD)

    snapshot = schema_snapshot(url, {"extracted_fact"})["extracted_fact"]

    column_names = {column["name"] for column in snapshot["columns"]}
    assert column_names == {
        "id",
        "created_at",
        "company_id",
        "fact_type",
        "value_type",
        "value",
        "unit",
        "period",
        "as_of_date",
        "supersedes_fact_id",
        "created_by_user_id",
    }

    check_names = {check["name"] for check in snapshot["check_constraints"]}
    assert "ck_extracted_fact_unit_requires_numeric" in check_names

    foreign_keys = {
        (tuple(fk["columns"]), fk["referred_table"]) for fk in snapshot["foreign_keys"]
    }
    assert (("company_id",), "company") in foreign_keys
    assert (("supersedes_fact_id",), "extracted_fact") in foreign_keys
    assert (("created_by_user_id",), "user") in foreign_keys


def test_fact_evidence_composite_primary_key_after_upgrade(tmp_path):
    url = _database_url(tmp_path, "pilot-fact-evidence-shape.db")
    upgrade_database(url, RESEARCH_BRAIN_PILOT_HEAD)

    snapshot = schema_snapshot(url, {"fact_evidence"})["fact_evidence"]
    assert sorted(snapshot["primary_key"]["columns"]) == ["evidence_id", "fact_id"]


def test_downgrade_removes_exactly_the_four_pilot_tables(tmp_path):
    url = _database_url(tmp_path, "pilot-downgrade.db")
    upgrade_database(url, RESEARCH_BRAIN_PILOT_HEAD)

    before_m1 = schema_snapshot(url, M1_TABLES)

    downgrade_database(url, M1_HEAD)

    engine = sa.create_engine(url)
    try:
        remaining_tables = set(sa.inspect(engine).get_table_names())
    finally:
        engine.dispose()

    assert not (remaining_tables & RESEARCH_BRAIN_PILOT_TABLES)

    after_m1 = schema_snapshot(url, M1_TABLES)
    assert after_m1 == before_m1, (
        "Downgrading the Research Brain Pilot revision must not change any "
        "frozen Milestone 1 table's schema"
    )
