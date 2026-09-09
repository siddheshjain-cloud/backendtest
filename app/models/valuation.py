"""Immutable method-neutral valuation revisions and reference lines.

``ValuationRevision`` stores an administrator-supplied valuation conclusion
for one company and one controlled valuation method. Revision streams are
independent per ``(company_id, valuation_method)``. ``ValuationReferenceLine``
records optional, extensible source/context inputs for exactly one immutable
valuation revision.

Milestone 1 stores administrator-supplied snapshots only. It never calculates
the enterprise-to-equity bridge, operating metrics, net debt, multiples, or
discounted values.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import sqlalchemy as sa
import sqlalchemy.orm as so

from app.models.base import BaseModel
from app.models.research_types import ValuationMethod, enum_type


class ValuationRevision(BaseModel):
    """One append-only valuation revision for one company and method."""

    __tablename__ = "valuation_revision"

    company_id: so.Mapped[str] = so.mapped_column(
        sa.ForeignKey("company.id"), nullable=False
    )
    valuation_method: so.Mapped[str] = so.mapped_column(
        enum_type(
            "valuation_method",
            (
                ValuationMethod.PE,
                ValuationMethod.EV_EBITDA,
                ValuationMethod.PB,
                ValuationMethod.NAV,
                ValuationMethod.SOTP,
                ValuationMethod.ASSET_VALUE,
                ValuationMethod.UNIT_BASED,
                ValuationMethod.OTHER,
            ),
        ),
        nullable=False,
    )
    revision_number: so.Mapped[int] = so.mapped_column(
        sa.Integer, nullable=False
    )
    supersedes_revision_id: so.Mapped[str | None] = so.mapped_column(
        sa.ForeignKey("valuation_revision.id"), nullable=True
    )

    justified_multiple: so.Mapped[Decimal | None] = so.mapped_column(
        sa.Numeric(20, 4), nullable=True
    )
    implied_enterprise_value: so.Mapped[Decimal | None] = so.mapped_column(
        sa.Numeric(20, 4), nullable=True
    )
    net_debt: so.Mapped[Decimal | None] = so.mapped_column(
        sa.Numeric(20, 4), nullable=True
    )
    other_equity_adjustment: so.Mapped[Decimal | None] = so.mapped_column(
        sa.Numeric(20, 4), nullable=True
    )
    implied_future_equity_value: so.Mapped[Decimal | None] = so.mapped_column(
        sa.Numeric(20, 4), nullable=True
    )
    required_return_pct: so.Mapped[Decimal | None] = so.mapped_column(
        sa.Numeric(20, 4), nullable=True
    )
    discount_period_years: so.Mapped[Decimal | None] = so.mapped_column(
        sa.Numeric(20, 4), nullable=True
    )
    present_value: so.Mapped[Decimal | None] = so.mapped_column(
        sa.Numeric(20, 4), nullable=True
    )
    current_market_cap: so.Mapped[Decimal | None] = so.mapped_column(
        sa.Numeric(20, 4), nullable=True
    )

    currency: so.Mapped[str | None] = so.mapped_column(
        sa.String(3), nullable=True
    )
    unit: so.Mapped[str | None] = so.mapped_column(
        sa.String(20), nullable=True
    )
    valuation_notes: so.Mapped[str | None] = so.mapped_column(
        sa.String(4000), nullable=True
    )
    as_of_date: so.Mapped[date] = so.mapped_column(
        sa.Date, nullable=False
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
            "valuation_method",
            "revision_number",
            name="uq_valuation_revision_company_method_number",
        ),
        sa.CheckConstraint(
            "revision_number > 0",
            name="ck_valuation_revision_number_positive",
        ),
        sa.CheckConstraint(
            "discount_period_years IS NULL OR discount_period_years > 0",
            name="ck_valuation_revision_discount_positive",
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
    supersedes: so.Mapped["ValuationRevision | None"] = so.relationship(
        "ValuationRevision",
        remote_side="ValuationRevision.id",
        viewonly=True,
        uselist=False,
    )
    reference_lines: so.Mapped[list["ValuationReferenceLine"]] = (
        so.relationship(
            "ValuationReferenceLine",
            order_by="ValuationReferenceLine.sort_order",
            cascade="all, delete-orphan",
            back_populates="valuation_revision",
        )
    )

    def __repr__(self) -> str:
        return (
            f"<ValuationRevision {self.company_id} "
            f"{self.valuation_method} #{self.revision_number}>"
        )


class ValuationReferenceLine(BaseModel):
    """One immutable reference/context input owned by a valuation revision."""

    __tablename__ = "valuation_reference_line"

    valuation_revision_id: so.Mapped[str] = so.mapped_column(
        sa.ForeignKey("valuation_revision.id"), nullable=False
    )
    reference_forecast_revision_id: so.Mapped[str | None] = so.mapped_column(
        sa.ForeignKey("forecast_revision.id"), nullable=True
    )
    reference_fiscal_year: so.Mapped[int | None] = so.mapped_column(
        sa.Integer, nullable=True
    )
    reference_metric: so.Mapped[str] = so.mapped_column(
        sa.String(64), nullable=False
    )
    reference_metric_value: so.Mapped[Decimal] = so.mapped_column(
        sa.Numeric(20, 4), nullable=False
    )
    reference_metric_unit: so.Mapped[str] = so.mapped_column(
        sa.String(64), nullable=False
    )
    reference_metric_basis: so.Mapped[str] = so.mapped_column(
        sa.String(4000), nullable=False
    )
    sort_order: so.Mapped[int] = so.mapped_column(
        sa.Integer, nullable=False
    )

    __table_args__ = (
        sa.UniqueConstraint(
            "valuation_revision_id",
            "sort_order",
            name="uq_valuation_reference_line_revision_sort_order",
        ),
        sa.CheckConstraint(
            "sort_order >= 0",
            name="ck_valuation_reference_line_sort_order_nonnegative",
        ),
        sa.Index(
            "ix_valuation_reference_line_valuation_revision_id",
            "valuation_revision_id",
        ),
        sa.Index(
            "ix_valuation_reference_line_forecast_revision_id",
            "reference_forecast_revision_id",
        ),
    )

    valuation_revision: so.Mapped["ValuationRevision"] = so.relationship(
        "ValuationRevision", back_populates="reference_lines"
    )
    reference_forecast_revision: so.Mapped[
        "ForecastRevision | None"
    ] = so.relationship("ForecastRevision", viewonly=True)

    def __repr__(self) -> str:
        return (
            f"<ValuationReferenceLine {self.reference_metric} "
            f"{self.sort_order}>"
        )


def _reject_immutable_update(_mapper, _connection, target) -> None:
    raise sa.exc.InvalidRequestError(
        f"{type(target).__name__} is immutable after insertion"
    )


def _reject_immutable_delete(_mapper, _connection, target) -> None:
    raise sa.exc.InvalidRequestError(
        f"{type(target).__name__} is immutable and cannot be deleted"
    )


for _immutable_model in (ValuationRevision, ValuationReferenceLine):
    sa.event.listen(
        _immutable_model,
        "before_update",
        _reject_immutable_update,
    )
    sa.event.listen(
        _immutable_model,
        "before_delete",
        _reject_immutable_delete,
    )
