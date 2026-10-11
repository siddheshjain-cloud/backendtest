"""Pre-P5A content-addressed document architecture regression tests.

These tests lock the smallest structural invariant set required before Plan 5
document integrations are designed: immutable binary identity is separate from
business metadata and from storage/provenance locations.
"""

from __future__ import annotations

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from app import db
from app.models import Company, Document
from app.models.document import (
    AcquisitionMethod,
    DiscoverySourceType,
    DistributionStatus,
    DocumentContent,
    DocumentStorageLocation,
    DocumentType,
    IngestionStatus,
    SourceAccess,
)
from app.services.document_library_service import DocumentLibraryService
from app.utils.research_errors import (
    ResearchConflictError,
    ResearchValidationError,
)


VALID_ISIN = "INE0LOJ01019"
HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64


def _make_company(ticker_factory) -> Company:
    ticker = ticker_factory(
        symbol="IKIO",
        instrument_token=1,
        name="IKIO Limited",
    )
    company = Company(
        ticker_id=ticker.id,
        legal_name="IKIO Limited",
        isin=VALID_ISIN,
    )
    db.session.add(company)
    db.session.commit()
    return company


@pytest.fixture
def company(ticker_factory):
    return _make_company(ticker_factory)


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


def _stored_fields(content_hash: str, storage_key: str) -> dict[str, object]:
    return _document_fields(
        source_access=SourceAccess.RESTRICTED,
        acquisition_method=AcquisitionMethod.USER_UPLOAD,
        distribution_status=DistributionStatus.PRIVATE_LIBRARY,
        ingestion_status=IngestionStatus.STORED,
        original_source_url=None,
        provided_by_user_id="provider-user",
        storage_provider="object-store",
        storage_key=storage_key,
        content_hash_sha256=content_hash,
        mime_type="application/pdf",
        file_size_bytes=1024,
    )


def _create(
    admin_user,
    *,
    document: dict[str, object],
    company_links: list[dict[str, object]] | None = None,
) -> Document:
    return DocumentLibraryService.create_document(
        {
            "document": document,
            "company_links": company_links or [],
        },
        actor_user_id=admin_user.id,
    )


def test_identical_sha256_shares_one_immutable_content_identity(
    app,
):
    first = DocumentContent(sha256=HASH_A)
    db.session.add(first)
    db.session.commit()

    duplicate = DocumentContent(sha256=HASH_A)
    db.session.add(duplicate)
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()

    assert (
        db.session.scalar(
            sa.select(sa.func.count())
            .select_from(DocumentContent)
            .where(DocumentContent.sha256 == HASH_A)
        )
        == 1
    )


def test_same_content_identity_supports_two_storage_locations(
    app,
):
    content = DocumentContent(sha256=HASH_A)
    db.session.add(content)
    db.session.flush()
    db.session.add_all(
        [
            DocumentStorageLocation(
                content_id=content.id,
                provider="object-store",
                storage_key="bucket/a/report.pdf",
                original_filename="report.pdf",
            ),
            DocumentStorageLocation(
                content_id=content.id,
                provider="future-drive",
                storage_key="folder/a/report.pdf",
                original_filename="report.pdf",
            ),
        ]
    )
    db.session.commit()

    persisted = db.session.get(DocumentContent, content.id)
    assert persisted.sha256 == HASH_A
    assert {
        (location.provider, location.storage_key)
        for location in persisted.locations
    } == {
        ("object-store", "bucket/a/report.pdf"),
        ("future-drive", "folder/a/report.pdf"),
    }


def test_changing_storage_location_does_not_create_new_binary_identity(
    app,
    admin_user,
    company,
):
    document = _create(
        admin_user,
        document=_stored_fields(HASH_A, "bucket/a/report.pdf"),
        company_links=[{"company_id": company.id, "is_primary": True}],
    )
    original_content_id = document.content_id

    updated = DocumentLibraryService.update_document(
        document_id=document.id,
        changes={
            "storage_provider": "object-store",
            "storage_key": "bucket/a/report-replicated.pdf",
        },
        actor_user_id=admin_user.id,
        reason=None,
    )

    assert updated.content_id == original_content_id
    assert updated.content_hash_sha256 == HASH_A
    assert updated.storage_key == "bucket/a/report-replicated.pdf"
    assert (
        db.session.scalar(
            sa.select(sa.func.count()).select_from(DocumentContent)
        )
        == 1
    )


