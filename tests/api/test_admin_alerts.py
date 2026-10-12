"""Continuous Monitoring V1 follow-up: ``GET /api/admin/alerts``.

Covers: admin-only access (401/403 for anonymous/non-admin callers),
newest-first ordering across heterogeneous event sources, the
``limit`` query param's validation bounds, and that the response dumps
exactly the fields ``AlertEvent`` carries -- no interpretation added.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.services.alert_service import AlertService


def _seed_event(event_type: str, source_id: str, occurred_at: datetime, **kwargs):
    return AlertService.record_event(
        event_type=event_type,
        source_table="trade",
        source_id=source_id,
        summary=f"{event_type} for {source_id}",
        recipient_user_ids=[],
        occurred_at=occurred_at,
        **kwargs,
    )


def test_anonymous_request_is_rejected(client):
    response = client.get("/api/admin/alerts/")
    assert response.status_code == 401


def test_non_admin_is_rejected(client, free_user, auth_headers):
    response = client.get("/api/admin/alerts/", headers=auth_headers(free_user))
    assert response.status_code == 403
    assert response.get_json()["error"] == "Admin access required"


def test_admin_sees_recent_events_newest_first(client, admin_user, auth_headers):
    now = datetime.now(timezone.utc)
    _seed_event("TRADE_ENTRY_HIT", "trade-older", now - timedelta(hours=2))
    _seed_event("TRADE_STOPLOSS_HIT", "trade-newer", now - timedelta(minutes=5))

    response = client.get("/api/admin/alerts/", headers=auth_headers(admin_user))

    assert response.status_code == 200
    items = response.get_json()["items"]
    assert len(items) == 2
    assert items[0]["event_type"] == "TRADE_STOPLOSS_HIT"
    assert items[1]["event_type"] == "TRADE_ENTRY_HIT"
    assert set(items[0].keys()) == {
        "id", "event_type", "source_table", "source_id", "company_id",
        "severity", "summary", "details", "occurred_at",
    }


def test_limit_query_param_is_respected(client, admin_user, auth_headers):
    now = datetime.now(timezone.utc)
    for i in range(5):
        _seed_event("TRADE_ENTRY_HIT", f"trade-{i}", now - timedelta(minutes=i))

    response = client.get(
        "/api/admin/alerts/?limit=2", headers=auth_headers(admin_user)
    )

    assert response.status_code == 200
    assert len(response.get_json()["items"]) == 2


def test_limit_out_of_bounds_is_rejected(client, admin_user, auth_headers):
    response = client.get(
        "/api/admin/alerts/?limit=201", headers=auth_headers(admin_user)
    )

    assert response.status_code == 400
    assert response.get_json()["error"] == "validation_error"


def test_no_events_returns_empty_list(client, admin_user, auth_headers):
    response = client.get("/api/admin/alerts/", headers=auth_headers(admin_user))

    assert response.status_code == 200
    assert response.get_json()["items"] == []
