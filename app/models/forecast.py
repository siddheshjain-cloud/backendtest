"""Immutable administrator-supplied forecast revisions and line snapshots.

``ForecastRevision`` records an administrator snapshot of future revenue,
EBITDA, PAT, margin, and per-share earnings for a company. Every value is
supplied: Milestone 1 performs no forecasting, EPS derivation, share-count
storage, or margin calculation. The stream is append-only and independent
from every other M1 revision stream.

Phase 3 Slice A adds ``scenario`` (nullable -- ``NULL`` preserves the
exact pre-Phase-3, single-stream meaning) and ``origin``. Zero rows exist
in this table system-wide as of Slice A, so neither addition carries any
backfill risk. ``scenario``'s uniqueness is enforced by two *partial*
unique indexes, not one composite ``UniqueConstraint`` -- SQL NULL is
never equal to another NULL for uniqueness purposes, so a single
composite constraint including a nullable ``scenario`` column would
silently stop enforcing the legacy "(company_id, revision_number) is
unique while scenario is NULL" invariant (verified empirically against
this project's SQLite engine before this was written). The fix mirrors
the filtered-index pair ``app/models/document.py`` already uses for
``metadata_fingerprint``.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import sqlalchemy as sa
import sqlalchemy.orm as so

from app.models.base import BaseModel
from app.models.research_types import (
    InvestmentOrigin,
    Scenario,
    enum_type,
    money_column,
)


class ForecastRevision(BaseModel):
    """One immutable forecast revision for a company."""

    __tablename__ = "forecast_revision"

    company_id: so.Mapped[str] = so.mapped_column(
        sa.ForeignKey("company.id"), nullable=False
    )
    revision_number: so.Mapped[int] = so.mapped_column(
        sa.Integer, nullable=False
    )
    scenario: so.Mapped[str | None] = so.mapped_column(
        enum_type(
            "forecast_revision_scenario",
            (
                Scenario.BULL,
                Scenario.BASE,
                Scenario.BEAR,
                Scenario.MID_CYCLE,
            ),
        ),
        nullable=True,
    )
    origin: so.Mapped[str] = so.mapped_column(
        enum_type(
            "forecast_revision_origin",
            (InvestmentOrigin.SYSTEM_DRAFT, InvestmentOrigin.HUMAN_AUTHORED),
        ),
        nullable=False,
        server_default=InvestmentOrigin.HUMAN_AUTHORED,
    )
    supersedes_revision_id: so.Mapped[str | None] = so.mapped_column(
        sa.ForeignKey("forecast_revision.id"), nullable=True
    )
    as_of_date: so.Mapped[date] = so.mapped_column(
        sa.Date, nullable=False
    )
    assumptions: so.Mapped[str | None] = so.mapped_column(
        sa.String(4000), nullable=True
    )
    change_reason: so.Mapped[str | None] = so.mapped_column(
        sa.String(2000), nullable=True
    )
    created_by_user_id: so.Mapped[str] = so.mapped_column(
        sa.ForeignKey("user.id"), nullable=False
    )

    __table_args__ = (
        sa.Index(
            "uq_forecast_revision_company_number_legacy",
            "company_id",
            "revision_number",
            unique=True,
            sqlite_where=sa.text("scenario IS NULL"),
            postgresql_where=sa.text("scenario IS NULL"),
        ),
        sa.Index(
            "uq_forecast_revision_company_scenario_number",
            "company_id",
            "scenario",
            "revision_number",
            unique=True,
            sqlite_where=sa.text("scenario IS NOT NULL"),
            postgresql_where=sa.text("scenario IS NOT NULL"),
        ),
        sa.CheckConstraint(
            "revision_number > 0",
            name="ck_forecast_revision_number_positive",
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
    supersedes: so.Mapped["ForecastRevision | None"] = so.relationship(
        "ForecastRevision",
        remote_side="ForecastRevision.id",
        viewonly=True,
        uselist=False,
    )
    lines: so.Mapped[list["ForecastLine"]] = so.relationship(
        "ForecastLine",
        order_by="ForecastLine.fiscal_year",
        cascade="all, delete-orphan",
        back_populates="forecast_revision",
    )

    def __repr__(self) -> str:
        return (
            f"<ForecastRevision {self.company_id} "
            f"#{self.revision_number}>"
        )


class ForecastLine(BaseModel):
    """One immutable fiscal-year estimate permanently owned by a revision."""

    __tablename__ = "forecast_line"

    forecast_revision_id: so.Mapped[str] = so.mapped_column(
        sa.ForeignKey("forecast_revision.id"), nullable=False
    )
    fiscal_year: so.Mapped[int] = so.mapped_column(
        sa.Integer, nullable=False
    )
    is_estimate: so.Mapped[bool] = so.mapped_column(
        sa.Boolean, nullable=False
    )
    revenue: so.Mapped[Decimal | None] = money_column()
    ebitda: so.Mapped[Decimal | None] = money_column()
    pat: so.Mapped[Decimal | None] = money_column()
    ebitda_margin_pct: so.Mapped[Decimal | None] = so.mapped_column(
        sa.Numeric(7, 4), nullable=True
    )
    eps: so.Mapped[Decimal | None] = money_column()
    currency: so.Mapped[str] = so.mapped_column(
        sa.String(3), nullable=False, server_default="INR"
    )
    unit: so.Mapped[str] = so.mapped_column(
        sa.String(20), nullable=False
    )

    __table_args__ = (
        sa.UniqueConstraint(
            "forecast_revision_id",
            "fiscal_year",
            name="uq_forecast_line_revision_fiscal_year",
        ),
        sa.CheckConstraint(
            "ebitda_margin_pct IS NULL OR "
            "(ebitda_margin_pct >= 0 AND ebitda_margin_pct <= 100)",
            name="ck_forecast_line_margin_range",
        ),
    )

    forecast_revision: so.Mapped["ForecastRevision"] = so.relationship(
        "ForecastRevision", back_populates="lines"
    )

    def __repr__(self) -> str:
        return (
            f"<ForecastLine {self.forecast_revision_id} "
            f"{self.fiscal_year}>"
        )


def _reject_immutable_update(_mapper, _connection, target) -> None:
    raise sa.exc.InvalidRequestError(
        f"{type(target).__name__} is immutable after insertion"
    )


def _reject_immutable_delete(_mapper, _connection, target) -> None:
    raise sa.exc.InvalidRequestError(
        f"{type(target).__name__} is immutable and cannot be deleted"
    )


for _immutable_model in (ForecastRevision, ForecastLine):
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
