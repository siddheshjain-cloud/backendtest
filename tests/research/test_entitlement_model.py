"""Plan 2 Task 3: one-row ``UserEntitlement`` persistence tests.

Covers the frozen entitlement model, its unique ``(user_id, product_code)``
constraint, in-place service upserts, the premium fixture, and the unchanged
legacy ``user`` table and serialization.
"""

import uuid
from datetime import datetime, timezone

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from app import db
from app.models import UserEntitlement
from app.models.entitlement import INVESTMENT_RESEARCH_PRODUCT_CODE
from app.models.research_types import EntitlementStatus, ResearchTier
from app.services.research_command_service import ResearchCommandService
from app.utils.research_errors import (
    ResearchNotFoundError,
    ResearchValidationError,
)
from app.utils.schemas import UserSchema


OTHER_PRODUCT = "TRADE_ALERTS"
VALID_FROM = datetime(2026, 1, 1, tzinfo=timezone.utc)
VALID_UNTIL = datetime(2027, 1, 1, tzinfo=timezone.utc)
REVISED_FROM = datetime(2026, 6, 1, tzinfo=timezone.utc)
REVISED_UNTIL = datetime(2028, 6, 1, tzinfo=timezone.utc)

EXPECTED_LEGACY_USER_COLUMNS = {
    "id",
    "created_at",
    "is_admin",
    "name",
    "email",
    "phone_number",
    "password_hash",
    "google_id",
    "telegram_chat_id",
    "telegram_username",
    "telegram_enabled",
    "telegram_connected_at",
}

EXPECTED_LEGACY_USER_KEYS = {
    "id",
    "name",
    "email",
    "phone_number",
    "is_admin",
    "created_at",
    "telegram_enabled",
    "telegram_username",
    "telegram_connected_at",
}


def _commit_expect_integrity() -> None:
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()


def _reflected_columns(table_name: str) -> dict[str, bool]:
    inspector = sa.inspect(db.engine)
    return {
        column["name"]: column["nullable"]
        for column in inspector.get_columns(table_name)
    }


def _utc_naive(value: datetime) -> datetime:
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc)
    return value.replace(tzinfo=None)


def _entitlement_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "tier": ResearchTier.PREMIUM,
        "status": EntitlementStatus.ACTIVE,
        "valid_from": VALID_FROM,
        "valid_until": VALID_UNTIL,
    }
    payload.update(overrides)
    return payload


def test_user_entitlement_declares_expected_table_name():
    assert UserEntitlement.__tablename__ == "user_entitlement"


def test_user_entitlement_table_has_exact_columns_and_named_constraints(
    app, user_factory
):
    user = user_factory(email="owner@example.com")
    entitlement = UserEntitlement(
        user_id=user.id,
        product_code=INVESTMENT_RESEARCH_PRODUCT_CODE,
        tier=ResearchTier.PREMIUM,
        status=EntitlementStatus.ACTIVE,
        valid_from=VALID_FROM,
        valid_until=VALID_UNTIL,
    )
    db.session.add(entitlement)
    db.session.commit()

    assert _reflected_columns("user_entitlement") == {
        "id": False,
        "created_at": False,
        "updated_at": False,
        "user_id": False,
        "product_code": False,
        "tier": False,
        "status": False,
        "valid_from": True,
        "valid_until": True,
    }

    inspector = sa.inspect(db.engine)
    unique_constraints = {
        constraint["name"]: constraint["column_names"]
        for constraint in inspector.get_unique_constraints("user_entitlement")
    }
    assert set(
        unique_constraints["uq_user_entitlement_user_product"]
    ) == {"user_id", "product_code"}

    foreign_keys = {
        frozenset(constraint["constrained_columns"]): constraint["referred_table"]
        for constraint in inspector.get_foreign_keys("user_entitlement")
    }
    assert foreign_keys[frozenset({"user_id"})] == "user"

    columns = {
        column["name"]: column["type"]
        for column in inspector.get_columns("user_entitlement")
    }
    assert str(columns["product_code"]) == "VARCHAR(64)"


def test_user_entitlement_uses_uuid_string_id_and_utc_timestamps(
    app, user_factory
):
    user = user_factory(email="owner@example.com")
    entitlement = UserEntitlement(
        user_id=user.id,
        product_code=INVESTMENT_RESEARCH_PRODUCT_CODE,
        tier=ResearchTier.PREMIUM,
        status=EntitlementStatus.ACTIVE,
    )
    db.session.add(entitlement)
    db.session.commit()

    assert isinstance(entitlement.id, str)
    assert len(entitlement.id) == 36
    assert uuid.UUID(entitlement.id).version == 4
    assert entitlement.created_at is not None
    assert entitlement.updated_at is not None
    assert entitlement.valid_from is None
    assert entitlement.valid_until is None


