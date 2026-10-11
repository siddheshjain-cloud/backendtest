"""Research Coverage & Fact Intelligence, Slice 1:
``ResearchCoverageService`` tests.

Covers dimension resolution precedence (native ``document_type`` vs.
``CoverageDocumentSubtype`` override), the mandatory, adversarial
completeness-rule tests for ``record_coverage_review_pass`` (a pass covering
only part of a document's units must never produce a ``CoverageRecord``;
overlapping/re-read passes must not be double-counted; a closed pair must
never receive a second ``CoverageRecord``), the material-content-sticks-
through-partial-passes rule, profile effective-dating, extraction-run
scoping after reprocessing, and the ``get_document_coverage_status`` rollup
logic.
"""

from __future__ import annotations

from datetime import date, timedelta

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
from app.models.research_coverage import CoverageRecord, CoverageReviewPass
from app.services.document_library_service import DocumentLibraryService
from app.services.research_brain_service import ResearchBrainService
from app.services.research_coverage_service import (
    ResearchCoverageService,
    _merge_intervals,
    _union_length,
)
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


def _make_document(admin_user, company, *, document_type, title):
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
def quarterly_document(admin_user, company):
    return _make_document(
        admin_user,
        company,
        document_type=DocumentType.QUARTERLY_RESULTS,
        title="IKIO_RESULTS_Q1_FY2027",
    )


@pytest.fixture
def other_typed_document(admin_user, company):
    """A document native-typed OTHER -- the overflow case a subtype tag
    exists for."""

    return _make_document(
        admin_user,
        company,
        document_type=DocumentType.OTHER,
        title="IKIO_DRHP_DRAFT",
    )


def _two_dimensions():
    governance = ResearchCoverageService.create_dimension(
        code="GOVERNANCE_RPT",
        name="Governance & Related-Party Transactions",
        description="RPT disclosures, KMP remuneration.",
    )
    corporate = ResearchCoverageService.create_dimension(
        code="CORPORATE_STRUCTURE_MA",
        name="Corporate Structure & M&A",
        description="Acquisitions, mergers, restructuring.",
    )
    return governance, corporate


def _make_profile(
    admin_user,
    document_type_code,
    dimensions,
    *,
    required_codes,
    effective_from=date(2026, 10, 7),
    supersedes_profile_id=None,
):
    requirements = [
        (dim.id, dim.code in required_codes, None) for dim in dimensions
    ]
    return ResearchCoverageService.create_coverage_profile(
        document_type_code=document_type_code,
        dimension_requirements=requirements,
        effective_from=effective_from,
        created_by_user_id=admin_user.id,
        supersedes_profile_id=supersedes_profile_id,
    )


def _extraction_units(admin_user, document, count: int, *, run=None):
    run = run or ResearchBrainService.create_extraction_run(
        document_id=document.id,
        method="manual_pilot",
        extracted_by_user_id=admin_user.id,
    )
    units = []
    for i in range(1, count + 1):
        units.append(
            ResearchBrainService.record_extraction_unit(
                extraction_run_id=run.id,
                document_id=document.id,
                unit_type="PAGE",
                sequence_number=i,
                content_text=f"page {i} text",
                created_by_user_id=admin_user.id,
            )
        )
    return run, units


def _coverage_records(document_id, dimension_id):
    return db.session.scalars(
        sa.select(CoverageRecord).where(
            CoverageRecord.document_id == document_id,
            CoverageRecord.research_dimension_id == dimension_id,
        )
    ).all()


# ---------------------------------------------------------------------------
# get_required_dimensions resolution precedence
# ---------------------------------------------------------------------------


def test_required_dimensions_resolve_from_native_document_type(
    app, admin_user, quarterly_document
):
    governance, corporate = _two_dimensions()
    _make_profile(
        admin_user,
        "QUARTERLY_RESULTS",
        [governance, corporate],
        required_codes={"CORPORATE_STRUCTURE_MA"},
    )

    required = ResearchCoverageService.get_required_dimensions(
        quarterly_document.id
    )
    assert {d.code for d in required} == {"CORPORATE_STRUCTURE_MA"}


def test_required_dimensions_resolve_via_subtype_when_native_type_is_other(
    app, admin_user, other_typed_document
):
    governance, corporate = _two_dimensions()
    _make_profile(
        admin_user,
        "DRHP",
        [governance, corporate],
        required_codes={"GOVERNANCE_RPT", "CORPORATE_STRUCTURE_MA"},
    )

    ResearchCoverageService.tag_document_subtype(
        document_id=other_typed_document.id,
        subtype_code="DRHP",
        assigned_by_user_id=admin_user.id,
    )

    required = ResearchCoverageService.get_required_dimensions(
        other_typed_document.id
    )
    assert {d.code for d in required} == {
        "GOVERNANCE_RPT",
        "CORPORATE_STRUCTURE_MA",
    }


