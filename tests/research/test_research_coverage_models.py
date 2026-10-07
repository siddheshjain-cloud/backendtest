"""Research Coverage & Fact Intelligence, Slice 1: model-level tests for the
six Coverage Foundation tables.

Locks: immutability (update and delete both raise ``InvalidRequestError``)
on all six models, ``CoverageProfile``'s supersession idiom (matching
``ExtractedFact``'s own "current = not referenced by any other row's
supersedes_*_id" pattern), and FK/constraint shape.
"""

from __future__ import annotations

import uuid
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
from app.models.research_coverage import (
    CoverageDocumentSubtype,
    CoverageProfile,
    CoverageProfileDimension,
    CoverageRecord,
    CoverageReviewPass,
    ResearchDimension,
)
from app.services.document_library_service import DocumentLibraryService
from app.services.research_brain_service import ResearchBrainService


def _reflected_columns(table_name: str) -> dict[str, bool]:
    inspector = sa.inspect(db.engine)
    return {
        column["name"]: column["nullable"]
        for column in inspector.get_columns(table_name)
    }


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


@pytest.fixture
def extraction_run(admin_user, document):
    return ResearchBrainService.create_extraction_run(
        document_id=document.id,
        method="manual_pilot",
        extracted_by_user_id=admin_user.id,
    )


@pytest.fixture
def dimension():
    dim = ResearchDimension(
        code="GOVERNANCE_RPT",
        name="Governance & Related-Party Transactions",
        description="RPT disclosures, KMP remuneration, audit-trail notes.",
    )
    db.session.add(dim)
    db.session.commit()
    return dim


# ---------------------------------------------------------------------------
# ResearchDimension
# ---------------------------------------------------------------------------


def test_research_dimension_has_exact_columns(app):
    assert _reflected_columns("research_dimension") == {
        "id": False,
        "created_at": False,
        "code": False,
        "name": False,
        "description": False,
        "is_active": False,
    }


def test_research_dimension_code_is_unique(app):
    db.session.add(
        ResearchDimension(
            code="GOVERNANCE_RPT", name="A", description="A description"
        )
    )
    db.session.commit()

    db.session.add(
        ResearchDimension(
            code="GOVERNANCE_RPT", name="B", description="B description"
        )
    )
    with pytest.raises(sa.exc.IntegrityError):
        db.session.commit()
    db.session.rollback()


def test_research_dimension_cannot_be_updated_or_deleted(app, dimension):
    dimension.name = "rewritten"
    with pytest.raises(sa.exc.InvalidRequestError, match="immutable"):
        db.session.commit()
    db.session.rollback()

    db.session.delete(db.session.get(ResearchDimension, dimension.id))
    with pytest.raises(sa.exc.InvalidRequestError, match="immutable"):
        db.session.commit()
    db.session.rollback()


# ---------------------------------------------------------------------------
# CoverageDocumentSubtype
# ---------------------------------------------------------------------------


def test_coverage_document_subtype_foreign_keys(app):
    inspector = sa.inspect(db.engine)
    foreign_keys = {
        frozenset(constraint["constrained_columns"]): constraint["referred_table"]
        for constraint in inspector.get_foreign_keys("coverage_document_subtype")
    }
    assert foreign_keys[frozenset({"document_id"})] == "document"
    assert foreign_keys[frozenset({"assigned_by_user_id"})] == "user"


def test_coverage_document_subtype_persists_and_is_immutable(
    app, admin_user, document
):
    tag = CoverageDocumentSubtype(
        document_id=document.id,
        subtype_code="DRHP",
        assigned_by_user_id=admin_user.id,
    )
    db.session.add(tag)
    db.session.commit()

    assert isinstance(tag.id, str)
    assert uuid.UUID(tag.id).version == 4

    tag.subtype_code = "RHP"
    with pytest.raises(sa.exc.InvalidRequestError, match="immutable"):
        db.session.commit()
    db.session.rollback()

    db.session.delete(db.session.get(CoverageDocumentSubtype, tag.id))
    with pytest.raises(sa.exc.InvalidRequestError, match="immutable"):
        db.session.commit()
    db.session.rollback()


def test_current_subtype_is_the_most_recent_row(app, admin_user, document):
    """No model-level "current" accessor is required by spec, but the
    convention (most recent row by created_at) must be queryable."""

    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)

    first = CoverageDocumentSubtype(
        document_id=document.id,
        subtype_code="DRHP",
        assigned_by_user_id=admin_user.id,
        created_at=now,
    )
    db.session.add(first)
    db.session.commit()

    second = CoverageDocumentSubtype(
        document_id=document.id,
        subtype_code="RHP",
        assigned_by_user_id=admin_user.id,
        created_at=now + timedelta(seconds=1),
    )
    db.session.add(second)
    db.session.commit()

    most_recent = db.session.scalars(
        sa.select(CoverageDocumentSubtype)
        .where(CoverageDocumentSubtype.document_id == document.id)
        .order_by(CoverageDocumentSubtype.created_at.desc())
        .limit(1)
    ).first()
    assert most_recent.subtype_code == "RHP"


