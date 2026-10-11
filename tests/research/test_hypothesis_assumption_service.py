"""Phase 3 Slice D: ``HypothesisAssumptionService`` acceptance tests.

Covers: hypothesis revision-chain integrity (unique revision numbers, no
branching, single genesis, deterministic "current" query, concurrent-race
rejection -- the same proof shape Slice C established for
``CycleAssessment``), citation validation (exactly one source, must
belong to the same company), immutability, the five-way "exactly one
provenance" constraint on ``ForecastAssumption``, and the P3-D atomicity
test: one scenario's assumption set built as two separate rows (one
cycle-side, one transformation-side), each independently queryable.
"""

from __future__ import annotations

from datetime import date

import pytest
import sqlalchemy as sa

from app import db
from app.models import Company
from app.models.cycle_intelligence import CompanyExposure
from app.models.document import (
    AcquisitionMethod,
    DiscoverySourceType,
    DistributionStatus,
    DocumentType,
    IngestionStatus,
    SourceAccess,
)
from app.models.investment_hypothesis import (
    ForecastAssumption,
    InvestmentHypothesis,
    InvestmentHypothesisCitation,
)
from app.models.research_types import ExposureDirection
from app.services.cycle_intelligence_service import CycleIntelligenceService
from app.services.document_library_service import DocumentLibraryService
from app.services.hypothesis_assumption_service import HypothesisAssumptionService
from app.services.research_brain_service import ResearchBrainService
from app.services.research_coverage_service import ResearchCoverageService
from app.utils.research_errors import ResearchNotFoundError, ResearchValidationError


@pytest.fixture
def company(ticker_factory):
    ticker = ticker_factory(symbol="HYPOTHESIS_TEST", instrument_token=6)
    company = Company(
        ticker_id=ticker.id, legal_name="Hypothesis Test Co Limited",
        isin="INE0LOJ04003",
    )
    db.session.add(company)
    db.session.commit()
    return company


@pytest.fixture
def other_company(ticker_factory):
    ticker = ticker_factory(symbol="HYPOTHESIS_OTHER", instrument_token=7)
    company = Company(
        ticker_id=ticker.id, legal_name="Other Co Limited", isin="INE0LOJ05004",
    )
    db.session.add(company)
    db.session.commit()
    return company


@pytest.fixture
def results_document(admin_user, company):
    payload = {
        "document": {
            "document_type": DocumentType.QUARTERLY_RESULTS,
            "title": "HYPOTHESIS_TEST_RESULTS",
            "document_date": "2026-08-04",
            "publisher_name": "Hypothesis Test Co Limited",
            "original_source_url": "https://example.in/hypothesis-test/results",
            "discovery_source_type": DiscoverySourceType.OFFICIAL_SITE,
            "source_access": SourceAccess.PUBLIC,
            "acquisition_method": AcquisitionMethod.MANUAL_REFERENCE,
            "distribution_status": DistributionStatus.LINK_ONLY,
            "ingestion_status": IngestionStatus.DISCOVERED,
        },
        "company_links": [{"company_id": company.id, "is_primary": True}],
    }
    return DocumentLibraryService.create_document(payload, admin_user.id)


def _evidence(*, admin_user, document, text="Capacity utilization ramped to 62% in Q1 FY27."):
    run = ResearchBrainService.create_extraction_run(
        document_id=document.id, method="manual_pilot", extracted_by_user_id=admin_user.id
    )
    return ResearchBrainService.record_evidence(
        extraction_run_id=run.id, document_id=document.id,
        text_snippet=text, created_by_user_id=admin_user.id,
    )


def _fact(*, admin_user, document, company, fact_type="fin.raw.revenue", value="592.32"):
    evidence = _evidence(admin_user=admin_user, document=document)
    return ResearchBrainService.record_fact(
        company_id=company.id, fact_type=fact_type, value_type="NUMERIC",
        value=value, period="FY2027Q1", as_of_date=date(2026, 6, 30),
        evidence_ids=[evidence.id], created_by_user_id=admin_user.id,
    )


