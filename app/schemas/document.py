"""Plan 4 Task 2: strict request schemas for the common Document Library.

These schemas describe the administrative write surface. Every schema raises
on unknown fields, HTTP(S) URLs are normalized without being followed, and
optional stored-file metadata is validated only when supplied. Cross-field
rights and ingestion rules live in ``DocumentValidationService`` so one
field-keyed ``ResearchValidationError`` can describe the complete request.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit, urlunsplit

from marshmallow import RAISE, Schema, fields, validate
from marshmallow import ValidationError

from app.models.document import (
    AcquisitionMethod,
    DiscoverySourceType,
    DistributionStatus,
    DocumentType,
    IngestionStatus,
    OriginalPublicationPrecision,
    SourceAccess,
)


SHA256_HEX_PATTERN = re.compile(r"^[0-9a-f]{64}$")
MIME_TYPE_PATTERN = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9!#$&^_.+-]{0,126}/"
    r"[A-Za-z0-9][A-Za-z0-9!#$&^_.+-]{0,126}$"
)


def normalize_http_url(value: str) -> str:
    """Trim and normalize an absolute HTTP/HTTPS URL without following it."""

    if not isinstance(value, str):
        raise ValueError("Must be an absolute HTTP or HTTPS URL")
    candidate = value.strip()
    parts = urlsplit(candidate)
    if parts.scheme.lower() not in {"http", "https"} or not parts.netloc:
        raise ValueError("Must be an absolute HTTP or HTTPS URL")
    return urlunsplit(
        (
            parts.scheme.lower(),
            parts.netloc.lower(),
            parts.path,
            parts.query,
            parts.fragment,
        )
    )


class _TrimmedStr(fields.String):
    """A string field that strips surrounding whitespace when supplied."""

    def _deserialize(self, value, attr, data, **kwargs):
        value = super()._deserialize(value, attr, data, **kwargs)
        return value.strip()


class _RequiredTrimmedStr(_TrimmedStr):
    """A required string field that rejects blank values."""

    def _deserialize(self, value, attr, data, **kwargs):
        value = super()._deserialize(value, attr, data, **kwargs)
        if not value:
            raise ValidationError("Shall not be blank.")
        return value


class _HttpUrlField(_TrimmedStr):
    """An absolute HTTP/HTTPS URL normalized without network access."""

    def _deserialize(self, value, attr, data, **kwargs):
        value = super()._deserialize(value, attr, data, **kwargs)
        try:
            return normalize_http_url(value)
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc


class _Sha256Field(_TrimmedStr):
    """A lowercase 64-character SHA-256 hexadecimal digest."""

    def _deserialize(self, value, attr, data, **kwargs):
        value = super()._deserialize(value, attr, data, **kwargs)
        if not SHA256_HEX_PATTERN.fullmatch(value):
            raise ValidationError(
                "Must be a lowercase 64-character SHA-256 hex digest."
            )
        return value


class _MimeTypeField(_TrimmedStr):
    """A syntactically valid MIME type."""

    def _deserialize(self, value, attr, data, **kwargs):
        value = super()._deserialize(value, attr, data, **kwargs)
        if not MIME_TYPE_PATTERN.fullmatch(value):
            raise ValidationError("Must be a valid MIME type.")
        return value


class _NonNegativeIntField(fields.Integer):
    """A non-negative integer that never accepts booleans."""

    def _deserialize(self, value, attr, data, **kwargs):
        if isinstance(value, bool):
            raise ValidationError("Must be a non-negative integer.")
        value = super()._deserialize(value, attr, data, **kwargs)
        if value < 0:
            raise ValidationError("Must be a non-negative integer.")
        return value


class DocumentCompanyLinkSchema(Schema):
    """One company link in a document creation request."""

    class Meta:
        unknown = RAISE

    company_id = _RequiredTrimmedStr(
        required=True, validate=validate.Length(max=64)
    )
    is_primary = fields.Boolean(load_default=False)


class _InstitutionalReportSchema(Schema):
    """Conditional institutional-research extension in a create request."""

    class Meta:
        unknown = RAISE

    institution_id = _RequiredTrimmedStr(
        required=True, validate=validate.Length(max=64)
    )
    analyst_name = _TrimmedStr(
        load_default=None, allow_none=True, validate=validate.Length(max=200)
    )
    report_type = _RequiredTrimmedStr(
        required=True, validate=validate.Length(max=100)
    )


class _DocumentFieldsSchema(Schema):
    """The Document portion of one creation request."""

    class Meta:
        unknown = RAISE

    document_type = fields.String(
        required=True,
        validate=validate.OneOf(
            (
                DocumentType.ANNUAL_REPORT,
                DocumentType.QUARTERLY_RESULTS,
                DocumentType.INVESTOR_PRESENTATION,
                DocumentType.CONCALL,
                DocumentType.SCREENER,
                DocumentType.REG30_ATTACHMENT,
                DocumentType.CREDIT_RATING_REPORT,
                DocumentType.INDUSTRY_REPORT,
                DocumentType.INSTITUTIONAL_RESEARCH,
                DocumentType.OTHER,
            )
        ),
    )
    title = _RequiredTrimmedStr(
        required=True, validate=validate.Length(max=300)
    )
    document_date = fields.Date(load_default=None, allow_none=True)
    original_published_date = fields.Date(
        load_default=None, allow_none=True
    )
    original_published_at = fields.DateTime(
        load_default=None, allow_none=True
    )
    original_published_at_precision = fields.String(
        load_default=OriginalPublicationPrecision.UNKNOWN,
        validate=validate.OneOf(
            (
                OriginalPublicationPrecision.DATE,
                OriginalPublicationPrecision.DATETIME,
                OriginalPublicationPrecision.UNKNOWN,
            )
        ),
    )
    reporting_period = _TrimmedStr(
        load_default=None, allow_none=True, validate=validate.Length(max=64)
    )
    publisher_name = _TrimmedStr(
        load_default=None, allow_none=True, validate=validate.Length(max=200)
    )
    publisher_reference = _TrimmedStr(
        load_default=None, allow_none=True, validate=validate.Length(max=1000)
    )
    original_source_url = _HttpUrlField(
        load_default=None, allow_none=True, validate=validate.Length(max=1000)
    )
    discovery_source_type = fields.String(
        load_default=DiscoverySourceType.OTHER,
        validate=validate.OneOf(
            (
                DiscoverySourceType.OFFICIAL_SITE,
                DiscoverySourceType.EXCHANGE,
                DiscoverySourceType.TELEGRAM,
                DiscoverySourceType.EMAIL,
                DiscoverySourceType.SEARCH,
                DiscoverySourceType.USER,
                DiscoverySourceType.OTHER,
            )
        ),
    )
    discovery_source_reference = _TrimmedStr(
        load_default=None, allow_none=True, validate=validate.Length(max=1000)
    )
    source_access = fields.String(
        required=True,
        validate=validate.OneOf(
            (
                SourceAccess.PUBLIC,
                SourceAccess.RESTRICTED,
                SourceAccess.UNKNOWN,
            )
        ),
    )
    acquisition_method = fields.String(
        required=True,
        validate=validate.OneOf(
            (
                AcquisitionMethod.PUBLIC_DOWNLOAD,
                AcquisitionMethod.USER_UPLOAD,
                AcquisitionMethod.MANUAL_REFERENCE,
                AcquisitionMethod.NOT_ACQUIRED,
            )
        ),
    )
    distribution_status = fields.String(
        load_default=None,
        allow_none=True,
        validate=validate.OneOf(
            (
                DistributionStatus.UNKNOWN,
                DistributionStatus.LINK_ONLY,
                DistributionStatus.PRIVATE_LIBRARY,
                DistributionStatus.APP_DISTRIBUTABLE,
            )
        ),
    )
    ingestion_status = fields.String(
        required=True,
        validate=validate.OneOf(
            (
                IngestionStatus.DISCOVERED,
                IngestionStatus.AWAITING_UPLOAD,
                IngestionStatus.STORED,
                IngestionStatus.ANALYSED,
            )
        ),
    )
    storage_provider = _TrimmedStr(
        load_default=None, allow_none=True, validate=validate.Length(max=50)
    )
    storage_key = _TrimmedStr(
        load_default=None, allow_none=True, validate=validate.Length(max=500)
    )
    content_hash_sha256 = _Sha256Field(load_default=None, allow_none=True)
    mime_type = _MimeTypeField(
        load_default=None, allow_none=True, validate=validate.Length(max=100)
    )
    file_size_bytes = _NonNegativeIntField(load_default=None, allow_none=True)
    provided_by_user_id = _TrimmedStr(
        load_default=None, allow_none=True, validate=validate.Length(max=64)
    )
    distribution_basis = _TrimmedStr(
        load_default=None, allow_none=True, validate=validate.Length(max=1000)
    )
    rights_verified_by_user_id = _TrimmedStr(
        load_default=None, allow_none=True, validate=validate.Length(max=64)
    )
    rights_verified_at = fields.DateTime(load_default=None, allow_none=True)


class DocumentCreateSchema(Schema):
    """The complete document-creation aggregate request."""

    class Meta:
        unknown = RAISE

    document = fields.Nested(_DocumentFieldsSchema, required=True)
    company_links = fields.List(
        fields.Nested(DocumentCompanyLinkSchema), load_default=list
    )
    institutional_report = fields.Nested(
        _InstitutionalReportSchema, load_default=None, allow_none=True
    )


class DocumentPatchSchema(Schema):
    """The patchable Document fields for one administrative update."""

    class Meta:
        unknown = RAISE

    document_type = fields.String(
        validate=validate.OneOf(
            (
                DocumentType.ANNUAL_REPORT,
                DocumentType.QUARTERLY_RESULTS,
                DocumentType.INVESTOR_PRESENTATION,
                DocumentType.CONCALL,
                DocumentType.SCREENER,
                DocumentType.REG30_ATTACHMENT,
                DocumentType.CREDIT_RATING_REPORT,
                DocumentType.INDUSTRY_REPORT,
                DocumentType.INSTITUTIONAL_RESEARCH,
                DocumentType.OTHER,
            )
        )
    )
    title = _RequiredTrimmedStr(validate=validate.Length(max=300))
    document_date = fields.Date(allow_none=True)
    original_published_date = fields.Date(allow_none=True)
    original_published_at = fields.DateTime(allow_none=True)
    original_published_at_precision = fields.String(
        validate=validate.OneOf(
            (
                OriginalPublicationPrecision.DATE,
                OriginalPublicationPrecision.DATETIME,
                OriginalPublicationPrecision.UNKNOWN,
            )
        )
    )
    reporting_period = _TrimmedStr(
        allow_none=True, validate=validate.Length(max=64)
    )
    publisher_name = _TrimmedStr(
        allow_none=True, validate=validate.Length(max=200)
    )
    publisher_reference = _TrimmedStr(
        allow_none=True, validate=validate.Length(max=1000)
    )
    original_source_url = _HttpUrlField(
        allow_none=True, validate=validate.Length(max=1000)
    )
    discovery_source_type = fields.String(
        validate=validate.OneOf(
            (
                DiscoverySourceType.OFFICIAL_SITE,
                DiscoverySourceType.EXCHANGE,
                DiscoverySourceType.TELEGRAM,
                DiscoverySourceType.EMAIL,
                DiscoverySourceType.SEARCH,
                DiscoverySourceType.USER,
                DiscoverySourceType.OTHER,
            )
        )
    )
    discovery_source_reference = _TrimmedStr(
        allow_none=True, validate=validate.Length(max=1000)
    )
    source_access = fields.String(
        validate=validate.OneOf(
            (
                SourceAccess.PUBLIC,
                SourceAccess.RESTRICTED,
                SourceAccess.UNKNOWN,
            )
        )
    )
    acquisition_method = fields.String(
        validate=validate.OneOf(
            (
                AcquisitionMethod.PUBLIC_DOWNLOAD,
                AcquisitionMethod.USER_UPLOAD,
                AcquisitionMethod.MANUAL_REFERENCE,
                AcquisitionMethod.NOT_ACQUIRED,
            )
        )
    )
    distribution_status = fields.String(
        validate=validate.OneOf(
            (
                DistributionStatus.UNKNOWN,
                DistributionStatus.LINK_ONLY,
                DistributionStatus.PRIVATE_LIBRARY,
                DistributionStatus.APP_DISTRIBUTABLE,
            )
        ),
    )
    ingestion_status = fields.String(
        validate=validate.OneOf(
            (
                IngestionStatus.DISCOVERED,
                IngestionStatus.AWAITING_UPLOAD,
                IngestionStatus.STORED,
                IngestionStatus.ANALYSED,
            )
        )
    )
    storage_provider = _TrimmedStr(
        allow_none=True, validate=validate.Length(max=50)
    )
    storage_key = _TrimmedStr(
        allow_none=True, validate=validate.Length(max=500)
    )
    content_hash_sha256 = _Sha256Field(allow_none=True)
    mime_type = _MimeTypeField(
        allow_none=True, validate=validate.Length(max=100)
    )
    file_size_bytes = _NonNegativeIntField(allow_none=True)
    provided_by_user_id = _TrimmedStr(
        allow_none=True, validate=validate.Length(max=64)
    )
    distribution_basis = _TrimmedStr(
        allow_none=True, validate=validate.Length(max=1000)
    )
    rights_verified_by_user_id = _TrimmedStr(
        allow_none=True, validate=validate.Length(max=64)
    )
    rights_verified_at = fields.DateTime(allow_none=True)
    archived_at = fields.DateTime(allow_none=True)


class InstitutionCreateSchema(Schema):
    """The request body for creating one canonical institution."""

    class Meta:
        unknown = RAISE

    name = _RequiredTrimmedStr(
        required=True, validate=validate.Length(max=200)
    )
    website = _HttpUrlField(
        load_default=None, allow_none=True, validate=validate.Length(max=1000)
    )


__all__ = [
    "DocumentCompanyLinkSchema",
    "DocumentCreateSchema",
    "DocumentPatchSchema",
    "InstitutionCreateSchema",
    "normalize_http_url",
]
