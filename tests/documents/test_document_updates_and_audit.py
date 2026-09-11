"""Plan 4 Task 7: audited document metadata, state, rights, and archival changes.

``DocumentLibraryService.update_document`` owns validated, transactional
metadata and state corrections. It must lock the existing Document, validate
the complete resulting state, reject Milestone 1's forbidden
``STORED -> ANALYSED`` transition, recompute and deduplicate only when a
canonical fingerprint input changes, and write focused append-only
``DocumentAuditEvent`` rows in the same transaction as the applied changes.
Audit rows must never contain raw storage keys or file content. Material
storage identity is recorded as a content hash and a deterministic storage-key
digest so binary replacements remain traceable.
"""

from __future__ import annotations

import hashlib
import json

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
    DuplicateDecision,
)
from app.services.document_library_service import DocumentLibraryService
from app.utils.research_errors import (
    ResearchConflictError,
    ResearchValidationError,
)


VALID_ISIN = "INE0LOJ01019"
SECOND_ISIN = "INE000A01001"
STORED_SHA256 = "a" * 64
VERIFIED_AT = "2026-09-04T09:30:00+00:00"
ARCHIVED_AT = "2026-09-04T10:00:00+00:00"


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


def _stored_document_fields(**overrides: object) -> dict[str, object]:
    provided_by_user_id = overrides.pop(
        "provided_by_user_id", "provider-user"
    )
    return _document_fields(
        source_access=SourceAccess.RESTRICTED,
        acquisition_method=AcquisitionMethod.USER_UPLOAD,
        distribution_status=DistributionStatus.PRIVATE_LIBRARY,
        ingestion_status=IngestionStatus.STORED,
        original_source_url=None,
        provided_by_user_id=provided_by_user_id,
        storage_provider="object-store",
        storage_key="opaque/object/key",
        content_hash_sha256=STORED_SHA256,
        mime_type="application/pdf",
        file_size_bytes=1024,
        **overrides,
    )


def _create_document(
    admin_user,
    *,
    document: dict[str, object] | None = None,
    company_links: list[dict[str, object]] | None = None,
) -> Document:
    return DocumentLibraryService.create_document(
        {
            "document": document or _document_fields(),
            "company_links": company_links
            if company_links is not None
            else [],
        },
        actor_user_id=admin_user.id,
    )


def _audit_rows(document_id: str) -> list[DocumentAuditEvent]:
    rows = db.session.scalars(
        sa.select(DocumentAuditEvent)
        .where(DocumentAuditEvent.document_id == document_id)
        .order_by(DocumentAuditEvent.created_at, DocumentAuditEvent.id)
    ).all()
    return sorted(
        rows,
        key=lambda row: json.dumps(
            row.old_value, sort_keys=True, default=str
        ),
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
    }


def _event_by_type(
    document_id: str, event_type: str
) -> DocumentAuditEvent | None:
    return db.session.scalar(
        sa.select(DocumentAuditEvent).where(
            DocumentAuditEvent.document_id == document_id,
            DocumentAuditEvent.event_type == event_type,
        )
    )


def _event_by_field(
    document_id: str, field_changed: str
) -> DocumentAuditEvent | None:
    return db.session.scalar(
        sa.select(DocumentAuditEvent).where(
            DocumentAuditEvent.document_id == document_id,
            DocumentAuditEvent.field_changed == field_changed,
        )
    )


def test_update_source_access_writes_focused_audit_without_fingerprint_change(
    app, admin_user, company
):
    document = _create_document(
        admin_user,
        company_links=[{"company_id": company.id, "is_primary": True}],
    )
    original_fingerprint = document.metadata_fingerprint

    updated = DocumentLibraryService.update_document(
        document_id=document.id,
        changes={"source_access": SourceAccess.RESTRICTED},
        actor_user_id=admin_user.id,
        reason="Correct source accessibility",
    )

    persisted = db.session.get(Document, document.id)
    assert updated.id == document.id
    assert persisted.source_access == SourceAccess.RESTRICTED
    assert persisted.metadata_fingerprint == original_fingerprint
    assert _audit_count(document.id) == 1

    event = _audit_rows(document.id)[0]
    assert event.event_type == DocumentAuditEventType.SOURCE_ACCESS_CHANGED
    assert event.field_changed == "source_access"
    assert event.old_value == SourceAccess.PUBLIC
    assert event.new_value == SourceAccess.RESTRICTED
    assert event.actor_user_id == admin_user.id
    assert event.reason == "Correct source accessibility"


