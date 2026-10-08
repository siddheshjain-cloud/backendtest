"""Phase 3 Slice A -- the controlled, standard-neutral financial metric
vocabulary that ``fin.raw.*``/``fin.norm.*`` Fact slugs (Slice B, not yet
built) will be drawn from.

A seed/lookup table, same idiom as ``ResearchDimension``/
``PropositionStageType``: new metrics are a data insert, never a migration.
``slug`` must name a canonical, accounting-standard-neutral economic
concept (e.g. ``operating_revenue``, not an Ind-AS-Schedule-III-specific
line-item name) -- standard-specific mapping is a future source-adapter's
job, not this table's.
"""

from __future__ import annotations

import sqlalchemy as sa
import sqlalchemy.orm as so

from app.models.base import BaseModel
from app.models.research_types import FinancialMetricNamespace, enum_type


class FinancialMetricDefinition(BaseModel):
    """One controlled financial metric slug."""

    __tablename__ = "financial_metric_definition"

    slug: so.Mapped[str] = so.mapped_column(sa.String(100), nullable=False)
    label: so.Mapped[str] = so.mapped_column(sa.String(200), nullable=False)
    statement_section: so.Mapped[str] = so.mapped_column(
        sa.String(100), nullable=False
    )
    namespace: so.Mapped[str] = so.mapped_column(
        enum_type(
            "financial_metric_namespace",
            (
                FinancialMetricNamespace.RAW,
                FinancialMetricNamespace.NORMALIZED,
            ),
        ),
        nullable=False,
    )
    standard_unit: so.Mapped[str] = so.mapped_column(
        sa.String(50), nullable=False
    )
    description: so.Mapped[str | None] = so.mapped_column(
        sa.String(2000), nullable=True
    )

    __table_args__ = (
        sa.UniqueConstraint(
            "slug", name="uq_financial_metric_definition_slug"
        ),
    )

    def __repr__(self) -> str:
        return f"<FinancialMetricDefinition {self.slug}>"
