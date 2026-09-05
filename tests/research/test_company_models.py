"""Plan 2 Task 2: BusinessGroup and Company identity tests.

Covers the frozen Company/BusinessGroup contract, service validation and
normalization, database uniqueness/check constraints, and the requirement
that the legacy ``ticker`` table gains no new schema.
"""

import uuid

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from app import db
from app.models import BusinessGroup, Company
from app.services.research_command_service import ResearchCommandService
from app.utils.research_errors import (
    ResearchConflictError,
    ResearchNotFoundError,
    ResearchValidationError,
)


VALID_ISIN = "INE0LOJ01019"
OTHER_VALID_ISIN = "US0378331005"


@pytest.fixture
def business_group_factory(app):
    def create_business_group(
        *,
        name: str,
        notes: str | None = None,
        source_reference: str | None = None,
    ) -> BusinessGroup:
        group = BusinessGroup(
            name=name,
            notes=notes,
            source_reference=source_reference,
        )
        db.session.add(group)
        db.session.commit()
        return group

    return create_business_group


def _commit_expect_integrity() -> None:
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()


def _company_payload(ticker_id: str, **overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "ticker_id": ticker_id,
        "legal_name": "IKIO Lighting Limited",
        "isin": VALID_ISIN,
    }
    payload.update(overrides)
    return payload


def _ticker(ticker_factory, symbol: str, token: int):
    return ticker_factory(symbol=symbol, instrument_token=token)


def _reflected_columns(table_name: str) -> dict[str, bool]:
    inspector = sa.inspect(db.engine)
    return {
        column["name"]: column["nullable"]
        for column in inspector.get_columns(table_name)
    }


def test_business_group_and_company_declare_expected_table_names():
    assert BusinessGroup.__tablename__ == "business_group"
    assert Company.__tablename__ == "company"


def test_business_group_and_company_use_uuid_string_ids_and_audit_timestamps(
    app, ticker_factory
):
    ticker = ticker_factory()
    group = BusinessGroup(name="IKIO Group")
    company = Company(
        ticker_id=ticker.id,
        legal_name="IKIO Lighting Limited",
        isin=VALID_ISIN,
    )

    db.session.add_all([group, company])
    db.session.commit()

    for model in (group, company):
        assert isinstance(model.id, str)
        assert len(model.id) == 36
        assert uuid.UUID(model.id).version == 4
        assert model.created_at is not None
        assert model.updated_at is not None


def test_company_table_has_exact_columns_nullability_and_indexed_sector_and_industry(
    app,
):
    assert _reflected_columns("company") == {
        "id": False,
        "created_at": False,
        "updated_at": False,
        "ticker_id": False,
        "legal_name": False,
        "display_name": True,
        "isin": False,
        "sector": True,
        "industry": True,
        "business_group_id": True,
        "business_group_basis": True,
        "business_group_source_reference": True,
    }

    inspector = sa.inspect(db.engine)
    indexes = {
        (index["name"], tuple(index["column_names"]), index["unique"])
        for index in inspector.get_indexes("company")
    }
    assert ("ix_company_sector", ("sector",), False) in indexes
    assert ("ix_company_industry", ("industry",), False) in indexes


def test_company_table_has_named_unique_fk_and_group_evidence_constraints(app):
    inspector = sa.inspect(db.engine)

    unique_constraints = {
        constraint["name"]: sorted(constraint["column_names"])
        for constraint in inspector.get_unique_constraints("company")
    }
    assert unique_constraints["uq_company_ticker_id"] == ["ticker_id"]
    assert unique_constraints["uq_company_isin"] == ["isin"]

    foreign_keys = {
        frozenset(constraint["constrained_columns"]): constraint["referred_table"]
        for constraint in inspector.get_foreign_keys("company")
    }
    assert foreign_keys[frozenset({"ticker_id"})] == "ticker"
    assert foreign_keys[frozenset({"business_group_id"})] == "business_group"

    check_names = {
        constraint["name"]
        for constraint in inspector.get_check_constraints("company")
    }
    assert "ck_company_business_group_evidence" in check_names


