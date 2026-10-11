"""I3AS Slice 1 -- Integrated Investment Intelligence Alert System.

Shared vocabulary for ``AlertEvent``/``AlertDelivery`` (see
``app/models/alert.py``). Owner-approved design proposal: `stock-price-
alert` repo, `docs/superpowers/plans/2026-10-12-integrated-investment-
intelligence-alert-system-design-proposal.md`.

``event_type`` itself is deliberately a plain ``String`` column on
``AlertEvent``, not wrapped in a closed ``CHECK`` enum -- the design
proposal's own "smallest practical... register incrementally" principle
(matching ``ExtractedFact.fact_type``/``ForecastAssumption.metric_slug``'s
named-by-convention idiom elsewhere in this codebase): a new event type
is a new string value used by convention, documented here, never a
migration. ``AlertEventType`` below is a plain namespace of known
values, not an enforced vocabulary.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa


class AlertSeverity:
    """How urgently an ``AlertEvent`` should be read."""

    INFO = "INFO"
    WATCH = "WATCH"
    CRITICAL = "CRITICAL"


class AlertChannel:
    """Delivery channel for one ``AlertDelivery`` attempt.

    Only ``TELEGRAM`` is wired to anything in Slice 1. ``WHATSAPP`` and
    ``WEBHOOK`` are reserved names only (owner-approved future
    possibilities -- WhatsApp, and a Sensybull-style outbound bridge,
    respectively) so the enum itself needs no migration when one of
    them is eventually built.
    """

    TELEGRAM = "TELEGRAM"
    WHATSAPP = "WHATSAPP"
    WEBHOOK = "WEBHOOK"


class AlertDeliveryStatus:
    """Lifecycle of one ``AlertDelivery`` row.

    ``PENDING`` is recorded durably *before* a delivery attempt is
    made, so a crash mid-attempt leaves a real, queryable row rather
    than losing the attempt silently. Exactly one transition is ever
    legal: ``PENDING`` -> ``SENT`` or ``PENDING`` -> ``FAILED``, never
    reversed or repeated -- see ``app/models/alert.py``'s
    ``_reject_illegal_delivery_update``.
    """

    PENDING = "PENDING"
    SENT = "SENT"
    FAILED = "FAILED"


class AlertEventType:
    """Known ``AlertEvent.event_type`` values (documentation, not an
    enforced vocabulary -- see module docstring).

    Slice 1 (legacy Trade/Telegram absorption): one specific event type
    per ``Trade`` status transition, deliberately *not* one generic
    ``TRADE_STATUS_CHANGED`` value -- a single ``Trade`` can legitimately
    fire more than one transition over its lifecycle (e.g. ``ENTRY``
    then later ``TARGET``), and ``AlertEvent``'s own uniqueness
    constraint is on ``(source_table, source_id, event_type)`` (see
    ``app/models/alert.py``). A single generic event type would make
    that constraint wrongly reject the trade's second real transition,
    since it would share both ``source_id`` and ``event_type`` with the
    first. Each specific transition below can only ever fire once per
    ``Trade`` (``Trade.check``'s own state machine: ``ACTIVE`` ->
    ``ENTRY`` -> a terminal ``STOPLOSS``/``TARGET``), so the natural key
    stays both unique and correct.
    """

    TRADE_ENTRY_HIT = "TRADE_ENTRY_HIT"
    TRADE_STOPLOSS_HIT = "TRADE_STOPLOSS_HIT"
    TRADE_TARGET_HIT = "TRADE_TARGET_HIT"

    # Slice 2 (Investment Brain integration). Each value's source row is
    # itself a fresh, newly-created id every time (InvestmentCaseKillSwitch
    # can only ever trigger once; each InvestmentCaseGateResult/InvestmentCase
    # is a brand-new append-only row even on supersession), so the
    # per-transition-naming discipline above is not needed here -- there is
    # no legitimate way for the same source_id to fire the same event_type
    # twice.
    KILL_SWITCH_TRIGGERED = "KILL_SWITCH_TRIGGERED"
    INVESTMENT_CASE_GATE_FAILED = "INVESTMENT_CASE_GATE_FAILED"
    INVESTMENT_CASE_CREATED = "INVESTMENT_CASE_CREATED"
    INVESTMENT_CASE_VIEW_CHANGED = "INVESTMENT_CASE_VIEW_CHANGED"


def enum_type(name: str, values: Sequence[str]) -> sa.Enum:
    return sa.Enum(*values, name=name, native_enum=False, validate_strings=True)
