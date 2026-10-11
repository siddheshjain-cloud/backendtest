def test_user_can_read_own_profile(client, free_user, auth_headers):
    response = client.get(f"/api/users/{free_user.id}", headers=auth_headers(free_user))

    assert response.status_code == 200
    assert set(response.get_json()) == {"user"}
    assert response.get_json()["user"]["id"] == free_user.id


def test_admin_can_read_another_users_profile(client, admin_user, free_user, auth_headers):
    response = client.get(
        f"/api/users/{free_user.id}", headers=auth_headers(admin_user)
    )

    assert response.status_code == 200
    assert response.get_json()["user"]["id"] == free_user.id


def test_non_admin_cannot_read_another_users_profile(
    client, free_user, premium_user, auth_headers
):
    response = client.get(
        f"/api/users/{premium_user.id}", headers=auth_headers(free_user)
    )

    assert response.status_code == 403
    assert response.get_json() == {"error": "Access denied"}


def test_authenticated_user_list_returns_users_and_total(client, free_user, auth_headers):
    response = client.get("/api/users/", headers=auth_headers(free_user))

    payload = response.get_json()
    assert response.status_code == 200
    assert set(payload) == {"users", "total"}
    assert payload["total"] == 1
    assert payload["users"][0]["id"] == free_user.id
