"""Plan 4 Task 8: rights-safe document and institutional report queries.

Query paths must apply document-rights policy before totals and pagination,
return only explicit policy projections, and keep storage references and
restricted hashes private. Institutional reports retain complete history while
``latest_per_institution`` selects one deterministic current row per
institution for the requested company.
"""

from __future__ import annotations

import hashlib
from datetime import date, datetime, timezone

import pytest

from app import db
from app.models import (
    Company,
    Document,
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
from app.models.research_types import ResearchTier
from app.policies.document_access import (
    MANAGED_DOCUMENT_FIELDS,
    PUBLIC_DOCUMENT_FIELDS,
)
from app.services.document_library_service import DocumentLibraryService
from app.services.entitlement_service import ResearchAccessContext
from app.services.research_query_service import PageResult
from app.utils.research_errors import ResearchNotFoundError


def _context(
    user_id: str,
    *,
    is_admin: bool = False,
    tier: str = ResearchTier.FREE,
) -> ResearchAccessContext:
    return ResearchAccessContext(user_id, is_admin, tier)


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
def other_company(ticker_factory):
    return _make_company(
        ticker_factory,
        symbol="PEER",
        isin="INE000A01001",
        instrument_token=102,
    )


def _fingerprint(identifier: str) -> str:
    return hashlib.sha256(identifier.encode("utf-8")).hexdigest()


def _make_document(
    *,
    identifier: str,
    created_by_user_id: str,
    company: Company | None,
    title: str,
    created_at: datetime,
    document_type: str = DocumentType.ANNUAL_REPORT,
    document_date: date | None = date(2026, 8, 31),
    publisher_name: str | None = None,
    source_access: str = SourceAccess.PUBLIC,
    distribution_status: str = DistributionStatus.LINK_ONLY,
    discovery_source_type: str = DiscoverySourceType.OFFICIAL_SITE,
    discovery_source_reference: str | None = None,
    original_source_url: str | None = "https://publisher.example/report",
    storage_provider: str | None = None,
    storage_key: str | None = None,
    content_hash_sha256: str | None = None,
    metadata_fingerprint: str | None = None,
    mime_type: str | None = None,
    file_size_bytes: int | None = None,
    provided_by_user_id: str | None = None,
    institution: Institution | None = None,
    report_type: str | None = None,
    analyst_name: str | None = None,
    archived_at: datetime | None = None,
    is_primary: bool = True,
    second_company: Company | None = None,
) -> Document:
    acquisition_method = (
        AcquisitionMethod.USER_UPLOAD
        if provided_by_user_id is not None
        else AcquisitionMethod.MANUAL_REFERENCE
    )
    document = Document(
        id=identifier,
        document_type=document_type,
        title=title,
        document_date=document_date,
        reporting_period=None,
        publisher_name=publisher_name,
        publisher_reference=(
            "https://publisher.example/about"
            if publisher_name is not None
            else None
        ),
        original_source_url=original_source_url,
        discovery_source_type=discovery_source_type,
        discovery_source_reference=discovery_source_reference,
        source_access=source_access,
        acquisition_method=acquisition_method,
        distribution_status=distribution_status,
        ingestion_status=(
            IngestionStatus.STORED
            if storage_key is not None
            else IngestionStatus.DISCOVERED
        ),
        storage_provider=storage_provider,
        storage_key=storage_key,
        content_hash_sha256=content_hash_sha256,
        metadata_fingerprint=(
            metadata_fingerprint or _fingerprint(identifier)
        ),
        mime_type=mime_type,
        file_size_bytes=file_size_bytes,
        provided_by_user_id=provided_by_user_id,
        distribution_basis=(
            "Verified public distribution basis"
            if distribution_status == DistributionStatus.APP_DISTRIBUTABLE
            else None
        ),
        rights_verified_by_user_id=(
            created_by_user_id
            if distribution_status == DistributionStatus.APP_DISTRIBUTABLE
            else None
        ),
        rights_verified_at=(
            created_at
            if distribution_status == DistributionStatus.APP_DISTRIBUTABLE
            else None
        ),
        created_by_user_id=created_by_user_id,
        created_at=created_at,
        updated_at=created_at,
        archived_at=archived_at,
    )
    db.session.add(document)
    db.session.flush()

    if company is not None:
        db.session.add(
            DocumentCompanyLink(
                document_id=document.id,
                company_id=company.id,
                is_primary=is_primary,
            )
        )
    if second_company is not None:
        db.session.add(
            DocumentCompanyLink(
                document_id=document.id,
                company_id=second_company.id,
                is_primary=False,
            )
        )
    if institution is not None:
        db.session.add(
            InstitutionalReportMetadata(
                document_id=document.id,
                institution_id=institution.id,
                analyst_name=analyst_name,
                report_type=report_type,
            )
        )

    db.session.commit()
    return document


def _make_institution(name: str, *, website: str | None = None) -> Institution:
    institution = Institution(
        name=name,
        normalized_name=name.casefold(),
        website=website,
    )
    db.session.add(institution)
    db.session.commit()
    return institution


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


def _assert_storage_absent(payload: object) -> None:
    keys = _all_keys(payload)
    assert "storage_key" not in keys
    assert "storage_provider" not in keys
    assert "opaque/object-store/private-key" not in str(payload)


def _assert_consumer_restricted_fields_absent(payload: object) -> None:
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
    ):
        assert forbidden not in keys


