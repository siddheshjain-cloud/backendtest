"""Plan Task 9: immutable method-neutral valuation revisions.

These tests lock the frozen ``ValuationRevision``/``ValuationReferenceLine``
contract: independent revision streams by company and method, append-only
reference lines, extensible metric/unit slugs, same-company forecast linkage,
the eight method-specific validation rules, optional administrator-supplied
enterprise-to-equity bridge snapshots, stale-base/unique-race semantics,
atomic rollback, and immutability.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from app import db
from app.models import (
    Company,
    ForecastLine,
    ForecastRevision,
    ValuationReferenceLine,
    ValuationRevision,
)
from app.models.research_types import ValuationMethod
from app.services.research_command_service import ResearchCommandService
from app.utils.research_errors import (
    ResearchConflictError,
    ResearchNotFoundError,
    ResearchValidationError,
)


AS_OF_DATE = date(2026, 9, 4)
VALID_ISIN = "INE0LOJ01019"
VALID_UNITS = ("ABSOLUTE", "THOUSAND", "LAKH", "CRORE", "MILLION")
ALL_METHODS = (
    ValuationMethod.PE,
    ValuationMethod.EV_EBITDA,
    ValuationMethod.PB,
    ValuationMethod.NAV,
    ValuationMethod.SOTP,
    ValuationMethod.ASSET_VALUE,
    ValuationMethod.UNIT_BASED,
    ValuationMethod.OTHER,
)


def _commit_expect_integrity() -> None:
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()


def _reference_line(**overrides: object) -> dict[str, object]:
    line: dict[str, object] = {
        "reference_forecast_revision_id": None,
        "reference_fiscal_year": None,
        "reference_metric": "NET_DEBT",
        "reference_metric_value": Decimal("75.0000"),
        "reference_metric_unit": "INR_CRORE",
        "reference_metric_basis": "Administrator balance-sheet snapshot",
        "sort_order": 0,
    }
    line.update(overrides)
    return line


def _valuation_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "valuation_method": ValuationMethod.EV_EBITDA,
        "reference_lines": [
            _reference_line(),
            _reference_line(
                reference_metric="EBITDA",
                reference_metric_value=Decimal("125.0000"),
                reference_metric_unit="INR_CRORE",
                reference_metric_basis="Internal FY27 estimate",
                sort_order=1,
            ),
        ],
        "justified_multiple": Decimal("12.0000"),
        "implied_enterprise_value": Decimal("1500.0000"),
        "net_debt": Decimal("75.0000"),
        "other_equity_adjustment": Decimal("0.0000"),
        "implied_future_equity_value": Decimal("1425.0000"),
        "required_return_pct": Decimal("15.0000"),
        "discount_period_years": Decimal("2.0000"),
        "present_value": Decimal("1077.5000"),
        "current_market_cap": Decimal("900.0000"),
        "currency": "INR",
        "unit": "CRORE",
        "valuation_notes": "Internal EV/EBITDA valuation conclusion",
        "as_of_date": AS_OF_DATE,
    }
    payload.update(overrides)
    return payload


def _forecast_line(
    *,
    forecast_revision_id: str,
    fiscal_year: int,
    metric: str,
) -> ForecastLine:
    values = {
        "forecast_revision_id": forecast_revision_id,
        "fiscal_year": fiscal_year,
        "is_estimate": True,
        "revenue": None,
        "ebitda": None,
        "ebitda_margin_pct": None,
        "pat": None,
        "eps": None,
        "currency": "INR",
        "unit": "CRORE",
    }
    if metric == "REVENUE":
        values["revenue"] = Decimal("100.0000")
    elif metric == "EBITDA":
        values["ebitda"] = Decimal("20.0000")
    elif metric == "PAT":
        values["pat"] = Decimal("10.0000")
    elif metric == "EPS":
        values["eps"] = Decimal("5.0000")
    else:
        raise ValueError(f"Unsupported test metric {metric}")
    return ForecastLine(**values)


def _forecast_with_line(
    *, company_id: str, actor_user_id: str, metric: str
) -> tuple[ForecastRevision, ForecastLine]:
    revision = ResearchCommandService.create_forecast_revision(
        company_id,
        actor_user_id=actor_user_id,
        payload={
            "as_of_date": AS_OF_DATE,
            "assumptions": "Administrator-supplied estimate",
            "lines": [
                {
                    "fiscal_year": 2027,
                    "is_estimate": True,
                    "revenue": (
                        Decimal("100.0000") if metric == "REVENUE" else None
                    ),
                    "ebitda": (
                        Decimal("20.0000") if metric == "EBITDA" else None
                    ),
                    "pat": (
                        Decimal("10.0000") if metric == "PAT" else None
                    ),
                    "eps": (
                        Decimal("5.0000") if metric == "EPS" else None
                    ),
                    "currency": "INR",
                    "unit": "CRORE",
                }
            ],
        },
    )
    line = db.session.scalar(
        sa.select(ForecastLine).where(
            ForecastLine.forecast_revision_id == revision.id
        )
    )
    return revision, line


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


def test_valuation_models_are_exported_from_app_models():
    assert ValuationRevision.__tablename__ == "valuation_revision"
    assert ValuationReferenceLine.__tablename__ == "valuation_reference_line"
    assert callable(ResearchCommandService.create_valuation_revision)


def test_valuation_tables_have_exact_columns_and_constraints(app):
    assert _reflected_columns("valuation_revision") == {
        "id": False,
        "created_at": False,
        "company_id": False,
        "valuation_method": False,
        "revision_number": False,
        "supersedes_revision_id": True,
        "justified_multiple": True,
        "implied_enterprise_value": True,
        "net_debt": True,
        "other_equity_adjustment": True,
        "implied_future_equity_value": True,
        "required_return_pct": True,
        "discount_period_years": True,
        "present_value": True,
        "current_market_cap": True,
        "currency": True,
        "unit": True,
        "valuation_notes": True,
        "as_of_date": False,
        "change_reason": True,
        "created_by_user_id": False,
    }
    assert _reflected_columns("valuation_reference_line") == {
        "id": False,
        "created_at": False,
        "valuation_revision_id": False,
        "reference_forecast_revision_id": True,
        "reference_fiscal_year": True,
        "reference_metric": False,
        "reference_metric_value": False,
        "reference_metric_unit": False,
        "reference_metric_basis": False,
        "sort_order": False,
    }

    inspector = sa.inspect(db.engine)
    revision_unique = {
        constraint["name"]: sorted(constraint["column_names"])
        for constraint in inspector.get_unique_constraints(
            "valuation_revision"
        )
    }
    assert revision_unique[
        "uq_valuation_revision_company_method_number"
    ] == ["company_id", "revision_number", "valuation_method"]

    line_unique = {
        constraint["name"]: sorted(constraint["column_names"])
        for constraint in inspector.get_unique_constraints(
            "valuation_reference_line"
        )
    }
    assert line_unique[
        "uq_valuation_reference_line_revision_sort_order"
    ] == ["sort_order", "valuation_revision_id"]

    revision_fks = {
        frozenset(constraint["constrained_columns"]): constraint[
            "referred_table"
        ]
        for constraint in inspector.get_foreign_keys("valuation_revision")
    }
    assert revision_fks[frozenset({"company_id"})] == "company"
    assert revision_fks[frozenset({"created_by_user_id"})] == "user"
    assert (
        revision_fks[frozenset({"supersedes_revision_id"})]
        == "valuation_revision"
    )

    line_fks = {
        frozenset(constraint["constrained_columns"]): constraint[
            "referred_table"
        ]
        for constraint in inspector.get_foreign_keys("valuation_reference_line")
    }
    assert line_fks[frozenset({"valuation_revision_id"})] == "valuation_revision"
    assert (
        line_fks[frozenset({"reference_forecast_revision_id"})]
        == "forecast_revision"
    )

    check_names = {
        constraint["name"]
        for constraint in inspector.get_check_constraints(
            "valuation_revision"
        )
    }
    assert "ck_valuation_revision_number_positive" in check_names
    assert "ck_valuation_revision_discount_positive" in check_names


def test_first_valuation_revision_persists_header_and_lines(
    app, admin_user, company
):
    revision = ResearchCommandService.create_valuation_revision(
        company.id,
        actor_user_id=admin_user.id,
        payload=_valuation_payload(),
    )

    persisted = db.session.get(ValuationRevision, revision.id)
    assert persisted.revision_number == 1
    assert persisted.supersedes_revision_id is None
    assert persisted.change_reason is None
    assert persisted.company_id == company.id
    assert persisted.created_by_user_id == admin_user.id
    assert persisted.valuation_method == ValuationMethod.EV_EBITDA
    assert persisted.as_of_date == AS_OF_DATE
    assert persisted.justified_multiple == Decimal("12.0000")
    assert persisted.implied_enterprise_value == Decimal("1500.0000")
    assert persisted.net_debt == Decimal("75.0000")
    assert persisted.other_equity_adjustment == Decimal("0.0000")
    assert persisted.implied_future_equity_value == Decimal("1425.0000")
    assert persisted.required_return_pct == Decimal("15.0000")
    assert persisted.discount_period_years == Decimal("2.0000")
    assert persisted.present_value == Decimal("1077.5000")
    assert persisted.current_market_cap == Decimal("900.0000")
    assert persisted.currency == "INR"
    assert persisted.unit == "CRORE"
    assert persisted.valuation_notes == "Internal EV/EBITDA valuation conclusion"
    assert persisted.created_at is not None
    assert isinstance(persisted.id, str)
    assert len(persisted.id) == 36
    assert uuid.UUID(persisted.id).version == 4

    lines = db.session.scalars(
        sa.select(ValuationReferenceLine)
        .where(
            ValuationReferenceLine.valuation_revision_id == revision.id
        )
        .order_by(ValuationReferenceLine.sort_order)
    ).all()
    assert [line.sort_order for line in lines] == [0, 1]
    assert [line.reference_metric for line in lines] == [
        "NET_DEBT",
        "EBITDA",
    ]
    assert all(line.valuation_revision_id == revision.id for line in lines)


def test_first_valuation_revision_accepts_absent_or_null_base_id(
    app, admin_user, company
):
    payload = _valuation_payload(reference_lines=[])
    payload.pop("base_revision_id", None)
    revision = ResearchCommandService.create_valuation_revision(
        company.id,
        actor_user_id=admin_user.id,
        payload=payload,
    )
    assert revision.revision_number == 1
    assert revision.supersedes_revision_id is None


def test_first_valuation_revision_rejects_non_null_base_or_change_reason(
    app, admin_user, company
):
    with pytest.raises(ResearchConflictError) as exc_info:
        ResearchCommandService.create_valuation_revision(
            company.id,
            actor_user_id=admin_user.id,
            payload=_valuation_payload(base_revision_id="does-not-exist"),
        )
    assert exc_info.value.code == "revision_conflict"

    with pytest.raises(ResearchValidationError) as exc_info:
        ResearchCommandService.create_valuation_revision(
            company.id,
            actor_user_id=admin_user.id,
            payload=_valuation_payload(change_reason="Initial valuation"),
        )
    assert "change_reason" in exc_info.value.details


def test_valuation_method_and_as_of_date_are_required(
    app, admin_user, company
):
    for field in ("valuation_method", "as_of_date"):
        payload = _valuation_payload()
        payload.pop(field)
        with pytest.raises(ResearchValidationError) as exc_info:
            ResearchCommandService.create_valuation_revision(
                company.id,
                actor_user_id=admin_user.id,
                payload=payload,
            )
        assert field in exc_info.value.details
        assert not db.session().in_transaction()


def test_valuation_method_is_a_controlled_classification(
    app, admin_user, company
):
    with pytest.raises(ResearchValidationError) as exc_info:
        ResearchCommandService.create_valuation_revision(
            company.id,
            actor_user_id=admin_user.id,
            payload=_valuation_payload(valuation_method="DCF"),
        )
    assert exc_info.value.code == "validation_error"
    assert "valuation_method" in exc_info.value.details


def test_zero_one_and_many_reference_lines_are_preserved(
    app, admin_user, company, ticker_factory
):
    zero = ResearchCommandService.create_valuation_revision(
        company.id,
        actor_user_id=admin_user.id,
        payload=_valuation_payload(
            valuation_method=ValuationMethod.ASSET_VALUE,
            reference_lines=[],
        ),
    )
    assert db.session.scalar(
        sa.select(sa.func.count())
        .select_from(ValuationReferenceLine)
        .where(ValuationReferenceLine.valuation_revision_id == zero.id)
    ) == 0

    one = ResearchCommandService.create_valuation_revision(
        company.id,
        actor_user_id=admin_user.id,
        payload=_valuation_payload(
            valuation_method=ValuationMethod.UNIT_BASED,
            reference_lines=[_reference_line()],
        ),
    )
    assert db.session.scalar(
        sa.select(sa.func.count())
        .select_from(ValuationReferenceLine)
        .where(ValuationReferenceLine.valuation_revision_id == one.id)
    ) == 1

    many = ResearchCommandService.create_valuation_revision(
        company.id,
        actor_user_id=admin_user.id,
        payload=_valuation_payload(
            valuation_method=ValuationMethod.OTHER,
            reference_lines=[
                _reference_line(sort_order=index)
                for index in range(4)
            ],
            valuation_notes="Extensible multi-input methodology",
        ),
    )
    assert db.session.scalar(
        sa.select(sa.func.count())
        .select_from(ValuationReferenceLine)
        .where(ValuationReferenceLine.valuation_revision_id == many.id)
    ) == 4


def test_reference_line_requires_metric_value_unit_and_basis(
    app, admin_user, company
):
    for field in (
        "reference_metric",
        "reference_metric_value",
        "reference_metric_unit",
        "reference_metric_basis",
    ):
        line = _reference_line()
        line.pop(field)
        with pytest.raises(ResearchValidationError) as exc_info:
            ResearchCommandService.create_valuation_revision(
                company.id,
                actor_user_id=admin_user.id,
                payload=_valuation_payload(
                    valuation_method=ValuationMethod.ASSET_VALUE,
                    reference_lines=[line],
                ),
            )
        assert exc_info.value.code == "validation_error"
        assert field in exc_info.value.details
        assert not db.session().in_transaction()


def test_reference_metric_and_unit_are_extensible_uppercase_slugs(
    app, admin_user, company
):
    revision = ResearchCommandService.create_valuation_revision(
        company.id,
        actor_user_id=admin_user.id,
        payload=_valuation_payload(
            valuation_method=ValuationMethod.UNIT_BASED,
            reference_lines=[
                _reference_line(
                    reference_metric="VALUE_PER_ROOM",
                    reference_metric_value=Decimal("12.0000"),
                    reference_metric_unit="INR_CRORE_PER_ROOM",
                    reference_metric_basis="Administrator benchmark",
                    sort_order=0,
                )
            ],
        ),
    )
    line = db.session.scalar(
        sa.select(ValuationReferenceLine).where(
            ValuationReferenceLine.valuation_revision_id == revision.id
        )
    )
    assert line.reference_metric == "VALUE_PER_ROOM"
    assert line.reference_metric_unit == "INR_CRORE_PER_ROOM"

    for field, bad in (
        ("reference_metric", "value_per_room"),
        ("reference_metric", "METRIC WITH SPACE"),
        ("reference_metric", ""),
        ("reference_metric", "A" * 65),
        ("reference_metric_unit", "inr_crore"),
        ("reference_metric_unit", "METRIC/UNIT"),
    ):
        line = _reference_line(**{field: bad})
        with pytest.raises(ResearchValidationError) as exc_info:
            ResearchCommandService.create_valuation_revision(
                company.id,
                actor_user_id=admin_user.id,
                payload=_valuation_payload(
                    valuation_method=ValuationMethod.ASSET_VALUE,
                    reference_lines=[line],
                ),
            )
        assert field in exc_info.value.details


def test_reference_line_value_must_be_finite_decimal(
    app, admin_user, company
):
    for bad in ("1.5", 1, Decimal("NaN"), None):
        with pytest.raises(ResearchValidationError) as exc_info:
            ResearchCommandService.create_valuation_revision(
                company.id,
                actor_user_id=admin_user.id,
                payload=_valuation_payload(
                    valuation_method=ValuationMethod.ASSET_VALUE,
                    reference_lines=[
                        _reference_line(reference_metric_value=bad)
                    ],
                ),
            )
        assert "reference_metric_value" in exc_info.value.details


def test_reference_sort_order_is_non_negative_and_unique(
    app, admin_user, company
):
    for bad in (-1, "0", 0.0, True, None):
        with pytest.raises(ResearchValidationError) as exc_info:
            ResearchCommandService.create_valuation_revision(
                company.id,
                actor_user_id=admin_user.id,
                payload=_valuation_payload(
                    valuation_method=ValuationMethod.UNIT_BASED,
                    reference_lines=[
                        _reference_line(sort_order=bad)
                    ],
                ),
            )
        assert "sort_order" in exc_info.value.details

    with pytest.raises(ResearchValidationError) as exc_info:
        ResearchCommandService.create_valuation_revision(
            company.id,
            actor_user_id=admin_user.id,
            payload=_valuation_payload(
                valuation_method=ValuationMethod.ASSET_VALUE,
                reference_lines=[
                    _reference_line(sort_order=0),
                    _reference_line(sort_order=0),
                ],
            ),
        )
    assert "sort_order" in exc_info.value.details


def test_forecast_link_requires_fiscal_year_and_existing_metric(
    app, admin_user, company
):
    forecast, _ = _forecast_with_line(
        company_id=company.id,
        actor_user_id=admin_user.id,
        metric="EBITDA",
    )

    with pytest.raises(ResearchValidationError) as exc_info:
        ResearchCommandService.create_valuation_revision(
            company.id,
            actor_user_id=admin_user.id,
            payload=_valuation_payload(
                reference_lines=[
                    _reference_line(
                        reference_forecast_revision_id=forecast.id,
                        reference_fiscal_year=None,
                        reference_metric="EBITDA",
                    )
                ]
            ),
        )
    assert "reference_fiscal_year" in exc_info.value.details

    with pytest.raises(ResearchValidationError) as exc_info:
        ResearchCommandService.create_valuation_revision(
            company.id,
            actor_user_id=admin_user.id,
            payload=_valuation_payload(
                reference_lines=[
                    _reference_line(
                        reference_forecast_revision_id=forecast.id,
                        reference_fiscal_year=2027,
                        reference_metric="EPS",
                    )
                ]
            ),
        )
    assert "reference_metric" in exc_info.value.details

    revision = ResearchCommandService.create_valuation_revision(
        company.id,
        actor_user_id=admin_user.id,
        payload=_valuation_payload(
            reference_lines=[
                _reference_line(
                    reference_forecast_revision_id=forecast.id,
                    reference_fiscal_year=2027,
                    reference_metric="EBITDA",
                )
            ]
        ),
    )
    line = db.session.scalar(
        sa.select(ValuationReferenceLine).where(
            ValuationReferenceLine.valuation_revision_id == revision.id
        )
    )
    assert line.reference_forecast_revision_id == forecast.id
    assert line.reference_fiscal_year == 2027


def test_forecast_link_must_belong_to_same_company(
    app, admin_user, company, ticker_factory
):
    other_ticker = ticker_factory(symbol="WIPRO", instrument_token=21)
    other_company = ResearchCommandService.create_company(
        {
            "ticker_id": other_ticker.id,
            "legal_name": "Second Limited",
            "isin": "US0378331005",
        },
        actor_user_id="actor-1",
    )
    forecast, _ = _forecast_with_line(
        company_id=other_company.id,
        actor_user_id=admin_user.id,
        metric="EBITDA",
    )

    with pytest.raises(ResearchValidationError) as exc_info:
        ResearchCommandService.create_valuation_revision(
            company.id,
            actor_user_id=admin_user.id,
            payload=_valuation_payload(
                reference_lines=[
                    _reference_line(
                        reference_forecast_revision_id=forecast.id,
                        reference_fiscal_year=2027,
                        reference_metric="EBITDA",
                    )
                ]
            ),
        )
    assert exc_info.value.code == "validation_error"


def test_pe_can_link_forecast_eps_and_requires_only_eps_or_pat(
    app, admin_user, company
):
    forecast, _ = _forecast_with_line(
        company_id=company.id,
        actor_user_id=admin_user.id,
        metric="EPS",
    )
    revision = ResearchCommandService.create_valuation_revision(
        company.id,
        actor_user_id=admin_user.id,
        payload=_valuation_payload(
            valuation_method=ValuationMethod.PE,
            reference_lines=[
                _reference_line(
                    reference_forecast_revision_id=forecast.id,
                    reference_fiscal_year=2027,
                    reference_metric="EPS",
                    reference_metric_unit="INR_PER_SHARE",
                )
            ],
        ),
    )
    assert revision.valuation_method == ValuationMethod.PE

    with pytest.raises(ResearchValidationError) as exc_info:
        ResearchCommandService.create_valuation_revision(
            company.id,
            actor_user_id=admin_user.id,
            payload=_valuation_payload(
                valuation_method=ValuationMethod.PE,
                reference_lines=[_reference_line(reference_metric="NET_DEBT")],
            ),
        )
    assert "reference_metric" in exc_info.value.details


def test_unit_based_requires_at_least_one_line_and_preserves_multiple_inputs(
    app, admin_user, company
):
    with pytest.raises(ResearchValidationError) as exc_info:
        ResearchCommandService.create_valuation_revision(
            company.id,
            actor_user_id=admin_user.id,
            payload=_valuation_payload(
                valuation_method=ValuationMethod.UNIT_BASED,
                reference_lines=[],
            ),
        )
    assert "reference_lines" in exc_info.value.details

    lines = [
        _reference_line(
            reference_metric="CAPACITY",
            reference_metric_value=Decimal("500000"),
            reference_metric_unit="TONNES_PER_YEAR",
            sort_order=0,
        ),
        _reference_line(
            reference_metric="EV_PER_TON",
            reference_metric_value=Decimal("20000"),
            reference_metric_unit="INR_PER_TON",
            sort_order=1,
        ),
        _reference_line(
            reference_metric="EBITDA_PER_TON",
            reference_metric_value=Decimal("8200"),
            reference_metric_unit="INR_PER_TON",
            sort_order=2,
        ),
        _reference_line(
            reference_metric="COST_PER_TON",
            reference_metric_value=Decimal("41500"),
            reference_metric_unit="INR_PER_TON",
            sort_order=3,
        ),
    ]
    revision = ResearchCommandService.create_valuation_revision(
        company.id,
        actor_user_id=admin_user.id,
        payload=_valuation_payload(
            valuation_method=ValuationMethod.UNIT_BASED,
            reference_lines=lines,
        ),
    )
    persisted = db.session.scalars(
        sa.select(ValuationReferenceLine)
        .where(
            ValuationReferenceLine.valuation_revision_id == revision.id
        )
        .order_by(ValuationReferenceLine.sort_order)
    ).all()
    assert [line.reference_metric for line in persisted] == [
        "CAPACITY",
        "EV_PER_TON",
        "EBITDA_PER_TON",
        "COST_PER_TON",
    ]


def test_nav_allows_at_most_one_nav_reference_line(
    app, admin_user, company
):
    revision = ResearchCommandService.create_valuation_revision(
        company.id,
        actor_user_id=admin_user.id,
        payload=_valuation_payload(
            valuation_method=ValuationMethod.NAV,
            reference_lines=[
                _reference_line(
                    reference_metric="NAV",
                    reference_metric_value=Decimal("100.0000"),
                    reference_metric_unit="INR_CRORE",
                )
            ],
        ),
    )
    assert revision.valuation_method == ValuationMethod.NAV

    with pytest.raises(ResearchValidationError) as exc_info:
        ResearchCommandService.create_valuation_revision(
            company.id,
            actor_user_id=admin_user.id,
            payload=_valuation_payload(
                valuation_method=ValuationMethod.NAV,
                reference_lines=[
                    _reference_line(
                        reference_metric="NAV",
                        sort_order=0,
                    ),
                    _reference_line(
                        reference_metric="NAV",
                        sort_order=1,
                    ),
                ],
            ),
        )
    assert "reference_lines" in exc_info.value.details


def test_sotp_and_other_require_meaningful_valuation_notes(
    app, admin_user, company
):
    with pytest.raises(ResearchValidationError) as exc_info:
        ResearchCommandService.create_valuation_revision(
            company.id,
            actor_user_id=admin_user.id,
            payload=_valuation_payload(
                valuation_method=ValuationMethod.SOTP,
                valuation_notes=None,
            ),
        )
    assert "valuation_notes" in exc_info.value.details

    with pytest.raises(ResearchValidationError) as exc_info:
        ResearchCommandService.create_valuation_revision(
            company.id,
            actor_user_id=admin_user.id,
            payload=_valuation_payload(
                valuation_method=ValuationMethod.OTHER,
                valuation_notes="   ",
            ),
        )
    assert "valuation_notes" in exc_info.value.details

    for method in (ValuationMethod.SOTP, ValuationMethod.OTHER):
        revision = ResearchCommandService.create_valuation_revision(
            company.id,
            actor_user_id=admin_user.id,
            payload=_valuation_payload(
                valuation_method=method,
                reference_lines=[],
                valuation_notes="Sourced aggregate basis",
            ),
        )
        assert revision.valuation_method == method


def test_equity_value_methods_may_leave_bridge_fields_null(
    app, admin_user, company
):
    revision = ResearchCommandService.create_valuation_revision(
        company.id,
        actor_user_id=admin_user.id,
        payload=_valuation_payload(
            valuation_method=ValuationMethod.ASSET_VALUE,
            reference_lines=[],
            justified_multiple=None,
            implied_enterprise_value=None,
            net_debt=None,
            other_equity_adjustment=None,
            implied_future_equity_value=None,
            required_return_pct=None,
            discount_period_years=None,
            present_value=None,
            current_market_cap=None,
            currency=None,
            unit=None,
            valuation_notes="Replacement-value asset basis",
        ),
    )
    assert revision.implied_enterprise_value is None
    assert revision.net_debt is None
    assert revision.other_equity_adjustment is None
    assert revision.implied_future_equity_value is None


def test_bridge_sign_convention_and_inconsistent_snapshots_are_preserved(
    app, admin_user, company
):
    positive_debt = ResearchCommandService.create_valuation_revision(
        company.id,
        actor_user_id=admin_user.id,
        payload=_valuation_payload(
            valuation_method=ValuationMethod.EV_EBITDA,
            net_debt=Decimal("75.0000"),
        ),
    )
    assert positive_debt.net_debt == Decimal("75.0000")

    negative_cash = ResearchCommandService.create_valuation_revision(
        company.id,
        actor_user_id=admin_user.id,
        payload=_valuation_payload(
            valuation_method=ValuationMethod.EV_EBITDA,
            base_revision_id=positive_debt.id,
            change_reason="Updated net cash snapshot",
            net_debt=Decimal("-25.0000"),
            implied_enterprise_value=Decimal("1000.0000"),
            other_equity_adjustment=Decimal("5.0000"),
            implied_future_equity_value=Decimal("9999.0000"),
        ),
    )
    assert negative_cash.net_debt == Decimal("-25.0000")
    assert negative_cash.implied_future_equity_value == Decimal("9999.0000")
    assert negative_cash.other_equity_adjustment == Decimal("5.0000")


def test_every_valuation_requires_a_meaningful_conclusion(
    app, admin_user, company
):
    payload = _valuation_payload(
        valuation_method=ValuationMethod.ASSET_VALUE,
        reference_lines=[],
        implied_enterprise_value=None,
        implied_future_equity_value=None,
        present_value=None,
        valuation_notes=None,
        currency=None,
        unit=None,
    )
    with pytest.raises(ResearchValidationError) as exc_info:
        ResearchCommandService.create_valuation_revision(
            company.id,
            actor_user_id=admin_user.id,
            payload=payload,
        )
    assert exc_info.value.code == "validation_error"
    assert "valuation_notes" in exc_info.value.details


def test_currency_and_unit_are_required_when_monetary_values_are_present(
    app, admin_user, company
):
    for missing in (
        {"currency": None, "unit": None},
        {"currency": None, "unit": "CRORE"},
        {"currency": "INR", "unit": None},
    ):
        with pytest.raises(ResearchValidationError) as exc_info:
            ResearchCommandService.create_valuation_revision(
                company.id,
                actor_user_id=admin_user.id,
                payload=_valuation_payload(
                    valuation_method=ValuationMethod.EV_EBITDA,
                    **missing,
                ),
            )
        assert exc_info.value.code == "validation_error"
        assert "currency" in exc_info.value.details or "unit" in exc_info.value.details

    with pytest.raises(ResearchValidationError) as exc_info:
        ResearchCommandService.create_valuation_revision(
            company.id,
            actor_user_id=admin_user.id,
            payload=_valuation_payload(
                valuation_method=ValuationMethod.EV_EBITDA,
                currency="USD",
            ),
        )
    assert "currency" in exc_info.value.details

    with pytest.raises(ResearchValidationError) as exc_info:
        ResearchCommandService.create_valuation_revision(
            company.id,
            actor_user_id=admin_user.id,
            payload=_valuation_payload(
                valuation_method=ValuationMethod.EV_EBITDA,
                unit="TONS",
            ),
        )
    assert "unit" in exc_info.value.details


def test_valuation_revision_streams_are_independent_by_company_and_method(
    app, admin_user, company
):
    first_pe = ResearchCommandService.create_valuation_revision(
        company.id,
        actor_user_id=admin_user.id,
        payload=_valuation_payload(
            valuation_method=ValuationMethod.PE,
            reference_lines=[],
        ),
    )
    first_ev = ResearchCommandService.create_valuation_revision(
        company.id,
        actor_user_id=admin_user.id,
        payload=_valuation_payload(
            valuation_method=ValuationMethod.EV_EBITDA,
            reference_lines=[],
        ),
    )
    assert first_pe.revision_number == 1
    assert first_ev.revision_number == 1

    second_pe = ResearchCommandService.create_valuation_revision(
        company.id,
        actor_user_id=admin_user.id,
        payload=_valuation_payload(
            valuation_method=ValuationMethod.PE,
            reference_lines=[],
            base_revision_id=first_pe.id,
            change_reason="Updated PE",
        ),
    )
    assert second_pe.revision_number == 2
    assert second_pe.supersedes_revision_id == first_pe.id
    assert second_pe.valuation_method == ValuationMethod.PE

    still_ev = db.session.scalar(
        sa.select(ValuationRevision)
        .where(
            ValuationRevision.company_id == company.id,
            ValuationRevision.valuation_method == ValuationMethod.EV_EBITDA,
        )
        .order_by(ValuationRevision.revision_number.desc())
    )
    assert still_ev.id == first_ev.id
    assert still_ev.revision_number == 1


def test_second_valuation_revision_requires_current_base_and_reason(
    app, admin_user, company
):
    first = ResearchCommandService.create_valuation_revision(
        company.id,
        actor_user_id=admin_user.id,
        payload=_valuation_payload(reference_lines=[]),
    )

    with pytest.raises(ResearchValidationError) as exc_info:
        ResearchCommandService.create_valuation_revision(
            company.id,
            actor_user_id=admin_user.id,
            payload=_valuation_payload(
                reference_lines=[],
                base_revision_id=None,
            ),
        )
    assert "base_revision_id" in exc_info.value.details

    for bad_reason in (None, "", "   "):
        with pytest.raises(ResearchValidationError) as exc_info:
            ResearchCommandService.create_valuation_revision(
                company.id,
                actor_user_id=admin_user.id,
                payload=_valuation_payload(
                    reference_lines=[],
                    base_revision_id=first.id,
                    change_reason=bad_reason,
                ),
            )
        assert "change_reason" in exc_info.value.details
        assert not db.session().in_transaction()


def test_stale_valuation_base_raises_conflict_without_overwriting(
    app, admin_user, company
):
    first = ResearchCommandService.create_valuation_revision(
        company.id,
        actor_user_id=admin_user.id,
        payload=_valuation_payload(reference_lines=[]),
    )
    ResearchCommandService.create_valuation_revision(
        company.id,
        actor_user_id=admin_user.id,
        payload=_valuation_payload(
            reference_lines=[],
            base_revision_id=first.id,
            change_reason="First update",
        ),
    )

    with pytest.raises(ResearchConflictError) as exc_info:
        ResearchCommandService.create_valuation_revision(
            company.id,
            actor_user_id=admin_user.id,
            payload=_valuation_payload(
                reference_lines=[],
                base_revision_id=first.id,
                change_reason="Stale update",
            ),
        )

    assert exc_info.value.code == "revision_conflict"
    assert exc_info.value.message == "Research revision changed"
    assert not db.session().in_transaction()

    numbers = db.session.scalars(
        sa.select(ValuationRevision.revision_number)
        .where(
            ValuationRevision.company_id == company.id,
            ValuationRevision.valuation_method == ValuationMethod.EV_EBITDA,
        )
        .order_by(ValuationRevision.revision_number)
    ).all()
    assert numbers == [1, 2]


def test_unique_race_is_translated_to_revision_conflict(
    app, admin_user, company, monkeypatch
):
    first = ResearchCommandService.create_valuation_revision(
        company.id,
        actor_user_id=admin_user.id,
        payload=_valuation_payload(reference_lines=[]),
    )
    ResearchCommandService.create_valuation_revision(
        company.id,
        actor_user_id=admin_user.id,
        payload=_valuation_payload(
            reference_lines=[],
            base_revision_id=first.id,
            change_reason="Winning writer",
        ),
    )

    def _stale_current(_cls, _company_id, _method):
        return first

    monkeypatch.setattr(
        ResearchCommandService,
        "_current_valuation_revision_locked",
        classmethod(_stale_current),
    )

    with pytest.raises(ResearchConflictError) as exc_info:
        ResearchCommandService.create_valuation_revision(
            company.id,
            actor_user_id=admin_user.id,
            payload=_valuation_payload(
                reference_lines=[],
                base_revision_id=first.id,
                change_reason="Losing writer",
            ),
        )

    assert exc_info.value.code == "revision_conflict"
    assert exc_info.value.message == "Research revision changed"
    assert not db.session().in_transaction()


def test_company_method_and_revision_number_are_unique_at_database(
    app, admin_user, company
):
    first = ResearchCommandService.create_valuation_revision(
        company.id,
        actor_user_id=admin_user.id,
        payload=_valuation_payload(reference_lines=[]),
    )
    db.session.add(
        ValuationRevision(
            company_id=company.id,
            valuation_method=first.valuation_method,
            revision_number=first.revision_number,
            supersedes_revision_id=None,
            as_of_date=AS_OF_DATE,
            change_reason=None,
            created_by_user_id=admin_user.id,
            implied_future_equity_value=Decimal("1.0000"),
            currency="INR",
            unit="CRORE",
        )
    )
    _commit_expect_integrity()


def test_invalid_valuation_line_causes_complete_atomic_rollback(
    app, admin_user, company
):
    with pytest.raises(ResearchValidationError):
        ResearchCommandService.create_valuation_revision(
            company.id,
            actor_user_id=admin_user.id,
            payload=_valuation_payload(
                valuation_method=ValuationMethod.ASSET_VALUE,
                reference_lines=[
                    _reference_line(sort_order=0),
                    _reference_line(sort_order=1, reference_metric="bad metric"),
                ],
            ),
        )

    assert not db.session().in_transaction()
    assert db.session.scalar(
        sa.select(sa.func.count()).select_from(ValuationRevision)
    ) == 0
    assert db.session.scalar(
        sa.select(sa.func.count()).select_from(ValuationReferenceLine)
    ) == 0


def test_reference_lines_are_immutable_and_cannot_be_deleted(
    app, admin_user, company
):
    revision = ResearchCommandService.create_valuation_revision(
        company.id,
        actor_user_id=admin_user.id,
        payload=_valuation_payload(),
    )
    line = db.session.scalar(
        sa.select(ValuationReferenceLine).where(
            ValuationReferenceLine.valuation_revision_id == revision.id
        )
    )
    line.reference_metric_value = Decimal("9999.0000")
    with pytest.raises(sa.exc.InvalidRequestError, match="immutable"):
        db.session.commit()
    db.session.rollback()

    db.session.delete(line)
    with pytest.raises(sa.exc.InvalidRequestError, match="immutable"):
        db.session.commit()
    db.session.rollback()

    persisted = db.session.get(ValuationReferenceLine, line.id)
    assert persisted.reference_metric_value == Decimal("75.0000")


def test_valuation_revision_is_immutable_and_cannot_be_deleted(
    app, admin_user, company
):
    revision = ResearchCommandService.create_valuation_revision(
        company.id,
        actor_user_id=admin_user.id,
        payload=_valuation_payload(reference_lines=[]),
    )
    revision.valuation_notes = "Rewritten"
    with pytest.raises(sa.exc.InvalidRequestError, match="immutable"):
        db.session.commit()
    db.session.rollback()

    db.session.delete(revision)
    with pytest.raises(sa.exc.InvalidRequestError, match="immutable"):
        db.session.commit()
    db.session.rollback()

    assert db.session.get(ValuationRevision, revision.id) is not None


def test_no_update_or_delete_valuation_commands_exist():
    forbidden = {
        "update_valuation_revision",
        "patch_valuation_revision",
        "delete_valuation_revision",
        "remove_valuation_revision",
        "update_valuation_reference_line",
        "delete_valuation_reference_line",
    }
    assert not {
        name
        for name in forbidden
        if hasattr(ResearchCommandService, name)
    }


def test_service_requires_existing_company_and_actor(
    app, admin_user, company
):
    with pytest.raises(ResearchNotFoundError):
        ResearchCommandService.create_valuation_revision(
            "missing-company",
            actor_user_id=admin_user.id,
            payload=_valuation_payload(reference_lines=[]),
        )

    with pytest.raises(ResearchNotFoundError):
        ResearchCommandService.create_valuation_revision(
            company.id,
            actor_user_id="missing-user",
            payload=_valuation_payload(reference_lines=[]),
        )


def test_same_forecast_can_support_multiple_valuation_methods(
    app, admin_user, company
):
    forecast, _ = _forecast_with_line(
        company_id=company.id,
        actor_user_id=admin_user.id,
        metric="EBITDA",
    )
    ev = ResearchCommandService.create_valuation_revision(
        company.id,
        actor_user_id=admin_user.id,
        payload=_valuation_payload(
            valuation_method=ValuationMethod.EV_EBITDA,
            reference_lines=[
                _reference_line(
                    reference_forecast_revision_id=forecast.id,
                    reference_fiscal_year=2027,
                    reference_metric="EBITDA",
                )
            ],
        ),
    )
    other = ResearchCommandService.create_valuation_revision(
        company.id,
        actor_user_id=admin_user.id,
        payload=_valuation_payload(
            valuation_method=ValuationMethod.OTHER,
            reference_lines=[
                _reference_line(
                    reference_forecast_revision_id=forecast.id,
                    reference_fiscal_year=2027,
                    reference_metric="EBITDA",
                )
            ],
            valuation_notes="Cross-check against EBITDA forecast",
        ),
    )
    assert ev.revision_number == 1
    assert other.revision_number == 1
    assert forecast.id == db.session.get(ForecastRevision, forecast.id).id


def test_valuation_uses_fixed_precision_numeric_columns(app):
    expected_numeric = {
        "justified_multiple",
        "implied_enterprise_value",
        "net_debt",
        "other_equity_adjustment",
        "implied_future_equity_value",
        "required_return_pct",
        "discount_period_years",
        "present_value",
        "current_market_cap",
    }
    for name in expected_numeric:
        column_type = ValuationRevision.__table__.columns[name].type
        assert isinstance(column_type, sa.Numeric)
        assert (column_type.precision, column_type.scale) == (20, 4)

    value_type = ValuationReferenceLine.__table__.columns[
        "reference_metric_value"
    ].type
    assert isinstance(value_type, sa.Numeric)
    assert (value_type.precision, value_type.scale) == (20, 4)


def test_valuation_revision_has_no_mutable_updated_at_column():
    assert "updated_at" not in {
        column.name for column in ValuationRevision.__table__.columns
    }
    assert "updated_at" not in {
        column.name for column in ValuationReferenceLine.__table__.columns
    }
