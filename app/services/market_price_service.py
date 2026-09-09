"""Plan 3 Task 3: read-only market price boundary.

Projects the existing Ticker market state without mutating Ticker, Trade,
Kite, websocket, or Telegram state. The service reads only already-loaded
Ticker attributes and never commits.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.models.ticker import Ticker


def _as_utc(value: datetime) -> datetime:
    """Normalize a stored UTC timestamp, including SQLite naive reloads."""

    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class MarketPriceService:
    """Read-only projection of the existing ticker market price."""

    @staticmethod
    def is_stale(
        as_of: datetime | None,
        now: datetime,
        max_age: timedelta,
    ) -> bool:
        """Return whether a quote timestamp is older than the allowed age."""

        if as_of is None:
            return True
        return _as_utc(now) - _as_utc(as_of) > max_age

    @staticmethod
    def project(
        ticker: Ticker,
        *,
        now: datetime | None = None,
        max_age: timedelta = timedelta(minutes=15),
    ) -> dict[str, object]:
        """Project one read-only current market quote from Ticker attributes.

        ``last_price == 0.0`` means no available market price and yields a null
        quote; a null ``last_updated`` is never fresh. The default 15-minute
        stale threshold is a method argument rather than global market logic.
        """

        check_time = now or datetime.now(timezone.utc)
        if ticker.last_price == 0.0:
            return {"cmp": None, "as_of": None, "stale": True}

        as_of = ticker.last_updated
        return {
            "cmp": ticker.last_price,
            "as_of": None if as_of is None else _as_utc(as_of),
            "stale": MarketPriceService.is_stale(
                as_of, check_time, max_age
            ),
        }
