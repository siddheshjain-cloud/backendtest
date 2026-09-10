"""Plan 3 Task 6: release-blocking free/premium/admin security matrix.

Independent-oracle rules enforced by this module:

* Every scenario's expected context tier, access tier, admin flag, projection
  kind, and locked-section metadata is literal data owned by the tests
  (``tests/conftest.py`` scenario specs). Nothing asserted here is derived
  from ``EntitlementService.resolve``, ``ResearchAccessPolicy``, or any other
  production function under test.
* Section contracts are frozen as literal test-owned sets for FREE, PREMIUM,
  and administrator projections and compared with equality, never subset
  inclusion.
* Every projection is checked against a frozen structural allowlist: exact
  top-level keys and exact nested key sets. A newly added count, range,
  existence flag, notes/source-reference field, renamed field, or
  premium-only field therefore fails the FREE contract unless it is
  deliberately added to the allowlists below.
* Disclosure and history assertions prove the collection is non-empty before
  any ``all(...)``-style check, so an accidentally empty response cannot
  satisfy the security test.
* Protected sentinel values, including catalyst/detail-like content, are
  scanned wherever they appear in a free projection or free collection item,
  so relocating protected content into an existing free-visible structure is
  detected.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
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

# The scenario whose literal expectation is FREE while its token claims
# PREMIUM plus admin.
STALE_JWT_SCENARIO_ID = "missing"

# --------------------------------------------------------------------------
# Frozen section contracts (literal, test-owned, never built from production).
# --------------------------------------------------------------------------

FREE_SECTION_KEYS = frozenset(
    {"company", "business_group", "market_quote", "access"}
)

PREMIUM_SECTION_KEYS = frozenset(
    {
        "company",
        "business_group",
        "market_quote",
        "access",
        "research",
        "management",
        "governance",
        "ownership",
        "market_plan",
        "forecast",
        "valuations",
    }
)

ADMIN_SECTION_KEYS = frozenset(
    {
        "company",
        "business_group",
        "market_quote",
        "access",
        "research",
        "management",
        "governance",
        "ownership",
        "market_plan",
        "forecast",
        "valuations",
    }
)

SECTION_KEYS_BY_PROJECTION = {
    "free": FREE_SECTION_KEYS,
    "premium": PREMIUM_SECTION_KEYS,
    "admin": ADMIN_SECTION_KEYS,
}

# --------------------------------------------------------------------------
# Frozen nested key sets (literal, test-owned).
# --------------------------------------------------------------------------

COMPANY_KEYS = frozenset(
    {"id", "name", "symbol", "exchange", "isin", "sector", "industry"}
)
BUSINESS_GROUP_KEYS = frozenset({"name"})
MARKET_QUOTE_KEYS = frozenset({"cmp", "as_of"})
ACCESS_KEYS = frozenset({"tier", "locked_sections"})
LOCKED_SECTION_KEYS = frozenset({"section", "required_tier"})

RESEARCH_KEYS = frozenset(
    {
        "revision",
        "why_selected",
        "what_is_changing",
        "business_journey",
        "thesis",
        "catalysts",
        "risks",
        "thesis_invalidation",
    }
)
RESEARCH_POINT_KEYS = frozenset(
    {
        "id",
        "kind",
        "title",
        "detail",
        "status",
        "target_date",
        "sort_order",
    }
)
MANAGEMENT_KEYS = frozenset({"summary", "quality", "rationale", "evidence"})
GOVERNANCE_KEYS = frozenset({"status", "flags"})
GOVERNANCE_FLAG_KEYS = frozenset(
    {
        "id",
        "flag_type",
        "title",
        "severity",
        "status",
        "factual_evidence",
        "source_title",
        "source_url_or_reference",
        "interpretation",
        "observed_on",
        "resolved_on",
    }
)
OWNERSHIP_KEYS = frozenset(
    {
        "as_of_date",
        "promoter_holding_pct",
        "promoter_pledge_pct",
        "notes",
        "source_reference",
    }
)
MARKET_PLAN_KEYS = frozenset(
    {
        "revision",
        "currency",
        "accumulation_low",
        "accumulation_high",
        "preferred_accumulation_price",
        "supply_low",
        "supply_high",
        "invalidation_level",
        "rationale",
        "effective_at",
    }
)
FORECAST_KEYS = frozenset(
    {"revision", "as_of_date", "assumptions", "lines"}
)
FORECAST_LINE_KEYS = frozenset(
    {
        "fiscal_year",
        "is_estimate",
        "revenue",
        "ebitda",
        "pat",
        "ebitda_margin_pct",
        "eps",
        "currency",
        "unit",
    }
)
VALUATION_KEYS = frozenset(
    {
        "revision",
        "valuation_method",
        "justified_multiple",
        "implied_enterprise_value",
        "net_debt",
        "other_equity_adjustment",
        "implied_future_equity_value",
        "required_return_pct",
        "discount_period_years",
        "present_value",
        "current_market_cap",
        "currency",
        "unit",
        "valuation_notes",
        "as_of_date",
        "reference_lines",
    }
)
VALUATION_REFERENCE_LINE_KEYS = frozenset(
    {
        "reference_forecast_revision_id",
        "reference_fiscal_year",
        "reference_metric",
        "reference_metric_value",
        "reference_metric_unit",
        "reference_metric_basis",
        "sort_order",
    }
)

FREE_DISCLOSURE_ITEM_KEYS = frozenset(
    {
        "id",
        "company_id",
        "event_type",
        "event_date",
        "title",
        "original_source_url_or_reference",
        "exchange_reference",
        "is_key",
        "document_id",
    }
)
PREMIUM_DISCLOSURE_ITEM_KEYS = frozenset(
    {
        "id",
        "company_id",
        "event_type",
        "event_date",
        "title",
        "original_source_url_or_reference",
        "exchange_reference",
        "is_key",
        "document_id",
        "significance_note",
    }
)

RESEARCH_HISTORY_ITEM_KEYS = frozenset(
    {
        "id",
        "revision",
        "created_at",
        "effective_at",
        "change_reason",
        "why_selected",
        "what_is_changing",
        "business_journey",
        "thesis",
        "thesis_invalidation",
        "governance_status",
        "catalysts",
        "risks",
    }
)

# Secondary defence-in-depth blacklist. The structural allowlists above are the
# primary leak detection; these names must never appear as keys in a free
# payload even if someone widens a free schema.
PROTECTED_FIELD_NAMES = frozenset(
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


@dataclass(frozen=True)
class ObjectShape:
    """Exact key set for one object, with optional nested child shapes."""

    keys: frozenset[str]
    children: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class ListShape:
    """Repeated child shape; can require the collection to be non-empty."""

    item: object
    require_non_empty: bool = False


COMPANY_SHAPE = ObjectShape(COMPANY_KEYS)
BUSINESS_GROUP_SHAPE = ObjectShape(BUSINESS_GROUP_KEYS)
MARKET_QUOTE_SHAPE = ObjectShape(MARKET_QUOTE_KEYS)
LOCKED_SECTION_SHAPE = ObjectShape(LOCKED_SECTION_KEYS)
ACCESS_SHAPE = ObjectShape(
    ACCESS_KEYS,
    {"locked_sections": ListShape(LOCKED_SECTION_SHAPE)},
)

RESEARCH_POINT_SHAPE = ObjectShape(RESEARCH_POINT_KEYS)
RESEARCH_SHAPE = ObjectShape(
    RESEARCH_KEYS,
    {
        "catalysts": ListShape(RESEARCH_POINT_SHAPE, require_non_empty=True),
        "risks": ListShape(RESEARCH_POINT_SHAPE, require_non_empty=True),
    },
)
RESEARCH_HISTORY_ITEM_SHAPE = ObjectShape(
    RESEARCH_HISTORY_ITEM_KEYS,
    {
        "catalysts": ListShape(RESEARCH_POINT_SHAPE, require_non_empty=True),
        "risks": ListShape(RESEARCH_POINT_SHAPE, require_non_empty=True),
    },
)
MANAGEMENT_SHAPE = ObjectShape(MANAGEMENT_KEYS)
GOVERNANCE_FLAG_SHAPE = ObjectShape(GOVERNANCE_FLAG_KEYS)
GOVERNANCE_SHAPE = ObjectShape(
    GOVERNANCE_KEYS,
    {"flags": ListShape(GOVERNANCE_FLAG_SHAPE, require_non_empty=True)},
)
OWNERSHIP_SHAPE = ObjectShape(OWNERSHIP_KEYS)
MARKET_PLAN_SHAPE = ObjectShape(MARKET_PLAN_KEYS)
FORECAST_LINE_SHAPE = ObjectShape(FORECAST_LINE_KEYS)
FORECAST_SHAPE = ObjectShape(
    FORECAST_KEYS,
    {"lines": ListShape(FORECAST_LINE_SHAPE, require_non_empty=True)},
)
VALUATION_REFERENCE_LINE_SHAPE = ObjectShape(VALUATION_REFERENCE_LINE_KEYS)
VALUATION_SHAPE = ObjectShape(
    VALUATION_KEYS,
    {
        "reference_lines": ListShape(
            VALUATION_REFERENCE_LINE_SHAPE, require_non_empty=True
        )
    },
)
VALUATIONS_SHAPE = ListShape(VALUATION_SHAPE, require_non_empty=True)

_SHARED_CHILD_SHAPES: dict[str, object] = {
    "company": COMPANY_SHAPE,
    "business_group": BUSINESS_GROUP_SHAPE,
    "market_quote": MARKET_QUOTE_SHAPE,
    "access": ACCESS_SHAPE,
}
_PREMIUM_CHILD_SHAPES: dict[str, object] = {
    **_SHARED_CHILD_SHAPES,
    "research": RESEARCH_SHAPE,
    "management": MANAGEMENT_SHAPE,
    "governance": GOVERNANCE_SHAPE,
    "ownership": OWNERSHIP_SHAPE,
    "market_plan": MARKET_PLAN_SHAPE,
    "forecast": FORECAST_SHAPE,
    "valuations": VALUATIONS_SHAPE,
}

FREE_DETAIL_SHAPE = ObjectShape(FREE_SECTION_KEYS, _SHARED_CHILD_SHAPES)
PREMIUM_DETAIL_SHAPE = ObjectShape(
    PREMIUM_SECTION_KEYS, _PREMIUM_CHILD_SHAPES
)
ADMIN_DETAIL_SHAPE = ObjectShape(ADMIN_SECTION_KEYS, _PREMIUM_CHILD_SHAPES)

SHAPES_BY_PROJECTION = {
    "free": FREE_DETAIL_SHAPE,
    "premium": PREMIUM_DETAIL_SHAPE,
    "admin": ADMIN_DETAIL_SHAPE,
}


def _value_text(value: object) -> str:
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _serialized_text(value: object) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _walk(value: object, path: str = "$"):
    yield path, value
    if isinstance(value, dict):
        for key in sorted(value):
            yield from _walk(value[key], f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            yield from _walk(child, f"{path}[{index}]")


def _assert_shape(value: object, shape: object, path: str) -> None:
    """Assert exact key sets for an object and every nested object."""

    if isinstance(shape, ListShape):
        assert isinstance(value, list), f"{path} must be a list"
        if shape.require_non_empty:
            assert value, f"{path} unexpectedly empty"
        for index, item in enumerate(value):
            _assert_shape(item, shape.item, f"{path}[{index}]")
        return

    assert isinstance(shape, ObjectShape)
    assert isinstance(value, dict), (
        f"{path} must be an object, got {type(value).__name__} ({value!r})"
    )
    assert set(value) == shape.keys, (
        f"{path} keys drifted: "
        f"missing={sorted(shape.keys - set(value))} "
        f"unexpected={sorted(set(value) - shape.keys)}"
    )
    for key, child_shape in shape.children.items():
        _assert_shape(value[key], child_shape, f"{path}.{key}")


def _assert_free_payload_carries_no_protected_content(
    payload: object,
    premium_markers: list[str],
    path: str,
) -> None:
    """No free value may carry protected wording or protected field names."""

    for node_path, value in _walk(payload, path):
        if isinstance(value, dict):
            for key in value:
                assert key not in PROTECTED_FIELD_NAMES, (
                    f"protected key {key!r} leaked into free payload at "
                    f"{node_path}"
                )
        elif isinstance(value, (str, int, float, Decimal, date, datetime)):
            text = _value_text(value)
            for marker in premium_markers:
                assert marker not in text, (
                    f"premium sentinel {marker!r} leaked into free payload "
                    f"at {node_path}"
                )


def _assert_projection_carries_every_premium_marker(
    projection: dict[str, object],
    premium_markers: list[str],
    scenario_id: str,
) -> None:
    projection_text = _serialized_text(projection)
    for marker in premium_markers:
        assert marker in projection_text, (
            f"{scenario_id} projection is missing {marker!r}"
        )


def test_matrix_covers_every_frozen_scenario(research_security_matrix):
    assert set(research_security_matrix["scenarios"]) == set(SCENARIO_IDS)


@pytest.mark.parametrize("scenario_id", SCENARIO_IDS)
def test_matrix_detail_projection_matches_frozen_contract(
    scenario_id: str, research_security_matrix
):
    matrix = research_security_matrix
    case = matrix["scenarios"][scenario_id]
    expected_projection = case["expected_projection"]

    # Production resolution is compared against literal expectations; a
    # regression in fail-closed resolution cannot satisfy this test.
    context = case["context"]
    assert context.tier == case["expected_context_tier"]
    assert context.is_admin is case["expected_is_admin"]

    aggregate = ResearchQueryService.get_company_detail(
        matrix["company_id"], context
    )
    projection = ResearchPresenter.company_detail(aggregate, context)

    assert set(projection) == SECTION_KEYS_BY_PROJECTION[expected_projection]
    assert projection["access"] == {
        "tier": case["expected_access_tier"],
        "locked_sections": case["expected_locked_sections"],
    }
    _assert_shape(
        projection,
        SHAPES_BY_PROJECTION[expected_projection],
        path=scenario_id,
    )

    if expected_projection == "free":
        _assert_free_payload_carries_no_protected_content(
            projection,
            matrix["premium_markers"],
            path=scenario_id,
        )
    else:
        _assert_projection_carries_every_premium_marker(
            projection,
            matrix["premium_markers"],
            scenario_id,
        )


@pytest.mark.parametrize("scenario_id", SCENARIO_IDS)
def test_matrix_disclosures_and_history_respect_tier(
    scenario_id: str, research_security_matrix
):
    matrix = research_security_matrix
    case = matrix["scenarios"][scenario_id]
    expected_projection = case["expected_projection"]

    disclosures = ResearchQueryService.list_disclosures(
        matrix["company_id"],
        event_type=None,
        is_key=None,
        date_from=None,
        date_to=None,
        newest_first=True,
        page=1,
        per_page=10,
        context=case["context"],
    )

    # The response is required to carry the seeded disclosure, so an
    # accidentally empty collection must fail instead of vacuously passing.
    assert disclosures.total_items == 1
    assert disclosures.items, "disclosure collection must not be empty"

    if expected_projection == "free":
        for item in disclosures.items:
            assert set(item) == FREE_DISCLOSURE_ITEM_KEYS
        _assert_free_payload_carries_no_protected_content(
            disclosures.items,
            matrix["premium_markers"],
            path=f"{scenario_id}.disclosures",
        )

        with pytest.raises(ResearchForbiddenError) as exc_info:
            ResearchQueryService.get_history(
                matrix["company_id"],
                section="research",
                valuation_method=None,
                page=1,
                per_page=10,
                context=case["context"],
            )
        assert exc_info.value.code == "history_forbidden"
        return

    for item in disclosures.items:
        assert set(item) == PREMIUM_DISCLOSURE_ITEM_KEYS
        assert item["significance_note"] == (
            matrix["disclosure_significance_marker"]
        )

    history = ResearchQueryService.get_history(
        matrix["company_id"],
        section="research",
        valuation_method=None,
        page=1,
        per_page=10,
        context=case["context"],
    )
    assert history.total_items == 1
    assert history.items, "history collection must not be empty"
    assert set(history.items[0]) == RESEARCH_HISTORY_ITEM_KEYS
    assert history.items[0]["thesis"] == matrix["research_thesis_marker"]
    _assert_shape(
        history.items[0],
        RESEARCH_HISTORY_ITEM_SHAPE,
        path=f"{scenario_id}.history[0]",
    )


def test_entitlement_changes_affect_next_call_without_new_jwt(
    app, research_security_matrix
):
    """One stale JWT keeps the database as the only tier authority."""

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
        additional_claims={"tier": "PREMIUM", "is_admin": True},
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

    def project_like_a_request() -> tuple[object, dict[str, object]]:
        context = EntitlementService.resolve(user, at=matrix["now"])
        projection = ResearchPresenter.company_detail(
            ResearchQueryService.get_company_detail(company_id, context),
            context,
        )
        return context, projection

    free_context, free_projection = project_like_a_request()
    assert free_context.tier == "FREE"
    assert free_context.is_admin is False
    assert set(free_projection) == FREE_SECTION_KEYS
    _assert_shape(free_projection, FREE_DETAIL_SHAPE, path="free")
    _assert_free_payload_carries_no_protected_content(
        free_projection, matrix["premium_markers"], path="free"
    )

    row = UserEntitlement.query.filter_by(
        user_id=user.id,
        product_code=INVESTMENT_RESEARCH_PRODUCT_CODE,
    ).one()
    row.tier = ResearchTier.PREMIUM
    db.session.commit()

    premium_context, premium_projection = project_like_a_request()
    assert premium_context.tier == "PREMIUM"
    assert set(premium_projection) == PREMIUM_SECTION_KEYS
    _assert_shape(premium_projection, PREMIUM_DETAIL_SHAPE, path="premium")
    _assert_projection_carries_every_premium_marker(
        premium_projection, matrix["premium_markers"], "premium"
    )

    row.status = EntitlementStatus.REVOKED
    db.session.commit()

    revoked_context, revoked_projection = project_like_a_request()
    assert revoked_context.tier == "FREE"
    assert set(revoked_projection) == FREE_SECTION_KEYS
    _assert_shape(revoked_projection, FREE_DETAIL_SHAPE, path="revoked")
    _assert_free_payload_carries_no_protected_content(
        revoked_projection, matrix["premium_markers"], path="revoked"
    )

    # The token is unchanged and still carries the stale, over-privileged
    # claims; only server-side resolution decided every outcome above.
    assert decode_token(token)["tier"] == "PREMIUM"
    assert decode_token(token)["is_admin"] is True


def test_stale_jwt_claim_is_rejected_at_the_real_request_boundary(
    client, research_security_matrix
):
    """Honest partial stale-JWT integration proof.

    The frozen P3T6 scope has no authenticated *research* request boundary
    (asserted by
    ``test_no_authenticated_research_request_boundary_exists_in_frozen_scope``),
    so this test pushes the stale-tier token through the authenticated request
    boundary that does exist and shows that boundary serves server-side state
    rather than the token's over-privileged claims. The entitlement outcome is
    then asserted through the same server-side resolution path the presenter
    consumes. Full entitlement-through-request-boundary integration is
    deferred until a research API exists; this test does not fake it.
    """

    matrix = research_security_matrix
    case = matrix["scenarios"][STALE_JWT_SCENARIO_ID]
    token = case["jwt"]

    claims = decode_token(token)
    assert claims["tier"] == "PREMIUM"
    assert claims["is_admin"] is True
    assert case["expected_context_tier"] == "FREE"
    assert case["expected_is_admin"] is False

    response = client.get(
        "/api/auth/me", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200
    served_user = response.get_json()["user"]
    assert served_user["id"] == case["user"].id
    # The request boundary re-reads server state; the stale admin claim is not
    # trusted even though the token itself is accepted.
    assert served_user["is_admin"] is False

    context = EntitlementService.resolve(case["user"], at=matrix["now"])
    assert context.user_id == case["user"].id
    assert context.is_admin is False
    assert context.tier == "FREE"

    projection = ResearchPresenter.company_detail(
        ResearchQueryService.get_company_detail(matrix["company_id"], context),
        context,
    )
    assert set(projection) == FREE_SECTION_KEYS
    _assert_shape(projection, FREE_DETAIL_SHAPE, path="stale-jwt")
    assert projection["access"]["tier"] == "FREE"
    _assert_free_payload_carries_no_protected_content(
        projection, matrix["premium_markers"], path="stale-jwt"
    )


def test_no_authenticated_research_request_boundary_exists_in_frozen_scope(app):
    """Guard for the deferred stale-JWT integration proof.

    If an authenticated research endpoint is added, this test fails and the
    deferred integration proof must be written against that real boundary
    instead of the service-level substitute.
    """

    rules = sorted(str(rule) for rule in app.url_map.iter_rules())
    research_rules = [rule for rule in rules if "research" in rule.lower()]
    assert research_rules == [], (
        "An authenticated research request boundary now exists: extend the "
        "stale-JWT test to exercise it end to end."
    )
    assert "/api/auth/me" in rules
