"""Transactional company identity commands for the M1 research domain."""

from __future__ import annotations

import re
from datetime import date, datetime, timezone

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from app import db
from app.models import (
    BusinessGroup,
    Company,
    ResearchPoint,
    ResearchRevision,
    User,
    UserEntitlement,
)
from app.models.entitlement import INVESTMENT_RESEARCH_PRODUCT_CODE
from app.models.ticker import Ticker
from app.models.research_types import (
    EntitlementStatus,
    GovernanceStatus,
    ManagementQuality,
    ResearchPointKind,
    ResearchTier,
)
from app.utils.research_errors import (
    ResearchConflictError,
    ResearchNotFoundError,
    ResearchValidationError,
)


ISIN_PATTERN = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")

_COMPANY_WRITABLE_FIELDS = {
    "ticker_id",
    "legal_name",
    "display_name",
    "isin",
    "sector",
    "industry",
    "business_group_id",
    "business_group_basis",
    "business_group_source_reference",
}

_ENTITLEMENT_WRITABLE_FIELDS = {
    "tier",
    "status",
    "valid_from",
    "valid_until",
}

_RESEARCH_REVISION_FIELDS = {
    "why_selected",
    "what_is_changing",
    "business_journey",
    "thesis",
    "thesis_invalidation",
    "management_summary",
    "management_quality",
    "management_rationale",
    "management_evidence",
    "governance_status",
    "change_reason",
    "effective_at",
}

_RESEARCH_POINT_FIELDS = {
    "kind",
    "title",
    "detail",
    "status",
    "target_date",
    "sort_order",
}


