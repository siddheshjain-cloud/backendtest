"""Transactional company identity commands for the M1 research domain."""

from __future__ import annotations

import re
from datetime import datetime, timezone

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from app import db
from app.models import BusinessGroup, Company, User, UserEntitlement
from app.models.entitlement import INVESTMENT_RESEARCH_PRODUCT_CODE
from app.models.ticker import Ticker
from app.models.research_types import EntitlementStatus, ResearchTier
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
