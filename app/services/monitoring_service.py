"""Continuous Research Monitoring V1 -- ``MonitoringService``.

Owner-approved design ("SPA -- Continuous Monitoring V1 Approved",
2026-10-12). Wraps the existing, unchanged, idempotent
``scripts/bridge_spa_manifest.run`` document-detection pathway with:

- a durable ``MonitoringRun`` audit record for every invocation,
  success or failure, scoped to a fixed company universe;
- a best-effort, explicitly-labelled manifest freshness check (never
  claims "no new filings" means "exchange coverage confirmed" -- see
  ``_assess_freshness``);
- I3AS alerting, reusing ``AlertService`` exactly as every other
  producer does, with one deliberate suppression rule: a clean,
  no-change, non-stale run raises no alert at all (owner requirement:
  avoid Telegram noise for successful no-change runs). The
  ``MonitoringRun`` row itself remains the audit trail for that case.

**Scope discipline.** This service performs document *detection* only.
It never creates a ``ResearchRevision``/``ExtractedFact``/
``InvestmentHypothesis``/``InvestmentCase`` row, and every alert
summary it writes says so explicitly -- interpretation of a
newly-detected document is a later, separate, human step.

``run_fn`` is an injectable seam (default: the real
``scripts.bridge_spa_manifest.run``) so tests can exercise this
service's own bookkeeping/alerting/staleness logic without live
Google Sheets credentials, without ever substituting fabricated
research conclusions for real ones -- it only ever stands in for the
*document-detection* call, the one piece of this service that talks to
an external system.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Callable

from app import db
from app.models.alert_types import AlertEventType, AlertSeverity
from app.models.monitoring import ManifestFreshnessStatus, MonitoringRun, MonitoringRunStatus
from app.services.alert_service import AlertService

logger = logging.getLogger(__name__)

# A manifest row older than this, with no fresher row seen for the
# scoped companies, is reported STALE -- a deliberately coarse,
# documented threshold (not derived from any SLA), chosen because the
# owner-approved three-company pilot has historically filed at least
# this often. It is a "worth checking" signal, not proof the upstream
# feed is actually broken.
_STALE_AFTER = timedelta(days=30)

_DATE_FORMATS = ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y")


def _parse_document_date(value: str) -> datetime | None:
    value = (value or "").strip()
    if not value:
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


class MonitoringService:
    """Owns writes to ``MonitoringRun`` only; delegates to
    ``AlertService`` for alerting and to the existing manifest bridge
    for detection. No writes anywhere else in the schema."""

    @classmethod
    def run_document_detection_scan(
        cls,
        *,
        actor_user_id: str,
        company_scope: dict[str, str],
        trigger_source: str,
        run_fn: Callable[..., list[dict]] | None = None,
    ) -> MonitoringRun:
        """``company_scope`` maps ``company_id`` -> ticker symbol for
        the fixed, owner-approved monitoring universe. ``run_fn``
        defaults to the real manifest bridge; see module docstring."""

        if run_fn is None:
            from scripts.bridge_spa_manifest import run as run_fn

        run = MonitoringRun(
            status=MonitoringRunStatus.RUNNING,
            trigger_source=trigger_source,
            triggered_by_user_id=actor_user_id,
            company_scope=list(company_scope.keys()),
        )
        db.session.add(run)
        db.session.commit()

        only_tickers = set(company_scope.values())
        error_summary: str | None = None
        results: list[dict] = []
        try:
            results = run_fn(
                actor_user_id,
                limit=0,
                verify_hash=False,
                dry_run=False,
                only_tickers=only_tickers,
            )
        except Exception as error:  # noqa: BLE001 -- must never crash the scheduler
            error_summary = f"{type(error).__name__}: {error}"
            logger.exception("MonitoringRun %s: document-detection scan failed", run.id)

        counts = {
            "REGISTERED": 0,
            "ALREADY_REGISTERED": 0,
            "REJECTED": 0,
            "UNRESOLVED": 0,
        }
        latest_document_date: datetime | None = None
        for entry in results:
            outcome = entry.get("outcome", "")
            if outcome == "REGISTERED":
                counts["REGISTERED"] += 1
            elif outcome == "ALREADY_REGISTERED":
                counts["ALREADY_REGISTERED"] += 1
            elif outcome in ("REJECTED", "MISSING_CONTENT_HASH"):
                counts["REJECTED"] += 1
            elif outcome in ("UNRESOLVED_COMPANY", "MISSING_DRIVE_FOLDER_ID"):
                counts["UNRESOLVED"] += 1
            parsed = _parse_document_date(entry.get("document_date", ""))
            if parsed is not None and (
                latest_document_date is None or parsed > latest_document_date
            ):
                latest_document_date = parsed

        freshness_status = cls._assess_freshness(latest_document_date)

        run.finished_at = datetime.now(timezone.utc)
        run.manifest_rows_scanned_count = len(results)
        run.documents_registered_count = counts["REGISTERED"]
        run.documents_already_registered_count = counts["ALREADY_REGISTERED"]
        run.documents_rejected_count = counts["REJECTED"]
        run.documents_unresolved_count = counts["UNRESOLVED"]
        run.manifest_latest_row_at = latest_document_date
        run.manifest_freshness_status = freshness_status
        run.error_summary = error_summary
        run.status = (
            MonitoringRunStatus.FAILED
            if error_summary is not None
            else MonitoringRunStatus.PARTIAL
            if counts["REJECTED"] or counts["UNRESOLVED"]
            else MonitoringRunStatus.SUCCESS
        )
        db.session.commit()

        cls._raise_alerts(run)
        return run

    @staticmethod
    def _assess_freshness(latest_document_date: datetime | None) -> str:
        """``UNKNOWN`` when there is nothing to measure against (no
        row at all for the scoped companies this run) -- never
        defaulted to FRESH, per the owner's "never equate a successful
        scan with complete exchange coverage" requirement."""

        if latest_document_date is None:
            return ManifestFreshnessStatus.UNKNOWN
        age = datetime.now(timezone.utc) - latest_document_date
        return (
            ManifestFreshnessStatus.STALE
            if age > _STALE_AFTER
            else ManifestFreshnessStatus.FRESH
        )

    @classmethod
    def _raise_alerts(cls, run: MonitoringRun) -> None:
        """Document-detection alerts only -- never an interpretation or
        investment conclusion (see module docstring). Suppresses the
        one case the owner named explicitly: a clean, no-change,
        non-stale success raises nothing; the ``MonitoringRun`` row is
        the audit trail for that case."""

        recipients = AlertService.admin_recipient_ids()

        if run.status == MonitoringRunStatus.FAILED:
            AlertService.record_event(
                event_type=AlertEventType.MONITORING_RUN_FAILED,
                source_table="monitoring_run",
                source_id=run.id,
                summary=(
                    f"Continuous monitoring scan failed for "
                    f"{', '.join(run.company_scope)}: {run.error_summary}. "
                    "No document detection could be completed this run."
                ),
                severity=AlertSeverity.WATCH,
                recipient_user_ids=recipients,
            )
            return

        if run.documents_registered_count > 0:
            AlertService.record_event(
                event_type=AlertEventType.MONITORING_RUN_DOCUMENTS_DETECTED,
                source_table="monitoring_run",
                source_id=run.id,
                summary=(
                    f"Continuous monitoring detected "
                    f"{run.documents_registered_count} new document(s) "
                    f"for {', '.join(run.company_scope)}. Document "
                    "detection only -- no interpretation or investment "
                    "conclusion is implied."
                ),
                severity=AlertSeverity.INFO,
                recipient_user_ids=recipients,
                details={
                    "documents_registered_count": run.documents_registered_count,
                    "documents_rejected_count": run.documents_rejected_count,
                    "documents_unresolved_count": run.documents_unresolved_count,
                },
            )

        if run.manifest_freshness_status == ManifestFreshnessStatus.STALE:
            AlertService.record_event(
                event_type=AlertEventType.MONITORING_MANIFEST_STALE,
                source_table="monitoring_run",
                source_id=run.id,
                summary=(
                    f"Continuous monitoring: the manifest's most recent "
                    f"known document date for {', '.join(run.company_scope)} "
                    f"is {run.manifest_latest_row_at}, older than the "
                    f"{_STALE_AFTER.days}-day freshness threshold. This "
                    "does not by itself prove the upstream feed is "
                    "broken -- it means staleness could not be ruled "
                    "out and is worth checking."
                ),
                severity=AlertSeverity.WATCH,
                recipient_user_ids=recipients,
            )
