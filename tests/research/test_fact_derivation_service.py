"""Research Coverage & Fact Intelligence, Slice 3:
``ResearchCoverageService.record_fact_derivation`` tests.

Covers: recording a derivation with raw-Evidence inputs (the real UNO
Minda EBITDA shape); validation that at least one input is required and
each input has exactly one source; the one-derivation-per-Fact guard; and
that a derived Fact is otherwise indistinguishable from a direct-quote
Fact except via ``get_fact_derivation``/``is_fact_derived``.
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
from app.services.document_library_service import DocumentLibraryService
from app.services.research_brain_service import ResearchBrainService
from app.services.research_coverage_service import ResearchCoverageService
from app.utils.research_errors import ResearchNotFoundError, ResearchValidationError


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
    run = ResearchBrainService.create_extraction_run(
        document_id=document.id,
        method="manual_pilot",
        extracted_by_user_id=admin_user.id,
    )
    return document, run


@pytest.fixture
def raw_line_evidence(admin_user, extraction_run):
    document, run = extraction_run
    labels = [
        ("Profit before exceptional items and tax", "242.52"),
        ("Finance costs", "18.11"),
        ("Depreciation and amortisation expense", "121.37"),
    ]
    evidence = []
    for label, value in labels:
        evidence.append(
            ResearchBrainService.record_evidence(
                extraction_run_id=run.id,
                document_id=document.id,
                text_snippet=f"{label}: Rs {value} Cr",
                created_by_user_id=admin_user.id,
            )
        )
    return evidence


@pytest.fixture
def computed_fact(admin_user, company, raw_line_evidence):
    """Simulates the real UNO Minda standalone-EBITDA case: EBITDA = PBEIT
    (242.52) + Finance costs (18.11) + D&A (121.37) = 382.00, computed, not
    directly stated anywhere in the source document."""

    return ResearchBrainService.record_fact(
        company_id=company.id,
        fact_type="standalone_ebitda_computed",
        value_type="NUMERIC",
        value="382.00",
        unit="INR_CRORE",
        period="Q1 FY2027",
        as_of_date=date(2026, 6, 30),
        evidence_ids=[raw_line_evidence[0].id],
        created_by_user_id=admin_user.id,
    )


def test_record_fact_derivation_with_evidence_only_inputs(
    admin_user, computed_fact, raw_line_evidence
):
    derivation = ResearchCoverageService.record_fact_derivation(
        derived_fact_id=computed_fact.id,
        formula_description=(
            "Standalone EBITDA = Profit before exceptional items and tax "
            "+ Finance costs + Depreciation and amortisation expense"
        ),
        created_by_user_id=admin_user.id,
        inputs=[
            {
                "input_evidence_id": raw_line_evidence[0].id,
                "role_label": "Profit before exceptional items and tax",
            },
            {
                "input_evidence_id": raw_line_evidence[1].id,
                "role_label": "Finance costs",
            },
            {
                "input_evidence_id": raw_line_evidence[2].id,
                "role_label": "Depreciation and amortisation expense",
            },
        ],
    )

    assert derivation.derived_fact_id == computed_fact.id
    assert len(derivation.inputs) == 3
    assert {i.role_label for i in derivation.inputs} == {
        "Profit before exceptional items and tax",
        "Finance costs",
        "Depreciation and amortisation expense",
    }
    assert all(i.input_fact_id is None for i in derivation.inputs)


def test_record_fact_derivation_accepts_fact_inputs_too(
    admin_user, company, computed_fact, raw_line_evidence
):
    upstream_fact = ResearchBrainService.record_fact(
        company_id=company.id,
        fact_type="finance_costs",
        value_type="NUMERIC",
        value="18.11",
        unit="INR_CRORE",
        period="Q1 FY2027",
        as_of_date=date(2026, 6, 30),
        evidence_ids=[raw_line_evidence[1].id],
        created_by_user_id=admin_user.id,
    )

    derivation = ResearchCoverageService.record_fact_derivation(
        derived_fact_id=computed_fact.id,
        formula_description="EBITDA = PBEIT + Finance costs (as its own Fact) + D&A",
        created_by_user_id=admin_user.id,
        inputs=[
            {"input_evidence_id": raw_line_evidence[0].id, "role_label": "PBEIT"},
            {"input_fact_id": upstream_fact.id, "role_label": "Finance costs"},
            {"input_evidence_id": raw_line_evidence[2].id, "role_label": "D&A"},
        ],
    )
    fact_inputs = [i for i in derivation.inputs if i.input_fact_id is not None]
    assert len(fact_inputs) == 1
    assert fact_inputs[0].input_fact_id == upstream_fact.id


def test_derivation_reports_not_found_for_a_bad_fact_id(admin_user):
    """A code-review finding: a nonexistent derived_fact_id used to fall
    through to a later FK-violation IntegrityError, caught and
    misreported as 'this Fact already has a derivation recorded' --
    masking a plain bad id as a duplicate-derivation conflict. Must now
    raise the same ResearchNotFoundError this codebase already uses
    elsewhere for a genuinely missing resource."""

    with pytest.raises(ResearchNotFoundError):
        ResearchCoverageService.record_fact_derivation(
            derived_fact_id="00000000-0000-0000-0000-000000000000",
            formula_description="irrelevant",
            created_by_user_id=admin_user.id,
            inputs=[{"input_evidence_id": "00000000-0000-0000-0000-000000000001"}],
        )


def test_derivation_requires_at_least_one_input(admin_user, computed_fact):
    with pytest.raises(ResearchValidationError):
        ResearchCoverageService.record_fact_derivation(
            derived_fact_id=computed_fact.id,
            formula_description="No inputs at all.",
            created_by_user_id=admin_user.id,
            inputs=[],
        )


def test_derivation_input_rejects_both_or_neither_source(
    admin_user, computed_fact, raw_line_evidence
):
    with pytest.raises(ResearchValidationError):
        ResearchCoverageService.record_fact_derivation(
            derived_fact_id=computed_fact.id,
            formula_description="Bad: neither source set.",
            created_by_user_id=admin_user.id,
            inputs=[{"role_label": "PBEIT"}],
        )

    with pytest.raises(ResearchValidationError):
        ResearchCoverageService.record_fact_derivation(
            derived_fact_id=computed_fact.id,
            formula_description="Bad: both sources set.",
            created_by_user_id=admin_user.id,
            inputs=[
                {
                    "input_fact_id": computed_fact.id,
                    "input_evidence_id": raw_line_evidence[0].id,
                    "role_label": "PBEIT",
                }
            ],
        )


def test_a_fact_can_only_be_derived_once(
    admin_user, computed_fact, raw_line_evidence
):
    ResearchCoverageService.record_fact_derivation(
        derived_fact_id=computed_fact.id,
        formula_description="First derivation.",
        created_by_user_id=admin_user.id,
        inputs=[{"input_evidence_id": raw_line_evidence[0].id}],
    )

    with pytest.raises(ResearchValidationError):
        ResearchCoverageService.record_fact_derivation(
            derived_fact_id=computed_fact.id,
            formula_description="A second, conflicting derivation.",
            created_by_user_id=admin_user.id,
            inputs=[{"input_evidence_id": raw_line_evidence[1].id}],
        )


def test_get_fact_derivation_and_is_fact_derived(
    admin_user, company, computed_fact, raw_line_evidence
):
    # A directly-quoted Fact (no derivation recorded) reads as not derived.
    quoted_fact = ResearchBrainService.record_fact(
        company_id=company.id,
        fact_type="finance_costs",
        value_type="NUMERIC",
        value="18.11",
        unit="INR_CRORE",
        period="Q1 FY2027",
        as_of_date=date(2026, 6, 30),
        evidence_ids=[raw_line_evidence[1].id],
        created_by_user_id=admin_user.id,
    )
    assert ResearchCoverageService.get_fact_derivation(quoted_fact.id) is None
    assert ResearchCoverageService.is_fact_derived(quoted_fact.id) is False

    ResearchCoverageService.record_fact_derivation(
        derived_fact_id=computed_fact.id,
        formula_description="EBITDA = PBEIT + Finance costs + D&A",
        created_by_user_id=admin_user.id,
        inputs=[{"input_evidence_id": raw_line_evidence[0].id}],
    )
    assert ResearchCoverageService.is_fact_derived(computed_fact.id) is True
    derivation = ResearchCoverageService.get_fact_derivation(computed_fact.id)
    assert derivation.derived_fact_id == computed_fact.id

    # The derived Fact is still a completely normal, immutable Fact,
    # readable through the unchanged Research View exactly like any other.
    facts = ResearchBrainService.get_company_facts(company.id)
    assert computed_fact.id in {f.id for f in facts}
