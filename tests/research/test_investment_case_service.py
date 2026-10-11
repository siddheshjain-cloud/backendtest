"""Phase 3 Slice G: ``InvestmentCaseService`` acceptance tests.

Covers: ``InvestmentCase`` revision-chain integrity (unique revision
numbers, no branching, single genesis, deterministic "current" query,
concurrent-race rejection -- the same proof shape Slices C/D established
for ``CycleAssessment``/``InvestmentHypothesis``); company-membership
validation for ``research_revision_id``/``investment_hypothesis_id``/
cited ``ForecastRevision``/``ValuationRevision`` ids; the
``InvestmentCaseForecast``/``InvestmentCaseValuation`` link tables;
immutability (including the one deliberate exception --
``InvestmentCaseKillSwitch.triggered_at`` may be set exactly once);
``InvestmentCaseGateResult``'s five-way "exactly one provenance"
constraint and its own per-gate revision chain; and
``get_scenario_return_spread``'s live, non-fabricating Bull/Base/Bear
upside/downside computation with ``MID_CYCLE`` excluded.

No real Chemplast numeric demonstration is attempted here -- the Slice
E/F checkpoint already recorded that no real, numeric Chemplast
``ForecastRevision``/``ValuationRevision`` exists yet (the cited
``ForecastAssumption`` rows carry no ``assumed_value``), a carried-
forward acceptance item independent of this slice. These tests instead
exercise the full ``InvestmentCase`` mechanism end to end against
disposable fixtures, the same substitution every engine slice's non-
acceptance tests already use.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
import sqlalchemy as sa

from app import db
from app.models import Company, ValuationRevision
from app.models.alert import AlertDelivery
from app.models.alert_types import AlertEventType, AlertSeverity
from app.models.document import (
    AcquisitionMethod,
    DiscoverySourceType,
    DistributionStatus,
    DocumentType,
    IngestionStatus,
    SourceAccess,
)
from app.models.investment_case import (
    InvestmentCase,
    InvestmentCaseForecast,
    InvestmentCaseGateResult,
    InvestmentCaseKillSwitch,
    InvestmentCaseValuation,
)
from app.models.research_types import (
    ExposureDirection,
    GateResultStatus,
    InvestmentCaseGate,
    InvestmentCaseView,
    ManagementQuality,
    GovernanceStatus,
    ValuationMethod,
)
from app.services.alert_service import AlertService
from app.services.cycle_intelligence_service import CycleIntelligenceService
from app.services.document_library_service import DocumentLibraryService
from app.services.hypothesis_assumption_service import HypothesisAssumptionService
from app.services.investment_case_service import InvestmentCaseService
from app.services.research_brain_service import ResearchBrainService
from app.services.research_command_service import ResearchCommandService
from app.utils.research_errors import ResearchNotFoundError, ResearchValidationError

AS_OF_DATE = date(2026, 9, 30)
EFFECTIVE_AT = datetime(2026, 9, 30, 12, 0, tzinfo=timezone.utc)

_ALL_GATES = (
    InvestmentCaseGate.GOVERNANCE,
    InvestmentCaseGate.TRANSFORMATION,
    InvestmentCaseGate.EXECUTION,
    InvestmentCaseGate.INSTITUTIONAL_ADVANTAGE,
    InvestmentCaseGate.FINANCIAL_VALIDATION,
    InvestmentCaseGate.MARKET_CAP_OPPORTUNITY,
    InvestmentCaseGate.OPTIONALITY,
    InvestmentCaseGate.VARIANT_PERCEPTION,
    InvestmentCaseGate.KILL_SWITCHES,
)


@pytest.fixture
def company(ticker_factory):
    ticker = ticker_factory(symbol="INVESTMENT_CASE_TEST", instrument_token=20)
    company = Company(
        ticker_id=ticker.id, legal_name="Investment Case Test Co Limited",
        isin="INE0LOJ09008",
    )
    db.session.add(company)
    db.session.commit()
    return company


@pytest.fixture
def other_company(ticker_factory):
    ticker = ticker_factory(symbol="INVESTMENT_CASE_OTHER", instrument_token=21)
    company = Company(
        ticker_id=ticker.id, legal_name="Other Co Limited", isin="INE0LOJ10009",
    )
    db.session.add(company)
    db.session.commit()
    return company


@pytest.fixture
def results_document(admin_user, company):
    payload = {
        "document": {
            "document_type": DocumentType.QUARTERLY_RESULTS,
            "title": "INVESTMENT_CASE_TEST_RESULTS",
            "document_date": "2026-08-04",
            "publisher_name": "Investment Case Test Co Limited",
            "original_source_url": "https://example.in/investment-case-test/results",
            "discovery_source_type": DiscoverySourceType.OFFICIAL_SITE,
            "source_access": SourceAccess.PUBLIC,
            "acquisition_method": AcquisitionMethod.MANUAL_REFERENCE,
            "distribution_status": DistributionStatus.LINK_ONLY,
            "ingestion_status": IngestionStatus.DISCOVERED,
        },
        "company_links": [{"company_id": company.id, "is_primary": True}],
    }
    return DocumentLibraryService.create_document(payload, admin_user.id)


def _evidence(*, admin_user, document, text="PVC spread normalizing toward mid-cycle."):
    run = ResearchBrainService.create_extraction_run(
        document_id=document.id, method="manual_pilot", extracted_by_user_id=admin_user.id
    )
    return ResearchBrainService.record_evidence(
        extraction_run_id=run.id, document_id=document.id,
        text_snippet=text, created_by_user_id=admin_user.id,
    )


def _fact(*, admin_user, document, company, fact_type="fin.raw.revenue", value="600.00"):
    evidence = _evidence(admin_user=admin_user, document=document)
    return ResearchBrainService.record_fact(
        company_id=company.id, fact_type=fact_type, value_type="NUMERIC",
        value=value, period="FY2027Q1", as_of_date=date(2026, 6, 30),
        evidence_ids=[evidence.id], created_by_user_id=admin_user.id,
    )


def _research_revision(*, admin_user, company):
    return ResearchCommandService.create_research_revision(
        company.id, actor_user_id=admin_user.id,
        payload={
            "why_selected": "PVC-cycle beneficiary with capacity ramp",
            "thesis": "Margins recover as spreads normalize and new line ramps",
            "thesis_invalidation": "Spread stays depressed or ramp stalls",
            "management_quality": ManagementQuality.ACCEPTABLE,
            "management_rationale": "Long promoter tenure with consistent delivery",
            "governance_status": GovernanceStatus.CLEAR,
            "effective_at": EFFECTIVE_AT,
        },
    )


def _cycle_assessment_and_exposure(*, admin_user, document, company, cycle_tag="PVC_RESIN_IC"):
    evidence = _evidence(admin_user=admin_user, document=document)
    observation = CycleIntelligenceService.record_cycle_observation(
        cycle_tag=cycle_tag, observed_on=date(2026, 9, 30),
        description="PVC-ACH spread widened.", created_by_user_id=admin_user.id,
        evidence_id=evidence.id,
    )
    assessment = CycleIntelligenceService.create_cycle_assessment(
        cycle_tag=cycle_tag, scenario="BASE", narrative="Normalizing.",
        observation_ids=[observation.id], created_by_user_id=admin_user.id,
    )
    exposure = CycleIntelligenceService.record_company_exposure(
        company_id=company.id, cycle_assessment_id=assessment.id,
        exposure_direction=ExposureDirection.BENEFICIARY,
        rationale="Net beneficiary of spread normalization.",
        created_by_user_id=admin_user.id,
    )
    return assessment, exposure


def _hypothesis(*, admin_user, document, company, fact):
    return HypothesisAssumptionService.create_investment_hypothesis(
        company_id=company.id, title="Margin recovery thesis",
        thesis_narrative="Margins recover as spreads normalize.",
        confidence="MEDIUM", created_by_user_id=admin_user.id,
        citations=[{"fact_id": fact.id}],
    )


def _forecast_revision(
    *, company_id, actor_user_id, scenario, ebitda=Decimal("20.0000"),
    base_revision_id=None,
):
    payload = {
        "as_of_date": AS_OF_DATE,
        "scenario": scenario,
        "lines": [
            {
                "fiscal_year": 2027, "is_estimate": True,
                "revenue": None, "ebitda": ebitda, "pat": None, "eps": None,
                "currency": "INR", "unit": "CRORE",
            }
        ],
    }
    if base_revision_id is not None:
        payload["base_revision_id"] = base_revision_id
        payload["change_reason"] = "update"
    return ResearchCommandService.create_forecast_revision(
        company_id, actor_user_id=actor_user_id, payload=payload
    )


def _valuation_revision(
    *, company_id, actor_user_id, scenario,
    implied_future_equity_value=None, current_market_cap=Decimal("1000.0000"),
    base_revision_id=None,
):
    payload = {
        "valuation_method": ValuationMethod.EV_EBITDA,
        "scenario": scenario,
        "as_of_date": AS_OF_DATE,
        "currency": "INR", "unit": "CRORE",
        "valuation_notes": f"{scenario} conclusion",
        "implied_future_equity_value": implied_future_equity_value,
        "current_market_cap": current_market_cap,
        "reference_lines": [
            {
                "reference_metric": "EBITDA", "reference_metric_value": Decimal("20.0000"),
                "reference_metric_unit": "INR_CRORE", "reference_metric_basis": "snapshot",
                "sort_order": 0,
            }
        ],
    }
    if base_revision_id is not None:
        payload["base_revision_id"] = base_revision_id
        payload["change_reason"] = "update"
    return ResearchCommandService.create_valuation_revision(
        company_id, actor_user_id, payload
    )


def _case_inputs(*, admin_user, company, results_document):
    fact = _fact(admin_user=admin_user, document=results_document, company=company)
    research_revision = _research_revision(admin_user=admin_user, company=company)
    hypothesis = _hypothesis(
        admin_user=admin_user, document=results_document, company=company, fact=fact
    )
    forecast = _forecast_revision(
        company_id=company.id, actor_user_id=admin_user.id, scenario="BASE"
    )
    valuation = _valuation_revision(
        company_id=company.id, actor_user_id=admin_user.id, scenario="BASE",
        implied_future_equity_value=Decimal("1100.0000"),
    )
    return research_revision, hypothesis, forecast, valuation


def _create_case(*, admin_user, company, research_revision, hypothesis, forecast, valuation, **overrides):
    kwargs = dict(
        company_id=company.id,
        research_revision_id=research_revision.id,
        investment_hypothesis_id=hypothesis.id,
        view=InvestmentCaseView.ADD,
        as_of_date=AS_OF_DATE,
        forecast_revision_ids=[forecast.id],
        valuation_revision_ids=[valuation.id],
        created_by_user_id=admin_user.id,
    )
    kwargs.update(overrides)
    return InvestmentCaseService.create_investment_case(**kwargs)


# --- Revision-chain integrity --------------------------------------------


def test_genesis_and_supersession_chain(admin_user, company, results_document):
    research_revision, hypothesis, forecast, valuation = _case_inputs(
        admin_user=admin_user, company=company, results_document=results_document
    )
    v1 = _create_case(
        admin_user=admin_user, company=company, research_revision=research_revision,
        hypothesis=hypothesis, forecast=forecast, valuation=valuation,
    )
    assert v1.revision_number == 1
    assert v1.supersedes_case_id is None

    v2 = _create_case(
        admin_user=admin_user, company=company, research_revision=research_revision,
        hypothesis=hypothesis, forecast=forecast, valuation=valuation,
        supersedes_case_id=v1.id, change_reason="Updated view",
    )
    assert v2.revision_number == 2

    current = InvestmentCaseService.get_current_investment_case(company.id)
    assert current.id == v2.id
    history = InvestmentCaseService.get_investment_case_history(company.id)
    assert [row.id for row in history] == [v1.id, v2.id]


def test_supersession_requires_change_reason(admin_user, company, results_document):
    research_revision, hypothesis, forecast, valuation = _case_inputs(
        admin_user=admin_user, company=company, results_document=results_document
    )
    v1 = _create_case(
        admin_user=admin_user, company=company, research_revision=research_revision,
        hypothesis=hypothesis, forecast=forecast, valuation=valuation,
    )
    with pytest.raises(ResearchValidationError):
        _create_case(
            admin_user=admin_user, company=company, research_revision=research_revision,
            hypothesis=hypothesis, forecast=forecast, valuation=valuation,
            supersedes_case_id=v1.id,
        )


def test_concurrent_supersession_is_rejected(admin_user, company, results_document):
    research_revision, hypothesis, forecast, valuation = _case_inputs(
        admin_user=admin_user, company=company, results_document=results_document
    )
    v1 = _create_case(
        admin_user=admin_user, company=company, research_revision=research_revision,
        hypothesis=hypothesis, forecast=forecast, valuation=valuation,
    )
    _create_case(
        admin_user=admin_user, company=company, research_revision=research_revision,
        hypothesis=hypothesis, forecast=forecast, valuation=valuation,
        supersedes_case_id=v1.id, change_reason="first writer",
    )
    with pytest.raises(ResearchValidationError):
        _create_case(
            admin_user=admin_user, company=company, research_revision=research_revision,
            hypothesis=hypothesis, forecast=forecast, valuation=valuation,
            supersedes_case_id=v1.id, change_reason="second writer",
        )
    assert not db.session().in_transaction()


def test_duplicate_genesis_rejected(admin_user, company, results_document):
    research_revision, hypothesis, forecast, valuation = _case_inputs(
        admin_user=admin_user, company=company, results_document=results_document
    )
    _create_case(
        admin_user=admin_user, company=company, research_revision=research_revision,
        hypothesis=hypothesis, forecast=forecast, valuation=valuation,
    )
    with pytest.raises(ResearchValidationError):
        _create_case(
            admin_user=admin_user, company=company, research_revision=research_revision,
            hypothesis=hypothesis, forecast=forecast, valuation=valuation,
        )


def test_branching_supersession_rejected_at_database_level(
    admin_user, company, results_document
):
    research_revision, hypothesis, forecast, valuation = _case_inputs(
        admin_user=admin_user, company=company, results_document=results_document
    )
    v1 = _create_case(
        admin_user=admin_user, company=company, research_revision=research_revision,
        hypothesis=hypothesis, forecast=forecast, valuation=valuation,
    )
    db.session.add(
        InvestmentCase(
            company_id=company.id, research_revision_id=research_revision.id,
            investment_hypothesis_id=hypothesis.id, view=InvestmentCaseView.HOLD,
            as_of_date=AS_OF_DATE, revision_number=2, supersedes_case_id=v1.id,
            created_by_user_id=admin_user.id,
        )
    )
    db.session.commit()
    db.session.add(
        InvestmentCase(
            company_id=company.id, research_revision_id=research_revision.id,
            investment_hypothesis_id=hypothesis.id, view=InvestmentCaseView.EXIT,
            as_of_date=AS_OF_DATE, revision_number=3, supersedes_case_id=v1.id,
            created_by_user_id=admin_user.id,
        )
    )
    with pytest.raises(sa.exc.IntegrityError):
        db.session.commit()
    db.session.rollback()


# --- Company-membership validation ---------------------------------------


def test_research_revision_must_belong_to_same_company(
    admin_user, company, other_company, results_document
):
    _, hypothesis, forecast, valuation = _case_inputs(
        admin_user=admin_user, company=company, results_document=results_document
    )
    foreign_revision = _research_revision(admin_user=admin_user, company=other_company)
    with pytest.raises(ResearchValidationError):
        _create_case(
            admin_user=admin_user, company=company, research_revision=foreign_revision,
            hypothesis=hypothesis, forecast=forecast, valuation=valuation,
        )


def test_forecast_revision_must_belong_to_same_company(
    admin_user, company, other_company, results_document
):
    research_revision, hypothesis, _, valuation = _case_inputs(
        admin_user=admin_user, company=company, results_document=results_document
    )
    foreign_forecast = _forecast_revision(
        company_id=other_company.id, actor_user_id=admin_user.id, scenario="BASE"
    )
    with pytest.raises(ResearchValidationError):
        _create_case(
            admin_user=admin_user, company=company, research_revision=research_revision,
            hypothesis=hypothesis, forecast=foreign_forecast, valuation=valuation,
        )


def test_requires_at_least_one_forecast_and_valuation_revision(
    admin_user, company, results_document
):
    research_revision, hypothesis, forecast, valuation = _case_inputs(
        admin_user=admin_user, company=company, results_document=results_document
    )
    with pytest.raises(ResearchValidationError):
        InvestmentCaseService.create_investment_case(
            company_id=company.id, research_revision_id=research_revision.id,
            investment_hypothesis_id=hypothesis.id, view=InvestmentCaseView.ADD,
            as_of_date=AS_OF_DATE, forecast_revision_ids=[], valuation_revision_ids=[valuation.id],
            created_by_user_id=admin_user.id,
        )
    with pytest.raises(ResearchValidationError):
        InvestmentCaseService.create_investment_case(
            company_id=company.id, research_revision_id=research_revision.id,
            investment_hypothesis_id=hypothesis.id, view=InvestmentCaseView.ADD,
            as_of_date=AS_OF_DATE, forecast_revision_ids=[forecast.id], valuation_revision_ids=[],
            created_by_user_id=admin_user.id,
        )


# --- Immutability ----------------------------------------------------------


def test_immutability_enforced(admin_user, company, results_document):
    research_revision, hypothesis, forecast, valuation = _case_inputs(
        admin_user=admin_user, company=company, results_document=results_document
    )
    case = _create_case(
        admin_user=admin_user, company=company, research_revision=research_revision,
        hypothesis=hypothesis, forecast=forecast, valuation=valuation,
    )
    forecast_link = db.session.scalar(
        sa.select(InvestmentCaseForecast).where(
            InvestmentCaseForecast.investment_case_id == case.id
        )
    )
    valuation_link = db.session.scalar(
        sa.select(InvestmentCaseValuation).where(
            InvestmentCaseValuation.investment_case_id == case.id
        )
    )

    case.view = InvestmentCaseView.EXIT
    with pytest.raises(sa.exc.InvalidRequestError):
        db.session.commit()
    db.session.rollback()

    forecast_link.forecast_revision_id = forecast_link.forecast_revision_id
    with pytest.raises(sa.exc.InvalidRequestError):
        db.session.commit()
    db.session.rollback()

    valuation_link.valuation_revision_id = valuation_link.valuation_revision_id
    with pytest.raises(sa.exc.InvalidRequestError):
        db.session.commit()
    db.session.rollback()

    for row in (case, forecast_link, valuation_link):
        db.session.delete(row)
        with pytest.raises(sa.exc.InvalidRequestError):
            db.session.commit()
        db.session.rollback()


def test_kill_switch_triggers_exactly_once(admin_user, company, results_document):
    research_revision, hypothesis, forecast, valuation = _case_inputs(
        admin_user=admin_user, company=company, results_document=results_document
    )
    case = _create_case(
        admin_user=admin_user, company=company, research_revision=research_revision,
        hypothesis=hypothesis, forecast=forecast, valuation=valuation,
    )
    kill_switch = InvestmentCaseService.record_kill_switch(
        investment_case_id=case.id, condition="PVC-ACH spread re-widens past 2026 lows",
        created_by_user_id=admin_user.id, data_source_hint="Monthly spread tracker",
    )
    assert kill_switch.triggered_at is None

    triggered = InvestmentCaseService.trigger_kill_switch(kill_switch_id=kill_switch.id)
    assert triggered.triggered_at is not None

    with pytest.raises(ResearchValidationError):
        InvestmentCaseService.trigger_kill_switch(kill_switch_id=kill_switch.id)

    # Direct ORM mutation of an already-triggered switch is rejected too.
    triggered.triggered_at = datetime.now(timezone.utc)
    with pytest.raises(sa.exc.InvalidRequestError):
        db.session.commit()
    db.session.rollback()

    # Mutating any other field is always rejected, even pre-trigger.
    db.session.refresh(kill_switch)
    kill_switch.condition = "changed"
    with pytest.raises(sa.exc.InvalidRequestError):
        db.session.commit()
    db.session.rollback()

    db.session.refresh(kill_switch)
    db.session.delete(kill_switch)
    with pytest.raises(sa.exc.InvalidRequestError):
        db.session.commit()
    db.session.rollback()


# --- Gate results -----------------------------------------------------------


def test_gate_result_exactly_one_provenance_enforced(admin_user, company, results_document):
    research_revision, hypothesis, forecast, valuation = _case_inputs(
        admin_user=admin_user, company=company, results_document=results_document
    )
    fact = _fact(admin_user=admin_user, document=results_document, company=company)
    case = _create_case(
        admin_user=admin_user, company=company, research_revision=research_revision,
        hypothesis=hypothesis, forecast=forecast, valuation=valuation,
    )

    with pytest.raises(ResearchValidationError):
        InvestmentCaseService.record_gate_result(
            investment_case_id=case.id, gate=InvestmentCaseGate.GOVERNANCE,
            status=GateResultStatus.PASS, rationale="no provenance",
            created_by_user_id=admin_user.id,
        )
    with pytest.raises(ResearchValidationError):
        InvestmentCaseService.record_gate_result(
            investment_case_id=case.id, gate=InvestmentCaseGate.GOVERNANCE,
            status=GateResultStatus.PASS, rationale="two sources",
            created_by_user_id=admin_user.id, fact_id=fact.id, evidence_id="not-exactly-one",
        )

    result = InvestmentCaseService.record_gate_result(
        investment_case_id=case.id, gate=InvestmentCaseGate.GOVERNANCE,
        status=GateResultStatus.PASS, rationale="clean governance record",
        created_by_user_id=admin_user.id, fact_id=fact.id,
    )
    assert result.fact_id == fact.id


def test_gate_result_genesis_and_supersession_chain(admin_user, company, results_document):
    research_revision, hypothesis, forecast, valuation = _case_inputs(
        admin_user=admin_user, company=company, results_document=results_document
    )
    fact = _fact(admin_user=admin_user, document=results_document, company=company)
    case = _create_case(
        admin_user=admin_user, company=company, research_revision=research_revision,
        hypothesis=hypothesis, forecast=forecast, valuation=valuation,
    )

    v1 = InvestmentCaseService.record_gate_result(
        investment_case_id=case.id, gate=InvestmentCaseGate.EXECUTION,
        status=GateResultStatus.WATCH, rationale="early days",
        created_by_user_id=admin_user.id, fact_id=fact.id,
    )
    # Second genesis for the same (case, gate) is rejected.
    with pytest.raises(ResearchValidationError):
        InvestmentCaseService.record_gate_result(
            investment_case_id=case.id, gate=InvestmentCaseGate.EXECUTION,
            status=GateResultStatus.PASS, rationale="duplicate genesis",
            created_by_user_id=admin_user.id, fact_id=fact.id,
        )

    v2 = InvestmentCaseService.record_gate_result(
        investment_case_id=case.id, gate=InvestmentCaseGate.EXECUTION,
        status=GateResultStatus.PASS, rationale="ramp on track",
        created_by_user_id=admin_user.id, fact_id=fact.id,
        supersedes_gate_result_id=v1.id,
    )
    current = InvestmentCaseService.get_current_gate_result(
        case.id, InvestmentCaseGate.EXECUTION
    )
    assert current.id == v2.id

    # A gate mismatch on supersession is rejected.
    with pytest.raises(ResearchValidationError):
        InvestmentCaseService.record_gate_result(
            investment_case_id=case.id, gate=InvestmentCaseGate.OPTIONALITY,
            status=GateResultStatus.PASS, rationale="wrong gate",
            created_by_user_id=admin_user.id, fact_id=fact.id,
            supersedes_gate_result_id=v1.id,
        )


# --- Full chain + scenario return spread ------------------------------------


def test_full_chain_nine_gates_kill_switch_and_scenario_spread(
    admin_user, company, results_document
):
    """The Slice G acceptance shape: a full InvestmentCase trace with a
    cited CycleAssessment, three scenario-tagged ForecastRevision/
    ValuationRevision pairs (Bull/Base/Bear, Mid-Cycle added separately
    and proven excluded), nine recorded InvestmentCaseGateResult rows
    (at least one PASS and one non-PASS), at least one kill switch, and
    a live, non-fabricated upside/downside spread."""

    fact = _fact(admin_user=admin_user, document=results_document, company=company)
    research_revision = _research_revision(admin_user=admin_user, company=company)
    cycle_assessment, exposure = _cycle_assessment_and_exposure(
        admin_user=admin_user, document=results_document, company=company
    )
    hypothesis = HypothesisAssumptionService.create_investment_hypothesis(
        company_id=company.id, title="PVC normalization + capacity ramp",
        thesis_narrative="External cycle normalization plus new-line ramp.",
        confidence="HIGH", created_by_user_id=admin_user.id,
        citations=[{"fact_id": fact.id}, {"company_exposure_id": exposure.id}],
    )

    scenarios = {
        "BULL": Decimal("1400.0000"),
        "BASE": Decimal("1100.0000"),
        "BEAR": Decimal("900.0000"),
    }
    forecast_ids = []
    valuation_ids = []
    for scenario, fair_value in scenarios.items():
        forecast = _forecast_revision(
            company_id=company.id, actor_user_id=admin_user.id, scenario=scenario
        )
        valuation = _valuation_revision(
            company_id=company.id, actor_user_id=admin_user.id, scenario=scenario,
            implied_future_equity_value=fair_value, current_market_cap=Decimal("1000.0000"),
        )
        forecast_ids.append(forecast.id)
        valuation_ids.append(valuation.id)

    # Mid-Cycle valuation is linked too, to prove it is excluded from the spread.
    mid_cycle_forecast = _forecast_revision(
        company_id=company.id, actor_user_id=admin_user.id, scenario="MID_CYCLE"
    )
    mid_cycle_valuation = _valuation_revision(
        company_id=company.id, actor_user_id=admin_user.id, scenario="MID_CYCLE",
        implied_future_equity_value=Decimal("999999.0000"),
        current_market_cap=Decimal("1000.0000"),
    )
    forecast_ids.append(mid_cycle_forecast.id)
    valuation_ids.append(mid_cycle_valuation.id)

    case = InvestmentCaseService.create_investment_case(
        company_id=company.id, research_revision_id=research_revision.id,
        investment_hypothesis_id=hypothesis.id, cycle_assessment_id=cycle_assessment.id,
        view=InvestmentCaseView.ADD, as_of_date=AS_OF_DATE,
        forecast_revision_ids=forecast_ids, valuation_revision_ids=valuation_ids,
        created_by_user_id=admin_user.id,
        expected_return_pct=Decimal("10.0000"),
        expected_return_basis="Base-case upside over current market cap",
    )

    kill_switch = InvestmentCaseService.record_kill_switch(
        investment_case_id=case.id,
        condition="PVC-ACH spread re-widens past 2026 lows for two consecutive quarters",
        created_by_user_id=admin_user.id, data_source_hint="Monthly spread tracker",
    )
    assert kill_switch.triggered_at is None

    statuses = [
        GateResultStatus.PASS, GateResultStatus.PASS, GateResultStatus.WATCH,
        GateResultStatus.PASS, GateResultStatus.PASS, GateResultStatus.FAIL,
        GateResultStatus.WAIVED, GateResultStatus.PASS, GateResultStatus.NOT_ASSESSED,
    ]
    for gate, status in zip(_ALL_GATES, statuses):
        InvestmentCaseService.record_gate_result(
            investment_case_id=case.id, gate=gate, status=status,
            rationale=f"{gate} assessed {status}",
            created_by_user_id=admin_user.id, fact_id=fact.id,
        )

    recorded = InvestmentCaseService.get_current_gate_results(case.id)
    assert {row.gate for row in recorded} == set(_ALL_GATES)
    assert any(row.status == GateResultStatus.PASS for row in recorded)
    assert any(row.status != GateResultStatus.PASS for row in recorded)

    spread = InvestmentCaseService.get_scenario_return_spread(case.id)
    assert spread["scenarios"]["BULL"]["return_pct"] == Decimal("40.00000000")
    assert spread["scenarios"]["BASE"]["return_pct"] == Decimal("10.00000000")
    assert spread["scenarios"]["BEAR"]["return_pct"] == Decimal("-10.00000000")
    assert spread["upside_pct"] == Decimal("30.00000000")
    assert spread["downside_pct"] == Decimal("20.00000000")
    # Mid-Cycle must never appear in the scenario spread at all.
    assert "MID_CYCLE" not in spread["scenarios"]


def test_scenario_return_spread_missing_scenario_not_fabricated(
    admin_user, company, results_document
):
    research_revision, hypothesis, forecast, valuation = _case_inputs(
        admin_user=admin_user, company=company, results_document=results_document
    )
    case = _create_case(
        admin_user=admin_user, company=company, research_revision=research_revision,
        hypothesis=hypothesis, forecast=forecast, valuation=valuation,
    )
    spread = InvestmentCaseService.get_scenario_return_spread(case.id)
    assert spread["scenarios"]["BASE"] is not None
    assert spread["scenarios"]["BULL"] is None
    assert spread["scenarios"]["BEAR"] is None
    assert spread["upside_pct"] is None
    assert spread["downside_pct"] is None


def test_scenario_return_spread_rejects_duplicate_scenario_links(
    admin_user, company, results_document
):
    research_revision, hypothesis, forecast, valuation = _case_inputs(
        admin_user=admin_user, company=company, results_document=results_document
    )
    second_base_forecast = _forecast_revision(
        company_id=company.id, actor_user_id=admin_user.id, scenario="BASE",
        ebitda=Decimal("25.0000"), base_revision_id=forecast.id,
    )
    second_base_valuation = _valuation_revision(
        company_id=company.id, actor_user_id=admin_user.id, scenario="BASE",
        implied_future_equity_value=Decimal("1200.0000"),
        base_revision_id=valuation.id,
    )
    # The same BASE stream's v1 and v2 (a supersession pair, not two
    # independent streams); link both revisions to one case.
    case = InvestmentCaseService.create_investment_case(
        company_id=company.id, research_revision_id=research_revision.id,
        investment_hypothesis_id=hypothesis.id, view=InvestmentCaseView.ADD,
        as_of_date=AS_OF_DATE,
        forecast_revision_ids=[forecast.id, second_base_forecast.id],
        valuation_revision_ids=[valuation.id, second_base_valuation.id],
        created_by_user_id=admin_user.id,
    )
    with pytest.raises(ResearchValidationError):
        InvestmentCaseService.get_scenario_return_spread(case.id)


# --- I3AS Alert-2: Investment Brain integration -----------------------------
#
# Reuses Alert-1's AlertEvent/AlertDelivery/AlertService infrastructure
# unchanged -- see app/services/investment_case_service.py's module
# docstring. Recipients are always AlertService.admin_recipient_ids();
# admin_user (is_admin=True, tests/conftest.py) is the only admin in
# these tests, so it is always the expected sole recipient.


def test_genesis_case_creation_emits_admin_alert(admin_user, company, results_document):
    research_revision, hypothesis, forecast, valuation = _case_inputs(
        admin_user=admin_user, company=company, results_document=results_document
    )
    case = _create_case(
        admin_user=admin_user, company=company, research_revision=research_revision,
        hypothesis=hypothesis, forecast=forecast, valuation=valuation,
    )

    events = AlertService.list_events_for_source(
        source_table="investment_case", source_id=case.id
    )
    assert [event.event_type for event in events] == [AlertEventType.INVESTMENT_CASE_CREATED]
    event = events[0]
    assert event.company_id == company.id
    assert event.severity == AlertSeverity.INFO

    delivery = db.session.scalar(
        sa.select(AlertDelivery).where(AlertDelivery.alert_event_id == event.id)
    )
    assert delivery.recipient_user_id == admin_user.id


def test_view_changing_supersession_emits_watch_alert_with_details(
    admin_user, company, results_document
):
    research_revision, hypothesis, forecast, valuation = _case_inputs(
        admin_user=admin_user, company=company, results_document=results_document
    )
    v1 = _create_case(
        admin_user=admin_user, company=company, research_revision=research_revision,
        hypothesis=hypothesis, forecast=forecast, valuation=valuation,
        view=InvestmentCaseView.ADD,
    )
    v2 = _create_case(
        admin_user=admin_user, company=company, research_revision=research_revision,
        hypothesis=hypothesis, forecast=forecast, valuation=valuation,
        view=InvestmentCaseView.EXIT,
        supersedes_case_id=v1.id, change_reason="Thesis invalidated",
    )

    events = AlertService.list_events_for_source(
        source_table="investment_case", source_id=v2.id
    )
    assert [event.event_type for event in events] == [
        AlertEventType.INVESTMENT_CASE_VIEW_CHANGED
    ]
    event = events[0]
    assert event.severity == AlertSeverity.WATCH
    assert event.details == {"previous_view": "ADD", "new_view": "EXIT"}


def test_supersession_with_unchanged_view_emits_no_alert(
    admin_user, company, results_document
):
    research_revision, hypothesis, forecast, valuation = _case_inputs(
        admin_user=admin_user, company=company, results_document=results_document
    )
    v1 = _create_case(
        admin_user=admin_user, company=company, research_revision=research_revision,
        hypothesis=hypothesis, forecast=forecast, valuation=valuation,
        view=InvestmentCaseView.ADD,
    )
    v2 = _create_case(
        admin_user=admin_user, company=company, research_revision=research_revision,
        hypothesis=hypothesis, forecast=forecast, valuation=valuation,
        view=InvestmentCaseView.ADD,
        supersedes_case_id=v1.id, change_reason="Minor update, view unchanged",
    )

    events = AlertService.list_events_for_source(
        source_table="investment_case", source_id=v2.id
    )
    assert events == []


def test_kill_switch_trigger_emits_critical_admin_alert(
    admin_user, company, results_document
):
    research_revision, hypothesis, forecast, valuation = _case_inputs(
        admin_user=admin_user, company=company, results_document=results_document
    )
    case = _create_case(
        admin_user=admin_user, company=company, research_revision=research_revision,
        hypothesis=hypothesis, forecast=forecast, valuation=valuation,
    )
    kill_switch = InvestmentCaseService.record_kill_switch(
        investment_case_id=case.id,
        condition="PVC-ACH spread re-widens past 2026 lows",
        created_by_user_id=admin_user.id,
    )

    InvestmentCaseService.trigger_kill_switch(kill_switch_id=kill_switch.id)

    events = AlertService.list_events_for_source(
        source_table="investment_case_kill_switch", source_id=kill_switch.id
    )
    assert [event.event_type for event in events] == [AlertEventType.KILL_SWITCH_TRIGGERED]
    event = events[0]
    assert event.severity == AlertSeverity.CRITICAL
    assert event.company_id == company.id
    assert "re-widens" in event.summary

    delivery = db.session.scalar(
        sa.select(AlertDelivery).where(AlertDelivery.alert_event_id == event.id)
    )
    assert delivery.recipient_user_id == admin_user.id


def test_gate_fail_emits_watch_alert_but_pass_does_not(
    admin_user, company, results_document
):
    research_revision, hypothesis, forecast, valuation = _case_inputs(
        admin_user=admin_user, company=company, results_document=results_document
    )
    fact = _fact(admin_user=admin_user, document=results_document, company=company)
    case = _create_case(
        admin_user=admin_user, company=company, research_revision=research_revision,
        hypothesis=hypothesis, forecast=forecast, valuation=valuation,
    )

    pass_result = InvestmentCaseService.record_gate_result(
        investment_case_id=case.id, gate=InvestmentCaseGate.GOVERNANCE,
        status=GateResultStatus.PASS, rationale="Clean record",
        created_by_user_id=admin_user.id, fact_id=fact.id,
    )
    fail_result = InvestmentCaseService.record_gate_result(
        investment_case_id=case.id, gate=InvestmentCaseGate.EXECUTION,
        status=GateResultStatus.FAIL, rationale="Capacity ramp behind schedule",
        created_by_user_id=admin_user.id, fact_id=fact.id,
    )

    assert AlertService.list_events_for_source(
        source_table="investment_case_gate_result", source_id=pass_result.id
    ) == []

    events = AlertService.list_events_for_source(
        source_table="investment_case_gate_result", source_id=fail_result.id
    )
    assert [event.event_type for event in events] == [
        AlertEventType.INVESTMENT_CASE_GATE_FAILED
    ]
    event = events[0]
    assert event.severity == AlertSeverity.WATCH
    assert event.details == {"gate": InvestmentCaseGate.EXECUTION, "status": GateResultStatus.FAIL}


def test_alert_recording_failure_never_affects_the_authoritative_write(
    admin_user, company, results_document, monkeypatch
):
    """Fail-safe isolation: InvestmentCaseService's own business write
    must succeed even if AlertService.record_event itself blows up
    (not just the ordinary, already-non-raising failure modes
    AlertService handles internally)."""

    research_revision, hypothesis, forecast, valuation = _case_inputs(
        admin_user=admin_user, company=company, results_document=results_document
    )

    def _boom(**kwargs):
        raise RuntimeError("simulated AlertService outage")

    monkeypatch.setattr(AlertService, "record_event", _boom)

    case = _create_case(
        admin_user=admin_user, company=company, research_revision=research_revision,
        hypothesis=hypothesis, forecast=forecast, valuation=valuation,
    )

    assert case is not None
    assert db.session.get(InvestmentCase, case.id) is not None
