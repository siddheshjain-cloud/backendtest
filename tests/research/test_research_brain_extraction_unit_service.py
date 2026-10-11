"""Research Brain Pilot Task 6: ``ResearchBrainService``'s ExtractionUnit
write/read methods, and ``record_evidence``'s new optional parameter.

Locks: one atomic commit per call (same shape as every other write
method); ``get_extraction_units`` as the "already processed?" signal;
and -- the explicit clarification this task was approved with -- a second,
independent extraction run may freely reprocess the same document, never
blocked by the existence of prior units.
"""

from __future__ import annotations

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from app import db
from app.models import Company, Evidence, ExtractionUnit
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
def document(admin_user, company):
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


def test_record_extraction_unit_persists_one_atomic_row(
    app, admin_user, document
):
    run = ResearchBrainService.create_extraction_run(
        document_id=document.id,
        method="manual_pilot",
        extracted_by_user_id=admin_user.id,
    )

    unit = ResearchBrainService.record_extraction_unit(
        extraction_run_id=run.id,
        document_id=document.id,
        unit_type="PAGE",
        sequence_number=1,
        content_text="Full extracted page 1 text.",
        created_by_user_id=admin_user.id,
        locator="page 1",
    )

    persisted = db.session.get(ExtractionUnit, unit.id)
    assert persisted.content_text == "Full extracted page 1 text."
    assert persisted.unit_type == "PAGE"


def test_get_extraction_units_signals_not_yet_processed(app, admin_user, document):
    assert ResearchBrainService.get_extraction_units(document.id) == []


def test_get_extraction_units_returns_all_persisted_units(app, admin_user, document):
    run = ResearchBrainService.create_extraction_run(
        document_id=document.id,
        method="manual_pilot",
        extracted_by_user_id=admin_user.id,
    )
    ResearchBrainService.record_extraction_unit(
        extraction_run_id=run.id,
        document_id=document.id,
        unit_type="PAGE",
        sequence_number=1,
        content_text="page 1 text",
        created_by_user_id=admin_user.id,
    )
    ResearchBrainService.record_extraction_unit(
        extraction_run_id=run.id,
        document_id=document.id,
        unit_type="TABLE",
        sequence_number=1,
        content_text="table 1 text",
        created_by_user_id=admin_user.id,
    )

    units = ResearchBrainService.get_extraction_units(document.id)
    assert len(units) == 2
    assert {unit.unit_type for unit in units} == {"PAGE", "TABLE"}


def test_reprocessing_via_a_new_run_is_never_blocked(app, admin_user, document):
    """The explicit clarification this task was approved with: existing
    units mean 'already processed', never 'permanently prohibited from
    reprocessing'."""

    first_run = ResearchBrainService.create_extraction_run(
        document_id=document.id,
        method="manual_pilot",
        extracted_by_user_id=admin_user.id,
    )
    ResearchBrainService.record_extraction_unit(
        extraction_run_id=first_run.id,
        document_id=document.id,
        unit_type="PAGE",
        sequence_number=1,
        content_text="first pass, page 1",
        created_by_user_id=admin_user.id,
    )

    assert len(ResearchBrainService.get_extraction_units(document.id)) == 1

    second_run = ResearchBrainService.create_extraction_run(
        document_id=document.id,
        method="manual_pilot_reprocessed",
        extracted_by_user_id=admin_user.id,
    )
    ResearchBrainService.record_extraction_unit(
        extraction_run_id=second_run.id,
        document_id=document.id,
        unit_type="PAGE",
        sequence_number=1,
        content_text="second, improved pass, page 1",
        created_by_user_id=admin_user.id,
    )

    units = ResearchBrainService.get_extraction_units(document.id)
    assert len(units) == 2
    texts = {unit.content_text for unit in units}
    assert texts == {"first pass, page 1", "second, improved pass, page 1"}


def test_duplicate_unit_within_the_same_run_is_rejected(app, admin_user, document):
    run = ResearchBrainService.create_extraction_run(
        document_id=document.id,
        method="manual_pilot",
        extracted_by_user_id=admin_user.id,
    )
    ResearchBrainService.record_extraction_unit(
        extraction_run_id=run.id,
        document_id=document.id,
        unit_type="PAGE",
        sequence_number=1,
        content_text="first attempt",
        created_by_user_id=admin_user.id,
    )

    with pytest.raises(IntegrityError):
        ResearchBrainService.record_extraction_unit(
            extraction_run_id=run.id,
            document_id=document.id,
            unit_type="PAGE",
            sequence_number=1,
            content_text="duplicate within the same run",
            created_by_user_id=admin_user.id,
        )
    db.session.rollback()

    assert len(ResearchBrainService.get_extraction_units(document.id)) == 1


def test_record_evidence_can_cite_a_source_extraction_unit(
    app, admin_user, document
):
    run = ResearchBrainService.create_extraction_run(
        document_id=document.id,
        method="manual_pilot",
        extracted_by_user_id=admin_user.id,
    )
    unit = ResearchBrainService.record_extraction_unit(
        extraction_run_id=run.id,
        document_id=document.id,
        unit_type="TABLE",
        sequence_number=1,
        content_text="Revenue from operations 1,602.89 ...",
        created_by_user_id=admin_user.id,
        locator="Statement of Unaudited Consolidated Financial Results",
    )

    evidence = ResearchBrainService.record_evidence(
        extraction_run_id=run.id,
        document_id=document.id,
        text_snippet="Revenue from operations 1,602.89",
        created_by_user_id=admin_user.id,
        source_extraction_unit_id=unit.id,
    )

    persisted = db.session.get(Evidence, evidence.id)
    assert persisted.source_extraction_unit_id == unit.id


def test_record_evidence_without_source_extraction_unit_still_works(
    app, admin_user, document
):
    """Existing call sites (Tasks 3-4, Snapshot A) are unaffected by the
    new optional parameter."""

    run = ResearchBrainService.create_extraction_run(
        document_id=document.id,
        method="manual_pilot",
        extracted_by_user_id=admin_user.id,
    )
    evidence = ResearchBrainService.record_evidence(
        extraction_run_id=run.id,
        document_id=document.id,
        text_snippet="Read directly, no persisted unit cited.",
        created_by_user_id=admin_user.id,
    )

    assert evidence.source_extraction_unit_id is None
