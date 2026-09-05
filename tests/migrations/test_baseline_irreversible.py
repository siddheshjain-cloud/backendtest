import pytest
from alembic.util.exc import CommandError

from scripts.inspect_database_schema import inspect_schema
from tests.migrations.helpers import (
    LEGACY_TABLES,
    create_legacy_schema_copy,
    downgrade_database,
    schema_snapshot,
    stamp_database,
)


def test_stamped_legacy_baseline_refuses_downgrade_to_base(tmp_path):
    database_url = f"sqlite:///{(tmp_path / 'irreversible-baseline.db').as_posix()}"

    create_legacy_schema_copy(database_url)
    stamp_database(database_url, "20260904_01")
    before_snapshot = schema_snapshot(database_url, LEGACY_TABLES)

    with pytest.raises(
        CommandError,
        match=r"20260904_01.*irreversible legacy baseline.*must not be downgraded to base",
    ):
        downgrade_database(database_url, "base")

    assert schema_snapshot(database_url, LEGACY_TABLES) == before_snapshot
    assert inspect_schema(database_url)["alembic_version"] == {
        "present": True,
        "versions": ["20260904_01"],
    }
