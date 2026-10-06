"""Research Brain Pilot: extraction/evidence/fact write path and the
``get_company_facts`` Research View read path.

Mirrors ``DocumentLibraryService``'s transactional shape: each write method
is one atomic commit, rolled back whole on any failure. ``record_fact``
enforces the one business rule that cannot be expressed as a single-table
database constraint: every fact must cite at least one piece of evidence --
a fact with none is rejected outright, never silently allowed.
"""

from __future__ import annotations

from datetime import date

import sqlalchemy as sa
import sqlalchemy.orm as so

from app import db
from app.models.research_brain import (
    Evidence,
    ExtractedFact,
    ExtractionRun,
    ExtractionUnit,
    FactEvidence,
)
from app.utils.research_errors import ResearchValidationError


class ResearchBrainService:
    """Owns the Research Brain Pilot's write commands and Research View read."""

    @classmethod
    def create_extraction_run(
        cls, *, document_id: str, method: str, extracted_by_user_id: str
    ) -> ExtractionRun:
        run = ExtractionRun(
            document_id=document_id,
            method=method,
            extracted_by_user_id=extracted_by_user_id,
        )
        try:
            db.session.add(run)
            db.session.commit()
        except Exception:
            db.session.rollback()
            raise
        return run

    @classmethod
    def record_extraction_unit(
        cls,
        *,
        extraction_run_id: str,
        document_id: str,
        unit_type: str,
        sequence_number: int,
        content_text: str,
        created_by_user_id: str,
        locator: str | None = None,
    ) -> ExtractionUnit:
        unit = ExtractionUnit(
            extraction_run_id=extraction_run_id,
            document_id=document_id,
            unit_type=unit_type,
            sequence_number=sequence_number,
            content_text=content_text,
            locator=locator,
            created_by_user_id=created_by_user_id,
        )
        try:
            db.session.add(unit)
            db.session.commit()
        except Exception:
            db.session.rollback()
            raise
        return unit

    @classmethod
    def get_extraction_units(cls, document_id: str) -> list[ExtractionUnit]:
        """Every persisted unit for a document, across all extraction runs.

        An empty result is the "not yet processed" signal; a non-empty
        result means "already processed" -- a per-caller choice to skip
        re-extraction, never a constraint this schema enforces. A later,
        intentional reprocessing pass simply calls ``record_extraction_unit``
        again under a new ``extraction_run_id``; the prior run's units are
        untouched.
        """

        return list(
            db.session.scalars(
                sa.select(ExtractionUnit).where(
                    ExtractionUnit.document_id == document_id
                )
            ).all()
        )

    @classmethod
    def record_evidence(
        cls,
        *,
        extraction_run_id: str,
        document_id: str,
        text_snippet: str,
        created_by_user_id: str,
        locator: str | None = None,
        source_extraction_unit_id: str | None = None,
    ) -> Evidence:
        evidence = Evidence(
            extraction_run_id=extraction_run_id,
            document_id=document_id,
            text_snippet=text_snippet,
            locator=locator,
            source_extraction_unit_id=source_extraction_unit_id,
            created_by_user_id=created_by_user_id,
        )
        try:
            db.session.add(evidence)
            db.session.commit()
        except Exception:
            db.session.rollback()
            raise
        return evidence

    @classmethod
    def record_fact(
        cls,
        *,
        company_id: str,
        fact_type: str,
        value_type: str,
        value: str,
        period: str,
        as_of_date: date,
        evidence_ids: list[str],
        created_by_user_id: str,
        unit: str | None = None,
        supersedes_fact_id: str | None = None,
    ) -> ExtractedFact:
        try:
            if not evidence_ids:
                raise ResearchValidationError(
                    {"evidence_ids": ["At least one evidence_id is required"]}
                )

            fact = ExtractedFact(
                company_id=company_id,
                fact_type=fact_type,
                value_type=value_type,
                value=value,
                unit=unit,
                period=period,
                as_of_date=as_of_date,
                supersedes_fact_id=supersedes_fact_id,
                created_by_user_id=created_by_user_id,
            )
            db.session.add(fact)
            db.session.flush()

            for evidence_id in evidence_ids:
                db.session.add(
                    FactEvidence(fact_id=fact.id, evidence_id=evidence_id)
                )
            db.session.flush()

            db.session.commit()
        except Exception:
            db.session.rollback()
            raise
        return fact

    @classmethod
    def get_company_facts(cls, company_id: str) -> list[ExtractedFact]:
        """The Research View: every current (non-superseded) fact for a
        company, each with its full evidence/document chain eager-loaded so
        the result needs no further queries to be fully sourced.

        "Current" mirrors how M1 already defines "current revision"
        elsewhere: the row never referenced by any other row's
        ``supersedes_fact_id``.
        """

        superseded_ids = sa.select(ExtractedFact.supersedes_fact_id).where(
            ExtractedFact.supersedes_fact_id.is_not(None)
        )

        return list(
            db.session.scalars(
                sa.select(ExtractedFact)
                .where(
                    ExtractedFact.company_id == company_id,
                    ExtractedFact.id.not_in(superseded_ids),
                )
                .options(
                    so.selectinload(ExtractedFact.fact_evidence_links).selectinload(
                        FactEvidence.evidence
                    ).selectinload(Evidence.document)
                )
            ).all()
        )
