"""Plan 5 Task 7: deterministic, idempotent IKIO research seed.

These tests pin the seed boundary before the implementation exists. They use
the real M1 command services through ``ResearchSeedService`` and verify the
coherent, rights-aware, revisioned IKIO research graph against a fresh M1
database and against a database upgraded from the frozen baseline.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
import sqlalchemy as sa

from app import create_app, db
from app.models import (
    BusinessGroup,
    Company,
    CompanyDisclosure,
    Document,
    DocumentCompanyLink,
    ForecastLine,
    ForecastRevision,
    GovernanceFlag,
    MarketPlanRevision,
    OwnershipSnapshot,
    ResearchPoint,
    ResearchRevision,
    Ticker,
    User,
    UserEntitlement,
    ValuationReferenceLine,
    ValuationRevision,
)
from app.models.document import DistributionStatus, SourceAccess
from app.models.entitlement import INVESTMENT_RESEARCH_PRODUCT_CODE
from app.models.research_types import (
    EntitlementStatus,
    ResearchPointKind,
    ResearchTier,
    ValuationMethod,
)
from app.services.document_library_service import DocumentLibraryService
from app.services.entitlement_service import EntitlementService
from app.services.research_presenter import ResearchPresenter
from app.services.research_query_service import ResearchQueryService
from app.services.research_seed_service import ResearchSeedService
from config import Config
from tests.migrations.helpers import upgrade_database


UTC = timezone.utc


def _count(model) -> int:
    return (
        db.session.scalar(sa.select(sa.func.count()).select_from(model))
        or 0
    )


def _ids(model, *, order_by=None) -> list[str]:
    statement = sa.select(model.id)
    if order_by is not None:
        statement = statement.order_by(order_by)
    return list(db.session.scalars(statement).all())


@pytest.fixture
def seeded(app):
    return ResearchSeedService.run()


def _company(app, company_id: str) -> Company:
    return db.session.get(Company, company_id)


def test_seed_succeeds_on_fresh_m1_database(seeded):
    assert seeded["company_id"]
    assert _company(None, seeded["company_id"]) is not None
    assert _count(ResearchRevision) == 1
    assert _count(ForecastRevision) == 1
    assert _count(ValuationRevision) == 1
    assert _count(MarketPlanRevision) == 1
    assert _count(OwnershipSnapshot) == 1
    assert _count(GovernanceFlag) == 1
    assert _count(CompanyDisclosure) == 1
    assert _count(Document) == 4


def test_ikio_company_resolves_to_correct_ticker_reference_identity(seeded):
    company = _company(None, seeded["company_id"])

    assert company.ticker_id == seeded["ticker_id"]
    assert company.ticker.symbol == "IKIO"
    assert company.ticker.exchange == "NSE"
    assert company.isin == "INE0LOJ01019"
    assert company.legal_name == "IKIO Lighting Limited"


def test_required_entitlement_reference_access_exists(seeded):
    premium = db.session.get(User, seeded["premium_user_id"])
    context = EntitlementService.resolve(premium, at=seeded["effective_at"])

    row = db.session.scalar(
        sa.select(UserEntitlement).where(
            UserEntitlement.user_id == premium.id,
            UserEntitlement.product_code
            == INVESTMENT_RESEARCH_PRODUCT_CODE,
        )
    )

    assert row is not None
    assert row.tier == ResearchTier.PREMIUM
    assert row.status == EntitlementStatus.ACTIVE
    assert context.tier == ResearchTier.PREMIUM


def test_required_document_evidence_relationships_exist(seeded):
    company = _company(None, seeded["company_id"])

    documents = db.session.scalars(
        sa.select(Document)
        .join(
            DocumentCompanyLink,
            DocumentCompanyLink.document_id == Document.id,
        )
        .where(
            DocumentCompanyLink.company_id == company.id,
            DocumentCompanyLink.is_primary.is_(True),
        )
        .order_by(Document.title)
    ).all()

    assert len(documents) == 4
    assert all(
        any(
            link.company_id == company.id and link.is_primary
            for link in document.company_links
        )
        for document in documents
    )


def test_research_revision_exists_and_points_are_coherent(seeded):
    revision = db.session.get(
        ResearchRevision, seeded["research_revision_id"]
    )

    assert revision.company_id == seeded["company_id"]
    assert revision.revision_number == 1
    assert revision.thesis
    assert revision.thesis_invalidation
    assert {
        point.kind for point in revision.points
    } >= {ResearchPointKind.CATALYST, ResearchPointKind.RISK}
    assert all(point.title for point in revision.points)


def test_forecast_revision_and_lines_exist_where_specified(seeded):
    revision = db.session.get(
        ForecastRevision, seeded["forecast_revision_id"]
    )

    assert revision.company_id == seeded["company_id"]
    assert revision.revision_number == 1
    assert len(revision.lines) == 1
    line = revision.lines[0]
    assert line.fiscal_year == 2027
    assert line.is_estimate is True
    assert line.revenue == Decimal("1234.5000")
    assert line.ebitda == Decimal("250.7500")
    assert line.pat == Decimal("10.0000")
    assert line.ebitda_margin_pct == Decimal("20.3100")
    assert line.eps == Decimal("5.0000")
    assert line.currency == "INR"
    assert line.unit == "CRORE"


def test_valuation_revision_and_reference_lines_exist_where_specified(seeded):
    revision = db.session.get(
        ValuationRevision, seeded["valuation_revision_id"]
    )

    assert revision.company_id == seeded["company_id"]
    assert revision.valuation_method == ValuationMethod.PE
    assert revision.revision_number == 1
    assert revision.justified_multiple == Decimal("12.5000")
    assert revision.implied_future_equity_value == Decimal("999999.0000")
    assert len(revision.reference_lines) == 1
    line = revision.reference_lines[0]
    assert line.reference_metric == "EPS"
    assert line.reference_fiscal_year == 2027
    assert line.reference_forecast_revision_id == (
        seeded["forecast_revision_id"]
    )


def test_market_plan_exists_where_specified(seeded):
    revision = db.session.get(
        MarketPlanRevision, seeded["market_plan_revision_id"]
    )

    assert revision.company_id == seeded["company_id"]
    assert revision.revision_number == 1
    assert revision.accumulation_low == Decimal("100.0000")
    assert revision.accumulation_high == Decimal("130.0000")
    assert revision.invalidation_level == Decimal("90.0000")


def test_governance_disclosure_ownership_records_exist(seeded):
    company_id = seeded["company_id"]

    governance = db.session.get(
        GovernanceFlag, seeded["governance_flag_id"]
    )
    disclosure = db.session.get(
        CompanyDisclosure, seeded["disclosure_id"]
    )
    ownership = db.session.get(
        OwnershipSnapshot, seeded["ownership_snapshot_id"]
    )

    assert governance.company_id == company_id
    assert disclosure.company_id == company_id
    assert ownership.company_id == company_id
    assert governance.status == "OPEN"
    assert disclosure.is_key is True
    assert ownership.promoter_holding_pct == Decimal("64.5000")


def test_actual_facts_retain_required_source_document_lineage(seeded):
    governance = db.session.get(
        GovernanceFlag, seeded["governance_flag_id"]
    )
    disclosure = db.session.get(
        CompanyDisclosure, seeded["disclosure_id"]
    )
    ownership = db.session.get(
        OwnershipSnapshot, seeded["ownership_snapshot_id"]
    )

    document_urls = {
        document.original_source_url
        for document in db.session.scalars(sa.select(Document)).all()
        if document.original_source_url is not None
    }

    assert governance.source_url_or_reference in document_urls
    assert disclosure.original_source_url_or_reference in document_urls
    assert ownership.source_reference in document_urls


def test_estimates_remain_distinguishable_from_actuals(seeded):
    forecast = db.session.get(
        ForecastRevision, seeded["forecast_revision_id"]
    )
    valuation = db.session.get(
        ValuationRevision, seeded["valuation_revision_id"]
    )
    ownership = db.session.get(
        OwnershipSnapshot, seeded["ownership_snapshot_id"]
    )

    assert forecast.lines[0].is_estimate is True
    assert valuation.valuation_notes is not None
    assert valuation.valuation_notes.strip()
    assert ownership.promoter_holding_pct is not None
    assert ownership.source_reference is not None


def test_current_research_projection_can_be_built(seeded):
    company = _company(None, seeded["company_id"])
    premium = db.session.get(User, seeded["premium_user_id"])
    context = EntitlementService.resolve(premium, at=seeded["effective_at"])

    aggregate = ResearchQueryService.get_company_detail(
        company.id, context
    )
    projection = ResearchPresenter.company_detail(aggregate, context)

    assert projection["company"]["id"] == company.id
    assert projection["research"]["revision"] == 1
    assert projection["forecast"]["revision"] == 1
    assert projection["valuations"][0]["revision"] == 1
    assert projection["market_plan"]["revision"] == 1
    assert projection["governance"]["flags"]
    assert projection["ownership"]["promoter_holding_pct"] == "64.5000"


def test_entitled_consumer_query_can_read_seeded_research(seeded):
    company = _company(None, seeded["company_id"])
    premium = db.session.get(User, seeded["premium_user_id"])
    context = EntitlementService.resolve(premium, at=seeded["effective_at"])

    page = ResearchQueryService.list_companies(
        q="IKIO",
        sector=None,
        industry=None,
        page=1,
        per_page=20,
        context=context,
    )

    assert page.total_items == 1
    assert page.items[0]["company"]["id"] == company.id

    detail = ResearchQueryService.get_company_detail(
        company.id, context
    )
    assert detail["research"] is not None
    assert detail["forecast"] is not None
    assert detail["valuations"]


def test_rights_safe_document_query_can_read_only_allowed_seeded_documents(
    seeded,
):
    company = _company(None, seeded["company_id"])
    premium = db.session.get(User, seeded["premium_user_id"])
    provider = db.session.get(User, seeded["provider_user_id"])
    premium_context = EntitlementService.resolve(
        premium, at=seeded["effective_at"]
    )
    provider_context = EntitlementService.resolve(
        provider, at=seeded["effective_at"]
    )

    premium_page = DocumentLibraryService.list_company_documents(
        company.id, {}, 1, 20, premium_context
    )
    assert premium_page.total_items == 3
    assert all(
        item["id"] != seeded["restricted_document_id"]
        for item in premium_page.items
    )

    with pytest.raises(Exception) as exc_info:
        DocumentLibraryService.get_document(
            seeded["restricted_document_id"], premium_context
        )
    assert exc_info.value.code == "document_not_found"

    provider_detail = DocumentLibraryService.get_document(
        seeded["restricted_document_id"], provider_context
    )
    assert provider_detail["id"] == seeded["restricted_document_id"]


def test_running_seed_twice_produces_no_duplicate_logical_records(seeded):
    before = {
        "research": _count(ResearchRevision),
        "points": _count(ResearchPoint),
        "forecast": _count(ForecastRevision),
        "forecast_lines": _count(ForecastLine),
        "valuation": _count(ValuationRevision),
        "valuation_lines": _count(ValuationReferenceLine),
        "market_plan": _count(MarketPlanRevision),
        "ownership": _count(OwnershipSnapshot),
        "governance": _count(GovernanceFlag),
        "disclosure": _count(CompanyDisclosure),
        "document": _count(Document),
        "company": _count(Company),
        "ticker": _count(Ticker),
        "business_group": _count(BusinessGroup),
        "entitlement": _count(UserEntitlement),
    }

    ResearchSeedService.run()

    after = {
        "research": _count(ResearchRevision),
        "points": _count(ResearchPoint),
        "forecast": _count(ForecastRevision),
        "forecast_lines": _count(ForecastLine),
        "valuation": _count(ValuationRevision),
        "valuation_lines": _count(ValuationReferenceLine),
        "market_plan": _count(MarketPlanRevision),
        "ownership": _count(OwnershipSnapshot),
        "governance": _count(GovernanceFlag),
        "disclosure": _count(CompanyDisclosure),
        "document": _count(Document),
        "company": _count(Company),
        "ticker": _count(Ticker),
        "business_group": _count(BusinessGroup),
        "entitlement": _count(UserEntitlement),
    }

    assert after == before


def test_revision_numbers_do_not_increment_merely_because_seed_reruns(seeded):
    before = {
        "research": db.session.scalar(
            sa.select(sa.func.max(ResearchRevision.revision_number)).where(
                ResearchRevision.company_id == seeded["company_id"]
            )
        ),
        "forecast": db.session.scalar(
            sa.select(sa.func.max(ForecastRevision.revision_number)).where(
                ForecastRevision.company_id == seeded["company_id"]
            )
        ),
        "valuation": db.session.scalar(
            sa.select(sa.func.max(ValuationRevision.revision_number)).where(
                ValuationRevision.company_id == seeded["company_id"]
            )
        ),
        "market_plan": db.session.scalar(
            sa.select(sa.func.max(MarketPlanRevision.revision_number)).where(
                MarketPlanRevision.company_id == seeded["company_id"]
            )
        ),
    }

    ResearchSeedService.run()

    after = {
        "research": db.session.scalar(
            sa.select(sa.func.max(ResearchRevision.revision_number)).where(
                ResearchRevision.company_id == seeded["company_id"]
            )
        ),
        "forecast": db.session.scalar(
            sa.select(sa.func.max(ForecastRevision.revision_number)).where(
                ForecastRevision.company_id == seeded["company_id"]
            )
        ),
        "valuation": db.session.scalar(
            sa.select(sa.func.max(ValuationRevision.revision_number)).where(
                ValuationRevision.company_id == seeded["company_id"]
            )
        ),
        "market_plan": db.session.scalar(
            sa.select(sa.func.max(MarketPlanRevision.revision_number)).where(
                MarketPlanRevision.company_id == seeded["company_id"]
            )
        ),
    }

    assert after == before == {
        "research": 1,
        "forecast": 1,
        "valuation": 1,
        "market_plan": 1,
    }


def test_no_legacy_tables_records_are_altered_unexpectedly(app):
    existing_ticker = Ticker(
        symbol="IKIO",
        instrument_token=999999,
        exchange="NSE",
        name="Existing IKIO Ticker",
        last_price=1.0,
    )
    existing_user = User(
        name="Existing Seed User",
        email="ikio-seed-admin@example.com",
        is_admin=True,
    )
    existing_user.set_password("existing-password")
    db.session.add(existing_ticker)
    db.session.add(existing_user)
    db.session.commit()

    result = ResearchSeedService.run()

    ticker = db.session.get(Ticker, result["ticker_id"])
    user = db.session.get(User, result["admin_user_id"])

    assert ticker.id == existing_ticker.id
    assert ticker.name == "Existing IKIO Ticker"
    assert ticker.instrument_token == 999999
    assert user.id == existing_user.id
    assert user.name == "Existing Seed User"


def test_seed_preserves_existing_premium_entitlement_row(app):
    existing_user = User(
        name="Existing Seed Premium Consumer",
        email="ikio-seed-premium@example.com",
        is_admin=False,
    )
    existing_user.set_password("existing-password")
    db.session.add(existing_user)
    db.session.commit()

    existing_entitlement = UserEntitlement(
        user_id=existing_user.id,
        product_code=INVESTMENT_RESEARCH_PRODUCT_CODE,
        tier=ResearchTier.FREE,
        status=EntitlementStatus.INACTIVE,
    )
    db.session.add(existing_entitlement)
    db.session.commit()

    result = ResearchSeedService.run()

    persisted = db.session.scalar(
        sa.select(UserEntitlement).where(
            UserEntitlement.user_id == existing_user.id,
            UserEntitlement.product_code
            == INVESTMENT_RESEARCH_PRODUCT_CODE,
        )
    )

    assert persisted.id == existing_entitlement.id
    assert persisted.tier == ResearchTier.FREE
    assert persisted.status == EntitlementStatus.INACTIVE
    assert result["premium_user_id"] == existing_user.id


def test_seed_works_after_migration_from_frozen_baseline_to_current_head(tmp_path):
    database_url = f"sqlite:///{(tmp_path / 'ikio-migrated.db').as_posix()}"
    upgrade_database(database_url, "head")

    class MigratedTestingConfig(Config):
        TESTING = True
        SQLALCHEMY_DATABASE_URI = database_url
        JWT_SECRET_KEY = "test-jwt-secret"
        ELASTICSEARCH_URL = None

    application = create_app(MigratedTestingConfig)

    with application.app_context():
        result = ResearchSeedService.run()

        company = db.session.get(Company, result["company_id"])
        assert company is not None
        assert company.isin == "INE0LOJ01019"
        assert _count(ResearchRevision) == 1
        assert _count(ForecastRevision) == 1
        assert _count(ValuationRevision) == 1

        ResearchSeedService.run()
        assert _count(ResearchRevision) == 1
