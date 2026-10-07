"""Research Coverage & Fact Intelligence, Slice 4:
``ResearchCoverageService`` proposition-linkage tests.

Covers: creating a proposition and stage types; linking a stage to an
existing Fact (document_id must be one of the Fact's real Evidence
documents) and to a CandidateFinding (document_id must match exactly);
not-found handling for every referenced id; the "exactly one source"
validation; and get_proposition_timeline's chronological ordering across
more than one document.
"""

from __future__ import annotations

from datetime import date

import pytest

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
from app.services.document_library_service import DocumentLibraryService
from app.services.research_brain_service import ResearchBrainService
from app.services.research_coverage_service import ResearchCoverageService
from app.utils.research_errors import ResearchNotFoundError, ResearchValidationError


@pytest.fixture
def company(ticker_factory):
    ticker = ticker_factory(symbol="UNOMINDA", instrument_token=2)
    company = Company(
        ticker_id=ticker.id,
        legal_name="UNO Minda Limited",
        isin="INE405E01023",
    )
    db.session.add(company)
    db.session.commit()
    return company


def _make_document(admin_user, company, *, document_type, title):
    payload = {
        "document": {
            "document_type": document_type,
            "title": title,
            "document_date": "2026-07-07",
            "reporting_period": None,
            "publisher_name": "UNO Minda Limited",
            "original_source_url": f"https://example.in/unominda/{title}",
            "discovery_source_type": DiscoverySourceType.EXCHANGE,
            "source_access": SourceAccess.PUBLIC,
            "acquisition_method": AcquisitionMethod.MANUAL_REFERENCE,
            "distribution_status": DistributionStatus.LINK_ONLY,
            "ingestion_status": IngestionStatus.DISCOVERED,
        },
        "company_links": [{"company_id": company.id, "is_primary": True}],
    }
    return DocumentLibraryService.create_document(payload, admin_user.id)


@pytest.fixture
def other_company(ticker_factory):
    ticker = ticker_factory(symbol="OTHERCO", instrument_token=3)
    company = Company(
        ticker_id=ticker.id,
        legal_name="Other Company Limited",
        isin="INE999E01099",
    )
    db.session.add(company)
    db.session.commit()
    return company


@pytest.fixture
def reg30_document(admin_user, company):
    return _make_document(
        admin_user, company,
        document_type=DocumentType.REG30_ATTACHMENT,
        title="REG30_CAPACITY_TEST",
    )


@pytest.fixture
def concall_document(admin_user, company):
    return _make_document(
        admin_user, company,
        document_type=DocumentType.CONCALL,
        title="CONCALL_TEST",
    )


def _fact_with_evidence(admin_user, company, document, *, fact_type, value, as_of_date, text_snippet=None):
    run = ResearchBrainService.create_extraction_run(
        document_id=document.id,
        method="manual_pilot",
        extracted_by_user_id=admin_user.id,
    )
    evidence = ResearchBrainService.record_evidence(
        extraction_run_id=run.id,
        document_id=document.id,
        text_snippet=text_snippet or value,
        created_by_user_id=admin_user.id,
    )
    fact = ResearchBrainService.record_fact(
        company_id=company.id,
        fact_type=fact_type,
        value_type="TEXT",
        value=value,
        period="Q1 FY2027",
        as_of_date=as_of_date,
        evidence_ids=[evidence.id],
        created_by_user_id=admin_user.id,
    )
    return fact


def test_create_proposition_and_stage_type(admin_user, company):
    proposition = ResearchCoverageService.create_proposition(
        company_id=company.id,
        title="CSN 4W Seating Plant",
        description="JV with Tachi-S.",
        created_by_user_id=admin_user.id,
    )
    assert proposition.company_id == company.id

    stage = ResearchCoverageService.create_proposition_stage_type(
        code="COMMITTED",
        name="Committed",
        description="Budget/JV formally approved.",
    )
    assert stage.code == "COMMITTED"


def test_create_proposition_requires_a_real_company(admin_user):
    with pytest.raises(ResearchNotFoundError):
        ResearchCoverageService.create_proposition(
            company_id="00000000-0000-0000-0000-000000000000",
            title="Bogus",
            created_by_user_id=admin_user.id,
        )


def test_link_stage_to_fact_requires_a_real_evidence_document(
    admin_user, company, reg30_document, concall_document
):
    fact = _fact_with_evidence(
        admin_user, company, reg30_document,
        fact_type="capacity_expansion_test",
        value="Board approved.",
        as_of_date=date(2026, 7, 7),
    )
    proposition = ResearchCoverageService.create_proposition(
        company_id=company.id, title="Test", created_by_user_id=admin_user.id,
    )
    stage = ResearchCoverageService.create_proposition_stage_type(
        code="COMMITTED", name="Committed", description="x",
    )

    # The fact's real evidence document works.
    link = ResearchCoverageService.link_proposition_stage(
        proposition_id=proposition.id,
        stage_type_id=stage.id,
        fact_id=fact.id,
        document_id=reg30_document.id,
        as_of_date=date(2026, 7, 7),
        created_by_user_id=admin_user.id,
    )
    assert link.document_id == reg30_document.id

    # An unrelated document does not.
    with pytest.raises(ResearchValidationError):
        ResearchCoverageService.link_proposition_stage(
            proposition_id=proposition.id,
            stage_type_id=stage.id,
            fact_id=fact.id,
            document_id=concall_document.id,
            created_by_user_id=admin_user.id,
        )