def test_update_acquisition_method_writes_focused_audit(
    app, admin_user, company
):
    document = _create_document(
        admin_user,
        company_links=[{"company_id": company.id, "is_primary": True}],
    )

    DocumentLibraryService.update_document(
        document_id=document.id,
        changes={"acquisition_method": AcquisitionMethod.NOT_ACQUIRED},
        actor_user_id=admin_user.id,
        reason="The report was never acquired",
    )

    event = _event_by_type(
        document.id, DocumentAuditEventType.ACQUISITION_METHOD_CHANGED
    )
    assert event is not None
    assert event.field_changed == "acquisition_method"
    assert event.old_value == AcquisitionMethod.MANUAL_REFERENCE
    assert event.new_value == AcquisitionMethod.NOT_ACQUIRED
    assert db.session.get(
        Document, document.id
    ).acquisition_method == AcquisitionMethod.NOT_ACQUIRED


def test_update_distribution_status_writes_focused_audit(
    app, admin_user, company
):
    document = _create_document(
        admin_user,
        company_links=[{"company_id": company.id, "is_primary": True}],
    )

    DocumentLibraryService.update_document(
        document_id=document.id,
        changes={"distribution_status": DistributionStatus.UNKNOWN},
        actor_user_id=admin_user.id,
        reason="Distribution status is unknown",
    )

    event = _event_by_type(
        document.id, DocumentAuditEventType.DISTRIBUTION_STATUS_CHANGED
    )
    assert event is not None
    assert event.field_changed == "distribution_status"
    assert event.old_value == DistributionStatus.LINK_ONLY
    assert event.new_value == DistributionStatus.UNKNOWN


def test_approved_ingestion_transition_writes_focused_audit(
    app, admin_user, company
):
    document = _create_document(
        admin_user,
        company_links=[{"company_id": company.id, "is_primary": True}],
    )

    DocumentLibraryService.update_document(
        document_id=document.id,
        changes={"ingestion_status": IngestionStatus.AWAITING_UPLOAD},
        actor_user_id=admin_user.id,
        reason="Waiting for the provider upload",
    )

    event = _event_by_type(
        document.id, DocumentAuditEventType.INGESTION_STATUS_CHANGED
    )
    assert event is not None
    assert event.field_changed == "ingestion_status"
    assert event.old_value == IngestionStatus.DISCOVERED
    assert event.new_value == IngestionStatus.AWAITING_UPLOAD


def test_non_rights_ingestion_transition_allows_null_reason(
    app, admin_user, company
):
    document = _create_document(
        admin_user,
        company_links=[{"company_id": company.id, "is_primary": True}],
    )

    DocumentLibraryService.update_document(
        document_id=document.id,
        changes={"ingestion_status": IngestionStatus.AWAITING_UPLOAD},
        actor_user_id=admin_user.id,
        reason=None,
    )

    event = _event_by_type(
        document.id, DocumentAuditEventType.INGESTION_STATUS_CHANGED
    )
    assert event is not None
    assert event.reason is None
    assert db.session.get(
        Document, document.id
    ).ingestion_status == IngestionStatus.AWAITING_UPLOAD


def test_stored_to_analysed_transition_is_rejected_without_mutation(
    app, admin_user, company
):
    document = _create_document(
        admin_user,
        document=_stored_document_fields(
            provided_by_user_id=admin_user.id
        ),
        company_links=[{"company_id": company.id, "is_primary": True}],
    )
    original_fingerprint = document.metadata_fingerprint
    original_rights = _rights_snapshot(document)

    with pytest.raises(ResearchValidationError) as exc_info:
        DocumentLibraryService.update_document(
            document_id=document.id,
            changes={"ingestion_status": IngestionStatus.ANALYSED},
            actor_user_id=admin_user.id,
            reason="Attempt forbidden analysis transition",
        )

    assert "ingestion_status" in exc_info.value.details
    persisted = db.session.get(Document, document.id)
    assert persisted.ingestion_status == IngestionStatus.STORED
    assert persisted.metadata_fingerprint == original_fingerprint
    assert _rights_snapshot(persisted) == original_rights
    assert _audit_count(document.id) == 0


def test_invalid_ingestion_transition_is_rejected(
    app, admin_user, company
):
    document = _create_document(
        admin_user,
        document=_document_fields(
            ingestion_status=IngestionStatus.AWAITING_UPLOAD
        ),
        company_links=[{"company_id": company.id, "is_primary": True}],
    )

    with pytest.raises(ResearchValidationError) as exc_info:
        DocumentLibraryService.update_document(
            document_id=document.id,
            changes={"ingestion_status": IngestionStatus.DISCOVERED},
            actor_user_id=admin_user.id,
            reason="Invalid backward transition",
        )

    assert "ingestion_status" in exc_info.value.details
    assert db.session.get(
        Document, document.id
    ).ingestion_status == IngestionStatus.AWAITING_UPLOAD
    assert _audit_count(document.id) == 0


