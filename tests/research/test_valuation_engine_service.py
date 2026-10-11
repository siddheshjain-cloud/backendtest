"""Phase 3 Slice F: scenario-aware ``ValuationRevision`` creation and
``ValuationEngineService`` acceptance tests.

Covers: the legacy (no-``scenario``) single-stream behaviour is
unchanged; independent per-``(company_id, valuation_method, scenario)``
revision chains (including that a race on a scenario-tagged stream is
now correctly translated into ``ResearchConflictError``, proving the
``_is_valuation_revision_number_unique_violation`` fix for the two index
names Slice A's migration actually created -- the valuation-side
analogue of Slice E's forecast fix); and
``ValuationEngineService.create_scenario_valuation``'s derived (never
caller-asserted) reference values, scenario-consistency against the
cited ``ForecastRevision``, and single-forecast-revision-per-call rule.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
import sqlalchemy as sa

from app import db
from app.models import Company, ForecastLine, ForecastRevision, ValuationRevision
from app.models.research_types import ValuationMethod
from app.services.research_command_service import ResearchCommandService
from app.services.valuation_engine_service import ValuationEngineService
from app.utils.research_errors import ResearchConflictError, ResearchValidationError

AS_OF_DATE = date(2026, 9, 4)


@pytest.fixture
def company(ticker_factory):
    ticker = ticker_factory(symbol="VALUATION_ENGINE_TEST", instrument_token=10)
    company = Company(
        ticker_id=ticker.id, legal_name="Valuation Engine Test Co Limited",
        isin="INE0LOJ08007",
    )
    db.session.add(company)
    db.session.commit()
    return company


def _ref_line(**overrides):
    line = {
        "reference_metric": "NET_DEBT", "reference_metric_value": Decimal("75.0000"),
        "reference_metric_unit": "INR_CRORE", "reference_metric_basis": "snapshot",
        "sort_order": 0,
    }
    line.update(overrides)
    return line


def _valuation_payload(**overrides):
    payload = {
        "valuation_method": ValuationMethod.EV_EBITDA,
        "reference_lines": [_ref_line()],
        "implied_enterprise_value": Decimal("1500.0000"),
        "currency": "INR", "unit": "CRORE",
        "valuation_notes": "Conclusion", "as_of_date": AS_OF_DATE,
    }
    payload.update(overrides)
    return payload


def _forecast_with_ebitda_line(
    *, company_id, actor_user_id, ebitda=Decimal("20.0000"), scenario=None,
    fiscal_year=2027, base_revision_id=None,
):
    payload = {
        "as_of_date": AS_OF_DATE,
        "lines": [
            {
                "fiscal_year": fiscal_year, "is_estimate": True,
                "revenue": None, "ebitda": ebitda, "pat": None, "eps": None,
                "currency": "INR", "unit": "CRORE",
            }
        ],
    }
    if scenario is not None:
        payload["scenario"] = scenario
    if base_revision_id is not None:
        payload["base_revision_id"] = base_revision_id
        payload["change_reason"] = "update"
    revision = ResearchCommandService.create_forecast_revision(
        company_id, actor_user_id=actor_user_id, payload=payload
    )
    line = db.session.scalar(
        sa.select(ForecastLine).where(ForecastLine.forecast_revision_id == revision.id)
    )
    return revision, line


# --- Legacy behaviour preserved ---------------------------------------


def test_legacy_single_stream_unaffected_by_scenario_column(admin_user, company):
    v1 = ResearchCommandService.create_valuation_revision(
        company.id, admin_user.id, _valuation_payload(),
    )
    assert v1.scenario is None
    assert v1.revision_number == 1

    v2 = ResearchCommandService.create_valuation_revision(
        company.id, admin_user.id,
        _valuation_payload(base_revision_id=v1.id, change_reason="update"),
    )
    assert v2.scenario is None
    assert v2.revision_number == 2


# --- Scenario-aware independent revision streams -----------------------


def test_independent_revision_streams_per_scenario(admin_user, company):
    bull_v1 = ResearchCommandService.create_valuation_revision(
        company.id, admin_user.id, _valuation_payload(scenario="BULL"),
    )
    base_v1 = ResearchCommandService.create_valuation_revision(
        company.id, admin_user.id, _valuation_payload(scenario="BASE"),
    )
    legacy_v1 = ResearchCommandService.create_valuation_revision(
        company.id, admin_user.id, _valuation_payload(),
    )
    assert bull_v1.revision_number == base_v1.revision_number == legacy_v1.revision_number == 1

    bull_v2 = ResearchCommandService.create_valuation_revision(
        company.id, admin_user.id,
        _valuation_payload(scenario="BULL", base_revision_id=bull_v1.id, change_reason="update"),
    )
    assert bull_v2.revision_number == 2

    base_current = db.session.scalar(
        sa.select(ValuationRevision).where(
            ValuationRevision.company_id == company.id,
            ValuationRevision.scenario == "BASE",
        )
    )
    assert base_current.id == base_v1.id


def test_scenario_stream_race_is_translated_to_revision_conflict(admin_user, company):
    """Proves the Slice F fix: before it, a race on a scenario-tagged
    valuation stream matched neither the stale constraint name nor the
    3-column SQLite fallback tuple, so it would have leaked a raw
    IntegrityError instead of ResearchConflictError."""

    first = ResearchCommandService.create_valuation_revision(
        company.id, admin_user.id, _valuation_payload(scenario="BULL"),
    )
    ResearchCommandService.create_valuation_revision(
        company.id, admin_user.id,
        _valuation_payload(scenario="BULL", base_revision_id=first.id, change_reason="winner"),
    )
    with pytest.raises(ResearchConflictError) as exc_info:
        ResearchCommandService.create_valuation_revision(
            company.id, admin_user.id,
            _valuation_payload(scenario="BULL", base_revision_id=first.id, change_reason="loser"),
        )
    assert exc_info.value.code == "revision_conflict"
    assert not db.session().in_transaction()


# --- ValuationEngineService ---------------------------------------------


def test_create_scenario_valuation_derives_reference_value(admin_user, company):
    forecast_revision, line = _forecast_with_ebitda_line(
        company_id=company.id, actor_user_id=admin_user.id,
        ebitda=Decimal("125.0000"), scenario="BULL",
    )
    revision = ValuationEngineService.create_scenario_valuation(
        company_id=company.id, scenario="BULL", valuation_method=ValuationMethod.EV_EBITDA,
        reference_specs=[
            {
                "forecast_revision_id": forecast_revision.id, "fiscal_year": 2027,
                "metric": "EBITDA", "basis": "FY27 BULL forecast", "unit": "INR_CRORE",
                "sort_order": 0,
            }
        ],
        as_of_date=AS_OF_DATE, created_by_user_id=admin_user.id,
        implied_enterprise_value=Decimal("1500.0000"), currency="INR", unit="CRORE",
        valuation_notes="EV/EBITDA on BULL forecast",
    )
    assert revision.scenario == "BULL"
    stored_line = revision.reference_lines[0]
    assert stored_line.reference_metric_value == line.ebitda == Decimal("125.0000")
    assert stored_line.reference_forecast_revision_id == forecast_revision.id


def test_derived_value_cannot_be_overridden_by_caller(admin_user, company):
    """reference_metric_value is never accepted from the caller at all --
    the signature has no such parameter, only fiscal_year/metric to
    derive from."""

    import inspect

    params = inspect.signature(ValuationEngineService._derive_reference_line).parameters
    assert "reference_metric_value" not in params
    params = inspect.signature(ValuationEngineService.create_scenario_valuation).parameters
    assert "reference_metric_value" not in params


def test_scenario_mismatch_against_forecast_rejected(admin_user, company):
    forecast_revision, _ = _forecast_with_ebitda_line(
        company_id=company.id, actor_user_id=admin_user.id, scenario="BULL",
    )
    with pytest.raises(ResearchValidationError):
        ValuationEngineService.create_scenario_valuation(
            company_id=company.id, scenario="BASE", valuation_method=ValuationMethod.EV_EBITDA,
            reference_specs=[
                {
                    "forecast_revision_id": forecast_revision.id, "fiscal_year": 2027,
                    "metric": "EBITDA", "basis": "mismatched", "unit": "INR_CRORE",
                    "sort_order": 0,
                }
            ],
            as_of_date=AS_OF_DATE, created_by_user_id=admin_user.id,
            implied_enterprise_value=Decimal("1500.0000"), currency="INR", unit="CRORE",
            valuation_notes="x",
        )


def test_missing_metric_on_forecast_line_reports_limitation(admin_user, company):
    forecast_revision, _ = _forecast_with_ebitda_line(
        company_id=company.id, actor_user_id=admin_user.id, ebitda=None, scenario="BULL",
    )
    with pytest.raises(ResearchValidationError) as exc_info:
        ValuationEngineService.create_scenario_valuation(
            company_id=company.id, scenario="BULL", valuation_method=ValuationMethod.EV_EBITDA,
            reference_specs=[
                {
                    "forecast_revision_id": forecast_revision.id, "fiscal_year": 2027,
                    "metric": "EBITDA", "basis": "no ebitda stored", "unit": "INR_CRORE",
                    "sort_order": 0,
                }
            ],
            as_of_date=AS_OF_DATE, created_by_user_id=admin_user.id,
            implied_enterprise_value=Decimal("1500.0000"), currency="INR", unit="CRORE",
            valuation_notes="x",
        )
    assert "without fabricating" in str(exc_info.value.details)


def test_mixed_forecast_revisions_in_one_call_rejected(admin_user, company):
    revision_one, _ = _forecast_with_ebitda_line(
        company_id=company.id, actor_user_id=admin_user.id, scenario="BULL",
    )
    revision_two, _ = _forecast_with_ebitda_line(
        company_id=company.id, actor_user_id=admin_user.id, scenario="BULL",
        fiscal_year=2028, base_revision_id=revision_one.id,
    )
    with pytest.raises(ResearchValidationError):
        ValuationEngineService.create_scenario_valuation(
            company_id=company.id, scenario="BULL", valuation_method=ValuationMethod.EV_EBITDA,
            reference_specs=[
                {
                    "forecast_revision_id": revision_one.id, "fiscal_year": 2027,
                    "metric": "EBITDA", "basis": "a", "unit": "INR_CRORE", "sort_order": 0,
                },
                {
                    "forecast_revision_id": revision_two.id, "fiscal_year": 2028,
                    "metric": "EBITDA", "basis": "b", "unit": "INR_CRORE", "sort_order": 1,
                },
            ],
            as_of_date=AS_OF_DATE, created_by_user_id=admin_user.id,
            implied_enterprise_value=Decimal("1500.0000"), currency="INR", unit="CRORE",
            valuation_notes="x",
        )
