"""Milestone 1 business-group and company identity models."""

from __future__ import annotations

from datetime import datetime, timezone

import sqlalchemy as sa
import sqlalchemy.orm as so

from app.models.base import BaseModel


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class BusinessGroup(BaseModel):
    """Curated business family or promoter group for M1 companies."""

    __tablename__ = "business_group"

    name: so.Mapped[str] = so.mapped_column(sa.String(200), nullable=False)
    notes: so.Mapped[str | None] = so.mapped_column(
        sa.String(2000), nullable=True
    )
    source_reference: so.Mapped[str | None] = so.mapped_column(
        sa.String(1000), nullable=True
    )
    updated_at: so.Mapped[datetime] = so.mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
        onupdate=_utcnow,
    )

    __table_args__ = (
        sa.UniqueConstraint("name", name="uq_business_group_name"),
    )

    def __repr__(self) -> str:
        return f"<BusinessGroup {self.name}>"


class Company(BaseModel):
    """Stable research identity for one M1 listed company.

    ``isin`` is the bounded M1 identity of the primary listed equity
    represented by the Company/Ticker relationship. It is not a permanent
    universal corporate-issuer identifier.
    """

    __tablename__ = "company"

    ticker_id: so.Mapped[str] = so.mapped_column(
        sa.ForeignKey("ticker.id"), nullable=False
    )
    legal_name: so.Mapped[str] = so.mapped_column(
        sa.String(200), nullable=False
    )
    display_name: so.Mapped[str | None] = so.mapped_column(
        sa.String(200), nullable=True
    )
    isin: so.Mapped[str] = so.mapped_column(sa.String(12), nullable=False)
    sector: so.Mapped[str | None] = so.mapped_column(
        sa.String(100), nullable=True, index=True
    )
    industry: so.Mapped[str | None] = so.mapped_column(
        sa.String(100), nullable=True, index=True
    )
    business_group_id: so.Mapped[str | None] = so.mapped_column(
        sa.ForeignKey("business_group.id"), nullable=True
    )
    business_group_basis: so.Mapped[str | None] = so.mapped_column(
        sa.String(200), nullable=True
    )
    business_group_source_reference: so.Mapped[str | None] = so.mapped_column(
        sa.String(1000), nullable=True
    )
    updated_at: so.Mapped[datetime] = so.mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
        onupdate=_utcnow,
    )

    __table_args__ = (
        sa.UniqueConstraint("ticker_id", name="uq_company_ticker_id"),
        sa.UniqueConstraint("isin", name="uq_company_isin"),
        sa.CheckConstraint(
            "(business_group_id IS NULL AND business_group_basis IS NULL "
            "AND business_group_source_reference IS NULL) OR "
            "(business_group_id IS NOT NULL AND business_group_basis IS NOT NULL "
            "AND business_group_source_reference IS NOT NULL)",
            name="ck_company_business_group_evidence",
        ),
    )

    # Unidirectional read relationship; no reverse column or back-reference is
    # added to the legacy Ticker model.
    ticker: so.Mapped["Ticker"] = so.relationship("Ticker", viewonly=True)
    business_group: so.Mapped["BusinessGroup | None"] = so.relationship(
        "BusinessGroup"
    )

    @so.validates("isin")
    def _normalize_isin(self, _key: str, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip().upper()

    @property
    def display_label(self) -> str:
        return self.display_name or self.legal_name

    def __repr__(self) -> str:
        return f"<Company {self.legal_name}>"
