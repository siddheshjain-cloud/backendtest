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
        "investment_case",
    }
)

# Sections ever shown to an unauthenticated (PUBLIC-tier) reader. Empty by
# default -- a section becomes public only via a separate, later, explicit
# decision to move it here, never by inheriting FREE_SECTIONS automatically.
# Kept as its own named constant, the same convention FREE_SECTIONS and
# PREMIUM_SECTIONS already use, rather than a derived subset.
PUBLIC_SECTIONS = frozenset()

ALL_RESEARCH_SECTIONS = FREE_SECTIONS | PREMIUM_SECTIONS


class ResearchAccessPolicy:
    """Map research access contexts to explicit entitlement-safe sections."""

    @staticmethod
    def allowed_sections(
        context: ResearchAccessContext,
    ) -> frozenset[str]:
        if context.is_admin or context.tier == ResearchTier.PREMIUM:
            return ALL_RESEARCH_SECTIONS
        if context.tier == ResearchTier.PUBLIC:
            return PUBLIC_SECTIONS
        return FREE_SECTIONS

    @staticmethod
    def locked_sections(
        context: ResearchAccessContext,
    ) -> list[dict[str, str]]:
        if context.is_admin or context.tier == ResearchTier.PREMIUM:
            return []
        if context.tier == ResearchTier.PUBLIC:
            locked = [
                {"section": section, "required_tier": ResearchTier.FREE}
                for section in sorted(FREE_SECTIONS - PUBLIC_SECTIONS)
            ]
            locked.extend(
                {"section": section, "required_tier": ResearchTier.PREMIUM}
                for section in sorted(PREMIUM_SECTIONS)
            )
            return locked
        return [
            {"section": section, "required_tier": ResearchTier.PREMIUM}
            for section in sorted(PREMIUM_SECTIONS)
        ]
