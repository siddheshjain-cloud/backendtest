"""Research Coverage & Fact Intelligence, Slice 3: model-level tests for
``FactDerivation`` and ``FactDerivationInput``.

Locks: immutability (update and delete both raise ``InvalidRequestError``)
on both models, the database-enforced "exactly one source" check
constraint on ``FactDerivationInput``, and the one-derivation-per-Fact
unique constraint.
"""

from __future__ import annotations

from datetime import date

import pytest
import sqlalchemy as sa

from app import db
from app.models import Company
from app.models.document import (
    AcquisitionMethod,
    DiscoverySourceType,
    DistributionStatus,
    DocumentType,
    IngestionStatus,
    SourceAccess,
)
from app.models.research_coverage import FactDerivation, FactDerivationInput
from app.services.document_library_service import DocumentLibraryService
from app.services.research_brain_service import ResearchBrainService


@pytest.fixture
def company(ticker_factory):
    ticker = ticker_factory(symbol="UNOMINDA", instrument_token=2)
    company = Company(
        ticker_id=ticker.id,
        legal_name="UNO Minda Limited",
        isin="INE405E01023",
    )
    db.session.add(company)
    db.session.commit()
    return company


@pytest.fixture
def document(admin_user, company):
    payload = {
        "document": {
            "document_type": DocumentType.QUARTERLY_RESULTS,
            "title": "UNOMINDA_RESULTS_Q1_FY2027_TEST",
            "document_date": "2026-08-04",
            "reporting_period": "Q1 FY2027",
            "publisher_name": "UNO Minda Limited",
            "original_source_url": "https://example.in/unominda/q1fy2027-results",
            "discovery_source_type": DiscoverySourceType.OFFICIAL_SITE,
            "source_access": SourceAccess.PUBLIC,
            "acquisition_method": AcquisitionMethod.MANUAL_REFERENCE,
            "distribution_status": DistributionStatus.LINK_ONLY,
            "ingestion_status": IngestionStatus.DISCOVERED,
        },
        "company_links": [{"company_id": company.id, "is_primary": True}],
    }
    return DocumentLibraryService.create_document(payload, admin_user.id)


@pytest.fixture
def extraction_run(admin_user, document):
    return ResearchBrainService.create_extraction_run(
        document_id=document.id,
        method="manual_pilot",
        extracted_by_user_id=admin_user.id,
    )


@pytest.fixture
def evidence_rows(admin_user, document, extraction_run):
    labels = ["PBEIT", "Finance costs", "Depreciation and amortisation"]
    return [
        ResearchBrainService.record_evidence(
            extraction_run_id=extraction_run.id,
            document_id=document.id,
            text_snippet=f"{label}: 100.00",
            created_by_user_id=admin_user.id,
        )
        for label in labels
    ]


@pytest.fixture
def seeded_evidence_and_fact(admin_user, company, evidence_rows):
    """A real promoted Fact citing the first Evidence row -- the normal,
    unchanged record_fact path -- so FactDerivation always attaches to a
    genuinely >=1-evidence Fact, never a bare placeholder."""

    fact = ResearchBrainService.record_fact(
        company_id=company.id,
        fact_type="standalone_ebitda_computed",
        value_type="NUMERIC",
        value="300.00",
        unit="INR_CRORE",
        period="Q1 FY2027",
        as_of_date=date(2026, 6, 30),
        evidence_ids=[evidence_rows[0].id],
        created_by_user_id=admin_user.id,
    )
    return fact, evidence_rows


def test_fact_derivation_foreign_keys(app):
    inspector = sa.inspect(db.engine)
    foreign_keys = {
        frozenset(constraint["constrained_columns"]): constraint["referred_table"]
        for constraint in inspector.get_foreign_keys("fact_derivation")
    }
    assert foreign_keys[frozenset({"derived_fact_id"})] == "extracted_fact"
    assert foreign_keys[frozenset({"created_by_user_id"})] == "user"

    unique_columns = {
        tuple(c["column_names"]) for c in inspector.get_unique_constraints("fact_derivation")
    }
    assert ("derived_fact_id",) in unique_columns