def test_get_document_returns_only_the_policy_projection(
    app, admin_user, premium_user, company
):
    document = _make_document(
        identifier="11111111-1111-1111-1111-111111111111",
        created_by_user_id=admin_user.id,
        company=company,
        title="Official source report",
        created_at=datetime(2026, 9, 1, 10, 0, tzinfo=timezone.utc),
        document_type=DocumentType.ANNUAL_REPORT,
        document_date=date(2026, 6, 30),
        discovery_source_type=DiscoverySourceType.TELEGRAM,
        discovery_source_reference="https://t.me/private-discovery/42",
        original_source_url="https://official.example/annual-report",
        storage_provider="object-store",
        storage_key="opaque/object-store/private-key",
        content_hash_sha256="a" * 64,
        mime_type="application/pdf",
        file_size_bytes=123_456,
    )

    projection = DocumentLibraryService.get_document(
        document.id,
        _context(premium_user.id, tier=ResearchTier.PREMIUM),
    )

    assert set(projection) == PUBLIC_DOCUMENT_FIELDS
    assert projection["title"] == "Official source report"
    assert projection["document_type"] == DocumentType.ANNUAL_REPORT
    assert projection["document_date"] == "2026-06-30"
    assert projection["original_source_url"] == (
        "https://official.example/annual-report"
    )
    assert "t.me" not in str(projection)
    _assert_consumer_restricted_fields_absent(projection)
    _assert_storage_absent(projection)

    managed = DocumentLibraryService.get_document(
        document.id,
        _context(admin_user.id, is_admin=True),
    )
    assert managed["discovery_source_type"] == (
        DiscoverySourceType.TELEGRAM
    )
    assert managed["discovery_source_reference"] == (
        "https://t.me/private-discovery/42"
    )
    assert managed["original_source_url"] == (
        "https://official.example/annual-report"
    )
    _assert_storage_absent(managed)


def test_get_document_is_non_revealing_for_missing_and_inaccessible_rows(
    app, admin_user, premium_user, user_factory, company
):
    provider = user_factory(email="restricted-provider@example.com")
    private_document = _make_document(
        identifier="22222222-2222-2222-2222-222222222222",
        created_by_user_id=admin_user.id,
        company=company,
        title="RESTRICTED-PRIVATE-SENTINEL",
        created_at=datetime(2026, 9, 1, 10, 0, tzinfo=timezone.utc),
        source_access=SourceAccess.RESTRICTED,
        distribution_status=DistributionStatus.PRIVATE_LIBRARY,
        original_source_url=None,
        provided_by_user_id=provider.id,
        storage_provider="object-store",
        storage_key="opaque/object-store/private-key",
    )
    context = _context(premium_user.id, tier=ResearchTier.PREMIUM)

    with pytest.raises(ResearchNotFoundError) as inaccessible:
        DocumentLibraryService.get_document(private_document.id, context)
    with pytest.raises(ResearchNotFoundError) as missing:
        DocumentLibraryService.get_document(
            "99999999-9999-9999-9999-999999999999",
            context,
        )

    assert inaccessible.value.code == "document_not_found"
    assert missing.value.code == "document_not_found"
    assert inaccessible.value.message == missing.value.message
    assert "RESTRICTED-PRIVATE-SENTINEL" not in (
        inaccessible.value.message
    )


