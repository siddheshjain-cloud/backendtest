"""Immutable administrator-supplied market-plan revision stream.

``MarketPlanRevision`` records manually entered accumulation and supply
bounds for a company. The stream is append-only and independent from every
other M1 revision stream. Price-based invalidation is a distinct required
concept from ``ResearchRevision.thesis_invalidation``; no technical engine
creates or updates these rows in Milestone 1.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import sqlalchemy as sa
import sqlalchemy.orm as so

from app.models.base import BaseModel


class MarketPlanRevision(BaseModel):
    """One immutable market-plan revision for a company."""

    __tablename__ = "market_plan_revision"

    company_id: so.Mapped[str] = so.mapped_column(
        sa.ForeignKey("company.id"), nullable=False
    )
    revision_number: so.Mapped[int] = so.mapped_column(
        sa.Integer, nullable=False
    )
    supersedes_revision_id: so.Mapped[str | None] = so.mapped_column(
        sa.ForeignKey("market_plan_revision.id"), nullable=True
    )

    currency: so.Mapped[str] = so.mapped_column(
        sa.String(3), nullable=False, server_default="INR"
    )
    accumulation_low: so.Mapped[Decimal] = so.mapped_column(
        sa.Numeric(20, 4), nullable=False
    )
    accumulation_high: so.Mapped[Decimal] = so.mapped_column(
        sa.Numeric(20, 4), nullable=False
    )
    preferred_accumulation_price: so.Mapped[Decimal | None] = so.mapped_column(
        sa.Numeric(20, 4), nullable=True
    )
    supply_low: so.Mapped[Decimal | None] = so.mapped_column(
        sa.Numeric(20, 4), nullable=True
    )
    supply_high: so.Mapped[Decimal | None] = so.mapped_column(
        sa.Numeric(20, 4), nullable=True
    )
    invalidation_level: so.Mapped[Decimal] = so.mapped_column(
        sa.Numeric(20, 4), nullable=False
    )
    rationale: so.Mapped[str | None] = so.mapped_column(
        sa.String(4000), nullable=True
    )
    effective_at: so.Mapped[datetime] = so.mapped_column(
        sa.DateTime(timezone=True), nullable=False
    )
    change_reason: so.Mapped[str | None] = so.mapped_column(
        sa.String(2000), nullable=True
    )
    created_by_user_id: so.Mapped[str] = so.mapped_column(
        sa.ForeignKey("user.id"), nullable=False
    )

    __table_args__ = (
        sa.UniqueConstraint(
            "company_id",
            "revision_number",
            name="uq_market_plan_revision_company_number",
        ),
        sa.CheckConstraint(
            "revision_number > 0",
            name="ck_market_plan_revision_number_positive",
        ),
        sa.CheckConstraint(
            "accumulation_low > 0 AND accumulation_high > 0",
            name="ck_market_plan_accumulation_positive",
        ),
        sa.CheckConstraint(
            "accumulation_low <= accumulation_high",
            name="ck_market_plan_accumulation_ordered",
        ),
        sa.CheckConstraint(
            "preferred_accumulation_price IS NULL OR "
            "(preferred_accumulation_price >= accumulation_low "
            "AND preferred_accumulation_price <= accumulation_high)",
            name="ck_market_plan_preferred_inside_range",
        ),
        sa.CheckConstraint(
            "(supply_low IS NULL AND supply_high IS NULL) OR "
            "(supply_low IS NOT NULL AND supply_high IS NOT NULL)",
            name="ck_market_plan_supply_pair_present",
        ),
        sa.CheckConstraint(
            "supply_low IS NULL OR supply_high IS NULL "
            "OR supply_low <= supply_high",
            name="ck_market_plan_supply_ordered",
        ),
        sa.CheckConstraint(
            "invalidation_level > 0",
            name="ck_market_plan_invalidation_positive",
        ),
    )

    # Unidirectional read relationships; no reverse column or back-reference
    # is added to the legacy Company or User models.
    company: so.Mapped["Company"] = so.relationship(
        "Company", viewonly=True
    )
    created_by_user: so.Mapped["User"] = so.relationship(
        "User", viewonly=True
    )
    supersedes: so.Mapped["MarketPlanRevision | None"] = so.relationship(
        "MarketPlanRevision",
        remote_side="MarketPlanRevision.id",
        viewonly=True,
        uselist=False,
    )

    def __repr__(self) -> str:
        return (
            f"<MarketPlanRevision {self.company_id} "
            f"#{self.revision_number}>"
        )


def _reject_immutable_update(_mapper, _connection, target) -> None:
    raise sa.exc.InvalidRequestError(
        f"{type(target).__name__} is immutable after insertion"
    )


def _reject_immutable_delete(_mapper, _connection, target) -> None:
    raise sa.exc.InvalidRequestError(
        f"{type(target).__name__} is immutable and cannot be deleted"
    )


sa.event.listen(
    MarketPlanRevision,
    "before_update",
    _reject_immutable_update,
)
sa.event.listen(
    MarketPlanRevision,
    "before_delete",
    _reject_immutable_delete,
)
