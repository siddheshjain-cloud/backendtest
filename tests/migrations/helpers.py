from pathlib import Path

import sqlalchemy as sa
from alembic import command
from alembic.config import Config

from scripts.inspect_database_schema import inspect_table


PROJECT_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = PROJECT_ROOT / "migrations"


def upgrade_database(database_url: str, revision: str) -> None:
    config = Config(str(MIGRATIONS_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    command.upgrade(config, revision)


def schema_snapshot(database_url: str, table_names: set[str]) -> dict:
    engine = sa.create_engine(database_url)
    try:
        inspector = sa.inspect(engine)
        existing_tables = set(inspector.get_table_names())
        missing_tables = table_names - existing_tables
        if missing_tables:
            raise AssertionError(f"Missing tables: {sorted(missing_tables)}")

        snapshot = {}
        for table_name in sorted(table_names):
            table = inspect_table(inspector, table_name)
            table["check_constraints"] = sorted(
                (
                    {
                        "name": constraint.get("name"),
                        "sqltext": constraint.get("sqltext"),
                    }
                    for constraint in inspector.get_check_constraints(table_name)
                ),
                key=lambda constraint: (
                    constraint["name"] or "",
                    constraint["sqltext"] or "",
                ),
            )
            snapshot[table_name] = table
        return snapshot
    finally:
        engine.dispose()