def test_get_document_managed_projection_keeps_rights_metadata_without_storage(
    app, admin_user
):
    document = _make_document(
        identifier="33333333-3333-3333-3333-333333333333",
        created_by_user_id=admin_user.id,
        company=None,
        title="Managed document",
        created_at=datetime(2026, 9, 2, 10, 0, tzinfo=timezone.utc),
        original_source_url="https://official.example/managed",
        storage_provider="object-store",
        storage_key="opaque/object-store/private-key",
        content_hash_sha256="b" * 64,
        mime_type="application/pdf",
        file_size_bytes=456_789,
    )

    projection = DocumentLibraryService.get_document(
        document.id,
        _context(admin_user.id, is_admin=True),
    )

    assert set(projection) == MANAGED_DOCUMENT_FIELDS
    assert projection["content_hash_sha256"] == "b" * 64
    _assert_storage_absent(projection)


def test_list_company_documents_applies_document_filters(
    app, admin_user, premium_user, company
):
    institution_a = _make_institution("Alpha Research")
    institution_b = _make_institution("Beta Research")
    context = _context(premium_user.id, tier=ResearchTier.PREMIUM)
    base_time = datetime(2026, 9, 1, 9, 0, tzinfo=timezone.utc)

    annual = _make_document(
        identifier="40000000-0000-0000-0000-000000000001",
        created_by_user_id=admin_user.id,
        company=company,
        title="Annual report",
        created_at=base_time,
        document_type=DocumentType.ANNUAL_REPORT,
        document_date=date(2026, 1, 15),
    )
    quarterly = _make_document(
        identifier="40000000-0000-0000-0000-000000000002",
        created_by_user_id=admin_user.id,
        company=company,
        title="Quarterly results",
        created_at=base_time,
        document_type=DocumentType.QUARTERLY_RESULTS,
        document_date=date(2026, 6, 20),
    )
    alpha_update = _make_document(
        identifier="40000000-0000-0000-0000-000000000003",
        created_by_user_id=admin_user.id,
        company=company,
        title="Alpha result update",
        created_at=base_time,
        document_type=DocumentType.INSTITUTIONAL_RESEARCH,
        document_date=date(2026, 4, 1),
        institution=institution_a,
        report_type="RESULT_UPDATE",
    )
    alpha_initiation = _make_document(
        identifier="40000000-0000-0000-0000-000000000004",
        created_by_user_id=admin_user.id,
        company=company,
        title="Alpha initiating coverage",
        created_at=base_time,
        document_type=DocumentType.INSTITUTIONAL_RESEARCH,
        document_date=date(2026, 3, 1),
        institution=institution_a,
        report_type="INITIATING_COVERAGE",
    )
    beta_initiation = _make_document(
        identifier="40000000-0000-0000-0000-000000000005",
        created_by_user_id=admin_user.id,
        company=company,
        title="Beta initiating coverage",
        created_at=base_time,
        document_type=DocumentType.INSTITUTIONAL_RESEARCH,
        document_date=date(2026, 5, 1),
        institution=institution_b,
        report_type="INITIATING_COVERAGE",
    )
    _make_document(
        identifier="40000000-0000-0000-0000-000000000006",
        created_by_user_id=admin_user.id,
        company=company,
        title="Annual report second",
        created_at=base_time,
        document_type=DocumentType.ANNUAL_REPORT,
        document_date=date(2026, 2, 15),
    )

    by_type = DocumentLibraryService.list_company_documents(
        company.id,
        {"document_type": DocumentType.QUARTERLY_RESULTS},
        1,
        20,
        context,
    )
    by_institution = DocumentLibraryService.list_company_documents(
        company.id,
        {"institution_id": institution_a.id},
        1,
        20,
        context,
    )
    by_report_type = DocumentLibraryService.list_company_documents(
        company.id,
        {
            "institution_id": institution_a.id,
            "report_type": "RESULT_UPDATE",
        },
        1,
        20,
        context,
    )
    by_date = DocumentLibraryService.list_company_documents(
        company.id,
        {"date_from": date(2026, 4, 1), "date_to": date(2026, 6, 20)},
        1,
        20,
        context,
    )

    assert isinstance(by_type, PageResult)
    assert [item["id"] for item in by_type.items] == [quarterly.id]
    assert {
        item["id"] for item in by_institution.items
    } == {alpha_update.id, alpha_initiation.id}
    assert [item["id"] for item in by_report_type.items] == [
        alpha_update.id
    ]
    assert [item["id"] for item in by_date.items] == [
        quarterly.id,
        beta_initiation.id,
        alpha_update.id,
    ]
    assert annual.id not in {
        item["id"] for item in by_date.items
    }
    for result in (by_type, by_institution, by_report_type, by_date):
        for item in result.items:
            _assert_storage_absent(item)
            _assert_consumer_restricted_fields_absent(item)


