"""Milestone 1 one-to-one institutional report metadata extension.

``InstitutionalReportMetadata`` extends exactly one ``INSTITUTIONAL_RESEARCH``
document with its institution, optional analyst, and required report type.
Several reports from the same institution are valid and older reports are
retained. This is a one-to-one extension, not a separate document store.
"""

from __future__ import annotations

from datetime import datetime, timezone

import sqlalchemy as sa
import sqlalchemy.orm as so

from app import db


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class InstitutionalReportMetadata(db.Model):
    """One-to-one institutional extension keyed by the document identifier."""

    __tablename__ = "institutional_report_metadata"

    document_id: so.Mapped[str] = so.mapped_column(
        sa.ForeignKey("document.id"), primary_key=True
    )
    institution_id: so.Mapped[str] = so.mapped_column(
        sa.ForeignKey("institution.id"), nullable=False
    )
    analyst_name: so.Mapped[str | None] = so.mapped_column(
        sa.String(200), nullable=True
    )
    report_type: so.Mapped[str] = so.mapped_column(
        sa.String(100), nullable=False
    )
    created_at: so.Mapped[datetime] = so.mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
    )
    updated_at: so.Mapped[datetime] = so.mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
        onupdate=_utcnow,
    )

    # Unidirectional read relationship; no reverse column or back-reference
    # is added to the legacy Institution model.
    document: so.Mapped["Document"] = so.relationship(
        "Document", back_populates="institutional_metadata"
    )
    institution: so.Mapped["Institution"] = so.relationship(
        "Institution", viewonly=True
    )

    def __repr__(self) -> str:
        return (
            f"<InstitutionalReportMetadata {self.document_id} "
            f"{self.institution_id}>"
        )