def test_business_group_table_has_exact_columns_and_named_unique_name(app):
    assert _reflected_columns("business_group") == {
        "id": False,
        "created_at": False,
        "updated_at": False,
        "name": False,
        "notes": True,
        "source_reference": True,
    }

    inspector = sa.inspect(db.engine)
    unique_constraints = {
        constraint["name"]: sorted(constraint["column_names"])
        for constraint in inspector.get_unique_constraints("business_group")
    }
    assert unique_constraints["uq_business_group_name"] == ["name"]


def test_business_group_name_duplicate_is_rejected_at_the_database(app):
    db.session.add(BusinessGroup(name="Adani Group"))
    db.session.commit()

    db.session.add(BusinessGroup(name="Adani Group"))
    _commit_expect_integrity()

    count = db.session.scalar(sa.select(sa.func.count()).select_from(BusinessGroup))
    assert count == 1


def test_company_ticker_duplicate_is_rejected_at_the_database(app, ticker_factory):
    ticker = _ticker(ticker_factory, "IKIO", 10)

    db.session.add_all(
        [
            Company(
                ticker_id=ticker.id,
                legal_name="First Limited",
                isin=VALID_ISIN,
            ),
            Company(
                ticker_id=ticker.id,
                legal_name="Second Limited",
                isin=OTHER_VALID_ISIN,
            ),
        ]
    )
    _commit_expect_integrity()

    count = db.session.scalar(sa.select(sa.func.count()).select_from(Company))
    assert count == 0


def test_company_isin_duplicate_is_rejected_at_the_database(
    app, ticker_factory
):
    first_ticker = _ticker(ticker_factory, "IKIO", 10)
    second_ticker = _ticker(ticker_factory, "WIPRO", 11)

    db.session.add_all(
        [
            Company(
                ticker_id=first_ticker.id,
                legal_name="First Limited",
                isin=VALID_ISIN,
            ),
            Company(
                ticker_id=second_ticker.id,
                legal_name="Second Limited",
                isin=VALID_ISIN,
            ),
        ]
    )
    _commit_expect_integrity()

    count = db.session.scalar(sa.select(sa.func.count()).select_from(Company))
    assert count == 0


def test_company_business_group_evidence_is_complete_at_the_database(
    app, ticker_factory, business_group_factory
):
    ticker = _ticker(ticker_factory, "IKIO", 10)
    group = business_group_factory(name="IKIO Group")

    db.session.add(
        Company(
            ticker_id=ticker.id,
            legal_name="IKIO Lighting Limited",
            isin=VALID_ISIN,
            business_group_id=group.id,
            business_group_basis="CONSOLIDATED_FINANCIALS",
            business_group_source_reference=None,
        )
    )
    _commit_expect_integrity()


def test_company_model_adds_no_columns_or_foreign_keys_to_legacy_ticker(app):
    inspector = sa.inspect(db.engine)

    ticker_columns = {column["name"] for column in inspector.get_columns("ticker")}
    assert ticker_columns == {
        "id",
        "created_at",
        "symbol",
        "exchange",
        "instrument_token",
        "name",
        "last_price",
        "last_updated",
    }
    assert inspector.get_foreign_keys("ticker") == []


def test_service_create_company_normalizes_lowercase_isin_and_links_ticker(
    app, ticker_factory
):
    ticker = _ticker(ticker_factory, "IKIO", 10)

    company = ResearchCommandService.create_company(
        _company_payload(ticker.id, isin="ine0loj01019"),
        actor_user_id="actor-1",
    )

    assert company.ticker_id == ticker.id
    assert company.ticker.symbol == ticker.symbol
    assert company.isin == VALID_ISIN
    assert db.session.get(Company, company.id).isin == VALID_ISIN
    count = db.session.scalar(sa.select(sa.func.count()).select_from(Company))
    assert count == 1


