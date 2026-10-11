"""Plan 5 Task 7: real-operation IKIO seed entrypoint (seed_ikio).

seed_ikio() is a narrow, independent entrypoint: it creates exactly one
Company and one QUARTERLY_RESULTS Document, with no research, forecast,
valuation, market-plan, ownership, governance, or disclosure data, and no
CMP. It does not call ResearchSeedService.run() (which builds a much larger
demo/test reference graph and is covered separately by
tests/research/test_p5t7_ikio_seed.py) and does not create any user other
than the supplied actor. These tests cover both the real-operation
preconditions (an existing ticker is required, the actor must be a real
admin, the payload's seed_version must match, a dry run must prove all of
that without writing anything) and the scope boundary itself.
"""

from __future__ import annotations

import json

import pytest
import sqlalchemy as sa

from app import db
from app.models import (
    Company,
    CompanyDisclosure,
    Document,
    ForecastRevision,
    GovernanceFlag,
    MarketPlanRevision,
    OwnershipSnapshot,
    ResearchRevision,
    Ticker,
    User,
    UserEntitlement,
    ValuationRevision,
)
from app.services.research_seed_service import (
    IKIO_COMPANY_NAME,
    IKIO_ISIN,
    IKIO_QUARTERLY_RESULTS_DATE,
    IKIO_QUARTERLY_RESULTS_PERIOD,
    IKIO_QUARTERLY_RESULTS_PUBLISHER,
    IKIO_QUARTERLY_RESULTS_TITLE,
    IKIO_QUARTERLY_RESULTS_URL,
    IKIO_SYMBOL,
    SEED_VERSION,
    seed_ikio,
)
from app.utils.research_errors import (
    ResearchForbiddenError,
    ResearchNotFoundError,
    ResearchValidationError,
)


def _payload_path(tmp_path, **overrides) -> "pathlib.Path":
    payload = {"seed_version": SEED_VERSION, "ticker_symbol": IKIO_SYMBOL}
    payload.update(overrides)
    path = tmp_path / "ikio_research.json"
    path.write_text(json.dumps(payload))
    return path


def _count(model) -> int:
    return (
        db.session.scalar(sa.select(sa.func.count()).select_from(model))
        or 0
    )


def test_missing_ticker_aborts_without_creating_market_data(
    app, admin_user, tmp_path
):
    payload = _payload_path(tmp_path)

    with pytest.raises(ResearchNotFoundError) as exc_info:
        seed_ikio(payload, admin_user.id)

    assert exc_info.value.code == "ikio_ticker_not_found"
    assert _count(Ticker) == 0
    assert _count(Company) == 0


def test_non_admin_actor_aborts(app, ticker_factory, user_factory, tmp_path):
    ticker_factory()
    non_admin = user_factory(email="not-an-admin@example.com")
    payload = _payload_path(tmp_path)

    with pytest.raises(ResearchForbiddenError) as exc_info:
        seed_ikio(payload, non_admin.id)

    assert exc_info.value.code == "seed_actor_not_admin"
    assert _count(Company) == 0


def test_unknown_actor_aborts(app, ticker_factory, tmp_path):
    ticker_factory()
    payload = _payload_path(tmp_path)

    with pytest.raises(ResearchForbiddenError):
        seed_ikio(payload, "00000000-0000-0000-0000-000000000000")

    assert _count(Company) == 0


def test_seed_version_mismatch_aborts(
    app, ticker_factory, admin_user, tmp_path
):
    ticker_factory()
    payload = _payload_path(tmp_path, seed_version="some-other-version")

    with pytest.raises(ResearchValidationError) as exc_info:
        seed_ikio(payload, admin_user.id)

    assert "seed_version" in exc_info.value.details
    assert _count(Company) == 0


def test_missing_payload_file_raises_validation_error(
    app, ticker_factory, admin_user, tmp_path
):
    ticker_factory()
    missing_path = tmp_path / "does-not-exist.json"

    with pytest.raises(ResearchValidationError):
        seed_ikio(missing_path, admin_user.id)

    assert _count(Company) == 0


def test_dry_run_validates_without_writing(
    app, ticker_factory, admin_user, tmp_path
):
    ticker_factory()
    payload = _payload_path(tmp_path)

    outcome = seed_ikio(payload, admin_user.id, dry_run=True)

    assert outcome.dry_run is True
    assert outcome.company_id is None
    assert outcome.quarterly_results_document_id is None
    assert _count(Company) == 0
    assert _count(Document) == 0


def test_real_run_creates_exactly_one_company_and_quarterly_results_document(
    app, ticker_factory, admin_user, tmp_path
):
    ticker = ticker_factory()
    unchanged_price = ticker.last_price
    payload = _payload_path(tmp_path)

    outcome = seed_ikio(payload, admin_user.id)

    assert outcome.dry_run is False
    assert outcome.company_id is not None
    company = db.session.get(Company, outcome.company_id)
    assert company is not None
    assert company.legal_name == IKIO_COMPANY_NAME
    assert company.display_name == IKIO_COMPANY_NAME
    assert company.isin == IKIO_ISIN
    assert company.ticker_id == ticker.id

    document = db.session.get(
        Document, outcome.quarterly_results_document_id
    )
    assert document is not None
    assert document.document_type == "QUARTERLY_RESULTS"
    assert document.title == IKIO_QUARTERLY_RESULTS_TITLE
    assert document.document_date.isoformat() == (
        IKIO_QUARTERLY_RESULTS_DATE.isoformat()
    )
    assert document.reporting_period == IKIO_QUARTERLY_RESULTS_PERIOD
    assert document.publisher_name == IKIO_QUARTERLY_RESULTS_PUBLISHER
    assert document.original_source_url == IKIO_QUARTERLY_RESULTS_URL
    assert document.created_by_user_id == admin_user.id

    # Exactly one company, one document -- not the full reference graph
    # ResearchSeedService.run() would build.
    assert _count(Company) == 1
    assert _count(Document) == 1

    # CMP is never seeded: the ticker seed_ikio required already existing is
    # left completely unchanged.
    db.session.refresh(ticker)
    assert ticker.last_price == unchanged_price

    # Unknown research/management/governance/ownership/forecast/valuation
    # fields remain absent, and no user other than the supplied actor exists.
    assert _count(ResearchRevision) == 0
    assert _count(ForecastRevision) == 0
    assert _count(ValuationRevision) == 0
    assert _count(MarketPlanRevision) == 0
    assert _count(OwnershipSnapshot) == 0
    assert _count(GovernanceFlag) == 0
    assert _count(CompanyDisclosure) == 0
    assert _count(UserEntitlement) == 0
    assert _count(User) == 1


def test_running_seed_twice_does_not_duplicate_quarterly_results_document(
    app, ticker_factory, admin_user, tmp_path
):
    ticker_factory()
    payload = _payload_path(tmp_path)

    first = seed_ikio(payload, admin_user.id)
    second = seed_ikio(payload, admin_user.id)

    assert first.company_id == second.company_id
    assert first.quarterly_results_document_id == (
        second.quarterly_results_document_id
    )
    matches = db.session.scalars(
        sa.select(Document).where(
            Document.title == IKIO_QUARTERLY_RESULTS_TITLE
        )
    ).all()
    assert len(matches) == 1
    assert _count(Company) == 1