def test_link_stage_to_candidate_finding_requires_its_own_document(
    admin_user, company, reg30_document, concall_document
):
    dimension = ResearchCoverageService.create_dimension(
        code="CAPACITY_CAPEX", name="Capacity & Capex", description="x",
    )
    run = ResearchBrainService.create_extraction_run(
        document_id=concall_document.id, method="manual_pilot",
        extracted_by_user_id=admin_user.id,
    )
    unit = ResearchBrainService.record_extraction_unit(
        extraction_run_id=run.id, document_id=concall_document.id,
        unit_type="PAGE", sequence_number=1, content_text="construction update",
        created_by_user_id=admin_user.id,
    )
    review_pass = ResearchCoverageService.record_coverage_review_pass(
        document_id=concall_document.id, research_dimension_id=dimension.id,
        performed_by_user_id=admin_user.id, units_considered_count=1,
        units_considered_min_seq=1, units_considered_max_seq=1,
        has_material_content=True,
    )
    candidate = ResearchCoverageService.record_candidate_finding(
        document_id=concall_document.id, research_dimension_id=dimension.id,
        source_extraction_unit_id=unit.id, review_pass_id=review_pass.id,
        raw_quote="construction on the drawing board", created_by_user_id=admin_user.id,
    )

    proposition = ResearchCoverageService.create_proposition(
        company_id=company.id, title="Test", created_by_user_id=admin_user.id,
    )
    stage = ResearchCoverageService.create_proposition_stage_type(
        code="CAPEX_DEPLOYED", name="Capex Deployed", description="x",
    )

    with pytest.raises(ResearchValidationError):
        ResearchCoverageService.link_proposition_stage(
            proposition_id=proposition.id, stage_type_id=stage.id,
            candidate_finding_id=candidate.id, document_id=reg30_document.id,
            created_by_user_id=admin_user.id,
        )

    link = ResearchCoverageService.link_proposition_stage(
        proposition_id=proposition.id, stage_type_id=stage.id,
        candidate_finding_id=candidate.id, document_id=concall_document.id,
        created_by_user_id=admin_user.id,
    )
    assert link.candidate_finding_id == candidate.id


def test_link_stage_to_fact_requires_matching_company(
    admin_user, company, other_company, reg30_document
):
    fact = _fact_with_evidence(
        admin_user, company, reg30_document,
        fact_type="capacity_expansion_test",
        value="Board approved.",
        as_of_date=date(2026, 7, 7),
    )
    # Proposition belongs to a different company than the Fact.
    proposition = ResearchCoverageService.create_proposition(
        company_id=other_company.id, title="Test", created_by_user_id=admin_user.id,
    )
    stage = ResearchCoverageService.create_proposition_stage_type(
        code="COMMITTED", name="Committed", description="x",
    )

    with pytest.raises(ResearchValidationError):
        ResearchCoverageService.link_proposition_stage(
            proposition_id=proposition.id, stage_type_id=stage.id,
            fact_id=fact.id, document_id=reg30_document.id,
            created_by_user_id=admin_user.id,
        )


def test_link_stage_to_candidate_finding_requires_matching_company(
    admin_user, company, other_company, concall_document
):
    dimension = ResearchCoverageService.create_dimension(
        code="CAPACITY_CAPEX", name="Capacity & Capex", description="x",
    )
    run = ResearchBrainService.create_extraction_run(
        document_id=concall_document.id, method="manual_pilot",
        extracted_by_user_id=admin_user.id,
    )
    unit = ResearchBrainService.record_extraction_unit(
        extraction_run_id=run.id, document_id=concall_document.id,
        unit_type="PAGE", sequence_number=1, content_text="construction update",
        created_by_user_id=admin_user.id,
    )
    review_pass = ResearchCoverageService.record_coverage_review_pass(
        document_id=concall_document.id, research_dimension_id=dimension.id,
        performed_by_user_id=admin_user.id, units_considered_count=1,
        units_considered_min_seq=1, units_considered_max_seq=1,
        has_material_content=True,
    )
    candidate = ResearchCoverageService.record_candidate_finding(
        document_id=concall_document.id, research_dimension_id=dimension.id,
        source_extraction_unit_id=unit.id, review_pass_id=review_pass.id,
        raw_quote="construction on the drawing board", created_by_user_id=admin_user.id,
    )

    # Proposition belongs to a different company than the document the
    # CandidateFinding is linked to.
    proposition = ResearchCoverageService.create_proposition(
        company_id=other_company.id, title="Test", created_by_user_id=admin_user.id,
    )
    stage = ResearchCoverageService.create_proposition_stage_type(
        code="CAPEX_DEPLOYED", name="Capex Deployed", description="x",
    )

    with pytest.raises(ResearchValidationError):
        ResearchCoverageService.link_proposition_stage(
            proposition_id=proposition.id, stage_type_id=stage.id,
            candidate_finding_id=candidate.id, document_id=concall_document.id,
            created_by_user_id=admin_user.id,
        )