def test_list_company_documents_orders_newest_first_deterministically(
    app, admin_user, premium_user, company
):
    context = _context(premium_user.id, tier=ResearchTier.PREMIUM)
    first = _make_document(
        identifier="50000000-0000-0000-0000-000000000001",
        created_by_user_id=admin_user.id,
        company=company,
        title="Older date",
        created_at=datetime(2026, 9, 10, 9, 0, tzinfo=timezone.utc),
        document_date=date(2026, 8, 1),
    )
    same_date_early = _make_document(
        identifier="50000000-0000-0000-0000-000000000002",
        created_by_user_id=admin_user.id,
        company=company,
        title="Same date early",
        created_at=datetime(2026, 9, 1, 9, 0, tzinfo=timezone.utc),
        document_date=date(2026, 9, 1),
    )
    same_date_late = _make_document(
        identifier="50000000-0000-0000-0000-000000000003",
        created_by_user_id=admin_user.id,
        company=company,
        title="Same date late",
        created_at=datetime(2026, 9, 1, 11, 0, tzinfo=timezone.utc),
        document_date=date(2026, 9, 1),
    )
    tie_alpha = _make_document(
        identifier="tie-alpha",
        created_by_user_id=admin_user.id,
        company=company,
        title="Tie alpha",
        created_at=datetime(2026, 9, 2, 9, 0, tzinfo=timezone.utc),
        document_date=date(2026, 9, 2),
    )
    tie_zulu = _make_document(
        identifier="tie-zulu",
        created_by_user_id=admin_user.id,
        company=company,
        title="Tie zulu",
        created_at=datetime(2026, 9, 2, 9, 0, tzinfo=timezone.utc),
        document_date=date(2026, 9, 2),
    )
    undated = _make_document(
        identifier="50000000-0000-0000-0000-000000000006",
        created_by_user_id=admin_user.id,
        company=company,
        title="Undated reference",
        created_at=datetime(2026, 9, 3, 9, 0, tzinfo=timezone.utc),
        document_date=None,
    )

    result = DocumentLibraryService.list_company_documents(
        company.id,
        {},
        1,
        20,
        context,
    )

    assert [item["id"] for item in result.items] == [
        tie_zulu.id,
        tie_alpha.id,
        same_date_late.id,
        same_date_early.id,
        first.id,
        undated.id,
    ]
    assert result.total_items == 6
    assert result.total_pages == 1


