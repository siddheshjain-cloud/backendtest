"""Research Coverage & Fact Intelligence, Slice 2: model-level tests for
``CandidateFinding`` and ``CandidateFindingDecision``.

Locks: immutability (update and delete both raise ``InvalidRequestError``)
on both models, and the database-enforced "decision implies exactly the
matching target field" check constraints (``PROMOTED`` iff
``promoted_to_fact_id`` set; ``DUPLICATE`` iff ``duplicate_of_candidate_id``
set).
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
from app.models.research_coverage import (
    CandidateFinding,
    CandidateFindingDecision,
    ResearchDimension,
)
from app.services.document_library_service import DocumentLibraryService
from app.services.research_brain_service import ResearchBrainService


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
            "document_type": DocumentType.QUARTERLY_RESULTS,
            "title": "IKIO_RESULTS_Q1_FY2027",
            "document_date": "2026-08-08",
            "reporting_period": "Q1 FY2027",
            "publisher_name": "IKIO Technologies Limited",
            "original_source_url": "https://example.in/ikio/q1fy2027-results",
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
def extraction_run(admin_user, document):
    return ResearchBrainService.create_extraction_run(
        document_id=document.id,
        method="manual_pilot",
        extracted_by_user_id=admin_user.id,
    )


@pytest.fixture
def extraction_unit(admin_user, document, extraction_run):
    return ResearchBrainService.record_extraction_unit(
        extraction_run_id=extraction_run.id,
        document_id=document.id,
        unit_type="PAGE",
        sequence_number=1,
        content_text="page 1 text",
        created_by_user_id=admin_user.id,
    )


@pytest.fixture
def dimension():
    dim = ResearchDimension(
        code="GOVERNANCE_RPT",
        name="Governance & Related-Party Transactions",
        description="RPT disclosures, KMP remuneration, audit-trail notes.",
    )
    db.session.add(dim)
    db.session.commit()
    return dim


@pytest.fixture
def review_pass(admin_user, document, dimension, extraction_run, extraction_unit):
    from app.services.research_coverage_service import ResearchCoverageService

    return ResearchCoverageService.record_coverage_review_pass(
        document_id=document.id,
        research_dimension_id=dimension.id,
        performed_by_user_id=admin_user.id,
        units_considered_count=1,
        units_considered_min_seq=1,
        units_considered_max_seq=1,
        has_material_content=True,
    )


@pytest.fixture
def candidate_finding(admin_user, document, dimension, extraction_unit, review_pass):
    finding = CandidateFinding(
        document_id=document.id,
        research_dimension_id=dimension.id,
        source_extraction_unit_id=extraction_unit.id,
        review_pass_id=review_pass.id,
        raw_quote="The company appointed Agarwal & Saxena as statutory auditor.",
        proposed_fact_type="statutory_auditor_appointment",
        proposed_value_type="TEXT",
        proposed_value="Agarwal & Saxena appointed as statutory auditor.",
        proposed_period="FY2026",
        proposed_as_of_date=date(2026, 8, 8),
        created_by_user_id=admin_user.id,
    )
    db.session.add(finding)
    db.session.commit()
    return finding


def test_candidate_finding_foreign_keys(app):
    inspector = sa.inspect(db.engine)
    foreign_keys = {
        frozenset(constraint["constrained_columns"]): constraint["referred_table"]
        for constraint in inspector.get_foreign_keys("candidate_finding")
    }
    assert foreign_keys[frozenset({"document_id"})] == "document"
    assert (
        foreign_keys[frozenset({"research_dimension_id"})] == "research_dimension"
    )
    assert (
        foreign_keys[frozenset({"source_extraction_unit_id"})] == "extraction_unit"
    )
    assert foreign_keys[frozenset({"review_pass_id"})] == "coverage_review_pass"
    assert foreign_keys[frozenset({"created_by_user_id"})] == "user"


def test_candidate_finding_cannot_be_updated_or_deleted(app, candidate_finding):
    candidate_finding.raw_quote = "edited"
    with pytest.raises(sa.exc.InvalidRequestError, match="immutable"):
        db.session.commit()
    db.session.rollback()

    db.session.delete(db.session.get(CandidateFinding, candidate_finding.id))
    with pytest.raises(sa.exc.InvalidRequestError, match="immutable"):
        db.session.commit()
    db.session.rollback()


def test_candidate_finding_unit_requires_numeric_value_type(
    admin_user, document, dimension, extraction_unit, review_pass
):
    bad = CandidateFinding(
        document_id=document.id,
        research_dimension_id=dimension.id,
        source_extraction_unit_id=extraction_unit.id,
        review_pass_id=review_pass.id,
        raw_quote="some text",
        proposed_value_type="TEXT",
        proposed_unit="INR_CRORE",
        created_by_user_id=admin_user.id,
    )
    db.session.add(bad)
    with pytest.raises(sa.exc.IntegrityError):
        db.session.commit()
    db.session.rollback()


def test_candidate_finding_unit_requires_numeric_value_type_even_when_type_is_null(
    admin_user, document, dimension, extraction_unit, review_pass
):
    """Adversarial case a code review caught: SQL NULL semantics let a bare
    ``proposed_value_type = 'NUMERIC'`` check silently pass when
    ``proposed_value_type`` itself is NULL -- the constraint must use
    ``coalesce`` to actually reject this."""

    bad = CandidateFinding(
        document_id=document.id,
        research_dimension_id=dimension.id,
        source_extraction_unit_id=extraction_unit.id,
        review_pass_id=review_pass.id,
        raw_quote="some text",
        proposed_value_type=None,
        proposed_unit="INR_CRORE",
        created_by_user_id=admin_user.id,
    )
    db.session.add(bad)
    with pytest.raises(sa.exc.IntegrityError):
        db.session.commit()
    db.session.rollback()


def test_candidate_finding_decision_foreign_keys(app):
    inspector = sa.inspect(db.engine)
    foreign_keys = {
        frozenset(constraint["constrained_columns"]): constraint["referred_table"]
        for constraint in inspector.get_foreign_keys("candidate_finding_decision")
    }
    assert (
        foreign_keys[frozenset({"candidate_finding_id"})] == "candidate_finding"
    )
    assert foreign_keys[frozenset({"promoted_to_fact_id"})] == "extracted_fact"
    assert (
        foreign_keys[frozenset({"duplicate_of_candidate_id"})] == "candidate_finding"
    )


def test_candidate_finding_decision_cannot_be_updated_or_deleted(
    admin_user, candidate_finding
):
    decision = CandidateFindingDecision(
        candidate_finding_id=candidate_finding.id,
        decision="REJECTED",
        reason="Not material.",
        created_by_user_id=admin_user.id,
    )
    db.session.add(decision)
    db.session.commit()

    decision.reason = "edited"
    with pytest.raises(sa.exc.InvalidRequestError, match="immutable"):
        db.session.commit()
    db.session.rollback()

    db.session.delete(db.session.get(CandidateFindingDecision, decision.id))
    with pytest.raises(sa.exc.InvalidRequestError, match="immutable"):
        db.session.commit()
    db.session.rollback()


def test_promoted_decision_requires_a_fact_id(admin_user, candidate_finding):
    bad = CandidateFindingDecision(
        candidate_finding_id=candidate_finding.id,
        decision="PROMOTED",
        promoted_to_fact_id=None,
        created_by_user_id=admin_user.id,
    )
    db.session.add(bad)
    with pytest.raises(sa.exc.IntegrityError):
        db.session.commit()
    db.session.rollback()


def test_rejected_decision_cannot_carry_a_fact_id(
    admin_user, candidate_finding
):
    # Fabricate a fact id string -- the check constraint fires before any
    # FK lookup would, since REJECTED must never carry promoted_to_fact_id.
    bad = CandidateFindingDecision(
        candidate_finding_id=candidate_finding.id,
        decision="REJECTED",
        promoted_to_fact_id="00000000-0000-0000-0000-000000000000",
        reason="irrelevant",
        created_by_user_id=admin_user.id,
    )
    db.session.add(bad)
    with pytest.raises(sa.exc.IntegrityError):
        db.session.commit()
    db.session.rollback()


def test_at_most_one_decision_row_per_candidate_db_enforced(
    admin_user, candidate_finding
):
    """The real race-proofing mechanism for concurrent triage calls: a
    second decision row for the same candidate must be rejected at the
    database level, not merely by application-level locking (a code
    review finding -- the application lock is released by intermediate
    commits inside promote_candidate_finding's call into
    ResearchBrainService, so the unique constraint is the actual
    guarantee)."""

    first = CandidateFindingDecision(
        candidate_finding_id=candidate_finding.id,
        decision="REJECTED",
        reason="First decision.",
        created_by_user_id=admin_user.id,
    )
    db.session.add(first)
    db.session.commit()

    second = CandidateFindingDecision(
        candidate_finding_id=candidate_finding.id,
        decision="REJECTED",
        reason="Second, conflicting decision for the same candidate.",
        created_by_user_id=admin_user.id,
    )
    db.session.add(second)
    with pytest.raises(sa.exc.IntegrityError):
        db.session.commit()
    db.session.rollback()


def test_duplicate_decision_requires_a_target(admin_user, candidate_finding):
    bad = CandidateFindingDecision(
        candidate_finding_id=candidate_finding.id,
        decision="DUPLICATE",
        duplicate_of_candidate_id=None,
        created_by_user_id=admin_user.id,
    )
    db.session.add(bad)
    with pytest.raises(sa.exc.IntegrityError):
        db.session.commit()
    db.session.rollback()
