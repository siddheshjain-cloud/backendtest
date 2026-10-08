"""Phase 3 Slice C: ``CycleIntelligenceService`` acceptance tests.

Covers: genesis/supersession chain integrity (unique revision numbers, no
branching, single genesis, deterministic "current" query), sibling-scenario
independence, immutability, the three-way "exactly one source" constraint
on ``CycleObservation``, exposure pinned to an exact assessment revision,
provenance integrity (external references must be identifiable;
observations cited by an assessment must belong to that cycle), and the
citation-separation rule between Cycle Intelligence and company
Transformation (``ResearchProposition``).
"""

from __future__ import annotations

from datetime import date

import pytest
import sqlalchemy as sa

from app import db
from app.models import Company
from app.models.cycle_intelligence import (
    CompanyExposure,
    CycleAssessment,
    CycleAssessmentObservation,
    CycleObservation,
)
from app.models.document import (
    AcquisitionMethod,
    DiscoverySourceType,
    DistributionStatus,
    DocumentType,
    IngestionStatus,
    SourceAccess,
)
from app.models.research_types import ExposureDirection
from app.services.cycle_intelligence_service import CycleIntelligenceService
from app.services.document_library_service import DocumentLibraryService
from app.services.research_brain_service import ResearchBrainService
from app.services.research_coverage_service import ResearchCoverageService
from app.utils.research_errors import ResearchNotFoundError, ResearchValidationError


@pytest.fixture
def company(ticker_factory):
    ticker = ticker_factory(symbol="CYCLE_TEST", instrument_token=5)
    company = Company(
        ticker_id=ticker.id, legal_name="Cycle Test Co Limited", isin="INE0LOJ03002",
    )
    db.session.add(company)
    db.session.commit()
    return company


@pytest.fixture
def results_document(admin_user, company):
    payload = {
        "document": {
            "document_type": DocumentType.QUARTERLY_RESULTS,
            "title": "CYCLE_TEST_RESULTS",
            "document_date": "2026-08-04",
            "publisher_name": "Cycle Test Co Limited",
            "original_source_url": "https://example.in/cycle-test/results",
            "discovery_source_type": DiscoverySourceType.OFFICIAL_SITE,
            "source_access": SourceAccess.PUBLIC,
            "acquisition_method": AcquisitionMethod.MANUAL_REFERENCE,
            "distribution_status": DistributionStatus.LINK_ONLY,
            "ingestion_status": IngestionStatus.DISCOVERED,
        },
        "company_links": [{"company_id": company.id, "is_primary": True}],
    }
    return DocumentLibraryService.create_document(payload, admin_user.id)


def _evidence(*, admin_user, document, text="PVC-ACH spread widened to $350/t in Sep 2026."):
    run = ResearchBrainService.create_extraction_run(
        document_id=document.id, method="manual_pilot", extracted_by_user_id=admin_user.id
    )
    return ResearchBrainService.record_evidence(
        extraction_run_id=run.id, document_id=document.id,
        text_snippet=text, created_by_user_id=admin_user.id,
    )


def _observation(*, admin_user, document, cycle_tag="PVC_RESIN", evidence=None):
    evidence = evidence or _evidence(admin_user=admin_user, document=document)
    return CycleIntelligenceService.record_cycle_observation(
        cycle_tag=cycle_tag, observed_on=date(2026, 9, 30),
        description="PVC-ACH spread widened quarter-on-quarter.",
        created_by_user_id=admin_user.id, evidence_id=evidence.id,
    )


def test_genesis_and_supersession_chain(admin_user, results_document):
    observation = _observation(admin_user=admin_user, document=results_document)
    v1 = CycleIntelligenceService.create_cycle_assessment(
        cycle_tag="PVC_RESIN", scenario="BASE",
        narrative="Spread normalization underway.",
        observation_ids=[observation.id], created_by_user_id=admin_user.id,
    )
    assert v1.revision_number == 1
    assert v1.supersedes_revision_id is None

    v2 = CycleIntelligenceService.create_cycle_assessment(
        cycle_tag="PVC_RESIN", scenario="BASE",
        narrative="Spread normalization further along than expected.",
        observation_ids=[observation.id], created_by_user_id=admin_user.id,
        supersedes_revision_id=v1.id, change_reason="New data point",
    )
    assert v2.revision_number == 2
    assert v2.supersedes_revision_id == v1.id

    current = CycleIntelligenceService.get_current_cycle_assessment("PVC_RESIN", "BASE")
    assert current.id == v2.id

    history = CycleIntelligenceService.get_cycle_assessment_history("PVC_RESIN", "BASE")
    assert [row.id for row in history] == [v1.id, v2.id]


