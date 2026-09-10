"""Plan 4 Task 4: explicit document access and derived-rights policy.

Document visibility is independent from product entitlement. The policy
examines source access, distribution status, provider ownership, archival
state, and the resolved access context, then returns an immutable decision
and an allowlisted projection. Storage references are never projected.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone

from app.models.document import DistributionStatus, Document, SourceAccess
from app.services.entitlement_service import ResearchAccessContext


PUBLIC_DOCUMENT_FIELDS = frozenset(
    {
        "id",
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
    }
)

MANAGED_DOCUMENT_FIELDS = frozenset(
    {
        "id",
        "document_type",
        "title",
        "document_date",
        "supersedes_document_id",
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
        "mime_type",
        "file_size_bytes",
        "provided_by_user_id",
        "distribution_basis",
        "rights_verified_by_user_id",
        "rights_verified_at",
        "created_by_user_id",
        "created_at",
        "updated_at",
        "archived_at",
        "content_hash_sha256",
        "metadata_fingerprint",
    }
)


@dataclass(frozen=True)
class DocumentAccessDecision:
    """Immutable rights decision for one document/context pair."""

    visible: bool
    metadata_fields: frozenset[str]
    may_contribute_to_aggregate: bool


def _is_provider(document: Document, context: ResearchAccessContext) -> bool:
    return (
        document.provided_by_user_id is not None
        and document.provided_by_user_id == context.user_id
    )


def _is_private_context_document(document: Document) -> bool:
    return (
        document.distribution_status
        in (
            DistributionStatus.PRIVATE_LIBRARY,
            DistributionStatus.UNKNOWN,
        )
        or document.source_access != SourceAccess.PUBLIC
    )


def _serialize_value(value: object) -> object:
    """Convert values to JSON-safe scalar representations."""

    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat().replace(
            "+00:00", "Z"
        )
    if isinstance(value, date):
        return value.isoformat()
    return value


class DocumentAccessPolicy:
    """Evaluate and project document rights without using tier as an override."""

    @staticmethod
    def evaluate(
        document: Document,
        context: ResearchAccessContext,
    ) -> DocumentAccessDecision:
        if document.archived_at is not None:
            return DocumentAccessDecision(False, frozenset(), False)

        is_managed = context.is_admin or _is_provider(document, context)
        if is_managed:
            may_contribute = (
                document.distribution_status
                == DistributionStatus.APP_DISTRIBUTABLE
                or _is_private_context_document(document)
            )
            return DocumentAccessDecision(
                True,
                MANAGED_DOCUMENT_FIELDS,
                may_contribute,
            )

        is_consumer_visible = (
            document.source_access == SourceAccess.PUBLIC
            and document.distribution_status
            in (
                DistributionStatus.LINK_ONLY,
                DistributionStatus.APP_DISTRIBUTABLE,
            )
        )
        if not is_consumer_visible:
            return DocumentAccessDecision(False, frozenset(), False)

        return DocumentAccessDecision(
            True,
            PUBLIC_DOCUMENT_FIELDS,
            document.distribution_status
            == DistributionStatus.APP_DISTRIBUTABLE,
        )

    @staticmethod
    def project(
        document: Document,
        decision: DocumentAccessDecision,
    ) -> dict[str, object]:
        if not decision.visible:
            return {}

        return {
            field: _serialize_value(getattr(document, field, None))
            for field in sorted(decision.metadata_fields)
        }


__all__ = [
    "DocumentAccessDecision",
    "DocumentAccessPolicy",
]

