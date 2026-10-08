"""Phase 3 Slice F: ``ValuationEngineService``.

Research Brain Valuation step: ``Forecast -> Valuation -> (feeds) SPA
Research View / Investment Case``. A thin wrapper around the existing,
completely unchanged ``ResearchCommandService.create_valuation_revision``
path -- the same relationship ``ForecastEngineService`` has to
``create_forecast_revision``. No new table, no parallel valuation
mechanism: every ``ValuationRevision`` this produces is a real row
created by the real, pre-existing revision-chain code, now correctly
scenario-scoped (see
``ResearchCommandService._current_valuation_revision_locked``).

**The one thing this service adds that the lower-level path does not
already do:** ``create_valuation_revision``'s own
``_validate_valuation_forecast_link`` already checks that a cited
``ForecastRevision``/fiscal-year/metric combination exists and is
non-null, but it never checked that the caller-supplied
``reference_metric_value`` actually *equals* the value stored in that
``ForecastLine`` -- a caller could cite a real forecast for provenance
while asserting an unrelated number. ``_derive_reference_line`` below
closes that gap by reading the value directly off the real
``ForecastLine`` column and passing that exact value through --
``reference_metric_value`` is never caller-supplied here, only
derived, so a cited reference line and the forecast it names can never
diverge. This preserves the distinction the owner requires: reported
Facts, analyst-authored assumptions (``ForecastAssumption``, Slice D),
and calculated forecasts/valuations are different things, and a
valuation's reference value is a *calculated* read of a forecast, not
an analyst's independent fourth assertion.

Scenario consistency: the valuation's own ``scenario`` must match the
cited ``ForecastRevision``'s own ``scenario`` (including the legacy
``None`` case) -- the same category of coherence rule
``ForecastEngineService`` already enforces between an assumption's
scenario and the forecast built from it.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import sqlalchemy as sa

from app import db
from app.models import ForecastLine, ForecastRevision
from app.models.research_types import Scenario
from app.services.research_command_service import (
    _FORECAST_METRIC_COLUMNS,
    ResearchCommandService,
)
from app.utils.research_errors import ResearchNotFoundError, ResearchValidationError

_SCENARIOS = (Scenario.BULL, Scenario.BASE, Scenario.BEAR, Scenario.MID_CYCLE)


class ValuationEngineService:
    """Builds one scenario-tagged ``ValuationRevision`` whose reference
    lines are derived, never asserted. No writes anywhere else in the
    schema."""

    @classmethod
    def _derive_reference_line(
        cls,
        *,
        company_id: str,
        forecast_revision_id: str,
        fiscal_year: int,
        metric: str,
        basis: str,
        unit: str,
        sort_order: int,
    ) -> dict:
        forecast_revision = db.session.get(ForecastRevision, forecast_revision_id)
        if forecast_revision is None:
            raise ResearchNotFoundError(
                "forecast_revision_not_found", "Forecast revision was not found"
            )
        if forecast_revision.company_id != company_id:
            raise ResearchValidationError(
                {
                    "forecast_revision_id": [
                        "Must belong to the same company"
                    ]
                }
            )

        column_name = _FORECAST_METRIC_COLUMNS.get(metric)
        if column_name is None:
            raise ResearchValidationError(
                {
                    "metric": [
                        f"Must be one of {sorted(_FORECAST_METRIC_COLUMNS)}"
                    ]
                }
            )

        line = db.session.scalar(
            sa.select(ForecastLine).where(
                ForecastLine.forecast_revision_id == forecast_revision_id,
                ForecastLine.fiscal_year == fiscal_year,
            )
        )
        if line is None:
            raise ResearchValidationError(
                {"fiscal_year": ["Referenced fiscal-year line does not exist"]}
            )

        value: Decimal | None = getattr(line, column_name)
        if value is None:
            raise ResearchValidationError(
                {
                    "metric": [
                        f"{metric} is not stored in the referenced forecast "
                        "line -- cannot derive a reference value without "
                        "fabricating one"
                    ]
                }
            )

        return {
            "reference_forecast_revision_id": forecast_revision_id,
            "reference_fiscal_year": fiscal_year,
            "reference_metric": metric,
            "reference_metric_value": value,
            "reference_metric_unit": unit,
            "reference_metric_basis": basis,
            "sort_order": sort_order,
        }

    @classmethod
    def create_scenario_valuation(
        cls,
        *,
        company_id: str,
        scenario: str,
        valuation_method: str,
        reference_specs: list[dict],
        as_of_date: date,
        created_by_user_id: str,
        base_revision_id: str | None = None,
        change_reason: str | None = None,
        **header_fields: object,
    ):
        if scenario not in _SCENARIOS:
            raise ResearchValidationError(
                {"scenario": [f"Must be one of {_SCENARIOS}"]}
            )
        if not reference_specs:
            raise ResearchValidationError(
                {"reference_specs": ["At least one reference line must be cited"]}
            )

        forecast_revision_ids = {
            spec["forecast_revision_id"] for spec in reference_specs
        }
        if len(forecast_revision_ids) != 1:
            raise ResearchValidationError(
                {
                    "reference_specs": [
                        "All reference lines in one scenario valuation "
                        "must cite the same ForecastRevision"
                    ]
                }
            )
        forecast_revision_id = forecast_revision_ids.pop()

        forecast_revision = db.session.get(ForecastRevision, forecast_revision_id)
        if forecast_revision is None:
            raise ResearchNotFoundError(
                "forecast_revision_not_found", "Forecast revision was not found"
            )
        if forecast_revision.scenario != scenario:
            raise ResearchValidationError(
                {
                    "scenario": [
                        "Must match the cited ForecastRevision's own "
                        f"scenario ({forecast_revision.scenario!r})"
                    ]
                }
            )

        reference_lines = [
            cls._derive_reference_line(
                company_id=company_id,
                forecast_revision_id=forecast_revision_id,
                fiscal_year=spec["fiscal_year"],
                metric=spec["metric"],
                basis=spec["basis"],
                unit=spec["unit"],
                sort_order=spec["sort_order"],
            )
            for spec in reference_specs
        ]

        payload: dict[str, object] = {
            "valuation_method": valuation_method,
            "scenario": scenario,
            "as_of_date": as_of_date,
            "change_reason": change_reason,
            "reference_lines": reference_lines,
            **header_fields,
        }
        if base_revision_id is not None:
            payload["base_revision_id"] = base_revision_id

        return ResearchCommandService.create_valuation_revision(
            company_id, created_by_user_id, payload
        )
