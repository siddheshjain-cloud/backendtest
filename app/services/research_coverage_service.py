"""Research Coverage & Fact Intelligence, Slice 1: ``ResearchCoverageService``.

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
"""

from __future__ import annotations

from datetime import date

import sqlalchemy as sa
import sqlalchemy.orm as so

from app import db
from app.models.document import Document, DocumentCompanyLink
from app.models.research_brain import ExtractionRun, ExtractionUnit
from app.models.research_coverage import (
    CoverageDocumentSubtype,
    CoverageProfile,
    CoverageProfileDimension,
    CoverageRecord,
    CoverageReviewPass,
    ResearchDimension,
)
from app.utils.research_errors import ResearchValidationError


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
        """Most recent ``CoverageDocumentSubtype`` row for a document."""

        return db.session.scalars(
            sa.select(CoverageDocumentSubtype)
            .where(CoverageDocumentSubtype.document_id == document_id)
            .order_by(CoverageDocumentSubtype.created_at.desc())
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
            raise ResearchValidationError(
                {"document_id": ["Document not found"]}
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
        """

        query = (
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
        if for_update:
            query = query.with_for_update()
        return db.session.scalar(query)

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

            total_units = db.session.scalar(
                sa.select(sa.func.count(ExtractionUnit.id)).where(
                    ExtractionUnit.document_id == document_id,
                    ExtractionUnit.extraction_run_id == current_run_id,
                )
            )

            # Lock existing passes for this pair+run before deciding whether
            # to close it, so two concurrent submissions can't both read
            # "not yet closed" and both insert a closing CoverageRecord.
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

            already_closed = (
                research_dimension_id
                in cls._current_coverage_records(
                    document_id, extraction_run_id=current_run_id
                )
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
                db.session.add(
                    CoverageRecord(
                        document_id=document_id,
                        extraction_run_id=current_run_id,
                        research_dimension_id=research_dimension_id,
                        state=state,
                        review_pass_id=review_pass.id,
                    )
                )
                db.session.flush()

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
