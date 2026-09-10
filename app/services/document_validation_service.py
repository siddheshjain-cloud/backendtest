"""Plan 4 Task 2: cross-field document and institutional state validation.

The schemas enforce field shapes and closed enum values. This service owns the
conditional rules that span fields: conservative distribution defaults, the
ingestion/acquisition/storage matrix, public and LINK_ONLY source URLs,
USER_UPLOAD provider attribution, APP_DISTRIBUTABLE rights evidence,
institutional metadata conditionality, and company-link primary rules.
"""

from __future__ import annotations

from marshmallow import ValidationError

from app.models.document import (
    AcquisitionMethod,
    DistributionStatus,
    Document,
    DocumentType,
    IngestionStatus,
    OriginalPublicationPrecision,
    SourceAccess,
)
from app.schemas.document import DocumentCreateSchema, DocumentPatchSchema
from app.utils.research_errors import ResearchValidationError


_STORAGE_REFERENCE_FIELDS = (
    "storage_provider",
    "storage_key",
    "content_hash_sha256",
)
_OPTIONAL_STORED_METADATA_FIELDS = ("mime_type", "file_size_bytes")
_RIGHTS_EVIDENCE_FIELDS = (
    "distribution_basis",
    "rights_verified_by_user_id",
    "rights_verified_at",
)
_DOCUMENT_FIELDS = (
    "document_type",
    "title",
    "document_date",
    "original_published_date",
    "original_published_at",
    "original_published_at_precision",
    "reporting_period",
    "publisher_name",
    "publisher_reference",
    "original_source_url",
    "discovery_source_type",
    "discovery_source_reference",
    "source_access",
    "acquisition_method",
    "distribution_status",
    "ingestion_status",
    "storage_provider",
    "storage_key",
    "content_hash_sha256",
    "mime_type",
    "file_size_bytes",
    "provided_by_user_id",
    "distribution_basis",
    "rights_verified_by_user_id",
    "rights_verified_at",
    "archived_at",
)

_ALLOWED_ACQUISITION_BY_INGESTION = {
    IngestionStatus.DISCOVERED: (
        AcquisitionMethod.NOT_ACQUIRED,
        AcquisitionMethod.MANUAL_REFERENCE,
    ),
    IngestionStatus.AWAITING_UPLOAD: (
        AcquisitionMethod.NOT_ACQUIRED,
        AcquisitionMethod.MANUAL_REFERENCE,
    ),
    IngestionStatus.STORED: (
        AcquisitionMethod.PUBLIC_DOWNLOAD,
        AcquisitionMethod.USER_UPLOAD,
    ),
    IngestionStatus.ANALYSED: (
        AcquisitionMethod.PUBLIC_DOWNLOAD,
        AcquisitionMethod.USER_UPLOAD,
    ),
}

_M1_ALLOWED_TRANSITIONS = {
    (IngestionStatus.DISCOVERED, IngestionStatus.AWAITING_UPLOAD),
    (IngestionStatus.DISCOVERED, IngestionStatus.STORED),
    (IngestionStatus.AWAITING_UPLOAD, IngestionStatus.STORED),
}


def _flatten_messages(messages, prefix: str = "") -> dict[str, list[str]]:
    """Flatten Marshmallow messages into dotted field-keyed details."""

    flattened: dict[str, list[str]] = {}

    def walk(node, path: str) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                child = f"{path}.{key}" if path else str(key)
                walk(value, child)
            return
        if isinstance(node, (list, tuple)):
            if all(isinstance(item, str) for item in node):
                if path:
                    flattened.setdefault(path, []).extend(node)
                return
            for index, value in enumerate(node):
                child = f"{path}.{index}" if path else str(index)
                walk(value, child)
            return
        if path:
            flattened.setdefault(path, []).append(str(node))

    walk(messages, prefix)
    return flattened


def _load(schema, payload, *, strip_prefix: str | None = None) -> dict:
    if not isinstance(payload, dict):
        raise ResearchValidationError(
            {"payload": ["Must be an object"]}
        )
    try:
        return schema.load(payload)
    except ValidationError as exc:
        details = _flatten_messages(exc.messages)
        if strip_prefix:
            prefix = f"{strip_prefix}."
            details = {
                key[len(prefix) :] if key.startswith(prefix) else key: value
                for key, value in details.items()
            }
        raise ResearchValidationError(
            details
        ) from exc


