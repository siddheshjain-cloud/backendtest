"""Phase 3 Slice C -- Cycle Intelligence (Commodity-Cycle & Transformation).

World-layer, deliberately narrow first slice. Covers only the external,
industry-wide cycle side (``CycleObservation`` -> ``CycleAssessment`` ->
``CompanyExposure``). Company-internal Transformation tracking is
explicitly *not* a new entity here -- it reuses the existing Slice 4
``ResearchProposition``/``PropositionStageType``/``PropositionLink``
mechanism (see ``app/models/research_coverage.py``) unchanged. Citation
discipline (the design doc's own stated gap-fix): wherever a hypothesis or
assumption depends on both a cycle driver and a transformation driver, the
two must be cited as two separate provenance entries -- a
``CompanyExposure``/``CycleAssessment`` id on one side, a
``ResearchProposition`` id on the other -- never blended into one
entity's rationale/narrative text. Nothing in this module reads from or
writes to ``research_coverage.py``'s tables; the separation is structural,
not just conventional.

Revision integrity for ``CycleAssessment`` is enforced at the database
level, not merely by service-layer discipline:
  1. ``uq_cycle_assessment_tag_scenario_revision`` -- no two rows of the
     same ``(cycle_tag, scenario)`` chain can share a ``revision_number``.
  2. ``uq_cycle_assessment_supersedes`` -- a unique constraint on
     ``supersedes_revision_id`` (NULLs excluded, standard SQL unique
     semantics) means a given row can be superseded by at most one
     successor -- branching supersession is rejected at INSERT/COMMIT,
     not merely discouraged.
  3. ``uq_cycle_assessment_single_genesis`` -- a partial unique index on
     ``(cycle_tag, scenario)`` where ``supersedes_revision_id IS NULL``
     (same dual-dialect ``sqlite_where``/``postgresql_where`` idiom Slice A
     used for ``forecast_revision``/``valuation_revision``) means at most
     one genesis row can exist per chain.
Together, (1)-(3) make each ``(cycle_tag, scenario)`` chain a single
linear list with no branch and no second root, so "the row nobody
supersedes" is always unique by construction -- not just by convention --
and two concurrent writers racing to extend the same chain collide on
constraint (2) at commit time rather than silently producing two
competing "current" rows.
"""

from __future__ import annotations

from datetime import date, datetime

import sqlalchemy as sa
import sqlalchemy.orm as so

from app.models.base import BaseModel
from app.models.research_types import ExposureDirection, enum_type


class CycleObservation(BaseModel):
    """An external, industry-wide, dated, cited data point about a named
    commodity/industry cycle (e.g. a PVC-ACH spread reading). Append-only
    and company-independent -- it says nothing about any one company's
    exposure; that linkage is ``CompanyExposure``'s job.

    Exactly one of ``evidence_id``/``document_id``/``external_reference``
    is set -- a three-way extension of the codebase's existing two-way
    "exactly one source" idiom (``FactDerivationInput``, ``PropositionLink``),
    expressed as a summed-CASE check constraint rather than a pairwise
    ``!=`` (which only generalizes to two columns).
    """

    __tablename__ = "cycle_observation"

    cycle_tag: so.Mapped[str] = so.mapped_column(sa.String(100), nullable=False)
    observed_on: so.Mapped[date] = so.mapped_column(sa.Date, nullable=False)
    description: so.Mapped[str] = so.mapped_column(sa.Text, nullable=False)
    evidence_id: so.Mapped[str | None] = so.mapped_column(
        sa.ForeignKey("evidence.id"), nullable=True
    )
    document_id: so.Mapped[str | None] = so.mapped_column(
        sa.ForeignKey("document.id"), nullable=True
    )
    external_reference: so.Mapped[str | None] = so.mapped_column(
        sa.String(500), nullable=True
    )
    created_by_user_id: so.Mapped[str] = so.mapped_column(
        sa.ForeignKey("user.id"), nullable=False
    )

    __table_args__ = (
        sa.CheckConstraint(
            "(CASE WHEN evidence_id IS NOT NULL THEN 1 ELSE 0 END) + "
            "(CASE WHEN document_id IS NOT NULL THEN 1 ELSE 0 END) + "
            "(CASE WHEN external_reference IS NOT NULL THEN 1 ELSE 0 END) = 1",
            name="ck_cycle_observation_exactly_one_source",
        ),
    )

    evidence: so.Mapped["Evidence | None"] = so.relationship(
        "Evidence", viewonly=True
    )
    document: so.Mapped["Document | None"] = so.relationship(
        "Document", viewonly=True
    )
    created_by_user: so.Mapped["User"] = so.relationship(
        "User", viewonly=True
    )

    def __repr__(self) -> str:
        return f"<CycleObservation {self.cycle_tag} {self.observed_on}>"