@pytest.mark.parametrize("field", ["ticker_id", "legal_name", "isin"])
def test_service_create_company_requires_each_identity_field(app, field):
    payload = _company_payload("missing-ticker")
    payload.pop(field)

    with pytest.raises(ResearchValidationError) as exc_info:
        ResearchCommandService.create_company(
            payload, actor_user_id="actor-1"
        )

    assert exc_info.value.code == "validation_error"
    assert field in exc_info.value.details


def test_service_create_company_rejects_blank_required_text(app):
    payload = _company_payload("missing-ticker", legal_name="   ")

    with pytest.raises(ResearchValidationError) as exc_info:
        ResearchCommandService.create_company(
            payload, actor_user_id="actor-1"
        )

    assert exc_info.value.code == "validation_error"
    assert "legal_name" in exc_info.value.details


@pytest.mark.parametrize(
    "malformed_isin",
    [
        "INE0LOJ0101",
        "INE0LOJ010199",
        "1NE0LOJ01019",
        "INE0LOJ0101A",
        "IN_0LOJ01019",
        "IN E0LOJ01019",
    ],
)
def test_service_create_company_rejects_malformed_isin(app, malformed_isin):
    payload = _company_payload("missing-ticker", isin=malformed_isin)

    with pytest.raises(ResearchValidationError) as exc_info:
        ResearchCommandService.create_company(
            payload, actor_user_id="actor-1"
        )

    assert exc_info.value.code == "validation_error"
    assert "isin" in exc_info.value.details


def test_service_create_company_rejects_unknown_write_fields(app, ticker_factory):
    ticker = _ticker(ticker_factory, "IKIO", 10)
    payload = _company_payload(
        ticker.id,
        created_at="2026-09-06T00:00:00Z",
    )

    with pytest.raises(ResearchValidationError) as exc_info:
        ResearchCommandService.create_company(
            payload, actor_user_id="actor-1"
        )

    assert exc_info.value.code == "validation_error"
    assert "created_at" in exc_info.value.details


def test_service_create_company_resolves_existing_ticker_and_commits_nothing(
    app,
):
    with pytest.raises(ResearchNotFoundError):
        ResearchCommandService.create_company(
            _company_payload("missing-ticker"),
            actor_user_id="actor-1",
        )

    count = db.session.scalar(sa.select(sa.func.count()).select_from(Company))
    assert count == 0


def test_service_create_company_translates_duplicate_ticker_to_conflict(
    app, ticker_factory
):
    ticker = _ticker(ticker_factory, "IKIO", 10)
    ResearchCommandService.create_company(
        _company_payload(ticker.id),
        actor_user_id="actor-1",
    )

    with pytest.raises(ResearchConflictError):
        ResearchCommandService.create_company(
            _company_payload(ticker.id, isin=OTHER_VALID_ISIN),
            actor_user_id="actor-1",
        )

    count = db.session.scalar(sa.select(sa.func.count()).select_from(Company))
    assert count == 1


def test_service_create_company_normalizes_isin_before_duplicate_check(
    app, ticker_factory
):
    first_ticker = _ticker(ticker_factory, "IKIO", 10)
    second_ticker = _ticker(ticker_factory, "WIPRO", 11)
    ResearchCommandService.create_company(
        _company_payload(first_ticker.id),
        actor_user_id="actor-1",
    )

    with pytest.raises(ResearchConflictError):
        ResearchCommandService.create_company(
            _company_payload(second_ticker.id, isin=VALID_ISIN.lower()),
            actor_user_id="actor-1",
        )

    count = db.session.scalar(sa.select(sa.func.count()).select_from(Company))
    assert count == 1


def test_service_create_company_requires_complete_group_assignment_evidence(
    app, ticker_factory, business_group_factory
):
    group = business_group_factory(name="IKIO Group")

    with pytest.raises(ResearchValidationError) as exc_info:
        ResearchCommandService.create_company(
            _company_payload(
                "missing-ticker",
                business_group_id=group.id,
                business_group_basis="CONSOLIDATED_FINANCIALS",
            ),
            actor_user_id="actor-1",
        )
    assert "business_group_source_reference" in exc_info.value.details

    with pytest.raises(ResearchValidationError) as exc_info:
        ResearchCommandService.create_company(
            _company_payload(
                "missing-ticker",
                business_group_id=group.id,
                business_group_source_reference="https://example.in/report",
            ),
            actor_user_id="actor-1",
        )
    assert "business_group_basis" in exc_info.value.details


