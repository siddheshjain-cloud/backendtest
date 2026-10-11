"""I3AS Slice 1: ``AlertEvent``/``AlertDelivery``/``AlertService``
acceptance tests.

Covers: the natural-key idempotency constraint on ``AlertEvent``
(including the per-transition-event-type refinement that lets the same
source record legitimately fire more than one distinct event without
being rejected as a duplicate); immutability of ``AlertEvent``; the one
legal ``PENDING`` -> ``SENT``/``FAILED`` transition on
``AlertDelivery`` and nothing else; ``AlertService.record_event``'s two
delivery modes (caller-reported outcome vs. a real send); and that
``AlertService`` never raises, by construction, regardless of what
fails underneath it.
"""

from __future__ import annotations

import sqlalchemy as sa

from app import db
from app.models.alert import AlertDelivery, AlertEvent
from app.models.alert_types import AlertDeliveryStatus
from app.services.alert_service import AlertService


def test_record_event_creates_event_and_delivery_with_caller_reported_outcome(
    free_user,
):
    free_user.telegram_enabled = True
    free_user.telegram_chat_id = "55555"
    db.session.commit()

    event = AlertService.record_event(
        event_type="TRADE_ENTRY_HIT",
        source_table="trade",
        source_id="trade-1",
        summary="Trade X reached Entry",
        recipient_user_ids=[free_user.id],
        already_delivered={free_user.id: True},
    )

    assert event is not None
    assert event.event_type == "TRADE_ENTRY_HIT"
    assert event.source_table == "trade"
    assert event.source_id == "trade-1"

    delivery = db.session.scalar(
        sa.select(AlertDelivery).where(AlertDelivery.alert_event_id == event.id)
    )
    assert delivery.recipient_user_id == free_user.id
    assert delivery.delivery_status == AlertDeliveryStatus.SENT
    assert delivery.resolved_at is not None
    assert delivery.error_detail is None


def test_record_event_caller_reported_outcome_never_calls_send_message(
    free_user, monkeypatch
):
    """The anti-duplication requirement: when the caller already
    performed the send (Alert-1's mode), AlertService must not attempt
    a second, independent Telegram send."""

    free_user.telegram_enabled = True
    free_user.telegram_chat_id = "55555"
    db.session.commit()

    from app.services.telegram_service import telegram_service

    calls = []
    monkeypatch.setattr(
        telegram_service, "send_message",
        lambda chat_id, text: calls.append((chat_id, text)) or True,
    )

    AlertService.record_event(
        event_type="TRADE_ENTRY_HIT",
        source_table="trade",
        source_id="trade-2",
        summary="Trade Y reached Entry",
        recipient_user_ids=[free_user.id],
        already_delivered={free_user.id: True},
    )

    assert calls == []


def test_record_event_without_already_delivered_performs_real_send(
    free_user, monkeypatch
):
    free_user.telegram_enabled = True
    free_user.telegram_chat_id = "55555"
    db.session.commit()

    from app.services.telegram_service import telegram_service

    calls = []
    monkeypatch.setattr(
        telegram_service, "send_message",
        lambda chat_id, text: calls.append((chat_id, text)) or True,
    )

    event = AlertService.record_event(
        event_type="KILL_SWITCH_TRIGGERED",
        source_table="investment_case_kill_switch",
        source_id="kill-switch-1",
        summary="Kill switch fired",
        recipient_user_ids=[free_user.id],
    )

    assert calls == [("55555", "Kill switch fired")]
    delivery = db.session.scalar(
        sa.select(AlertDelivery).where(AlertDelivery.alert_event_id == event.id)
    )
    assert delivery.delivery_status == AlertDeliveryStatus.SENT


def test_record_event_marks_failed_when_not_connected(free_user):
    event = AlertService.record_event(
        event_type="KILL_SWITCH_TRIGGERED",
        source_table="investment_case_kill_switch",
        source_id="kill-switch-2",
        summary="Kill switch fired",
        recipient_user_ids=[free_user.id],
    )

    delivery = db.session.scalar(
        sa.select(AlertDelivery).where(AlertDelivery.alert_event_id == event.id)
    )
    assert delivery.delivery_status == AlertDeliveryStatus.FAILED
    assert delivery.error_detail == "Recipient has no connected Telegram"
    assert delivery in AlertService.list_unresolved_deliveries()