class CycleAssessment(BaseModel):
    """SPA's own synthesized, versioned read of where a named cycle
    stands, for one scenario of that cycle's own trajectory (not a
    company forecast scenario -- see module docstring). Sibling scenarios
    for the same ``cycle_tag`` coexist as independent chains; see the
    revision-integrity constraints in the module docstring.
    """

    __tablename__ = "cycle_assessment"

    cycle_tag: so.Mapped[str] = so.mapped_column(sa.String(100), nullable=False)
    scenario: so.Mapped[str] = so.mapped_column(
        enum_type("cycle_assessment_scenario", ("BULL", "BASE", "BEAR")),
        nullable=False,
    )
    revision_number: so.Mapped[int] = so.mapped_column(sa.Integer, nullable=False)
    supersedes_revision_id: so.Mapped[str | None] = so.mapped_column(
        sa.ForeignKey("cycle_assessment.id"), nullable=True
    )
    narrative: so.Mapped[str] = so.mapped_column(sa.Text, nullable=False)
    confidence: so.Mapped[str | None] = so.mapped_column(
        sa.String(20), nullable=True
    )
    change_reason: so.Mapped[str | None] = so.mapped_column(
        sa.String(2000), nullable=True
    )
    created_by_user_id: so.Mapped[str] = so.mapped_column(
        sa.ForeignKey("user.id"), nullable=False
    )

    __table_args__ = (
        sa.UniqueConstraint(
            "cycle_tag", "scenario", "revision_number",
            name="uq_cycle_assessment_tag_scenario_revision",
        ),
        sa.UniqueConstraint(
            "supersedes_revision_id", name="uq_cycle_assessment_supersedes",
        ),
        sa.Index(
            "uq_cycle_assessment_single_genesis",
            "cycle_tag", "scenario",
            unique=True,
            sqlite_where=sa.text("supersedes_revision_id IS NULL"),
            postgresql_where=sa.text("supersedes_revision_id IS NULL"),
        ),
    )

    supersedes: so.Mapped["CycleAssessment | None"] = so.relationship(
        "CycleAssessment", remote_side="CycleAssessment.id", viewonly=True,
        uselist=False,
    )
    created_by_user: so.Mapped["User"] = so.relationship(
        "User", viewonly=True
    )
    observation_links: so.Mapped[list["CycleAssessmentObservation"]] = so.relationship(
        "CycleAssessmentObservation", viewonly=True
    )

    def __repr__(self) -> str:
        return f"<CycleAssessment {self.cycle_tag} {self.scenario} v{self.revision_number}>"


class CycleAssessmentObservation(BaseModel):
    """One citation of one ``CycleObservation`` by one ``CycleAssessment``
    -- the row-per-citation table backing "cites: list of CycleObservation
    ids" (same role ``FactDerivationInput`` plays for ``FactDerivation``).
    Immutable, append-only.
    """

    __tablename__ = "cycle_assessment_observation"

    cycle_assessment_id: so.Mapped[str] = so.mapped_column(
        sa.ForeignKey("cycle_assessment.id"), nullable=False
    )
    cycle_observation_id: so.Mapped[str] = so.mapped_column(
        sa.ForeignKey("cycle_observation.id"), nullable=False
    )

    __table_args__ = (
        sa.UniqueConstraint(
            "cycle_assessment_id", "cycle_observation_id",
            name="uq_cycle_assessment_observation_pair",
        ),
    )

    cycle_assessment: so.Mapped["CycleAssessment"] = so.relationship(
        "CycleAssessment", viewonly=True
    )
    cycle_observation: so.Mapped["CycleObservation"] = so.relationship(
        "CycleObservation", viewonly=True
    )

    def __repr__(self) -> str:
        return (
            f"<CycleAssessmentObservation assessment={self.cycle_assessment_id} "
            f"observation={self.cycle_observation_id}>"
        )


class CompanyExposure(BaseModel):
    """Links one company to one *exact* ``CycleAssessment`` revision --
    never to a bare ``cycle_tag`` -- so exposure provenance survives later
    supersession of that assessment unchanged. Append-only.
    """

    __tablename__ = "company_exposure"

    company_id: so.Mapped[str] = so.mapped_column(
        sa.ForeignKey("company.id"), nullable=False
    )
    cycle_assessment_id: so.Mapped[str] = so.mapped_column(
        sa.ForeignKey("cycle_assessment.id"), nullable=False
    )
    exposure_direction: so.Mapped[str] = so.mapped_column(
        enum_type(
            "company_exposure_direction",
            (
                ExposureDirection.BENEFICIARY,
                ExposureDirection.LOSER,
                ExposureDirection.MIXED,
            ),
        ),
        nullable=False,
    )
    rationale: so.Mapped[str] = so.mapped_column(sa.Text, nullable=False)
    created_by_user_id: so.Mapped[str] = so.mapped_column(
        sa.ForeignKey("user.id"), nullable=False
    )

    company: so.Mapped["Company"] = so.relationship("Company", viewonly=True)
    cycle_assessment: so.Mapped["CycleAssessment"] = so.relationship(
        "CycleAssessment", viewonly=True
    )
    created_by_user: so.Mapped["User"] = so.relationship(
        "User", viewonly=True
    )

    def __repr__(self) -> str:
        return f"<CompanyExposure company={self.company_id} {self.exposure_direction}>"


def _reject_immutable_update(_mapper, _connection, target) -> None:
    raise sa.exc.InvalidRequestError(
        f"{type(target).__name__} is immutable after insertion"
    )


def _reject_immutable_delete(_mapper, _connection, target) -> None:
    raise sa.exc.InvalidRequestError(
        f"{type(target).__name__} is immutable and cannot be deleted"
    )


for _immutable_model in (
    CycleObservation,
    CycleAssessment,
    CycleAssessmentObservation,
    CompanyExposure,
):
    sa.event.listen(_immutable_model, "before_update", _reject_immutable_update)
    sa.event.listen(_immutable_model, "before_delete", _reject_immutable_delete)
