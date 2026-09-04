import ast
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from app import db
from app.models import Ticker, Trade
from live import websocket


FORBIDDEN_IMPORT_PREFIXES = (
    "app.services.research",
    "app.services.document",
    "app.policies",
)


def test_live_websocket_has_no_research_document_or_policy_imports():
    source_path = Path(websocket.__file__)
    tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.append(node.module)

    assert not [
        name for name in imports if name.startswith(FORBIDDEN_IMPORT_PREFIXES)
    ]


def test_update_ticker_price_only_changes_price_and_timestamp(
    app, ticker_factory, monkeypatch
):
    ticker = ticker_factory()
    manager = _isolated_manager(app, monkeypatch)
    unchanged_before = _unchanged_ticker_values(ticker)
    timestamp = datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc)

    manager.update_ticker_price(ticker, 123.45, timestamp)

    db.session.expire_all()
    updated = db.session.get(Ticker, ticker.id)
    assert updated.last_price == 123.45
    assert updated.last_updated.replace(tzinfo=timezone.utc) == timestamp
    assert _unchanged_ticker_values(updated) == unchanged_before


def test_check_trades_uses_legacy_trade_and_notification_paths(app, monkeypatch):
    manager = _isolated_manager(app, monkeypatch)
    calls = []

    class LegacyTrade:
        user = SimpleNamespace(id="user-1")

        def check(self, candle):
            calls.append(("check", candle))
            return True

        def update_etas(self):
            calls.append(("update_etas", None))

    trade = LegacyTrade()
    monkeypatch.setattr(
        Trade,
        "get_active_trades_for_ticker",
        classmethod(lambda cls, ticker_id: calls.append(("query", ticker_id)) or [trade]),
    )
    monkeypatch.setattr(
        manager,
        "send_trade_notification",
        lambda user, changed_trade: calls.append(("notify", (user, changed_trade))),
    )
    candle = SimpleNamespace(high=101.0, low=99.0)

    manager.check_trades("ticker-1", candle)

    assert calls == [
        ("query", "ticker-1"),
        ("check", candle),
        ("notify", (trade.user, trade)),
        ("update_etas", None),
    ]


def _isolated_manager(app, monkeypatch):
    monkeypatch.setattr(websocket, "create_app", lambda: app)
    manager = websocket.TickerManager()
    assert manager.k is None
    assert manager.kws is None
    assert manager.candle_timer is None
    return manager


def _unchanged_ticker_values(ticker):
    return {
        "id": ticker.id,
        "symbol": ticker.symbol,
        "exchange": ticker.exchange,
        "instrument_token": ticker.instrument_token,
        "name": ticker.name,
        "created_at": ticker.created_at,
    }
