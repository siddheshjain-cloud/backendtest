"""Plan 5 Task 3: strict administrative research request and response schemas.

Every create/update request schema raises on unknown fields and excludes
server-owned identifiers, actor fields, timestamps, revision numbers, and the
entitlement product code. Patch schemas intentionally omit ``load_default``
for most fields so routes and services can distinguish an omitted field from
an explicitly supplied value.
"""

from __future__ import annotations

from datetime import datetime, timezone

from marshmallow import RAISE, Schema, ValidationError, fields, validate

from app.models.research_types import (
    EntitlementStatus,
    GovernanceFlagStatus,
    GovernanceSeverity,
    GovernanceStatus,
    ManagementQuality,
    ResearchPointKind,
    ResearchTier,
    ValuationMethod,
)
from app.utils.research_errors import ResearchValidationError


def _flatten_validation_messages(messages: object) -> dict[str, list[str]]:
    """Normalize nested Marshmallow validation messages for the research 400."""

    details: dict[str, list[str]] = {}

    if not isinstance(messages, dict):
        return {"payload": ["Must be a JSON object"]}

    for field, value in messages.items():
        if isinstance(value, list):
            details[field] = [str(item) for item in value]
        elif isinstance(value, dict):
            for nested_field, nested_value in value.items():
                key = f"{field}.{nested_field}"
                if isinstance(nested_value, list):
                    details[key] = [str(item) for item in nested_value]
                else:
                    details[key] = [str(nested_value)]
        else:
            details[field] = [str(value)]

    return details


def load_admin_payload(schema: Schema, payload: object) -> dict:
    """Load a strict request payload and translate Marshmallow failures."""

    if not isinstance(payload, dict):
        raise ResearchValidationError(
            {"payload": ["Must be a JSON object"]}
        )

    try:
        return schema.load(payload)
    except ValidationError as exc:
        raise ResearchValidationError(
            _flatten_validation_messages(exc.messages)
        ) from exc


class _UtcDateTime(fields.DateTime):
    """Serialize naive SQLite UTC datetimes as timezone-aware UTC."""

    def _serialize(self, value, attr, obj, **kwargs):
        if isinstance(value, datetime) and value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return super()._serialize(value, attr, obj, **kwargs)


class _StrictRequestSchema(Schema):
    class Meta:
        unknown = RAISE


class CompanyCreateSchema(_StrictRequestSchema):
    ticker_id = fields.Str(
        required=True, validate=validate.Length(max=64)
    )
    legal_name = fields.Str(
        required=True, validate=validate.Length(max=200)
    )
    display_name = fields.Str(
        load_default=None, allow_none=True, validate=validate.Length(max=200)
    )
    isin = fields.Str(
        required=True, validate=validate.Length(max=12)
    )
    sector = fields.Str(
        load_default=None, allow_none=True, validate=validate.Length(max=100)
    )
    industry = fields.Str(
        load_default=None, allow_none=True, validate=validate.Length(max=100)
    )
    business_group_id = fields.Str(
        load_default=None, allow_none=True, validate=validate.Length(max=36)
    )
    business_group_basis = fields.Str(
        load_default=None, allow_none=True, validate=validate.Length(max=200)
    )
    business_group_source_reference = fields.Str(
        load_default=None, allow_none=True, validate=validate.Length(max=1000)
    )


class CompanyPatchSchema(_StrictRequestSchema):
    ticker_id = fields.Str(
        allow_none=True, validate=validate.Length(max=64)
    )
    legal_name = fields.Str(
        allow_none=True, validate=validate.Length(max=200)
    )
    display_name = fields.Str(
        allow_none=True, validate=validate.Length(max=200)
    )
    isin = fields.Str(
        allow_none=True, validate=validate.Length(max=12)
    )
    sector = fields.Str(
        allow_none=True, validate=validate.Length(max=100)
    )
    industry = fields.Str(
        allow_none=True, validate=validate.Length(max=100)
    )
    business_group_id = fields.Str(
        allow_none=True, validate=validate.Length(max=36)
    )
    business_group_basis = fields.Str(
        allow_none=True, validate=validate.Length(max=200)
    )
    business_group_source_reference = fields.Str(
        allow_none=True, validate=validate.Length(max=1000)
    )


