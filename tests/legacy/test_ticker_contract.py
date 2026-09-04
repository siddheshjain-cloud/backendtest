def test_ticker_list_with_empty_query_returns_empty_collection(
    client, free_user, auth_headers, ticker_factory
):
    ticker_factory()

    response = client.get("/api/tickers/", headers=auth_headers(free_user))

    assert response.status_code == 200
    assert response.get_json() == {"tickers": [], "total": 0, "page": 1, "per_page": 10}


def test_ticker_search_returns_matching_ticker_and_pagination(
    client, free_user, auth_headers, ticker_factory
):
    ticker_factory()

    response = client.get(
        "/api/tickers/?q=IKI&page=2&per_page=5", headers=auth_headers(free_user)
    )

    payload = response.get_json()
    assert response.status_code == 200
    assert set(payload) == {"tickers", "total", "page", "per_page"}
    assert payload["total"] == 1
    assert payload["page"] == 2
    assert payload["per_page"] == 5
    assert payload["tickers"][0]["symbol"] == "IKIO"
    assert set(payload["tickers"][0]) == {
        "id",
        "symbol",
        "exchange",
        "name",
        "last_price",
        "last_updated",
    }


def test_ticker_search_requires_a_jwt(client):
    response = client.get("/api/tickers/?q=IKIO")

    assert response.status_code == 401
    assert response.get_json() == {"msg": "Missing Authorization Header"}
