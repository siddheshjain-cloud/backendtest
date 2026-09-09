"""Plan 3 Task 5: disclosure collections and immutable history query tests.

These tests lock the frozen ``ResearchQueryService`` collection contract:
SQL-applied disclosure filters, deterministic newest/oldest ordering with
``event_date``, ``created_at``, and ID tie-breakers, archived-row exclusion,
free-disclosure omission of ``significance_note``, and premium/admin-only
immutable revision history with section and valuation-method validation.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from app import db
from app.models import (
    Company,
    CompanyDisclosure,
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
    ResearchTier,
    ValuationMethod,
)
from app.services.entitlement_service import ResearchAccessContext
from app.services.research_query_service import (
    PageResult,
    ResearchQueryService,
)
from app.utils.research_errors import (
    ResearchForbiddenError,
    ResearchNotFoundError,
    ResearchValidationError,
)


UTC = timezone.utc
FIXED_NOW = datetime(2026, 9, 10, 12, 0, tzinfo=UTC)

FREE_CONTEXT = ResearchAccessContext("user-free", False, ResearchTier.FREE)
PREMIUM_CONTEXT = ResearchAccessContext(
    "user-premium", False, ResearchTier.PREMIUM
)
ADMIN_CONTEXT = ResearchAccessContext("user-admin", True, ResearchTier.FREE)


def _utc(day: int, hour: int = 12) -> datetime:
    return datetime(2026, 9, day, hour, tzinfo=UTC)


@pytest.fixture
def company(app, ticker_factory) -> object:
    ticker = ticker_factory(
        symbol="IKIO",
        instrument_token=1,
        exchange="NSE",
        name="IKIO Technologies Limited",
        last_price=123.45,
    )
    ticker.last_updated = FIXED_NOW
    db.session.flush()

    row = Company(
        ticker_id=ticker.id,
        legal_name="IKIO Lighting Limited",
        display_name=None,
        isin="INE0LOJ01019",
        sector="Industrials",
        industry="LED lighting",
        business_group_id=None,
        business_group_basis=None,
        business_group_source_reference=None,
    )
    db.session.add(row)
    db.session.flush()
    return row


def _disclosure(
    company_id: str,
    actor_user_id: str,
    *,
    disclosure_id: str,
    event_type: str,
    event_date: date,
    created_at: datetime,
    is_key: bool = False,
    significance_note: str | None = None,
    archived_at: datetime | None = None,
) -> CompanyDisclosure:
    row = CompanyDisclosure(
        id=disclosure_id,
        company_id=company_id,
        event_type=event_type,
        event_date=event_date,
        title=f"Disclosure {disclosure_id}",
        original_source_url_or_reference=(
            f"https://exchange.example/ref/{disclosure_id}"
        ),
        exchange_reference="BSE:REFERENCE",
        significance_note=significance_note,
        is_key=is_key,
        document_id=None,
        created_by_user_id=actor_user_id,
        created_at=created_at,
        archived_at=archived_at,
    )
    db.session.add(row)
    db.session.flush()
    return row


def _seed_key_reg30_disclosures(
    company_id: str, actor_user_id: str
) -> list[CompanyDisclosure]:
    """Seven live plus one archived key Reg. 30 disclosure."""

    _disclosure(
        company_id,
        actor_user_id,
        disclosure_id="reg-a",
        event_type="REG30",
        event_date=date(2026, 9, 7),
        created_at=_utc(7, 7),
        is_key=True,
        archived_at=FIXED_NOW,
    )
    rows = [
        _disclosure(
            company_id,
            actor_user_id,
            disclosure_id=f"key-{label}",
            event_type="REG30",
            event_date=event_date,
            created_at=created_at,
            is_key=True,
            significance_note=(
                None if label == "1" else f"Analytical note {label}"
            ),
        )
        for label, event_date, created_at in (
            ("1", date(2026, 9, 6), _utc(6, 6)),
            ("2", date(2026, 9, 5), _utc(5, 5)),
            ("3", date(2026, 9, 4), _utc(4, 12)),
            ("4", date(2026, 9, 4), _utc(4, 9)),
            ("5", date(2026, 9, 4), _utc(4, 9)),
            ("6", date(2026, 9, 3), _utc(3, 3)),
            ("7", date(2026, 9, 2), _utc(2, 2)),
        )
    ]
    db.session.commit()
    return rows


def test_collection_query_contract_symbols_exist():
    assert callable(ResearchQueryService.list_disclosures)
    assert callable(ResearchQueryService.get_history)
    assert tuple(field.name for field in PageResult.__dataclass_fields__.values()) == (
        "items",
        "page",
        "per_page",
        "total_items",
        "total_pages",
    )


def test_list_disclosures_returns_deterministic_latest_five_key_reg30(
    app, company, admin_user
):
    rows = _seed_key_reg30_disclosures(company.id, admin_user.id)

    first = ResearchQueryService.list_disclosures(
        company.id,
        event_type="REG30",
        is_key=True,
        date_from=None,
        date_to=None,
        newest_first=True,
        page=1,
        per_page=5,
        context=PREMIUM_CONTEXT,
    )
    second = ResearchQueryService.list_disclosures(
        company.id,
        event_type="REG30",
        is_key=True,
        date_from=None,
        date_to=None,
        newest_first=True,
        page=2,
        per_page=5,
        context=PREMIUM_CONTEXT,
    )

    by_id = {row.id: row for row in rows}
    assert [item["id"] for item in first.items] == [
        "key-1",
        "key-2",
        "key-3",
        "key-5",
        "key-4",
    ]
    assert [item["id"] for item in second.items] == ["key-6", "key-7"]
    assert "reg-a" not in {item["id"] for item in first.items}
    assert "reg-a" not in {item["id"] for item in second.items}
    assert first.page == 1
    assert first.per_page == 5
    assert first.total_items == 7
    assert first.total_pages == 2

    for item in first.items + second.items:
        assert item["event_type"] == "REG30"
        assert item["is_key"] is True
        assert item["event_date"] == by_id[item["id"]].event_date
        assert item["title"] == by_id[item["id"]].title
        assert (
            item["original_source_url_or_reference"]
            == by_id[item["id"]].original_source_url_or_reference
        )


def test_list_disclosures_applies_date_type_is_key_and_order_filters(
    app, company, admin_user
):
    _disclosure(
        company.id,
        admin_user.id,
        disclosure_id="date-1",
        event_type="REG30",
        event_date=date(2026, 9, 6),
        created_at=_utc(6, 6),
        is_key=True,
    )
    _disclosure(
        company.id,
        admin_user.id,
        disclosure_id="date-2",
        event_type="REG30",
        event_date=date(2026, 9, 5),
        created_at=_utc(5, 5),
        is_key=False,
    )
    _disclosure(
        company.id,
        admin_user.id,
        disclosure_id="date-3",
        event_type="REG30",
        event_date=date(2026, 9, 4),
        created_at=_utc(4, 4),
        is_key=True,
    )
    _disclosure(
        company.id,
        admin_user.id,
        disclosure_id="date-4",
        event_type="ANNOUNCEMENT",
        event_date=date(2026, 9, 3),
        created_at=_utc(3, 3),
        is_key=True,
    )
    db.session.commit()

    newest = ResearchQueryService.list_disclosures(
        company.id,
        event_type=None,
        is_key=None,
        date_from=date(2026, 9, 4),
        date_to=date(2026, 9, 6),
        newest_first=True,
        page=1,
        per_page=10,
        context=FREE_CONTEXT,
    )
    assert [item["id"] for item in newest.items] == [
        "date-1",
        "date-2",
        "date-3",
    ]
    assert newest.total_items == 3

    oldest = ResearchQueryService.list_disclosures(
        company.id,
        event_type="REG30",
        is_key=True,
        date_from=None,
        date_to=None,
        newest_first=False,
        page=1,
        per_page=10,
        context=FREE_CONTEXT,
    )
    assert [item["id"] for item in oldest.items] == [
        "date-3",
        "date-1",
    ]
    assert oldest.total_items == 2


def test_free_disclosure_items_omit_significance_note(
    app, company, admin_user
):
    sentinel = "SENTINEL-FREE-DISCLOSURE-SIGNIFICANCE"
    _disclosure(
        company.id,
        admin_user.id,
        disclosure_id="notes-1",
        event_type="REG30",
        event_date=date(2026, 9, 4),
        created_at=_utc(4, 4),
        is_key=True,
        significance_note=sentinel,
    )
    db.session.commit()

    free = ResearchQueryService.list_disclosures(
        company.id,
        event_type=None,
        is_key=None,
        date_from=None,
        date_to=None,
        newest_first=True,
        page=1,
        per_page=10,
        context=FREE_CONTEXT,
    )
    premium = ResearchQueryService.list_disclosures(
        company.id,
        event_type=None,
        is_key=None,
        date_from=None,
        date_to=None,
        newest_first=True,
        page=1,
        per_page=10,
        context=PREMIUM_CONTEXT,
    )
    admin = ResearchQueryService.list_disclosures(
        company.id,
        event_type=None,
        is_key=None,
        date_from=None,
        date_to=None,
        newest_first=True,
        page=1,
        per_page=10,
        context=ADMIN_CONTEXT,
    )

    assert all(
        "significance_note" not in item for item in free.items
    )
    assert all(
        item["significance_note"] == sentinel for item in premium.items
    )
    assert all(
        item["significance_note"] == sentinel for item in admin.items
    )


def test_list_disclosures_raises_typed_errors_for_unknown_company_and_filters(
    app, company
):
    with pytest.raises(ResearchNotFoundError) as exc_info:
        ResearchQueryService.list_disclosures(
            "missing-company",
            event_type=None,
            is_key=None,
            date_from=None,
            date_to=None,
            newest_first=True,
            page=1,
            per_page=10,
            context=FREE_CONTEXT,
        )
    assert exc_info.value.code == "company_not_found"
    assert exc_info.value.message == "Company was not found"

    invalid_calls = [
        {
            "page": 0,
            "per_page": 10,
            "date_from": None,
            "date_to": None,
            "is_key": None,
            "newest_first": True,
        },
        {
            "page": 1,
            "per_page": 101,
            "date_from": None,
            "date_to": None,
            "is_key": None,
            "newest_first": True,
        },
        {
            "page": 1,
            "per_page": 10,
            "date_from": date(2026, 9, 5),
            "date_to": date(2026, 9, 4),
            "is_key": None,
            "newest_first": True,
        },
        {
            "page": 1,
            "per_page": 10,
            "date_from": None,
            "date_to": None,
            "is_key": "yes",
            "newest_first": True,
        },
        {
            "page": 1,
            "per_page": 10,
            "date_from": None,
            "date_to": None,
            "is_key": None,
            "newest_first": None,
        },
    ]
    for invalid in invalid_calls:
        with pytest.raises(ResearchValidationError) as exc_info:
            ResearchQueryService.list_disclosures(
                company.id,
                event_type=None,
                context=FREE_CONTEXT,
                **invalid,
            )
        assert exc_info.value.code == "validation_error"
        assert exc_info.value.details


def _research_revision(
    company_id: str,
    actor_user_id: str,
    revision_number: int,
) -> ResearchRevision:
    revision = ResearchRevision(
        company_id=company_id,
        revision_number=revision_number,
        supersedes_revision_id=None,
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
        effective_at=_utc(4 + revision_number),
        created_by_user_id=actor_user_id,
        created_at=_utc(4 + revision_number, 1),
    )
    db.session.add(revision)
    revision.points.append(
        ResearchPoint(
            kind=ResearchPointKind.CATALYST,
            title=f"Catalyst {revision_number}",
            sort_order=0,
        )
    )
    db.session.flush()
    return revision


def _market_plan_revision(
    company_id: str,
    actor_user_id: str,
    revision_number: int,
) -> MarketPlanRevision:
    revision = MarketPlanRevision(
        company_id=company_id,
        revision_number=revision_number,
        supersedes_revision_id=None,
        currency="INR",
        accumulation_low=Decimal("100.0000"),
        accumulation_high=Decimal("130.0000"),
        preferred_accumulation_price=None,
        supply_low=None,
        supply_high=None,
        invalidation_level=Decimal("90.0000"),
        rationale=f"Market plan {revision_number}",
        effective_at=_utc(4 + revision_number),
        change_reason=(
            None if revision_number == 1 else f"Change {revision_number}"
        ),
        created_by_user_id=actor_user_id,
        created_at=_utc(4 + revision_number, 1),
    )
    db.session.add(revision)
    db.session.flush()
    return revision


def _forecast_revision(
    company_id: str,
    actor_user_id: str,
    revision_number: int,
) -> ForecastRevision:
    revision = ForecastRevision(
        company_id=company_id,
        revision_number=revision_number,
        supersedes_revision_id=None,
        as_of_date=date(2026, 9, 4),
        assumptions=f"Forecast {revision_number}",
        change_reason=(
            None if revision_number == 1 else f"Change {revision_number}"
        ),
        created_by_user_id=actor_user_id,
        created_at=_utc(4 + revision_number, 1),
    )
    db.session.add(revision)
    db.session.flush()
    db.session.add(
        ForecastLine(
            forecast_revision_id=revision.id,
            fiscal_year=2027,
            is_estimate=True,
            revenue=Decimal("100.0000"),
            ebitda=Decimal("20.0000"),
            ebitda_margin_pct=Decimal("20.0000"),
            pat=Decimal("10.0000"),
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
    method: str,
    revision_number: int,
) -> ValuationRevision:
    revision = ValuationRevision(
        company_id=company_id,
        valuation_method=method,
        revision_number=revision_number,
        supersedes_revision_id=None,
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
        valuation_notes=f"{method} valuation {revision_number}",
        as_of_date=date(2026, 9, 4),
        change_reason=(
            None if revision_number == 1 else f"Change {revision_number}"
        ),
        created_by_user_id=actor_user_id,
        created_at=_utc(4 + revision_number, 1),
    )
    db.session.add(revision)
    db.session.flush()
    db.session.add(
        ValuationReferenceLine(
            valuation_revision_id=revision.id,
            reference_forecast_revision_id=None,
            reference_fiscal_year=None,
            reference_metric="EPS",
            reference_metric_value=Decimal("5.0000"),
            reference_metric_unit="INR_PER_SHARE",
            reference_metric_basis="Administrator snapshot",
            sort_order=0,
        )
    )
    db.session.flush()
    return revision


def test_get_history_research_paginates_for_premium_and_admin(
    app, company, admin_user
):
    _research_revision(company.id, admin_user.id, 1)
    _research_revision(company.id, admin_user.id, 2)
    _research_revision(company.id, admin_user.id, 3)
    db.session.commit()

    premium_pages = [
        ResearchQueryService.get_history(
            company.id,
            section="research",
            valuation_method=None,
            page=1,
            per_page=2,
            context=PREMIUM_CONTEXT,
        ),
        ResearchQueryService.get_history(
            company.id,
            section="research",
            valuation_method=None,
            page=2,
            per_page=2,
            context=PREMIUM_CONTEXT,
        ),
    ]
    admin_result = ResearchQueryService.get_history(
        company.id,
        section="research",
        valuation_method=None,
        page=1,
        per_page=10,
        context=ADMIN_CONTEXT,
    )

    assert [item["revision"] for page in premium_pages for item in page.items] == [
        1,
        2,
        3,
    ]
    assert premium_pages[0].total_items == 3
    assert premium_pages[0].total_pages == 2
    assert premium_pages[1].items[0]["revision"] == 3
    assert [item["revision"] for item in admin_result.items] == [1, 2, 3]
    for item in premium_pages[0].items + admin_result.items:
        assert item["thesis"].startswith("Thesis ")
        assert "catalysts" in item
        assert "created_at" in item
        assert "effective_at" in item


def test_get_history_supports_market_and_forecast_sections(
    app, company, admin_user
):
    _market_plan_revision(company.id, admin_user.id, 1)
    _market_plan_revision(company.id, admin_user.id, 2)
    _forecast_revision(company.id, admin_user.id, 1)
    _forecast_revision(company.id, admin_user.id, 2)
    db.session.commit()

    market = ResearchQueryService.get_history(
        company.id,
        section="market",
        valuation_method=None,
        page=1,
        per_page=10,
        context=PREMIUM_CONTEXT,
    )
    forecast = ResearchQueryService.get_history(
        company.id,
        section="forecast",
        valuation_method=None,
        page=1,
        per_page=10,
        context=PREMIUM_CONTEXT,
    )

    assert [item["revision"] for item in market.items] == [1, 2]
    assert market.items[0]["accumulation_low"] == Decimal("100.0000")
    assert [item["revision"] for item in forecast.items] == [1, 2]
    assert forecast.items[0]["lines"][0]["fiscal_year"] == 2027


def test_get_history_valuation_filters_method_and_validates_sections(
    app, company, admin_user
):
    _valuation_revision(
        company.id, admin_user.id, ValuationMethod.PE, 1
    )
    _valuation_revision(
        company.id, admin_user.id, ValuationMethod.PE, 2
    )
    _valuation_revision(
        company.id, admin_user.id, ValuationMethod.NAV, 1
    )
    db.session.commit()

    pe_only = ResearchQueryService.get_history(
        company.id,
        section="valuation",
        valuation_method=ValuationMethod.PE,
        page=1,
        per_page=10,
        context=PREMIUM_CONTEXT,
    )
    all_methods = ResearchQueryService.get_history(
        company.id,
        section="valuation",
        valuation_method=None,
        page=1,
        per_page=10,
        context=PREMIUM_CONTEXT,
    )

    assert [item["revision"] for item in pe_only.items] == [1, 2]
    assert pe_only.total_items == 2
    assert all(
        item["valuation_method"] == ValuationMethod.PE
        for item in pe_only.items
    )
    assert [
        (item["valuation_method"], item["revision"])
        for item in all_methods.items
    ] == [
        (ValuationMethod.NAV, 1),
        (ValuationMethod.PE, 1),
        (ValuationMethod.PE, 2),
    ]


def test_get_history_rejects_free_context_with_typed_forbidden_error(
    app, company, admin_user
):
    _research_revision(company.id, admin_user.id, 1)
    _research_revision(company.id, admin_user.id, 2)
    db.session.commit()

    with pytest.raises(ResearchForbiddenError) as exc_info:
        ResearchQueryService.get_history(
            company.id,
            section="research",
            valuation_method=None,
            page=1,
            per_page=10,
            context=FREE_CONTEXT,
        )
    assert exc_info.value.code == "history_forbidden"
    assert "premium" in exc_info.value.message.lower()


def test_get_history_validates_section_method_and_unknown_company(
    app, company, admin_user
):
    _research_revision(company.id, admin_user.id, 1)
    _valuation_revision(
        company.id, admin_user.id, ValuationMethod.PE, 1
    )
    db.session.commit()

    with pytest.raises(ResearchValidationError) as exc_info:
        ResearchQueryService.get_history(
            company.id,
            section="documents",
            valuation_method=None,
            page=1,
            per_page=10,
            context=PREMIUM_CONTEXT,
        )
    assert exc_info.value.code == "validation_error"
    assert "section" in exc_info.value.details

    with pytest.raises(ResearchValidationError) as exc_info:
        ResearchQueryService.get_history(
            company.id,
            section="research",
            valuation_method=ValuationMethod.PE,
            page=1,
            per_page=10,
            context=PREMIUM_CONTEXT,
        )
    assert exc_info.value.code == "validation_error"
    assert "valuation_method" in exc_info.value.details

    with pytest.raises(ResearchValidationError) as exc_info:
        ResearchQueryService.get_history(
            company.id,
            section="valuation",
            valuation_method="NOT_A_METHOD",
            page=1,
            per_page=10,
            context=PREMIUM_CONTEXT,
        )
    assert exc_info.value.code == "validation_error"
    assert "valuation_method" in exc_info.value.details

    with pytest.raises(ResearchNotFoundError) as exc_info:
        ResearchQueryService.get_history(
            "missing-company",
            section="research",
            valuation_method=None,
            page=1,
            per_page=10,
            context=PREMIUM_CONTEXT,
        )
    assert exc_info.value.code == "company_not_found"
