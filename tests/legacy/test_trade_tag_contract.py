import pytest

from app import db
from app.models import Tag, Trade
from app.models.utils import TradeETA, TradeStatus


TRADE_KEYS = {
    "id",
    "symbol",
    "last_price",
    "status",
    "side",
    "type",
    "notes",
    "entry",
    "stoploss",
    "target",
    "timeframe",
    "score",
    "entry_x",
    "stoploss_x",
    "target_x",
    "entry_eta",
    "stoploss_eta",
    "target_eta",
    "entry_at",
    "stoploss_at",
    "target_at",
    "created_at",
    "edited_at",
    "updated_at",
    "status_updated_at",
    "ticker",
    "risk_reward_ratio",
    "risk_per_unit",
    "reward_per_unit",
    "tags",
}


def test_create_trade_returns_legacy_payload(client, free_user, auth_headers, ticker_factory):
    ticker = ticker_factory()

    response = client.post(
        "/api/trades/",
        headers=auth_headers(free_user),
        json={
            "ticker_id": ticker.id,
            "side": "BUY",
            "entry": 101.0,
            "stoploss": 99.0,
            "target": 105.0,
            "tags": [{"name": "swing"}],
        },
    )

    payload = response.get_json()
    assert response.status_code == 201
    assert set(payload) == {"message", "trade"}
    assert payload["message"] == "Trade created successfully"
    assert set(payload["trade"]) == TRADE_KEYS
    assert payload["trade"]["type"] == "Crossing Above"
    assert payload["trade"]["status"] == "Active"
    assert payload["trade"]["entry_eta"] == "1 Hour"
    assert payload["trade"]["tags"] == [{"id": payload["trade"]["tags"][0]["id"], "name": "swing"}]


def test_read_trade_returns_owner_trade(client, free_user, auth_headers, ticker_factory, trade_factory):
    trade = trade_factory(user=free_user, ticker=ticker_factory())

    response = client.get(f"/api/trades/{trade.id}", headers=auth_headers(free_user))

    assert response.status_code == 200
    assert set(response.get_json()) == {"trade"}
    assert set(response.get_json()["trade"]) == TRADE_KEYS
    assert response.get_json()["trade"]["id"] == trade.id


def test_update_trade_returns_legacy_payload(client, free_user, auth_headers, ticker_factory, trade_factory):
    trade = trade_factory(user=free_user, ticker=ticker_factory())

    response = client.put(
        f"/api/trades/{trade.id}",
        headers=auth_headers(free_user),
        json={"notes": "updated", "tags": [{"name": "position"}]},
    )

    payload = response.get_json()
    assert response.status_code == 200
    assert set(payload) == {"message", "trade"}
    assert payload["message"] == "Trade updated successfully"
    assert payload["trade"]["notes"] == "updated"
    assert payload["trade"]["tags"][0]["name"] == "position"
    assert payload["trade"]["edited_at"] is not None


def test_delete_trade_returns_message(client, free_user, auth_headers, ticker_factory, trade_factory):
    trade = trade_factory(user=free_user, ticker=ticker_factory())

    response = client.delete(f"/api/trades/{trade.id}", headers=auth_headers(free_user))

    assert response.status_code == 200
    assert response.get_json() == {"message": "Trade deleted successfully"}
    assert db.session.get(Trade, trade.id) is None


def test_trade_creation_reuses_tags_for_one_user_but_not_between_users(
    client, free_user, premium_user, auth_headers, ticker_factory
):
    ticker = ticker_factory()
    request_json = {
        "ticker_id": ticker.id,
        "side": "BUY",
        "entry": 101.0,
        "tags": [{"name": "swing"}],
    }

    client.post("/api/trades/", headers=auth_headers(free_user), json=request_json)
    client.post("/api/trades/", headers=auth_headers(free_user), json=request_json)
    client.post("/api/trades/", headers=auth_headers(premium_user), json=request_json)

    tags = Tag.query.filter_by(name="swing").all()
    assert len(tags) == 2
    assert {tag.user_id for tag in tags} == {free_user.id, premium_user.id}


