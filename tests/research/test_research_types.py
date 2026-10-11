import pytest
import sqlalchemy as sa

from app.models.research_types import (
    EntitlementStatus,
    GovernanceFlagStatus,
    GovernanceSeverity,
    GovernanceStatus,
    ManagementQuality,
    ResearchPointKind,
    ResearchTier,
    ValuationMethod,
    enum_type,
    money_column,
)


def public_values(classification):
    return {key: value for key, value in vars(classification).items() if not key.startswith("_")}


def test_closed_research_classifications_expose_only_approved_machine_values():
    assert public_values(ResearchTier) == {"FREE": "FREE", "PREMIUM": "PREMIUM"}
    assert public_values(EntitlementStatus) == {
        "ACTIVE": "ACTIVE",
        "INACTIVE": "INACTIVE",
        "REVOKED": "REVOKED",
    }
    assert public_values(ManagementQuality) == {
        "UNASSESSED": "UNASSESSED",
        "WEAK": "WEAK",
        "WATCH": "WATCH",
        "ACCEPTABLE": "ACCEPTABLE",
        "STRONG": "STRONG",
    }
    assert public_values(GovernanceStatus) == {
        "UNREVIEWED": "UNREVIEWED",
        "CLEAR": "CLEAR",
        "WATCH": "WATCH",
        "HIGH_RISK": "HIGH_RISK",
    }
    assert public_values(GovernanceSeverity) == {
        "INFO": "INFO",
        "LOW": "LOW",
        "MEDIUM": "MEDIUM",
        "HIGH": "HIGH",
        "CRITICAL": "CRITICAL",
    }
    assert public_values(GovernanceFlagStatus) == {
        "OPEN": "OPEN",
        "MONITORING": "MONITORING",
        "RESOLVED": "RESOLVED",
        "DISMISSED": "DISMISSED",
    }
    assert public_values(ResearchPointKind) == {"CATALYST": "CATALYST", "RISK": "RISK"}
    assert public_values(ValuationMethod) == {
        "PE": "PE",
        "EV_EBITDA": "EV_EBITDA",
        "PB": "PB",
        "NAV": "NAV",
        "SOTP": "SOTP",
        "ASSET_VALUE": "ASSET_VALUE",
        "UNIT_BASED": "UNIT_BASED",
        "OTHER": "OTHER",
    }


def test_enum_type_is_portable_and_rejects_unknown_closed_value():
    classification = enum_type("valuation_method", ("PE", "NAV"))

    assert classification.native_enum is False
    assert classification.validate_strings is True
    assert classification.enums == ["PE", "NAV"]
    with pytest.raises(LookupError):
        classification._db_value_for_elem("OTHER")


def test_money_column_uses_fixed_financial_precision_and_requested_nullability():
    nullable_column = money_column()
    required_column = money_column(nullable=False)

    assert isinstance(nullable_column.type, sa.Numeric)
    assert (nullable_column.type.precision, nullable_column.type.scale) == (20, 4)
    assert nullable_column.nullable is True
    assert required_column.nullable is False
