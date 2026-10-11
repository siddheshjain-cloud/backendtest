"""Plan 5 Task 6: strict consumer document query and response schemas."""

from __future__ import annotations

from marshmallow import RAISE, Schema, fields

from app.schemas.research_consumer import load_consumer_query


class DocumentListQuerySchema(Schema):
    """Validated consumer filters and pagination for company documents."""

    class Meta:
        unknown = RAISE

    document_type = fields.Str(load_default=None, allow_none=True)
    institution_id = fields.Str(load_default=None, allow_none=True)
    report_type = fields.Str(load_default=None, allow_none=True)
    date_from = fields.Date(load_default=None, allow_none=True)
    date_to = fields.Date(load_default=None, allow_none=True)
    page = fields.Int(load_default=1)
    per_page = fields.Int(load_default=20)


class ConsumerInstitutionalReportSchema(Schema):
    institution_id = fields.Str(dump_only=True)
    institution_name = fields.Str(dump_only=True, allow_none=True)
    report_type = fields.Str(dump_only=True)


class ConsumerDocumentSchema(Schema):
    """Explicit rights-safe projection for the consumer document boundary.

    The existing query service only supplies the fields it authorized for the
    requesting context. Missing keys are omitted by Marshmallow, so a public
    consumer receives only the public projection while a provider still
    receives the provider-managed projection.
    """

    class Meta:
        unknown = RAISE

    id = fields.Str(dump_only=True)
    document_type = fields.Str(dump_only=True)
    title = fields.Str(dump_only=True)
    document_date = fields.Str(dump_only=True, allow_none=True)
    supersedes_document_id = fields.Str(dump_only=True, allow_none=True)
    original_published_date = fields.Str(dump_only=True, allow_none=True)
    original_published_at = fields.Str(dump_only=True, allow_none=True)
    original_published_at_precision = fields.Str(dump_only=True)
    reporting_period = fields.Str(dump_only=True, allow_none=True)
    publisher_name = fields.Str(dump_only=True, allow_none=True)
    publisher_reference = fields.Str(dump_only=True, allow_none=True)
    original_source_url = fields.Str(dump_only=True, allow_none=True)
    discovery_source_type = fields.Str(dump_only=True, allow_none=True)
    discovery_source_reference = fields.Str(dump_only=True, allow_none=True)
    source_access = fields.Str(dump_only=True, allow_none=True)
    acquisition_method = fields.Str(dump_only=True, allow_none=True)
    distribution_status = fields.Str(dump_only=True, allow_none=True)
    ingestion_status = fields.Str(dump_only=True, allow_none=True)
    mime_type = fields.Str(dump_only=True, allow_none=True)
    file_size_bytes = fields.Int(dump_only=True, allow_none=True)
    provided_by_user_id = fields.Str(dump_only=True, allow_none=True)
    distribution_basis = fields.Str(dump_only=True, allow_none=True)
    rights_verified_by_user_id = fields.Str(dump_only=True, allow_none=True)
    rights_verified_at = fields.Str(dump_only=True, allow_none=True)
    created_by_user_id = fields.Str(dump_only=True, allow_none=True)
    created_at = fields.Str(dump_only=True, allow_none=True)
    updated_at = fields.Str(dump_only=True, allow_none=True)
    archived_at = fields.Str(dump_only=True, allow_none=True)
    content_hash_sha256 = fields.Str(dump_only=True, allow_none=True)
    metadata_fingerprint = fields.Str(dump_only=True, allow_none=True)
    institutional_report = fields.Nested(
        ConsumerInstitutionalReportSchema,
        dump_only=True,
        allow_none=True,
    )


class DocumentPageSchema(Schema):
    items = fields.List(
        fields.Nested(ConsumerDocumentSchema), dump_only=True
    )
    page = fields.Int(dump_only=True)
    per_page = fields.Int(dump_only=True)
    total_items = fields.Int(dump_only=True)
    total_pages = fields.Int(dump_only=True)


__all__ = [
    "ConsumerDocumentSchema",
    "ConsumerInstitutionalReportSchema",
    "DocumentListQuerySchema",
    "DocumentPageSchema",
    "load_consumer_query",
]