def _company_exposure(*, admin_user, document, company, cycle_tag="PVC_RESIN"):
    evidence = _evidence(admin_user=admin_user, document=document, text="PVC spread normalizing.")
    observation = CycleIntelligenceService.record_cycle_observation(
        cycle_tag=cycle_tag, observed_on=date(2026, 9, 30),
        description="PVC-ACH spread widened.", created_by_user_id=admin_user.id,
        evidence_id=evidence.id,
    )
    assessment = CycleIntelligenceService.create_cycle_assessment(
        cycle_tag=cycle_tag, scenario="BASE", narrative="Normalizing.",
        observation_ids=[observation.id], created_by_user_id=admin_user.id,
    )
    return CycleIntelligenceService.record_company_exposure(
        company_id=company.id, cycle_assessment_id=assessment.id,
        exposure_direction=ExposureDirection.BENEFICIARY,
        rationale="Net beneficiary of spread normalization.",
        created_by_user_id=admin_user.id,
    )


def _transformation_proposition(*, admin_user, document, company):
    proposition = ResearchCoverageService.create_proposition(
        company_id=company.id, title="Capacity ramp -- new line",
        created_by_user_id=admin_user.id,
    )
    stage_type = ResearchCoverageService.create_proposition_stage_type(
        code="CAPACITY_COMMISSIONED", name="Capacity commissioned",
        description="New capacity commissioned.",
    )
    fact = _fact(
        admin_user=admin_user, document=document, company=company,
        fact_type="fin.raw.capacity_utilization_pct", value="62.0",
    )
    ResearchCoverageService.link_proposition_stage(
        proposition_id=proposition.id, stage_type_id=stage_type.id,
        document_id=document.id, created_by_user_id=admin_user.id, fact_id=fact.id,
    )
    return proposition


def test_genesis_and_supersession_chain(admin_user, results_document, company):
    fact = _fact(admin_user=admin_user, document=results_document, company=company)
    v1 = HypothesisAssumptionService.create_investment_hypothesis(
        company_id=company.id, title="Margin recovery thesis",
        thesis_narrative="Margins recover as spreads normalize.",
        confidence="MEDIUM", created_by_user_id=admin_user.id,
        citations=[{"fact_id": fact.id}],
    )
    assert v1.revision_number == 1
    assert v1.supersedes_revision_id is None

    v2 = HypothesisAssumptionService.create_investment_hypothesis(
        company_id=company.id, title="Margin recovery thesis (updated)",
        thesis_narrative="Margins recovering faster than expected.",
        confidence="HIGH", created_by_user_id=admin_user.id,
        citations=[{"fact_id": fact.id}],
        supersedes_revision_id=v1.id, change_reason="New data",
    )
    assert v2.revision_number == 2

    current = HypothesisAssumptionService.get_current_investment_hypothesis(company.id)
    assert current.id == v2.id
    history = HypothesisAssumptionService.get_investment_hypothesis_history(company.id)
    assert [row.id for row in history] == [v1.id, v2.id]


def test_supersession_requires_change_reason(admin_user, results_document, company):
    fact = _fact(admin_user=admin_user, document=results_document, company=company)
    v1 = HypothesisAssumptionService.create_investment_hypothesis(
        company_id=company.id, title="t", thesis_narrative="n", confidence="LOW",
        created_by_user_id=admin_user.id, citations=[{"fact_id": fact.id}],
    )
    with pytest.raises(ResearchValidationError):
        HypothesisAssumptionService.create_investment_hypothesis(
            company_id=company.id, title="t2", thesis_narrative="n2", confidence="LOW",
            created_by_user_id=admin_user.id, citations=[{"fact_id": fact.id}],
            supersedes_revision_id=v1.id,
        )


def test_concurrent_supersession_is_rejected(admin_user, results_document, company):
    fact = _fact(admin_user=admin_user, document=results_document, company=company)
    v1 = HypothesisAssumptionService.create_investment_hypothesis(
        company_id=company.id, title="t", thesis_narrative="n", confidence="LOW",
        created_by_user_id=admin_user.id, citations=[{"fact_id": fact.id}],
    )
    HypothesisAssumptionService.create_investment_hypothesis(
        company_id=company.id, title="winner", thesis_narrative="n", confidence="LOW",
        created_by_user_id=admin_user.id, citations=[{"fact_id": fact.id}],
        supersedes_revision_id=v1.id, change_reason="first writer",
    )
    with pytest.raises(ResearchValidationError):
        HypothesisAssumptionService.create_investment_hypothesis(
            company_id=company.id, title="loser", thesis_narrative="n", confidence="LOW",
            created_by_user_id=admin_user.id, citations=[{"fact_id": fact.id}],
            supersedes_revision_id=v1.id, change_reason="second writer",
        )
    assert not db.session().in_transaction()


