"""Plan 3 Task 4: company list and current-detail query service tests.

Covers the frozen ``ResearchQueryService`` contract: filtered and paginated
company summaries ordered by display label then ID, page-bound validation,
current revision selection by highest number, independent current valuation
streams per method, latest ownership by date, active-only governance flags,
the read-only market quote projection, typed not-found handling, and a strict
guarantee that free queries never load or return premium relationships.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from dataclasses import fields
from decimal import Decimal
import re

import pytest
import sqlalchemy as sa

from app import db
from app.models import (
    BusinessGroup,
    Company,
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
from app.models.research_types import (
    GovernanceFlagStatus,
    GovernanceSeverity,
    GovernanceStatus,
    ManagementQuality,
    ResearchPointKind,
    ResearchTier,
    ValuationMethod,
)
from app.services.entitlement_service import ResearchAccessContext
from app.services.market_price_service import MarketPriceService
from app.services.research_query_service import (
    PageResult,
    ResearchQueryService,
)
from app.utils.research_errors import (
    ResearchNotFoundError,
    ResearchValidationError,
)


UTC = timezone.utc
FIXED_NOW = datetime(2026, 9, 10, 12, 0, tzinfo=UTC)
AS_OF_DATE = date(2026, 9, 4)

FREE_CONTEXT = ResearchAccessContext("user-free", False, ResearchTier.FREE)
PREMIUM_CONTEXT = ResearchAccessContext(
    "user-premium", False, ResearchTier.PREMIUM
)
ADMIN_CONTEXT = ResearchAccessContext("user-admin", True, ResearchTier.FREE)

PREMIUM_DETAIL_KEYS = frozenset(
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

PREMIUM_TABLE_NAMES = frozenset(
    {
        "research_revision",
        "research_point",
        "ownership_snapshot",
        "governance_flag",
        "market_plan_revision",
        "forecast_revision",
        "forecast_line",
        "valuation_revision",
        "valuation_reference_line",
    }
)


def _research_revision(
    company_id: str,
    actor_user_id: str,
    revision_number: int,
    supersedes_revision_id: str | None = None,
) -> ResearchRevision:
    revision = ResearchRevision(
        company_id=company_id,
        revision_number=revision_number,
        supersedes_revision_id=supersedes_revision_id,
        why_selected=f"Why selected {revision_number}",
        what_is_changing=f"What is changing {revision_number}",
        business_journey=f"Business journey {revision_number}",
        thesis=f"Thesis {revision_number}",
        thesis_invalidation=f"Invalidation {revision_number}",
        management_summary=f"Management {revision_number}",
        management_quality=ManagementQuality.WATCH,
        management_rationale=f"Rationale {revision_number}",
        management_evidence=f"Evidence {revision_number}",
        governance_status=GovernanceStatus.WATCH,
        change_reason=(
            None if revision_number == 1 else f"Change {revision_number}"
        ),
        effective_at=FIXED_NOW,
        created_by_user_id=actor_user_id,
    )
    db.session.add(revision)
    revision.points.extend(
        [
            ResearchPoint(
                kind=ResearchPointKind.CATALYST,
                title=f"Catalyst {revision_number}",
                sort_order=0,
            ),
            ResearchPoint(
                kind=ResearchPointKind.RISK,
                title=f"Risk {revision_number}",
                sort_order=0,
            ),
        ]
    )
    db.session.flush()
    return revision


def _market_plan(
    company_id: str,
    actor_user_id: str,
    revision_number: int,
    supersedes_revision_id: str | None = None,
) -> MarketPlanRevision:
    revision = MarketPlanRevision(
        company_id=company_id,
        revision_number=revision_number,
        supersedes_revision_id=supersedes_revision_id,
        currency="INR",
        accumulation_low=Decimal("100.0000"),
        accumulation_high=Decimal("130.0000"),
        preferred_accumulation_price=None,
        supply_low=None,
        supply_high=None,
        invalidation_level=Decimal("90.0000"),
        rationale=f"Market plan {revision_number}",
        effective_at=FIXED_NOW,
        change_reason=(
            None if revision_number == 1 else f"Change {revision_number}"
        ),
        created_by_user_id=actor_user_id,
    )
    if revision_number == 2:
        revision.accumulation_low = Decimal("95.0000")
        revision.accumulation_high = Decimal("120.0000")
        revision.invalidation_level = Decimal("85.0000")
    db.session.add(revision)
    db.session.flush()
    return revision


def _forecast(
    company_id: str,
    actor_user_id: str,
    revision_number: int,
    supersedes_revision_id: str | None = None,
) -> ForecastRevision:
    revision = ForecastRevision(
        company_id=company_id,
        revision_number=revision_number,
        supersedes_revision_id=supersedes_revision_id,
        as_of_date=AS_OF_DATE,
        assumptions=f"Forecast {revision_number}",
        change_reason=(
            None if revision_number == 1 else f"Change {revision_number}"
        ),
        created_by_user_id=actor_user_id,
    )
    db.session.add(revision)
    db.session.flush()
    return revision


def _forecast_line(
    forecast_revision_id: str, fiscal_year: int
) -> ForecastLine:
    line = ForecastLine(
        forecast_revision_id=forecast_revision_id,
        fiscal_year=fiscal_year,
        is_estimate=True,
        revenue=Decimal("100.0000"),
        ebitda=Decimal("20.0000"),
        ebitda_margin_pct=Decimal("20.0000"),
        pat=Decimal("10.0000"),
        eps=Decimal("5.0000"),
        currency="INR",
        unit="CRORE",
    )
    db.session.add(line)
    db.session.flush()
    return line


def _valuation(
    company_id: str,
    actor_user_id: str,
    method: str,
    revision_number: int,
    supersedes_revision_id: str | None = None,
) -> ValuationRevision:
    revision = ValuationRevision(
        company_id=company_id,
        valuation_method=method,
        revision_number=revision_number,
        supersedes_revision_id=supersedes_revision_id,
        justified_multiple=Decimal("12.0000"),
        implied_enterprise_value=None,
        net_debt=None,
        other_equity_adjustment=None,
        implied_future_equity_value=Decimal("1200.0000"),
        required_return_pct=None,
        discount_period_years=None,
        present_value=None,
        current_market_cap=Decimal("900.0000"),
        currency="INR",
        unit="CRORE",
        valuation_notes=(
            f"{method} valuation {revision_number}"
        ),
        as_of_date=AS_OF_DATE,
        change_reason=(
            None if revision_number == 1 else f"Change {revision_number}"
        ),
        created_by_user_id=actor_user_id,
    )
    db.session.add(revision)
    db.session.flush()
    return revision


def _reference_line(
    valuation_revision_id: str,
    sort_order: int,
    metric: str = "EPS",
    value: Decimal = Decimal("5.0000"),
) -> ValuationReferenceLine:
    line = ValuationReferenceLine(
        valuation_revision_id=valuation_revision_id,
        reference_forecast_revision_id=None,
        reference_fiscal_year=None,
        reference_metric=metric,
        reference_metric_value=value,
        reference_metric_unit=(
            "INR_PER_SHARE" if metric == "EPS" else "INR_CRORE"
        ),
        reference_metric_basis=f"Basis {metric}",
        sort_order=sort_order,
    )
    db.session.add(line)
    db.session.flush()
    return line


def _governance_flag(
    company_id: str, actor_user_id: str, *, title: str
) -> GovernanceFlag:
    flag = GovernanceFlag(
        company_id=company_id,
        flag_type="PROMOTER_PLEDGE",
        title=title,
        severity=GovernanceSeverity.HIGH,
        status=GovernanceFlagStatus.OPEN,
        factual_evidence=f"Factual evidence for {title}",
        source_title="Source title",
        source_url_or_reference="https://example.in/governance/source",
        interpretation=f"Interpretation for {title}",
        observed_on=AS_OF_DATE,
        resolved_on=None,
        created_by_user_id=actor_user_id,
    )
    db.session.add(flag)
    db.session.flush()
    return flag


def _company_payload(company: Company) -> dict[str, object]:
    return {
        "id": company.id,
        "name": company.display_label,
        "symbol": company.ticker.symbol,
        "exchange": company.ticker.exchange,
        "isin": company.isin,
        "sector": company.sector,
        "industry": company.industry,
    }


def _fresh_quote_time() -> datetime:
    """Execution-relative timestamp inside the production freshness window."""

    return datetime.now(timezone.utc)


def _quote(ticker: Ticker) -> dict[str, object]:
    return MarketPriceService.project(ticker, now=_fresh_quote_time())


@pytest.fixture
def company_factory(app, ticker_factory):
    def create_company(
        *,
        legal_name: str,
        symbol: str,
        ticker_name: str,
        isin: str,
        instrument_token: int,
        display_name: str | None = None,
        sector: str | None = None,
        industry: str | None = None,
        exchange: str = "NSE",
        business_group: BusinessGroup | None = None,
    ) -> Company:
        ticker = ticker_factory(
            symbol=symbol,
            instrument_token=instrument_token,
            exchange=exchange,
            name=ticker_name,
            last_price=123.45,
        )
        ticker.last_updated = _fresh_quote_time()
        db.session.flush()
        company = Company(
            ticker_id=ticker.id,
            legal_name=legal_name,
            display_name=display_name,
            isin=isin,
            sector=sector,
            industry=industry,
            business_group_id=(
                None if business_group is None else business_group.id
            ),
            business_group_basis=(
                None
                if business_group is None
                else "CONSOLIDATED_FINANCIALS"
            ),
            business_group_source_reference=(
                None
                if business_group is None
                else "https://example.in/group/source"
            ),
        )
        db.session.add(company)
        db.session.flush()
        return company

    return create_company


@pytest.fixture
def business_group_factory(app):
    def create_group(name: str) -> BusinessGroup:
        group = BusinessGroup(name=name)
        db.session.add(group)
        db.session.flush()
        return group

    return create_group


@pytest.fixture
def seeded_company(app, company_factory):
    company = company_factory(
        legal_name="IKIO Lighting Limited",
        symbol="IKIO",
        ticker_name="IKIO Lighting",
        isin="INE0LOJ01019",
        instrument_token=1001,
    )
    company.ticker.last_updated = _fresh_quote_time()
    db.session.flush()
    return company


@pytest.fixture
def query_tables(app):
    """Capture base table names touched by each SQL statement."""

    touched: list[frozenset[str]] = []

    def after_execute(
        conn, cursor, statement, parameters, context, executemany
    ):
        del conn, cursor, parameters, executemany
        tables = set()
        sql_text = statement if isinstance(statement, str) else ""
        for match in re.findall(
            r"\b(?:FROM|JOIN)\s+([A-Za-z_][A-Za-z0-9_]*)",
            sql_text,
            re.IGNORECASE,
        ):
            tables.add(match.lower())
        touched.append(frozenset(tables))

    sa.event.listen(db.engine, "after_cursor_execute", after_execute)

    yield touched

    sa.event.remove(db.engine, "after_cursor_execute", after_execute)


def test_query_service_exports_contract_symbols():
    assert callable(ResearchQueryService.list_companies)
    assert callable(ResearchQueryService.get_company_detail)
    assert tuple(field.name for field in fields(PageResult)) == (
        "items",
        "page",
        "per_page",
        "total_items",
        "total_pages",
    )


def test_list_companies_searches_legal_name_display_name_symbol_and_isin(
    app, company_factory
):
    legal = company_factory(
        legal_name="IKIO Lighting Limited",
        display_name=None,
        symbol="IKIO",
        ticker_name="IKIO Lighting",
        isin="INE0LOJ01019",
        instrument_token=1,
    )
    display = company_factory(
        legal_name="First Limited",
        display_name="Wipro Technologies",
        symbol="WIPRO",
        ticker_name="Wipro Info Tech",
        isin="INE075A01022",
        instrument_token=2,
    )
    symbol = company_factory(
        legal_name="Tata Steel Limited",
        symbol="TATASTEEL",
        ticker_name="Tata Steel",
        isin="INE081A01012",
        instrument_token=3,
    )
    isin_match = company_factory(
        legal_name="Reliance Industries Limited",
        symbol="RELIANCE",
        ticker_name="Reliance Industries",
        isin="US0378331005",
        instrument_token=4,
    )

    for text, expected_ids in (
        ("ikio", {legal.id}),
        ("wipro", {display.id}),
        ("TATASTEEL", {symbol.id}),
        ("us0378331005", {isin_match.id}),
        ("lighting", {legal.id}),
        ("limited", {legal.id, display.id, symbol.id, isin_match.id}),
        ("absent-company", set()),
    ):
        result = ResearchQueryService.list_companies(
            q=text,
            sector=None,
            industry=None,
            page=1,
            per_page=50,
            context=FREE_CONTEXT,
        )
        assert {item["company"]["id"] for item in result.items} == expected_ids


def test_list_companies_filters_sector_and_industry_exactly(
    app, company_factory
):
    electrical = company_factory(
        legal_name="IKIO Lighting Limited",
        symbol="IKIO",
        ticker_name="IKIO Lighting",
        isin="INE0LOJ01019",
        instrument_token=1,
        sector="Electricals",
        industry="Lighting",
    )
    steel = company_factory(
        legal_name="Tata Steel Limited",
        symbol="TATASTEEL",
        ticker_name="Tata Steel",
        isin="INE081A01012",
        instrument_token=2,
        sector="Metals",
        industry="Steel",
    )

    result = ResearchQueryService.list_companies(
        q=None,
        sector="Electricals",
        industry=None,
        page=1,
        per_page=50,
        context=FREE_CONTEXT,
    )
    assert [item["company"]["id"] for item in result.items] == [electrical.id]

    result = ResearchQueryService.list_companies(
        q=None,
        sector=None,
        industry="Steel",
        page=1,
        per_page=50,
        context=FREE_CONTEXT,
    )
    assert [item["company"]["id"] for item in result.items] == [steel.id]

    result = ResearchQueryService.list_companies(
        q=None,
        sector="electricals",
        industry=None,
        page=1,
        per_page=50,
        context=FREE_CONTEXT,
    )
    assert result.items == []
    assert result.total_items == 0

    result = ResearchQueryService.list_companies(
        q=None,
        sector="Metals",
        industry="Lighting",
        page=1,
        per_page=50,
        context=FREE_CONTEXT,
    )
    assert result.items == []


def test_list_companies_orders_by_display_label_then_id_and_paginates(
    app, company_factory, business_group_factory
):
    ikio = company_factory(
        legal_name="IKIO Lighting Limited",
        symbol="IKIO",
        ticker_name="IKIO Lighting",
        isin="INE0LOJ01019",
        instrument_token=1,
    )
    grouped = company_factory(
        legal_name="Tata Steel Limited",
        symbol="TATASTEEL",
        ticker_name="Tata Steel",
        isin="INE081A01012",
        instrument_token=2,
        business_group=business_group_factory("Tata Group"),
    )
    wipro = company_factory(
        legal_name="Wipro Limited",
        symbol="WIPRO",
        ticker_name="Wipro Limited",
        isin="INE075A01022",
        instrument_token=3,
    )

    first = ResearchQueryService.list_companies(
        q=None,
        sector=None,
        industry=None,
        page=1,
        per_page=2,
        context=FREE_CONTEXT,
    )
    second = ResearchQueryService.list_companies(
        q=None,
        sector=None,
        industry=None,
        page=2,
        per_page=2,
        context=FREE_CONTEXT,
    )

    first_names = [item["company"]["name"] for item in first.items]
    second_names = [item["company"]["name"] for item in second.items]
    assert first_names == ["IKIO Lighting Limited", "Tata Steel Limited"]
    assert second_names == ["Wipro Limited"]

    assert first.page == 1
    assert first.per_page == 2
    assert first.total_items == 3
    assert first.total_pages == 2
    assert second.page == 2
    assert second.per_page == 2
    assert second.total_items == 3
    assert second.total_pages == 2

    all_labels_and_ids = [
        (item["company"]["name"], item["company"]["id"])
        for item in list(first.items) + list(second.items)
    ]
    assert all_labels_and_ids == sorted(all_labels_and_ids)
    assert all_labels_and_ids[1][1] == grouped.id
    assert {ikio.id, grouped.id, wipro.id} == {
        company_id for _label, company_id in all_labels_and_ids
    }

    grouped_item = next(
        item
        for item in first.items
        if item["company"]["id"] == grouped.id
    )
    assert grouped_item["business_group"] == {
        "name": "Tata Group",
        "notes": None,
        "source_reference": None,
    }
    assert grouped_item["market_quote"]["cmp"] == 123.45
    assert grouped_item["market_quote"]["as_of"] is not None


def test_list_companies_validates_page_bounds_and_returns_page_outside_page(
    app, seeded_company
):
    invalid_calls = [
        {"page": 0, "per_page": 10},
        {"page": -1, "per_page": 10},
        {"page": 1, "per_page": 0},
        {"page": 1, "per_page": -1},
        {"page": 1, "per_page": 101},
    ]
    for invalid in invalid_calls:
        with pytest.raises(ResearchValidationError) as exc_info:
            ResearchQueryService.list_companies(
                q=None,
                sector=None,
                industry=None,
                context=FREE_CONTEXT,
                **invalid,
            )
        assert exc_info.value.code == "validation_error"
        assert exc_info.value.details

    outside = ResearchQueryService.list_companies(
        q=None,
        sector=None,
        industry=None,
        page=2,
        per_page=10,
        context=FREE_CONTEXT,
    )
    assert outside.items == []
    assert outside.page == 2
    assert outside.total_items == 1
    assert outside.total_pages == 1


def test_get_company_detail_builds_market_quote_and_optional_business_group(
    app, seeded_company
):
    aggregate = ResearchQueryService.get_company_detail(
        seeded_company.id, FREE_CONTEXT
    )

    assert aggregate["company"] == _company_payload(seeded_company)
    assert aggregate["market_quote"] == _quote(seeded_company.ticker)
    assert "business_group" not in aggregate
    assert PREMIUM_DETAIL_KEYS.isdisjoint(aggregate)
    assert aggregate["access_input"] == {
        "user_id": FREE_CONTEXT.user_id,
        "is_admin": False,
        "tier": ResearchTier.FREE,
    }


def test_get_company_detail_raises_typed_error_for_unknown_company(app):
    with pytest.raises(ResearchNotFoundError) as exc_info:
        ResearchQueryService.get_company_detail(
            "missing-company", FREE_CONTEXT
        )
    assert exc_info.value.code == "company_not_found"
    assert exc_info.value.message == "Company was not found"


def test_detail_selects_current_research_revision_by_highest_number(
    app, seeded_company, admin_user
):
    company_id = seeded_company.id
    actor = admin_user.id
    first = _research_revision(company_id, actor, 1)
    second = _research_revision(company_id, actor, 2, first.id)
    _research_revision(company_id, actor, 3, second.id)

    aggregate = ResearchQueryService.get_company_detail(
        seeded_company.id, PREMIUM_CONTEXT
    )

    assert aggregate["research"]["revision"] == 3
    assert aggregate["research"]["thesis"] == "Thesis 3"
    catalysts = aggregate["research"]["catalysts"]
    risks = aggregate["research"]["risks"]
    assert len(catalysts) == 1
    assert catalysts[0]["kind"] == ResearchPointKind.CATALYST
    assert catalysts[0]["title"] == "Catalyst 3"
    assert catalysts[0]["sort_order"] == 0
    assert catalysts[0]["detail"] is None
    assert catalysts[0]["status"] is None
    assert catalysts[0]["target_date"] is None
    assert len(risks) == 1
    assert risks[0]["kind"] == ResearchPointKind.RISK
    assert risks[0]["title"] == "Risk 3"
    assert risks[0]["sort_order"] == 0
    assert aggregate["management"]["summary"] == f"Management 3"
    assert aggregate["management"]["quality"] == ManagementQuality.WATCH
    assert aggregate["management"]["rationale"] == f"Rationale 3"
    assert aggregate["management"]["evidence"] == f"Evidence 3"
    assert aggregate["governance"]["status"] == GovernanceStatus.WATCH


def test_detail_selects_current_valuation_independently_per_method(
    app, seeded_company, admin_user
):
    company_id = seeded_company.id
    actor = admin_user.id
    pe_1 = _valuation(company_id, actor, ValuationMethod.PE, 1)
    pe_2 = _valuation(company_id, actor, ValuationMethod.PE, 2, pe_1.id)
    ev_1 = _valuation(
        company_id, actor, ValuationMethod.EV_EBITDA, 1
    )
    _valuation(
        company_id,
        actor,
        ValuationMethod.EV_EBITDA,
        2,
        ev_1.id,
    )
    _reference_line(pe_2.id, 0)
    _reference_line(pe_2.id, 1, "NET_DEBT", Decimal("75.0000"))

    aggregate = ResearchQueryService.get_company_detail(
        seeded_company.id, PREMIUM_CONTEXT
    )

    valuations = aggregate["valuations"]
    assert len(valuations) == 2
    by_method = {item["valuation_method"]: item for item in valuations}
    assert by_method[ValuationMethod.PE]["revision"] == 2
    assert (
        by_method[ValuationMethod.PE]["valuation_notes"]
        == "PE valuation 2"
    )
    assert [
        line["reference_metric"]
        for line in by_method[ValuationMethod.PE]["reference_lines"]
    ] == ["EPS", "NET_DEBT"]
    assert by_method[ValuationMethod.EV_EBITDA]["revision"] == 2
    assert (
        by_method[ValuationMethod.EV_EBITDA]["valuation_notes"]
        == "EV_EBITDA valuation 2"
    )
    assert (
        by_method[ValuationMethod.EV_EBITDA]["reference_lines"]
        == []
    )


def test_detail_selects_latest_ownership_by_date_and_only_active_governance(
    app, seeded_company, admin_user
):
    company_id = seeded_company.id
    actor = admin_user.id
    db.session.add(
        OwnershipSnapshot(
            company_id=company_id,
            as_of_date=date(2026, 8, 4),
            promoter_holding_pct=Decimal("62.3500"),
            promoter_pledge_pct=None,
            notes="Older snapshot",
            source_reference="https://example.in/ownership/1",
            created_by_user_id=actor,
        )
    )
    db.session.add(
        OwnershipSnapshot(
            company_id=company_id,
            as_of_date=date(2026, 9, 4),
            promoter_holding_pct=Decimal("60.1000"),
            promoter_pledge_pct=Decimal("2.5000"),
            notes="Latest snapshot",
            source_reference="https://example.in/ownership/2",
            created_by_user_id=actor,
        )
    )
    active = _governance_flag(company_id, actor, title="Active flag")
    archived = _governance_flag(company_id, actor, title="Archived flag")
    archived.archived_at = FIXED_NOW
    db.session.flush()

    aggregate = ResearchQueryService.get_company_detail(
        seeded_company.id, PREMIUM_CONTEXT
    )

    ownership = aggregate["ownership"]
    assert ownership["as_of_date"] == date(2026, 9, 4)
    assert ownership["promoter_holding_pct"] == Decimal("60.1000")
    assert ownership["promoter_pledge_pct"] == Decimal("2.5000")
    assert ownership["notes"] == "Latest snapshot"
    assert ownership["source_reference"] == "https://example.in/ownership/2"

    assert [flag["id"] for flag in aggregate["governance"]["flags"]] == [
        active.id
    ]
    assert archived.id not in {
        flag["id"] for flag in aggregate["governance"]["flags"]
    }


def test_detail_includes_current_market_plan_forecast_and_valuations(
    app, seeded_company, admin_user
):
    company_id = seeded_company.id
    actor = admin_user.id
    mp_1 = _market_plan(company_id, actor, 1)
    _market_plan(company_id, actor, 2, mp_1.id)
    fc_1 = _forecast(company_id, actor, 1)
    fc_2 = _forecast(company_id, actor, 2, fc_1.id)
    _forecast_line(fc_2.id, 2027)
    _forecast_line(fc_2.id, 2028)
    valuation = _valuation(company_id, actor, ValuationMethod.NAV, 1)
    _reference_line(valuation.id, 0, "NAV", Decimal("100.0000"))

    aggregate = ResearchQueryService.get_company_detail(
        seeded_company.id, PREMIUM_CONTEXT
    )

    assert aggregate["market_plan"]["revision"] == 2
    assert aggregate["market_plan"]["accumulation_low"] == Decimal("95.0000")
    assert aggregate["market_plan"]["accumulation_high"] == Decimal("120.0000")
    assert aggregate["market_plan"]["invalidation_level"] == Decimal("85.0000")
    assert aggregate["market_plan"]["effective_at"] == FIXED_NOW

    assert aggregate["forecast"]["revision"] == 2
    assert aggregate["forecast"]["assumptions"] == "Forecast 2"
    assert [line["fiscal_year"] for line in aggregate["forecast"]["lines"]] == [
        2027,
        2028,
    ]
    assert (
        aggregate["forecast"]["lines"][0]["revenue"] == Decimal("100.0000")
    )

    assert aggregate["valuations"][0]["revision"] == 1
    assert aggregate["valuations"][0]["valuation_method"] == "NAV"
    assert [
        line["reference_metric"]
        for line in aggregate["valuations"][0]["reference_lines"]
    ] == ["NAV"]


def test_premium_detail_present_null_known_missing_and_admin_allows_all(
    app, seeded_company
):
    premium = ResearchQueryService.get_company_detail(
        seeded_company.id, PREMIUM_CONTEXT
    )
    admin = ResearchQueryService.get_company_detail(
        seeded_company.id, ADMIN_CONTEXT
    )

    for aggregate in (premium, admin):
        assert PREMIUM_DETAIL_KEYS.issubset(aggregate)
        assert aggregate["research"] is None
        assert aggregate["management"] is None
        assert aggregate["governance"] == {"status": None, "flags": []}
        assert aggregate["ownership"] is None
        assert aggregate["market_plan"] is None
        assert aggregate["forecast"] is None
        assert aggregate["valuations"] == []


def test_free_queries_never_touch_or_return_premium_relationships(
    app, seeded_company, admin_user, query_tables
):
    company_id = seeded_company.id
    actor = admin_user.id
    research = _research_revision(company_id, actor, 1)
    db.session.add(
        OwnershipSnapshot(
            company_id=company_id,
            as_of_date=AS_OF_DATE,
            promoter_holding_pct=Decimal("62.3500"),
            promoter_pledge_pct=None,
            notes="Premium ownership",
            source_reference="https://example.in/ownership",
            created_by_user_id=actor,
        )
    )
    _governance_flag(company_id, actor, title="Premium flag")
    _market_plan(company_id, actor, 1)
    forecast = _forecast(company_id, actor, 1)
    _forecast_line(forecast.id, 2027)
    valuation = _valuation(company_id, actor, ValuationMethod.PE, 1)
    _reference_line(valuation.id, 0, "EPS", Decimal("5.0000"))
    db.session.commit()

    detail = ResearchQueryService.get_company_detail(
        seeded_company.id, FREE_CONTEXT
    )
    listing = ResearchQueryService.list_companies(
        q="IKIO",
        sector=None,
        industry=None,
        page=1,
        per_page=10,
        context=FREE_CONTEXT,
    )

    assert PREMIUM_DETAIL_KEYS.isdisjoint(detail)
    assert all(PREMIUM_DETAIL_KEYS.isdisjoint(item) for item in listing.items)
    assert not any(
        PREMIUM_TABLE_NAMES.intersection(tables) for tables in query_tables
    )
