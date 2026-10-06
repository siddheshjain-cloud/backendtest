"""Translate one SPA manifest ``Documents`` row into an M1 Document
Library ``create_document`` payload.

Pure function, no I/O and no SQLAlchemy -- ``company_id`` must already
be resolved (fail-closed, see ``company_resolver.py``) before calling
this. As approved in the bridge design:

* ``document_type``/``discovery_source_type`` use a deterministic lookup
  table with an explicit ``OTHER`` fallback -- never a guess -- and the
  original SPA values are always preserved in
  ``discovery_source_reference`` so an ``OTHER``-bucketed row stays
  traceable and reversible without a schema change.
* ``distribution_status`` is deliberately left out of the payload so
  ``DocumentValidationService``'s own conservative default applies,
  rather than the bridge inventing a second rights policy.
* ``original_published_date``/``_at``/``_precision`` are left at M1's
  own ``UNKNOWN`` default -- SPA's ``document_date`` is a filing/
  recording date, not an independently verified original-publication
  timestamp, and fabricating precision is exactly what M1's own model
  docstring warns against.
"""

from __future__ import annotations

DOCUMENT_TYPE_MAP = {
    "ANNUAL_REPORT": "ANNUAL_REPORT",
    "QUARTERLY_RESULTS": "QUARTERLY_RESULTS",
    "INVESTOR_PRESENTATION": "INVESTOR_PRESENTATION",
    "CONCALL_TRANSCRIPT": "CONCALL",
    "REG30": "REG30_ATTACHMENT",
}

DISCOVERY_SOURCE_MAP = {
    "exchange_rss": "EXCHANGE",
    "exchange_archive": "EXCHANGE",
    "recorded_source": "OFFICIAL_SITE",
    "issuer_ir": "OFFICIAL_SITE",
    "screener": "OTHER",
    "business_standard": "OTHER",
    "qualified_mirror": "OTHER",
    "manual_upload_queue": "USER",
    "legacy_manual_backfill": "USER",
    "reconciliation": "USER",
    "metadata_correction": "USER",
    "hash_backfill": "USER",
}


def map_document_type(spa_document_type: str) -> str:
    return DOCUMENT_TYPE_MAP.get((spa_document_type or "").strip().upper(), "OTHER")


def map_discovery_source_type(spa_acquisition_route: str) -> str:
    return DISCOVERY_SOURCE_MAP.get((spa_acquisition_route or "").strip(), "OTHER")


def build_discovery_source_reference(
    spa_document_type: str, spa_acquisition_route: str, sheet_row: int
) -> str:
    return (
        f"SPA manifest row {sheet_row}; "
        f"document_type={spa_document_type or '(blank)'}; "
        f"acquisition_route={spa_acquisition_route or '(blank)'}"
    )


def canonical_title(canonical_filename: str) -> str:
    name = (canonical_filename or "").strip()
    if name.lower().endswith(".pdf"):
        name = name[: -len(".pdf")]
    return name or "Untitled SPA document"


def build_document_payload(
    *,
    record: dict,
    company_id: str,
    content_sha256: str,
    mime_type: str,
    file_size_bytes: int | None,
    provided_by_user_id: str,
) -> dict:
    """``record`` is one row dict from the ``Documents`` tab (SPA header names)."""

    original_source_url = (record.get("Original Source URL") or "").strip()
    sheet_row = int(record.get("__row_number", 0) or 0)

    document: dict = {
        "document_type": map_document_type(record.get("Document Type", "")),
        "title": canonical_title(record.get("Canonical Filename", "")),
        "discovery_source_type": map_discovery_source_type(
            record.get("Discovery Source Type", "")
        ),
        "discovery_source_reference": build_discovery_source_reference(
            record.get("Document Type", ""),
            record.get("Discovery Source Type", ""),
            sheet_row,
        ),
        "ingestion_status": "STORED",
        "mime_type": mime_type,
        "file_size_bytes": file_size_bytes,
        "storage_provider": "google_drive",
        "storage_key": record.get("Drive File ID", "") or None,
        "content_hash_sha256": content_sha256,
    }

    reporting_period = (record.get("Reporting Period") or "").strip()
    if reporting_period:
        document["reporting_period"] = reporting_period

    publisher_reference = (record.get("Publisher Reference") or "").strip()
    if publisher_reference:
        document["publisher_reference"] = publisher_reference

    document_date = (record.get("Document Date") or "").strip()
    if document_date:
        document["document_date"] = document_date

    if original_source_url:
        document["original_source_url"] = original_source_url
        document["source_access"] = "PUBLIC"
        document["acquisition_method"] = "PUBLIC_DOWNLOAD"
    else:
        document["source_access"] = "UNKNOWN"
        document["acquisition_method"] = "USER_UPLOAD"
        document["provided_by_user_id"] = provided_by_user_id

    return {
        "document": document,
        "company_links": [{"company_id": company_id, "is_primary": True}],
    }
