"""Plan 5 Task 6: entitled consumer document read API tests.

The routes under test delegate to the existing DB-backed entitlement resolver
and the rights-safe ``DocumentLibraryService`` query/projection layer. The
suite seeds the frozen document domain directly and asserts non-revealing
consumer behavior over the real Flask boundary.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest
import sqlalchemy as sa

from app import db
from app.models import (
    Company,
    Document,
    DocumentCompanyLink,
    Institution,
    InstitutionalReportMetadata,
)
from app.models.entitlement import (
    INVESTMENT_RESEARCH_PRODUCT_CODE,
    UserEntitlement,
)
from app.models.document import (
    AcquisitionMethod,
    DiscoverySourceType,
    DistributionStatus,
    DocumentType,
    IngestionStatus,
    SourceAccess,
)
from app.models.research_types import EntitlementStatus, ResearchTier
from app.services.document_library_service import DocumentLibraryService


UTC = timezone.utc


def _make_company(
    ticker_factory,
    *,
    symbol: str,
    isin: str,
    instrument_token: int,
) -> Company:
    ticker = ticker_factory(
        symbol=symbol,
        instrument_token=instrument_token,
        name=f"{symbol} Limited",
    )
    company = Company(
        ticker_id=ticker.id,
        legal_name=f"{symbol} Limited",
        isin=isin,
    )
    db.session.add(company)
    db.session.commit()
    return company


@pytest.fixture
def company(ticker_factory):
    return _make_company(
        ticker_factory,
        symbol="IKIO",
        isin="INE0LOJ01019",
        instrument_token=201,
    )


@pytest.fixture
def other_company(ticker_factory):
    return _make_company(
        ticker_factory,
        symbol="PEER",
        isin="INE000A01001",
        instrument_token=202,
    )


def _document_fields(**overrides: object) -> dict[str, object]:
    fields: dict[str, object] = {
        "document_type": DocumentType.ANNUAL_REPORT,
        "title": "Consumer Annual Report",
        "document_date": "2026-06-30",
        "reporting_period": "FY26",
        "publisher_name": "IKIO Limited",
        "original_source_url": "https://publisher.example/report",
        "discovery_source_type": DiscoverySourceType.OFFICIAL_SITE,
        "source_access": SourceAccess.PUBLIC,
        "acquisition_method": AcquisitionMethod.MANUAL_REFERENCE,
        "distribution_status": DistributionStatus.LINK_ONLY,
        "ingestion_status": IngestionStatus.DISCOVERED,
    }
    fields.update(overrides)
    return fields


def _create_document(
    admin_user,
    *,
    company: Company,
    document: dict[str, object],
    company_links: list[dict[str, object]] | None = None,
    institutional_report: dict[str, object] | None = None,
) -> Document:
    payload: dict[str, object] = {
        "document": document,
        "company_links": company_links or [],
    }
    if institutional_report is not None:
        payload["institutional_report"] = institutional_report
    return DocumentLibraryService.create_document(
        payload, actor_user_id=admin_user.id
    )


def _restricted_fields(
    *,
    provider_user_id: str,
    title: str,
    storage_key: str,
    content_hash_sha256: str = "d" * 64,
) -> dict[str, object]:
    return _document_fields(
        title=title,
        source_access=SourceAccess.RESTRICTED,
        acquisition_method=AcquisitionMethod.USER_UPLOAD,
        distribution_status=DistributionStatus.PRIVATE_LIBRARY,
        ingestion_status=IngestionStatus.STORED,
        original_source_url=None,
        provided_by_user_id=provider_user_id,
        storage_provider="object-store",
        storage_key=storage_key,
        content_hash_sha256=content_hash_sha256,
        mime_type="application/pdf",
        file_size_bytes=2048,
    )


def _all_keys(payload: object) -> set[str]:
    keys: set[str] = set()
    if isinstance(payload, dict):
        for key, value in payload.items():
            keys.add(key)
            keys.update(_all_keys(value))
    elif isinstance(payload, (list, tuple)):
        for value in payload:
            keys.update(_all_keys(value))
    return keys


def _assert_no_private_keys(payload: object) -> None:
    keys = _all_keys(payload)
    for forbidden in (
        "storage_key",
        "storage_provider",
        "content_hash_sha256",
        "metadata_fingerprint",
        "file_size_bytes",
        "mime_type",
        "provided_by_user_id",
        "distribution_basis",
        "rights_verified_by_user_id",
        "rights_verified_at",
        "discovery_source_reference",
        "created_by_user_id",
    ):
        assert forbidden not in keys


def _assert_no_storage_keys(payload: object) -> None:
    keys = _all_keys(payload)
    assert "storage_key" not in keys
    assert "storage_provider" not in keys
    assert "opaque/private/own.pdf" not in str(payload)


# ---------------------------------------------------------------------------
# Authentication and basic access
# ---------------------------------------------------------------------------


def test_consumer_document_missing_token_returns_legacy_401(client):
    response = client.get("/api/research/documents/missing-id")

    assert response.status_code == 401
    assert response.get_json() == {"error": "Token is invalid"}


def test_consumer_document_invalid_token_returns_legacy_401(client):
    response = client.get(
        "/api/research/documents/missing-id",
        headers={"Authorization": "Bearer not-a-valid-token"},
    )

    assert response.status_code == 401
    assert response.get_json() == {"error": "Token is invalid"}


def test_entitled_user_sees_allowed_document(
    client, company, admin_user, premium_user, auth_headers
):
    document = _create_document(
        admin_user,
        company=company,
        document=_document_fields(title="PUBLIC-CONSUMER-DOCUMENT"),
        company_links=[
            {"company_id": company.id, "is_primary": True}
        ],
    )

    detail = client.get(
        f"/api/research/documents/{document.id}",
        headers=auth_headers(premium_user),
    )
    listing = client.get(
        f"/api/research/companies/{company.id}/documents",
        headers=auth_headers(premium_user),
    )

    assert detail.status_code == 200
    assert detail.get_json()["title"] == "PUBLIC-CONSUMER-DOCUMENT"
    assert listing.status_code == 200
    assert listing.get_json()["total_items"] == 1
    assert listing.get_json()["items"][0]["id"] == document.id
    _assert_no_private_keys(detail.get_json())
    _assert_no_private_keys(listing.get_json())


def test_restricted_document_is_invisible_to_unrelated_consumer(
    client,
    company,
    admin_user,
    premium_user,
    user_factory,
    auth_headers,
):
    provider = user_factory(email="document-provider@example.com")
    document = _create_document(
        admin_user,
        company=company,
        document=_restricted_fields(
            provider_user_id=provider.id,
            title="RESTRICTED-PRIVATE-SENTINEL",
            storage_key="opaque/private/provider.pdf",
        ),
        company_links=[
            {"company_id": company.id, "is_primary": True}
        ],
    )

    detail = client.get(
        f"/api/research/documents/{document.id}",
        headers=auth_headers(premium_user),
    )
    listing = client.get(
        f"/api/research/companies/{company.id}/documents",
        headers=auth_headers(premium_user),
    )

    assert detail.status_code == 404
    assert detail.get_json()["error"] == "document_not_found"
    assert listing.status_code == 200
    assert listing.get_json()["total_items"] == 0
    assert "RESTRICTED-PRIVATE-SENTINEL" not in str(detail.get_json())
    assert "RESTRICTED-PRIVATE-SENTINEL" not in str(listing.get_json())


def test_provider_sees_own_restricted_document_without_private_storage(
    client,
    company,
    admin_user,
    user_factory,
    auth_headers,
):
    provider = user_factory(email="own-provider@example.com")
    document = _create_document(
        admin_user,
        company=company,
        document=_restricted_fields(
            provider_user_id=provider.id,
            title="OWN-PROVIDER-DOCUMENT",
            storage_key="opaque/private/own.pdf",
        ),
        company_links=[
            {"company_id": company.id, "is_primary": True}
        ],
    )

    detail = client.get(
        f"/api/research/documents/{document.id}",
        headers=auth_headers(provider),
    )

    assert detail.status_code == 200
    assert detail.get_json()["title"] == "OWN-PROVIDER-DOCUMENT"
    assert detail.get_json()["provided_by_user_id"] == provider.id
    _assert_no_storage_keys(detail.get_json())
    assert "opaque/private/own.pdf" not in str(detail.get_json())


def test_missing_document_returns_stable_404(
    client, premium_user, auth_headers
):
    response = client.get(
        "/api/research/documents/99999999-9999-9999-9999-999999999999",
        headers=auth_headers(premium_user),
    )

    assert response.status_code == 404
    assert response.get_json()["error"] == "document_not_found"


# ---------------------------------------------------------------------------
# Company isolation
# ---------------------------------------------------------------------------


def test_list_is_company_scoped_without_cross_company_leakage(
    client,
    company,
    other_company,
    admin_user,
    premium_user,
    auth_headers,
):
    document = _create_document(
        admin_user,
        company=company,
        document=_document_fields(title="COMPANY-A-DOCUMENT"),
        company_links=[
            {"company_id": company.id, "is_primary": True}
        ],
    )

    other_list = client.get(
        f"/api/research/companies/{other_company.id}/documents",
        headers=auth_headers(premium_user),
    )

    assert other_list.status_code == 200
    assert other_list.get_json()["total_items"] == 0
    assert document.id not in str(other_list.get_json())


def test_detail_does_not_leak_company_links_or_internal_relationships(
    client, company, admin_user, premium_user, auth_headers
):
    document = _create_document(
        admin_user,
        company=company,
        document=_document_fields(title="NO-COMPANY-RELATIONSHIP-LEAK"),
        company_links=[
            {"company_id": company.id, "is_primary": True}
        ],
    )

    response = client.get(
        f"/api/research/documents/{document.id}",
        headers=auth_headers(premium_user),
    )

    assert response.status_code == 200
    assert "company_links" not in response.get_json()
    assert "company_id" not in response.get_json()


# ---------------------------------------------------------------------------
# Lineage / rights leakage
# ---------------------------------------------------------------------------


def test_hidden_predecessor_is_not_exposed_to_consumer(
    client,
    company,
    admin_user,
    premium_user,
    user_factory,
    auth_headers,
):
    provider = user_factory(email="lineage-provider@example.com")
    predecessor = _create_document(
        admin_user,
        company=company,
        document=_restricted_fields(
            provider_user_id=provider.id,
            title="PRIVATE-PREDECESSOR",
            storage_key="opaque/private/predecessor.pdf",
        ),
        company_links=[
            {"company_id": company.id, "is_primary": True}
        ],
    )
    current = _create_document(
        admin_user,
        company=company,
        document=_document_fields(
            title="PUBLIC-SUCCESSOR",
            supersedes_document_id=predecessor.id,
        ),
        company_links=[
            {"company_id": company.id, "is_primary": True}
        ],
    )

    detail = client.get(
        f"/api/research/documents/{current.id}",
        headers=auth_headers(premium_user),
    )

    assert detail.status_code == 200
    assert "supersedes_document_id" not in detail.get_json()
    assert predecessor.id not in str(detail.get_json())


def test_provider_lineage_hides_inaccessible_predecessor_id(
    client,
    company,
    admin_user,
    user_factory,
    auth_headers,
):
    provider = user_factory(email="current-provider@example.com")
    other_provider = user_factory(email="other-provider@example.com")
    predecessor = _create_document(
        admin_user,
        company=company,
        document=_restricted_fields(
            provider_user_id=other_provider.id,
            title="OTHER-PROVIDER-PREDECESSOR",
            storage_key="opaque/private/other.pdf",
        ),
        company_links=[
            {"company_id": company.id, "is_primary": True}
        ],
    )
    current = _create_document(
        admin_user,
        company=company,
        document={
            **_restricted_fields(
                provider_user_id=provider.id,
                title="CURRENT-PROVIDER-SUCCESSOR",
                storage_key="opaque/private/current.pdf",
                content_hash_sha256="e" * 64,
            ),
            "supersedes_document_id": predecessor.id,
        },
        company_links=[
            {"company_id": company.id, "is_primary": True}
        ],
    )

    detail = client.get(
        f"/api/research/documents/{current.id}",
        headers=auth_headers(provider),
    )

    assert detail.status_code == 200
    assert detail.get_json()["supersedes_document_id"] is None
    assert predecessor.id not in str(detail.get_json())


# ---------------------------------------------------------------------------
# Institution metadata / filtering
# ---------------------------------------------------------------------------


def test_institution_metadata_serializes_only_approved_projection(
    client,
    company,
    admin_user,
    premium_user,
    auth_headers,
):
    institution = Institution(
        name="Alpha Research",
        normalized_name="alpha research",
        website="https://alpha.example/private",
    )
    db.session.add(institution)
    db.session.commit()
    document = _create_document(
        admin_user,
        company=company,
        document=_document_fields(
            document_type=DocumentType.INSTITUTIONAL_RESEARCH,
            title="Alpha coverage",
        ),
        company_links=[
            {"company_id": company.id, "is_primary": True}
        ],
        institutional_report={
            "institution_id": institution.id,
            "analyst_name": "ANALYST-PRIVATE-SENTINEL",
            "report_type": "INITIATING_COVERAGE",
        },
    )

    response = client.get(
        f"/api/research/documents/{document.id}",
        headers=auth_headers(premium_user),
    )

    assert response.status_code == 200
    assert response.get_json()["institutional_report"] == {
        "institution_id": institution.id,
        "institution_name": "Alpha Research",
        "report_type": "INITIATING_COVERAGE",
    }
    assert "ANALYST-PRIVATE-SENTINEL" not in str(response.get_json())
    assert "alpha.example" not in str(response.get_json())


def test_list_filters_are_deterministic(
    client,
    company,
    admin_user,
    premium_user,
    auth_headers,
):
    annual = _create_document(
        admin_user,
        company=company,
        document=_document_fields(
            title="Annual report",
            document_date="2026-01-15",
        ),
        company_links=[
            {"company_id": company.id, "is_primary": True}
        ],
    )
    quarterly = _create_document(
        admin_user,
        company=company,
        document=_document_fields(
            document_type=DocumentType.QUARTERLY_RESULTS,
            title="Quarterly results",
            document_date="2026-06-20",
        ),
        company_links=[
            {"company_id": company.id, "is_primary": True}
        ],
    )

    response = client.get(
        f"/api/research/companies/{company.id}/documents",
        query_string={"document_type": DocumentType.QUARTERLY_RESULTS},
        headers=auth_headers(premium_user),
    )

    assert response.status_code == 200
    assert response.get_json()["total_items"] == 1
    assert [item["id"] for item in response.get_json()["items"]] == [
        quarterly.id
    ]
    assert annual.id not in str(response.get_json())


def test_entitlement_resolution_is_database_backed_without_new_jwt(
    client,
    company,
    admin_user,
    user_factory,
    auth_headers,
):
    user = user_factory(email="dynamic-entitlement@example.com")
    document = _create_document(
        admin_user,
        company=company,
        document=_document_fields(title="DYNAMIC-ENTITLEMENT-DOCUMENT"),
        company_links=[
            {"company_id": company.id, "is_primary": True}
        ],
    )
    headers = auth_headers(user)

    before = client.get(
        f"/api/research/documents/{document.id}",
        headers=headers,
    )
    # Add an active premium entitlement after the JWT was issued. Document
    # visibility remains rights-based; the request must not use a stale token
    # tier and must continue through the DB-backed resolver.
    db.session.add(
        UserEntitlement(
            user_id=user.id,
            product_code=INVESTMENT_RESEARCH_PRODUCT_CODE,
            tier=ResearchTier.PREMIUM,
            status=EntitlementStatus.ACTIVE,
        )
    )
    db.session.commit()

    after = client.get(
        f"/api/research/documents/{document.id}",
        headers=headers,
    )

    assert before.status_code == 200
    assert after.status_code == 200
    assert after.get_json()["id"] == document.id


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def test_unknown_query_parameter_is_rejected(
    client, company, premium_user, auth_headers
):
    response = client.get(
        f"/api/research/companies/{company.id}/documents",
        query_string={"not_a_filter": "true"},
        headers=auth_headers(premium_user),
    )

    assert response.status_code == 400
    assert "not_a_filter" in response.get_json()["details"]


def test_invalid_pagination_is_rejected(
    client, company, premium_user, auth_headers
):
    response = client.get(
        f"/api/research/companies/{company.id}/documents",
        query_string={"page": "0"},
        headers=auth_headers(premium_user),
    )

    assert response.status_code == 400
    assert "page" in response.get_json()["details"]
