"""Phase 3 Slice E: ``ForecastEngineService``.

Research Brain Forecast step: ``Hypothesis/Inference -> Forecast ->
(feeds) Valuation``. A thin wrapper around the existing, unchanged
``ResearchCommandService.create_forecast_revision`` path -- it resolves
cited ``ForecastAssumption`` rows (Slice D), enforces the two rules the
design doc requires in code rather than in the schema, computes
``ForecastLine`` values, and records the citation trail via
``ForecastRevisionAssumption``. It creates no parallel forecast
mechanism: every ``ForecastRevision`` it produces is a real row created
by the real, pre-existing revision-chain code path, now correctly
scenario-scoped (see ``ResearchCommandService._current_forecast_revision_locked``).

Two rules enforced here, by design-doc requirement, neither expressible
as a single-table database constraint:

1. **Scenario consistency.** Every cited ``ForecastAssumption`` must
   carry the same ``scenario`` value being forecast -- a BULL forecast
   cannot cite a BASE-scenario assumption.
2. **Hypothesis-revision consistency.** Every cited assumption must
   share one ``investment_hypothesis_id`` -- mixing assumptions from two
   different hypothesis revisions into one forecast is rejected.
3. **Scenario-family coherence (§4 P3-A).** Every scenario forecast ever
   built from the same ``investment_hypothesis_id`` must share one
   ``as_of_date`` -- checked against every ``ForecastRevision`` already
   linked to that hypothesis via ``ForecastRevisionAssumption``, not just
   against revisions created in the same call.

**Reproducible, non-fabricating arithmetic (deliberately simple, since
the design doc leaves the arithmetic unspecified):** a ``ForecastLine``
field is populated *only* by a direct, unweighted pass-through of a
cited assumption's own ``metric_slug``/``assumed_value`` -- e.g. an
assumption with ``metric_slug="ebitda_margin_pct"`` and
``assumed_value="24.5"`` sets exactly that field, verbatim. Nothing is
multiplied against an inferred base, extrapolated, or interpolated. If
none of the cited assumptions carry a usable numeric
``metric_slug``/``assumed_value``, this raises rather than creating an
empty or invented ``ForecastLine`` -- the explicit "report the
limitation, don't fabricate" contract.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation

import sqlalchemy as sa

from app import db
from app.models import ForecastRevision
from app.models.forecast_revision_assumption import ForecastRevisionAssumption
from app.models.investment_hypothesis import ForecastAssumption, InvestmentHypothesis
from app.models.research_types import Scenario
from app.services.research_command_service import ResearchCommandService
from app.utils.research_errors import ResearchNotFoundError, ResearchValidationError

_NUMERIC_LINE_FIELDS = {"revenue", "ebitda", "pat", "ebitda_margin_pct", "eps"}
_SCENARIOS = (Scenario.BULL, Scenario.BASE, Scenario.BEAR, Scenario.MID_CYCLE)


class ForecastEngineService:
    """Builds one scenario-tagged ``ForecastRevision`` from already-cited
    ``ForecastAssumption`` rows. No writes anywhere else in the schema."""

    @classmethod
    def _resolve_assumptions(
        cls, assumption_ids: list[str], scenario: str
    ) -> list[ForecastAssumption]:
        assumptions = [
            db.session.get(ForecastAssumption, assumption_id)
            for assumption_id in assumption_ids
        ]
        missing = [
            assumption_ids[i]
            for i, assumption in enumerate(assumptions)
            if assumption is None
        ]
        if missing:
            raise ResearchNotFoundError(
                "forecast_assumption_not_found",
                f"ForecastAssumption(s) not found: {', '.join(missing)}",
            )

        mismatched = [
            assumption.id for assumption in assumptions if assumption.scenario != scenario
        ]
        if mismatched:
            raise ResearchValidationError(
                {
                    "assumption_ids": [
                        f"ForecastAssumption(s) {', '.join(mismatched)} do "
                        f"not match the requested scenario {scenario!r}"
                    ]
                }
            )

        hypothesis_ids = {assumption.investment_hypothesis_id for assumption in assumptions}
        if len(hypothesis_ids) != 1:
            raise ResearchValidationError(
                {
                    "assumption_ids": [
                        "All cited assumptions must share one "
                        "investment_hypothesis_id -- got "
                        f"{sorted(hypothesis_ids)}"
                    ]
                }
            )
        return assumptions

    @classmethod
    def _validate_coherent_as_of_date(
        cls, investment_hypothesis_id: str, as_of_date: date
    ) -> None:
        existing_dates = set(
            db.session.scalars(
                sa.select(ForecastRevision.as_of_date)
                .join(
                    ForecastRevisionAssumption,
                    ForecastRevisionAssumption.forecast_revision_id == ForecastRevision.id,
                )
                .join(
                    ForecastAssumption,
                    ForecastAssumption.id
                    == ForecastRevisionAssumption.forecast_assumption_id,
                )
                .where(ForecastAssumption.investment_hypothesis_id == investment_hypothesis_id)
            ).all()
        )
        if existing_dates and as_of_date not in existing_dates:
            raise ResearchValidationError(
                {
                    "as_of_date": [
                        "Must match the coherent scenario family's "
                        f"existing as_of_date(s): {sorted(existing_dates)}"
                    ]
                }
            )

    @classmethod
    def _resolve_numeric_line_values(
        cls, assumptions: list[ForecastAssumption]
    ) -> dict[str, Decimal]:
        values: dict[str, Decimal] = {}
        for assumption in assumptions:
            if (
                assumption.metric_slug not in _NUMERIC_LINE_FIELDS
                or assumption.assumed_value is None
            ):
                continue
            try:
                values[assumption.metric_slug] = Decimal(assumption.assumed_value)
            except InvalidOperation:
                raise ResearchValidationError(
                    {
                        "assumption_ids": [
                            f"ForecastAssumption {assumption.id}'s "
                            f"assumed_value {assumption.assumed_value!r} is "
                            "not a valid decimal"
                        ]
                    }
                )

        if not values:
            raise ResearchValidationError(
                {
                    "assumption_ids": [
                        "None of the cited assumptions carry a usable "
                        "numeric metric_slug/assumed_value -- a "
                        "ForecastLine cannot be computed without "
                        "fabricating a figure. Record a numeric "
                        "assumed_value on at least one cited assumption "
                        "(metric_slug in "
                        f"{sorted(_NUMERIC_LINE_FIELDS)}) first."
                    ]
                }
            )
        return values

    @classmethod
    def create_scenario_forecast(
        cls,
        *,
        company_id: str,
        scenario: str,
        assumption_ids: list[str],
        fiscal_years: list[int],
        as_of_date: date,
        unit: str,
        created_by_user_id: str,
        base_revision_id: str | None = None,
        change_reason: str | None = None,
    ) -> ForecastRevision:
        if scenario not in _SCENARIOS:
            raise ResearchValidationError(
                {"scenario": [f"Must be one of {_SCENARIOS}"]}
            )
        if not assumption_ids:
            raise ResearchValidationError(
                {"assumption_ids": ["At least one ForecastAssumption must be cited"]}
            )
        if not fiscal_years:
            raise ResearchValidationError(
                {"fiscal_years": ["At least one fiscal year must be requested"]}
            )

        assumptions = cls._resolve_assumptions(assumption_ids, scenario)
        investment_hypothesis_id = assumptions[0].investment_hypothesis_id
        hypothesis = db.session.get(InvestmentHypothesis, investment_hypothesis_id)
        if hypothesis is None:
            raise ResearchNotFoundError(
                "investment_hypothesis_not_found",
                "InvestmentHypothesis was not found",
            )
        if hypothesis.company_id != company_id:
            raise ResearchValidationError(
                {
                    "company_id": [
                        "Cited assumptions' hypothesis must belong to the "
                        "same company"
                    ]
                }
            )

        cls._validate_coherent_as_of_date(investment_hypothesis_id, as_of_date)
        numeric_values = cls._resolve_numeric_line_values(assumptions)

        lines = [
            {**numeric_values, "fiscal_year": fiscal_year, "is_estimate": True, "unit": unit}
            for fiscal_year in fiscal_years
        ]
        payload = {
            "as_of_date": as_of_date,
            "scenario": scenario,
            "change_reason": change_reason,
            "lines": lines,
        }
        if base_revision_id is not None:
            payload["base_revision_id"] = base_revision_id

        revision = ResearchCommandService.create_forecast_revision(
            company_id, created_by_user_id, payload
        )

        try:
            for assumption_id in assumption_ids:
                db.session.add(
                    ForecastRevisionAssumption(
                        forecast_revision_id=revision.id,
                        forecast_assumption_id=assumption_id,
                    )
                )
            db.session.commit()
        except Exception:
            db.session.rollback()
            raise
        return revision
