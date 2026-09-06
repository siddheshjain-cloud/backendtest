"""Plan 2 Task 4: immutable narrative research revisions and concurrency.

These tests lock the frozen ``ResearchRevision``/``ResearchPoint`` contract,
first-versus-subsequent revision semantics, the stale-base/unique-race conflict
translation, atomic header-plus-points transactions, append-only immutability,
and the point-in-time meaning of ``effective_at``/``created_at``.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from app import db
from app.models import Company, ResearchPoint, ResearchRevision
from app.models.research_types import (
    GovernanceStatus,
    ManagementQuality,
    ResearchPointKind,
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


def _revision_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "why_selected": "Exposed to durable LED-lighting demand growth",
        "what_is_changing": "Capacity expansion and customer concentration",
        "business_journey": "Supplier-led manufacturing scale-up",
        "thesis": "Operating leverage and export tailwinds compound",
        "thesis_invalidation": "Customer concentration or margin erosion",
        "management_summary": "Experienced promoter-led execution",
        "management_quality": ManagementQuality.ACCEPTABLE,
        "management_rationale": "Long promoter tenure with consistent delivery",
        "management_evidence": "Annual reports and investor call transcripts",
        "governance_status": GovernanceStatus.CLEAR,
        "effective_at": EFFECTIVE_AT,
        "points": [
            {
                "kind": ResearchPointKind.CATALYST,
                "title": "New capacity commissioning",
                "detail": "Production capacity ramps during FY27",
                "status": "OPEN",
                "target_date": date(2027, 3, 31),
                "sort_order": 0,
            },
            {
                "kind": ResearchPointKind.RISK,
                "title": "Customer concentration",
                "detail": "Top customer concentration remains high",
                "sort_order": 0,
            },
        ],
    }
    payload.update(overrides)
    return payload


def _point(kind: str, sort_order: int, **overrides: object) -> dict[str, object]:
    point: dict[str, object] = {
        "kind": kind,
        "title": f"Point {sort_order}",
        "sort_order": sort_order,
    }
    point.update(overrides)
    return point


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


def _reflected_columns(table_name: str) -> dict[str, bool]:
    inspector = sa.inspect(db.engine)
    return {
        column["name"]: column["nullable"]
        for column in inspector.get_columns(table_name)
    }


def test_revision_models_are_exported_from_app_models():
    assert ResearchRevision.__tablename__ == "research_revision"
    assert ResearchPoint.__tablename__ == "research_point"


def test_first_revision_has_number_one_null_supersedes_and_null_change_reason(
    app, admin_user, company
):
    revision = ResearchCommandService.create_research_revision(
        company.id,
        actor_user_id=admin_user.id,
        payload=_revision_payload(change_reason=None),
    )

    persisted = db.session.get(ResearchRevision, revision.id)
    assert persisted.revision_number == 1
    assert persisted.supersedes_revision_id is None
    assert persisted.change_reason is None
    assert persisted.company_id == company.id
    assert persisted.created_by_user_id == admin_user.id
    assert persisted.why_selected == _revision_payload()["why_selected"]
    assert persisted.what_is_changing == _revision_payload()["what_is_changing"]
    assert persisted.business_journey == _revision_payload()["business_journey"]
    assert persisted.thesis == _revision_payload()["thesis"]
    assert (
        persisted.thesis_invalidation
        == _revision_payload()["thesis_invalidation"]
    )
    assert persisted.management_quality == ManagementQuality.ACCEPTABLE
    assert (
        persisted.management_rationale
        == _revision_payload()["management_rationale"]
    )
    assert persisted.governance_status == GovernanceStatus.CLEAR
    assert _utc_naive(persisted.effective_at) == _utc_naive(EFFECTIVE_AT)
    assert persisted.created_at is not None


def test_first_revision_accepts_absent_or_null_base_revision_id(
    app, admin_user, company, ticker_factory
):
    without_base = _revision_payload()
    without_base.pop("base_revision_id", None)

    first = ResearchCommandService.create_research_revision(
        company.id, actor_user_id=admin_user.id, payload=without_base
    )
    second_ticker = ticker_factory(symbol="WIPRO", instrument_token=11)
    second_company = ResearchCommandService.create_company(
        {
            "ticker_id": second_ticker.id,
            "legal_name": "Second Limited",
            "isin": "US0378331005",
        },
        actor_user_id="actor-1",
    )

    with_null = _revision_payload(base_revision_id=None)
    second = ResearchCommandService.create_research_revision(
        second_company.id,
        actor_user_id=admin_user.id,
        payload=with_null,
    )

    assert first.revision_number == 1
    assert second.revision_number == 1
    assert first.supersedes_revision_id is None
    assert second.supersedes_revision_id is None


def test_first_revision_rejects_a_non_null_base_revision_id(
    app, admin_user, company
):
    with pytest.raises(ResearchConflictError) as exc_info:
        ResearchCommandService.create_research_revision(
            company.id,
            actor_user_id=admin_user.id,
            payload=_revision_payload(base_revision_id="does-not-exist"),
        )

    assert exc_info.value.code == "revision_conflict"
    assert exc_info.value.message == "Research revision changed"


def test_first_revision_rejects_a_non_null_change_reason(
    app, admin_user, company
):
    with pytest.raises(ResearchValidationError) as exc_info:
        ResearchCommandService.create_research_revision(
            company.id,
            actor_user_id=admin_user.id,
            payload=_revision_payload(change_reason="Initial creation"),
        )

    assert exc_info.value.code == "validation_error"
    assert "change_reason" in exc_info.value.details


def test_ordered_points_are_persisted_with_their_revision(
    app, admin_user, company
):
    revision = ResearchCommandService.create_research_revision(
        company.id,
        actor_user_id=admin_user.id,
        payload=_revision_payload(),
    )

    points = db.session.scalars(
        sa.select(ResearchPoint)
        .where(ResearchPoint.research_revision_id == revision.id)
        .order_by(ResearchPoint.kind, ResearchPoint.sort_order)
    ).all()

    assert [(p.kind, p.sort_order, p.title) for p in points] == [
        (ResearchPointKind.CATALYST, 0, "New capacity commissioning"),
        (ResearchPointKind.RISK, 0, "Customer concentration"),
    ]
    assert all(p.research_revision_id == revision.id for p in points)


def test_point_sort_order_must_be_a_non_negative_integer(
    app, admin_user, company
):
    for bad_sort_order in (-1, 1.5, True, "0"):
        with pytest.raises(ResearchValidationError) as exc_info:
            ResearchCommandService.create_research_revision(
                company.id,
                actor_user_id=admin_user.id,
                payload=_revision_payload(
                    points=[
                        _point(
                            ResearchPointKind.CATALYST,
                            bad_sort_order,
                        )
                    ]
                ),
            )
        assert exc_info.value.code == "validation_error"
        assert "points" in exc_info.value.details


def test_points_are_unique_by_revision_kind_and_sort_order_at_database(
    app, admin_user, company
):
    revision = ResearchCommandService.create_research_revision(
        company.id,
        actor_user_id=admin_user.id,
        payload=_revision_payload(points=[]),
    )

    db.session.add_all(
        [
            ResearchPoint(
                research_revision_id=revision.id,
                kind=ResearchPointKind.CATALYST,
                title="First",
                sort_order=0,
            ),
            ResearchPoint(
                research_revision_id=revision.id,
                kind=ResearchPointKind.CATALYST,
                title="Duplicate",
                sort_order=0,
            ),
        ]
    )
    _commit_expect_integrity()

    count = db.session.scalar(
        sa.select(sa.func.count())
        .select_from(ResearchPoint)
        .where(ResearchPoint.research_revision_id == revision.id)
    )
    assert count == 0


def test_second_revision_requires_current_base_and_increments_and_supersedes(
    app, admin_user, company
):
    first = ResearchCommandService.create_research_revision(
        company.id,
        actor_user_id=admin_user.id,
        payload=_revision_payload(),
    )

    with pytest.raises(ResearchValidationError) as exc_info:
        ResearchCommandService.create_research_revision(
            company.id,
            actor_user_id=admin_user.id,
            payload=_revision_payload(base_revision_id=None),
        )
    assert "base_revision_id" in exc_info.value.details

    second = ResearchCommandService.create_research_revision(
        company.id,
        actor_user_id=admin_user.id,
        payload=_revision_payload(
            base_revision_id=first.id,
            change_reason="Capacity thesis updated",
            points=[
                _point(ResearchPointKind.CATALYST, 0),
                _point(ResearchPointKind.RISK, 0),
            ],
        ),
    )

    assert second.revision_number == 2
    assert second.supersedes_revision_id == first.id
    assert second.change_reason == "Capacity thesis updated"


def test_second_revision_requires_non_empty_change_reason(
    app, admin_user, company
):
    first = ResearchCommandService.create_research_revision(
        company.id,
        actor_user_id=admin_user.id,
        payload=_revision_payload(),
    )

    for bad_change_reason in (None, "", "   "):
        with pytest.raises(ResearchValidationError) as exc_info:
            ResearchCommandService.create_research_revision(
                company.id,
                actor_user_id=admin_user.id,
                payload=_revision_payload(
                    base_revision_id=first.id,
                    change_reason=bad_change_reason,
                ),
            )
        assert exc_info.value.code == "validation_error"
        assert "change_reason" in exc_info.value.details


def test_stale_base_raises_revision_conflict_without_overwriting(
    app, admin_user, company
):
    first = ResearchCommandService.create_research_revision(
        company.id,
        actor_user_id=admin_user.id,
        payload=_revision_payload(),
    )
    ResearchCommandService.create_research_revision(
        company.id,
        actor_user_id=admin_user.id,
        payload=_revision_payload(
            base_revision_id=first.id,
            change_reason="First update",
        ),
    )

    with pytest.raises(ResearchConflictError) as exc_info:
        ResearchCommandService.create_research_revision(
            company.id,
            actor_user_id=admin_user.id,
            payload=_revision_payload(
                base_revision_id=first.id,
                change_reason="Stale update",
            ),
        )

    assert exc_info.value.code == "revision_conflict"
    assert exc_info.value.message == "Research revision changed"

    revision_numbers = db.session.scalars(
        sa.select(ResearchRevision.revision_number)
        .where(ResearchRevision.company_id == company.id)
        .order_by(ResearchRevision.revision_number)
    ).all()
    assert revision_numbers == [1, 2]


def test_thesis_invalidation_is_required(app, admin_user, company):
    payload = _revision_payload()
    payload.pop("thesis_invalidation")

    with pytest.raises(ResearchValidationError) as exc_info:
        ResearchCommandService.create_research_revision(
            company.id,
            actor_user_id=admin_user.id,
            payload=payload,
        )

    assert "thesis_invalidation" in exc_info.value.details


def test_management_rationale_is_required_when_quality_is_not_unassessed(
    app, admin_user, company
):
    payload = _revision_payload(management_rationale=None)

    with pytest.raises(ResearchValidationError) as exc_info:
        ResearchCommandService.create_research_revision(
            company.id,
            actor_user_id=admin_user.id,
            payload=payload,
        )

    assert "management_rationale" in exc_info.value.details


def test_unassessed_management_quality_allows_null_rationale(
    app, admin_user, company
):
    revision = ResearchCommandService.create_research_revision(
        company.id,
        actor_user_id=admin_user.id,
        payload=_revision_payload(
            management_quality=ManagementQuality.UNASSESSED,
            management_rationale=None,
            management_evidence=None,
        ),
    )

    persisted = db.session.get(ResearchRevision, revision.id)
    assert persisted.management_quality == ManagementQuality.UNASSESSED
    assert persisted.management_rationale is None


def test_company_and_revision_number_are_unique_at_database(
    app, admin_user, company
):
    first = ResearchCommandService.create_research_revision(
        company.id,
        actor_user_id=admin_user.id,
        payload=_revision_payload(points=[]),
    )

    db.session.add(
        ResearchRevision(
            company_id=company.id,
            revision_number=first.revision_number,
            why_selected="Duplicate",
            thesis="Duplicate",
            thesis_invalidation="Duplicate",
            management_quality=ManagementQuality.UNASSESSED,
            governance_status=GovernanceStatus.UNREVIEWED,
            effective_at=EFFECTIVE_AT,
            created_by_user_id=admin_user.id,
        )
    )
    _commit_expect_integrity()

    count = db.session.scalar(
        sa.select(sa.func.count())
        .select_from(ResearchRevision)
        .where(ResearchRevision.company_id == company.id)
    )
    assert count == 1


def test_invalid_child_causes_complete_atomic_rollback(
    app, admin_user, company
):
    with pytest.raises(ResearchValidationError):
        ResearchCommandService.create_research_revision(
            company.id,
            actor_user_id=admin_user.id,
            payload=_revision_payload(
                points=[
                    _point(ResearchPointKind.CATALYST, 0),
                    _point(ResearchPointKind.CATALYST, "bad"),
                ]
            ),
        )

    assert (
        db.session.scalar(
            sa.select(sa.func.count())
            .select_from(ResearchRevision)
            .where(ResearchRevision.company_id == company.id)
        )
        == 0
    )
    assert db.session.scalar(sa.select(sa.func.count()).select_from(ResearchPoint)) == 0


def test_prior_revision_remains_unchanged_after_later_revision(
    app, admin_user, company
):
    first = ResearchCommandService.create_research_revision(
        company.id,
        actor_user_id=admin_user.id,
        payload=_revision_payload(),
    )
    original_fields = {
        field: getattr(first, field)
        for field in (
            "revision_number",
            "supersedes_revision_id",
            "why_selected",
            "thesis",
            "thesis_invalidation",
            "change_reason",
            "management_quality",
            "management_rationale",
            "governance_status",
            "effective_at",
        )
    }

    ResearchCommandService.create_research_revision(
        company.id,
        actor_user_id=admin_user.id,
        payload=_revision_payload(
            base_revision_id=first.id,
            change_reason="Updated thesis",
            thesis="A substantially different updated thesis",
        ),
    )

    db.session.expire_all()
    persisted = db.session.get(ResearchRevision, first.id)
    assert {
        field: getattr(persisted, field)
        for field in original_fields
    } == original_fields


def test_existing_points_remain_unchanged_after_later_revision(
    app, admin_user, company
):
    first = ResearchCommandService.create_research_revision(
        company.id,
        actor_user_id=admin_user.id,
        payload=_revision_payload(),
    )
    first_points = db.session.scalars(
        sa.select(ResearchPoint)
        .where(ResearchPoint.research_revision_id == first.id)
        .order_by(ResearchPoint.kind, ResearchPoint.sort_order)
    ).all()
    first_snapshot = [
        (
            p.kind,
            p.sort_order,
            p.title,
            p.detail,
            p.status,
            p.target_date,
        )
        for p in first_points
    ]

    ResearchCommandService.create_research_revision(
        company.id,
        actor_user_id=admin_user.id,
        payload=_revision_payload(
            base_revision_id=first.id,
            change_reason="New revision with different points",
            points=[_point(ResearchPointKind.CATALYST, 0)],
        ),
    )

    db.session.expire_all()
    persisted_points = db.session.scalars(
        sa.select(ResearchPoint)
        .where(ResearchPoint.research_revision_id == first.id)
        .order_by(ResearchPoint.kind, ResearchPoint.sort_order)
    ).all()
    assert [
        (
            p.kind,
            p.sort_order,
            p.title,
            p.detail,
            p.status,
            p.target_date,
        )
        for p in persisted_points
    ] == first_snapshot


def test_no_update_or_delete_research_revision_commands_exist():
    forbidden = {
        "update_research_revision",
        "patch_research_revision",
        "delete_research_revision",
    }
    assert not {name for name in forbidden if hasattr(ResearchCommandService, name)}


def test_no_update_or_delete_research_point_commands_exist():
    forbidden = {
        "update_research_point",
        "delete_research_point",
    }
    assert not {name for name in forbidden if hasattr(ResearchCommandService, name)}


def test_revision_has_no_mutable_updated_at_column():
    assert "updated_at" not in {
        column.name for column in ResearchRevision.__table__.columns
    }


def test_point_has_no_mutable_updated_at_column():
    assert "updated_at" not in {
        column.name for column in ResearchPoint.__table__.columns
    }


def test_created_at_is_audit_timestamp_and_effective_at_is_required(
    app, admin_user, company
):
    revision = ResearchCommandService.create_research_revision(
        company.id,
        actor_user_id=admin_user.id,
        payload=_revision_payload(),
    )

    assert revision.created_at is not None
    assert revision.effective_at is not None
    assert _utc_naive(revision.effective_at) == _utc_naive(EFFECTIVE_AT)
    assert (
        ResearchRevision.__table__.columns["created_at"].nullable is False
    )
    assert (
        ResearchRevision.__table__.columns["effective_at"].nullable is False
    )


def test_no_confidence_or_evidence_cutoff_fields_exist():
    revision_columns = {
        column.name for column in ResearchRevision.__table__.columns
    }
    point_columns = {column.name for column in ResearchPoint.__table__.columns}
    forbidden = {
        "confidence",
        "research_confidence",
        "evidence_cutoff",
        "evidence_cutoff_at",
        "methodology_version",
    }

    assert not (revision_columns & forbidden)
    assert not (point_columns & forbidden)


def test_revision_tables_have_exact_columns_and_constraints(app):
    assert _reflected_columns("research_revision") == {
        "id": False,
        "created_at": False,
        "company_id": False,
        "revision_number": False,
        "supersedes_revision_id": True,
        "why_selected": False,
        "what_is_changing": True,
        "business_journey": True,
        "thesis": False,
        "thesis_invalidation": False,
        "management_summary": True,
        "management_quality": False,
        "management_rationale": True,
        "management_evidence": True,
        "governance_status": False,
        "change_reason": True,
        "effective_at": False,
        "created_by_user_id": False,
    }
    assert _reflected_columns("research_point") == {
        "id": False,
        "created_at": False,
        "research_revision_id": False,
        "kind": False,
        "title": False,
        "detail": True,
        "status": True,
        "target_date": True,
        "sort_order": False,
    }

    inspector = sa.inspect(db.engine)
    revision_unique = {
        constraint["name"]: sorted(constraint["column_names"])
        for constraint in inspector.get_unique_constraints("research_revision")
    }
    assert revision_unique["uq_research_revision_company_number"] == [
        "company_id",
        "revision_number",
    ]

    point_unique = {
        constraint["name"]: sorted(constraint["column_names"])
        for constraint in inspector.get_unique_constraints("research_point")
    }
    assert point_unique["uq_research_point_revision_kind_sort"] == [
        "kind",
        "research_revision_id",
        "sort_order",
    ]

    revision_foreign_keys = {
        frozenset(constraint["constrained_columns"]): constraint["referred_table"]
        for constraint in inspector.get_foreign_keys("research_revision")
    }
    assert revision_foreign_keys[frozenset({"company_id"})] == "company"
    assert revision_foreign_keys[frozenset({"created_by_user_id"})] == "user"
    assert revision_foreign_keys[frozenset({"supersedes_revision_id"})] == "research_revision"

    point_foreign_keys = {
        frozenset(constraint["constrained_columns"]): constraint["referred_table"]
        for constraint in inspector.get_foreign_keys("research_point")
    }
    assert (
        point_foreign_keys[frozenset({"research_revision_id"})]
        == "research_revision"
    )

    revision_check_names = {
        constraint["name"]
        for constraint in inspector.get_check_constraints("research_revision")
    }
    point_check_names = {
        constraint["name"]
        for constraint in inspector.get_check_constraints("research_point")
    }
    assert "ck_research_revision_number_positive" in revision_check_names
    assert "ck_research_point_sort_order_nonnegative" in point_check_names


def test_service_requires_existing_company(app, admin_user):
    with pytest.raises(ResearchNotFoundError):
        ResearchCommandService.create_research_revision(
            "missing-company",
            actor_user_id=admin_user.id,
            payload=_revision_payload(),
        )


def test_service_requires_existing_actor_user(app, company):
    with pytest.raises(ResearchNotFoundError):
        ResearchCommandService.create_research_revision(
            company.id,
            actor_user_id="missing-user",
            payload=_revision_payload(),
        )


def test_unique_race_is_translated_to_revision_conflict(
    app, admin_user, company, monkeypatch
):
    first = ResearchCommandService.create_research_revision(
        company.id,
        actor_user_id=admin_user.id,
        payload=_revision_payload(),
    )
    second = ResearchCommandService.create_research_revision(
        company.id,
        actor_user_id=admin_user.id,
        payload=_revision_payload(
            base_revision_id=first.id,
            change_reason="Winning writer",
        ),
    )

    def _stale_current(_cls, _company_id):
        return first

    monkeypatch.setattr(
        ResearchCommandService,
        "_current_revision_locked",
        classmethod(_stale_current),
    )

    with pytest.raises(ResearchConflictError) as exc_info:
        ResearchCommandService.create_research_revision(
            company.id,
            actor_user_id=admin_user.id,
            payload=_revision_payload(
                base_revision_id=first.id,
                change_reason="Losing writer",
            ),
        )

    assert exc_info.value.code == "revision_conflict"
    assert exc_info.value.message == "Research revision changed"

    revision_numbers = db.session.scalars(
        sa.select(ResearchRevision.revision_number)
        .where(ResearchRevision.company_id == company.id)
        .order_by(ResearchRevision.revision_number)
    ).all()
    assert revision_numbers == [1, 2]