def test_user_entitlement_persists_tier_status_and_validity_and_links_user(
    app, user_factory
):
    user = user_factory(email="owner@example.com")
    entitlement = UserEntitlement(
        user_id=user.id,
        product_code=INVESTMENT_RESEARCH_PRODUCT_CODE,
        tier=ResearchTier.PREMIUM,
        status=EntitlementStatus.ACTIVE,
        valid_from=VALID_FROM,
        valid_until=VALID_UNTIL,
    )
    db.session.add(entitlement)
    db.session.commit()

    persisted = db.session.get(UserEntitlement, entitlement.id)
    assert persisted.user_id == user.id
    assert persisted.user.id == user.id
    assert persisted.product_code == INVESTMENT_RESEARCH_PRODUCT_CODE
    assert persisted.tier == ResearchTier.PREMIUM
    assert persisted.status == EntitlementStatus.ACTIVE
    assert _utc_naive(persisted.valid_from) == _utc_naive(VALID_FROM)
    assert _utc_naive(persisted.valid_until) == _utc_naive(VALID_UNTIL)


def test_duplicate_same_user_and_product_is_rejected_at_the_database(
    app, user_factory
):
    user = user_factory(email="owner@example.com")
    db.session.add(
        UserEntitlement(
            user_id=user.id,
            product_code=INVESTMENT_RESEARCH_PRODUCT_CODE,
            tier=ResearchTier.FREE,
            status=EntitlementStatus.ACTIVE,
        )
    )
    db.session.commit()

    db.session.add(
        UserEntitlement(
            user_id=user.id,
            product_code=INVESTMENT_RESEARCH_PRODUCT_CODE,
            tier=ResearchTier.PREMIUM,
            status=EntitlementStatus.ACTIVE,
        )
    )
    _commit_expect_integrity()

    count = db.session.scalar(
        sa.select(sa.func.count()).select_from(UserEntitlement)
    )
    assert count == 1


def test_different_products_are_allowed_for_one_user(app, user_factory):
    user = user_factory(email="owner@example.com")
    db.session.add_all(
        [
            UserEntitlement(
                user_id=user.id,
                product_code=INVESTMENT_RESEARCH_PRODUCT_CODE,
                tier=ResearchTier.PREMIUM,
                status=EntitlementStatus.ACTIVE,
            ),
            UserEntitlement(
                user_id=user.id,
                product_code=OTHER_PRODUCT,
                tier=ResearchTier.FREE,
                status=EntitlementStatus.ACTIVE,
            ),
        ]
    )
    db.session.commit()

    count = db.session.scalar(
        sa.select(sa.func.count()).select_from(UserEntitlement)
    )
    assert count == 2


def test_no_entitlement_history_or_revision_table_exists(app):
    declared = set(db.metadata.tables)
    entitlement_history_like = {
        name
        for name in declared
        if "entitlement" in name and name != "user_entitlement"
    }
    assert entitlement_history_like == set()

    created = set(sa.inspect(db.engine).get_table_names())
    assert created == declared


def test_legacy_user_table_gains_no_columns(app):
    assert set(_reflected_columns("user")) == EXPECTED_LEGACY_USER_COLUMNS


def test_legacy_user_serialization_has_no_entitlement_fields(app, free_user):
    dumped = UserSchema().dump(free_user)

    assert set(dumped) == EXPECTED_LEGACY_USER_KEYS
    assert not {
        key
        for key in dumped
        if "entitlement" in key
        or "tier" in key
        or "product" in key
    }


def test_service_upsert_entitlement_creates_missing_row(app, user_factory):
    user = user_factory(email="owner@example.com")

    entitlement = ResearchCommandService.upsert_entitlement(
        user.id,
        _entitlement_payload(),
        actor_user_id="actor-1",
    )

    assert entitlement.user_id == user.id
    assert entitlement.product_code == INVESTMENT_RESEARCH_PRODUCT_CODE
    assert entitlement.tier == ResearchTier.PREMIUM
    assert entitlement.status == EntitlementStatus.ACTIVE
    assert _utc_naive(entitlement.valid_from) == _utc_naive(VALID_FROM)
    assert _utc_naive(entitlement.valid_until) == _utc_naive(VALID_UNTIL)

    persisted = db.session.get(UserEntitlement, entitlement.id)
    assert persisted is not None
    count = db.session.scalar(
        sa.select(sa.func.count()).select_from(UserEntitlement)
    )
    assert count == 1


