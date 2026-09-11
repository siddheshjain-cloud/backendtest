"""Milestone 1 common Document Library models.

One library holds institutional research, annual reports, investor
presentations, concalls, Screener references, Reg. 30 attachments, credit
rating reports, industry reports, and other research material. Provenance
(``source_access``), acquisition, distribution, and ingestion are independent
dimensions; public accessibility never implies redistribution rights.

``storage_provider`` and ``storage_key`` are opaque internal references and
are never returned by a service projection or API schema. Distribution rights
are recorded on the row and are enforced by the policy, validation, and
command layers that later tasks own; this module only fixes the persistence
contract.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import sqlalchemy as sa
import sqlalchemy.orm as so

from app import db
from app.models.base import BaseModel
from app.models.research_types import enum_type


class DocumentType:
    ANNUAL_REPORT = "ANNUAL_REPORT"
    QUARTERLY_RESULTS = "QUARTERLY_RESULTS"
    INVESTOR_PRESENTATION = "INVESTOR_PRESENTATION"
    CONCALL = "CONCALL"
    SCREENER = "SCREENER"
    REG30_ATTACHMENT = "REG30_ATTACHMENT"
    CREDIT_RATING_REPORT = "CREDIT_RATING_REPORT"
    INDUSTRY_REPORT = "INDUSTRY_REPORT"
    INSTITUTIONAL_RESEARCH = "INSTITUTIONAL_RESEARCH"
    OTHER = "OTHER"


class DiscoverySourceType:
    OFFICIAL_SITE = "OFFICIAL_SITE"
    EXCHANGE = "EXCHANGE"
    TELEGRAM = "TELEGRAM"
    EMAIL = "EMAIL"
    SEARCH = "SEARCH"
    USER = "USER"
    OTHER = "OTHER"


class SourceAccess:
    PUBLIC = "PUBLIC"
    RESTRICTED = "RESTRICTED"
    UNKNOWN = "UNKNOWN"


class AcquisitionMethod:
    PUBLIC_DOWNLOAD = "PUBLIC_DOWNLOAD"
    USER_UPLOAD = "USER_UPLOAD"
    MANUAL_REFERENCE = "MANUAL_REFERENCE"
    NOT_ACQUIRED = "NOT_ACQUIRED"


class DistributionStatus:
    UNKNOWN = "UNKNOWN"
    LINK_ONLY = "LINK_ONLY"
    PRIVATE_LIBRARY = "PRIVATE_LIBRARY"
    APP_DISTRIBUTABLE = "APP_DISTRIBUTABLE"


class IngestionStatus:
    DISCOVERED = "DISCOVERED"
    AWAITING_UPLOAD = "AWAITING_UPLOAD"
    STORED = "STORED"
    ANALYSED = "ANALYSED"


class OriginalPublicationPrecision:
    """How precisely SPA knows the original publisher publication time."""

    DATE = "DATE"
    DATETIME = "DATETIME"
    UNKNOWN = "UNKNOWN"


class DocumentAuditEventType:
    METADATA_CHANGED = "METADATA_CHANGED"
    SOURCE_ACCESS_CHANGED = "SOURCE_ACCESS_CHANGED"
    ACQUISITION_METHOD_CHANGED = "ACQUISITION_METHOD_CHANGED"
    DISTRIBUTION_STATUS_CHANGED = "DISTRIBUTION_STATUS_CHANGED"
    INGESTION_STATUS_CHANGED = "INGESTION_STATUS_CHANGED"
    RIGHTS_VERIFIED = "RIGHTS_VERIFIED"
    RIGHTS_VERIFICATION_REVOKED = "RIGHTS_VERIFICATION_REVOKED"
    STORAGE_ATTACHED = "STORAGE_ATTACHED"
    COMPANY_LINKS_CHANGED = "COMPANY_LINKS_CHANGED"
    ARCHIVED = "ARCHIVED"
    RESTORED = "RESTORED"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Document(BaseModel):
    """One reference or stored copy in the common Document Library."""

    __tablename__ = "document"

    document_type: so.Mapped[str] = so.mapped_column(
        enum_type(
            "document_type",
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
            ),
        ),
        nullable=False,
    )
    title: so.Mapped[str] = so.mapped_column(sa.String(300), nullable=False)
    document_date: so.Mapped[date | None] = so.mapped_column(
        sa.Date, nullable=True
    )
    # Explicit corrected/reissued lineage: a corrected document points at the
    # earlier document it replaces. The predecessor remains a separate,
    # immutable historical row. NULL means an ordinary, non-superseding record.
    supersedes_document_id: so.Mapped[str | None] = so.mapped_column(
        sa.ForeignKey("document.id"), nullable=True
    )
    # The original publisher's first-publication time is distinct from SPA
    # record creation and from discovery/acquisition/ingestion. DATE precision
    # never fabricates a midnight timestamp; UNKNOWN makes no claim.
    original_published_date: so.Mapped[date | None] = so.mapped_column(
        sa.Date, nullable=True
    )
    original_published_at: so.Mapped[datetime | None] = so.mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )
    original_published_at_precision: so.Mapped[str] = so.mapped_column(
        enum_type(
            "document_original_publication_precision",
            (
                OriginalPublicationPrecision.DATE,
                OriginalPublicationPrecision.DATETIME,
                OriginalPublicationPrecision.UNKNOWN,
            ),
        ),
        nullable=False,
        default=OriginalPublicationPrecision.UNKNOWN,
    )
    reporting_period: so.Mapped[str | None] = so.mapped_column(
        sa.String(64), nullable=True
    )
    publisher_name: so.Mapped[str | None] = so.mapped_column(
        sa.String(200), nullable=True
    )
    publisher_reference: so.Mapped[str | None] = so.mapped_column(
        sa.String(1000), nullable=True
    )
    original_source_url: so.Mapped[str | None] = so.mapped_column(
        sa.String(1000), nullable=True
    )
    discovery_source_type: so.Mapped[str] = so.mapped_column(
        enum_type(
            "document_discovery_source_type",
            (
                DiscoverySourceType.OFFICIAL_SITE,
                DiscoverySourceType.EXCHANGE,
                DiscoverySourceType.TELEGRAM,
                DiscoverySourceType.EMAIL,
                DiscoverySourceType.SEARCH,
                DiscoverySourceType.USER,
                DiscoverySourceType.OTHER,
            ),
        ),
        nullable=False,
    )
    discovery_source_reference: so.Mapped[str | None] = so.mapped_column(
        sa.String(1000), nullable=True
    )
    source_access: so.Mapped[str] = so.mapped_column(
        enum_type(
            "document_source_access",
            (
                SourceAccess.PUBLIC,
                SourceAccess.RESTRICTED,
                SourceAccess.UNKNOWN,
            ),
        ),
        nullable=False,
    )
    acquisition_method: so.Mapped[str] = so.mapped_column(
        enum_type(
            "document_acquisition_method",
            (
                AcquisitionMethod.PUBLIC_DOWNLOAD,
                AcquisitionMethod.USER_UPLOAD,
                AcquisitionMethod.MANUAL_REFERENCE,
                AcquisitionMethod.NOT_ACQUIRED,
            ),
        ),
        nullable=False,
    )
    distribution_status: so.Mapped[str] = so.mapped_column(
        enum_type(
            "document_distribution_status",
            (
                DistributionStatus.UNKNOWN,
                DistributionStatus.LINK_ONLY,
                DistributionStatus.PRIVATE_LIBRARY,
                DistributionStatus.APP_DISTRIBUTABLE,
            ),
        ),
        nullable=False,
    )
    ingestion_status: so.Mapped[str] = so.mapped_column(
        enum_type(
            "document_ingestion_status",
            (
                IngestionStatus.DISCOVERED,
                IngestionStatus.AWAITING_UPLOAD,
                IngestionStatus.STORED,
                IngestionStatus.ANALYSED,
            ),
        ),
        nullable=False,
    )
    storage_provider: so.Mapped[str | None] = so.mapped_column(
        sa.String(50), nullable=True
    )
    storage_key: so.Mapped[str | None] = so.mapped_column(
        sa.String(500), nullable=True
    )
    content_hash_sha256: so.Mapped[str | None] = so.mapped_column(
        sa.String(64), nullable=True
    )
    metadata_fingerprint: so.Mapped[str] = so.mapped_column(
        sa.String(64), nullable=False
    )
    mime_type: so.Mapped[str | None] = so.mapped_column(
        sa.String(100), nullable=True
    )
    file_size_bytes: so.Mapped[int | None] = so.mapped_column(
        sa.BigInteger, nullable=True
    )
    provided_by_user_id: so.Mapped[str | None] = so.mapped_column(
        sa.ForeignKey("user.id"), nullable=True
    )
    distribution_basis: so.Mapped[str | None] = so.mapped_column(
        sa.String(1000), nullable=True
    )
    rights_verified_by_user_id: so.Mapped[str | None] = so.mapped_column(
        sa.ForeignKey("user.id"), nullable=True
    )
    rights_verified_at: so.Mapped[datetime | None] = so.mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )
    created_by_user_id: so.Mapped[str] = so.mapped_column(
        sa.ForeignKey("user.id"), nullable=False
    )
    updated_at: so.Mapped[datetime] = so.mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
        onupdate=_utcnow,
    )
    archived_at: so.Mapped[datetime | None] = so.mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        sa.UniqueConstraint(
            "metadata_fingerprint",
            name="uq_document_metadata_fingerprint",
        ),
        sa.CheckConstraint(
            "supersedes_document_id IS NULL OR id != supersedes_document_id",
            name="ck_document_not_self_superseding",
        ),
    )

    # Unidirectional relationships; no reverse column or back-reference is
    # added to the legacy User model. ``storage_key`` is never projected.
    company_links: so.Mapped[list["DocumentCompanyLink"]] = so.relationship(
        "DocumentCompanyLink",
        back_populates="document",
        cascade="all, delete-orphan",
        order_by="DocumentCompanyLink.company_id",
    )
    supersedes: so.Mapped["Document | None"] = so.relationship(
        "Document",
        remote_side="Document.id",
        foreign_keys=[supersedes_document_id],
        viewonly=True,
        uselist=False,
    )
    superseded_by: so.Mapped[list["Document"]] = so.relationship(
        "Document",
        foreign_keys=[supersedes_document_id],
        viewonly=True,
    )
    institutional_metadata: so.Mapped[
        "InstitutionalReportMetadata | None"
    ] = so.relationship(
        "InstitutionalReportMetadata",
        back_populates="document",
        uselist=False,
        cascade="all, delete-orphan",
    )
    provided_by_user: so.Mapped["User | None"] = so.relationship(
        "User", foreign_keys=[provided_by_user_id], viewonly=True
    )
    rights_verified_by_user: so.Mapped["User | None"] = so.relationship(
        "User", foreign_keys=[rights_verified_by_user_id], viewonly=True
    )
    created_by_user: so.Mapped["User"] = so.relationship(
        "User", foreign_keys=[created_by_user_id], viewonly=True
    )

    def __repr__(self) -> str:
        return f"<Document {self.document_type} {self.title!r}>"


class DocumentCompanyLink(db.Model):
    """Composite link between one document and one linked company."""

    __tablename__ = "document_company_link"

    document_id: so.Mapped[str] = so.mapped_column(
        sa.ForeignKey("document.id"), primary_key=True
    )
    company_id: so.Mapped[str] = so.mapped_column(
        sa.ForeignKey("company.id"), primary_key=True
    )
    is_primary: so.Mapped[bool] = so.mapped_column(
        sa.Boolean,
        nullable=False,
        default=False,
        server_default=sa.false(),
    )
    created_at: so.Mapped[datetime] = so.mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
    )

    __table_args__ = (
        # A filtered unique index permits at most one primary link per
        # document on dialects that support partial indexes. Dialects without
        # that support rely on service-level enforcement.
        sa.Index(
            "uq_document_company_link_primary",
            "document_id",
            unique=True,
            sqlite_where=sa.text("is_primary = 1"),
            postgresql_where=sa.text("is_primary"),
        ),
    )

    document: so.Mapped["Document"] = so.relationship(
        "Document", back_populates="company_links"
    )
    company: so.Mapped["Company"] = so.relationship("Company", viewonly=True)

    def __repr__(self) -> str:
        return f"<DocumentCompanyLink {self.document_id} {self.company_id}>"


class DocumentAuditEvent(BaseModel):
    """Focused append-only audit row for one meaningful document change."""

    __tablename__ = "document_audit_event"

    document_id: so.Mapped[str] = so.mapped_column(
        sa.ForeignKey("document.id"), nullable=False
    )
    event_type: so.Mapped[str] = so.mapped_column(
        enum_type(
            "document_audit_event_type",
            (
                DocumentAuditEventType.METADATA_CHANGED,
                DocumentAuditEventType.SOURCE_ACCESS_CHANGED,
                DocumentAuditEventType.ACQUISITION_METHOD_CHANGED,
                DocumentAuditEventType.DISTRIBUTION_STATUS_CHANGED,
                DocumentAuditEventType.INGESTION_STATUS_CHANGED,
                DocumentAuditEventType.RIGHTS_VERIFIED,
                DocumentAuditEventType.RIGHTS_VERIFICATION_REVOKED,
                DocumentAuditEventType.STORAGE_ATTACHED,
                DocumentAuditEventType.COMPANY_LINKS_CHANGED,
                DocumentAuditEventType.ARCHIVED,
                DocumentAuditEventType.RESTORED,
            ),
        ),
        nullable=False,
    )
    field_changed: so.Mapped[str | None] = so.mapped_column(
        sa.String(100), nullable=True
    )
    old_value: so.Mapped[object | None] = so.mapped_column(
        sa.JSON, nullable=True
    )
    new_value: so.Mapped[object | None] = so.mapped_column(
        sa.JSON, nullable=True
    )
    actor_user_id: so.Mapped[str] = so.mapped_column(
        sa.ForeignKey("user.id"), nullable=False
    )
    reason: so.Mapped[str | None] = so.mapped_column(
        sa.String(1000), nullable=True
    )

    # Unidirectional read relationships; no reverse column or back-reference
    # is added to the legacy User model.
    document: so.Mapped["Document"] = so.relationship(
        "Document", viewonly=True
    )
    actor_user: so.Mapped["User"] = so.relationship("User", viewonly=True)

    def __repr__(self) -> str:
        return f"<DocumentAuditEvent {self.document_id} {self.event_type}>"


def _reject_audit_update(_mapper, _connection, target) -> None:
    raise sa.exc.InvalidRequestError(
        "DocumentAuditEvent is append-only and cannot be updated"
    )


def _reject_audit_delete(_mapper, _connection, target) -> None:
    raise sa.exc.InvalidRequestError(
        "DocumentAuditEvent is append-only and cannot be deleted"
    )


sa.event.listen(
    DocumentAuditEvent, "before_update", _reject_audit_update
)
sa.event.listen(
    DocumentAuditEvent, "before_delete", _reject_audit_delete
)
