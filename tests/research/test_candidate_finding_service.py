"""Research Coverage & Fact Intelligence, Slice 2:
``ResearchCoverageService`` candidate-finding tests.

Covers: recording a candidate finding tied to a real review pass;
promotion creating a real, fully-sourced Fact through the existing,
unchanged ``record_evidence``/``record_fact`` (still enforcing >=1
evidence, still participating in supersession); rejection requiring a
reason; duplicate-marking; and the "already decided" guard that prevents
triaging the same candidate twice.
"""

from __future__ import annotations

from datetime import date

import pytest
import sqlalchemy as sa

from app import db
from app.models import Company
from app.models.document import (
    AcquisitionMethod,
    DiscoverySourceType,
    DistributionStatus,
    DocumentType,
    IngestionStatus,
    SourceAccess,
)
from app.models.research_brain import Evidence, ExtractedFact, FactEvidence
from app.services.document_library_service import DocumentLibraryService
from app.services.research_brain_service import ResearchBrainService
from app.services.research_coverage_service import ResearchCoverageService
from app.utils.research_errors import ResearchNotFoundError, ResearchValidationError


@pytest.fixture
def company(ticker_factory):
    ticker = ticker_factory(symbol="IKIO", instrument_token=1)
    company = Company(
        ticker_id=ticker.id,
        legal_name="IKIO Technologies Limited",
        isin="INE0LOJ01019",
    )
    db.session.add(company)
    db.session.commit()
    return company


@pytest.fixture
def document(admin_user, company):
    payload = {
        "document": {
            "document_type": DocumentType.ANNUAL_REPORT,
            "title": "IKIO_AR_FY2026_TEST",
            "document_date": "2026-08-08",
            "reporting_period": "FY2026",
            "publisher_name": "IKIO Technologies Limited",
            "original_source_url": "https://example.in/ikio/ar-fy2026",
            "discovery_source_type": DiscoverySourceType.OFFICIAL_SITE,
            "source_access": SourceAccess.PUBLIC,
            "acquisition_method": AcquisitionMethod.MANUAL_REFERENCE,
            "distribution_status": DistributionStatus.LINK_ONLY,
            "ingestion_status": IngestionStatus.DISCOVERED,
        },
        "company_links": [{"company_id": company.id, "is_primary": True}],
    }
    return DocumentLibraryService.create_document(payload, admin_user.id)


@pytest.fixture
def dimension():
    return ResearchCoverageService.create_dimension(
        code="GOVERNANCE_RPT",
        name="Governance & Related-Party Transactions",
        description="RPT disclosures, KMP remuneration, audit-trail notes.",
    )


@pytest.fixture
def swept_pass(admin_user, document, dimension):
    """A document with 5 ExtractionUnits, fully swept for one dimension --
    the realistic precondition for a candidate finding to exist."""

    run = ResearchBrainService.create_extraction_run(
        document_id=document.id,
        method="manual_pilot",
        extracted_by_user_id=admin_user.id,
    )
    units = []
    for i in range(1, 6):
        units.append(
            ResearchBrainService.record_extraction_unit(
                extraction_run_id=run.id,
                document_id=document.id,
                unit_type="PAGE",
                sequence_number=i,
                content_text=f"page {i}: some governance-relevant text here.",
                created_by_user_id=admin_user.id,
            )
        )
    review_pass = ResearchCoverageService.record_coverage_review_pass(
        document_id=document.id,
        research_dimension_id=dimension.id,
        performed_by_user_id=admin_user.id,
        units_considered_count=5,
        units_considered_min_seq=1,
        units_considered_max_seq=5,
        has_material_content=True,
        notes="Found a statutory auditor appointment on page 3.",
    )
    return review_pass, units