def test_rights_verification_writes_verified_event(
    app, admin_user, company
):
    document = _create_document(
        admin_user,
        company_links=[{"company_id": company.id, "is_primary": True}],
    )
    DocumentLibraryService.update_document(
        document_id=document.id,
        changes={
            "distribution_status": DistributionStatus.APP_DISTRIBUTABLE,
            "distribution_basis": "Signed redistribution licence on file",
            "rights_verified_by_user_id": admin_user.id,
            "rights_verified_at": VERIFIED_AT,
        },
        actor_user_id=admin_user.id,
        reason="Verified distribution rights",
    )

    persisted = db.session.get(Document, document.id)
    assert persisted.distribution_status == DistributionStatus.APP_DISTRIBUTABLE
    assert persisted.distribution_basis == (
        "Signed redistribution licence on file"
    )
    assert persisted.rights_verified_by_user_id == admin_user.id

    distribution_event = _event_by_type(
        document.id, DocumentAuditEventType.DISTRIBUTION_STATUS_CHANGED
    )
    assert distribution_event is not None
    assert distribution_event.old_value == DistributionStatus.LINK_ONLY
    assert distribution_event.new_value == DistributionStatus.APP_DISTRIBUTABLE

    rights_event = _event_by_type(
        document.id, DocumentAuditEventType.RIGHTS_VERIFIED
    )
    assert rights_event is not None
    assert rights_event.field_changed == "rights_verification"
    assert rights_event.old_value == {
        "distribution_basis": None,
        "rights_verified_by_user_id": None,
        "rights_verified_at": None,
    }
    assert rights_event.new_value == {
        "distribution_basis": "Signed redistribution licence on file",
        "rights_verified_by_user_id": admin_user.id,
        "rights_verified_at": VERIFIED_AT,
    }
    assert rights_event.actor_user_id == admin_user.id
    assert rights_event.reason == "Verified distribution rights"


def test_rights_verification_revocation_writes_revoked_event(
    app, admin_user, company
):
    document = _create_document(
        admin_user,
        document=_document_fields(
            distribution_status=DistributionStatus.APP_DISTRIBUTABLE,
            distribution_basis="Signed redistribution licence on file",
            rights_verified_by_user_id=admin_user.id,
            rights_verified_at=VERIFIED_AT,
        ),
        company_links=[{"company_id": company.id, "is_primary": True}],
    )

    DocumentLibraryService.update_document(
        document_id=document.id,
        changes={
            "distribution_status": DistributionStatus.UNKNOWN,
            "distribution_basis": None,
            "rights_verified_by_user_id": None,
            "rights_verified_at": None,
        },
        actor_user_id=admin_user.id,
        reason="Rights evidence was revoked",
    )

    revoked_event = _event_by_type(
        document.id, DocumentAuditEventType.RIGHTS_VERIFICATION_REVOKED
    )
    assert revoked_event is not None
    assert revoked_event.field_changed == "rights_verification"
    assert revoked_event.old_value == {
        "distribution_basis": "Signed redistribution licence on file",
        "rights_verified_by_user_id": admin_user.id,
        "rights_verified_at": VERIFIED_AT,
    }
    assert revoked_event.new_value == {
        "distribution_basis": None,
        "rights_verified_by_user_id": None,
        "rights_verified_at": None,
    }


def test_storage_attachment_metadata_is_audited_without_raw_keys(
    app, admin_user, company
):
    document = _create_document(
        admin_user,
        document=_stored_document_fields(
            provided_by_user_id=admin_user.id
        ),
        company_links=[{"company_id": company.id, "is_primary": True}],
    )
    original_fingerprint = document.metadata_fingerprint

    DocumentLibraryService.update_document(
        document_id=document.id,
        changes={
            "mime_type": "application/octet-stream",
            "file_size_bytes": 2048,
        },
        actor_user_id=admin_user.id,
        reason="Correct stored-file metadata",
    )

    persisted = db.session.get(Document, document.id)
    assert persisted.mime_type == "application/octet-stream"
    assert persisted.file_size_bytes == 2048
    assert persisted.metadata_fingerprint == original_fingerprint

    event = _event_by_type(
        document.id, DocumentAuditEventType.STORAGE_ATTACHED
    )
    assert event is not None
    assert event.field_changed == "storage_attachment"
    for audit_value in (event.old_value, event.new_value):
        serialized = json.dumps(audit_value, sort_keys=True)
        assert '"storage_key":' not in serialized
        assert "opaque/object/key" not in serialized

    assert event.old_value == {
        "storage_provider": "object-store",
        "storage_key_sha256": hashlib.sha256(
            b"opaque/object/key"
        ).hexdigest(),
        "content_hash_sha256": STORED_SHA256,
        "mime_type": "application/pdf",
        "file_size_bytes": 1024,
    }
    assert event.new_value == {
        "storage_provider": "object-store",
        "storage_key_sha256": hashlib.sha256(
            b"opaque/object/key"
        ).hexdigest(),
        "content_hash_sha256": STORED_SHA256,
        "mime_type": "application/octet-stream",
        "file_size_bytes": 2048,
    }


