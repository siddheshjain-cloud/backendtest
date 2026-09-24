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


def assert_m1_schema_invariants(tables: dict[str, dict]) -> None:
    """Assert the cross-cutting Milestone 1 schema invariants.

    ``tables`` maps each table name to its ``inspect_table`` result. These
    invariants are the migration-facing subset of the frozen Milestone 1
    design: the one-row entitlement constraint, nullable forecast EPS, the
    independent valuation-method stream, ordered valuation reference lines, a
    non-null unique document fingerprint, the document/company link identity,
    canonical institution uniqueness, and the document audit relationships.
    """

    entitlement = tables["user_entitlement"]
    assert any(
        set(constraint["columns"]) == {"user_id", "product_code"}
        for constraint in entitlement["unique_constraints"]
    )

    forecast_line = tables["forecast_line"]
    eps = next(
        column for column in forecast_line["columns"] if column["name"] == "eps"
    )
    assert eps["nullable"] is True

    valuation = tables["valuation_revision"]
    assert any(
        set(constraint["columns"])
        == {"company_id", "valuation_method", "revision_number"}
        for constraint in valuation["unique_constraints"]
    )

    reference = tables["valuation_reference_line"]
    assert any(
        set(constraint["columns"]) == {"valuation_revision_id", "sort_order"}
        for constraint in reference["unique_constraints"]
    )
    assert any(
        check["name"] == "ck_valuation_reference_line_sort_order_nonnegative"
        for check in reference["check_constraints"]
    )

    document = tables["document"]
    fingerprint = next(
        column
        for column in document["columns"]
        if column["name"] == "metadata_fingerprint"
    )
    assert fingerprint["nullable"] is False
    assert any(
        index["unique"] and index["columns"] == ["metadata_fingerprint"]
        for index in document["indexes"]
    )

    document_link = tables["document_company_link"]
    assert set(document_link["primary_key"]["columns"]) == {
        "document_id",
        "company_id",
    }
    assert any(
        index["unique"] and index["columns"] == ["document_id"]
        for index in document_link["indexes"]
    )

    institution = tables["institution"]
    assert any(
        set(constraint["columns"]) == {"normalized_name"}
        for constraint in institution["unique_constraints"]
    )

    audit = tables["document_audit_event"]
    audit_foreign_keys = {
        (tuple(foreign_key["columns"]), foreign_key["referred_table"])
        for foreign_key in audit["foreign_keys"]
    }
    assert (("document_id",), "document") in audit_foreign_keys
    assert (("actor_user_id",), "user") in audit_foreign_keys