def test_list_company_documents_filters_rights_before_counts_and_pagination(
    app,
    admin_user,
    premium_user,
    user_factory,
    company,
):
    provider = user_factory(email="document-provider@example.com")
    other_provider = user_factory(email="other-provider@example.com")
    context = _context(premium_user.id, tier=ResearchTier.PREMIUM)
    base_time = datetime(2026, 9, 1, 9, 0, tzinfo=timezone.utc)

    public_app = _make_document(
        identifier="60000000-0000-0000-0000-000000000001",
        created_by_user_id=admin_user.id,
        company=company,
        title="PUBLIC-APP-DOCUMENT",
        created_at=base_time,
        document_date=date(2026, 9, 3),
        distribution_status=DistributionStatus.APP_DISTRIBUTABLE,
    )
    public_link = _make_document(
        identifier="60000000-0000-0000-0000-000000000002",
        created_by_user_id=admin_user.id,
        company=company,
        title="PUBLIC-LINK-DOCUMENT",
        created_at=base_time,
        document_date=date(2026, 9, 2),
    )
    public_old = _make_document(
        identifier="60000000-0000-0000-0000-000000000003",
        created_by_user_id=admin_user.id,
        company=company,
        title="PUBLIC-OLD-DOCUMENT",
        created_at=base_time,
        document_date=date(2026, 9, 1),
    )
    provider_newest = _make_document(
        identifier="60000000-0000-0000-0000-000000000004",
        created_by_user_id=admin_user.id,
        company=company,
        title="PROVIDER-PRIVATE-NEWEST",
        created_at=base_time,
        document_date=date(2026, 12, 1),
        source_access=SourceAccess.RESTRICTED,
        distribution_status=DistributionStatus.PRIVATE_LIBRARY,
        original_source_url=None,
        provided_by_user_id=provider.id,
        storage_provider="object-store",
        storage_key="opaque/object-store/private-key",
        content_hash_sha256="c" * 64,
        mime_type="application/pdf",
        file_size_bytes=987_654,
    )
    provider_second = _make_document(
        identifier="60000000-0000-0000-0000-000000000005",
        created_by_user_id=admin_user.id,
        company=company,
        title="PROVIDER-PRIVATE-SECOND",
        created_at=base_time,
        document_date=date(2026, 11, 1),
        source_access=SourceAccess.RESTRICTED,
        distribution_status=DistributionStatus.PRIVATE_LIBRARY,
        original_source_url=None,
        provided_by_user_id=provider.id,
        storage_provider="object-store",
        storage_key="opaque/object-store/private-key",
        content_hash_sha256="d" * 64,
        mime_type="application/pdf",
        file_size_bytes=111_111,
    )
    unrelated_private = _make_document(
        identifier="60000000-0000-0000-0000-000000000006",
        created_by_user_id=admin_user.id,
        company=company,
        title="UNRELATED-PRIVATE-DOCUMENT",
        created_at=base_time,
        document_date=date(2026, 10, 1),
        source_access=SourceAccess.RESTRICTED,
        distribution_status=DistributionStatus.PRIVATE_LIBRARY,
        original_source_url=None,
        provided_by_user_id=other_provider.id,
        storage_provider="object-store",
        storage_key="opaque/object-store/private-key",
    )
    _make_document(
        identifier="60000000-0000-0000-0000-000000000007",
        created_by_user_id=admin_user.id,
        company=company,
        title="ARCHIVED-PUBLIC-DOCUMENT",
        created_at=base_time,
        document_date=date(2026, 9, 4),
        archived_at=datetime(2026, 9, 10, tzinfo=timezone.utc),
    )

    consumer_page_one = DocumentLibraryService.list_company_documents(
        company.id,
        {},
        1,
        2,
        context,
    )
    consumer_page_two = DocumentLibraryService.list_company_documents(
        company.id,
        {},
        2,
        2,
        context,
    )
    provider_result = DocumentLibraryService.list_company_documents(
        company.id,
        {},
        1,
        4,
        _context(provider.id),
    )

    assert consumer_page_one.total_items == 3
    assert consumer_page_one.total_pages == 2
    assert [item["id"] for item in consumer_page_one.items] == [
        public_app.id,
        public_link.id,
    ]
    assert [item["id"] for item in consumer_page_two.items] == [
        public_old.id
    ]
    for item in consumer_page_one.items + consumer_page_two.items:
        _assert_consumer_restricted_fields_absent(item)
        _assert_storage_absent(item)
    assert provider_result.total_items == 5
    assert provider_result.total_pages == 2
    assert [item["id"] for item in provider_result.items] == [
        provider_newest.id,
        provider_second.id,
        public_app.id,
        public_link.id,
    ]
    assert unrelated_private.id not in {
        item["id"] for item in provider_result.items
    }
    for item in provider_result.items:
        _assert_storage_absent(item)


