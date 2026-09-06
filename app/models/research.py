"""Immutable narrative research revisions and their ordered child points.

``ResearchRevision`` is the authoritative, append-only M1 SPA Research View for
a company at a point in time. The current revision is always the row with the
highest ``revision_number`` for that company. Neither ``effective_at`` nor the
inherited ``created_at`` audit timestamp is an evidence cutoff, and no
confidence/evidence-cutoff fields are part of this frozen M1 model.
"""

from __future__ import annotations

from datetime import date, datetime

import sqlalchemy as sa
import sqlalchemy.orm as so

from app.models.base import BaseModel
from app.models.research_types import (
    GovernanceStatus,
    ManagementQuality,
    ResearchPointKind,
    enum_type,
)

class ResearchRevision(BaseModel):
    """Append-only snapshot of a company's narrative research."""

    __tablename__ = "research_revision"

    company_id: so.Mapped[str] = so.mapped_column(
        sa.ForeignKey("company.id"), nullable=False
    )
    revision_number: so.Mapped[int] = so.mapped_column(
        sa.Integer, nullable=False
    )
    supersedes_revision_id: so.Mapped[str | None] = so.mapped_column(
        sa.ForeignKey("research_revision.id"), nullable=True
    )

    why_selected: so.Mapped[str] = so.mapped_column(
        sa.String(4000), nullable=False
    )
    what_is_changing: so.Mapped[str | None] = so.mapped_column(
        sa.String(4000), nullable=True
    )
    business_journey: so.Mapped[str | None] = so.mapped_column(
        sa.String(4000), nullable=True
    )
    thesis: so.Mapped[str] = so.mapped_column(sa.String(4000), nullable=False)
    thesis_invalidation: so.Mapped[str] = so.mapped_column(
        sa.String(4000), nullable=False
    )

    management_summary: so.Mapped[str | None] = so.mapped_column(
        sa.String(4000), nullable=True
    )
    management_quality: so.Mapped[str] = so.mapped_column(
        enum_type(
            "management_quality",
            (
                ManagementQuality.UNASSESSED,
                ManagementQuality.WEAK,
                ManagementQuality.WATCH,
                ManagementQuality.ACCEPTABLE,
                ManagementQuality.STRONG,
            ),
        ),
        nullable=False,
    )
    management_rationale: so.Mapped[str | None] = so.mapped_column(
        sa.String(4000), nullable=True
    )
    management_evidence: so.Mapped[str | None] = so.mapped_column(
        sa.String(4000), nullable=True
    )

    governance_status: so.Mapped[str] = so.mapped_column(
        enum_type(
            "governance_status",
            (
                GovernanceStatus.UNREVIEWED,
                GovernanceStatus.CLEAR,
                GovernanceStatus.WATCH,
                GovernanceStatus.HIGH_RISK,
            ),
        ),
        nullable=False,
    )

    change_reason: so.Mapped[str | None] = so.mapped_column(
        sa.String(2000), nullable=True
    )
    effective_at: so.Mapped[datetime] = so.mapped_column(
        sa.DateTime(timezone=True), nullable=False
    )
    created_by_user_id: so.Mapped[str] = so.mapped_column(
        sa.ForeignKey("user.id"), nullable=False
    )

    __table_args__ = (
        sa.UniqueConstraint(
            "company_id",
            "revision_number",
            name="uq_research_revision_company_number",
        ),
        sa.CheckConstraint(
            "revision_number > 0",
            name="ck_research_revision_number_positive",
        ),
    )

    company: so.Mapped["Company"] = so.relationship(
        "Company", viewonly=True
    )
    created_by_user: so.Mapped["User"] = so.relationship(
        "User", viewonly=True
    )
    supersedes: so.Mapped["ResearchRevision | None"] = so.relationship(
        "ResearchRevision",
        remote_side="ResearchRevision.id",
        viewonly=True,
        uselist=False,
    )
    points: so.Mapped[list["ResearchPoint"]] = so.relationship(
        "ResearchPoint",
        order_by="ResearchPoint.sort_order",
        cascade="all, delete-orphan",
        back_populates="research_revision",
    )

    def __repr__(self) -> str:
        return (
            f"<ResearchRevision {self.company_id} "
            f"#{self.revision_number}>"
        )


class ResearchPoint(BaseModel):
    """A catalyst or risk permanently owned by one immutable revision."""

    __tablename__ = "research_point"

    research_revision_id: so.Mapped[str] = so.mapped_column(
        sa.ForeignKey("research_revision.id"), nullable=False
    )
    kind: so.Mapped[str] = so.mapped_column(
        enum_type(
            "research_point_kind",
            (ResearchPointKind.CATALYST, ResearchPointKind.RISK),
        ),
        nullable=False,
    )
    title: so.Mapped[str] = so.mapped_column(sa.String(300), nullable=False)
    detail: so.Mapped[str | None] = so.mapped_column(
        sa.String(4000), nullable=True
    )
    status: so.Mapped[str | None] = so.mapped_column(
        sa.String(100), nullable=True
    )
    target_date: so.Mapped[date | None] = so.mapped_column(
        sa.Date, nullable=True
    )
    sort_order: so.Mapped[int] = so.mapped_column(
        sa.Integer, nullable=False
    )

    __table_args__ = (
        sa.UniqueConstraint(
            "research_revision_id",
            "kind",
            "sort_order",
            name="uq_research_point_revision_kind_sort",
        ),
        sa.CheckConstraint(
            "sort_order >= 0",
            name="ck_research_point_sort_order_nonnegative",
        ),
    )

    research_revision: so.Mapped["ResearchRevision"] = so.relationship(
        "ResearchRevision", back_populates="points"
    )

    def __repr__(self) -> str:
        return f"<ResearchPoint {self.kind} {self.sort_order}>"