def test_archive_and_restore_write_audit_events(
    app, admin_user, company
):
    document = _create_document(
        admin_user,
        company_links=[{"company_id": company.id, "is_primary": True}],
    )
    DocumentLibraryService.update_document(
        document_id=document.id,
        changes={"archived_at": ARCHIVED_AT},
        actor_user_id=admin_user.id,
        reason="Duplicate of the official filing",
    )

    archived_event = _event_by_type(
        document.id, DocumentAuditEventType.ARCHIVED
    )
    assert archived_event is not None
    assert archived_event.field_changed == "archived_at"
    assert archived_event.old_value is None
    assert archived_event.new_value == ARCHIVED_AT

    DocumentLibraryService.update_document(
        document_id=document.id,
        changes={"archived_at": None},
        actor_user_id=admin_user.id,
        reason="Restore after duplicate review",
    )

    restored_event = _event_by_type(
        document.id, DocumentAuditEventType.RESTORED
    )
    assert restored_event is not None
    assert restored_event.field_changed == "archived_at"
    assert restored_event.old_value == ARCHIVED_AT
    assert restored_event.new_value is None
    assert _audit_count(document.id) == 2


def test_canonical_metadata_change_recomputes_fingerprint(
    app, admin_user, company
):
    document = _create_document(
        admin_user,
        company_links=[{"company_id": company.id, "is_primary": True}],
    )
    original_fingerprint = document.metadata_fingerprint

    DocumentLibraryService.update_document(
        document_id=document.id,
        changes={"title": "Corrected FY26 Annual Report"},
        actor_user_id=admin_user.id,
        reason="Correct the report title",
    )

    persisted = db.session.get(Document, document.id)
    expected_fingerprint = DocumentDeduplicationService.metadata_fingerprint(
        document_type=persisted.document_type,
        company_ids=[company.id],
        document_date=persisted.document_date,
        title="Corrected FY26 Annual Report",
        publisher_name=persisted.publisher_name,
        institution_id=None,
        reporting_period=persisted.reporting_period,
        report_type=None,
    )

    assert persisted.title == "Corrected FY26 Annual Report"
    assert persisted.metadata_fingerprint == expected_fingerprint
    assert persisted.metadata_fingerprint != original_fingerprint


def test_canonical_change_restores_ordinary_fingerprint_race_guard(
    app, admin_user, company, monkeypatch
):
    original = _create_document(
        admin_user,
        company_links=[{"company_id": company.id, "is_primary": True}],
    )
    corrected = _create_document(
        admin_user,
        document=_document_fields(supersedes_document_id=original.id),
        company_links=[{"company_id": company.id, "is_primary": True}],
    )
    DocumentLibraryService.update_document(
        document_id=corrected.id,
        changes={"title": "Corrected FY26 Annual Report"},
        actor_user_id=admin_user.id,
        reason="Correct the report title",
    )

    def _stale_no_match(**_kwargs: object) -> DuplicateDecision:
        return DuplicateDecision(kind="NONE", matched_document_id=None)

    monkeypatch.setattr(
        DocumentDeduplicationService,
        "find_duplicate",
        staticmethod(_stale_no_match),
    )

    with pytest.raises(ResearchConflictError) as exc_info:
        _create_document(
            admin_user,
            document=_document_fields(
                title="Corrected FY26 Annual Report",
            ),
            company_links=[
                {"company_id": company.id, "is_primary": True}
            ],
        )

    assert exc_info.value.code == "document_duplicate"


def test_predecessor_canonical_change_transfers_the_old_race_guard(
    app, admin_user, company, monkeypatch
):
    original = _create_document(
        admin_user,
        company_links=[{"company_id": company.id, "is_primary": True}],
    )
    corrected = _create_document(
        admin_user,
        document=_document_fields(supersedes_document_id=original.id),
        company_links=[{"company_id": company.id, "is_primary": True}],
    )
    DocumentLibraryService.update_document(
        document_id=original.id,
        changes={"title": "Original report retitled"},
        actor_user_id=admin_user.id,
        reason="Correct the original title",
    )
    assert (
        db.session.get(Document, corrected.id).is_fingerprint_duplicate
        is False
    )

    def _stale_no_match(**_kwargs: object) -> DuplicateDecision:
        return DuplicateDecision(kind="NONE", matched_document_id=None)

    monkeypatch.setattr(
        DocumentDeduplicationService,
        "find_duplicate",
        staticmethod(_stale_no_match),
    )

    with pytest.raises(ResearchConflictError) as exc_info:
        _create_document(
            admin_user,
            company_links=[
                {"company_id": company.id, "is_primary": True}
            ],
        )

    assert exc_info.value.code == "document_duplicate"