def test_record_candidate_finding_cites_real_source(
    admin_user, document, dimension, swept_pass
):
    review_pass, units = swept_pass
    finding = ResearchCoverageService.record_candidate_finding(
        document_id=document.id,
        research_dimension_id=dimension.id,
        source_extraction_unit_id=units[2].id,
        review_pass_id=review_pass.id,
        raw_quote="The Board approved Agarwal & Saxena as statutory auditor.",
        created_by_user_id=admin_user.id,
        proposed_fact_type="statutory_auditor_appointment",
        proposed_value_type="TEXT",
        proposed_value="Agarwal & Saxena appointed statutory auditor.",
        proposed_period="FY2026",
        proposed_as_of_date=date(2026, 8, 8),
    )
    assert finding.source_extraction_unit_id == units[2].id
    assert finding.review_pass_id == review_pass.id
    assert ResearchCoverageService.get_candidate_finding_status(finding.id) == "OPEN"


def test_promotion_creates_a_real_fact_with_at_least_one_evidence(
    admin_user, company, document, dimension, swept_pass
):
    review_pass, units = swept_pass
    finding = ResearchCoverageService.record_candidate_finding(
        document_id=document.id,
        research_dimension_id=dimension.id,
        source_extraction_unit_id=units[2].id,
        review_pass_id=review_pass.id,
        raw_quote="The Board approved Agarwal & Saxena as statutory auditor.",
        created_by_user_id=admin_user.id,
        proposed_fact_type="statutory_auditor_appointment",
        proposed_value_type="TEXT",
        proposed_value="Agarwal & Saxena appointed statutory auditor.",
        proposed_period="FY2026",
        proposed_as_of_date=date(2026, 8, 8),
    )

    decision = ResearchCoverageService.promote_candidate_finding(
        candidate_finding_id=finding.id,
        company_id=company.id,
        created_by_user_id=admin_user.id,
    )

    assert decision.decision == "PROMOTED"
    assert decision.promoted_to_fact_id is not None

    fact = db.session.get(ExtractedFact, decision.promoted_to_fact_id)
    assert fact.company_id == company.id
    assert fact.fact_type == "statutory_auditor_appointment"
    assert fact.value == "Agarwal & Saxena appointed statutory auditor."

    evidence_links = db.session.scalars(
        sa.select(FactEvidence).where(FactEvidence.fact_id == fact.id)
    ).all()
    assert len(evidence_links) >= 1

    evidence = db.session.get(Evidence, evidence_links[0].evidence_id)
    assert evidence.source_extraction_unit_id == units[2].id
    assert evidence.text_snippet == finding.raw_quote

    assert (
        ResearchCoverageService.get_candidate_finding_status(finding.id)
        == "PROMOTED"
    )

    # The promoted Fact is still immutable, like every other Fact.
    fact.value = "edited"
    with pytest.raises(sa.exc.InvalidRequestError, match="immutable"):
        db.session.commit()
    db.session.rollback()


def test_promotion_without_a_fact_type_anywhere_is_rejected(
    admin_user, company, document, dimension, swept_pass
):
    review_pass, units = swept_pass
    finding = ResearchCoverageService.record_candidate_finding(
        document_id=document.id,
        research_dimension_id=dimension.id,
        source_extraction_unit_id=units[0].id,
        review_pass_id=review_pass.id,
        raw_quote="Some quote with no proposed shape filled in.",
        created_by_user_id=admin_user.id,
    )

    with pytest.raises(ResearchValidationError):
        ResearchCoverageService.promote_candidate_finding(
            candidate_finding_id=finding.id,
            company_id=company.id,
            created_by_user_id=admin_user.id,
        )


