"""Phase 3 Slice B: FinancialDiagnosticsService acceptance tests.

Covers acceptance tests 1-9 of the revised Slice B plan (namespace,
diagnostic correctness for all five groups, period correctness,
missing-data absence, disclosure-cadence, adjustment citation gate,
reconciliation, direction, double-counting protection).

Figures used below are illustrative fixture values shaped like a real
cyclicals company's financials, not Chemplast Sanmar's actual filed
numbers -- extracting Chemplast's real figures requires document content
currently stored only on external (Google Drive) storage with zero
ExtractionRun history, which is outside this slice's authorized scope
(flagged separately, not fabricated here).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
import sqlalchemy as sa

from app import db
from app.models import Company, FinancialMetricDefinition
from app.models.document import (
    AcquisitionMethod,
    DiscoverySourceType,
    DistributionStatus,
    DocumentType,
    IngestionStatus,
    SourceAccess,
)
from app.models.research_types import AdjustmentDirection, FinancialMetricNamespace
from app.services.document_library_service import DocumentLibraryService
from app.services.financial_diagnostics_service import (
    RAW_CAPEX,
    RAW_CFO,
    RAW_COST_OF_GOODS_SOLD,
    RAW_EBITDA,
    RAW_INVENTORY,
    RAW_PAT,
    RAW_REVENUE,
    RAW_TOTAL_ASSETS,
    RAW_TRADE_PAYABLES,
    RAW_TRADE_RECEIVABLES,
    FinancialDiagnosticsService,
)
from app.services.research_brain_service import ResearchBrainService
from app.services.research_coverage_service import ResearchCoverageService
from app.utils.research_errors import ResearchValidationError


@pytest.fixture
def company(ticker_factory):
    ticker = ticker_factory(symbol="CHEMPLAST_TEST", instrument_token=3)
    company = Company(
        ticker_id=ticker.id,
        legal_name="Chemplast Sanmar Limited",
        isin="INE0LOJ01999",
    )
    db.session.add(company)
    db.session.commit()
    return company


def _document(admin_user, company, document_type, title):
    payload = {
        "document": {
            "document_type": document_type,
            "title": title,
            "document_date": "2026-08-04",
            "publisher_name": "Chemplast Sanmar Limited",
            "original_source_url": f"https://example.in/chemplast/{title.lower()}",
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
    return _document(admin_user, company, DocumentType.QUARTERLY_RESULTS, "CHEMPLAST_RESULTS_TEST")


@pytest.fixture
def annual_report(admin_user, company):
    return _document(admin_user, company, DocumentType.ANNUAL_REPORT, "CHEMPLAST_AR_TEST")


@pytest.fixture
def concall_document(admin_user, company):
    return _document(admin_user, company, DocumentType.CONCALL, "CHEMPLAST_CONCALL_TEST")


@pytest.fixture
def presentation_document(admin_user, company):
    return _document(
        admin_user, company, DocumentType.INVESTOR_PRESENTATION, "CHEMPLAST_PRESENTATION_TEST"
    )


def _raw_fact(*, admin_user, document, company, fact_type, value, period, as_of_date):
    run = ResearchBrainService.create_extraction_run(
        document_id=document.id, method="manual_pilot", extracted_by_user_id=admin_user.id
    )
    evidence = ResearchBrainService.record_evidence(
        extraction_run_id=run.id,
        document_id=document.id,
        text_snippet=f"{fact_type}: {value} ({period})",
        created_by_user_id=admin_user.id,
    )
    return ResearchBrainService.record_fact(
        company_id=company.id,
        fact_type=f"fin.raw.{fact_type}",
        value_type="NUMERIC",
        value=value,
        unit="INR_CRORE",
        period=period,
        as_of_date=as_of_date,
        evidence_ids=[evidence.id],
        created_by_user_id=admin_user.id,
    )


@pytest.fixture
def seeded_facts(admin_user, company, results_document, annual_report):
    """FY2024Q2 (YoY comparator baseline), FY2025Q1 (QoQ comparator),
    FY2025Q2 (the main test period -- revenue/COGS/EBITDA/PAT/working-
    capital present, CFO/Capex/Total Assets deliberately absent), and
    FY2025 ANNUAL (CFO/Capex/Total Assets present, for the cash-
    conversion/accruals-ratio group).
    """

    facts = {}
    facts["fy2024q2_revenue"] = _raw_fact(
        admin_user=admin_user, document=results_document, company=company,
        fact_type=RAW_REVENUE, value="100.00", period="FY2024Q2", as_of_date=date(2023, 9, 30),
    )
    facts["fy2024q2_ebitda"] = _raw_fact(
        admin_user=admin_user, document=results_document, company=company,
        fact_type=RAW_EBITDA, value="20.00", period="FY2024Q2", as_of_date=date(2023, 9, 30),
    )
    facts["fy2025q1_revenue"] = _raw_fact(
        admin_user=admin_user, document=results_document, company=company,
        fact_type=RAW_REVENUE, value="110.00", period="FY2025Q1", as_of_date=date(2024, 6, 30),
    )
    facts["fy2025q1_ebitda"] = _raw_fact(
        admin_user=admin_user, document=results_document, company=company,
        fact_type=RAW_EBITDA, value="25.00", period="FY2025Q1", as_of_date=date(2024, 6, 30),
    )

    q2_as_of = date(2024, 9, 30)
    facts["fy2025q2_revenue"] = _raw_fact(
        admin_user=admin_user, document=results_document, company=company,
        fact_type=RAW_REVENUE, value="120.00", period="FY2025Q2", as_of_date=q2_as_of,
    )
    facts["fy2025q2_cogs"] = _raw_fact(
        admin_user=admin_user, document=results_document, company=company,
        fact_type=RAW_COST_OF_GOODS_SOLD, value="70.00", period="FY2025Q2", as_of_date=q2_as_of,
    )
    facts["fy2025q2_ebitda"] = _raw_fact(
        admin_user=admin_user, document=results_document, company=company,
        fact_type=RAW_EBITDA, value="28.00", period="FY2025Q2", as_of_date=q2_as_of,
    )
    facts["fy2025q2_pat"] = _raw_fact(
        admin_user=admin_user, document=results_document, company=company,
        fact_type=RAW_PAT, value="15.00", period="FY2025Q2", as_of_date=q2_as_of,
    )
    facts["fy2025q2_inventory"] = _raw_fact(
        admin_user=admin_user, document=results_document, company=company,
        fact_type=RAW_INVENTORY, value="40.00", period="FY2025Q2", as_of_date=q2_as_of,
    )
    facts["fy2025q2_receivables"] = _raw_fact(
        admin_user=admin_user, document=results_document, company=company,
        fact_type=RAW_TRADE_RECEIVABLES, value="25.00", period="FY2025Q2", as_of_date=q2_as_of,
    )
    facts["fy2025q2_payables"] = _raw_fact(
        admin_user=admin_user, document=results_document, company=company,
        fact_type=RAW_TRADE_PAYABLES, value="20.00", period="FY2025Q2", as_of_date=q2_as_of,
    )

    annual_as_of = date(2025, 3, 31)
    facts["fy2025_revenue"] = _raw_fact(
        admin_user=admin_user, document=annual_report, company=company,
        fact_type=RAW_REVENUE, value="460.00", period="FY2025", as_of_date=annual_as_of,
    )
    facts["fy2025_ebitda"] = _raw_fact(
        admin_user=admin_user, document=annual_report, company=company,
        fact_type=RAW_EBITDA, value="110.00", period="FY2025", as_of_date=annual_as_of,
    )
    facts["fy2025_pat"] = _raw_fact(
        admin_user=admin_user, document=annual_report, company=company,
        fact_type=RAW_PAT, value="60.00", period="FY2025", as_of_date=annual_as_of,
    )
    facts["fy2025_cfo"] = _raw_fact(
        admin_user=admin_user, document=annual_report, company=company,
        fact_type=RAW_CFO, value="90.00", period="FY2025", as_of_date=annual_as_of,
    )
    facts["fy2025_capex"] = _raw_fact(
        admin_user=admin_user, document=annual_report, company=company,
        fact_type=RAW_CAPEX, value="30.00", period="FY2025", as_of_date=annual_as_of,
    )
    facts["fy2025_total_assets"] = _raw_fact(
        admin_user=admin_user, document=annual_report, company=company,
        fact_type=RAW_TOTAL_ASSETS, value="500.00", period="FY2025", as_of_date=annual_as_of,
    )
    FinancialDiagnosticsService.ensure_normalized_metric_definitions()
    return facts


# ---------------------------------------------------------------------
# 1. Namespace
# ---------------------------------------------------------------------


def test_adjusted_namespace_is_accepted_and_invalid_value_still_rejected(app):
    db.session.add(
        FinancialMetricDefinition(
            slug="ebitda_excl_one_off",
            label="Adjusted EBITDA (excl. one-off items)",
            statement_section="INCOME_STATEMENT",
            namespace=FinancialMetricNamespace.ADJUSTED,
            standard_unit="INR_CRORE",
        )
    )
    db.session.commit()
    assert (
        db.session.scalar(
            sa.select(FinancialMetricDefinition).where(
                FinancialMetricDefinition.slug == "ebitda_excl_one_off"
            )
        ).namespace
        == "ADJUSTED"
    )

    db.session.add(
        FinancialMetricDefinition(
            slug="bogus_metric",
            label="Bogus",
            statement_section="INCOME_STATEMENT",
            namespace="NOT_A_REAL_NAMESPACE",
            standard_unit="INR_CRORE",
        )
    )
    with pytest.raises(sa.exc.StatementError) as exc_info:
        db.session.commit()
    assert isinstance(exc_info.value.orig, LookupError)
    db.session.rollback()


# ---------------------------------------------------------------------
# 2. Diagnostic correctness, all five groups
# ---------------------------------------------------------------------


def test_margins(admin_user, company, seeded_facts):
    gross = FinancialDiagnosticsService.compute_gross_margin(
        company_id=company.id, period="FY2025Q2", as_of_date=date(2024, 9, 30),
        created_by_user_id=admin_user.id,
    )
    assert Decimal(gross.value) == ((Decimal("120.00") - Decimal("70.00")) / Decimal("120.00") * 100).quantize(Decimal("0.0001"))

    ebitda_margin = FinancialDiagnosticsService.compute_ebitda_margin(
        company_id=company.id, period="FY2025Q2", as_of_date=date(2024, 9, 30),
        created_by_user_id=admin_user.id,
    )
    assert Decimal(ebitda_margin.value) == (Decimal("28.00") / Decimal("120.00") * 100).quantize(Decimal("0.0001"))

    pat_margin = FinancialDiagnosticsService.compute_pat_margin(
        company_id=company.id, period="FY2025Q2", as_of_date=date(2024, 9, 30),
        created_by_user_id=admin_user.id,
    )
    assert Decimal(pat_margin.value) == (Decimal("15.00") / Decimal("120.00") * 100).quantize(Decimal("0.0001"))


def test_cash_conversion_and_fcf_conversion(admin_user, company, seeded_facts):
    cfo_to_ebitda = FinancialDiagnosticsService.compute_cfo_to_ebitda(
        company_id=company.id, period="FY2025", as_of_date=date(2025, 3, 31),
        created_by_user_id=admin_user.id,
    )
    assert Decimal(cfo_to_ebitda.value) == (Decimal("90.00") / Decimal("110.00") * 100).quantize(Decimal("0.0001"))

    fcf = FinancialDiagnosticsService.compute_fcf_conversion(
        company_id=company.id, period="FY2025", as_of_date=date(2025, 3, 31),
        created_by_user_id=admin_user.id,
    )
    assert Decimal(fcf.value) == ((Decimal("90.00") - Decimal("30.00")) / Decimal("60.00") * 100).quantize(Decimal("0.0001"))


def test_working_capital_days_and_cash_conversion_cycle(admin_user, company, seeded_facts):
    period_days = FinancialDiagnosticsService._period_days("FY2025Q2")

    inventory_days = FinancialDiagnosticsService.compute_inventory_days(
        company_id=company.id, period="FY2025Q2", as_of_date=date(2024, 9, 30),
        created_by_user_id=admin_user.id,
    )
    assert Decimal(inventory_days.value) == (Decimal("40.00") / Decimal("70.00") * period_days).quantize(Decimal("0.0001"))

    receivable_days = FinancialDiagnosticsService.compute_receivable_days(
        company_id=company.id, period="FY2025Q2", as_of_date=date(2024, 9, 30),
        created_by_user_id=admin_user.id,
    )
    assert Decimal(receivable_days.value) == (Decimal("25.00") / Decimal("120.00") * period_days).quantize(Decimal("0.0001"))

    payable_days = FinancialDiagnosticsService.compute_payable_days(
        company_id=company.id, period="FY2025Q2", as_of_date=date(2024, 9, 30),
        created_by_user_id=admin_user.id,
    )
    assert Decimal(payable_days.value) == (Decimal("20.00") / Decimal("70.00") * period_days).quantize(Decimal("0.0001"))

    ccc = FinancialDiagnosticsService.compute_cash_conversion_cycle(
        company_id=company.id, period="FY2025Q2", as_of_date=date(2024, 9, 30),
        created_by_user_id=admin_user.id,
    )
    assert Decimal(ccc.value) == (
        Decimal(inventory_days.value) + Decimal(receivable_days.value) - Decimal(payable_days.value)
    )


def test_growth_yoy_and_qoq(admin_user, company, seeded_facts):
    revenue_yoy = FinancialDiagnosticsService.compute_revenue_growth_yoy(
        company_id=company.id, period="FY2025Q2", as_of_date=date(2024, 9, 30),
        created_by_user_id=admin_user.id,
    )
    assert Decimal(revenue_yoy.value) == ((Decimal("120.00") - Decimal("100.00")) / Decimal("100.00") * 100).quantize(Decimal("0.0001"))

    revenue_qoq = FinancialDiagnosticsService.compute_revenue_growth_qoq(
        company_id=company.id, period="FY2025Q2", as_of_date=date(2024, 9, 30),
        created_by_user_id=admin_user.id,
    )
    assert Decimal(revenue_qoq.value) == ((Decimal("120.00") - Decimal("110.00")) / Decimal("110.00") * 100).quantize(Decimal("0.0001"))

    ebitda_yoy = FinancialDiagnosticsService.compute_ebitda_growth_yoy(
        company_id=company.id, period="FY2025Q2", as_of_date=date(2024, 9, 30),
        created_by_user_id=admin_user.id,
    )
    assert Decimal(ebitda_yoy.value) == ((Decimal("28.00") - Decimal("20.00")) / Decimal("20.00") * 100).quantize(Decimal("0.0001"))


def test_accruals_ratio(admin_user, company, seeded_facts):
    accruals = FinancialDiagnosticsService.compute_accruals_ratio(
        company_id=company.id, period="FY2025", as_of_date=date(2025, 3, 31),
        created_by_user_id=admin_user.id,
    )
    assert Decimal(accruals.value) == ((Decimal("60.00") - Decimal("90.00")) / Decimal("500.00") * 100).quantize(Decimal("0.0001"))


# ---------------------------------------------------------------------
# 3. Period correctness (FiscalPeriod comparator resolution)
# ---------------------------------------------------------------------


def test_yoy_growth_resolves_the_correct_comparator_period(admin_user, company, seeded_facts):
    """FY2025Q2's YoY comparator must be FY2024Q2, not a hand-wired
    fiscal_year - 1 string concatenation that happens to look similar."""

    from app.services.fiscal_period import FiscalPeriod

    comparator = FiscalPeriod.parse("FY2025Q2").prior_year()
    assert comparator.label() == "FY2024Q2"

    # and QoQ crosses the fiscal-year boundary correctly
    q1_comparator = FiscalPeriod.parse("FY2025Q1").previous_quarter()
    assert q1_comparator.label() == "FY2024Q4"


# ---------------------------------------------------------------------
# 4 & 5. Missing-data absence / disclosure-cadence
# ---------------------------------------------------------------------


def test_cfo_dependent_metrics_are_absent_when_cfo_not_disclosed_for_the_period(
    admin_user, company, seeded_facts
):
    """FY2025Q2 has no CFO Fact (Chemplast, like most Indian filers,
    does not disclose quarterly cash flow) -- cfo_to_ebitda,
    fcf_conversion, and accruals_ratio must not be computed for it,
    not computed-with-a-null-value."""

    assert FinancialDiagnosticsService.compute_cfo_to_ebitda(
        company_id=company.id, period="FY2025Q2", as_of_date=date(2024, 9, 30),
        created_by_user_id=admin_user.id,
    ) is None
    assert FinancialDiagnosticsService.compute_fcf_conversion(
        company_id=company.id, period="FY2025Q2", as_of_date=date(2024, 9, 30),
        created_by_user_id=admin_user.id,
    ) is None
    assert FinancialDiagnosticsService.compute_accruals_ratio(
        company_id=company.id, period="FY2025Q2", as_of_date=date(2024, 9, 30),
        created_by_user_id=admin_user.id,
    ) is None

    from app.models.research_brain import ExtractedFact

    no_fact = db.session.scalar(
        sa.select(sa.func.count())
        .select_from(ExtractedFact)
        .where(
            ExtractedFact.company_id == company.id,
            ExtractedFact.fact_type.like("fin.norm.cfo%"),
        )
    )
    assert no_fact == 0


def test_growth_is_absent_without_a_comparator_period(admin_user, company, seeded_facts):
    """FY2024Q2 has no FY2023Q2 comparator -- growth must not be
    computed, not computed as a fabricated/zero value."""

    assert FinancialDiagnosticsService.compute_revenue_growth_yoy(
        company_id=company.id, period="FY2024Q2", as_of_date=date(2023, 9, 30),
        created_by_user_id=admin_user.id,
    ) is None


# ---------------------------------------------------------------------
# 6-9. Adjustment integrity: citation gate, reconciliation, direction,
# double-counting protection
# ---------------------------------------------------------------------


@pytest.fixture
def adjusted_metric_definition():
    definition = FinancialMetricDefinition(
        slug="ebitda_excl_one_offs",
        label="EBITDA excl. one-off items",
        statement_section="INCOME_STATEMENT",
        namespace=FinancialMetricNamespace.ADJUSTED,
        standard_unit="INR_CRORE",
    )
    db.session.add(definition)
    db.session.commit()
    return definition


def _concall_evidence(admin_user, concall_document, text):
    run = ResearchBrainService.create_extraction_run(
        document_id=concall_document.id, method="manual_pilot",
        extracted_by_user_id=admin_user.id,
    )
    return ResearchBrainService.record_evidence(
        extraction_run_id=run.id, document_id=concall_document.id,
        text_snippet=text, created_by_user_id=admin_user.id,
    )


def test_adjustment_citation_gate_rejects_a_non_disclosure_document(
    admin_user, company, seeded_facts, adjusted_metric_definition, presentation_document
):
    run = ResearchBrainService.create_extraction_run(
        document_id=presentation_document.id, method="manual_pilot",
        extracted_by_user_id=admin_user.id,
    )
    bad_evidence = ResearchBrainService.record_evidence(
        extraction_run_id=run.id, document_id=presentation_document.id,
        text_snippet="Slide mentions one-off cost", created_by_user_id=admin_user.id,
    )
    with pytest.raises(ResearchValidationError):
        FinancialDiagnosticsService.record_adjusted_fact(
            company_id=company.id,
            metric_slug="ebitda_excl_one_offs",
            base_fact_id=seeded_facts["fy2025_ebitda"].id,
            adjustment_items=[
                {
                    "amount": "6.00",
                    "direction": AdjustmentDirection.ADD_BACK,
                    "description": "one-off redundancy cost",
                    "justification_evidence_id": bad_evidence.id,
                }
            ],
            period="FY2025",
            as_of_date=date(2025, 3, 31),
            created_by_user_id=admin_user.id,
        )


def test_adjustment_reconciliation_direction_and_double_counting(
    admin_user, company, seeded_facts, adjusted_metric_definition, concall_document
):
    add_back_evidence = _concall_evidence(
        admin_user, concall_document,
        "We incurred a one-off redundancy cost of INR 6.00 Cr this year, "
        "which is not expected to recur.",
    )
    deduct_evidence = _concall_evidence(
        admin_user, concall_document,
        "Other income included a one-off gain of INR 4.00 Cr on sale of a "
        "surplus asset.",
    )

    fact = FinancialDiagnosticsService.record_adjusted_fact(
        company_id=company.id,
        metric_slug="ebitda_excl_one_offs",
        base_fact_id=seeded_facts["fy2025_ebitda"].id,
        adjustment_items=[
            {
                "amount": "6.00",
                "direction": AdjustmentDirection.ADD_BACK,
                "description": "one-off redundancy cost",
                "justification_evidence_id": add_back_evidence.id,
            },
            {
                "amount": "4.00",
                "direction": AdjustmentDirection.DEDUCT,
                "description": "one-off gain on asset sale",
                "justification_evidence_id": deduct_evidence.id,
            },
        ],
        period="FY2025",
        as_of_date=date(2025, 3, 31),
        created_by_user_id=admin_user.id,
    )

    # Reconciliation: 110 + 6 - 4 = 112, exactly.
    assert Decimal(fact.value) == Decimal("112.0000")
    assert fact.fact_type == "fin.adj.ebitda_excl_one_offs"

    derivation = ResearchCoverageService.get_fact_derivation(fact.id)
    assert derivation is not None

    # Direction: the add-back and deduction are each labeled correctly.
    justification_labels = {
        i.role_label for i in derivation.inputs if i.input_evidence_id is not None
    }
    assert any("redundancy cost" in label for label in justification_labels)
    assert any("gain on asset sale" in label for label in justification_labels)
    assert all(
        label.startswith("Adjustment justification: ") for label in justification_labels
    )

    # Double-counting protection: no input_fact_id repeats within this
    # derivation (there is exactly one -- the base figure).
    fact_input_ids = [i.input_fact_id for i in derivation.inputs if i.input_fact_id]
    assert len(fact_input_ids) == len(set(fact_input_ids)) == 1
    assert fact_input_ids[0] == seeded_facts["fy2025_ebitda"].id

    # The base figure's own value is independently verifiable as the
    # unadjusted reported number (110.00), never already-adjusted.
    assert seeded_facts["fy2025_ebitda"].value == "110.00"


def test_adjustment_rejects_a_missing_justification(
    admin_user, company, seeded_facts, adjusted_metric_definition
):
    with pytest.raises(ResearchValidationError):
        FinancialDiagnosticsService.record_adjusted_fact(
            company_id=company.id,
            metric_slug="ebitda_excl_one_offs",
            base_fact_id=seeded_facts["fy2025_ebitda"].id,
            adjustment_items=[
                {
                    "amount": "6.00",
                    "direction": AdjustmentDirection.ADD_BACK,
                    "description": "one-off redundancy cost",
                    "justification_evidence_id": None,
                }
            ],
            period="FY2025",
            as_of_date=date(2025, 3, 31),
            created_by_user_id=admin_user.id,
        )


def test_adjustment_rejects_adjusting_an_adjustment(
    admin_user, company, seeded_facts, adjusted_metric_definition, concall_document
):
    evidence = _concall_evidence(admin_user, concall_document, "one-off item disclosed")
    first = FinancialDiagnosticsService.record_adjusted_fact(
        company_id=company.id,
        metric_slug="ebitda_excl_one_offs",
        base_fact_id=seeded_facts["fy2025_ebitda"].id,
        adjustment_items=[
            {
                "amount": "6.00",
                "direction": AdjustmentDirection.ADD_BACK,
                "description": "one-off redundancy cost",
                "justification_evidence_id": evidence.id,
            }
        ],
        period="FY2025",
        as_of_date=date(2025, 3, 31),
        created_by_user_id=admin_user.id,
    )

    with pytest.raises(ResearchValidationError):
        FinancialDiagnosticsService.record_adjusted_fact(
            company_id=company.id,
            metric_slug="ebitda_excl_one_offs",
            base_fact_id=first.id,
            adjustment_items=[
                {
                    "amount": "1.00",
                    "direction": AdjustmentDirection.ADD_BACK,
                    "description": "stacking another adjustment",
                    "justification_evidence_id": evidence.id,
                }
            ],
            period="FY2025",
            as_of_date=date(2025, 3, 31),
            created_by_user_id=admin_user.id,
        )