def test_noncanonical_metadata_change_does_not_recompute_fingerprint(
    app, admin_user, company
):
    document = _create_document(
        admin_user,
        company_links=[{"company_id": company.id, "is_primary": True}],
    )
    original_fingerprint = document.metadata_fingerprint

    DocumentLibraryService.update_document(
        document_id=document.id,
        changes={
            "publisher_reference": "Corrected publisher reference",
            "discovery_source_type": DiscoverySourceType.TELEGRAM,
            "discovery_source_reference": "Telegram research channel",
        },
        actor_user_id=admin_user.id,
        reason="Correct discovery provenance",
    )

    persisted = db.session.get(Document, document.id)
    assert persisted.publisher_reference == "Corrected publisher reference"
    assert persisted.discovery_source_type == DiscoverySourceType.TELEGRAM
    assert persisted.discovery_source_reference == "Telegram research channel"
    assert persisted.metadata_fingerprint == original_fingerprint


def test_provenance_changes_write_metadata_audit_events(
    app, admin_user, company
):
    document = _create_document(
        admin_user,
        company_links=[{"company_id": company.id, "is_primary": True}],
    )

    DocumentLibraryService.update_document(
        document_id=document.id,
        changes={
            "publisher_reference": "Corrected publisher reference",
            "discovery_source_type": DiscoverySourceType.TELEGRAM,
            "discovery_source_reference": "Telegram research channel",
        },
        actor_user_id=admin_user.id,
        reason="Correct discovery provenance",
    )

    assert _audit_count(document.id) == 3
    for field, old_value, new_value in (
        (
            "publisher_reference",
            None,
            "Corrected publisher reference",
        ),
        (
            "discovery_source_type",
            DiscoverySourceType.OFFICIAL_SITE,
            DiscoverySourceType.TELEGRAM,
        ),
        (
            "discovery_source_reference",
            None,
            "Telegram research channel",
        ),
    ):
        event = _event_by_field(document.id, field)
        assert event is not None
        assert event.event_type == DocumentAuditEventType.METADATA_CHANGED
        assert event.old_value == old_value
        assert event.new_value == new_value
        assert event.actor_user_id == admin_user.id
        assert event.reason == "Correct discovery provenance"


def test_supersedes_change_writes_metadata_audit_event(
    app, admin_user, company
):
    original = _create_document(
        admin_user,
        document=_document_fields(title="Original annual report"),
        company_links=[{"company_id": company.id, "is_primary": True}],
    )
    corrected = _create_document(
        admin_user,
        document=_document_fields(title="Corrected annual report"),
        company_links=[{"company_id": company.id, "is_primary": True}],
    )

    DocumentLibraryService.update_document(
        document_id=corrected.id,
        changes={"supersedes_document_id": original.id},
        actor_user_id=admin_user.id,
        reason="Link corrected report to its predecessor",
    )

    event = _event_by_field(corrected.id, "supersedes_document_id")
    assert event is not None
    assert event.event_type == DocumentAuditEventType.METADATA_CHANGED
    assert event.old_value is None
    assert event.new_value == original.id
    assert event.reason == "Link corrected report to its predecessor"


@pytest.mark.parametrize("replacement", [None, "unrelated"])
def test_same_fingerprint_successor_cannot_leave_its_direct_predecessor(
    app, admin_user, company, replacement
):
    original = _create_document(
        admin_user,
        company_links=[{"company_id": company.id, "is_primary": True}],
    )
    corrected = _create_document(
        admin_user,
        document=_document_fields(supersedes_document_id=original.id),
        company_links=[{"company_id": company.id, "is_primary": True}],
    )
    unrelated = _create_document(
        admin_user,
        document=_document_fields(title="Unrelated report"),
        company_links=[{"company_id": company.id, "is_primary": True}],
    )
    replacement_id = unrelated.id if replacement == "unrelated" else None

    with pytest.raises(ResearchConflictError) as exc_info:
        DocumentLibraryService.update_document(
            document_id=corrected.id,
            changes={"supersedes_document_id": replacement_id},
            actor_user_id=admin_user.id,
            reason="Replace the predecessor link",
        )

    assert exc_info.value.code == "document_duplicate"
    assert corrected.supersedes_document_id == original.id
    assert _audit_count(corrected.id) == 0