class EntitlementPutSchema(_StrictRequestSchema):
    tier = fields.Str(
        required=True,
        validate=validate.OneOf((ResearchTier.FREE, ResearchTier.PREMIUM)),
    )
    status = fields.Str(
        required=True,
        validate=validate.OneOf(
            (
                EntitlementStatus.ACTIVE,
                EntitlementStatus.INACTIVE,
                EntitlementStatus.REVOKED,
            )
        ),
    )
    valid_from = fields.DateTime(load_default=None, allow_none=True)
    valid_until = fields.DateTime(load_default=None, allow_none=True)


class OwnershipSnapshotCreateSchema(_StrictRequestSchema):
    as_of_date = fields.Date(required=True)
    promoter_holding_pct = fields.Decimal(
        load_default=None, allow_none=True
    )
    promoter_pledge_pct = fields.Decimal(
        load_default=None, allow_none=True
    )
    notes = fields.Str(
        load_default=None, allow_none=True, validate=validate.Length(max=4000)
    )
    source_reference = fields.Str(
        load_default=None, allow_none=True, validate=validate.Length(max=1000)
    )


class GovernanceFlagCreateSchema(_StrictRequestSchema):
    flag_type = fields.Str(
        required=True, validate=validate.Length(max=100)
    )
    title = fields.Str(
        required=True, validate=validate.Length(max=300)
    )
    severity = fields.Str(
        required=True,
        validate=validate.OneOf(
            (
                GovernanceSeverity.INFO,
                GovernanceSeverity.LOW,
                GovernanceSeverity.MEDIUM,
                GovernanceSeverity.HIGH,
                GovernanceSeverity.CRITICAL,
            )
        ),
    )
    status = fields.Str(
        required=True,
        validate=validate.OneOf(
            (
                GovernanceFlagStatus.OPEN,
                GovernanceFlagStatus.MONITORING,
                GovernanceFlagStatus.RESOLVED,
                GovernanceFlagStatus.DISMISSED,
            )
        ),
    )
    factual_evidence = fields.Str(
        required=True, validate=validate.Length(max=4000)
    )
    source_title = fields.Str(
        load_default=None, allow_none=True, validate=validate.Length(max=300)
    )
    source_url_or_reference = fields.Str(
        required=True, validate=validate.Length(max=1000)
    )
    interpretation = fields.Str(
        required=True, validate=validate.Length(max=4000)
    )
    observed_on = fields.Date(load_default=None, allow_none=True)
    resolved_on = fields.Date(load_default=None, allow_none=True)


class GovernanceFlagPatchSchema(_StrictRequestSchema):
    flag_type = fields.Str(allow_none=True, validate=validate.Length(max=100))
    title = fields.Str(allow_none=True, validate=validate.Length(max=300))
    severity = fields.Str(
        allow_none=True,
        validate=validate.OneOf(
            (
                GovernanceSeverity.INFO,
                GovernanceSeverity.LOW,
                GovernanceSeverity.MEDIUM,
                GovernanceSeverity.HIGH,
                GovernanceSeverity.CRITICAL,
            )
        ),
    )
    status = fields.Str(
        allow_none=True,
        validate=validate.OneOf(
            (
                GovernanceFlagStatus.OPEN,
                GovernanceFlagStatus.MONITORING,
                GovernanceFlagStatus.RESOLVED,
                GovernanceFlagStatus.DISMISSED,
            )
        ),
    )
    factual_evidence = fields.Str(
        allow_none=True, validate=validate.Length(max=4000)
    )
    source_title = fields.Str(
        allow_none=True, validate=validate.Length(max=300)
    )
    source_url_or_reference = fields.Str(
        allow_none=True, validate=validate.Length(max=1000)
    )
    interpretation = fields.Str(
        allow_none=True, validate=validate.Length(max=4000)
    )
    observed_on = fields.Date(allow_none=True)
    resolved_on = fields.Date(allow_none=True)
    archived = fields.Boolean(allow_none=True)


class DisclosureCreateSchema(_StrictRequestSchema):
    event_type = fields.Str(
        required=True, validate=validate.Length(max=64)
    )
    event_date = fields.Date(required=True)
    title = fields.Str(
        required=True, validate=validate.Length(max=300)
    )
    original_source_url_or_reference = fields.Str(
        required=True, validate=validate.Length(max=1000)
    )
    exchange_reference = fields.Str(
        load_default=None, allow_none=True, validate=validate.Length(max=200)
    )
    significance_note = fields.Str(
        load_default=None, allow_none=True, validate=validate.Length(max=4000)
    )
    is_key = fields.Boolean(load_default=False)
    document_id = fields.Raw(load_default=None, allow_none=True)


