"""Milestone 1 manually curated company disclosures.

A disclosure represents the event; an attached filing is represented once in
the common Document Library. ``is_key`` is a manual designation only, and
Milestone 1 has no numeric importance field or automated classification.
``document_id`` remains a nullable pre-Document string placeholder in this
plan until the document table enters metadata.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import sqlalchemy as sa
import sqlalchemy.orm as so

from app.models.base import BaseModel


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class CompanyDisclosure(BaseModel):
    """One manually curated disclosure event for an M1 company."""

    __tablename__ = "company_disclosure"

    company_id: so.Mapped[str] = so.mapped_column(
        sa.ForeignKey("company.id"), nullable=False
    )
    event_type: so.Mapped[str] = so.mapped_column(
        sa.String(64), nullable=False
    )
    event_date: so.Mapped[date] = so.mapped_column(
        sa.Date, nullable=False
    )
    title: so.Mapped[str] = so.mapped_column(sa.String(300), nullable=False)
    original_source_url_or_reference: so.Mapped[str] = so.mapped_column(
        sa.String(1000), nullable=False
    )
    exchange_reference: so.Mapped[str | None] = so.mapped_column(
        sa.String(200), nullable=True
    )
    significance_note: so.Mapped[str | None] = so.mapped_column(
        sa.String(4000), nullable=True
    )
    is_key: so.Mapped[bool] = so.mapped_column(
        sa.Boolean,
        nullable=False,
        default=False,
        server_default=sa.false(),
    )
    document_id: so.Mapped[str | None] = so.mapped_column(
        sa.String(36), nullable=True
    )
    created_by_user_id: so.Mapped[str] = so.mapped_column(
        sa.ForeignKey("user.id"), nullable=False
    )
    updated_at: so.Mapped[datetime] = so.mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
        onupdate=_utcnow,
    )
    archived_at: so.Mapped[datetime | None] = so.mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )

    # Unidirectional read relationships; no reverse column or back-reference is
    # added to the legacy Company or User models.
    company: so.Mapped["Company"] = so.relationship(
        "Company", viewonly=True
    )
    created_by_user: so.Mapped["User"] = so.relationship(
        "User", viewonly=True
    )

    def __repr__(self) -> str:
        return f"<CompanyDisclosure {self.company_id} {self.event_type}>"
