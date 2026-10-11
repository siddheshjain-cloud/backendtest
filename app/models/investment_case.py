"""Phase 3 Slice G -- Investment View / ResearchRevision.

Investment Brain layer (genuinely new), strictly additive to the SPA
Research View: ``SPA Research View`` and beyond. The one rule this
module cannot violate (design proposal §7, §19.D): no second
authoritative research-view table. ``ResearchRevision`` itself is not
modified -- no new column, no reverse reference added to it.

``InvestmentCase`` is one evolving investment decision thread per
company -- the same "one chain per scope" revision-integrity shape
Slice C proved on ``CycleAssessment`` and Slice D proved on
``InvestmentHypothesis``:
  1. ``uq_investment_case_company_revision`` -- no two rows for the same
     company share a ``revision_number``.
  2. ``uq_investment_case_supersedes`` -- unique on ``supersedes_case_id``,
     so a row can be superseded by at most one successor (no branching).
  3. ``uq_investment_case_single_genesis`` -- partial unique index on
     ``company_id`` where ``supersedes_case_id IS NULL`` (at most one
     genesis row per company).
Together, as in Slices C/D, this makes "the current investment case for
this company" unique and race-safe by construction. ``origin``
(``SYSTEM_DRAFT``/``HUMAN_AUTHORED``) follows the §9a promotion contract
exactly: no ``promoted_by_user_id``/``promoted_at`` field -- this table
is immutable after insertion, so promotion is a new ``HUMAN_AUTHORED``
row whose ``supersedes_case_id`` names the exact ``SYSTEM_DRAFT`` row it
promotes.

``InvestmentCaseForecast``/``InvestmentCaseValuation`` are plain,
immutable join tables -- the same row-per-citation idiom as
``ForecastRevisionAssumption`` -- recording the exact, authoritative set
of immutable scenario-family ``ForecastRevision``/``ValuationRevision``
ids one ``InvestmentCase`` was built from. This is what guarantees
point-in-time scenario-family reconstruction (design proposal §4 P3-A);
no separate ``ScenarioSet`` wrapper table is needed on top of it.

``InvestmentCaseGateResult`` is the nine-gate assessment, one row per
gate judgment. ``gate``/``status`` are closed ``CHECK``-constrained
enums (via ``enum_type``, matching the rest of this codebase's non-native
``sa.Enum`` idiom) rather than open lookup tables -- a deliberate
deviation from ``ResearchDimension``/``PropositionStageType``'s usual
"new values are data inserts" convention, because the nine gates and
five statuses are a curated, owner-fixed taxonomy, not an open sweep
vocabulary expected to grow. ``provenance`` is a five-way "exactly one
source" union, the same summed-CASE idiom ``ForecastAssumption`` already
uses. ``supersedes_gate_result_id`` gives one append-only supersession
chain per ``(investment_case_id, gate)`` -- the same revision-chain
integrity pattern as ``CycleAssessment``/``InvestmentHypothesis``, minus
an explicit ``revision_number`` column (not named in the design
proposal's shape for this table): a unique constraint on
``supersedes_gate_result_id`` rejects branching, and a partial unique
index on ``(investment_case_id, gate)`` where
``supersedes_gate_result_id IS NULL`` rejects a second genesis row for
the same gate -- together making "the current assessment of this gate"
deterministic and race-safe, exactly like every other revision chain in
this codebase.

``InvestmentCaseKillSwitch`` is the one deliberate exception to this
module's immutability idiom: ``triggered_at`` starts ``NULL`` and may be
set exactly once (never cleared, never re-set) -- a condition fires or it
doesn't, and once fired that fact is permanent. Every other field, and
every row once ``triggered_at`` is already set, remains immutable. This
is enforced by a dedicated ``before_update`` listener on this one model,
not the blanket "reject every update" listener every other Phase 3
table uses.

``get_scenario_return_spread`` (``InvestmentCaseService``) is a
read-only query contract computed live from the linked Bull/Base/Bear
``ValuationRevision``s against each one's own ``current_market_cap`` --
no new stored field, and ``MID_CYCLE`` is excluded from it exactly as
design proposal §4 P3-E/P3-G require (it is a normalized-earnings
reference point, not a probability-weighted scenario).
"""

