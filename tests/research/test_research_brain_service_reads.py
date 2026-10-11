"""Research Brain Pilot Task 4: ``ResearchBrainService.get_company_facts``.

Locks the Research View read contract: only the current (non-superseded)
fact per ``(fact_type, period)`` is returned, every returned fact carries
its full evidence/document chain, and -- the design proposal's amended
contradiction-acceptance success criterion -- a superseded fact is never
deleted or mutated, only excluded from the current view while remaining
directly queryable.

Entirely against the disposable test database/fixture, never the real one.
"""

from __future__ import annotations

from datetime import date

import pytest

from app import db
from app.models import Company, ExtractedFact
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
def other_company(ticker_factory):
    ticker = ticker_factory(symbol="UNOMINDA", instrument_token=2)
    company = Company(
        ticker_id=ticker.id,
        legal_name="UNO Minda Limited",
        isin="INE405E01023",
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
            "publisher_name": company.legal_name,
            "original_source_url": f"https://example.in/{title}",
            "discovery_source_type": DiscoverySourceType.OFFICIAL_SITE,
            "source_access": SourceAccess.PUBLIC,
            "acquisition_method": AcquisitionMethod.MANUAL_REFERENCE,
            "distribution_status": DistributionStatus.LINK_ONLY,
            "ingestion_status": IngestionStatus.DISCOVERED,
        },
        "company_links": [{"company_id": company.id, "is_primary": True}],
    }
    return DocumentLibraryService.create_document(payload, admin_user.id)


def _seed_fact(admin_user, company, document, *, value, as_of_date, text_snippet):
    run = ResearchBrainService.create_extraction_run(
        document_id=document.id,
        method="manual_pilot",
        extracted_by_user_id=admin_user.id,
    )
    evidence = ResearchBrainService.record_evidence(
        extraction_run_id=run.id,
        document_id=document.id,
        text_snippet=text_snippet,
        created_by_user_id=admin_user.id,
    )
    return (
        ResearchBrainService.record_fact(
            company_id=company.id,
            fact_type="quarterly_revenue",
            value_type="NUMERIC",
            value=value,
            unit="INR",
            period="Q1 FY2027",
            as_of_date=as_of_date,
            evidence_ids=[evidence.id],
            created_by_user_id=admin_user.id,
        ),
        evidence,
    )


def test_get_company_facts_returns_fact_with_evidence_and_document(
    app, admin_user, company
):
    document = _make_document(
        admin_user, company, title="IKIO_RESULTS_Q1_FY2027", document_type=DocumentType.QUARTERLY_RESULTS
    )
    fact, evidence = _seed_fact(
        admin_user,
        company,
        document,
        value="1234000000",
        as_of_date=date(2026, 8, 8),
        text_snippet="Revenue for Q1 FY2027 was INR 123.4 crore.",
    )

    results = ResearchBrainService.get_company_facts(company.id)

    assert len(results) == 1
    assert results[0].id == fact.id
    linked_evidence = [link.evidence for link in results[0].fact_evidence_links]
    assert len(linked_evidence) == 1
    assert linked_evidence[0].id == evidence.id
    assert linked_evidence[0].document.id == document.id


def test_get_company_facts_only_returns_scoped_company(
    app, admin_user, company, other_company
):
    ikio_document = _make_document(
        admin_user, company, title="IKIO_RESULTS_Q1_FY2027", document_type=DocumentType.QUARTERLY_RESULTS
    )
    unominda_document = _make_document(
        admin_user,
        other_company,
        title="UNOMINDA_RESULTS_Q1_FY2027",
        document_type=DocumentType.QUARTERLY_RESULTS,
    )
    _seed_fact(
        admin_user,
        company,
        ikio_document,
        value="1234000000",
        as_of_date=date(2026, 8, 8),
        text_snippet="IKIO Q1 revenue was INR 123.4 crore.",
    )
    _seed_fact(
        admin_user,
        other_company,
        unominda_document,
        value="40000000000",
        as_of_date=date(2026, 8, 4),
        text_snippet="UNO Minda Q1 revenue was INR 4,000 crore.",
    )

    ikio_results = ResearchBrainService.get_company_facts(company.id)
    assert {fact.company_id for fact in ikio_results} == {company.id}


def test_get_company_facts_returns_only_current_fact_after_supersession(
    app, admin_user, company
):
    """The amended contradiction-acceptance success criterion: a superseded
    fact is excluded from the current view but never deleted or mutated."""

    document = _make_document(
        admin_user, company, title="IKIO_RESULTS_Q1_FY2027", document_type=DocumentType.QUARTERLY_RESULTS
    )
    original, _ = _seed_fact(
        admin_user,
        company,
        document,
        value="1234000000",
        as_of_date=date(2026, 8, 8),
        text_snippet="Revenue for Q1 FY2027 was INR 123.4 crore.",
    )

    run = ResearchBrainService.create_extraction_run(
        document_id=document.id,
        method="manual_pilot",
        extracted_by_user_id=admin_user.id,
    )
    restatement_evidence = ResearchBrainService.record_evidence(
        extraction_run_id=run.id,
        document_id=document.id,
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

    results = ResearchBrainService.get_company_facts(company.id)

    assert [fact.id for fact in results] == [restated.id]

    # The original is excluded from the current view but still fully intact
    # and directly queryable -- proving supersession, not deletion.
    reloaded_original = db.session.get(ExtractedFact, original.id)
    assert reloaded_original is not None
    assert reloaded_original.value == "1234000000"


def test_get_company_facts_keeps_unrelated_fact_types_and_periods_independent(
    app, admin_user, company
):
    document = _make_document(
        admin_user, company, title="IKIO_RESULTS_Q1_FY2027", document_type=DocumentType.QUARTERLY_RESULTS
    )
    revenue_fact, _ = _seed_fact(
        admin_user,
        company,
        document,
        value="1234000000",
        as_of_date=date(2026, 8, 8),
        text_snippet="Revenue for Q1 FY2027 was INR 123.4 crore.",
    )

    run = ResearchBrainService.create_extraction_run(
        document_id=document.id,
        method="manual_pilot",
        extracted_by_user_id=admin_user.id,
    )
    margin_evidence = ResearchBrainService.record_evidence(
        extraction_run_id=run.id,
        document_id=document.id,
        text_snippet="EBITDA margin for Q1 FY2027 was 18.5%.",
        created_by_user_id=admin_user.id,
    )
    margin_fact = ResearchBrainService.record_fact(
        company_id=company.id,
        fact_type="ebitda_margin",
        value_type="NUMERIC",
        value="18.5",
        unit="pct",
        period="Q1 FY2027",
        as_of_date=date(2026, 8, 8),
        evidence_ids=[margin_evidence.id],
        created_by_user_id=admin_user.id,
    )

    results = ResearchBrainService.get_company_facts(company.id)
    assert {fact.id for fact in results} == {revenue_fact.id, margin_fact.id}