class DisclosurePatchSchema(_StrictRequestSchema):
    event_type = fields.Str(allow_none=True, validate=validate.Length(max=64))
    event_date = fields.Date(allow_none=True)
    title = fields.Str(allow_none=True, validate=validate.Length(max=300))
    original_source_url_or_reference = fields.Str(
        allow_none=True, validate=validate.Length(max=1000)
    )
    exchange_reference = fields.Str(
        allow_none=True, validate=validate.Length(max=200)
    )
    significance_note = fields.Str(
        allow_none=True, validate=validate.Length(max=4000)
    )
    is_key = fields.Boolean(allow_none=True)
    document_id = fields.Raw(allow_none=True)
    archived = fields.Boolean(allow_none=True)


class InstitutionCreateSchema(_StrictRequestSchema):
    name = fields.Str(
        required=True, validate=validate.Length(max=200)
    )
    website = fields.Str(
        load_default=None, allow_none=True, validate=validate.Length(max=1000)
    )


class InstitutionPatchSchema(_StrictRequestSchema):
    name = fields.Str(allow_none=True, validate=validate.Length(max=200))
    website = fields.Str(
        allow_none=True, validate=validate.Length(max=1000)
    )


class CompanyResponseSchema(Schema):
    id = fields.Str(dump_only=True)
    ticker_id = fields.Str(dump_only=True)
    legal_name = fields.Str(dump_only=True)
    display_name = fields.Str(dump_only=True, allow_none=True)
    isin = fields.Str(dump_only=True)
    sector = fields.Str(dump_only=True, allow_none=True)
    industry = fields.Str(dump_only=True, allow_none=True)
    business_group_id = fields.Str(dump_only=True, allow_none=True)
    business_group_basis = fields.Str(dump_only=True, allow_none=True)
    business_group_source_reference = fields.Str(
        dump_only=True, allow_none=True
    )
    created_at = _UtcDateTime(dump_only=True)
    updated_at = _UtcDateTime(dump_only=True)


class EntitlementResponseSchema(Schema):
    id = fields.Str(dump_only=True)
    user_id = fields.Str(dump_only=True)
    product_code = fields.Str(dump_only=True)
    tier = fields.Str(dump_only=True)
    status = fields.Str(dump_only=True)
    valid_from = _UtcDateTime(dump_only=True, allow_none=True)
    valid_until = _UtcDateTime(dump_only=True, allow_none=True)
    created_at = _UtcDateTime(dump_only=True)
    updated_at = _UtcDateTime(dump_only=True)


class OwnershipSnapshotResponseSchema(Schema):
    id = fields.Str(dump_only=True)
    company_id = fields.Str(dump_only=True)
    as_of_date = fields.Date(dump_only=True)
    promoter_holding_pct = fields.Decimal(
        as_string=True, dump_only=True, allow_none=True
    )
    promoter_pledge_pct = fields.Decimal(
        as_string=True, dump_only=True, allow_none=True
    )
    notes = fields.Str(dump_only=True, allow_none=True)
    source_reference = fields.Str(dump_only=True, allow_none=True)
    created_at = _UtcDateTime(dump_only=True)


class GovernanceFlagResponseSchema(Schema):
    id = fields.Str(dump_only=True)
    company_id = fields.Str(dump_only=True)
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
    archived_at = _UtcDateTime(dump_only=True, allow_none=True)
    created_at = _UtcDateTime(dump_only=True)
    updated_at = _UtcDateTime(dump_only=True)


class DisclosureResponseSchema(Schema):
    id = fields.Str(dump_only=True)
    company_id = fields.Str(dump_only=True)
    event_type = fields.Str(dump_only=True)
    event_date = fields.Date(dump_only=True)
    title = fields.Str(dump_only=True)
    original_source_url_or_reference = fields.Str(dump_only=True)
    exchange_reference = fields.Str(dump_only=True, allow_none=True)
    significance_note = fields.Str(dump_only=True, allow_none=True)
    is_key = fields.Boolean(dump_only=True)
    document_id = fields.Str(dump_only=True, allow_none=True)
    archived_at = _UtcDateTime(dump_only=True, allow_none=True)
    created_at = _UtcDateTime(dump_only=True)
    updated_at = _UtcDateTime(dump_only=True)