def test_supersession_requires_change_reason(admin_user, results_document):
    observation = _observation(admin_user=admin_user, document=results_document)
    v1 = CycleIntelligenceService.create_cycle_assessment(
        cycle_tag="PVC_RESIN", scenario="BASE", narrative="v1",
        observation_ids=[observation.id], created_by_user_id=admin_user.id,
    )
    with pytest.raises(ResearchValidationError):
        CycleIntelligenceService.create_cycle_assessment(
            cycle_tag="PVC_RESIN", scenario="BASE", narrative="v2",
            observation_ids=[observation.id], created_by_user_id=admin_user.id,
            supersedes_revision_id=v1.id,
        )


def test_sibling_scenarios_coexist_independently(admin_user, results_document):
    observation = _observation(admin_user=admin_user, document=results_document)
    bull = CycleIntelligenceService.create_cycle_assessment(
        cycle_tag="PVC_RESIN", scenario="BULL", narrative="Bull case",
        observation_ids=[observation.id], created_by_user_id=admin_user.id,
    )
    base = CycleIntelligenceService.create_cycle_assessment(
        cycle_tag="PVC_RESIN", scenario="BASE", narrative="Base case",
        observation_ids=[observation.id], created_by_user_id=admin_user.id,
    )
    bear = CycleIntelligenceService.create_cycle_assessment(
        cycle_tag="PVC_RESIN", scenario="BEAR", narrative="Bear case",
        observation_ids=[observation.id], created_by_user_id=admin_user.id,
    )
    assert bull.revision_number == base.revision_number == bear.revision_number == 1

    bull_v2 = CycleIntelligenceService.create_cycle_assessment(
        cycle_tag="PVC_RESIN", scenario="BULL", narrative="Bull case v2",
        observation_ids=[observation.id], created_by_user_id=admin_user.id,
        supersedes_revision_id=bull.id, change_reason="Update",
    )
    assert CycleIntelligenceService.get_current_cycle_assessment("PVC_RESIN", "BULL").id == bull_v2.id
    assert CycleIntelligenceService.get_current_cycle_assessment("PVC_RESIN", "BASE").id == base.id
    assert CycleIntelligenceService.get_current_cycle_assessment("PVC_RESIN", "BEAR").id == bear.id


def test_concurrent_supersession_is_rejected(admin_user, results_document):
    """Two callers racing to extend the same chain from the same
    predecessor collide on the real database unique constraint, not just
    an application-level check -- the second writer's commit fails and is
    translated into a clean domain error, never a second "current" row."""

    observation = _observation(admin_user=admin_user, document=results_document)
    v1 = CycleIntelligenceService.create_cycle_assessment(
        cycle_tag="PVC_RESIN", scenario="BASE", narrative="v1",
        observation_ids=[observation.id], created_by_user_id=admin_user.id,
    )
    CycleIntelligenceService.create_cycle_assessment(
        cycle_tag="PVC_RESIN", scenario="BASE", narrative="Winning writer",
        observation_ids=[observation.id], created_by_user_id=admin_user.id,
        supersedes_revision_id=v1.id, change_reason="First writer",
    )

    with pytest.raises(ResearchValidationError):
        CycleIntelligenceService.create_cycle_assessment(
            cycle_tag="PVC_RESIN", scenario="BASE", narrative="Losing writer",
            observation_ids=[observation.id], created_by_user_id=admin_user.id,
            supersedes_revision_id=v1.id, change_reason="Second writer",
        )
    assert not db.session().in_transaction()

    revision_numbers = db.session.scalars(
        sa.select(CycleAssessment.revision_number)
        .where(CycleAssessment.cycle_tag == "PVC_RESIN", CycleAssessment.scenario == "BASE")
        .order_by(CycleAssessment.revision_number)
    ).all()
    assert revision_numbers == [1, 2]


def test_duplicate_genesis_rejected(admin_user, results_document):
    observation = _observation(admin_user=admin_user, document=results_document)
    CycleIntelligenceService.create_cycle_assessment(
        cycle_tag="PVC_RESIN", scenario="BASE", narrative="genesis 1",
        observation_ids=[observation.id], created_by_user_id=admin_user.id,
    )
    with pytest.raises(ResearchValidationError):
        CycleIntelligenceService.create_cycle_assessment(
            cycle_tag="PVC_RESIN", scenario="BASE", narrative="genesis 2",
            observation_ids=[observation.id], created_by_user_id=admin_user.id,
        )
    assert not db.session().in_transaction()