def test_service_upsert_entitlement_updates_the_same_row_in_place(
    app, user_factory
):
    user = user_factory(email="owner@example.com")

    first = ResearchCommandService.upsert_entitlement(
        user.id,
        _entitlement_payload(tier=ResearchTier.FREE),
        actor_user_id="actor-1",
    )
    first_updated_at = db.session.get(UserEntitlement, first.id).updated_at

    second = ResearchCommandService.upsert_entitlement(
        user.id,
        _entitlement_payload(
            tier=ResearchTier.PREMIUM,
            status=EntitlementStatus.REVOKED,
            valid_from=REVISED_FROM,
            valid_until=REVISED_UNTIL,
        ),
        actor_user_id="actor-2",
    )

    assert second.id == first.id
    persisted = db.session.get(UserEntitlement, first.id)
    assert persisted.product_code == INVESTMENT_RESEARCH_PRODUCT_CODE
    assert persisted.tier == ResearchTier.PREMIUM
    assert persisted.status == EntitlementStatus.REVOKED
    assert _utc_naive(persisted.valid_from) == _utc_naive(REVISED_FROM)
    assert _utc_naive(persisted.valid_until) == _utc_naive(REVISED_UNTIL)
    assert persisted.updated_at >= first_updated_at

    count = db.session.scalar(
        sa.select(sa.func.count()).select_from(UserEntitlement)
    )
    assert count == 1


def test_service_upsert_entitlement_does_not_create_history_rows(
    app, user_factory
):
    user = user_factory(email="owner@example.com")

    first = ResearchCommandService.upsert_entitlement(
        user.id,
        _entitlement_payload(status=EntitlementStatus.ACTIVE),
        actor_user_id="actor-1",
    )
    ResearchCommandService.upsert_entitlement(
        user.id,
        _entitlement_payload(status=EntitlementStatus.INACTIVE),
        actor_user_id="actor-1",
    )
    ResearchCommandService.upsert_entitlement(
        user.id,
        _entitlement_payload(status=EntitlementStatus.REVOKED),
        actor_user_id="actor-1",
    )

    rows = db.session.scalars(
        sa.select(UserEntitlement).order_by(UserEntitlement.created_at)
    ).all()
    assert [row.id for row in rows] == [first.id]
    assert [row.status for row in rows] == [EntitlementStatus.REVOKED]


def test_service_upsert_entitlement_requires_an_existing_user(app):
    with pytest.raises(ResearchNotFoundError):
        ResearchCommandService.upsert_entitlement(
            "missing-user",
            _entitlement_payload(),
            actor_user_id="actor-1",
        )

    count = db.session.scalar(
        sa.select(sa.func.count()).select_from(UserEntitlement)
    )
    assert count == 0


@pytest.mark.parametrize(
    "overrides",
    [
        {"tier": "GOLD"},
        {"status": "SUSPENDED"},
        {"valid_from": datetime(2026, 1, 1)},
        {"valid_until": datetime(2027, 1, 1)},
        {"created_at": VALID_FROM},
        {"product_code": INVESTMENT_RESEARCH_PRODUCT_CODE},
    ],
)
def test_service_upsert_entitlement_rejects_invalid_or_unknown_payload_fields(
    app, user_factory, overrides
):
    user = user_factory(email="owner@example.com")

    with pytest.raises(ResearchValidationError):
        ResearchCommandService.upsert_entitlement(
            user.id,
            _entitlement_payload(**overrides),
            actor_user_id="actor-1",
        )

    count = db.session.scalar(
        sa.select(sa.func.count()).select_from(UserEntitlement)
    )
    assert count == 0


def test_premium_user_owns_an_active_premium_research_entitlement(
    app, premium_user
):
    rows = db.session.scalars(
        sa.select(UserEntitlement).where(
            UserEntitlement.user_id == premium_user.id
        )
    ).all()

    assert len(rows) == 1
    assert rows[0].product_code == INVESTMENT_RESEARCH_PRODUCT_CODE
    assert rows[0].tier == ResearchTier.PREMIUM
    assert rows[0].status == EntitlementStatus.ACTIVE