def test_update_rejects_two_document_supersession_cycle(
    app, admin_user, company
):
    first = _create_document(
        admin_user,
        document=_document_fields(title="First report"),
        company_links=[{"company_id": company.id, "is_primary": True}],
    )
    second = _create_document(
        admin_user,
        document=_document_fields(title="Second report"),
        company_links=[{"company_id": company.id, "is_primary": True}],
    )
    DocumentLibraryService.update_document(
        document_id=first.id,
        changes={"supersedes_document_id": second.id},
        actor_user_id=admin_user.id,
        reason="First correction link",
    )

    with pytest.raises(ResearchValidationError) as exc_info:
        DocumentLibraryService.update_document(
            document_id=second.id,
            changes={"supersedes_document_id": first.id},
            actor_user_id=admin_user.id,
            reason="Would close a two-document cycle",
        )

    assert "supersedes_document_id" in exc_info.value.details
    assert db.session.get(Document, second.id).supersedes_document_id is None
    assert _audit_count(second.id) == 0


def test_update_rejects_longer_supersession_cycle(
    app, admin_user, company
):
    original = _create_document(
        admin_user,
        document=_document_fields(title="Original report"),
        company_links=[{"company_id": company.id, "is_primary": True}],
    )
    corrected = _create_document(
        admin_user,
        document=_document_fields(title="Corrected report"),
        company_links=[{"company_id": company.id, "is_primary": True}],
    )
    reissued = _create_document(
        admin_user,
        document=_document_fields(title="Reissued report"),
        company_links=[{"company_id": company.id, "is_primary": True}],
    )
    DocumentLibraryService.update_document(
        document_id=corrected.id,
        changes={"supersedes_document_id": original.id},
        actor_user_id=admin_user.id,
        reason="Correct the original",
    )
    DocumentLibraryService.update_document(
        document_id=reissued.id,
        changes={"supersedes_document_id": corrected.id},
        actor_user_id=admin_user.id,
        reason="Reissue the correction",
    )

    with pytest.raises(ResearchValidationError) as exc_info:
        DocumentLibraryService.update_document(
            document_id=original.id,
            changes={"supersedes_document_id": reissued.id},
            actor_user_id=admin_user.id,
            reason="Would close a longer cycle",
        )

    assert "supersedes_document_id" in exc_info.value.details
    assert db.session.get(Document, original.id).supersedes_document_id is None
    assert _audit_count(original.id) == 0


def test_create_preserves_valid_acyclic_supersession_chain(
    app, admin_user, company
):
    original = _create_document(
        admin_user,
        document=_document_fields(title="Original chain report"),
        company_links=[{"company_id": company.id, "is_primary": True}],
    )
    corrected = _create_document(
        admin_user,
        document=_document_fields(
            title="Corrected chain report",
            supersedes_document_id=original.id,
        ),
        company_links=[{"company_id": company.id, "is_primary": True}],
    )
    reissued = _create_document(
        admin_user,
        document=_document_fields(
            title="Reissued chain report",
            supersedes_document_id=corrected.id,
        ),
        company_links=[{"company_id": company.id, "is_primary": True}],
    )

    assert corrected.supersedes_document_id == original.id
    assert reissued.supersedes_document_id == corrected.id


def test_update_requires_reason_for_actual_changes(
    app, admin_user, company
):
    document = _create_document(
        admin_user,
        company_links=[{"company_id": company.id, "is_primary": True}],
    )
    original_fingerprint = document.metadata_fingerprint
    original_rights = _rights_snapshot(document)

    with pytest.raises(ResearchValidationError) as exc_info:
        DocumentLibraryService.update_document(
            document_id=document.id,
            changes={"source_access": SourceAccess.RESTRICTED},
            actor_user_id=admin_user.id,
            reason=None,
        )

    assert "reason" in exc_info.value.details
    persisted = db.session.get(Document, document.id)
    assert persisted.source_access == SourceAccess.PUBLIC
    assert persisted.metadata_fingerprint == original_fingerprint
    assert _rights_snapshot(persisted) == original_rights
    assert _audit_count(document.id) == 0


