"""Plan 4 Task 5: atomic initial document aggregate creation.

These tests lock the service-level creation contract: normalized and unique
institutions, correction-only institution updates, one-transaction Document
creation with complete initial company links and conditional institutional
metadata, deterministic fingerprints from the complete sorted link set,
duplicate review conflicts, conservative default rights, and complete
rollback when any aggregate stage fails. Initial creation deliberately does
not write a ``DocumentAuditEvent`` because the approved audit model targets
meaningful later state changes.
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
    DocumentCompanyLink,
    Institution,
    InstitutionalReportMetadata,
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
    ResearchNotFoundError,
    ResearchValidationError,
)


VALID_ISIN = "INE0LOJ01019"
SECOND_ISIN = "INE000A01001"
STORED_SHA256 = "a" * 64


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
def institution():
    row = Institution(
        name="Motilal Oswal Securities",
        normalized_name="motilal oswal securities",
        website="https://motilaloswal.example/research",
    )
    db.session.add(row)
    db.session.commit()
    return row


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


def _institutional_document_fields(**overrides: object) -> dict[str, object]:
    fields = _document_fields(
        document_type=DocumentType.INSTITUTIONAL_RESEARCH,
        title="Example institutional report",
        document_date="2026-08-31",
        reporting_period="FY27E",
        publisher_name="Ignored publisher",
        original_source_url="https://institution.example/research/report",
    )
    fields.update(overrides)
    return fields


def _payload(
    document: dict[str, object] | None = None,
    *,
    company_links: list[dict[str, object]] | None = None,
    institutional_report: dict[str, object] | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "document": document or _document_fields(),
        "company_links": company_links or [],
    }
    if institutional_report is not None:
        payload["institutional_report"] = institutional_report
    return payload


def _stored_document_fields() -> dict[str, object]:
    return _document_fields(
        source_access=SourceAccess.RESTRICTED,
        acquisition_method=AcquisitionMethod.USER_UPLOAD,
        distribution_status=DistributionStatus.PRIVATE_LIBRARY,
        ingestion_status=IngestionStatus.STORED,
        original_source_url=None,
        provided_by_user_id="provider-user",
        storage_provider="object-store",
        storage_key="opaque/object/key",
        content_hash_sha256=STORED_SHA256,
        mime_type="application/pdf",
        file_size_bytes=1024,
    )


def _aggregate_counts() -> dict[str, int]:
    return {
        "documents": db.session.scalar(
            sa.select(sa.func.count()).select_from(Document)
        )
        or 0,
        "links": db.session.scalar(
            sa.select(sa.func.count()).select_from(DocumentCompanyLink)
        )
        or 0,
        "metadata": db.session.scalar(
            sa.select(sa.func.count()).select_from(
                InstitutionalReportMetadata
            )
        )
        or 0,
        "audit": db.session.scalar(
            sa.select(sa.func.count()).select_from(DocumentAuditEvent)
        )
        or 0,
    }


def _assert_empty_aggregate() -> None:
    assert _aggregate_counts() == {
        "documents": 0,
        "links": 0,
        "metadata": 0,
        "audit": 0,
    }


# ---------------------------------------------------------------------------
# Institution commands
# ---------------------------------------------------------------------------


def test_create_institution_normalizes_name_and_website(
    app, admin_user
):
    institution = DocumentLibraryService.create_institution(
        {
            "name": "  Motilal   Oswal Securities  ",
            "website": "HTTPS://Example.IN/Research",
        },
        actor_user_id=admin_user.id,
    )

    persisted = db.session.get(Institution, institution.id)
    assert persisted.name == "Motilal   Oswal Securities"
    assert persisted.normalized_name == "motilal oswal securities"
    assert persisted.website == "https://example.in/Research"

    with pytest.raises(ResearchConflictError) as exc_info:
        DocumentLibraryService.create_institution(
            {"name": "MOTILAL OSWAL SECURITIES"},
            actor_user_id=admin_user.id,
        )

    assert exc_info.value.code == "institution_name_conflict"
    assert db.session.scalar(
        sa.select(sa.func.count()).select_from(Institution)
    ) == 1


def test_update_institution_is_correction_only_and_unique(
    app, admin_user
):
    first = DocumentLibraryService.create_institution(
        {"name": "First Research"},
        actor_user_id=admin_user.id,
    )
    second = DocumentLibraryService.create_institution(
        {"name": "Second Research"},
        actor_user_id=admin_user.id,
    )

    updated = DocumentLibraryService.update_institution(
        first.id,
        {"website": "HTTPS://First.Example/"},
        actor_user_id=admin_user.id,
    )

    assert updated.name == "First Research"
    assert updated.normalized_name == "first research"
    assert updated.website == "https://first.example/"

    with pytest.raises(ResearchConflictError) as exc_info:
        DocumentLibraryService.update_institution(
            second.id,
            {"name": "FIRST RESEARCH"},
            actor_user_id=admin_user.id,
        )
    assert exc_info.value.code == "institution_name_conflict"

    with pytest.raises(ResearchValidationError):
        DocumentLibraryService.update_institution(
            first.id,
            {"normalized_name": "tampered"},
            actor_user_id=admin_user.id,
        )

    persisted = db.session.get(Institution, second.id)
    assert persisted.name == "Second Research"
    assert persisted.normalized_name == "second research"


# ---------------------------------------------------------------------------
# Document aggregate creation
# ---------------------------------------------------------------------------


def test_single_company_document_is_created_atomically_with_fingerprint(
    app, admin_user, company
):
    document = DocumentLibraryService.create_document(
        _payload(
            company_links=[
                {"company_id": company.id, "is_primary": True}
            ]
        ),
        actor_user_id=admin_user.id,
    )

    persisted = db.session.get(Document, document.id)
    expected_fingerprint = DocumentDeduplicationService.metadata_fingerprint(
        document_type=DocumentType.ANNUAL_REPORT,
        company_ids=[company.id],
        document_date=date(2026, 6, 30),
        title="FY26 Annual Report",
        publisher_name="IKIO Lighting Limited",
        institution_id=None,
        reporting_period="FY26",
        report_type=None,
    )

    assert persisted.metadata_fingerprint == expected_fingerprint
    assert persisted.created_by_user_id == admin_user.id
    assert persisted.document_type == DocumentType.ANNUAL_REPORT
    assert persisted.distribution_status == DistributionStatus.LINK_ONLY

    links = db.session.scalars(
        sa.select(DocumentCompanyLink).where(
            DocumentCompanyLink.document_id == document.id
        )
    ).all()
    assert [(link.company_id, link.is_primary) for link in links] == [
        (company.id, True)
    ]
    assert db.session.scalar(
        sa.select(sa.func.count())
        .select_from(InstitutionalReportMetadata)
        .where(InstitutionalReportMetadata.document_id == document.id)
    ) == 0
    assert _aggregate_counts()["audit"] == 0


def test_zero_primary_multi_company_industry_document_uses_sorted_links(
    app, admin_user, company, second_company
):
    document = DocumentLibraryService.create_document(
        _payload(
            document=_document_fields(
                document_type=DocumentType.INDUSTRY_REPORT,
                title="LED Industry Review",
                publisher_name=None,
            ),
            company_links=[
                {"company_id": second_company.id, "is_primary": False},
                {"company_id": company.id, "is_primary": False},
            ],
        ),
        actor_user_id=admin_user.id,
    )

    expected_fingerprint = DocumentDeduplicationService.metadata_fingerprint(
        document_type=DocumentType.INDUSTRY_REPORT,
        company_ids=[second_company.id, company.id],
        document_date=date(2026, 6, 30),
        title="LED Industry Review",
        publisher_name=None,
        institution_id=None,
        reporting_period="FY26",
        report_type=None,
    )
    assert document.metadata_fingerprint == expected_fingerprint

    links = db.session.scalars(
        sa.select(DocumentCompanyLink)
        .where(DocumentCompanyLink.document_id == document.id)
        .order_by(DocumentCompanyLink.company_id)
    ).all()
    assert [link.company_id for link in links] == sorted(
        [company.id, second_company.id]
    )
    assert all(link.is_primary is False for link in links)
    assert _aggregate_counts()["audit"] == 0


def test_institutional_document_persists_required_metadata_and_link(
    app, admin_user, company, institution
):
    document = DocumentLibraryService.create_document(
        _payload(
            document=_institutional_document_fields(),
            company_links=[
                {"company_id": company.id, "is_primary": True}
            ],
            institutional_report={
                "institution_id": institution.id,
                "analyst_name": "A. Analyst",
                "report_type": "INITIATING_COVERAGE",
            },
        ),
        actor_user_id=admin_user.id,
    )

    persisted = db.session.get(Document, document.id)
    metadata = db.session.get(InstitutionalReportMetadata, document.id)

    expected_fingerprint = DocumentDeduplicationService.metadata_fingerprint(
        document_type=DocumentType.INSTITUTIONAL_RESEARCH,
        company_ids=[company.id],
        document_date=date(2026, 8, 31),
        title="Example institutional report",
        publisher_name="Ignored publisher",
        institution_id=institution.id,
        reporting_period="FY27E",
        report_type="INITIATING_COVERAGE",
    )

    assert persisted.metadata_fingerprint == expected_fingerprint
    assert metadata.institution_id == institution.id
    assert metadata.analyst_name == "A. Analyst"
    assert metadata.report_type == "INITIATING_COVERAGE"
    assert db.session.scalar(
        sa.select(sa.func.count())
        .select_from(DocumentCompanyLink)
        .where(DocumentCompanyLink.document_id == document.id)
    ) == 1
    assert _aggregate_counts()["audit"] == 0


def test_institutional_document_requires_existing_institution_and_rolls_back(
    app, admin_user, company
):
    with pytest.raises(ResearchNotFoundError) as exc_info:
        DocumentLibraryService.create_document(
            _payload(
                document=_institutional_document_fields(),
                company_links=[
                    {"company_id": company.id, "is_primary": True}
                ],
                institutional_report={
                    "institution_id": "missing-institution",
                    "analyst_name": None,
                    "report_type": "INITIATING_COVERAGE",
                },
            ),
            actor_user_id=admin_user.id,
        )

    assert exc_info.value.code == "institution_not_found"
    _assert_empty_aggregate()


def test_invalid_company_link_rolls_back_every_aggregate_row(
    app, admin_user, institution
):
    with pytest.raises(ResearchNotFoundError) as exc_info:
        DocumentLibraryService.create_document(
            _payload(
                document=_institutional_document_fields(),
                company_links=[
                    {
                        "company_id": "missing-company",
                        "is_primary": True,
                    }
                ],
                institutional_report={
                    "institution_id": institution.id,
                    "analyst_name": None,
                    "report_type": "INITIATING_COVERAGE",
                },
            ),
            actor_user_id=admin_user.id,
        )

    assert exc_info.value.code == "company_not_found"
    _assert_empty_aggregate()


def test_metadata_duplicate_conflicts_before_insertion(
    app, admin_user, company
):
    payload = _payload(
        company_links=[{"company_id": company.id, "is_primary": True}]
    )
    DocumentLibraryService.create_document(
        payload,
        actor_user_id=admin_user.id,
    )

    with pytest.raises(ResearchConflictError) as exc_info:
        DocumentLibraryService.create_document(
            payload,
            actor_user_id=admin_user.id,
        )

    assert exc_info.value.code == "document_duplicate"
    assert exc_info.value.message == "Document requires duplicate review"
    assert _aggregate_counts() == {
        "documents": 1,
        "links": 1,
        "metadata": 0,
        "audit": 0,
    }


def test_exact_binary_duplicate_conflicts_before_insertion(
    app, admin_user, company
):
    payload = _payload(
        document=_stored_document_fields(),
        company_links=[{"company_id": company.id, "is_primary": True}],
    )
    DocumentLibraryService.create_document(
        payload,
        actor_user_id=admin_user.id,
    )

    with pytest.raises(ResearchConflictError) as exc_info:
        DocumentLibraryService.create_document(
            payload,
            actor_user_id=admin_user.id,
        )

    assert exc_info.value.code == "document_duplicate"
    assert _aggregate_counts() == {
        "documents": 1,
        "links": 1,
        "metadata": 0,
        "audit": 0,
    }


def test_public_defaults_to_link_only_until_rights_are_verified(
    app, admin_user, company
):
    document_fields = _document_fields()
    document_fields.pop("distribution_status")

    document = DocumentLibraryService.create_document(
        _payload(
            document=document_fields,
            company_links=[
                {"company_id": company.id, "is_primary": True}
            ],
        ),
        actor_user_id=admin_user.id,
    )

    assert document.distribution_status == DistributionStatus.LINK_ONLY


def test_user_upload_defaults_to_private_library(
    app, admin_user, company
):
    document_fields = _stored_document_fields()
    document_fields["provided_by_user_id"] = admin_user.id

    document = DocumentLibraryService.create_document(
        _payload(
            document=document_fields,
            company_links=[
                {"company_id": company.id, "is_primary": True}
            ],
        ),
        actor_user_id=admin_user.id,
    )

    assert document.distribution_status == DistributionStatus.PRIVATE_LIBRARY
    assert document.provided_by_user_id == admin_user.id


def test_injected_failure_after_document_insert_rolls_back(
    app, admin_user, company, monkeypatch
):
    original_flush = db.session.flush
    calls = 0

    def fail_after_document():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("injected after document insert")
        return original_flush()

    monkeypatch.setattr(db.session, "flush", fail_after_document)

    with pytest.raises(RuntimeError, match="after document insert"):
        DocumentLibraryService.create_document(
            _payload(
                company_links=[
                    {"company_id": company.id, "is_primary": True}
                ]
            ),
            actor_user_id=admin_user.id,
        )

    _assert_empty_aggregate()


def test_injected_failure_after_company_links_rolls_back(
    app, admin_user, company, monkeypatch
):
    original_flush = db.session.flush
    calls = 0

    def fail_after_links():
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("injected after company links")
        return original_flush()

    monkeypatch.setattr(db.session, "flush", fail_after_links)

    with pytest.raises(RuntimeError, match="after company links"):
        DocumentLibraryService.create_document(
            _payload(
                company_links=[
                    {"company_id": company.id, "is_primary": True}
                ]
            ),
            actor_user_id=admin_user.id,
        )

    _assert_empty_aggregate()


def test_injected_failure_after_institutional_metadata_rolls_back(
    app, admin_user, company, institution, monkeypatch
):
    original_flush = db.session.flush
    calls = 0

    def fail_after_metadata():
        nonlocal calls
        calls += 1
        if calls == 3:
            raise RuntimeError("injected after institutional metadata")
        return original_flush()

    monkeypatch.setattr(db.session, "flush", fail_after_metadata)

    with pytest.raises(
        RuntimeError, match="after institutional metadata"
    ):
        DocumentLibraryService.create_document(
            _payload(
                document=_institutional_document_fields(),
                company_links=[
                    {"company_id": company.id, "is_primary": True}
                ],
                institutional_report={
                    "institution_id": institution.id,
                    "analyst_name": None,
                    "report_type": "INITIATING_COVERAGE",
                },
            ),
            actor_user_id=admin_user.id,
        )

    _assert_empty_aggregate()


def test_unique_fingerprint_race_is_translated_to_duplicate_review(
    app, admin_user, company, monkeypatch
):
    payload = _payload(
        company_links=[{"company_id": company.id, "is_primary": True}]
    )
    DocumentLibraryService.create_document(
        payload,
        actor_user_id=admin_user.id,
    )

    def _none_duplicate(**_kwargs: object) -> DuplicateDecision:
        return DuplicateDecision(kind="NONE", matched_document_id=None)

    monkeypatch.setattr(
        DocumentDeduplicationService,
        "find_duplicate",
        staticmethod(_none_duplicate),
    )

    with pytest.raises(ResearchConflictError) as exc_info:
        DocumentLibraryService.create_document(
            payload,
            actor_user_id=admin_user.id,
        )

    assert exc_info.value.code == "document_duplicate"
    assert exc_info.value.message == "Document requires duplicate review"
    assert _aggregate_counts() == {
        "documents": 1,
        "links": 1,
        "metadata": 0,
        "audit": 0,
    }
