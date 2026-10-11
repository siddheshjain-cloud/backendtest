"""Plan 5 Task 5: strict consumer research query and response schemas."""

from __future__ import annotations

from datetime import datetime, timezone

from marshmallow import RAISE, Schema, ValidationError, fields

from app.schemas.research import (
    CompanySchema,
    ForecastSchema,
    MarketPlanSchema,
    MarketQuoteSchema,
    ResearchPointSchema,
    ValuationSchema,
)
from app.utils.research_errors import ResearchValidationError


class _UtcDateTime(fields.DateTime):
    """Serialize naive SQLite UTC datetimes as timezone-aware UTC."""

    def _serialize(self, value, attr, obj, **kwargs):
        if isinstance(value, datetime) and value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return super()._serialize(value, attr, obj, **kwargs)


def _flatten_messages(messages: object) -> dict[str, list[str]]:
    details: dict[str, list[str]] = {}
    if not isinstance(messages, dict):
        return {"query": ["Must be a valid query string"]}

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


def load_consumer_query(schema: Schema, args) -> dict:
    """Load and strictly validate a consumer query string."""

    try:
        return schema.load(args.to_dict(flat=True))
    except ValidationError as exc:
        raise ResearchValidationError(
            _flatten_messages(exc.messages)
        ) from exc


class _StrictQuerySchema(Schema):
    class Meta:
        unknown = RAISE


class CompanyListQuerySchema(_StrictQuerySchema):
    q = fields.Str(load_default=None, allow_none=True)
    sector = fields.Str(load_default=None, allow_none=True)
    industry = fields.Str(load_default=None, allow_none=True)
    page = fields.Int(load_default=1)
    per_page = fields.Int(load_default=20)


class DisclosureListQuerySchema(_StrictQuerySchema):
    event_type = fields.Str(load_default=None, allow_none=True)
    is_key = fields.Boolean(load_default=None, allow_none=True)
    date_from = fields.Date(load_default=None, allow_none=True)
    date_to = fields.Date(load_default=None, allow_none=True)
    newest_first = fields.Boolean(load_default=True)
    page = fields.Int(load_default=1)
    per_page = fields.Int(load_default=20)


class HistoryQuerySchema(_StrictQuerySchema):
    section = fields.Str(required=True)
    valuation_method = fields.Str(load_default=None, allow_none=True)
    page = fields.Int(load_default=1)
    per_page = fields.Int(load_default=20)


class ConsumerBusinessGroupSchema(Schema):
    """Consumer-visible business-group summary from the query service."""

    name = fields.Str(dump_only=True)
    notes = fields.Str(dump_only=True, allow_none=True)
    source_reference = fields.Str(dump_only=True, allow_none=True)


class CompanyListSummarySchema(Schema):
    company = fields.Nested(CompanySchema, dump_only=True)
    business_group = fields.Nested(
        ConsumerBusinessGroupSchema, dump_only=True, allow_none=True
    )
    market_quote = fields.Nested(MarketQuoteSchema, dump_only=True)


class CompanyListPageSchema(Schema):
    items = fields.List(
        fields.Nested(CompanyListSummarySchema), dump_only=True
    )
    page = fields.Int(dump_only=True)
    per_page = fields.Int(dump_only=True)
    total_items = fields.Int(dump_only=True)
    total_pages = fields.Int(dump_only=True)


class ConsumerDisclosureSchema(Schema):
    id = fields.Str(dump_only=True)
    company_id = fields.Str(dump_only=True)
    event_type = fields.Str(dump_only=True)
    event_date = fields.Date(dump_only=True)
    title = fields.Str(dump_only=True)
    original_source_url_or_reference = fields.Str(dump_only=True)
    exchange_reference = fields.Str(dump_only=True, allow_none=True)
    is_key = fields.Boolean(dump_only=True)
    document_id = fields.Str(dump_only=True, allow_none=True)
    significance_note = fields.Str(dump_only=True, allow_none=True)