class ResearchCommandService:
    """Owns M1 domain validation, transactions, and rollbacks."""

    @classmethod
    def create_company(cls, payload: dict, actor_user_id: str) -> Company:
        del actor_user_id  # entitlement and actor enforcement arrive with later tasks.
        values = cls._build_company_values(payload)
        cls._resolve_ticker(values["ticker_id"])
        if values["business_group_id"] is not None:
            cls._resolve_business_group(values["business_group_id"])

        company = Company(**values)
        db.session.add(company)
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            raise ResearchConflictError(
                "company_identity_conflict",
                "Company identity already exists",
            ) from None
        return company

    @classmethod
    def update_company(
        cls, company_id: str, changes: dict, actor_user_id: str
    ) -> Company:
        del actor_user_id
        cls._reject_unknown_fields(changes)

        company = db.session.get(Company, company_id)
        if company is None:
            raise ResearchNotFoundError(
                "company_not_found", "Company was not found"
            )

        values = {
            field: getattr(company, field) for field in _COMPANY_WRITABLE_FIELDS
        }
        for field, value in changes.items():
            if field == "isin":
                values[field] = cls._normalize_isin(value, field)
            elif field in {"ticker_id", "legal_name"}:
                values[field] = cls._required_text(value, field)
            else:
                values[field] = cls._optional_text(value, field)

        cls._validate_group_evidence(values)
        cls._resolve_ticker(values["ticker_id"])
        if values["business_group_id"] is not None:
            cls._resolve_business_group(values["business_group_id"])

        for field, value in values.items():
            setattr(company, field, value)

        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            raise ResearchConflictError(
                "company_identity_conflict",
                "Company identity already exists",
            ) from None
        return company

    @classmethod
    def upsert_entitlement(
        cls, user_id: str, payload: dict, actor_user_id: str
    ) -> UserEntitlement:
        """Create or update the unique INVESTMENT_RESEARCH entitlement row."""

        del actor_user_id  # actor enforcement arrives with later tasks.
        values = cls._build_entitlement_values(payload)

        if db.session.get(User, user_id) is None:
            raise ResearchNotFoundError(
                "user_not_found", "User was not found"
            )

        entitlement = db.session.scalar(
            sa.select(UserEntitlement)
            .where(
                UserEntitlement.user_id == user_id,
                UserEntitlement.product_code
                == INVESTMENT_RESEARCH_PRODUCT_CODE,
            )
            .with_for_update()
        )
        if entitlement is None:
            entitlement = UserEntitlement(
                user_id=user_id,
                product_code=INVESTMENT_RESEARCH_PRODUCT_CODE,
                **values,
            )
            db.session.add(entitlement)
        else:
            for field, value in values.items():
                setattr(entitlement, field, value)

        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            raise ResearchConflictError(
                "entitlement_conflict",
                "User entitlement already exists",
            ) from None
        return entitlement

    @classmethod
    def create_research_revision(
        cls, company_id: str, actor_user_id: str, payload: dict
    ) -> ResearchRevision:
        """Create an immutable research revision and all points atomically."""

        if db.session.get(Company, company_id) is None:
            raise ResearchNotFoundError(
                "company_not_found", "Company was not found"
            )
        if db.session.get(User, actor_user_id) is None:
            raise ResearchNotFoundError(
                "user_not_found", "User was not found"
            )

        values = cls._build_research_revision_values(payload)
        points = cls._build_research_points(payload.get("points", []))
        supplied_base = payload.get("base_revision_id")

        current = cls._current_revision_locked(company_id)
        if current is None:
            if supplied_base is not None:
                raise ResearchConflictError(
                    "revision_conflict",
                    "Research revision changed",
                )
            revision_number = 1
            supersedes_revision_id = None
            change_reason = values["change_reason"]
            if change_reason is not None:
                raise ResearchValidationError(
                    {
                        "change_reason": [
                            "Cannot be supplied for the first revision"
                        ]
                    }
                )
        else:
            if supplied_base is None:
                raise ResearchValidationError(
                    {
                        "base_revision_id": [
                            "The current research revision must be supplied"
                        ]
                    }
                )
            if supplied_base != current.id:
                raise ResearchConflictError(
                    "revision_conflict",
                    "Research revision changed",
                )
            change_reason = values["change_reason"]
            if not isinstance(change_reason, str) or not change_reason.strip():
                raise ResearchValidationError(
                    {
                        "change_reason": [
                            "Must be a non-empty reason for later revisions"
                        ]
                    }
                )
            revision_number = current.revision_number + 1
            supersedes_revision_id = current.id

        revision = ResearchRevision(
            company_id=company_id,
            revision_number=revision_number,
            supersedes_revision_id=supersedes_revision_id,
            created_by_user_id=actor_user_id,
            **values,
        )
        db.session.add(revision)

        for point_values in points:
            point = ResearchPoint(
                **point_values,
            )
            revision.points.append(point)

        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            raise ResearchConflictError(
                "revision_conflict",
                "Research revision changed",
            ) from None
        return revision

    @staticmethod
    def _current_revision_locked(
        company_id: str,
    ) -> ResearchRevision | None:
        """Select the current revision by highest number under a row lock."""

        return db.session.scalar(
            sa.select(ResearchRevision)
            .where(ResearchRevision.company_id == company_id)
            .order_by(ResearchRevision.revision_number.desc())
            .with_for_update()
        )

    @classmethod
    def _build_company_values(cls, payload: dict) -> dict:
        cls._reject_unknown_fields(payload)
        values = {
            "ticker_id": cls._required_text(payload.get("ticker_id"), "ticker_id"),
            "legal_name": cls._required_text(
                payload.get("legal_name"), "legal_name"
            ),
            "display_name": cls._optional_text(
                payload.get("display_name"), "display_name"
            ),
            "isin": cls._normalize_isin(payload.get("isin"), "isin"),
            "sector": cls._optional_text(payload.get("sector"), "sector"),
            "industry": cls._optional_text(
                payload.get("industry"), "industry"
            ),
            "business_group_id": cls._optional_text(
                payload.get("business_group_id"), "business_group_id"
            ),
            "business_group_basis": cls._optional_text(
                payload.get("business_group_basis"),
                "business_group_basis",
            ),
            "business_group_source_reference": cls._optional_text(
                payload.get("business_group_source_reference"),
                "business_group_source_reference",
            ),
        }
        cls._validate_group_evidence(values)
        return values

    @classmethod
    def _build_research_revision_values(cls, payload: dict) -> dict:
        unknown = sorted(
            set(payload)
            - _RESEARCH_REVISION_FIELDS
            - {"base_revision_id", "points"}
        )
        if unknown:
            raise ResearchValidationError(
                {field: ["Unknown field"] for field in unknown}
            )

        management_quality = cls._closed_research_value(
            payload.get("management_quality"),
            "management_quality",
            (
                ManagementQuality.UNASSESSED,
                ManagementQuality.WEAK,
                ManagementQuality.WATCH,
                ManagementQuality.ACCEPTABLE,
                ManagementQuality.STRONG,
            ),
        )
        management_rationale = cls._optional_text(
            payload.get("management_rationale"), "management_rationale"
        )
        if (
            management_quality != ManagementQuality.UNASSESSED
            and management_rationale is None
        ):
            raise ResearchValidationError(
                {
                    "management_rationale": [
                        "Required when management quality is not UNASSESSED"
                    ]
                }
            )

        return {
            "why_selected": cls._required_text(
                payload.get("why_selected"), "why_selected"
            ),
            "what_is_changing": cls._optional_text(
                payload.get("what_is_changing"), "what_is_changing"
            ),
            "business_journey": cls._optional_text(
                payload.get("business_journey"), "business_journey"
            ),
            "thesis": cls._required_text(
                payload.get("thesis"), "thesis"
            ),
            "thesis_invalidation": cls._required_text(
                payload.get("thesis_invalidation"), "thesis_invalidation"
            ),
            "management_summary": cls._optional_text(
                payload.get("management_summary"), "management_summary"
            ),
            "management_quality": management_quality,
            "management_rationale": management_rationale,
            "management_evidence": cls._optional_text(
                payload.get("management_evidence"), "management_evidence"
            ),
            "governance_status": cls._closed_research_value(
                payload.get("governance_status"),
                "governance_status",
                (
                    GovernanceStatus.UNREVIEWED,
                    GovernanceStatus.CLEAR,
                    GovernanceStatus.WATCH,
                    GovernanceStatus.HIGH_RISK,
                ),
            ),
            "change_reason": cls._optional_text(
                payload.get("change_reason"), "change_reason"
            ),
            "effective_at": cls._required_utc_datetime(
                payload.get("effective_at"), "effective_at"
            ),
        }

    @classmethod
    def _build_research_points(cls, raw_points: object) -> list[dict]:
        if not isinstance(raw_points, list):
            raise ResearchValidationError(
                {"points": ["Must be a list of ordered points"]}
            )

        points = []
        for index, raw_point in enumerate(raw_points):
            if not isinstance(raw_point, dict):
                raise ResearchValidationError(
                    {
                        "points": [
                            f"Point {index} must be an object"
                        ]
                    }
                )
            unknown = sorted(set(raw_point) - _RESEARCH_POINT_FIELDS)
            if unknown:
                raise ResearchValidationError(
                    {
                        "points": [
                            f"Point {index} has unknown fields: "
                            + ", ".join(unknown)
                        ]
                    }
                )
            point = {
                "kind": cls._closed_research_value(
                    raw_point.get("kind"),
                    "points",
                    (
                        ResearchPointKind.CATALYST,
                        ResearchPointKind.RISK,
                    ),
                ),
                "title": cls._required_text(
                    raw_point.get("title"), "points"
                ),
                "detail": cls._optional_text(
                    raw_point.get("detail"), "points"
                ),
                "status": cls._optional_text(
                    raw_point.get("status"), "points"
                ),
                "target_date": cls._optional_date(
                    raw_point.get("target_date"), "points"
                ),
                "sort_order": cls._non_negative_int(
                    raw_point.get("sort_order"), "points"
                ),
            }
            points.append(point)
        return points

    @classmethod
    def _build_entitlement_values(cls, payload: dict) -> dict:
        cls._reject_unknown_entitlement_fields(payload)
        return {
            "tier": cls._closed_entitlement_value(
                payload.get("tier"),
                "tier",
                (ResearchTier.FREE, ResearchTier.PREMIUM),
            ),
            "status": cls._closed_entitlement_value(
                payload.get("status"),
                "status",
                (
                    EntitlementStatus.ACTIVE,
                    EntitlementStatus.INACTIVE,
                    EntitlementStatus.REVOKED,
                ),
            ),
            "valid_from": cls._optional_utc_datetime(
                payload.get("valid_from"), "valid_from"
            ),
            "valid_until": cls._optional_utc_datetime(
                payload.get("valid_until"), "valid_until"
            ),
        }

    @staticmethod
    def _reject_unknown_fields(payload: dict) -> None:
        unknown = sorted(set(payload) - _COMPANY_WRITABLE_FIELDS)
        if unknown:
            raise ResearchValidationError(
                {field: ["Unknown field"] for field in unknown}
            )

    @staticmethod
    def _reject_unknown_entitlement_fields(payload: dict) -> None:
        unknown = sorted(set(payload) - _ENTITLEMENT_WRITABLE_FIELDS)
        if unknown:
            raise ResearchValidationError(
                {field: ["Unknown field"] for field in unknown}
            )

    @staticmethod
    def _closed_entitlement_value(
        value: object, field: str, allowed: tuple[str, ...]
    ) -> str:
        if not isinstance(value, str) or value not in allowed:
            raise ResearchValidationError(
                {field: ["Must be one of the approved values"]}
            )
        return value

    @staticmethod
    def _optional_utc_datetime(
        value: object, field: str
    ) -> datetime | None:
        if value is None:
            return None
        if not isinstance(value, datetime) or value.tzinfo is None:
            raise ResearchValidationError(
                {field: ["Must be a timezone-aware UTC datetime"]}
            )
        return value.astimezone(timezone.utc)

    @staticmethod
    def _required_utc_datetime(value: object, field: str) -> datetime:
        if not isinstance(value, datetime) or value.tzinfo is None:
            raise ResearchValidationError(
                {field: ["Must be a timezone-aware datetime"]}
            )
        return value.astimezone(timezone.utc)

    @staticmethod
    def _optional_date(value: object, field: str) -> date | None:
        if value is None:
            return None
        if not isinstance(value, date) or isinstance(value, datetime):
            raise ResearchValidationError(
                {field: ["Must be a date"]}
            )
        return value

    @staticmethod
    def _non_negative_int(value: object, field: str) -> int:
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or value < 0
        ):
            raise ResearchValidationError(
                {field: ["Must be a non-negative integer"]}
            )
        return value

    @staticmethod
    def _required_text(value: object, field: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ResearchValidationError({field: ["Must not be blank"]})
        return value.strip()

    @staticmethod
    def _optional_text(value: object, field: str) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ResearchValidationError({field: ["Must be text"]})
        return value.strip() or None

    @staticmethod
    def _closed_research_value(
        value: object, field: str, allowed: tuple[str, ...]
    ) -> str:
        if not isinstance(value, str) or value not in allowed:
            raise ResearchValidationError(
                {field: ["Must be one of the approved values"]}
            )
        return value

    @staticmethod
    def _normalize_isin(value: object, field: str) -> str:
        normalized = ResearchCommandService._required_text(value, field).upper()
        if not ISIN_PATTERN.fullmatch(normalized):
            raise ResearchValidationError(
                {field: ["Must be a valid 12-character ISIN"]}
            )
        return normalized

    @staticmethod
    def _validate_group_evidence(values: dict) -> None:
        details: dict[str, list[str]] = {}
        has_group = values["business_group_id"] is not None
        has_basis = values["business_group_basis"] is not None
        has_source = values["business_group_source_reference"] is not None

        if has_group:
            if not has_basis:
                details["business_group_basis"] = [
                    "Required when a business group is assigned"
                ]
            if not has_source:
                details["business_group_source_reference"] = [
                    "Required when a business group is assigned"
                ]
        else:
            if has_basis:
                details["business_group_basis"] = [
                    "Cannot be supplied without a business group"
                ]
            if has_source:
                details["business_group_source_reference"] = [
                    "Cannot be supplied without a business group"
                ]

        if details:
            raise ResearchValidationError(details)

    @staticmethod
    def _resolve_ticker(ticker_id: str) -> Ticker:
        ticker = db.session.get(Ticker, ticker_id)
        if ticker is None:
            raise ResearchNotFoundError(
                "ticker_not_found", "Ticker was not found"
            )
        return ticker

    @staticmethod
    def _resolve_business_group(business_group_id: str) -> BusinessGroup:
        group = db.session.get(BusinessGroup, business_group_id)
        if group is None:
            raise ResearchNotFoundError(
                "business_group_not_found", "Business group was not found"
            )
        return group
