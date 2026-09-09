"""Plan Task 8: immutable forecast revisions with administrator EPS.

These tests lock the frozen ``ForecastRevision``/``ForecastLine`` contract:
unique fiscal years inside a revision, optional fixed-precision revenue,
EBITDA, PAT, margin, and administrator-supplied EPS, exact Decimal
persistence, 0-100 margins, the closed M1 currency/unit conventions,
no share-count or derived EPS/margin logic, atomic rollback, stale-base
revision conflict semantics, and append-only immutability.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from app import db
from app.models import Company, ForecastLine, ForecastRevision
from app.services.research_command_service import ResearchCommandService
from app.utils.research_errors import (
    ResearchConflictError,
    ResearchNotFoundError,
    ResearchValidationError,
)


AS_OF_DATE = date(2026, 9, 4)
VALID_ISIN = "INE0LOJ01019"
VALID_UNITS = ("ABSOLUTE", "THOUSAND", "LAKH", "CRORE", "MILLION")


def _commit_expect_integrity() -> None:
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()


def _line(**overrides: object) -> dict[str, object]:
    line: dict[str, object] = {
        "fiscal_year": 2027,
        "is_estimate": True,
        "revenue": Decimal("1847.1234"),
        "ebitda": Decimal("295.5000"),
        "ebitda_margin_pct": Decimal("16.0000"),
        "pat": Decimal("120.0000"),
        "eps": Decimal("57.1234"),
        "currency": "INR",
        "unit": "CRORE",
    }
    line.update(overrides)
    return line


def _forecast_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "as_of_date": AS_OF_DATE,
        "assumptions": "Administrator-supplied estimate snapshot",
        "lines": [
            _line(),
            _line(
                fiscal_year=2028,
                revenue=Decimal("2245.0000"),
                ebitda=Decimal("389.2500"),
                ebitda_margin_pct=Decimal("17.3400"),
                pat=Decimal("174.0000"),
                eps=Decimal("82.9876"),
            ),
        ],
    }
    payload.update(overrides)
    return payload


def _forecast_row(
    *,
    company_id: str,
    actor_user_id: str,
    revision_number: int,
    supersedes_revision_id: str | None = None,
) -> ForecastRevision:
    return ForecastRevision(
        company_id=company_id,
        revision_number=revision_number,
        supersedes_revision_id=supersedes_revision_id,
        as_of_date=AS_OF_DATE,
        assumptions=f"Revision {revision_number}",
        change_reason=None if revision_number == 1 else "Updated forecast",
        created_by_user_id=actor_user_id,
    )


def _forecast_line_row(
    *, forecast_revision_id: str, fiscal_year: int
) -> ForecastLine:
    return ForecastLine(
        forecast_revision_id=forecast_revision_id,
        fiscal_year=fiscal_year,
        is_estimate=True,
        revenue=Decimal("100.0000"),
        ebitda=Decimal("20.0000"),
        ebitda_margin_pct=Decimal("20.0000"),
        pat=Decimal("10.0000"),
        eps=Decimal("5.0000"),
        currency="INR",
        unit="CRORE",
    )


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
            "legal_name": "IKIO Technologies Limited",
            "isin": VALID_ISIN,
        },
        actor_user_id="actor-1",
    )


def test_forecast_models_are_exported_from_app_models():
    assert ForecastRevision.__tablename__ == "forecast_revision"
    assert ForecastLine.__tablename__ == "forecast_line"
    assert callable(
        ResearchCommandService.create_forecast_revision
    )


def test_forecast_tables_have_exact_columns_and_constraints(app):
    assert _reflected_columns("forecast_revision") == {
        "id": False,
        "created_at": False,
        "company_id": False,
        "revision_number": False,
        "supersedes_revision_id": True,
        "as_of_date": False,
        "assumptions": True,
        "change_reason": True,
        "created_by_user_id": False,
    }
    assert _reflected_columns("forecast_line") == {
        "id": False,
        "created_at": False,
        "forecast_revision_id": False,
        "fiscal_year": False,
        "is_estimate": False,
        "revenue": True,
        "ebitda": True,
        "pat": True,
        "ebitda_margin_pct": True,
        "eps": True,
        "currency": False,
        "unit": False,
    }

    inspector = sa.inspect(db.engine)
    revision_unique = {
        constraint["name"]: sorted(constraint["column_names"])
        for constraint in inspector.get_unique_constraints(
            "forecast_revision"
        )
    }
    assert revision_unique["uq_forecast_revision_company_number"] == [
        "company_id",
        "revision_number",
    ]

    line_unique = {
        constraint["name"]: sorted(constraint["column_names"])
        for constraint in inspector.get_unique_constraints("forecast_line")
    }
    assert line_unique["uq_forecast_line_revision_fiscal_year"] == [
        "fiscal_year",
        "forecast_revision_id",
    ]

    revision_fks = {
        frozenset(constraint["constrained_columns"]): constraint[
            "referred_table"
        ]
        for constraint in inspector.get_foreign_keys("forecast_revision")
    }
    assert revision_fks[frozenset({"company_id"})] == "company"
    assert revision_fks[frozenset({"created_by_user_id"})] == "user"
    assert (
        revision_fks[frozenset({"supersedes_revision_id"})]
        == "forecast_revision"
    )

    line_fks = {
        frozenset(constraint["constrained_columns"]): constraint[
            "referred_table"
        ]
        for constraint in inspector.get_foreign_keys("forecast_line")
    }
    assert (
        line_fks[frozenset({"forecast_revision_id"})]
        == "forecast_revision"
    )

    check_names = {
        constraint["name"]
        for constraint in inspector.get_check_constraints(
            "forecast_revision"
        )
    }
    assert "ck_forecast_revision_number_positive" in check_names


def test_first_forecast_revision_persists_header_and_lines(
    app, admin_user, company
):
    revision = ResearchCommandService.create_forecast_revision(
        company.id,
        actor_user_id=admin_user.id,
        payload=_forecast_payload(),
    )

    persisted = db.session.get(ForecastRevision, revision.id)
    assert persisted.revision_number == 1
    assert persisted.supersedes_revision_id is None
    assert persisted.change_reason is None
    assert persisted.company_id == company.id
    assert persisted.created_by_user_id == admin_user.id
    assert persisted.as_of_date == AS_OF_DATE
    assert persisted.assumptions == (
        "Administrator-supplied estimate snapshot"
    )
    assert persisted.created_at is not None
    assert isinstance(persisted.id, str)
    assert len(persisted.id) == 36
    assert uuid.UUID(persisted.id).version == 4

    lines = db.session.scalars(
        sa.select(ForecastLine)
        .where(ForecastLine.forecast_revision_id == revision.id)
        .order_by(ForecastLine.fiscal_year)
    ).all()
    assert [line.fiscal_year for line in lines] == [2027, 2028]
    assert all(line.forecast_revision_id == revision.id for line in lines)

    first = lines[0]
    assert first.is_estimate is True
    assert first.revenue == Decimal("1847.1234")
    assert first.ebitda == Decimal("295.5000")
    assert first.ebitda_margin_pct == Decimal("16.0000")
    assert first.pat == Decimal("120.0000")
    assert first.eps == Decimal("57.1234")
    assert first.currency == "INR"
    assert first.unit == "CRORE"


def test_first_forecast_revision_accepts_absent_or_null_base_id(
    app, admin_user, company, ticker_factory
):
    without_base = _forecast_payload()
    without_base.pop("base_revision_id", None)
    first = ResearchCommandService.create_forecast_revision(
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
    second = ResearchCommandService.create_forecast_revision(
        second_company.id,
        actor_user_id=admin_user.id,
        payload=_forecast_payload(base_revision_id=None),
    )

    assert first.revision_number == 1
    assert second.revision_number == 1
    assert first.supersedes_revision_id is None
    assert second.supersedes_revision_id is None


def test_first_forecast_revision_rejects_a_non_null_base_id(
    app, admin_user, company
):
    with pytest.raises(ResearchConflictError) as exc_info:
        ResearchCommandService.create_forecast_revision(
            company.id,
            actor_user_id=admin_user.id,
            payload=_forecast_payload(
                base_revision_id="does-not-exist"
            ),
        )

    assert exc_info.value.code == "revision_conflict"
    assert exc_info.value.message == "Research revision changed"


def test_first_forecast_revision_rejects_a_non_null_change_reason(
    app, admin_user, company
):
    with pytest.raises(ResearchValidationError) as exc_info:
        ResearchCommandService.create_forecast_revision(
            company.id,
            actor_user_id=admin_user.id,
            payload=_forecast_payload(
                change_reason="Initial forecast"
            ),
        )

    assert exc_info.value.code == "validation_error"
    assert "change_reason" in exc_info.value.details


def test_forecast_as_of_date_is_required(app, admin_user, company):
    payload = _forecast_payload()
    payload.pop("as_of_date")

    with pytest.raises(ResearchValidationError) as exc_info:
        ResearchCommandService.create_forecast_revision(
            company.id,
            actor_user_id=admin_user.id,
            payload=payload,
        )

    assert exc_info.value.code == "validation_error"
    assert "as_of_date" in exc_info.value.details
    assert not db.session().in_transaction()


def test_forecast_assumptions_are_optional(app, admin_user, company):
    revision = ResearchCommandService.create_forecast_revision(
        company.id,
        actor_user_id=admin_user.id,
        payload=_forecast_payload(
            assumptions=None,
            lines=[_line()],
        ),
    )

    assert db.session.get(ForecastRevision, revision.id).assumptions is None


def test_all_line_financial_values_are_optional_and_persist_nulls(
    app, admin_user, company
):
    revision = ResearchCommandService.create_forecast_revision(
        company.id,
        actor_user_id=admin_user.id,
        payload=_forecast_payload(
            lines=[
                _line(
                    revenue=None,
                    ebitda=None,
                    ebitda_margin_pct=None,
                    pat=None,
                    eps=None,
                )
            ]
        ),
    )

    line = db.session.scalar(
        sa.select(ForecastLine).where(
            ForecastLine.forecast_revision_id == revision.id
        )
    )
    assert line.revenue is None
    assert line.ebitda is None
    assert line.ebitda_margin_pct is None
    assert line.pat is None
    assert line.eps is None
    assert line.is_estimate is True
    assert line.fiscal_year == 2027
    assert line.currency == "INR"
    assert line.unit == "CRORE"


def test_exact_decimal_values_are_persisted_unchanged(
    app, admin_user, company
):
    line = _line(
        revenue=Decimal("123456789.0123"),
        ebitda=Decimal("98765432.1098"),
        ebitda_margin_pct=Decimal("100.0000"),
        pat=Decimal("-12345.6789"),
        eps=Decimal("9999.0001"),
    )
    revision = ResearchCommandService.create_forecast_revision(
        company.id,
        actor_user_id=admin_user.id,
        payload=_forecast_payload(lines=[line]),
    )

    db.session.expire_all()
    persisted = db.session.scalar(
        sa.select(ForecastLine).where(
            ForecastLine.forecast_revision_id == revision.id
        )
    )
    assert persisted.revenue == Decimal("123456789.0123")
    assert persisted.ebitda == Decimal("98765432.1098")
    assert persisted.ebitda_margin_pct == Decimal("100.0000")
    assert persisted.pat == Decimal("-12345.6789")
    assert persisted.eps == Decimal("9999.0001")


@pytest.mark.parametrize(
    "field",
    [
        "revenue",
        "ebitda",
        "pat",
        "eps",
    ],
)
@pytest.mark.parametrize(
    "bad_value",
    [
        "1847.1234",
        1847,
        1847.5,
        Decimal("Infinity"),
    ],
)
def test_financial_values_when_present_must_be_finite_decimals(
    app, admin_user, company, field, bad_value
):
    with pytest.raises(ResearchValidationError) as exc_info:
        ResearchCommandService.create_forecast_revision(
            company.id,
            actor_user_id=admin_user.id,
            payload=_forecast_payload(
                lines=[_line(**{field: bad_value})]
            ),
        )

    assert exc_info.value.code == "validation_error"
    assert field in exc_info.value.details
    assert not db.session().in_transaction()


def test_ebitda_margin_must_be_between_zero_and_100(
    app, admin_user, company, ticker_factory
):
    for margin in (
        Decimal("-0.0001"),
        Decimal("100.0001"),
        Decimal("NaN"),
        "16",
        16,
    ):
        with pytest.raises(ResearchValidationError) as exc_info:
            ResearchCommandService.create_forecast_revision(
                company.id,
                actor_user_id=admin_user.id,
                payload=_forecast_payload(
                    lines=[_line(ebitda_margin_pct=margin)]
                ),
            )
        assert exc_info.value.code == "validation_error"
        assert "ebitda_margin_pct" in exc_info.value.details

    for index, margin in enumerate(
        (Decimal("0.0000"), Decimal("100.0000"))
    ):
        ticker = ticker_factory(
            symbol=f"MARGIN{index}", instrument_token=100 + index
        )
        fresh_company = ResearchCommandService.create_company(
            {
                "ticker_id": ticker.id,
                "legal_name": f"Margin Company {index}",
                "isin": f"US00000000{index}5",
            },
            actor_user_id="actor-1",
        )
        revision = ResearchCommandService.create_forecast_revision(
            fresh_company.id,
            actor_user_id=admin_user.id,
            payload=_forecast_payload(
                lines=[_line(ebitda_margin_pct=margin)]
            ),
        )
        persisted = db.session.scalar(
            sa.select(ForecastLine).where(
                ForecastLine.forecast_revision_id == revision.id
            )
        )
        assert persisted.ebitda_margin_pct == margin


@pytest.mark.parametrize("unit", VALID_UNITS)
def test_forecast_line_accepts_each_valid_unit(
    app, admin_user, company, unit
):
    revision = ResearchCommandService.create_forecast_revision(
        company.id,
        actor_user_id=admin_user.id,
        payload=_forecast_payload(lines=[_line(unit=unit)]),
    )

    persisted = db.session.scalar(
        sa.select(ForecastLine).where(
            ForecastLine.forecast_revision_id == revision.id
        )
    )
    assert persisted.unit == unit


@pytest.mark.parametrize(
    "unit",
    [
        "lakh",
        "TONS",
        "UNITS",
        " CRORE",
        "CRORE ",
        None,
        100,
    ],
)
def test_forecast_line_rejects_invalid_unit_values(
    app, admin_user, company, unit
):
    with pytest.raises(ResearchValidationError) as exc_info:
        ResearchCommandService.create_forecast_revision(
            company.id,
            actor_user_id=admin_user.id,
            payload=_forecast_payload(lines=[_line(unit=unit)]),
        )

    assert exc_info.value.code == "validation_error"
    assert "unit" in exc_info.value.details
    assert (
        db.session.scalar(
            sa.select(sa.func.count())
            .select_from(ForecastRevision)
            .where(ForecastRevision.company_id == company.id)
        )
        == 0
    )


def test_forecast_currency_is_fixed_to_inr(app, admin_user, company):
    revision = ResearchCommandService.create_forecast_revision(
        company.id,
        actor_user_id=admin_user.id,
        payload=_forecast_payload(lines=[_line(currency="INR")]),
    )
    persisted = db.session.scalar(
        sa.select(ForecastLine).where(
            ForecastLine.forecast_revision_id == revision.id
        )
    )
    assert persisted.currency == "INR"

    with pytest.raises(ResearchValidationError) as exc_info:
        ResearchCommandService.create_forecast_revision(
            company.id,
            actor_user_id=admin_user.id,
            payload=_forecast_payload(lines=[_line(currency="USD")]),
        )
    assert "currency" in exc_info.value.details


def test_required_line_metadata_must_be_valid(
    app, admin_user, company
):
    for bad_year in (999, 10000, "2027", 2027.5, True, None):
        with pytest.raises(ResearchValidationError) as exc_info:
            ResearchCommandService.create_forecast_revision(
                company.id,
                actor_user_id=admin_user.id,
                payload=_forecast_payload(
                    lines=[_line(fiscal_year=bad_year)]
                ),
            )
        assert exc_info.value.code == "validation_error"
        assert "fiscal_year" in exc_info.value.details

    for bad_estimate in (None, 1, 0, "true"):
        with pytest.raises(ResearchValidationError) as exc_info:
            ResearchCommandService.create_forecast_revision(
                company.id,
                actor_user_id=admin_user.id,
                payload=_forecast_payload(
                    lines=[_line(is_estimate=bad_estimate)]
                ),
            )
        assert exc_info.value.code == "validation_error"
        assert "is_estimate" in exc_info.value.details


def test_fiscal_years_are_unique_inside_one_revision(
    app, admin_user, company
):
    with pytest.raises(ResearchValidationError) as exc_info:
        ResearchCommandService.create_forecast_revision(
            company.id,
            actor_user_id=admin_user.id,
            payload=_forecast_payload(
                lines=[
                    _line(fiscal_year=2027),
                    _line(
                        fiscal_year=2027,
                        revenue=Decimal("9999.0000"),
                    ),
                ]
            ),
        )

    assert exc_info.value.code == "validation_error"
    assert "fiscal_year" in exc_info.value.details
    assert not db.session().in_transaction()
    assert (
        db.session.scalar(
            sa.select(sa.func.count())
            .select_from(ForecastRevision)
            .where(ForecastRevision.company_id == company.id)
        )
        == 0
    )
    assert db.session.scalar(
        sa.select(sa.func.count()).select_from(ForecastLine)
    ) == 0


def test_fiscal_year_uniqueness_is_enforced_at_the_database(
    app, admin_user, company
):
    first = ResearchCommandService.create_forecast_revision(
        company.id,
        actor_user_id=admin_user.id,
        payload=_forecast_payload(lines=[_line(fiscal_year=2027)]),
    )
    db.session.add(
        _forecast_line_row(
            forecast_revision_id=first.id,
            fiscal_year=2027,
        )
    )
    _commit_expect_integrity()

    count = db.session.scalar(
        sa.select(sa.func.count())
        .select_from(ForecastLine)
        .where(ForecastLine.forecast_revision_id == first.id)
    )
    assert count == 1


def test_eps_may_be_present_without_pat_and_is_never_derived_from_pat(
    app, admin_user, company
):
    revision = ResearchCommandService.create_forecast_revision(
        company.id,
        actor_user_id=admin_user.id,
        payload=_forecast_payload(
            lines=[
                _line(
                    pat=None,
                    eps=Decimal("45.6789"),
                )
            ]
        ),
    )

    line = db.session.scalar(
        sa.select(ForecastLine).where(
            ForecastLine.forecast_revision_id == revision.id
        )
    )
    assert line.pat is None
    assert line.eps == Decimal("45.6789")


def test_pat_may_be_present_while_eps_remains_null(
    app, admin_user, company
):
    revision = ResearchCommandService.create_forecast_revision(
        company.id,
        actor_user_id=admin_user.id,
        payload=_forecast_payload(
            lines=[
                _line(
                    pat=Decimal("120.0000"),
                    eps=None,
                )
            ]
        ),
    )

    line = db.session.scalar(
        sa.select(ForecastLine).where(
            ForecastLine.forecast_revision_id == revision.id
        )
    )
    assert line.pat == Decimal("120.0000")
    assert line.eps is None


def test_no_share_count_or_derived_eps_calculation_field_exists(app):
    line_columns = {
        column.name for column in ForecastLine.__table__.columns
    }
    forbidden = {
        "share_count",
        "shares_outstanding",
        "shares",
        "calculated_eps",
    }
    assert not (line_columns & forbidden)
    assert "eps" in line_columns
    assert "pat" in line_columns


def test_no_margin_or_eps_is_calculated_from_other_line_values(
    app, admin_user, company
):
    revision = ResearchCommandService.create_forecast_revision(
        company.id,
        actor_user_id=admin_user.id,
        payload=_forecast_payload(
            lines=[
                _line(
                    revenue=Decimal("1000.0000"),
                    ebitda=Decimal("200.0000"),
                    ebitda_margin_pct=None,
                    pat=Decimal("100.0000"),
                    eps=None,
                )
            ]
        ),
    )

    line = db.session.scalar(
        sa.select(ForecastLine).where(
            ForecastLine.forecast_revision_id == revision.id
        )
    )
    assert line.ebitda_margin_pct is None
    assert line.eps is None


def test_supplied_eps_is_stored_exactly_without_arithmetic(
    app, admin_user, company
):
    revision = ResearchCommandService.create_forecast_revision(
        company.id,
        actor_user_id=admin_user.id,
        payload=_forecast_payload(
            lines=[
                _line(
                    pat=Decimal("9999.0000"),
                    eps=Decimal("10.0000"),
                )
            ]
        ),
    )

    line = db.session.scalar(
        sa.select(ForecastLine).where(
            ForecastLine.forecast_revision_id == revision.id
        )
    )
    assert line.eps == Decimal("10.0000")


def test_second_forecast_revision_requires_current_base_and_reason(
    app, admin_user, company
):
    first = ResearchCommandService.create_forecast_revision(
        company.id,
        actor_user_id=admin_user.id,
        payload=_forecast_payload(),
    )

    with pytest.raises(ResearchValidationError) as exc_info:
        ResearchCommandService.create_forecast_revision(
            company.id,
            actor_user_id=admin_user.id,
            payload=_forecast_payload(base_revision_id=None),
        )
    assert "base_revision_id" in exc_info.value.details

    for bad_reason in (None, "", "   "):
        with pytest.raises(ResearchValidationError) as exc_info:
            ResearchCommandService.create_forecast_revision(
                company.id,
                actor_user_id=admin_user.id,
                payload=_forecast_payload(
                    base_revision_id=first.id,
                    change_reason=bad_reason,
                ),
            )
        assert exc_info.value.code == "validation_error"
        assert "change_reason" in exc_info.value.details
        assert not db.session().in_transaction()

    second = ResearchCommandService.create_forecast_revision(
        company.id,
        actor_user_id=admin_user.id,
        payload=_forecast_payload(
            base_revision_id=first.id,
            change_reason="Updated after Q1 results",
            lines=[
                _line(
                    fiscal_year=2027,
                    revenue=Decimal("1900.0000"),
                ),
                _line(fiscal_year=2029, eps=Decimal("95.0000")),
            ],
        ),
    )

    assert second.revision_number == 2
    assert second.supersedes_revision_id == first.id
    assert second.change_reason == "Updated after Q1 results"
    assert db.session.scalars(
        sa.select(ForecastLine.fiscal_year)
        .where(ForecastLine.forecast_revision_id == second.id)
        .order_by(ForecastLine.fiscal_year)
    ).all() == [2027, 2029]


def test_stale_forecast_base_raises_conflict_without_overwriting(
    app, admin_user, company
):
    first = ResearchCommandService.create_forecast_revision(
        company.id,
        actor_user_id=admin_user.id,
        payload=_forecast_payload(lines=[_line()]),
    )
    ResearchCommandService.create_forecast_revision(
        company.id,
        actor_user_id=admin_user.id,
        payload=_forecast_payload(
            base_revision_id=first.id,
            change_reason="First update",
            lines=[_line(fiscal_year=2027, revenue=Decimal("2000.0000"))],
        ),
    )

    with pytest.raises(ResearchConflictError) as exc_info:
        ResearchCommandService.create_forecast_revision(
            company.id,
            actor_user_id=admin_user.id,
            payload=_forecast_payload(
                base_revision_id=first.id,
                change_reason="Stale update",
            ),
        )

    assert exc_info.value.code == "revision_conflict"
    assert exc_info.value.message == "Research revision changed"
    assert not db.session().in_transaction()

    revision_numbers = db.session.scalars(
        sa.select(ForecastRevision.revision_number)
        .where(ForecastRevision.company_id == company.id)
        .order_by(ForecastRevision.revision_number)
    ).all()
    assert revision_numbers == [1, 2]


def test_unique_race_is_translated_to_revision_conflict(
    app, admin_user, company, monkeypatch
):
    first = ResearchCommandService.create_forecast_revision(
        company.id,
        actor_user_id=admin_user.id,
        payload=_forecast_payload(lines=[_line()]),
    )
    ResearchCommandService.create_forecast_revision(
        company.id,
        actor_user_id=admin_user.id,
        payload=_forecast_payload(
            base_revision_id=first.id,
            change_reason="Winning writer",
        ),
    )

    def _stale_current(_cls, _company_id):
        return first

    monkeypatch.setattr(
        ResearchCommandService,
        "_current_forecast_revision_locked",
        classmethod(_stale_current),
    )

    with pytest.raises(ResearchConflictError) as exc_info:
        ResearchCommandService.create_forecast_revision(
            company.id,
            actor_user_id=admin_user.id,
            payload=_forecast_payload(
                base_revision_id=first.id,
                change_reason="Losing writer",
            ),
        )

    assert exc_info.value.code == "revision_conflict"
    assert exc_info.value.message == "Research revision changed"
    assert not db.session().in_transaction()

    revision_numbers = db.session.scalars(
        sa.select(ForecastRevision.revision_number)
        .where(ForecastRevision.company_id == company.id)
        .order_by(ForecastRevision.revision_number)
    ).all()
    assert revision_numbers == [1, 2]


def test_invalid_line_causes_complete_atomic_rollback(
    app, admin_user, company
):
    with pytest.raises(ResearchValidationError) as exc_info:
        ResearchCommandService.create_forecast_revision(
            company.id,
            actor_user_id=admin_user.id,
            payload=_forecast_payload(
                lines=[
                    _line(),
                    _line(fiscal_year=2028, ebitda_margin_pct=Decimal("101.0000")),
                ]
            ),
        )

    assert exc_info.value.code == "validation_error"
    assert "ebitda_margin_pct" in exc_info.value.details
    assert not db.session().in_transaction()
    assert db.session.scalar(
        sa.select(sa.func.count()).select_from(ForecastRevision)
    ) == 0
    assert db.session.scalar(
        sa.select(sa.func.count()).select_from(ForecastLine)
    ) == 0


def test_unknown_and_malformed_write_fields_are_rejected(
    app, admin_user, company
):
    with pytest.raises(ResearchValidationError) as exc_info:
        ResearchCommandService.create_forecast_revision(
            company.id,
            actor_user_id=admin_user.id,
            payload=_forecast_payload(
                revision_number=99,
                lines=[_line()],
            ),
        )
    assert exc_info.value.code == "validation_error"
    assert "revision_number" in exc_info.value.details

    with pytest.raises(ResearchValidationError) as exc_info:
        ResearchCommandService.create_forecast_revision(
            company.id,
            actor_user_id=admin_user.id,
            payload=_forecast_payload(
                lines=[_line(sort_order=0)],
            ),
        )
    assert exc_info.value.code == "validation_error"

    for malformed_lines in ("lines", None, 42, [None, {}]):
        with pytest.raises(ResearchValidationError) as exc_info:
            ResearchCommandService.create_forecast_revision(
                company.id,
                actor_user_id=admin_user.id,
                payload=_forecast_payload(lines=malformed_lines),
            )
        assert exc_info.value.code == "validation_error"
        assert not db.session().in_transaction()


def test_company_and_forecast_revision_number_are_unique_at_database(
    app, admin_user, company
):
    first = ResearchCommandService.create_forecast_revision(
        company.id,
        actor_user_id=admin_user.id,
        payload=_forecast_payload(lines=[_line()]),
    )

    db.session.add(
        _forecast_row(
            company_id=company.id,
            actor_user_id=admin_user.id,
            revision_number=first.revision_number,
        )
    )
    _commit_expect_integrity()

    count = db.session.scalar(
        sa.select(sa.func.count())
        .select_from(ForecastRevision)
        .where(ForecastRevision.company_id == company.id)
    )
    assert count == 1


def test_forecast_revisions_are_independent_per_company(
    app, admin_user, company, ticker_factory
):
    ResearchCommandService.create_forecast_revision(
        company.id,
        actor_user_id=admin_user.id,
        payload=_forecast_payload(lines=[_line()]),
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
    ResearchCommandService.create_forecast_revision(
        second_company.id,
        actor_user_id=admin_user.id,
        payload=_forecast_payload(lines=[_line()]),
    )

    numbers = {
        row[0]: row[1]
        for row in db.session.execute(
            sa.select(
                ForecastRevision.company_id,
                ForecastRevision.revision_number,
            )
        ).all()
    }
    assert numbers[company.id] == 1
    assert numbers[second_company.id] == 1


def test_service_requires_existing_company_and_actor(
    app, admin_user, company
):
    with pytest.raises(ResearchNotFoundError):
        ResearchCommandService.create_forecast_revision(
            "missing-company",
            actor_user_id=admin_user.id,
            payload=_forecast_payload(),
        )

    with pytest.raises(ResearchNotFoundError):
        ResearchCommandService.create_forecast_revision(
            company.id,
            actor_user_id="missing-user",
            payload=_forecast_payload(),
        )


def test_forecast_uses_fixed_precision_numeric_columns(app):
    expected_money = {
        "revenue",
        "ebitda",
        "pat",
        "eps",
    }
    for name in expected_money:
        column_type = ForecastLine.__table__.columns[name].type
        assert isinstance(column_type, sa.Numeric)
        assert (column_type.precision, column_type.scale) == (20, 4)

    margin_type = ForecastLine.__table__.columns[
        "ebitda_margin_pct"
    ].type
    assert isinstance(margin_type, sa.Numeric)
    assert (margin_type.precision, margin_type.scale) == (7, 4)


def test_forecast_tables_have_no_mutable_updated_at_column():
    assert "updated_at" not in {
        column.name for column in ForecastRevision.__table__.columns
    }
    assert "updated_at" not in {
        column.name for column in ForecastLine.__table__.columns
    }


def test_persisted_forecast_revision_cannot_be_updated(
    app, admin_user, company
):
    revision = ResearchCommandService.create_forecast_revision(
        company.id,
        actor_user_id=admin_user.id,
        payload=_forecast_payload(lines=[_line()]),
    )
    revision_id = revision.id

    revision.assumptions = "Silently rewritten assumptions"
    with pytest.raises(sa.exc.InvalidRequestError, match="immutable"):
        db.session.commit()
    db.session.rollback()

    assert db.session.get(ForecastRevision, revision_id).assumptions == (
        "Administrator-supplied estimate snapshot"
    )


def test_persisted_forecast_line_cannot_be_updated(
    app, admin_user, company
):
    revision = ResearchCommandService.create_forecast_revision(
        company.id,
        actor_user_id=admin_user.id,
        payload=_forecast_payload(lines=[_line()]),
    )
    line = db.session.scalar(
        sa.select(ForecastLine).where(
            ForecastLine.forecast_revision_id == revision.id
        )
    )
    line_id = line.id

    line.eps = Decimal("9999.0000")
    with pytest.raises(sa.exc.InvalidRequestError, match="immutable"):
        db.session.commit()
    db.session.rollback()

    assert db.session.get(ForecastLine, line_id).eps == Decimal("57.1234")


@pytest.mark.parametrize("target_name", ["revision", "line"])
def test_persisted_forecast_history_cannot_be_deleted(
    app, admin_user, company, target_name
):
    revision = ResearchCommandService.create_forecast_revision(
        company.id,
        actor_user_id=admin_user.id,
        payload=_forecast_payload(lines=[_line()]),
    )
    target = revision
    if target_name == "line":
        target = db.session.scalar(
            sa.select(ForecastLine).where(
                ForecastLine.forecast_revision_id == revision.id
            )
        )

    db.session.delete(target)
    with pytest.raises(sa.exc.InvalidRequestError, match="immutable"):
        db.session.commit()
    db.session.rollback()

    assert db.session.get(type(target), target.id) is not None


def test_no_update_or_delete_forecast_commands_exist():
    forbidden = {
        "update_forecast_revision",
        "patch_forecast_revision",
        "delete_forecast_revision",
        "remove_forecast_revision",
        "update_forecast_line",
        "delete_forecast_line",
    }
    assert not {
        name
        for name in forbidden
        if hasattr(ResearchCommandService, name)
    }


def test_prior_forecast_revision_remains_unchanged_after_later_revision(
    app, admin_user, company
):
    first = ResearchCommandService.create_forecast_revision(
        company.id,
        actor_user_id=admin_user.id,
        payload=_forecast_payload(lines=[_line()]),
    )
    original_fields = {
        field: getattr(first, field)
        for field in (
            "revision_number",
            "supersedes_revision_id",
            "as_of_date",
            "assumptions",
            "change_reason",
        )
    }

    ResearchCommandService.create_forecast_revision(
        company.id,
        actor_user_id=admin_user.id,
        payload=_forecast_payload(
            base_revision_id=first.id,
            change_reason="Updated forecast",
            lines=[
                _line(fiscal_year=2027, revenue=Decimal("2500.0000"))
            ],
        ),
    )

    db.session.expire_all()
    persisted = db.session.get(ForecastRevision, first.id)
    assert {
        field: getattr(persisted, field)
        for field in original_fields
    } == original_fields
