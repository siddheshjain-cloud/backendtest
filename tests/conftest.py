import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
from flask_jwt_extended import create_access_token

from app import create_app, db
from app.models.company import BusinessGroup, Company
from app.models.disclosure import CompanyDisclosure
from app.models.entitlement import (
    INVESTMENT_RESEARCH_PRODUCT_CODE,
    UserEntitlement,
)
from app.models.forecast import ForecastLine, ForecastRevision
from app.models.governance import GovernanceFlag
from app.models.market_plan import MarketPlanRevision
from app.models.ownership import OwnershipSnapshot
from app.models.research import ResearchPoint, ResearchRevision
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
from app.models.ticker import Ticker
from app.models.trade import Trade
from app.models.user import User
from app.models.utils import TradeSide, TradeStatus, TradeTimeframe, TradeType
from app.models.valuation import ValuationReferenceLine, ValuationRevision
from app.services.entitlement_service import EntitlementService
from config import Config


class TestingConfig(Config):
    TESTING = True
    JWT_SECRET_KEY = "test-jwt-secret"
    ELASTICSEARCH_URL = None


@pytest.fixture
def app(tmp_path):
    TestingConfig.SQLALCHEMY_DATABASE_URI = (
        f"sqlite:///{(tmp_path / f'test-{uuid.uuid4()}.db').as_posix()}"
    )
    application = create_app(TestingConfig)

    with application.app_context():
        import app.models

        db.create_all()
        yield application
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def user_factory(app):
    def create_user(*, email: str, is_admin: bool = False) -> User:
        user = User(name=email.split("@")[0], email=email, is_admin=is_admin)
        user.set_password("test-password")
        db.session.add(user)
        db.session.commit()
        return user

    return create_user


@pytest.fixture
def ticker_factory(app):
    def create_ticker(
        *,
        symbol: str = "IKIO",
        instrument_token: int = 1,
        exchange: str = "NSE",
        name: str = "IKIO Technologies Limited",
        last_price: float = 100.0,
    ) -> Ticker:
        ticker = Ticker(
            symbol=symbol,
            instrument_token=instrument_token,
            exchange=exchange,
            name=name,
            last_price=last_price,
        )
        db.session.add(ticker)
        db.session.commit()
        return ticker

    return create_ticker


@pytest.fixture
def trade_factory(app):
    def create_trade(
        *,
        user: User,
        ticker: Ticker,
        side: str = TradeSide.BUY,
        trade_type: str = TradeType.CROSSING_ABOVE,
        status: str = TradeStatus.ACTIVE,
        entry: float = 101.0,
        stoploss: float | None = 99.0,
        target: float | None = 105.0,
        notes: str = "",
        timeframe: str = TradeTimeframe.DAY,
    ) -> Trade:
        trade = Trade(
            symbol=ticker.symbol,
            side=side,
            type=trade_type,
            status=status,
            entry=entry,
            stoploss=stoploss,
            target=target,
            notes=notes,
            timeframe=timeframe,
            user_id=user.id,
            ticker_id=ticker.id,
        )
        db.session.add(trade)
        db.session.commit()
        return trade

    return create_trade


@pytest.fixture
def candle_factory():
    def create_candle(*, high: float = 100.0, low: float = 100.0):
        return SimpleNamespace(high=high, low=low)

    return create_candle


@pytest.fixture
def auth_headers(app):
    def make_headers(user: User) -> dict[str, str]:
        with app.app_context():
            return {"Authorization": f"Bearer {create_access_token(identity=user.id)}"}

    return make_headers


@pytest.fixture
def admin_user(user_factory):
    return user_factory(email="admin@example.com", is_admin=True)


@pytest.fixture
def free_user(user_factory):
    return user_factory(email="free@example.com")


@pytest.fixture
def premium_user(user_factory):
    user = user_factory(email="premium@example.com")
    db.session.add(
        UserEntitlement(
            user_id=user.id,
            product_code=INVESTMENT_RESEARCH_PRODUCT_CODE,
            tier=ResearchTier.PREMIUM,
            status=EntitlementStatus.ACTIVE,
        )
    )
    db.session.commit()
    return user


