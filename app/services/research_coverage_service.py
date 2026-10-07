"""Research Coverage & Fact Intelligence, Slice 1: ``ResearchCoverageService``.

Mirrors ``ResearchBrainService``'s transactional shape exactly: each write
method is one atomic commit (explicit ``db.session.add``/``flush``/
``commit``, rolled back whole on any failure). The one business rule that
cannot be expressed as a single-table database constraint --
``record_coverage_review_pass``'s completeness check -- is enforced here in
code, the same style ``record_fact``'s "at least one evidence" rule already
uses.

``record_coverage_review_pass`` is a deliberate, approved collapse of the
design proposal's two-step ``open_review_pass``/``record_review_pass_result``
into one atomic call: a review pass is realistically reported as one
retrospective "I read X, here's what I found" statement, not a long-running
session that needs opening in advance. In one transaction it (a) inserts
the ``CoverageReviewPass`` row, (b) reads the document's true total
``ExtractionUnit`` count directly (never trusting a caller-supplied total),
(c) sums ``units_considered_count`` across every ``CoverageReviewPass`` ever
recorded for that exact ``(document_id, research_dimension_id)`` pair, and
(d) writes a ``CoverageRecord`` only if that sum has reached the true total
-- the mechanical guard against "narrow-task tunnel vision" the whole
capability exists to build.
"""

from __future__ import annotations

from datetime import date

import sqlalchemy as sa
import sqlalchemy.orm as so

from app import db
from app.models.document import Document, DocumentCompanyLink
from app.models.research_brain import ExtractionUnit
from app.models.research_coverage import (
    CoverageDocumentSubtype,
    CoverageProfile,
    CoverageProfileDimension,
    CoverageRecord,
    CoverageReviewPass,
    ResearchDimension,
)
from app.utils.research_errors import ResearchValidationError


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
    def get_active_profile(cls, document_type_code: str) -> CoverageProfile | None:
        """The current (non-superseded) profile for a ``document_type_code``.

        "Current" = the row not referenced by any other row's
        ``supersedes_profile_id``, same idiom as ``ExtractedFact``.
        """

        superseded_ids = sa.select(CoverageProfile.supersedes_profile_id).where(
            CoverageProfile.supersedes_profile_id.is_not(None)
        )

        return db.session.scalars(
            sa.select(CoverageProfile)
            .where(
                CoverageProfile.document_type_code == document_type_code,
                CoverageProfile.id.not_in(superseded_ids),
            )
            .order_by(CoverageProfile.effective_from.desc())
            .limit(1)
        ).first()

    @classmethod
    def get_required_dimensions(cls, document_id: str) -> list[ResearchDimension]:
        """Resolve a document's required dimensions via its active profile.

        Precedence: if a ``CoverageDocumentSubtype`` tag exists for the
        document, its ``subtype_code`` is used as the ``document_type_code``
        lookup key in preference to the document's native
        ``Document.document_type`` -- a document explicitly tagged (e.g. as
        a DRHP) should use that profile even though its native
        ``document_type`` may sit at ``OTHER``. Otherwise the native
        ``document_type`` value is used directly.
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
    def record_coverage_review_pass(
        cls,
        *,
        document_id: str,
        research_dimension_id: str,
        performed_by_user_id: str,
        units_considered_count: int,
        units_considered_min_seq: int | None = None,
        units_considered_max_seq: int | None = None,
        has_material_content: bool = False,
        notes: str | None = None,
        coverage_profile_id: str | None = None,
    ) -> CoverageReviewPass:
        """Record one complete, immutable review pass and, if cumulative
        coverage for this ``(document_id, research_dimension_id)`` pair is
        now complete, close it with a ``CoverageRecord`` -- all in one
        transaction.

        A pass covering only part of a document's units can never produce a
        ``CoverageRecord``: the completeness comparison is always against
        the document's true ``ExtractionUnit`` count, read fresh from the
        database, never a caller-supplied total.
        """

        try:
            review_pass = CoverageReviewPass(
                document_id=document_id,
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
                    ExtractionUnit.document_id == document_id
                )
            )

            passes_for_pair = db.session.scalars(
                sa.select(CoverageReviewPass).where(
                    CoverageReviewPass.document_id == document_id,
                    CoverageReviewPass.research_dimension_id
                    == research_dimension_id,
                )
            ).all()

            cumulative_units_considered = sum(
                p.units_considered_count for p in passes_for_pair
            )
            any_material_content = any(
                p.has_material_content for p in passes_for_pair
            )

            if total_units > 0 and cumulative_units_considered >= total_units:
                state = (
                    "FINDING_GENERATED"
                    if any_material_content
                    else "REVIEWED_NO_FINDING"
                )
                db.session.add(
                    CoverageRecord(
                        document_id=document_id,
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
    def _current_coverage_records(cls, document_id: str) -> dict[str, CoverageRecord]:
        """Map ``research_dimension_id`` -> its most recent ``CoverageRecord``
        row for a document (there is at most one, by construction, since
        once a record closes a dimension no further record is ever written
        for that pair in this slice)."""

        rows = db.session.scalars(
            sa.select(CoverageRecord)
            .where(CoverageRecord.document_id == document_id)
            .order_by(CoverageRecord.created_at.desc())
        ).all()

        current: dict[str, CoverageRecord] = {}
        for row in rows:
            current.setdefault(row.research_dimension_id, row)
        return current

    @classmethod
    def get_document_dimension_coverage(cls, document_id: str) -> dict[str, str]:
        """Dimension code -> current state for every required dimension.

        ``NOT_REVIEWED`` for any required dimension with no ``CoverageRecord``
        row yet.
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
        document = db.session.get(Document, document_id)
        if document is None:
            raise ResearchValidationError(
                {"document_id": ["Document not found"]}
            )

        subtype = cls._current_subtype(document_id)
        document_type_code = (
            subtype.subtype_code if subtype is not None else document.document_type
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
