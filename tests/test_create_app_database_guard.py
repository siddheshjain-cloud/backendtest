"""Root-cause safeguard for the 2026-10-12 live-database incident: an
ad-hoc debugging script called ``create_app()`` with no arguments and
silently connected to the real ``instance/trading_app.db``, because no
``DATABASE_URL``/``.env`` is configured in this environment and nothing
distinguished that call from a legitimate production entrypoint's.

These tests exercise ``app._require_explicit_database_opt_in`` directly
(a pure, dependency-free function -- no Flask app, no engine, no I/O)
for the "allowed" cases, so this file can never itself open the real
database file. Only the "rejected" case is also exercised through the
full ``create_app()`` call, which is safe specifically because the
guard is unconditionally the first statement in ``create_app()`` and
raises before any ``Flask``/``db`` object is even constructed.
"""

from __future__ import annotations

import pytest

from app import _DEFAULT_SQLITE_URI, _require_explicit_database_opt_in, create_app


class _FakeConfig:
    SQLALCHEMY_DATABASE_URI = _DEFAULT_SQLITE_URI


class _FakeTestConfig:
    SQLALCHEMY_DATABASE_URI = "sqlite:///C:/some/tmp/test-123.db"


def test_bare_default_uri_without_opt_in_is_rejected():
    with pytest.raises(RuntimeError, match="live database"):
        _require_explicit_database_opt_in(_FakeConfig, allow_default_database=False)


def test_bare_default_uri_with_opt_in_is_allowed():
    _require_explicit_database_opt_in(_FakeConfig, allow_default_database=True)


def test_overridden_uri_is_never_blocked_even_without_opt_in():
    """This is exactly the tests/conftest.py `app` fixture's own shape:
    it overrides SQLALCHEMY_DATABASE_URI before calling create_app() and
    never passes allow_default_database -- this must keep working."""

    _require_explicit_database_opt_in(_FakeTestConfig, allow_default_database=False)


def test_create_app_with_no_arguments_fails_closed():
    """The exact incident call shape. Must raise before touching any
    Flask/db object -- proven by the fact that this test can run inside
    the normal unisolated test process at all without ever creating
    instance/trading_app.db or any WAL/journal file alongside it."""

    with pytest.raises(RuntimeError, match="live database"):
        create_app()