class InstitutionResponseSchema(Schema):
    id = fields.Str(dump_only=True)
    name = fields.Str(dump_only=True)
    website = fields.Str(dump_only=True, allow_none=True)
    created_at = _UtcDateTime(dump_only=True)
    updated_at = _UtcDateTime(dump_only=True)


class ResearchPointCreateSchema(_StrictRequestSchema):
    kind = fields.Str(
        required=True,
        validate=validate.OneOf(
            (ResearchPointKind.CATALYST, ResearchPointKind.RISK)
        ),
    )
    title = fields.Str(
        required=True, validate=validate.Length(max=300)
    )
    detail = fields.Str(
        load_default=None, allow_none=True, validate=validate.Length(max=4000)
    )
    status = fields.Str(
        load_default=None, allow_none=True, validate=validate.Length(max=100)
    )
    target_date = fields.Date(load_default=None, allow_none=True)
    sort_order = fields.Int(
        required=True, validate=validate.Range(min=0)
    )


class ResearchRevisionCreateSchema(_StrictRequestSchema):
    base_revision_id = fields.Str(
        load_default=None, allow_none=True, validate=validate.Length(max=36)
    )
    why_selected = fields.Str(
        required=True, validate=validate.Length(max=4000)
    )
    what_is_changing = fields.Str(
        load_default=None, allow_none=True, validate=validate.Length(max=4000)
    )
    business_journey = fields.Str(
        load_default=None, allow_none=True, validate=validate.Length(max=4000)
    )
    thesis = fields.Str(
        required=True, validate=validate.Length(max=4000)
    )
    thesis_invalidation = fields.Str(
        required=True, validate=validate.Length(max=4000)
    )
    management_summary = fields.Str(
        load_default=None, allow_none=True, validate=validate.Length(max=4000)
    )
    management_quality = fields.Str(
        required=True,
        validate=validate.OneOf(
            (
                ManagementQuality.UNASSESSED,
                ManagementQuality.WEAK,
                ManagementQuality.WATCH,
                ManagementQuality.ACCEPTABLE,
                ManagementQuality.STRONG,
            )
        ),
    )
    management_rationale = fields.Str(
        load_default=None, allow_none=True, validate=validate.Length(max=4000)
    )
    management_evidence = fields.Str(
        load_default=None, allow_none=True, validate=validate.Length(max=4000)
    )
    governance_status = fields.Str(
        required=True,
        validate=validate.OneOf(
            (
                GovernanceStatus.UNREVIEWED,
                GovernanceStatus.CLEAR,
                GovernanceStatus.WATCH,
                GovernanceStatus.HIGH_RISK,
            )
        ),
    )
    change_reason = fields.Str(
        load_default=None, allow_none=True, validate=validate.Length(max=2000)
    )
    effective_at = fields.DateTime(required=True)
    points = fields.List(
        fields.Nested(ResearchPointCreateSchema),
        load_default=list,
    )


class MarketPlanRevisionCreateSchema(_StrictRequestSchema):
    base_revision_id = fields.Str(
        load_default=None, allow_none=True, validate=validate.Length(max=36)
    )
    currency = fields.Str(
        load_default="INR", validate=validate.Length(max=3)
    )
    accumulation_low = fields.Decimal(required=True)
    accumulation_high = fields.Decimal(required=True)
    preferred_accumulation_price = fields.Decimal(
        load_default=None, allow_none=True
    )
    supply_low = fields.Decimal(load_default=None, allow_none=True)
    supply_high = fields.Decimal(load_default=None, allow_none=True)
    invalidation_level = fields.Decimal(required=True)
    rationale = fields.Str(
        load_default=None, allow_none=True, validate=validate.Length(max=4000)
    )
    effective_at = fields.DateTime(required=True)
    change_reason = fields.Str(
        load_default=None, allow_none=True, validate=validate.Length(max=2000)
    )


