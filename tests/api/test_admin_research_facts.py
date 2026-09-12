"""Plan 5 Task 3: administrative research fact and identity APIs.

The tests below pin the new administrative write surface before any routes or
schemas exist. They deliberately call through the real Flask application and
verify the stable research error payloads, server-derived identity fields,
in-place entitlement updates, and no-document/no-legacy side effects.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
import sqlalchemy as sa

from app import db
from app.models import (
    Company,
    CompanyDisclosure,
    Document,
    GovernanceFlag,
    Institution,
    OwnershipSnapshot,
    UserEntitlement,
)
from app.models.entitlement import INVESTMENT_RESEARCH_PRODUCT_CODE
from app.models.research_types import (
    EntitlementStatus,
    GovernanceFlagStatus,
    GovernanceSeverity,
    ResearchTier,
)
from app.services.entitlement_service import EntitlementService
from app.services.research_command_service import ResearchCommandService


VALID_ISIN = "INE0LOJ01019"
OTHER_VALID_ISIN = "US0378331005"
OBSERVED_ON = date(2026, 8, 1)
RESOLVED_ON = date(2026, 8, 31)
EVENT_DATE = date(2026, 8, 8)
SOURCE_REFERENCE = "https://example.in/shareholding-pattern"


def _company_payload(ticker_id: str, **overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "ticker_id": ticker_id,
        "legal_name": "IKIO Lighting Limited",
        "display_name": "IKIO Lighting",
        "isin": VALID_ISIN,
        "sector": "Industrials",
        "industry": "LED lighting",
    }
    payload.update(overrides)
    return payload


def _ownership_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "as_of_date": "2026-09-04",
        "promoter_holding_pct": "62.3500",
        "promoter_pledge_pct": "3.1200",
        "notes": "Promoter holding stable",
        "source_reference": SOURCE_REFERENCE,
    }
    payload.update(overrides)
    return payload


def _governance_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "flag_type": "PROMOTER_PLEDGE",
        "title": "Promoter pledge crosses disclosure threshold",
        "severity": GovernanceSeverity.HIGH,
        "status": GovernanceFlagStatus.OPEN,
        "factual_evidence": "Quarterly shareholding pattern shows pledged equity",
        "source_title": "Shareholding pattern disclosure",
        "source_url_or_reference": SOURCE_REFERENCE,
        "interpretation": "Elevated pledge may increase refinancing risk",
        "observed_on": OBSERVED_ON.isoformat(),
    }
    payload.update(overrides)
    return payload


def _disclosure_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "event_type": "REG30",
        "event_date": EVENT_DATE.isoformat(),
        "title": "Disclosure under Regulation 30 of SEBI LODR",
        "original_source_url_or_reference": SOURCE_REFERENCE,
        "exchange_reference": "NSE:IKIO",
        "significance_note": "Capacity expansion update",
    }
    payload.update(overrides)
    return payload


@pytest.fixture
def company(ticker_factory, admin_user):
    ticker = ticker_factory()
    return ResearchCommandService.create_company(
        {
            "ticker_id": ticker.id,
            "legal_name": "IKIO Lighting Limited",
            "isin": VALID_ISIN,
        },
        actor_user_id=admin_user.id,
    )


def _post(client, url: str, payload: dict, headers: dict):
    return client.post(url, json=payload, headers=headers)


def _patch(client, url: str, payload: dict, headers: dict):
    return client.patch(url, json=payload, headers=headers)


def _put(client, url: str, payload: dict, headers: dict):
    return client.put(url, json=payload, headers=headers)


def _count(model) -> int:
    return db.session.scalar(sa.select(sa.func.count()).select_from(model))


# ---------------------------------------------------------------------------
# Authentication and authorization
# ---------------------------------------------------------------------------


def test_missing_token_returns_legacy_401(client):
    response = client.post("/api/admin/research/companies", json={})

    assert response.status_code == 401
    assert response.get_json() == {"error": "Token is invalid"}


def test_invalid_token_returns_legacy_401(client):
    response = client.post(
        "/api/admin/research/companies",
        json={},
        headers={"Authorization": "Bearer not-a-valid-token"},
    )

    assert response.status_code == 401
    assert response.get_json() == {"error": "Token is invalid"}


def test_non_admin_returns_legacy_403(client, free_user, auth_headers):
    response = client.post(
        "/api/admin/research/companies",
        json={},
        headers=auth_headers(free_user),
    )

    assert response.status_code == 403
    assert response.get_json() == {"error": "Admin access required"}


def test_admin_can_reach_research_admin_surface(
    client, ticker_factory, admin_user, auth_headers
):
    ticker = ticker_factory()
    response = client.post(
        "/api/admin/research/companies",
        json=_company_payload(ticker.id),
        headers=auth_headers(admin_user),
    )

    assert response.status_code == 201


# ---------------------------------------------------------------------------
# Company administrative API
# ---------------------------------------------------------------------------


def test_create_company_links_to_existing_ticker(
    client, ticker_factory, admin_user, auth_headers
):
    ticker = ticker_factory()

    response = client.post(
        "/api/admin/research/companies",
        json=_company_payload(ticker.id),
        headers=auth_headers(admin_user),
    )

    assert response.status_code == 201
    body = response.get_json()
    assert body["ticker_id"] == ticker.id
    assert body["legal_name"] == "IKIO Lighting Limited"
    assert body["isin"] == VALID_ISIN
    assert body["id"]
    assert "created_by_user_id" not in body
    assert db.session.get(Company, body["id"]).ticker_id == ticker.id


def test_create_company_rejects_unknown_ticker(
    client, admin_user, auth_headers
):
    response = client.post(
        "/api/admin/research/companies",
        json=_company_payload("missing-ticker"),
        headers=auth_headers(admin_user),
    )

    assert response.status_code == 404
    assert response.get_json()["error"] == "ticker_not_found"


def test_create_company_rejects_duplicate_ticker_identity(
    client, ticker_factory, admin_user, auth_headers
):
    ticker = ticker_factory()
    first = client.post(
        "/api/admin/research/companies",
        json=_company_payload(ticker.id),
        headers=auth_headers(admin_user),
    )
    assert first.status_code == 201

    duplicate = client.post(
        "/api/admin/research/companies",
        json=_company_payload(ticker.id, isin=OTHER_VALID_ISIN),
        headers=auth_headers(admin_user),
    )

    assert duplicate.status_code == 409
    assert duplicate.get_json()["error"] == "company_identity_conflict"


def test_patch_company_corrects_stable_metadata(
    client, ticker_factory, admin_user, auth_headers
):
    ticker = ticker_factory()
    created = client.post(
        "/api/admin/research/companies",
        json=_company_payload(ticker.id),
        headers=auth_headers(admin_user),
    ).get_json()

    response = client.patch(
        f"/api/admin/research/companies/{created['id']}",
        json={"display_name": "IKIO Lighting (India)", "sector": "Industrials"},
        headers=auth_headers(admin_user),
    )

    assert response.status_code == 200
    assert response.get_json()["display_name"] == "IKIO Lighting (India)"
    persisted = db.session.get(Company, created["id"])
    assert persisted.display_name == "IKIO Lighting (India)"
    assert persisted.legal_name == "IKIO Lighting Limited"
    assert persisted.ticker_id == ticker.id


def test_patch_company_404_for_missing_company(
    client, admin_user, auth_headers
):
    response = client.patch(
        "/api/admin/research/companies/missing-company",
        json={"display_name": "Does not exist"},
        headers=auth_headers(admin_user),
    )

    assert response.status_code == 404
    assert response.get_json()["error"] == "company_not_found"


def test_company_create_and_patch_reject_server_owned_fields(
    client, ticker_factory, admin_user, auth_headers
):
    ticker = ticker_factory()

    create_response = client.post(
        "/api/admin/research/companies",
        json=_company_payload(
            ticker.id,
            id="spoofed-id",
            created_by_user_id="spoofed-actor",
        ),
        headers=auth_headers(admin_user),
    )
    assert create_response.status_code == 400
    assert "id" in create_response.get_json()["details"]
    assert "created_by_user_id" in create_response.get_json()["details"]

    created = client.post(
        "/api/admin/research/companies",
        json=_company_payload(ticker.id),
        headers=auth_headers(admin_user),
    ).get_json()

    patch_response = client.patch(
        f"/api/admin/research/companies/{created['id']}",
        json={"updated_at": "2026-09-12T00:00:00+00:00"},
        headers=auth_headers(admin_user),
    )
    assert patch_response.status_code == 400
    assert "updated_at" in patch_response.get_json()["details"]


# ---------------------------------------------------------------------------
# Entitlement administrative API
# ---------------------------------------------------------------------------


def _entitlement_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "tier": ResearchTier.PREMIUM,
        "status": EntitlementStatus.ACTIVE,
    }
    payload.update(overrides)
    return payload


def test_entitlement_missing_row_creates_exactly_one_row(
    client, free_user, admin_user, auth_headers
):
    response = client.put(
        f"/api/admin/users/{free_user.id}/entitlements/investment-research",
        json=_entitlement_payload(),
        headers=auth_headers(admin_user),
    )

    assert response.status_code == 200
    body = response.get_json()
    assert body["user_id"] == free_user.id
    assert body["product_code"] == INVESTMENT_RESEARCH_PRODUCT_CODE
    assert body["tier"] == ResearchTier.PREMIUM
    assert body["status"] == EntitlementStatus.ACTIVE
    assert _count(UserEntitlement) == 1


def test_entitlement_existing_row_updates_in_place_without_history(
    client, free_user, admin_user, auth_headers
):
    first = client.put(
        f"/api/admin/users/{free_user.id}/entitlements/investment-research",
        json=_entitlement_payload(),
        headers=auth_headers(admin_user),
    ).get_json()

    second = client.put(
        f"/api/admin/users/{free_user.id}/entitlements/investment-research",
        json=_entitlement_payload(
            tier=ResearchTier.FREE,
            status=EntitlementStatus.INACTIVE,
        ),
        headers=auth_headers(admin_user),
    ).get_json()

    assert second["id"] == first["id"]
    assert second["tier"] == ResearchTier.FREE
    assert second["status"] == EntitlementStatus.INACTIVE
    assert _count(UserEntitlement) == 1


@pytest.mark.parametrize(
    "status",
    [
        EntitlementStatus.ACTIVE,
        EntitlementStatus.INACTIVE,
        EntitlementStatus.REVOKED,
    ],
)
def test_entitlement_supports_active_inactive_and_revoked_statuses(
    client, free_user, admin_user, auth_headers, status
):
    response = client.put(
        f"/api/admin/users/{free_user.id}/entitlements/investment-research",
        json=_entitlement_payload(status=status),
        headers=auth_headers(admin_user),
    )

    assert response.status_code == 200
    assert response.get_json()["status"] == status
    assert _count(UserEntitlement) == 1


def test_entitlement_rejects_client_product_code_and_derives_it(
    client, free_user, admin_user, auth_headers
):
    rejected = client.put(
        f"/api/admin/users/{free_user.id}/entitlements/investment-research",
        json=_entitlement_payload(
            product_code=INVESTMENT_RESEARCH_PRODUCT_CODE,
        ),
        headers=auth_headers(admin_user),
    )
    assert rejected.status_code == 400
    assert "product_code" in rejected.get_json()["details"]

    accepted = client.put(
        f"/api/admin/users/{free_user.id}/entitlements/investment-research",
        json=_entitlement_payload(),
        headers=auth_headers(admin_user),
    )
    assert accepted.status_code == 200
    assert accepted.get_json()["product_code"] == INVESTMENT_RESEARCH_PRODUCT_CODE


def test_changed_entitlement_is_effective_without_new_jwt(
    app, client, free_user, admin_user, auth_headers
):
    headers = auth_headers(admin_user)
    assert EntitlementService.resolve(free_user).tier == ResearchTier.FREE

    response = client.put(
        f"/api/admin/users/{free_user.id}/entitlements/investment-research",
        json=_entitlement_payload(
            tier=ResearchTier.PREMIUM,
            status=EntitlementStatus.ACTIVE,
        ),
        headers=headers,
    )
    assert response.status_code == 200

    with app.app_context():
        assert EntitlementService.resolve(free_user).tier == ResearchTier.PREMIUM


# ---------------------------------------------------------------------------
# Ownership administrative API
# ---------------------------------------------------------------------------


def test_create_ownership_snapshot_returns_decimal_strings(
    client, company, admin_user, auth_headers
):
    response = client.post(
        f"/api/admin/research/companies/{company.id}/ownership-snapshots",
        json=_ownership_payload(),
        headers=auth_headers(admin_user),
    )

    assert response.status_code == 201
    body = response.get_json()
    assert isinstance(body["promoter_holding_pct"], str)
    assert isinstance(body["promoter_pledge_pct"], str)
    assert Decimal(body["promoter_holding_pct"]) == Decimal("62.3500")
    assert Decimal(body["promoter_pledge_pct"]) == Decimal("3.1200")
    persisted = db.session.get(OwnershipSnapshot, body["id"])
    assert persisted.created_by_user_id == admin_user.id
    assert persisted.as_of_date == date(2026, 9, 4)


def test_create_ownership_snapshot_rejects_missing_date(
    client, company, admin_user, auth_headers
):
    response = client.post(
        f"/api/admin/research/companies/{company.id}/ownership-snapshots",
        json=_ownership_payload(as_of_date=None),
        headers=auth_headers(admin_user),
    )

    assert response.status_code == 400
    assert "as_of_date" in response.get_json()["details"]
    assert _count(OwnershipSnapshot) == 0


# ---------------------------------------------------------------------------
# Governance administrative API
# ---------------------------------------------------------------------------


def test_create_governance_flag(
    client, company, admin_user, auth_headers
):
    response = client.post(
        f"/api/admin/research/companies/{company.id}/governance-flags",
        json=_governance_payload(),
        headers=auth_headers(admin_user),
    )

    assert response.status_code == 201
    body = response.get_json()
    assert body["company_id"] == company.id
    assert body["flag_type"] == "PROMOTER_PLEDGE"
    assert body["status"] == GovernanceFlagStatus.OPEN
    assert body["resolved_on"] is None
    assert db.session.get(GovernanceFlag, body["id"]).created_by_user_id == admin_user.id


def test_patch_governance_flag_can_resolve_and_archive(
    client, company, admin_user, auth_headers
):
    created = client.post(
        f"/api/admin/research/companies/{company.id}/governance-flags",
        json=_governance_payload(),
        headers=auth_headers(admin_user),
    ).get_json()

    resolved = client.patch(
        f"/api/admin/research/governance-flags/{created['id']}",
        json={
            "status": GovernanceFlagStatus.RESOLVED,
            "resolved_on": RESOLVED_ON.isoformat(),
            "interpretation": "Corrected after audit",
        },
        headers=auth_headers(admin_user),
    )
    assert resolved.status_code == 200
    assert resolved.get_json()["status"] == GovernanceFlagStatus.RESOLVED
    assert resolved.get_json()["observed_on"] == OBSERVED_ON.isoformat()
    assert resolved.get_json()["archived_at"] is None

    archived = client.patch(
        f"/api/admin/research/governance-flags/{created['id']}",
        json={"archived": True},
        headers=auth_headers(admin_user),
    )
    assert archived.status_code == 200
    assert archived.get_json()["archived_at"] is not None
    assert db.session.get(GovernanceFlag, created["id"]).archived_at is not None


def test_patch_governance_flag_404(client, admin_user, auth_headers):
    response = client.patch(
        "/api/admin/research/governance-flags/missing-flag",
        json={"archived": True},
        headers=auth_headers(admin_user),
    )

    assert response.status_code == 404
    assert response.get_json()["error"] == "governance_flag_not_found"


# ---------------------------------------------------------------------------
# Disclosure administrative API
# ---------------------------------------------------------------------------


def test_create_disclosure_defaults_is_key_false(
    client, company, admin_user, auth_headers
):
    response = client.post(
        f"/api/admin/research/companies/{company.id}/disclosures",
        json=_disclosure_payload(),
        headers=auth_headers(admin_user),
    )

    assert response.status_code == 201
    body = response.get_json()
    assert body["is_key"] is False
    assert body["document_id"] is None
    assert _count(Document) == 0
    assert db.session.get(CompanyDisclosure, body["id"]).document_id is None


def test_disclosure_is_key_manual_changes_and_archive(
    client, company, admin_user, auth_headers
):
    created = client.post(
        f"/api/admin/research/companies/{company.id}/disclosures",
        json=_disclosure_payload(is_key=True),
        headers=auth_headers(admin_user),
    ).get_json()
    assert created["is_key"] is True

    corrected = client.patch(
        f"/api/admin/research/disclosures/{created['id']}",
        json={"is_key": False, "title": "Corrected title"},
        headers=auth_headers(admin_user),
    )
    assert corrected.status_code == 200
    assert corrected.get_json()["is_key"] is False
    assert corrected.get_json()["title"] == "Corrected title"

    archived = client.patch(
        f"/api/admin/research/disclosures/{created['id']}",
        json={"archived": True},
        headers=auth_headers(admin_user),
    )
    assert archived.status_code == 200
    assert archived.get_json()["archived_at"] is not None
    assert db.session.get(CompanyDisclosure, created["id"]).archived_at is not None
    assert _count(Document) == 0


def test_patch_disclosure_404(client, admin_user, auth_headers):
    response = client.patch(
        "/api/admin/research/disclosures/missing-disclosure",
        json={"archived": True},
        headers=auth_headers(admin_user),
    )

    assert response.status_code == 404
    assert response.get_json()["error"] == "disclosure_not_found"


# ---------------------------------------------------------------------------
# Institution administrative API
# ---------------------------------------------------------------------------


def test_create_institution_normalizes_name(
    client, admin_user, auth_headers
):
    response = client.post(
        "/api/admin/research/institutions",
        json={
            "name": "  Motilal   Oswal Securities  ",
            "website": "HTTPS://Example.IN/Research",
        },
        headers=auth_headers(admin_user),
    )

    assert response.status_code == 201
    body = response.get_json()
    assert body["name"] == "Motilal   Oswal Securities"
    assert body["website"] == "https://example.in/Research"
    assert "normalized_name" not in body
    persisted = db.session.get(Institution, body["id"])
    assert persisted.normalized_name == "motilal oswal securities"


def test_create_institution_rejects_duplicate_normalized_name(
    client, admin_user, auth_headers
):
    first = client.post(
        "/api/admin/research/institutions",
        json={"name": "Motilal Oswal Securities"},
        headers=auth_headers(admin_user),
    )
    assert first.status_code == 201

    duplicate = client.post(
        "/api/admin/research/institutions",
        json={"name": "MOTILAL OSWAL SECURITIES"},
        headers=auth_headers(admin_user),
    )

    assert duplicate.status_code == 409
    assert duplicate.get_json()["error"] == "institution_name_conflict"
    assert _count(Institution) == 1


def test_patch_institution_corrects_metadata(
    client, admin_user, auth_headers
):
    created = client.post(
        "/api/admin/research/institutions",
        json={"name": "First Research", "website": "https://first.example/"},
        headers=auth_headers(admin_user),
    ).get_json()

    response = client.patch(
        f"/api/admin/research/institutions/{created['id']}",
        json={"website": "HTTPS://First.Example/New"},
        headers=auth_headers(admin_user),
    )

    assert response.status_code == 200
    assert response.get_json()["name"] == "First Research"
    assert response.get_json()["website"] == "https://first.example/New"
    persisted = db.session.get(Institution, created["id"])
    assert persisted.normalized_name == "first research"


def test_patch_institution_404(client, admin_user, auth_headers):
    response = client.patch(
        "/api/admin/research/institutions/missing-institution",
        json={"name": "Updated"},
        headers=auth_headers(admin_user),
    )

    assert response.status_code == 404
    assert response.get_json()["error"] == "institution_not_found"


# ---------------------------------------------------------------------------
# Unknown/server-owned fields and sensitive response hygiene
# ---------------------------------------------------------------------------


def test_research_fact_responses_do_not_leak_actor_or_private_fields(
    client, company, admin_user, auth_headers
):
    ownership = client.post(
        f"/api/admin/research/companies/{company.id}/ownership-snapshots",
        json=_ownership_payload(),
        headers=auth_headers(admin_user),
    ).get_json()

    assert "created_by_user_id" not in ownership
    assert "updated_by_user_id" not in ownership
    assert "password_hash" not in ownership
    assert "revision_number" not in ownership

    governance = client.post(
        f"/api/admin/research/companies/{company.id}/governance-flags",
        json=_governance_payload(),
        headers=auth_headers(admin_user),
    ).get_json()
    assert "created_by_user_id" not in governance

    disclosure = client.post(
        f"/api/admin/research/companies/{company.id}/disclosures",
        json=_disclosure_payload(),
        headers=auth_headers(admin_user),
    ).get_json()
    assert "created_by_user_id" not in disclosure


@pytest.mark.parametrize(
    "method,url,payload",
    [
        (
            "post",
            "/api/admin/research/companies",
            {"ticker_id": "t", "legal_name": "Name", "isin": VALID_ISIN, "id": "x"},
        ),
        (
            "put",
            "/api/admin/users/user-id/entitlements/investment-research",
            {"tier": "PREMIUM", "status": "ACTIVE", "updated_at": "x"},
        ),
        (
            "post",
            "/api/admin/research/companies/company-id/ownership-snapshots",
            {"as_of_date": "2026-01-01", "revision_number": 1},
        ),
        (
            "post",
            "/api/admin/research/companies/company-id/governance-flags",
            _governance_payload(id="x"),
        ),
        (
            "post",
            "/api/admin/research/companies/company-id/disclosures",
            _disclosure_payload(created_by_user_id="x"),
        ),
        (
            "post",
            "/api/admin/research/institutions",
            {"name": "Name", "normalized_name": "name"},
        ),
    ],
)
def test_create_and_put_schemas_reject_unknown_server_owned_fields(
    client, admin_user, auth_headers, method, url, payload
):
    headers = auth_headers(admin_user)
    if method == "post":
        response = client.post(url, json=payload, headers=headers)
    else:
        response = client.put(url, json=payload, headers=headers)

    assert response.status_code == 400
    assert response.get_json()["error"] == "validation_error"


@pytest.mark.parametrize(
    "url,payload",
    [
        (
            "/api/admin/research/companies/company-id",
            {"id": "spoofed"},
        ),
        (
            "/api/admin/research/governance-flags/flag-id",
            {"created_by_user_id": "spoofed"},
        ),
        (
            "/api/admin/research/disclosures/disclosure-id",
            {"updated_at": "spoofed"},
        ),
        (
            "/api/admin/research/institutions/institution-id",
            {"normalized_name": "spoofed"},
        ),
    ],
)
def test_patch_schemas_reject_unknown_server_owned_fields(
    client, admin_user, auth_headers, url, payload
):
    response = client.patch(
        url,
        json=payload,
        headers=auth_headers(admin_user),
    )

    assert response.status_code == 400
    assert response.get_json()["error"] == "validation_error"
