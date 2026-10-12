"""Continuous Research Monitoring V1 -- ``MonitoringRun``.

Owner-approved design ("SPA -- Continuous Monitoring V1 Approved",
2026-10-12): one row per document-detection scan pass over the
manifest bridge, scoped to a fixed company universe. Deliberately the
*only* new table this slice adds -- it reuses the existing,
unchanged, idempotent ``scripts/bridge_spa_manifest.py`` pathway for
the actual detection work (see ``MonitoringService``) rather than
building a second ingestion mechanism.

Scope discipline (owner requirement): this table records **document
detection** only -- how many rows the scan saw and what became of
them. It never asserts a Fact, Hypothesis, Forecast, Valuation, or
``InvestmentCase`` conclusion, and nothing in the Phase 3 epistemic
chain may ever cite a ``MonitoringRun`` as provenance. Interpretation
of a newly-detected document is a separate, later, human/analyst step
-- out of scope here by design, not an oversight.

Unlike every append-only revision-chain table elsewhere in this
codebase, a ``MonitoringRun`` row is genuinely mutable by design: it
is inserted once as ``RUNNING`` and updated exactly once, at the end
of the same scan, to its terminal state. ``BaseModel`` itself carries
no blanket immutability listener (each table opts in individually --
see ``app/models/alert.py``), so no listener is registered here; this
is a plain operational/audit log row, the same mutability shape
``Trade`` already uses for its own state machine.

``manifest_freshness_status`` is deliberately three-valued
(``FRESH``/``STALE``/``UNKNOWN``) rather than a boolean: the owner's
explicit requirement is to "detect upstream manifest staleness" where
*verifiable* and never claim coverage that wasn't actually confirmed --
``UNKNOWN`` is the honest answer whenever the manifest exposes no
reliable freshness signal, not a defect to hide.
"""

from __future__ import annotations

from datetime import datetime

import sqlalchemy as sa
import sqlalchemy.orm as so

from app.models.base import BaseModel
from app.models.research_types import enum_type


class MonitoringRunStatus:
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"


class ManifestFreshnessStatus:
    FRESH = "FRESH"
    STALE = "STALE"
    UNKNOWN = "UNKNOWN"


class MonitoringRun(BaseModel):
    """One document-detection scan pass. Mutable: ``RUNNING`` ->
    exactly one terminal update. See module docstring."""

    __tablename__ = "monitoring_run"

    status: so.Mapped[str] = so.mapped_column(
        enum_type(
            "monitoring_run_status",
            (
                MonitoringRunStatus.RUNNING,
                MonitoringRunStatus.SUCCESS,
                MonitoringRunStatus.PARTIAL,
                MonitoringRunStatus.FAILED,
            ),
        ),
        nullable=False,
        server_default=MonitoringRunStatus.RUNNING,
    )
    # Free-text, documented-by-convention (e.g. "WINDOWS_TASK_SCHEDULER",
    # "MANUAL") -- deliberately not a CHECK enum, so the scheduling
    # mechanism can change without a migration (owner requirement: keep
    # scheduler configuration external to, and decoupled from, this
    # table's own schema).
    trigger_source: so.Mapped[str] = so.mapped_column(
        sa.String(50), nullable=False
    )
    triggered_by_user_id: so.Mapped[str] = so.mapped_column(
        sa.ForeignKey("user.id"), nullable=False
    )
    # The exact company universe this run was scoped to -- a plain JSON
    # list of company ids, mirroring AlertEvent.details's already-
    # precedented "structured per-row data as JSON" idiom rather than a
    # new join table for a fixed, owner-approved three-company pilot.
    company_scope: so.Mapped[list] = so.mapped_column(sa.JSON, nullable=False)
    finished_at: so.Mapped[datetime | None] = so.mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )
    manifest_rows_scanned_count: so.Mapped[int] = so.mapped_column(
        sa.Integer, nullable=False, server_default="0"
    )
    documents_registered_count: so.Mapped[int] = so.mapped_column(
        sa.Integer, nullable=False, server_default="0"
    )
    documents_already_registered_count: so.Mapped[int] = so.mapped_column(
        sa.Integer, nullable=False, server_default="0"
    )
    documents_rejected_count: so.Mapped[int] = so.mapped_column(
        sa.Integer, nullable=False, server_default="0"
    )
    documents_unresolved_count: so.Mapped[int] = so.mapped_column(
        sa.Integer, nullable=False, server_default="0"
    )
    manifest_latest_row_at: so.Mapped[datetime | None] = so.mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )
    manifest_freshness_status: so.Mapped[str] = so.mapped_column(
        enum_type(
            "monitoring_manifest_freshness_status",
            (
                ManifestFreshnessStatus.FRESH,
                ManifestFreshnessStatus.STALE,
                ManifestFreshnessStatus.UNKNOWN,
            ),
        ),
        nullable=False,
        server_default=ManifestFreshnessStatus.UNKNOWN,
    )
    error_summary: so.Mapped[str | None] = so.mapped_column(sa.Text, nullable=True)

    triggered_by_user: so.Mapped["User"] = so.relationship("User", viewonly=True)

    def __repr__(self) -> str:
        return f"<MonitoringRun {self.id} {self.status} scope={self.company_scope}>"
