"""Plan 4 Task 6: atomic company-link addition and fingerprint recomputation.

Adding a company link must load the complete existing link set, reject
duplicate links and a second primary designation, preserve every rights field,
recompute the metadata fingerprint from the complete sorted company set, and
write exactly one ``COMPANY_LINKS_CHANGED`` audit event in the same transaction
as the link and fingerprint change. Any duplicate conflict or injected failure
must roll back the new link, the fingerprint update, and the audit event.
"""

from __future__ import annotations

from datetime import date

import pytest
import sqlalchemy as sa

from app import db
from app.models import (
    Company,
    Document,
    DocumentAuditEvent,
    DocumentAuditEventType,
    DocumentCompanyLink,
)
from app.models.document import (
    AcquisitionMethod,
    DiscoverySourceType,
    DistributionStatus,
    DocumentType,
    IngestionStatus,
    SourceAccess,
)
from app.services.document_deduplication_service import (
    DocumentDeduplicationService,
)
from app.services.document_library_service import DocumentLibraryService
from app.utils.research_errors import (
    ResearchConflictError,
    ResearchValidationError,
)


VALID_ISIN = "INE0LOJ01019"
SECOND_ISIN = "INE000A01001"
THIRD_ISIN = "INE000A01002"


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
        isin=VALID_ISIN,
        instrument_token=1,
    )


@pytest.fixture
def second_company(ticker_factory):
    return _make_company(
        ticker_factory,
        symbol="ACME",
        isin=SECOND_ISIN,
        instrument_token=2,
    )


@pytest.fixture
def third_company(ticker_factory):
    return _make_company(
        ticker_factory,
        symbol="ZETA",
        isin=THIRD_ISIN,
        instrument_token=3,
    )


def _document_fields(**overrides: object) -> dict[str, object]:
    fields: dict[str, object] = {
        "document_type": DocumentType.ANNUAL_REPORT,
        "title": "FY26 Annual Report",
        "document_date": "2026-06-30",
        "reporting_period": "FY26",
        "publisher_name": "IKIO Lighting Limited",
        "original_source_url": "https://publisher.example/report",
        "discovery_source_type": DiscoverySourceType.OFFICIAL_SITE,
        "source_access": SourceAccess.PUBLIC,
        "acquisition_method": AcquisitionMethod.MANUAL_REFERENCE,
        "distribution_status": DistributionStatus.LINK_ONLY,
        "ingestion_status": IngestionStatus.DISCOVERED,
    }
    fields.update(overrides)
    return fields


