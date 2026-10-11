"""I3AS Slice 1 -- characterization tests.

Pin today's exact Trade -> Telegram alert behavior *before* any I3AS
code touches this call site (``live/websocket.py``'s
``TickerManager.send_trade_notification`` and
``TelegramService.send_trade_alert``). These tests must pass unchanged
both before and after Alert-1's integration -- that is the proof that
nothing about the existing live alert pathway regressed: same message
content, same recipient, same return value, same call sequence. No
real network call (same convention as
``tests/legacy/test_telegram_contract.py``).

``TickerManager.send_trade_notification`` never reads ``self`` (verified
directly from its bytecode, not assumed), so it is called here as an
unbound method against a throwaway object rather than a real
``TickerManager()`` instance -- instantiating the real class would
invoke ``create_app()`` again in its constructor, a heavier and
unrelated side effect this test has no need of.
"""

from __future__ import annotations

from app import db
from app.models.utils import TradeStatus
from app.services.telegram_service import telegram_service
from live.websocket import TickerManager


def _connect_telegram(user, *, chat_id="99999"):
    user.telegram_enabled = True
    user.telegram_chat_id = chat_id
    db.session.commit()


def test_send_trade_alert_entry_hit_message_content(
    free_user, ticker_factory, trade_factory, monkeypatch
):
    _connect_telegram(free_user)
    ticker = ticker_factory(last_price=101.5)
    trade = trade_factory(
        user=free_user, ticker=ticker, status=TradeStatus.ENTRY,
        entry=101.0, stoploss=99.0, target=105.0, notes="swing idea",
    )

    sent = []
    monkeypatch.setattr(
        telegram_service, "send_message",
        lambda chat_id, text: sent.append((chat_id, text)) or True,
    )

    result = telegram_service.send_trade_alert(free_user, trade)

    assert result is True
    assert len(sent) == 1
    chat_id, text = sent[0]
    assert chat_id == "99999"
    assert "Entry Hit" in text
    assert "✅" in text
    assert trade.symbol in text
    assert "₹101.00" in text
    assert "₹101.50" in text
    assert "swing idea" in text


def test_send_trade_alert_stoploss_hit_message_content(
    free_user, ticker_factory, trade_factory, monkeypatch
):
    _connect_telegram(free_user)
    ticker = ticker_factory(last_price=98.75)
    trade = trade_factory(
        user=free_user, ticker=ticker, status=TradeStatus.STOPLOSS,
        entry=101.0, stoploss=99.0, target=105.0,
    )

    sent = []
    monkeypatch.setattr(
        telegram_service, "send_message",
        lambda chat_id, text: sent.append((chat_id, text)) or True,
    )

    result = telegram_service.send_trade_alert(free_user, trade)

    assert result is True
    chat_id, text = sent[0]
    assert "Stop Loss Hit" in text
    assert "🛑" in text
    assert "₹99.00" in text


def test_send_trade_alert_target_hit_message_content(
    free_user, ticker_factory, trade_factory, monkeypatch
):
    _connect_telegram(free_user)
    ticker = ticker_factory(last_price=105.25)
    trade = trade_factory(
        user=free_user, ticker=ticker, status=TradeStatus.TARGET,
        entry=101.0, stoploss=99.0, target=105.0,
    )

    sent = []
    monkeypatch.setattr(
        telegram_service, "send_message",
        lambda chat_id, text: sent.append((chat_id, text)) or True,
    )

    result = telegram_service.send_trade_alert(free_user, trade)

    assert result is True
    chat_id, text = sent[0]
    assert "Target Hit" in text
    assert "🎯" in text
    assert "₹105.00" in text


def test_send_trade_alert_returns_false_for_active_status(
    free_user, ticker_factory, trade_factory, monkeypatch
):
    _connect_telegram(free_user)
    trade = trade_factory(
        user=free_user, ticker=ticker_factory(), status=TradeStatus.ACTIVE,
    )

    sent = []
    monkeypatch.setattr(
        telegram_service, "send_message",
        lambda chat_id, text: sent.append((chat_id, text)) or True,
    )

    result = telegram_service.send_trade_alert(free_user, trade)

    assert result is False
    assert sent == []


def test_send_trade_alert_returns_false_without_telegram_connected(
    free_user, ticker_factory, trade_factory, monkeypatch
):
    trade = trade_factory(
        user=free_user, ticker=ticker_factory(), status=TradeStatus.ENTRY,
    )
    sent = []
    monkeypatch.setattr(
        telegram_service, "send_message",
        lambda chat_id, text: sent.append((chat_id, text)) or True,
    )

    result = telegram_service.send_trade_alert(free_user, trade)

    assert result is False
    assert sent == []


def test_ticker_manager_send_trade_notification_delegates_unconditionally(
    free_user, ticker_factory, trade_factory, monkeypatch
):
    """The call site I3AS will extend: today it does exactly one thing."""

    _connect_telegram(free_user)
    trade = trade_factory(
        user=free_user, ticker=ticker_factory(), status=TradeStatus.ENTRY,
    )
    calls = []
    monkeypatch.setattr(
        telegram_service, "send_trade_alert",
        lambda user, trade: calls.append((user.id, trade.id)) or True,
    )

    TickerManager.send_trade_notification(object(), free_user, trade)

    assert calls == [(free_user.id, trade.id)]
