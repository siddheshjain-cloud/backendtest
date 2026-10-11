"""I3AS Slice 1: ``AlertService``.

Owns writes to ``AlertEvent``/``AlertDelivery`` only -- mirrors Phase
3's P3-A service-ownership-boundary rule. Every producing call site
(Alert-1: ``live/websocket.py``'s ``TickerManager``; future Alert-2+:
``InvestmentCaseService`` etc.) calls ``record_event`` as one
additional step after its own write, never the reverse.

**Fail-safe by construction.** ``record_event`` never raises. A
duplicate natural key (idempotency -- see ``AlertEvent``'s own unique
constraint), a database error, or a delivery exception is caught,
logged, and swallowed, because the caller's own business write (and,
for Alert-1, the legacy direct Telegram send) has already completed by
the time this runs and must never be disrupted by it.

**Two delivery modes**, chosen per recipient by whether the caller
already knows the outcome:

- ``already_delivered`` supplied for a recipient: ``AlertService`` only
  *records* that already-happened outcome (``PENDING`` -> ``SENT``/
  ``FAILED``) -- it makes no network call of its own. This is Alert-1's
  mode: the legacy ``TelegramService.send_trade_alert`` call has
  already run and already sent (or not) the message; attempting a
  second send here would deliver a real duplicate message to the user,
  which the owner's explicit requirement prohibits.
- ``already_delivered`` omitted for a recipient: ``AlertService``
  performs the one real delivery attempt itself, via the existing,
  unchanged ``TelegramService.send_message``. This is Alert-2+'s mode,
  for event types with no pre-existing delivery mechanism of their own.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import sqlalchemy as sa

from app import db
from app.models.alert import AlertDelivery, AlertEvent
from app.models.alert_types import AlertChannel, AlertDeliveryStatus, AlertSeverity
from app.models.user import User

logger = logging.getLogger(__name__)


class AlertService:
    """Create ``AlertEvent``/``AlertDelivery`` rows. No writes anywhere
    else in the schema. Never raises -- see module docstring."""

    @classmethod
    def record_event(
        cls,
        *,
        event_type: str,
        source_table: str,
        source_id: str,
        summary: str,
        recipient_user_ids: list[str],
        company_id: str | None = None,
        severity: str = AlertSeverity.INFO,
        details: dict | None = None,
        occurred_at: datetime | None = None,
        already_delivered: dict[str, bool] | None = None,
        channel: str = AlertChannel.TELEGRAM,
    ) -> AlertEvent | None:
        """Record one alert-worthy occurrence and attempt delivery to
        each recipient. Returns the created ``AlertEvent``, or ``None``
        if it was already recorded (idempotent no-op) or if recording
        failed outright -- never raises."""

        already_delivered = already_delivered or {}
        try:
            event = AlertEvent(
                event_type=event_type,
                source_table=source_table,
                source_id=source_id,
                company_id=company_id,
                severity=severity,
                summary=summary,
                details=details,
                occurred_at=occurred_at or datetime.now(timezone.utc),
            )
            db.session.add(event)
            db.session.commit()
        except sa.exc.IntegrityError:
            db.session.rollback()
            logger.info(
                "AlertEvent already recorded for %s:%s/%s -- skipping "
                "(idempotent no-op)",
                source_table, source_id, event_type,
            )
            return None
        except Exception:
            db.session.rollback()
            logger.exception(
                "AlertService failed to record AlertEvent for %s:%s/%s",
                source_table, source_id, event_type,
            )
            return None

        for recipient_user_id in recipient_user_ids:
            try:
                cls._deliver_one(
                    event=event,
                    recipient_user_id=recipient_user_id,
                    channel=channel,
                    already_sent=already_delivered.get(recipient_user_id),
                )
            except Exception:
                logger.exception(
                    "AlertService failed to process delivery for event "
                    "%s recipient %s", event.id, recipient_user_id,
                )
        return event

    @classmethod
    def _deliver_one(
        cls,
        *,
        event: AlertEvent,
        recipient_user_id: str,
        channel: str,
        already_sent: bool | None,
    ) -> None:
        delivery = AlertDelivery(
            alert_event_id=event.id,
            recipient_user_id=recipient_user_id,
            channel=channel,
            delivery_status=AlertDeliveryStatus.PENDING,
        )
        try:
            db.session.add(delivery)
            db.session.commit()
        except Exception:
            db.session.rollback()
            logger.exception(
                "AlertService failed to record PENDING AlertDelivery for "
                "event %s recipient %s", event.id, recipient_user_id,
            )
            return

        if already_sent is not None:
            cls._finalize_delivery(
                delivery,
                sent=already_sent,
                error_detail=None if already_sent else "Reported undelivered by caller",
            )
            return

        if channel != AlertChannel.TELEGRAM:
            cls._finalize_delivery(
                delivery, sent=False,
                error_detail=f"Channel {channel!r} is reserved, not implemented",
            )
            return

        user = db.session.get(User, recipient_user_id)
        if user is None or not user.telegram_enabled or not user.telegram_chat_id:
            cls._finalize_delivery(
                delivery, sent=False,
                error_detail="Recipient has no connected Telegram",
            )
            return

        from app.services.telegram_service import telegram_service
        try:
            sent = telegram_service.send_message(user.telegram_chat_id, event.summary)
        except Exception as error:
            cls._finalize_delivery(delivery, sent=False, error_detail=str(error))
            return
        cls._finalize_delivery(
            delivery, sent=sent,
            error_detail=None if sent else "send_message returned False",
        )

    @classmethod
    def _finalize_delivery(
        cls, delivery: AlertDelivery, *, sent: bool, error_detail: str | None
    ) -> None:
        try:
            delivery.delivery_status = (
                AlertDeliveryStatus.SENT if sent else AlertDeliveryStatus.FAILED
            )
            delivery.resolved_at = datetime.now(timezone.utc)
            delivery.error_detail = error_detail
            db.session.commit()
        except Exception:
            db.session.rollback()
            logger.exception(
                "AlertService failed to finalize AlertDelivery %s", delivery.id,
            )

    @classmethod
    def list_events_for_source(cls, *, source_table: str, source_id: str) -> list[AlertEvent]:
        """Read-only, computed live. Every event recorded for one
        source row, oldest first."""

        return list(
            db.session.scalars(
                sa.select(AlertEvent)
                .where(
                    AlertEvent.source_table == source_table,
                    AlertEvent.source_id == source_id,
                )
                .order_by(AlertEvent.occurred_at.asc())
            ).all()
        )

    @classmethod
    def list_unresolved_deliveries(cls) -> list[AlertDelivery]:
        """Read-only. Every delivery still ``PENDING`` or already
        ``FAILED`` -- the set a future recovery process could act on.
        No such process is built in Slice 1; this only makes the set
        identifiable, per the owner's explicit requirement."""

        return list(
            db.session.scalars(
                sa.select(AlertDelivery).where(
                    AlertDelivery.delivery_status.in_(
                        (AlertDeliveryStatus.PENDING, AlertDeliveryStatus.FAILED)
                    )
                )
            ).all()
        )
