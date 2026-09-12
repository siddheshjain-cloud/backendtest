"""Plan 4 Task 5: atomic Document Library command service.

Institution commands own normalized-name uniqueness and correction-only
updates. Document creation validates and normalizes the complete aggregate
before inserting any rows, resolves every company and conditional
institution, computes the final fingerprint from the complete sorted link
set, reruns duplicate detection inside the transaction, inserts the Document
and all child rows, and commits once. A unique-fingerprint race is
translated into the same duplicate-review conflict as a pre-insert duplicate
lookup. Initial creation intentionally writes no audit event.
"""

from __future__ import annotations

import hashlib
from datetime import date, datetime, timezone

from marshmallow import ValidationError
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from app import db
from app.models import (
    Company,
    DistributionStatus,
    Document,
    DocumentAuditEvent,
    DocumentAuditEventType,
    DocumentCompanyLink,
    DocumentContent,
    DocumentStorageLocation,
    DocumentType,
    IngestionStatus,
    Institution,
    InstitutionalReportMetadata,
    SourceAccess,
)
from app.policies.document_access import (
    DocumentAccessDecision,
    DocumentAccessPolicy,
)
from app.schemas.document import InstitutionCreateSchema
from app.services.document_deduplication_service import (
    DocumentDeduplicationService,
    normalize_fingerprint_text,
)
from app.services.document_validation_service import (
    DocumentValidationService,
)
from app.services.entitlement_service import ResearchAccessContext
from app.services.research_query_service import PageResult
from app.utils.research_errors import (
    ResearchConflictError,
    ResearchNotFoundError,
    ResearchValidationError,
)


_PATCHABLE_FIELDS = (
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
    "archived_at",
)

_CANONICAL_FINGERPRINT_FIELDS = frozenset(
    {
        "document_type",
        "title",
        "document_date",
        "publisher_name",
        "reporting_period",
    }
)

_STATE_AUDIT_FIELDS = {
    "source_access": DocumentAuditEventType.SOURCE_ACCESS_CHANGED,
    "acquisition_method": DocumentAuditEventType.ACQUISITION_METHOD_CHANGED,
    "distribution_status": DocumentAuditEventType.DISTRIBUTION_STATUS_CHANGED,
    "ingestion_status": DocumentAuditEventType.INGESTION_STATUS_CHANGED,
}

_STORAGE_FIELDS = frozenset(
    {
        "storage_provider",
        "storage_key",
        "content_hash_sha256",
        "mime_type",
        "file_size_bytes",
    }
)

_STORAGE_PATCH_FIELDS = frozenset(
    {
        "storage_provider",
        "storage_key",
        "content_hash_sha256",
    }
)

_STORED_INGESTION_STATUSES = frozenset(
    {IngestionStatus.STORED, IngestionStatus.ANALYSED}
)

_RIGHTS_FIELDS = frozenset(
    {
        "distribution_basis",
        "rights_verified_by_user_id",
        "rights_verified_at",
    }
)

_METADATA_AUDIT_FIELDS = frozenset(_PATCHABLE_FIELDS).difference(
    _STATE_AUDIT_FIELDS,
    _STORAGE_FIELDS,
    _RIGHTS_FIELDS,
    {"archived_at"},
)

_REASON_REQUIRED_FIELDS = frozenset(
    {
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
        "distribution_status",
        "provided_by_user_id",
        "distribution_basis",
        "rights_verified_by_user_id",
        "rights_verified_at",
        "archived_at",
    }
)


def _validate_document_page_bounds(page: int, per_page: int) -> None:
    """Validate the shared collection page contract."""

    details: dict[str, list[str]] = {}
    if not isinstance(page, int) or isinstance(page, bool) or page < 1:
        details["page"] = ["Must be a positive integer"]
    if (
        not isinstance(per_page, int)
        or isinstance(per_page, bool)
        or per_page < 1
        or per_page > 100
    ):
        details["per_page"] = ["Must be an integer between 1 and 100"]
    if details:
        raise ResearchValidationError(details)


def _validate_document_filters(filters: dict | None) -> dict[str, object]:
    """Validate and normalize the frozen document collection filters."""

    if filters is None:
        return {}
    if not isinstance(filters, dict):
        raise ResearchValidationError(
            {"filters": ["Must be an object"]}
        )

    values: dict[str, object] = {}
    document_type = filters.get("document_type")
    institution_id = filters.get("institution_id")
    report_type = filters.get("report_type")
    date_from = filters.get("date_from")
    date_to = filters.get("date_to")
    details: dict[str, list[str]] = {}

    if document_type is not None:
        if (
            not isinstance(document_type, str)
            or document_type
            not in {
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
            }
        ):
            details["document_type"] = [
                "Must be a supported document type"
            ]
        else:
            values["document_type"] = document_type

    if institution_id is not None:
        if (
            not isinstance(institution_id, str)
            or not institution_id.strip()
        ):
            details["institution_id"] = ["Must be a non-empty string"]
        else:
            values["institution_id"] = institution_id

    if report_type is not None:
        if not isinstance(report_type, str) or not report_type.strip():
            details["report_type"] = ["Must be a non-empty string"]
        else:
            values["report_type"] = report_type

    if date_from is not None and not isinstance(date_from, date):
        details["date_from"] = ["Must be an ISO date"]
    if date_to is not None and not isinstance(date_to, date):
        details["date_to"] = ["Must be an ISO date"]
    if (
        isinstance(date_from, date)
        and isinstance(date_to, date)
        and date_from > date_to
    ):
        details["date_from"] = [
            "date_from must be on or before date_to"
        ]
    if details:
        raise ResearchValidationError(details)

    if date_from is not None:
        values["date_from"] = date_from
    if date_to is not None:
        values["date_to"] = date_to
    return values


