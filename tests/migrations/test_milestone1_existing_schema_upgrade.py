"""Existing-schema Milestone 1 migration path.

Create an exact legacy schema copy -> stamp ``20260904_01`` -> snapshot the
legacy tables -> ``upgrade head`` -> assert the legacy snapshot is unchanged
and the complete Milestone 1 table set now exists.
"""

from __future__ import annotations

from migrations.m1_table_inventory import M1_TABLES
from scripts.inspect_database_schema import inspect_schema
from tests.migrations.helpers import (
    LEGACY_TABLES,
    assert_m1_partial_index_predicates,
    assert_m1_schema_invariants,
    create_legacy_schema_copy,
    schema_snapshot,
    stamp_database,
    upgrade_database,
)


M1_HEAD = "20260904_02"


def test_existing_schema_copy_upgrades_additively_without_legacy_changes(tmp_path):
    url = f"sqlite:///{(tmp_path / 'existing-schema.db').as_posix()}"

    create_legacy_schema_copy(url)

    before_inventory = inspect_schema(url)
    assert set(before_inventory["tables"]) == LEGACY_TABLES
    assert before_inventory["alembic_version"] == {
        "present": False,
        "versions": [],
    }

    stamp_database(url, "20260904_01")

    legacy_snapshot = schema_snapshot(url, LEGACY_TABLES)

    upgrade_database(url, "head")

    after_inventory = inspect_schema(url)

    assert after_inventory["alembic_version"] == {
        "present": True,
        "versions": [M1_HEAD],
    }
    assert schema_snapshot(url, LEGACY_TABLES) == legacy_snapshot
    assert set(after_inventory["tables"]) == M1_TABLES | LEGACY_TABLES | {
        "alembic_version"
    }

    assert_m1_schema_invariants(after_inventory["tables"])
    assert_m1_partial_index_predicates(url)