def test_stored_binary_content_identity_cannot_be_replaced_in_place(
    app,
    admin_user,
    company,
):
    document = _create(
        admin_user,
        document=_stored_fields(HASH_A, "bucket/a/report.pdf"),
        company_links=[{"company_id": company.id, "is_primary": True}],
    )

    with pytest.raises(ResearchValidationError):
        DocumentLibraryService.update_document(
            document_id=document.id,
            changes={"content_hash_sha256": HASH_B},
            actor_user_id=admin_user.id,
            reason="Replace the immutable bytes in place",
        )

    db.session.expire_all()
    persisted = db.session.get(Document, document.id)
    assert persisted.content_hash_sha256 == HASH_A
    assert persisted.content_id == document.content_id


def test_corrected_reissued_bytes_use_new_content_identity_and_lineage(
    app,
    admin_user,
    company,
):
    original = _create(
        admin_user,
        document=_stored_fields(HASH_A, "bucket/a/original.pdf"),
        company_links=[{"company_id": company.id, "is_primary": True}],
    )

    corrected = _create(
        admin_user,
        document=_document_fields(
            title="FY26 Annual Report",
            supersedes_document_id=original.id,
            source_access=SourceAccess.RESTRICTED,
            acquisition_method=AcquisitionMethod.USER_UPLOAD,
            distribution_status=DistributionStatus.PRIVATE_LIBRARY,
            ingestion_status=IngestionStatus.STORED,
            original_source_url=None,
            provided_by_user_id="provider-user",
            storage_provider="object-store",
            storage_key="bucket/a/corrected.pdf",
            content_hash_sha256=HASH_B,
            mime_type="application/pdf",
            file_size_bytes=2048,
        ),
        company_links=[{"company_id": company.id, "is_primary": True}],
    )

    assert corrected.supersedes_document_id == original.id
    assert corrected.content_hash_sha256 == HASH_B
    assert corrected.content_id != original.content_id
    assert original.content_hash_sha256 == HASH_A


def test_unrelated_duplicate_protection_still_works(
    app,
    admin_user,
    company,
):
    _create(
        admin_user,
        document=_stored_fields(HASH_A, "bucket/a/report.pdf"),
        company_links=[{"company_id": company.id, "is_primary": True}],
    )

    with pytest.raises(ResearchConflictError) as exc_info:
        _create(
            admin_user,
            document=_stored_fields(
                HASH_B,
                "bucket/a/different-metadata-same-business-record.pdf",
            ),
            company_links=[{"company_id": company.id, "is_primary": True}],
        )

    assert exc_info.value.code == "document_duplicate"


def test_existing_a_b_c_lineage_remains_valid(
    app,
    admin_user,
    company,
):
    a = _create(
        admin_user,
        document=_stored_fields(HASH_A, "bucket/a/a.pdf"),
        company_links=[{"company_id": company.id, "is_primary": True}],
    )
    b = _create(
        admin_user,
        document=_document_fields(
            supersedes_document_id=a.id,
            source_access=SourceAccess.RESTRICTED,
            acquisition_method=AcquisitionMethod.USER_UPLOAD,
            distribution_status=DistributionStatus.PRIVATE_LIBRARY,
            ingestion_status=IngestionStatus.STORED,
            original_source_url=None,
            provided_by_user_id="provider-user",
            storage_provider="object-store",
            storage_key="bucket/a/b.pdf",
            content_hash_sha256=HASH_B,
            mime_type="application/pdf",
            file_size_bytes=2048,
        ),
        company_links=[{"company_id": company.id, "is_primary": True}],
    )
    c = _create(
        admin_user,
        document=_document_fields(
            supersedes_document_id=b.id,
            source_access=SourceAccess.RESTRICTED,
            acquisition_method=AcquisitionMethod.USER_UPLOAD,
            distribution_status=DistributionStatus.PRIVATE_LIBRARY,
            ingestion_status=IngestionStatus.STORED,
            original_source_url=None,
            provided_by_user_id="provider-user",
            storage_provider="object-store",
            storage_key="bucket/a/c.pdf",
            content_hash_sha256=HASH_C,
            mime_type="application/pdf",
            file_size_bytes=3072,
        ),
        company_links=[{"company_id": company.id, "is_primary": True}],
    )

    assert b.supersedes_document_id == a.id
    assert c.supersedes_document_id == b.id
    assert {row.id for row in a.superseded_by} == {b.id}
    assert {row.id for row in b.superseded_by} == {c.id}
    assert len({a.content_id, b.content_id, c.content_id}) == 3
