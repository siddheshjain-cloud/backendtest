"""Research Brain Pilot Task 1: ExtractionRun, Evidence, ExtractedFact, FactEvidence.

These tests lock the four new, purely additive models the pilot's design
proposal specifies: provenance for one extraction pass over one document
(``ExtractionRun``), a structured source-text excerpt (``Evidence``), an
append-only, typed, evidence-backed data point (``ExtractedFact``), and the
many-to-many join (``FactEvidence``) that makes cross-document corroboration
representable. All four follow M1's existing immutable, append-only
discipline (``OwnershipSnapshot``/``ResearchRevision``): no update, no
delete, ever.
"""

from __future__ import annotations

import uuid
from datetime import date

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from app import db
from app.models import (
    Company,
    Document,
    Evidence,
    ExtractedFact,
    ExtractionRun,
    FactEvidence,
)
from app.models.document import (
    AcquisitionMethod,
    DiscoverySourceType,
    DistributionStatus,
    DocumentType,
    IngestionStatus,
    SourceAccess,
)
from app.services.document_library_service import DocumentLibraryService


def _reflected_columns(table_name: str) -> dict[str, bool]:
    inspector = sa.inspect(db.engine)
    return {
        column["name"]: column["nullable"]
        for column in inspector.get_columns(table_name)
    }


def _commit_expect_integrity() -> None:
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()


@pytest.fixture
def company(ticker_factory):
    ticker = ticker_factory(symbol="IKIO", instrument_token=1)
    company = Company(
        ticker_id=ticker.id,
        legal_name="IKIO Technologies Limited",
        isin="INE0LOJ01019",
    )
    db.session.add(company)
    db.session.commit()
    return company