def test_promotion_rejects_mismatched_unit_and_value_type_before_any_write(
    admin_user, company, document, dimension, swept_pass
):
    """A code-review finding: an override/fallback combination that only
    becomes an invalid unit+value_type pairing *after* promotion resolves
    final_unit/final_value_type -- the candidate itself is perfectly
    valid (TEXT, no unit) -- used to reach record_fact's own DB check
    constraint only after record_evidence had already committed a real,
    now-orphaned Evidence row. Must now be rejected before either write
    happens."""

    review_pass, units = swept_pass
    finding = ResearchCoverageService.record_candidate_finding(
        document_id=document.id,
        research_dimension_id=dimension.id,
        source_extraction_unit_id=units[0].id,
        review_pass_id=review_pass.id,
        raw_quote="Some narrative disclosure, not a number.",
        created_by_user_id=admin_user.id,
        proposed_fact_type="some_text_fact",
        proposed_value_type="TEXT",
        proposed_value="A qualitative statement.",
        proposed_period="FY2026",
    )

    from app.models.research_brain import Evidence
    import sqlalchemy as sa

    evidence_count_before = db.session.scalar(
        sa.select(sa.func.count(Evidence.id))
    )

    with pytest.raises(ResearchValidationError):
        ResearchCoverageService.promote_candidate_finding(
            candidate_finding_id=finding.id,
            company_id=company.id,
            created_by_user_id=admin_user.id,
            # value_type stays TEXT (from the candidate's proposed_*), but
            # this override alone makes the final combination invalid --
            # the mismatch only exists after promotion resolves both.
            unit="INR_CRORE",
        )

    evidence_count_after = db.session.scalar(
        sa.select(sa.func.count(Evidence.id))
    )
    assert evidence_count_after == evidence_count_before, (
        "no Evidence row should be created when promotion is rejected "
        "before any write"
    )
    assert (
        ResearchCoverageService.get_candidate_finding_status(finding.id)
        == "OPEN"
    )


def test_reject_and_duplicate_report_not_found_for_a_bad_candidate_id(
    admin_user,
):
    """A code-review finding: a nonexistent candidate_finding_id used to
    fall through to a later FK-violation IntegrityError, caught and
    misreported as 'already decided by a concurrent call' -- masking a
    plain bad id as a transient race. Must now raise the same
    ResearchNotFoundError this codebase already uses elsewhere for a
    genuinely missing resource (document_library_service.py), not a
    validation error."""

    bogus_id = "00000000-0000-0000-0000-000000000000"

    with pytest.raises(ResearchNotFoundError):
        ResearchCoverageService.reject_candidate_finding(
            candidate_finding_id=bogus_id,
            reason="irrelevant",
            created_by_user_id=admin_user.id,
        )

    with pytest.raises(ResearchNotFoundError):
        ResearchCoverageService.mark_candidate_finding_duplicate(
            candidate_finding_id=bogus_id,
            duplicate_of_candidate_id=bogus_id,
            created_by_user_id=admin_user.id,
        )


def test_promotion_override_takes_precedence_over_proposed(
    admin_user, company, document, dimension, swept_pass
):
    review_pass, units = swept_pass
    finding = ResearchCoverageService.record_candidate_finding(
        document_id=document.id,
        research_dimension_id=dimension.id,
        source_extraction_unit_id=units[0].id,
        review_pass_id=review_pass.id,
        raw_quote="FY26 employee turnover was 49.00%.",
        created_by_user_id=admin_user.id,
        proposed_fact_type="employee_turnover_rate",
        proposed_value_type="NUMERIC",
        proposed_value="49.00",
        proposed_unit="PERCENT",
        proposed_period="FY2026",
        proposed_as_of_date=date(2026, 8, 8),
    )

    decision = ResearchCoverageService.promote_candidate_finding(
        candidate_finding_id=finding.id,
        company_id=company.id,
        created_by_user_id=admin_user.id,
        value="49.0",  # override: normalized trailing-zero form
    )
    fact = db.session.get(ExtractedFact, decision.promoted_to_fact_id)
    assert fact.value == "49.0"
    assert fact.unit == "PERCENT"  # un-overridden fields still fall back


def test_rejection_requires_a_reason(
    admin_user, document, dimension, swept_pass
):
    review_pass, units = swept_pass
    finding = ResearchCoverageService.record_candidate_finding(
        document_id=document.id,
        research_dimension_id=dimension.id,
        source_extraction_unit_id=units[0].id,
        review_pass_id=review_pass.id,
        raw_quote="Routine boilerplate, not material.",
        created_by_user_id=admin_user.id,
    )

    with pytest.raises(ResearchValidationError):
        ResearchCoverageService.reject_candidate_finding(
            candidate_finding_id=finding.id,
            reason="",
            created_by_user_id=admin_user.id,
        )

    decision = ResearchCoverageService.reject_candidate_finding(
        candidate_finding_id=finding.id,
        reason="Standard boilerplate disclosure, no material new information.",
        created_by_user_id=admin_user.id,
    )
    assert decision.decision == "REJECTED"
    assert (
        ResearchCoverageService.get_candidate_finding_status(finding.id)
        == "REJECTED"
    )