class DisclosurePageSchema(Schema):
    items = fields.List(
        fields.Nested(ConsumerDisclosureSchema), dump_only=True
    )
    page = fields.Int(dump_only=True)
    per_page = fields.Int(dump_only=True)
    total_items = fields.Int(dump_only=True)
    total_pages = fields.Int(dump_only=True)


class ResearchHistoryItemSchema(Schema):
    id = fields.Str(dump_only=True)
    revision = fields.Int(dump_only=True)
    created_at = _UtcDateTime(dump_only=True)
    effective_at = _UtcDateTime(dump_only=True)
    change_reason = fields.Str(dump_only=True, allow_none=True)
    why_selected = fields.Str(dump_only=True)
    what_is_changing = fields.Str(dump_only=True, allow_none=True)
    business_journey = fields.Str(dump_only=True, allow_none=True)
    thesis = fields.Str(dump_only=True)
    thesis_invalidation = fields.Str(dump_only=True)
    governance_status = fields.Str(dump_only=True)
    catalysts = fields.List(
        fields.Nested(ResearchPointSchema), dump_only=True
    )
    risks = fields.List(
        fields.Nested(ResearchPointSchema), dump_only=True
    )


class ResearchHistoryPageSchema(Schema):
    items = fields.List(
        fields.Nested(ResearchHistoryItemSchema), dump_only=True
    )
    page = fields.Int(dump_only=True)
    per_page = fields.Int(dump_only=True)
    total_items = fields.Int(dump_only=True)
    total_pages = fields.Int(dump_only=True)


class MarketPlanHistoryItemSchema(MarketPlanSchema):
    id = fields.Str(dump_only=True)
    created_at = _UtcDateTime(dump_only=True)
    change_reason = fields.Str(dump_only=True, allow_none=True)


class MarketPlanHistoryPageSchema(Schema):
    items = fields.List(
        fields.Nested(MarketPlanHistoryItemSchema), dump_only=True
    )
    page = fields.Int(dump_only=True)
    per_page = fields.Int(dump_only=True)
    total_items = fields.Int(dump_only=True)
    total_pages = fields.Int(dump_only=True)


class ForecastHistoryItemSchema(ForecastSchema):
    id = fields.Str(dump_only=True)
    created_at = _UtcDateTime(dump_only=True)
    change_reason = fields.Str(dump_only=True, allow_none=True)


class ForecastHistoryPageSchema(Schema):
    items = fields.List(
        fields.Nested(ForecastHistoryItemSchema), dump_only=True
    )
    page = fields.Int(dump_only=True)
    per_page = fields.Int(dump_only=True)
    total_items = fields.Int(dump_only=True)
    total_pages = fields.Int(dump_only=True)


class ValuationHistoryItemSchema(ValuationSchema):
    id = fields.Str(dump_only=True)
    created_at = _UtcDateTime(dump_only=True)
    change_reason = fields.Str(dump_only=True, allow_none=True)


class ValuationHistoryPageSchema(Schema):
    items = fields.List(
        fields.Nested(ValuationHistoryItemSchema), dump_only=True
    )
    page = fields.Int(dump_only=True)
    per_page = fields.Int(dump_only=True)
    total_items = fields.Int(dump_only=True)
    total_pages = fields.Int(dump_only=True)


HISTORY_PAGE_SCHEMAS = {
    "research": ResearchHistoryPageSchema,
    "market": MarketPlanHistoryPageSchema,
    "forecast": ForecastHistoryPageSchema,
    "valuation": ValuationHistoryPageSchema,
}


__all__ = [
    "CompanyListPageSchema",
    "CompanyListQuerySchema",
    "DisclosureListQuerySchema",
    "DisclosurePageSchema",
    "ForecastHistoryPageSchema",
    "HISTORY_PAGE_SCHEMAS",
    "HistoryQuerySchema",
    "MarketPlanHistoryPageSchema",
    "ResearchHistoryPageSchema",
    "ValuationHistoryPageSchema",
    "load_consumer_query",
]
