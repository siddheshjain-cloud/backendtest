"""Research Brain Pilot Task 3: ``ResearchBrainService`` write path.

Locks the write-path contract: one atomic commit per call, a fact may cite
multiple evidence rows from independent documents (the cross-document-
corroboration shape the pilot exists to prove), a fact may supersede a prior
fact without mutating it, and a fact with zero evidence is rejected outright
-- every fact must cite at least one source, no exceptions.

All fixtures here use the disposable test database only.
"""

from __future__ import annotations

from datetime import date

import pytest
import sqlalchemy as sa

from app import db
from app.models import Company, Evidence, ExtractedFact, ExtractionRun, FactEvidence
from app.models.document import (
    AcquisitionMethod,
    DiscoverySourceType,
    DistributionStatus,
    DocumentType,
    IngestionStatus,
    SourceAccess,
)
from app.services.document_library_service import DocumentLibraryService
from app.services.research_brain_service import ResearchBrainService
from app.utils.research_errors import ResearchValidationError


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


def _make_document(admin_user, company, *, title: str, document_type: str):
    payload = {
        "document": {
            "document_type": document_type,
            "title": title,
            "document_date": "2026-08-08",
            "reporting_period": "Q1 FY2027",
            "publisher_name": "IKIO Technologies Limited",
            "original_source_url": f"https://example.in/ikio/{title}",
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
def results_document(admin_user, company):
    return _make_document(
        admin_user,
        company,
        title="IKIO_RESULTS_Q1_FY2027",
        document_type=DocumentType.QUARTERLY_RESULTS,
    )


@pytest.fixture
def concall_document(admin_user, company):
    return _make_document(
        admin_user,
        company,
        title="IKIO_CONCALL_14_08_2026",
        document_type=DocumentType.CONCALL,
    )


def test_create_extraction_run_persists_one_atomic_row(
    app, admin_user, results_document
):
    run = ResearchBrainService.create_extraction_run(
        document_id=results_document.id,
        method="manual_pilot",
        extracted_by_user_id=admin_user.id,
    )

    assert db.session.get(ExtractionRun, run.id) is not None
    assert run.document_id == results_document.id
    assert run.method == "manual_pilot"


def test_record_evidence_persists_one_atomic_row(app, admin_user, results_document):
    run = ResearchBrainService.create_extraction_run(
        document_id=results_document.id,
        method="manual_pilot",
        extracted_by_user_id=admin_user.id,
    )

    evidence = ResearchBrainService.record_evidence(
        extraction_run_id=run.id,
        document_id=results_document.id,
        text_snippet="Revenue for Q1 FY2027 was INR 123.4 crore.",
        created_by_user_id=admin_user.id,
        locator="page 4",
    )

    persisted = db.session.get(Evidence, evidence.id)
    assert persisted.text_snippet == "Revenue for Q1 FY2027 was INR 123.4 crore."
    assert persisted.locator == "page 4"


def test_record_fact_links_multiple_evidence_rows_from_independent_documents(
    app, admin_user, company, results_document, concall_document
):
    results_run = ResearchBrainService.create_extraction_run(
        document_id=results_document.id,
        method="manual_pilot",
        extracted_by_user_id=admin_user.id,
    )
    concall_run = ResearchBrainService.create_extraction_run(
        document_id=concall_document.id,
        method="manual_pilot",
        extracted_by_user_id=admin_user.id,
    )

    results_evidence = ResearchBrainService.record_evidence(
        extraction_run_id=results_run.id,
        document_id=results_document.id,
        text_snippet="Revenue for Q1 FY2027 was INR 123.4 crore.",
        created_by_user_id=admin_user.id,
    )
    concall_evidence = ResearchBrainService.record_evidence(
        extraction_run_id=concall_run.id,
        document_id=concall_document.id,
        text_snippet="On the call, management confirmed Q1 revenue of INR 123.4 crore.",
        created_by_user_id=admin_user.id,
    )

    fact = ResearchBrainService.record_fact(
        company_id=company.id,
        fact_type="quarterly_revenue",
        value_type="NUMERIC",
        value="1234000000",
        unit="INR",
        period="Q1 FY2027",
        as_of_date=date(2026, 8, 8),
        evidence_ids=[results_evidence.id, concall_evidence.id],
        created_by_user_id=admin_user.id,
    )

    linked_evidence_ids = {
        row.evidence_id
        for row in db.session.scalars(
            sa.select(FactEvidence).where(FactEvidence.fact_id == fact.id)
        )
    }
    assert linked_evidence_ids == {results_evidence.id, concall_evidence.id}


def test_record_fact_rejects_zero_evidence(app, admin_user, company):
    with pytest.raises(ResearchValidationError) as exc_info:
        ResearchBrainService.record_fact(
            company_id=company.id,
            fact_type="quarterly_revenue",
            value_type="NUMERIC",
            value="1234000000",
            unit="INR",
            period="Q1 FY2027",
            as_of_date=date(2026, 8, 8),
            evidence_ids=[],
            created_by_user_id=admin_user.id,
        )

    assert exc_info.value.code == "validation_error"
    assert "evidence_ids" in exc_info.value.details
    assert not db.session().in_transaction()
    assert (
        db.session.scalar(sa.select(sa.func.count()).select_from(ExtractedFact)) == 0
    )


def test_record_fact_with_supersedes_does_not_mutate_the_original(
    app, admin_user, company, results_document
):
    run = ResearchBrainService.create_extraction_run(
        document_id=results_document.id,
        method="manual_pilot",
        extracted_by_user_id=admin_user.id,
    )
    evidence = ResearchBrainService.record_evidence(
        extraction_run_id=run.id,
        document_id=results_document.id,
        text_snippet="Revenue for Q1 FY2027 was INR 123.4 crore.",
        created_by_user_id=admin_user.id,
    )

    original = ResearchBrainService.record_fact(
        company_id=company.id,
        fact_type="quarterly_revenue",
        value_type="NUMERIC",
        value="1234000000",
        unit="INR",
        period="Q1 FY2027",
        as_of_date=date(2026, 8, 8),
        evidence_ids=[evidence.id],
        created_by_user_id=admin_user.id,
    )

    restatement_evidence = ResearchBrainService.record_evidence(
        extraction_run_id=run.id,
        document_id=results_document.id,
        text_snippet="Restated: Q1 FY2027 revenue was INR 125.0 crore.",
        created_by_user_id=admin_user.id,
    )
    restated = ResearchBrainService.record_fact(
        company_id=company.id,
        fact_type="quarterly_revenue",
        value_type="NUMERIC",
        value="1250000000",
        unit="INR",
        period="Q1 FY2027",
        as_of_date=date(2026, 9, 1),
        evidence_ids=[restatement_evidence.id],
        supersedes_fact_id=original.id,
        created_by_user_id=admin_user.id,
    )

    reloaded_original = db.session.get(ExtractedFact, original.id)
    assert reloaded_original.value == "1234000000"
    assert restated.supersedes_fact_id == original.id
    assert (
        db.session.scalar(sa.select(sa.func.count()).select_from(ExtractedFact)) == 2
    )
