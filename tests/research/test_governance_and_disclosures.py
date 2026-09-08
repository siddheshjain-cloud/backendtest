"""Plan 2 Task 6: governance flags and manually curated disclosures.

These tests lock the frozen ``GovernanceFlag``/``CompanyDisclosure`` contract:
sourced factual evidence separated from interpretation, closed severity/status
values, resolved-date cross-field rules, archival preservation through the
update commands, the manual ``is_key`` boolean with no numeric importance
field, extensible uppercase disclosure event slugs, and the pre-Document
``document_id`` placeholder that must remain null in this plan.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from app import db
from app.models import CompanyDisclosure, GovernanceFlag
from app.models.research_types import (
    GovernanceFlagStatus,
    GovernanceSeverity,
)
from app.services.research_command_service import ResearchCommandService
from app.utils.research_errors import (
    ResearchNotFoundError,
    ResearchValidationError,
)


OBSERVED_ON = date(2026, 8, 1)
RESOLVED_ON = date(2026, 8, 31)
EVENT_DATE = date(2026, 8, 8)
SOURCE_REFERENCE = "https://example.in/official-disclosure"
FLAG_TITLE = "Promoter pledge crosses disclosure threshold"
DISCLOSURE_TITLE = "Disclosure under Regulation 30 of SEBI LODR"


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


def _flag_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "flag_type": "PROMOTER_PLEDGE",
        "title": FLAG_TITLE,
        "severity": GovernanceSeverity.HIGH,
        "status": GovernanceFlagStatus.OPEN,
        "factual_evidence": (
            "Quarterly shareholding pattern shows 18.5% of equity pledged"
        ),
        "source_title": "Shareholding pattern disclosure",
        "source_url_or_reference": SOURCE_REFERENCE,
        "interpretation": (
            "Elevated pledge may increase refinancing and control risk"
        ),
        "observed_on": OBSERVED_ON,
    }
    payload.update(overrides)
    return payload


def _disclosure_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "event_type": "REG30",
        "event_date": EVENT_DATE,
        "title": DISCLOSURE_TITLE,
        "original_source_url_or_reference": SOURCE_REFERENCE,
        "exchange_reference": "NSE:IKIO",
        "significance_note": "Capacity expansion update",
    }
    payload.update(overrides)
    return payload


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


def test_governance_and_disclosure_models_are_exported():
    assert GovernanceFlag.__tablename__ == "governance_flag"
    assert CompanyDisclosure.__tablename__ == "company_disclosure"
    assert callable(ResearchCommandService.create_governance_flag)
    assert callable(ResearchCommandService.update_governance_flag)
    assert callable(ResearchCommandService.create_disclosure)
    assert callable(ResearchCommandService.update_disclosure)


def test_governance_and_disclosure_tables_have_exact_columns(app):
    assert _reflected_columns("governance_flag") == {
        "id": False,
        "created_at": False,
        "updated_at": False,
        "archived_at": True,
        "company_id": False,
        "flag_type": False,
        "title": False,
        "severity": False,
        "status": False,
        "factual_evidence": False,
        "source_title": True,
        "source_url_or_reference": False,
        "interpretation": False,
        "observed_on": True,
        "resolved_on": True,
        "created_by_user_id": False,
    }
    assert _reflected_columns("company_disclosure") == {
        "id": False,
        "created_at": False,
        "updated_at": False,
        "archived_at": True,
        "company_id": False,
        "event_type": False,
        "event_date": False,
        "title": False,
        "original_source_url_or_reference": False,
        "exchange_reference": True,
        "significance_note": True,
        "is_key": False,
        "document_id": True,
        "created_by_user_id": False,
    }


def test_governance_and_disclosure_tables_have_named_foreign_keys_and_checks(
    app,
):
    inspector = sa.inspect(db.engine)

    governance_foreign_keys = {
        frozenset(constraint["constrained_columns"]): constraint["referred_table"]
        for constraint in inspector.get_foreign_keys("governance_flag")
    }
    assert governance_foreign_keys[frozenset({"company_id"})] == "company"
    assert governance_foreign_keys[frozenset({"created_by_user_id"})] == "user"

    disclosure_foreign_keys = {
        frozenset(constraint["constrained_columns"]): constraint["referred_table"]
        for constraint in inspector.get_foreign_keys("company_disclosure")
    }
    assert disclosure_foreign_keys[frozenset({"company_id"})] == "company"
    assert disclosure_foreign_keys[frozenset({"created_by_user_id"})] == "user"

    governance_checks = {
        constraint["name"]
        for constraint in inspector.get_check_constraints("governance_flag")
    }
    assert "ck_governance_flag_resolved_state" in governance_checks
    assert "ck_governance_flag_resolved_after_observed" in governance_checks


def test_disclosure_event_type_is_a_length_limited_string_not_an_enum(app):
    inspector = sa.inspect(db.engine)
    event_type_column = next(
        column
        for column in inspector.get_columns("company_disclosure")
        if column["name"] == "event_type"
    )

    assert event_type_column["type"].length == 64
    assert not isinstance(event_type_column["type"], sa.Enum)


def test_governance_flag_and_disclosure_use_uuid_ids_and_audit_timestamps(
    app, admin_user, company
):
    flag = ResearchCommandService.create_governance_flag(
        company.id,
        actor_user_id=admin_user.id,
        payload=_flag_payload(),
    )
    disclosure = ResearchCommandService.create_disclosure(
        company.id,
        actor_user_id=admin_user.id,
        payload=_disclosure_payload(),
    )

    for record in (flag, disclosure):
        assert isinstance(record.id, str)
        assert len(record.id) == 36
        assert uuid.UUID(record.id).version == 4
        assert record.created_at is not None
        assert record.updated_at is not None
        assert record.archived_at is None


def test_service_persists_complete_governance_flag(
    app, admin_user, company
):
    flag = ResearchCommandService.create_governance_flag(
        company.id,
        actor_user_id=admin_user.id,
        payload=_flag_payload(),
    )

    persisted = db.session.get(GovernanceFlag, flag.id)
    assert persisted.company_id == company.id
    assert persisted.flag_type == "PROMOTER_PLEDGE"
    assert persisted.title == FLAG_TITLE
    assert persisted.severity == GovernanceSeverity.HIGH
    assert persisted.status == GovernanceFlagStatus.OPEN
    assert "Quarterly shareholding pattern" in persisted.factual_evidence
    assert persisted.source_title == "Shareholding pattern disclosure"
    assert persisted.source_url_or_reference == SOURCE_REFERENCE
    assert "control risk" in persisted.interpretation
    assert persisted.observed_on == OBSERVED_ON
    assert persisted.resolved_on is None
    assert persisted.created_by_user_id == admin_user.id


@pytest.mark.parametrize(
    "field",
    [
        "flag_type",
        "title",
        "severity",
        "status",
        "factual_evidence",
        "source_url_or_reference",
        "interpretation",
    ],
)
def test_service_create_governance_flag_requires_core_fields(
    app, admin_user, company, field
):
    payload = _flag_payload()
    payload.pop(field)

    with pytest.raises(ResearchValidationError) as exc_info:
        ResearchCommandService.create_governance_flag(
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
        "factual_evidence",
        "source_url_or_reference",
        "interpretation",
    ],
)
def test_service_rejects_blank_required_governance_evidence_fields(
    app, admin_user, company, field
):
    with pytest.raises(ResearchValidationError) as exc_info:
        ResearchCommandService.create_governance_flag(
            company.id,
            actor_user_id=admin_user.id,
            payload=_flag_payload(**{field: "   "}),
        )

    assert field in exc_info.value.details
    assert not db.session().in_transaction()


@pytest.mark.parametrize(
    "severity",
    [
        "INFORMATIONAL",
        "HIGH_RISK",
        "OPEN",
        "",
        "high",
        3,
        None,
    ],
)
def test_service_rejects_invalid_governance_severity(
    app, admin_user, company, severity
):
    with pytest.raises(ResearchValidationError) as exc_info:
        ResearchCommandService.create_governance_flag(
            company.id,
            actor_user_id=admin_user.id,
            payload=_flag_payload(severity=severity),
        )

    assert exc_info.value.code == "validation_error"
    assert "severity" in exc_info.value.details


@pytest.mark.parametrize(
    "status",
    [
        "RESOLVE",
        "CLOSED",
        "ARCHIVED",
        "open",
        "",
        1,
        None,
    ],
)
def test_service_rejects_invalid_governance_status(
    app, admin_user, company, status
):
    with pytest.raises(ResearchValidationError) as exc_info:
        ResearchCommandService.create_governance_flag(
            company.id,
            actor_user_id=admin_user.id,
            payload=_flag_payload(status=status),
        )

    assert exc_info.value.code == "validation_error"
    assert "status" in exc_info.value.details


def test_resolved_flag_requires_resolved_on_and_rejects_it_for_other_states(
    app, admin_user, company
):
    resolved = ResearchCommandService.create_governance_flag(
        company.id,
        actor_user_id=admin_user.id,
        payload=_flag_payload(
            status=GovernanceFlagStatus.RESOLVED,
            resolved_on=RESOLVED_ON,
        ),
    )
    assert resolved.status == GovernanceFlagStatus.RESOLVED
    assert resolved.resolved_on == RESOLVED_ON

    for status in (
        GovernanceFlagStatus.OPEN,
        GovernanceFlagStatus.MONITORING,
        GovernanceFlagStatus.DISMISSED,
    ):
        with pytest.raises(ResearchValidationError) as exc_info:
            ResearchCommandService.create_governance_flag(
                company.id,
                actor_user_id=admin_user.id,
                payload=_flag_payload(
                    status=status,
                    resolved_on=RESOLVED_ON,
                ),
            )
        assert exc_info.value.code == "validation_error"
        assert "resolved_on" in exc_info.value.details

    with pytest.raises(ResearchValidationError) as exc_info:
        ResearchCommandService.create_governance_flag(
            company.id,
            actor_user_id=admin_user.id,
            payload=_flag_payload(
                status=GovernanceFlagStatus.RESOLVED,
                resolved_on=None,
            ),
        )
    assert "resolved_on" in exc_info.value.details
    assert not db.session().in_transaction()


def test_resolved_on_cannot_precede_observed_on(
    app, admin_user, company
):
    with pytest.raises(ResearchValidationError) as exc_info:
        ResearchCommandService.create_governance_flag(
            company.id,
            actor_user_id=admin_user.id,
            payload=_flag_payload(
                status=GovernanceFlagStatus.RESOLVED,
                observed_on=OBSERVED_ON,
                resolved_on=date(2026, 7, 31),
            ),
        )

    assert exc_info.value.code == "validation_error"
    assert "resolved_on" in exc_info.value.details


def test_resolved_state_and_event_ordering_are_enforced_at_database(
    app, admin_user, company
):
    invalid_rows = [
        _flag_payload(
            status=GovernanceFlagStatus.RESOLVED,
            resolved_on=None,
        ),
        _flag_payload(
            status=GovernanceFlagStatus.OPEN,
            resolved_on=RESOLVED_ON,
        ),
        _flag_payload(
            status=GovernanceFlagStatus.RESOLVED,
            observed_on=RESOLVED_ON,
            resolved_on=OBSERVED_ON,
        ),
    ]

    for values in invalid_rows:
        db.session.add(
            GovernanceFlag(
                company_id=company.id,
                created_by_user_id=admin_user.id,
                **values,
            )
        )
        _commit_expect_integrity()

    assert (
        db.session.scalar(
            sa.select(sa.func.count()).select_from(GovernanceFlag)
        )
        == 0
    )


def test_update_governance_flag_corrects_fields_and_resolves_it(
    app, admin_user, company
):
    flag = ResearchCommandService.create_governance_flag(
        company.id,
        actor_user_id=admin_user.id,
        payload=_flag_payload(),
    )

    updated = ResearchCommandService.update_governance_flag(
        flag.id,
        actor_user_id=admin_user.id,
        changes={
            "severity": GovernanceSeverity.CRITICAL,
            "status": GovernanceFlagStatus.RESOLVED,
            "resolved_on": RESOLVED_ON,
            "interpretation": "Corrected interpretation after audit",
            "source_url_or_reference": SOURCE_REFERENCE + "/updated",
        },
    )

    persisted = db.session.get(GovernanceFlag, flag.id)
    assert persisted.severity == GovernanceSeverity.CRITICAL
    assert persisted.status == GovernanceFlagStatus.RESOLVED
    assert persisted.resolved_on == RESOLVED_ON
    assert persisted.interpretation == "Corrected interpretation after audit"
    assert persisted.source_url_or_reference == SOURCE_REFERENCE + "/updated"
    assert persisted.observed_on == OBSERVED_ON
    assert persisted.factual_evidence == updated.factual_evidence


def test_update_governance_flag_archives_and_preserves_the_record(
    app, admin_user, company
):
    flag = ResearchCommandService.create_governance_flag(
        company.id,
        actor_user_id=admin_user.id,
        payload=_flag_payload(),
    )
    flag_id = flag.id

    updated = ResearchCommandService.update_governance_flag(
        flag.id,
        actor_user_id=admin_user.id,
        changes={"archived": True},
    )

    assert updated.archived_at is not None
    persisted = db.session.get(GovernanceFlag, flag_id)
    assert persisted.archived_at is not None
    assert persisted.title == FLAG_TITLE
    assert persisted.factual_evidence is not None


def test_service_rejects_unarchiving_a_governance_flag(
    app, admin_user, company
):
    flag = ResearchCommandService.create_governance_flag(
        company.id,
        actor_user_id=admin_user.id,
        payload=_flag_payload(),
    )
    ResearchCommandService.update_governance_flag(
        flag.id,
        actor_user_id=admin_user.id,
        changes={"archived": True},
    )

    with pytest.raises(ResearchValidationError) as exc_info:
        ResearchCommandService.update_governance_flag(
            flag.id,
            actor_user_id=admin_user.id,
            changes={"archived": False},
        )

    assert "archived" in exc_info.value.details
    assert db.session.get(GovernanceFlag, flag.id).archived_at is not None


def test_update_governance_flag_rolls_back_invalid_resolved_state(
    app, admin_user, company
):
    flag = ResearchCommandService.create_governance_flag(
        company.id,
        actor_user_id=admin_user.id,
        payload=_flag_payload(),
    )

    with pytest.raises(ResearchValidationError):
        ResearchCommandService.update_governance_flag(
            flag.id,
            actor_user_id=admin_user.id,
            changes={
                "status": GovernanceFlagStatus.RESOLVED,
                "resolved_on": None,
            },
        )

    assert not db.session().in_transaction()
    assert (
        db.session.get(GovernanceFlag, flag.id).status
        == GovernanceFlagStatus.OPEN
    )


def test_service_persists_complete_disclosure_with_is_key_default_false(
    app, admin_user, company
):
    disclosure = ResearchCommandService.create_disclosure(
        company.id,
        actor_user_id=admin_user.id,
        payload=_disclosure_payload(),
    )

    persisted = db.session.get(CompanyDisclosure, disclosure.id)
    assert persisted.company_id == company.id
    assert persisted.event_type == "REG30"
    assert persisted.event_date == EVENT_DATE
    assert persisted.title == DISCLOSURE_TITLE
    assert persisted.original_source_url_or_reference == SOURCE_REFERENCE
    assert persisted.exchange_reference == "NSE:IKIO"
    assert persisted.significance_note == "Capacity expansion update"
    assert persisted.is_key is False
    assert persisted.document_id is None
    assert persisted.created_by_user_id == admin_user.id


def test_is_key_accepts_manual_true_and_false_and_rejects_non_boolean(
    app, admin_user, company
):
    key_disclosure = ResearchCommandService.create_disclosure(
        company.id,
        actor_user_id=admin_user.id,
        payload=_disclosure_payload(is_key=True),
    )
    assert key_disclosure.is_key is True

    updated = ResearchCommandService.update_disclosure(
        key_disclosure.id,
        actor_user_id=admin_user.id,
        changes={"is_key": False},
    )
    assert updated.is_key is False

    restored = ResearchCommandService.update_disclosure(
        key_disclosure.id,
        actor_user_id=admin_user.id,
        changes={"is_key": True},
    )
    assert restored.is_key is True

    for bad_value in (1, 0, "true", "false", None):
        with pytest.raises(ResearchValidationError) as exc_info:
            ResearchCommandService.update_disclosure(
                key_disclosure.id,
                actor_user_id=admin_user.id,
                changes={"is_key": bad_value},
            )
        assert exc_info.value.code == "validation_error"
        assert "is_key" in exc_info.value.details


@pytest.mark.parametrize(
    "field",
    [
        "event_type",
        "event_date",
        "title",
        "original_source_url_or_reference",
    ],
)
def test_service_create_disclosure_requires_core_fields(
    app, admin_user, company, field
):
    payload = _disclosure_payload()
    payload.pop(field)

    with pytest.raises(ResearchValidationError) as exc_info:
        ResearchCommandService.create_disclosure(
            company.id,
            actor_user_id=admin_user.id,
            payload=payload,
        )

    assert exc_info.value.code == "validation_error"
    assert field in exc_info.value.details
    assert not db.session().in_transaction()


@pytest.mark.parametrize(
    "event_type",
    [
        "reg30",
        "Reg30",
        "REG 30",
        "REG-30",
        "",
        "REG30_ATTACHMENT_" + "A" * 64,
        "30REG",
    ],
)
def test_service_rejects_non_uppercase_or_overlong_disclosure_event_types(
    app, admin_user, company, event_type
):
    with pytest.raises(ResearchValidationError) as exc_info:
        ResearchCommandService.create_disclosure(
            company.id,
            actor_user_id=admin_user.id,
            payload=_disclosure_payload(event_type=event_type),
        )

    assert exc_info.value.code == "validation_error"
    assert "event_type" in exc_info.value.details


def test_disclosure_accepts_another_stable_uppercase_event_type(
    app, admin_user, company
):
    disclosure = ResearchCommandService.create_disclosure(
        company.id,
        actor_user_id=admin_user.id,
        payload=_disclosure_payload(
            event_type="CREDIT_RATING_REPORT",
            title="Credit rating change",
        ),
    )

    assert disclosure.event_type == "CREDIT_RATING_REPORT"


def test_document_id_must_remain_null_in_this_plan(
    app, admin_user, company
):
    disclosure = ResearchCommandService.create_disclosure(
        company.id,
        actor_user_id=admin_user.id,
        payload=_disclosure_payload(document_id=None),
    )
    assert disclosure.document_id is None

    with pytest.raises(ResearchValidationError) as exc_info:
        ResearchCommandService.create_disclosure(
            company.id,
            actor_user_id=admin_user.id,
            payload=_disclosure_payload(
                document_id="11111111-1111-4111-8111-111111111111"
            ),
        )
    assert exc_info.value.code == "validation_error"
    assert "document_id" in exc_info.value.details

    with pytest.raises(ResearchValidationError) as exc_info:
        ResearchCommandService.update_disclosure(
            disclosure.id,
            actor_user_id=admin_user.id,
            changes={"document_id": "11111111-1111-4111-8111-111111111111"},
        )
    assert "document_id" in exc_info.value.details


def test_disclosure_has_no_numeric_importance_column_or_input(
    app, admin_user, company
):
    column_names = {
        column.name for column in CompanyDisclosure.__table__.columns
    }
    assert not {
        name
        for name in ("importance", "importance_score", "importance_pct")
        if name in column_names
    }

    with pytest.raises(ResearchValidationError) as exc_info:
        ResearchCommandService.create_disclosure(
            company.id,
            actor_user_id=admin_user.id,
            payload=_disclosure_payload(importance_score=95),
        )
    assert exc_info.value.code == "validation_error"
    assert "importance_score" in exc_info.value.details


def test_update_disclosure_corrects_fields_and_archives_it(
    app, admin_user, company
):
    disclosure = ResearchCommandService.create_disclosure(
        company.id,
        actor_user_id=admin_user.id,
        payload=_disclosure_payload(),
    )
    disclosure_id = disclosure.id

    corrected = ResearchCommandService.update_disclosure(
        disclosure.id,
        actor_user_id=admin_user.id,
        changes={
            "event_date": date(2026, 8, 9),
            "title": "Corrected disclosure title",
            "significance_note": "Corrected analytical note",
        },
    )
    assert corrected.event_date == date(2026, 8, 9)
    assert corrected.title == "Corrected disclosure title"
    assert corrected.archived_at is None

    archived = ResearchCommandService.update_disclosure(
        disclosure.id,
        actor_user_id=admin_user.id,
        changes={"archived": True},
    )
    assert archived.archived_at is not None
    persisted = db.session.get(CompanyDisclosure, disclosure_id)
    assert persisted.archived_at is not None
    assert persisted.title == "Corrected disclosure title"
    assert persisted.event_type == "REG30"


def test_service_requires_existing_company_and_actor_for_create(
    app, admin_user, company
):
    with pytest.raises(ResearchNotFoundError):
        ResearchCommandService.create_governance_flag(
            "missing-company",
            actor_user_id=admin_user.id,
            payload=_flag_payload(),
        )
    with pytest.raises(ResearchNotFoundError):
        ResearchCommandService.create_disclosure(
            company.id,
            actor_user_id="missing-user",
            payload=_disclosure_payload(),
        )


def test_service_requires_existing_flag_disclosure_and_actor_for_update(
    app, admin_user, company
):
    flag = ResearchCommandService.create_governance_flag(
        company.id,
        actor_user_id=admin_user.id,
        payload=_flag_payload(),
    )
    disclosure = ResearchCommandService.create_disclosure(
        company.id,
        actor_user_id=admin_user.id,
        payload=_disclosure_payload(),
    )

    with pytest.raises(ResearchNotFoundError):
        ResearchCommandService.update_governance_flag(
            "missing-flag",
            actor_user_id=admin_user.id,
            changes={"title": "Updated"},
        )
    with pytest.raises(ResearchNotFoundError):
        ResearchCommandService.update_disclosure(
            "missing-disclosure",
            actor_user_id=admin_user.id,
            changes={"title": "Updated"},
        )
    with pytest.raises(ResearchNotFoundError):
        ResearchCommandService.update_governance_flag(
            flag.id,
            actor_user_id="missing-user",
            changes={"title": "Updated"},
        )
    with pytest.raises(ResearchNotFoundError):
        ResearchCommandService.update_disclosure(
            disclosure.id,
            actor_user_id="missing-user",
            changes={"title": "Updated"},
        )


def test_service_rejects_unknown_and_server_owned_update_fields(
    app, admin_user, company
):
    flag = ResearchCommandService.create_governance_flag(
        company.id,
        actor_user_id=admin_user.id,
        payload=_flag_payload(),
    )
    disclosure = ResearchCommandService.create_disclosure(
        company.id,
        actor_user_id=admin_user.id,
        payload=_disclosure_payload(),
    )

    with pytest.raises(ResearchValidationError) as exc_info:
        ResearchCommandService.update_governance_flag(
            flag.id,
            actor_user_id=admin_user.id,
            changes={"id": "spoofed", "created_by_user_id": "spoofed"},
        )
    assert "id" in exc_info.value.details
    assert "created_by_user_id" in exc_info.value.details

    with pytest.raises(ResearchValidationError) as exc_info:
        ResearchCommandService.update_disclosure(
            disclosure.id,
            actor_user_id=admin_user.id,
            changes={"archived_at": datetime.now(timezone.utc)},
        )
    assert "archived_at" in exc_info.value.details
    assert not db.session().in_transaction()


def test_no_delete_governance_or_disclosure_commands_exist():
    forbidden = {
        "delete_governance_flag",
        "delete_disclosure",
        "remove_governance_flag",
        "remove_disclosure",
    }
    assert not {name for name in forbidden if hasattr(ResearchCommandService, name)}