def test_subtype_tag_takes_precedence_over_native_type(
    app, admin_user, quarterly_document
):
    """Even though the document's native type (QUARTERLY_RESULTS) has its
    own seeded profile, an explicit subtype tag wins."""

    governance, corporate = _two_dimensions()
    _make_profile(
        admin_user,
        "QUARTERLY_RESULTS",
        [governance, corporate],
        required_codes={"CORPORATE_STRUCTURE_MA"},
    )
    _make_profile(
        admin_user,
        "MERGER_SCHEME_DOCUMENT",
        [governance, corporate],
        required_codes={"GOVERNANCE_RPT"},
    )

    ResearchCoverageService.tag_document_subtype(
        document_id=quarterly_document.id,
        subtype_code="MERGER_SCHEME_DOCUMENT",
        assigned_by_user_id=admin_user.id,
    )

    required = ResearchCoverageService.get_required_dimensions(
        quarterly_document.id
    )
    assert {d.code for d in required} == {"GOVERNANCE_RPT"}


def test_tagging_a_subtype_with_no_profile_yet_is_rejected(
    app, admin_user, quarterly_document
):
    """A code-review finding: tagging a document with an unknown/typo'd
    subtype_code used to succeed silently, and get_required_dimensions
    would then return an empty list -- indistinguishable from a document
    type that genuinely has zero required dimensions."""

    from app.utils.research_errors import ResearchValidationError

    with pytest.raises(ResearchValidationError):
        ResearchCoverageService.tag_document_subtype(
            document_id=quarterly_document.id,
            subtype_code="NO_SUCH_PROFILE_YET",
            assigned_by_user_id=admin_user.id,
        )


def test_code_fields_must_be_upper_slugs(app, admin_user, quarterly_document):
    from app.utils.research_errors import ResearchValidationError

    with pytest.raises(ResearchValidationError):
        ResearchCoverageService.create_dimension(
            code="not-upper-slug",
            name="Bad code",
            description="x",
        )

    with pytest.raises(ResearchValidationError):
        ResearchCoverageService.create_coverage_profile(
            document_type_code="lowercase_type",
            dimension_requirements=[],
            effective_from=date(2026, 1, 1),
            created_by_user_id=admin_user.id,
        )

    with pytest.raises(ResearchValidationError):
        ResearchCoverageService.tag_document_subtype(
            document_id=quarterly_document.id,
            subtype_code="not-a-slug",
            assigned_by_user_id=admin_user.id,
        )


# ---------------------------------------------------------------------------
# Interval-merge helpers (pure functions, no DB)
# ---------------------------------------------------------------------------


def test_merge_intervals_merges_overlapping_and_adjacent_ranges():
    assert _merge_intervals([(1, 10), (6, 15)]) == [(1, 15)]
    assert _merge_intervals([(1, 5), (6, 10)]) == [(1, 10)]
    assert _merge_intervals([(1, 5), (20, 30)]) == [(1, 5), (20, 30)]
    assert _merge_intervals([(20, 30), (1, 5)]) == [(1, 5), (20, 30)]


def test_union_length_does_not_double_count_overlap():
    assert _union_length([(1, 10), (6, 15)]) == 15
    assert _union_length([(1, 10), (1, 10)]) == 10
    assert _union_length([(1, 5), (10, 15)]) == 11


# ---------------------------------------------------------------------------
# record_coverage_review_pass: contiguous-range validation
# ---------------------------------------------------------------------------


def test_non_contiguous_range_is_rejected(app, admin_user, quarterly_document):
    governance, _ = _two_dimensions()
    _extraction_units(admin_user, quarterly_document, count=10)

    with pytest.raises(ResearchValidationError):
        ResearchCoverageService.record_coverage_review_pass(
            document_id=quarterly_document.id,
            research_dimension_id=governance.id,
            performed_by_user_id=admin_user.id,
            units_considered_count=37,
            units_considered_min_seq=1,
            units_considered_max_seq=482,
        )


def test_inverted_range_is_rejected(app, admin_user, quarterly_document):
    governance, _ = _two_dimensions()
    _extraction_units(admin_user, quarterly_document, count=10)

    with pytest.raises(ResearchValidationError):
        ResearchCoverageService.record_coverage_review_pass(
            document_id=quarterly_document.id,
            research_dimension_id=governance.id,
            performed_by_user_id=admin_user.id,
            units_considered_count=3,
            units_considered_min_seq=5,
            units_considered_max_seq=3,
        )


