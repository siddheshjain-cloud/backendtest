"""Milestone 1 one-row user entitlement model."""

from __future__ import annotations

from datetime import datetime, timezone

import sqlalchemy as sa
import sqlalchemy.orm as so

from app.models.base import BaseModel
from app.models.research_types import (
    EntitlementStatus,
    ResearchTier,
    enum_type,
)


INVESTMENT_RESEARCH_PRODUCT_CODE = "INVESTMENT_RESEARCH"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class UserEntitlement(BaseModel):
    """Exactly zero or one mutable entitlement row per user and product."""

    __tablename__ = "user_entitlement"

    user_id: so.Mapped[str] = so.mapped_column(
        sa.ForeignKey("user.id"), nullable=False
    )
    product_code: so.Mapped[str] = so.mapped_column(
        sa.String(64), nullable=False
    )
    tier: so.Mapped[str] = so.mapped_column(
        enum_type(
            "user_entitlement_tier",
            (ResearchTier.FREE, ResearchTier.PREMIUM),
        ),
        nullable=False,
    )
    status: so.Mapped[str] = so.mapped_column(
        enum_type(
            "user_entitlement_status",
            (
                EntitlementStatus.ACTIVE,
                EntitlementStatus.INACTIVE,
                EntitlementStatus.REVOKED,
            ),
        ),
        nullable=False,
    )
    valid_from: so.Mapped[datetime | None] = so.mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )
    valid_until: so.Mapped[datetime | None] = so.mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )
    updated_at: so.Mapped[datetime] = so.mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
        onupdate=_utcnow,
    )

    __table_args__ = (
        sa.UniqueConstraint(
            "user_id",
            "product_code",
            name="uq_user_entitlement_user_product",
        ),
    )

    # Unidirectional read relationship; no reverse relationship or column is
    # added to the legacy User model.
    user: so.Mapped["User"] = so.relationship("User", viewonly=True)

    def __repr__(self) -> str:
        return f"<UserEntitlement {self.user_id} {self.product_code}>"