def test_list_institutional_reports_latest_preserves_paginated_history(
    app, admin_user, premium_user, company, other_company
):
    institution_a = _make_institution(
        "Alpha Research",
        website="https://alpha.example/about",
    )
    institution_b = _make_institution("Beta Research")
    context = _context(premium_user.id, tier=ResearchTier.PREMIUM)
    base_time = datetime(2026, 9, 1, 9, 0, tzinfo=timezone.utc)

    alpha_old = _make_document(
        identifier="70000000-0000-0000-0000-000000000001",
        created_by_user_id=admin_user.id,
        company=company,
        title="Alpha old report",
        created_at=base_time,
        document_type=DocumentType.INSTITUTIONAL_RESEARCH,
        document_date=date(2026, 2, 1),
        institution=institution_a,
        report_type="RESULT_UPDATE",
        analyst_name="ANALYST-PRIVATE-SENTINEL",
    )
    alpha_mid = _make_document(
        identifier="70000000-0000-0000-0000-000000000002",
        created_by_user_id=admin_user.id,
        company=company,
        title="Alpha mid report",
        created_at=datetime(2026, 9, 1, 10, 0, tzinfo=timezone.utc),
        document_type=DocumentType.INSTITUTIONAL_RESEARCH,
        document_date=date(2026, 8, 1),
        institution=institution_a,
        report_type="RESULT_UPDATE",
    )
    alpha_latest = _make_document(
        identifier="70000000-0000-0000-0000-000000000003",
        created_by_user_id=admin_user.id,
        company=company,
        title="Alpha latest report",
        created_at=datetime(2026, 9, 1, 11, 0, tzinfo=timezone.utc),
        document_type=DocumentType.INSTITUTIONAL_RESEARCH,
        document_date=date(2026, 8, 1),
        institution=institution_a,
        report_type="INITIATING_COVERAGE",
    )
    beta_only = _make_document(
        identifier="70000000-0000-0000-0000-000000000004",
        created_by_user_id=admin_user.id,
        company=company,
        title="Beta only report",
        created_at=base_time,
        document_type=DocumentType.INSTITUTIONAL_RESEARCH,
        document_date=date(2026, 5, 1),
        institution=institution_b,
        report_type="INITIATING_COVERAGE",
    )
    _make_document(
        identifier="70000000-0000-0000-0000-000000000005",
        created_by_user_id=admin_user.id,
        company=other_company,
        title="OTHER-COMPANY-ALPHA-REPORT",
        created_at=base_time,
        document_type=DocumentType.INSTITUTIONAL_RESEARCH,
        document_date=date(2026, 12, 1),
        institution=institution_a,
        report_type="RESULT_UPDATE",
    )

    latest = DocumentLibraryService.list_institutional_reports(
        company.id,
        latest_per_institution=True,
        page=1,
        per_page=10,
        context=context,
    )
    history = DocumentLibraryService.list_institutional_reports(
        company.id,
        latest_per_institution=False,
        page=1,
        per_page=10,
        context=context,
    )

    assert [item["id"] for item in latest.items] == [
        alpha_latest.id,
        beta_only.id,
    ]
    assert latest.total_items == 2
    assert latest.total_pages == 1
    assert [item["id"] for item in history.items] == [
        alpha_latest.id,
        alpha_mid.id,
        beta_only.id,
        alpha_old.id,
    ]
    assert history.total_items == 4
    assert history.total_pages == 1
    assert DocumentLibraryService.get_document(
        alpha_old.id, context
    )["title"] == "Alpha old report"
    for item in history.items:
        _assert_consumer_restricted_fields_absent(item)
        _assert_storage_absent(item)
    assert "ANALYST-PRIVATE-SENTINEL" not in str(history.items)


def test_institutional_items_include_only_rights_safe_institution_metadata(
    app, admin_user, premium_user, company
):
    institution = _make_institution(
        "Alpha Research",
        website="https://alpha.example/about",
    )
    document = _make_document(
        identifier="80000000-0000-0000-0000-000000000001",
        created_by_user_id=admin_user.id,
        company=company,
        title="Alpha coverage",
        created_at=datetime(2026, 9, 1, 10, 0, tzinfo=timezone.utc),
        document_type=DocumentType.INSTITUTIONAL_RESEARCH,
        document_date=date(2026, 8, 1),
        institution=institution,
        report_type="INITIATING_COVERAGE",
        analyst_name="Analyst Name",
        storage_provider="object-store",
        storage_key="opaque/object-store/private-key",
        content_hash_sha256="e" * 64,
    )
    context = _context(premium_user.id, tier=ResearchTier.PREMIUM)

    detail = DocumentLibraryService.get_document(document.id, context)
    listing = DocumentLibraryService.list_institutional_reports(
        company.id,
        latest_per_institution=False,
        page=1,
        per_page=10,
        context=context,
    )

    expected_institution = {
        "institution_id": institution.id,
        "institution_name": "Alpha Research",
        "report_type": "INITIATING_COVERAGE",
    }
    assert detail["institutional_report"] == expected_institution
    assert listing.items[0]["institutional_report"] == (
        expected_institution
    )
    assert "Analyst Name" not in str(detail)
    assert "Analyst Name" not in str(listing.items)
    assert "alpha.example" not in str(detail)
    assert "alpha.example" not in str(listing.items)
    _assert_consumer_restricted_fields_absent(detail)
    _assert_consumer_restricted_fields_absent(listing.items)
    _assert_storage_absent(detail)
    _assert_storage_absent(listing.items)


def test_list_company_documents_rejects_unknown_company(
    app, admin_user, premium_user
):
    context = _context(premium_user.id, tier=ResearchTier.PREMIUM)

    with pytest.raises(ResearchNotFoundError) as exc_info:
        DocumentLibraryService.list_company_documents(
            "99999999-9999-9999-9999-999999999999",
            {},
            1,
            20,
            context,
        )

    assert exc_info.value.code == "company_not_found"
    assert exc_info.value.message == "Company was not found"