def test_branching_supersession_rejected_at_database_level(admin_user, results_document):
    """Bypasses the service layer entirely to prove the constraint lives
    in the schema, not only in application code."""

    observation = _observation(admin_user=admin_user, document=results_document)
    v1 = CycleIntelligenceService.create_cycle_assessment(
        cycle_tag="PVC_RESIN", scenario="BASE", narrative="v1",
        observation_ids=[observation.id], created_by_user_id=admin_user.id,
    )
    db.session.add(
        CycleAssessment(
            cycle_tag="PVC_RESIN", scenario="BASE", revision_number=2,
            supersedes_revision_id=v1.id, narrative="branch A",
            created_by_user_id=admin_user.id,
        )
    )
    db.session.commit()

    db.session.add(
        CycleAssessment(
            cycle_tag="PVC_RESIN", scenario="BASE", revision_number=3,
            supersedes_revision_id=v1.id, narrative="branch B (rejected)",
            created_by_user_id=admin_user.id,
        )
    )
    with pytest.raises(sa.exc.IntegrityError):
        db.session.commit()
    db.session.rollback()


def test_cycle_observation_exactly_one_source_enforced(admin_user, results_document):
    evidence = _evidence(admin_user=admin_user, document=results_document)

    with pytest.raises(ResearchValidationError):
        CycleIntelligenceService.record_cycle_observation(
            cycle_tag="PVC_RESIN", observed_on=date(2026, 9, 30),
            description="no source", created_by_user_id=admin_user.id,
        )
    with pytest.raises(ResearchValidationError):
        CycleIntelligenceService.record_cycle_observation(
            cycle_tag="PVC_RESIN", observed_on=date(2026, 9, 30),
            description="two sources", created_by_user_id=admin_user.id,
            evidence_id=evidence.id, document_id=results_document.id,
        )

    observation = CycleIntelligenceService.record_cycle_observation(
        cycle_tag="PVC_RESIN", observed_on=date(2026, 9, 30),
        description="one source", created_by_user_id=admin_user.id,
        evidence_id=evidence.id,
    )
    assert observation.evidence_id == evidence.id


def test_external_reference_must_be_identifiable(admin_user):
    with pytest.raises(ResearchValidationError):
        CycleIntelligenceService.record_cycle_observation(
            cycle_tag="PVC_RESIN", observed_on=date(2026, 9, 30),
            description="blank external reference",
            created_by_user_id=admin_user.id, external_reference="   ",
        )

    observation = CycleIntelligenceService.record_cycle_observation(
        cycle_tag="PVC_RESIN", observed_on=date(2026, 9, 30),
        description="named external reference",
        created_by_user_id=admin_user.id,
        external_reference="Bloomberg PVC-ACH spread series, 2026-10-01",
    )
    assert observation.external_reference == "Bloomberg PVC-ACH spread series, 2026-10-01"


def test_observation_must_belong_to_assessed_cycle(admin_user, results_document):
    pvc_observation = _observation(
        admin_user=admin_user, document=results_document, cycle_tag="PVC_RESIN"
    )
    caustic_observation = _observation(
        admin_user=admin_user, document=results_document, cycle_tag="CAUSTIC_SODA"
    )

    with pytest.raises(ResearchValidationError):
        CycleIntelligenceService.create_cycle_assessment(
            cycle_tag="PVC_RESIN", scenario="BASE", narrative="mismatched citation",
            observation_ids=[pvc_observation.id, caustic_observation.id],
            created_by_user_id=admin_user.id,
        )


def test_exposure_pins_to_exact_revision_not_current(admin_user, results_document, company):
    observation = _observation(admin_user=admin_user, document=results_document)
    v1 = CycleIntelligenceService.create_cycle_assessment(
        cycle_tag="PVC_RESIN", scenario="BASE", narrative="v1",
        observation_ids=[observation.id], created_by_user_id=admin_user.id,
    )
    exposure = CycleIntelligenceService.record_company_exposure(
        company_id=company.id, cycle_assessment_id=v1.id,
        exposure_direction=ExposureDirection.BENEFICIARY,
        rationale="Net PVC producer benefits from spread normalization.",
        created_by_user_id=admin_user.id,
    )

    CycleIntelligenceService.create_cycle_assessment(
        cycle_tag="PVC_RESIN", scenario="BASE", narrative="v2",
        observation_ids=[observation.id], created_by_user_id=admin_user.id,
        supersedes_revision_id=v1.id, change_reason="Update",
    )

    refreshed = db.session.get(CompanyExposure, exposure.id)
    assert refreshed.cycle_assessment_id == v1.id
    assert CycleIntelligenceService.get_current_cycle_assessment("PVC_RESIN", "BASE").id != v1.id


