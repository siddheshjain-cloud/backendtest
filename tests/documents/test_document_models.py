"""Plan 4 Task 1: document, institution, link, metadata, and audit models.

These tests lock the frozen Milestone 1 Common Document Library persistence
contract: every first-class document type, the four independent access,
acquisition, distribution, and ingestion state dimensions, opaque storage
fields, SHA-256/fingerprint lengths, provider and rights-verifier foreign
keys, normalized institution identity, one-to-one institutional metadata,
the composite document/company link with zero-or-one primary designation,
append-only audit rows, and the disclosure-to-document foreign key.

The suite only asserts what this task authorizes. It does not test the
validation, deduplication, rights, or command services that later tasks own.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import date, datetime, timezone

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from app import db
from app.models import (
    Company,
    CompanyDisclosure,
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
    DocumentAuditEventType,
    DocumentType,
    IngestionStatus,
    OriginalPublicationPrecision,
    SourceAccess,
)
from app.models.ticker import Ticker


DOCUMENT_TYPES = (
    DocumentType.ANNUAL_REPORT,
    DocumentType.QUARTERLY_RESULTS,
    DocumentType.INVESTOR_PRESENTATION,
    DocumentType.CONCALL,
    DocumentType.SCREENER,
    DocumentType.REG30_ATTACHMENT,
    DocumentType.CREDIT_RATING_REPORT,
    DocumentType.INDUSTRY_REPORT,
    DocumentType.INSTITUTIONAL_RESEARCH,
    DocumentType.OTHER,
)

SOURCE_ACCESS_VALUES = (
    SourceAccess.PUBLIC,
    SourceAccess.RESTRICTED,
    SourceAccess.UNKNOWN,
)

ACQUISITION_METHOD_VALUES = (
    AcquisitionMethod.PUBLIC_DOWNLOAD,
    AcquisitionMethod.USER_UPLOAD,
    AcquisitionMethod.MANUAL_REFERENCE,
    AcquisitionMethod.NOT_ACQUIRED,
)

DISTRIBUTION_STATUS_VALUES = (
    DistributionStatus.UNKNOWN,
    DistributionStatus.LINK_ONLY,
    DistributionStatus.PRIVATE_LIBRARY,
    DistributionStatus.APP_DISTRIBUTABLE,
)

INGESTION_STATUS_VALUES = (
    IngestionStatus.DISCOVERED,
    IngestionStatus.AWAITING_UPLOAD,
    IngestionStatus.STORED,
    IngestionStatus.ANALYSED,
)


def _fingerprint(seed: str) -> str:
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()


def _reflected_columns(table_name: str) -> dict[str, bool]:
    inspector = sa.inspect(db.engine)
    return {
        column["name"]: column["nullable"]
        for column in inspector.get_columns(table_name)
    }


def _column_type(table_name: str, column_name: str) -> object:
    inspector = sa.inspect(db.engine)
    return next(
        column["type"]
        for column in inspector.get_columns(table_name)
        if column["name"] == column_name
    )


def _commit_expect_integrity() -> None:
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()


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
        instrument_token=1,
    )


@pytest.fixture
def second_company(ticker_factory):
    return _make_company(
        ticker_factory,
        symbol="ACME",
        isin="INE000A01001",
        instrument_token=2,
    )


def _document(**overrides: object) -> Document:
    values: dict[str, object] = {
        "document_type": DocumentType.ANNUAL_REPORT,
        "title": "FY26 Annual Report",
        "document_date": date(2026, 6, 30),
        "reporting_period": "FY26",
        "publisher_name": "IKIO Lighting Limited",
        "discovery_source_type": DiscoverySourceType.EXCHANGE,
        "source_access": SourceAccess.PUBLIC,
        "acquisition_method": AcquisitionMethod.MANUAL_REFERENCE,
        "distribution_status": DistributionStatus.LINK_ONLY,
        "ingestion_status": IngestionStatus.DISCOVERED,
        "metadata_fingerprint": _fingerprint(str(uuid.uuid4())),
    }
    values.update(overrides)
    return Document(**values)


def test_document_models_are_exported_with_frozen_table_names():
    assert Document.__tablename__ == "document"
    assert DocumentCompanyLink.__tablename__ == "document_company_link"
    assert DocumentAuditEvent.__tablename__ == "document_audit_event"
    assert Institution.__tablename__ == "institution"
    assert (
        InstitutionalReportMetadata.__tablename__
        == "institutional_report_metadata"
    )


def test_document_tables_have_exact_columns_and_nullability(app):
    assert _reflected_columns("document") == {
        "id": False,
        "created_at": False,
        "document_type": False,
        "title": False,
        "document_date": True,
        "supersedes_document_id": True,
        "original_published_date": True,
        "original_published_at": True,
        "original_published_at_precision": False,
        "reporting_period": True,
        "publisher_name": True,
        "publisher_reference": True,
        "original_source_url": True,
        "discovery_source_type": False,
        "discovery_source_reference": True,
        "source_access": False,
        "acquisition_method": False,
        "distribution_status": False,
        "ingestion_status": False,
        "storage_provider": True,
        "storage_key": True,
        "content_hash_sha256": True,
        "metadata_fingerprint": False,
        "mime_type": True,
        "file_size_bytes": True,
        "provided_by_user_id": True,
        "distribution_basis": True,
        "rights_verified_by_user_id": True,
        "rights_verified_at": True,
        "created_by_user_id": False,
        "updated_at": False,
        "archived_at": True,
    }
    assert _reflected_columns("document_company_link") == {
        "document_id": False,
        "company_id": False,
        "is_primary": False,
        "created_at": False,
    }
    assert _reflected_columns("institution") == {
        "id": False,
        "created_at": False,
        "name": False,
        "normalized_name": False,
        "website": True,
        "updated_at": False,
    }
    assert _reflected_columns("institutional_report_metadata") == {
        "document_id": False,
        "institution_id": False,
        "analyst_name": True,
        "report_type": False,
        "created_at": False,
        "updated_at": False,
    }
    assert _reflected_columns("document_audit_event") == {
        "id": False,
        "created_at": False,
        "document_id": False,
        "event_type": False,
        "field_changed": True,
        "old_value": True,
        "new_value": True,
        "actor_user_id": False,
        "reason": True,
    }


@pytest.mark.parametrize("document_type", DOCUMENT_TYPES)
def test_document_accepts_every_first_class_type(
    app, admin_user, company, document_type
):
    document = _document(
        document_type=document_type,
        title=f"{document_type} artifact",
        created_by_user_id=admin_user.id,
    )
    db.session.add(document)
    db.session.commit()

    persisted = db.session.get(Document, document.id)
    assert persisted.document_type == document_type


@pytest.mark.parametrize("value", SOURCE_ACCESS_VALUES)
@pytest.mark.parametrize("method", ACQUISITION_METHOD_VALUES)
@pytest.mark.parametrize("distribution", DISTRIBUTION_STATUS_VALUES)
@pytest.mark.parametrize("ingestion", INGESTION_STATUS_VALUES)
def test_document_state_dimensions_accept_independent_closed_values(
    app, admin_user, company, value, method, distribution, ingestion
):
    document = _document(
        source_access=value,
        acquisition_method=method,
        distribution_status=distribution,
        ingestion_status=ingestion,
        created_by_user_id=admin_user.id,
    )
    db.session.add(document)
    db.session.commit()

    persisted = db.session.get(Document, document.id)
    assert persisted.source_access == value
    assert persisted.acquisition_method == method
    assert persisted.distribution_status == distribution
    assert persisted.ingestion_status == ingestion


def _enum_values(model: type, column_name: str) -> tuple[set[str], bool]:
    column_type = model.__table__.columns[column_name].type
    assert isinstance(column_type, sa.Enum)
    return set(column_type.enums), bool(column_type.native_enum)


def test_state_columns_are_portable_string_backed_closed_enums(app):
    assert _enum_values(Document, "document_type") == (
        set(DOCUMENT_TYPES),
        False,
    )
    assert _enum_values(Document, "discovery_source_type") == (
        {
            DiscoverySourceType.OFFICIAL_SITE,
            DiscoverySourceType.EXCHANGE,
            DiscoverySourceType.TELEGRAM,
            DiscoverySourceType.EMAIL,
            DiscoverySourceType.SEARCH,
            DiscoverySourceType.USER,
            DiscoverySourceType.OTHER,
        },
        False,
    )
    assert _enum_values(Document, "source_access") == (
        set(SOURCE_ACCESS_VALUES),
        False,
    )
    assert _enum_values(Document, "acquisition_method") == (
        set(ACQUISITION_METHOD_VALUES),
        False,
    )
    assert _enum_values(Document, "distribution_status") == (
        set(DISTRIBUTION_STATUS_VALUES),
        False,
    )
    assert _enum_values(Document, "ingestion_status") == (
        set(INGESTION_STATUS_VALUES),
        False,
    )
    assert _enum_values(DocumentAuditEvent, "event_type") == (
        {
            DocumentAuditEventType.SOURCE_ACCESS_CHANGED,
            DocumentAuditEventType.ACQUISITION_METHOD_CHANGED,
            DocumentAuditEventType.DISTRIBUTION_STATUS_CHANGED,
            DocumentAuditEventType.INGESTION_STATUS_CHANGED,
            DocumentAuditEventType.RIGHTS_VERIFIED,
            DocumentAuditEventType.RIGHTS_VERIFICATION_REVOKED,
            DocumentAuditEventType.STORAGE_ATTACHED,
            DocumentAuditEventType.COMPANY_LINKS_CHANGED,
            DocumentAuditEventType.ARCHIVED,
            DocumentAuditEventType.RESTORED,
        },
        False,
    )


def test_document_rejects_a_value_outside_the_closed_enum(
    app, admin_user, company
):
    document = _document(
        source_access="SECRET",
        created_by_user_id=admin_user.id,
    )
    db.session.add(document)

    with pytest.raises((sa.exc.StatementError, LookupError, IntegrityError)):
        db.session.commit()
    db.session.rollback()


def test_document_storage_fields_are_opaque_and_hash_lengths_are_fixed(
    app, admin_user, company
):
    content_hash = hashlib.sha256(b"stored-bytes").hexdigest()
    fingerprint = _fingerprint("opaque-storage-document")

    document = _document(
        document_type=DocumentType.REG30_ATTACHMENT,
        ingestion_status=IngestionStatus.STORED,
        acquisition_method=AcquisitionMethod.USER_UPLOAD,
        source_access=SourceAccess.RESTRICTED,
        distribution_status=DistributionStatus.PRIVATE_LIBRARY,
        storage_provider="object-store",
        storage_key="private/library/ab/cd/opaque-key",
        content_hash_sha256=content_hash,
        metadata_fingerprint=fingerprint,
        mime_type="application/pdf",
        file_size_bytes=2_147_483_648,
        provided_by_user_id=admin_user.id,
        created_by_user_id=admin_user.id,
    )
    db.session.add(document)
    db.session.commit()

    persisted = db.session.get(Document, document.id)
    assert persisted.storage_provider == "object-store"
    assert persisted.storage_key == "private/library/ab/cd/opaque-key"
    assert persisted.content_hash_sha256 == content_hash
    assert persisted.file_size_bytes == 2_147_483_648
    assert len(persisted.content_hash_sha256) == 64
    assert len(persisted.metadata_fingerprint) == 64

    assert isinstance(_column_type("document", "storage_key"), sa.String)
    assert isinstance(_column_type("document", "storage_provider"), sa.String)
    assert _column_type("document", "content_hash_sha256").length == 64
    assert _column_type("document", "metadata_fingerprint").length == 64
    assert isinstance(_column_type("document", "file_size_bytes"), sa.BigInteger)
    assert not hasattr(Document, "storage_url")
    assert "storage_url" not in Document.__table__.columns


def test_metadata_fingerprint_is_required_and_unique(app, admin_user, company):
    fingerprint = _fingerprint("duplicate-fingerprint")

    first = _document(
        metadata_fingerprint=fingerprint,
        created_by_user_id=admin_user.id,
    )
    db.session.add(first)
    db.session.commit()

    second = _document(
        metadata_fingerprint=fingerprint,
        created_by_user_id=admin_user.id,
    )
    db.session.add(second)
    _commit_expect_integrity()

    missing = _document(
        metadata_fingerprint=None,
        created_by_user_id=admin_user.id,
    )
    db.session.add(missing)
    _commit_expect_integrity()


def test_document_has_provider_and_rights_verifier_foreign_keys(app):
    inspector = sa.inspect(db.engine)
    foreign_keys = {
        frozenset(constraint["constrained_columns"]): constraint[
            "referred_table"
        ]
        for constraint in inspector.get_foreign_keys("document")
    }

    assert foreign_keys[frozenset({"provided_by_user_id"})] == "user"
    assert foreign_keys[frozenset({"rights_verified_by_user_id"})] == "user"
    assert foreign_keys[frozenset({"created_by_user_id"})] == "user"


def test_document_records_provider_and_rights_verification(
    app, admin_user, premium_user, company
):
    verified_at = datetime(2026, 9, 1, 9, 30, tzinfo=timezone.utc)
    document = _document(
        acquisition_method=AcquisitionMethod.USER_UPLOAD,
        source_access=SourceAccess.RESTRICTED,
        distribution_status=DistributionStatus.APP_DISTRIBUTABLE,
        distribution_basis="Signed redistribution licence on file",
        provided_by_user_id=premium_user.id,
        rights_verified_by_user_id=admin_user.id,
        rights_verified_at=verified_at,
        created_by_user_id=admin_user.id,
    )
    db.session.add(document)
    db.session.commit()

    persisted = db.session.get(Document, document.id)
    assert persisted.provided_by_user_id == premium_user.id
    assert persisted.rights_verified_by_user_id == admin_user.id
    assert persisted.rights_verified_at.replace(tzinfo=timezone.utc) == (
        verified_at
    )
    assert persisted.distribution_basis == "Signed redistribution licence on file"


def test_institution_normalized_name_is_unique(app):
    first = Institution(
        name="Motilal Oswal Securities",
        normalized_name="motilal oswal securities",
    )
    db.session.add(first)
    db.session.commit()

    duplicate = Institution(
        name="Motilal Oswal",
        normalized_name="motilal oswal securities",
    )
    db.session.add(duplicate)
    _commit_expect_integrity()

    distinct = Institution(
        name="ICICI Securities",
        normalized_name="icici securities",
        website="https://example.in/icici",
    )
    db.session.add(distinct)
    db.session.commit()
    assert db.session.get(Institution, distinct.id).website == (
        "https://example.in/icici"
    )


def test_institutional_metadata_is_one_to_one_per_document(
    app, admin_user, company
):
    institution = Institution(
        name="Motilal Oswal Securities",
        normalized_name="motilal oswal securities",
    )
    db.session.add(institution)
    db.session.flush()

    document = _document(
        document_type=DocumentType.INSTITUTIONAL_RESEARCH,
        title="IKIO initiating coverage",
        created_by_user_id=admin_user.id,
    )
    db.session.add(document)
    db.session.flush()

    db.session.add(
        InstitutionalReportMetadata(
            document_id=document.id,
            institution_id=institution.id,
            analyst_name="A. Analyst",
            report_type="INITIATING_COVERAGE",
        )
    )
    db.session.commit()

    persisted = db.session.get(InstitutionalReportMetadata, document.id)
    assert persisted.institution_id == institution.id
    assert persisted.report_type == "INITIATING_COVERAGE"
    assert persisted.analyst_name == "A. Analyst"

    # A direct INSERT bypasses the identity map so the primary-key violation
    # is raised by the database rather than by ORM identity reconciliation.
    with pytest.raises(IntegrityError):
        db.session.execute(
            sa.insert(InstitutionalReportMetadata).values(
                document_id=document.id,
                institution_id=institution.id,
                report_type="UPDATE",
            )
        )
        db.session.commit()
    db.session.rollback()

    inspector = sa.inspect(db.engine)
    assert inspector.get_pk_constraint("institutional_report_metadata")[
        "constrained_columns"
    ] == ["document_id"]


def test_institutional_metadata_requires_document_and_institution_fks(app):
    inspector = sa.inspect(db.engine)
    foreign_keys = {
        frozenset(constraint["constrained_columns"]): constraint[
            "referred_table"
        ]
        for constraint in inspector.get_foreign_keys(
            "institutional_report_metadata"
        )
    }

    assert foreign_keys[frozenset({"document_id"})] == "document"
    assert foreign_keys[frozenset({"institution_id"})] == "institution"


def test_multiple_reports_from_one_institution_are_retained(
    app, admin_user, company
):
    institution = Institution(name="ICICI Securities", normalized_name="icici securities")
    db.session.add(institution)
    db.session.flush()

    first = _document(
        document_type=DocumentType.INSTITUTIONAL_RESEARCH,
        title="Q1 FY27 results review",
        created_by_user_id=admin_user.id,
    )
    second = _document(
        document_type=DocumentType.INSTITUTIONAL_RESEARCH,
        title="Q2 FY27 results review",
        created_by_user_id=admin_user.id,
    )
    db.session.add_all([first, second])
    db.session.flush()
    db.session.add_all(
        [
            InstitutionalReportMetadata(
                document_id=first.id,
                institution_id=institution.id,
                report_type="RESULT_UPDATE",
            ),
            InstitutionalReportMetadata(
                document_id=second.id,
                institution_id=institution.id,
                report_type="RESULT_UPDATE",
            ),
        ]
    )
    db.session.commit()

    retained = db.session.scalars(
        sa.select(InstitutionalReportMetadata).where(
            InstitutionalReportMetadata.institution_id == institution.id
        )
    ).all()
    assert {row.document_id for row in retained} == {first.id, second.id}


def test_document_company_link_is_composite_unique(app, admin_user, company):
    document = _document(created_by_user_id=admin_user.id)
    db.session.add(document)
    db.session.flush()

    db.session.add(
        DocumentCompanyLink(
            document_id=document.id,
            company_id=company.id,
            is_primary=True,
        )
    )
    db.session.commit()

    # A direct INSERT bypasses the identity map so the composite-key
    # violation is raised by the database rather than by identity
    # reconciliation.
    with pytest.raises(IntegrityError):
        db.session.execute(
            sa.insert(DocumentCompanyLink).values(
                document_id=document.id,
                company_id=company.id,
                is_primary=False,
            )
        )
        db.session.commit()
    db.session.rollback()

    inspector = sa.inspect(db.engine)
    primary_key = inspector.get_pk_constraint("document_company_link")
    assert set(primary_key["constrained_columns"]) == {
        "document_id",
        "company_id",
    }


def test_document_company_link_allows_zero_or_one_primary(
    app, admin_user, company, second_company
):
    zero_primary = _document(created_by_user_id=admin_user.id)
    db.session.add(zero_primary)
    db.session.flush()
    db.session.add_all(
        [
            DocumentCompanyLink(
                document_id=zero_primary.id,
                company_id=company.id,
                is_primary=False,
            ),
            DocumentCompanyLink(
                document_id=zero_primary.id,
                company_id=second_company.id,
                is_primary=False,
            ),
        ]
    )
    db.session.commit()

    one_primary = _document(created_by_user_id=admin_user.id)
    db.session.add(one_primary)
    db.session.flush()
    db.session.add_all(
        [
            DocumentCompanyLink(
                document_id=one_primary.id,
                company_id=company.id,
                is_primary=True,
            ),
            DocumentCompanyLink(
                document_id=one_primary.id,
                company_id=second_company.id,
                is_primary=False,
            ),
        ]
    )
    db.session.commit()

    two_primary = _document(created_by_user_id=admin_user.id)
    db.session.add(two_primary)
    db.session.flush()
    db.session.add_all(
        [
            DocumentCompanyLink(
                document_id=two_primary.id,
                company_id=company.id,
                is_primary=True,
            ),
            DocumentCompanyLink(
                document_id=two_primary.id,
                company_id=second_company.id,
                is_primary=True,
            ),
        ]
    )
    _commit_expect_integrity()


def test_document_company_link_has_no_surrogate_id_column(app):
    assert "id" not in {column.name for column in DocumentCompanyLink.__table__.columns}
    assert set(DocumentCompanyLink.__table__.primary_key.columns.keys()) == {
        "document_id",
        "company_id",
    }


def test_document_company_link_declares_document_and_company_foreign_keys(app):
    inspector = sa.inspect(db.engine)
    foreign_keys = {
        frozenset(constraint["constrained_columns"]): constraint[
            "referred_table"
        ]
        for constraint in inspector.get_foreign_keys("document_company_link")
    }

    assert foreign_keys[frozenset({"document_id"})] == "document"
    assert foreign_keys[frozenset({"company_id"})] == "company"


def test_document_audit_event_persists_append_only_fields(
    app, admin_user, company
):
    document = _document(created_by_user_id=admin_user.id)
    db.session.add(document)
    db.session.flush()

    event = DocumentAuditEvent(
        document_id=document.id,
        event_type=DocumentAuditEventType.COMPANY_LINKS_CHANGED,
        field_changed="company_links",
        old_value={"company_ids": [company.id], "primary_company_id": company.id},
        new_value={"company_ids": [], "primary_company_id": None},
        actor_user_id=admin_user.id,
        reason="Corrected link set",
    )
    db.session.add(event)
    db.session.commit()

    persisted = db.session.get(DocumentAuditEvent, event.id)
    assert persisted.event_type == DocumentAuditEventType.COMPANY_LINKS_CHANGED
    assert persisted.field_changed == "company_links"
    assert persisted.old_value == {
        "company_ids": [company.id],
        "primary_company_id": company.id,
    }
    assert persisted.new_value == {"company_ids": [], "primary_company_id": None}
    assert persisted.actor_user_id == admin_user.id
    assert persisted.reason == "Corrected link set"
    assert persisted.created_at is not None
    assert "updated_at" not in {
        column.name for column in DocumentAuditEvent.__table__.columns
    }

    inspector = sa.inspect(db.engine)
    old_value_type = next(
        column["type"]
        for column in inspector.get_columns("document_audit_event")
        if column["name"] == "old_value"
    )
    new_value_type = next(
        column["type"]
        for column in inspector.get_columns("document_audit_event")
        if column["name"] == "new_value"
    )
    assert isinstance(old_value_type, sa.JSON)
    assert isinstance(new_value_type, sa.JSON)


def test_document_audit_event_has_document_and_actor_foreign_keys(app):
    inspector = sa.inspect(db.engine)
    foreign_keys = {
        frozenset(constraint["constrained_columns"]): constraint[
            "referred_table"
        ]
        for constraint in inspector.get_foreign_keys("document_audit_event")
    }

    assert foreign_keys[frozenset({"document_id"})] == "document"
    assert foreign_keys[frozenset({"actor_user_id"})] == "user"


def test_document_audit_events_cannot_be_updated_or_deleted(
    app, admin_user, company
):
    document = _document(created_by_user_id=admin_user.id)
    db.session.add(document)
    db.session.flush()
    event = DocumentAuditEvent(
        document_id=document.id,
        event_type=DocumentAuditEventType.ARCHIVED,
        actor_user_id=admin_user.id,
        reason="Duplicate of official filing",
    )
    db.session.add(event)
    db.session.commit()
    event_id = event.id

    db.session.expire_all()
    stored = db.session.get(DocumentAuditEvent, event_id)
    stored.reason = "Tampered reason"
    with pytest.raises(sa.exc.InvalidRequestError):
        db.session.commit()
    db.session.rollback()

    db.session.expire_all()
    stored = db.session.get(DocumentAuditEvent, event_id)
    db.session.delete(stored)
    with pytest.raises(sa.exc.InvalidRequestError):
        db.session.commit()
    db.session.rollback()

    assert db.session.get(DocumentAuditEvent, event_id) is not None


def test_disclosure_document_id_maps_to_document_foreign_key(
    app, admin_user, company
):
    inspector = sa.inspect(db.engine)
    foreign_keys = {
        frozenset(constraint["constrained_columns"]): constraint[
            "referred_table"
        ]
        for constraint in inspector.get_foreign_keys("company_disclosure")
    }
    assert foreign_keys[frozenset({"document_id"})] == "document"

    document = _document(created_by_user_id=admin_user.id)
    db.session.add(document)
    db.session.commit()

    disclosure = CompanyDisclosure(
        company_id=company.id,
        event_type="REG30",
        event_date=date(2026, 9, 4),
        title="Filing attachment",
        original_source_url_or_reference="https://exchange.example/reg30",
        document_id=document.id,
        created_by_user_id=admin_user.id,
    )
    db.session.add(disclosure)
    db.session.commit()

    assert db.session.get(CompanyDisclosure, disclosure.id).document_id == (
        document.id
    )

    # A disclosure without an attached document remains valid.
    unlinked = CompanyDisclosure(
        company_id=company.id,
        event_type="REG30",
        event_date=date(2026, 9, 4),
        title="Disclosure without an attachment",
        original_source_url_or_reference="https://exchange.example/reg30-2",
        document_id=None,
        created_by_user_id=admin_user.id,
    )
    db.session.add(unlinked)
    db.session.commit()
    assert db.session.get(CompanyDisclosure, unlinked.id).document_id is None


def test_legacy_tables_gain_no_document_columns(app):
    expected_legacy_columns = {
        "user": {
            "id",
            "created_at",
            "is_admin",
            "name",
            "email",
            "phone_number",
            "password_hash",
            "google_id",
            "telegram_chat_id",
            "telegram_username",
            "telegram_enabled",
            "telegram_connected_at",
        },
        "ticker": {
            "id",
            "created_at",
            "symbol",
            "exchange",
            "instrument_token",
            "name",
            "last_price",
            "last_updated",
        },
        "trade": {
            "id",
            "created_at",
            "symbol",
            "side",
            "type",
            "status",
            "notes",
            "entry",
            "stoploss",
            "target",
            "timeframe",
            "score",
            "entry_x",
            "stoploss_x",
            "target_x",
            "entry_eta",
            "stoploss_eta",
            "target_eta",
            "entry_at",
            "stoploss_at",
            "target_at",
            "edited_at",
            "status_updated_at",
            "updated_at",
            "user_id",
            "ticker_id",
        },
        "tag": {"id", "created_at", "name", "user_id"},
        "telegram_verification": {
            "id",
            "created_at",
            "user_id",
            "verification_code",
            "expires_at",
            "verified",
        },
        "trade_tags": {"trade_id", "tag_id"},
    }

    for table_name, expected in expected_legacy_columns.items():
        reflected = set(_reflected_columns(table_name))
        assert reflected == expected, (
            f"legacy table '{table_name}' changed columns: "
            f"added={sorted(reflected - expected)} "
            f"removed={sorted(expected - reflected)}"
        )


def test_document_represents_an_exact_original_publication_timestamp(
    app, admin_user, company
):
    published_at = datetime(2026, 8, 6, 18, 42, 17, tzinfo=timezone.utc)
    document = _document(
        created_by_user_id=admin_user.id,
        original_published_at=published_at,
        original_published_at_precision=OriginalPublicationPrecision.DATETIME,
    )
    db.session.add(document)
    db.session.commit()

    persisted = db.session.get(Document, document.id)
    assert (
        persisted.original_published_at_precision
        == OriginalPublicationPrecision.DATETIME
    )
    # SQLite persists the wall-clock fields without an offset.
    assert persisted.original_published_at == published_at.replace(tzinfo=None)
    assert persisted.original_published_date is None


def test_document_records_a_date_only_publication_without_inventing_a_time(
    app, admin_user, company
):
    document = _document(
        created_by_user_id=admin_user.id,
        original_published_date=date(2026, 8, 6),
        original_published_at_precision=OriginalPublicationPrecision.DATE,
    )
    db.session.add(document)
    db.session.commit()

    persisted = db.session.get(Document, document.id)
    assert (
        persisted.original_published_at_precision
        == OriginalPublicationPrecision.DATE
    )
    assert persisted.original_published_date == date(2026, 8, 6)
    # A date-only publication must never fabricate a midnight timestamp.
    assert persisted.original_published_at is None


def test_document_original_publication_defaults_to_unknown(
    app, admin_user, company
):
    document = _document(created_by_user_id=admin_user.id)
    db.session.add(document)
    db.session.commit()

    persisted = db.session.get(Document, document.id)
    assert (
        persisted.original_published_at_precision
        == OriginalPublicationPrecision.UNKNOWN
    )
    assert persisted.original_published_at is None
    assert persisted.original_published_date is None
    # The SPA record still records when it was created, independent of the
    # (here, unknown) original publication time.
    assert persisted.created_at is not None


def test_document_date_is_not_treated_as_original_publication(
    app, admin_user, company
):
    document = _document(
        created_by_user_id=admin_user.id,
        document_date=date(2026, 6, 30),
    )
    db.session.add(document)
    db.session.commit()

    persisted = db.session.get(Document, document.id)
    assert persisted.document_date == date(2026, 6, 30)
    # The document's own date never stands in for publisher release time.
    assert (
        persisted.original_published_at_precision
        == OriginalPublicationPrecision.UNKNOWN
    )
    assert persisted.original_published_at is None
    assert persisted.original_published_date is None


def test_spa_created_at_is_independent_of_original_publication_time(
    app, admin_user, company
):
    record_created_at = datetime(2026, 9, 9, 8, 0, tzinfo=timezone.utc)
    published_at = datetime(2026, 8, 6, 18, 42, 17, tzinfo=timezone.utc)
    document = _document(
        created_by_user_id=admin_user.id,
        created_at=record_created_at,
        original_published_at=published_at,
        original_published_at_precision=OriginalPublicationPrecision.DATETIME,
    )
    db.session.add(document)
    db.session.commit()

    persisted = db.session.get(Document, document.id)
    assert persisted.created_at == record_created_at.replace(tzinfo=None)
    assert persisted.original_published_at == published_at.replace(tzinfo=None)
    assert persisted.created_at != persisted.original_published_at


def test_original_publication_columns_follow_existing_datetime_conventions(app):
    assert isinstance(
        _column_type("document", "original_published_date"), sa.Date
    )
    assert isinstance(
        _column_type("document", "original_published_at"), sa.DateTime
    )
    # Match the established SPA timezone-aware datetime convention.
    assert Document.__table__.c.original_published_at.type.timezone is True


def test_original_publication_precision_is_a_portable_closed_enum(app):
    assert _enum_values(Document, "original_published_at_precision") == (
        {
            OriginalPublicationPrecision.DATE,
            OriginalPublicationPrecision.DATETIME,
            OriginalPublicationPrecision.UNKNOWN,
        },
        False,
    )


def test_document_exposes_a_nullable_self_supersedes_foreign_key(app):
    assert _reflected_columns("document")["supersedes_document_id"] is True

    foreign_keys = {
        frozenset(constraint["constrained_columns"]): constraint[
            "referred_table"
        ]
        for constraint in sa.inspect(db.engine).get_foreign_keys("document")
    }
    assert foreign_keys[frozenset({"supersedes_document_id"})] == "document"

    check_names = {
        constraint["name"]
        for constraint in sa.inspect(db.engine).get_check_constraints(
            "document"
        )
    }
    assert "ck_document_not_self_superseding" in check_names


def test_ordinary_document_without_a_predecessor_is_valid(
    app, admin_user, company
):
    document = _document(created_by_user_id=admin_user.id)
    db.session.add(document)
    db.session.commit()

    persisted = db.session.get(Document, document.id)
    assert persisted.supersedes_document_id is None
    assert persisted.supersedes is None
    assert persisted.superseded_by == []


def test_corrected_document_records_its_predecessor(
    app, admin_user, company
):
    original = _document(created_by_user_id=admin_user.id)
    db.session.add(original)
    db.session.commit()

    corrected = _document(
        title="FY26 Annual Report (corrected)",
        created_by_user_id=admin_user.id,
        supersedes_document_id=original.id,
    )
    db.session.add(corrected)
    db.session.commit()

    persisted = db.session.get(Document, corrected.id)
    assert persisted.supersedes_document_id == original.id
    assert persisted.supersedes is not None
    assert persisted.supersedes.id == original.id
    assert persisted.supersedes.title == "FY26 Annual Report"
    assert [row.id for row in original.superseded_by] == [corrected.id]


def test_creating_a_correction_leaves_the_predecessor_unchanged(
    app, admin_user, company
):
    original = _document(created_by_user_id=admin_user.id)
    db.session.add(original)
    db.session.commit()
    original_id = original.id
    original_title = original.title
    original_fingerprint = original.metadata_fingerprint

    corrected = _document(
        title="FY26 Annual Report (corrected)",
        created_by_user_id=admin_user.id,
        supersedes_document_id=original_id,
    )
    db.session.add(corrected)
    db.session.commit()

    persisted = db.session.get(Document, original_id)
    assert persisted.id == original_id
    assert persisted.title == original_title
    assert persisted.metadata_fingerprint == original_fingerprint
    assert persisted.supersedes_document_id is None
    assert persisted.id != corrected.id


def test_document_cannot_supersede_itself(app, admin_user, company):
    document = _document(
        created_by_user_id=admin_user.id,
        id="self-superseding-document",
        supersedes_document_id="self-superseding-document",
    )
    db.session.add(document)
    _commit_expect_integrity()