@pytest.fixture
def research_security_matrix(app):
    """One shared, release-blocking research isolation matrix.

    The fixture seeds a complete premium company record and returns the same
    entitlement scenarios that Plan 3 uses directly and Plan 5 API tests can
    reuse.

    Every scenario owns literal, independently-defined expectations (expected
    context tier, admin flag, access tier, projection kind, and locked-section
    metadata) that are never derived from ``EntitlementService``,
    ``ResearchAccessPolicy``, or any other production helper under test. Each
    scenario also keeps the resolved access context, which is the *input*
    under test, plus a JWT whose tier/admin claims are intentionally stale so
    the database remains the only tier authority.

    An out-of-enum stored tier is deliberately not a scenario: the
    ``user_entitlement.tier`` column rejects values outside FREE/PREMIUM at
    the database/ORM boundary, so such a row cannot even be loaded and no
    resolver scenario can honestly exist for it.
    """

    fixed_now = datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc)
    as_of_date = date(2026, 9, 4)

    # Literal free-caller locked-section contract, written out in the
    # deterministic order the response must use.
    free_locked_sections = [
        {"section": "disclosure_significance", "required_tier": "PREMIUM"},
        {"section": "forecast", "required_tier": "PREMIUM"},
        {"section": "governance", "required_tier": "PREMIUM"},
        {"section": "history", "required_tier": "PREMIUM"},
        {"section": "management", "required_tier": "PREMIUM"},
        {"section": "market_plan", "required_tier": "PREMIUM"},
        {"section": "ownership", "required_tier": "PREMIUM"},
        {"section": "research", "required_tier": "PREMIUM"},
        {"section": "valuation", "required_tier": "PREMIUM"},
    ]

    def _make_user(email: str, *, is_admin: bool = False) -> User:
        user = User(name=email.split("@")[0], email=email, is_admin=is_admin)
        user.set_password("test-password")
        db.session.add(user)
        db.session.flush()
        return user

    def _add_entitlement(
        user: User,
        *,
        tier: str,
        status: str,
        valid_from: datetime | None = None,
        valid_until: datetime | None = None,
    ) -> None:
        db.session.add(
            UserEntitlement(
                user_id=user.id,
                product_code=INVESTMENT_RESEARCH_PRODUCT_CODE,
                tier=tier,
                status=status,
                valid_from=valid_from,
                valid_until=valid_until,
            )
        )
        db.session.flush()

    actor = _make_user("research-actor@example.com", is_admin=True)

    ticker = Ticker(
        symbol="IKIO",
        instrument_token=1001,
        exchange="NSE",
        name="IKIO Technologies Limited",
        last_price=123.45,
        last_updated=fixed_now,
    )
    db.session.add(ticker)
    db.session.flush()

    business_group = BusinessGroup(
        name="IKIO Promoter Group",
        notes="public group context",
        source_reference="https://example.in/group/source",
    )
    db.session.add(business_group)
    db.session.flush()

    company = Company(
        ticker_id=ticker.id,
        legal_name="IKIO Lighting Limited",
        display_name=None,
        isin="INE0LOJ01019",
        sector="Industrials",
        industry="LED lighting",
        business_group_id=business_group.id,
        business_group_basis="CONSOLIDATED_FINANCIALS",
        business_group_source_reference=(
            "https://example.in/group/source"
        ),
    )
    db.session.add(company)
    db.session.flush()

    research_thesis_marker = "SENTINEL-RESEARCH-THESIS"
    research = ResearchRevision(
        company_id=company.id,
        revision_number=1,
        supersedes_revision_id=None,
        why_selected="SENTINEL-RESEARCH-WHY-SELECTED",
        what_is_changing="SENTINEL-RESEARCH-WHAT-IS-CHANGING",
        business_journey="SENTINEL-RESEARCH-BUSINESS-JOURNEY",
        thesis=research_thesis_marker,
        thesis_invalidation="SENTINEL-RESEARCH-INVALIDATION",
        management_summary="SENTINEL-MANAGEMENT-SUMMARY",
        management_quality=ManagementQuality.WATCH,
        management_rationale="SENTINEL-MANAGEMENT-RATIONALE",
        management_evidence="SENTINEL-MANAGEMENT-EVIDENCE",
        governance_status=GovernanceStatus.WATCH,
        change_reason=None,
        effective_at=fixed_now,
        created_by_user_id=actor.id,
    )
    db.session.add(research)
    db.session.flush()
    db.session.add(
        ResearchPoint(
            research_revision_id=research.id,
            kind=ResearchPointKind.CATALYST,
            title="SENTINEL-RESEARCH-CATALYST-TITLE",
            detail="SENTINEL-RESEARCH-CATALYST-DETAIL",
            status="OPEN",
            target_date=date(2027, 3, 31),
            sort_order=0,
        )
    )
    db.session.add(
        ResearchPoint(
            research_revision_id=research.id,
            kind=ResearchPointKind.RISK,
            title="SENTINEL-RESEARCH-RISK-TITLE",
            detail="SENTINEL-RESEARCH-RISK-DETAIL",
            status=None,
            target_date=None,
            sort_order=0,
        )
    )

    db.session.add(
        OwnershipSnapshot(
            company_id=company.id,
            as_of_date=as_of_date,
            promoter_holding_pct=Decimal("64.5000"),
            promoter_pledge_pct=None,
            notes="SENTINEL-OWNERSHIP-NOTES",
            source_reference="https://example.in/ownership/source",
            created_by_user_id=actor.id,
        )
    )
    db.session.add(
        GovernanceFlag(
            company_id=company.id,
            flag_type="PROMOTER_PLEDGE",
            title="SENTINEL-GOVERNANCE-TITLE",
            severity=GovernanceSeverity.HIGH,
            status=GovernanceFlagStatus.OPEN,
            factual_evidence="SENTINEL-GOVERNANCE-EVIDENCE",
            source_title="SENTINEL-GOVERNANCE-SOURCE-TITLE",
            source_url_or_reference=(
                "SENTINEL-GOVERNANCE-SOURCE-URL"
            ),
            interpretation="SENTINEL-GOVERNANCE-INTERPRETATION",
            observed_on=as_of_date,
            resolved_on=None,
            created_by_user_id=actor.id,
        )
    )
    db.session.add(
        MarketPlanRevision(
            company_id=company.id,
            revision_number=1,
            supersedes_revision_id=None,
            currency="INR",
            accumulation_low=Decimal("100.0000"),
            accumulation_high=Decimal("130.0000"),
            preferred_accumulation_price=None,
            supply_low=None,
            supply_high=None,
            invalidation_level=Decimal("90.0000"),
            rationale="SENTINEL-MARKET-PLAN-RATIONALE",
            effective_at=fixed_now,
            change_reason=None,
            created_by_user_id=actor.id,
        )
    )

    forecast = ForecastRevision(
        company_id=company.id,
        revision_number=1,
        supersedes_revision_id=None,
        as_of_date=as_of_date,
        assumptions="SENTINEL-FORECAST-ASSUMPTIONS",
        change_reason=None,
        created_by_user_id=actor.id,
    )
    db.session.add(forecast)
    db.session.flush()
    db.session.add(
        ForecastLine(
            forecast_revision_id=forecast.id,
            fiscal_year=2027,
            is_estimate=True,
            revenue=Decimal("1234.5000"),
            ebitda=Decimal("250.7500"),
            pat=Decimal("10.0000"),
            ebitda_margin_pct=Decimal("20.3100"),
            eps=Decimal("5.0000"),
            currency="INR",
            unit="CRORE",
        )
    )

    valuation = ValuationRevision(
        company_id=company.id,
        valuation_method=ValuationMethod.PE,
        revision_number=1,
        supersedes_revision_id=None,
        justified_multiple=Decimal("12.5000"),
        implied_enterprise_value=None,
        net_debt=Decimal("998877.5000"),
        other_equity_adjustment=None,
        implied_future_equity_value=Decimal("999999.0000"),
        required_return_pct=None,
        discount_period_years=None,
        present_value=None,
        current_market_cap=Decimal("900.0000"),
        currency="INR",
        unit="CRORE",
        valuation_notes="SENTINEL-VALUATION-NOTES",
        as_of_date=as_of_date,
        change_reason=None,
        created_by_user_id=actor.id,
    )
    db.session.add(valuation)
    db.session.flush()
    db.session.add(
        ValuationReferenceLine(
            valuation_revision_id=valuation.id,
            reference_forecast_revision_id=forecast.id,
            reference_fiscal_year=2027,
            reference_metric="EPS",
            reference_metric_value=Decimal("5.0000"),
            reference_metric_unit="INR_PER_SHARE",
            reference_metric_basis=(
                "SENTINEL-VALUATION-REFERENCE-BASIS"
            ),
            sort_order=0,
        )
    )

    disclosure_significance_marker = (
        "SENTINEL-DISCLOSURE-SIGNIFICANCE"
    )
    db.session.add(
        CompanyDisclosure(
            company_id=company.id,
            event_type="REG30",
            event_date=as_of_date,
            title="Key disclosure",
            original_source_url_or_reference=(
                "https://exchange.example/ref/key"
            ),
            exchange_reference="BSE:REFERENCE",
            significance_note=disclosure_significance_marker,
            is_key=True,
            document_id=None,
            created_by_user_id=actor.id,
            archived_at=None,
        )
    )

    scenario_specs = [
        {
            "id": "missing",
            "expected_context_tier": "FREE",
            "expected_is_admin": False,
            "expected_access_tier": "FREE",
            "expected_projection": "free",
            "expected_locked_sections": free_locked_sections,
            "is_admin": False,
            "entitlement": None,
            "token_tier": ResearchTier.PREMIUM,
            "token_is_admin": True,
        },
        {
            "id": "inactive",
            "expected_context_tier": "FREE",
            "expected_is_admin": False,
            "expected_access_tier": "FREE",
            "expected_projection": "free",
            "expected_locked_sections": free_locked_sections,
            "is_admin": False,
            "entitlement": {
                "tier": ResearchTier.PREMIUM,
                "status": EntitlementStatus.INACTIVE,
            },
            "token_tier": ResearchTier.FREE,
            "token_is_admin": True,
        },
        {
            "id": "revoked",
            "expected_context_tier": "FREE",
            "expected_is_admin": False,
            "expected_access_tier": "FREE",
            "expected_projection": "free",
            "expected_locked_sections": free_locked_sections,
            "is_admin": False,
            "entitlement": {
                "tier": ResearchTier.PREMIUM,
                "status": EntitlementStatus.REVOKED,
            },
            "token_tier": ResearchTier.FREE,
            "token_is_admin": True,
        },
        {
            "id": "future",
            "expected_context_tier": "FREE",
            "expected_is_admin": False,
            "expected_access_tier": "FREE",
            "expected_projection": "free",
            "expected_locked_sections": free_locked_sections,
            "is_admin": False,
            "entitlement": {
                "tier": ResearchTier.PREMIUM,
                "status": EntitlementStatus.ACTIVE,
                "valid_from": datetime(
                    2026, 10, 1, tzinfo=timezone.utc
                ),
            },
            "token_tier": ResearchTier.FREE,
            "token_is_admin": True,
        },
        {
            "id": "expired",
            "expected_context_tier": "FREE",
            "expected_is_admin": False,
            "expected_access_tier": "FREE",
            "expected_projection": "free",
            "expected_locked_sections": free_locked_sections,
            "is_admin": False,
            "entitlement": {
                "tier": ResearchTier.PREMIUM,
                "status": EntitlementStatus.ACTIVE,
                "valid_until": datetime(
                    2026, 8, 1, tzinfo=timezone.utc
                ),
            },
            "token_tier": ResearchTier.FREE,
            "token_is_admin": True,
        },
        {
            "id": "active_free",
            "expected_context_tier": "FREE",
            "expected_is_admin": False,
            "expected_access_tier": "FREE",
            "expected_projection": "free",
            "expected_locked_sections": free_locked_sections,
            "is_admin": False,
            "entitlement": {
                "tier": ResearchTier.FREE,
                "status": EntitlementStatus.ACTIVE,
            },
            "token_tier": ResearchTier.PREMIUM,
            "token_is_admin": True,
        },
        {
            "id": "active_premium",
            "expected_context_tier": "PREMIUM",
            "expected_is_admin": False,
            "expected_access_tier": "PREMIUM",
            "expected_projection": "premium",
            "expected_locked_sections": [],
            "is_admin": False,
            "entitlement": {
                "tier": ResearchTier.PREMIUM,
                "status": EntitlementStatus.ACTIVE,
            },
            "token_tier": ResearchTier.FREE,
            "token_is_admin": False,
        },
        {
            "id": "admin_without_row",
            "expected_context_tier": "FREE",
            "expected_is_admin": True,
            "expected_access_tier": "ADMIN",
            "expected_projection": "admin",
            "expected_locked_sections": [],
            "is_admin": True,
            "entitlement": None,
            "token_tier": ResearchTier.FREE,
            "token_is_admin": True,
        },
        {
            "id": "admin_with_stale_tier_claim",
            "expected_context_tier": "FREE",
            "expected_is_admin": True,
            "expected_access_tier": "ADMIN",
            "expected_projection": "admin",
            "expected_locked_sections": [],
            "is_admin": True,
            "entitlement": None,
            "token_tier": ResearchTier.PREMIUM,
            "token_is_admin": False,
        },
    ]

    users_by_scenario: dict[str, User] = {}
    for spec in scenario_specs:
        user = _make_user(
            f"{spec['id']}@example.com",
            is_admin=spec["is_admin"],
        )
        entitlement = spec["entitlement"]
        if entitlement is not None:
            _add_entitlement(
                user,
                tier=entitlement["tier"],
                status=entitlement["status"],
                valid_from=entitlement.get("valid_from"),
                valid_until=entitlement.get("valid_until"),
            )
        users_by_scenario[spec["id"]] = user

    db.session.commit()

    premium_markers = [
        "SENTINEL-RESEARCH-WHY-SELECTED",
        "SENTINEL-RESEARCH-WHAT-IS-CHANGING",
        "SENTINEL-RESEARCH-BUSINESS-JOURNEY",
        "SENTINEL-RESEARCH-CATALYST-TITLE",
        "SENTINEL-RESEARCH-CATALYST-DETAIL",
        "SENTINEL-RESEARCH-RISK-TITLE",
        "SENTINEL-RESEARCH-RISK-DETAIL",
        "SENTINEL-RESEARCH-INVALIDATION",
        "SENTINEL-MANAGEMENT-SUMMARY",
        "SENTINEL-MANAGEMENT-RATIONALE",
        "SENTINEL-MANAGEMENT-EVIDENCE",
        "SENTINEL-GOVERNANCE-TITLE",
        "SENTINEL-GOVERNANCE-EVIDENCE",
        "SENTINEL-GOVERNANCE-SOURCE-TITLE",
        "SENTINEL-GOVERNANCE-SOURCE-URL",
        "SENTINEL-GOVERNANCE-INTERPRETATION",
        "SENTINEL-OWNERSHIP-NOTES",
        "SENTINEL-MARKET-PLAN-RATIONALE",
        "SENTINEL-FORECAST-ASSUMPTIONS",
        "SENTINEL-VALUATION-NOTES",
        "SENTINEL-VALUATION-REFERENCE-BASIS",
        "998877.5000",
        "999999.0000",
        research_thesis_marker,
    ]

    scenarios: dict[str, dict[str, object]] = {}
    for spec in scenario_specs:
        user = users_by_scenario[spec["id"]]
        scenarios[spec["id"]] = {
            "id": spec["id"],
            "user": user,
            # Input under test, never an oracle.
            "context": EntitlementService.resolve(user, at=fixed_now),
            # Literal expectations owned by the tests.
            "expected_context_tier": spec["expected_context_tier"],
            "expected_is_admin": spec["expected_is_admin"],
            "expected_access_tier": spec["expected_access_tier"],
            "expected_projection": spec["expected_projection"],
            "expected_locked_sections": [
                dict(item) for item in spec["expected_locked_sections"]
            ],
            "jwt": create_access_token(
                identity=user.id,
                additional_claims={
                    "tier": spec["token_tier"],
                    "is_admin": spec["token_is_admin"],
                },
            ),
        }

    return {
        "now": fixed_now,
        "company_id": company.id,
        "company": company,
        "ticker": ticker,
        "actor_user_id": actor.id,
        "free_locked_sections": [
            dict(item) for item in free_locked_sections
        ],
        "premium_markers": premium_markers,
        "research_thesis_marker": research_thesis_marker,
        "disclosure_significance_marker": disclosure_significance_marker,
        "scenarios": scenarios,
    }