def test_duplicate_genesis_rejected(admin_user, results_document, company):
    fact = _fact(admin_user=admin_user, document=results_document, company=company)
    HypothesisAssumptionService.create_investment_hypothesis(
        company_id=company.id, title="genesis 1", thesis_narrative="n", confidence="LOW",
        created_by_user_id=admin_user.id, citations=[{"fact_id": fact.id}],
    )
    with pytest.raises(ResearchValidationError):
        HypothesisAssumptionService.create_investment_hypothesis(
            company_id=company.id, title="genesis 2", thesis_narrative="n", confidence="LOW",
            created_by_user_id=admin_user.id, citations=[{"fact_id": fact.id}],
        )


def test_branching_supersession_rejected_at_database_level(
    admin_user, results_document, company
):
    fact = _fact(admin_user=admin_user, document=results_document, company=company)
    v1 = HypothesisAssumptionService.create_investment_hypothesis(
        company_id=company.id, title="t", thesis_narrative="n", confidence="LOW",
        created_by_user_id=admin_user.id, citations=[{"fact_id": fact.id}],
    )
    db.session.add(
        InvestmentHypothesis(
            company_id=company.id, title="branch A", thesis_narrative="n",
            confidence="LOW", revision_number=2, supersedes_revision_id=v1.id,
            created_by_user_id=admin_user.id,
        )
    )
    db.session.commit()
    db.session.add(
        InvestmentHypothesis(
            company_id=company.id, title="branch B", thesis_narrative="n",
            confidence="LOW", revision_number=3, supersedes_revision_id=v1.id,
            created_by_user_id=admin_user.id,
        )
    )
    with pytest.raises(sa.exc.IntegrityError):
        db.session.commit()
    db.session.rollback()


def test_citation_must_belong_to_same_company(
    admin_user, results_document, company, other_company
):
    foreign_fact = _fact(
        admin_user=admin_user, document=results_document, company=other_company
    )
    with pytest.raises(ResearchValidationError):
        HypothesisAssumptionService.create_investment_hypothesis(
            company_id=company.id, title="t", thesis_narrative="n", confidence="LOW",
            created_by_user_id=admin_user.id, citations=[{"fact_id": foreign_fact.id}],
        )


def test_citation_exactly_one_source_enforced(admin_user, results_document, company):
    fact = _fact(admin_user=admin_user, document=results_document, company=company)
    exposure = _company_exposure(
        admin_user=admin_user, document=results_document, company=company
    )
    with pytest.raises(ResearchValidationError):
        HypothesisAssumptionService.create_investment_hypothesis(
            company_id=company.id, title="t", thesis_narrative="n", confidence="LOW",
            created_by_user_id=admin_user.id,
            citations=[{"fact_id": fact.id, "company_exposure_id": exposure.id}],
        )
    with pytest.raises(ResearchValidationError):
        HypothesisAssumptionService.create_investment_hypothesis(
            company_id=company.id, title="t", thesis_narrative="n", confidence="LOW",
            created_by_user_id=admin_user.id, citations=[],
        )


def test_forecast_assumption_exactly_one_provenance_enforced(
    admin_user, results_document, company
):
    fact = _fact(admin_user=admin_user, document=results_document, company=company)
    exposure = _company_exposure(
        admin_user=admin_user, document=results_document, company=company
    )
    hypothesis = HypothesisAssumptionService.create_investment_hypothesis(
        company_id=company.id, title="t", thesis_narrative="n", confidence="LOW",
        created_by_user_id=admin_user.id, citations=[{"fact_id": fact.id}],
    )

    with pytest.raises(ResearchValidationError):
        HypothesisAssumptionService.record_forecast_assumption(
            investment_hypothesis_id=hypothesis.id, scenario="BASE",
            assumption_statement="no provenance", created_by_user_id=admin_user.id,
        )
    with pytest.raises(ResearchValidationError):
        HypothesisAssumptionService.record_forecast_assumption(
            investment_hypothesis_id=hypothesis.id, scenario="BASE",
            assumption_statement="two sources", created_by_user_id=admin_user.id,
            fact_id=fact.id, cycle_exposure_id=exposure.id,
        )

    assumption = HypothesisAssumptionService.record_forecast_assumption(
        investment_hypothesis_id=hypothesis.id, scenario="BASE",
        assumption_statement="one source", created_by_user_id=admin_user.id,
        fact_id=fact.id,
    )
    assert assumption.fact_id == fact.id