# ---------------------------------------------------------------------------
# CoverageProfile / CoverageProfileDimension
# ---------------------------------------------------------------------------


def test_coverage_profile_foreign_keys(app):
    inspector = sa.inspect(db.engine)
    foreign_keys = {
        frozenset(constraint["constrained_columns"]): constraint["referred_table"]
        for constraint in inspector.get_foreign_keys("coverage_profile")
    }
    assert foreign_keys[frozenset({"supersedes_profile_id"})] == "coverage_profile"
    assert foreign_keys[frozenset({"created_by_user_id"})] == "user"


def test_coverage_profile_cannot_be_updated_or_deleted(app, admin_user):
    profile = CoverageProfile(
        document_type_code="ANNUAL_REPORT",
        effective_from=date(2026, 10, 7),
        created_by_user_id=admin_user.id,
    )
    db.session.add(profile)
    db.session.commit()

    profile.document_type_code = "QUARTERLY_RESULTS"
    with pytest.raises(sa.exc.InvalidRequestError, match="immutable"):
        db.session.commit()
    db.session.rollback()

    db.session.delete(db.session.get(CoverageProfile, profile.id))
    with pytest.raises(sa.exc.InvalidRequestError, match="immutable"):
        db.session.commit()
    db.session.rollback()


def test_coverage_profile_supersession_matches_extracted_fact_pattern(
    app, admin_user
):
    """"Current" = the row not referenced by any other row's
    supersedes_profile_id -- identical idiom to ExtractedFact."""

    original = CoverageProfile(
        document_type_code="ANNUAL_REPORT",
        effective_from=date(2026, 1, 1),
        created_by_user_id=admin_user.id,
    )
    db.session.add(original)
    db.session.commit()

    revised = CoverageProfile(
        document_type_code="ANNUAL_REPORT",
        effective_from=date(2026, 10, 7),
        supersedes_profile_id=original.id,
        created_by_user_id=admin_user.id,
    )
    db.session.add(revised)
    db.session.commit()

    superseded_ids = sa.select(CoverageProfile.supersedes_profile_id).where(
        CoverageProfile.supersedes_profile_id.is_not(None)
    )
    current = db.session.scalars(
        sa.select(CoverageProfile).where(
            CoverageProfile.document_type_code == "ANNUAL_REPORT",
            CoverageProfile.id.not_in(superseded_ids),
        )
    ).all()

    assert len(current) == 1
    assert current[0].id == revised.id

    # The original is untouched, not deleted or edited in place.
    reloaded_original = db.session.get(CoverageProfile, original.id)
    assert reloaded_original.effective_from == date(2026, 1, 1)


def test_coverage_profile_dimension_foreign_keys(app):
    inspector = sa.inspect(db.engine)
    foreign_keys = {
        frozenset(constraint["constrained_columns"]): constraint["referred_table"]
        for constraint in inspector.get_foreign_keys("coverage_profile_dimension")
    }
    assert (
        foreign_keys[frozenset({"coverage_profile_id"})] == "coverage_profile"
    )
    assert (
        foreign_keys[frozenset({"research_dimension_id"})] == "research_dimension"
    )


def test_coverage_profile_dimension_cannot_be_updated_or_deleted(
    app, admin_user, dimension
):
    profile = CoverageProfile(
        document_type_code="ANNUAL_REPORT",
        effective_from=date(2026, 10, 7),
        created_by_user_id=admin_user.id,
    )
    db.session.add(profile)
    db.session.flush()

    requirement = CoverageProfileDimension(
        coverage_profile_id=profile.id,
        research_dimension_id=dimension.id,
        is_required=True,
    )
    db.session.add(requirement)
    db.session.commit()

    requirement.is_required = False
    with pytest.raises(sa.exc.InvalidRequestError, match="immutable"):
        db.session.commit()
    db.session.rollback()

    db.session.delete(db.session.get(CoverageProfileDimension, requirement.id))
    with pytest.raises(sa.exc.InvalidRequestError, match="immutable"):
        db.session.commit()
    db.session.rollback()


# ---------------------------------------------------------------------------
# CoverageReviewPass / CoverageRecord
# ---------------------------------------------------------------------------


def test_coverage_review_pass_foreign_keys(app):
    inspector = sa.inspect(db.engine)
    foreign_keys = {
        frozenset(constraint["constrained_columns"]): constraint["referred_table"]
        for constraint in inspector.get_foreign_keys("coverage_review_pass")
    }
    assert foreign_keys[frozenset({"document_id"})] == "document"
    assert foreign_keys[frozenset({"extraction_run_id"})] == "extraction_run"
    assert (
        foreign_keys[frozenset({"research_dimension_id"})] == "research_dimension"
    )
    assert foreign_keys[frozenset({"coverage_profile_id"})] == "coverage_profile"
    assert foreign_keys[frozenset({"performed_by_user_id"})] == "user"


