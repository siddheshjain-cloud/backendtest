"""Phase 3 Slice G: ``InvestmentCaseService``.

Investment Brain layer (genuinely new): ``SPA Research View`` and
beyond, strictly additive to it. Owns writes to ``InvestmentCase``,
``InvestmentCaseForecast``/``InvestmentCaseValuation`` (the link tables
recording the exact immutable scenario-family revisions a case was
built from), ``InvestmentCaseKillSwitch``, and
``InvestmentCaseGateResult``. No writes anywhere else in the schema --
``ResearchRevision`` is read-only here, never modified.

Mirrors ``HypothesisAssumptionService``/``CycleIntelligenceService``'s
transactional shape exactly: each write method is one atomic commit;
revision-chain integrity for ``InvestmentCase`` (no branching, no
duplicate revision numbers, no second genesis per company) and for
``InvestmentCaseGateResult`` (no branching, no second genesis per
``(investment_case_id, gate)``) is enforced at the database level by the
constraints declared on those models -- the checks here are defense in
depth, and the one race each chain can produce (two callers racing to
extend the same chain) is translated into a clean domain error exactly
like every prior Phase 3 slice's own race handling.

``get_scenario_return_spread`` is the one read-only query contract named
in design proposal §4 P3-G: computed live from the ``InvestmentCase``'s
linked Bull/Base/Bear ``ValuationRevision``s (``MID_CYCLE`` excluded, per
§4 P3-E/P3-G) against each valuation's own ``current_market_cap`` --
never fabricating a return for a scenario whose linked valuation lacks
an ``implied_future_equity_value``, consistent with every other Phase 3
engine's "report the limitation, don't fabricate" discipline.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

import sqlalchemy as sa

from app import db
from app.models.company import Company
from app.models.cycle_intelligence import CycleAssessment
from app.models.document import DocumentCompanyLink
from app.models.forecast import ForecastRevision
from app.models.investment_case import (
    InvestmentCase,
    InvestmentCaseForecast,
    InvestmentCaseGateResult,
    InvestmentCaseKillSwitch,
    InvestmentCaseValuation,
)
from app.models.investment_hypothesis import InvestmentHypothesis
from app.models.research import ResearchRevision
from app.models.research_brain import Evidence, ExtractedFact
from app.models.research_coverage import CandidateFinding, ResearchProposition
from app.models.research_types import (
    GateResultStatus,
    InvestmentCaseGate,
    InvestmentCaseView,
    InvestmentOrigin,
    Scenario,
)
from app.models.valuation import ValuationRevision
from app.utils.research_errors import ResearchNotFoundError, ResearchValidationError

_VIEWS = (
    InvestmentCaseView.ADD,
    InvestmentCaseView.HOLD,
    InvestmentCaseView.DO_NOT_ADD,
    InvestmentCaseView.REDUCE,
    InvestmentCaseView.EXIT,
)
_GATES = (
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
_GATE_STATUSES = (
    GateResultStatus.PASS,
    GateResultStatus.FAIL,
    GateResultStatus.WATCH,
    GateResultStatus.NOT_ASSESSED,
    GateResultStatus.WAIVED,
)
_GATE_PROVENANCE_FIELDS = (
    "fact_id",
    "evidence_id",
    "candidate_finding_id",
    "research_proposition_id",
    "cycle_assessment_id",
)
_RETURN_SCENARIOS = (Scenario.BULL, Scenario.BASE, Scenario.BEAR)


class InvestmentCaseService:
    """Create/query ``InvestmentCase``, ``InvestmentCaseKillSwitch`` and
    ``InvestmentCaseGateResult`` rows. No writes anywhere else in the
    schema."""

    @classmethod
    def _document_ids_for_company(cls, company_id: str) -> set[str]:
        return set(
            db.session.scalars(
                sa.select(DocumentCompanyLink.document_id).where(
                    DocumentCompanyLink.company_id == company_id
                )
            ).all()
        )

    @classmethod
    def _validate_gate_provenance(
        cls,
        *,
        company_id: str,
        fact_id: str | None,
        evidence_id: str | None,
        candidate_finding_id: str | None,
        research_proposition_id: str | None,
        cycle_assessment_id: str | None,
    ) -> None:
        if fact_id is not None:
            fact = db.session.get(ExtractedFact, fact_id)
            if fact is None:
                raise ResearchNotFoundError(
                    "extracted_fact_not_found", "ExtractedFact was not found"
                )
            if fact.company_id != company_id:
                raise ResearchValidationError(
                    {"provenance": ["Fact must belong to the same company"]}
                )
        elif evidence_id is not None:
            evidence = db.session.get(Evidence, evidence_id)
            if evidence is None:
                raise ResearchNotFoundError(
                    "evidence_not_found", "Evidence was not found"
                )
            if evidence.document_id not in cls._document_ids_for_company(company_id):
                raise ResearchValidationError(
                    {"provenance": ["Evidence must belong to the same company"]}
                )
        elif candidate_finding_id is not None:
            candidate = db.session.get(CandidateFinding, candidate_finding_id)
            if candidate is None:
                raise ResearchNotFoundError(
                    "candidate_finding_not_found",
                    "CandidateFinding was not found",
                )
            if candidate.document_id not in cls._document_ids_for_company(company_id):
                raise ResearchValidationError(
                    {
                        "provenance": [
                            "CandidateFinding must belong to the same company"
                        ]
                    }
                )
        elif research_proposition_id is not None:
            proposition = db.session.get(ResearchProposition, research_proposition_id)
            if proposition is None:
                raise ResearchNotFoundError(
                    "research_proposition_not_found",
                    "ResearchProposition was not found",
                )
            if proposition.company_id != company_id:
                raise ResearchValidationError(
                    {
                        "provenance": [
                            "ResearchProposition must belong to the same company"
                        ]
                    }
                )
        elif cycle_assessment_id is not None:
            # CycleAssessment is World-layer and company-independent by
            # design (§4 P3-C) -- existence is the only check.
            if db.session.get(CycleAssessment, cycle_assessment_id) is None:
                raise ResearchNotFoundError(
                    "cycle_assessment_not_found", "CycleAssessment was not found"
                )

    @classmethod
    def create_investment_case(
        cls,
        *,
        company_id: str,
        research_revision_id: str,
        investment_hypothesis_id: str,
        view: str,
        as_of_date: date,
        forecast_revision_ids: list[str],
        valuation_revision_ids: list[str],
        created_by_user_id: str,
        cycle_assessment_id: str | None = None,
        expected_return_pct: Decimal | None = None,
        expected_return_basis: str | None = None,
        origin: str = InvestmentOrigin.HUMAN_AUTHORED,
        supersedes_case_id: str | None = None,
        change_reason: str | None = None,
    ) -> InvestmentCase:
        if view not in _VIEWS:
            raise ResearchValidationError({"view": [f"Must be one of {_VIEWS}"]})
        if db.session.get(Company, company_id) is None:
            raise ResearchNotFoundError("company_not_found", "Company was not found")

        research_revision = db.session.get(ResearchRevision, research_revision_id)
        if research_revision is None:
            raise ResearchNotFoundError(
                "research_revision_not_found", "ResearchRevision was not found"
            )
        if research_revision.company_id != company_id:
            raise ResearchValidationError(
                {"research_revision_id": ["Must belong to the same company"]}
            )

        hypothesis = db.session.get(InvestmentHypothesis, investment_hypothesis_id)
        if hypothesis is None:
            raise ResearchNotFoundError(
                "investment_hypothesis_not_found",
                "InvestmentHypothesis was not found",
            )
        if hypothesis.company_id != company_id:
            raise ResearchValidationError(
                {"investment_hypothesis_id": ["Must belong to the same company"]}
            )

        if cycle_assessment_id is not None:
            if db.session.get(CycleAssessment, cycle_assessment_id) is None:
                raise ResearchNotFoundError(
                    "cycle_assessment_not_found", "CycleAssessment was not found"
                )

        if not forecast_revision_ids:
            raise ResearchValidationError(
                {"forecast_revision_ids": ["At least one ForecastRevision must be cited"]}
            )
        if not valuation_revision_ids:
            raise ResearchValidationError(
                {"valuation_revision_ids": ["At least one ValuationRevision must be cited"]}
            )

        forecast_revisions = [
            db.session.get(ForecastRevision, revision_id)
            for revision_id in forecast_revision_ids
        ]
        missing_forecasts = [
            forecast_revision_ids[i]
            for i, revision in enumerate(forecast_revisions)
            if revision is None
        ]
        if missing_forecasts:
            raise ResearchNotFoundError(
                "forecast_revision_not_found",
                f"ForecastRevision(s) not found: {', '.join(missing_forecasts)}",
            )
        mismatched_forecasts = [
            revision.id for revision in forecast_revisions if revision.company_id != company_id
        ]
        if mismatched_forecasts:
            raise ResearchValidationError(
                {
                    "forecast_revision_ids": [
                        f"Must belong to the same company: {', '.join(mismatched_forecasts)}"
                    ]
                }
            )

        valuation_revisions = [
            db.session.get(ValuationRevision, revision_id)
            for revision_id in valuation_revision_ids
        ]
        missing_valuations = [
            valuation_revision_ids[i]
            for i, revision in enumerate(valuation_revisions)
            if revision is None
        ]
        if missing_valuations:
            raise ResearchNotFoundError(
                "valuation_revision_not_found",
                f"ValuationRevision(s) not found: {', '.join(missing_valuations)}",
            )
        mismatched_valuations = [
            revision.id for revision in valuation_revisions if revision.company_id != company_id
        ]
        if mismatched_valuations:
            raise ResearchValidationError(
                {
                    "valuation_revision_ids": [
                        f"Must belong to the same company: {', '.join(mismatched_valuations)}"
                    ]
                }
            )

        if supersedes_case_id is not None:
            predecessor = db.session.get(InvestmentCase, supersedes_case_id)
            if predecessor is None:
                raise ResearchNotFoundError(
                    "investment_case_not_found",
                    "InvestmentCase to supersede was not found",
                )
            if predecessor.company_id != company_id:
                raise ResearchValidationError(
                    {"supersedes_case_id": ["Must belong to the same company"]}
                )
            if not change_reason or not change_reason.strip():
                raise ResearchValidationError(
                    {
                        "change_reason": [
                            "Required when superseding an existing investment case"
                        ]
                    }
                )
            revision_number = predecessor.revision_number + 1
        else:
            revision_number = 1

        case = InvestmentCase(
            company_id=company_id,
            research_revision_id=research_revision_id,
            investment_hypothesis_id=investment_hypothesis_id,
            cycle_assessment_id=cycle_assessment_id,
            view=view,
            expected_return_pct=expected_return_pct,
            expected_return_basis=expected_return_basis,
            as_of_date=as_of_date,
            revision_number=revision_number,
            supersedes_case_id=supersedes_case_id,
            origin=origin,
            change_reason=change_reason,
            created_by_user_id=created_by_user_id,
        )
        try:
            db.session.add(case)
            db.session.flush()
            for revision_id in forecast_revision_ids:
                db.session.add(
                    InvestmentCaseForecast(
                        investment_case_id=case.id,
                        forecast_revision_id=revision_id,
                    )
                )
            for revision_id in valuation_revision_ids:
                db.session.add(
                    InvestmentCaseValuation(
                        investment_case_id=case.id,
                        valuation_revision_id=revision_id,
                    )
                )
            db.session.commit()
        except sa.exc.IntegrityError:
            db.session.rollback()
            raise ResearchValidationError(
                {
                    "supersedes_case_id": [
                        "Already superseded by a concurrent call, or this "
                        "company already has a genesis investment case -- "
                        "retry against the current revision"
                    ]
                }
            )
        except Exception:
            db.session.rollback()
            raise
        return case

    @classmethod
    def get_current_investment_case(cls, company_id: str) -> InvestmentCase | None:
        """Deterministic by construction -- see module docstring. Uses
        ``one_or_none()`` so a constraint violation would fail loudly
        rather than silently picking a row."""

        superseded_ids = sa.select(InvestmentCase.supersedes_case_id).where(
            InvestmentCase.supersedes_case_id.is_not(None)
        )
        return db.session.scalars(
            sa.select(InvestmentCase).where(
                InvestmentCase.company_id == company_id,
                InvestmentCase.id.not_in(superseded_ids),
            )
        ).one_or_none()

    @classmethod
    def get_investment_case_history(cls, company_id: str) -> list[InvestmentCase]:
        return list(
            db.session.scalars(
                sa.select(InvestmentCase)
                .where(InvestmentCase.company_id == company_id)
                .order_by(InvestmentCase.revision_number.asc())
            ).all()
        )

    @classmethod
    def record_kill_switch(
        cls,
        *,
        investment_case_id: str,
        condition: str,
        created_by_user_id: str,
        data_source_hint: str | None = None,
    ) -> InvestmentCaseKillSwitch:
        if db.session.get(InvestmentCase, investment_case_id) is None:
            raise ResearchNotFoundError(
                "investment_case_not_found", "InvestmentCase was not found"
            )

        kill_switch = InvestmentCaseKillSwitch(
            investment_case_id=investment_case_id,
            condition=condition,
            data_source_hint=data_source_hint,
            created_by_user_id=created_by_user_id,
        )
        try:
            db.session.add(kill_switch)
            db.session.commit()
        except Exception:
            db.session.rollback()
            raise
        return kill_switch

    @classmethod
    def trigger_kill_switch(
        cls, *, kill_switch_id: str, triggered_at: datetime | None = None
    ) -> InvestmentCaseKillSwitch:
        kill_switch = db.session.get(InvestmentCaseKillSwitch, kill_switch_id)
        if kill_switch is None:
            raise ResearchNotFoundError(
                "investment_case_kill_switch_not_found",
                "InvestmentCaseKillSwitch was not found",
            )
        if kill_switch.triggered_at is not None:
            raise ResearchValidationError(
                {"triggered_at": ["This kill switch has already been triggered"]}
            )

        kill_switch.triggered_at = triggered_at or datetime.now(timezone.utc)
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
            raise
        return kill_switch

    @classmethod
    def record_gate_result(
        cls,
        *,
        investment_case_id: str,
        gate: str,
        status: str,
        rationale: str,
        created_by_user_id: str,
        fact_id: str | None = None,
        evidence_id: str | None = None,
        candidate_finding_id: str | None = None,
        research_proposition_id: str | None = None,
        cycle_assessment_id: str | None = None,
        supersedes_gate_result_id: str | None = None,
    ) -> InvestmentCaseGateResult:
        case = db.session.get(InvestmentCase, investment_case_id)
        if case is None:
            raise ResearchNotFoundError(
                "investment_case_not_found", "InvestmentCase was not found"
            )
        if gate not in _GATES:
            raise ResearchValidationError({"gate": [f"Must be one of {_GATES}"]})
        if status not in _GATE_STATUSES:
            raise ResearchValidationError(
                {"status": [f"Must be one of {_GATE_STATUSES}"]}
            )

        provenance = {
            "fact_id": fact_id,
            "evidence_id": evidence_id,
            "candidate_finding_id": candidate_finding_id,
            "research_proposition_id": research_proposition_id,
            "cycle_assessment_id": cycle_assessment_id,
        }
        if sum(1 for value in provenance.values() if value is not None) != 1:
            raise ResearchValidationError(
                {
                    "provenance": [
                        "Exactly one of " + "/".join(_GATE_PROVENANCE_FIELDS) +
                        " must be set"
                    ]
                }
            )
        cls._validate_gate_provenance(company_id=case.company_id, **provenance)

        if supersedes_gate_result_id is not None:
            predecessor = db.session.get(
                InvestmentCaseGateResult, supersedes_gate_result_id
            )
            if predecessor is None:
                raise ResearchNotFoundError(
                    "investment_case_gate_result_not_found",
                    "InvestmentCaseGateResult to supersede was not found",
                )
            if (
                predecessor.investment_case_id != investment_case_id
                or predecessor.gate != gate
            ):
                raise ResearchValidationError(
                    {
                        "supersedes_gate_result_id": [
                            "Must belong to the same investment case and gate"
                        ]
                    }
                )

        gate_result = InvestmentCaseGateResult(
            investment_case_id=investment_case_id,
            gate=gate,
            status=status,
            rationale=rationale,
            supersedes_gate_result_id=supersedes_gate_result_id,
            created_by_user_id=created_by_user_id,
            **provenance,
        )
        try:
            db.session.add(gate_result)
            db.session.commit()
        except sa.exc.IntegrityError:
            db.session.rollback()
            raise ResearchValidationError(
                {
                    "supersedes_gate_result_id": [
                        "Already superseded by a concurrent call, or this "
                        "gate already has a genesis assessment for this "
                        "case -- retry against the current assessment"
                    ]
                }
            )
        except Exception:
            db.session.rollback()
            raise
        return gate_result

    @classmethod
    def get_current_gate_result(
        cls, investment_case_id: str, gate: str
    ) -> InvestmentCaseGateResult | None:
        superseded_ids = sa.select(
            InvestmentCaseGateResult.supersedes_gate_result_id
        ).where(InvestmentCaseGateResult.supersedes_gate_result_id.is_not(None))
        return db.session.scalars(
            sa.select(InvestmentCaseGateResult).where(
                InvestmentCaseGateResult.investment_case_id == investment_case_id,
                InvestmentCaseGateResult.gate == gate,
                InvestmentCaseGateResult.id.not_in(superseded_ids),
            )
        ).one_or_none()

    @classmethod
    def get_current_gate_results(
        cls, investment_case_id: str
    ) -> list[InvestmentCaseGateResult]:
        """Every gate's current (non-superseded) assessment for one case,
        at most one row per gate by construction."""

        superseded_ids = sa.select(
            InvestmentCaseGateResult.supersedes_gate_result_id
        ).where(InvestmentCaseGateResult.supersedes_gate_result_id.is_not(None))
        return list(
            db.session.scalars(
                sa.select(InvestmentCaseGateResult)
                .where(
                    InvestmentCaseGateResult.investment_case_id == investment_case_id,
                    InvestmentCaseGateResult.id.not_in(superseded_ids),
                )
                .order_by(InvestmentCaseGateResult.gate.asc())
            ).all()
        )

    @classmethod
    def get_scenario_return_spread(cls, investment_case_id: str) -> dict:
        """Computed live from the linked Bull/Base/Bear ``ValuationRevision``s
        -- no new stored field. ``MID_CYCLE`` is excluded (§4 P3-E/P3-G).
        A scenario with no linked valuation, or a linked valuation with
        no ``implied_future_equity_value``, is reported as unavailable
        rather than fabricated."""

        if db.session.get(InvestmentCase, investment_case_id) is None:
            raise ResearchNotFoundError(
                "investment_case_not_found", "InvestmentCase was not found"
            )

        valuation_ids = sa.select(InvestmentCaseValuation.valuation_revision_id).where(
            InvestmentCaseValuation.investment_case_id == investment_case_id
        )
        valuations = list(
            db.session.scalars(
                sa.select(ValuationRevision).where(
                    ValuationRevision.id.in_(valuation_ids),
                    ValuationRevision.scenario.in_(_RETURN_SCENARIOS),
                )
            ).all()
        )

        by_scenario: dict[str, ValuationRevision] = {}
        for valuation in valuations:
            if valuation.scenario in by_scenario:
                raise ResearchValidationError(
                    {
                        "valuation_revision_ids": [
                            f"More than one linked ValuationRevision shares "
                            f"scenario {valuation.scenario!r} -- the scenario "
                            "family is not coherent"
                        ]
                    }
                )
            by_scenario[valuation.scenario] = valuation

        scenarios: dict[str, dict | None] = {}
        for scenario in _RETURN_SCENARIOS:
            valuation = by_scenario.get(scenario)
            if valuation is None:
                scenarios[scenario] = None
                continue

            fair_value = valuation.implied_future_equity_value
            market_cap = valuation.current_market_cap
            return_pct: Decimal | None = None
            if fair_value is not None and market_cap is not None and market_cap != 0:
                return_pct = (fair_value - market_cap) / market_cap * Decimal("100")

            scenarios[scenario] = {
                "valuation_revision_id": valuation.id,
                "implied_future_equity_value": fair_value,
                "current_market_cap": market_cap,
                "return_pct": return_pct,
            }

        def _return_pct(scenario: str) -> Decimal | None:
            entry = scenarios.get(scenario)
            return entry["return_pct"] if entry else None

        bull_return = _return_pct(Scenario.BULL)
        base_return = _return_pct(Scenario.BASE)
        bear_return = _return_pct(Scenario.BEAR)

        upside_pct = (
            bull_return - base_return
            if bull_return is not None and base_return is not None
            else None
        )
        downside_pct = (
            base_return - bear_return
            if base_return is not None and bear_return is not None
            else None
        )

        return {
            "investment_case_id": investment_case_id,
            "scenarios": scenarios,
            "upside_pct": upside_pct,
            "downside_pct": downside_pct,
        }