def _company_exists(company_id: str) -> bool:
    return (
        db.session.scalar(
            sa.select(sa.func.count())
            .select_from(Company)
            .where(Company.id == company_id)
        )
        > 0
    )


def _document_order_by() -> tuple[object, ...]:
    """Deterministic newest-first ordering with date and identity ties."""

    return (
        sa.nullslast(sa.desc(Document.document_date)),
        sa.desc(Document.created_at),
        sa.desc(Document.id),
    )


def _document_visibility_predicate(
    context: ResearchAccessContext,
) -> object:
    """Return the SQL-expressible part of the document-rights decision."""

    if context.is_admin:
        return Document.archived_at.is_(None)

    return sa.and_(
        Document.archived_at.is_(None),
        sa.or_(
            sa.and_(
                Document.source_access == SourceAccess.PUBLIC,
                Document.distribution_status.in_(
                    (
                        DistributionStatus.LINK_ONLY,
                        DistributionStatus.APP_DISTRIBUTABLE,
                    )
                ),
            ),
            Document.provided_by_user_id == context.user_id,
        ),
    )


class DocumentLibraryService:
    """Owns document and institution transactional commands."""

    # ------------------------------------------------------------------
    # Institutions
    # ------------------------------------------------------------------

    @classmethod
    def create_institution(
        cls, payload: dict, actor_user_id: str
    ) -> Institution:
        del actor_user_id

        values = cls._load_institution_values(payload)
        normalized_name = cls._normalize_institution_name(values["name"])
        institution = Institution(
            name=values["name"],
            normalized_name=normalized_name,
            website=values["website"],
        )

        try:
            db.session.add(institution)
            db.session.commit()
        except IntegrityError as error:
            db.session.rollback()
            if cls._is_institution_name_unique_violation(error):
                raise ResearchConflictError(
                    "institution_name_conflict",
                    "Institution name already exists",
                ) from None
            raise
        except Exception:
            db.session.rollback()
            raise

        return institution

    @classmethod
    def update_institution(
        cls,
        institution_id: str,
        changes: dict,
        actor_user_id: str,
    ) -> Institution:
        del actor_user_id

        try:
            institution = db.session.get(Institution, institution_id)
            if institution is None:
                raise ResearchNotFoundError(
                    "institution_not_found",
                    "Institution was not found",
                )

            values = cls._load_institution_values(
                changes,
                existing=institution,
            )
            institution.name = values["name"]
            institution.website = values["website"]
            institution.normalized_name = cls._normalize_institution_name(
                values["name"]
            )
            db.session.commit()
        except IntegrityError as error:
            db.session.rollback()
            if cls._is_institution_name_unique_violation(error):
                raise ResearchConflictError(
                    "institution_name_conflict",
                    "Institution name already exists",
                ) from None
            raise
        except Exception:
            db.session.rollback()
            raise

        return institution

    # ------------------------------------------------------------------
    # Documents
    # ------------------------------------------------------------------

    @classmethod
    def create_document(
        cls, payload: dict, actor_user_id: str
    ) -> Document:
        aggregate = DocumentValidationService.validate_create(payload)
        document_values = dict(aggregate["document"])
        content_hash_sha256 = document_values.pop(
            "content_hash_sha256", None
        )
        storage_provider = document_values.pop("storage_provider", None)
        storage_key = document_values.pop("storage_key", None)
        company_links = [
            dict(link) for link in aggregate.get("company_links", [])
        ]
        institutional_report = aggregate.get("institutional_report")
        if institutional_report is not None:
            institutional_report = dict(institutional_report)

        try:
            cls._resolve_companies(company_links)
            if institutional_report is not None:
                cls._resolve_institution(
                    institutional_report["institution_id"]
                )
            cls._validate_supersedes_lineage(
                None,
                document_values.get("supersedes_document_id"),
            )

            fingerprint = DocumentDeduplicationService.metadata_fingerprint(
                document_type=document_values["document_type"],
                company_ids=[
                    link["company_id"] for link in company_links
                ],
                document_date=document_values.get("document_date"),
                title=document_values["title"],
                publisher_name=document_values.get("publisher_name"),
                institution_id=(
                    institutional_report["institution_id"]
                    if institutional_report is not None
                    else None
                ),
                reporting_period=document_values.get("reporting_period"),
                report_type=(
                    institutional_report["report_type"]
                    if institutional_report is not None
                    else None
                ),
            )

            decision = DocumentDeduplicationService.find_duplicate(
                metadata_fingerprint=fingerprint,
                content_hash_sha256=content_hash_sha256,
            )
            supersedes_document_id = document_values.get(
                "supersedes_document_id"
            )
            is_direct_metadata_reissue = (
                decision.kind == "METADATA_MATCH"
                and supersedes_document_id is not None
                and db.session.scalar(
                    sa.select(Document.id).where(
                        Document.id == supersedes_document_id,
                        Document.metadata_fingerprint == fingerprint,
                    )
                )
                is not None
            )
            if decision.kind != "NONE" and not is_direct_metadata_reissue:
                raise ResearchConflictError(
                    "document_duplicate",
                    "Document requires duplicate review",
                )

            document = Document(
                created_by_user_id=actor_user_id,
                metadata_fingerprint=fingerprint,
                is_fingerprint_duplicate=is_direct_metadata_reissue,
                **document_values,
            )
            db.session.add(document)
            db.session.flush()

            if content_hash_sha256 is not None:
                content = cls._find_or_create_content(content_hash_sha256)
                document.content = content
                if storage_provider is not None or storage_key is not None:
                    cls._add_storage_location(
                        content,
                        provider=storage_provider,
                        storage_key=storage_key,
                    )

            for link in company_links:
                db.session.add(
                    DocumentCompanyLink(
                        document_id=document.id,
                        company_id=link["company_id"],
                        is_primary=link["is_primary"],
                    )
                )
            db.session.flush()

            if institutional_report is not None:
                db.session.add(
                    InstitutionalReportMetadata(
                        document_id=document.id,
                        institution_id=institutional_report[
                            "institution_id"
                        ],
                        analyst_name=institutional_report.get(
                            "analyst_name"
                        ),
                        report_type=institutional_report["report_type"],
                    )
                )
                db.session.flush()

            db.session.commit()
        except IntegrityError as error:
            db.session.rollback()
            if cls._is_document_fingerprint_unique_violation(error):
                raise ResearchConflictError(
                    "document_duplicate",
                    "Document requires duplicate review",
                ) from None
            raise
        except Exception:
            db.session.rollback()
            raise

        return document

    @classmethod
    def add_company_link(
        cls,
        document_id: str,
        company_id: str,
        is_primary: bool,
        actor_user_id: str,
        reason: str,
    ) -> Document:
        """Add one company link and recompute the document fingerprint.

        The complete existing link set is loaded before any mutation, the
        proposed set is validated without mutating the current rows, and the
        new link, fingerprint, and focused audit event commit together. A
        duplicate link, a second primary link, or a duplicate-fingerprint
        conflict rolls back every change and preserves the prior rights and
        fingerprint.
        """

        try:
            document = db.session.scalar(
                sa.select(Document)
                .where(Document.id == document_id)
                .with_for_update()
            )
            if document is None:
                raise ResearchNotFoundError(
                    "document_not_found",
                    "Document was not found",
                )

            existing_links = db.session.scalars(
                sa.select(DocumentCompanyLink)
                .where(DocumentCompanyLink.document_id == document_id)
                .with_for_update()
            ).all()

            if any(
                link.company_id == company_id for link in existing_links
            ):
                raise ResearchValidationError(
                    {
                        "company_links": [
                            "Duplicate company links are not allowed"
                        ]
                    }
                )

            if db.session.get(Company, company_id) is None:
                raise ResearchNotFoundError(
                    "company_not_found",
                    "Company was not found",
                )

            proposed_links = [
                {
                    "company_id": link.company_id,
                    "is_primary": bool(link.is_primary),
                }
                for link in existing_links
            ]
            proposed_links.append(
                {
                    "company_id": company_id,
                    "is_primary": bool(is_primary),
                }
            )

            if sum(
                1 for link in proposed_links if link["is_primary"]
            ) > 1:
                raise ResearchValidationError(
                    {
                        "company_links": [
                            "At most one company link may be primary"
                        ]
                    }
                )

            proposed_company_ids = sorted(
                {link["company_id"] for link in proposed_links}
            )
            institutional_metadata = document.institutional_metadata
            institution_id = (
                institutional_metadata.institution_id
                if institutional_metadata is not None
                else None
            )
            report_type = (
                institutional_metadata.report_type
                if institutional_metadata is not None
                else None
            )

            proposed_fingerprint = (
                DocumentDeduplicationService.metadata_fingerprint(
                    document_type=document.document_type,
                    company_ids=proposed_company_ids,
                    document_date=document.document_date,
                    title=document.title,
                    publisher_name=document.publisher_name,
                    institution_id=institution_id,
                    reporting_period=document.reporting_period,
                    report_type=report_type,
                )
            )

            decision = DocumentDeduplicationService.find_duplicate(
                metadata_fingerprint=proposed_fingerprint,
                content_hash_sha256=document.content_hash_sha256,
                exclude_document_id=document.id,
            )
            if decision.kind != "NONE":
                raise ResearchConflictError(
                    "document_duplicate",
                    "Document requires duplicate review",
                )

            old_snapshot = cls._company_link_snapshot(
                existing_links,
                document.metadata_fingerprint,
            )

            successor = cls._direct_successor_using_fingerprint_slot(document)
            was_fingerprint_duplicate = document.is_fingerprint_duplicate
            document.metadata_fingerprint = proposed_fingerprint
            document.is_fingerprint_duplicate = False
            db.session.flush([document])
            if successor is not None and not was_fingerprint_duplicate:
                successor.is_fingerprint_duplicate = False

            new_link = DocumentCompanyLink(
                document_id=document_id,
                company_id=company_id,
                is_primary=is_primary,
            )
            db.session.add(new_link)

            new_snapshot = cls._company_link_snapshot(
                existing_links + [new_link],
                proposed_fingerprint,
            )

            db.session.add(
                DocumentAuditEvent(
                    document_id=document_id,
                    event_type=DocumentAuditEventType.COMPANY_LINKS_CHANGED,
                    field_changed="company_links",
                    old_value=old_snapshot,
                    new_value=new_snapshot,
                    actor_user_id=actor_user_id,
                    reason=reason,
                )
            )

            db.session.commit()
        except IntegrityError as error:
            db.session.rollback()
            if cls._is_document_fingerprint_unique_violation(error):
                raise ResearchConflictError(
                    "document_duplicate",
                    "Document requires duplicate review",
                ) from None
            raise
        except Exception:
            db.session.rollback()
            raise

        return document

    @classmethod
    def update_document(
        cls,
        document_id: str,
        changes: dict,
        actor_user_id: str,
        reason: str | None,
    ) -> Document:
        """Apply one validated, audited metadata/state/rights change.

        The existing Document is locked, the complete resulting state is
        validated before mutation, and Milestone 1's forbidden
        ``STORED -> ANALYSED`` transition is rejected. Canonical fingerprint
        inputs are recomputed and deduplicated before being applied; access,
        discovery, storage, and rights changes leave the fingerprint alone.
        Each meaningful change writes a focused append-only audit event in the
        same transaction, and any exception rolls back every current-state
        change and audit row.
        """

        try:
            document = db.session.scalar(
                sa.select(Document)
                .where(Document.id == document_id)
                .with_for_update()
            )
            if document is None:
                raise ResearchNotFoundError(
                    "document_not_found",
                    "Document was not found",
                )

            normalized = dict(
                DocumentValidationService.validate_patch(
                    document, changes
                )
            )
            DocumentValidationService.validate_transition(
                document, changes
            )

            storage_changes = {
                field: normalized.pop(field)
                for field in _STORAGE_PATCH_FIELDS
                if field in normalized
            }

            proposed = {
                field: getattr(document, field)
                for field in _PATCHABLE_FIELDS
            }
            proposed.update(normalized)

            changed_fields = {
                field
                for field in proposed
                if field in normalized
                if not cls._values_equal(
                    getattr(document, field),
                    proposed[field],
                )
            }

            proposed_storage = {
                "storage_provider": storage_changes.get(
                    "storage_provider",
                    document.storage_provider,
                ),
                "storage_key": storage_changes.get(
                    "storage_key",
                    document.storage_key,
                ),
                "content_hash_sha256": storage_changes.get(
                    "content_hash_sha256",
                    document.content_hash_sha256,
                ),
            }
            storage_changed_fields = {
                field
                for field, new_value in proposed_storage.items()
                if not cls._values_equal(
                    getattr(document, field),
                    new_value,
                )
            }

            if "supersedes_document_id" in normalized:
                cls._validate_supersedes_lineage(
                    document.id,
                    proposed["supersedes_document_id"],
                )
                if (
                    "supersedes_document_id" in changed_fields
                    and document.is_fingerprint_duplicate
                ):
                    raise ResearchConflictError(
                        "document_duplicate",
                        "Document requires duplicate review",
                    )

            if (
                changed_fields
                and _REASON_REQUIRED_FIELDS.intersection(changed_fields)
            ):
                if not isinstance(reason, str) or not reason.strip():
                    raise ResearchValidationError(
                        {
                            "reason": [
                                "A non-empty reason is required for document changes"
                            ]
                        }
                    )

            fingerprint_for_duplicate = document.metadata_fingerprint
            dedup_checked = False

            if _CANONICAL_FINGERPRINT_FIELDS.intersection(changed_fields):
                company_ids = sorted(
                    {
                        link.company_id
                        for link in db.session.scalars(
                            sa.select(DocumentCompanyLink)
                            .where(
                                DocumentCompanyLink.document_id
                                == document_id
                            )
                            .with_for_update()
                        ).all()
                    }
                )
                institutional_metadata = db.session.get(
                    InstitutionalReportMetadata, document_id
                )
                proposed_fingerprint = (
                    DocumentDeduplicationService.metadata_fingerprint(
                        document_type=proposed["document_type"],
                        company_ids=company_ids,
                        document_date=proposed["document_date"],
                        title=proposed["title"],
                        publisher_name=proposed["publisher_name"],
                        institution_id=(
                            institutional_metadata.institution_id
                            if institutional_metadata is not None
                            else None
                        ),
                        reporting_period=proposed["reporting_period"],
                        report_type=(
                            institutional_metadata.report_type
                            if institutional_metadata is not None
                            else None
                        ),
                    )
                )
                fingerprint_for_duplicate = proposed_fingerprint

                if proposed_fingerprint != document.metadata_fingerprint:
                    decision = DocumentDeduplicationService.find_duplicate(
                        metadata_fingerprint=proposed_fingerprint,
                        content_hash_sha256=proposed_storage[
                            "content_hash_sha256"
                        ],
                        exclude_document_id=document.id,
                    )
                    dedup_checked = True
                    if decision.kind != "NONE":
                        raise ResearchConflictError(
                            "document_duplicate",
                            "Document requires duplicate review",
                        )
                    successor = (
                        cls._direct_successor_using_fingerprint_slot(
                            document
                        )
                    )
                    was_fingerprint_duplicate = (
                        document.is_fingerprint_duplicate
                    )
                    document.metadata_fingerprint = proposed_fingerprint
                    document.is_fingerprint_duplicate = False
                    db.session.flush([document])
                    if (
                        successor is not None
                        and not was_fingerprint_duplicate
                    ):
                        successor.is_fingerprint_duplicate = False

            was_stored = (
                document.ingestion_status
                in _STORED_INGESTION_STATUSES
            )
            will_be_stored = (
                proposed["ingestion_status"]
                in _STORED_INGESTION_STATUSES
            )
            content_hash_changed = (
                proposed_storage["content_hash_sha256"]
                != document.content_hash_sha256
            )
            binary_identity_established = (
                not was_stored
                and will_be_stored
                and proposed_storage["content_hash_sha256"] is not None
            )
            if (
                document.content_hash_sha256 is not None
                and content_hash_changed
            ):
                raise ResearchValidationError(
                    {
                        "content_hash_sha256": [
                            "Stored binary content identity cannot be "
                            "replaced in place; create a corrected/reissued "
                            "document"
                        ]
                    }
                )
            if (
                not dedup_checked
                and (
                    content_hash_changed
                    or binary_identity_established
                )
            ):
                lineage_metadata_matches = (
                    cls._same_fingerprint_lineage_document_ids(
                        document,
                        fingerprint_for_duplicate,
                    )
                )
                decision = DocumentDeduplicationService.find_duplicate(
                    metadata_fingerprint=fingerprint_for_duplicate,
                    content_hash_sha256=proposed_storage[
                        "content_hash_sha256"
                    ],
                    exclude_document_id=document.id,
                    ignored_metadata_match_document_ids=(
                        lineage_metadata_matches
                    ),
                )
                if decision.kind != "NONE":
                    raise ResearchConflictError(
                        "document_duplicate",
                        "Document requires duplicate review",
                    )

            audit_proposed = dict(proposed)
            audit_proposed.update(proposed_storage)
            audit_changed_fields = changed_fields | storage_changed_fields
            audit_events = cls._build_audit_events(
                document,
                audit_proposed,
                audit_changed_fields,
                actor_user_id,
                reason,
            )
            for event in audit_events:
                db.session.add(event)

            for field in normalized:
                setattr(document, field, proposed[field])

            if storage_changed_fields or binary_identity_established:
                cls._apply_storage_changes(
                    document,
                    content_hash_sha256=proposed_storage[
                        "content_hash_sha256"
                    ],
                    storage_provider=proposed_storage[
                        "storage_provider"
                    ],
                    storage_key=proposed_storage["storage_key"],
                )

            db.session.commit()
        except IntegrityError as error:
            db.session.rollback()
            if cls._is_document_fingerprint_unique_violation(error):
                raise ResearchConflictError(
                    "document_duplicate",
                    "Document requires duplicate review",
                ) from None
            raise
        except Exception:
            db.session.rollback()
            raise

        return document

    # ------------------------------------------------------------------
    # Rights-safe queries
    # ------------------------------------------------------------------

    @classmethod
    def get_document(
        cls,
        document_id: str,
        context: ResearchAccessContext,
    ) -> dict[str, object]:
        """Return one explicit policy projection or a non-revealing miss."""

        document = db.session.scalar(
            sa.select(Document)
            .where(Document.id == document_id)
            .options(
                selectinload(Document.institutional_metadata).selectinload(
                    InstitutionalReportMetadata.institution
                )
            )
        )
        if document is None:
            raise cls._document_not_found()

        decision = DocumentAccessPolicy.evaluate(document, context)
        if not decision.visible:
            raise cls._document_not_found()

        return cls._document_query_payload(document, decision, context)

    @classmethod
    def list_company_documents(
        cls,
        company_id: str,
        filters: dict,
        page: int,
        per_page: int,
        context: ResearchAccessContext,
    ) -> PageResult:
        """Return a rights-filtered, paginated document page."""

        _validate_document_page_bounds(page, per_page)
        filter_values = _validate_document_filters(filters)
        if not _company_exists(company_id):
            raise ResearchNotFoundError(
                "company_not_found", "Company was not found"
            )

        statement = cls._company_documents_statement(
            company_id,
            filter_values,
            context,
        )
        total_items = (
            db.session.scalar(
                sa.select(sa.func.count()).select_from(
                    statement.subquery()
                )
            )
            or 0
        )
        rows = db.session.scalars(
            statement.options(
                selectinload(Document.institutional_metadata).selectinload(
                    InstitutionalReportMetadata.institution
                )
            )
            .order_by(*_document_order_by())
            .offset((page - 1) * per_page)
            .limit(per_page)
        ).all()

        return PageResult(
            items=cls._document_query_items(rows, context),
            page=page,
            per_page=per_page,
            total_items=total_items,
            total_pages=max(
                1, (total_items + per_page - 1) // per_page
            ),
        )

    @classmethod
    def list_institutional_reports(
        cls,
        company_id: str,
        *,
        latest_per_institution: bool,
        page: int,
        per_page: int,
        context: ResearchAccessContext,
    ) -> PageResult:
        """Return rights-safe institutional metadata."""

        _validate_document_page_bounds(page, per_page)
        if not isinstance(latest_per_institution, bool):
            raise ResearchValidationError(
                {
                    "latest_per_institution": [
                        "Must be a boolean"
                    ]
                }
            )
        if not _company_exists(company_id):
            raise ResearchNotFoundError(
                "company_not_found", "Company was not found"
            )

        statement = (
            sa.select(Document)
            .join(
                DocumentCompanyLink,
                DocumentCompanyLink.document_id == Document.id,
            )
            .join(
                InstitutionalReportMetadata,
                InstitutionalReportMetadata.document_id == Document.id,
            )
            .where(
                DocumentCompanyLink.company_id == company_id,
                Document.document_type
                == DocumentType.INSTITUTIONAL_RESEARCH,
                _document_visibility_predicate(context),
            )
        )
        if latest_per_institution:
            latest_ids = cls._latest_institutional_report_ids(
                company_id,
                context,
            )
            statement = statement.where(Document.id.in_(latest_ids))

        total_items = (
            db.session.scalar(
                sa.select(sa.func.count()).select_from(
                    statement.subquery()
                )
            )
            or 0
        )
        rows = db.session.scalars(
            statement.options(
                selectinload(Document.institutional_metadata).selectinload(
                    InstitutionalReportMetadata.institution
                )
            )
            .order_by(*_document_order_by())
            .offset((page - 1) * per_page)
            .limit(per_page)
        ).all()

        return PageResult(
            items=cls._document_query_items(rows, context),
            page=page,
            per_page=per_page,
            total_items=total_items,
            total_pages=max(
                1, (total_items + per_page - 1) // per_page
            ),
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @classmethod
    def _company_documents_statement(
        cls,
        company_id: str,
        filters: dict[str, object],
        context: ResearchAccessContext,
    ):
        statement = (
            sa.select(Document)
            .join(
                DocumentCompanyLink,
                DocumentCompanyLink.document_id == Document.id,
            )
            .where(
                DocumentCompanyLink.company_id == company_id,
                _document_visibility_predicate(context),
            )
        )

        if (
            "institution_id" in filters
            or "report_type" in filters
        ):
            statement = statement.join(
                InstitutionalReportMetadata,
                InstitutionalReportMetadata.document_id == Document.id,
            )
        if "document_type" in filters:
            statement = statement.where(
                Document.document_type == filters["document_type"]
            )
        if "institution_id" in filters:
            statement = statement.where(
                InstitutionalReportMetadata.institution_id
                == filters["institution_id"]
            )
        if "report_type" in filters:
            statement = statement.where(
                InstitutionalReportMetadata.report_type
                == filters["report_type"]
            )
        if "date_from" in filters:
            statement = statement.where(
                Document.document_date >= filters["date_from"]
            )
        if "date_to" in filters:
            statement = statement.where(
                Document.document_date <= filters["date_to"]
            )

        return statement

    @classmethod
    def _latest_institutional_report_ids(
        cls,
        company_id: str,
        context: ResearchAccessContext,
    ):
        """Select the newest visible report per institution for one company."""

        ranked = (
            sa.select(
                InstitutionalReportMetadata.document_id.label(
                    "document_id"
                ),
                sa.func.row_number()
                .over(
                    partition_by=(
                        InstitutionalReportMetadata.institution_id
                    ),
                    order_by=_document_order_by(),
                )
                .label("report_rank"),
            )
            .join(
                Document,
                Document.id
                == InstitutionalReportMetadata.document_id,
            )
            .join(
                DocumentCompanyLink,
                DocumentCompanyLink.document_id == Document.id,
            )
            .where(
                DocumentCompanyLink.company_id == company_id,
                Document.document_type
                == DocumentType.INSTITUTIONAL_RESEARCH,
                _document_visibility_predicate(context),
            )
            .subquery()
        )
        return sa.select(ranked.c.document_id).where(
            ranked.c.report_rank == 1
        )

    @classmethod
    def _document_query_items(
        cls,
        documents: list[Document],
        context: ResearchAccessContext,
    ) -> list[dict[str, object]]:
        items: list[dict[str, object]] = []
        for document in documents:
            decision = DocumentAccessPolicy.evaluate(document, context)
            if decision.visible:
                items.append(
                    cls._document_query_payload(document, decision, context)
                )
        return items

    @classmethod
    def _document_query_payload(
        cls,
        document: Document,
        decision: DocumentAccessDecision,
        context: ResearchAccessContext,
    ) -> dict[str, object]:
        payload = DocumentAccessPolicy.project(document, decision)
        if "supersedes_document_id" in payload:
            predecessor = document.supersedes
            predecessor_visible = (
                predecessor is not None
                and DocumentAccessPolicy.evaluate(
                    predecessor, context
                ).visible
            )
            if not predecessor_visible:
                payload["supersedes_document_id"] = None
        institutional_report = cls._institutional_report_projection(
            document
        )
        if institutional_report is not None:
            payload["institutional_report"] = institutional_report
        return payload

    @staticmethod
    def _institutional_report_projection(
        document: Document,
    ) -> dict[str, object] | None:
        metadata = document.institutional_metadata
        if metadata is None:
            return None

        institution = metadata.institution
        return {
            "institution_id": metadata.institution_id,
            "institution_name": (
                institution.name
                if institution is not None
                else None
            ),
            "report_type": metadata.report_type,
        }

    @staticmethod
    def _document_not_found() -> ResearchNotFoundError:
        return ResearchNotFoundError(
            "document_not_found", "Document was not found"
        )

    @staticmethod
    def _resolve_companies(company_links: list[dict]) -> list[Company]:
        companies = []
        for link in company_links:
            company = db.session.get(Company, link["company_id"])
            if company is None:
                raise ResearchNotFoundError(
                    "company_not_found",
                    "Company was not found",
                )
            companies.append(company)
        return companies

    @staticmethod
    def _resolve_institution(institution_id: str) -> Institution:
        institution = db.session.get(Institution, institution_id)
        if institution is None:
            raise ResearchNotFoundError(
                "institution_not_found",
                "Institution was not found",
            )
        return institution

    @classmethod
    def _load_institution_values(
        cls,
        payload: dict,
        *,
        existing: Institution | None = None,
    ) -> dict:
        if not isinstance(payload, dict):
            raise ResearchValidationError(
                {"payload": ["Must be an object"]}
            )

        if existing is not None:
            unknown = sorted(set(payload) - {"name", "website"})
            if unknown:
                raise ResearchValidationError(
                    {field: ["Unknown field"] for field in unknown}
                )
            payload = {
                "name": payload.get("name", existing.name),
                "website": payload.get("website", existing.website),
            }

        try:
            return InstitutionCreateSchema().load(payload)
        except ValidationError as exc:
            details: dict[str, list[str]] = {}
            for field, messages in exc.messages.items():
                if isinstance(messages, list):
                    details[field] = [
                        str(message) for message in messages
                    ]
                elif isinstance(messages, dict):
                    for nested_field, nested_messages in messages.items():
                        key = f"{field}.{nested_field}"
                        if isinstance(nested_messages, list):
                            details[key] = [
                                str(message)
                                for message in nested_messages
                            ]
                        else:
                            details[key] = [str(nested_messages)]
                else:
                    details[field] = [str(messages)]
            raise ResearchValidationError(details) from exc

    @staticmethod
    def _normalize_institution_name(name: str) -> str:
        normalized = normalize_fingerprint_text(name)
        if not normalized:
            raise ResearchValidationError(
                {"name": ["Must not be blank"]}
            )
        return normalized

    @staticmethod
    def _is_institution_name_unique_violation(
        error: IntegrityError,
    ) -> bool:
        original = error.orig
        if original is None:
            return False
        message = str(original)
        return (
            "institution.normalized_name" in message
            or "uq_institution_normalized_name" in message
            or "UNIQUE constraint failed: institution.normalized_name"
            in message
        )

    @staticmethod
    def _is_document_fingerprint_unique_violation(
        error: IntegrityError,
    ) -> bool:
        original = error.orig
        if original is None:
            return False
        message = str(original)
        return (
            "document.metadata_fingerprint" in message
            or "uq_document_metadata_fingerprint_ordinary" in message
            or "uq_document_metadata_fingerprint_successor" in message
            or "UNIQUE constraint failed: document.metadata_fingerprint"
            in message
        )

    @staticmethod
    def _company_link_snapshot(
        links: list[DocumentCompanyLink],
        metadata_fingerprint: str,
    ) -> dict[str, object]:
        """Build the audit-only company-link snapshot.

        ``COMPANY_LINKS_CHANGED`` audit rows contain sorted canonical company
        IDs, the optional primary company ID, and the corresponding metadata
        fingerprint. No document title, source reference, storage reference,
        or content is included.
        """

        company_ids = sorted({link.company_id for link in links})
        primary_company_id = next(
            (
                link.company_id
                for link in links
                if link.is_primary
            ),
            None,
        )
        return {
            "company_ids": company_ids,
            "primary_company_id": primary_company_id,
            "metadata_fingerprint": metadata_fingerprint,
        }

    @classmethod
    def _build_audit_events(
        cls,
        document: Document,
        proposed: dict[str, object],
        changed_fields: set[str],
        actor_user_id: str,
        reason: str | None,
    ) -> list[DocumentAuditEvent]:
        """Build one focused audit row for each meaningful changed field."""

        events: list[DocumentAuditEvent] = []

        for field in sorted(
            _METADATA_AUDIT_FIELDS.intersection(changed_fields)
        ):
            events.append(
                DocumentAuditEvent(
                    document_id=document.id,
                    event_type=DocumentAuditEventType.METADATA_CHANGED,
                    field_changed=field,
                    old_value=cls._json_safe(getattr(document, field)),
                    new_value=cls._json_safe(proposed[field]),
                    actor_user_id=actor_user_id,
                    reason=reason,
                )
            )

        for field, event_type in _STATE_AUDIT_FIELDS.items():
            if field in changed_fields:
                events.append(
                    DocumentAuditEvent(
                        document_id=document.id,
                        event_type=event_type,
                        field_changed=field,
                        old_value=cls._json_safe(
                            getattr(document, field)
                        ),
                        new_value=cls._json_safe(proposed[field]),
                        actor_user_id=actor_user_id,
                        reason=reason,
                    )
                )

        if _STORAGE_FIELDS.intersection(changed_fields):
            events.append(
                DocumentAuditEvent(
                    document_id=document.id,
                    event_type=DocumentAuditEventType.STORAGE_ATTACHED,
                    field_changed="storage_attachment",
                    old_value=cls._storage_snapshot(document),
                    new_value=cls._storage_snapshot(proposed),
                    actor_user_id=actor_user_id,
                    reason=reason,
                )
            )

        if _RIGHTS_FIELDS.intersection(changed_fields):
            old_rights = cls._rights_snapshot(document)
            new_rights = cls._rights_snapshot(proposed)
            event_type = (
                DocumentAuditEventType.RIGHTS_VERIFIED
                if cls._rights_are_verified(new_rights)
                else DocumentAuditEventType.RIGHTS_VERIFICATION_REVOKED
            )
            events.append(
                DocumentAuditEvent(
                    document_id=document.id,
                    event_type=event_type,
                    field_changed="rights_verification",
                    old_value=old_rights,
                    new_value=new_rights,
                    actor_user_id=actor_user_id,
                    reason=reason,
                )
            )

        if "archived_at" in changed_fields:
            event_type = (
                DocumentAuditEventType.ARCHIVED
                if proposed["archived_at"] is not None
                else DocumentAuditEventType.RESTORED
            )
            events.append(
                DocumentAuditEvent(
                    document_id=document.id,
                    event_type=event_type,
                    field_changed="archived_at",
                    old_value=cls._json_safe(document.archived_at),
                    new_value=cls._json_safe(proposed["archived_at"]),
                    actor_user_id=actor_user_id,
                    reason=reason,
                )
            )

        return events

    @staticmethod
    def _validate_supersedes_lineage(
        document_id: str | None,
        supersedes_document_id: object,
    ) -> None:
        """Reject a predecessor pointer that reaches itself or another cycle."""

        if supersedes_document_id is None:
            return

        predecessor_id = str(supersedes_document_id)
        visited: set[str] = set()
        while predecessor_id is not None:
            if predecessor_id == document_id or predecessor_id in visited:
                raise ResearchValidationError(
                    {
                        "supersedes_document_id": [
                            "Corrected/reissued lineage cannot contain a cycle"
                        ]
                    }
                )
            visited.add(predecessor_id)
            predecessor_id = db.session.scalar(
                sa.select(Document.supersedes_document_id)
                .where(Document.id == predecessor_id)
                .with_for_update()
            )

    @staticmethod
    def _direct_successor_using_fingerprint_slot(
        document: Document,
    ) -> Document | None:
        """Return the direct successor holding this fingerprint's slot 1."""

        return db.session.scalar(
            sa.select(Document)
            .where(
                Document.supersedes_document_id == document.id,
                Document.metadata_fingerprint
                == document.metadata_fingerprint,
                Document.is_fingerprint_duplicate.is_(True),
            )
            .with_for_update()
        )

    @classmethod
    def _same_fingerprint_lineage_document_ids(
        cls,
        document: Document,
        metadata_fingerprint: str,
    ) -> set[str]:
        """Return lineage relatives sharing the supplied fingerprint.

        Corrected/reissued chains may contain multiple rows with the same
        canonical fingerprint. When a node in such a chain receives a
        non-canonical binary/storage update, those relatives are valid
        lineage records rather than ordinary duplicates.
        """

        related_ids: set[str] = set()

        ancestor_id = document.supersedes_document_id
        visited_ancestor_ids: set[str] = set()
        while ancestor_id is not None and ancestor_id not in visited_ancestor_ids:
            visited_ancestor_ids.add(ancestor_id)
            ancestor = db.session.get(Document, ancestor_id)
            if ancestor is None:
                break
            if ancestor.metadata_fingerprint == metadata_fingerprint:
                related_ids.add(ancestor.id)
            ancestor_id = ancestor.supersedes_document_id

        pending_ids = [document.id]
        visited_descendant_ids = {document.id}
        while pending_ids:
            parent_id = pending_ids.pop()
            child_ids = db.session.scalars(
                sa.select(Document.id)
                .where(Document.supersedes_document_id == parent_id)
                .with_for_update()
            ).all()
            for child_id in child_ids:
                if child_id in visited_descendant_ids:
                    continue
                visited_descendant_ids.add(child_id)
                child = db.session.get(Document, child_id)
                if child is None:
                    continue
                if child.metadata_fingerprint == metadata_fingerprint:
                    related_ids.add(child.id)
                pending_ids.append(child.id)

        return related_ids

    @staticmethod
    def _values_equal(left: object, right: object) -> bool:
        """Compare two values, treating UTC datetimes as timezone-insensitive."""

        if isinstance(left, datetime) and isinstance(right, datetime):
            left_utc = (
                left
                if left.tzinfo is not None
                else left.replace(tzinfo=timezone.utc)
            )
            right_utc = (
                right
                if right.tzinfo is not None
                else right.replace(tzinfo=timezone.utc)
            )
            return left_utc.astimezone(timezone.utc).replace(
                tzinfo=None
            ) == right_utc.astimezone(timezone.utc).replace(tzinfo=None)

        return left == right

    @staticmethod
    def _json_safe(value: object) -> object:
        """Convert temporal values to JSON-safe ISO strings."""

        if isinstance(value, datetime):
            if value.tzinfo is None:
                value = value.replace(tzinfo=timezone.utc)
            return value.astimezone(timezone.utc).isoformat()
        if isinstance(value, date):
            return value.isoformat()
        return value

    @classmethod
    def _storage_snapshot(cls, source: object) -> dict[str, object]:
        """Return an auditable storage/binary identity snapshot.

        The raw storage key is never included. Its deterministic digest and
        the content hash are included so a storage replacement can be traced
        without exposing the opaque object reference.
        """

        if isinstance(source, dict):
            get = source.get
        else:
            get = lambda field: getattr(source, field)  # noqa: E731

        storage_key = get("storage_key")
        return {
            "storage_provider": get("storage_provider"),
            "storage_key_sha256": cls._sha256_digest(storage_key),
            "content_hash_sha256": get("content_hash_sha256"),
            "mime_type": get("mime_type"),
            "file_size_bytes": get("file_size_bytes"),
        }

    @staticmethod
    def _sha256_digest(value: object) -> str | None:
        """Return a stable SHA-256 digest for an opaque audit value."""

        if value is None:
            return None
        return hashlib.sha256(str(value).encode("utf-8")).hexdigest()

    @staticmethod
    def _find_or_create_content(sha256: str) -> DocumentContent:
        """Return the shared immutable content identity for a SHA-256."""

        content = db.session.scalar(
            sa.select(DocumentContent).where(
                DocumentContent.sha256 == sha256
            )
        )
        if content is not None:
            return content

        content = DocumentContent(sha256=sha256)
        db.session.add(content)
        db.session.flush()
        return content

    @staticmethod
    def _add_storage_location(
        content: DocumentContent,
        *,
        provider: str | None,
        storage_key: str | None,
    ) -> DocumentStorageLocation:
        location = DocumentStorageLocation(
            content_id=content.id,
            provider=provider,
            storage_key=storage_key,
        )
        db.session.add(location)
        db.session.flush()
        return location

    @classmethod
    def _apply_storage_changes(
        cls,
        document: Document,
        *,
        content_hash_sha256: str | None,
        storage_provider: str | None,
        storage_key: str | None,
    ) -> None:
        """Attach immutable content identity without mutating old bytes."""

        if content_hash_sha256 is None:
            return

        content = cls._find_or_create_content(content_hash_sha256)
        document.content = content
        if storage_provider is not None or storage_key is not None:
            cls._add_storage_location(
                content,
                provider=storage_provider,
                storage_key=storage_key,
            )

    @classmethod
    def _rights_snapshot(cls, source: object) -> dict[str, object]:
        """Return the complete rights-verification evidence snapshot."""

        if isinstance(source, dict):
            get = source.get
        else:
            get = lambda field: getattr(source, field)  # noqa: E731

        return {
            "distribution_basis": get("distribution_basis"),
            "rights_verified_by_user_id": get(
                "rights_verified_by_user_id"
            ),
            "rights_verified_at": cls._json_safe(
                get("rights_verified_at")
            ),
        }

    @staticmethod
    def _rights_are_verified(rights: dict[str, object]) -> bool:
        return all(value is not None for value in rights.values())
