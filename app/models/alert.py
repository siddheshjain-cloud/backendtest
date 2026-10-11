"""I3AS Slice 1 -- ``AlertEvent``/``AlertDelivery``.

Cross-cutting notification/delivery layer, not a North-Star layer
itself (owner-approved design proposal, `stock-price-alert` repo,
`docs/superpowers/plans/2026-10-12-integrated-investment-intelligence-
alert-system-design-proposal.md`, §3/§5/§9). An ``AlertEvent`` is a
notification *about* an already-immutable row elsewhere in the system
-- it never asserts a Fact, Hypothesis, Forecast, Valuation,
``InvestmentCase``, or any other node on the Phase 3 epistemic chain,
and no chain object ever cites an alert-system table as provenance.
Alerts point one way: chain -> alert, never the reverse.

``AlertEvent`` is append-only and immutable, uniquely identified by
``(source_table, source_id, event_type)`` -- a natural-key idempotency
guarantee borrowed directly from Sensybull's own ``FilingEvent.edgar_id``
precedent (design doc §2a/§9.1), so the same real-world occurrence can
never be recorded twice even if a producing call site retries.
``source_table``/``source_id`` are a plain, uncoupled reference named
by convention, the same deliberate choice this codebase already made
for ``ForecastAssumption.metric_slug`` -- not a foreign key, so this one
table can accept events from an open-ended, incrementally-registered
set of sources without an ever-growing column list. ``details`` is an
optional ``sa.JSON`` column (already a precedented type in this
codebase -- ``app/models/document.py``) for event-specific structured
data, mirroring Sensybull's ``EventType.attributes`` rather than a
second nullable column per event type.

``AlertDelivery`` is the one deliberate exception to this module's
immutability idiom, by owner requirement: a row is inserted as
``PENDING`` (durably recording intent *before* any delivery attempt,
so a crash mid-attempt cannot silently lose the attempt), then
transitions exactly once to ``SENT`` or ``FAILED`` -- enforced by
``_reject_illegal_delivery_update``, the same "set once" pattern
``InvestmentCaseKillSwitch.triggered_at`` already uses for the
identical reason. Unique on ``(alert_event_id, recipient_user_id,
channel)`` -- one event produces at most one delivery attempt per
recipient per channel.
"""

from __future__ import annotations

from datetime import datetime

import sqlalchemy as sa
import sqlalchemy.orm as so

from app.models.alert_types import AlertChannel, AlertDeliveryStatus, AlertSeverity, enum_type
from app.models.base import BaseModel


class AlertEvent(BaseModel):
    """One immutable row per alert-worthy occurrence."""

    __tablename__ = "alert_event"

    event_type: so.Mapped[str] = so.mapped_column(sa.String(100), nullable=False)
    source_table: so.Mapped[str] = so.mapped_column(sa.String(100), nullable=False)
    source_id: so.Mapped[str] = so.mapped_column(sa.String(36), nullable=False)
    company_id: so.Mapped[str | None] = so.mapped_column(
        sa.ForeignKey("company.id"), nullable=True
    )
    severity: so.Mapped[str] = so.mapped_column(
        enum_type(
            "alert_event_severity",
            (AlertSeverity.INFO, AlertSeverity.WATCH, AlertSeverity.CRITICAL),
        ),
        nullable=False,
        server_default=AlertSeverity.INFO,
    )
    summary: so.Mapped[str] = so.mapped_column(sa.Text, nullable=False)
    details: so.Mapped[dict | None] = so.mapped_column(sa.JSON, nullable=True)
    occurred_at: so.Mapped[datetime] = so.mapped_column(
        sa.DateTime(timezone=True), nullable=False
    )

    __table_args__ = (
        sa.UniqueConstraint(
            "source_table", "source_id", "event_type",
            name="uq_alert_event_source_identity",
        ),
        sa.Index("ix_alert_event_company_id", "company_id"),
    )

    company: so.Mapped["Company | None"] = so.relationship("Company", viewonly=True)
    deliveries: so.Mapped[list["AlertDelivery"]] = so.relationship(
        "AlertDelivery", viewonly=True
    )

    def __repr__(self) -> str:
        return f"<AlertEvent {self.event_type} {self.source_table}:{self.source_id}>"


