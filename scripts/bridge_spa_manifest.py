"""Bridge: register SPA-ingested, Drive-stored documents into the M1
Document Library.

Reads the live SPA manifest's ``Documents`` tab (Google Sheets, via the
same application-default credentials the production worker uses) filtered
to ``Ingestion Status == STORED``, resolves each row's company against the
existing M1 Company/Ticker chain -- fail-closed: a company with no match
is reported and skipped, never created -- and calls
``DocumentLibraryService.create_document`` directly, the same write path
the admin HTTP API uses.

Idempotent: a repeat run that hits an already-registered document's exact
(metadata_fingerprint, content_hash_sha256) is reported as
``ALREADY_REGISTERED``, not an error.

Never writes to the SPA Sheet, Drive, or the ``SqliteManifestRepository``
mirror; never touches the M1 schema or migrations.

Usage:
    python scripts/bridge_spa_manifest.py --actor-user-id <admin-id> --dry-run
    python scripts/bridge_spa_manifest.py --actor-user-id <admin-id> --limit 1 --verify-hash

Required environment (same variables the live SPA worker uses, see
``C:\\SPA\\cloudrun_fixed_v7_5\\Run-Local.ps1``):

    SPA_MANIFEST_SHEET_ID   -- the manifest spreadsheet ID
    GOOGLE_APPLICATION_CREDENTIALS (or other ADC source) -- Sheets read access
    SPA_DRIVE_OAUTH_JSON    -- only required when --verify-hash is passed
"""

from __future__ import annotations

import argparse
import sys

from dotenv import load_dotenv

load_dotenv()

from app import create_app  # noqa: E402
from app.services.document_library_service import DocumentLibraryService  # noqa: E402
from app.utils.research_errors import (  # noqa: E402
    ResearchConflictError,
    ResearchNotFoundError,
    ResearchValidationError,
)
from scripts.spa_bridge.company_resolver import resolve_company_id  # noqa: E402
from scripts.spa_bridge.drive_evidence import (  # noqa: E402
    build_drive_service,
    download_and_hash,
    file_metadata,
)
from scripts.spa_bridge.payload_builder import build_document_payload  # noqa: E402
from scripts.spa_bridge.sheets_reader import (  # noqa: E402
    ManifestSheetsReader,
    build_sheets_service,
    manifest_spreadsheet_id,
)
from scripts.spa_bridge.universe_identity import (  # noqa: E402
    MISSING_DRIVE_FOLDER_ID,
    build_universe_identity_index,
    resolve_universe_codes,
)