def test_immutability_enforced_on_all_four_tables(admin_user, results_document, company):
    observation = _observation(admin_user=admin_user, document=results_document)
    assessment = CycleIntelligenceService.create_cycle_assessment(
        cycle_tag="PVC_RESIN", scenario="BASE", narrative="v1",
        observation_ids=[observation.id], created_by_user_id=admin_user.id,
    )
    exposure = CycleIntelligenceService.record_company_exposure(
        company_id=company.id, cycle_assessment_id=assessment.id,
        exposure_direction=ExposureDirection.BENEFICIARY,
        rationale="rationale", created_by_user_id=admin_user.id,
    )
    link = db.session.scalar(
        sa.select(CycleAssessmentObservation).where(
            CycleAssessmentObservation.cycle_assessment_id == assessment.id
        )
    )

    for row in (observation, assessment, exposure):
        row.created_by_user_id = admin_user.id
        with pytest.raises(sa.exc.InvalidRequestError):
            db.session.commit()
        db.session.rollback()

    link.cycle_observation_id = observation.id
    with pytest.raises(sa.exc.InvalidRequestError):
        db.session.commit()
    db.session.rollback()

    for row in (observation, assessment, exposure, link):
        db.session.delete(row)
        with pytest.raises(sa.exc.InvalidRequestError):
            db.session.commit()
        db.session.rollback()


def test_cycle_and_transformation_citation_separation(
    admin_user, results_document, company
):
    """The load-bearing acceptance test: Chemplast's own canonical
    two-driver case (PVC normalization = cycle, capacity ramp =
    transformation) must be representable, and independently
    retrievable, as two disjoint evidence chains -- never one blended
    rationale, and never requiring narrative parsing to separate them."""

    cycle_observation = _observation(
        admin_user=admin_user, document=results_document, cycle_tag="PVC_RESIN"
    )
    assessment = CycleIntelligenceService.create_cycle_assessment(
        cycle_tag="PVC_RESIN", scenario="BASE",
        narrative="PVC-ACH spread normalizing toward mid-cycle levels.",
        observation_ids=[cycle_observation.id], created_by_user_id=admin_user.id,
    )
    exposure = CycleIntelligenceService.record_company_exposure(
        company_id=company.id, cycle_assessment_id=assessment.id,
        exposure_direction=ExposureDirection.BENEFICIARY,
        rationale="Benefits purely from the external PVC-ACH spread cycle.",
        created_by_user_id=admin_user.id,
    )

    proposition = ResearchCoverageService.create_proposition(
        company_id=company.id, title="Capacity ramp -- new PVC line",
        created_by_user_id=admin_user.id,
    )
    stage_type = ResearchCoverageService.create_proposition_stage_type(
        code="CAPACITY_COMMISSIONED", name="Capacity commissioned",
        description="New capacity has been commissioned.",
    )
    transformation_evidence = _evidence(
        admin_user=admin_user, document=results_document,
        text="New PVC line commissioned and ramping utilization.",
    )
    transformation_fact = ResearchBrainService.record_fact(
        company_id=company.id, fact_type="fin.raw.capacity_utilization_pct",
        value_type="NUMERIC", value="62.0", period="FY2027Q1",
        as_of_date=date(2026, 6, 30), evidence_ids=[transformation_evidence.id],
        created_by_user_id=admin_user.id,
    )
    ResearchCoverageService.link_proposition_stage(
        proposition_id=proposition.id, stage_type_id=stage_type.id,
        document_id=results_document.id, created_by_user_id=admin_user.id,
        fact_id=transformation_fact.id,
    )

    cycle_chain = CycleIntelligenceService.get_cycle_evidence_chain(exposure.id)
    transformation_chain = ResearchCoverageService.get_proposition_timeline(proposition.id)

    # (a) distinct id spaces -- no shared row between the two mechanisms.
    cycle_ids = {cycle_chain["company_exposure_id"], cycle_chain["cycle_assessment_id"]}
    cycle_ids.update(obs["cycle_observation_id"] for obs in cycle_chain["observations"])
    transformation_ids = {proposition.id, stage_type.id, transformation_fact.id}
    assert cycle_ids.isdisjoint(transformation_ids)

    # (b) each chain is independently resolvable without the other's ids.
    assert cycle_chain["observations"][0]["evidence_id"] == cycle_observation.evidence_id
    assert transformation_chain[0]["fact_id"] == transformation_fact.id

    # (c) neither chain's own text needs to be parsed to tell them apart --
    # the calling mechanism (CycleIntelligenceService vs.
    # ResearchCoverageService) and the id namespace are the discriminator.
    assert "cycle_assessment_id" in cycle_chain
    assert all("cycle_assessment_id" not in stage for stage in transformation_chain)