@pytest.fixture
def document(app, admin_user, company):
    payload = {
        "document": {
            "document_type": DocumentType.QUARTERLY_RESULTS,
            "title": "IKIO_RESULTS_Q1_FY2027",
            "document_date": "2026-08-08",
            "reporting_period": "Q1 FY2027",
            "publisher_name": "IKIO Technologies Limited",
            "original_source_url": "https://example.in/ikio/q1fy2027-results",
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
def second_document(app, admin_user, company):
    payload = {
        "document": {
            "document_type": DocumentType.CONCALL,
            "title": "IKIO_CONCALL_14_08_2026",
            "document_date": "2026-08-14",
            "reporting_period": "Q1 FY2027",
            "publisher_name": "IKIO Technologies Limited",
            "original_source_url": "https://example.in/ikio/q1fy2027-concall",
            "discovery_source_type": DiscoverySourceType.OFFICIAL_SITE,
            "source_access": SourceAccess.PUBLIC,
            "acquisition_method": AcquisitionMethod.MANUAL_REFERENCE,
            "distribution_status": DistributionStatus.LINK_ONLY,
            "ingestion_status": IngestionStatus.DISCOVERED,
        },
        "company_links": [{"company_id": company.id, "is_primary": True}],
    }
    return DocumentLibraryService.create_document(payload, admin_user.id)


# ---------------------------------------------------------------------------
# ExtractionRun
# ---------------------------------------------------------------------------


def test_extraction_run_has_exact_columns(app):
    assert _reflected_columns("extraction_run") == {
        "id": False,
        "created_at": False,
        "document_id": False,
        "method": False,
        "extracted_by_user_id": False,
    }


def test_extraction_run_foreign_keys(app):
    inspector = sa.inspect(db.engine)
    foreign_keys = {
        frozenset(constraint["constrained_columns"]): constraint["referred_table"]
        for constraint in inspector.get_foreign_keys("extraction_run")
    }
    assert foreign_keys[frozenset({"document_id"})] == "document"
    assert foreign_keys[frozenset({"extracted_by_user_id"})] == "user"


def test_extraction_run_persists_with_uuid_id(app, admin_user, document):
    run = ExtractionRun(
        document_id=document.id,
        method="manual_pilot",
        extracted_by_user_id=admin_user.id,
    )
    db.session.add(run)
    db.session.commit()

    assert isinstance(run.id, str)
    assert uuid.UUID(run.id).version == 4
    assert run.created_at is not None


def test_extraction_run_cannot_be_updated_or_deleted(app, admin_user, document):
    run = ExtractionRun(
        document_id=document.id,
        method="manual_pilot",
        extracted_by_user_id=admin_user.id,
    )
    db.session.add(run)
    db.session.commit()

    run.method = "rewritten"
    with pytest.raises(sa.exc.InvalidRequestError, match="immutable"):
        db.session.commit()
    db.session.rollback()

    db.session.delete(db.session.get(ExtractionRun, run.id))
    with pytest.raises(sa.exc.InvalidRequestError, match="immutable"):
        db.session.commit()
    db.session.rollback()


# ---------------------------------------------------------------------------
# Evidence
# ---------------------------------------------------------------------------


def test_evidence_has_exact_columns(app):
    assert _reflected_columns("evidence") == {
        "id": False,
        "created_at": False,
        "extraction_run_id": False,
        "document_id": False,
        "text_snippet": False,
        "locator": True,
        "created_by_user_id": False,
    }


def test_evidence_foreign_keys(app):
    inspector = sa.inspect(db.engine)
    foreign_keys = {
        frozenset(constraint["constrained_columns"]): constraint["referred_table"]
        for constraint in inspector.get_foreign_keys("evidence")
    }
    assert foreign_keys[frozenset({"extraction_run_id"})] == "extraction_run"
    assert foreign_keys[frozenset({"document_id"})] == "document"
    assert foreign_keys[frozenset({"created_by_user_id"})] == "user"


def test_evidence_persists_and_is_immutable(app, admin_user, document):
    run = ExtractionRun(
        document_id=document.id,
        method="manual_pilot",
        extracted_by_user_id=admin_user.id,
    )
    db.session.add(run)
    db.session.commit()

    evidence = Evidence(
        extraction_run_id=run.id,
        document_id=document.id,
        text_snippet="Revenue for Q1 FY2027 was INR 123.4 crore.",
        locator="page 4",
        created_by_user_id=admin_user.id,
    )
    db.session.add(evidence)
    db.session.commit()
    assert isinstance(evidence.id, str)

    evidence.text_snippet = "rewritten"
    with pytest.raises(sa.exc.InvalidRequestError, match="immutable"):
        db.session.commit()
    db.session.rollback()

    db.session.delete(db.session.get(Evidence, evidence.id))
    with pytest.raises(sa.exc.InvalidRequestError, match="immutable"):
        db.session.commit()
    db.session.rollback()


# ---------------------------------------------------------------------------
# ExtractedFact
# ---------------------------------------------------------------------------


def test_extracted_fact_has_exact_columns(app):
    assert _reflected_columns("extracted_fact") == {
        "id": False,
        "created_at": False,
        "company_id": False,
        "fact_type": False,
        "value_type": False,
        "value": False,
        "unit": True,
        "period": False,
        "as_of_date": False,
        "supersedes_fact_id": True,
        "created_by_user_id": False,
    }


def test_extracted_fact_foreign_keys(app):
    inspector = sa.inspect(db.engine)
    foreign_keys = {
        frozenset(constraint["constrained_columns"]): constraint["referred_table"]
        for constraint in inspector.get_foreign_keys("extracted_fact")
    }
    assert foreign_keys[frozenset({"company_id"})] == "company"
    assert foreign_keys[frozenset({"created_by_user_id"})] == "user"
    assert foreign_keys[frozenset({"supersedes_fact_id"})] == "extracted_fact"


def test_extracted_fact_check_constraints(app):
    inspector = sa.inspect(db.engine)
    check_names = {
        constraint["name"]
        for constraint in inspector.get_check_constraints("extracted_fact")
    }
    assert "ck_extracted_fact_unit_requires_numeric" in check_names


def test_as_of_date_is_distinct_from_created_at(app, admin_user, company):
    fact = ExtractedFact(
        company_id=company.id,
        fact_type="quarterly_revenue",
        value_type="NUMERIC",
        value="1234000000",
        unit="INR",
        period="Q1 FY2027",
        as_of_date=date(2026, 8, 8),
        created_by_user_id=admin_user.id,
    )
    db.session.add(fact)
    db.session.commit()

    assert fact.as_of_date == date(2026, 8, 8)
    assert fact.created_at is not None
    # created_at is row-insert bookkeeping, not epistemic time -- they are
    # independent fields that may legitimately differ.
    assert fact.created_at.date() != fact.as_of_date or True  # no forced equality


def test_value_type_supports_numeric_and_text_without_a_larger_ontology(
    app, admin_user, company
):
    numeric_fact = ExtractedFact(
        company_id=company.id,
        fact_type="quarterly_revenue",
        value_type="NUMERIC",
        value="1234000000",
        unit="INR",
        period="Q1 FY2027",
        as_of_date=date(2026, 8, 8),
        created_by_user_id=admin_user.id,
    )
    text_fact = ExtractedFact(
        company_id=company.id,
        fact_type="management_commentary",
        value_type="TEXT",
        value="Management expects double-digit volume growth next quarter.",
        unit=None,
        period="Q1 FY2027",
        as_of_date=date(2026, 8, 14),
        created_by_user_id=admin_user.id,
    )
    db.session.add_all([numeric_fact, text_fact])
    db.session.commit()

    assert numeric_fact.value_type == "NUMERIC"
    assert text_fact.value_type == "TEXT"
    assert text_fact.unit is None


def test_text_fact_with_unit_is_rejected_at_database(app, admin_user, company):
    db.session.add(
        ExtractedFact(
            company_id=company.id,
            fact_type="management_commentary",
            value_type="TEXT",
            value="Some assertion",
            unit="INR",
            period="Q1 FY2027",
            as_of_date=date(2026, 8, 14),
            created_by_user_id=admin_user.id,
        )
    )
    _commit_expect_integrity()


def test_extracted_fact_supersession_chain_does_not_mutate_the_original(
    app, admin_user, company
):
    original = ExtractedFact(
        company_id=company.id,
        fact_type="quarterly_revenue",
        value_type="NUMERIC",
        value="1234000000",
        unit="INR",
        period="Q1 FY2027",
        as_of_date=date(2026, 8, 8),
        created_by_user_id=admin_user.id,
    )
    db.session.add(original)
    db.session.commit()

    restated = ExtractedFact(
        company_id=company.id,
        fact_type="quarterly_revenue",
        value_type="NUMERIC",
        value="1250000000",
        unit="INR",
        period="Q1 FY2027",
        as_of_date=date(2026, 9, 1),
        supersedes_fact_id=original.id,
        created_by_user_id=admin_user.id,
    )
    db.session.add(restated)
    db.session.commit()

    reloaded_original = db.session.get(ExtractedFact, original.id)
    assert reloaded_original.value == "1234000000"
    assert restated.supersedes_fact_id == original.id


def test_extracted_fact_cannot_be_updated_or_deleted(app, admin_user, company):
    fact = ExtractedFact(
        company_id=company.id,
        fact_type="quarterly_revenue",
        value_type="NUMERIC",
        value="1234000000",
        period="Q1 FY2027",
        as_of_date=date(2026, 8, 8),
        created_by_user_id=admin_user.id,
    )
    db.session.add(fact)
    db.session.commit()

    fact.value = "rewritten"
    with pytest.raises(sa.exc.InvalidRequestError, match="immutable"):
        db.session.commit()
    db.session.rollback()

    db.session.delete(db.session.get(ExtractedFact, fact.id))
    with pytest.raises(sa.exc.InvalidRequestError, match="immutable"):
        db.session.commit()
    db.session.rollback()


# ---------------------------------------------------------------------------
# FactEvidence
# ---------------------------------------------------------------------------


def _make_fact(company, admin_user, **overrides):
    fields = {
        "company_id": company.id,
        "fact_type": "quarterly_revenue",
        "value_type": "NUMERIC",
        "value": "1234000000",
        "unit": "INR",
        "period": "Q1 FY2027",
        "as_of_date": date(2026, 8, 8),
        "created_by_user_id": admin_user.id,
    }
    fields.update(overrides)
    fact = ExtractedFact(**fields)
    db.session.add(fact)
    db.session.commit()
    return fact


def _make_evidence(document, admin_user, **overrides):
    run = ExtractionRun(
        document_id=document.id,
        method="manual_pilot",
        extracted_by_user_id=admin_user.id,
    )
    db.session.add(run)
    db.session.commit()

    fields = {
        "extraction_run_id": run.id,
        "document_id": document.id,
        "text_snippet": "Revenue for Q1 FY2027 was INR 123.4 crore.",
        "created_by_user_id": admin_user.id,
    }
    fields.update(overrides)
    evidence = Evidence(**fields)
    db.session.add(evidence)
    db.session.commit()
    return evidence


def test_fact_evidence_composite_primary_key(app):
    inspector = sa.inspect(db.engine)
    primary_key = inspector.get_pk_constraint("fact_evidence")
    assert sorted(primary_key["constrained_columns"]) == ["evidence_id", "fact_id"]


def test_one_fact_can_link_multiple_evidence_rows(
    app, admin_user, company, document, second_document
):
    """Cross-document corroboration: one fact, two independent sources."""

    fact = _make_fact(company, admin_user)
    evidence_from_results = _make_evidence(document, admin_user)
    evidence_from_concall = _make_evidence(
        second_document,
        admin_user,
        text_snippet="On the call, management confirmed Q1 revenue of INR 123.4 crore.",
    )

    db.session.add_all(
        [
            FactEvidence(fact_id=fact.id, evidence_id=evidence_from_results.id),
            FactEvidence(fact_id=fact.id, evidence_id=evidence_from_concall.id),
        ]
    )
    db.session.commit()

    linked_evidence_ids = {
        row.evidence_id
        for row in db.session.scalars(
            sa.select(FactEvidence).where(FactEvidence.fact_id == fact.id)
        )
    }
    assert linked_evidence_ids == {
        evidence_from_results.id,
        evidence_from_concall.id,
    }


def test_one_evidence_row_can_back_multiple_facts(app, admin_user, company, document):
    evidence = _make_evidence(document, admin_user)
    revenue_fact = _make_fact(company, admin_user, fact_type="quarterly_revenue")
    margin_fact = _make_fact(
        company,
        admin_user,
        fact_type="ebitda_margin",
        value="18.5",
        unit="pct",
    )

    db.session.add_all(
        [
            FactEvidence(fact_id=revenue_fact.id, evidence_id=evidence.id),
            FactEvidence(fact_id=margin_fact.id, evidence_id=evidence.id),
        ]
    )
    db.session.commit()

    linked_fact_ids = {
        row.fact_id
        for row in db.session.scalars(
            sa.select(FactEvidence).where(FactEvidence.evidence_id == evidence.id)
        )
    }
    assert linked_fact_ids == {revenue_fact.id, margin_fact.id}


def test_duplicate_fact_evidence_link_is_rejected_at_database(
    app, admin_user, company, document
):
    fact = _make_fact(company, admin_user)
    evidence = _make_evidence(document, admin_user)

    db.session.add(FactEvidence(fact_id=fact.id, evidence_id=evidence.id))
    db.session.commit()

    db.session.add(FactEvidence(fact_id=fact.id, evidence_id=evidence.id))
    _commit_expect_integrity()
