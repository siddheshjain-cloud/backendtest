"""Plan 3 Task 2: explicit projections and leakage-safe presenter tests.

These tests lock the frozen response schemas and the pure presenter
contract: free projections contain no premium sentinel strings, numbers,
keys, snippets, counts, or aggregates; locked metadata is limited to the
deterministic section/required-tier pair; premium and administrative
projections include authorized unknown values as null and serialize known
fixed-precision values as decimal strings.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from decimal import Decimal

from marshmallow import Schema

from app.models.research_types import ResearchTier
from app.policies.research_access import ResearchAccessPolicy
from app.schemas.research import (
    CompanyAdminSchema,
    CompanyFreeSchema,
    CompanyPremiumSchema,
)
from app.services.entitlement_service import ResearchAccessContext
from app.services.research_presenter import ResearchPresenter


FREE_CONTEXT = ResearchAccessContext(
    "user-free", False, ResearchTier.FREE
)
PREMIUM_CONTEXT = ResearchAccessContext(
    "user-premium", False, ResearchTier.PREMIUM
)
ADMIN_CONTEXT = ResearchAccessContext(
    "user-admin", True, ResearchTier.FREE
)

PREMIUM_DETAIL_SECTIONS = frozenset(
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

PROTECTED_OUTPUT_KEYS = frozenset(
    {
        "thesis",
        "management_rationale",
        "factual_evidence",
        "promoter_holding_pct",
        "accumulation_low",
        "revenue",
        "valuation_method",
        "significance_note",
        "total_items",
    }
)

_UTC = timezone.utc


def _free_context() -> ResearchAccessContext:
    return FREE_CONTEXT


def _premium_context() -> ResearchAccessContext:
    return PREMIUM_CONTEXT


def _admin_context() -> ResearchAccessContext:
    return ADMIN_CONTEXT


def _scalar_text(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _scalars(value: object):
    if isinstance(value, dict):
        for child in value.values():
            yield from _scalars(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            yield from _scalars(child)
    else:
        yield value


def _company() -> dict[str, object]:
    return {
        "id": "company-1",
        "name": "IKIO Lighting Limited",
        "symbol": "IKIO",
        "exchange": "NSE",
        "isin": "INE0LOJ01019",
        "sector": "Industrials",
        "industry": "LED lighting",
    }


def _business_group() -> dict[str, object]:
    return {
        "id": "group-1",
        "name": "IKIO Promoter Group",
        "notes": "SENTINEL-BUSINESS-GROUP-NOTES",
        "source_reference": "https://example.in/group/source",
    }


def _market_quote() -> dict[str, object]:
    return {
        "cmp": 123.45,
        "as_of": datetime(2026, 9, 4, 9, 30, tzinfo=_UTC),
        "stale": False,
    }


def _full_aggregate() -> dict[str, object]:
    return {
        "company": _company(),
        "business_group": _business_group(),
        "market_quote": _market_quote(),
        "research": {
            "revision": 3,
            "why_selected": "Durable LED demand tailwind",
            "what_is_changing": None,
            "business_journey": "SENTINEL-RESEARCH-JOURNEY",
            "thesis": "Operating leverage compounds",
            "catalysts": [
                {
                    "id": "point-1",
                    "kind": "CATALYST",
                    "title": "SENTINEL-RESEARCH-CATALYST",
                    "detail": "Capacity commissioning",
                    "status": "OPEN",
                    "target_date": date(2027, 3, 31),
                    "sort_order": 0,
                }
            ],
            "risks": [
                {
                    "id": "point-2",
                    "kind": "RISK",
                    "title": "Customer concentration",
                    "detail": None,
                    "status": None,
                    "target_date": None,
                    "sort_order": 0,
                }
            ],
            "thesis_invalidation": "Margin erosion or concentration",
        },
        "management": {
            "summary": None,
            "quality": "WATCH",
            "rationale": "SENTINEL-MANAGEMENT-RATIONALE",
            "evidence": "SENTINEL-MANAGEMENT-EVIDENCE",
        },
        "governance": {
            "status": "CLEAR",
            "flags": [
                {
                    "id": "flag-1",
                    "flag_type": "RELATED_PARTY",
                    "title": "SENTINEL-GOVERNANCE-TITLE",
                    "severity": "HIGH",
                    "status": "OPEN",
                    "factual_evidence": "SENTINEL-GOVERNANCE-EVIDENCE",
                    "source_title": "SENTINEL-GOVERNANCE-SOURCE",
                    "source_url_or_reference": (
                        "https://example.in/governance/source"
                    ),
                    "interpretation": "SENTINEL-GOVERNANCE-INTERPRETATION",
                    "observed_on": date(2026, 8, 1),
                    "resolved_on": None,
                }
            ],
        },
        "ownership": {
            "as_of_date": date(2026, 8, 31),
            "promoter_holding_pct": Decimal("64.5000"),
            "promoter_pledge_pct": None,
            "notes": "SENTINEL-OWNERSHIP-NOTES",
            "source_reference": "https://example.in/ownership/source",
        },
        "market_plan": {
            "revision": 2,
            "currency": "INR",
            "accumulation_low": Decimal("100.0000"),
            "accumulation_high": Decimal("130.0000"),
            "preferred_accumulation_price": None,
            "supply_low": None,
            "supply_high": None,
            "invalidation_level": Decimal("90.0000"),
            "rationale": "SENTINEL-MARKET-PLAN-RATIONALE",
            "effective_at": datetime(2026, 9, 1, 12, 0, tzinfo=_UTC),
        },
        "forecast": {
            "revision": 1,
            "as_of_date": date(2026, 9, 1),
            "assumptions": "SENTINEL-FORECAST-ASSUMPTIONS",
            "lines": [
                {
                    "fiscal_year": 2027,
                    "is_estimate": True,
                    "revenue": Decimal("1234.5000"),
                    "ebitda": Decimal("250.7500"),
                    "pat": None,
                    "ebitda_margin_pct": Decimal("20.3100"),
                    "eps": None,
                    "currency": "INR",
                    "unit": "CRORE",
                }
            ],
        },
        "valuations": [
            {
                "revision": 1,
                "valuation_method": "PE",
                "justified_multiple": Decimal("12.5000"),
                "implied_enterprise_value": None,
                "net_debt": Decimal("998877.5000"),
                "other_equity_adjustment": None,
                "implied_future_equity_value": None,
                "required_return_pct": None,
                "discount_period_years": None,
                "present_value": None,
                "current_market_cap": None,
                "currency": "INR",
                "unit": "CRORE",
                "valuation_notes": "SENTINEL-VALUATION-NOTES",
                "as_of_date": date(2026, 9, 1),
                "reference_lines": [
                    {
                        "reference_forecast_revision_id": "forecast-1",
                        "reference_fiscal_year": 2027,
                        "reference_metric": "EPS",
                        "reference_metric_value": Decimal("12.0000"),
                        "reference_metric_unit": "INR_PER_SHARE",
                        "reference_metric_basis": (
                            "SENTINEL-VALUATION-REFERENCE-BASIS"
                        ),
                        "sort_order": 0,
                    }
                ],
            }
        ],
        # Sections that use dedicated projections outside company detail and
        # must never leak from the aggregate into a company projection.
        "disclosure_significance": {
            "significance_note": "SENTINEL-SIGNIFICANCE-NOTE"
        },
        "history": {
            "section": "research",
            "items": [{"thesis": "SENTINEL-HISTORY-THESIS"}],
            "page": 1,
            "per_page": 5,
            "total_items": 42,
            "total_pages": 9,
        },
        "access_input": {
            "user_id": "user-1",
            "is_admin": False,
            "tier": ResearchTier.FREE,
        },
    }


def _unknown_aggregate() -> dict[str, object]:
    """Every authorized research section exists but has no known value."""

    return {
        "company": _company(),
        "business_group": None,
        "market_quote": {"cmp": None, "as_of": None},
        "research": None,
        "management": None,
        "governance": None,
        "ownership": None,
        "market_plan": None,
        "forecast": None,
        "valuations": None,
    }


def _sentinel_markers() -> list[str]:
    return [
        "SENTINEL-BUSINESS-GROUP-NOTES",
        "SENTINEL-RESEARCH-JOURNEY",
        "SENTINEL-RESEARCH-CATALYST",
        "SENTINEL-MANAGEMENT-RATIONALE",
        "SENTINEL-MANAGEMENT-EVIDENCE",
        "SENTINEL-GOVERNANCE-TITLE",
        "SENTINEL-GOVERNANCE-EVIDENCE",
        "SENTINEL-GOVERNANCE-SOURCE",
        "SENTINEL-GOVERNANCE-INTERPRETATION",
        "SENTINEL-OWNERSHIP-NOTES",
        "SENTINEL-MARKET-PLAN-RATIONALE",
        "SENTINEL-FORECAST-ASSUMPTIONS",
        "SENTINEL-VALUATION-NOTES",
        "SENTINEL-VALUATION-REFERENCE-BASIS",
        "SENTINEL-SIGNIFICANCE-NOTE",
        "SENTINEL-HISTORY-THESIS",
        "998877.5000",
    ]


def test_explicit_projection_schemas_are_exported():
    for schema_type in (
        CompanyFreeSchema,
        CompanyPremiumSchema,
        CompanyAdminSchema,
    ):
        assert issubclass(schema_type, Schema)


def test_free_projection_is_leakage_safe_across_every_premium_section():
    projection = ResearchPresenter.company_detail(
        _full_aggregate(), _free_context()
    )

    assert {
        "company",
        "business_group",
        "market_quote",
        "access",
    }.issubset(projection)
    assert PREMIUM_DETAIL_SECTIONS.isdisjoint(projection)

    text = _scalar_text(projection)
    for marker in _sentinel_markers():
        assert marker not in text
    assert all(key not in text for key in PROTECTED_OUTPUT_KEYS)

    for scalar in _scalars(projection):
        if isinstance(scalar, str):
            for marker in _sentinel_markers():
                assert marker not in scalar


def test_free_locked_metadata_reveals_only_deterministic_section_names():
    projection = ResearchPresenter.company_detail(
        _full_aggregate(), _free_context()
    )

    access = projection["access"]
    assert access["tier"] == ResearchTier.FREE
    assert access["locked_sections"] == ResearchAccessPolicy.locked_sections(
        _free_context()
    )
    assert all(
        set(item) == {"section", "required_tier"}
        for item in access["locked_sections"]
    )
    locked_text = _scalar_text(access["locked_sections"])
    for marker in _sentinel_markers():
        assert marker not in locked_text


def test_premium_projection_includes_sections_and_decimal_strings():
    projection = ResearchPresenter.company_detail(
        _full_aggregate(), _premium_context()
    )

    assert PREMIUM_DETAIL_SECTIONS.issubset(projection)
    assert projection["access"]["tier"] == ResearchTier.PREMIUM
    assert projection["access"]["locked_sections"] == []

    assert projection["ownership"]["promoter_holding_pct"] == "64.5000"
    assert projection["ownership"]["promoter_pledge_pct"] is None
    assert projection["market_plan"]["accumulation_low"] == "100.0000"
    assert projection["market_plan"]["preferred_accumulation_price"] is None
    assert (
        projection["forecast"]["lines"][0]["revenue"] == "1234.5000"
    )
    assert (
        projection["valuations"][0]["justified_multiple"] == "12.5000"
    )
    assert projection["research"]["what_is_changing"] is None
    assert projection["management"]["summary"] is None
    assert projection["governance"]["flags"][0]["resolved_on"] is None


def test_admin_projection_is_complete_without_a_premium_tier_row():
    projection = ResearchPresenter.company_detail(
        _full_aggregate(), _admin_context()
    )

    assert PREMIUM_DETAIL_SECTIONS.issubset(projection)
    assert projection["access"]["tier"] == "ADMIN"
    assert projection["access"]["locked_sections"] == []
    assert projection["ownership"]["promoter_holding_pct"] == "64.5000"
    assert projection["valuations"][0]["net_debt"] == "998877.5000"


def test_authorized_unknown_fields_are_present_as_null_for_premium_and_admin():
    premium = ResearchPresenter.company_detail(
        _unknown_aggregate(), _premium_context()
    )
    admin = ResearchPresenter.company_detail(
        _unknown_aggregate(), _admin_context()
    )

    for projection in (premium, admin):
        for section in PREMIUM_DETAIL_SECTIONS:
            assert projection[section] is None
        assert projection["market_quote"]["cmp"] is None
        assert projection["market_quote"]["as_of"] is None
        assert "business_group" not in projection
