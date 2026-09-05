"""Prove the real Flask-Migrate/Alembic operator path uses the application's database.

The migration helpers in ``tests/migrations/helpers.py`` inject ``sqlalchemy.url``
programmatically, so they cannot detect a broken operator path. These tests drive
``flask_migrate`` inside a Flask application context exactly as the documented
``flask --app main:app db ...`` commands do.
"""

import pytest
from alembic.util.exc import CommandError
from flask_migrate import stamp, upgrade

from app import create_app
from config import Config
from scripts.inspect_database_schema import inspect_schema
from tests.migrations.helpers import LEGACY_TABLES, MIGRATIONS_DIR, upgrade_database


def _operator_app(database_url: str):
    class OperatorConfig(Config):
        SQLALCHEMY_DATABASE_URI = database_url
        TESTING = True
        ELASTICSEARCH_URL = None

    return create_app(OperatorConfig)


def _sqlite_url(path) -> str:
    return f"sqlite:///{path.as_posix()}"


def test_flask_migrate_upgrade_targets_the_configured_application_database(tmp_path):
    database_url = _sqlite_url(tmp_path / "operator-upgrade.db")

    with _operator_app(database_url).app_context():
        upgrade(directory=str(MIGRATIONS_DIR), revision="20260904_01")

    inventory = inspect_schema(database_url)
    assert set(inventory["tables"]) == LEGACY_TABLES | {"alembic_version"}
    assert inventory["alembic_version"] == {
        "present": True,
        "versions": ["20260904_01"],
    }


def test_flask_migrate_stamp_targets_the_configured_application_database(tmp_path):
    database_url = _sqlite_url(tmp_path / "operator-stamp.db")
    application = _operator_app(database_url)

    with application.app_context():
        from app import db

        db.create_all()
        stamp(directory=str(MIGRATIONS_DIR), revision="20260904_01")

    inventory = inspect_schema(database_url)
    assert inventory["alembic_version"] == {
        "present": True,
        "versions": ["20260904_01"],
    }
    assert LEGACY_TABLES <= set(inventory["tables"])


def test_explicit_programmatic_url_still_wins_over_application_configuration(tmp_path):
    """The migration test helpers must keep full control of their target database."""
    helper_url = _sqlite_url(tmp_path / "helper-target.db")
    application_url = _sqlite_url(tmp_path / "application-target.db")

    with _operator_app(application_url).app_context():
        upgrade_database(helper_url, "20260904_01")

    helper_inventory = inspect_schema(helper_url)
    assert helper_inventory["alembic_version"] == {
        "present": True,
        "versions": ["20260904_01"],
    }
    assert LEGACY_TABLES <= set(helper_inventory["tables"])

    application_inventory = inspect_schema(application_url)
    assert application_inventory["tables"] == {}


def test_url_containing_a_percent_sign_is_not_corrupted(tmp_path):
    """A literal '%' (legal in a password or query string) must survive verbatim."""
    target = tmp_path / "pct%sign.db"
    database_url = _sqlite_url(target)
    assert "%" in database_url

    with _operator_app(database_url).app_context():
        upgrade(directory=str(MIGRATIONS_DIR), revision="20260904_01")

    assert target.exists(), "migration was applied to a different, escaped path"
    assert LEGACY_TABLES <= set(inspect_schema(database_url)["tables"])


def test_missing_url_and_application_context_fails_instead_of_using_memory():
    """No silent in-memory fallback: an unresolvable target must fail loudly.

    ``flask_migrate`` wraps ``CommandError``/``RuntimeError`` in ``sys.exit(1)``,
    so ``SystemExit`` is the observable operator-facing outcome.
    """
    with pytest.raises((SystemExit, CommandError, RuntimeError)):
        upgrade(directory=str(MIGRATIONS_DIR), revision="20260904_01")