# ---------------------------------------------------------------------------
# The mandatory, adversarial completeness-rule tests
# ---------------------------------------------------------------------------


def test_partial_pass_never_produces_a_coverage_record(
    app, admin_user, quarterly_document
):
    governance, _ = _two_dimensions()
    _extraction_units(admin_user, quarterly_document, count=10)

    ResearchCoverageService.record_coverage_review_pass(
        document_id=quarterly_document.id,
        research_dimension_id=governance.id,
        performed_by_user_id=admin_user.id,
        units_considered_count=3,
        units_considered_min_seq=1,
        units_considered_max_seq=3,
    )

    assert _coverage_records(quarterly_document.id, governance.id) == []


def test_cumulative_passes_close_coverage_once_total_is_reached(
    app, admin_user, quarterly_document
):
    governance, _ = _two_dimensions()
    _extraction_units(admin_user, quarterly_document, count=10)

    ResearchCoverageService.record_coverage_review_pass(
        document_id=quarterly_document.id,
        research_dimension_id=governance.id,
        performed_by_user_id=admin_user.id,
        units_considered_count=3,
        units_considered_min_seq=1,
        units_considered_max_seq=3,
    )
    assert _coverage_records(quarterly_document.id, governance.id) == []

    second_pass = ResearchCoverageService.record_coverage_review_pass(
        document_id=quarterly_document.id,
        research_dimension_id=governance.id,
        performed_by_user_id=admin_user.id,
        units_considered_count=7,
        units_considered_min_seq=4,
        units_considered_max_seq=10,
    )

    records_after_complete = _coverage_records(
        quarterly_document.id, governance.id
    )
    assert len(records_after_complete) == 1
    assert records_after_complete[0].state == "REVIEWED_NO_FINDING"
    assert records_after_complete[0].review_pass_id == second_pass.id


def test_overlapping_passes_are_not_double_counted(
    app, admin_user, quarterly_document
):
    """The bug the code review caught: re-reading pages 1-6 after already
    reading 1-6 must not let 6+6=12 >= 10 falsely close a 10-unit document."""

    governance, _ = _two_dimensions()
    _extraction_units(admin_user, quarterly_document, count=10)

    ResearchCoverageService.record_coverage_review_pass(
        document_id=quarterly_document.id,
        research_dimension_id=governance.id,
        performed_by_user_id=admin_user.id,
        units_considered_count=6,
        units_considered_min_seq=1,
        units_considered_max_seq=6,
    )
    # Re-read the same first six pages again -- genuinely re-read, but zero
    # NEW pages. Raw counts would sum to 12 >= 10 and wrongly close this.
    ResearchCoverageService.record_coverage_review_pass(
        document_id=quarterly_document.id,
        research_dimension_id=governance.id,
        performed_by_user_id=admin_user.id,
        units_considered_count=6,
        units_considered_min_seq=1,
        units_considered_max_seq=6,
    )
    assert _coverage_records(quarterly_document.id, governance.id) == []

    # Now genuinely cover the remaining, never-before-seen pages 7-10.
    ResearchCoverageService.record_coverage_review_pass(
        document_id=quarterly_document.id,
        research_dimension_id=governance.id,
        performed_by_user_id=admin_user.id,
        units_considered_count=4,
        units_considered_min_seq=7,
        units_considered_max_seq=10,
    )
    records = _coverage_records(quarterly_document.id, governance.id)
    assert len(records) == 1
    assert records[0].state == "REVIEWED_NO_FINDING"


def test_closing_the_pair_twice_never_inserts_a_second_coverage_record(
    app, admin_user, quarterly_document
):
    """The bug the code review caught: once closed, a later legitimate pass
    for the same pair must not insert a redundant second CoverageRecord."""

    governance, _ = _two_dimensions()
    _extraction_units(admin_user, quarterly_document, count=10)

    ResearchCoverageService.record_coverage_review_pass(
        document_id=quarterly_document.id,
        research_dimension_id=governance.id,
        performed_by_user_id=admin_user.id,
        units_considered_count=10,
        units_considered_min_seq=1,
        units_considered_max_seq=10,
    )
    assert len(_coverage_records(quarterly_document.id, governance.id)) == 1

    # A later, legitimate re-confirmation pass over the same, already-closed
    # pair must not create a second CoverageRecord.
    ResearchCoverageService.record_coverage_review_pass(
        document_id=quarterly_document.id,
        research_dimension_id=governance.id,
        performed_by_user_id=admin_user.id,
        units_considered_count=10,
        units_considered_min_seq=1,
        units_considered_max_seq=10,
    )
    records = _coverage_records(quarterly_document.id, governance.id)
    assert len(records) == 1