from __future__ import annotations

from datetime import date, datetime

import sqlalchemy as sa
import sqlalchemy.orm as so

from app.models.base import BaseModel
from app.models.research_types import (
    GateResultStatus,
    InvestmentCaseGate,
    InvestmentCaseView,
    InvestmentOrigin,
    enum_type,
)


class InvestmentCase(BaseModel):
    """One evolving investment decision for one company."""

    __tablename__ = "investment_case"

    company_id: so.Mapped[str] = so.mapped_column(
        sa.ForeignKey("company.id"), nullable=False
    )
    research_revision_id: so.Mapped[str] = so.mapped_column(
        sa.ForeignKey("research_revision.id"), nullable=False
    )
    investment_hypothesis_id: so.Mapped[str] = so.mapped_column(
        sa.ForeignKey("investment_hypothesis.id"), nullable=False
    )
    cycle_assessment_id: so.Mapped[str | None] = so.mapped_column(
        sa.ForeignKey("cycle_assessment.id"), nullable=True
    )
    view: so.Mapped[str] = so.mapped_column(
        enum_type(
            "investment_case_view",
            (
                InvestmentCaseView.ADD,
                InvestmentCaseView.HOLD,
                InvestmentCaseView.DO_NOT_ADD,
                InvestmentCaseView.REDUCE,
                InvestmentCaseView.EXIT,
            ),
        ),
        nullable=False,
    )
    expected_return_pct: so.Mapped[sa.Numeric | None] = so.mapped_column(
        sa.Numeric(20, 4), nullable=True
    )
    expected_return_basis: so.Mapped[str | None] = so.mapped_column(
        sa.Text, nullable=True
    )
    as_of_date: so.Mapped[date] = so.mapped_column(sa.Date, nullable=False)
    revision_number: so.Mapped[int] = so.mapped_column(sa.Integer, nullable=False)
    supersedes_case_id: so.Mapped[str | None] = so.mapped_column(
        sa.ForeignKey("investment_case.id"), nullable=True
    )
    origin: so.Mapped[str] = so.mapped_column(
        enum_type(
            "investment_case_origin",
            (InvestmentOrigin.SYSTEM_DRAFT, InvestmentOrigin.HUMAN_AUTHORED),
        ),
        nullable=False,
        server_default=InvestmentOrigin.HUMAN_AUTHORED,
    )
    change_reason: so.Mapped[str | None] = so.mapped_column(
        sa.String(2000), nullable=True
    )
    created_by_user_id: so.Mapped[str] = so.mapped_column(
        sa.ForeignKey("user.id"), nullable=False
    )

    __table_args__ = (
        sa.UniqueConstraint(
            "company_id", "revision_number",
            name="uq_investment_case_company_revision",
        ),
        sa.UniqueConstraint(
            "supersedes_case_id", name="uq_investment_case_supersedes",
        ),
        sa.Index(
            "uq_investment_case_single_genesis",
            "company_id",
            unique=True,
            sqlite_where=sa.text("supersedes_case_id IS NULL"),
            postgresql_where=sa.text("supersedes_case_id IS NULL"),
        ),
    )

    company: so.Mapped["Company"] = so.relationship("Company", viewonly=True)
    research_revision: so.Mapped["ResearchRevision"] = so.relationship(
        "ResearchRevision", viewonly=True
    )
    investment_hypothesis: so.Mapped["InvestmentHypothesis"] = so.relationship(
        "InvestmentHypothesis", viewonly=True
    )
    cycle_assessment: so.Mapped["CycleAssessment | None"] = so.relationship(
        "CycleAssessment", viewonly=True
    )
    supersedes: so.Mapped["InvestmentCase | None"] = so.relationship(
        "InvestmentCase", remote_side="InvestmentCase.id",
        viewonly=True, uselist=False,
    )
    created_by_user: so.Mapped["User"] = so.relationship("User", viewonly=True)
    forecast_links: so.Mapped[list["InvestmentCaseForecast"]] = so.relationship(
        "InvestmentCaseForecast", viewonly=True
    )
    valuation_links: so.Mapped[list["InvestmentCaseValuation"]] = so.relationship(
        "InvestmentCaseValuation", viewonly=True
    )
    kill_switches: so.Mapped[list["InvestmentCaseKillSwitch"]] = so.relationship(
        "InvestmentCaseKillSwitch", viewonly=True
    )
    gate_results: so.Mapped[list["InvestmentCaseGateResult"]] = so.relationship(
        "InvestmentCaseGateResult", viewonly=True
    )

    def __repr__(self) -> str:
        return f"<InvestmentCase company={self.company_id} v{self.revision_number}>"


