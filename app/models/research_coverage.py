"""Research Coverage & Fact Intelligence, Slices 1-3: Coverage Foundation,
Candidate Findings, and Derived Facts.

Ten purely additive models, independent of the frozen Milestone 1 schema
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
sweep attempt of one document against one dimension, recording exactly
which contiguous range of the document's current ``ExtractionRun``'s
``ExtractionUnit``s that pass actually walked. ``CoverageRecord`` is the
append-only state log for a (document, dimension) pair -- current state =
the most recent row by ``created_at`` -- written only once the service
layer (``ResearchCoverageService.record_coverage_review_pass``) merges every
pass recorded against the document's *current* extraction run as true
intervals (never a raw sum, which would double-count overlapping or
re-read pages) and finds their union covers the run's entire
``ExtractionUnit`` count for that dimension. This is the mechanical
tunnel-vision guard: a pass covering only part of a document's units --
or re-reading pages another pass already covered -- can never, by
construction, produce a ``CoverageRecord``.

Slice 2 adds ``CandidateFinding`` and ``CandidateFindingDecision`` -- the
evidence-backed staging step between a coverage sweep finding something and
that something becoming an authoritative ``ExtractedFact``. A
``CandidateFinding`` always cites a real ``ExtractionUnit`` and the
``CoverageReviewPass`` that found it; triage into exactly one of
``ResearchCoverageService.promote_candidate_finding``,
``reject_candidate_finding``, or ``mark_candidate_finding_duplicate`` is a
deliberate, separate, human-decided act. Promotion calls the existing,
unchanged ``ResearchBrainService.record_evidence``/``record_fact`` -- never
a shortcut around the "every Fact needs >=1 Evidence" rule those methods
already enforce.

Slice 3 adds ``FactDerivation``/``FactDerivationInput`` -- provenance-safe
support for a Fact that was *computed* by SPA (e.g. EBITDA from already-
extracted raw P&L lines) rather than directly quoted. ``ExtractedFact``
itself gains no new column and no new "kind": a derived Fact is created
through the same unchanged ``record_fact`` call as any other Fact, and
``FactDerivation`` is purely additional metadata recorded alongside it via
``ResearchCoverageService.record_fact_derivation``, naming the formula and
citing each raw input as either an existing Fact or a raw ``Evidence`` row.
"""

from __future__ import annotations

from datetime import date

import sqlalchemy as sa
import sqlalchemy.orm as so

