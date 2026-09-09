"""Plan 3 Task 2: explicit company-detail response projections.

The presenter selects one explicit schema per access context. A schema never
dumps the full aggregate; unknown aggregate keys are ignored by Marshmallow,
so premium sections cannot accidentally reach the free projection.
"""

from __future__ import annotations

from datetime import datetime, timezone

from marshmallow import Schema, fields


class _UtcDateTime(fields.DateTime):
    """DateTime that treats naive stored UTC values as timezone-aware UTC."""

    def _serialize(self, value, attr, obj, **kwargs):
        if isinstance(value, datetime) and value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return super()._serialize(value, attr, obj, **kwargs)


class LockedSectionSchema(Schema):
    """The only locked-section metadata a response may reveal."""

    section = fields.Str(dump_only=True)
    required_tier = fields.Str(dump_only=True)


class AccessSchema(Schema):
    """Resolved projection access metadata."""

    tier = fields.Str(dump_only=True)
    locked_sections = fields.List(
        fields.Nested(LockedSectionSchema), dump_only=True
    )


class CompanySchema(Schema):
    """Free company identity projected from Company and its linked Ticker."""

    id = fields.Str(dump_only=True)
    name = fields.Str(dump_only=True)
    symbol = fields.Str(dump_only=True)
    exchange = fields.Str(dump_only=True)
    isin = fields.Str(dump_only=True)
    sector = fields.Str(dump_only=True, allow_none=True)
    industry = fields.Str(dump_only=True, allow_none=True)


class BusinessGroupSchema(Schema):
    """Free business-group label projection."""

    name = fields.Str(dump_only=True)


class MarketQuoteSchema(Schema):
    """Read-only current market quote projection."""

    cmp = fields.Float(dump_only=True, allow_none=True)
    as_of = _UtcDateTime(dump_only=True, allow_none=True)


class ResearchPointSchema(Schema):
    """One catalyst or risk in the current research projection."""

    id = fields.Str(dump_only=True)
    kind = fields.Str(dump_only=True)
    title = fields.Str(dump_only=True)
    detail = fields.Str(dump_only=True, allow_none=True)
    status = fields.Str(dump_only=True, allow_none=True)
    target_date = fields.Date(dump_only=True, allow_none=True)
    sort_order = fields.Int(dump_only=True)


class ResearchSchema(Schema):
    """Current immutable narrative research revision."""

    revision = fields.Int(dump_only=True)
    why_selected = fields.Str(dump_only=True)
    what_is_changing = fields.Str(dump_only=True, allow_none=True)
    business_journey = fields.Str(dump_only=True, allow_none=True)
    thesis = fields.Str(dump_only=True)
    catalysts = fields.List(
        fields.Nested(ResearchPointSchema), dump_only=True
    )
    risks = fields.List(
        fields.Nested(ResearchPointSchema), dump_only=True
    )
    thesis_invalidation = fields.Str(dump_only=True)


class ManagementSchema(Schema):
    """Current management assessment projection."""

    summary = fields.Str(dump_only=True, allow_none=True)
    quality = fields.Str(dump_only=True)
    rationale = fields.Str(dump_only=True, allow_none=True)
    evidence = fields.Str(dump_only=True, allow_none=True)


class GovernanceFlagSchema(Schema):
    """One active governance flag in the current projection."""

    id = fields.Str(dump_only=True)
    flag_type = fields.Str(dump_only=True)
    title = fields.Str(dump_only=True)
    severity = fields.Str(dump_only=True)
    status = fields.Str(dump_only=True)
    factual_evidence = fields.Str(dump_only=True)
    source_title = fields.Str(dump_only=True, allow_none=True)
    source_url_or_reference = fields.Str(dump_only=True)
    interpretation = fields.Str(dump_only=True)
    observed_on = fields.Date(dump_only=True, allow_none=True)
    resolved_on = fields.Date(dump_only=True, allow_none=True)


class GovernanceSchema(Schema):
    """Current governance status and active flags projection."""

    status = fields.Str(dump_only=True)
    flags = fields.List(
        fields.Nested(GovernanceFlagSchema), dump_only=True
    )


class OwnershipSchema(Schema):
    """Latest dated ownership snapshot projection."""

    as_of_date = fields.Date(dump_only=True)
    promoter_holding_pct = fields.Decimal(
        as_string=True, dump_only=True, allow_none=True
    )
    promoter_pledge_pct = fields.Decimal(
        as_string=True, dump_only=True, allow_none=True
    )
    notes = fields.Str(dump_only=True, allow_none=True)
    source_reference = fields.Str(dump_only=True, allow_none=True)


class MarketPlanSchema(Schema):
    """Current immutable market-plan revision projection."""

    revision = fields.Int(dump_only=True)
    currency = fields.Str(dump_only=True)
    accumulation_low = fields.Decimal(as_string=True, dump_only=True)
    accumulation_high = fields.Decimal(as_string=True, dump_only=True)
    preferred_accumulation_price = fields.Decimal(
        as_string=True, dump_only=True, allow_none=True
    )
    supply_low = fields.Decimal(
        as_string=True, dump_only=True, allow_none=True
    )
    supply_high = fields.Decimal(
        as_string=True, dump_only=True, allow_none=True
    )
    invalidation_level = fields.Decimal(as_string=True, dump_only=True)
    rationale = fields.Str(dump_only=True, allow_none=True)
    effective_at = _UtcDateTime(dump_only=True)