def test_coverage_review_pass_requires_a_contiguous_range(
    app, admin_user, document, dimension, extraction_run
):
    """DB-level check constraint: count must equal max - min + 1."""

    mismatched = CoverageReviewPass(
        document_id=document.id,
        extraction_run_id=extraction_run.id,
        research_dimension_id=dimension.id,
        units_considered_count=3,
        units_considered_min_seq=1,
        units_considered_max_seq=10,
        performed_by_user_id=admin_user.id,
    )
    db.session.add(mismatched)
    with pytest.raises(sa.exc.IntegrityError):
        db.session.commit()
    db.session.rollback()


def test_coverage_review_pass_cannot_be_updated_or_deleted(
    app, admin_user, document, dimension, extraction_run
):
    review_pass = CoverageReviewPass(
        document_id=document.id,
        extraction_run_id=extraction_run.id,
        research_dimension_id=dimension.id,
        units_considered_count=3,
        units_considered_min_seq=1,
        units_considered_max_seq=3,
        performed_by_user_id=admin_user.id,
    )
    db.session.add(review_pass)
    db.session.commit()

    review_pass.units_considered_count = 99
    with pytest.raises(sa.exc.InvalidRequestError, match="immutable"):
        db.session.commit()
    db.session.rollback()

    db.session.delete(db.session.get(CoverageReviewPass, review_pass.id))
    with pytest.raises(sa.exc.InvalidRequestError, match="immutable"):
        db.session.commit()
    db.session.rollback()


def test_coverage_record_foreign_keys(app):
    inspector = sa.inspect(db.engine)
    foreign_keys = {
        frozenset(constraint["constrained_columns"]): constraint["referred_table"]
        for constraint in inspector.get_foreign_keys("coverage_record")
    }
    assert foreign_keys[frozenset({"document_id"})] == "document"
    assert (
        foreign_keys[frozenset({"research_dimension_id"})] == "research_dimension"
    )
    assert foreign_keys[frozenset({"review_pass_id"})] == "coverage_review_pass"


def test_coverage_record_cannot_be_updated_or_deleted(
    app, admin_user, document, dimension, extraction_run
):
    review_pass = CoverageReviewPass(
        document_id=document.id,
        extraction_run_id=extraction_run.id,
        research_dimension_id=dimension.id,
        units_considered_count=1,
        units_considered_min_seq=1,
        units_considered_max_seq=1,
        performed_by_user_id=admin_user.id,
    )
    db.session.add(review_pass)
    db.session.flush()

    record = CoverageRecord(
        document_id=document.id,
        extraction_run_id=extraction_run.id,
        research_dimension_id=dimension.id,
        state="REVIEWED_NO_FINDING",
        review_pass_id=review_pass.id,
    )
    db.session.add(record)
    db.session.commit()

    record.state = "FINDING_GENERATED"
    with pytest.raises(sa.exc.InvalidRequestError, match="immutable"):
        db.session.commit()
    db.session.rollback()

    db.session.delete(db.session.get(CoverageRecord, record.id))
    with pytest.raises(sa.exc.InvalidRequestError, match="immutable"):
        db.session.commit()
    db.session.rollback()


def test_at_most_one_coverage_record_per_run_dimension_db_enforced(
    app, admin_user, document, dimension, extraction_run
):
    """A code-review finding: the application-level 'lock existing rows,
    then decide' guard in record_coverage_review_pass has a real gap for
    the *first-ever* close of a (document, run, dimension) triple -- there
    is no existing row yet to lock. The database unique constraint is the
    actual guarantee; this test proves it directly, independent of the
    service's locking logic."""

    pass_one = CoverageReviewPass(
        document_id=document.id,
        extraction_run_id=extraction_run.id,
        research_dimension_id=dimension.id,
        units_considered_count=1,
        units_considered_min_seq=1,
        units_considered_max_seq=1,
        performed_by_user_id=admin_user.id,
    )
    db.session.add(pass_one)
    db.session.flush()

    first = CoverageRecord(
        document_id=document.id,
        extraction_run_id=extraction_run.id,
        research_dimension_id=dimension.id,
        state="REVIEWED_NO_FINDING",
        review_pass_id=pass_one.id,
    )
    db.session.add(first)
    db.session.commit()

    second = CoverageRecord(
        document_id=document.id,
        extraction_run_id=extraction_run.id,
        research_dimension_id=dimension.id,
        state="FINDING_GENERATED",
        review_pass_id=pass_one.id,
    )
    db.session.add(second)
    with pytest.raises(sa.exc.IntegrityError):
        db.session.commit()
    db.session.rollback()


def test_coverage_record_state_rejects_invalid_value(
    app, admin_user, document, dimension, extraction_run
):
    review_pass = CoverageReviewPass(
        document_id=document.id,
        extraction_run_id=extraction_run.id,
        research_dimension_id=dimension.id,
        units_considered_count=1,
        units_considered_min_seq=1,
        units_considered_max_seq=1,
        performed_by_user_id=admin_user.id,
    )
    db.session.add(review_pass)
    db.session.flush()

    with pytest.raises(sa.exc.StatementError):
        db.session.add(
            CoverageRecord(
                document_id=document.id,
                extraction_run_id=extraction_run.id,
                research_dimension_id=dimension.id,
                state="BOGUS_STATE",
                review_pass_id=review_pass.id,
            )
        )
        db.session.commit()
    db.session.rollback()
