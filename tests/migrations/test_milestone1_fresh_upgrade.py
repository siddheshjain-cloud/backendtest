"""Fresh-database Milestone 1 migration path.

Empty temporary SQLite -> ``upgrade head`` -> assert the legacy schema plus the
complete Milestone 1 table set, constraints, indexes, and revision marker.
"""

from __future__ import annotations

from app import db
import app.models  # noqa: F401 - registers the current Milestone 1 metadata
from scripts.inspect_database_schema import inspect_schema
from tests.migrations.helpers import (
    LEGACY_TABLES,
    assert_m1_schema_invariants,
    upgrade_database,
)


M1_HEAD = "20260904_02"


def _fresh_database(tmp_path, name: str) -> str:
    url = f"sqlite:///{(tmp_path / name).as_posix()}"
    upgrade_database(url, "head")
    return url


def test_fresh_upgrade_reaches_head_with_legacy_and_m1_tables(tmp_path):
    url = _fresh_database(tmp_path, "fresh-head.db")

    inventory = inspect_schema(url)

    assert inventory["alembic_version"] == {
        "present": True,
        "versions": [M1_HEAD],
    }
    assert set(inventory["tables"]) == set(db.metadata.tables) | {
        "alembic_version"
    }
    assert LEGACY_TABLES <= set(inventory["tables"])


def test_fresh_upgrade_applies_m1_schema_invariants(tmp_path):
    url = _fresh_database(tmp_path, "fresh-invariants.db")

    inventory = inspect_schema(url)

    assert_m1_schema_invariants(inventory["tables"])
