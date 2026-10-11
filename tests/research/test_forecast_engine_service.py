"""Phase 3 Slice E: scenario-aware ``ForecastRevision`` creation and
``ForecastEngineService`` acceptance tests.

Covers: the legacy (no-``scenario``) single-stream behaviour is
unchanged; independent per-``(company_id, scenario)``revision chains
(including that a race on a scenario-tagged stream is now correctly
translated into ``ResearchConflictError``, proving the
``_is_forecast_revision_number_unique_violation`` fix for the two index
names Slice A's migration actually created); and
``ForecastEngineService.create_scenario_forecast``'s scenario
consistency, hypothesis-revision consistency, scenario-family coherence,
reproducible non-fabricating arithmetic, and citation-trail immutability.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from app import db
from app.models import Company, ForecastRevision
from app.models.forecast_revision_assumption import ForecastRevisionAssumption
from app.services.document_library_service import DocumentLibraryService
from app.services.forecast_engine_service import ForecastEngineService
from app.services.hypothesis_assumption_service import HypothesisAssumptionService
from app.services.research_brain_service import ResearchBrainService
from app.services.research_command_service import ResearchCommandService
from app.models.document import (
    AcquisitionMethod,
    DiscoverySourceType,
    DistributionStatus,
    DocumentType,
    IngestionStatus,
    SourceAccess,
)
from app.utils.research_errors import ResearchConflictError, ResearchValidationError

AS_OF_DATE = date(2026, 9, 4)


@pytest.fixture
def company(ticker_factory):
    ticker = ticker_factory(symbol="FORECAST_ENGINE_TEST", instrument_token=8)
    company = Company(
        ticker_id=ticker.id, legal_name="Forecast Engine Test Co Limited",
        isin="INE0LOJ06005",
    )
    db.session.add(company)
    db.session.commit()
    return company


@pytest.fixture
def other_company(ticker_factory):
    ticker = ticker_factory(symbol="FORECAST_ENGINE_OTHER", instrument_token=9)
    company = Company(
        ticker_id=ticker.id, legal_name="Other Forecast Co Limited",
        isin="INE0LOJ07006",
    )
    db.session.add(company)
    db.session.commit()
    return company


@pytest.fixture
def results_document(admin_user, company):
    payload = {
        "document": {
            "document_type": DocumentType.QUARTERLY_RESULTS,
            "title": "FORECAST_ENGINE_TEST_RESULTS",
            "document_date": "2026-08-04",
            "publisher_name": "Forecast Engine Test Co Limited",
            "original_source_url": "https://example.in/forecast-engine-test/results",
            "discovery_source_type": DiscoverySourceType.OFFICIAL_SITE,
            "source_access": SourceAccess.PUBLIC,
            "acquisition_method": AcquisitionMethod.MANUAL_REFERENCE,
            "distribution_status": DistributionStatus.LINK_ONLY,
            "ingestion_status": IngestionStatus.DISCOVERED,
        },
        "company_links": [{"company_id": company.id, "is_primary": True}],
    }
    return DocumentLibraryService.create_document(payload, admin_user.id)


def _line(fiscal_year=2027, **overrides):
    line = {"fiscal_year": fiscal_year, "is_estimate": True, "unit": "CRORE"}
    line.update(overrides)
    return line


def _evidence(*, admin_user, document, text="Spread normalizing."):
    run = ResearchBrainService.create_extraction_run(
        document_id=document.id, method="manual_pilot", extracted_by_user_id=admin_user.id
    )
    return ResearchBrainService.record_evidence(
        extraction_run_id=run.id, document_id=document.id,
        text_snippet=text, created_by_user_id=admin_user.id,
    )


def _fact(*, admin_user, document, company):
    evidence = _evidence(admin_user=admin_user, document=document)
    return ResearchBrainService.record_fact(
        company_id=company.id, fact_type="fin.raw.revenue", value_type="NUMERIC",
        value="100.0", period="FY2027Q1", as_of_date=date(2026, 6, 30),
        evidence_ids=[evidence.id], created_by_user_id=admin_user.id,
    )


def _hypothesis_with_assumption(
    *, admin_user, document, company, scenario="BULL",
    metric_slug="ebitda_margin_pct", assumed_value="24.5",
):
    fact = _fact(admin_user=admin_user, document=document, company=company)
    hypothesis = HypothesisAssumptionService.create_investment_hypothesis(
        company_id=company.id, title="t", thesis_narrative="n", confidence="MEDIUM",
        created_by_user_id=admin_user.id, citations=[{"fact_id": fact.id}],
    )
    assumption = HypothesisAssumptionService.record_forecast_assumption(
        investment_hypothesis_id=hypothesis.id, scenario=scenario,
        assumption_statement="s", created_by_user_id=admin_user.id,
        fact_id=fact.id, metric_slug=metric_slug, assumed_value=assumed_value,
    )
    return hypothesis, assumption


# --- Legacy behaviour preserved ---------------------------------------


def test_legacy_single_stream_unaffected_by_scenario_column(admin_user, company):
    v1 = ResearchCommandService.create_forecast_revision(
        company.id, admin_user.id,
        {"as_of_date": AS_OF_DATE, "lines": [_line()]},
    )
    assert v1.scenario is None
    assert v1.revision_number == 1

    v2 = ResearchCommandService.create_forecast_revision(
        company.id, admin_user.id,
        {
            "as_of_date": AS_OF_DATE, "base_revision_id": v1.id,
            "change_reason": "update", "lines": [_line()],
        },
    )
    assert v2.scenario is None
    assert v2.revision_number == 2


# --- Scenario-aware independent revision streams ----------------------


def test_independent_revision_streams_per_scenario(admin_user, company):
    bull_v1 = ResearchCommandService.create_forecast_revision(
        company.id, admin_user.id,
        {"as_of_date": AS_OF_DATE, "scenario": "BULL", "lines": [_line()]},
    )
    base_v1 = ResearchCommandService.create_forecast_revision(
        company.id, admin_user.id,
        {"as_of_date": AS_OF_DATE, "scenario": "BASE", "lines": [_line()]},
    )
    legacy_v1 = ResearchCommandService.create_forecast_revision(
        company.id, admin_user.id,
        {"as_of_date": AS_OF_DATE, "lines": [_line()]},
    )
    assert bull_v1.revision_number == base_v1.revision_number == legacy_v1.revision_number == 1

    bull_v2 = ResearchCommandService.create_forecast_revision(
        company.id, admin_user.id,
        {
            "as_of_date": AS_OF_DATE, "scenario": "BULL",
            "base_revision_id": bull_v1.id, "change_reason": "update",
            "lines": [_line()],
        },
    )
    assert bull_v2.revision_number == 2

    base_current = db.session.scalar(
        sa.select(ForecastRevision).where(
            ForecastRevision.company_id == company.id,
            ForecastRevision.scenario == "BASE",
        )
    )
    assert base_current.id == base_v1.id
    assert base_current.revision_number == 1


def test_scenario_stream_race_is_translated_to_revision_conflict(admin_user, company):
    """Proves the Slice E fix: before it, a race on a scenario-tagged
    stream matched neither the stale constraint name nor the 2-column
    SQLite fallback tuple, so it would have leaked a raw IntegrityError
    instead of ResearchConflictError."""

    first = ResearchCommandService.create_forecast_revision(
        company.id, admin_user.id,
        {"as_of_date": AS_OF_DATE, "scenario": "BULL", "lines": [_line()]},
    )
    ResearchCommandService.create_forecast_revision(
        company.id, admin_user.id,
        {
            "as_of_date": AS_OF_DATE, "scenario": "BULL",
            "base_revision_id": first.id, "change_reason": "winner",
            "lines": [_line()],
        },
    )
    with pytest.raises(ResearchConflictError) as exc_info:
        ResearchCommandService.create_forecast_revision(
            company.id, admin_user.id,
            {
                "as_of_date": AS_OF_DATE, "scenario": "BULL",
                "base_revision_id": first.id, "change_reason": "loser",
                "lines": [_line()],
            },
        )
    assert exc_info.value.code == "revision_conflict"
    assert not db.session().in_transaction()


def test_invalid_scenario_rejected(admin_user, company):
    with pytest.raises(ResearchValidationError):
        ResearchCommandService.create_forecast_revision(
            company.id, admin_user.id,
            {"as_of_date": AS_OF_DATE, "scenario": "NOT_A_SCENARIO", "lines": [_line()]},
        )


# --- ForecastEngineService ---------------------------------------------


def test_create_scenario_forecast_happy_path(admin_user, results_document, company):
    hypothesis, assumption = _hypothesis_with_assumption(
        admin_user=admin_user, document=results_document, company=company,
    )
    revision = ForecastEngineService.create_scenario_forecast(
        company_id=company.id, scenario="BULL", assumption_ids=[assumption.id],
        fiscal_years=[2027], as_of_date=AS_OF_DATE, unit="CRORE",
        created_by_user_id=admin_user.id,
    )
    assert revision.scenario == "BULL"
    assert revision.lines[0].ebitda_margin_pct == Decimal("24.5")

    link = db.session.scalar(
        sa.select(ForecastRevisionAssumption).where(
            ForecastRevisionAssumption.forecast_revision_id == revision.id
        )
    )
    assert link.forecast_assumption_id == assumption.id


def test_scenario_mismatch_rejected(admin_user, results_document, company):
    _, bull_assumption = _hypothesis_with_assumption(
        admin_user=admin_user, document=results_document, company=company, scenario="BULL",
    )
    with pytest.raises(ResearchValidationError):
        ForecastEngineService.create_scenario_forecast(
            company_id=company.id, scenario="BASE",
            assumption_ids=[bull_assumption.id], fiscal_years=[2027],
            as_of_date=AS_OF_DATE, unit="CRORE", created_by_user_id=admin_user.id,
        )


def test_mixed_hypothesis_assumptions_rejected(
    admin_user, results_document, company, other_company
):
    _, assumption_one = _hypothesis_with_assumption(
        admin_user=admin_user, document=results_document, company=company,
    )
    _, assumption_two = _hypothesis_with_assumption(
        admin_user=admin_user, document=results_document, company=other_company,
    )
    with pytest.raises(ResearchValidationError):
        ForecastEngineService.create_scenario_forecast(
            company_id=company.id, scenario="BULL",
            assumption_ids=[assumption_one.id, assumption_two.id], fiscal_years=[2027],
            as_of_date=AS_OF_DATE, unit="CRORE", created_by_user_id=admin_user.id,
        )


def test_scenario_family_coherence_enforced(admin_user, results_document, company):
    hypothesis, bull_assumption = _hypothesis_with_assumption(
        admin_user=admin_user, document=results_document, company=company, scenario="BULL",
    )
    base_assumption = HypothesisAssumptionService.record_forecast_assumption(
        investment_hypothesis_id=hypothesis.id, scenario="BASE",
        assumption_statement="s", created_by_user_id=admin_user.id,
        fact_id=bull_assumption.fact_id, metric_slug="ebitda_margin_pct",
        assumed_value="18.0",
    )
    ForecastEngineService.create_scenario_forecast(
        company_id=company.id, scenario="BULL", assumption_ids=[bull_assumption.id],
        fiscal_years=[2027], as_of_date=AS_OF_DATE, unit="CRORE",
        created_by_user_id=admin_user.id,
    )
    with pytest.raises(ResearchValidationError):
        ForecastEngineService.create_scenario_forecast(
            company_id=company.id, scenario="BASE", assumption_ids=[base_assumption.id],
            fiscal_years=[2027], as_of_date=date(2026, 12, 31), unit="CRORE",
            created_by_user_id=admin_user.id,
        )


def test_no_usable_numeric_assumption_reports_limitation_not_fabrication(
    admin_user, results_document, company
):
    fact = _fact(admin_user=admin_user, document=results_document, company=company)
    hypothesis = HypothesisAssumptionService.create_investment_hypothesis(
        company_id=company.id, title="t", thesis_narrative="n", confidence="LOW",
        created_by_user_id=admin_user.id, citations=[{"fact_id": fact.id}],
    )
    qualitative_assumption = HypothesisAssumptionService.record_forecast_assumption(
        investment_hypothesis_id=hypothesis.id, scenario="BULL",
        assumption_statement="Qualitative only, no numeric value yet.",
        created_by_user_id=admin_user.id, fact_id=fact.id,
    )
    with pytest.raises(ResearchValidationError) as exc_info:
        ForecastEngineService.create_scenario_forecast(
            company_id=company.id, scenario="BULL",
            assumption_ids=[qualitative_assumption.id], fiscal_years=[2027],
            as_of_date=AS_OF_DATE, unit="CRORE", created_by_user_id=admin_user.id,
        )
    assert "cannot be computed without fabricating" in str(exc_info.value.details)
    # Rejected before any write -- no ForecastRevision was created.
    assert (
        db.session.scalar(
            sa.select(sa.func.count()).select_from(ForecastRevision).where(
                ForecastRevision.company_id == company.id
            )
        )
        == 0
    )


def test_forecast_revision_assumption_immutable(admin_user, results_document, company):
    hypothesis, assumption = _hypothesis_with_assumption(
        admin_user=admin_user, document=results_document, company=company,
    )
    revision = ForecastEngineService.create_scenario_forecast(
        company_id=company.id, scenario="BULL", assumption_ids=[assumption.id],
        fiscal_years=[2027], as_of_date=AS_OF_DATE, unit="CRORE",
        created_by_user_id=admin_user.id,
    )
    link = db.session.scalar(
        sa.select(ForecastRevisionAssumption).where(
            ForecastRevisionAssumption.forecast_revision_id == revision.id
        )
    )
    link.forecast_assumption_id = link.forecast_assumption_id
    with pytest.raises(sa.exc.InvalidRequestError):
        db.session.commit()
    db.session.rollback()

    db.session.delete(link)
    with pytest.raises(sa.exc.InvalidRequestError):
        db.session.commit()
    db.session.rollback()