def test_material_content_on_any_partial_pass_produces_finding_generated(
    app, admin_user, quarterly_document
):
    governance, _ = _two_dimensions()
    _extraction_units(admin_user, quarterly_document, count=10)

    ResearchCoverageService.record_coverage_review_pass(
        document_id=quarterly_document.id,
        research_dimension_id=governance.id,
        performed_by_user_id=admin_user.id,
        units_considered_count=4,
        units_considered_min_seq=1,
        units_considered_max_seq=4,
        has_material_content=True,
        notes="Found an RPT disclosure in pages 1-4.",
    )
    ResearchCoverageService.record_coverage_review_pass(
        document_id=quarterly_document.id,
        research_dimension_id=governance.id,
        performed_by_user_id=admin_user.id,
        units_considered_count=3,
        units_considered_min_seq=5,
        units_considered_max_seq=7,
        has_material_content=False,
    )
    ResearchCoverageService.record_coverage_review_pass(
        document_id=quarterly_document.id,
        research_dimension_id=governance.id,
        performed_by_user_id=admin_user.id,
        units_considered_count=3,
        units_considered_min_seq=8,
        units_considered_max_seq=10,
        has_material_content=False,
    )

    records = _coverage_records(quarterly_document.id, governance.id)
    assert len(records) == 1
    assert records[0].state == "FINDING_GENERATED"


def test_get_document_dimension_coverage_reports_not_reviewed_until_closed(
    app, admin_user, quarterly_document
):
    governance, corporate = _two_dimensions()
    _make_profile(
        admin_user,
        "QUARTERLY_RESULTS",
        [governance, corporate],
        required_codes={"GOVERNANCE_RPT", "CORPORATE_STRUCTURE_MA"},
    )
    _extraction_units(admin_user, quarterly_document, count=5)

    coverage = ResearchCoverageService.get_document_dimension_coverage(
        quarterly_document.id
    )
    assert coverage == {
        "GOVERNANCE_RPT": "NOT_REVIEWED",
        "CORPORATE_STRUCTURE_MA": "NOT_REVIEWED",
    }

    ResearchCoverageService.record_coverage_review_pass(
        document_id=quarterly_document.id,
        research_dimension_id=governance.id,
        performed_by_user_id=admin_user.id,
        units_considered_count=5,
        units_considered_min_seq=1,
        units_considered_max_seq=5,
    )

    coverage = ResearchCoverageService.get_document_dimension_coverage(
        quarterly_document.id
    )
    assert coverage == {
        "GOVERNANCE_RPT": "REVIEWED_NO_FINDING",
        "CORPORATE_STRUCTURE_MA": "NOT_REVIEWED",
    }


# ---------------------------------------------------------------------------
# ExtractionRun scoping: reprocessing starts a fresh completeness target
# ---------------------------------------------------------------------------


def test_coverage_is_scoped_to_the_documents_current_extraction_run(
    app, admin_user, quarterly_document
):
    governance, _ = _two_dimensions()
    first_run, _ = _extraction_units(admin_user, quarterly_document, count=5)

    ResearchCoverageService.record_coverage_review_pass(
        document_id=quarterly_document.id,
        research_dimension_id=governance.id,
        performed_by_user_id=admin_user.id,
        units_considered_count=5,
        units_considered_min_seq=1,
        units_considered_max_seq=5,
    )
    assert len(_coverage_records(quarterly_document.id, governance.id)) == 1

    # Reprocess the document under a brand-new ExtractionRun with a
    # different number of units -- explicitly supported per ExtractionUnit's
    # own docstring. The old run's pass must not count toward the new run's
    # completeness target, and the old CoverageRecord is untouched history.
    second_run, _ = _extraction_units(
        admin_user, quarterly_document, count=8
    )
    assert second_run.id != first_run.id

    pass_against_new_run = ResearchCoverageService.record_coverage_review_pass(
        document_id=quarterly_document.id,
        research_dimension_id=governance.id,
        performed_by_user_id=admin_user.id,
        units_considered_count=5,
        units_considered_min_seq=1,
        units_considered_max_seq=5,
    )
    assert pass_against_new_run.extraction_run_id == second_run.id

    # Still only the one, original CoverageRecord -- 5 of 8 new-run units
    # considered is not complete, regardless of the old run's closed pass.
    records = _coverage_records(quarterly_document.id, governance.id)
    assert len(records) == 1

    ResearchCoverageService.record_coverage_review_pass(
        document_id=quarterly_document.id,
        research_dimension_id=governance.id,
        performed_by_user_id=admin_user.id,
        units_considered_count=3,
        units_considered_min_seq=6,
        units_considered_max_seq=8,
    )
    records = _coverage_records(quarterly_document.id, governance.id)
    assert len(records) == 2


