import sqlalchemy as sa

from app import db
import app.models  # noqa: F401 - registers all legacy tables in db.metadata
from scripts.inspect_database_schema import inspect_schema
from tests.migrations.helpers import schema_snapshot, upgrade_database


LEGACY_TABLES = {
    "ticker",
    "user",
    "tag",
    "telegram_verification",
    "trade",
    "trade_tags",
}


def test_legacy_baseline_matches_current_model_schema(tmp_path):
    model_url = f"sqlite:///{(tmp_path / 'model-schema.db').as_posix()}"
    baseline_url = f"sqlite:///{(tmp_path / 'baseline-schema.db').as_posix()}"

    model_engine = sa.create_engine(model_url)
    try:
        db.metadata.create_all(model_engine)
    finally:
        model_engine.dispose()

    upgrade_database(baseline_url, "20260904_01")

    inventory = inspect_schema(baseline_url)
    assert set(inventory["tables"]) == LEGACY_TABLES | {"alembic_version"}
    assert inventory["alembic_version"] == {
        "present": True,
        "versions": ["20260904_01"],
    }
    assert schema_snapshot(baseline_url, LEGACY_TABLES) == schema_snapshot(
        model_url, LEGACY_TABLES
    )


def test_legacy_baseline_contains_no_investment_operating_system_tables(tmp_path):
    baseline_url = f"sqlite:///{(tmp_path / 'baseline-only.db').as_posix()}"

    upgrade_database(baseline_url, "20260904_01")

    table_names = set(inspect_schema(baseline_url)["tables"])
    assert table_names == LEGACY_TABLES | {"alembic_version"}
    assert not {
        name
        for name in table_names
        if "research" in name or "document" in name or "entitlement" in name
    }