class InvestmentCaseForecast(BaseModel):
    """One citation of one exact, immutable ``ForecastRevision`` by one
    ``InvestmentCase``. Immutable, append-only."""

    __tablename__ = "investment_case_forecast"

    investment_case_id: so.Mapped[str] = so.mapped_column(
        sa.ForeignKey("investment_case.id"), nullable=False
    )
    forecast_revision_id: so.Mapped[str] = so.mapped_column(
        sa.ForeignKey("forecast_revision.id"), nullable=False
    )

    __table_args__ = (
        sa.UniqueConstraint(
            "investment_case_id", "forecast_revision_id",
            name="uq_investment_case_forecast_pair",
        ),
    )

    investment_case: so.Mapped["InvestmentCase"] = so.relationship(
        "InvestmentCase", viewonly=True
    )
    forecast_revision: so.Mapped["ForecastRevision"] = so.relationship(
        "ForecastRevision", viewonly=True
    )

    def __repr__(self) -> str:
        return (
            f"<InvestmentCaseForecast case={self.investment_case_id} "
            f"forecast={self.forecast_revision_id}>"
        )


class InvestmentCaseValuation(BaseModel):
    """One citation of one exact, immutable ``ValuationRevision`` by one
    ``InvestmentCase``. Immutable, append-only."""

    __tablename__ = "investment_case_valuation"

    investment_case_id: so.Mapped[str] = so.mapped_column(
        sa.ForeignKey("investment_case.id"), nullable=False
    )
    valuation_revision_id: so.Mapped[str] = so.mapped_column(
        sa.ForeignKey("valuation_revision.id"), nullable=False
    )

    __table_args__ = (
        sa.UniqueConstraint(
            "investment_case_id", "valuation_revision_id",
            name="uq_investment_case_valuation_pair",
        ),
    )

    investment_case: so.Mapped["InvestmentCase"] = so.relationship(
        "InvestmentCase", viewonly=True
    )
    valuation_revision: so.Mapped["ValuationRevision"] = so.relationship(
        "ValuationRevision", viewonly=True
    )

    def __repr__(self) -> str:
        return (
            f"<InvestmentCaseValuation case={self.investment_case_id} "
            f"valuation={self.valuation_revision_id}>"
        )


