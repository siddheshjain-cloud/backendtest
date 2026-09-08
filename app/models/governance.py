"""Milestone 1 mutable governance facts.

Governance flags require sourced factual evidence and an explicitly separated
interpretation. The record is corrected or archived through the command
service; archival preserves the row and there is no delete command.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import sqlalchemy as sa
import sqlalchemy.orm as so

from app.models.base import BaseModel
from app.models.research_types import (
    GovernanceFlagStatus,
    GovernanceSeverity,
    enum_type,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class GovernanceFlag(BaseModel):
    """One curated governance risk flag for an M1 company."""

    __tablename__ = "governance_flag"

    company_id: so.Mapped[str] = so.mapped_column(
        sa.ForeignKey("company.id"), nullable=False
    )
    flag_type: so.Mapped[str] = so.mapped_column(
        sa.String(100), nullable=False
    )
    title: so.Mapped[str] = so.mapped_column(sa.String(300), nullable=False)
    severity: so.Mapped[str] = so.mapped_column(
        enum_type(
            "governance_severity",
            (
                GovernanceSeverity.INFO,
                GovernanceSeverity.LOW,
                GovernanceSeverity.MEDIUM,
                GovernanceSeverity.HIGH,
                GovernanceSeverity.CRITICAL,
            ),
        ),
        nullable=False,
    )
    status: so.Mapped[str] = so.mapped_column(
        enum_type(
            "governance_flag_status",
            (
                GovernanceFlagStatus.OPEN,
                GovernanceFlagStatus.MONITORING,
                GovernanceFlagStatus.RESOLVED,
                GovernanceFlagStatus.DISMISSED,
            ),
        ),
        nullable=False,
    )
    factual_evidence: so.Mapped[str] = so.mapped_column(
        sa.String(4000), nullable=False
    )
    source_title: so.Mapped[str | None] = so.mapped_column(
        sa.String(300), nullable=True
    )
    source_url_or_reference: so.Mapped[str] = so.mapped_column(
        sa.String(1000), nullable=False
    )
    interpretation: so.Mapped[str] = so.mapped_column(
        sa.String(4000), nullable=False
    )
    observed_on: so.Mapped[date | None] = so.mapped_column(
        sa.Date, nullable=True
    )
    resolved_on: so.Mapped[date | None] = so.mapped_column(
        sa.Date, nullable=True
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

    __table_args__ = (
        sa.CheckConstraint(
            "(status <> 'RESOLVED' AND resolved_on IS NULL) OR "
            "(status = 'RESOLVED' AND resolved_on IS NOT NULL)",
            name="ck_governance_flag_resolved_state",
        ),
        sa.CheckConstraint(
            "resolved_on IS NULL OR observed_on IS NULL "
            "OR resolved_on >= observed_on",
            name="ck_governance_flag_resolved_after_observed",
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
        return f"<GovernanceFlag {self.company_id} {self.flag_type}>"
