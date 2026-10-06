"""Research Brain Pilot Task 6: ``ExtractionUnit`` and ``Evidence``'s new
optional provenance link to it.

Locks the persistence-layer contract: one page/section/table-level content
unit per extraction pass, immutable, with the key reprocessing behavior
made explicit -- existing units mean "already processed," never
"permanently prohibited from reprocessing." Two independent extraction
runs over the same document may each own a unit at the same
``(unit_type, sequence_number)``; only a duplicate *within one run* is
rejected.
"""

from __future__ import annotations

import uuid

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from app import db
from app.models import Company, Evidence, ExtractionRun, ExtractionUnit
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


def test_extraction_unit_has_exact_columns(app):
    assert _reflected_columns("extraction_unit") == {
        "id": False,
        "created_at": False,
        "extraction_run_id": False,
        "document_id": False,
        "unit_type": False,
        "sequence_number": False,
        "locator": True,
        "content_text": False,
        "created_by_user_id": False,
    }


def test_content_text_is_unbounded_text_not_bounded_string(app):
    inspector = sa.inspect(db.engine)
    columns = {
        column["name"]: column["type"]
        for column in inspector.get_columns("extraction_unit")
    }
    # sa.Text has no length cap, unlike sa.String(4000).
    assert getattr(columns["content_text"], "length", None) is None


def test_extraction_unit_foreign_keys(app):
    inspector = sa.inspect(db.engine)
    foreign_keys = {
        frozenset(constraint["constrained_columns"]): constraint["referred_table"]
        for constraint in inspector.get_foreign_keys("extraction_unit")
    }
    assert foreign_keys[frozenset({"extraction_run_id"})] == "extraction_run"
    assert foreign_keys[frozenset({"document_id"})] == "document"
    assert foreign_keys[frozenset({"created_by_user_id"})] == "user"


def test_extraction_unit_unique_constraint(app):
    inspector = sa.inspect(db.engine)
    unique_constraints = {
        constraint["name"]: sorted(constraint["column_names"])
        for constraint in inspector.get_unique_constraints("extraction_unit")
    }
    assert unique_constraints["uq_extraction_unit_run_type_sequence"] == [
        "extraction_run_id",
        "sequence_number",
        "unit_type",
    ]


def test_extraction_unit_persists_with_uuid_id(app, admin_user, document):
    run = ExtractionRun(
        document_id=document.id,
        method="manual_pilot",
        extracted_by_user_id=admin_user.id,
    )
    db.session.add(run)
    db.session.commit()

    unit = ExtractionUnit(
        extraction_run_id=run.id,
        document_id=document.id,
        unit_type="PAGE",
        sequence_number=1,
        locator="page 1",
        content_text="Full extracted page text goes here.",
        created_by_user_id=admin_user.id,
    )
    db.session.add(unit)
    db.session.commit()

    assert isinstance(unit.id, str)
    assert uuid.UUID(unit.id).version == 4
    assert unit.unit_type == "PAGE"


def test_extraction_unit_cannot_be_updated_or_deleted(app, admin_user, document):
    run = ExtractionRun(
        document_id=document.id,
        method="manual_pilot",
        extracted_by_user_id=admin_user.id,
    )
    db.session.add(run)
    db.session.commit()

    unit = ExtractionUnit(
        extraction_run_id=run.id,
        document_id=document.id,
        unit_type="PAGE",
        sequence_number=1,
        content_text="original text",
        created_by_user_id=admin_user.id,
    )
    db.session.add(unit)
    db.session.commit()

    unit.content_text = "rewritten"
    with pytest.raises(sa.exc.InvalidRequestError, match="immutable"):
        db.session.commit()
    db.session.rollback()

    db.session.delete(db.session.get(ExtractionUnit, unit.id))
    with pytest.raises(sa.exc.InvalidRequestError, match="immutable"):
        db.session.commit()
    db.session.rollback()