def test_canonical_duplicate_fingerprint_conflict_rolls_back(
    app, admin_user, company
):
    first = _create_document(
        admin_user,
        document=_document_fields(title="Duplicate Target Title"),
        company_links=[{"company_id": company.id, "is_primary": True}],
    )
    target = _create_document(
        admin_user,
        document=_document_fields(title="Different Title"),
        company_links=[{"company_id": company.id, "is_primary": True}],
    )
    original_fingerprint = target.metadata_fingerprint

    with pytest.raises(ResearchConflictError) as exc_info:
        DocumentLibraryService.update_document(
            document_id=target.id,
            changes={"title": first.title},
            actor_user_id=admin_user.id,
            reason="Attempt duplicate canonical metadata",
        )

    assert exc_info.value.code == "document_duplicate"
    assert exc_info.value.message == "Document requires duplicate review"
    persisted = db.session.get(Document, target.id)
    assert persisted.title == "Different Title"
    assert persisted.metadata_fingerprint == original_fingerprint
    assert _audit_count(target.id) == 0


def test_injected_failure_rolls_back_current_state_and_audit_rows(
    app, admin_user, company, monkeypatch
):
    document = _create_document(
        admin_user,
        company_links=[{"company_id": company.id, "is_primary": True}],
    )
    original_fingerprint = document.metadata_fingerprint
    original_rights = _rights_snapshot(document)

    def fail_commit():
        raise RuntimeError("injected before commit")

    monkeypatch.setattr(db.session, "commit", fail_commit)

    with pytest.raises(RuntimeError, match="injected before commit"):
        DocumentLibraryService.update_document(
            document_id=document.id,
            changes={"source_access": SourceAccess.RESTRICTED},
            actor_user_id=admin_user.id,
            reason="Injected failure",
        )

    persisted = db.session.get(Document, document.id)
    assert persisted.source_access == SourceAccess.PUBLIC
    assert persisted.metadata_fingerprint == original_fingerprint
    assert _rights_snapshot(persisted) == original_rights
    assert _audit_count(document.id) == 0


def test_audit_rows_accumulate_without_mutating_prior_rows(
    app, admin_user, company
):
    document = _create_document(
        admin_user,
        company_links=[{"company_id": company.id, "is_primary": True}],
    )

    DocumentLibraryService.update_document(
        document_id=document.id,
        changes={"source_access": SourceAccess.RESTRICTED},
        actor_user_id=admin_user.id,
        reason="First source correction",
    )
    DocumentLibraryService.update_document(
        document_id=document.id,
        changes={"source_access": SourceAccess.UNKNOWN},
        actor_user_id=admin_user.id,
        reason="Second source correction",
    )

    rows = _audit_rows(document.id)
    assert len(rows) == 2
    assert [row.old_value for row in rows] == [
        SourceAccess.PUBLIC,
        SourceAccess.RESTRICTED,
    ]
    assert [row.new_value for row in rows] == [
        SourceAccess.RESTRICTED,
        SourceAccess.UNKNOWN,
    ]
    assert len({row.id for row in rows}) == 2
    assert all(
        row.event_type == DocumentAuditEventType.SOURCE_ACCESS_CHANGED
        for row in rows
    )


def test_storage_transition_with_already_set_hash_runs_duplicate_protection(
    app, admin_user, company
):
    original = _create_document(
        admin_user,
        document=_stored_document_fields(
            provided_by_user_id=admin_user.id,
            title="Stored original",
        ),
        company_links=[{"company_id": company.id, "is_primary": True}],
    )
    fingerprint = DocumentDeduplicationService.metadata_fingerprint(
        document_type=DocumentType.ANNUAL_REPORT,
        company_ids=[],
        document_date=None,
        title="Awaiting replacement",
        publisher_name=None,
        institution_id=None,
        reporting_period=None,
        report_type=None,
    )
    awaiting = Document(
        document_type=DocumentType.ANNUAL_REPORT,
        title="Awaiting replacement",
        document_date=None,
        discovery_source_type=DiscoverySourceType.OFFICIAL_SITE,
        source_access=SourceAccess.RESTRICTED,
        acquisition_method=AcquisitionMethod.MANUAL_REFERENCE,
        distribution_status=DistributionStatus.UNKNOWN,
        ingestion_status=IngestionStatus.DISCOVERED,
        content_hash_sha256=STORED_SHA256,
        metadata_fingerprint=fingerprint,
        created_by_user_id=admin_user.id,
    )
    db.session.add(awaiting)
    db.session.commit()

    with pytest.raises(ResearchConflictError) as exc_info:
        DocumentLibraryService.update_document(
            document_id=awaiting.id,
            changes={
                "ingestion_status": IngestionStatus.STORED,
                "acquisition_method": AcquisitionMethod.USER_UPLOAD,
                "source_access": SourceAccess.RESTRICTED,
                "distribution_status": DistributionStatus.PRIVATE_LIBRARY,
                "original_source_url": None,
                "provided_by_user_id": admin_user.id,
                "storage_provider": "object-store",
                "storage_key": "opaque/object/key/replacement",
                "content_hash_sha256": STORED_SHA256,
                "mime_type": "application/pdf",
                "file_size_bytes": 1024,
            },
            actor_user_id=admin_user.id,
            reason="Attach the uploaded binary",
        )

    assert exc_info.value.code == "document_duplicate"
    persisted = db.session.get(Document, awaiting.id)
    assert persisted.ingestion_status == IngestionStatus.DISCOVERED
    assert persisted.content_hash_sha256 == STORED_SHA256
    assert _audit_count(awaiting.id) == 0