def test_fact_derivation_cannot_be_updated_or_deleted(
    admin_user, seeded_evidence_and_fact
):
    fact, _ = seeded_evidence_and_fact
    derivation = FactDerivation(
        derived_fact_id=fact.id,
        formula_description="EBITDA = PBEIT + Finance costs + D&A",
        created_by_user_id=admin_user.id,
    )
    db.session.add(derivation)
    db.session.commit()

    derivation.formula_description = "edited"
    with pytest.raises(sa.exc.InvalidRequestError, match="immutable"):
        db.session.commit()
    db.session.rollback()

    db.session.delete(db.session.get(FactDerivation, derivation.id))
    with pytest.raises(sa.exc.InvalidRequestError, match="immutable"):
        db.session.commit()
    db.session.rollback()


def test_at_most_one_derivation_per_fact_db_enforced(
    admin_user, seeded_evidence_and_fact
):
    fact, _ = seeded_evidence_and_fact
    first = FactDerivation(
        derived_fact_id=fact.id,
        formula_description="First formula.",
        created_by_user_id=admin_user.id,
    )
    db.session.add(first)
    db.session.commit()

    second = FactDerivation(
        derived_fact_id=fact.id,
        formula_description="A second, conflicting derivation.",
        created_by_user_id=admin_user.id,
    )
    db.session.add(second)
    with pytest.raises(sa.exc.IntegrityError):
        db.session.commit()
    db.session.rollback()


def test_fact_derivation_input_requires_exactly_one_source(
    admin_user, seeded_evidence_and_fact
):
    fact, evidence_rows = seeded_evidence_and_fact
    derivation = FactDerivation(
        derived_fact_id=fact.id,
        formula_description="EBITDA = PBEIT + Finance costs + D&A",
        created_by_user_id=admin_user.id,
    )
    db.session.add(derivation)
    db.session.flush()

    # Neither set.
    bad_neither = FactDerivationInput(
        fact_derivation_id=derivation.id,
        input_fact_id=None,
        input_evidence_id=None,
        role_label="PBEIT",
    )
    db.session.add(bad_neither)
    with pytest.raises(sa.exc.IntegrityError):
        db.session.commit()
    db.session.rollback()

    # Both set.
    db.session.add(derivation)
    db.session.flush()
    bad_both = FactDerivationInput(
        fact_derivation_id=derivation.id,
        input_fact_id=fact.id,
        input_evidence_id=evidence_rows[0].id,
        role_label="PBEIT",
    )
    db.session.add(bad_both)
    with pytest.raises(sa.exc.IntegrityError):
        db.session.commit()
    db.session.rollback()


def test_fact_derivation_input_accepts_evidence_only_source(
    admin_user, seeded_evidence_and_fact
):
    fact, evidence_rows = seeded_evidence_and_fact
    derivation = FactDerivation(
        derived_fact_id=fact.id,
        formula_description="EBITDA = PBEIT + Finance costs + D&A",
        created_by_user_id=admin_user.id,
    )
    db.session.add(derivation)
    db.session.flush()

    good = FactDerivationInput(
        fact_derivation_id=derivation.id,
        input_evidence_id=evidence_rows[1].id,
        role_label="Finance costs",
    )
    db.session.add(good)
    db.session.commit()
    assert good.input_fact_id is None


def test_fact_derivation_input_cannot_be_updated_or_deleted(
    admin_user, seeded_evidence_and_fact
):
    fact, evidence_rows = seeded_evidence_and_fact
    derivation = FactDerivation(
        derived_fact_id=fact.id,
        formula_description="EBITDA = PBEIT + Finance costs + D&A",
        created_by_user_id=admin_user.id,
    )
    db.session.add(derivation)
    db.session.flush()

    item = FactDerivationInput(
        fact_derivation_id=derivation.id,
        input_evidence_id=evidence_rows[2].id,
        role_label="Depreciation and amortisation",
    )
    db.session.add(item)
    db.session.commit()

    item.role_label = "edited"
    with pytest.raises(sa.exc.InvalidRequestError, match="immutable"):
        db.session.commit()
    db.session.rollback()

    db.session.delete(db.session.get(FactDerivationInput, item.id))
    with pytest.raises(sa.exc.InvalidRequestError, match="immutable"):
        db.session.commit()
    db.session.rollback()
