"""Prove the six legacy tables stay equivalent to the frozen baseline.

Plans 2, 4 and 5 run this file at the checkpoints where new Investment Operating
System models join ``db.metadata``. Its job is to fail the moment a legacy table
gains, loses or changes a column, constraint or index relative to revision
``20260904_01`` -- whether that happens through a model edit, a relationship that
writes back to a legacy table, or an M1 model redefining a legacy table name.

All schema knowledge comes from the frozen baseline revision and the existing
helpers; this module declares no schema of its own.
"""

import sqlalchemy as sa

from app import db
import app.models  # noqa: F401 - registers all legacy tables in db.metadata
from scripts.inspect_database_schema import inspect_schema
from tests.migrations.helpers import LEGACY_TABLES, schema_snapshot, upgrade_database


def _model_metadata_database(tmp_path, name: str) -> str:
    """Create every currently declared model, legacy and Milestone 1 alike."""
    url = f"sqlite:///{(tmp_path / name).as_posix()}"
    engine = sa.create_engine(url)
    try:
        db.metadata.create_all(engine)
    finally:
        engine.dispose()
    return url


def _baseline_database(tmp_path, name: str) -> str:
    url = f"sqlite:///{(tmp_path / name).as_posix()}"
    upgrade_database(url, "20260904_01")
    return url


def test_legacy_tables_match_the_frozen_baseline(tmp_path):
    """Full structural equality for the six legacy tables."""
    model_url = _model_metadata_database(tmp_path, "models.db")
    baseline_url = _baseline_database(tmp_path, "baseline.db")

    assert schema_snapshot(model_url, LEGACY_TABLES) == schema_snapshot(
        baseline_url, LEGACY_TABLES
    )


def test_legacy_tables_gain_no_columns_as_milestone_1_models_are_added(tmp_path):
    """A per-table column diff, so a violation names the offending table."""
    model_url = _model_metadata_database(tmp_path, "models-columns.db")
    baseline_url = _baseline_database(tmp_path, "baseline-columns.db")

    model_tables = inspect_schema(model_url)["tables"]
    baseline_tables = inspect_schema(baseline_url)["tables"]

    for table_name in sorted(LEGACY_TABLES):
        model_columns = {
            column["name"] for column in model_tables[table_name]["columns"]
        }
        baseline_columns = {
            column["name"] for column in baseline_tables[table_name]["columns"]
        }
        assert model_columns == baseline_columns, (
            f"legacy table '{table_name}' changed columns: "
            f"added={sorted(model_columns - baseline_columns)} "
            f"removed={sorted(baseline_columns - model_columns)}"
        )


def test_baseline_still_creates_only_the_legacy_schema(tmp_path):
    """New M1 models must never be pulled into the legacy baseline revision."""
    baseline_url = _baseline_database(tmp_path, "baseline-only.db")

    inventory = inspect_schema(baseline_url)

    assert set(inventory["tables"]) == LEGACY_TABLES | {"alembic_version"}
    assert inventory["alembic_version"] == {
        "present": True,
        "versions": ["20260904_01"],
    }


def test_milestone_1_models_do_not_redefine_a_legacy_table(tmp_path):
    """Every legacy table is still declared, and new models sit outside that set."""
    declared = set(db.metadata.tables)

    assert LEGACY_TABLES <= declared

    model_url = _model_metadata_database(tmp_path, "models-tables.db")
    created = set(inspect_schema(model_url)["tables"])

    # Anything beyond the legacy six is a new Milestone 1 table, never a
    # replacement for one of them.
    assert LEGACY_TABLES <= created