class ForecastLineSchema(Schema):
    """One administrator-supplied fiscal-year forecast line."""

    fiscal_year = fields.Int(dump_only=True)
    is_estimate = fields.Bool(dump_only=True)
    revenue = fields.Decimal(
        as_string=True, dump_only=True, allow_none=True
    )
    ebitda = fields.Decimal(
        as_string=True, dump_only=True, allow_none=True
    )
    pat = fields.Decimal(
        as_string=True, dump_only=True, allow_none=True
    )
    ebitda_margin_pct = fields.Decimal(
        as_string=True, dump_only=True, allow_none=True
    )
    eps = fields.Decimal(
        as_string=True, dump_only=True, allow_none=True
    )
    currency = fields.Str(dump_only=True)
    unit = fields.Str(dump_only=True)


class ForecastSchema(Schema):
    """Current immutable forecast revision and lines."""

    revision = fields.Int(dump_only=True)
    as_of_date = fields.Date(dump_only=True)
    assumptions = fields.Str(dump_only=True, allow_none=True)
    lines = fields.List(
        fields.Nested(ForecastLineSchema), dump_only=True
    )


class ValuationReferenceLineSchema(Schema):
    """One immutable valuation reference/context input line."""

    reference_forecast_revision_id = fields.Str(
        dump_only=True, allow_none=True
    )
    reference_fiscal_year = fields.Int(dump_only=True, allow_none=True)
    reference_metric = fields.Str(dump_only=True)
    reference_metric_value = fields.Decimal(
        as_string=True, dump_only=True
    )
    reference_metric_unit = fields.Str(dump_only=True)
    reference_metric_basis = fields.Str(dump_only=True)
    sort_order = fields.Int(dump_only=True)


class ValuationSchema(Schema):
    """One current immutable valuation revision for its method."""

    revision = fields.Int(dump_only=True)
    valuation_method = fields.Str(dump_only=True)
    justified_multiple = fields.Decimal(
        as_string=True, dump_only=True, allow_none=True
    )
    implied_enterprise_value = fields.Decimal(
        as_string=True, dump_only=True, allow_none=True
    )
    net_debt = fields.Decimal(
        as_string=True, dump_only=True, allow_none=True
    )
    other_equity_adjustment = fields.Decimal(
        as_string=True, dump_only=True, allow_none=True
    )
    implied_future_equity_value = fields.Decimal(
        as_string=True, dump_only=True, allow_none=True
    )
    required_return_pct = fields.Decimal(
        as_string=True, dump_only=True, allow_none=True
    )
    discount_period_years = fields.Decimal(
        as_string=True, dump_only=True, allow_none=True
    )
    present_value = fields.Decimal(
        as_string=True, dump_only=True, allow_none=True
    )
    current_market_cap = fields.Decimal(
        as_string=True, dump_only=True, allow_none=True
    )
    currency = fields.Str(dump_only=True, allow_none=True)
    unit = fields.Str(dump_only=True, allow_none=True)
    valuation_notes = fields.Str(dump_only=True, allow_none=True)
    as_of_date = fields.Date(dump_only=True)
    reference_lines = fields.List(
        fields.Nested(ValuationReferenceLineSchema), dump_only=True
    )


class _CompanyDetailSchema(Schema):
    """Shared explicit company-detail fields."""

    company = fields.Nested(CompanySchema, dump_only=True)
    business_group = fields.Nested(
        BusinessGroupSchema, dump_only=True, allow_none=True
    )
    market_quote = fields.Nested(MarketQuoteSchema, dump_only=True)
    access = fields.Nested(AccessSchema, dump_only=True)


class CompanyFreeSchema(_CompanyDetailSchema):
    """Free company detail: company, assigned group label, quote, access."""


class CompanyPremiumSchema(_CompanyDetailSchema):
    """Premium company detail: every current premium research section."""

    research = fields.Nested(ResearchSchema, dump_only=True, allow_none=True)
    management = fields.Nested(
        ManagementSchema, dump_only=True, allow_none=True
    )
    governance = fields.Nested(
        GovernanceSchema, dump_only=True, allow_none=True
    )
    ownership = fields.Nested(
        OwnershipSchema, dump_only=True, allow_none=True
    )
    market_plan = fields.Nested(
        MarketPlanSchema, dump_only=True, allow_none=True
    )
    forecast = fields.Nested(
        ForecastSchema, dump_only=True, allow_none=True
    )
    valuations = fields.List(
        fields.Nested(ValuationSchema), dump_only=True, allow_none=True
    )


class CompanyAdminSchema(CompanyPremiumSchema):
    """Administrative company detail: full research with ADMIN access."""
