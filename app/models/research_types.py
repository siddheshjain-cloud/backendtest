from collections.abc import Sequence

import sqlalchemy as sa


class ResearchTier:
    FREE = "FREE"
    PREMIUM = "PREMIUM"


class EntitlementStatus:
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"
    REVOKED = "REVOKED"


class ManagementQuality:
    UNASSESSED = "UNASSESSED"
    WEAK = "WEAK"
    WATCH = "WATCH"
    ACCEPTABLE = "ACCEPTABLE"
    STRONG = "STRONG"


class GovernanceStatus:
    UNREVIEWED = "UNREVIEWED"
    CLEAR = "CLEAR"
    WATCH = "WATCH"
    HIGH_RISK = "HIGH_RISK"


class GovernanceSeverity:
    INFO = "INFO"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class GovernanceFlagStatus:
    OPEN = "OPEN"
    MONITORING = "MONITORING"
    RESOLVED = "RESOLVED"
    DISMISSED = "DISMISSED"


class ResearchPointKind:
    CATALYST = "CATALYST"
    RISK = "RISK"


class ValuationMethod:
    PE = "PE"
    EV_EBITDA = "EV_EBITDA"
    PB = "PB"
    NAV = "NAV"
    SOTP = "SOTP"
    ASSET_VALUE = "ASSET_VALUE"
    UNIT_BASED = "UNIT_BASED"
    OTHER = "OTHER"


class Scenario:
    """Phase 3 scenario slug, shared by ForecastRevision and ValuationRevision.

    ``MID_CYCLE`` is a normalized-earnings reference point, not a
    probability-weighted scenario -- callers computing expected-return
    weighting must exclude it (Phase 3 Slice G, not built yet).
    """

    BULL = "BULL"
    BASE = "BASE"
    BEAR = "BEAR"
    MID_CYCLE = "MID_CYCLE"


class InvestmentOrigin:
    """Who authored a Phase 3 judgment-layer row.

    Set once, at insertion, on tables that are immutable after insertion
    (ForecastRevision, ValuationRevision, and -- once built in their own
    slices -- InvestmentHypothesis/InvestmentCase). A ``SYSTEM_DRAFT`` row
    is never authoritative on its own; "promotion" is a later
    ``HUMAN_AUTHORED`` revision that supersedes it via the existing
    ``supersedes_revision_id``/``supersedes_case_id`` chain -- there is no
    separate ``promoted_by``/``promoted_at`` field, because the superseding
    revision's own ``created_by_user_id``/``created_at`` already carries
    that audit trail, and a mutable "promoted" marker on an
    immutable-after-insertion row is not implementable.
    """

    SYSTEM_DRAFT = "SYSTEM_DRAFT"
    HUMAN_AUTHORED = "HUMAN_AUTHORED"


class FinancialMetricNamespace:
    RAW = "RAW"
    NORMALIZED = "NORMALIZED"


def enum_type(name: str, values: Sequence[str]) -> sa.Enum:
    return sa.Enum(*values, name=name, native_enum=False, validate_strings=True)


def money_column(nullable: bool = True) -> sa.Column:
    return sa.Column(sa.Numeric(20, 4), nullable=nullable)
