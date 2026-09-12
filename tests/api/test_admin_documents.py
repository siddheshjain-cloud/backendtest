"""Plan 5 Task 6: administrative document API tests.

These tests pin the approved administrative document surface before the
routes exist: document aggregate creation, audited metadata updates, and
atomic company-link addition. They exercise the real Flask application and
the existing ``DocumentLibraryService`` commands.
"""

from __future__ import annotations

import pytest
import sqlalchemy as sa

from app import db
from app.models import (
    Company,
    Document,
    DocumentAuditEvent,
    DocumentAuditEventType,
    DocumentCompanyLink,
    DocumentContent,
    DocumentStorageLocation,
)
from app.models.document import (
    AcquisitionMethod,
    DiscoverySourceType,
    DistributionStatus,
    DocumentType,
    IngestionStatus,
    SourceAccess,
)


HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64


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
        instrument_token=101,
    )


@pytest.fixture
def second_company(ticker_factory):
    return _make_company(
        ticker_factory,
        symbol="ACME",
        isin="INE000A01001",
        instrument_token=102,
    )


def _document_fields(**overrides: object) -> dict[str, object]:
    fields: dict[str, object] = {
        "document_type": DocumentType.ANNUAL_REPORT,
        "title": "FY26 Annual Report",
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


def _stored_fields(
    *,
    content_hash_sha256: str,
    storage_key: str,
    provided_by_user_id: str,
    title: str = "Stored Annual Report",
) -> dict[str, object]:
    return _document_fields(
        title=title,
        source_access=SourceAccess.RESTRICTED,
        acquisition_method=AcquisitionMethod.USER_UPLOAD,
        distribution_status=DistributionStatus.PRIVATE_LIBRARY,
        ingestion_status=IngestionStatus.STORED,
        original_source_url=None,
        provided_by_user_id=provided_by_user_id,
        storage_provider="object-store",
        storage_key=storage_key,
        content_hash_sha256=content_hash_sha256,
        mime_type="application/pdf",
        file_size_bytes=1024,
    )


def _create_payload(
    *,
    document: dict[str, object] | None = None,
    company_links: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    return {
        "document": document or _document_fields(),
        "company_links": company_links or [],
    }


def _post_document(client, payload: dict, headers: dict):
    return client.post(
        "/api/admin/research/documents",
        json=payload,
        headers=headers,
    )


def _patch_document(
    client,
    document_id: str,
    payload: dict,
    headers: dict,
):
    return client.patch(
        f"/api/admin/research/documents/{document_id}",
        json=payload,
        headers=headers,
    )


def _add_link(
    client,
    document_id: str,
    payload: dict,
    headers: dict,
):
    return client.post(
        f"/api/admin/research/documents/{document_id}/company-links",
        json=payload,
        headers=headers,
    )


def _count(model) -> int:
    return (
        db.session.scalar(
            sa.select(sa.func.count()).select_from(model)
        )
        or 0
    )


# ---------------------------------------------------------------------------
# Authentication and authorization
# ---------------------------------------------------------------------------


def test_admin_document_missing_token_returns_legacy_401(client):
    response = _post_document(client, _create_payload(), {})

    assert response.status_code == 401
    assert response.get_json() == {"error": "Token is invalid"}


def test_admin_document_invalid_token_returns_legacy_401(client):
    response = _post_document(
        client,
        _create_payload(),
        {"Authorization": "Bearer not-a-valid-token"},
    )

    assert response.status_code == 401
    assert response.get_json() == {"error": "Token is invalid"}


def test_admin_document_non_admin_returns_legacy_403(
    client, free_user, auth_headers
):
    response = _post_document(
        client,
        _create_payload(),
        auth_headers(free_user),
    )

    assert response.status_code == 403
    assert response.get_json() == {"error": "Admin access required"}


def test_admin_can_create_document(
    client, company, admin_user, auth_headers
):
    response = _post_document(
        client,
        _create_payload(
            company_links=[
                {"company_id": company.id, "is_primary": True}
            ]
        ),
        auth_headers(admin_user),
    )

    assert response.status_code == 201
    body = response.get_json()
    assert body["id"]
    assert body["title"] == "FY26 Annual Report"
    assert body["company_links"] == [
        {"company_id": company.id, "is_primary": True}
    ]
    assert "storage_key" not in body
    assert "storage_provider" not in body
    assert db.session.get(Document, body["id"]).created_by_user_id == (
        admin_user.id
    )


# ---------------------------------------------------------------------------
# Create / content identity
# ---------------------------------------------------------------------------


def test_create_stored_document_registers_content_identity(
    client, company, admin_user, auth_headers
):
    response = _post_document(
        client,
        _create_payload(
            document=_stored_fields(
                content_hash_sha256=HASH_A,
                storage_key="bucket/private/one.pdf",
                provided_by_user_id=admin_user.id,
            ),
            company_links=[
                {"company_id": company.id, "is_primary": True}
            ],
        ),
        auth_headers(admin_user),
    )

    assert response.status_code == 201
    body = response.get_json()
    assert body["content_hash_sha256"] == HASH_A
    assert "storage_key" not in body

    document = db.session.get(Document, body["id"])
    assert document.content_id is not None
    assert _count(DocumentContent) == 1
    assert _count(DocumentStorageLocation) == 1


def test_identical_sha256_reuses_one_content_identity(
    client, company, admin_user, auth_headers
):
    created = _post_document(
        client,
        _create_payload(
            document=_stored_fields(
                content_hash_sha256=HASH_A,
                storage_key="bucket/private/original.pdf",
                provided_by_user_id=admin_user.id,
                title="Stored Annual Report",
            ),
            company_links=[
                {"company_id": company.id, "is_primary": True}
            ],
        ),
        auth_headers(admin_user),
    )
    document_id = created.get_json()["id"]
    first = db.session.get(Document, document_id)

    replicated_response = _patch_document(
        client,
        document_id,
        {
            "changes": {
                "storage_provider": "object-store",
                "storage_key": "bucket/private/replicated.pdf",
            }
        },
        auth_headers(admin_user),
    )

    assert replicated_response.status_code == 200
    persisted = db.session.get(Document, document_id)
    assert persisted.content_id == first.content_id
    assert _count(DocumentContent) == 1
    assert _count(DocumentStorageLocation) == 2


def test_stored_content_identity_cannot_be_replaced_in_place(
    client, company, admin_user, auth_headers
):
    created = _post_document(
        client,
        _create_payload(
            document=_stored_fields(
                content_hash_sha256=HASH_A,
                storage_key="bucket/private/original.pdf",
                provided_by_user_id=admin_user.id,
            ),
            company_links=[
                {"company_id": company.id, "is_primary": True}
            ],
        ),
        auth_headers(admin_user),
    )
    document_id = created.get_json()["id"]

    response = _patch_document(
        client,
        document_id,
        {
            "reason": "Replace immutable bytes in place",
            "changes": {"content_hash_sha256": HASH_B},
        },
        auth_headers(admin_user),
    )

    assert response.status_code == 400
    assert "content_hash_sha256" in response.get_json()["details"]
    db.session.expire_all()
    assert db.session.get(Document, document_id).content_hash_sha256 == (
        HASH_A
    )


def test_corrected_and_reissued_bytes_preserve_lineage_and_content_identity(
    client, company, admin_user, auth_headers
):
    original = _post_document(
        client,
        _create_payload(
            document=_stored_fields(
                content_hash_sha256=HASH_A,
                storage_key="bucket/private/a.pdf",
                provided_by_user_id=admin_user.id,
                title="Stored Annual Report",
            ),
            company_links=[
                {"company_id": company.id, "is_primary": True}
            ],
        ),
        auth_headers(admin_user),
    )
    original_id = original.get_json()["id"]
    original_row = db.session.get(Document, original_id)

    corrected = _post_document(
        client,
        _create_payload(
            document={
                **_stored_fields(
                    content_hash_sha256=HASH_B,
                    storage_key="bucket/private/b.pdf",
                    provided_by_user_id=admin_user.id,
                    title="Stored Annual Report",
                ),
                "supersedes_document_id": original_id,
            },
            company_links=[
                {"company_id": company.id, "is_primary": True}
            ],
        ),
        auth_headers(admin_user),
    )
    corrected_id = corrected.get_json()["id"]
    corrected_row = db.session.get(Document, corrected_id)

    reissued = _post_document(
        client,
        _create_payload(
            document={
                **_stored_fields(
                    content_hash_sha256=HASH_C,
                    storage_key="bucket/private/c.pdf",
                    provided_by_user_id=admin_user.id,
                    title="Stored Annual Report",
                ),
                "supersedes_document_id": corrected_id,
            },
            company_links=[
                {"company_id": company.id, "is_primary": True}
            ],
        ),
        auth_headers(admin_user),
    )
    reissued_id = reissued.get_json()["id"]
    reissued_row = db.session.get(Document, reissued_id)

    assert corrected_row.content_hash_sha256 == HASH_B
    assert corrected_row.content_id != original_row.content_id
    assert reissued_row.content_hash_sha256 == HASH_C
    assert reissued_row.content_id != corrected_row.content_id
    assert original_row.content_hash_sha256 == HASH_A
    assert corrected_row.supersedes_document_id == original_id
    assert reissued_row.supersedes_document_id == corrected_id


# ---------------------------------------------------------------------------
# Company links
# ---------------------------------------------------------------------------


def test_add_company_link_preserves_primary_rules(
    client, company, second_company, admin_user, auth_headers
):
    created = _post_document(
        client,
        _create_payload(),
        auth_headers(admin_user),
    )
    document_id = created.get_json()["id"]

    first_link = _add_link(
        client,
        document_id,
        {"company_id": company.id, "is_primary": True, "reason": "Primary"},
        auth_headers(admin_user),
    )

    assert first_link.status_code == 201
    assert first_link.get_json()["company_links"] == [
        {"company_id": company.id, "is_primary": True}
    ]

    duplicate = _add_link(
        client,
        document_id,
        {
            "company_id": company.id,
            "is_primary": False,
            "reason": "Duplicate",
        },
        auth_headers(admin_user),
    )
    assert duplicate.status_code == 400

    second_primary = _add_link(
        client,
        document_id,
        {
            "company_id": second_company.id,
            "is_primary": True,
            "reason": "Second primary",
        },
        auth_headers(admin_user),
    )
    assert second_primary.status_code == 400
    assert "company_links" in second_primary.get_json()["details"]

    assert _count(DocumentCompanyLink) == 1
    link = db.session.scalar(
        sa.select(DocumentCompanyLink).where(
            DocumentCompanyLink.document_id == document_id
        )
    )
    assert link.company_id == company.id
    assert link.is_primary is True


def test_add_company_link_rejects_unknown_company(
    client, admin_user, auth_headers
):
    created = _post_document(
        client,
        _create_payload(),
        auth_headers(admin_user),
    )
    document_id = created.get_json()["id"]

    response = _add_link(
        client,
        document_id,
        {
            "company_id": "missing-company",
            "is_primary": True,
            "reason": "Missing company",
        },
        auth_headers(admin_user),
    )

    assert response.status_code == 404
    assert response.get_json()["error"] == "company_not_found"


# ---------------------------------------------------------------------------
# Lineage
# ---------------------------------------------------------------------------


def test_patch_self_supersession_is_rejected(
    client, company, admin_user, auth_headers
):
    created = _post_document(
        client,
        _create_payload(
            company_links=[
                {"company_id": company.id, "is_primary": True}
            ]
        ),
        auth_headers(admin_user),
    )
    document_id = created.get_json()["id"]

    response = _patch_document(
        client,
        document_id,
        {
            "reason": "Self supersession",
            "changes": {"supersedes_document_id": document_id},
        },
        auth_headers(admin_user),
    )

    assert response.status_code == 400
    assert "supersedes_document_id" in response.get_json()["details"]


def test_patch_cycle_is_rejected(
    client, company, admin_user, auth_headers
):
    first = _post_document(
        client,
        _create_payload(
            document=_document_fields(title="First Report"),
            company_links=[
                {"company_id": company.id, "is_primary": True}
            ],
        ),
        auth_headers(admin_user),
    )
    second = _post_document(
        client,
        _create_payload(
            document=_document_fields(title="Second Report"),
            company_links=[
                {"company_id": company.id, "is_primary": True}
            ],
        ),
        auth_headers(admin_user),
    )
    first_id = first.get_json()["id"]
    second_id = second.get_json()["id"]

    _patch_document(
        client,
        first_id,
        {
            "reason": "First correction link",
            "changes": {"supersedes_document_id": second_id},
        },
        auth_headers(admin_user),
    )
    response = _patch_document(
        client,
        second_id,
        {
            "reason": "Would close a cycle",
            "changes": {"supersedes_document_id": first_id},
        },
        auth_headers(admin_user),
    )

    assert response.status_code == 400
    assert "supersedes_document_id" in response.get_json()["details"]


def test_patch_lineage_does_not_mutate_predecessor(
    client, company, admin_user, auth_headers
):
    original = _post_document(
        client,
        _create_payload(
            document=_document_fields(title="Original Annual Report"),
            company_links=[
                {"company_id": company.id, "is_primary": True}
            ],
        ),
        auth_headers(admin_user),
    )
    original_id = original.get_json()["id"]
    corrected = _post_document(
        client,
        _create_payload(
            document=_document_fields(
                title="Original Annual Report",
                supersedes_document_id=original_id,
            ),
            company_links=[
                {"company_id": company.id, "is_primary": True}
            ],
        ),
        auth_headers(admin_user),
    )
    corrected_id = corrected.get_json()["id"]

    response = _patch_document(
        client,
        corrected_id,
        {
            "reason": "Correct the successor title",
            "changes": {"title": "Corrected Annual Report"},
        },
        auth_headers(admin_user),
    )

    assert response.status_code == 200
    assert db.session.get(Document, original_id).title == (
        "Original Annual Report"
    )
    assert db.session.get(Document, corrected_id).supersedes_document_id == (
        original_id
    )


# ---------------------------------------------------------------------------
# Validation and audit
# ---------------------------------------------------------------------------


def test_unknown_fields_are_rejected(
    client, company, admin_user, auth_headers
):
    payload = _create_payload(
        document=_document_fields(id="server-owned-id"),
        company_links=[
            {"company_id": company.id, "is_primary": True}
        ],
    )

    response = _post_document(
        client, payload, auth_headers(admin_user)
    )

    assert response.status_code == 400
    assert "document.id" in response.get_json()["details"]


def test_server_owned_fields_are_rejected(
    client, company, admin_user, auth_headers
):
    payload = _create_payload(
        document=_document_fields(
            created_by_user_id="server-owned",
            created_at="2026-01-01T00:00:00+00:00",
        ),
        company_links=[
            {"company_id": company.id, "is_primary": True}
        ],
    )

    response = _post_document(
        client, payload, auth_headers(admin_user)
    )

    assert response.status_code == 400
    details = response.get_json()["details"]
    assert "document.created_by_user_id" in details
    assert "document.created_at" in details


def test_duplicate_metadata_conflict_returns_409(
    client, company, admin_user, auth_headers
):
    payload = _create_payload(
        company_links=[
            {"company_id": company.id, "is_primary": True}
        ]
    )
    first = _post_document(
        client, payload, auth_headers(admin_user)
    )

    duplicate = _post_document(
        client, payload, auth_headers(admin_user)
    )

    assert first.status_code == 201
    assert duplicate.status_code == 409
    assert duplicate.get_json()["error"] == "document_duplicate"


def test_missing_document_update_returns_404(
    client, admin_user, auth_headers
):
    response = _patch_document(
        client,
        "99999999-9999-9999-9999-999999999999",
        {"reason": "Missing", "changes": {"title": "Changed"}},
        auth_headers(admin_user),
    )

    assert response.status_code == 404
    assert response.get_json()["error"] == "document_not_found"


def test_update_without_reason_returns_400(
    client, company, admin_user, auth_headers
):
    created = _post_document(
        client,
        _create_payload(
            company_links=[
                {"company_id": company.id, "is_primary": True}
            ]
        ),
        auth_headers(admin_user),
    )
    document_id = created.get_json()["id"]

    response = _patch_document(
        client,
        document_id,
        {"reason": None, "changes": {"title": "Changed"}},
        auth_headers(admin_user),
    )

    assert response.status_code == 400
    assert "reason" in response.get_json()["details"]


def test_metadata_update_writes_append_only_audit_event(
    client, company, admin_user, auth_headers
):
    created = _post_document(
        client,
        _create_payload(
            company_links=[
                {"company_id": company.id, "is_primary": True}
            ]
        ),
        auth_headers(admin_user),
    )
    document_id = created.get_json()["id"]

    response = _patch_document(
        client,
        document_id,
        {
            "reason": "Correct report title",
            "changes": {"title": "Corrected Annual Report"},
        },
        auth_headers(admin_user),
    )

    assert response.status_code == 200
    assert response.get_json()["title"] == "Corrected Annual Report"
    assert _count(DocumentAuditEvent) == 1

    event = db.session.scalar(
        sa.select(DocumentAuditEvent).where(
            DocumentAuditEvent.document_id == document_id
        )
    )
    assert event.event_type == DocumentAuditEventType.METADATA_CHANGED
    assert event.field_changed == "title"
    assert event.actor_user_id == admin_user.id
    assert event.reason == "Correct report title"
