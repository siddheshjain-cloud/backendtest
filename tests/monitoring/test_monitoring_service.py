"""Continuous Research Monitoring V1 -- ``MonitoringService`` acceptance
tests.

Uses the injectable ``run_fn`` seam (never real Google Sheets
credentials) to verify this service's own bookkeeping, freshness
assessment, and I3AS alerting/suppression rules in isolation from the
real external manifest -- see ``MonitoringService``'s module
docstring for why this seam exists and what it does not stand in for.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import sqlalchemy as sa

from app import db
from app.models.alert import AlertDelivery, AlertEvent
from app.models.alert_types import AlertEventType
from app.models.monitoring import ManifestFreshnessStatus, MonitoringRun, MonitoringRunStatus
from app.services.monitoring_service import MonitoringService

COMPANY_SCOPE = {
    "company-chemplast": "CHEMPLASTS",
    "company-uno-minda": "UNOMINDA",
    "company-axiscades": "AXISCADES",
}


def _today_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _stale_str() -> str:
    return (datetime.now(timezone.utc) - timedelta(days=45)).strftime("%Y-%m-%d")


def test_scan_with_new_documents_raises_detection_alert_only(admin_user):
    captured_calls = []

    def fake_run(actor_user_id, *, limit, verify_hash, dry_run, only_tickers):
        captured_calls.append(
            {
                "actor_user_id": actor_user_id,
                "limit": limit,
                "verify_hash": verify_hash,
                "dry_run": dry_run,
                "only_tickers": only_tickers,
            }
        )
        return [
            {"sheet_row": 2, "outcome": "REGISTERED", "document_id": "doc-1",
             "document_date": _today_str()},
            {"sheet_row": 3, "outcome": "ALREADY_REGISTERED",
             "document_date": _today_str()},
        ]

    run = MonitoringService.run_document_detection_scan(
        actor_user_id=admin_user.id,
        company_scope=COMPANY_SCOPE,
        trigger_source="MANUAL",
        run_fn=fake_run,
    )

    assert run.status == MonitoringRunStatus.SUCCESS
    assert run.documents_registered_count == 1
    assert run.documents_already_registered_count == 1
    assert run.documents_rejected_count == 0
    assert run.documents_unresolved_count == 0
    assert run.manifest_rows_scanned_count == 2
    assert run.manifest_freshness_status == ManifestFreshnessStatus.FRESH
    assert run.error_summary is None
    assert sorted(run.company_scope) == sorted(COMPANY_SCOPE.keys())

    assert captured_calls[0]["only_tickers"] == set(COMPANY_SCOPE.values())
    assert captured_calls[0]["dry_run"] is False

    events = db.session.scalars(
        sa.select(AlertEvent).where(AlertEvent.source_id == run.id)
    ).all()
    assert len(events) == 1
    assert events[0].event_type == AlertEventType.MONITORING_RUN_DOCUMENTS_DETECTED
    assert "no interpretation or investment conclusion" in events[0].summary.lower()

    delivery = db.session.scalar(
        sa.select(AlertDelivery).where(AlertDelivery.alert_event_id == events[0].id)
    )
    assert delivery.recipient_user_id == admin_user.id


def test_clean_no_change_fresh_run_raises_no_alert_at_all(admin_user, free_user):
    """Owner requirement: avoid Telegram noise for successful no-change
    runs. The MonitoringRun row remains the audit trail."""

    def fake_run(actor_user_id, *, limit, verify_hash, dry_run, only_tickers):
        return [
            {"sheet_row": 2, "outcome": "ALREADY_REGISTERED",
             "document_date": _today_str()},
        ]

    run = MonitoringService.run_document_detection_scan(
        actor_user_id=admin_user.id,
        company_scope=COMPANY_SCOPE,
        trigger_source="MANUAL",
        run_fn=fake_run,
    )

    assert run.status == MonitoringRunStatus.SUCCESS
    assert run.documents_registered_count == 0
    assert run.manifest_freshness_status == ManifestFreshnessStatus.FRESH

    events = db.session.scalars(
        sa.select(AlertEvent).where(AlertEvent.source_id == run.id)
    ).all()
    assert events == []


def test_stale_manifest_raises_staleness_alert_even_with_no_new_documents(admin_user):
    def fake_run(actor_user_id, *, limit, verify_hash, dry_run, only_tickers):
        return [
            {"sheet_row": 2, "outcome": "ALREADY_REGISTERED",
             "document_date": _stale_str()},
        ]

    run = MonitoringService.run_document_detection_scan(
        actor_user_id=admin_user.id,
        company_scope=COMPANY_SCOPE,
        trigger_source="MANUAL",
        run_fn=fake_run,
    )

    assert run.manifest_freshness_status == ManifestFreshnessStatus.STALE
    assert run.documents_registered_count == 0

    events = db.session.scalars(
        sa.select(AlertEvent).where(AlertEvent.source_id == run.id)
    ).all()
    assert len(events) == 1
    assert events[0].event_type == AlertEventType.MONITORING_MANIFEST_STALE
    assert "worth checking" in events[0].summary.lower()


def test_empty_result_set_reports_unknown_freshness_never_fresh(admin_user):
    """Never equate a successful scan with complete exchange coverage:
    nothing to measure against must report UNKNOWN, not FRESH."""

    def fake_run(actor_user_id, *, limit, verify_hash, dry_run, only_tickers):
        return []

    run = MonitoringService.run_document_detection_scan(
        actor_user_id=admin_user.id,
        company_scope=COMPANY_SCOPE,
        trigger_source="MANUAL",
        run_fn=fake_run,
    )

    assert run.manifest_freshness_status == ManifestFreshnessStatus.UNKNOWN
    assert run.status == MonitoringRunStatus.SUCCESS

    events = db.session.scalars(
        sa.select(AlertEvent).where(AlertEvent.source_id == run.id)
    ).all()
    assert events == []


def test_run_fn_exception_is_recorded_as_failed_and_alerts_admins(admin_user):
    def fake_run(actor_user_id, *, limit, verify_hash, dry_run, only_tickers):
        raise RuntimeError("SPA_MANIFEST_SHEET_ID is required")

    run = MonitoringService.run_document_detection_scan(
        actor_user_id=admin_user.id,
        company_scope=COMPANY_SCOPE,
        trigger_source="MANUAL",
        run_fn=fake_run,
    )

    assert run.status == MonitoringRunStatus.FAILED
    assert "SPA_MANIFEST_SHEET_ID" in run.error_summary
    assert run.manifest_rows_scanned_count == 0
    assert run.manifest_freshness_status == ManifestFreshnessStatus.UNKNOWN

    events = db.session.scalars(
        sa.select(AlertEvent).where(AlertEvent.source_id == run.id)
    ).all()
    assert len(events) == 1
    assert events[0].event_type == AlertEventType.MONITORING_RUN_FAILED


def test_rejected_and_unresolved_rows_mark_the_run_partial(admin_user):
    def fake_run(actor_user_id, *, limit, verify_hash, dry_run, only_tickers):
        return [
            {"sheet_row": 2, "outcome": "REGISTERED", "document_id": "doc-1",
             "document_date": _today_str()},
            {"sheet_row": 3, "outcome": "REJECTED", "error": "validation failed",
             "document_date": _today_str()},
            {"sheet_row": 4, "outcome": "UNRESOLVED_COMPANY", "company": "Unknown Co",
             "document_date": _today_str()},
        ]

    run = MonitoringService.run_document_detection_scan(
        actor_user_id=admin_user.id,
        company_scope=COMPANY_SCOPE,
        trigger_source="MANUAL",
        run_fn=fake_run,
    )

    assert run.status == MonitoringRunStatus.PARTIAL
    assert run.documents_registered_count == 1
    assert run.documents_rejected_count == 1
    assert run.documents_unresolved_count == 1

    events = db.session.scalars(
        sa.select(AlertEvent).where(AlertEvent.source_id == run.id)
    ).all()
    assert len(events) == 1
    assert events[0].event_type == AlertEventType.MONITORING_RUN_DOCUMENTS_DETECTED


def test_alert_event_natural_key_prevents_duplicate_alerts_for_the_same_run(admin_user):
    """Stable deduplication key reuse (owner requirement): re-raising
    alerts for a run that already has one must not create a second
    AlertEvent row for the same (source_table, source_id, event_type)."""

    def fake_run(actor_user_id, *, limit, verify_hash, dry_run, only_tickers):
        return [
            {"sheet_row": 2, "outcome": "REGISTERED", "document_id": "doc-1",
             "document_date": _today_str()},
        ]

    run = MonitoringService.run_document_detection_scan(
        actor_user_id=admin_user.id,
        company_scope=COMPANY_SCOPE,
        trigger_source="MANUAL",
        run_fn=fake_run,
    )

    MonitoringService._raise_alerts(run)
    MonitoringService._raise_alerts(run)

    events = db.session.scalars(
        sa.select(AlertEvent).where(AlertEvent.source_id == run.id)
    ).all()
    assert len(events) == 1


def test_trigger_source_and_actor_are_recorded_verbatim(admin_user):
    def fake_run(actor_user_id, *, limit, verify_hash, dry_run, only_tickers):
        return []

    run = MonitoringService.run_document_detection_scan(
        actor_user_id=admin_user.id,
        company_scope=COMPANY_SCOPE,
        trigger_source="WINDOWS_TASK_SCHEDULER",
        run_fn=fake_run,
    )

    assert run.trigger_source == "WINDOWS_TASK_SCHEDULER"
    assert run.triggered_by_user_id == admin_user.id
    reloaded = db.session.get(MonitoringRun, run.id)
    assert reloaded.finished_at is not None
    assert reloaded.finished_at >= reloaded.created_at
