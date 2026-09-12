"""Plan 5 Task 6: strict administrative document request and response schemas."""

from __future__ import annotations

from datetime import datetime, timezone

from marshmallow import RAISE, Schema, ValidationError, fields, validate

from app.schemas.document import DocumentPatchSchema


class _UtcDateTime(fields.DateTime):
    """Serialize naive SQLite UTC datetimes as timezone-aware UTC."""

    def _serialize(self, value, attr, obj, **kwargs):
        if isinstance(value, datetime) and value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return super()._serialize(value, attr, obj, **kwargs)


class _RequiredTrimmedStr(fields.Str):
    """A required string that rejects blank values."""

    def _deserialize(self, value, attr, data, **kwargs):
        value = super()._deserialize(value, attr, data, **kwargs)
        value = value.strip()
        if not value:
            raise ValidationError("Shall not be blank.")
        return value


class DocumentCompanyLinkResponseSchema(Schema):
    company_id = fields.Str(dump_only=True)
    is_primary = fields.Boolean(dump_only=True)


class InstitutionalReportResponseSchema(Schema):
    institution_id = fields.Str(dump_only=True)
    institution_name = fields.Str(
        attribute="institution.name", dump_only=True, allow_none=True
    )
    report_type = fields.Str(dump_only=True)


class DocumentResponseSchema(Schema):
    """Explicit administrative response for one document aggregate."""

    class Meta:
        unknown = RAISE

    id = fields.Str(dump_only=True)
    document_type = fields.Str(dump_only=True)
    title = fields.Str(dump_only=True)
    document_date = fields.Date(dump_only=True, allow_none=True)
    supersedes_document_id = fields.Str(dump_only=True, allow_none=True)
    original_published_date = fields.Date(dump_only=True, allow_none=True)
    original_published_at = _UtcDateTime(dump_only=True, allow_none=True)
    original_published_at_precision = fields.Str(dump_only=True)
    reporting_period = fields.Str(dump_only=True, allow_none=True)
    publisher_name = fields.Str(dump_only=True, allow_none=True)
    publisher_reference = fields.Str(dump_only=True, allow_none=True)
    original_source_url = fields.Str(dump_only=True, allow_none=True)
    discovery_source_type = fields.Str(dump_only=True)
    discovery_source_reference = fields.Str(dump_only=True, allow_none=True)
    source_access = fields.Str(dump_only=True)
    acquisition_method = fields.Str(dump_only=True)
    distribution_status = fields.Str(dump_only=True)
    ingestion_status = fields.Str(dump_only=True)
    mime_type = fields.Str(dump_only=True, allow_none=True)
    file_size_bytes = fields.Int(dump_only=True, allow_none=True)
    provided_by_user_id = fields.Str(dump_only=True, allow_none=True)
    distribution_basis = fields.Str(dump_only=True, allow_none=True)
    rights_verified_by_user_id = fields.Str(dump_only=True, allow_none=True)
    rights_verified_at = _UtcDateTime(dump_only=True, allow_none=True)
    created_by_user_id = fields.Str(dump_only=True)
    created_at = _UtcDateTime(dump_only=True)
    updated_at = _UtcDateTime(dump_only=True)
    archived_at = _UtcDateTime(dump_only=True, allow_none=True)
    content_hash_sha256 = fields.Str(dump_only=True, allow_none=True)
    metadata_fingerprint = fields.Str(dump_only=True)
    company_links = fields.List(
        fields.Nested(DocumentCompanyLinkResponseSchema),
        dump_only=True,
    )
    institutional_report = fields.Nested(
        InstitutionalReportResponseSchema,
        attribute="institutional_metadata",
        dump_only=True,
        allow_none=True,
    )


class DocumentUpdateSchema(Schema):
    """The audited administrative document update envelope."""

    class Meta:
        unknown = RAISE

    reason = fields.Str(
        load_default=None, allow_none=True, validate=validate.Length(max=1000)
    )
    changes = fields.Nested(DocumentPatchSchema, required=True)


class DocumentCompanyLinkCreateSchema(Schema):
    """One atomic administrative company-link addition."""

    class Meta:
        unknown = RAISE

    company_id = _RequiredTrimmedStr(
        required=True, validate=validate.Length(max=64)
    )
    is_primary = fields.Boolean(load_default=False)
    reason = _RequiredTrimmedStr(
        required=True, validate=validate.Length(max=1000)
    )


__all__ = [
    "DocumentCompanyLinkCreateSchema",
    "DocumentCompanyLinkResponseSchema",
    "DocumentResponseSchema",
    "DocumentUpdateSchema",
    "InstitutionalReportResponseSchema",
]
