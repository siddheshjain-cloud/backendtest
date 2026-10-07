"""Research Coverage & Fact Intelligence, Slice 1: the Coverage Foundation.

Six purely additive models, independent of the frozen Milestone 1 schema
(``migrations/m1_table_inventory.py``'s ``M1_TABLES``) and of the Research
Brain Pilot's own two frozen sets (``RESEARCH_BRAIN_PILOT_TABLES``,
``RESEARCH_BRAIN_EXTRACTION_UNIT_TABLES``). This slice is the "accounting
layer" described in the approved design proposal
(``docs/superpowers/plans/2026-10-07-research-coverage-fact-intelligence-design-proposal.md``,
S1.3): a way to track, per document and per research topic, whether anyone
has ever checked it, and to mechanically prevent claiming "checked" without
actually having walked the whole document. It starts strictly *after* the
existing Extraction layer (``ExtractionRun``/``ExtractionUnit``) and reuses
``Document.document_type`` directly wherever that closed native enum
already covers a document's real type -- it never alters that enum, that
column, or the ``document`` table.

``ResearchDimension`` is a seed/lookup table of material research topics
(e.g. ``GOVERNANCE_RPT``, ``CREDIT_DEBT``); new dimensions are a data
insert, never a migration. ``CoverageDocumentSubtype`` is a narrow,
append-only side-tag used only by this capability's profile resolution, for
the document types ``Document.document_type``'s closed enum does not cover
(DRHP, RHP, Letter/Offer, Merger/Scheme, Shareholding filings) -- it never
competes with or claims to replace ``document_type``. ``CoverageProfile``
declares, for one ``document_type_code`` (either a real ``document_type``
enum value or a ``CoverageDocumentSubtype.subtype_code`` value -- this table
does not care which), which ``ResearchDimension``s are required; like
``ExtractedFact``, it is immutable and append-only, with "current" resolved
by the same supersession idiom (``supersedes_profile_id``, "current" = the
row not referenced by any other row's ``supersedes_profile_id``) rather than
a mutable ``is_active`` flag -- kept fully immutable like every other row in
this system, with zero in-place updates anywhere.
``CoverageProfileDimension`` is the profile's fixed set of dimension
requirements, created together with its parent ``CoverageProfile`` in the
same transaction and never added to afterward: a profile revision is a
brand-new ``CoverageProfile`` plus a full new set of
``CoverageProfileDimension`` rows, never a partial edit.

``CoverageReviewPass`` is one immutable, retrospective row per reported
sweep attempt of one document against one dimension, recording how many of
the document's ``ExtractionUnit``s that pass actually walked.
``CoverageRecord`` is the append-only state log for a (document, dimension)
pair -- current state = the most recent row by ``created_at`` -- written
only once the service layer (``ResearchCoverageService.record_coverage_review_pass``)
determines cumulative passes have covered the document's entire
``ExtractionUnit`` count for that dimension. This is the mechanical
tunnel-vision guard: a pass covering only part of a document's units can
never, by construction, produce a ``CoverageRecord``.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import sqlalchemy as sa
import sqlalchemy.orm as so

from app import db
from app.models.base import BaseModel
from app.models.research_types import enum_type


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ResearchDimension(BaseModel):
    """A material research topic a document can be checked for.

    Seed/lookup table (S2.1 of the design proposal). ``code`` is the stable
    identifier call sites reference (e.g. ``GOVERNANCE_RPT``); new dimensions
    are added by inserting a row, never by a migration.
    """

    __tablename__ = "research_dimension"

    code: so.Mapped[str] = so.mapped_column(
        sa.String(50), nullable=False, unique=True
    )
    name: so.Mapped[str] = so.mapped_column(sa.String(100), nullable=False)
    description: so.Mapped[str] = so.mapped_column(sa.Text, nullable=False)
    is_active: so.Mapped[bool] = so.mapped_column(
        sa.Boolean, nullable=False, default=True, server_default=sa.true()
    )

    def __repr__(self) -> str:
        return f"<ResearchDimension {self.code}>"


class CoverageDocumentSubtype(BaseModel):
    """A side-tag naming a document's real type for coverage-profile lookup.

    Immutable, append-only. Only meaningful for documents whose real nature
    falls outside ``Document.document_type``'s closed native enum (DRHP,
    RHP, Letter/Offer, Merger/Scheme, Shareholding filings). Does not touch
    or compete with ``document_type`` as the canonical classification --
    that column stays exactly what M1 defines, unchanged. "Current subtype"
    for a document is the most recent row by ``created_at``.
    """

    __tablename__ = "coverage_document_subtype"

    document_id: so.Mapped[str] = so.mapped_column(
        sa.ForeignKey("document.id"), nullable=False
    )
    subtype_code: so.Mapped[str] = so.mapped_column(
        sa.String(50), nullable=False
    )
    assigned_by_user_id: so.Mapped[str] = so.mapped_column(
        sa.ForeignKey("user.id"), nullable=False
    )

    document: so.Mapped["Document"] = so.relationship(
        "Document", viewonly=True
    )
    assigned_by_user: so.Mapped["User"] = so.relationship(
        "User", viewonly=True
    )

    def __repr__(self) -> str:
        return f"<CoverageDocumentSubtype {self.document_id} {self.subtype_code}>"


class CoverageProfile(BaseModel):
    """One version of a document type's coverage contract.

    ``document_type_code`` is a plain validated string, not a foreign key
    into a new identity table -- it holds either an existing
    ``Document.document_type`` enum string value, or a
    ``CoverageDocumentSubtype.subtype_code`` value; this table doesn't care
    which. Immutable, append-only -- the same supersession idiom as
    ``ExtractedFact.supersedes_fact_id`` (a refinement over the design
    proposal's mutable ``is_active`` flag, chosen to keep every row in this
    system equally immutable): "current" for a given ``document_type_code``
    is the row not referenced by any other row's ``supersedes_profile_id``.
    A profile revision is always a brand-new row plus a brand-new, complete
    set of ``CoverageProfileDimension`` rows, never a partial edit of an
    existing profile's dimensions.
    """

    __tablename__ = "coverage_profile"

    document_type_code: so.Mapped[str] = so.mapped_column(
        sa.String(50), nullable=False
    )
    supersedes_profile_id: so.Mapped[str | None] = so.mapped_column(
        sa.ForeignKey("coverage_profile.id"), nullable=True
    )
    effective_from: so.Mapped[date] = so.mapped_column(
        sa.Date, nullable=False
    )
    created_by_user_id: so.Mapped[str] = so.mapped_column(
        sa.ForeignKey("user.id"), nullable=False
    )

    supersedes: so.Mapped["CoverageProfile | None"] = so.relationship(
        "CoverageProfile",
        remote_side="CoverageProfile.id",
        viewonly=True,
        uselist=False,
    )
    created_by_user: so.Mapped["User"] = so.relationship(
        "User", viewonly=True
    )
    dimensions: so.Mapped[list["CoverageProfileDimension"]] = so.relationship(
        "CoverageProfileDimension", viewonly=True
    )

    def __repr__(self) -> str:
        return f"<CoverageProfile {self.document_type_code}>"


class CoverageProfileDimension(BaseModel):
    """One required/optional dimension declaration within a coverage profile.

    Immutable. Created together with its parent ``CoverageProfile`` row in
    the same transaction and never added to afterward.
    """

    __tablename__ = "coverage_profile_dimension"

    coverage_profile_id: so.Mapped[str] = so.mapped_column(
        sa.ForeignKey("coverage_profile.id"), nullable=False
    )
    research_dimension_id: so.Mapped[str] = so.mapped_column(
        sa.ForeignKey("research_dimension.id"), nullable=False
    )
    is_required: so.Mapped[bool] = so.mapped_column(
        sa.Boolean, nullable=False, default=True, server_default=sa.true()
    )
    notes: so.Mapped[str | None] = so.mapped_column(sa.Text, nullable=True)

    coverage_profile: so.Mapped["CoverageProfile"] = so.relationship(
        "CoverageProfile", viewonly=True
    )
    research_dimension: so.Mapped["ResearchDimension"] = so.relationship(
        "ResearchDimension", viewonly=True
    )

    def __repr__(self) -> str:
        return (
            f"<CoverageProfileDimension profile={self.coverage_profile_id} "
            f"dimension={self.research_dimension_id} required={self.is_required}>"
        )


class CoverageReviewPass(BaseModel):
    """One reported, retrospective sweep attempt of one document against one
    dimension.

    Immutable. Records how many of the document's ``ExtractionUnit``s this
    pass actually walked (``units_considered_count``), not a caller-supplied
    claim of completeness -- the service layer is the only place that
    computes whether cumulative passes close out a ``CoverageRecord``.
    """

    __tablename__ = "coverage_review_pass"

    document_id: so.Mapped[str] = so.mapped_column(
        sa.ForeignKey("document.id"), nullable=False
    )
    research_dimension_id: so.Mapped[str] = so.mapped_column(
        sa.ForeignKey("research_dimension.id"), nullable=False
    )
    coverage_profile_id: so.Mapped[str | None] = so.mapped_column(
        sa.ForeignKey("coverage_profile.id"), nullable=True
    )
    units_considered_count: so.Mapped[int] = so.mapped_column(
        sa.Integer, nullable=False
    )
    units_considered_min_seq: so.Mapped[int | None] = so.mapped_column(
        sa.Integer, nullable=True
    )
    units_considered_max_seq: so.Mapped[int | None] = so.mapped_column(
        sa.Integer, nullable=True
    )
    has_material_content: so.Mapped[bool] = so.mapped_column(
        sa.Boolean, nullable=False, default=False, server_default=sa.false()
    )
    performed_by_user_id: so.Mapped[str] = so.mapped_column(
        sa.ForeignKey("user.id"), nullable=False
    )
    notes: so.Mapped[str | None] = so.mapped_column(sa.Text, nullable=True)

    document: so.Mapped["Document"] = so.relationship(
        "Document", viewonly=True
    )
    research_dimension: so.Mapped["ResearchDimension"] = so.relationship(
        "ResearchDimension", viewonly=True
    )
    coverage_profile: so.Mapped["CoverageProfile | None"] = so.relationship(
        "CoverageProfile", viewonly=True
    )
    performed_by_user: so.Mapped["User"] = so.relationship(
        "User", viewonly=True
    )

    def __repr__(self) -> str:
        return (
            f"<CoverageReviewPass document={self.document_id} "
            f"dimension={self.research_dimension_id} "
            f"units={self.units_considered_count}>"
        )


class CoverageRecord(BaseModel):
    """Append-only coverage state for one (document, dimension) pair.

    Immutable. "Current state" for a given ``(document_id,
    research_dimension_id)`` pair is the most recent row by ``created_at``
    -- the same idiom as ``ExtractedFact`` supersession, applied to a state
    log instead of a value. The absence of any row for a pair already means
    ``NOT_REVIEWED``; no row is ever written to represent that state.
    """

    __tablename__ = "coverage_record"

    document_id: so.Mapped[str] = so.mapped_column(
        sa.ForeignKey("document.id"), nullable=False
    )
    research_dimension_id: so.Mapped[str] = so.mapped_column(
        sa.ForeignKey("research_dimension.id"), nullable=False
    )
    state: so.Mapped[str] = so.mapped_column(
        enum_type(
            "coverage_record_state",
            ("NOT_REVIEWED", "REVIEWED_NO_FINDING", "FINDING_GENERATED"),
        ),
        nullable=False,
    )
    review_pass_id: so.Mapped[str] = so.mapped_column(
        sa.ForeignKey("coverage_review_pass.id"), nullable=False
    )

    document: so.Mapped["Document"] = so.relationship(
        "Document", viewonly=True
    )
    research_dimension: so.Mapped["ResearchDimension"] = so.relationship(
        "ResearchDimension", viewonly=True
    )
    review_pass: so.Mapped["CoverageReviewPass"] = so.relationship(
        "CoverageReviewPass", viewonly=True
    )

    def __repr__(self) -> str:
        return (
            f"<CoverageRecord document={self.document_id} "
            f"dimension={self.research_dimension_id} state={self.state}>"
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
    ResearchDimension,
    CoverageDocumentSubtype,
    CoverageProfile,
    CoverageProfileDimension,
    CoverageReviewPass,
    CoverageRecord,
):
    sa.event.listen(_immutable_model, "before_update", _reject_immutable_update)
    sa.event.listen(_immutable_model, "before_delete", _reject_immutable_delete)
