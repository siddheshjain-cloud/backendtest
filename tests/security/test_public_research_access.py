"""Anonymous public-access proposal §2.1/§2.3: PUBLIC tier at the real
request boundary.

This is deliberately a separate module from
``test_research_data_isolation.py`` rather than a new scenario folded into
its ``research_security_matrix`` fixture: that matrix is entirely about
entitlement-row *states for an authenticated user* (missing/inactive/
revoked/future/expired/active_free/active_premium/admin). PUBLIC is a
different axis -- presence or absence of a JWT at all -- that matrix was
never designed to vary. Proving PUBLIC doesn't weaken any of its frozen
assertions; it adds a boundary that module has no scenario for.
"""

from __future__ import annotations

import pytest

from app.models.research_types import PublicResearchStatus, ResearchTier
from app.services.entitlement_service import EntitlementService
from app.services.research_command_service import ResearchCommandService


VALID_ISIN = "INE0PUB01019"


@pytest.fixture
def company(ticker_factory, admin_user):
    ticker = ticker_factory(symbol="PUBCO", instrument_token=9001)
    return ResearchCommandService.create_company(
        {
            "ticker_id": ticker.id,
            "legal_name": "Public Research Test Co Limited",
            "isin": VALID_ISIN,
        },
        actor_user_id=admin_user.id,
    )


# --------------------------------------------------------------------------
# EntitlementService.resolve_optional -- unit level
# --------------------------------------------------------------------------


def test_resolve_optional_with_no_user_is_public_without_raising(app):
    with app.app_context():
        context = EntitlementService.resolve_optional(None)

    assert context.tier == ResearchTier.PUBLIC
    assert context.is_admin is False
    assert context.user_id is None


def test_resolve_optional_with_a_real_user_matches_resolve(app, free_user):
    with app.app_context():
        via_optional = EntitlementService.resolve_optional(free_user)
        via_resolve = EntitlementService.resolve(free_user)

    assert via_optional == via_resolve
    assert via_optional.tier == ResearchTier.FREE


# --------------------------------------------------------------------------
# GET /api/research/companies/<id> -- real HTTP boundary
# --------------------------------------------------------------------------


def test_unpublished_company_404s_for_an_anonymous_caller(client, company):
    assert company.public_research_status == PublicResearchStatus.NOT_PUBLIC

    response = client.get(f"/api/research/companies/{company.id}")

    assert response.status_code == 404


def test_published_company_is_reachable_with_no_authorization_header(
    client, company, admin_user
):
    ResearchCommandService.set_public_research_status(
        company.id, admin_user.id, PublicResearchStatus.PUBLISHED
    )

    response = client.get(f"/api/research/companies/{company.id}")

    assert response.status_code == 200
    body = response.get_json()
    assert body["access"]["tier"] == ResearchTier.PUBLIC
    # Exact top-level key set -- PUBLIC_SECTIONS is empty today, so no
    # section key may appear. A future section added to PUBLIC_SECTIONS
    # must update this assertion deliberately, not pass by accident.
    assert set(body.keys()) == {"company", "market_quote", "access"}
    assert body["company"]["id"] == company.id


def test_an_invalid_bearer_token_still_fails_even_on_the_anonymous_route(
    client, company, admin_user
):
    """optional_consumer widens 'no token', never 'bad token'."""

    ResearchCommandService.set_public_research_status(
        company.id, admin_user.id, PublicResearchStatus.PUBLISHED
    )

    response = client.get(
        f"/api/research/companies/{company.id}",
        headers={"Authorization": "Bearer not-a-real-token"},
    )

    assert response.status_code == 401


def test_authenticated_free_user_is_unaffected_by_the_optional_decorator(
    client, company, admin_user, free_user, auth_headers
):
    """Switching the route from consumer_required to optional_consumer must
    not change behavior for a real, already-authenticated caller."""

    ResearchCommandService.set_public_research_status(
        company.id, admin_user.id, PublicResearchStatus.PUBLISHED
    )

    response = client.get(
        f"/api/research/companies/{company.id}",
        headers=auth_headers(free_user),
    )

    assert response.status_code == 200
    assert response.get_json()["access"]["tier"] == ResearchTier.FREE


# --------------------------------------------------------------------------
# GET /api/research/companies -- list filtering
# --------------------------------------------------------------------------


def test_anonymous_list_excludes_unpublished_companies(client, company):
    response = client.get("/api/research/companies")

    assert response.status_code == 200
    ids = {item["company"]["id"] for item in response.get_json()["items"]}
    assert company.id not in ids


def test_anonymous_list_includes_published_companies(
    client, company, admin_user
):
    ResearchCommandService.set_public_research_status(
        company.id, admin_user.id, PublicResearchStatus.PUBLISHED
    )

    response = client.get("/api/research/companies")

    assert response.status_code == 200
    ids = {item["company"]["id"] for item in response.get_json()["items"]}
    assert company.id in ids


def test_authenticated_list_is_unaffected_by_publication_status(
    client, company, free_user, auth_headers
):
    """A signed-in FREE reader must keep seeing every company regardless of
    public_research_status -- that gate exists only for PUBLIC."""

    response = client.get(
        "/api/research/companies", headers=auth_headers(free_user)
    )

    assert response.status_code == 200
    ids = {item["company"]["id"] for item in response.get_json()["items"]}
    assert company.id in ids
