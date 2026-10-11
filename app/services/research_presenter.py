"""Plan 3 Task 2: entitlement-safe company detail presenter."""

from __future__ import annotations

from marshmallow import Schema

from app.models.research_types import ResearchTier
from app.policies.research_access import ResearchAccessPolicy
from app.schemas.research import (
    CompanyAdminSchema,
    CompanyFreeSchema,
    CompanyPremiumSchema,
)
from app.services.entitlement_service import ResearchAccessContext


_PREMIUM_DETAIL_SECTIONS = frozenset(
    {
        "research",
        "management",
        "governance",
        "ownership",
        "market_plan",
        "forecast",
        "valuations",
    }
)


def _schema_type(
    context: ResearchAccessContext,
) -> type[Schema]:
    """Choose exactly one explicit projection schema from the context."""

    if context.is_admin:
        return CompanyAdminSchema
    if context.tier == ResearchTier.PREMIUM:
        return CompanyPremiumSchema
    return CompanyFreeSchema


def _access_tier(context: ResearchAccessContext) -> str:
    if context.is_admin:
        return "ADMIN"
    if context.tier == ResearchTier.PREMIUM:
        return ResearchTier.PREMIUM
    return ResearchTier.FREE


def _access_payload(context: ResearchAccessContext) -> dict[str, object]:
    return {
        "tier": _access_tier(context),
        "locked_sections": ResearchAccessPolicy.locked_sections(context),
    }


class ResearchPresenter:
    """Serialize an already-loaded company aggregate through explicit schemas."""

    @staticmethod
    def company_detail(
        aggregate: dict[str, object],
        context: ResearchAccessContext,
    ) -> dict[str, object]:
        """Project one company detail without inspecting JWT or querying DB."""

        payload: dict[str, object] = {
            "company": aggregate["company"],
            "market_quote": aggregate["market_quote"],
        }

        if aggregate.get("business_group") is not None:
            payload["business_group"] = aggregate["business_group"]

        # The chosen explicit schema gates premium sections; keep section keys
        # only when the query service supplied them so null-versus-omission
        # semantics remain stable for authorized callers.
        for section in _PREMIUM_DETAIL_SECTIONS:
            if section in aggregate:
                payload[section] = aggregate[section]

        payload["access"] = _access_payload(context)
        return _schema_type(context)().dump(payload)
