"""Phase 3 Slice D -- Investment Hypothesis & Assumption Engine.

Research Brain layer (the Hypothesis/Inference step): ``Fact ->
Hypothesis/Inference -> (feeds) Forecast``. The Forecast step itself
(``ForecastRevisionAssumption``, scenario-forking logic, ``ForecastEngineService``
extension) is Slice E, explicitly not built here -- this module only
produces the atomic, citable ``ForecastAssumption`` rows a future
Slice E will consume.

``InvestmentHypothesis`` is one evolving thesis thread per company --
the same "one chain per scope" shape as ``ForecastRevision`` before
Slice A's scenario-fork (there is no `scenario` column on the hypothesis
itself; scenario-forking happens one level down, at ``ForecastAssumption``).
Revision integrity reuses, unchanged, the exact three-constraint pattern
Slice C proved on ``CycleAssessment``:
  1. ``uq_investment_hypothesis_company_revision`` -- no two rows for the
     same company share a ``revision_number``.
  2. ``uq_investment_hypothesis_supersedes`` -- unique on
     ``supersedes_revision_id``, so a row can be superseded by at most
     one successor (no branching).
  3. ``uq_investment_hypothesis_single_genesis`` -- partial unique index
     on ``company_id`` where ``supersedes_revision_id IS NULL`` (at most
     one genesis row per company).
Together, as in Slice C, this makes "the current hypothesis for this
company" unique and race-safe by construction.

``origin`` (``SYSTEM_DRAFT``/``HUMAN_AUTHORED``) follows the corrected
§9a promotion contract exactly: no ``promoted_by_user_id``/``promoted_at``
field (this table is immutable after insertion, so a later ``UPDATE``-set
field is not implementable against that contract) -- promotion is a new
``HUMAN_AUTHORED`` row whose ``supersedes_revision_id`` names the exact
``SYSTEM_DRAFT`` row it promotes, distinguishable by that row's own
``origin``.

``InvestmentHypothesisCitation`` backs "cites: list of Fact/
ResearchProposition/CompanyExposure ids" -- the same row-per-citation
join idiom as ``CycleAssessmentObservation``/``FactDerivationInput``, now
a three-way "exactly one source" union. This is also where the
Cycle-vs-Transformation citation-separation rule actually lives for a
hypothesis: a cycle-side driver is cited via ``company_exposure_id``, a
transformation-side driver via ``research_proposition_id`` -- two
separate citation rows, never one row or one blended text field.

``ForecastAssumption`` is append-only (no revision chain of its own --
each row is pinned to one exact ``investment_hypothesis_id`` revision,
the same "pin to an exact revision" principle ``CompanyExposure`` uses
for ``cycle_assessment_id``). Its ``provenance`` is a five-way "exactly
one source" union -- the same summed-CASE idiom as ``CycleObservation``'s
three-way union, extended by two columns. The atomicity rule (one row =
one driver = one provenance source; a scenario depending on both a
cycle driver and a transformation driver is two separate rows, never one
row with both reasons blended into ``assumption_statement``) is a
content-judgment rule the schema's discriminated union cannot fully
express by itself -- it is enforced in
``HypothesisAssumptionService.record_forecast_assumption`` and verified
by the atomicity acceptance test, not by a database constraint.

``metric_slug`` is a plain, uncoupled string (not a foreign key) --
consistent with how ``ExtractedFact.fact_type`` already names
``FinancialMetricDefinition`` slugs by convention rather than by FK
throughout Slice A/B; adding a hard FK here would be new coupling this
slice does not need.
"""

from __future__ import annotations

import sqlalchemy as sa
import sqlalchemy.orm as so

from app.models.base import BaseModel
from app.models.research_types import InvestmentOrigin, Scenario, enum_type


