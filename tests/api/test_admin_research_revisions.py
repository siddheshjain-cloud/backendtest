"""Plan 5 Task 4: administrative immutable research revision APIs.

These tests call the real Flask application and pin the revision creation
routes, strict schemas, server-derived lineage, decimal serialization, and
append-only immutability at the API boundary.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
import sqlalchemy as sa

from app import db
from app.models import (
    ForecastLine,
    ForecastRevision,
    MarketPlanRevision,
    ResearchPoint,
    ResearchRevision,
    ValuationReferenceLine,
    ValuationRevision,
)
from app.models.research_types import (
    GovernanceStatus,
    ManagementQuality,
    ResearchPointKind,
    ValuationMethod,
)
from app.services.research_command_service import ResearchCommandService


VALID_ISIN = "INE0LOJ01019"
EFFECTIVE_AT = "2026-09-12T00:00:00+00:00"
AS_OF_DATE = "2026-09-04"


@pytest.fixture
def company(ticker_factory, admin_user):
    ticker = ticker_factory()
    return ResearchCommandService.create_company(
        {
            "ticker_id": ticker.id,
            "legal_name": "IKIO Lighting Limited",
            "isin": VALID_ISIN,
        },
        actor_user_id=admin_user.id,
    )


def _research_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "why_selected": "Durable demand growth",
        "what_is_changing": "Capacity expansion",
        "business_journey": "Expanding into new product lines",
        "thesis": "Operating leverage compounds",
        "thesis_invalidation": "Customer concentration or margin erosion",
        "management_summary": "Experienced leadership team",
        "management_quality": ManagementQuality.UNASSESSED,
        "management_rationale": None,
        "management_evidence": None,
        "governance_status": GovernanceStatus.UNREVIEWED,
        "effective_at": EFFECTIVE_AT,
        "points": [
            {
                "kind": ResearchPointKind.CATALYST,
                "title": "New plant commissioning",
                "detail": "Capacity doubles by FY27",
                "status": "OPEN",
                "target_date": "2027-03-31",
                "sort_order": 0,
            },
            {
                "kind": ResearchPointKind.RISK,
                "title": "Customer concentration",
                "detail": None,
                "status": None,
                "target_date": None,
                "sort_order": 0,
            },
        ],
    }
    payload.update(overrides)
    return payload


def _market_plan_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "currency": "INR",
        "accumulation_low": "100.0000",
        "accumulation_high": "130.0000",
        "preferred_accumulation_price": "115.0000",
        "supply_low": None,
        "supply_high": None,
        "invalidation_level": "90.0000",
        "rationale": "Accumulate below fair value",
        "effective_at": EFFECTIVE_AT,
    }
    payload.update(overrides)
    return payload


def _forecast_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "as_of_date": AS_OF_DATE,
        "assumptions": "Volume-led growth",
        "lines": [
            {
                "fiscal_year": 2027,
                "is_estimate": True,
                "revenue": "1234.5000",
                "ebitda": "250.7500",
                "pat": "10.0000",
                "ebitda_margin_pct": "20.3100",
                "eps": "5.0000",
                "currency": "INR",
                "unit": "CRORE",
            }
        ],
    }
    payload.update(overrides)
    return payload


def _valuation_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "valuation_method": ValuationMethod.PE,
        "justified_multiple": "12.5000",
        "implied_enterprise_value": None,
        "net_debt": None,
        "other_equity_adjustment": None,
        "implied_future_equity_value": None,
        "required_return_pct": None,
        "discount_period_years": None,
        "present_value": None,
        "current_market_cap": None,
        "currency": None,
        "unit": None,
        "valuation_notes": "PE based on administratively supplied EPS",
        "as_of_date": AS_OF_DATE,
        "reference_lines": [],
    }
    payload.update(overrides)
    return payload


def _post(client, url: str, payload: dict, headers: dict):
    return client.post(url, json=payload, headers=headers)


def _count(model) -> int:
    return db.session.scalar(sa.select(sa.func.count()).select_from(model))


def _research_revision_url(company_id: str) -> str:
    return f"/api/admin/research/companies/{company_id}/research-revisions"


def _market_plan_revision_url(company_id: str) -> str:
    return f"/api/admin/research/companies/{company_id}/market-plan-revisions"


def _forecast_revision_url(company_id: str) -> str:
    return f"/api/admin/research/companies/{company_id}/forecast-revisions"


def _valuation_revision_url(company_id: str) -> str:
    return f"/api/admin/research/companies/{company_id}/valuation-revisions"


# ---------------------------------------------------------------------------
# Authentication and authorization
# ---------------------------------------------------------------------------


def test_missing_token_returns_legacy_401(client, company):
    response = client.post(_research_revision_url(company.id), json={})

    assert response.status_code == 401
    assert response.get_json() == {"error": "Token is invalid"}


def test_non_admin_returns_legacy_403(
    client, company, free_user, auth_headers
):
    response = client.post(
        _research_revision_url(company.id),
        json={},
        headers=auth_headers(free_user),
    )

    assert response.status_code == 403
    assert response.get_json() == {"error": "Admin access required"}


def test_admin_can_reach_all_revision_create_routes(
    client, company, admin_user, auth_headers
):
    headers = auth_headers(admin_user)

    research = _post(
        client, _research_revision_url(company.id), _research_payload(), headers
    )
    assert research.status_code == 201

    market_plan = _post(
        client,
        _market_plan_revision_url(company.id),
        _market_plan_payload(),
        headers,
    )
    assert market_plan.status_code == 201

    forecast = _post(
        client,
        _forecast_revision_url(company.id),
        _forecast_payload(),
        headers,
    )
    assert forecast.status_code == 201

    valuation = _post(
        client,
        _valuation_revision_url(company.id),
        _valuation_payload(),
        headers,
    )
    assert valuation.status_code == 201


# ---------------------------------------------------------------------------
# Revision numbering and immutability
# ---------------------------------------------------------------------------


def test_first_research_revision_gets_number_one(
    client, company, admin_user, auth_headers
):
    response = _post(
        client,
        _research_revision_url(company.id),
        _research_payload(),
        auth_headers(admin_user),
    )

    assert response.status_code == 201
    body = response.get_json()
    assert body["revision_number"] == 1
    assert body["company_id"] == company.id
    assert body["supersedes_revision_id"] is None
    assert len(body["points"]) == 2
    assert _count(ResearchRevision) == 1


def test_second_research_revision_gets_number_two(
    client, company, admin_user, auth_headers
):
    headers = auth_headers(admin_user)
    first = _post(
        client,
        _research_revision_url(company.id),
        _research_payload(),
        headers,
    ).get_json()

    second = _post(
        client,
        _research_revision_url(company.id),
        _research_payload(
            base_revision_id=first["id"],
            change_reason="Revised after earnings",
            thesis="A substantially updated thesis",
        ),
        headers,
    )

    assert second.status_code == 201
    body = second.get_json()
    assert body["revision_number"] == 2
    assert body["supersedes_revision_id"] == first["id"]
    assert _count(ResearchRevision) == 2


def test_revision_one_remains_unchanged_after_revision_two(
    client, company, admin_user, auth_headers
):
    headers = auth_headers(admin_user)
    first = _post(
        client,
        _research_revision_url(company.id),
        _research_payload(),
        headers,
    ).get_json()
    original_row = db.session.get(ResearchRevision, first["id"])
    original_snapshot = {
        "thesis": original_row.thesis,
        "revision_number": original_row.revision_number,
        "supersedes_revision_id": original_row.supersedes_revision_id,
    }

    _post(
        client,
        _research_revision_url(company.id),
        _research_payload(
            base_revision_id=first["id"],
            change_reason="Revised after earnings",
            thesis="A substantially updated thesis",
        ),
        headers,
    )

    db.session.expire_all()
    persisted = db.session.get(ResearchRevision, first["id"])
    assert persisted.thesis == original_snapshot["thesis"]
    assert persisted.revision_number == original_snapshot["revision_number"]
    assert (
        persisted.supersedes_revision_id
        == original_snapshot["supersedes_revision_id"]
    )


def test_stale_base_returns_409_without_new_revision(
    client, company, admin_user, auth_headers
):
    headers = auth_headers(admin_user)
    first = _post(
        client,
        _research_revision_url(company.id),
        _research_payload(),
        headers,
    ).get_json()
    _post(
        client,
        _research_revision_url(company.id),
        _research_payload(
            base_revision_id=first["id"],
            change_reason="First update",
        ),
        headers,
    )

    stale = _post(
        client,
        _research_revision_url(company.id),
        _research_payload(
            base_revision_id=first["id"],
            change_reason="Stale update",
        ),
        headers,
    )

    assert stale.status_code == 409
    assert stale.get_json()["error"] == "revision_conflict"
    assert _count(ResearchRevision) == 2


# ---------------------------------------------------------------------------
# Strict schemas and server-owned fields
# ---------------------------------------------------------------------------


def test_revision_number_cannot_be_supplied_by_client(
    client, company, admin_user, auth_headers
):
    response = _post(
        client,
        _research_revision_url(company.id),
        _research_payload(revision_number=1),
        auth_headers(admin_user),
    )

    assert response.status_code == 400
    assert response.get_json()["error"] == "validation_error"
    assert "revision_number" in response.get_json()["details"]
    assert _count(ResearchRevision) == 0


def test_ids_actor_and_server_timestamps_cannot_be_supplied(
    client, company, admin_user, auth_headers
):
    response = _post(
        client,
        _research_revision_url(company.id),
        _research_payload(
            id="spoofed-id",
            created_by_user_id="spoofed-actor",
            created_at="2026-01-01T00:00:00+00:00",
        ),
        auth_headers(admin_user),
    )

    assert response.status_code == 400
    details = response.get_json()["details"]
    assert "id" in details
    assert "created_by_user_id" in details
    assert "created_at" in details


def test_unknown_top_level_and_nested_fields_are_rejected(
    client, company, admin_user, auth_headers
):
    top_level = _post(
        client,
        _research_revision_url(company.id),
        _research_payload(top_level_unknown=True),
        auth_headers(admin_user),
    )
    assert top_level.status_code == 400
    assert "top_level_unknown" in top_level.get_json()["details"]

    nested = _post(
        client,
        _research_revision_url(company.id),
        _research_payload(
            points=[
                {
                    "kind": ResearchPointKind.CATALYST,
                    "title": "Point",
                    "sort_order": 0,
                    "point_unknown": True,
                }
            ]
        ),
        auth_headers(admin_user),
    )
    assert nested.status_code == 400
    assert nested.get_json()["error"] == "validation_error"


def test_validation_error_returns_stable_400(
    client, company, admin_user, auth_headers
):
    response = _post(
        client,
        _forecast_revision_url(company.id),
        _forecast_payload(as_of_date=None),
        auth_headers(admin_user),
    )

    assert response.status_code == 400
    assert response.get_json()["error"] == "validation_error"
    assert _count(ForecastRevision) == 0


def test_missing_company_returns_stable_404(
    client, admin_user, auth_headers
):
    response = _post(
        client,
        _research_revision_url("missing-company"),
        _research_payload(),
        auth_headers(admin_user),
    )

    assert response.status_code == 404
    assert response.get_json()["error"] == "company_not_found"


def test_missing_valuation_reference_forecast_returns_404(
    client, company, admin_user, auth_headers
):
    response = _post(
        client,
        _valuation_revision_url(company.id),
        _valuation_payload(
            reference_lines=[
                {
                    "reference_forecast_revision_id": "missing-forecast",
                    "reference_fiscal_year": 2027,
                    "reference_metric": "EPS",
                    "reference_metric_value": "5.0000",
                    "reference_metric_unit": "INR_PER_SHARE",
                    "reference_metric_basis": "Administrator supplied EPS",
                    "sort_order": 0,
                }
            ]
        ),
        auth_headers(admin_user),
    )

    assert response.status_code == 404
    assert response.get_json()["error"] == "forecast_revision_not_found"


def test_unexpected_error_hides_exception_text_and_rolls_back(
    client, company, admin_user, auth_headers, monkeypatch
):
    def _fail(_cls, _company_id, _actor_user_id, _payload):
        db.session.add(
            ResearchRevision(
                company_id=company.id,
                revision_number=999,
                why_selected="rollback probe",
                thesis="rollback probe",
                thesis_invalidation="rollback probe",
                management_quality=ManagementQuality.UNASSESSED,
                governance_status=GovernanceStatus.UNREVIEWED,
                effective_at=EFFECTIVE_AT,
                created_by_user_id=admin_user.id,
            )
        )
        db.session.flush()
        raise RuntimeError("sensitive internal detail")

    monkeypatch.setattr(
        ResearchCommandService,
        "create_research_revision",
        classmethod(_fail),
    )

    response = _post(
        client,
        _research_revision_url(company.id),
        _research_payload(),
        auth_headers(admin_user),
    )

    assert response.status_code == 500
    assert response.get_json() == {
        "error": "internal_error",
        "message": "Internal server error",
        "details": {},
    }
    assert "sensitive internal detail" not in response.get_data(as_text=True)
    assert _count(ResearchRevision) == 0


# ---------------------------------------------------------------------------
# Decimal serialization and domain invariants
# ---------------------------------------------------------------------------


def test_decimal_response_values_are_strings(
    client, company, admin_user, auth_headers
):
    headers = auth_headers(admin_user)
    market_plan = _post(
        client,
        _market_plan_revision_url(company.id),
        _market_plan_payload(),
        headers,
    ).get_json()
    assert market_plan["accumulation_low"] == "100.0000"
    assert market_plan["invalidation_level"] == "90.0000"
    assert isinstance(market_plan["accumulation_high"], str)

    forecast = _post(
        client,
        _forecast_revision_url(company.id),
        _forecast_payload(),
        headers,
    ).get_json()
    assert forecast["lines"][0]["revenue"] == "1234.5000"
    assert isinstance(forecast["lines"][0]["ebitda"], str)


def test_forecast_lines_preserve_estimate_actual_and_values(
    client, company, admin_user, auth_headers
):
    response = _post(
        client,
        _forecast_revision_url(company.id),
        _forecast_payload(
            assumptions="Actual prior year plus management estimates",
            lines=[
                {
                    "fiscal_year": 2027,
                    "is_estimate": False,
                    "revenue": "987.6500",
                    "ebitda": None,
                    "pat": "45.6700",
                    "ebitda_margin_pct": None,
                    "eps": "2.5000",
                    "currency": "INR",
                    "unit": "CRORE",
                }
            ],
        ),
        auth_headers(admin_user),
    )

    assert response.status_code == 201
    body = response.get_json()
    line = body["lines"][0]
    assert line["is_estimate"] is False
    assert line["revenue"] == "987.6500"
    assert line["pat"] == "45.6700"
    assert line["ebitda"] is None

    persisted = db.session.scalar(
        sa.select(ForecastLine).where(
            ForecastLine.forecast_revision_id == body["id"]
        )
    )
    assert persisted.is_estimate is False
    assert persisted.revenue == Decimal("987.6500")


def test_valuation_reference_lines_preserve_invariants(
    client, company, admin_user, auth_headers
):
    headers = auth_headers(admin_user)
    forecast = _post(
        client,
        _forecast_revision_url(company.id),
        _forecast_payload(),
        headers,
    ).get_json()

    valuation = _post(
        client,
        _valuation_revision_url(company.id),
        _valuation_payload(
            reference_lines=[
                {
                    "reference_forecast_revision_id": forecast["id"],
                    "reference_fiscal_year": 2027,
                    "reference_metric": "EPS",
                    "reference_metric_value": "5.0000",
                    "reference_metric_unit": "INR_PER_SHARE",
                    "reference_metric_basis": "Administrator supplied EPS",
                    "sort_order": 0,
                }
            ]
        ),
        headers,
    )

    assert valuation.status_code == 201
    line = valuation.get_json()["reference_lines"][0]
    assert line["reference_metric"] == "EPS"
    assert line["reference_metric_value"] == "5.0000"
    assert line["reference_metric_basis"] == "Administrator supplied EPS"
    assert _count(ValuationReferenceLine) == 1

    invalid = _post(
        client,
        _valuation_revision_url(company.id),
        _valuation_payload(
            reference_lines=[
                {
                    "reference_forecast_revision_id": forecast["id"],
                    "reference_fiscal_year": 2027,
                    "reference_metric": "REVENUE",
                    "reference_metric_value": "1234.5000",
                    "reference_metric_unit": "INR_CRORE",
                    "reference_metric_basis": "Not valid for PE",
                    "sort_order": 0,
                }
            ]
        ),
        headers,
    )

    assert invalid.status_code == 400
    assert invalid.get_json()["error"] == "validation_error"


def test_research_points_preserve_kind_status_and_ordering(
    client, company, admin_user, auth_headers
):
    response = _post(
        client,
        _research_revision_url(company.id),
        _research_payload(),
        auth_headers(admin_user),
    )

    assert response.status_code == 201
    points = response.get_json()["points"]
    assert [point["kind"] for point in points] == [
        ResearchPointKind.CATALYST,
        ResearchPointKind.RISK,
    ]
    assert points[0]["status"] == "OPEN"
    assert points[0]["target_date"] == "2027-03-31"
    assert points[1]["status"] is None
    assert _count(ResearchPoint) == 2


def test_historical_revision_cannot_be_patched_in_place(
    client, company, admin_user, auth_headers
):
    headers = auth_headers(admin_user)
    created = _post(
        client,
        _research_revision_url(company.id),
        _research_payload(),
        headers,
    ).get_json()
    original = db.session.get(ResearchRevision, created["id"])
    original_thesis = original.thesis

    patched = client.patch(
        f"/api/admin/research/research-revisions/{created['id']}",
        json={"thesis": "Overwritten thesis"},
        headers=headers,
    )

    assert patched.status_code == 404
    db.session.expire_all()
    assert db.session.get(ResearchRevision, created["id"]).thesis == original_thesis