class ForecastLineCreateSchema(_StrictRequestSchema):
    fiscal_year = fields.Int(
        required=True, validate=validate.Range(min=1000, max=9999)
    )
    is_estimate = fields.Boolean(required=True)
    revenue = fields.Decimal(load_default=None, allow_none=True)
    ebitda = fields.Decimal(load_default=None, allow_none=True)
    pat = fields.Decimal(load_default=None, allow_none=True)
    ebitda_margin_pct = fields.Decimal(load_default=None, allow_none=True)
    eps = fields.Decimal(load_default=None, allow_none=True)
    currency = fields.Str(
        load_default="INR", validate=validate.Length(max=3)
    )
    unit = fields.Str(
        required=True,
        validate=validate.OneOf(
            ("ABSOLUTE", "THOUSAND", "LAKH", "CRORE", "MILLION")
        ),
    )


class ForecastRevisionCreateSchema(_StrictRequestSchema):
    base_revision_id = fields.Str(
        load_default=None, allow_none=True, validate=validate.Length(max=36)
    )
    as_of_date = fields.Date(required=True)
    assumptions = fields.Str(
        load_default=None, allow_none=True, validate=validate.Length(max=4000)
    )
    change_reason = fields.Str(
        load_default=None, allow_none=True, validate=validate.Length(max=2000)
    )
    lines = fields.List(
        fields.Nested(ForecastLineCreateSchema),
        load_default=list,
    )


class ValuationReferenceLineCreateSchema(_StrictRequestSchema):
    reference_forecast_revision_id = fields.Str(
        load_default=None, allow_none=True, validate=validate.Length(max=36)
    )
    reference_fiscal_year = fields.Int(
        load_default=None, allow_none=True
    )
    reference_metric = fields.Str(
        required=True, validate=validate.Length(max=64)
    )
    reference_metric_value = fields.Decimal(required=True)
    reference_metric_unit = fields.Str(
        required=True, validate=validate.Length(max=64)
    )
    reference_metric_basis = fields.Str(
        required=True, validate=validate.Length(max=4000)
    )
    sort_order = fields.Int(
        required=True, validate=validate.Range(min=0)
    )


class ValuationRevisionCreateSchema(_StrictRequestSchema):
    base_revision_id = fields.Str(
        load_default=None, allow_none=True, validate=validate.Length(max=36)
    )
    valuation_method = fields.Str(
        required=True,
        validate=validate.OneOf(
            (
                ValuationMethod.PE,
                ValuationMethod.EV_EBITDA,
                ValuationMethod.PB,
                ValuationMethod.NAV,
                ValuationMethod.SOTP,
                ValuationMethod.ASSET_VALUE,
                ValuationMethod.UNIT_BASED,
                ValuationMethod.OTHER,
            )
        ),
    )
    justified_multiple = fields.Decimal(load_default=None, allow_none=True)
    implied_enterprise_value = fields.Decimal(
        load_default=None, allow_none=True
    )
    net_debt = fields.Decimal(load_default=None, allow_none=True)
    other_equity_adjustment = fields.Decimal(
        load_default=None, allow_none=True
    )
    implied_future_equity_value = fields.Decimal(
        load_default=None, allow_none=True
    )
    required_return_pct = fields.Decimal(load_default=None, allow_none=True)
    discount_period_years = fields.Decimal(
        load_default=None, allow_none=True
    )
    present_value = fields.Decimal(load_default=None, allow_none=True)
    current_market_cap = fields.Decimal(load_default=None, allow_none=True)
    currency = fields.Str(
        load_default=None, allow_none=True, validate=validate.Length(max=3)
    )
    unit = fields.Str(
        load_default=None, allow_none=True, validate=validate.Length(max=20)
    )
    valuation_notes = fields.Str(
        load_default=None, allow_none=True, validate=validate.Length(max=4000)
    )
    as_of_date = fields.Date(required=True)
    change_reason = fields.Str(
        load_default=None, allow_none=True, validate=validate.Length(max=2000)
    )
    reference_lines = fields.List(
        fields.Nested(ValuationReferenceLineCreateSchema),
        load_default=list,
    )


class ResearchPointResponseSchema(Schema):
    id = fields.Str(dump_only=True)
    kind = fields.Str(dump_only=True)
    title = fields.Str(dump_only=True)
    detail = fields.Str(dump_only=True, allow_none=True)
    status = fields.Str(dump_only=True, allow_none=True)
    target_date = fields.Date(dump_only=True, allow_none=True)
    sort_order = fields.Int(dump_only=True)