# ---------------------------------------------------------------------------
# CoverageProfile effective-dating
# ---------------------------------------------------------------------------


def test_future_dated_profile_does_not_take_effect_immediately(
    app, admin_user, quarterly_document
):
    governance, corporate = _two_dimensions()
    old_profile = _make_profile(
        admin_user,
        "QUARTERLY_RESULTS",
        [governance, corporate],
        required_codes={"GOVERNANCE_RPT"},
        effective_from=date(2026, 1, 1),
    )

    future_profile = _make_profile(
        admin_user,
        "QUARTERLY_RESULTS",
        [governance, corporate],
        required_codes={"CORPORATE_STRUCTURE_MA"},
        effective_from=date.today() + timedelta(days=30),
        supersedes_profile_id=old_profile.id,
    )

    # The old profile is technically "superseded" by the future one, but the
    # future one isn't effective yet -- lookups must still see the old
    # profile's requirements today.
    required = ResearchCoverageService.get_required_dimensions(
        quarterly_document.id
    )
    assert {d.code for d in required} == {"GOVERNANCE_RPT"}

    # Once the future date actually arrives, the new profile governs.
    required_later = ResearchCoverageService.get_active_profile(
        "QUARTERLY_RESULTS", as_of=date.today() + timedelta(days=31)
    )
    assert required_later.id == future_profile.id


# ---------------------------------------------------------------------------
# get_document_coverage_status rollup
# ---------------------------------------------------------------------------


def test_document_coverage_status_rollup_not_started_in_progress_fully_swept(
    app, admin_user, quarterly_document
):
    governance, corporate = _two_dimensions()
    _make_profile(
        admin_user,
        "QUARTERLY_RESULTS",
        [governance, corporate],
        required_codes={"GOVERNANCE_RPT", "CORPORATE_STRUCTURE_MA"},
    )
    _extraction_units(admin_user, quarterly_document, count=4)

    status = ResearchCoverageService.get_document_coverage_status(
        quarterly_document.id
    )
    assert status["rollup"] == "NOT_STARTED"
    assert status["document_type_code"] == "QUARTERLY_RESULTS"

    ResearchCoverageService.record_coverage_review_pass(
        document_id=quarterly_document.id,
        research_dimension_id=governance.id,
        performed_by_user_id=admin_user.id,
        units_considered_count=4,
        units_considered_min_seq=1,
        units_considered_max_seq=4,
    )

    status = ResearchCoverageService.get_document_coverage_status(
        quarterly_document.id
    )
    assert status["rollup"] == "IN_PROGRESS"

    ResearchCoverageService.record_coverage_review_pass(
        document_id=quarterly_document.id,
        research_dimension_id=corporate.id,
        performed_by_user_id=admin_user.id,
        units_considered_count=4,
        units_considered_min_seq=1,
        units_considered_max_seq=4,
        has_material_content=True,
    )

    status = ResearchCoverageService.get_document_coverage_status(
        quarterly_document.id
    )
    assert status["rollup"] == "FULLY_SWEPT"
    assert status["dimensions"] == {
        "GOVERNANCE_RPT": "REVIEWED_NO_FINDING",
        "CORPORATE_STRUCTURE_MA": "FINDING_GENERATED",
    }


def test_company_coverage_summary_rolls_up_across_documents(
    app, admin_user, company, quarterly_document
):
    governance, corporate = _two_dimensions()
    _make_profile(
        admin_user,
        "QUARTERLY_RESULTS",
        [governance, corporate],
        required_codes={"GOVERNANCE_RPT"},
    )
    _extraction_units(admin_user, quarterly_document, count=2)

    summary = ResearchCoverageService.get_company_coverage_summary(company.id)
    assert summary["document_count"] == 1
    assert summary["documents_by_rollup"]["NOT_STARTED"] == 1

    ResearchCoverageService.record_coverage_review_pass(
        document_id=quarterly_document.id,
        research_dimension_id=governance.id,
        performed_by_user_id=admin_user.id,
        units_considered_count=2,
        units_considered_min_seq=1,
        units_considered_max_seq=2,
    )

    summary = ResearchCoverageService.get_company_coverage_summary(company.id)
    assert summary["documents_by_rollup"]["FULLY_SWEPT"] == 1
    assert summary["required_dimension_coverage_pct"] == 100.0
