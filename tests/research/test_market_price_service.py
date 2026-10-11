"""Plan 3 Task 3: read-only market price boundary tests.

Covers the frozen MarketPriceService contract: current quotes, stale quotes,
zero/unavailable and null-timestamp handling, the default 15-minute stale
threshold supplied as a method argument, and a strict guarantee that
projection never mutates Ticker or Trade state and never commits.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import sqlalchemy as sa

from app import db
from app.models import Ticker, Trade
from app.services.market_price_service import MarketPriceService


UTC = timezone.utc
NOW = datetime(2026, 9, 4, 10, 0, tzinfo=UTC)
DEFAULT_MAX_AGE = timedelta(minutes=15)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _object_snapshot(*models) -> tuple[tuple, ...]:
    """Snapshot loaded SQLAlchemy column attribute history for each model."""

    snapshots: list[tuple] = []
    for model in models:
        state = sa.inspect(model)
        columns = set(model.__table__.columns.keys())
        # Load expired columns so attribute history has a stable unchanged view.
        for key in sorted(columns):
            getattr(model, key)
        histories = tuple(
            (
                key,
                tuple(state.attrs[key].history.added),
                tuple(state.attrs[key].history.deleted),
                tuple(state.attrs[key].history.unchanged),
            )
            for key in sorted(columns)
        )
        snapshots.append(histories)
    return tuple(snapshots)


def _session_snapshot() -> tuple[frozenset, frozenset, frozenset]:
    return (
        frozenset(id(obj) for obj in db.session.dirty),
        frozenset(id(obj) for obj in db.session.new),
        frozenset(id(obj) for obj in db.session.deleted),
    )


def test_project_returns_current_quote_when_timestamp_is_fresh(
    app, ticker_factory
):
    timestamp = NOW - timedelta(minutes=5)
    ticker = ticker_factory(last_price=123.45)
    ticker.last_updated = timestamp
    db.session.commit()

    quote = MarketPriceService.project(ticker, now=NOW)

    assert quote == {
        "cmp": 123.45,
        "as_of": _as_utc(timestamp),
        "stale": False,
    }


def test_project_marks_quote_stale_past_default_fifteen_minute_threshold(
    app, ticker_factory
):
    timestamp = NOW - timedelta(minutes=16)
    ticker = ticker_factory(last_price=123.45)
    ticker.last_updated = timestamp
    db.session.commit()

    quote = MarketPriceService.project(ticker, now=NOW)

    assert quote == {
        "cmp": 123.45,
        "as_of": _as_utc(timestamp),
        "stale": True,
    }


def test_project_accepts_an_explicit_stale_threshold_argument(
    app, ticker_factory
):
    timestamp = NOW - timedelta(minutes=20)
    ticker = ticker_factory(last_price=123.45)
    ticker.last_updated = timestamp
    db.session.commit()

    quote = MarketPriceService.project(
        ticker,
        now=NOW,
        max_age=timedelta(minutes=30),
    )

    assert quote == {
        "cmp": 123.45,
        "as_of": _as_utc(timestamp),
        "stale": False,
    }


def test_project_treats_zero_last_price_as_unavailable(app, ticker_factory):
    timestamp = NOW - timedelta(minutes=1)
    ticker = ticker_factory(last_price=0.0)
    ticker.last_updated = timestamp
    db.session.commit()

    quote = MarketPriceService.project(ticker, now=NOW)

    assert quote == {"cmp": None, "as_of": None, "stale": True}


def test_project_treats_null_last_updated_as_stale():
    ticker = Ticker(
        symbol="IKIO-NULL",
        exchange="NSE",
        instrument_token=1001,
        name="IKIO Null Timestamp",
        last_price=123.45,
    )
    assert ticker.last_updated is None

    quote = MarketPriceService.project(ticker, now=NOW)

    assert quote == {"cmp": 123.45, "as_of": None, "stale": True}


def test_is_stale_returns_true_for_null_timestamp():
    assert MarketPriceService.is_stale(None, NOW, DEFAULT_MAX_AGE) is True


def test_is_stale_uses_strictly_older_than_max_age_boundary():
    exact_limit = NOW - DEFAULT_MAX_AGE
    past_limit = NOW - DEFAULT_MAX_AGE - timedelta(seconds=1)

    assert (
        MarketPriceService.is_stale(exact_limit, NOW, DEFAULT_MAX_AGE)
        is False
    )
    assert (
        MarketPriceService.is_stale(past_limit, NOW, DEFAULT_MAX_AGE)
        is True
    )


def test_projection_never_mutates_ticker_or_trade_and_never_commits(
    app, user_factory, ticker_factory, trade_factory, monkeypatch
):
    ticker = ticker_factory(last_price=123.45)
    user = user_factory(email="trader@example.com")
    trade = trade_factory(user=user, ticker=ticker)

    before = _object_snapshot(ticker, trade)
    session_before = _session_snapshot()
    commits: list[tuple] = []
    monkeypatch.setattr(
        db.session,
        "commit",
        lambda *args, **kwargs: commits.append((args, kwargs)),
    )

    MarketPriceService.project(ticker, now=NOW)

    assert commits == []
    assert session_before == _session_snapshot()
    assert before == _object_snapshot(ticker, trade)
    assert ticker not in db.session.dirty
    assert trade not in db.session.dirty
