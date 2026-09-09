"""Plan 3 Task 1: database-backed research entitlement resolution tests."""

from datetime import datetime, timezone

from flask_jwt_extended import create_access_token, decode_token

from app.models.entitlement import (
    INVESTMENT_RESEARCH_PRODUCT_CODE,
    UserEntitlement,
)
from app.models.research_types import EntitlementStatus, ResearchTier
from app.models.user import User
from app.services.entitlement_service import EntitlementService


CHECK_TIME = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)


def _add_entitlement(
    user: User,
    *,
    tier: str = ResearchTier.PREMIUM,
    status: str = EntitlementStatus.ACTIVE,
    valid_from: datetime | None = None,
    valid_until: datetime | None = None,
) -> None:
    from app import db

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
    db.session.commit()


def test_resolve_missing_entitlement_row_safely_returns_free(
    app, user_factory
):
    user = user_factory(email="missing@example.com")

    context = EntitlementService.resolve(user, at=CHECK_TIME)

    assert context.user_id == user.id
    assert context.is_admin is False
    assert context.tier == ResearchTier.FREE


def test_resolve_inactive_entitlement_row_returns_free(app, user_factory):
    user = user_factory(email="inactive@example.com")
    _add_entitlement(
        user, tier=ResearchTier.PREMIUM, status=EntitlementStatus.INACTIVE
    )

    context = EntitlementService.resolve(user, at=CHECK_TIME)

    assert context.tier == ResearchTier.FREE


def test_resolve_revoked_entitlement_row_returns_free(app, user_factory):
    user = user_factory(email="revoked@example.com")
    _add_entitlement(
        user, tier=ResearchTier.PREMIUM, status=EntitlementStatus.REVOKED
    )

    context = EntitlementService.resolve(user, at=CHECK_TIME)

    assert context.tier == ResearchTier.FREE


def test_resolve_not_yet_valid_entitlement_returns_free(app, user_factory):
    user = user_factory(email="future@example.com")
    _add_entitlement(
        user,
        tier=ResearchTier.PREMIUM,
        status=EntitlementStatus.ACTIVE,
        valid_from=datetime(2026, 7, 1, tzinfo=timezone.utc),
    )

    context = EntitlementService.resolve(user, at=CHECK_TIME)

    assert context.tier == ResearchTier.FREE


def test_resolve_expired_entitlement_returns_free(app, user_factory):
    user = user_factory(email="expired@example.com")
    _add_entitlement(
        user,
        tier=ResearchTier.PREMIUM,
        status=EntitlementStatus.ACTIVE,
        valid_until=datetime(2026, 5, 1, tzinfo=timezone.utc),
    )

    context = EntitlementService.resolve(user, at=CHECK_TIME)

    assert context.tier == ResearchTier.FREE


def test_resolve_active_free_entitlement_returns_free(app, user_factory):
    user = user_factory(email="active-free@example.com")
    _add_entitlement(
        user, tier=ResearchTier.FREE, status=EntitlementStatus.ACTIVE
    )

    context = EntitlementService.resolve(user, at=CHECK_TIME)

    assert context.tier == ResearchTier.FREE


def test_resolve_active_premium_entitlement_returns_premium(
    app, user_factory
):
    user = user_factory(email="premium@example.com")
    _add_entitlement(
        user,
        tier=ResearchTier.PREMIUM,
        status=EntitlementStatus.ACTIVE,
        valid_from=datetime(2026, 1, 1, tzinfo=timezone.utc),
        valid_until=datetime(2026, 12, 31, tzinfo=timezone.utc),
    )

    context = EntitlementService.resolve(user, at=CHECK_TIME)

    assert context.user_id == user.id
    assert context.is_admin is False
    assert context.tier == ResearchTier.PREMIUM


def test_resolve_administrator_without_row_reports_admin_and_free_tier(
    app, user_factory
):
    admin = user_factory(email="admin@example.com", is_admin=True)

    context = EntitlementService.resolve(admin, at=CHECK_TIME)

    assert context.user_id == admin.id
    assert context.is_admin is True
    assert context.tier == ResearchTier.FREE


def test_resolve_ignores_stale_jwt_tier_claim(app, user_factory):
    user = user_factory(email="stale-claim@example.com")
    _add_entitlement(
        user, tier=ResearchTier.PREMIUM, status=EntitlementStatus.ACTIVE
    )
    token = create_access_token(
        identity=user.id,
        additional_claims={
            "tier": ResearchTier.FREE,
            "is_admin": False,
        },
    )
    assert decode_token(token)["tier"] == ResearchTier.FREE

    context = EntitlementService.resolve(user, at=CHECK_TIME)

    assert context.tier == ResearchTier.PREMIUM
