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

from datetime import date, datetime, timezone

from marshmallow import ValidationError
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from app import db
from app.models import (
    Company,
    Document,
    DocumentAuditEvent,
    DocumentAuditEventType,
    DocumentCompanyLink,
    Institution,
    InstitutionalReportMetadata,
)
from app.schemas.document import InstitutionCreateSchema
from app.services.document_deduplication_service import (
    DocumentDeduplicationService,
    normalize_fingerprint_text,
)
from app.services.document_validation_service import (
    DocumentValidationService,
)
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

_RIGHTS_FIELDS = frozenset(
    {
        "distribution_basis",
        "rights_verified_by_user_id",
        "rights_verified_at",
    }
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
                content_hash_sha256=document_values.get(
                    "content_hash_sha256"
                ),
            )
            if decision.kind != "NONE":
                raise ResearchConflictError(
                    "document_duplicate",
                    "Document requires duplicate review",
                )

            document = Document(
                created_by_user_id=actor_user_id,
                metadata_fingerprint=fingerprint,
                **document_values,
            )
            db.session.add(document)
            db.session.flush()

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

            new_link = DocumentCompanyLink(
                document_id=document_id,
                company_id=company_id,
                is_primary=is_primary,
            )
            db.session.add(new_link)
            document.metadata_fingerprint = proposed_fingerprint

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

            normalized = DocumentValidationService.validate_patch(
                document, changes
            )
            DocumentValidationService.validate_transition(
                document, changes
            )

            proposed = {
                field: getattr(document, field)
                for field in _PATCHABLE_FIELDS
            }
            proposed.update(normalized)

            changed_fields = {
                field
                for field in normalized
                if not cls._values_equal(
                    getattr(document, field),
                    proposed[field],
                )
            }

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

                if proposed_fingerprint != document.metadata_fingerprint:
                    decision = DocumentDeduplicationService.find_duplicate(
                        metadata_fingerprint=proposed_fingerprint,
                        content_hash_sha256=proposed[
                            "content_hash_sha256"
                        ],
                    )
                    if decision.kind != "NONE":
                        raise ResearchConflictError(
                            "document_duplicate",
                            "Document requires duplicate review",
                        )
                    document.metadata_fingerprint = proposed_fingerprint

            audit_events = cls._build_audit_events(
                document,
                proposed,
                changed_fields,
                actor_user_id,
                reason,
            )
            for event in audit_events:
                db.session.add(event)

            for field in normalized:
                setattr(document, field, proposed[field])

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
    # Helpers
    # ------------------------------------------------------------------

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
            or "uq_document_metadata_fingerprint" in message
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
        """Return a storage audit snapshot without keys, hashes, or content."""

        if isinstance(source, dict):
            get = source.get
        else:
            get = lambda field: getattr(source, field)  # noqa: E731

        return {
            "storage_provider": get("storage_provider"),
            "mime_type": get("mime_type"),
            "file_size_bytes": get("file_size_bytes"),
            "storage_reference": (
                "attached" if get("storage_key") else None
            ),
            "content_attached": get("content_hash_sha256") is not None,
        }

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
