"""Continuous Monitoring V1 follow-up: the admin-facing alert feed.

Read-only. ``AlertEventSchema`` dumps exactly the fields ``AlertEvent``
already carries (app/models/alert.py) -- this schema adds no
interpretation of its own, the same "presenter never invents data"
discipline every other research schema in this codebase follows.
"""

from __future__ import annotations

from datetime import datetime, timezone

from marshmallow import RAISE, Schema, ValidationError, fields

from app.utils.research_errors import ResearchValidationError


class _UtcDateTime(fields.DateTime):
    """Serialize naive SQLite UTC datetimes as timezone-aware UTC."""

    def _serialize(self, value, attr, obj, **kwargs):
        if isinstance(value, datetime) and value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return super()._serialize(value, attr, obj, **kwargs)


class AlertEventListQuerySchema(Schema):
    class Meta:
        unknown = RAISE

    limit = fields.Int(load_default=50, validate=lambda n: 1 <= n <= 200)


def load_alert_list_query(args) -> dict:
    try:
        return AlertEventListQuerySchema().load(args.to_dict(flat=True))
    except ValidationError as exc:
        raise ResearchValidationError(
            {
                field: [str(item) for item in messages]
                if isinstance(messages, list)
                else [str(messages)]
                for field, messages in exc.messages.items()
            }
        ) from exc


class AlertEventSchema(Schema):
    """One ``AlertEvent`` row, exactly as recorded -- no interpretation."""

    id = fields.Str(dump_only=True)
    event_type = fields.Str(dump_only=True)
    source_table = fields.Str(dump_only=True)
    source_id = fields.Str(dump_only=True)
    company_id = fields.Str(dump_only=True, allow_none=True)
    severity = fields.Str(dump_only=True)
    summary = fields.Str(dump_only=True)
    details = fields.Dict(dump_only=True, allow_none=True)
    occurred_at = _UtcDateTime(dump_only=True)


class AlertEventListSchema(Schema):
    items = fields.List(fields.Nested(AlertEventSchema), dump_only=True)
