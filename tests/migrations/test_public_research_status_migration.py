"""Anonymous public-access proposal §2.2: migration round-trip for
``company.public_research_status``.

Isolated temporary SQLite only, exactly like every other migration test in
this suite -- never the live ``instance/trading_app.db``.
"""

from __future__ import annotations

import sqlalchemy as sa

from tests.migrations.helpers import downgrade_database, upgrade_database


NEW_REVISION = "dce47b38cde9"
PRIOR_REVISION = "1848bf398f95"


def _columns(database_url: str, table_name: str) -> dict[str, dict]:
    engine = sa.create_engine(database_url)
    try:
        inspector = sa.inspect(engine)
        return {
            column["name"]: column
            for column in inspector.get_columns(table_name)
        }
    finally:
        engine.dispose()


def test_upgrade_adds_a_nonnullable_defaulted_column(tmp_path):
    url = f"sqlite:///{(tmp_path / 'public-status-upgrade.db').as_posix()}"
    upgrade_database(url, "head")

    columns = _columns(url, "company")

    assert "public_research_status" in columns
    assert columns["public_research_status"]["nullable"] is False


def test_an_existing_company_row_backfills_to_not_public(tmp_path):
    url = f"sqlite:///{(tmp_path / 'public-status-backfill.db').as_posix()}"
    # Build the schema up to just before this migration, insert a company
    # row the old way, then upgrade past it and confirm the new column
    # backfilled to the fail-closed default rather than NULL or an error.
    upgrade_database(url, PRIOR_REVISION)

    engine = sa.create_engine(url)
    try:
        with engine.begin() as connection:
            connection.execute(
                sa.text(
                    "INSERT INTO ticker "
                    "(id, symbol, instrument_token, exchange, name, "
                    "last_price, last_updated, created_at) "
                    "VALUES ('t1', 'PRE', 1, 'NSE', 'Pre-existing Ticker', "
                    "10.0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                )
            )
            connection.execute(
                sa.text(
                    "INSERT INTO company "
                    "(id, ticker_id, legal_name, isin, updated_at, created_at) "
                    "VALUES ('c1', 't1', 'Pre-existing Co', 'INE0PRE01019', "
                    "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                )
            )
    finally:
        engine.dispose()

    upgrade_database(url, NEW_REVISION)

    engine = sa.create_engine(url)
    try:
        with engine.connect() as connection:
            status = connection.execute(
                sa.text(
                    "SELECT public_research_status FROM company WHERE id = 'c1'"
                )
            ).scalar_one()
    finally:
        engine.dispose()

    assert status == "NOT_PUBLIC"


def test_downgrade_cleanly_removes_the_column(tmp_path):
    url = f"sqlite:///{(tmp_path / 'public-status-downgrade.db').as_posix()}"
    upgrade_database(url, "head")
    assert "public_research_status" in _columns(url, "company")

    downgrade_database(url, PRIOR_REVISION)

    assert "public_research_status" not in _columns(url, "company")