def run(
    actor_user_id: str,
    *,
    limit: int,
    verify_hash: bool,
    dry_run: bool,
    only_tickers: set[str] | None = None,
) -> list[dict]:
    """``only_tickers``, when given, restricts this run to rows whose
    resolved NSE symbol or BSE security code is in the set -- used to
    scope a historical-backfill batch to specific companies (e.g. one
    Phase 1 identity-seed cohort) without touching any other company's
    rows, including ones that already resolve."""

    sheets = ManifestSheetsReader(build_sheets_service(), manifest_spreadsheet_id())
    universe_identity = build_universe_identity_index(sheets.read_tab("Universe"))
    drive_service = build_drive_service() if verify_hash else None

    results: list[dict] = []
    processed = 0

    for row in sheets.read_tab("Documents"):
        if (row.get("Ingestion Status", "") or "").strip().upper() != "STORED":
            continue

        sheet_row = int(row.get("__row_number", 0) or 0)
        codes = resolve_universe_codes(row, universe_identity)

        if only_tickers is not None:
            if codes == MISSING_DRIVE_FOLDER_ID:
                continue
            nse_symbol, bse_security_code = codes
            if nse_symbol not in only_tickers and bse_security_code not in only_tickers:
                continue

        if limit and processed >= limit:
            break
        processed += 1

        if codes == MISSING_DRIVE_FOLDER_ID:
            results.append(
                {
                    "sheet_row": sheet_row,
                    "outcome": MISSING_DRIVE_FOLDER_ID,
                    "company": row.get("Company", ""),
                }
            )
            continue
        nse_symbol, bse_security_code = codes
        company_id = resolve_company_id(nse_symbol, bse_security_code)
        if company_id is None:
            results.append(
                {
                    "sheet_row": sheet_row,
                    "outcome": "UNRESOLVED_COMPANY",
                    "company": row.get("Company", ""),
                }
            )
            continue

        drive_file_id = row.get("Drive File ID", "")
        content_sha256 = (row.get("Content SHA256", "") or "").strip().lower()
        mime_type = "application/pdf"
        file_size_bytes: int | None = None

        if drive_service is not None:
            if not drive_file_id:
                results.append(
                    {"sheet_row": sheet_row, "outcome": "MISSING_DRIVE_FILE_ID"}
                )
                continue
            content, verified_sha256 = download_and_hash(drive_service, drive_file_id)
            if content_sha256 and verified_sha256 != content_sha256:
                results.append(
                    {
                        "sheet_row": sheet_row,
                        "outcome": "HASH_MISMATCH",
                        "manifest_sha256": content_sha256,
                        "drive_sha256": verified_sha256,
                    }
                )
                continue
            content_sha256 = verified_sha256
            file_size_bytes = len(content)
        elif drive_file_id:
            metadata = file_metadata(build_drive_service(), drive_file_id)
            file_size_bytes = int(metadata.get("size") or 0) or None
            mime_type = metadata.get("mimeType") or mime_type

        if not content_sha256:
            results.append({"sheet_row": sheet_row, "outcome": "MISSING_CONTENT_HASH"})
            continue

        payload = build_document_payload(
            record=row,
            company_id=company_id,
            content_sha256=content_sha256,
            mime_type=mime_type,
            file_size_bytes=file_size_bytes,
            provided_by_user_id=actor_user_id,
        )

        if dry_run:
            results.append({"sheet_row": sheet_row, "outcome": "DRY_RUN_OK"})
            continue

        try:
            document = DocumentLibraryService.create_document(payload, actor_user_id)
        except ResearchConflictError as error:
            if error.code == "document_duplicate":
                results.append(
                    {"sheet_row": sheet_row, "outcome": "ALREADY_REGISTERED"}
                )
                continue
            raise
        except (ResearchValidationError, ResearchNotFoundError) as error:
            results.append(
                {
                    "sheet_row": sheet_row,
                    "outcome": "REJECTED",
                    "error": str(error),
                }
            )
            continue

        results.append(
            {
                "sheet_row": sheet_row,
                "outcome": "REGISTERED",
                "document_id": document.id,
            }
        )

    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--actor-user-id",
        required=True,
        help="ID of the existing administrator running this bridge",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Stop after this many STORED rows (0 = no limit)",
    )
    parser.add_argument(
        "--verify-hash",
        action="store_true",
        help=(
            "Re-download and re-hash each file from Drive instead of "
            "trusting the manifest's recorded Content SHA256 -- canary "
            "use; steady-state runs should omit this."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Resolve and validate every row without writing anything",
    )
    parser.add_argument(
        "--only-tickers",
        default="",
        help=(
            "Comma-separated NSE symbols / BSE security codes to restrict "
            "this run to -- for scoping a historical-backfill batch to one "
            "cohort of companies without touching any other company's rows"
        ),
    )
    args = parser.parse_args()

    only_tickers = {t.strip() for t in args.only_tickers.split(",") if t.strip()} or None

    app = create_app(register_research=True)
    with app.app_context():
        results = run(
            args.actor_user_id,
            limit=args.limit,
            verify_hash=args.verify_hash,
            dry_run=args.dry_run,
            only_tickers=only_tickers,
        )

    for result in results:
        print(result)

    failed = [r for r in results if r["outcome"] in ("REJECTED", "HASH_MISMATCH")]
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
