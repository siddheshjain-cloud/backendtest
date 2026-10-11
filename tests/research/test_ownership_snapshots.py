"""Plan Task 5: append-only ownership snapshot persistence.

These tests lock the frozen ``OwnershipSnapshot`` contract: one dated row per
company, 0-100 percentages, sourced percentages, notes-only snapshots, exact
fixed-precision storage, database rollback on duplicate dates, and immutable
append-only history with no update or delete command.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from app import db
from app.models import OwnershipSnapshot
from app.services.research_command_service import ResearchCommandService
from app.utils.research_errors import (
    ResearchConflictError,
    ResearchNotFoundError,
    ResearchValidationError,
)


VALID_AS_OF = date(2026, 9, 4)
SOURCE = "https://example.in/shareholding-pattern"


def _snapshot_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "as_of_date": VALID_AS_OF,
        "promoter_holding_pct": Decimal("62.35"),
        "promoter_pledge_pct": Decimal("3.12"),
        "notes": "Promoter holding stable during the quarter",
        "source_reference": SOURCE,
    }
    payload.update(overrides)
    return payload


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


@pytest.fixture
def company(ticker_factory):
    ticker = ticker_factory()
    return ResearchCommandService.create_company(
        {
            "ticker_id": ticker.id,
            "legal_name": "IKIO Lighting Limited",
            "isin": "INE0LOJ01019",
        },
        actor_user_id="actor-1",
    )


def test_ownership_snapshot_model_is_exported_from_app_models():
    assert OwnershipSnapshot.__tablename__ == "ownership_snapshot"
    assert callable(ResearchCommandService.add_ownership_snapshot)


def test_ownership_snapshot_table_has_exact_columns_and_no_updated_at(app):
    assert _reflected_columns("ownership_snapshot") == {
        "id": False,
        "created_at": False,
        "company_id": False,
        "as_of_date": False,
        "promoter_holding_pct": True,
        "promoter_pledge_pct": True,
        "notes": True,
        "source_reference": True,
        "created_by_user_id": False,
    }


def test_ownership_percentage_columns_are_numeric_seven_four(app):
    inspector = sa.inspect(db.engine)
    columns = {
        column["name"]: column["type"]
        for column in inspector.get_columns("ownership_snapshot")
    }

    for name in ("promoter_holding_pct", "promoter_pledge_pct"):
        column_type = columns[name]
        assert column_type.precision == 7
        assert column_type.scale == 4


def test_ownership_snapshot_table_has_named_unique_fk_and_range_checks(app):
    inspector = sa.inspect(db.engine)

    unique_constraints = {
        constraint["name"]: sorted(constraint["column_names"])
        for constraint in inspector.get_unique_constraints("ownership_snapshot")
    }
    assert unique_constraints["uq_ownership_snapshot_company_as_of"] == [
        "as_of_date",
        "company_id",
    ]

    foreign_keys = {
        frozenset(constraint["constrained_columns"]): constraint["referred_table"]
        for constraint in inspector.get_foreign_keys("ownership_snapshot")
    }
    assert foreign_keys[frozenset({"company_id"})] == "company"
    assert foreign_keys[frozenset({"created_by_user_id"})] == "user"

    check_names = {
        constraint["name"]
        for constraint in inspector.get_check_constraints("ownership_snapshot")
    }
    assert "ck_ownership_promoter_holding_range" in check_names
    assert "ck_ownership_promoter_pledge_range" in check_names
    assert "ck_ownership_snapshot_source_reference" in check_names


def test_ownership_snapshot_uses_uuid_string_id_and_audit_created_at(
    app, admin_user, company
):
    snapshot = ResearchCommandService.add_ownership_snapshot(
        company.id,
        actor_user_id=admin_user.id,
        payload=_snapshot_payload(),
    )

    assert isinstance(snapshot.id, str)
    assert len(snapshot.id) == 36
    assert uuid.UUID(snapshot.id).version == 4
    assert snapshot.created_at is not None
    assert "updated_at" not in {
        column.name for column in OwnershipSnapshot.__table__.columns
    }


def test_service_persists_complete_snapshot_and_exact_decimals(
    app, admin_user, company
):
    snapshot = ResearchCommandService.add_ownership_snapshot(
        company.id,
        actor_user_id=admin_user.id,
        payload=_snapshot_payload(),
    )

    persisted = db.session.get(OwnershipSnapshot, snapshot.id)
    assert persisted.company_id == company.id
    assert persisted.as_of_date == VALID_AS_OF
    assert persisted.promoter_holding_pct == Decimal("62.35")
    assert persisted.promoter_pledge_pct == Decimal("3.12")
    assert persisted.notes == "Promoter holding stable during the quarter"
    assert persisted.source_reference == SOURCE
    assert persisted.created_by_user_id == admin_user.id


def test_service_allows_a_notes_only_snapshot_without_source(
    app, admin_user, company
):
    snapshot = ResearchCommandService.add_ownership_snapshot(
        company.id,
        actor_user_id=admin_user.id,
        payload=_snapshot_payload(
            promoter_holding_pct=None,
            promoter_pledge_pct=None,
            source_reference=None,
            notes="No new ownership disclosure since the last snapshot",
        ),
    )

    assert snapshot.promoter_holding_pct is None
    assert snapshot.promoter_pledge_pct is None
    assert snapshot.source_reference is None
    assert snapshot.notes is not None


@pytest.mark.parametrize(
    "bad_value",
    [
        Decimal("-0.0001"),
        Decimal("100.0001"),
        Decimal("150"),
        float(50),
        "50",
        True,
    ],
)
def test_service_rejects_percentages_outside_zero_through_100(
    app, admin_user, company, bad_value
):
    for percentage_field in ("promoter_holding_pct", "promoter_pledge_pct"):
        with pytest.raises(ResearchValidationError) as exc_info:
            ResearchCommandService.add_ownership_snapshot(
                company.id,
                actor_user_id=admin_user.id,
                payload=_snapshot_payload(
                    **{percentage_field: bad_value},
                    source_reference=SOURCE,
                ),
            )

        assert exc_info.value.code == "validation_error"
        assert percentage_field in exc_info.value.details
        assert not db.session().in_transaction()

    assert (
        db.session.scalar(
            sa.select(sa.func.count()).select_from(OwnershipSnapshot)
        )
        == 0
    )


def test_service_requires_source_reference_when_either_percentage_is_present(
    app, admin_user, company
):
    incomplete_payloads = [
        _snapshot_payload(promoter_pledge_pct=None, source_reference=None),
        _snapshot_payload(promoter_holding_pct=None, source_reference=None),
    ]

    for payload in incomplete_payloads:
        with pytest.raises(ResearchValidationError) as exc_info:
            ResearchCommandService.add_ownership_snapshot(
                company.id,
                actor_user_id=admin_user.id,
                payload=payload,
            )
        assert exc_info.value.code == "validation_error"
        assert "source_reference" in exc_info.value.details
        assert not db.session().in_transaction()

    assert (
        db.session.scalar(
            sa.select(sa.func.count()).select_from(OwnershipSnapshot)
        )
        == 0
    )


def test_source_reference_is_required_at_database_when_a_percentage_exists(
    app, admin_user, company
):
    db.session.add(
        OwnershipSnapshot(
            company_id=company.id,
            as_of_date=VALID_AS_OF,
            promoter_holding_pct=Decimal("62.35"),
            created_by_user_id=admin_user.id,
        )
    )
    _commit_expect_integrity()

    assert (
        db.session.scalar(
            sa.select(sa.func.count()).select_from(OwnershipSnapshot)
        )
        == 0
    )


def test_percentage_range_is_enforced_at_database(
    app, admin_user, company
):
    db.session.add(
        OwnershipSnapshot(
            company_id=company.id,
            as_of_date=VALID_AS_OF,
            promoter_holding_pct=Decimal("100.0001"),
            source_reference=SOURCE,
            created_by_user_id=admin_user.id,
        )
    )
    _commit_expect_integrity()

    assert (
        db.session.scalar(
            sa.select(sa.func.count()).select_from(OwnershipSnapshot)
        )
        == 0
    )


def test_company_and_as_of_date_are_unique_at_database(
    app, admin_user, company
):
    db.session.add_all(
        [
            OwnershipSnapshot(
                company_id=company.id,
                as_of_date=VALID_AS_OF,
                promoter_holding_pct=Decimal("60"),
                source_reference=SOURCE,
                created_by_user_id=admin_user.id,
            ),
            OwnershipSnapshot(
                company_id=company.id,
                as_of_date=VALID_AS_OF,
                promoter_holding_pct=Decimal("62"),
                source_reference=SOURCE,
                created_by_user_id=admin_user.id,
            ),
        ]
    )
    _commit_expect_integrity()

    assert (
        db.session.scalar(
            sa.select(sa.func.count()).select_from(OwnershipSnapshot)
        )
        == 0
    )


def test_service_duplicate_date_rolls_back_without_overwriting(
    app, admin_user, company
):
    first = ResearchCommandService.add_ownership_snapshot(
        company.id,
        actor_user_id=admin_user.id,
        payload=_snapshot_payload(),
    )

    with pytest.raises(ResearchConflictError) as exc_info:
        ResearchCommandService.add_ownership_snapshot(
            company.id,
            actor_user_id=admin_user.id,
            payload=_snapshot_payload(
                promoter_holding_pct=Decimal("60"),
            ),
        )

    assert exc_info.value.code == "ownership_snapshot_conflict"
    assert not db.session().in_transaction()
    assert (
        db.session.scalar(
            sa.select(sa.func.count()).select_from(OwnershipSnapshot)
        )
        == 1
    )
    assert db.session.get(OwnershipSnapshot, first.id).promoter_holding_pct == Decimal(
        "62.35"
    )


def test_service_requires_existing_company_and_actor_user(
    app, admin_user, company
):
    with pytest.raises(ResearchNotFoundError):
        ResearchCommandService.add_ownership_snapshot(
            "missing-company",
            actor_user_id=admin_user.id,
            payload=_snapshot_payload(),
        )

    with pytest.raises(ResearchNotFoundError):
        ResearchCommandService.add_ownership_snapshot(
            company.id,
            actor_user_id="missing-user",
            payload=_snapshot_payload(),
        )


def test_service_requires_as_of_date_and_rejects_unknown_write_fields(
    app, admin_user, company
):
    missing_date_payload = _snapshot_payload()
    missing_date_payload.pop("as_of_date")
    with pytest.raises(ResearchValidationError) as exc_info:
        ResearchCommandService.add_ownership_snapshot(
            company.id,
            actor_user_id=admin_user.id,
            payload=missing_date_payload,
        )
    assert "as_of_date" in exc_info.value.details

    with pytest.raises(ResearchValidationError) as exc_info:
        ResearchCommandService.add_ownership_snapshot(
            company.id,
            actor_user_id=admin_user.id,
            payload=_snapshot_payload(updated_at="2026-09-05T00:00:00Z"),
        )
    assert "updated_at" in exc_info.value.details
    assert not db.session().in_transaction()


def test_different_dates_form_append_only_history(app, admin_user, company):
    first = ResearchCommandService.add_ownership_snapshot(
        company.id,
        actor_user_id=admin_user.id,
        payload=_snapshot_payload(),
    )
    second = ResearchCommandService.add_ownership_snapshot(
        company.id,
        actor_user_id=admin_user.id,
        payload=_snapshot_payload(
            as_of_date=date(2026, 10, 4),
            promoter_holding_pct=Decimal("60.10"),
        ),
    )

    snapshots = db.session.scalars(
        sa.select(OwnershipSnapshot)
        .where(OwnershipSnapshot.company_id == company.id)
        .order_by(OwnershipSnapshot.as_of_date)
    ).all()

    assert [snapshot.id for snapshot in snapshots] == [first.id, second.id]
    assert snapshots[0].promoter_holding_pct == Decimal("62.35")
    assert snapshots[1].promoter_holding_pct == Decimal("60.10")


def test_persisted_ownership_snapshot_cannot_be_updated(
    app, admin_user, company
):
    snapshot = ResearchCommandService.add_ownership_snapshot(
        company.id,
        actor_user_id=admin_user.id,
        payload=_snapshot_payload(),
    )
    snapshot_id = snapshot.id
    original_notes = snapshot.notes

    snapshot.notes = "Silently rewritten ownership history"
    with pytest.raises(sa.exc.InvalidRequestError, match="immutable"):
        db.session.commit()
    db.session.rollback()

    assert db.session.get(OwnershipSnapshot, snapshot_id).notes == original_notes


def test_persisted_ownership_snapshot_cannot_be_deleted(
    app, admin_user, company
):
    snapshot = ResearchCommandService.add_ownership_snapshot(
        company.id,
        actor_user_id=admin_user.id,
        payload=_snapshot_payload(),
    )

    db.session.delete(snapshot)
    with pytest.raises(sa.exc.InvalidRequestError, match="immutable"):
        db.session.commit()
    db.session.rollback()

    assert db.session.get(OwnershipSnapshot, snapshot.id) is not None


def test_no_update_or_delete_ownership_snapshot_commands_exist():
    forbidden = {
        "update_ownership_snapshot",
        "delete_ownership_snapshot",
    }
    assert not {
        name for name in forbidden if hasattr(ResearchCommandService, name)
    }
