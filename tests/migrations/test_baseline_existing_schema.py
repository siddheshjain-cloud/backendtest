from scripts.inspect_database_schema import inspect_schema
from tests.migrations.helpers import (
    create_legacy_schema_copy,
    schema_snapshot,
    stamp_database,
)


LEGACY_TABLES = {
    "user",
    "ticker",
    "trade",
    "tag",
    "telegram_verification",
    "trade_tags",
}


def test_existing_legacy_schema_can_be_stamped_without_schema_changes(tmp_path):
    database_url = f"sqlite:///{(tmp_path / 'existing-schema.db').as_posix()}"

    create_legacy_schema_copy(database_url)

    before_inventory = inspect_schema(database_url)
    before_tables = set(before_inventory["tables"])
    before_snapshot = schema_snapshot(database_url, LEGACY_TABLES)

    assert before_tables == LEGACY_TABLES
    assert before_inventory["alembic_version"] == {
        "present": False,
        "versions": [],
    }

    stamp_database(database_url, "20260904_01")

    after_inventory = inspect_schema(database_url)
    after_tables = set(after_inventory["tables"])
    after_snapshot = schema_snapshot(database_url, LEGACY_TABLES)

    assert after_inventory["alembic_version"] == {
        "present": True,
        "versions": ["20260904_01"],
    }
    assert after_tables - before_tables == {"alembic_version"}
    assert after_tables == LEGACY_TABLES | {"alembic_version"}
    assert after_snapshot == before_snapshot