def _add(details: dict[str, list[str]], field: str, message: str) -> None:
    bucket = details.setdefault(field, [])
    if message not in bucket:
        bucket.append(message)


class DocumentValidationService:
    """Validate document create, patch, and ingestion-transition requests."""

    @staticmethod
    def validate_create(payload: dict) -> dict:
        """Validate and normalize one complete document-creation aggregate."""

        data = _load(
            DocumentCreateSchema(), payload, strip_prefix="document"
        )
        document = dict(data["document"])
        company_links = [dict(link) for link in data.get("company_links", [])]
        institutional_report = data.get("institutional_report")

        details: dict[str, list[str]] = {}
        _validate_company_links(company_links, details)
        _apply_distribution_default(document)
        _validate_document_state(document, details)
        _validate_institutional_reporting(
            document,
            institutional_report,
            len(company_links),
            details,
        )

        if details:
            raise ResearchValidationError(details)

        return {
            "document": document,
            "company_links": company_links,
            "institutional_report": institutional_report,
        }

    @staticmethod
    def validate_patch(document: Document, changes: dict) -> dict:
        """Validate one patch against the complete resulting document state."""

        normalized = _load(DocumentPatchSchema(), changes)

        resulting = {
            field: getattr(document, field, None)
            for field in _DOCUMENT_FIELDS
        }
        resulting.update(normalized)

        details: dict[str, list[str]] = {}
        _validate_document_state(resulting, details)
        _validate_existing_institutional_metadata(
            resulting,
            getattr(document, "institutional_metadata", None) is not None,
            len(getattr(document, "company_links", []) or []),
            details,
        )

        if details:
            raise ResearchValidationError(details)

        return normalized

    @staticmethod
    def validate_transition(document: Document, changes: dict) -> None:
        """Validate an ingestion-status transition for the M1 endpoint."""

        if not isinstance(changes, dict):
            raise ResearchValidationError(
                {"payload": ["Must be an object"]}
            )

        target = changes.get("ingestion_status")
        if target is None:
            return

        current = document.ingestion_status
        if target == current:
            return

        if target == IngestionStatus.ANALYSED:
            raise ResearchValidationError(
                {
                    "ingestion_status": [
                        "Milestone 1 cannot transition documents to ANALYSED"
                    ]
                }
            )

        if (current, target) not in _M1_ALLOWED_TRANSITIONS:
            raise ResearchValidationError(
                {
                    "ingestion_status": [
                        "Invalid ingestion status transition"
                    ]
                }
            )


def _apply_distribution_default(document: dict) -> None:
    """Apply the conservative distribution default when none was supplied."""

    if document.get("distribution_status") is not None:
        return

    source_access = document.get("source_access")
    acquisition_method = document.get("acquisition_method")
    verified = all(
        document.get(field) is not None
        for field in _RIGHTS_EVIDENCE_FIELDS
    )

    if source_access == SourceAccess.PUBLIC:
        document["distribution_status"] = (
            DistributionStatus.APP_DISTRIBUTABLE
            if verified
            else DistributionStatus.LINK_ONLY
        )
    elif acquisition_method == AcquisitionMethod.USER_UPLOAD:
        document["distribution_status"] = DistributionStatus.PRIVATE_LIBRARY
    else:
        document["distribution_status"] = DistributionStatus.UNKNOWN


def _validate_company_links(
    company_links: list[dict], details: dict[str, list[str]]
) -> None:
    seen: set[str] = set()
    primary_count = 0

    for link in company_links:
        company_id = link["company_id"]
        if company_id in seen:
            _add(
                details,
                "company_links",
                "Duplicate company links are not allowed",
            )
        seen.add(company_id)
        if link.get("is_primary", False):
            primary_count += 1

    if primary_count > 1:
        _add(
            details,
            "company_links",
            "At most one company link may be primary",
        )


def _is_stored(ingestion_status: object) -> bool:
    return ingestion_status in (
        IngestionStatus.STORED,
        IngestionStatus.ANALYSED,
    )


