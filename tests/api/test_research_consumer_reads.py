"""Plan 5 Task 5: entitled consumer research read APIs.

These tests exercise the real Flask research consumer boundary. They seed the
frozen M1 domain directly, issue the same JWT twice where required, and assert
that the routes delegate to the existing entitlement-aware query/presenter
services without introducing write behavior or duplicating entitlement logic.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
import sqlalchemy as sa

from app import db
from app.models import (
    Company,
    CompanyDisclosure,
    ForecastLine,
    ForecastRevision,
    GovernanceFlag,
    MarketPlanRevision,
    OwnershipSnapshot,
    ResearchPoint,
    ResearchRevision,
    Ticker,
    ValuationReferenceLine,
    ValuationRevision,
)
from app.models.entitlement import (
    INVESTMENT_RESEARCH_PRODUCT_CODE,
    UserEntitlement,
)
from app.models.research_types import (
    EntitlementStatus,
    GovernanceFlagStatus,
    GovernanceSeverity,
    GovernanceStatus,
    ManagementQuality,
    ResearchPointKind,
    ResearchTier,
    ValuationMethod,
)


UTC = timezone.utc
AS_OF_DATE = date(2026, 9, 4)
VALID_ISIN = "INE0LOJ01019"


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _make_company(
    ticker: Ticker,
    *,
    legal_name: str = "IKIO Lighting Limited",
    isin: str = VALID_ISIN,
) -> Company:
    company = Company(
        ticker_id=ticker.id,
        legal_name=legal_name,
        display_name=None,
        isin=isin,
        sector="Industrials",
        industry="LED lighting",
        business_group_id=None,
        business_group_basis=None,
        business_group_source_reference=None,
    )
    db.session.add(company)
    db.session.flush()
    return company


@pytest.fixture
def company(ticker_factory):
    ticker = ticker_factory(
        symbol="IKIO",
        instrument_token=1001,
        exchange="NSE",
        name="IKIO Technologies Limited",
        last_price=123.45,
    )
    ticker.last_updated = _utc_now()
    db.session.flush()
    return _make_company(ticker)


@pytest.fixture
def other_company(ticker_factory):
    ticker = ticker_factory(
        symbol="WIPRO",
        instrument_token=1002,
        exchange="NSE",
        name="Wipro Limited",
        last_price=500.0,
    )
    ticker.last_updated = _utc_now()
    db.session.flush()
    return _make_company(
        ticker,
        legal_name="Wipro Limited",
        isin="INE075A01022",
    )


def _research_revision(
    company_id: str,
    actor_user_id: str,
    revision_number: int,
    *,
    thesis: str,
    supersedes_revision_id: str | None = None,
) -> ResearchRevision:
    revision = ResearchRevision(
        company_id=company_id,
        revision_number=revision_number,
        supersedes_revision_id=supersedes_revision_id,
        why_selected=f"Why selected {revision_number}",
        what_is_changing=f"What is changing {revision_number}",
        business_journey=f"Business journey {revision_number}",
        thesis=thesis,
        thesis_invalidation=f"Invalidation {revision_number}",
        management_summary=f"Management {revision_number}",
        management_quality=ManagementQuality.WATCH,
        management_rationale=f"Rationale {revision_number}",
        management_evidence=f"Evidence {revision_number}",
        governance_status=GovernanceStatus.WATCH,
        change_reason=(
            None if revision_number == 1 else f"Change {revision_number}"
        ),
        effective_at=_utc_now(),
        created_by_user_id=actor_user_id,
    )
    db.session.add(revision)
    revision.points.extend(
        [
            ResearchPoint(
                kind=ResearchPointKind.CATALYST,
                title=f"Catalyst {revision_number}",
                detail=None,
                status="OPEN",
                target_date=None,
                sort_order=0,
            ),
            ResearchPoint(
                kind=ResearchPointKind.RISK,
                title=f"Risk {revision_number}",
                detail=None,
                status=None,
                target_date=None,
                sort_order=0,
            ),
        ]
    )
    db.session.flush()
    return revision


def _market_plan_revision(
    company_id: str,
    actor_user_id: str,
    revision_number: int,
    *,
    accumulation_low: str,
    supersedes_revision_id: str | None = None,
) -> MarketPlanRevision:
    revision = MarketPlanRevision(
        company_id=company_id,
        revision_number=revision_number,
        supersedes_revision_id=supersedes_revision_id,
        currency="INR",
        accumulation_low=Decimal(accumulation_low),
        accumulation_high=Decimal("130.0000"),
        preferred_accumulation_price=None,
        supply_low=None,
        supply_high=None,
        invalidation_level=Decimal("90.0000"),
        rationale=f"Market plan {revision_number}",
        effective_at=_utc_now(),
        change_reason=(
            None if revision_number == 1 else f"Change {revision_number}"
        ),
        created_by_user_id=actor_user_id,
    )
    db.session.add(revision)
    db.session.flush()
    return revision


def _forecast_revision(
    company_id: str,
    actor_user_id: str,
    revision_number: int,
    *,
    assumptions: str,
    revenue: str,
    is_estimate: bool = True,
    supersedes_revision_id: str | None = None,
) -> ForecastRevision:
    revision = ForecastRevision(
        company_id=company_id,
        revision_number=revision_number,
        supersedes_revision_id=supersedes_revision_id,
        as_of_date=AS_OF_DATE,
        assumptions=assumptions,
        change_reason=(
            None if revision_number == 1 else f"Change {revision_number}"
        ),
        created_by_user_id=actor_user_id,
    )
    db.session.add(revision)
    db.session.flush()
    db.session.add(
        ForecastLine(
            forecast_revision_id=revision.id,
            fiscal_year=2027,
            is_estimate=is_estimate,
            revenue=Decimal(revenue),
            ebitda=Decimal("20.0000"),
            pat=Decimal("10.0000"),
            ebitda_margin_pct=Decimal("20.0000"),
            eps=Decimal("5.0000"),
            currency="INR",
            unit="CRORE",
        )
    )
    db.session.flush()
    return revision


def _valuation_revision(
    company_id: str,
    actor_user_id: str,
    revision_number: int,
    *,
    justified_multiple: str,
    supersedes_revision_id: str | None = None,
) -> ValuationRevision:
    revision = ValuationRevision(
        company_id=company_id,
        valuation_method=ValuationMethod.PE,
        revision_number=revision_number,
        supersedes_revision_id=supersedes_revision_id,
        justified_multiple=Decimal(justified_multiple),
        implied_enterprise_value=None,
        net_debt=Decimal("10.0000"),
        other_equity_adjustment=None,
        implied_future_equity_value=Decimal("1200.0000"),
        required_return_pct=None,
        discount_period_years=None,
        present_value=None,
        current_market_cap=Decimal("900.0000"),
        currency="INR",
        unit="CRORE",
        valuation_notes=f"Valuation {revision_number}",
        as_of_date=AS_OF_DATE,
        change_reason=(
            None if revision_number == 1 else f"Change {revision_number}"
        ),
        created_by_user_id=actor_user_id,
    )
    db.session.add(revision)
    db.session.flush()
    db.session.add(
        ValuationReferenceLine(
            valuation_revision_id=revision.id,
            reference_forecast_revision_id=None,
            reference_fiscal_year=2027,
            reference_metric="EPS",
            reference_metric_value=Decimal("5.0000"),
            reference_metric_unit="INR_PER_SHARE",
            reference_metric_basis=f"Basis {revision_number}",
            sort_order=0,
        )
    )
    db.session.flush()
    return revision


def _ownership_snapshot(company_id: str, actor_user_id: str) -> OwnershipSnapshot:
    snapshot = OwnershipSnapshot(
        company_id=company_id,
        as_of_date=AS_OF_DATE,
        promoter_holding_pct=Decimal("62.3500"),
        promoter_pledge_pct=Decimal("3.1200"),
        notes="Latest ownership snapshot",
        source_reference="https://example.in/ownership",
        created_by_user_id=actor_user_id,
    )
    db.session.add(snapshot)
    db.session.flush()
    return snapshot


def _governance_flag(company_id: str, actor_user_id: str) -> GovernanceFlag:
    flag = GovernanceFlag(
        company_id=company_id,
        flag_type="PROMOTER_PLEDGE",
        title="Promoter pledge flag",
        severity=GovernanceSeverity.HIGH,
        status=GovernanceFlagStatus.OPEN,
        factual_evidence="Factual evidence",
        source_title="Source title",
        source_url_or_reference="https://example.in/governance",
        interpretation="Interpretation",
        observed_on=AS_OF_DATE,
        resolved_on=None,
        created_by_user_id=actor_user_id,
    )
    db.session.add(flag)
    db.session.flush()
    return flag


def _disclosure(
    company_id: str,
    actor_user_id: str,
    *,
    title: str = "Key disclosure",
    significance_note: str | None = "Analytical note",
) -> CompanyDisclosure:
    disclosure = CompanyDisclosure(
        company_id=company_id,
        event_type="REG30",
        event_date=AS_OF_DATE,
        title=title,
        original_source_url_or_reference="https://exchange.example/ref",
        exchange_reference="BSE:REFERENCE",
        significance_note=significance_note,
        is_key=True,
        document_id=None,
        created_by_user_id=actor_user_id,
        archived_at=None,
    )
    db.session.add(disclosure)
    db.session.flush()
    return disclosure


def _seed_all(company: Company, admin_user) -> None:
    actor = admin_user.id
    first_research = _research_revision(
        company.id, actor, 1, thesis="Thesis one"
    )
    _research_revision(
        company.id,
        actor,
        2,
        thesis="Thesis two",
        supersedes_revision_id=first_research.id,
    )
    first_market = _market_plan_revision(
        company.id, actor, 1, accumulation_low="100.0000"
    )
    _market_plan_revision(
        company.id,
        actor,
        2,
        accumulation_low="95.0000",
        supersedes_revision_id=first_market.id,
    )
    first_forecast = _forecast_revision(
        company.id,
        actor,
        1,
        assumptions="Forecast one",
        revenue="100.0000",
    )
    _forecast_revision(
        company.id,
        actor,
        2,
        assumptions="Forecast two",
        revenue="110.5000",
        supersedes_revision_id=first_forecast.id,
    )
    first_valuation = _valuation_revision(
        company.id, actor, 1, justified_multiple="12.0000"
    )
    _valuation_revision(
        company.id,
        actor,
        2,
        justified_multiple="14.5000",
        supersedes_revision_id=first_valuation.id,
    )
    _ownership_snapshot(company.id, actor)
    _governance_flag(company.id, actor)
    _disclosure(company.id, actor)
    db.session.commit()


def _add_entitlement(
    user,
    *,
    tier: str = ResearchTier.PREMIUM,
    status: str = EntitlementStatus.ACTIVE,
) -> UserEntitlement:
    row = UserEntitlement(
        user_id=user.id,
        product_code=INVESTMENT_RESEARCH_PRODUCT_CODE,
        tier=tier,
        status=status,
    )
    db.session.add(row)
    db.session.commit()
    return row


# ---------------------------------------------------------------------------
# Authentication and entitlement
# ---------------------------------------------------------------------------


def test_consumer_research_routes_require_jwt(client):
    response = client.get("/api/research/companies")

    assert response.status_code == 401
    assert response.get_json() == {"error": "Token is invalid"}


def test_consumer_research_routes_reject_invalid_jwt(client):
    response = client.get(
        "/api/research/companies",
        headers={"Authorization": "Bearer not-a-valid-token"},
    )

    assert response.status_code == 401
    assert response.get_json() == {"error": "Token is invalid"}


def test_premium_user_can_read_history(
    client, company, admin_user, premium_user, auth_headers
):
    _seed_all(company, admin_user)

    response = client.get(
        f"/api/research/companies/{company.id}/history",
        query_string={"section": "research"},
        headers=auth_headers(premium_user),
    )

    assert response.status_code == 200
    assert [item["revision"] for item in response.get_json()["items"]] == [1, 2]


@pytest.mark.parametrize(
    "status",
    [EntitlementStatus.INACTIVE, EntitlementStatus.REVOKED],
)
def test_inactive_or_revoked_entitlement_denies_history(
    client,
    company,
    admin_user,
    user_factory,
    auth_headers,
    status,
):
    _seed_all(company, admin_user)
    user = user_factory(email=f"{status.lower()}@example.com")
    _add_entitlement(
        user, tier=ResearchTier.PREMIUM, status=status
    )

    response = client.get(
        f"/api/research/companies/{company.id}/history",
        query_string={"section": "research"},
        headers=auth_headers(user),
    )

    assert response.status_code == 403
    assert response.get_json()["error"] == "history_forbidden"


def test_entitlement_change_is_effective_without_new_jwt(
    client, company, admin_user, user_factory, auth_headers
):
    _seed_all(company, admin_user)
    user = user_factory(email="mutable-entitlement@example.com")
    headers = auth_headers(user)
    url = (
        f"/api/research/companies/{company.id}/history"
        "?section=research"
    )

    before = client.get(url, headers=headers)
    assert before.status_code == 403
    assert before.get_json()["error"] == "history_forbidden"

    row = _add_entitlement(
        user, tier=ResearchTier.PREMIUM, status=EntitlementStatus.ACTIVE
    )

    after = client.get(url, headers=headers)
    assert after.status_code == 200
    assert [item["revision"] for item in after.get_json()["items"]] == [1, 2]

    row.status = EntitlementStatus.REVOKED
    db.session.commit()

    revoked = client.get(url, headers=headers)
    assert revoked.status_code == 403
    assert revoked.get_json()["error"] == "history_forbidden"


# ---------------------------------------------------------------------------
# Current research and company scoping
# ---------------------------------------------------------------------------


def test_premium_user_sees_current_research_projection(
    client, company, admin_user, premium_user, auth_headers
):
    _seed_all(company, admin_user)

    response = client.get(
        f"/api/research/companies/{company.id}",
        headers=auth_headers(premium_user),
    )

    assert response.status_code == 200
    body = response.get_json()
    assert body["company"]["id"] == company.id
    assert body["access"]["tier"] == "PREMIUM"
    assert body["research"]["revision"] == 2
    assert body["research"]["thesis"] == "Thesis two"
    assert body["research"]["catalysts"][0]["title"] == "Catalyst 2"
    assert body["market_plan"]["revision"] == 2
    assert body["forecast"]["revision"] == 2
    assert body["valuations"][0]["revision"] == 2


def test_admin_user_reaches_full_current_projection_without_entitlement(
    client, company, admin_user, auth_headers
):
    _seed_all(company, admin_user)

    response = client.get(
        f"/api/research/companies/{company.id}",
        headers=auth_headers(admin_user),
    )

    assert response.status_code == 200
    body = response.get_json()
    assert body["access"]["tier"] == "ADMIN"
    assert body["research"]["thesis"] == "Thesis two"
    assert body["ownership"]["promoter_holding_pct"] == "62.3500"


def test_missing_company_returns_stable_research_404(
    client, premium_user, auth_headers
):
    response = client.get(
        "/api/research/companies/missing-company",
        headers=auth_headers(premium_user),
    )

    assert response.status_code == 404
    assert response.get_json() == {
        "error": "company_not_found",
        "message": "Company was not found",
        "details": {},
    }


# ---------------------------------------------------------------------------
# Immutable revision history
# ---------------------------------------------------------------------------


def test_research_history_returns_both_revisions_without_mutating_them(
    client, company, admin_user, premium_user, auth_headers
):
    first = _research_revision(
        company.id, admin_user.id, 1, thesis="Thesis one"
    )
    _research_revision(
        company.id,
        admin_user.id,
        2,
        thesis="Thesis two",
        supersedes_revision_id=first.id,
    )
    db.session.commit()
    url = (
        f"/api/research/companies/{company.id}/history"
        "?section=research"
    )

    history = client.get(url, headers=auth_headers(premium_user))

    assert history.status_code == 200
    items = history.get_json()["items"]
    assert [item["revision"] for item in items] == [1, 2]
    assert items[0]["thesis"] == "Thesis one"
    assert items[1]["thesis"] == "Thesis two"
    assert "supersedes_revision_id" not in items[0]
    assert "supersedes_revision_id" not in items[1]

    current = client.get(
        f"/api/research/companies/{company.id}",
        headers=auth_headers(premium_user),
    ).get_json()
    assert current["research"]["revision"] == 2
    assert current["research"]["thesis"] == "Thesis two"

    history_again = client.get(url, headers=auth_headers(premium_user)).get_json()
    assert history_again["items"][0]["thesis"] == "Thesis one"
    assert history_again["items"][1]["thesis"] == "Thesis two"


# ---------------------------------------------------------------------------
# Forecast / valuation / market plan current and history
# ---------------------------------------------------------------------------


def test_forecast_current_and_history_preserve_decimal_strings_and_estimate(
    client, company, admin_user, premium_user, auth_headers
):
    first = _forecast_revision(
        company.id,
        admin_user.id,
        1,
        assumptions="Forecast one",
        revenue="100.0000",
    )
    second = _forecast_revision(
        company.id,
        admin_user.id,
        2,
        assumptions="Forecast two",
        revenue="110.5000",
        is_estimate=False,
        supersedes_revision_id=first.id,
    )
    db.session.commit()

    current = client.get(
        f"/api/research/companies/{company.id}",
        headers=auth_headers(premium_user),
    ).get_json()
    assert current["forecast"]["revision"] == 2
    assert current["forecast"]["lines"][0]["revenue"] == "110.5000"
    assert current["forecast"]["lines"][0]["is_estimate"] is False

    history = client.get(
        f"/api/research/companies/{company.id}/history",
        query_string={"section": "forecast"},
        headers=auth_headers(premium_user),
    ).get_json()
    assert [item["revision"] for item in history["items"]] == [1, 2]
    assert history["items"][0]["assumptions"] == "Forecast one"
    assert history["items"][1]["assumptions"] == "Forecast two"
    assert history["items"][0]["lines"][0]["revenue"] == "100.0000"
    assert history["items"][1]["lines"][0]["revenue"] == "110.5000"
    assert history["items"][0]["lines"][0]["is_estimate"] is True
    assert history["items"][1]["lines"][0]["is_estimate"] is False


def test_valuation_current_and_history_preserve_reference_lines(
    client, company, admin_user, premium_user, auth_headers
):
    first = _valuation_revision(
        company.id, admin_user.id, 1, justified_multiple="12.0000"
    )
    _valuation_revision(
        company.id,
        admin_user.id,
        2,
        justified_multiple="14.5000",
        supersedes_revision_id=first.id,
    )
    db.session.commit()

    current = client.get(
        f"/api/research/companies/{company.id}",
        headers=auth_headers(premium_user),
    ).get_json()
    assert current["valuations"][0]["revision"] == 2
    assert current["valuations"][0]["justified_multiple"] == "14.5000"
    assert current["valuations"][0]["reference_lines"][0]["reference_metric"] == "EPS"

    history = client.get(
        f"/api/research/companies/{company.id}/history",
        query_string={"section": "valuation", "valuation_method": "PE"},
        headers=auth_headers(premium_user),
    ).get_json()
    assert [item["revision"] for item in history["items"]] == [1, 2]
    assert history["items"][0]["justified_multiple"] == "12.0000"
    assert history["items"][1]["justified_multiple"] == "14.5000"
    assert history["items"][0]["reference_lines"][0]["reference_metric_basis"] == "Basis 1"
    assert history["items"][1]["reference_lines"][0]["reference_metric_basis"] == "Basis 2"


def test_market_plan_current_and_history_are_immutable(
    client, company, admin_user, premium_user, auth_headers
):
    first = _market_plan_revision(
        company.id, admin_user.id, 1, accumulation_low="100.0000"
    )
    _market_plan_revision(
        company.id,
        admin_user.id,
        2,
        accumulation_low="95.0000",
        supersedes_revision_id=first.id,
    )
    db.session.commit()

    current = client.get(
        f"/api/research/companies/{company.id}",
        headers=auth_headers(premium_user),
    ).get_json()
    assert current["market_plan"]["revision"] == 2
    assert current["market_plan"]["accumulation_low"] == "95.0000"

    history = client.get(
        f"/api/research/companies/{company.id}/history",
        query_string={"section": "market"},
        headers=auth_headers(premium_user),
    ).get_json()
    assert history["items"][0]["accumulation_low"] == "100.0000"
    assert history["items"][1]["accumulation_low"] == "95.0000"


# ---------------------------------------------------------------------------
# Disclosures, governance, ownership and leakage hygiene
# ---------------------------------------------------------------------------


def test_disclosures_governance_and_ownership_are_consumer_visible_when_entitled(
    client, company, admin_user, premium_user, auth_headers
):
    _seed_all(company, admin_user)

    detail = client.get(
        f"/api/research/companies/{company.id}",
        headers=auth_headers(premium_user),
    ).get_json()
    assert detail["governance"]["status"] == GovernanceStatus.WATCH
    assert detail["governance"]["flags"][0]["title"] == "Promoter pledge flag"
    assert detail["ownership"]["promoter_holding_pct"] == "62.3500"

    disclosures = client.get(
        f"/api/research/companies/{company.id}/disclosures",
        headers=auth_headers(premium_user),
    ).get_json()
    assert disclosures["total_items"] == 1
    assert disclosures["items"][0]["significance_note"] == "Analytical note"


def test_free_and_private_fields_are_omitted(
    client, company, admin_user, free_user, auth_headers
):
    _seed_all(company, admin_user)

    detail = client.get(
        f"/api/research/companies/{company.id}",
        headers=auth_headers(free_user),
    ).get_json()
    assert "research" not in detail
    assert "management" not in detail
    assert "governance" not in detail
    assert "ownership" not in detail
    assert "market_plan" not in detail
    assert "forecast" not in detail
    assert "valuations" not in detail

    disclosures = client.get(
        f"/api/research/companies/{company.id}/disclosures",
        headers=auth_headers(free_user),
    ).get_json()
    assert "significance_note" not in disclosures["items"][0]


def test_consumer_responses_omit_private_admin_and_storage_fields(
    client, company, admin_user, premium_user, auth_headers
):
    _seed_all(company, admin_user)

    detail = client.get(
        f"/api/research/companies/{company.id}",
        headers=auth_headers(premium_user),
    ).get_data(as_text=True)
    history = client.get(
        f"/api/research/companies/{company.id}/history",
        query_string={"section": "research"},
        headers=auth_headers(premium_user),
    ).get_data(as_text=True)
    disclosures = client.get(
        f"/api/research/companies/{company.id}/disclosures",
        headers=auth_headers(premium_user),
    ).get_data(as_text=True)

    for text in (detail, history, disclosures):
        assert "created_by_user_id" not in text
        assert "updated_by_user_id" not in text
        assert "password_hash" not in text
        assert "storage_key" not in text
        assert "storage_provider" not in text
        assert "content_hash_sha256" not in text


# ---------------------------------------------------------------------------
# Isolation and validation
# ---------------------------------------------------------------------------


def test_consumer_reads_are_company_scoped(
    client, company, other_company, admin_user, premium_user, auth_headers
):
    _seed_all(company, admin_user)
    _research_revision(
        other_company.id, admin_user.id, 1, thesis="Other company thesis"
    )
    db.session.commit()
    headers = auth_headers(premium_user)

    detail = client.get(
        f"/api/research/companies/{company.id}", headers=headers
    ).get_json()
    assert detail["company"]["id"] == company.id
    assert detail["research"]["thesis"] == "Thesis two"
    assert "Other company thesis" not in client.get(
        f"/api/research/companies/{company.id}", headers=headers
    ).get_data(as_text=True)

    history = client.get(
        f"/api/research/companies/{company.id}/history",
        query_string={"section": "research"},
        headers=headers,
    ).get_json()
    assert "Other company thesis" not in str(history)

    disclosures = client.get(
        f"/api/research/companies/{company.id}/disclosures",
        headers=headers,
    ).get_json()
    assert all(item["company_id"] == company.id for item in disclosures["items"])


@pytest.mark.parametrize(
    "url,query_string",
    [
        ("/api/research/companies", {"unknown": "value"}),
        (
            "/api/research/companies/company-id/disclosures",
            {"unknown": "value"},
        ),
        (
            "/api/research/companies/company-id/history",
            {"section": "research", "unknown": "value"},
        ),
    ],
)
def test_unknown_query_parameters_return_stable_validation_error(
    client, premium_user, auth_headers, url, query_string
):
    response = client.get(
        url,
        query_string=query_string,
        headers=auth_headers(premium_user),
    )

    assert response.status_code == 400
    assert response.get_json()["error"] == "validation_error"
    assert "unknown" in response.get_json()["details"]


def test_consumer_reads_do_not_commit_or_create_rows(
    app, client, company, admin_user, premium_user, auth_headers
):
    _seed_all(company, admin_user)
    before_counts = {
        "research": db.session.scalar(
            sa.select(sa.func.count()).select_from(ResearchRevision)
        ),
        "company": db.session.scalar(
            sa.select(sa.func.count()).select_from(Company)
        ),
    }
    headers = auth_headers(premium_user)

    client.get("/api/research/companies", headers=headers)
    client.get(f"/api/research/companies/{company.id}", headers=headers)
    client.get(
        f"/api/research/companies/{company.id}/disclosures",
        headers=headers,
    )
    client.get(
        f"/api/research/companies/{company.id}/history",
        query_string={"section": "research"},
        headers=headers,
    )

    with app.app_context():
        db.session.rollback()
        after_counts = {
            "research": db.session.scalar(
                sa.select(sa.func.count()).select_from(ResearchRevision)
            ),
            "company": db.session.scalar(
                sa.select(sa.func.count()).select_from(Company)
            ),
        }
    assert after_counts == before_counts