class ResearchRevisionResponseSchema(Schema):
    id = fields.Str(dump_only=True)
    company_id = fields.Str(dump_only=True)
    revision_number = fields.Int(dump_only=True)
    supersedes_revision_id = fields.Str(dump_only=True, allow_none=True)
    why_selected = fields.Str(dump_only=True)
    what_is_changing = fields.Str(dump_only=True, allow_none=True)
    business_journey = fields.Str(dump_only=True, allow_none=True)
    thesis = fields.Str(dump_only=True)
    thesis_invalidation = fields.Str(dump_only=True)
    management_summary = fields.Str(dump_only=True, allow_none=True)
    management_quality = fields.Str(dump_only=True)
    management_rationale = fields.Str(dump_only=True, allow_none=True)
    management_evidence = fields.Str(dump_only=True, allow_none=True)
    governance_status = fields.Str(dump_only=True)
    change_reason = fields.Str(dump_only=True, allow_none=True)
    effective_at = _UtcDateTime(dump_only=True)
    created_at = _UtcDateTime(dump_only=True)
    points = fields.List(
        fields.Nested(ResearchPointResponseSchema), dump_only=True
    )


class MarketPlanRevisionResponseSchema(Schema):
    id = fields.Str(dump_only=True)
    company_id = fields.Str(dump_only=True)
    revision_number = fields.Int(dump_only=True)
    supersedes_revision_id = fields.Str(dump_only=True, allow_none=True)
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
    change_reason = fields.Str(dump_only=True, allow_none=True)
    created_at = _UtcDateTime(dump_only=True)


class ForecastLineResponseSchema(Schema):
    id = fields.Str(dump_only=True)
    fiscal_year = fields.Int(dump_only=True)
    is_estimate = fields.Boolean(dump_only=True)
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


class ForecastRevisionResponseSchema(Schema):
    id = fields.Str(dump_only=True)
    company_id = fields.Str(dump_only=True)
    revision_number = fields.Int(dump_only=True)
    supersedes_revision_id = fields.Str(dump_only=True, allow_none=True)
    as_of_date = fields.Date(dump_only=True)
    assumptions = fields.Str(dump_only=True, allow_none=True)
    change_reason = fields.Str(dump_only=True, allow_none=True)
    created_at = _UtcDateTime(dump_only=True)
    lines = fields.List(
        fields.Nested(ForecastLineResponseSchema), dump_only=True
    )


class ValuationReferenceLineResponseSchema(Schema):
    id = fields.Str(dump_only=True)
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


class ValuationRevisionResponseSchema(Schema):
    id = fields.Str(dump_only=True)
    company_id = fields.Str(dump_only=True)
    valuation_method = fields.Str(dump_only=True)
    revision_number = fields.Int(dump_only=True)
    supersedes_revision_id = fields.Str(dump_only=True, allow_none=True)
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
    change_reason = fields.Str(dump_only=True, allow_none=True)
    created_at = _UtcDateTime(dump_only=True)
    reference_lines = fields.List(
        fields.Nested(ValuationReferenceLineResponseSchema), dump_only=True
    )


__all__ = [
    "CompanyCreateSchema",
    "CompanyPatchSchema",
    "CompanyResponseSchema",
    "DisclosureCreateSchema",
    "DisclosurePatchSchema",
    "DisclosureResponseSchema",
    "EntitlementPutSchema",
    "EntitlementResponseSchema",
    "ForecastLineCreateSchema",
    "ForecastLineResponseSchema",
    "ForecastRevisionCreateSchema",
    "ForecastRevisionResponseSchema",
    "GovernanceFlagCreateSchema",
    "GovernanceFlagPatchSchema",
    "GovernanceFlagResponseSchema",
    "InstitutionCreateSchema",
    "InstitutionPatchSchema",
    "InstitutionResponseSchema",
    "MarketPlanRevisionCreateSchema",
    "MarketPlanRevisionResponseSchema",
    "OwnershipSnapshotCreateSchema",
    "OwnershipSnapshotResponseSchema",
    "ResearchPointCreateSchema",
    "ResearchPointResponseSchema",
    "ResearchRevisionCreateSchema",
    "ResearchRevisionResponseSchema",
    "ValuationReferenceLineCreateSchema",
    "ValuationReferenceLineResponseSchema",
    "ValuationRevisionCreateSchema",
    "ValuationRevisionResponseSchema",
    "load_admin_payload",
]
