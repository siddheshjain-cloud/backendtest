"""Research Coverage & Fact Intelligence, Slices 1-3: ``ResearchCoverageService``.

Mirrors ``ResearchBrainService``'s transactional shape exactly: each write
method is one atomic commit (explicit ``db.session.add``/``flush``/
``commit``, rolled back whole on any failure). The business rules that
cannot be expressed as single-table database constraints --
``record_coverage_review_pass``'s completeness check and its "no duplicate
closing record" invariant -- are enforced here in code, the same style
``record_fact``'s "at least one evidence" rule already uses.

``record_coverage_review_pass`` is a deliberate, approved collapse of the
design proposal's two-step ``open_review_pass``/``record_review_pass_result``
into one atomic call: a review pass is realistically reported as one
retrospective "I read X, here's what I found" statement, not a long-running
session that needs opening in advance. In one transaction it (a) resolves
the document's *current* ``ExtractionRun`` and inserts the
``CoverageReviewPass`` row pinned to it, (b) reads that run's true total
``ExtractionUnit`` count directly (never trusting a caller-supplied total),
(c) merges every pass ever recorded for that exact ``(document_id,
extraction_run_id, research_dimension_id)`` triple as true integer
intervals -- never a raw sum, which would silently double-count a
re-read or overlapping range -- and (d) writes a ``CoverageRecord`` only if
that merged union has reached the true total, and only if one does not
already exist for the pair. This is the mechanical guard against
"narrow-task tunnel vision" the whole capability exists to build; a pass
covering only part of a document, or re-reading pages already covered,
can never, by construction, produce a false completion.

Slice 2 adds candidate-finding staging: ``record_candidate_finding`` logs
an evidence-grounded proposal found during a sweep without asserting it as
a Fact; ``promote_candidate_finding``/``reject_candidate_finding``/
``mark_candidate_finding_duplicate`` triage it exactly once (locked against
the same concurrent-double-decision race Slice 1's duplicate-CoverageRecord
bug exposed). Promotion never bypasses ``ResearchBrainService.record_fact``'s
"at least one evidence" rule -- it always creates a real ``Evidence`` row
from the candidate's own quote first.

Slice 3 adds ``record_fact_derivation``: provenance-safe metadata for a
Fact that was computed (e.g. EBITDA from already-extracted raw P&L lines)
rather than directly quoted. It never touches the Fact itself -- it is a
pure side-annotation, recorded after the Fact already exists via the
unchanged ``record_fact``, naming the formula and citing each input as
either an existing Fact or a raw ``Evidence`` row.
"""

from __future__ import annotations

from datetime import date

import sqlalchemy as sa
import sqlalchemy.orm as so

from app import db
from app.models.document import Document, DocumentCompanyLink
from app.models.research_brain import Evidence, ExtractedFact, ExtractionRun, ExtractionUnit
from app.models.research_coverage import (
    CandidateFinding,
    CandidateFindingDecision,
    CoverageDocumentSubtype,
    CoverageProfile,
    CoverageProfileDimension,
    CoverageRecord,
    CoverageReviewPass,
    FactDerivation,
    FactDerivationInput,
    ResearchDimension,
)
from app.services.research_brain_service import ResearchBrainService
from app.utils.research_errors import ResearchNotFoundError, ResearchValidationError
from app.utils.research_validation import validate_upper_slug


