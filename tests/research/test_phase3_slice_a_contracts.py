"""Phase 3 Slice A: ``scenario``/``origin`` on ForecastRevision and
ValuationRevision, and the new FinancialMetricDefinition lookup table.

Locks the Slice A contract at the ORM level (migration-level coverage
already lives in tests/migrations/test_phase3_slice_a_contracts_migration.py):
nullable ``scenario`` preserving the legacy single-stream invariant via two
partial unique indexes (not a composite UniqueConstraint -- see
app/models/forecast.py's module docstring for why), ``origin`` defaulting
to ``HUMAN_AUTHORED``, the pre-existing immutable-after-insertion contract
surviving the new columns unchanged, and FinancialMetricDefinition's slug
uniqueness. Also reconfirms, unchanged, the one concrete market-agnostic
blocker Slice A's own design review surfaced: ResearchCommandService still
hard-rejects any currency other than INR -- Slice A does not touch this
frozen M1 validation, so this test exists to prove Slice A's new columns
don't accidentally interact with it, not to lift it.
"""

from __future__ import annotations

from datetime import date

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from app import db
from app.models import FinancialMetricDefinition, ForecastRevision, ValuationRevision
from app.models.research_types import FinancialMetricNamespace, InvestmentOrigin, Scenario
from app.services.research_command_service import ResearchCommandService
from app.utils.research_errors import ResearchValidationError


AS_OF_DATE = date(2026, 10, 8)
VALID_ISIN = "INE0LOJ01020"


@pytest.fixture
def company(ticker_factory):
    ticker = ticker_factory()
    return ResearchCommandService.create_company(
        {
            "ticker_id": ticker.id,
            "legal_name": "Phase 3 Slice A Test Company",
            "isin": VALID_ISIN,
        },
        actor_user_id="actor-1",
    )


def _commit_expect_integrity() -> None:
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()


def _forecast_row(*, company_id, actor_user_id, revision_number, scenario=None, origin=None):
    kwargs = dict(
        company_id=company_id,
        revision_number=revision_number,
        scenario=scenario,
        as_of_date=AS_OF_DATE,
        created_by_user_id=actor_user_id,
    )
    if origin is not None:
        kwargs["origin"] = origin
    return ForecastRevision(**kwargs)


def _valuation_row(
    *, company_id, actor_user_id, revision_number, scenario=None, valuation_method="PE"
):
    return ValuationRevision(
        company_id=company_id,
        valuation_method=valuation_method,
        revision_number=revision_number,
        scenario=scenario,
        as_of_date=AS_OF_DATE,
        created_by_user_id=actor_user_id,
    )


def test_forecast_revision_origin_defaults_to_human_authored(app, admin_user, company):
    revision = _forecast_row(
        company_id=company.id, actor_user_id=admin_user.id, revision_number=1
    )
    db.session.add(revision)
    db.session.commit()

    persisted = db.session.get(ForecastRevision, revision.id)
    assert persisted.origin == InvestmentOrigin.HUMAN_AUTHORED
    assert persisted.scenario is None


def test_forecast_revision_system_draft_origin_is_settable(app, admin_user, company):
    revision = _forecast_row(
        company_id=company.id,
        actor_user_id=admin_user.id,
        revision_number=1,
        origin=InvestmentOrigin.SYSTEM_DRAFT,
    )
    db.session.add(revision)
    db.session.commit()

    assert db.session.get(ForecastRevision, revision.id).origin == "SYSTEM_DRAFT"


def test_forecast_revision_legacy_null_scenario_uniqueness_still_enforced(
    app, admin_user, company
):
    """The exact invariant the partial-index fix exists to preserve: two
    NULL-scenario rows at the same (company_id, revision_number) must still
    collide, exactly as the single pre-Slice-A UniqueConstraint guaranteed.
    """

    db.session.add(
        _forecast_row(
            company_id=company.id, actor_user_id=admin_user.id, revision_number=1
        )
    )
    db.session.commit()

    db.session.add(
        _forecast_row(
            company_id=company.id, actor_user_id=admin_user.id, revision_number=1
        )
    )
    _commit_expect_integrity()


