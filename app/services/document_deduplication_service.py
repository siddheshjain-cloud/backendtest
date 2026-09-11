"""Deterministic document fingerprints and duplicate decisions.

Every document type uses the same canonical payload. Optional scalar values
use an explicit ``<NULL>`` sentinel so absent metadata cannot collide with a
present empty-looking value, and company IDs use the complete sorted unique
set so link order and primary designation cannot change a fingerprint.
"""

from __future__ import annotations

import hashlib
import json
import unicodedata
from dataclasses import dataclass
from datetime import date

import sqlalchemy as sa

from app import db
from app.models.document import Document


FINGERPRINT_VERSION = 1

_NULL_SENTINEL = "<NULL>"


def normalize_fingerprint_text(value: str | None) -> str | None:
    """Normalize text without removing normalized punctuation."""

    if value is None:
        return None

    normalized = unicodedata.normalize("NFKC", value)
    normalized = " ".join(normalized.split())
    return normalized.casefold()


def _canonical_scalar(value: str | None) -> str:
    normalized = normalize_fingerprint_text(value)
    return normalized if normalized else _NULL_SENTINEL


def _canonical_document_type(value: str) -> str:
    """Canonicalize the type while retaining its uppercase enum value."""

    normalized = unicodedata.normalize("NFKC", value)
    return " ".join(normalized.split())


@dataclass(frozen=True)
class DuplicateDecision:
    """Immutable outcome of a duplicate lookup."""

    kind: str
    matched_document_id: str | None


class DocumentDeduplicationService:
    """Build canonical fingerprints and classify duplicate candidates."""

    @staticmethod
    def canonical_payload(
        *,
        document_type: str,
        company_ids: list[str],
        document_date: date | None,
        title: str,
        publisher_name: str | None,
        institution_id: str | None,
        reporting_period: str | None,
        report_type: str | None,
    ) -> dict[str, object]:
        """Return the exact payload hashed for every document type."""

        normalized_company_ids = {
            normalized
            for company_id in company_ids
            if (normalized := normalize_fingerprint_text(company_id))
        }
        normalized_institution_id = normalize_fingerprint_text(
            institution_id
        )
        publisher_or_institution = (
            normalized_institution_id
            if normalized_institution_id
            else _canonical_scalar(publisher_name)
        )

        return {
            "version": FINGERPRINT_VERSION,
            "document_type": _canonical_document_type(document_type),
            "company_ids": sorted(normalized_company_ids),
            "document_date": (
                document_date.isoformat()
                if document_date is not None
                else _NULL_SENTINEL
            ),
            "title": _canonical_scalar(title),
            "publisher_or_institution": publisher_or_institution,
            "reporting_period": _canonical_scalar(reporting_period),
            "institutional_report_type": _canonical_scalar(report_type),
        }

    @staticmethod
    def metadata_fingerprint(
        *,
        document_type: str,
        company_ids: list[str],
        document_date: date | None,
        title: str,
        publisher_name: str | None,
        institution_id: str | None,
        reporting_period: str | None,
        report_type: str | None,
    ) -> str:
        """Hash the canonical payload as stable UTF-8 JSON."""

        payload = DocumentDeduplicationService.canonical_payload(
            document_type=document_type,
            company_ids=company_ids,
            document_date=document_date,
            title=title,
            publisher_name=publisher_name,
            institution_id=institution_id,
            reporting_period=reporting_period,
            report_type=report_type,
        )
        canonical_json = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()

    @staticmethod
    def find_duplicate(
        *,
        metadata_fingerprint: str,
        content_hash_sha256: str | None,
        exclude_document_id: str | None = None,
        ignored_metadata_match_document_ids: set[str] | None = None,
    ) -> DuplicateDecision:
        """Classify a candidate using binary evidence before metadata.

        ``exclude_document_id`` removes the row being updated from both
        content and metadata lookups. ``ignored_metadata_match_document_ids``
        removes only valid same-fingerprint lineage relatives from the
        metadata lookup, so a lineage node can be maintained without being
        mistaken for an unrelated ordinary duplicate. Binary matches remain
        authoritative even for those relatives.
        """

        identity_filters: list[object] = []
        if exclude_document_id is not None:
            identity_filters.append(Document.id != exclude_document_id)

        if content_hash_sha256:
            content_match = db.session.scalar(
                sa.select(Document)
                .where(
                    Document.content_hash_sha256 == content_hash_sha256,
                    *identity_filters,
                )
                .order_by(Document.created_at, Document.id)
                .limit(1)
            )
            if content_match is not None:
                if (
                    content_match.metadata_fingerprint
                    == metadata_fingerprint
                ):
                    return DuplicateDecision(
                        kind="EXACT_BINARY",
                        matched_document_id=content_match.id,
                    )
                return DuplicateDecision(
                    kind="BINARY_METADATA_CONFLICT",
                    matched_document_id=content_match.id,
                )

        metadata_filters = [
            Document.metadata_fingerprint == metadata_fingerprint,
            *identity_filters,
        ]
        if ignored_metadata_match_document_ids:
            metadata_filters.append(
                Document.id.not_in(
                    ignored_metadata_match_document_ids
                )
            )

        metadata_match = db.session.scalar(
            sa.select(Document)
            .where(*metadata_filters)
            .limit(1)
        )
        if metadata_match is not None:
            return DuplicateDecision(
                kind="METADATA_MATCH",
                matched_document_id=metadata_match.id,
            )

        return DuplicateDecision(kind="NONE", matched_document_id=None)