class InvestmentHypothesis(BaseModel):
    """One evolving investment thesis for one company."""

    __tablename__ = "investment_hypothesis"

    company_id: so.Mapped[str] = so.mapped_column(
        sa.ForeignKey("company.id"), nullable=False
    )
    title: so.Mapped[str] = so.mapped_column(sa.String(200), nullable=False)
    thesis_narrative: so.Mapped[str] = so.mapped_column(sa.Text, nullable=False)
    consensus_view: so.Mapped[str | None] = so.mapped_column(sa.Text, nullable=True)
    variant_view: so.Mapped[str | None] = so.mapped_column(sa.Text, nullable=True)
    why_market_is_wrong: so.Mapped[str | None] = so.mapped_column(
        sa.Text, nullable=True
    )
    falsifiers: so.Mapped[str | None] = so.mapped_column(sa.Text, nullable=True)
    confidence: so.Mapped[str] = so.mapped_column(sa.String(20), nullable=False)
    revision_number: so.Mapped[int] = so.mapped_column(sa.Integer, nullable=False)
    supersedes_revision_id: so.Mapped[str | None] = so.mapped_column(
        sa.ForeignKey("investment_hypothesis.id"), nullable=True
    )
    origin: so.Mapped[str] = so.mapped_column(
        enum_type(
            "investment_hypothesis_origin",
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
            name="uq_investment_hypothesis_company_revision",
        ),
        sa.UniqueConstraint(
            "supersedes_revision_id", name="uq_investment_hypothesis_supersedes",
        ),
        sa.Index(
            "uq_investment_hypothesis_single_genesis",
            "company_id",
            unique=True,
            sqlite_where=sa.text("supersedes_revision_id IS NULL"),
            postgresql_where=sa.text("supersedes_revision_id IS NULL"),
        ),
    )

    company: so.Mapped["Company"] = so.relationship("Company", viewonly=True)
    supersedes: so.Mapped["InvestmentHypothesis | None"] = so.relationship(
        "InvestmentHypothesis", remote_side="InvestmentHypothesis.id",
        viewonly=True, uselist=False,
    )
    created_by_user: so.Mapped["User"] = so.relationship("User", viewonly=True)
    citations: so.Mapped[list["InvestmentHypothesisCitation"]] = so.relationship(
        "InvestmentHypothesisCitation", viewonly=True
    )
    assumptions: so.Mapped[list["ForecastAssumption"]] = so.relationship(
        "ForecastAssumption", viewonly=True
    )

    def __repr__(self) -> str:
        return f"<InvestmentHypothesis company={self.company_id} v{self.revision_number}>"


class InvestmentHypothesisCitation(BaseModel):
    """One citation of a ``Fact``/``ResearchProposition``/``CompanyExposure``
    by one ``InvestmentHypothesis`` revision. Exactly one of the three is
    set. Immutable, append-only."""

    __tablename__ = "investment_hypothesis_citation"

    investment_hypothesis_id: so.Mapped[str] = so.mapped_column(
        sa.ForeignKey("investment_hypothesis.id"), nullable=False
    )
    fact_id: so.Mapped[str | None] = so.mapped_column(
        sa.ForeignKey("extracted_fact.id"), nullable=True
    )
    research_proposition_id: so.Mapped[str | None] = so.mapped_column(
        sa.ForeignKey("research_proposition.id"), nullable=True
    )
    company_exposure_id: so.Mapped[str | None] = so.mapped_column(
        sa.ForeignKey("company_exposure.id"), nullable=True
    )

    __table_args__ = (
        sa.CheckConstraint(
            "(CASE WHEN fact_id IS NOT NULL THEN 1 ELSE 0 END) + "
            "(CASE WHEN research_proposition_id IS NOT NULL THEN 1 ELSE 0 END) + "
            "(CASE WHEN company_exposure_id IS NOT NULL THEN 1 ELSE 0 END) = 1",
            name="ck_investment_hypothesis_citation_exactly_one_source",
        ),
    )

    investment_hypothesis: so.Mapped["InvestmentHypothesis"] = so.relationship(
        "InvestmentHypothesis", viewonly=True
    )
    fact: so.Mapped["ExtractedFact | None"] = so.relationship(
        "ExtractedFact", viewonly=True
    )
    research_proposition: so.Mapped["ResearchProposition | None"] = so.relationship(
        "ResearchProposition", viewonly=True
    )
    company_exposure: so.Mapped["CompanyExposure | None"] = so.relationship(
        "CompanyExposure", viewonly=True
    )

    def __repr__(self) -> str:
        return f"<InvestmentHypothesisCitation hypothesis={self.investment_hypothesis_id}>"


