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
    ResearchTier,
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


__all__ = [
    "CompanyCreateSchema",
    "CompanyPatchSchema",
    "CompanyResponseSchema",
    "DisclosureCreateSchema",
    "DisclosurePatchSchema",
    "DisclosureResponseSchema",
    "EntitlementPutSchema",
    "EntitlementResponseSchema",
    "GovernanceFlagCreateSchema",
    "GovernanceFlagPatchSchema",
    "GovernanceFlagResponseSchema",
    "InstitutionCreateSchema",
    "InstitutionPatchSchema",
    "InstitutionResponseSchema",
    "OwnershipSnapshotCreateSchema",
    "OwnershipSnapshotResponseSchema",
    "load_admin_payload",
]
