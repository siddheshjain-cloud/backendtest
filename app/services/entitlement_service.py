"""Plan 3 Task 1: database-backed research entitlement resolution."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from app.models.entitlement import (
    INVESTMENT_RESEARCH_PRODUCT_CODE,
    UserEntitlement,
)
from app.models.research_types import EntitlementStatus, ResearchTier
from app.models.user import User


@dataclass(frozen=True)
class ResearchAccessContext:
    """Immutable research access context for one resolved request."""

    user_id: str
    is_admin: bool
    tier: str


def _as_utc(value: datetime) -> datetime:
    """Normalize a stored UTC timestamp, including SQLite naive reloads."""

    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def resolve_effective_tier(
    row: UserEntitlement | None, check_time: datetime
) -> str:
    """Resolve the effective tier, failing closed for every unsafe state."""

    if row is None:
        return ResearchTier.FREE
    if row.status != EntitlementStatus.ACTIVE:
        return ResearchTier.FREE
    if row.tier not in (ResearchTier.FREE, ResearchTier.PREMIUM):
        return ResearchTier.FREE
    if (
        row.valid_from is not None
        and _as_utc(row.valid_from) > check_time
    ):
        return ResearchTier.FREE
    if (
        row.valid_until is not None
        and _as_utc(row.valid_until) < check_time
    ):
        return ResearchTier.FREE
    return row.tier


class EntitlementService:
    """Resolve research access from the supplied user and database row only."""

    @staticmethod
    def resolve(user: User, at: datetime | None = None) -> ResearchAccessContext:
        check_time = at or datetime.now(timezone.utc)
        row = UserEntitlement.query.filter_by(
            user_id=user.id,
            product_code=INVESTMENT_RESEARCH_PRODUCT_CODE,
        ).one_or_none()
        tier = resolve_effective_tier(row, check_time)
        return ResearchAccessContext(user.id, bool(user.is_admin), tier)