def test_service_create_company_rejects_evidence_without_a_group(
    app, ticker_factory
):
    ticker = _ticker(ticker_factory, "IKIO", 10)

    with pytest.raises(ResearchValidationError):
        ResearchCommandService.create_company(
            _company_payload(
                ticker.id,
                business_group_basis="SURNAME_SIMILARITY",
            ),
            actor_user_id="actor-1",
        )

    count = db.session.scalar(sa.select(sa.func.count()).select_from(Company))
    assert count == 0


def test_service_create_company_persists_group_and_display_label_fallback(
    app, ticker_factory, business_group_factory
):
    ticker = _ticker(ticker_factory, "IKIO", 10)
    group = business_group_factory(
        name="IKIO Group",
        notes="Curated promoter family",
        source_reference="https://example.in/source",
    )

    company = ResearchCommandService.create_company(
        _company_payload(
            ticker.id,
            business_group_id=group.id,
            business_group_basis="CONSOLIDATED_FINANCIALS",
            business_group_source_reference="https://example.in/financials",
        ),
        actor_user_id="actor-1",
    )

    assert company.business_group.name == group.name
    assert company.display_label == company.legal_name

    second_ticker = _ticker(ticker_factory, "WIPRO", 11)
    display_company = ResearchCommandService.create_company(
        _company_payload(
            second_ticker.id,
            isin=OTHER_VALID_ISIN,
            display_name="Wipro Limited",
        ),
        actor_user_id="actor-1",
    )
    assert display_company.display_label == "Wipro Limited"


def test_service_update_company_changes_mutable_fields(
    app, ticker_factory, business_group_factory
):
    ticker = _ticker(ticker_factory, "IKIO", 10)
    group = business_group_factory(name="IKIO Group")
    company = ResearchCommandService.create_company(
        _company_payload(ticker.id),
        actor_user_id="actor-1",
    )

    updated = ResearchCommandService.update_company(
        company.id,
        {
            "display_name": "IKIO Technologies Limited",
            "sector": "Electricals",
            "industry": "Lighting",
            "business_group_id": group.id,
            "business_group_basis": "CONSOLIDATED_FINANCIALS",
            "business_group_source_reference": "https://example.in/financials",
        },
        actor_user_id="actor-1",
    )

    assert updated.legal_name == company.legal_name
    assert updated.display_label == "IKIO Technologies Limited"
    assert updated.sector == "Electricals"
    assert updated.industry == "Lighting"
    assert updated.business_group.name == group.name
    assert updated.isin == VALID_ISIN


def test_service_update_company_rejects_unknown_company(app):
    with pytest.raises(ResearchNotFoundError):
        ResearchCommandService.update_company(
            "missing-company-id",
            {"display_name": "Unknown Limited"},
            actor_user_id="actor-1",
        )


def test_service_update_company_translates_duplicate_isin_to_conflict(
    app, ticker_factory
):
    first_ticker = _ticker(ticker_factory, "IKIO", 10)
    second_ticker = _ticker(ticker_factory, "WIPRO", 11)
    first = ResearchCommandService.create_company(
        _company_payload(first_ticker.id),
        actor_user_id="actor-1",
    )
    second = ResearchCommandService.create_company(
        _company_payload(second_ticker.id, isin=OTHER_VALID_ISIN),
        actor_user_id="actor-1",
    )

    with pytest.raises(ResearchConflictError):
        ResearchCommandService.update_company(
            second.id,
            {"isin": VALID_ISIN},
            actor_user_id="actor-1",
        )

    assert db.session.get(Company, second.id).isin == OTHER_VALID_ISIN
    assert db.session.get(Company, first.id).isin == VALID_ISIN