class ForecastAssumption(BaseModel):
    """One atomic, scenario-forked, singly-provenanced driver of a
    forecast. Append-only -- pinned to an exact ``InvestmentHypothesis``
    revision, never to a bare company/thesis identity."""

    __tablename__ = "forecast_assumption"

    investment_hypothesis_id: so.Mapped[str] = so.mapped_column(
        sa.ForeignKey("investment_hypothesis.id"), nullable=False
    )
    scenario: so.Mapped[str] = so.mapped_column(
        enum_type(
            "forecast_assumption_scenario",
            (Scenario.BULL, Scenario.BASE, Scenario.BEAR, Scenario.MID_CYCLE),
        ),
        nullable=False,
    )
    assumption_statement: so.Mapped[str] = so.mapped_column(sa.Text, nullable=False)
    metric_slug: so.Mapped[str | None] = so.mapped_column(
        sa.String(100), nullable=True
    )
    assumed_value: so.Mapped[str | None] = so.mapped_column(
        sa.String(4000), nullable=True
    )
    assumed_unit: so.Mapped[str | None] = so.mapped_column(
        sa.String(50), nullable=True
    )
    cycle_exposure_id: so.Mapped[str | None] = so.mapped_column(
        sa.ForeignKey("company_exposure.id"), nullable=True
    )
    research_proposition_id: so.Mapped[str | None] = so.mapped_column(
        sa.ForeignKey("research_proposition.id"), nullable=True
    )
    fact_id: so.Mapped[str | None] = so.mapped_column(
        sa.ForeignKey("extracted_fact.id"), nullable=True
    )
    candidate_finding_id: so.Mapped[str | None] = so.mapped_column(
        sa.ForeignKey("candidate_finding.id"), nullable=True
    )
    evidence_id: so.Mapped[str | None] = so.mapped_column(
        sa.ForeignKey("evidence.id"), nullable=True
    )
    created_by_user_id: so.Mapped[str] = so.mapped_column(
        sa.ForeignKey("user.id"), nullable=False
    )

    __table_args__ = (
        sa.CheckConstraint(
            "(CASE WHEN cycle_exposure_id IS NOT NULL THEN 1 ELSE 0 END) + "
            "(CASE WHEN research_proposition_id IS NOT NULL THEN 1 ELSE 0 END) + "
            "(CASE WHEN fact_id IS NOT NULL THEN 1 ELSE 0 END) + "
            "(CASE WHEN candidate_finding_id IS NOT NULL THEN 1 ELSE 0 END) + "
            "(CASE WHEN evidence_id IS NOT NULL THEN 1 ELSE 0 END) = 1",
            name="ck_forecast_assumption_exactly_one_provenance",
        ),
    )

    investment_hypothesis: so.Mapped["InvestmentHypothesis"] = so.relationship(
        "InvestmentHypothesis", viewonly=True
    )
    cycle_exposure: so.Mapped["CompanyExposure | None"] = so.relationship(
        "CompanyExposure", viewonly=True
    )
    research_proposition: so.Mapped["ResearchProposition | None"] = so.relationship(
        "ResearchProposition", viewonly=True
    )
    fact: so.Mapped["ExtractedFact | None"] = so.relationship(
        "ExtractedFact", viewonly=True
    )
    candidate_finding: so.Mapped["CandidateFinding | None"] = so.relationship(
        "CandidateFinding", viewonly=True
    )
    evidence: so.Mapped["Evidence | None"] = so.relationship(
        "Evidence", viewonly=True
    )
    created_by_user: so.Mapped["User"] = so.relationship("User", viewonly=True)

    def __repr__(self) -> str:
        return (
            f"<ForecastAssumption hypothesis={self.investment_hypothesis_id} "
            f"{self.scenario}>"
        )


def _reject_immutable_update(_mapper, _connection, target) -> None:
    raise sa.exc.InvalidRequestError(
        f"{type(target).__name__} is immutable after insertion"
    )


def _reject_immutable_delete(_mapper, _connection, target) -> None:
    raise sa.exc.InvalidRequestError(
        f"{type(target).__name__} is immutable and cannot be deleted"
    )


for _immutable_model in (
    InvestmentHypothesis,
    InvestmentHypothesisCitation,
    ForecastAssumption,
):
    sa.event.listen(_immutable_model, "before_update", _reject_immutable_update)
    sa.event.listen(_immutable_model, "before_delete", _reject_immutable_delete)
