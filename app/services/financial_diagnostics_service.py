"""Phase 3 Slice B: deterministic financial diagnostics and analytical
adjustments.

Pure arithmetic over already-promoted ``ExtractedFact`` rows via the
existing ``ResearchBrainService.record_fact``/
``ResearchCoverageService.record_fact_derivation`` write paths -- no new
table, no AI/LLM dependency, no new write mechanism. Raw ingestion itself
(``fin.raw.*``) is out of scope here: this service only reads raw/
normalized Facts that already exist and computes `NORMALIZED`/`ADJUSTED`
Facts from them.

One governing rule for every diagnostic below: a diagnostic Fact is
computed from complete, actually-disclosed inputs for that exact period,
or it is not created at all. There is no partial, estimated,
interpolated, or zero-substituted computation anywhere in this module.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation

import sqlalchemy as sa

from app import db
from app.models.document import Document, DocumentType
from app.models.financial_metric_definition import FinancialMetricDefinition
from app.models.research_brain import Evidence, ExtractedFact
from app.models.research_types import AdjustmentDirection, FinancialMetricNamespace
from app.services.fiscal_period import FiscalPeriod
from app.services.research_brain_service import ResearchBrainService
from app.services.research_coverage_service import ResearchCoverageService
from app.utils.research_errors import ResearchNotFoundError, ResearchValidationError


# Raw input slugs this service reads (fact_type = "fin.raw.<slug>"). Not
# promoted by this service -- promotion stays the existing CandidateFinding
# path; these are the names that path's promoted Facts must use for this
# service's diagnostics to find them.
RAW_REVENUE = "revenue"
RAW_COST_OF_GOODS_SOLD = "cost_of_goods_sold"
RAW_EBITDA = "ebitda"
RAW_PAT = "pat"
RAW_CFO = "cash_flow_from_operations"
RAW_CAPEX = "capex"
RAW_INVENTORY = "inventory"
RAW_TRADE_RECEIVABLES = "trade_receivables"
RAW_TRADE_PAYABLES = "trade_payables"
RAW_TOTAL_ASSETS = "total_assets"

# Disclosure document types a valid adjustment justification may cite.
_VALID_JUSTIFICATION_DOCUMENT_TYPES = frozenset(
    {
        DocumentType.CONCALL,
        DocumentType.QUARTERLY_RESULTS,
        DocumentType.ANNUAL_REPORT,
    }
)

_JUSTIFICATION_PREFIX = "Adjustment justification: "


def _raw(slug: str) -> str:
    return f"fin.raw.{slug}"


def _norm(slug: str) -> str:
    return f"fin.norm.{slug}"


class FinancialDiagnosticsService:
    """Deterministic diagnostics over already-promoted Facts."""

    # -----------------------------------------------------------------
    # Controlled vocabulary (data inserts, never a migration)
    # -----------------------------------------------------------------

    _NORMALIZED_METRIC_DEFINITIONS: tuple[dict, ...] = (
        {"slug": "gross_margin_pct", "label": "Gross Margin", "statement_section": "INCOME_STATEMENT", "standard_unit": "PERCENT"},
        {"slug": "ebitda_margin_pct", "label": "EBITDA Margin", "statement_section": "INCOME_STATEMENT", "standard_unit": "PERCENT"},
        {"slug": "pat_margin_pct", "label": "PAT Margin", "statement_section": "INCOME_STATEMENT", "standard_unit": "PERCENT"},
        {"slug": "cfo_to_ebitda_pct", "label": "CFO / EBITDA", "statement_section": "CASH_FLOW_STATEMENT", "standard_unit": "PERCENT"},
        {"slug": "fcf_conversion_pct", "label": "Free Cash Flow Conversion", "statement_section": "CASH_FLOW_STATEMENT", "standard_unit": "PERCENT"},
        {"slug": "inventory_days", "label": "Inventory Days", "statement_section": "BALANCE_SHEET", "standard_unit": "DAYS"},
        {"slug": "receivable_days", "label": "Receivable Days", "statement_section": "BALANCE_SHEET", "standard_unit": "DAYS"},
        {"slug": "payable_days", "label": "Payable Days", "statement_section": "BALANCE_SHEET", "standard_unit": "DAYS"},
        {"slug": "cash_conversion_cycle_days", "label": "Cash Conversion Cycle", "statement_section": "BALANCE_SHEET", "standard_unit": "DAYS"},
        {"slug": "revenue_growth_yoy_pct", "label": "Revenue Growth (YoY)", "statement_section": "INCOME_STATEMENT", "standard_unit": "PERCENT"},
        {"slug": "revenue_growth_qoq_pct", "label": "Revenue Growth (QoQ)", "statement_section": "INCOME_STATEMENT", "standard_unit": "PERCENT"},
        {"slug": "ebitda_growth_yoy_pct", "label": "EBITDA Growth (YoY)", "statement_section": "INCOME_STATEMENT", "standard_unit": "PERCENT"},
        {"slug": "ebitda_growth_qoq_pct", "label": "EBITDA Growth (QoQ)", "statement_section": "INCOME_STATEMENT", "standard_unit": "PERCENT"},
        {"slug": "accruals_ratio_pct", "label": "Accruals Ratio", "statement_section": "CASH_FLOW_STATEMENT", "standard_unit": "PERCENT"},
    )

    @classmethod
    def ensure_normalized_metric_definitions(cls) -> None:
        """Idempotent data insert of the NORMALIZED diagnostic vocabulary.
        Never a migration -- same convention as ResearchDimension/
        PropositionStageType.
        """

        for item in cls._NORMALIZED_METRIC_DEFINITIONS:
            existing = db.session.scalar(
                sa.select(FinancialMetricDefinition).where(
                    FinancialMetricDefinition.slug == item["slug"]
                )
            )
            if existing is not None:
                continue
            db.session.add(
                FinancialMetricDefinition(
                    slug=item["slug"],
                    label=item["label"],
                    statement_section=item["statement_section"],
                    namespace=FinancialMetricNamespace.NORMALIZED,
                    standard_unit=item["standard_unit"],
                )
            )
        db.session.commit()

    # -----------------------------------------------------------------
    # Internal helpers
    # -----------------------------------------------------------------

    @classmethod
    def _current_fact(
        cls, company_id: str, fact_type: str, period: str
    ) -> ExtractedFact | None:
        superseded_ids = sa.select(ExtractedFact.supersedes_fact_id).where(
            ExtractedFact.supersedes_fact_id.is_not(None)
        )
        return db.session.scalar(
            sa.select(ExtractedFact)
            .where(
                ExtractedFact.company_id == company_id,
                ExtractedFact.fact_type == fact_type,
                ExtractedFact.period == period,
                ExtractedFact.id.not_in(superseded_ids),
            )
            .order_by(ExtractedFact.created_at.desc())
        )

    @classmethod
    def _decimal(cls, fact: ExtractedFact) -> Decimal:
        try:
            return Decimal(fact.value)
        except InvalidOperation:
            raise ResearchValidationError(
                {"value": [f"Fact {fact.id} has a non-numeric value"]}
            )

    @classmethod
    def _primary_evidence_id(cls, fact: ExtractedFact) -> str:
        links = fact.fact_evidence_links
        if not links:
            raise ResearchValidationError(
                {"fact_id": [f"Fact {fact.id} has no cited evidence"]}
            )
        return links[0].evidence_id

    @classmethod
    def _record_normalized(
        cls,
        *,
        company_id: str,
        slug: str,
        value: Decimal,
        period: str,
        as_of_date: date,
        created_by_user_id: str,
        formula_description: str,
        inputs: list[dict],
    ) -> ExtractedFact:
        """Create the NORMALIZED Fact plus its FactDerivation in one call --
        every calculated diagnostic in this service goes through here so
        the citation/derivation shape is identical across all of them.
        """

        primary_fact = next(
            (
                db.session.get(ExtractedFact, item["input_fact_id"])
                for item in inputs
                if item.get("input_fact_id")
            ),
            None,
        )
        evidence_ids = (
            [cls._primary_evidence_id(primary_fact)] if primary_fact else []
        )
        if not evidence_ids:
            raise ResearchValidationError(
                {"inputs": ["At least one Fact input is required"]}
            )

        fact = ResearchBrainService.record_fact(
            company_id=company_id,
            fact_type=_norm(slug),
            value_type="NUMERIC",
            value=str(value.quantize(Decimal("0.0001"))),
            period=period,
            as_of_date=as_of_date,
            evidence_ids=evidence_ids,
            created_by_user_id=created_by_user_id,
        )
        ResearchCoverageService.record_fact_derivation(
            derived_fact_id=fact.id,
            formula_description=formula_description,
            created_by_user_id=created_by_user_id,
            inputs=inputs,
        )
        return fact

    # -----------------------------------------------------------------
    # Margins
    # -----------------------------------------------------------------

    @classmethod
    def compute_gross_margin(
        cls, *, company_id: str, period: str, as_of_date: date, created_by_user_id: str
    ) -> ExtractedFact | None:
        revenue = cls._current_fact(company_id, _raw(RAW_REVENUE), period)
        cogs = cls._current_fact(company_id, _raw(RAW_COST_OF_GOODS_SOLD), period)
        if revenue is None or cogs is None:
            return None
        revenue_v, cogs_v = cls._decimal(revenue), cls._decimal(cogs)
        if revenue_v == 0:
            return None
        value = (revenue_v - cogs_v) / revenue_v * 100
        return cls._record_normalized(
            company_id=company_id,
            slug="gross_margin_pct",
            value=value,
            period=period,
            as_of_date=as_of_date,
            created_by_user_id=created_by_user_id,
            formula_description=(
                f"Gross margin % = (Revenue ({revenue_v}) - COGS ({cogs_v})) "
                f"/ Revenue ({revenue_v}) * 100 = {value.quantize(Decimal('0.0001'))}"
            ),
            inputs=[
                {"input_fact_id": revenue.id, "role_label": "Revenue"},
                {"input_fact_id": cogs.id, "role_label": "Cost of goods sold"},
            ],
        )

    @classmethod
    def _margin(
        cls,
        *,
        company_id: str,
        numerator_raw: str,
        slug: str,
        label: str,
        period: str,
        as_of_date: date,
        created_by_user_id: str,
    ) -> ExtractedFact | None:
        numerator = cls._current_fact(company_id, _raw(numerator_raw), period)
        revenue = cls._current_fact(company_id, _raw(RAW_REVENUE), period)
        if numerator is None or revenue is None:
            return None
        revenue_v = cls._decimal(revenue)
        if revenue_v == 0:
            return None
        numerator_v = cls._decimal(numerator)
        value = numerator_v / revenue_v * 100
        return cls._record_normalized(
            company_id=company_id,
            slug=slug,
            value=value,
            period=period,
            as_of_date=as_of_date,
            created_by_user_id=created_by_user_id,
            formula_description=(
                f"{label} % = {label} ({numerator_v}) / Revenue ({revenue_v}) "
                f"* 100 = {value.quantize(Decimal('0.0001'))}"
            ),
            inputs=[
                {"input_fact_id": numerator.id, "role_label": label},
                {"input_fact_id": revenue.id, "role_label": "Revenue"},
            ],
        )

    @classmethod
    def compute_ebitda_margin(cls, *, company_id, period, as_of_date, created_by_user_id):
        return cls._margin(
            company_id=company_id,
            numerator_raw=RAW_EBITDA,
            slug="ebitda_margin_pct",
            label="EBITDA",
            period=period,
            as_of_date=as_of_date,
            created_by_user_id=created_by_user_id,
        )

    @classmethod
    def compute_pat_margin(cls, *, company_id, period, as_of_date, created_by_user_id):
        return cls._margin(
            company_id=company_id,
            numerator_raw=RAW_PAT,
            slug="pat_margin_pct",
            label="PAT",
            period=period,
            as_of_date=as_of_date,
            created_by_user_id=created_by_user_id,
        )

    # -----------------------------------------------------------------
    # Cash conversion -- CFO is disclosed far less often than quarterly
    # P&L lines; these simply do not compute when CFO is absent for the
    # exact period asked for. No annual/quarterly special-casing is
    # needed beyond that -- "absent means absent" already covers it.
    # -----------------------------------------------------------------

    @classmethod
    def compute_cfo_to_ebitda(
        cls, *, company_id: str, period: str, as_of_date: date, created_by_user_id: str
    ) -> ExtractedFact | None:
        cfo = cls._current_fact(company_id, _raw(RAW_CFO), period)
        ebitda = cls._current_fact(company_id, _raw(RAW_EBITDA), period)
        if cfo is None or ebitda is None:
            return None
        ebitda_v = cls._decimal(ebitda)
        if ebitda_v == 0:
            return None
        cfo_v = cls._decimal(cfo)
        value = cfo_v / ebitda_v * 100
        return cls._record_normalized(
            company_id=company_id,
            slug="cfo_to_ebitda_pct",
            value=value,
            period=period,
            as_of_date=as_of_date,
            created_by_user_id=created_by_user_id,
            formula_description=(
                f"CFO/EBITDA % = CFO ({cfo_v}) / EBITDA ({ebitda_v}) * 100 "
                f"= {value.quantize(Decimal('0.0001'))}"
            ),
            inputs=[
                {"input_fact_id": cfo.id, "role_label": "Cash flow from operations"},
                {"input_fact_id": ebitda.id, "role_label": "EBITDA"},
            ],
        )

    @classmethod
    def compute_fcf_conversion(
        cls, *, company_id: str, period: str, as_of_date: date, created_by_user_id: str
    ) -> ExtractedFact | None:
        cfo = cls._current_fact(company_id, _raw(RAW_CFO), period)
        capex = cls._current_fact(company_id, _raw(RAW_CAPEX), period)
        pat = cls._current_fact(company_id, _raw(RAW_PAT), period)
        if cfo is None or capex is None or pat is None:
            return None
        pat_v = cls._decimal(pat)
        if pat_v == 0:
            return None
        cfo_v, capex_v = cls._decimal(cfo), cls._decimal(capex)
        value = (cfo_v - capex_v) / pat_v * 100
        return cls._record_normalized(
            company_id=company_id,
            slug="fcf_conversion_pct",
            value=value,
            period=period,
            as_of_date=as_of_date,
            created_by_user_id=created_by_user_id,
            formula_description=(
                f"FCF conversion % = (CFO ({cfo_v}) - Capex ({capex_v})) "
                f"/ PAT ({pat_v}) * 100 = {value.quantize(Decimal('0.0001'))}"
            ),
            inputs=[
                {"input_fact_id": cfo.id, "role_label": "Cash flow from operations"},
                {"input_fact_id": capex.id, "role_label": "Capital expenditure"},
                {"input_fact_id": pat.id, "role_label": "PAT"},
            ],
        )

    # -----------------------------------------------------------------
    # Working capital -- period_days always comes from FiscalPeriod's
    # real calendar span, never a hardcoded 365, so a half-year balance
    # sheet is never scaled as if it were a full year.
    # -----------------------------------------------------------------

    @classmethod
    def _period_days(cls, period: str) -> int:
        start, end = FiscalPeriod.parse(period).resolve()
        return (end - start).days + 1

    @classmethod
    def _working_capital_days(
        cls,
        *,
        company_id: str,
        balance_raw: str,
        scale_raw: str,
        slug: str,
        label: str,
        period: str,
        as_of_date: date,
        created_by_user_id: str,
    ) -> ExtractedFact | None:
        balance = cls._current_fact(company_id, _raw(balance_raw), period)
        scale = cls._current_fact(company_id, _raw(scale_raw), period)
        if balance is None or scale is None:
            return None
        scale_v = cls._decimal(scale)
        if scale_v == 0:
            return None
        balance_v = cls._decimal(balance)
        period_days = cls._period_days(period)
        value = balance_v / scale_v * period_days
        return cls._record_normalized(
            company_id=company_id,
            slug=slug,
            value=value,
            period=period,
            as_of_date=as_of_date,
            created_by_user_id=created_by_user_id,
            formula_description=(
                f"{label} = {label} balance ({balance_v}) / scale ({scale_v}) "
                f"* period days ({period_days}) = {value.quantize(Decimal('0.0001'))}"
            ),
            inputs=[
                {"input_fact_id": balance.id, "role_label": f"{label} balance"},
                {"input_fact_id": scale.id, "role_label": "Scale (Revenue/COGS)"},
            ],
        )

    @classmethod
    def compute_inventory_days(cls, *, company_id, period, as_of_date, created_by_user_id):
        return cls._working_capital_days(
            company_id=company_id,
            balance_raw=RAW_INVENTORY,
            scale_raw=RAW_COST_OF_GOODS_SOLD,
            slug="inventory_days",
            label="Inventory",
            period=period,
            as_of_date=as_of_date,
            created_by_user_id=created_by_user_id,
        )

    @classmethod
    def compute_receivable_days(cls, *, company_id, period, as_of_date, created_by_user_id):
        return cls._working_capital_days(
            company_id=company_id,
            balance_raw=RAW_TRADE_RECEIVABLES,
            scale_raw=RAW_REVENUE,
            slug="receivable_days",
            label="Receivable",
            period=period,
            as_of_date=as_of_date,
            created_by_user_id=created_by_user_id,
        )

    @classmethod
    def compute_payable_days(cls, *, company_id, period, as_of_date, created_by_user_id):
        return cls._working_capital_days(
            company_id=company_id,
            balance_raw=RAW_TRADE_PAYABLES,
            scale_raw=RAW_COST_OF_GOODS_SOLD,
            slug="payable_days",
            label="Payable",
            period=period,
            as_of_date=as_of_date,
            created_by_user_id=created_by_user_id,
        )

    @classmethod
    def compute_cash_conversion_cycle(
        cls, *, company_id: str, period: str, as_of_date: date, created_by_user_id: str
    ) -> ExtractedFact | None:
        inventory = cls._current_fact(company_id, _norm("inventory_days"), period)
        receivable = cls._current_fact(company_id, _norm("receivable_days"), period)
        payable = cls._current_fact(company_id, _norm("payable_days"), period)
        if inventory is None or receivable is None or payable is None:
            return None
        inv_v, rec_v, pay_v = (
            cls._decimal(inventory),
            cls._decimal(receivable),
            cls._decimal(payable),
        )
        value = inv_v + rec_v - pay_v
        return cls._record_normalized(
            company_id=company_id,
            slug="cash_conversion_cycle_days",
            value=value,
            period=period,
            as_of_date=as_of_date,
            created_by_user_id=created_by_user_id,
            formula_description=(
                f"Cash conversion cycle = Inventory days ({inv_v}) + "
                f"Receivable days ({rec_v}) - Payable days ({pay_v}) = "
                f"{value.quantize(Decimal('0.0001'))}"
            ),
            inputs=[
                {"input_fact_id": inventory.id, "role_label": "Inventory days"},
                {"input_fact_id": receivable.id, "role_label": "Receivable days"},
                {"input_fact_id": payable.id, "role_label": "Payable days"},
            ],
        )

    # -----------------------------------------------------------------
    # Growth & trend -- comparator resolved via FiscalPeriod, never a
    # hand-wired offset. Not computed if the comparator Fact is absent
    # or its value is <= 0 (percentage growth is degenerate there).
    # -----------------------------------------------------------------

    @classmethod
    def _growth(
        cls,
        *,
        company_id: str,
        raw_slug: str,
        slug: str,
        label: str,
        period: str,
        as_of_date: date,
        created_by_user_id: str,
        comparator: str,
    ) -> ExtractedFact | None:
        current = cls._current_fact(company_id, _raw(raw_slug), period)
        if current is None:
            return None
        parsed = FiscalPeriod.parse(period)
        comparator_period = (
            parsed.prior_year() if comparator == "YOY" else parsed.previous_quarter()
        )
        prior = cls._current_fact(
            company_id, _raw(raw_slug), comparator_period.label()
        )
        if prior is None:
            return None
        prior_v = cls._decimal(prior)
        if prior_v <= 0:
            return None
        current_v = cls._decimal(current)
        value = (current_v - prior_v) / prior_v * 100
        return cls._record_normalized(
            company_id=company_id,
            slug=slug,
            value=value,
            period=period,
            as_of_date=as_of_date,
            created_by_user_id=created_by_user_id,
            formula_description=(
                f"{label} growth ({comparator}) % = (Current ({current_v}) - "
                f"Comparator [{comparator_period.label()}] ({prior_v})) / "
                f"Comparator ({prior_v}) * 100 = "
                f"{value.quantize(Decimal('0.0001'))}"
            ),
            inputs=[
                {"input_fact_id": current.id, "role_label": f"{label} (current period)"},
                {
                    "input_fact_id": prior.id,
                    "role_label": f"{label} (comparator: {comparator_period.label()})",
                },
            ],
        )

    @classmethod
    def compute_revenue_growth_yoy(cls, *, company_id, period, as_of_date, created_by_user_id):
        return cls._growth(
            company_id=company_id, raw_slug=RAW_REVENUE, slug="revenue_growth_yoy_pct",
            label="Revenue", period=period, as_of_date=as_of_date,
            created_by_user_id=created_by_user_id, comparator="YOY",
        )

    @classmethod
    def compute_revenue_growth_qoq(cls, *, company_id, period, as_of_date, created_by_user_id):
        return cls._growth(
            company_id=company_id, raw_slug=RAW_REVENUE, slug="revenue_growth_qoq_pct",
            label="Revenue", period=period, as_of_date=as_of_date,
            created_by_user_id=created_by_user_id, comparator="QOQ",
        )

    @classmethod
    def compute_ebitda_growth_yoy(cls, *, company_id, period, as_of_date, created_by_user_id):
        return cls._growth(
            company_id=company_id, raw_slug=RAW_EBITDA, slug="ebitda_growth_yoy_pct",
            label="EBITDA", period=period, as_of_date=as_of_date,
            created_by_user_id=created_by_user_id, comparator="YOY",
        )

    @classmethod
    def compute_ebitda_growth_qoq(cls, *, company_id, period, as_of_date, created_by_user_id):
        return cls._growth(
            company_id=company_id, raw_slug=RAW_EBITDA, slug="ebitda_growth_qoq_pct",
            label="EBITDA", period=period, as_of_date=as_of_date,
            created_by_user_id=created_by_user_id, comparator="QOQ",
        )

    # -----------------------------------------------------------------
    # Earnings quality -- flags divergence magnitude only. Never paired
    # with an inferred explanation; an explanation requires its own
    # ADJUSTED Fact with its own citation (record_adjusted_fact below).
    # -----------------------------------------------------------------

    @classmethod
    def compute_accruals_ratio(
        cls, *, company_id: str, period: str, as_of_date: date, created_by_user_id: str
    ) -> ExtractedFact | None:
        pat = cls._current_fact(company_id, _raw(RAW_PAT), period)
        cfo = cls._current_fact(company_id, _raw(RAW_CFO), period)
        total_assets = cls._current_fact(company_id, _raw(RAW_TOTAL_ASSETS), period)
        if pat is None or cfo is None or total_assets is None:
            return None
        assets_v = cls._decimal(total_assets)
        if assets_v == 0:
            return None
        pat_v, cfo_v = cls._decimal(pat), cls._decimal(cfo)
        value = (pat_v - cfo_v) / assets_v * 100
        return cls._record_normalized(
            company_id=company_id,
            slug="accruals_ratio_pct",
            value=value,
            period=period,
            as_of_date=as_of_date,
            created_by_user_id=created_by_user_id,
            formula_description=(
                f"Accruals ratio % = (PAT ({pat_v}) - CFO ({cfo_v})) / "
                f"Total assets ({assets_v}) * 100 = "
                f"{value.quantize(Decimal('0.0001'))}. Flags the magnitude "
                f"of accrual-vs-cash earnings divergence only; no cause is "
                f"asserted."
            ),
            inputs=[
                {"input_fact_id": pat.id, "role_label": "PAT"},
                {"input_fact_id": cfo.id, "role_label": "Cash flow from operations"},
                {"input_fact_id": total_assets.id, "role_label": "Total assets"},
            ],
        )

    # -----------------------------------------------------------------
    # Analytical adjustments (ADJUSTED) -- reconciliation, direction, and
    # justification are guaranteed by construction (the service computes
    # the value itself from cited inputs; it never accepts a caller-
    # supplied adjusted value). Double-counting is prevented by
    # construction too: exactly one base Fact plus N distinct evidence-
    # cited deltas, never two inputs citing the same Fact.
    # -----------------------------------------------------------------

    @classmethod
    def record_adjusted_fact(
        cls,
        *,
        company_id: str,
        metric_slug: str,
        base_fact_id: str,
        adjustment_items: list[dict],
        period: str,
        as_of_date: date,
        created_by_user_id: str,
    ) -> ExtractedFact:
        """``adjustment_items``: each a dict with ``amount`` (str/Decimal),
        ``direction`` (``AdjustmentDirection.ADD_BACK``/``DEDUCT``),
        ``description`` (str), and ``justification_evidence_id`` (an
        ``Evidence`` id whose ``Document.document_type`` is CONCALL,
        QUARTERLY_RESULTS, or ANNUAL_REPORT).
        """

        definition = db.session.scalar(
            sa.select(FinancialMetricDefinition).where(
                FinancialMetricDefinition.slug == metric_slug
            )
        )
        if definition is None:
            raise ResearchNotFoundError(
                "financial_metric_definition_not_found",
                f"No FinancialMetricDefinition for slug {metric_slug!r} -- "
                "create it first (a data insert, never a migration)",
            )
        if definition.namespace != FinancialMetricNamespace.ADJUSTED:
            raise ResearchValidationError(
                {
                    "metric_slug": [
                        f"{metric_slug!r} is namespace "
                        f"{definition.namespace!r}, not ADJUSTED"
                    ]
                }
            )

        base_fact = db.session.get(ExtractedFact, base_fact_id)
        if base_fact is None or base_fact.company_id != company_id:
            raise ResearchNotFoundError(
                "extracted_fact_not_found",
                "Base Fact was not found for this company",
            )
        if base_fact.fact_type.startswith("fin.adj."):
            raise ResearchValidationError(
                {
                    "base_fact_id": [
                        "The base figure must be a RAW or NORMALIZED Fact, "
                        "not itself an adjustment -- adjusting an "
                        "adjustment is not supported"
                    ]
                }
            )

        if not adjustment_items:
            raise ResearchValidationError(
                {"adjustment_items": ["At least one adjustment item is required"]}
            )

        base_value = cls._decimal(base_fact)
        value = base_value
        inputs = [
            {
                "input_fact_id": base_fact.id,
                "role_label": f"Reported {definition.label} (pre-adjustment)",
            }
        ]
        formula_parts = [f"Adjusted {definition.label} = Reported ({base_value})"]

        for item in adjustment_items:
            direction = item.get("direction")
            if direction not in (
                AdjustmentDirection.ADD_BACK,
                AdjustmentDirection.DEDUCT,
            ):
                raise ResearchValidationError(
                    {
                        "direction": [
                            "Must be ADD_BACK (excluding a one-off expense) "
                            "or DEDUCT (excluding a one-off gain)"
                        ]
                    }
                )
            try:
                amount = Decimal(str(item.get("amount")))
            except InvalidOperation:
                raise ResearchValidationError({"amount": ["Must be numeric"]})

            evidence_id = item.get("justification_evidence_id")
            evidence = db.session.get(Evidence, evidence_id) if evidence_id else None
            if evidence is None:
                raise ResearchValidationError(
                    {
                        "justification_evidence_id": [
                            "A justification Evidence citation is required "
                            "for every adjustment item"
                        ]
                    }
                )
            document = db.session.get(Document, evidence.document_id)
            if (
                document is None
                or document.document_type not in _VALID_JUSTIFICATION_DOCUMENT_TYPES
            ):
                raise ResearchValidationError(
                    {
                        "justification_evidence_id": [
                            "The cited Evidence must come from a CONCALL, "
                            "QUARTERLY_RESULTS, or ANNUAL_REPORT document"
                        ]
                    }
                )

            description = item.get("description")
            if not description:
                raise ResearchValidationError(
                    {"description": ["Required for every adjustment item"]}
                )

            if direction == AdjustmentDirection.ADD_BACK:
                value += amount
                formula_parts.append(f"+ add-back {amount} ({description})")
            else:
                value -= amount
                formula_parts.append(f"- deduction {amount} ({description})")

            inputs.append(
                {
                    "input_evidence_id": evidence.id,
                    "role_label": f"{_JUSTIFICATION_PREFIX}{description}",
                }
            )

        formula_parts.append(f"= {value.quantize(Decimal('0.0001'))}")
        formula_description = " ".join(formula_parts)

        fact = ResearchBrainService.record_fact(
            company_id=company_id,
            fact_type=f"fin.adj.{metric_slug}",
            value_type="NUMERIC",
            value=str(value.quantize(Decimal("0.0001"))),
            period=period,
            as_of_date=as_of_date,
            evidence_ids=[inputs[1]["input_evidence_id"]],
            created_by_user_id=created_by_user_id,
        )
        ResearchCoverageService.record_fact_derivation(
            derived_fact_id=fact.id,
            formula_description=formula_description,
            created_by_user_id=created_by_user_id,
            inputs=inputs,
        )
        return fact