def test_intermediate_same_fingerprint_lineage_remains_editable(
    app, admin_user, company
):
    original = _create_document(
        admin_user,
        company_links=[{"company_id": company.id, "is_primary": True}],
    )
    corrected = _create_document(
        admin_user,
        document=_document_fields(supersedes_document_id=original.id),
        company_links=[{"company_id": company.id, "is_primary": True}],
    )
    reissued = _create_document(
        admin_user,
        document=_document_fields(supersedes_document_id=corrected.id),
        company_links=[{"company_id": company.id, "is_primary": True}],
    )
    original_fingerprint = original.metadata_fingerprint
    corrected_fingerprint = corrected.metadata_fingerprint

    updated = DocumentLibraryService.update_document(
        document_id=corrected.id,
        changes={"title": "Corrected FY26 Annual Report"},
        actor_user_id=admin_user.id,
        reason="Correct the intermediate title",
    )

    persisted_corrected = db.session.get(Document, corrected.id)
    persisted_reissued = db.session.get(Document, reissued.id)
    assert updated.title == "Corrected FY26 Annual Report"
    assert persisted_corrected.metadata_fingerprint != corrected_fingerprint
    assert persisted_corrected.is_fingerprint_duplicate is False
    assert persisted_reissued.supersedes_document_id == corrected.id
    assert persisted_reissued.metadata_fingerprint == original_fingerprint
    assert persisted_reissued.is_fingerprint_duplicate is True

    with pytest.raises(ResearchConflictError) as exc_info:
        _create_document(
            admin_user,
            document=_document_fields(),
            company_links=[
                {"company_id": company.id, "is_primary": True}
            ],
        )

    assert exc_info.value.code == "document_duplicate"


def test_intermediate_lineage_storage_attachment_is_not_an_ordinary_duplicate(
    app, admin_user, company
):
    original = _create_document(
        admin_user,
        company_links=[{"company_id": company.id, "is_primary": True}],
    )
    corrected = _create_document(
        admin_user,
        document=_document_fields(supersedes_document_id=original.id),
        company_links=[{"company_id": company.id, "is_primary": True}],
    )
    reissued = _create_document(
        admin_user,
        document=_document_fields(supersedes_document_id=corrected.id),
        company_links=[{"company_id": company.id, "is_primary": True}],
    )
    lineage_fingerprint = original.metadata_fingerprint
    replacement_hash = "b" * 64

    updated = DocumentLibraryService.update_document(
        document_id=corrected.id,
        changes={
            "ingestion_status": IngestionStatus.STORED,
            "acquisition_method": AcquisitionMethod.USER_UPLOAD,
            "source_access": SourceAccess.RESTRICTED,
            "distribution_status": DistributionStatus.PRIVATE_LIBRARY,
            "original_source_url": None,
            "provided_by_user_id": admin_user.id,
            "storage_provider": "object-store",
            "storage_key": "opaque/object/key/corrected",
            "content_hash_sha256": replacement_hash,
            "mime_type": "application/pdf",
            "file_size_bytes": 2048,
        },
        actor_user_id=admin_user.id,
        reason="Attach corrected binary",
    )

    persisted_corrected = db.session.get(Document, corrected.id)
    persisted_reissued = db.session.get(Document, reissued.id)
    assert updated.content_hash_sha256 == replacement_hash
    assert persisted_corrected.ingestion_status == IngestionStatus.STORED
    assert persisted_corrected.metadata_fingerprint == lineage_fingerprint
    assert persisted_corrected.is_fingerprint_duplicate is True
    assert persisted_reissued.supersedes_document_id == corrected.id
    assert persisted_reissued.metadata_fingerprint == lineage_fingerprint
    assert persisted_reissued.is_fingerprint_duplicate is True

    unrelated_stored_fields = _stored_document_fields()
    unrelated_stored_fields["content_hash_sha256"] = replacement_hash

    with pytest.raises(ResearchConflictError) as exc_info:
        _create_document(
            admin_user,
            document=unrelated_stored_fields,
            company_links=[
                {"company_id": company.id, "is_primary": True}
            ],
        )

    assert exc_info.value.code == "document_duplicate"