def test_forecast_revision_named_scenarios_coexist_and_dedupe_within_scenario(
    app, admin_user, company
):
    db.session.add_all(
        [
            _forecast_row(
                company_id=company.id,
                actor_user_id=admin_user.id,
                revision_number=1,
                scenario=Scenario.BULL,
            ),
            _forecast_row(
                company_id=company.id,
                actor_user_id=admin_user.id,
                revision_number=1,
                scenario=Scenario.BEAR,
            ),
            _forecast_row(
                company_id=company.id,
                actor_user_id=admin_user.id,
                revision_number=1,
                scenario=Scenario.MID_CYCLE,
            ),
            # and the legacy NULL stream coexists with all three
            _forecast_row(
                company_id=company.id, actor_user_id=admin_user.id, revision_number=1
            ),
        ]
    )
    db.session.commit()

    count = db.session.scalar(
        sa.select(sa.func.count()).select_from(ForecastRevision).where(
            ForecastRevision.company_id == company.id,
            ForecastRevision.revision_number == 1,
        )
    )
    assert count == 4

    db.session.add(
        _forecast_row(
            company_id=company.id,
            actor_user_id=admin_user.id,
            revision_number=1,
            scenario=Scenario.BULL,
        )
    )
    _commit_expect_integrity()


def test_forecast_revision_immutability_preserved_for_new_columns(
    app, admin_user, company
):
    """Slice A adds columns to an already-immutable-after-insertion table;
    this proves that contract still holds for the new columns too, not
    just the pre-existing ones.
    """

    revision = _forecast_row(
        company_id=company.id, actor_user_id=admin_user.id, revision_number=1
    )
    db.session.add(revision)
    db.session.commit()

    revision.origin = InvestmentOrigin.SYSTEM_DRAFT
    with pytest.raises(sa.exc.InvalidRequestError):
        db.session.commit()
    db.session.rollback()


def test_valuation_revision_scenario_uniqueness_mirrors_forecast_revision(
    app, admin_user, company
):
    db.session.add_all(
        [
            _valuation_row(
                company_id=company.id, actor_user_id=admin_user.id, revision_number=1
            ),
            _valuation_row(
                company_id=company.id,
                actor_user_id=admin_user.id,
                revision_number=1,
                scenario=Scenario.BULL,
            ),
        ]
    )
    db.session.commit()
    assert (
        db.session.scalar(
            sa.select(sa.func.count()).select_from(ValuationRevision)
        )
        == 2
    )

    db.session.add(
        _valuation_row(
            company_id=company.id, actor_user_id=admin_user.id, revision_number=1
        )
    )
    _commit_expect_integrity()


def test_financial_metric_definition_slug_uniqueness(app):
    db.session.add(
        FinancialMetricDefinition(
            slug="operating_revenue",
            label="Operating Revenue",
            statement_section="INCOME_STATEMENT",
            namespace=FinancialMetricNamespace.RAW,
            standard_unit="CRORE",
        )
    )
    db.session.commit()

    db.session.add(
        FinancialMetricDefinition(
            slug="operating_revenue",
            label="Operating Revenue (duplicate slug)",
            statement_section="INCOME_STATEMENT",
            namespace=FinancialMetricNamespace.RAW,
            standard_unit="CRORE",
        )
    )
    _commit_expect_integrity()

    db.session.add(
        FinancialMetricDefinition(
            slug="ebitda",
            label="EBITDA",
            statement_section="INCOME_STATEMENT",
            namespace=FinancialMetricNamespace.NORMALIZED,
            standard_unit="CRORE",
        )
    )
    db.session.commit()
    assert (
        db.session.scalar(
            sa.select(sa.func.count()).select_from(FinancialMetricDefinition)
        )
        == 2
    )


def test_currency_still_hard_locked_to_inr_unchanged_by_slice_a(
    app, admin_user, company
):
    """Not a new restriction -- confirms the pre-existing, frozen M1
    validation (tests/research/test_forecast_revisions.py::
    test_forecast_currency_is_fixed_to_inr) is untouched by Slice A's new
    scenario/origin columns. This IS the concrete, owner-flagged blocker
    for future US coverage (design doc §3.5/§10): ResearchCommandService
    rejects any non-INR currency outright, in frozen M1 code Slice A does
    not touch. Lifting it requires its own, separate, explicitly-approved
    change -- not something this test argues for, only documents.
    """

    with pytest.raises(ResearchValidationError) as exc_info:
        ResearchCommandService.create_forecast_revision(
            company.id,
            actor_user_id=admin_user.id,
            payload={
                "as_of_date": AS_OF_DATE,
                "lines": [
                    {
                        "fiscal_year": 2027,
                        "is_estimate": True,
                        "currency": "USD",
                        "unit": "CRORE",
                    }
                ],
            },
        )
    assert "currency" in exc_info.value.details