def test_tag_search_returns_matches_across_users(
    client, free_user, premium_user, auth_headers
):
    db.session.add_all(
        [
            Tag(name="swing", user_id=free_user.id),
            Tag(name="swing", user_id=premium_user.id),
        ]
    )
    db.session.commit()

    response = client.get("/api/tags/?q=swi", headers=auth_headers(free_user))

    payload = response.get_json()
    assert response.status_code == 200
    assert set(payload) == {"tags", "total", "page", "per_page"}
    assert payload["total"] == 2
    assert payload["page"] == 1
    assert payload["per_page"] == 20
    assert [tag["name"] for tag in payload["tags"]] == ["swing", "swing"]


@pytest.mark.parametrize(
    ("initial_status", "high", "low", "expected_status", "timestamp_field"),
    [
        (TradeStatus.ACTIVE, 101.0, 100.0, TradeStatus.ENTRY, "entry_at"),
        (TradeStatus.ENTRY, 100.0, 98.0, TradeStatus.STOPLOSS, "stoploss_at"),
        (TradeStatus.ENTRY, 105.0, 100.0, TradeStatus.TARGET, "target_at"),
    ],
)
def test_trade_check_applies_existing_buy_transitions(
    free_user,
    ticker_factory,
    trade_factory,
    candle_factory,
    initial_status,
    high,
    low,
    expected_status,
    timestamp_field,
):
    trade = trade_factory(
        user=free_user, ticker=ticker_factory(), status=initial_status
    )
    prior_updated_at = trade.updated_at

    changed = trade.check(candle_factory(high=high, low=low))

    assert changed is True
    assert trade.status == expected_status
    assert getattr(trade, timestamp_field) is not None
    assert trade.status_updated_at is not None
    assert trade.updated_at >= prior_updated_at


def test_trade_check_returns_false_when_no_threshold_is_crossed(
    free_user, ticker_factory, trade_factory, candle_factory
):
    trade = trade_factory(user=free_user, ticker=ticker_factory())

    changed = trade.check(candle_factory(high=100.5, low=100.0))

    assert changed is False
    assert trade.status == TradeStatus.ACTIVE
    assert trade.status_updated_at is None


def test_update_etas_uses_current_status_and_ticker_price(
    free_user, ticker_factory, trade_factory
):
    trade = trade_factory(
        user=free_user,
        ticker=ticker_factory(last_price=100.0),
        entry=101.0,
        stoploss=98.0,
        target=105.0,
    )

    trade.update_etas()
    assert (trade.entry_eta, trade.stoploss_eta, trade.target_eta) == (
        TradeETA.ONE_HOUR,
        None,
        None,
    )

    trade.status = TradeStatus.ENTRY
    trade.update_etas()
    assert (trade.entry_eta, trade.stoploss_eta, trade.target_eta) == (
        None,
        TradeETA.ONE_DAY,
        TradeETA.ONE_WEEK,
    )

    trade.status = TradeStatus.TARGET
    trade.update_etas()
    assert (trade.entry_eta, trade.stoploss_eta, trade.target_eta) == (None, None, None)


def test_trade_and_tag_routes_reject_missing_and_invalid_jwts(client):
    missing_trade = client.get("/api/trades/")
    invalid_trade = client.get(
        "/api/trades/", headers={"Authorization": "Bearer invalid"}
    )
    missing_tag = client.get("/api/tags/?q=swing")

    assert missing_trade.status_code == 401
    assert missing_trade.get_json() == {"msg": "Missing Authorization Header"}
    assert invalid_trade.status_code == 422
    assert invalid_trade.get_json() == {"msg": "Not enough segments"}
    assert missing_tag.status_code == 401
    assert missing_tag.get_json() == {"msg": "Missing Authorization Header"}
