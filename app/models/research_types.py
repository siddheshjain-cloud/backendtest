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


def enum_type(name: str, values: Sequence[str]) -> sa.Enum:
    return sa.Enum(*values, name=name, native_enum=False, validate_strings=True)


def money_column(nullable: bool = True) -> sa.Column:
    return sa.Column(sa.Numeric(20, 4), nullable=nullable)