class AlertDelivery(BaseModel):
    """One delivery attempt of one ``AlertEvent`` to one recipient over
    one channel. ``PENDING`` -> ``SENT``/``FAILED`` is the one legal
    transition -- see module docstring."""

    __tablename__ = "alert_delivery"

    alert_event_id: so.Mapped[str] = so.mapped_column(
        sa.ForeignKey("alert_event.id"), nullable=False
    )
    recipient_user_id: so.Mapped[str] = so.mapped_column(
        sa.ForeignKey("user.id"), nullable=False
    )
    channel: so.Mapped[str] = so.mapped_column(
        enum_type(
            "alert_delivery_channel",
            (AlertChannel.TELEGRAM, AlertChannel.WHATSAPP, AlertChannel.WEBHOOK),
        ),
        nullable=False,
    )
    delivery_status: so.Mapped[str] = so.mapped_column(
        enum_type(
            "alert_delivery_status",
            (
                AlertDeliveryStatus.PENDING,
                AlertDeliveryStatus.SENT,
                AlertDeliveryStatus.FAILED,
            ),
        ),
        nullable=False,
        server_default=AlertDeliveryStatus.PENDING,
    )
    resolved_at: so.Mapped[datetime | None] = so.mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )
    error_detail: so.Mapped[str | None] = so.mapped_column(sa.Text, nullable=True)

    __table_args__ = (
        sa.UniqueConstraint(
            "alert_event_id", "recipient_user_id", "channel",
            name="uq_alert_delivery_event_recipient_channel",
        ),
        sa.Index("ix_alert_delivery_status", "delivery_status"),
    )

    alert_event: so.Mapped["AlertEvent"] = so.relationship(
        "AlertEvent", viewonly=True
    )
    recipient: so.Mapped["User"] = so.relationship("User", viewonly=True)

    def __repr__(self) -> str:
        return (
            f"<AlertDelivery event={self.alert_event_id} "
            f"recipient={self.recipient_user_id} {self.delivery_status}>"
        )


def _reject_immutable_update(_mapper, _connection, target) -> None:
    raise sa.exc.InvalidRequestError(
        f"{type(target).__name__} is immutable after insertion"
    )


def _reject_immutable_delete(_mapper, _connection, target) -> None:
    raise sa.exc.InvalidRequestError(
        f"{type(target).__name__} is immutable and cannot be deleted"
    )


def _reject_illegal_delivery_update(_mapper, connection, target: AlertDelivery) -> None:
    """Allow exactly one legal mutation: ``delivery_status`` going from
    ``PENDING`` to ``SENT``/``FAILED`` (with ``resolved_at``/
    ``error_detail`` set in the same transition). Every other field,
    and any further change once a delivery has reached a terminal
    status, is rejected.

    Deliberately reads the row's *currently persisted* values straight
    from ``connection`` (available mid-flush, before the pending UPDATE
    is issued) rather than trusting the ORM's in-memory attribute
    history. ``expire_on_commit`` (the session default) leaves a
    just-inserted attribute unloaded until something reads it; writing
    straight over an unloaded attribute -- exactly what
    ``AlertService._finalize_delivery`` does, setting ``delivery_status``
    immediately after the insert's own commit, with no read in between
    -- makes SQLAlchemy's own history show no "previous value" at all
    (``deleted=()``), not because there wasn't one, but because it was
    never loaded into memory. Asking the database directly is correct
    regardless of that timing, the same "verify against the real state,
    not an assumption" discipline this codebase applies everywhere
    else."""

    table = AlertDelivery.__table__
    row = connection.execute(
        sa.select(
            table.c.alert_event_id, table.c.recipient_user_id, table.c.channel,
            table.c.delivery_status, table.c.resolved_at, table.c.error_detail,
        ).where(table.c.id == target.id)
    ).one()

    for column_name in ("alert_event_id", "recipient_user_id", "channel"):
        if getattr(row, column_name) != getattr(target, column_name):
            raise sa.exc.InvalidRequestError(
                "AlertDelivery is immutable except for a single "
                "PENDING -> SENT/FAILED transition"
            )

    if row.delivery_status == target.delivery_status:
        if row.resolved_at != target.resolved_at or row.error_detail != target.error_detail:
            raise sa.exc.InvalidRequestError(
                "AlertDelivery is immutable except for a single "
                "PENDING -> SENT/FAILED transition"
            )
        return

    if row.delivery_status != AlertDeliveryStatus.PENDING:
        raise sa.exc.InvalidRequestError(
            "AlertDelivery.delivery_status can only transition once, "
            "from PENDING"
        )
    if target.delivery_status not in (AlertDeliveryStatus.SENT, AlertDeliveryStatus.FAILED):
        raise sa.exc.InvalidRequestError(
            "AlertDelivery.delivery_status may only become SENT or FAILED"
        )


for _immutable_model in (AlertEvent,):
    sa.event.listen(_immutable_model, "before_update", _reject_immutable_update)
    sa.event.listen(_immutable_model, "before_delete", _reject_immutable_delete)

sa.event.listen(AlertDelivery, "before_update", _reject_illegal_delivery_update)
sa.event.listen(AlertDelivery, "before_delete", _reject_immutable_delete)
