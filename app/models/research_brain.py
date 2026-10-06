"""Research Brain Pilot: Document -> Extraction -> Evidence -> Facts.

Four purely additive models, independent of the frozen Milestone 1 schema
(``migrations/m1_table_inventory.py``'s ``M1_TABLES``). ``ExtractionRun`` and
``Evidence`` are append-only provenance for one extraction pass over one
document's already-stored content. ``ExtractedFact`` is a structured, typed
data point following the exact immutable/append-only discipline already used
by ``OwnershipSnapshot``/``ResearchRevision``: a later, contradictory reading
of the same ``(company_id, fact_type, period)`` is recorded as a *new* row
naming the fact it supersedes, never an in-place edit. ``FactEvidence`` is the
many-to-many join that makes cross-document corroboration representable --
one fact may be backed by evidence from several independent documents.

``as_of_date`` is deliberately distinct from the inherited ``created_at``:
``created_at`` is row-insert bookkeeping, while ``as_of_date`` is epistemic
time -- when the underlying information actually became knowable/applicable.
Point-in-time research must always reason from ``as_of_date``, never from
``created_at``.
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


class ExtractionRun(BaseModel):
    """Provenance for one extraction pass over one document. Immutable."""

    __tablename__ = "extraction_run"

    document_id: so.Mapped[str] = so.mapped_column(
        sa.ForeignKey("document.id"), nullable=False
    )
    method: so.Mapped[str] = so.mapped_column(sa.String(100), nullable=False)
    extracted_by_user_id: so.Mapped[str] = so.mapped_column(
        sa.ForeignKey("user.id"), nullable=False
    )

    document: so.Mapped["Document"] = so.relationship(
        "Document", viewonly=True
    )
    extracted_by_user: so.Mapped["User"] = so.relationship(
        "User", viewonly=True
    )

    def __repr__(self) -> str:
        return f"<ExtractionRun {self.document_id} {self.method}>"


class Evidence(BaseModel):
    """One excerpt of source text tied to exactly one extraction run/document.

    Immutable -- never updated or deleted, matching ``GovernanceFlag``'s
    evidence-citation discipline but as a structured, queryable row instead
    of a free-text field.
    """

    __tablename__ = "evidence"

    extraction_run_id: so.Mapped[str] = so.mapped_column(
        sa.ForeignKey("extraction_run.id"), nullable=False
    )
    document_id: so.Mapped[str] = so.mapped_column(
        sa.ForeignKey("document.id"), nullable=False
    )
    text_snippet: so.Mapped[str] = so.mapped_column(
        sa.String(4000), nullable=False
    )
    locator: so.Mapped[str | None] = so.mapped_column(
        sa.String(200), nullable=True
    )
    created_by_user_id: so.Mapped[str] = so.mapped_column(
        sa.ForeignKey("user.id"), nullable=False
    )

    extraction_run: so.Mapped["ExtractionRun"] = so.relationship(
        "ExtractionRun", viewonly=True
    )
    document: so.Mapped["Document"] = so.relationship(
        "Document", viewonly=True
    )
    created_by_user: so.Mapped["User"] = so.relationship(
        "User", viewonly=True
    )

    def __repr__(self) -> str:
        return f"<Evidence {self.document_id} {self.locator!r}>"


class ExtractedFact(BaseModel):
    """A structured, typed, evidence-backed data point.

    ``value_type`` is the one discriminator (``NUMERIC`` | ``TEXT``) that lets
    a single ``value`` column hold either a numeric metric or a textual/
    management assertion, without a larger per-type ontology. ``unit`` is
    only meaningful when ``value_type = NUMERIC``, enforced at the database
    via a check constraint, not merely by convention.

    Append-only: a later, contradictory reading of the same
    ``(company_id, fact_type, period)`` is recorded as a new row naming the
    fact it supersedes via ``supersedes_fact_id`` -- the same shape as
    ``ResearchRevision.supersedes_revision_id``. The "current" fact for a
    given ``(company_id, fact_type, period)`` is the row not referenced by
    any other row's ``supersedes_fact_id``.
    """

    __tablename__ = "extracted_fact"

    company_id: so.Mapped[str] = so.mapped_column(
        sa.ForeignKey("company.id"), nullable=False
    )
    fact_type: so.Mapped[str] = so.mapped_column(
        sa.String(100), nullable=False
    )
    value_type: so.Mapped[str] = so.mapped_column(
        enum_type("extracted_fact_value_type", ("NUMERIC", "TEXT")),
        nullable=False,
    )
    value: so.Mapped[str] = so.mapped_column(sa.String(4000), nullable=False)
    unit: so.Mapped[str | None] = so.mapped_column(
        sa.String(50), nullable=True
    )
    period: so.Mapped[str] = so.mapped_column(sa.String(50), nullable=False)
    as_of_date: so.Mapped[date] = so.mapped_column(sa.Date, nullable=False)
    supersedes_fact_id: so.Mapped[str | None] = so.mapped_column(
        sa.ForeignKey("extracted_fact.id"), nullable=True
    )
    created_by_user_id: so.Mapped[str] = so.mapped_column(
        sa.ForeignKey("user.id"), nullable=False
    )

    __table_args__ = (
        sa.CheckConstraint(
            "value_type = 'NUMERIC' OR unit IS NULL",
            name="ck_extracted_fact_unit_requires_numeric",
        ),
    )

    company: so.Mapped["Company"] = so.relationship(
        "Company", viewonly=True
    )
    created_by_user: so.Mapped["User"] = so.relationship(
        "User", viewonly=True
    )
    supersedes: so.Mapped["ExtractedFact | None"] = so.relationship(
        "ExtractedFact",
        remote_side="ExtractedFact.id",
        viewonly=True,
        uselist=False,
    )
    fact_evidence_links: so.Mapped[list["FactEvidence"]] = so.relationship(
        "FactEvidence", viewonly=True
    )

    def __repr__(self) -> str:
        return f"<ExtractedFact {self.company_id} {self.fact_type} {self.period}>"


class FactEvidence(db.Model):
    """Composite-PK join between one fact and one supporting evidence row.

    Same shape as ``DocumentCompanyLink``. This is what makes cross-document
    corroboration representable: one ``ExtractedFact`` row may link to many
    ``Evidence`` rows, each pointing at a different source ``Document``.
    """

    __tablename__ = "fact_evidence"

    fact_id: so.Mapped[str] = so.mapped_column(
        sa.ForeignKey("extracted_fact.id"), primary_key=True
    )
    evidence_id: so.Mapped[str] = so.mapped_column(
        sa.ForeignKey("evidence.id"), primary_key=True
    )
    created_at: so.Mapped[datetime] = so.mapped_column(
        sa.DateTime(timezone=True), nullable=False, default=_utcnow
    )

    fact: so.Mapped["ExtractedFact"] = so.relationship(
        "ExtractedFact", viewonly=True
    )
    evidence: so.Mapped["Evidence"] = so.relationship(
        "Evidence", viewonly=True
    )

    def __repr__(self) -> str:
        return f"<FactEvidence fact={self.fact_id} evidence={self.evidence_id}>"


def _reject_immutable_update(_mapper, _connection, target) -> None:
    raise sa.exc.InvalidRequestError(
        f"{type(target).__name__} is immutable after insertion"
    )


def _reject_immutable_delete(_mapper, _connection, target) -> None:
    raise sa.exc.InvalidRequestError(
        f"{type(target).__name__} is immutable and cannot be deleted"
    )


for _immutable_model in (ExtractionRun, Evidence, ExtractedFact):
    sa.event.listen(_immutable_model, "before_update", _reject_immutable_update)
    sa.event.listen(_immutable_model, "before_delete", _reject_immutable_delete)
