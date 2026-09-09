"""Plan 3 Task 1: research access policy section matrix tests."""

from app.models.research_types import ResearchTier
from app.policies.research_access import ResearchAccessPolicy
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

ALL_SECTIONS = FREE_SECTIONS | PREMIUM_SECTIONS


def _free_context() -> ResearchAccessContext:
    return ResearchAccessContext("user-1", False, ResearchTier.FREE)


def _premium_context() -> ResearchAccessContext:
    return ResearchAccessContext("user-2", False, ResearchTier.PREMIUM)


def _admin_context(tier: str = ResearchTier.FREE) -> ResearchAccessContext:
    return ResearchAccessContext("admin-1", True, tier)


def test_free_allowed_sections_are_exactly_public_sections():
    assert (
        ResearchAccessPolicy.allowed_sections(_free_context())
        == FREE_SECTIONS
    )


def test_premium_allowed_sections_add_every_premium_section():
    assert (
        ResearchAccessPolicy.allowed_sections(_premium_context())
        == ALL_SECTIONS
    )


def test_admin_without_premium_tier_still_receives_all_research_sections():
    assert (
        ResearchAccessPolicy.allowed_sections(_admin_context())
        == ALL_SECTIONS
    )


def test_admin_with_premium_tier_receives_all_research_sections():
    assert (
        ResearchAccessPolicy.allowed_sections(
            _admin_context(tier=ResearchTier.PREMIUM)
        )
        == ALL_SECTIONS
    )


def test_unknown_tier_without_admin_fails_closed_to_free_sections():
    context = ResearchAccessContext("user-3", False, "GOLD")

    assert ResearchAccessPolicy.allowed_sections(context) == FREE_SECTIONS


def test_free_locked_sections_are_deterministic_and_reveal_only_metadata():
    locked = ResearchAccessPolicy.locked_sections(_free_context())

    assert locked == [
        {"section": section, "required_tier": ResearchTier.PREMIUM}
        for section in sorted(PREMIUM_SECTIONS)
    ]
    assert len(locked) == len(PREMIUM_SECTIONS)
    assert all(set(item) == {"section", "required_tier"} for item in locked)


def test_premium_and_admin_contexts_have_no_locked_sections():
    assert ResearchAccessPolicy.locked_sections(_premium_context()) == []
    assert ResearchAccessPolicy.locked_sections(_admin_context()) == []