from app.models.base import BaseModel
from app.models.research_types import enum_type


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

    Immutable. Records exactly which contiguous range of the document's
    ``ExtractionUnit``s (for one specific ``ExtractionRun``) this pass
    actually walked -- ``units_considered_min_seq``/``units_considered_max_seq``
    are required precisely because the completeness guarantee depends on
    merging these ranges as true intervals, never on trusting a raw
    caller-supplied count. A pass covering a *non-contiguous* set of pages
    (e.g. pages 1-10 and 50-60 read in one sitting) must be recorded as two
    separate calls, one per contiguous chunk -- the service layer rejects a
    pass whose ``units_considered_count`` does not equal
    ``units_considered_max_seq - units_considered_min_seq + 1``.

    ``extraction_run_id`` pins a pass to the specific extraction pass it was
    read against: if a document is later reprocessed under a new
    ``ExtractionRun`` (a new, independent set of ``ExtractionUnit`` sequence
    numbers), passes recorded against the old run must never be mixed into
    the new run's completeness arithmetic -- the service layer only sums
    passes sharing the document's *current* ``extraction_run_id``.
    """

    __tablename__ = "coverage_review_pass"

    document_id: so.Mapped[str] = so.mapped_column(
        sa.ForeignKey("document.id"), nullable=False
    )
    extraction_run_id: so.Mapped[str] = so.mapped_column(
        sa.ForeignKey("extraction_run.id"), nullable=False
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
    units_considered_min_seq: so.Mapped[int] = so.mapped_column(
        sa.Integer, nullable=False
    )
    units_considered_max_seq: so.Mapped[int] = so.mapped_column(
        sa.Integer, nullable=False
    )
    has_material_content: so.Mapped[bool] = so.mapped_column(
        sa.Boolean, nullable=False, default=False, server_default=sa.false()
    )
    performed_by_user_id: so.Mapped[str] = so.mapped_column(
        sa.ForeignKey("user.id"), nullable=False
    )
    notes: so.Mapped[str | None] = so.mapped_column(sa.Text, nullable=True)

    __table_args__ = (
        sa.CheckConstraint(
            "units_considered_max_seq >= units_considered_min_seq",
            name="ck_coverage_review_pass_seq_range_valid",
        ),
        sa.CheckConstraint(
            "units_considered_count = "
            "units_considered_max_seq - units_considered_min_seq + 1",
            name="ck_coverage_review_pass_count_matches_contiguous_range",
        ),
    )

    document: so.Mapped["Document"] = so.relationship(
        "Document", viewonly=True
    )
    extraction_run: so.Mapped["ExtractionRun"] = so.relationship(
        "ExtractionRun", viewonly=True
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
    """Append-only coverage state for one (document, extraction run,
    dimension) triple.

    Immutable. At most one row ever exists per ``(document_id,
    extraction_run_id, research_dimension_id)`` triple -- enforced by a
    database unique constraint, not merely application-level care, because
    a purely application-level "lock existing rows, then decide" guard has
    a real gap: the *first-ever* close for a triple has no existing row to
    lock, so two concurrent first-time closes could otherwise both insert.
    The absence of any row for a triple means ``NOT_REVIEWED``; no row is
    ever written to represent that state.

    ``extraction_run_id`` matters for the same reason it matters on
    ``CoverageReviewPass``: if a document is reprocessed under a new run, a
    closing record against the *old* run must never be read as "this
    dimension is already covered" for the *new* run's content -- each run
    gets its own independent completeness history.
    """

    __tablename__ = "coverage_record"

    document_id: so.Mapped[str] = so.mapped_column(
        sa.ForeignKey("document.id"), nullable=False
    )
    extraction_run_id: so.Mapped[str] = so.mapped_column(
        sa.ForeignKey("extraction_run.id"), nullable=False
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

    __table_args__ = (
        sa.UniqueConstraint(
            "document_id",
            "extraction_run_id",
            "research_dimension_id",
            name="uq_coverage_record_run_dimension",
        ),
    )

    document: so.Mapped["Document"] = so.relationship(
        "Document", viewonly=True
    )
    extraction_run: so.Mapped["ExtractionRun"] = so.relationship(
        "ExtractionRun", viewonly=True
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


class CandidateFinding(BaseModel):
    """Slice 2: one evidence-grounded, pre-authoritative proposal surfaced
    during a coverage sweep.

    Immutable. Always cites exactly where it came from -- the document, the
    dimension it was found under, the specific ``ExtractionUnit`` the quote
    is drawn from, and the ``CoverageReviewPass`` that found it. A
    ``CandidateFinding`` is not a Fact; it only becomes one via
    ``ResearchCoverageService.promote_candidate_finding``, which calls the
    existing, unchanged ``ResearchBrainService.record_evidence``/
    ``record_fact`` -- promotion is exactly as rigorous as a Fact authored
    today, just arrived at systematically instead of ad hoc.
    """

    __tablename__ = "candidate_finding"

    document_id: so.Mapped[str] = so.mapped_column(
        sa.ForeignKey("document.id"), nullable=False
    )
    research_dimension_id: so.Mapped[str] = so.mapped_column(
        sa.ForeignKey("research_dimension.id"), nullable=False
    )
    source_extraction_unit_id: so.Mapped[str] = so.mapped_column(
        sa.ForeignKey("extraction_unit.id"), nullable=False
    )
    review_pass_id: so.Mapped[str] = so.mapped_column(
        sa.ForeignKey("coverage_review_pass.id"), nullable=False
    )
    raw_quote: so.Mapped[str] = so.mapped_column(sa.Text, nullable=False)
    proposed_fact_type: so.Mapped[str | None] = so.mapped_column(
        sa.String(100), nullable=True
    )
    proposed_value: so.Mapped[str | None] = so.mapped_column(
        sa.String(4000), nullable=True
    )
    proposed_value_type: so.Mapped[str | None] = so.mapped_column(
        enum_type("candidate_finding_value_type", ("NUMERIC", "TEXT")),
        nullable=True,
    )
    proposed_unit: so.Mapped[str | None] = so.mapped_column(
        sa.String(50), nullable=True
    )
    proposed_period: so.Mapped[str | None] = so.mapped_column(
        sa.String(50), nullable=True
    )
    proposed_as_of_date: so.Mapped[date | None] = so.mapped_column(
        sa.Date, nullable=True
    )
    created_by_user_id: so.Mapped[str] = so.mapped_column(
        sa.ForeignKey("user.id"), nullable=False
    )

    __table_args__ = (
        sa.CheckConstraint(
            # coalesce(), not a bare `proposed_value_type = 'NUMERIC'`: this
            # column (unlike ExtractedFact.value_type, which is NOT NULL) is
            # nullable, and a bare equality against NULL evaluates to NULL
            # rather than FALSE -- SQL CHECK constraints treat a NULL result
            # as satisfied, so `proposed_unit` set with `proposed_value_type`
            # left NULL would silently pass without the coalesce.
            "proposed_unit IS NULL "
            "OR coalesce(proposed_value_type, '') = 'NUMERIC'",
            name="ck_candidate_finding_unit_requires_numeric",
        ),
    )

    document: so.Mapped["Document"] = so.relationship(
        "Document", viewonly=True
    )
    research_dimension: so.Mapped["ResearchDimension"] = so.relationship(
        "ResearchDimension", viewonly=True
    )
    source_extraction_unit: so.Mapped["ExtractionUnit"] = so.relationship(
        "ExtractionUnit", viewonly=True
    )
    review_pass: so.Mapped["CoverageReviewPass"] = so.relationship(
        "CoverageReviewPass", viewonly=True
    )
    created_by_user: so.Mapped["User"] = so.relationship(
        "User", viewonly=True
    )

    def __repr__(self) -> str:
        return (
            f"<CandidateFinding document={self.document_id} "
            f"dimension={self.research_dimension_id}>"
        )


class CandidateFindingDecision(BaseModel):
    """Slice 2: the terminal decision for one ``CandidateFinding``.

    Immutable, insert-only -- and, like ``CoverageRecord``, constrained at
    the database level to at most one row per ``candidate_finding_id``
    (``uq_candidate_finding_decision_one_per_candidate``), not merely by
    application-level care: once decided, a candidate is never re-opened or
    re-decided, so this is a genuine one-to-zero-or-one relationship, not
    an app-level "most recent wins" sequence. The absence of any row means
    ``OPEN`` -- no row is ever written to represent that state, same idiom
    as ``CoverageRecord``'s absence meaning ``NOT_REVIEWED``. Exactly one of
    ``promoted_to_fact_id``/``duplicate_of_candidate_id`` is set, and only
    for the matching ``decision`` -- also enforced at the database via check
    constraints, not merely convention.
    """

    __tablename__ = "candidate_finding_decision"

    candidate_finding_id: so.Mapped[str] = so.mapped_column(
        sa.ForeignKey("candidate_finding.id"), nullable=False
    )
    decision: so.Mapped[str] = so.mapped_column(
        enum_type(
            "candidate_finding_decision_type",
            ("PROMOTED", "REJECTED", "DUPLICATE"),
        ),
        nullable=False,
    )
    promoted_to_fact_id: so.Mapped[str | None] = so.mapped_column(
        sa.ForeignKey("extracted_fact.id"), nullable=True
    )
    duplicate_of_candidate_id: so.Mapped[str | None] = so.mapped_column(
        sa.ForeignKey("candidate_finding.id"), nullable=True
    )
    reason: so.Mapped[str | None] = so.mapped_column(sa.Text, nullable=True)
    created_by_user_id: so.Mapped[str] = so.mapped_column(
        sa.ForeignKey("user.id"), nullable=False
    )

    __table_args__ = (
        sa.CheckConstraint(
            "(decision = 'PROMOTED') = (promoted_to_fact_id IS NOT NULL)",
            name="ck_candidate_finding_decision_promoted_iff_fact",
        ),
        sa.CheckConstraint(
            "(decision = 'DUPLICATE') = (duplicate_of_candidate_id IS NOT NULL)",
            name="ck_candidate_finding_decision_duplicate_iff_target",
        ),
        sa.UniqueConstraint(
            "candidate_finding_id",
            name="uq_candidate_finding_decision_one_per_candidate",
        ),
    )

    candidate_finding: so.Mapped["CandidateFinding"] = so.relationship(
        "CandidateFinding",
        foreign_keys="CandidateFindingDecision.candidate_finding_id",
        viewonly=True,
    )
    promoted_to_fact: so.Mapped["ExtractedFact | None"] = so.relationship(
        "ExtractedFact", viewonly=True
    )
    duplicate_of_candidate: so.Mapped["CandidateFinding | None"] = so.relationship(
        "CandidateFinding",
        foreign_keys="CandidateFindingDecision.duplicate_of_candidate_id",
        viewonly=True,
    )
    created_by_user: so.Mapped["User"] = so.relationship(
        "User", viewonly=True
    )

    def __repr__(self) -> str:
        return (
            f"<CandidateFindingDecision candidate={self.candidate_finding_id} "
            f"decision={self.decision}>"
        )


class FactDerivation(BaseModel):
    """Slice 3: provenance-safe record that an ``ExtractedFact`` was
    computed by SPA, not directly quoted from a source.

    Immutable. Exactly one per ``derived_fact_id`` (``unique=True``).
    ``ExtractedFact`` itself is untouched -- no new column, no new "kind"
    of fact -- so a derived Fact looks like any other Fact to every
    existing reader (``get_company_facts`` needs no change). A reader who
    wants to know "stated by the company or computed by SPA" joins to this
    table: present = computed, absent = direct quote.
    """

    __tablename__ = "fact_derivation"

    derived_fact_id: so.Mapped[str] = so.mapped_column(
        sa.ForeignKey("extracted_fact.id"), nullable=False, unique=True
    )
    formula_description: so.Mapped[str] = so.mapped_column(
        sa.Text, nullable=False
    )
    created_by_user_id: so.Mapped[str] = so.mapped_column(
        sa.ForeignKey("user.id"), nullable=False
    )

    derived_fact: so.Mapped["ExtractedFact"] = so.relationship(
        "ExtractedFact", viewonly=True
    )
    created_by_user: so.Mapped["User"] = so.relationship(
        "User", viewonly=True
    )
    inputs: so.Mapped[list["FactDerivationInput"]] = so.relationship(
        "FactDerivationInput", viewonly=True
    )

    def __repr__(self) -> str:
        return f"<FactDerivation fact={self.derived_fact_id}>"


class FactDerivationInput(BaseModel):
    """Slice 3: one input to a derivation -- either an already-promoted
    ``ExtractedFact`` or a raw ``Evidence`` row not (yet) promoted to its
    own Fact. Immutable.

    Exactly one of ``input_fact_id``/``input_evidence_id`` is set --
    enforced via a NULL-safe ``IS NOT NULL`` comparison on both sides
    (never a bare equality against a nullable column's *value*, which is
    exactly the check-constraint class of bug Slice 2's code review found:
    ``x IS NOT NULL`` is always a true boolean, never SQL NULL, regardless
    of whether ``x`` itself is NULL).
    """

    __tablename__ = "fact_derivation_input"

    fact_derivation_id: so.Mapped[str] = so.mapped_column(
        sa.ForeignKey("fact_derivation.id"), nullable=False
    )
    input_fact_id: so.Mapped[str | None] = so.mapped_column(
        sa.ForeignKey("extracted_fact.id"), nullable=True
    )
    input_evidence_id: so.Mapped[str | None] = so.mapped_column(
        sa.ForeignKey("evidence.id"), nullable=True
    )
    role_label: so.Mapped[str | None] = so.mapped_column(
        sa.String(100), nullable=True
    )

    __table_args__ = (
        sa.CheckConstraint(
            "(input_fact_id IS NOT NULL) != (input_evidence_id IS NOT NULL)",
            name="ck_fact_derivation_input_exactly_one_source",
        ),
    )

    fact_derivation: so.Mapped["FactDerivation"] = so.relationship(
        "FactDerivation", viewonly=True
    )
    input_fact: so.Mapped["ExtractedFact | None"] = so.relationship(
        "ExtractedFact", viewonly=True
    )
    input_evidence: so.Mapped["Evidence | None"] = so.relationship(
        "Evidence", viewonly=True
    )

    def __repr__(self) -> str:
        return (
            f"<FactDerivationInput derivation={self.fact_derivation_id} "
            f"role={self.role_label!r}>"
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
    CandidateFinding,
    CandidateFindingDecision,
    FactDerivation,
    FactDerivationInput,
):
    sa.event.listen(_immutable_model, "before_update", _reject_immutable_update)
    sa.event.listen(_immutable_model, "before_delete", _reject_immutable_delete)
