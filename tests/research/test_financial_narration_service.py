"""Phase 3 Slice B: FinancialNarrationService acceptance tests.

Covers acceptance tests 10-11 of the revised Slice B plan: every rendered
sentence carries a resolvable citation, and no causal/explanatory clause
ever appears without one.
"""

from __future__ import annotations

from datetime import date

import pytest

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
from app.models.research_brain import ExtractedFact
from app.models.research_types import AdjustmentDirection, FinancialMetricNamespace
from app.models.financial_metric_definition import FinancialMetricDefinition
from app.services.document_library_service import DocumentLibraryService
from app.services.financial_diagnostics_service import RAW_EBITDA, RAW_REVENUE, FinancialDiagnosticsService
from app.services.financial_narration_service import FinancialNarrationService
from app.services.research_brain_service import ResearchBrainService


@pytest.fixture
def company(ticker_factory):
    ticker = ticker_factory(symbol="NARRATION_TEST", instrument_token=4)
    company = Company(
        ticker_id=ticker.id, legal_name="Narration Test Co Limited", isin="INE0LOJ02001",
    )
    db.session.add(company)
    db.session.commit()
    return company


@pytest.fixture
def results_document(admin_user, company):
    payload = {
        "document": {
            "document_type": DocumentType.QUARTERLY_RESULTS,
            "title": "NARRATION_TEST_RESULTS",
            "document_date": "2026-08-04",
            "publisher_name": "Narration Test Co Limited",
            "original_source_url": "https://example.in/narration-test/results",
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
def concall_document(admin_user, company):
    payload = {
        "document": {
            "document_type": DocumentType.CONCALL,
            "title": "NARRATION_TEST_CONCALL",
            "document_date": "2026-08-04",
            "publisher_name": "Narration Test Co Limited",
            "original_source_url": "https://example.in/narration-test/concall",
            "discovery_source_type": DiscoverySourceType.OFFICIAL_SITE,
            "source_access": SourceAccess.PUBLIC,
            "acquisition_method": AcquisitionMethod.MANUAL_REFERENCE,
            "distribution_status": DistributionStatus.LINK_ONLY,
            "ingestion_status": IngestionStatus.DISCOVERED,
        },
        "company_links": [{"company_id": company.id, "is_primary": True}],
    }
    return DocumentLibraryService.create_document(payload, admin_user.id)


def _raw_fact(*, admin_user, document, company, fact_type, value, period, as_of_date):
    run = ResearchBrainService.create_extraction_run(
        document_id=document.id, method="manual_pilot", extracted_by_user_id=admin_user.id
    )
    evidence = ResearchBrainService.record_evidence(
        extraction_run_id=run.id, document_id=document.id,
        text_snippet=f"{fact_type}: {value} ({period})", created_by_user_id=admin_user.id,
    )
    return ResearchBrainService.record_fact(
        company_id=company.id, fact_type=f"fin.raw.{fact_type}", value_type="NUMERIC",
        value=value, unit="INR_CRORE", period=period, as_of_date=as_of_date,
        evidence_ids=[evidence.id], created_by_user_id=admin_user.id,
    )


def test_every_sentence_carries_a_resolvable_citation(admin_user, company, results_document):
    FinancialDiagnosticsService.ensure_normalized_metric_definitions()
    _raw_fact(
        admin_user=admin_user, document=results_document, company=company,
        fact_type=RAW_REVENUE, value="100.00", period="FY2025Q2", as_of_date=date(2024, 9, 30),
    )
    _raw_fact(
        admin_user=admin_user, document=results_document, company=company,
        fact_type=RAW_EBITDA, value="20.00", period="FY2025Q2", as_of_date=date(2024, 9, 30),
    )
    FinancialDiagnosticsService.compute_ebitda_margin(
        company_id=company.id, period="FY2025Q2", as_of_date=date(2024, 9, 30),
        created_by_user_id=admin_user.id,
    )

    sentences = FinancialNarrationService.render_company_period_narrative(
        company_id=company.id, period="FY2025Q2"
    )
    assert sentences, "expected at least one rendered sentence"
    for sentence in sentences:
        assert sentence.fact_ids or sentence.evidence_ids, (
            f"sentence has no citation at all: {sentence.text!r}"
        )
        for fact_id in sentence.fact_ids:
            assert db.session.get(ExtractedFact, fact_id) is not None


def test_no_causal_clause_without_an_adjustment_citation(admin_user, company, results_document):
    """A margin with no ADJUSTED counterpart must render descriptively
    only -- no explanatory clause, uncited or otherwise."""

    FinancialDiagnosticsService.ensure_normalized_metric_definitions()
    _raw_fact(
        admin_user=admin_user, document=results_document, company=company,
        fact_type=RAW_REVENUE, value="100.00", period="FY2025Q2", as_of_date=date(2024, 9, 30),
    )
    _raw_fact(
        admin_user=admin_user, document=results_document, company=company,
        fact_type=RAW_EBITDA, value="20.00", period="FY2025Q2", as_of_date=date(2024, 9, 30),
    )
    FinancialDiagnosticsService.compute_ebitda_margin(
        company_id=company.id, period="FY2025Q2", as_of_date=date(2024, 9, 30),
        created_by_user_id=admin_user.id,
    )

    sentences = FinancialNarrationService.render_company_period_narrative(
        company_id=company.id, period="FY2025Q2"
    )
    causal_markers = ("because", "due to", "driven by", "owing to", "as a result of")
    for sentence in sentences:
        assert not any(marker in sentence.text.lower() for marker in causal_markers), (
            f"uncited causal language in: {sentence.text!r}"
        )


def test_adjustment_commentary_only_renders_with_its_own_citation(
    admin_user, company, results_document, concall_document
):
    FinancialDiagnosticsService.ensure_normalized_metric_definitions()
    ebitda_fact = _raw_fact(
        admin_user=admin_user, document=results_document, company=company,
        fact_type=RAW_EBITDA, value="110.00", period="FY2025", as_of_date=date(2025, 3, 31),
    )
    definition = FinancialMetricDefinition(
        slug="ebitda_excl_one_offs", label="EBITDA excl. one-off items",
        statement_section="INCOME_STATEMENT", namespace=FinancialMetricNamespace.ADJUSTED,
        standard_unit="INR_CRORE",
    )
    db.session.add(definition)
    db.session.commit()

    run = ResearchBrainService.create_extraction_run(
        document_id=concall_document.id, method="manual_pilot", extracted_by_user_id=admin_user.id
    )
    justification = ResearchBrainService.record_evidence(
        extraction_run_id=run.id, document_id=concall_document.id,
        text_snippet="One-off redundancy cost of INR 6.00 Cr this year.",
        created_by_user_id=admin_user.id,
    )

    FinancialDiagnosticsService.record_adjusted_fact(
        company_id=company.id, metric_slug="ebitda_excl_one_offs",
        base_fact_id=ebitda_fact.id,
        adjustment_items=[
            {
                "amount": "6.00", "direction": AdjustmentDirection.ADD_BACK,
                "description": "one-off redundancy cost",
                "justification_evidence_id": justification.id,
            }
        ],
        period="FY2025", as_of_date=date(2025, 3, 31), created_by_user_id=admin_user.id,
    )

    sentences = FinancialNarrationService.render_company_period_narrative(
        company_id=company.id, period="FY2025"
    )
    adjustment_sentences = [s for s in sentences if "excluding" in s.text]
    assert len(adjustment_sentences) == 1
    rendered = adjustment_sentences[0]
    assert "one-off redundancy cost" in rendered.text
    assert justification.id in rendered.evidence_ids
    assert ebitda_fact.id in rendered.fact_ids
