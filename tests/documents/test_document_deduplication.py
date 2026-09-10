"""Plan 4 Task 3: deterministic document fingerprints and duplicate decisions.

This suite locks the generic fingerprint contract shared by every document
type and the four duplicate outcomes used by later aggregate commands.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import FrozenInstanceError
from datetime import date

import pytest

from app import db
from app.models.document import (
    AcquisitionMethod,
    DiscoverySourceType,
    DistributionStatus,
    Document,
    DocumentType,
    IngestionStatus,
    SourceAccess,
)
from app.services.document_deduplication_service import (
    FINGERPRINT_VERSION,
    DocumentDeduplicationService,
    normalize_fingerprint_text,
)


GOLDEN_CANONICAL_JSON = (
    '{"company_ids":["company-a","company-b"],'
    '"document_date":"2026-08-31",'
    '"document_type":"INSTITUTIONAL_RESEARCH",'
    '"institutional_report_type":"initiating_coverage",'
    '"publisher_or_institution":"institution-1",'
    '"reporting_period":"fy27e",'
    '"title":"example report",'
    '"version":1}'
)
GOLDEN_FINGERPRINT = (
    "53d6d92f91928606498b4775c3d6a5eb6d412fc6210087422d21a78fd463ad8d"
)


def _fingerprint_inputs(**overrides: object) -> dict[str, object]:
    inputs: dict[str, object] = {
        "document_type": DocumentType.INSTITUTIONAL_RESEARCH,
        "company_ids": ["company-a", "company-b"],
        "document_date": date(2026, 8, 31),
        "title": "Example report",
        "publisher_name": "Publisher name",
        "institution_id": "institution-1",
        "reporting_period": "FY27E",
        "report_type": "INITIATING_COVERAGE",
    }
    inputs.update(overrides)
    return inputs


def _document(
    *,
    created_by_user_id: str,
    metadata_fingerprint: str,
    content_hash_sha256: str | None = None,
    title: str = "Existing report",
) -> Document:
    document = Document(
        document_type=DocumentType.ANNUAL_REPORT,
        title=title,
        document_date=date(2026, 8, 31),
        discovery_source_type=DiscoverySourceType.OFFICIAL_SITE,
        source_access=SourceAccess.PUBLIC,
        acquisition_method=AcquisitionMethod.MANUAL_REFERENCE,
        distribution_status=DistributionStatus.LINK_ONLY,
        ingestion_status=IngestionStatus.DISCOVERED,
        content_hash_sha256=content_hash_sha256,
        metadata_fingerprint=metadata_fingerprint,
        created_by_user_id=created_by_user_id,
    )
    db.session.add(document)
    db.session.commit()
    return document


def test_normalize_fingerprint_text_uses_nfkc_whitespace_and_casefold():
    assert (
        normalize_fingerprint_text("  ＦＵＬＬ　Width\tReport\nName  ")
        == "full width report name"
    )
    assert (
        normalize_fingerprint_text("MiXeD.Punctuation/Case:Kept!")
        == "mixed.punctuation/case:kept!"
    )
    assert normalize_fingerprint_text(None) is None


def test_canonical_payload_has_exact_versioned_shape_and_null_sentinels():
    payload = DocumentDeduplicationService.canonical_payload(
        document_type=DocumentType.OTHER,
        company_ids=[],
        document_date=None,
        title="Undated notice",
        publisher_name=None,
        institution_id=None,
        reporting_period=None,
        report_type=None,
    )

    assert payload == {
        "version": FINGERPRINT_VERSION,
        "document_type": DocumentType.OTHER,
        "company_ids": [],
        "document_date": "<NULL>",
        "title": "undated notice",
        "publisher_or_institution": "<NULL>",
        "reporting_period": "<NULL>",
        "institutional_report_type": "<NULL>",
    }
    assert "analyst_name" not in payload


def test_canonical_payload_uses_iso_dates_and_stable_sorted_json():
    payload = DocumentDeduplicationService.canonical_payload(
        **_fingerprint_inputs()
    )

    assert payload["document_date"] == "2026-08-31"
    assert json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ) == GOLDEN_CANONICAL_JSON


def test_company_ids_are_sorted_unique_and_independent_of_link_order():
    first = DocumentDeduplicationService.canonical_payload(
        **_fingerprint_inputs(
            company_ids=["company-b", "company-a", "company-b"]
        )
    )
    second = DocumentDeduplicationService.canonical_payload(
        **_fingerprint_inputs(
            company_ids=["company-a", "company-b"]
        )
    )

    assert first["company_ids"] == ["company-a", "company-b"]
    assert first == second
    assert "is_primary" not in first
    assert "primary_company_id" not in first


def test_institution_id_takes_precedence_over_publisher_name():
    institutional = DocumentDeduplicationService.canonical_payload(
        **_fingerprint_inputs(
            publisher_name="Generic publisher",
            institution_id="Institution-1",
        )
    )
    generic = DocumentDeduplicationService.canonical_payload(
        **_fingerprint_inputs(
            publisher_name="  Generic   Publisher ",
            institution_id=None,
        )
    )
    absent = DocumentDeduplicationService.canonical_payload(
        **_fingerprint_inputs(
            publisher_name=None,
            institution_id=None,
        )
    )

    assert institutional["publisher_or_institution"] == "institution-1"
    assert generic["publisher_or_institution"] == "generic publisher"
    assert absent["publisher_or_institution"] == "<NULL>"


def test_reporting_period_and_institutional_report_type_affect_fingerprint():
    base = DocumentDeduplicationService.metadata_fingerprint(
        **_fingerprint_inputs()
    )
    changed_period = DocumentDeduplicationService.metadata_fingerprint(
        **_fingerprint_inputs(reporting_period="FY28E")
    )
    changed_type = DocumentDeduplicationService.metadata_fingerprint(
        **_fingerprint_inputs(report_type="RESULT_UPDATE")
    )

    assert base != changed_period
    assert base != changed_type


def test_document_type_differentiates_otherwise_identical_metadata():
    institutional = DocumentDeduplicationService.canonical_payload(
        **_fingerprint_inputs(document_type=DocumentType.INSTITUTIONAL_RESEARCH)
    )
    industry = DocumentDeduplicationService.canonical_payload(
        **_fingerprint_inputs(document_type=DocumentType.INDUSTRY_REPORT)
    )

    assert (
        institutional["document_type"]
        == DocumentType.INSTITUTIONAL_RESEARCH
    )
    assert industry["document_type"] == DocumentType.INDUSTRY_REPORT
    assert institutional != industry


def test_known_golden_sha256():
    payload = DocumentDeduplicationService.canonical_payload(
        document_type=DocumentType.INSTITUTIONAL_RESEARCH,
        company_ids=["company-b", "company-a"],
        document_date=date(2026, 8, 31),
        title="  Example   Report ",
        publisher_name="Ignored publisher",
        institution_id="Institution-1",
        reporting_period="FY27E",
        report_type="INITIATING_COVERAGE",
    )
    canonical_json = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )

    assert canonical_json == GOLDEN_CANONICAL_JSON
    assert DocumentDeduplicationService.metadata_fingerprint(
        document_type=DocumentType.INSTITUTIONAL_RESEARCH,
        company_ids=["company-b", "company-a"],
        document_date=date(2026, 8, 31),
        title="  Example   Report ",
        publisher_name="Ignored publisher",
        institution_id="Institution-1",
        reporting_period="FY27E",
        report_type="INITIATING_COVERAGE",
    ) == GOLDEN_FINGERPRINT
    assert hashlib.sha256(canonical_json.encode("utf-8")).hexdigest() == (
        GOLDEN_FINGERPRINT
    )


def test_no_duplicate_returns_immutable_none_decision(app):
    decision = DocumentDeduplicationService.find_duplicate(
        metadata_fingerprint="a" * 64,
        content_hash_sha256="b" * 64,
    )

    assert decision.kind == "NONE"
    assert decision.matched_document_id is None
    with pytest.raises(FrozenInstanceError):
        decision.kind = "METADATA_MATCH"


def test_metadata_match_requires_review_when_content_hash_is_absent(
    app, admin_user
):
    existing = _document(
        created_by_user_id=admin_user.id,
        metadata_fingerprint="c" * 64,
        content_hash_sha256="d" * 64,
    )

    decision = DocumentDeduplicationService.find_duplicate(
        metadata_fingerprint="c" * 64,
        content_hash_sha256=None,
    )

    assert decision.kind == "METADATA_MATCH"
    assert decision.matched_document_id == existing.id


def test_metadata_match_requires_review_when_content_hash_differs(
    app, admin_user
):
    existing = _document(
        created_by_user_id=admin_user.id,
        metadata_fingerprint="c" * 64,
        content_hash_sha256="d" * 64,
    )

    decision = DocumentDeduplicationService.find_duplicate(
        metadata_fingerprint="c" * 64,
        content_hash_sha256="e" * 64,
    )

    assert decision.kind == "METADATA_MATCH"
    assert decision.matched_document_id == existing.id


def test_exact_binary_match_uses_content_hash_and_matching_metadata(
    app, admin_user
):
    existing = _document(
        created_by_user_id=admin_user.id,
        metadata_fingerprint="c" * 64,
        content_hash_sha256="d" * 64,
    )

    decision = DocumentDeduplicationService.find_duplicate(
        metadata_fingerprint="c" * 64,
        content_hash_sha256="d" * 64,
    )

    assert decision.kind == "EXACT_BINARY"
    assert decision.matched_document_id == existing.id


def test_matching_binary_with_different_metadata_is_a_conflict(
    app, admin_user
):
    existing = _document(
        created_by_user_id=admin_user.id,
        metadata_fingerprint="c" * 64,
        content_hash_sha256="d" * 64,
    )

    decision = DocumentDeduplicationService.find_duplicate(
        metadata_fingerprint="e" * 64,
        content_hash_sha256="d" * 64,
    )

    assert decision.kind == "BINARY_METADATA_CONFLICT"
    assert decision.matched_document_id == existing.id


def test_institution_and_date_alone_never_create_a_duplicate(app, admin_user):
    metadata_a = _fingerprint_inputs(
        title="First report",
        company_ids=["company-a"],
    )
    metadata_b = _fingerprint_inputs(
        title="Second report",
        company_ids=["company-a"],
    )
    fingerprint_a = DocumentDeduplicationService.metadata_fingerprint(
        **metadata_a
    )
    fingerprint_b = DocumentDeduplicationService.metadata_fingerprint(
        **metadata_b
    )

    assert fingerprint_a != fingerprint_b

    existing = _document(
        created_by_user_id=admin_user.id,
        metadata_fingerprint=fingerprint_a,
    )
    decision = DocumentDeduplicationService.find_duplicate(
        metadata_fingerprint=fingerprint_b,
        content_hash_sha256=None,
    )

    assert existing.document_date == date(2026, 8, 31)
    assert decision.kind == "NONE"
    assert decision.matched_document_id is None