def test_duplicate_unit_within_one_run_is_rejected_at_database(
    app, admin_user, document
):
    run = ExtractionRun(
        document_id=document.id,
        method="manual_pilot",
        extracted_by_user_id=admin_user.id,
    )
    db.session.add(run)
    db.session.commit()

    db.session.add(
        ExtractionUnit(
            extraction_run_id=run.id,
            document_id=document.id,
            unit_type="PAGE",
            sequence_number=1,
            content_text="first pass at page 1",
            created_by_user_id=admin_user.id,
        )
    )
    db.session.commit()

    db.session.add(
        ExtractionUnit(
            extraction_run_id=run.id,
            document_id=document.id,
            unit_type="PAGE",
            sequence_number=1,
            content_text="duplicate within the same run",
            created_by_user_id=admin_user.id,
        )
    )
    _commit_expect_integrity()


def test_reprocessing_under_a_new_run_is_never_blocked(app, admin_user, document):
    """Existing units mean 'already processed', never 'permanently
    prohibited from reprocessing' -- a second, independent ExtractionRun
    may own its own unit at the same (unit_type, sequence_number)."""

    first_run = ExtractionRun(
        document_id=document.id,
        method="manual_pilot",
        extracted_by_user_id=admin_user.id,
    )
    db.session.add(first_run)
    db.session.commit()

    first_unit = ExtractionUnit(
        extraction_run_id=first_run.id,
        document_id=document.id,
        unit_type="PAGE",
        sequence_number=1,
        content_text="first extraction pass, page 1",
        created_by_user_id=admin_user.id,
    )
    db.session.add(first_unit)
    db.session.commit()

    second_run = ExtractionRun(
        document_id=document.id,
        method="manual_pilot_reprocessed",
        extracted_by_user_id=admin_user.id,
    )
    db.session.add(second_run)
    db.session.commit()

    second_unit = ExtractionUnit(
        extraction_run_id=second_run.id,
        document_id=document.id,
        unit_type="PAGE",
        sequence_number=1,
        content_text="second, improved extraction pass, page 1",
        created_by_user_id=admin_user.id,
    )
    db.session.add(second_unit)
    db.session.commit()

    # Both runs and both units persist unchanged, side by side.
    reloaded_first = db.session.get(ExtractionUnit, first_unit.id)
    reloaded_second = db.session.get(ExtractionUnit, second_unit.id)
    assert reloaded_first.content_text == "first extraction pass, page 1"
    assert reloaded_second.content_text == "second, improved extraction pass, page 1"
    assert reloaded_first.extraction_run_id != reloaded_second.extraction_run_id


# ---------------------------------------------------------------------------
# Evidence.source_extraction_unit_id -- additive only
# ---------------------------------------------------------------------------


def test_evidence_gained_exactly_one_new_nullable_column(app):
    assert _reflected_columns("evidence") == {
        "id": False,
        "created_at": False,
        "extraction_run_id": False,
        "document_id": False,
        "text_snippet": False,
        "locator": True,
        "source_extraction_unit_id": True,
        "created_by_user_id": False,
    }


def test_evidence_without_source_extraction_unit_remains_valid(
    app, admin_user, document
):
    """Pre-existing-style Evidence rows (Snapshot A's shape) stay valid with
    this column left null -- no backfill is implied or required."""

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
        text_snippet="Read directly from the PDF, no persisted unit cited.",
        created_by_user_id=admin_user.id,
    )
    db.session.add(evidence)
    db.session.commit()

    assert evidence.source_extraction_unit_id is None


def test_evidence_can_cite_a_specific_extraction_unit(app, admin_user, document):
    run = ExtractionRun(
        document_id=document.id,
        method="manual_pilot",
        extracted_by_user_id=admin_user.id,
    )
    db.session.add(run)
    db.session.commit()

    unit = ExtractionUnit(
        extraction_run_id=run.id,
        document_id=document.id,
        unit_type="TABLE",
        sequence_number=1,
        locator="Statement of Unaudited Consolidated Financial Results",
        content_text="Revenue from operations 1,602.89 ...",
        created_by_user_id=admin_user.id,
    )
    db.session.add(unit)
    db.session.commit()

    evidence = Evidence(
        extraction_run_id=run.id,
        document_id=document.id,
        text_snippet="Revenue from operations 1,602.89",
        source_extraction_unit_id=unit.id,
        created_by_user_id=admin_user.id,
    )
    db.session.add(evidence)
    db.session.commit()

    assert evidence.source_extraction_unit.id == unit.id
