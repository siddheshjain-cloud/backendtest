def test_register_requires_phone_number(client):
    response = client.post(
        "/api/auth/register",
        json={"name": "Ada", "email": "ada@example.com", "password": "password"},
    )

    assert response.status_code == 400
    assert response.get_json() == {
        "error": "Validation error",
        "details": {"phone_number": ["Missing data for required field."]},
    }


def test_register_returns_user_and_token_pair(client):
    response = client.post(
        "/api/auth/register",
        json={
            "name": "Ada",
            "email": "ada@example.com",
            "password": "password",
            "phone_number": "1234567890",
        },
    )

    payload = response.get_json()
    assert response.status_code == 201
    assert set(payload) == {"message", "user", "access_token", "refresh_token"}
    assert payload["message"] == "User created successfully"
    assert payload["user"]["name"] == "Ada"
    assert payload["user"]["email"] == "ada@example.com"


def test_login_returns_user_and_token_pair(client, free_user):
    response = client.post(
        "/api/auth/login",
        json={"email": free_user.email, "password": "test-password"},
    )

    payload = response.get_json()
    assert response.status_code == 200
    assert set(payload) == {"message", "user", "access_token", "refresh_token"}
    assert payload["message"] == "Login successful"
    assert payload["user"]["id"] == free_user.id


def test_login_rejects_invalid_password(client, free_user):
    response = client.post(
        "/api/auth/login",
        json={"email": free_user.email, "password": "incorrect"},
    )

    assert response.status_code == 401
    assert response.get_json() == {"error": "Invalid email or password"}


def test_refresh_returns_access_token_and_user(client, free_user, app):
    from flask_jwt_extended import create_refresh_token

    with app.app_context():
        refresh_token = create_refresh_token(identity=free_user.id)

    response = client.post(
        "/api/auth/refresh", headers={"Authorization": f"Bearer {refresh_token}"}
    )

    payload = response.get_json()
    assert response.status_code == 200
    assert set(payload) == {"access_token", "user"}
    assert payload["user"]["id"] == free_user.id


def test_me_returns_authenticated_user(client, free_user, auth_headers):
    response = client.get("/api/auth/me", headers=auth_headers(free_user))

    assert response.status_code == 200
    assert response.get_json()["user"]["id"] == free_user.id


def test_me_rejects_missing_and_invalid_jwts(client):
    missing_response = client.get("/api/auth/me")
    invalid_response = client.get(
        "/api/auth/me", headers={"Authorization": "Bearer invalid"}
    )

    assert missing_response.status_code == 401
    assert missing_response.get_json() == {"msg": "Missing Authorization Header"}
    assert invalid_response.status_code == 422
    assert invalid_response.get_json() == {"msg": "Not enough segments"}


def test_google_login_uses_verified_payload_without_network(client, monkeypatch):
    monkeypatch.setattr(
        "app.routes.auth.verify_google_token",
        lambda token: {"email": "google@example.com", "name": "Google User", "sub": "google-1"},
    )

    response = client.post("/api/auth/google", json={"token": "test-google-token"})

    payload = response.get_json()
    assert response.status_code == 200
    assert set(payload) == {"message", "user", "access_token", "refresh_token"}
    assert payload["message"] == "Google login successful"
    assert payload["user"]["email"] == "google@example.com"