class InvestmentCaseKillSwitch(BaseModel):
    """One named, falsifiable condition that would invalidate an
    ``InvestmentCase``. ``triggered_at`` starts ``NULL`` and may be set
    exactly once -- see module docstring; this is the one Phase 3 Slice
    G row that is not fully immutable after insertion."""

    __tablename__ = "investment_case_kill_switch"

    investment_case_id: so.Mapped[str] = so.mapped_column(
        sa.ForeignKey("investment_case.id"), nullable=False
    )
    condition: so.Mapped[str] = so.mapped_column(sa.Text, nullable=False)
    data_source_hint: so.Mapped[str | None] = so.mapped_column(
        sa.String(500), nullable=True
    )
    triggered_at: so.Mapped[datetime | None] = so.mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )
    created_by_user_id: so.Mapped[str] = so.mapped_column(
        sa.ForeignKey("user.id"), nullable=False
    )

    investment_case: so.Mapped["InvestmentCase"] = so.relationship(
        "InvestmentCase", viewonly=True
    )
    created_by_user: so.Mapped["User"] = so.relationship("User", viewonly=True)

    def __repr__(self) -> str:
        return (
            f"<InvestmentCaseKillSwitch case={self.investment_case_id} "
            f"triggered={self.triggered_at is not None}>"
        )


class InvestmentCaseGateResult(BaseModel):
    """One judgment of one of the nine gates for one ``InvestmentCase``.
    Immutable, append-only; ``supersedes_gate_result_id`` chains later
    re-assessments of the same ``(investment_case_id, gate)`` -- see
    module docstring for the revision-integrity constraints."""

    __tablename__ = "investment_case_gate_result"

    investment_case_id: so.Mapped[str] = so.mapped_column(
        sa.ForeignKey("investment_case.id"), nullable=False
    )
    gate: so.Mapped[str] = so.mapped_column(
        enum_type(
            "investment_case_gate",
            (
                InvestmentCaseGate.GOVERNANCE,
                InvestmentCaseGate.TRANSFORMATION,
                InvestmentCaseGate.EXECUTION,
                InvestmentCaseGate.INSTITUTIONAL_ADVANTAGE,
                InvestmentCaseGate.FINANCIAL_VALIDATION,
                InvestmentCaseGate.MARKET_CAP_OPPORTUNITY,
                InvestmentCaseGate.OPTIONALITY,
                InvestmentCaseGate.VARIANT_PERCEPTION,
                InvestmentCaseGate.KILL_SWITCHES,
            ),
        ),
        nullable=False,
    )
    status: so.Mapped[str] = so.mapped_column(
        enum_type(
            "investment_case_gate_status",
            (
                GateResultStatus.PASS,
                GateResultStatus.FAIL,
                GateResultStatus.WATCH,
                GateResultStatus.NOT_ASSESSED,
                GateResultStatus.WAIVED,
            ),
        ),
        nullable=False,
    )
    rationale: so.Mapped[str] = so.mapped_column(sa.Text, nullable=False)
    fact_id: so.Mapped[str | None] = so.mapped_column(
        sa.ForeignKey("extracted_fact.id"), nullable=True
    )
    evidence_id: so.Mapped[str | None] = so.mapped_column(
        sa.ForeignKey("evidence.id"), nullable=True
    )
    candidate_finding_id: so.Mapped[str | None] = so.mapped_column(
        sa.ForeignKey("candidate_finding.id"), nullable=True
    )
    research_proposition_id: so.Mapped[str | None] = so.mapped_column(
        sa.ForeignKey("research_proposition.id"), nullable=True
    )
    cycle_assessment_id: so.Mapped[str | None] = so.mapped_column(
        sa.ForeignKey("cycle_assessment.id"), nullable=True
    )
    supersedes_gate_result_id: so.Mapped[str | None] = so.mapped_column(
        sa.ForeignKey("investment_case_gate_result.id"), nullable=True
    )
    created_by_user_id: so.Mapped[str] = so.mapped_column(
        sa.ForeignKey("user.id"), nullable=False
    )

    __table_args__ = (
        sa.CheckConstraint(
            "(CASE WHEN fact_id IS NOT NULL THEN 1 ELSE 0 END) + "
            "(CASE WHEN evidence_id IS NOT NULL THEN 1 ELSE 0 END) + "
            "(CASE WHEN candidate_finding_id IS NOT NULL THEN 1 ELSE 0 END) + "
            "(CASE WHEN research_proposition_id IS NOT NULL THEN 1 ELSE 0 END) + "
            "(CASE WHEN cycle_assessment_id IS NOT NULL THEN 1 ELSE 0 END) = 1",
            name="ck_investment_case_gate_result_exactly_one_provenance",
        ),
        sa.UniqueConstraint(
            "supersedes_gate_result_id",
            name="uq_investment_case_gate_result_supersedes",
        ),
        sa.Index(
            "uq_investment_case_gate_result_single_genesis",
            "investment_case_id", "gate",
            unique=True,
            sqlite_where=sa.text("supersedes_gate_result_id IS NULL"),
            postgresql_where=sa.text("supersedes_gate_result_id IS NULL"),
        ),
    )

    investment_case: so.Mapped["InvestmentCase"] = so.relationship(
        "InvestmentCase", viewonly=True
    )
    fact: so.Mapped["ExtractedFact | None"] = so.relationship(
        "ExtractedFact", viewonly=True
    )
    evidence: so.Mapped["Evidence | None"] = so.relationship(
        "Evidence", viewonly=True
    )
    candidate_finding: so.Mapped["CandidateFinding | None"] = so.relationship(
        "CandidateFinding", viewonly=True
    )
    research_proposition: so.Mapped["ResearchProposition | None"] = so.relationship(
        "ResearchProposition", viewonly=True
    )
    cycle_assessment: so.Mapped["CycleAssessment | None"] = so.relationship(
        "CycleAssessment", viewonly=True
    )
    supersedes: so.Mapped["InvestmentCaseGateResult | None"] = so.relationship(
        "InvestmentCaseGateResult",
        remote_side="InvestmentCaseGateResult.id",
        viewonly=True, uselist=False,
    )
    created_by_user: so.Mapped["User"] = so.relationship("User", viewonly=True)

    def __repr__(self) -> str:
        return (
            f"<InvestmentCaseGateResult case={self.investment_case_id} "
            f"{self.gate} {self.status}>"
        )


