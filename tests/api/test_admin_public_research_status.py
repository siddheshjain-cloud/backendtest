"""Anonymous public-access proposal §2.2: the only write path onto
``Company.public_research_status`` -- admin-only, closed value set.
"""

from __future__ import annotations

import pytest

from app.models.research_types import PublicResearchStatus
from app.services.research_command_service import ResearchCommandService


VALID_ISIN = "INE0ADM01019"


@pytest.fixture
def company(ticker_factory, admin_user):
    ticker = ticker_factory(symbol="ADMPUB", instrument_token=9002)
    return ResearchCommandService.create_company(
        {
            "ticker_id": ticker.id,
            "legal_name": "Admin Publish Test Co Limited",
            "isin": VALID_ISIN,
        },
        actor_user_id=admin_user.id,
    )


def _url(company_id: str) -> str:
    return f"/api/admin/research/companies/{company_id}/public-research-status"


def test_new_company_defaults_to_not_public(company):
    assert company.public_research_status == PublicResearchStatus.NOT_PUBLIC


def test_admin_can_publish_a_company(client, company, admin_user, auth_headers):
    response = client.patch(
        _url(company.id),
        json={"status": PublicResearchStatus.PUBLISHED},
        headers=auth_headers(admin_user),
    )

    assert response.status_code == 200
    assert (
        response.get_json()["public_research_status"]
        == PublicResearchStatus.PUBLISHED
    )


def test_admin_can_revert_a_company_to_not_public(
    client, company, admin_user, auth_headers
):
    headers = auth_headers(admin_user)
    client.patch(
        _url(company.id),
        json={"status": PublicResearchStatus.PUBLISHED},
        headers=headers,
    )

    response = client.patch(
        _url(company.id),
        json={"status": PublicResearchStatus.NOT_PUBLIC},
        headers=headers,
    )

    assert response.status_code == 200
    assert (
        response.get_json()["public_research_status"]
        == PublicResearchStatus.NOT_PUBLIC
    )


def test_non_admin_cannot_publish_a_company(
    client, company, free_user, auth_headers
):
    response = client.patch(
        _url(company.id),
        json={"status": PublicResearchStatus.PUBLISHED},
        headers=auth_headers(free_user),
    )

    assert response.status_code == 403


def test_anonymous_cannot_publish_a_company(client, company):
    response = client.patch(
        _url(company.id),
        json={"status": PublicResearchStatus.PUBLISHED},
    )

    assert response.status_code == 401


def test_an_invalid_status_value_is_rejected(client, company, admin_user, auth_headers):
    response = client.patch(
        _url(company.id),
        json={"status": "LIVE"},
        headers=auth_headers(admin_user),
    )

    assert response.status_code == 400


def test_unknown_company_404s(client, admin_user, auth_headers):
    response = client.patch(
        _url("not-a-real-company-id"),
        json={"status": PublicResearchStatus.PUBLISHED},
        headers=auth_headers(admin_user),
    )

    assert response.status_code == 404