def test_duplicate_marking_requires_a_real_target_and_not_itself(
    admin_user, document, dimension, swept_pass
):
    review_pass, units = swept_pass
    first = ResearchCoverageService.record_candidate_finding(
        document_id=document.id,
        research_dimension_id=dimension.id,
        source_extraction_unit_id=units[0].id,
        review_pass_id=review_pass.id,
        raw_quote="Auditor appointed: Agarwal & Saxena.",
        created_by_user_id=admin_user.id,
    )
    second = ResearchCoverageService.record_candidate_finding(
        document_id=document.id,
        research_dimension_id=dimension.id,
        source_extraction_unit_id=units[1].id,
        review_pass_id=review_pass.id,
        raw_quote="Same auditor appointment, restated on a later page.",
        created_by_user_id=admin_user.id,
    )

    with pytest.raises(ResearchValidationError):
        ResearchCoverageService.mark_candidate_finding_duplicate(
            candidate_finding_id=second.id,
            duplicate_of_candidate_id=second.id,
            created_by_user_id=admin_user.id,
        )

    decision = ResearchCoverageService.mark_candidate_finding_duplicate(
        candidate_finding_id=second.id,
        duplicate_of_candidate_id=first.id,
        created_by_user_id=admin_user.id,
        reason="Same auditor-appointment fact restated verbatim.",
    )
    assert decision.decision == "DUPLICATE"
    assert decision.duplicate_of_candidate_id == first.id


def test_a_decided_candidate_cannot_be_triaged_again(
    admin_user, company, document, dimension, swept_pass
):
    review_pass, units = swept_pass
    finding = ResearchCoverageService.record_candidate_finding(
        document_id=document.id,
        research_dimension_id=dimension.id,
        source_extraction_unit_id=units[0].id,
        review_pass_id=review_pass.id,
        raw_quote="Not material.",
        created_by_user_id=admin_user.id,
    )
    ResearchCoverageService.reject_candidate_finding(
        candidate_finding_id=finding.id,
        reason="Immaterial boilerplate.",
        created_by_user_id=admin_user.id,
    )

    with pytest.raises(ResearchValidationError):
        ResearchCoverageService.reject_candidate_finding(
            candidate_finding_id=finding.id,
            reason="Trying again.",
            created_by_user_id=admin_user.id,
        )

    with pytest.raises(ResearchValidationError):
        ResearchCoverageService.promote_candidate_finding(
            candidate_finding_id=finding.id,
            company_id=company.id,
            created_by_user_id=admin_user.id,
        )


def test_get_open_candidate_findings_excludes_decided_ones(
    admin_user, company, document, dimension, swept_pass
):
    review_pass, units = swept_pass
    open_one = ResearchCoverageService.record_candidate_finding(
        document_id=document.id,
        research_dimension_id=dimension.id,
        source_extraction_unit_id=units[0].id,
        review_pass_id=review_pass.id,
        raw_quote="Still open.",
        created_by_user_id=admin_user.id,
    )
    rejected_one = ResearchCoverageService.record_candidate_finding(
        document_id=document.id,
        research_dimension_id=dimension.id,
        source_extraction_unit_id=units[1].id,
        review_pass_id=review_pass.id,
        raw_quote="Will be rejected.",
        created_by_user_id=admin_user.id,
    )
    ResearchCoverageService.reject_candidate_finding(
        candidate_finding_id=rejected_one.id,
        reason="Not material.",
        created_by_user_id=admin_user.id,
    )

    open_findings = ResearchCoverageService.get_open_candidate_findings(
        document_id=document.id
    )
    open_ids = {f.id for f in open_findings}
    assert open_one.id in open_ids
    assert rejected_one.id not in open_ids