def test_immutability_enforced(admin_user, results_document, company):
    fact = _fact(admin_user=admin_user, document=results_document, company=company)
    hypothesis = HypothesisAssumptionService.create_investment_hypothesis(
        company_id=company.id, title="t", thesis_narrative="n", confidence="LOW",
        created_by_user_id=admin_user.id, citations=[{"fact_id": fact.id}],
    )
    assumption = HypothesisAssumptionService.record_forecast_assumption(
        investment_hypothesis_id=hypothesis.id, scenario="BASE",
        assumption_statement="s", created_by_user_id=admin_user.id, fact_id=fact.id,
    )
    citation = db.session.scalar(
        sa.select(InvestmentHypothesisCitation).where(
            InvestmentHypothesisCitation.investment_hypothesis_id == hypothesis.id
        )
    )

    hypothesis.title = "changed"
    with pytest.raises(sa.exc.InvalidRequestError):
        db.session.commit()
    db.session.rollback()

    assumption.assumption_statement = "changed"
    with pytest.raises(sa.exc.InvalidRequestError):
        db.session.commit()
    db.session.rollback()

    citation.fact_id = citation.fact_id
    with pytest.raises(sa.exc.InvalidRequestError):
        db.session.commit()
    db.session.rollback()

    for row in (hypothesis, assumption, citation):
        db.session.delete(row)
        with pytest.raises(sa.exc.InvalidRequestError):
            db.session.commit()
        db.session.rollback()


def test_atomicity_rule_two_driver_scenario_is_two_rows(
    admin_user, results_document, company
):
    """The load-bearing P3-D acceptance test: BULL's assumption set built
    as exactly two ForecastAssumption rows -- one citing cycle_exposure_id
    (the PVC CompanyExposure), one citing research_proposition_id (the
    capacity-ramp ResearchProposition) -- each with exactly one populated
    provenance field, the two fields differing, and both independently
    queryable as disjoint cycle-driver/transformation-driver sets."""

    exposure = _company_exposure(
        admin_user=admin_user, document=results_document, company=company
    )
    proposition = _transformation_proposition(
        admin_user=admin_user, document=results_document, company=company
    )
    hypothesis = HypothesisAssumptionService.create_investment_hypothesis(
        company_id=company.id, title="PVC normalization + capacity ramp",
        thesis_narrative=(
            "Two independent drivers: external PVC-ACH spread normalization "
            "and the company's own new-line capacity ramp."
        ),
        confidence="HIGH", created_by_user_id=admin_user.id,
        citations=[
            {"company_exposure_id": exposure.id},
            {"research_proposition_id": proposition.id},
        ],
    )

    cycle_assumption = HypothesisAssumptionService.record_forecast_assumption(
        investment_hypothesis_id=hypothesis.id, scenario="BULL",
        assumption_statement="PVC-ACH spread normalizes to mid-cycle levels.",
        created_by_user_id=admin_user.id, cycle_exposure_id=exposure.id,
        metric_slug="gross_margin_pct", assumed_value="55.0", assumed_unit="PERCENT",
    )
    transformation_assumption = HypothesisAssumptionService.record_forecast_assumption(
        investment_hypothesis_id=hypothesis.id, scenario="BULL",
        assumption_statement="New line ramps to 85% utilization.",
        created_by_user_id=admin_user.id, research_proposition_id=proposition.id,
        metric_slug="capacity_utilization_pct", assumed_value="85.0", assumed_unit="PERCENT",
    )

    # (a) each row has exactly one non-null provenance field.
    def populated_fields(row: ForecastAssumption) -> list[str]:
        return [
            field
            for field in (
                "cycle_exposure_id", "research_proposition_id", "fact_id",
                "candidate_finding_id", "evidence_id",
            )
            if getattr(row, field) is not None
        ]

    assert populated_fields(cycle_assumption) == ["cycle_exposure_id"]
    assert populated_fields(transformation_assumption) == ["research_proposition_id"]

    # (b) the two rows' populated provenance field differs.
    assert populated_fields(cycle_assumption) != populated_fields(transformation_assumption)

    # (c) queryable as two disjoint sets, no narrative parsing.
    cycle_driver_rows = HypothesisAssumptionService.get_cycle_driver_assumptions(
        hypothesis.id, "BULL"
    )
    transformation_driver_rows = HypothesisAssumptionService.get_transformation_driver_assumptions(
        hypothesis.id, "BULL"
    )
    assert [row.id for row in cycle_driver_rows] == [cycle_assumption.id]
    assert [row.id for row in transformation_driver_rows] == [transformation_assumption.id]
    assert set(row.id for row in cycle_driver_rows).isdisjoint(
        row.id for row in transformation_driver_rows
    )