def _payload(
    *,
    document: dict[str, object] | None = None,
    company_links: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    return {
        "document": document or _document_fields(),
        "company_links": company_links or [],
    }


def _create_document(
    admin_user,
    *,
    document: dict[str, object] | None = None,
    company_links: list[dict[str, object]] | None = None,
) -> Document:
    return DocumentLibraryService.create_document(
        _payload(
            document=document,
            company_links=company_links,
        ),
        actor_user_id=admin_user.id,
    )


def _link_count(document_id: str) -> int:
    return (
        db.session.scalar(
            sa.select(sa.func.count())
            .select_from(DocumentCompanyLink)
            .where(DocumentCompanyLink.document_id == document_id)
        )
        or 0
    )


def _audit_count(document_id: str) -> int:
    return (
        db.session.scalar(
            sa.select(sa.func.count())
            .select_from(DocumentAuditEvent)
            .where(DocumentAuditEvent.document_id == document_id)
        )
        or 0
    )


def _audit_event(document_id: str) -> DocumentAuditEvent | None:
    return db.session.scalar(
        sa.select(DocumentAuditEvent)
        .where(DocumentAuditEvent.document_id == document_id)
        .limit(1)
    )


def _link_ids(document_id: str) -> list[str]:
    rows = db.session.scalars(
        sa.select(DocumentCompanyLink)
        .where(DocumentCompanyLink.document_id == document_id)
        .order_by(DocumentCompanyLink.company_id)
    ).all()
    return [row.company_id for row in rows]


def _rights_snapshot(document: Document) -> dict[str, object]:
    return {
        "source_access": document.source_access,
        "acquisition_method": document.acquisition_method,
        "distribution_status": document.distribution_status,
        "ingestion_status": document.ingestion_status,
        "storage_provider": document.storage_provider,
        "storage_key": document.storage_key,
        "content_hash_sha256": document.content_hash_sha256,
        "mime_type": document.mime_type,
        "file_size_bytes": document.file_size_bytes,
        "provided_by_user_id": document.provided_by_user_id,
        "distribution_basis": document.distribution_basis,
        "rights_verified_by_user_id": document.rights_verified_by_user_id,
        "rights_verified_at": document.rights_verified_at,
        "original_source_url": document.original_source_url,
        "discovery_source_type": document.discovery_source_type,
        "discovery_source_reference": document.discovery_source_reference,
    }


def test_add_company_link_recomputes_fingerprint_and_writes_focused_audit(
    app, admin_user, company, second_company
):
    document = _create_document(
        admin_user,
        company_links=[
            {"company_id": company.id, "is_primary": True}
        ],
    )
    original_fingerprint = document.metadata_fingerprint
    original_rights = _rights_snapshot(document)

    updated = DocumentLibraryService.add_company_link(
        document_id=document.id,
        company_id=second_company.id,
        is_primary=False,
        actor_user_id=admin_user.id,
        reason="Add peer-company coverage",
    )

    persisted = db.session.get(Document, document.id)
    expected_fingerprint = DocumentDeduplicationService.metadata_fingerprint(
        document_type=persisted.document_type,
        company_ids=sorted([company.id, second_company.id]),
        document_date=persisted.document_date,
        title=persisted.title,
        publisher_name=persisted.publisher_name,
        institution_id=None,
        reporting_period=persisted.reporting_period,
        report_type=None,
    )

    assert updated.id == document.id
    assert persisted.metadata_fingerprint == expected_fingerprint
    assert persisted.metadata_fingerprint != original_fingerprint
    assert _link_ids(document.id) == sorted(
        [company.id, second_company.id]
    )
    assert _rights_snapshot(persisted) == original_rights
    assert _audit_count(document.id) == 1

    event = _audit_event(document.id)
    assert event is not None
    assert event.event_type == DocumentAuditEventType.COMPANY_LINKS_CHANGED
    assert event.field_changed == "company_links"
    assert event.actor_user_id == admin_user.id
    assert event.reason == "Add peer-company coverage"
    assert event.old_value == {
        "company_ids": [company.id],
        "primary_company_id": company.id,
        "metadata_fingerprint": original_fingerprint,
    }
    assert event.new_value == {
        "company_ids": sorted([company.id, second_company.id]),
        "primary_company_id": company.id,
        "metadata_fingerprint": expected_fingerprint,
    }


def test_duplicate_company_link_is_rejected_without_mutation(
    app, admin_user, company
):
    document = _create_document(
        admin_user,
        company_links=[
            {"company_id": company.id, "is_primary": True}
        ],
    )
    original_fingerprint = document.metadata_fingerprint
    original_rights = _rights_snapshot(document)

    with pytest.raises(ResearchValidationError) as exc_info:
        DocumentLibraryService.add_company_link(
            document_id=document.id,
            company_id=company.id,
            is_primary=False,
            actor_user_id=admin_user.id,
            reason="Duplicate attempt",
        )

    assert "company_links" in exc_info.value.details
    persisted = db.session.get(Document, document.id)
    assert persisted.metadata_fingerprint == original_fingerprint
    assert _rights_snapshot(persisted) == original_rights
    assert _link_count(document.id) == 1
    assert _audit_count(document.id) == 0


def test_second_primary_company_link_is_rejected_without_mutation(
    app, admin_user, company, second_company
):
    document = _create_document(
        admin_user,
        company_links=[
            {"company_id": company.id, "is_primary": True}
        ],
    )
    original_fingerprint = document.metadata_fingerprint
    original_rights = _rights_snapshot(document)

    with pytest.raises(ResearchValidationError) as exc_info:
        DocumentLibraryService.add_company_link(
            document_id=document.id,
            company_id=second_company.id,
            is_primary=True,
            actor_user_id=admin_user.id,
            reason="Attempt second primary",
        )

    assert "company_links" in exc_info.value.details
    persisted = db.session.get(Document, document.id)
    assert persisted.metadata_fingerprint == original_fingerprint
    assert _rights_snapshot(persisted) == original_rights
    assert _link_count(document.id) == 1
    assert _audit_count(document.id) == 0


def test_zero_primary_multi_company_result_recomputes_sorted_fingerprint(
    app,
    admin_user,
    company,
    second_company,
    third_company,
):
    document = _create_document(
        admin_user,
        document=_document_fields(
            document_type=DocumentType.INDUSTRY_REPORT,
            title="LED Industry Review",
            publisher_name=None,
        ),
        company_links=[
            {"company_id": second_company.id, "is_primary": False},
            {"company_id": company.id, "is_primary": False},
        ],
    )
    original_fingerprint = document.metadata_fingerprint

    updated = DocumentLibraryService.add_company_link(
        document_id=document.id,
        company_id=third_company.id,
        is_primary=False,
        actor_user_id=admin_user.id,
        reason="Add another industry peer",
    )

    persisted = db.session.get(Document, document.id)
    expected_fingerprint = DocumentDeduplicationService.metadata_fingerprint(
        document_type=persisted.document_type,
        company_ids=sorted(
            [company.id, second_company.id, third_company.id]
        ),
        document_date=persisted.document_date,
        title=persisted.title,
        publisher_name=persisted.publisher_name,
        institution_id=None,
        reporting_period=persisted.reporting_period,
        report_type=None,
    )

    assert updated.id == document.id
    assert persisted.metadata_fingerprint == expected_fingerprint
    assert persisted.metadata_fingerprint != original_fingerprint
    assert _link_ids(document.id) == sorted(
        [company.id, second_company.id, third_company.id]
    )
    links = db.session.scalars(
        sa.select(DocumentCompanyLink).where(
            DocumentCompanyLink.document_id == document.id
        )
    ).all()
    assert all(link.is_primary is False for link in links)
    assert _audit_count(document.id) == 1

    event = _audit_event(document.id)
    assert event is not None
    assert event.old_value["primary_company_id"] is None
    assert event.new_value["primary_company_id"] is None
    assert event.old_value["metadata_fingerprint"] == original_fingerprint
    assert event.new_value["metadata_fingerprint"] == expected_fingerprint


def test_fingerprint_is_independent_of_link_order_and_primary_designation(
    app, admin_user, company, second_company
):
    document = _create_document(
        admin_user,
        company_links=[
            {"company_id": company.id, "is_primary": True}
        ],
    )

    DocumentLibraryService.add_company_link(
        document_id=document.id,
        company_id=second_company.id,
        is_primary=False,
        actor_user_id=admin_user.id,
        reason="Add peer company",
    )

    persisted = db.session.get(Document, document.id)
    forward = DocumentDeduplicationService.metadata_fingerprint(
        document_type=persisted.document_type,
        company_ids=[company.id, second_company.id],
        document_date=persisted.document_date,
        title=persisted.title,
        publisher_name=persisted.publisher_name,
        institution_id=None,
        reporting_period=persisted.reporting_period,
        report_type=None,
    )
    reversed_ids = DocumentDeduplicationService.metadata_fingerprint(
        document_type=persisted.document_type,
        company_ids=[second_company.id, company.id],
        document_date=persisted.document_date,
        title=persisted.title,
        publisher_name=persisted.publisher_name,
        institution_id=None,
        reporting_period=persisted.reporting_period,
        report_type=None,
    )

    assert persisted.metadata_fingerprint == forward
    assert persisted.metadata_fingerprint == reversed_ids


def test_duplicate_fingerprint_conflict_rolls_back_link_fingerprint_and_audit(
    app, admin_user, company, second_company
):
    _create_document(
        admin_user,
        company_links=[
            {"company_id": company.id, "is_primary": False},
            {"company_id": second_company.id, "is_primary": False},
        ],
    )
    target = _create_document(
        admin_user,
        company_links=[
            {"company_id": company.id, "is_primary": False}
        ],
    )
    original_fingerprint = target.metadata_fingerprint
    original_rights = _rights_snapshot(target)

    with pytest.raises(ResearchConflictError) as exc_info:
        DocumentLibraryService.add_company_link(
            document_id=target.id,
            company_id=second_company.id,
            is_primary=False,
            actor_user_id=admin_user.id,
            reason="Attempt duplicate aggregate",
        )

    assert exc_info.value.code == "document_duplicate"
    assert exc_info.value.message == "Document requires duplicate review"
    persisted = db.session.get(Document, target.id)
    assert persisted.metadata_fingerprint == original_fingerprint
    assert _rights_snapshot(persisted) == original_rights
    assert _link_count(target.id) == 1
    assert _audit_count(target.id) == 0


def test_injected_failure_after_mutation_rolls_back(
    app, admin_user, company, second_company, monkeypatch
):
    document = _create_document(
        admin_user,
        company_links=[
            {"company_id": company.id, "is_primary": True}
        ],
    )
    original_fingerprint = document.metadata_fingerprint
    original_rights = _rights_snapshot(document)

    def fail_commit():
        raise RuntimeError("injected before commit")

    monkeypatch.setattr(db.session, "commit", fail_commit)

    with pytest.raises(RuntimeError, match="injected before commit"):
        DocumentLibraryService.add_company_link(
            document_id=document.id,
            company_id=second_company.id,
            is_primary=False,
            actor_user_id=admin_user.id,
            reason="Injected failure",
        )

    persisted = db.session.get(Document, document.id)
    assert persisted.metadata_fingerprint == original_fingerprint
    assert _rights_snapshot(persisted) == original_rights
    assert _link_count(document.id) == 1
    assert _audit_count(document.id) == 0
