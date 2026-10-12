"""Continuous Research Monitoring V1 -- single-pass CLI entrypoint.

Owner-approved design ("SPA -- Continuous Monitoring V1 Approved",
2026-10-12). Runs exactly one document-detection scan via
``MonitoringService`` and exits -- it has no internal loop, sleep, or
cadence of its own, by design: the scheduling mechanism (Windows Task
Scheduler for this bounded pilot) stays entirely external to, and
decoupled from, this script, so the trigger mechanism can change later
without touching monitoring logic.

Deliberately does **not** pass ``allow_default_database=True`` to
``create_app()`` -- this script always requires an explicit
``DATABASE_URL``, the same fail-closed guard every other entrypoint
in this codebase relies on since the live-database incident. A
scheduled task that forgets to set it fails immediately rather than
silently touching the default database file.

Usage:
    python scripts/run_monitoring_scan.py --actor-user-id <admin-id> --trigger-source MANUAL
    python scripts/run_monitoring_scan.py --actor-user-id <admin-id> --trigger-source WINDOWS_TASK_SCHEDULER

Required environment:
    DATABASE_URL                    -- must point at an approved database;
                                        never silently defaults
    SPA_MANIFEST_SHEET_ID            -- the manifest spreadsheet ID
    GOOGLE_APPLICATION_CREDENTIALS (or other ADC source) -- Sheets read access
"""

from __future__ import annotations

import argparse
import os
import sys

# Make the repo root importable regardless of invocation style (direct
# file execution -- the one a Task Scheduler Action uses -- puts only
# this file's own directory on sys.path, not the repo root).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()

from app import create_app  # noqa: E402
from app.models.monitoring import MonitoringRunStatus  # noqa: E402
from app.services.monitoring_service import MonitoringService  # noqa: E402

# The owner-approved Continuous Monitoring V1 pilot universe (2026-10-12):
# added only after each company's source coverage and baseline were
# independently verified against an isolated database copy. Company ids
# are stable M1 identities; ticker symbols are the legacy Ticker.symbol
# values resolved against this same universe.
MONITORING_COMPANY_SCOPE = {
    "fbc97e85-b038-4b1a-9634-9946a5c706a0": "CHEMPLASTS",   # Chemplast Sanmar Limited
    "9cbdabe1-3008-45dc-8ef6-dad859946990": "UNOMINDA",     # UNO Minda Limited
    "64b0d724-7071-48a9-93be-138de4d321f6": "AXISCADES",    # AXISCADES Technologies Limited
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--actor-user-id",
        required=True,
        help="ID of the existing administrator this run is attributed to",
    )
    parser.add_argument(
        "--trigger-source",
        default="MANUAL",
        help=(
            "Free-text label for what invoked this run (e.g. "
            "WINDOWS_TASK_SCHEDULER, MANUAL) -- recorded verbatim on "
            "the MonitoringRun row, never interpreted by this script"
        ),
    )
    args = parser.parse_args()

    app = create_app(register_research=True)
    with app.app_context():
        run = MonitoringService.run_document_detection_scan(
            actor_user_id=args.actor_user_id,
            company_scope=MONITORING_COMPANY_SCOPE,
            trigger_source=args.trigger_source,
        )

        print(f"MonitoringRun {run.id}")
        print(f"  status: {run.status}")
        print(f"  company_scope: {run.company_scope}")
        print(f"  manifest_rows_scanned_count: {run.manifest_rows_scanned_count}")
        print(f"  documents_registered_count: {run.documents_registered_count}")
        print(f"  documents_already_registered_count: {run.documents_already_registered_count}")
        print(f"  documents_rejected_count: {run.documents_rejected_count}")
        print(f"  documents_unresolved_count: {run.documents_unresolved_count}")
        print(f"  manifest_latest_row_at: {run.manifest_latest_row_at}")
        print(f"  manifest_freshness_status: {run.manifest_freshness_status}")
        print(f"  error_summary: {run.error_summary}")

    return 1 if run.status == MonitoringRunStatus.FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