def _merge_intervals(intervals: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Merge overlapping/adjacent ``[min_seq, max_seq]`` integer intervals.

    This is what makes the completeness check correct under re-reads and
    overlaps: two passes covering pages 1-10 and 6-15 merge into one 1-15
    interval (10 genuinely-new pages, not 10 + 10 = 20 double-counted ones).
    """

    if not intervals:
        return []
    ordered = sorted(intervals)
    merged = [ordered[0]]
    for start, end in ordered[1:]:
        last_start, last_end = merged[-1]
        if start <= last_end + 1:
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))
    return merged


def _union_length(intervals: list[tuple[int, int]]) -> int:
    """Total distinct units covered by a set of intervals, after merging."""

    return sum(end - start + 1 for start, end in _merge_intervals(intervals))


class ResearchCoverageService:
    """Owns Slice 1's write commands and coverage-status read path."""

    # -----------------------------------------------------------------
    # Dimensions
    # -----------------------------------------------------------------

    @classmethod
    def create_dimension(
        cls,
        *,
        code: str,
        name: str,
        description: str,
        is_active: bool = True,
    ) -> ResearchDimension:
        code = validate_upper_slug(code, "code")
        dimension = ResearchDimension(
            code=code, name=name, description=description, is_active=is_active
        )
        try:
            db.session.add(dimension)
            db.session.commit()
        except Exception:
            db.session.rollback()
            raise
        return dimension

    # -----------------------------------------------------------------
    # Document subtype tagging
    # -----------------------------------------------------------------

    @classmethod
    def tag_document_subtype(
        cls, *, document_id: str, subtype_code: str, assigned_by_user_id: str
    ) -> CoverageDocumentSubtype:
        """Tag a document with a subtype outside ``document_type``'s
        closed native enum (DRHP, RHP, ...).

        Requires a ``CoverageProfile`` to already exist for
        ``subtype_code``. Without this check, a typo'd or not-yet-defined
        subtype_code would be accepted silently, and
        ``get_required_dimensions`` would then return an empty list for
        the tagged document -- indistinguishable from a document type that
        genuinely has zero required dimensions, with no error raised
        anywhere.
        """

        subtype_code = validate_upper_slug(subtype_code, "subtype_code")
        if cls.get_active_profile(subtype_code) is None:
            raise ResearchValidationError(
                {
                    "subtype_code": [
                        "No CoverageProfile exists for this subtype_code "
                        "yet -- create one before tagging a document with it"
                    ]
                }
            )

        tag = CoverageDocumentSubtype(
            document_id=document_id,
            subtype_code=subtype_code,
            assigned_by_user_id=assigned_by_user_id,
        )
        try:
            db.session.add(tag)
            db.session.commit()
        except Exception:
            db.session.rollback()
            raise
        return tag

    @classmethod
    def _current_subtype(cls, document_id: str) -> CoverageDocumentSubtype | None:
        """Most recent ``CoverageDocumentSubtype`` row for a document.

        Tie-broken by ``id`` (same idiom as ``get_active_profile``'s
        double sort): without a deterministic tiebreaker, two rows
        inserted within the same ``created_at`` resolution window would
        make "current subtype" -- and therefore which coverage profile
        governs the document -- non-deterministic across reads of the
        same, unchanged data.
        """

        return db.session.scalars(
            sa.select(CoverageDocumentSubtype)
            .where(CoverageDocumentSubtype.document_id == document_id)
            .order_by(
                CoverageDocumentSubtype.created_at.desc(),
                CoverageDocumentSubtype.id.desc(),
            )
            .limit(1)
        ).first()

    # -----------------------------------------------------------------
    # Coverage profiles
    # -----------------------------------------------------------------

    @classmethod
    def create_coverage_profile(
        cls,
        *,
        document_type_code: str,
        dimension_requirements: list[tuple[str, bool, str | None]],
        effective_from: date,
        created_by_user_id: str,
        supersedes_profile_id: str | None = None,
    ) -> CoverageProfile:
        """Insert a profile plus its full set of dimension rows atomically.

        ``dimension_requirements`` is a list of
        ``(research_dimension_id, is_required, notes)`` tuples.
        """

        document_type_code = validate_upper_slug(
            document_type_code, "document_type_code"
        )

        try:
            profile = CoverageProfile(
                document_type_code=document_type_code,
                supersedes_profile_id=supersedes_profile_id,
                effective_from=effective_from,
                created_by_user_id=created_by_user_id,
            )
            db.session.add(profile)
            db.session.flush()

            for research_dimension_id, is_required, notes in dimension_requirements:
                db.session.add(
                    CoverageProfileDimension(
                        coverage_profile_id=profile.id,
                        research_dimension_id=research_dimension_id,
                        is_required=is_required,
                        notes=notes,
                    )
                )
            db.session.flush()

            db.session.commit()
        except Exception:
            db.session.rollback()
            raise
        return profile

    @classmethod
    def get_active_profile(
        cls, document_type_code: str, *, as_of: date | None = None
    ) -> CoverageProfile | None:
        """The profile actually governing lookups for a ``document_type_code``
        as of a given date (default: today).

        "Active" = the already-effective row (``effective_from <= as_of``)
        with the latest ``effective_from`` (ties broken by ``created_at``).
        Deliberately does *not* filter by whether a row is referenced by
        another row's ``supersedes_profile_id``: a future-dated revision
        correctly records its lineage via ``supersedes_profile_id`` the
        moment it is inserted, but must not make the *still-current* prior
        revision disappear from "active" lookups before its own
        ``effective_from`` arrives. ``supersedes_profile_id`` is lineage/
        audit history, not a second activation gate on top of
        ``effective_from``.
        """

        as_of = as_of if as_of is not None else date.today()

        return db.session.scalars(
            sa.select(CoverageProfile)
            .where(
                CoverageProfile.document_type_code == document_type_code,
                CoverageProfile.effective_from <= as_of,
            )
            .order_by(
                CoverageProfile.effective_from.desc(),
                CoverageProfile.created_at.desc(),
            )
            .limit(1)
        ).first()

    @classmethod
    def _resolve_document_type_code(cls, document_id: str) -> tuple[Document, str]:
        """The ``Document`` row plus its resolved ``document_type_code``.

        Precedence: if a ``CoverageDocumentSubtype`` tag exists for the
        document, its ``subtype_code`` is used as the ``document_type_code``
        lookup key in preference to the document's native
        ``Document.document_type`` -- a document explicitly tagged (e.g. as
        a DRHP) should use that profile even though its native
        ``document_type`` may sit at ``OTHER``. Otherwise the native
        ``document_type`` value is used directly. Single shared resolution
        so this precedence rule lives in exactly one place.
        """

        document = db.session.get(Document, document_id)
        if document is None:
            raise ResearchNotFoundError(
                "document_not_found", "Document was not found"
            )

        subtype = cls._current_subtype(document_id)
        document_type_code = (
            subtype.subtype_code if subtype is not None else document.document_type
        )
        return document, document_type_code

    @classmethod
    def get_required_dimensions(cls, document_id: str) -> list[ResearchDimension]:
        """Resolve a document's required dimensions via its active profile."""

        _document, document_type_code = cls._resolve_document_type_code(document_id)

        profile = cls.get_active_profile(document_type_code)
        if profile is None:
            return []

        rows = db.session.scalars(
            sa.select(CoverageProfileDimension)
            .where(
                CoverageProfileDimension.coverage_profile_id == profile.id,
                CoverageProfileDimension.is_required.is_(True),
            )
            .options(
                so.joinedload(CoverageProfileDimension.research_dimension)
            )
        ).all()
        return [row.research_dimension for row in rows]

    # -----------------------------------------------------------------
    # Review passes / coverage records
    # -----------------------------------------------------------------

    @classmethod
    def _resolve_current_run_id(
        cls, document_id: str, *, for_update: bool = False
    ) -> str | None:
        """The most recent ``ExtractionRun`` for this document that
        actually has at least one ``ExtractionUnit`` -- not merely the most
        recently-created row. Real corpora can carry empty, abandoned
        ``ExtractionRun`` rows (e.g. an ingestion attempt superseded before
        any unit was recorded); picking the latest row by ``created_at``
        alone, with no units, would silently and permanently block
        completeness (its unit count is 0) even though a prior run holds
        the document's real, fully-captured text.

        The id is resolved via a plain (unlocked) aggregate query -- a
        ``GROUP BY``/``HAVING`` query combined with ``FOR UPDATE`` is
        rejected outright by PostgreSQL ("FOR UPDATE is not allowed with
        GROUP BY clause"), so locking, when requested, is a second, simple
        by-id row lock against the already-resolved id instead of being
        folded into the aggregate query itself.
        """

        run_id = db.session.scalar(
            sa.select(ExtractionRun.id)
            .join(
                ExtractionUnit,
                ExtractionUnit.extraction_run_id == ExtractionRun.id,
            )
            .where(ExtractionRun.document_id == document_id)
            .group_by(ExtractionRun.id)
            .having(sa.func.count(ExtractionUnit.id) > 0)
            .order_by(ExtractionRun.created_at.desc())
            .limit(1)
        )
        if for_update and run_id is not None:
            db.session.scalar(
                sa.select(ExtractionRun.id)
                .where(ExtractionRun.id == run_id)
                .with_for_update()
            )
        return run_id

    @classmethod
    def record_coverage_review_pass(
        cls,
        *,
        document_id: str,
        research_dimension_id: str,
        performed_by_user_id: str,
        units_considered_count: int,
        units_considered_min_seq: int,
        units_considered_max_seq: int,
        has_material_content: bool = False,
        notes: str | None = None,
        coverage_profile_id: str | None = None,
    ) -> CoverageReviewPass:
        """Record one complete, immutable review pass and, if the merged
        union of every pass recorded against the document's *current*
        extraction run for this ``(document_id, research_dimension_id)``
        pair now covers that run's entire ``ExtractionUnit`` count, close
        it with a ``CoverageRecord`` -- all in one transaction.

        ``units_considered_min_seq``/``units_considered_max_seq`` must
        describe one contiguous range whose length equals
        ``units_considered_count``; a non-contiguous sweep (e.g. pages 1-10
        and 50-60 read in one sitting) must be recorded as two separate
        calls, one per contiguous chunk -- this is what lets completeness be
        computed as a true interval union instead of a raw, double-counting
        sum. A pass covering only part of a run's units -- or one that only
        re-reads pages another pass already covered -- can never produce a
        ``CoverageRecord``. If the pair is already closed, this call still
        records the new pass (for audit purposes) but never inserts a
        second, redundant ``CoverageRecord``.
        """

        if units_considered_max_seq < units_considered_min_seq:
            raise ResearchValidationError(
                {
                    "units_considered_max_seq": [
                        "Must be >= units_considered_min_seq"
                    ]
                }
            )
        expected_count = (
            units_considered_max_seq - units_considered_min_seq + 1
        )
        if units_considered_count != expected_count:
            raise ResearchValidationError(
                {
                    "units_considered_count": [
                        f"Must equal max_seq - min_seq + 1 ({expected_count}) "
                        "for a contiguous range -- record a non-contiguous "
                        "sweep as separate calls, one per contiguous chunk"
                    ]
                }
            )

        try:
            # Lock the Document row itself for the duration of this call --
            # closes a TOCTOU gap in _resolve_current_run_id: that method
            # resolves the "current" run via an unlocked aggregate query,
            # then takes a plain by-id lock only on the row it already
            # found. Without a lock scoped to the document as a whole, a
            # concurrent transaction could create and commit a brand-new
            # ExtractionRun (reprocessing) in the gap between that resolve
            # and that by-id lock, and this call would record its pass
            # against a run that's already been superseded.
            db.session.scalar(
                sa.select(Document.id)
                .where(Document.id == document_id)
                .with_for_update()
            )

            current_run_id = cls._resolve_current_run_id(
                document_id, for_update=True
            )
            if current_run_id is None:
                raise ResearchValidationError(
                    {
                        "document_id": [
                            "Document has no ExtractionRun with any "
                            "recorded ExtractionUnit yet"
                        ]
                    }
                )

            review_pass = CoverageReviewPass(
                document_id=document_id,
                extraction_run_id=current_run_id,
                research_dimension_id=research_dimension_id,
                coverage_profile_id=coverage_profile_id,
                units_considered_count=units_considered_count,
                units_considered_min_seq=units_considered_min_seq,
                units_considered_max_seq=units_considered_max_seq,
                has_material_content=has_material_content,
                performed_by_user_id=performed_by_user_id,
                notes=notes,
            )
            db.session.add(review_pass)
            db.session.flush()

            # Cheap check first: once a (document, run, dimension) triple
            # is closed it never reopens, so there is no need to lock and
            # re-merge every historical pass for an already-closed triple
            # on every subsequent, merely-for-the-record pass against it.
            already_closed = (
                research_dimension_id
                in cls._current_coverage_records(
                    document_id, extraction_run_id=current_run_id
                )
            )

            total_units = 0
            covered_units = 0
            any_material_content = False
            if not already_closed:
                total_units = db.session.scalar(
                    sa.select(sa.func.count(ExtractionUnit.id)).where(
                        ExtractionUnit.document_id == document_id,
                        ExtractionUnit.extraction_run_id == current_run_id,
                    )
                )

                # Lock existing passes for this pair+run before deciding
                # whether to close it, so two concurrent submissions can't
                # both read "not yet closed" and both insert a closing
                # CoverageRecord.
                passes_for_pair = db.session.scalars(
                    sa.select(CoverageReviewPass)
                    .where(
                        CoverageReviewPass.document_id == document_id,
                        CoverageReviewPass.extraction_run_id == current_run_id,
                        CoverageReviewPass.research_dimension_id
                        == research_dimension_id,
                    )
                    .with_for_update()
                ).all()

                covered_units = _union_length(
                    [
                        (p.units_considered_min_seq, p.units_considered_max_seq)
                        for p in passes_for_pair
                    ]
                )
                any_material_content = any(
                    p.has_material_content for p in passes_for_pair
                )

            if (
                not already_closed
                and total_units > 0
                and covered_units >= total_units
            ):
                state = (
                    "FINDING_GENERATED"
                    if any_material_content
                    else "REVIEWED_NO_FINDING"
                )
                # A savepoint, not a bare add/flush: the application-level
                # "lock existing rows, then decide" guard above has a real
                # gap for the *first-ever* close of a triple -- there is no
                # existing CoverageRecord row yet to lock, so two concurrent
                # first-time closes could both reach this branch. The
                # database-level unique constraint on CoverageRecord is the
                # actual guarantee; a savepoint lets that constraint's
                # violation (the loser of the race) roll back only this
                # insert, not the review pass this call already recorded.
                try:
                    with db.session.begin_nested():
                        db.session.add(
                            CoverageRecord(
                                document_id=document_id,
                                extraction_run_id=current_run_id,
                                research_dimension_id=research_dimension_id,
                                state=state,
                                review_pass_id=review_pass.id,
                            )
                        )
                except sa.exc.IntegrityError:
                    pass  # another concurrent call already closed this triple

            db.session.commit()
        except Exception:
            db.session.rollback()
            raise
        return review_pass

    @classmethod
    def _current_coverage_records(
        cls, document_id: str, *, extraction_run_id: str | None = None
    ) -> dict[str, CoverageRecord]:
        """Map ``research_dimension_id`` -> its most recent ``CoverageRecord``
        row for a document, scoped to one ``extraction_run_id`` (there is at
        most one per dimension within a single run, by construction, since
        once a record closes a dimension for that run no further record is
        ever written for that (run, dimension) pair in this slice).

        If ``extraction_run_id`` is omitted, it resolves to the document's
        current run (the same resolution ``record_coverage_review_pass``
        uses) -- a closing record against a now-superseded run must never
        be read as "this dimension is covered" for the document's current
        content.
        """

        if extraction_run_id is None:
            extraction_run_id = cls._resolve_current_run_id(document_id)
            if extraction_run_id is None:
                return {}

        rows = db.session.scalars(
            sa.select(CoverageRecord)
            .where(
                CoverageRecord.document_id == document_id,
                CoverageRecord.extraction_run_id == extraction_run_id,
            )
            .order_by(CoverageRecord.created_at.desc())
        ).all()

        current: dict[str, CoverageRecord] = {}
        for row in rows:
            current.setdefault(row.research_dimension_id, row)
        return current

    @classmethod
    def get_document_dimension_coverage(cls, document_id: str) -> dict[str, str]:
        """Dimension code -> current state for every required dimension,
        scoped to the document's current ``ExtractionRun``.

        ``NOT_REVIEWED`` for any required dimension with no ``CoverageRecord``
        row yet for that run.
        """

        required_dimensions = cls.get_required_dimensions(document_id)
        current_records = cls._current_coverage_records(document_id)

        coverage: dict[str, str] = {}
        for dimension in required_dimensions:
            record = current_records.get(dimension.id)
            coverage[dimension.code] = record.state if record is not None else "NOT_REVIEWED"
        return coverage

    @classmethod
    def get_document_coverage_status(cls, document_id: str) -> dict:
        _document, document_type_code = cls._resolve_document_type_code(
            document_id
        )

        dimensions = cls.get_document_dimension_coverage(document_id)

        closed_states = {"REVIEWED_NO_FINDING", "FINDING_GENERATED"}
        if not dimensions:
            rollup = "NOT_STARTED"
        elif all(state in closed_states for state in dimensions.values()):
            rollup = "FULLY_SWEPT"
        elif any(state in closed_states for state in dimensions.values()):
            rollup = "IN_PROGRESS"
        else:
            rollup = "NOT_STARTED"

        return {
            "document_type_code": document_type_code,
            "dimensions": dimensions,
            "rollup": rollup,
        }

    @classmethod
    def get_company_coverage_summary(cls, company_id: str) -> dict:
        """Roll up ``get_document_coverage_status`` across every ``Document``
        linked to this company via ``DocumentCompanyLink``."""

        document_ids = db.session.scalars(
            sa.select(DocumentCompanyLink.document_id).where(
                DocumentCompanyLink.company_id == company_id
            )
        ).all()

        rollup_counts = {"NOT_STARTED": 0, "IN_PROGRESS": 0, "FULLY_SWEPT": 0}
        total_required_dimension_slots = 0
        total_closed_dimension_slots = 0
        document_statuses: dict[str, dict] = {}

        for document_id in document_ids:
            status = cls.get_document_coverage_status(document_id)
            document_statuses[document_id] = status
            rollup_counts[status["rollup"]] += 1

            closed_states = {"REVIEWED_NO_FINDING", "FINDING_GENERATED"}
            for state in status["dimensions"].values():
                total_required_dimension_slots += 1
                if state in closed_states:
                    total_closed_dimension_slots += 1

        coverage_percentage = (
            round(100.0 * total_closed_dimension_slots / total_required_dimension_slots, 2)
            if total_required_dimension_slots
            else 0.0
        )

        return {
            "company_id": company_id,
            "document_count": len(document_ids),
            "documents_by_rollup": rollup_counts,
            "required_dimension_coverage_pct": coverage_percentage,
            "document_statuses": document_statuses,
        }

    # -----------------------------------------------------------------
    # Candidate findings (Slice 2)
    # -----------------------------------------------------------------

    @classmethod
    def record_candidate_finding(
        cls,
        *,
        document_id: str,
        research_dimension_id: str,
        source_extraction_unit_id: str,
        review_pass_id: str,
        raw_quote: str,
        created_by_user_id: str,
        proposed_fact_type: str | None = None,
        proposed_value: str | None = None,
        proposed_value_type: str | None = None,
        proposed_unit: str | None = None,
        proposed_period: str | None = None,
        proposed_as_of_date: date | None = None,
    ) -> CandidateFinding:
        """Stage one evidence-grounded proposal found during a coverage
        sweep. Non-authoritative -- it only becomes a Fact through
        ``promote_candidate_finding``."""

        finding = CandidateFinding(
            document_id=document_id,
            research_dimension_id=research_dimension_id,
            source_extraction_unit_id=source_extraction_unit_id,
            review_pass_id=review_pass_id,
            raw_quote=raw_quote,
            proposed_fact_type=proposed_fact_type,
            proposed_value=proposed_value,
            proposed_value_type=proposed_value_type,
            proposed_unit=proposed_unit,
            proposed_period=proposed_period,
            proposed_as_of_date=proposed_as_of_date,
            created_by_user_id=created_by_user_id,
        )
        try:
            db.session.add(finding)
            db.session.commit()
        except Exception:
            db.session.rollback()
            raise
        return finding

    @classmethod
    def _current_decision(
        cls, candidate_finding_id: str, *, for_update: bool = False
    ) -> CandidateFindingDecision | None:
        """Most recent decision row for a candidate, or ``None`` if it is
        still ``OPEN`` -- no row is ever written to represent ``OPEN``."""

        query = (
            sa.select(CandidateFindingDecision)
            .where(
                CandidateFindingDecision.candidate_finding_id
                == candidate_finding_id
            )
            .order_by(CandidateFindingDecision.created_at.desc())
            .limit(1)
        )
        if for_update:
            query = query.with_for_update()
        return db.session.scalars(query).first()

    @classmethod
    def get_candidate_finding_status(cls, candidate_finding_id: str) -> str:
        """``OPEN`` | ``PROMOTED`` | ``REJECTED`` | ``DUPLICATE``."""

        decision = cls._current_decision(candidate_finding_id)
        return decision.decision if decision is not None else "OPEN"

    @classmethod
    def _lock_and_require_open(cls, candidate_finding_id: str) -> CandidateFinding:
        """Check the candidate exists, then lock any existing decision row
        for it before deciding whether it's still OPEN -- the same race
        Slice 1's duplicate-CoverageRecord bug exposed: two concurrent
        triage calls must not both read "still open" and both write a
        decision.

        The existence check runs first and raises its own clear error,
        rather than letting a nonexistent id fall through to the later
        FK-violation IntegrityError that promote/reject/duplicate's own
        commit would raise -- which gets caught and reported as "already
        decided by a concurrent call," masking a plain bad id as a
        transient race.
        """

        candidate = db.session.get(CandidateFinding, candidate_finding_id)
        if candidate is None:
            raise ResearchNotFoundError(
                "candidate_finding_not_found", "CandidateFinding was not found"
            )

        decision = cls._current_decision(candidate_finding_id, for_update=True)
        if decision is not None:
            raise ResearchValidationError(
                {
                    "candidate_finding_id": [
                        f"Already decided: {decision.decision}"
                    ]
                }
            )
        return candidate

    @classmethod
    def promote_candidate_finding(
        cls,
        *,
        candidate_finding_id: str,
        company_id: str,
        created_by_user_id: str,
        fact_type: str | None = None,
        value: str | None = None,
        value_type: str | None = None,
        unit: str | None = None,
        period: str | None = None,
        as_of_date: date | None = None,
        supersedes_fact_id: str | None = None,
        locator: str | None = None,
    ) -> CandidateFindingDecision:
        """Promote a ``CandidateFinding`` into a real, fully-sourced Fact.

        Creates a fresh ``Evidence`` row from the candidate's own
        ``raw_quote``/``source_extraction_unit_id`` and calls the existing,
        unchanged ``ResearchBrainService.record_fact`` -- still requires
        >=1 Evidence, still participates in existing supersession. A
        promoted Candidate Finding is exactly as rigorous as a Fact
        authored today; it is just arrived at systematically.

        Each of ``fact_type``/``value``/``value_type``/``unit``/``period``/
        ``as_of_date`` left as ``None`` falls back to the candidate's own
        ``proposed_*`` field. ``company_id`` has no proposed fallback --
        a ``CandidateFinding`` is document-scoped, not company-scoped, and
        a document can in principle link to more than one company, so the
        caller must say which company this Fact belongs to.

        Known, accepted limitation: this is not one atomic transaction
        across Evidence + Fact + Decision, because ``record_evidence`` and
        ``record_fact`` each commit on their own and must not be modified
        (per the approved design, Slice 1-2 reuse them unchanged). The
        upfront "still open" check's lock is therefore released by those
        intermediate commits before this method's own final commit -- but
        the database's ``uq_candidate_finding_decision_one_per_candidate``
        constraint is the real guarantee, not that lock: if two concurrent
        calls both pass the upfront check, both create their own Evidence
        and Fact (a real, accepted waste on the rare losing side), but only
        one of them can ever successfully insert the ``CandidateFindingDecision``
        row -- the other fails the unique constraint and raises, so the
        candidate never ends up ambiguously decided and no silently-lost
        decision is possible.
        """

        candidate = cls._lock_and_require_open(candidate_finding_id)

        # `is not None`, consistently, for every field -- not `or` -- so an
        # explicit override of "" (or 0) is never silently swallowed and
        # replaced by the candidate's proposed_* value behind the caller's
        # back.
        final_fact_type = (
            fact_type if fact_type is not None else candidate.proposed_fact_type
        )
        final_value = value if value is not None else candidate.proposed_value
        final_value_type = (
            value_type if value_type is not None else candidate.proposed_value_type
        )
        final_unit = unit if unit is not None else candidate.proposed_unit
        final_period = (
            period if period is not None else candidate.proposed_period
        )
        final_as_of_date = (
            as_of_date if as_of_date is not None else candidate.proposed_as_of_date
        )

        missing = [
            field_name
            for field_name, field_value in (
                ("fact_type", final_fact_type),
                ("value", final_value),
                ("value_type", final_value_type),
                ("period", final_period),
                ("as_of_date", final_as_of_date),
            )
            if not field_value
        ]
        if missing:
            raise ResearchValidationError(
                {
                    field: [
                        "Required to promote -- no override given and no "
                        "proposed_* value on the candidate"
                    ]
                    for field in missing
                }
            )
        # Fail before any write, not after: the same "unit only valid for
        # NUMERIC" rule ExtractedFact enforces as a DB check constraint,
        # checked here too so a mismatched override/fallback combination
        # never gets as far as committing a real Evidence row for a Fact
        # that record_fact is about to reject -- an orphaned Evidence row
        # a retry would simply duplicate, not clean up.
        if final_unit is not None and final_value_type != "NUMERIC":
            raise ResearchValidationError(
                {"unit": ["Only valid when value_type is NUMERIC"]}
            )

        source_unit = db.session.get(
            ExtractionUnit, candidate.source_extraction_unit_id
        )

        evidence = ResearchBrainService.record_evidence(
            extraction_run_id=source_unit.extraction_run_id,
            document_id=candidate.document_id,
            text_snippet=candidate.raw_quote,
            created_by_user_id=created_by_user_id,
            locator=locator if locator is not None else source_unit.locator,
            source_extraction_unit_id=candidate.source_extraction_unit_id,
        )
        fact = ResearchBrainService.record_fact(
            company_id=company_id,
            fact_type=final_fact_type,
            value_type=final_value_type,
            value=final_value,
            unit=final_unit,
            period=final_period,
            as_of_date=final_as_of_date,
            evidence_ids=[evidence.id],
            created_by_user_id=created_by_user_id,
            supersedes_fact_id=supersedes_fact_id,
        )

        try:
            decision = CandidateFindingDecision(
                candidate_finding_id=candidate_finding_id,
                decision="PROMOTED",
                promoted_to_fact_id=fact.id,
                created_by_user_id=created_by_user_id,
            )
            db.session.add(decision)
            db.session.commit()
        except sa.exc.IntegrityError:
            db.session.rollback()
            raise ResearchValidationError(
                {
                    "candidate_finding_id": [
                        "Already decided by a concurrent call -- this "
                        "call's Evidence/Fact were created but this "
                        "Decision was not recorded"
                    ]
                }
            )
        except Exception:
            db.session.rollback()
            raise
        return decision

    @classmethod
    def reject_candidate_finding(
        cls, *, candidate_finding_id: str, reason: str, created_by_user_id: str
    ) -> CandidateFindingDecision:
        """Reject a ``CandidateFinding``. ``reason`` is required -- the
        service-enforced-not-DB-enforced style ``record_fact``'s "at least
        one evidence" rule already uses, and the same "never assert
        without saying why" discipline ``GovernanceFlag`` already applies
        (``factual_evidence`` vs. ``interpretation``)."""

        cls._lock_and_require_open(candidate_finding_id)
        if not reason or not reason.strip():
            raise ResearchValidationError(
                {"reason": ["Required to reject a candidate finding"]}
            )

        decision = CandidateFindingDecision(
            candidate_finding_id=candidate_finding_id,
            decision="REJECTED",
            reason=reason,
            created_by_user_id=created_by_user_id,
        )
        try:
            db.session.add(decision)
            db.session.commit()
        except sa.exc.IntegrityError:
            db.session.rollback()
            raise ResearchValidationError(
                {
                    "candidate_finding_id": [
                        "Already decided by a concurrent call"
                    ]
                }
            )
        except Exception:
            db.session.rollback()
            raise
        return decision

    @classmethod
    def mark_candidate_finding_duplicate(
        cls,
        *,
        candidate_finding_id: str,
        duplicate_of_candidate_id: str,
        created_by_user_id: str,
        reason: str | None = None,
    ) -> CandidateFindingDecision:
        """Mark a ``CandidateFinding`` as a duplicate of another, already
        on record, candidate finding."""

        cls._lock_and_require_open(candidate_finding_id)
        if candidate_finding_id == duplicate_of_candidate_id:
            raise ResearchValidationError(
                {
                    "duplicate_of_candidate_id": [
                        "A candidate finding cannot be a duplicate of itself"
                    ]
                }
            )
        if db.session.get(CandidateFinding, duplicate_of_candidate_id) is None:
            raise ResearchNotFoundError(
                "candidate_finding_not_found", "CandidateFinding was not found"
            )

        decision = CandidateFindingDecision(
            candidate_finding_id=candidate_finding_id,
            decision="DUPLICATE",
            duplicate_of_candidate_id=duplicate_of_candidate_id,
            reason=reason,
            created_by_user_id=created_by_user_id,
        )
        try:
            db.session.add(decision)
            db.session.commit()
        except sa.exc.IntegrityError:
            db.session.rollback()
            raise ResearchValidationError(
                {
                    "candidate_finding_id": [
                        "Already decided by a concurrent call"
                    ]
                }
            )
        except Exception:
            db.session.rollback()
            raise
        return decision

    @classmethod
    def get_candidate_findings_for_review_pass(
        cls, review_pass_id: str
    ) -> list[CandidateFinding]:
        return list(
            db.session.scalars(
                sa.select(CandidateFinding).where(
                    CandidateFinding.review_pass_id == review_pass_id
                )
            ).all()
        )

    @classmethod
    def get_open_candidate_findings(
        cls, document_id: str | None = None
    ) -> list[CandidateFinding]:
        """Every ``CandidateFinding`` with no decision row at all
        (``OPEN``), optionally narrowed to one document.

        One query, not one-plus-N: excludes by id against every candidate
        that has *any* decision row (a candidate, once decided, is never
        re-opened, so "has any decision" and "has a current decision" are
        the same set here).
        """

        decided_ids = sa.select(
            CandidateFindingDecision.candidate_finding_id
        ).distinct()

        query = sa.select(CandidateFinding).where(
            CandidateFinding.id.not_in(decided_ids)
        )
        if document_id is not None:
            query = query.where(CandidateFinding.document_id == document_id)
        return list(db.session.scalars(query).all())

    # -----------------------------------------------------------------
    # Fact derivation (Slice 3)
    # -----------------------------------------------------------------

    @classmethod
    def record_fact_derivation(
        cls,
        *,
        derived_fact_id: str,
        formula_description: str,
        created_by_user_id: str,
        inputs: list[dict],
    ) -> FactDerivation:
        """Record that an already-existing ``ExtractedFact`` was computed
        by SPA from other already-sourced inputs, rather than directly
        quoted. Does not touch ``ExtractedFact`` at all -- this is purely
        additional provenance metadata recorded alongside a Fact that was
        already created through the normal, unchanged ``record_fact`` call.

        ``inputs`` is a list of dicts, each with exactly one of
        ``input_fact_id``/``input_evidence_id`` set, plus an optional
        ``role_label`` (e.g. ``"Finance costs"``). At least one input is
        required -- a "derivation" from zero inputs is not a derivation.
        """

        if db.session.get(ExtractedFact, derived_fact_id) is None:
            raise ResearchNotFoundError(
                "extracted_fact_not_found", "ExtractedFact was not found"
            )
        if not inputs:
            raise ResearchValidationError(
                {"inputs": ["At least one derivation input is required"]}
            )
        for index, item in enumerate(inputs):
            has_fact = item.get("input_fact_id") is not None
            has_evidence = item.get("input_evidence_id") is not None
            if has_fact == has_evidence:
                raise ResearchValidationError(
                    {
                        f"inputs[{index}]": [
                            "Exactly one of input_fact_id/input_evidence_id "
                            "must be set"
                        ]
                    }
                )

        try:
            derivation = FactDerivation(
                derived_fact_id=derived_fact_id,
                formula_description=formula_description,
                created_by_user_id=created_by_user_id,
            )
            db.session.add(derivation)
            db.session.flush()

            for item in inputs:
                db.session.add(
                    FactDerivationInput(
                        fact_derivation_id=derivation.id,
                        input_fact_id=item.get("input_fact_id"),
                        input_evidence_id=item.get("input_evidence_id"),
                        role_label=item.get("role_label"),
                    )
                )
            db.session.flush()
            db.session.commit()
        except sa.exc.IntegrityError:
            db.session.rollback()
            raise ResearchValidationError(
                {
                    "derived_fact_id": [
                        "This Fact already has a derivation recorded -- "
                        "at most one FactDerivation per Fact"
                    ]
                }
            )
        except Exception:
            db.session.rollback()
            raise
        return derivation

    @classmethod
    def get_fact_derivation(cls, fact_id: str) -> FactDerivation | None:
        """The ``FactDerivation`` for a Fact, or ``None`` if it was
        directly quoted rather than computed."""

        return db.session.scalars(
            sa.select(FactDerivation).where(
                FactDerivation.derived_fact_id == fact_id
            )
        ).first()

    @classmethod
    def is_fact_derived(cls, fact_id: str) -> bool:
        return cls.get_fact_derivation(fact_id) is not None