def test_duplicate_occurrence_is_idempotent_no_op(free_user):
    first = AlertService.record_event(
        event_type="TRADE_ENTRY_HIT",
        source_table="trade",
        source_id="trade-3",
        summary="Trade Z reached Entry",
        recipient_user_ids=[free_user.id],
        already_delivered={free_user.id: True},
    )
    second = AlertService.record_event(
        event_type="TRADE_ENTRY_HIT",
        source_table="trade",
        source_id="trade-3",
        summary="Trade Z reached Entry (retried)",
        recipient_user_ids=[free_user.id],
        already_delivered={free_user.id: True},
    )

    assert first is not None
    assert second is None
    assert not db.session().in_transaction()
    rows = db.session.scalars(
        sa.select(AlertEvent).where(
            AlertEvent.source_table == "trade", AlertEvent.source_id == "trade-3"
        )
    ).all()
    assert len(rows) == 1


def test_same_source_two_distinct_transitions_both_recorded(free_user):
    """The per-transition event-type refinement: the same Trade (same
    source_id) legitimately fires two different real occurrences --
    Entry, then later Target -- and both must be recorded, not rejected
    as duplicates of each other."""

    entry_event = AlertService.record_event(
        event_type="TRADE_ENTRY_HIT",
        source_table="trade",
        source_id="trade-4",
        summary="Trade W reached Entry",
        recipient_user_ids=[free_user.id],
        already_delivered={free_user.id: True},
    )
    target_event = AlertService.record_event(
        event_type="TRADE_TARGET_HIT",
        source_table="trade",
        source_id="trade-4",
        summary="Trade W reached Target",
        recipient_user_ids=[free_user.id],
        already_delivered={free_user.id: True},
    )

    assert entry_event is not None
    assert target_event is not None
    assert entry_event.id != target_event.id
    rows = AlertService.list_events_for_source(source_table="trade", source_id="trade-4")
    assert [row.event_type for row in rows] == ["TRADE_ENTRY_HIT", "TRADE_TARGET_HIT"]


def test_record_event_never_raises_on_database_failure(free_user, monkeypatch):
    """Fail-safe by construction: even a hard failure recording the
    AlertEvent itself must not propagate past AlertService."""

    def _boom(*args, **kwargs):
        raise RuntimeError("simulated database outage")

    monkeypatch.setattr(db.session, "commit", _boom)

    result = AlertService.record_event(
        event_type="TRADE_ENTRY_HIT",
        source_table="trade",
        source_id="trade-5",
        summary="Trade V reached Entry",
        recipient_user_ids=[free_user.id],
        already_delivered={free_user.id: True},
    )

    assert result is None
    assert not db.session().in_transaction()


def test_alert_event_immutability_enforced(free_user):
    event = AlertService.record_event(
        event_type="TRADE_ENTRY_HIT",
        source_table="trade",
        source_id="trade-6",
        summary="Trade U reached Entry",
        recipient_user_ids=[free_user.id],
        already_delivered={free_user.id: True},
    )

    event.summary = "changed"
    try:
        import pytest
        with pytest.raises(sa.exc.InvalidRequestError):
            db.session.commit()
    finally:
        db.session.rollback()

    import pytest
    with pytest.raises(sa.exc.InvalidRequestError):
        db.session.delete(event)
        db.session.commit()
    db.session.rollback()


def test_alert_delivery_transitions_exactly_once(free_user):
    import pytest

    event = AlertService.record_event(
        event_type="TRADE_ENTRY_HIT",
        source_table="trade",
        source_id="trade-7",
        summary="Trade T reached Entry",
        recipient_user_ids=[free_user.id],
        already_delivered={free_user.id: True},
    )
    delivery = db.session.scalar(
        sa.select(AlertDelivery).where(AlertDelivery.alert_event_id == event.id)
    )
    assert delivery.delivery_status == AlertDeliveryStatus.SENT

    # Re-finalizing an already-terminal delivery is rejected.
    delivery.delivery_status = AlertDeliveryStatus.FAILED
    with pytest.raises(sa.exc.InvalidRequestError):
        db.session.commit()
    db.session.rollback()

    # Mutating any other field is always rejected.
    db.session.refresh(delivery)
    delivery.error_detail = "changed"
    with pytest.raises(sa.exc.InvalidRequestError):
        db.session.commit()
    db.session.rollback()
