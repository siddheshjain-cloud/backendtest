from datetime import datetime, timezone

from app import db
from app.models.telegram_verification import TelegramVerification
from app.services.telegram_service import telegram_service


def test_telegram_status_returns_legacy_shape(client, free_user, auth_headers):
    response = client.get("/api/telegram/status", headers=auth_headers(free_user))

    assert response.status_code == 200
    assert response.get_json() == {
        "connected": False,
        "username": None,
        "connected_at": None,
    }


def test_generate_code_persists_deterministic_verification(
    client, free_user, auth_headers, monkeypatch
):
    monkeypatch.setattr(
        telegram_service, "generate_verification_code", lambda: "TESTCODE"
    )

    response = client.post(
        "/api/telegram/generate-code", headers=auth_headers(free_user)
    )

    payload = response.get_json()
    verification = TelegramVerification.query.filter_by(user_id=free_user.id).one()
    assert response.status_code == 200
    assert set(payload) == {"verification_code", "expires_at", "bot_username"}
    assert payload["verification_code"] == "TESTCODE"
    assert payload["bot_username"] == "your_bot"
    assert verification.verification_code == "TESTCODE"
    assert verification.verified is False


def test_webhook_verifies_code_and_connects_user_without_sending_network_request(
    client, free_user, auth_headers, monkeypatch
):
    monkeypatch.setattr(
        telegram_service, "generate_verification_code", lambda: "TESTCODE"
    )
    welcome_calls = []
    monkeypatch.setattr(
        telegram_service,
        "send_welcome_message",
        lambda chat_id, name: welcome_calls.append((chat_id, name)) or True,
    )
    client.post("/api/telegram/generate-code", headers=auth_headers(free_user))

    response = client.post(
        "/api/telegram/webhook",
        json={
            "message": {
                "chat": {"id": 12345},
                "text": "/start TESTCODE",
                "from": {"username": "ada", "first_name": "Ada"},
            }
        },
    )

    db.session.refresh(free_user)
    assert response.status_code == 200
    assert response.get_json() == {"ok": True}
    assert free_user.telegram_enabled is True
    assert free_user.telegram_chat_id == "12345"
    assert free_user.telegram_username == "ada"
    assert free_user.telegram_connected_at is not None
    assert welcome_calls == [("12345", free_user.name)]


def test_disconnect_clears_existing_connection(client, free_user, auth_headers):
    free_user.telegram_enabled = True
    free_user.telegram_chat_id = "12345"
    free_user.telegram_username = "ada"
    free_user.telegram_connected_at = datetime.now(timezone.utc)
    db.session.commit()

    response = client.post(
        "/api/telegram/disconnect", headers=auth_headers(free_user)
    )

    db.session.refresh(free_user)
    assert response.status_code == 200
    assert response.get_json() == {"message": "Telegram disconnected successfully"}
    assert free_user.telegram_enabled is False
    assert free_user.telegram_chat_id is None
    assert free_user.telegram_username is None
    assert free_user.telegram_connected_at is None


def test_disconnect_rejects_user_without_connection(client, free_user, auth_headers):
    response = client.post(
        "/api/telegram/disconnect", headers=auth_headers(free_user)
    )

    assert response.status_code == 400
    assert response.get_json() == {"error": "Telegram not connected"}


def test_send_message_posts_legacy_telegram_payload(monkeypatch):
    requests_seen = []

    class Response:
        def raise_for_status(self):
            return None

    def fake_post(url, *, json, timeout):
        requests_seen.append((url, json, timeout))
        return Response()

    monkeypatch.setattr("app.services.telegram_service.requests.post", fake_post)

    sent = telegram_service.send_message("12345", "hello")

    assert sent is True
    assert requests_seen == [
        (
            f"{telegram_service.base_url}/sendMessage",
            {"chat_id": "12345", "text": "hello", "parse_mode": "HTML"},
            10,
        )
    ]


def test_telegram_authenticated_routes_require_jwt(client):
    response = client.get("/api/telegram/status")

    assert response.status_code == 401
    assert response.get_json() == {"msg": "Missing Authorization Header"}