def _reject_immutable_update(_mapper, _connection, target) -> None:
    raise sa.exc.InvalidRequestError(
        f"{type(target).__name__} is immutable after insertion"
    )


def _reject_immutable_delete(_mapper, _connection, target) -> None:
    raise sa.exc.InvalidRequestError(
        f"{type(target).__name__} is immutable and cannot be deleted"
    )


def _reject_illegal_kill_switch_update(
    _mapper, _connection, target: InvestmentCaseKillSwitch
) -> None:
    """Allow exactly one legal mutation: ``triggered_at`` going from
    ``NULL`` to a non-``NULL`` value. Every other field, and any further
    change to ``triggered_at`` once set, is rejected."""

    state = sa.inspect(target)
    for attr in state.mapper.column_attrs:
        if attr.key == "triggered_at":
            continue
        if getattr(state.attrs, attr.key).history.has_changes():
            raise sa.exc.InvalidRequestError(
                "InvestmentCaseKillSwitch is immutable except for a "
                "single triggered_at transition from NULL"
            )

    triggered_history = getattr(state.attrs, "triggered_at").history
    if triggered_history.has_changes():
        previous = triggered_history.deleted[0] if triggered_history.deleted else None
        if previous is not None:
            raise sa.exc.InvalidRequestError(
                "InvestmentCaseKillSwitch.triggered_at cannot be changed "
                "once set"
            )


for _immutable_model in (
    InvestmentCase,
    InvestmentCaseForecast,
    InvestmentCaseValuation,
    InvestmentCaseGateResult,
):
    sa.event.listen(_immutable_model, "before_update", _reject_immutable_update)
    sa.event.listen(_immutable_model, "before_delete", _reject_immutable_delete)

sa.event.listen(
    InvestmentCaseKillSwitch, "before_update", _reject_illegal_kill_switch_update
)
sa.event.listen(
    InvestmentCaseKillSwitch, "before_delete", _reject_immutable_delete
)