def test_link_requires_exactly_one_source(
    admin_user, company, reg30_document
):
    proposition = ResearchCoverageService.create_proposition(
        company_id=company.id, title="Test", created_by_user_id=admin_user.id,
    )
    stage = ResearchCoverageService.create_proposition_stage_type(
        code="COMMITTED", name="Committed", description="x",
    )

    with pytest.raises(ResearchValidationError):
        ResearchCoverageService.link_proposition_stage(
            proposition_id=proposition.id, stage_type_id=stage.id,
            document_id=reg30_document.id, created_by_user_id=admin_user.id,
        )


def test_link_reports_not_found_for_bad_ids(admin_user, company, reg30_document):
    fact = _fact_with_evidence(
        admin_user, company, reg30_document,
        fact_type="x", value="x", as_of_date=date(2026, 7, 7),
    )
    proposition = ResearchCoverageService.create_proposition(
        company_id=company.id, title="Test", created_by_user_id=admin_user.id,
    )
    stage = ResearchCoverageService.create_proposition_stage_type(
        code="COMMITTED", name="Committed", description="x",
    )
    bogus = "00000000-0000-0000-0000-000000000000"

    with pytest.raises(ResearchNotFoundError):
        ResearchCoverageService.link_proposition_stage(
            proposition_id=bogus, stage_type_id=stage.id, fact_id=fact.id,
            document_id=reg30_document.id, created_by_user_id=admin_user.id,
        )
    with pytest.raises(ResearchNotFoundError):
        ResearchCoverageService.link_proposition_stage(
            proposition_id=proposition.id, stage_type_id=bogus, fact_id=fact.id,
            document_id=reg30_document.id, created_by_user_id=admin_user.id,
        )
    with pytest.raises(ResearchNotFoundError):
        ResearchCoverageService.link_proposition_stage(
            proposition_id=proposition.id, stage_type_id=stage.id, fact_id=bogus,
            document_id=reg30_document.id, created_by_user_id=admin_user.id,
        )
    with pytest.raises(ResearchNotFoundError):
        ResearchCoverageService.link_proposition_stage(
            proposition_id=proposition.id, stage_type_id=stage.id, fact_id=fact.id,
            document_id=bogus, created_by_user_id=admin_user.id,
        )


def test_get_proposition_timeline_orders_chronologically_across_documents(
    admin_user, company, reg30_document, concall_document
):
    committed_fact = _fact_with_evidence(
        admin_user, company, reg30_document,
        fact_type="capacity_expansion_test", value="Board approved, Rs 320 Cr.",
        as_of_date=date(2026, 7, 7),
    )
    deployed_fact = _fact_with_evidence(
        admin_user, company, concall_document,
        fact_type="capacity_expansion_construction_status",
        value="Construction on the drawing board, starting shortly.",
        as_of_date=date(2026, 8, 4),
    )

    proposition = ResearchCoverageService.create_proposition(
        company_id=company.id, title="CSN 4W Seating Plant",
        created_by_user_id=admin_user.id,
    )
    committed_stage = ResearchCoverageService.create_proposition_stage_type(
        code="COMMITTED", name="Committed", description="x",
    )
    deployed_stage = ResearchCoverageService.create_proposition_stage_type(
        code="CAPEX_DEPLOYED", name="Capex Deployed", description="x",
    )

    # Link out of chronological order -- the timeline must still sort correctly.
    ResearchCoverageService.link_proposition_stage(
        proposition_id=proposition.id, stage_type_id=deployed_stage.id,
        fact_id=deployed_fact.id, document_id=concall_document.id,
        as_of_date=date(2026, 8, 4), created_by_user_id=admin_user.id,
    )
    ResearchCoverageService.link_proposition_stage(
        proposition_id=proposition.id, stage_type_id=committed_stage.id,
        fact_id=committed_fact.id, document_id=reg30_document.id,
        as_of_date=date(2026, 7, 7), created_by_user_id=admin_user.id,
    )

    timeline = ResearchCoverageService.get_proposition_timeline(proposition.id)
    assert len(timeline) == 2
    assert timeline[0]["stage_code"] == "COMMITTED"
    assert timeline[0]["as_of_date"] == date(2026, 7, 7)
    assert timeline[1]["stage_code"] == "CAPEX_DEPLOYED"
    assert timeline[1]["as_of_date"] == date(2026, 8, 4)
    assert {s["document_id"] for s in timeline} == {
        reg30_document.id,
        concall_document.id,
    }
    assert {s["document_type"] for s in timeline} == {"REG30_ATTACHMENT", "CONCALL"}
