"""Plan 3 Task 1: research section access policy."""

from __future__ import annotations

from app.models.research_types import ResearchTier
from app.services.entitlement_service import ResearchAccessContext


FREE_SECTIONS = frozenset(
    {
        "company",
        "business_group",
        "market_quote",
        "public_documents",
        "public_disclosures",
    }
)

PREMIUM_SECTIONS = frozenset(
    {
        "ownership",
        "management",
        "research",
        "governance",
        "disclosure_significance",
        "market_plan",
        "forecast",
        "valuation",
        "history",
    }
)

ALL_RESEARCH_SECTIONS = FREE_SECTIONS | PREMIUM_SECTIONS


class ResearchAccessPolicy:
    """Map research access contexts to explicit entitlement-safe sections."""

    @staticmethod
    def allowed_sections(
        context: ResearchAccessContext,
    ) -> frozenset[str]:
        if context.is_admin or context.tier == ResearchTier.PREMIUM:
            return ALL_RESEARCH_SECTIONS
        return FREE_SECTIONS

    @staticmethod
    def locked_sections(
        context: ResearchAccessContext,
    ) -> list[dict[str, str]]:
        if context.is_admin or context.tier == ResearchTier.PREMIUM:
            return []
        return [
            {"section": section, "required_tier": ResearchTier.PREMIUM}
            for section in sorted(PREMIUM_SECTIONS)
        ]
