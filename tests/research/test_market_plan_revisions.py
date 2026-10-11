"""Plan Task 7: immutable market-plan revision stream.

These tests lock the frozen ``MarketPlanRevision`` contract: ordered positive
accumulation bounds, an optional preferred price inside the range, paired
ascending supply bounds, a required positive price-based invalidation level
that remains distinct from ``ResearchRevision.thesis_invalidation``, the
Task 4 stale-base/unique-race conflict semantics, atomic rollback, append-only
immutability, and independent revision numbering per company.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import db
from app.models import Company, MarketPlanRevision, ResearchRevision
from app.models.research_types import (
    GovernanceStatus,
    ManagementQuality,
)
from app.services.research_command_service import ResearchCommandService
from app.utils.research_errors import (
    ResearchConflictError,
    ResearchNotFoundError,
    ResearchValidationError,
)


EFFECTIVE_AT = datetime(2026, 9, 6, 12, 0, tzinfo=timezone.utc)
VALID_ISIN = "INE0LOJ01019"


def _utc_naive(value: datetime) -> datetime:
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc)
    return value.replace(tzinfo=None)


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


def _market_plan_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "currency": "INR",
        "accumulation_low": Decimal("100.0000"),
        "accumulation_high": Decimal("130.0000"),
        "preferred_accumulation_price": Decimal("115.0000"),
        "supply_low": Decimal("165.0000"),
        "supply_high": Decimal("180.0000"),
        "invalidation_level": Decimal("90.0000"),
        "rationale": "Accumulate while the thesis remains intact",
        "effective_at": EFFECTIVE_AT,
    }
    payload.update(overrides)
    return payload


def _market_plan_row(
    *,
    company_id: str,
    actor_user_id: str,
    revision_number: int,
    supersedes_revision_id: str | None = None,
) -> MarketPlanRevision:
    return MarketPlanRevision(
        company_id=company_id,
        revision_number=revision_number,
        supersedes_revision_id=supersedes_revision_id,
        currency="INR",
        accumulation_low=Decimal("100.0000"),
        accumulation_high=Decimal("130.0000"),
        invalidation_level=Decimal("90.0000"),
        change_reason=None if revision_number == 1 else "Updated market plan",
        effective_at=EFFECTIVE_AT,
        created_by_user_id=actor_user_id,
    )


@pytest.fixture
def company(ticker_factory):
    ticker = ticker_factory()
    return ResearchCommandService.create_company(
        {
            "ticker_id": ticker.id,
            "legal_name": "IKIO Lighting Limited",
            "isin": VALID_ISIN,
        },
        actor_user_id="actor-1",
    )


def test_market_plan_model_is_exported_from_app_models():
    assert MarketPlanRevision.__tablename__ == "market_plan_revision"
    assert callable(
        ResearchCommandService.create_market_plan_revision
    )


def test_market_plan_revision_table_has_exact_columns_and_constraints(app):
    assert _reflected_columns("market_plan_revision") == {
        "id": False,
        "created_at": False,
        "company_id": False,
        "revision_number": False,
        "supersedes_revision_id": True,
        "currency": False,
        "accumulation_low": False,
        "accumulation_high": False,
        "preferred_accumulation_price": True,
        "supply_low": True,
        "supply_high": True,
        "invalidation_level": False,
        "rationale": True,
        "effective_at": False,
        "change_reason": True,
        "created_by_user_id": False,
    }
    assert "updated_at" not in {
        column.name for column in MarketPlanRevision.__table__.columns
    }

    inspector = sa.inspect(db.engine)
    unique_constraints = {
        constraint["name"]: sorted(constraint["column_names"])
        for constraint in inspector.get_unique_constraints(
            "market_plan_revision"
        )
    }
    assert unique_constraints[
        "uq_market_plan_revision_company_number"
    ] == ["company_id", "revision_number"]

    foreign_keys = {
        frozenset(constraint["constrained_columns"]): constraint[
            "referred_table"
        ]
        for constraint in inspector.get_foreign_keys("market_plan_revision")
    }
    assert foreign_keys[frozenset({"company_id"})] == "company"
    assert foreign_keys[frozenset({"created_by_user_id"})] == "user"
    assert (
        foreign_keys[frozenset({"supersedes_revision_id"})]
        == "market_plan_revision"
    )

    check_names = {
        constraint["name"]
        for constraint in inspector.get_check_constraints(
            "market_plan_revision"
        )
    }
    assert "ck_market_plan_revision_number_positive" in check_names
    assert "ck_market_plan_accumulation_positive" in check_names
    assert "ck_market_plan_accumulation_ordered" in check_names
    assert "ck_market_plan_preferred_inside_range" in check_names
    assert "ck_market_plan_supply_pair_present" in check_names
    assert "ck_market_plan_supply_ordered" in check_names
    assert "ck_market_plan_invalidation_positive" in check_names


def test_first_market_plan_revision_is_persisted_with_number_one(
    app, admin_user, company
):
    revision = ResearchCommandService.create_market_plan_revision(
        company.id,
        actor_user_id=admin_user.id,
        payload=_market_plan_payload(),
    )

    persisted = db.session.get(MarketPlanRevision, revision.id)
    assert persisted.revision_number == 1
    assert persisted.supersedes_revision_id is None
    assert persisted.change_reason is None
    assert persisted.company_id == company.id
    assert persisted.created_by_user_id == admin_user.id
    assert persisted.currency == "INR"
    assert persisted.accumulation_low == Decimal("100.0000")
    assert persisted.accumulation_high == Decimal("130.0000")
    assert persisted.preferred_accumulation_price == Decimal("115.0000")
    assert persisted.supply_low == Decimal("165.0000")
    assert persisted.supply_high == Decimal("180.0000")
    assert persisted.invalidation_level == Decimal("90.0000")
    assert persisted.rationale == "Accumulate while the thesis remains intact"
    assert _utc_naive(persisted.effective_at) == _utc_naive(EFFECTIVE_AT)
    assert persisted.created_at is not None
    assert isinstance(persisted.id, str)
    assert len(persisted.id) == 36
    assert uuid.UUID(persisted.id).version == 4


def test_first_market_plan_revision_accepts_absent_or_null_base_id(
    app, admin_user, company, ticker_factory
):
    without_base = _market_plan_payload()
    without_base.pop("base_revision_id", None)
    first = ResearchCommandService.create_market_plan_revision(
        company.id,
        actor_user_id=admin_user.id,
        payload=without_base,
    )

    second_ticker = ticker_factory(symbol="WIPRO", instrument_token=21)
    second_company = ResearchCommandService.create_company(
        {
            "ticker_id": second_ticker.id,
            "legal_name": "Second Limited",
            "isin": "US0378331005",
        },
        actor_user_id="actor-1",
    )
    second = ResearchCommandService.create_market_plan_revision(
        second_company.id,
        actor_user_id=admin_user.id,
        payload=_market_plan_payload(base_revision_id=None),
    )

    assert first.revision_number == 1
    assert second.revision_number == 1
    assert first.supersedes_revision_id is None
    assert second.supersedes_revision_id is None


def test_first_market_plan_revision_rejects_a_non_null_base_id(
    app, admin_user, company
):
    with pytest.raises(ResearchConflictError) as exc_info:
        ResearchCommandService.create_market_plan_revision(
            company.id,
            actor_user_id=admin_user.id,
            payload=_market_plan_payload(
                base_revision_id="does-not-exist"
            ),
        )

    assert exc_info.value.code == "revision_conflict"
    assert exc_info.value.message == "Research revision changed"


def test_first_market_plan_revision_rejects_a_non_null_change_reason(
    app, admin_user, company
):
    with pytest.raises(ResearchValidationError) as exc_info:
        ResearchCommandService.create_market_plan_revision(
            company.id,
            actor_user_id=admin_user.id,
            payload=_market_plan_payload(
                change_reason="Initial creation"
            ),
        )

    assert exc_info.value.code == "validation_error"
    assert "change_reason" in exc_info.value.details


@pytest.mark.parametrize(
    "field",
    [
        "accumulation_low",
        "accumulation_high",
        "invalidation_level",
        "effective_at",
    ],
)
def test_required_market_plan_fields_cannot_be_omitted(
    app, admin_user, company, field
):
    payload = _market_plan_payload()
    payload.pop(field)

    with pytest.raises(ResearchValidationError) as exc_info:
        ResearchCommandService.create_market_plan_revision(
            company.id,
            actor_user_id=admin_user.id,
            payload=payload,
        )

    assert exc_info.value.code == "validation_error"
    assert field in exc_info.value.details
    assert not db.session().in_transaction()


@pytest.mark.parametrize(
    "field",
    [
        "accumulation_low",
        "accumulation_high",
        "invalidation_level",
    ],
)
@pytest.mark.parametrize(
    "bad_value",
    [
        Decimal("-1.0000"),
        Decimal("0.0000"),
        "100",
        100,
        True,
        None,
    ],
)
def test_required_price_and_invalidation_values_must_be_positive_decimals(
    app, admin_user, company, field, bad_value
):
    with pytest.raises(ResearchValidationError) as exc_info:
        ResearchCommandService.create_market_plan_revision(
            company.id,
            actor_user_id=admin_user.id,
            payload=_market_plan_payload(**{field: bad_value}),
        )

    assert exc_info.value.code == "validation_error"
    assert field in exc_info.value.details


@pytest.mark.parametrize(
    "field",
    [
        "preferred_accumulation_price",
        "supply_low",
        "supply_high",
    ],
)
@pytest.mark.parametrize(
    "bad_value",
    [
        Decimal("-1.0000"),
        Decimal("0.0000"),
        "100",
        100,
        True,
    ],
)
def test_optional_price_values_when_present_must_be_positive_decimals(
    app, admin_user, company, field, bad_value
):
    with pytest.raises(ResearchValidationError) as exc_info:
        ResearchCommandService.create_market_plan_revision(
            company.id,
            actor_user_id=admin_user.id,
            payload=_market_plan_payload(**{field: bad_value}),
        )

    assert exc_info.value.code == "validation_error"
    assert field in exc_info.value.details


def test_accumulation_bounds_must_be_ordered(app, admin_user, company):
    with pytest.raises(ResearchValidationError) as exc_info:
        ResearchCommandService.create_market_plan_revision(
            company.id,
            actor_user_id=admin_user.id,
            payload=_market_plan_payload(
                accumulation_low=Decimal("130.0000"),
                accumulation_high=Decimal("100.0000"),
            ),
        )

    assert exc_info.value.code == "validation_error"
    assert "accumulation_low" in exc_info.value.details
    assert not db.session().in_transaction()


def test_equal_accumulation_bounds_are_allowed_when_preferred_matches(
    app, admin_user, company
):
    revision = ResearchCommandService.create_market_plan_revision(
        company.id,
        actor_user_id=admin_user.id,
        payload=_market_plan_payload(
            accumulation_low=Decimal("120.0000"),
            accumulation_high=Decimal("120.0000"),
            preferred_accumulation_price=Decimal("120.0000"),
        ),
    )

    assert revision.accumulation_low == Decimal("120.0000")
    assert revision.accumulation_high == Decimal("120.0000")
    assert revision.preferred_accumulation_price == Decimal("120.0000")


def test_preferred_accumulation_price_must_be_inside_the_accumulation_range(
    app, admin_user, company
):
    for preferred in (
        Decimal("99.9999"),
        Decimal("130.0001"),
    ):
        with pytest.raises(ResearchValidationError) as exc_info:
            ResearchCommandService.create_market_plan_revision(
                company.id,
                actor_user_id=admin_user.id,
                payload=_market_plan_payload(
                    preferred_accumulation_price=preferred
                ),
            )

        assert exc_info.value.code == "validation_error"
        assert "preferred_accumulation_price" in exc_info.value.details


def test_preferred_accumulation_price_is_optional(app, admin_user, company):
    revision = ResearchCommandService.create_market_plan_revision(
        company.id,
        actor_user_id=admin_user.id,
        payload=_market_plan_payload(preferred_accumulation_price=None),
    )

    assert revision.preferred_accumulation_price is None


def test_supply_bounds_must_be_supplied_together(
    app, admin_user, company
):
    with pytest.raises(ResearchValidationError) as exc_info:
        ResearchCommandService.create_market_plan_revision(
            company.id,
            actor_user_id=admin_user.id,
            payload=_market_plan_payload(
                supply_low=Decimal("165.0000"),
                supply_high=None,
            ),
        )
    assert exc_info.value.code == "validation_error"
    assert "supply_high" in exc_info.value.details

    with pytest.raises(ResearchValidationError) as exc_info:
        ResearchCommandService.create_market_plan_revision(
            company.id,
            actor_user_id=admin_user.id,
            payload=_market_plan_payload(
                supply_low=None,
                supply_high=Decimal("180.0000"),
            ),
        )
    assert exc_info.value.code == "validation_error"
    assert "supply_low" in exc_info.value.details
    assert not db.session().in_transaction()


def test_supply_bounds_when_present_must_be_ordered(
    app, admin_user, company
):
    with pytest.raises(ResearchValidationError) as exc_info:
        ResearchCommandService.create_market_plan_revision(
            company.id,
            actor_user_id=admin_user.id,
            payload=_market_plan_payload(
                supply_low=Decimal("180.0000"),
                supply_high=Decimal("165.0000"),
            ),
        )

    assert exc_info.value.code == "validation_error"
    assert "supply_low" in exc_info.value.details


def test_supply_bounds_may_be_absent_together(
    app, admin_user, company
):
    revision = ResearchCommandService.create_market_plan_revision(
        company.id,
        actor_user_id=admin_user.id,
        payload=_market_plan_payload(
            supply_low=None,
            supply_high=None,
        ),
    )

    assert revision.supply_low is None
    assert revision.supply_high is None


def test_invalidation_level_is_required_positive_and_independent(
    app, admin_user, company
):
    with pytest.raises(ResearchValidationError) as exc_info:
        ResearchCommandService.create_market_plan_revision(
            company.id,
            actor_user_id=admin_user.id,
            payload=_market_plan_payload(invalidation_level=None),
        )
    assert "invalidation_level" in exc_info.value.details

    revision = ResearchCommandService.create_market_plan_revision(
        company.id,
        actor_user_id=admin_user.id,
        payload=_market_plan_payload(),
    )
    research_revision = ResearchCommandService.create_research_revision(
        company.id,
        actor_user_id=admin_user.id,
        payload={
            "why_selected": "Exposed to durable demand growth",
            "thesis": "Operating leverage compounds",
            "thesis_invalidation": "Customer concentration or margin erosion",
            "management_quality": ManagementQuality.UNASSESSED,
            "governance_status": GovernanceStatus.UNREVIEWED,
            "effective_at": EFFECTIVE_AT,
        },
    )

    market_persisted = db.session.get(MarketPlanRevision, revision.id)
    research_persisted = db.session.get(
        ResearchRevision, research_revision.id
    )
    assert market_persisted.invalidation_level == Decimal("90.0000")
    assert (
        research_persisted.thesis_invalidation
        == "Customer concentration or margin erosion"
    )
    assert "thesis_invalidation" not in {
        column.name for column in MarketPlanRevision.__table__.columns
    }
    assert "invalidation_level" not in {
        column.name for column in ResearchRevision.__table__.columns
    }


def test_second_market_plan_revision_requires_current_base_and_reason(
    app, admin_user, company
):
    first = ResearchCommandService.create_market_plan_revision(
        company.id,
        actor_user_id=admin_user.id,
        payload=_market_plan_payload(),
    )

    with pytest.raises(ResearchValidationError) as exc_info:
        ResearchCommandService.create_market_plan_revision(
            company.id,
            actor_user_id=admin_user.id,
            payload=_market_plan_payload(base_revision_id=None),
        )
    assert "base_revision_id" in exc_info.value.details

    for bad_reason in (None, "", "   "):
        with pytest.raises(ResearchValidationError) as exc_info:
            ResearchCommandService.create_market_plan_revision(
                company.id,
                actor_user_id=admin_user.id,
                payload=_market_plan_payload(
                    base_revision_id=first.id,
                    change_reason=bad_reason,
                ),
            )
        assert exc_info.value.code == "validation_error"
        assert "change_reason" in exc_info.value.details
        assert not db.session().in_transaction()

    second = ResearchCommandService.create_market_plan_revision(
        company.id,
        actor_user_id=admin_user.id,
        payload=_market_plan_payload(
            base_revision_id=first.id,
            change_reason="Zone tightened after earnings",
            accumulation_low=Decimal("95.0000"),
            accumulation_high=Decimal("120.0000"),
            preferred_accumulation_price=Decimal("108.0000"),
        ),
    )

    assert second.revision_number == 2
    assert second.supersedes_revision_id == first.id
    assert second.change_reason == "Zone tightened after earnings"
    assert second.accumulation_low == Decimal("95.0000")


def test_stale_market_plan_base_raises_conflict_without_overwriting(
    app, admin_user, company
):
    first = ResearchCommandService.create_market_plan_revision(
        company.id,
        actor_user_id=admin_user.id,
        payload=_market_plan_payload(),
    )
    ResearchCommandService.create_market_plan_revision(
        company.id,
        actor_user_id=admin_user.id,
        payload=_market_plan_payload(
            base_revision_id=first.id,
            change_reason="First update",
        ),
    )

    with pytest.raises(ResearchConflictError) as exc_info:
        ResearchCommandService.create_market_plan_revision(
            company.id,
            actor_user_id=admin_user.id,
            payload=_market_plan_payload(
                base_revision_id=first.id,
                change_reason="Stale update",
            ),
        )

    assert exc_info.value.code == "revision_conflict"
    assert exc_info.value.message == "Research revision changed"
    assert not db.session().in_transaction()

    revision_numbers = db.session.scalars(
        sa.select(MarketPlanRevision.revision_number)
        .where(MarketPlanRevision.company_id == company.id)
        .order_by(MarketPlanRevision.revision_number)
    ).all()
    assert revision_numbers == [1, 2]


def test_unique_race_is_translated_to_revision_conflict(
    app, admin_user, company, monkeypatch
):
    first = ResearchCommandService.create_market_plan_revision(
        company.id,
        actor_user_id=admin_user.id,
        payload=_market_plan_payload(),
    )
    second = ResearchCommandService.create_market_plan_revision(
        company.id,
        actor_user_id=admin_user.id,
        payload=_market_plan_payload(
            base_revision_id=first.id,
            change_reason="Winning writer",
        ),
    )

    def _stale_current(_cls, _company_id):
        return first

    monkeypatch.setattr(
        ResearchCommandService,
        "_current_market_plan_revision_locked",
        classmethod(_stale_current),
    )

    with pytest.raises(ResearchConflictError) as exc_info:
        ResearchCommandService.create_market_plan_revision(
            company.id,
            actor_user_id=admin_user.id,
            payload=_market_plan_payload(
                base_revision_id=first.id,
                change_reason="Losing writer",
            ),
        )

    assert exc_info.value.code == "revision_conflict"
    assert exc_info.value.message == "Research revision changed"
    assert not db.session().in_transaction()

    revision_numbers = db.session.scalars(
        sa.select(MarketPlanRevision.revision_number)
        .where(MarketPlanRevision.company_id == company.id)
        .order_by(MarketPlanRevision.revision_number)
    ).all()
    assert revision_numbers == [1, 2]


def test_company_and_market_plan_revision_number_are_unique_at_database(
    app, admin_user, company
):
    first = ResearchCommandService.create_market_plan_revision(
        company.id,
        actor_user_id=admin_user.id,
        payload=_market_plan_payload(),
    )

    db.session.add(
        _market_plan_row(
            company_id=company.id,
            actor_user_id=admin_user.id,
            revision_number=first.revision_number,
        )
    )
    _commit_expect_integrity()

    count = db.session.scalar(
        sa.select(sa.func.count())
        .select_from(MarketPlanRevision)
        .where(MarketPlanRevision.company_id == company.id)
    )
    assert count == 1


def test_same_next_market_plan_unique_backstop_across_independent_sessions(
    app, admin_user, company
):
    company_id = company.id
    actor_user_id = admin_user.id
    first = ResearchCommandService.create_market_plan_revision(
        company_id,
        actor_user_id=actor_user_id,
        payload=_market_plan_payload(),
    )
    first_id = first.id

    with Session(db.engine) as winner, Session(db.engine) as loser:
        winner.add(
            _market_plan_row(
                company_id=company_id,
                actor_user_id=actor_user_id,
                revision_number=2,
                supersedes_revision_id=first_id,
            )
        )
        winner.commit()

        loser.add(
            _market_plan_row(
                company_id=company_id,
                actor_user_id=actor_user_id,
                revision_number=2,
                supersedes_revision_id=first_id,
            )
        )
        with pytest.raises(IntegrityError):
            loser.commit()
        loser.rollback()

    assert db.session.scalars(
        sa.select(MarketPlanRevision.revision_number)
        .where(MarketPlanRevision.company_id == company_id)
        .order_by(MarketPlanRevision.revision_number)
    ).all() == [1, 2]


def test_invalid_market_plan_values_cause_complete_atomic_rollback(
    app, admin_user, company
):
    with pytest.raises(ResearchValidationError):
        ResearchCommandService.create_market_plan_revision(
            company.id,
            actor_user_id=admin_user.id,
            payload=_market_plan_payload(
                accumulation_low=Decimal("130.0000"),
                accumulation_high=Decimal("100.0000"),
            ),
        )

    assert not db.session().in_transaction()
    assert (
        db.session.scalar(
            sa.select(sa.func.count())
            .select_from(MarketPlanRevision)
            .where(MarketPlanRevision.company_id == company.id)
        )
        == 0
    )


def test_persisted_market_plan_revision_cannot_be_updated(
    app, admin_user, company
):
    revision = ResearchCommandService.create_market_plan_revision(
        company.id,
        actor_user_id=admin_user.id,
        payload=_market_plan_payload(),
    )
    revision_id = revision.id
    original_low = revision.accumulation_low

    revision.accumulation_low = Decimal("1.0000")
    with pytest.raises(sa.exc.InvalidRequestError, match="immutable"):
        db.session.commit()
    db.session.rollback()

    assert (
        db.session.get(MarketPlanRevision, revision_id).accumulation_low
        == original_low
    )


def test_persisted_market_plan_history_cannot_be_deleted(
    app, admin_user, company
):
    revision = ResearchCommandService.create_market_plan_revision(
        company.id,
        actor_user_id=admin_user.id,
        payload=_market_plan_payload(),
    )

    db.session.delete(revision)
    with pytest.raises(sa.exc.InvalidRequestError, match="immutable"):
        db.session.commit()
    db.session.rollback()

    assert db.session.get(MarketPlanRevision, revision.id) is not None


def test_no_update_or_delete_market_plan_commands_exist():
    forbidden = {
        "update_market_plan_revision",
        "patch_market_plan_revision",
        "delete_market_plan_revision",
        "remove_market_plan_revision",
    }
    assert not {
        name
        for name in forbidden
        if hasattr(ResearchCommandService, name)
    }


def test_market_plan_revisions_are_independent_per_company(
    app, admin_user, company, ticker_factory
):
    ResearchCommandService.create_market_plan_revision(
        company.id,
        actor_user_id=admin_user.id,
        payload=_market_plan_payload(),
    )
    second_ticker = ticker_factory(symbol="TATASTEEL", instrument_token=31)
    second_company = ResearchCommandService.create_company(
        {
            "ticker_id": second_ticker.id,
            "legal_name": "Steel Limited",
            "isin": "INE081A01012",
        },
        actor_user_id="actor-1",
    )

    ResearchCommandService.create_market_plan_revision(
        second_company.id,
        actor_user_id=admin_user.id,
        payload=_market_plan_payload(),
    )

    numbers = {
        row[0]: row[1]
        for row in db.session.execute(
            sa.select(
                MarketPlanRevision.company_id,
                MarketPlanRevision.revision_number,
            )
        ).all()
    }
    assert numbers[company.id] == 1
    assert numbers[second_company.id] == 1


def test_service_requires_existing_company_and_actor(
    app, admin_user, company
):
    with pytest.raises(ResearchNotFoundError):
        ResearchCommandService.create_market_plan_revision(
            "missing-company",
            actor_user_id=admin_user.id,
            payload=_market_plan_payload(),
        )

    with pytest.raises(ResearchNotFoundError):
        ResearchCommandService.create_market_plan_revision(
            company.id,
            actor_user_id="missing-user",
            payload=_market_plan_payload(),
        )


def test_market_plan_currency_is_fixed_to_inr(app, admin_user, company):
    revision = ResearchCommandService.create_market_plan_revision(
        company.id,
        actor_user_id=admin_user.id,
        payload=_market_plan_payload(currency="INR"),
    )
    assert revision.currency == "INR"

    with pytest.raises(ResearchValidationError) as exc_info:
        ResearchCommandService.create_market_plan_revision(
            company.id,
            actor_user_id=admin_user.id,
            payload=_market_plan_payload(currency="USD"),
        )
    assert "currency" in exc_info.value.details


def test_market_plan_prices_use_fixed_precision_numeric_columns(app):
    numeric_columns = {
        column.name
        for column in MarketPlanRevision.__table__.columns
        if isinstance(column.type, sa.Numeric)
    }
    assert numeric_columns == {
        "accumulation_low",
        "accumulation_high",
        "preferred_accumulation_price",
        "supply_low",
        "supply_high",
        "invalidation_level",
    }


def test_prior_market_plan_revision_remains_unchanged_after_later_revision(
    app, admin_user, company
):
    first = ResearchCommandService.create_market_plan_revision(
        company.id,
        actor_user_id=admin_user.id,
        payload=_market_plan_payload(),
    )
    original_fields = {
        field: getattr(first, field)
        for field in (
            "revision_number",
            "supersedes_revision_id",
            "accumulation_low",
            "accumulation_high",
            "preferred_accumulation_price",
            "supply_low",
            "supply_high",
            "invalidation_level",
            "rationale",
            "change_reason",
            "effective_at",
        )
    }

    ResearchCommandService.create_market_plan_revision(
        company.id,
        actor_user_id=admin_user.id,
        payload=_market_plan_payload(
            base_revision_id=first.id,
            change_reason="Updated zone",
            accumulation_low=Decimal("90.0000"),
            accumulation_high=Decimal("110.0000"),
            preferred_accumulation_price=Decimal("100.0000"),
            invalidation_level=Decimal("80.0000"),
        ),
    )

    db.session.expire_all()
    persisted = db.session.get(MarketPlanRevision, first.id)
    assert {
        field: getattr(persisted, field)
        for field in original_fields
    } == original_fields
