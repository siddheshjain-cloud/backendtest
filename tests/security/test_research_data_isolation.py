"""Plan 3 Task 6: release-blocking free/premium/admin security matrix.

These tests integrate every Plan 3 interface: database-backed entitlement
resolution, access policy, query composition, read-only market projection,
explicit response schemas, and collection/history authorization. Each matrix
case is projected through the query service and presenter, then recursively
inspected against an explicit allowed-section set. A single shared fixture in
``tests/conftest.py`` keeps the same matrix available for Plan 5 API tests.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from flask_jwt_extended import create_access_token, decode_token

from app import db
from app.models.entitlement import (
    INVESTMENT_RESEARCH_PRODUCT_CODE,
    UserEntitlement,
)
from app.models.research_types import EntitlementStatus, ResearchTier
from app.models.user import User
from app.services.entitlement_service import EntitlementService
from app.services.research_presenter import ResearchPresenter
from app.services.research_query_service import ResearchQueryService
from app.utils.research_errors import ResearchForbiddenError


SCENARIO_IDS = [
    "missing",
    "inactive",
    "revoked",
    "future",
    "expired",
    "active_free",
    "active_premium",
    "admin_without_row",
    "admin_with_stale_tier_claim",
]

FREE_DETAIL_SECTIONS = frozenset(
    {"company", "business_group", "market_quote", "access"}
)

PREMIUM_DETAIL_SECTIONS = FREE_DETAIL_SECTIONS | frozenset(
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

PREMIUM_ONLY_DETAIL_SECTIONS = PREMIUM_DETAIL_SECTIONS - FREE_DETAIL_SECTIONS

PROTECTED_OUTPUT_KEYS = frozenset(
    {
        "why_selected",
        "what_is_changing",
        "business_journey",
        "thesis",
        "thesis_invalidation",
        "management_summary",
        "management_quality",
        "management_rationale",
        "management_evidence",
        "factual_evidence",
        "source_url_or_reference",
        "interpretation",
        "promoter_holding_pct",
        "promoter_pledge_pct",
        "accumulation_low",
        "accumulation_high",
        "invalidation_level",
        "revenue",
        "ebitda",
        "pat",
        "ebitda_margin_pct",
        "eps",
        "justified_multiple",
        "net_debt",
        "present_value",
        "current_market_cap",
        "valuation_notes",
        "reference_metric_value",
        "reference_metric_basis",
        "significance_note",
        "total_items",
    }
)


def _value_text(value: object) -> str:
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _walk(value: object, path: str = "$"):
    yield path, value
    if isinstance(value, dict):
        for key in sorted(value):
            yield from _walk(value[key], f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            yield from _walk(child, f"{path}[{index}]")


def _projection_text(projection: dict[str, object]) -> str:
    return json.dumps(
        projection,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _assert_free_projection_safe(
    aggregate: dict[str, object],
    projection: dict[str, object],
    premium_markers: list[str],
) -> None:
    assert PREMIUM_ONLY_DETAIL_SECTIONS.isdisjoint(aggregate)
    assert set(projection) == FREE_DETAIL_SECTIONS

    for path, value in _walk(projection):
        if isinstance(value, dict):
            for key in value:
                assert key not in PROTECTED_OUTPUT_KEYS, (
                    f"unexpected protected key at {path}.{key}"
                )
        else:
            text = _value_text(value)
            for marker in premium_markers:
                assert marker not in text, (
                    f"premium sentinel {marker!r} leaked at {path}"
                )


@pytest.mark.parametrize("scenario_id", SCENARIO_IDS)
def test_matrix_projections_are_entitlement_safe(
    scenario_id: str, research_security_matrix
):
    matrix = research_security_matrix
    case = matrix["scenarios"][scenario_id]
    company_id = matrix["company_id"]

    aggregate = ResearchQueryService.get_company_detail(
        company_id, case["context"]
    )
    projection = ResearchPresenter.company_detail(
        aggregate, case["context"]
    )

    assert projection["access"]["tier"] == case["access_tier"]
    assert (
        projection["access"]["locked_sections"]
        == case["locked_sections"]
    )

    if case["access_tier"] == ResearchTier.FREE:
        _assert_free_projection_safe(
            aggregate,
            projection,
            matrix["premium_markers"],
        )
    else:
        assert PREMIUM_DETAIL_SECTIONS.issubset(projection)
        assert projection["access"]["locked_sections"] == []
        projection_text = _projection_text(projection)
        for marker in matrix["premium_markers"]:
            assert marker in projection_text, (
                f"{scenario_id} projection is missing {marker!r}"
            )


@pytest.mark.parametrize("scenario_id", SCENARIO_IDS)
def test_matrix_disclosures_and_history_respect_tier(
    scenario_id: str, research_security_matrix
):
    matrix = research_security_matrix
    case = matrix["scenarios"][scenario_id]
    company_id = matrix["company_id"]

    disclosures = ResearchQueryService.list_disclosures(
        company_id,
        event_type=None,
        is_key=None,
        date_from=None,
        date_to=None,
        newest_first=True,
        page=1,
        per_page=10,
        context=case["context"],
    )

    if case["access_tier"] == ResearchTier.FREE:
        assert all(
            "significance_note" not in item for item in disclosures.items
        )
        with pytest.raises(ResearchForbiddenError) as exc_info:
            ResearchQueryService.get_history(
                company_id,
                section="research",
                valuation_method=None,
                page=1,
                per_page=10,
                context=case["context"],
            )
        assert exc_info.value.code == "history_forbidden"
        return

    assert all(
        item["significance_note"]
        == matrix["disclosure_significance_marker"]
        for item in disclosures.items
    )

    history = ResearchQueryService.get_history(
        company_id,
        section="research",
        valuation_method=None,
        page=1,
        per_page=10,
        context=case["context"],
    )
    assert history.total_items == 1
    assert history.items[0]["thesis"] == matrix["research_thesis_marker"]


def test_entitlement_changes_affect_next_call_without_new_jwt(
    app, research_security_matrix
):
    matrix = research_security_matrix
    company_id = matrix["company_id"]

    user = User(
        name="mutable",
        email="mutable@example.com",
        is_admin=False,
    )
    user.set_password("test-password")
    db.session.add(user)
    db.session.commit()

    token = create_access_token(
        identity=user.id,
        additional_claims={"tier": ResearchTier.PREMIUM},
    )
    db.session.add(
        UserEntitlement(
            user_id=user.id,
            product_code=INVESTMENT_RESEARCH_PRODUCT_CODE,
            tier=ResearchTier.FREE,
            status=EntitlementStatus.ACTIVE,
            valid_from=datetime(2026, 1, 1, tzinfo=timezone.utc),
            valid_until=datetime(2026, 12, 31, tzinfo=timezone.utc),
        )
    )
    db.session.commit()

    free_context = EntitlementService.resolve(
        user, at=matrix["now"]
    )
    free_projection = ResearchPresenter.company_detail(
        ResearchQueryService.get_company_detail(
            company_id, free_context
        ),
        free_context,
    )
    assert free_context.tier == ResearchTier.FREE
    assert set(free_projection) == FREE_DETAIL_SECTIONS
    assert decode_token(token)["tier"] == ResearchTier.PREMIUM

    row = UserEntitlement.query.filter_by(
        user_id=user.id,
        product_code=INVESTMENT_RESEARCH_PRODUCT_CODE,
    ).one()
    row.tier = ResearchTier.PREMIUM
    db.session.commit()

    premium_context = EntitlementService.resolve(
        user, at=matrix["now"]
    )
    premium_projection = ResearchPresenter.company_detail(
        ResearchQueryService.get_company_detail(
            company_id, premium_context
        ),
        premium_context,
    )
    assert premium_context.tier == ResearchTier.PREMIUM
    assert PREMIUM_DETAIL_SECTIONS.issubset(premium_projection)
    assert decode_token(token)["tier"] == ResearchTier.PREMIUM

    row.status = EntitlementStatus.REVOKED
    db.session.commit()

    revoked_context = EntitlementService.resolve(
        user, at=matrix["now"]
    )
    revoked_projection = ResearchPresenter.company_detail(
        ResearchQueryService.get_company_detail(
            company_id, revoked_context
        ),
        revoked_context,
    )
    assert revoked_context.tier == ResearchTier.FREE
    assert set(revoked_projection) == FREE_DETAIL_SECTIONS
    assert decode_token(token)["tier"] == ResearchTier.PREMIUM
