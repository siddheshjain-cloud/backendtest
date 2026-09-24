"""Fresh-database Milestone 1 migration path.

Empty temporary SQLite -> ``upgrade head`` -> assert the legacy schema plus the
complete Milestone 1 table set, constraints, indexes, and revision marker.
"""

from __future__ import annotations

from migrations.m1_table_inventory import M1_TABLES
from scripts.inspect_database_schema import inspect_schema
from tests.migrations.helpers import (
    LEGACY_TABLES,
    assert_m1_partial_index_predicates,
    assert_m1_schema_invariants,
    schema_snapshot,
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
    assert set(inventory["tables"]) == M1_TABLES | LEGACY_TABLES | {
        "alembic_version"
    }


def test_fresh_upgrade_leaves_legacy_tables_unchanged(tmp_path):
    """Even starting from empty, the additive revision must not touch legacy
    tables. Snapshot the legacy baseline before applying 20260904_02 and
    assert it is unchanged after -- the fresh path otherwise never proves
    this, unlike the existing-schema path.
    """

    url = f"sqlite:///{(tmp_path / 'fresh-legacy-snapshot.db').as_posix()}"
    upgrade_database(url, "20260904_01")

    legacy_snapshot = schema_snapshot(url, LEGACY_TABLES)

    upgrade_database(url, "head")

    assert schema_snapshot(url, LEGACY_TABLES) == legacy_snapshot


def test_fresh_upgrade_applies_m1_schema_invariants(tmp_path):
    url = _fresh_database(tmp_path, "fresh-invariants.db")

    inventory = inspect_schema(url)

    assert_m1_schema_invariants(inventory["tables"])
    assert_m1_partial_index_predicates(url)