def _validate_original_publication(
    document: dict, details: dict[str, list[str]]
) -> None:
    precision = (
        document.get("original_published_at_precision")
        or OriginalPublicationPrecision.UNKNOWN
    )
    published_at = document.get("original_published_at")
    published_date = document.get("original_published_date")

    if precision == OriginalPublicationPrecision.DATETIME:
        if published_at is None:
            _add(
                details,
                "original_published_at",
                "Required for DATETIME publication precision",
            )
        if published_date is not None:
            _add(
                details,
                "original_published_date",
                "Not permitted when publication precision is DATETIME",
            )
    elif precision == OriginalPublicationPrecision.DATE:
        if published_date is None:
            _add(
                details,
                "original_published_date",
                "Required for DATE publication precision",
            )
        if published_at is not None:
            _add(
                details,
                "original_published_at",
                "Not permitted when publication precision is DATE",
            )
    else:
        if published_at is not None:
            _add(
                details,
                "original_published_at",
                "Not permitted without DATETIME publication precision",
            )
        if published_date is not None:
            _add(
                details,
                "original_published_date",
                "Not permitted without DATE publication precision",
            )


def _validate_document_state(
    document: dict, details: dict[str, list[str]]
) -> None:
    source_access = document.get("source_access")
    acquisition_method = document.get("acquisition_method")
    distribution_status = document.get("distribution_status")
    ingestion_status = document.get("ingestion_status")
    original_source_url = document.get("original_source_url")

    _validate_original_publication(document, details)

    if source_access == SourceAccess.PUBLIC and not original_source_url:
        _add(
            details,
            "original_source_url",
            "Required when source access is public",
        )

    if distribution_status == DistributionStatus.LINK_ONLY and not original_source_url:
        _add(
            details,
            "original_source_url",
            "Required when distribution is LINK_ONLY",
        )

    allowed_acquisition = _ALLOWED_ACQUISITION_BY_INGESTION.get(
        ingestion_status
    )
    if allowed_acquisition is not None and acquisition_method not in allowed_acquisition:
        _add(
            details,
            "acquisition_method",
            "Not a valid acquisition method for the ingestion status",
        )

    if (
        acquisition_method == AcquisitionMethod.PUBLIC_DOWNLOAD
        and source_access in (SourceAccess.RESTRICTED, SourceAccess.UNKNOWN)
    ):
        _add(
            details,
            "acquisition_method",
            "PUBLIC_DOWNLOAD is not permitted for restricted or unknown sources",
        )

    if (
        acquisition_method == AcquisitionMethod.USER_UPLOAD
        and not document.get("provided_by_user_id")
    ):
        _add(
            details,
            "provided_by_user_id",
            "Required when acquisition method is USER_UPLOAD",
        )

    if _is_stored(ingestion_status):
        for field in _STORAGE_REFERENCE_FIELDS:
            if not document.get(field):
                _add(
                    details,
                    field,
                    "Required for stored or analysed documents",
                )
    else:
        for field in (
            _STORAGE_REFERENCE_FIELDS + _OPTIONAL_STORED_METADATA_FIELDS
        ):
            if document.get(field) is not None:
                _add(
                    details,
                    field,
                    "Only permitted for stored or analysed documents",
                )

    if distribution_status == DistributionStatus.APP_DISTRIBUTABLE:
        for field in _RIGHTS_EVIDENCE_FIELDS:
            if document.get(field) is None:
                _add(
                    details,
                    field,
                    "Required when distribution is APP_DISTRIBUTABLE",
                )


def _validate_institutional_reporting(
    document: dict,
    institutional_report: dict | None,
    company_link_count: int,
    details: dict[str, list[str]],
) -> None:
    document_type = document.get("document_type")

    if document_type == DocumentType.INSTITUTIONAL_RESEARCH:
        if institutional_report is None:
            _add(
                details,
                "institutional_report",
                "Required for institutional research documents",
            )
        if company_link_count == 0:
            _add(
                details,
                "company_links",
                "Institutional research requires at least one company link",
            )
    elif institutional_report is not None:
        _add(
            details,
            "institutional_report",
            "Permitted only for institutional research documents",
        )


def _validate_existing_institutional_metadata(
    document: dict,
    has_institutional_metadata: bool,
    company_link_count: int,
    details: dict[str, list[str]],
) -> None:
    document_type = document.get("document_type")

    if document_type == DocumentType.INSTITUTIONAL_RESEARCH:
        if not has_institutional_metadata:
            _add(
                details,
                "document_type",
                "Institutional research requires institutional metadata",
            )
        if company_link_count == 0:
            _add(
                details,
                "company_links",
                "Institutional research requires at least one company link",
            )
    elif has_institutional_metadata:
        _add(
            details,
            "document_type",
            "Institutional metadata is only permitted for institutional research documents",
        )
