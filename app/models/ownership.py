"""Milestone 1 append-only dated ownership snapshots."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import sqlalchemy as sa
import sqlalchemy.orm as so

from app.models.base import BaseModel


class OwnershipSnapshot(BaseModel):
    """One dated, sourced ownership record for a company.

    Ownership history is append-only: changes are represented by later
    snapshots and prior rows are never updated or deleted.
    """

    __tablename__ = "ownership_snapshot"

    company_id: so.Mapped[str] = so.mapped_column(
        sa.ForeignKey("company.id"), nullable=False
    )
    as_of_date: so.Mapped[date] = so.mapped_column(
        sa.Date, nullable=False
    )
    promoter_holding_pct: so.Mapped[Decimal | None] = so.mapped_column(
        sa.Numeric(7, 4), nullable=True
    )
    promoter_pledge_pct: so.Mapped[Decimal | None] = so.mapped_column(
        sa.Numeric(7, 4), nullable=True
    )
    notes: so.Mapped[str | None] = so.mapped_column(
        sa.String(4000), nullable=True
    )
    source_reference: so.Mapped[str | None] = so.mapped_column(
        sa.String(1000), nullable=True
    )
    created_by_user_id: so.Mapped[str] = so.mapped_column(
        sa.ForeignKey("user.id"), nullable=False
    )

    __table_args__ = (
        sa.UniqueConstraint(
            "company_id",
            "as_of_date",
            name="uq_ownership_snapshot_company_as_of",
        ),
        sa.CheckConstraint(
            "promoter_holding_pct IS NULL OR "
            "(promoter_holding_pct >= 0 AND promoter_holding_pct <= 100)",
            name="ck_ownership_promoter_holding_range",
        ),
        sa.CheckConstraint(
            "promoter_pledge_pct IS NULL OR "
            "(promoter_pledge_pct >= 0 AND promoter_pledge_pct <= 100)",
            name="ck_ownership_promoter_pledge_range",
        ),
        sa.CheckConstraint(
            "(promoter_holding_pct IS NULL AND promoter_pledge_pct IS NULL) "
            "OR source_reference IS NOT NULL",
            name="ck_ownership_snapshot_source_reference",
        ),
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
        return f"<OwnershipSnapshot {self.company_id} {self.as_of_date}>"


def _reject_immutable_update(_mapper, _connection, target) -> None:
    raise sa.exc.InvalidRequestError(
        f"{type(target).__name__} is immutable after insertion"
    )


def _reject_immutable_delete(_mapper, _connection, target) -> None:
    raise sa.exc.InvalidRequestError(
        f"{type(target).__name__} is immutable and cannot be deleted"
    )


sa.event.listen(
    OwnershipSnapshot,
    "before_update",
    _reject_immutable_update,
)
sa.event.listen(
    OwnershipSnapshot,
    "before_delete",
    _reject_immutable_delete,
)
