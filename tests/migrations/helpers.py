from pathlib import Path

import sqlalchemy as sa
from alembic import command
from alembic.config import Config

from app import db
import app.models  # noqa: F401 - registers all legacy tables in db.metadata
from scripts.inspect_database_schema import inspect_table


PROJECT_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = PROJECT_ROOT / "migrations"
LEGACY_TABLES = {
    "user",
    "ticker",
    "trade",
    "tag",
    "telegram_verification",
    "trade_tags",
}


def _alembic_config(database_url: str) -> Config:
    config = Config(str(MIGRATIONS_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return config


def create_legacy_schema_copy(database_url: str) -> None:
    metadata = sa.MetaData()
    for table_name in sorted(LEGACY_TABLES):
        db.metadata.tables[table_name].to_metadata(metadata)

    engine = sa.create_engine(database_url)
    try:
        metadata.create_all(engine)
    finally:
        engine.dispose()


def stamp_database(database_url: str, revision: str) -> None:
    command.stamp(_alembic_config(database_url), revision)


def upgrade_database(database_url: str, revision: str) -> None:
    command.upgrade(_alembic_config(database_url), revision)


def downgrade_database(database_url: str, revision: str) -> None:
    command.downgrade(_alembic_config(database_url), revision)


def schema_snapshot(database_url: str, table_names: set[str]) -> dict:
    engine = sa.create_engine(database_url)
    try:
        inspector = sa.inspect(engine)
        existing_tables = set(inspector.get_table_names())
        missing_tables = table_names - existing_tables
        if missing_tables:
            raise AssertionError(f"Missing tables: {sorted(missing_tables)}")

        # inspect_table already reports sorted check constraints.
        return {
            table_name: inspect_table(inspector, table_name)
            for table_name in sorted(table_names)
        }
    finally:
        engine.dispose()
