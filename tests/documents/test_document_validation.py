"""Plan 4 Task 2: document request schemas and state validation.

This suite locks the frozen Milestone 1 request contract: strict Marshmallow
schemas, conservative rights defaults, ingestion/acquisition/storage
combinations, institutional metadata conditionality, company-link primary
rules, URL and SHA-256 formats, and the administrative transition surface.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest
from marshmallow import ValidationError

from app.models.document import (
    AcquisitionMethod,
    DiscoverySourceType,
    DistributionStatus,
    Document,
    DocumentType,
    IngestionStatus,
    SourceAccess,
)
from app.schemas.document import (
    DocumentCompanyLinkSchema,
    DocumentCreateSchema,
    DocumentPatchSchema,
    InstitutionCreateSchema,
)
from app.services.document_validation_service import DocumentValidationService
from app.utils.research_errors import ResearchValidationError


SHA256_HEX = "a" * 64
OTHER_SHA256_HEX = "b" * 64
VERIFIED_AT = "2026-09-10T12:00:00+00:00"


def _document_fields(**overrides: object) -> dict[str, object]:
    fields: dict[str, object] = {
        "document_type": DocumentType.ANNUAL_REPORT,
        "title": "Example annual report",
        "discovery_source_type": DiscoverySourceType.OFFICIAL_SITE,
        "source_access": SourceAccess.PUBLIC,
        "acquisition_method": AcquisitionMethod.MANUAL_REFERENCE,
        "distribution_status": DistributionStatus.LINK_ONLY,
        "ingestion_status": IngestionStatus.DISCOVERED,
        "original_source_url": "https://publisher.example/report",
    }
    fields.update(overrides)
    return fields


def _stored_fields(**overrides: object) -> dict[str, object]:
    fields = _document_fields(
        source_access=SourceAccess.RESTRICTED,
        acquisition_method=AcquisitionMethod.USER_UPLOAD,
        distribution_status=DistributionStatus.PRIVATE_LIBRARY,
        ingestion_status=IngestionStatus.STORED,
        original_source_url=None,
        provided_by_user_id="user-provider",
        storage_provider="object-store",
        storage_key="opaque/object/key",
        content_hash_sha256=SHA256_HEX,
        mime_type="application/pdf",
        file_size_bytes=1024,
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


def _institutional_report(
    **overrides: object,
) -> dict[str, object]:
    report: dict[str, object] = {
        "institution_id": "institution-1",
        "analyst_name": None,
        "report_type": "INITIATING_COVERAGE",
    }
    report.update(overrides)
    return report


def _document_instance(**overrides: object) -> Document:
    values: dict[str, object] = {
        "document_type": DocumentType.ANNUAL_REPORT,
        "title": "Existing document",
        "document_date": date(2026, 8, 31),
        "reporting_period": None,
        "publisher_name": None,
        "publisher_reference": None,
        "original_source_url": "https://publisher.example/report",
        "discovery_source_type": DiscoverySourceType.OFFICIAL_SITE,
        "discovery_source_reference": None,
        "source_access": SourceAccess.PUBLIC,
        "acquisition_method": AcquisitionMethod.MANUAL_REFERENCE,
        "distribution_status": DistributionStatus.LINK_ONLY,
        "ingestion_status": IngestionStatus.DISCOVERED,
        "storage_provider": None,
        "storage_key": None,
        "content_hash_sha256": None,
        "metadata_fingerprint": "c" * 64,
        "mime_type": None,
        "file_size_bytes": None,
        "provided_by_user_id": None,
        "distribution_basis": None,
        "rights_verified_by_user_id": None,
        "rights_verified_at": None,
        "created_by_user_id": "admin-user",
        "archived_at": None,
    }
    values.update(overrides)
    document = Document(**values)
    document.company_links = []
    document.institutional_metadata = None
    return document


def _error_details(callable_) -> dict[str, list[str]]:
    with pytest.raises(ResearchValidationError) as exc_info:
        callable_()
    return exc_info.value.details


# ---------------------------------------------------------------------------
# Strict request schemas
# ---------------------------------------------------------------------------


def test_create_schema_rejects_unknown_top_level_request_fields():
    details = _error_details(
        lambda: DocumentValidationService.validate_create(
            {**_payload(), "unexpected": "value"}
        )
    )

    assert "unexpected" in details


def test_create_schema_rejects_unknown_document_fields():
    details = _error_details(
        lambda: DocumentValidationService.validate_create(
            _payload({**_document_fields(), "unexpected": "value"})
        )
    )

    assert any("unexpected" in key for key in details)


def test_create_schema_rejects_unknown_company_link_fields():
    details = _error_details(
        lambda: DocumentValidationService.validate_create(
            _payload(
                company_links=[
                    {"company_id": "company-1", "unexpected": True}
                ]
            )
        )
    )

    assert any("unexpected" in key for key in details)


def test_company_link_schema_defaults_primary_to_false():
    assert DocumentCompanyLinkSchema().load({"company_id": "company-1"}) == {
        "company_id": "company-1",
        "is_primary": False,
    }


def test_patch_schema_rejects_unknown_fields():
    with pytest.raises(ValidationError) as exc_info:
        DocumentPatchSchema().load({"unexpected": "value"})

    assert "unexpected" in exc_info.value.messages


# ---------------------------------------------------------------------------
# Ingestion/acquisition/storage matrix
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("document", "company_links"),
    [
        (
            _document_fields(
                source_access=SourceAccess.RESTRICTED,
                acquisition_method=AcquisitionMethod.NOT_ACQUIRED,
                distribution_status=DistributionStatus.UNKNOWN,
                ingestion_status=IngestionStatus.DISCOVERED,
                original_source_url=None,
            ),
            [],
        ),
        (
            _document_fields(
                source_access=SourceAccess.RESTRICTED,
                acquisition_method=AcquisitionMethod.MANUAL_REFERENCE,
                distribution_status=DistributionStatus.UNKNOWN,
                ingestion_status=IngestionStatus.AWAITING_UPLOAD,
                original_source_url=None,
            ),
            [],
        ),
        (
            _stored_fields(),
            [{"company_id": "company-1", "is_primary": True}],
        ),
        (
            _stored_fields(ingestion_status=IngestionStatus.ANALYSED),
            [],
        ),
    ],
)
def test_valid_ingestion_state_combinations_are_accepted(document, company_links):
    result = DocumentValidationService.validate_create(
        _payload(document, company_links=company_links)
    )

    assert result["document"]["ingestion_status"] == document["ingestion_status"]
    assert result["company_links"] == [
        {"company_id": link["company_id"], "is_primary": link.get("is_primary", False)}
        for link in company_links
    ]


@pytest.mark.parametrize(
    ("ingestion_status", "acquisition_method"),
    [
        (IngestionStatus.DISCOVERED, AcquisitionMethod.PUBLIC_DOWNLOAD),
        (IngestionStatus.AWAITING_UPLOAD, AcquisitionMethod.USER_UPLOAD),
        (IngestionStatus.STORED, AcquisitionMethod.MANUAL_REFERENCE),
        (IngestionStatus.ANALYSED, AcquisitionMethod.NOT_ACQUIRED),
    ],
)
def test_invalid_ingestion_acquisition_combinations_are_rejected(
    ingestion_status, acquisition_method
):
    document = _document_fields(
        source_access=SourceAccess.PUBLIC,
        acquisition_method=acquisition_method,
        distribution_status=DistributionStatus.LINK_ONLY,
        ingestion_status=ingestion_status,
        original_source_url="https://publisher.example/report",
        provided_by_user_id="user-provider",
        storage_provider="object-store",
        storage_key="opaque/object/key",
        content_hash_sha256=SHA256_HEX,
    )

    details = _error_details(
        lambda: DocumentValidationService.validate_create(_payload(document))
    )

    assert "acquisition_method" in details


@pytest.mark.parametrize(
    "source_access", [SourceAccess.RESTRICTED, SourceAccess.UNKNOWN]
)
def test_public_download_is_rejected_for_restricted_or_unknown_sources(
    source_access,
):
    document = _document_fields(
        source_access=source_access,
        acquisition_method=AcquisitionMethod.PUBLIC_DOWNLOAD,
        distribution_status=DistributionStatus.PRIVATE_LIBRARY,
        ingestion_status=IngestionStatus.STORED,
        original_source_url=None,
        storage_provider="object-store",
        storage_key="opaque/object/key",
        content_hash_sha256=SHA256_HEX,
    )

    details = _error_details(
        lambda: DocumentValidationService.validate_create(_payload(document))
    )

    assert "acquisition_method" in details


def test_user_upload_requires_a_providing_user():
    document = _stored_fields(provided_by_user_id=None)

    details = _error_details(
        lambda: DocumentValidationService.validate_create(_payload(document))
    )

    assert "provided_by_user_id" in details


@pytest.mark.parametrize(
    "overrides",
    [
        {"storage_provider": None},
        {"storage_key": None},
        {"content_hash_sha256": None},
    ],
)
def test_stored_and_analysed_documents_require_storage_reference_and_hash(
    overrides,
):
    for ingestion_status in (
        IngestionStatus.STORED,
        IngestionStatus.ANALYSED,
    ):
        document = _stored_fields(
            ingestion_status=ingestion_status, **overrides
        )
        details = _error_details(
            lambda: DocumentValidationService.validate_create(_payload(document))
        )
        assert details, (ingestion_status, overrides)


@pytest.mark.parametrize(
    "content_hash_sha256",
    [
        "A" * 64,
        "a" * 63,
        "a" * 65,
        "g" * 64,
        "not-a-hash",
    ],
)
def test_content_hash_must_be_a_lowercase_sha256_hex_digest(
    content_hash_sha256,
):
    details = _error_details(
        lambda: DocumentValidationService.validate_create(
            _payload(_stored_fields(content_hash_sha256=content_hash_sha256))
        )
    )

    assert "content_hash_sha256" in details


@pytest.mark.parametrize(
    ("mime_type", "file_size_bytes"),
    [
        ("not-a-mime-type", 10),
        ("application/pdf", -1),
        ("text/plain", "large"),
    ],
)
def test_optional_stored_file_metadata_is_validated_when_supplied(
    mime_type, file_size_bytes
):
    details = _error_details(
        lambda: DocumentValidationService.validate_create(
            _payload(
                _stored_fields(
                    mime_type=mime_type, file_size_bytes=file_size_bytes
                )
            )
        )
    )

    assert "mime_type" in details or "file_size_bytes" in details


def test_optional_stored_file_metadata_is_accepted_when_omitted():
    result = DocumentValidationService.validate_create(
        _payload(_stored_fields(mime_type=None, file_size_bytes=None))
    )

    assert result["document"]["mime_type"] is None
    assert result["document"]["file_size_bytes"] is None


@pytest.mark.parametrize(
    "storage", ["storage_provider", "storage_key", "content_hash_sha256"]
)
def test_non_stored_documents_must_not_supply_storage_metadata(storage):
    document = _document_fields(
        source_access=SourceAccess.RESTRICTED,
        acquisition_method=AcquisitionMethod.MANUAL_REFERENCE,
        distribution_status=DistributionStatus.UNKNOWN,
        ingestion_status=IngestionStatus.AWAITING_UPLOAD,
        original_source_url=None,
    )
    document[storage] = {
        "storage_provider": "object-store",
        "storage_key": "opaque/object/key",
        "content_hash_sha256": SHA256_HEX,
    }[storage]

    details = _error_details(
        lambda: DocumentValidationService.validate_create(_payload(document))
    )

    assert storage in details


# ---------------------------------------------------------------------------
# Rights defaults and APP_DISTRIBUTABLE evidence
# ---------------------------------------------------------------------------


def test_public_source_defaults_to_link_only():
    document = _document_fields(distribution_status=None)

    result = DocumentValidationService.validate_create(_payload(document))

    assert (
        result["document"]["distribution_status"]
        == DistributionStatus.LINK_ONLY
    )


def test_restricted_user_upload_defaults_to_private_library():
    document = _stored_fields(distribution_status=None)

    result = DocumentValidationService.validate_create(_payload(document))

    assert (
        result["document"]["distribution_status"]
        == DistributionStatus.PRIVATE_LIBRARY
    )


@pytest.mark.parametrize(
    "missing_field",
    [
        "distribution_basis",
        "rights_verified_by_user_id",
        "rights_verified_at",
    ],
)
def test_app_distributable_requires_basis_verifier_and_time(missing_field):
    document = _document_fields(
        source_access=SourceAccess.PUBLIC,
        acquisition_method=AcquisitionMethod.MANUAL_REFERENCE,
        distribution_status=DistributionStatus.APP_DISTRIBUTABLE,
        ingestion_status=IngestionStatus.DISCOVERED,
        distribution_basis="Publisher grants redistribution",
        rights_verified_by_user_id="reviewer-1",
        rights_verified_at=VERIFIED_AT,
    )
    document[missing_field] = None

    details = _error_details(
        lambda: DocumentValidationService.validate_create(_payload(document))
    )

    assert missing_field in details


def test_explicit_app_distributable_evidence_is_preserved():
    document = _document_fields(
        distribution_status=DistributionStatus.APP_DISTRIBUTABLE,
        distribution_basis="Publisher grants redistribution",
        rights_verified_by_user_id="reviewer-1",
        rights_verified_at=VERIFIED_AT,
    )

    result = DocumentValidationService.validate_create(_payload(document))

    document_result = result["document"]
    assert (
        document_result["distribution_status"]
        == DistributionStatus.APP_DISTRIBUTABLE
    )
    assert (
        document_result["distribution_basis"]
        == "Publisher grants redistribution"
    )
    assert document_result["rights_verified_by_user_id"] == "reviewer-1"
    assert isinstance(document_result["rights_verified_at"], datetime)


# ---------------------------------------------------------------------------
# URL rules
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "document",
    [
        _document_fields(
            source_access=SourceAccess.PUBLIC,
            original_source_url=None,
            distribution_status=DistributionStatus.UNKNOWN,
        ),
        _document_fields(
            source_access=SourceAccess.RESTRICTED,
            acquisition_method=AcquisitionMethod.MANUAL_REFERENCE,
            original_source_url=None,
            distribution_status=DistributionStatus.LINK_ONLY,
            ingestion_status=IngestionStatus.DISCOVERED,
        ),
    ],
)
def test_public_sources_and_link_only_distribution_require_a_source_url(
    document,
):
    details = _error_details(
        lambda: DocumentValidationService.validate_create(_payload(document))
    )

    assert "original_source_url" in details


@pytest.mark.parametrize(
    "url",
    [
        "ftp://publisher.example/report",
        "file:///tmp/report.pdf",
        "javascript:alert(1)",
        "publisher.example/report",
    ],
)
def test_source_url_rejects_non_http_or_https_schemes(url):
    details = _error_details(
        lambda: DocumentValidationService.validate_create(
            _payload(_document_fields(original_source_url=url))
        )
    )

    assert "original_source_url" in details


def test_http_urls_are_normalized_without_being_followed():
    result = DocumentValidationService.validate_create(
        _payload(
            _document_fields(
                original_source_url="HTTPS://Publisher.Example:443/Report"
            )
        )
    )

    assert (
        result["document"]["original_source_url"]
        == "https://publisher.example:443/Report"
    )


# ---------------------------------------------------------------------------
# Institutional metadata and company links
# ---------------------------------------------------------------------------


def test_institutional_metadata_is_rejected_for_non_institutional_type():
    details = _error_details(
        lambda: DocumentValidationService.validate_create(
            _payload(institutional_report=_institutional_report())
        )
    )

    assert "institutional_report" in details


def test_institutional_research_requires_institutional_metadata():
    document = _stored_fields(document_type=DocumentType.INSTITUTIONAL_RESEARCH)

    details = _error_details(
        lambda: DocumentValidationService.validate_create(
            _payload(
                document,
                company_links=[{"company_id": "company-1", "is_primary": True}],
            )
        )
    )

    assert "institutional_report" in details


def test_institutional_research_requires_at_least_one_company_link():
    document = _stored_fields(document_type=DocumentType.INSTITUTIONAL_RESEARCH)

    details = _error_details(
        lambda: DocumentValidationService.validate_create(
            _payload(
                document,
                company_links=[],
                institutional_report=_institutional_report(),
            )
        )
    )

    assert "company_links" in details


def test_institutional_research_accepts_complete_aggregate():
    document = _stored_fields(document_type=DocumentType.INSTITUTIONAL_RESEARCH)

    result = DocumentValidationService.validate_create(
        _payload(
            document,
            company_links=[
                {"company_id": "company-2", "is_primary": False},
                {"company_id": "company-1", "is_primary": True},
            ],
            institutional_report=_institutional_report(
                institution_id="institution-9",
                analyst_name="A. Analyst",
                report_type="INITIATING_COVERAGE",
            ),
        )
    )

    assert result["institutional_report"] == {
        "institution_id": "institution-9",
        "analyst_name": "A. Analyst",
        "report_type": "INITIATING_COVERAGE",
    }
    assert result["company_links"] == [
        {"company_id": "company-2", "is_primary": False},
        {"company_id": "company-1", "is_primary": True},
    ]


def test_two_primary_company_links_are_rejected():
    details = _error_details(
        lambda: DocumentValidationService.validate_create(
            _payload(
                company_links=[
                    {"company_id": "company-1", "is_primary": True},
                    {"company_id": "company-2", "is_primary": True},
                ]
            )
        )
    )

    assert "company_links" in details


def test_zero_primary_multi_company_document_is_allowed():
    result = DocumentValidationService.validate_create(
        _payload(
            company_links=[
                {"company_id": "company-1", "is_primary": False},
                {"company_id": "company-2", "is_primary": False},
            ]
        )
    )

    assert all(
        link["is_primary"] is False for link in result["company_links"]
    )


def test_duplicate_company_links_are_rejected():
    details = _error_details(
        lambda: DocumentValidationService.validate_create(
            _payload(
                company_links=[
                    {"company_id": "company-1", "is_primary": True},
                    {"company_id": "company-1", "is_primary": False},
                ]
            )
        )
    )

    assert "company_links" in details


# ---------------------------------------------------------------------------
# Patch validation
# ---------------------------------------------------------------------------


def test_validate_patch_returns_normalized_changes():
    document = _document_instance(
        source_access=SourceAccess.RESTRICTED,
        acquisition_method=AcquisitionMethod.USER_UPLOAD,
        distribution_status=DistributionStatus.PRIVATE_LIBRARY,
        ingestion_status=IngestionStatus.AWAITING_UPLOAD,
        original_source_url=None,
        provided_by_user_id="user-provider",
    )

    changes = DocumentValidationService.validate_patch(
        document,
        {
            "ingestion_status": IngestionStatus.STORED,
            "original_source_url": "HTTPS://Publisher.Example/Report",
            "storage_provider": "object-store",
            "storage_key": "opaque/object/key",
            "content_hash_sha256": SHA256_HEX,
        },
    )

    assert changes["ingestion_status"] == IngestionStatus.STORED
    assert (
        changes["original_source_url"]
        == "https://publisher.example/Report"
    )
    assert changes["content_hash_sha256"] == SHA256_HEX


def test_validate_patch_rejects_invalid_resulting_state():
    document = _document_instance(
        source_access=SourceAccess.RESTRICTED,
        acquisition_method=AcquisitionMethod.USER_UPLOAD,
        distribution_status=DistributionStatus.PRIVATE_LIBRARY,
        ingestion_status=IngestionStatus.AWAITING_UPLOAD,
        original_source_url=None,
        provided_by_user_id="user-provider",
    )

    details = _error_details(
        lambda: DocumentValidationService.validate_patch(
            document, {"ingestion_status": IngestionStatus.STORED}
        )
    )

    assert (
        "storage_provider" in details
        or "storage_key" in details
        or "content_hash_sha256" in details
    )


def test_validate_patch_recomputes_public_source_requirements():
    document = _document_instance(
        source_access=SourceAccess.RESTRICTED,
        acquisition_method=AcquisitionMethod.MANUAL_REFERENCE,
        distribution_status=DistributionStatus.UNKNOWN,
        ingestion_status=IngestionStatus.DISCOVERED,
        original_source_url=None,
    )

    details = _error_details(
        lambda: DocumentValidationService.validate_patch(
            document, {"source_access": SourceAccess.PUBLIC}
        )
    )

    assert "original_source_url" in details


def test_validate_patch_rejects_unknown_fields():
    document = _document_instance()

    details = _error_details(
        lambda: DocumentValidationService.validate_patch(
            document, {"unexpected": "value"}
        )
    )

    assert "unexpected" in details


# ---------------------------------------------------------------------------
# Transition validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (IngestionStatus.DISCOVERED, IngestionStatus.AWAITING_UPLOAD),
        (IngestionStatus.DISCOVERED, IngestionStatus.STORED),
        (IngestionStatus.AWAITING_UPLOAD, IngestionStatus.STORED),
    ],
)
def test_forward_ingestion_transitions_are_allowed(current, target):
    document = _document_instance(ingestion_status=current)

    assert (
        DocumentValidationService.validate_transition(
            document, {"ingestion_status": target}
        )
        is None
    )


def test_milestone_1_cannot_transition_stored_to_analysed():
    document = _document_instance(ingestion_status=IngestionStatus.STORED)

    details = _error_details(
        lambda: DocumentValidationService.validate_transition(
            document, {"ingestion_status": IngestionStatus.ANALYSED}
        )
    )

    assert "ingestion_status" in details


def test_backwards_ingestion_transition_is_rejected():
    document = _document_instance(ingestion_status=IngestionStatus.STORED)

    details = _error_details(
        lambda: DocumentValidationService.validate_transition(
            document, {"ingestion_status": IngestionStatus.DISCOVERED}
        )
    )

    assert "ingestion_status" in details


def test_unchanged_ingestion_status_is_allowed():
    document = _document_instance(ingestion_status=IngestionStatus.DISCOVERED)

    assert (
        DocumentValidationService.validate_transition(
            document, {"ingestion_status": IngestionStatus.DISCOVERED}
        )
        is None
    )


# ---------------------------------------------------------------------------
# Institution creation schema
# ---------------------------------------------------------------------------


def test_institution_create_schema_accepts_and_normalizes_name_and_website():
    result = InstitutionCreateSchema().load(
        {"name": "Example Research", "website": "HTTPS://Research.Example/"}
    )

    assert result["name"] == "Example Research"
    assert result["website"] == "https://research.example/"


def test_institution_create_schema_defaults_website_to_none():
    result = InstitutionCreateSchema().load({"name": "Example Research"})

    assert result == {"name": "Example Research", "website": None}


def test_institution_create_schema_rejects_blank_name():
    with pytest.raises(ValidationError) as exc_info:
        InstitutionCreateSchema().load({"name": "   "})

    assert "name" in exc_info.value.messages


def test_institution_create_schema_rejects_invalid_website_scheme():
    with pytest.raises(ValidationError) as exc_info:
        InstitutionCreateSchema().load(
            {"name": "Example Research", "website": "ftp://example.com"}
        )

    assert "website" in exc_info.value.messages


def test_institution_create_schema_rejects_unknown_fields():
    with pytest.raises(ValidationError) as exc_info:
        InstitutionCreateSchema().load(
            {"name": "Example Research", "unexpected": True}
        )

    assert "unexpected" in exc_info.value.messages
