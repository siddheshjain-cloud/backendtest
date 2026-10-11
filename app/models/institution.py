"""Milestone 1 institution identity for institutional research documents."""

from __future__ import annotations

from datetime import datetime, timezone

import sqlalchemy as sa
import sqlalchemy.orm as so

from app.models.base import BaseModel


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Institution(BaseModel):
    """One curated research publisher or institution."""

    __tablename__ = "institution"

    name: so.Mapped[str] = so.mapped_column(sa.String(200), nullable=False)
    normalized_name: so.Mapped[str] = so.mapped_column(
        sa.String(200), nullable=False
    )
    website: so.Mapped[str | None] = so.mapped_column(
        sa.String(1000), nullable=True
    )
    updated_at: so.Mapped[datetime] = so.mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
        onupdate=_utcnow,
    )

    __table_args__ = (
        sa.UniqueConstraint(
            "normalized_name",
            name="uq_institution_normalized_name",
        ),
    )

    def __repr__(self) -> str:
        return f"<Institution {self.name!r}>"
