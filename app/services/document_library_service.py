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
