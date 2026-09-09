"""Transactional company identity commands for the M1 research domain."""

from __future__ import annotations

import re
from datetime import date, datetime, timezone
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from app import db
from app.models import (
    BusinessGroup,
    Company,
    CompanyDisclosure,
    ForecastLine,
    ForecastRevision,
    GovernanceFlag,
    MarketPlanRevision,
    OwnershipSnapshot,
    ResearchPoint,
    ResearchRevision,
    User,
    UserEntitlement,
)
from app.models.entitlement import INVESTMENT_RESEARCH_PRODUCT_CODE
from app.models.ticker import Ticker
from app.models.research_types import (
    EntitlementStatus,
    GovernanceFlagStatus,
    GovernanceSeverity,
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
from app.utils.research_validation import (
    validate_percentage,
    validate_upper_slug,
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

_OWNERSHIP_SNAPSHOT_FIELDS = {
    "as_of_date",
    "promoter_holding_pct",
    "promoter_pledge_pct",
    "notes",
    "source_reference",
}

_GOVERNANCE_FLAG_WRITABLE_FIELDS = {
    "flag_type",
    "title",
    "severity",
    "status",
    "factual_evidence",
    "source_title",
    "source_url_or_reference",
    "interpretation",
    "observed_on",
    "resolved_on",
}

_DISCLOSURE_WRITABLE_FIELDS = {
    "event_type",
    "event_date",
    "title",
    "original_source_url_or_reference",
    "exchange_reference",
    "significance_note",
    "is_key",
    "document_id",
}

_MARKET_PLAN_REVISION_FIELDS = {
    "currency",
    "accumulation_low",
    "accumulation_high",
    "preferred_accumulation_price",
    "supply_low",
    "supply_high",
    "invalidation_level",
    "rationale",
    "effective_at",
    "change_reason",
}

_FORECAST_REVISION_FIELDS = {
    "as_of_date",
    "assumptions",
    "change_reason",
}

_FORECAST_LINE_FIELDS = {
    "fiscal_year",
    "is_estimate",
    "revenue",
    "ebitda",
    "pat",
    "ebitda_margin_pct",
    "eps",
    "currency",
    "unit",
}

_FORECAST_UNITS = {
    "ABSOLUTE",
    "THOUSAND",
    "LAKH",
    "CRORE",
    "MILLION",
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

        try:
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
                if (
                    not isinstance(change_reason, str)
                    or not change_reason.strip()
                ):
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
                revision.points.append(ResearchPoint(**point_values))

            db.session.commit()
        except IntegrityError as error:
            is_revision_race = cls._is_revision_number_unique_violation(error)
            db.session.rollback()
            if is_revision_race:
                raise ResearchConflictError(
                    "revision_conflict",
                    "Research revision changed",
                ) from None
            raise
        except Exception:
            db.session.rollback()
            raise
        return revision

    @classmethod
    def create_market_plan_revision(
        cls, company_id: str, actor_user_id: str, payload: dict
    ) -> MarketPlanRevision:
        """Create one immutable market-plan revision atomically."""

        try:
            if db.session.get(Company, company_id) is None:
                raise ResearchNotFoundError(
                    "company_not_found", "Company was not found"
                )
            if db.session.get(User, actor_user_id) is None:
                raise ResearchNotFoundError(
                    "user_not_found", "User was not found"
                )

            values = cls._build_market_plan_revision_values(payload)
            supplied_base = payload.get("base_revision_id")

            current = cls._current_market_plan_revision_locked(company_id)
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
                                "The current market plan revision "
                                "must be supplied"
                            ]
                        }
                    )
                if supplied_base != current.id:
                    raise ResearchConflictError(
                        "revision_conflict",
                        "Research revision changed",
                    )
                change_reason = values["change_reason"]
                if (
                    not isinstance(change_reason, str)
                    or not change_reason.strip()
                ):
                    raise ResearchValidationError(
                        {
                            "change_reason": [
                                "Must be a non-empty reason for later "
                                "revisions"
                            ]
                        }
                    )
                revision_number = current.revision_number + 1
                supersedes_revision_id = current.id

            revision = MarketPlanRevision(
                company_id=company_id,
                revision_number=revision_number,
                supersedes_revision_id=supersedes_revision_id,
                created_by_user_id=actor_user_id,
                **values,
            )
            db.session.add(revision)
            db.session.commit()
        except IntegrityError as error:
            is_revision_race = (
                cls._is_market_plan_revision_number_unique_violation(error)
            )
            db.session.rollback()
            if is_revision_race:
                raise ResearchConflictError(
                    "revision_conflict",
                    "Research revision changed",
                ) from None
            raise
        except Exception:
            db.session.rollback()
            raise
        return revision

    @classmethod
    def create_forecast_revision(
        cls, company_id: str, actor_user_id: str, payload: dict
    ) -> ForecastRevision:
        """Create one immutable forecast revision and lines atomically."""

        try:
            if db.session.get(Company, company_id) is None:
                raise ResearchNotFoundError(
                    "company_not_found", "Company was not found"
                )
            if db.session.get(User, actor_user_id) is None:
                raise ResearchNotFoundError(
                    "user_not_found", "User was not found"
                )

            values = cls._build_forecast_revision_values(payload)
            lines = cls._build_forecast_lines(payload.get("lines", []))
            supplied_base = payload.get("base_revision_id")

            current = cls._current_forecast_revision_locked(company_id)
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
                                "The current forecast revision "
                                "must be supplied"
                            ]
                        }
                    )
                if supplied_base != current.id:
                    raise ResearchConflictError(
                        "revision_conflict",
                        "Research revision changed",
                    )
                change_reason = values["change_reason"]
                if (
                    not isinstance(change_reason, str)
                    or not change_reason.strip()
                ):
                    raise ResearchValidationError(
                        {
                            "change_reason": [
                                "Must be a non-empty reason for later "
                                "revisions"
                            ]
                        }
                    )
                revision_number = current.revision_number + 1
                supersedes_revision_id = current.id

            revision = ForecastRevision(
                company_id=company_id,
                revision_number=revision_number,
                supersedes_revision_id=supersedes_revision_id,
                created_by_user_id=actor_user_id,
                **values,
            )
            db.session.add(revision)

            for line_values in lines:
                revision.lines.append(ForecastLine(**line_values))

            db.session.commit()
        except IntegrityError as error:
            is_revision_race = (
                cls._is_forecast_revision_number_unique_violation(error)
            )
            db.session.rollback()
            if is_revision_race:
                raise ResearchConflictError(
                    "revision_conflict",
                    "Research revision changed",
                ) from None
            raise
        except Exception:
            db.session.rollback()
            raise
        return revision

    @classmethod
    def add_ownership_snapshot(
        cls, company_id: str, actor_user_id: str, payload: dict
    ) -> OwnershipSnapshot:
        """Persist one dated, append-only ownership snapshot atomically."""

        try:
            if db.session.get(Company, company_id) is None:
                raise ResearchNotFoundError(
                    "company_not_found", "Company was not found"
                )
            if db.session.get(User, actor_user_id) is None:
                raise ResearchNotFoundError(
                    "user_not_found", "User was not found"
                )

            values = cls._build_ownership_snapshot_values(payload)
            snapshot = OwnershipSnapshot(
                company_id=company_id,
                created_by_user_id=actor_user_id,
                **values,
            )
            db.session.add(snapshot)
            db.session.commit()
        except IntegrityError as error:
            is_duplicate_date = cls._is_ownership_snapshot_duplicate(error)
            db.session.rollback()
            if is_duplicate_date:
                raise ResearchConflictError(
                    "ownership_snapshot_conflict",
                    "Ownership snapshot already exists",
                ) from None
            raise
        except Exception:
            db.session.rollback()
            raise
        return snapshot

    @classmethod
    def create_governance_flag(
        cls, company_id: str, actor_user_id: str, payload: dict
    ) -> GovernanceFlag:
        """Persist one curated, sourced governance flag atomically."""

        try:
            if db.session.get(Company, company_id) is None:
                raise ResearchNotFoundError(
                    "company_not_found", "Company was not found"
                )
            if db.session.get(User, actor_user_id) is None:
                raise ResearchNotFoundError(
                    "user_not_found", "User was not found"
                )

            values = cls._build_governance_flag_values(payload)
            flag = GovernanceFlag(
                company_id=company_id,
                created_by_user_id=actor_user_id,
                **values,
            )
            db.session.add(flag)
            db.session.commit()
        except Exception:
            db.session.rollback()
            raise
        return flag

    @classmethod
    def update_governance_flag(
        cls, flag_id: str, actor_user_id: str, changes: dict
    ) -> GovernanceFlag:
        """Correct mutable flag metadata or archive the flag."""

        try:
            if db.session.get(User, actor_user_id) is None:
                raise ResearchNotFoundError(
                    "user_not_found", "User was not found"
                )
            flag = db.session.get(GovernanceFlag, flag_id)
            if flag is None:
                raise ResearchNotFoundError(
                    "governance_flag_not_found",
                    "Governance flag was not found",
                )

            archive = changes.get("archived", None)
            unknown = sorted(
                set(changes)
                - _GOVERNANCE_FLAG_WRITABLE_FIELDS
                - {"archived"}
            )
            if unknown:
                raise ResearchValidationError(
                    {field: ["Unknown field"] for field in unknown}
                )

            values = {
                field: getattr(flag, field)
                for field in _GOVERNANCE_FLAG_WRITABLE_FIELDS
            }
            for field in _GOVERNANCE_FLAG_WRITABLE_FIELDS:
                if field in changes:
                    values[field] = cls._governance_flag_value(
                        field, changes[field]
                    )
            cls._validate_governance_flag_values(values)

            if archive is not None:
                if not isinstance(archive, bool):
                    raise ResearchValidationError(
                        {
                            "archived": [
                                "Must be a boolean archive action"
                            ]
                        }
                    )
                if not archive:
                    raise ResearchValidationError(
                        {
                            "archived": [
                                "Only archival is supported; "
                                "unarchiving is not available"
                            ]
                        }
                    )
                if flag.archived_at is None:
                    flag.archived_at = datetime.now(timezone.utc)

            for field, value in values.items():
                setattr(flag, field, value)
            db.session.commit()
        except Exception:
            db.session.rollback()
            raise
        return flag

    @classmethod
    def create_disclosure(
        cls, company_id: str, actor_user_id: str, payload: dict
    ) -> CompanyDisclosure:
        """Persist one manually curated company disclosure atomically."""

        try:
            if db.session.get(Company, company_id) is None:
                raise ResearchNotFoundError(
                    "company_not_found", "Company was not found"
                )
            if db.session.get(User, actor_user_id) is None:
                raise ResearchNotFoundError(
                    "user_not_found", "User was not found"
                )

            values = cls._build_disclosure_values(payload)
            disclosure = CompanyDisclosure(
                company_id=company_id,
                created_by_user_id=actor_user_id,
                **values,
            )
            db.session.add(disclosure)
            db.session.commit()
        except Exception:
            db.session.rollback()
            raise
        return disclosure

    @classmethod
    def update_disclosure(
        cls, disclosure_id: str, actor_user_id: str, changes: dict
    ) -> CompanyDisclosure:
        """Correct mutable disclosure metadata or archive the disclosure."""

        try:
            if db.session.get(User, actor_user_id) is None:
                raise ResearchNotFoundError(
                    "user_not_found", "User was not found"
                )
            disclosure = db.session.get(
                CompanyDisclosure, disclosure_id
            )
            if disclosure is None:
                raise ResearchNotFoundError(
                    "disclosure_not_found",
                    "Company disclosure was not found",
                )

            archive = changes.get("archived", None)
            unknown = sorted(
                set(changes)
                - _DISCLOSURE_WRITABLE_FIELDS
                - {"archived"}
            )
            if unknown:
                raise ResearchValidationError(
                    {field: ["Unknown field"] for field in unknown}
                )

            values = {
                field: getattr(disclosure, field)
                for field in _DISCLOSURE_WRITABLE_FIELDS
            }
            for field in _DISCLOSURE_WRITABLE_FIELDS:
                if field in changes:
                    values[field] = cls._disclosure_value(
                        field, changes[field]
                    )

            if archive is not None:
                if not isinstance(archive, bool):
                    raise ResearchValidationError(
                        {
                            "archived": [
                                "Must be a boolean archive action"
                            ]
                        }
                    )
                if not archive:
                    raise ResearchValidationError(
                        {
                            "archived": [
                                "Only archival is supported; "
                                "unarchiving is not available"
                            ]
                        }
                    )
                if disclosure.archived_at is None:
                    disclosure.archived_at = datetime.now(timezone.utc)

            for field, value in values.items():
                setattr(disclosure, field, value)
            db.session.commit()
        except Exception:
            db.session.rollback()
            raise
        return disclosure

    @staticmethod
    def _is_ownership_snapshot_duplicate(error: IntegrityError) -> bool:
        """Identify only the unique race for a company's snapshot date."""

        constraint_name = getattr(
            getattr(error.orig, "diag", None),
            "constraint_name",
            None,
        )
        if constraint_name is not None:
            return constraint_name == "uq_ownership_snapshot_company_as_of"

        if db.engine.dialect.name != "sqlite":
            return False
        if (
            getattr(error.orig, "sqlite_errorname", None)
            != "SQLITE_CONSTRAINT_UNIQUE"
        ):
            return False

        prefix = "UNIQUE constraint failed: "
        message = str(error.orig)
        if not message.startswith(prefix):
            return False
        columns = tuple(
            column.strip() for column in message[len(prefix) :].split(",")
        )
        return columns == (
            "ownership_snapshot.company_id",
            "ownership_snapshot.as_of_date",
        )

    @staticmethod
    def _is_revision_number_unique_violation(error: IntegrityError) -> bool:
        """Identify only the unique race for a company's revision number."""

        constraint_name = getattr(
            getattr(error.orig, "diag", None),
            "constraint_name",
            None,
        )
        if constraint_name is not None:
            return constraint_name == "uq_research_revision_company_number"

        if db.engine.dialect.name != "sqlite":
            return False
        if (
            getattr(error.orig, "sqlite_errorname", None)
            != "SQLITE_CONSTRAINT_UNIQUE"
        ):
            return False

        prefix = "UNIQUE constraint failed: "
        message = str(error.orig)
        if not message.startswith(prefix):
            return False
        columns = tuple(
            column.strip() for column in message[len(prefix) :].split(",")
        )
        return columns == (
            "research_revision.company_id",
            "research_revision.revision_number",
        )

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

    @staticmethod
    def _is_market_plan_revision_number_unique_violation(
        error: IntegrityError,
    ) -> bool:
        """Identify only the unique race for a company's market-plan number."""

        constraint_name = getattr(
            getattr(error.orig, "diag", None),
            "constraint_name",
            None,
        )
        if constraint_name is not None:
            return (
                constraint_name
                == "uq_market_plan_revision_company_number"
            )

        if db.engine.dialect.name != "sqlite":
            return False
        if (
            getattr(error.orig, "sqlite_errorname", None)
            != "SQLITE_CONSTRAINT_UNIQUE"
        ):
            return False

        prefix = "UNIQUE constraint failed: "
        message = str(error.orig)
        if not message.startswith(prefix):
            return False
        columns = tuple(
            column.strip() for column in message[len(prefix) :].split(",")
        )
        return columns == (
            "market_plan_revision.company_id",
            "market_plan_revision.revision_number",
        )

    @staticmethod
    def _current_market_plan_revision_locked(
        company_id: str,
    ) -> MarketPlanRevision | None:
        """Select the current market-plan revision by highest number."""

        return db.session.scalar(
            sa.select(MarketPlanRevision)
            .where(MarketPlanRevision.company_id == company_id)
            .order_by(MarketPlanRevision.revision_number.desc())
            .with_for_update()
        )

    @classmethod
    def _is_forecast_revision_number_unique_violation(
        cls, error: IntegrityError
    ) -> bool:
        """Identify only the unique race for a company's forecast number."""

        constraint_name = getattr(
            getattr(error.orig, "diag", None),
            "constraint_name",
            None,
        )
        if constraint_name is not None:
            return (
                constraint_name
                == "uq_forecast_revision_company_number"
            )

        if db.engine.dialect.name != "sqlite":
            return False
        if (
            getattr(error.orig, "sqlite_errorname", None)
            != "SQLITE_CONSTRAINT_UNIQUE"
        ):
            return False

        prefix = "UNIQUE constraint failed: "
        message = str(error.orig)
        if not message.startswith(prefix):
            return False
        columns = tuple(
            column.strip() for column in message[len(prefix) :].split(",")
        )
        return columns == (
            "forecast_revision.company_id",
            "forecast_revision.revision_number",
        )

    @staticmethod
    def _current_forecast_revision_locked(
        company_id: str,
    ) -> ForecastRevision | None:
        """Select the current forecast revision by highest number."""

        return db.session.scalar(
            sa.select(ForecastRevision)
            .where(ForecastRevision.company_id == company_id)
            .order_by(ForecastRevision.revision_number.desc())
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
    def _build_market_plan_revision_values(cls, payload: dict) -> dict:
        unknown = sorted(
            set(payload)
            - _MARKET_PLAN_REVISION_FIELDS
            - {"base_revision_id"}
        )
        if unknown:
            raise ResearchValidationError(
                {field: ["Unknown field"] for field in unknown}
            )

        currency = cls._required_text(
            payload.get("currency", "INR"), "currency"
        )
        if currency != "INR":
            raise ResearchValidationError(
                {"currency": ["Must be INR in Milestone 1"]}
            )

        accumulation_low = cls._required_positive_decimal(
            payload.get("accumulation_low"), "accumulation_low"
        )
        accumulation_high = cls._required_positive_decimal(
            payload.get("accumulation_high"), "accumulation_high"
        )
        preferred = cls._optional_positive_decimal(
            payload.get("preferred_accumulation_price"),
            "preferred_accumulation_price",
        )
        supply_low = cls._optional_positive_decimal(
            payload.get("supply_low"), "supply_low"
        )
        supply_high = cls._optional_positive_decimal(
            payload.get("supply_high"), "supply_high"
        )
        invalidation_level = cls._required_positive_decimal(
            payload.get("invalidation_level"), "invalidation_level"
        )

        values = {
            "currency": currency,
            "accumulation_low": accumulation_low,
            "accumulation_high": accumulation_high,
            "preferred_accumulation_price": preferred,
            "supply_low": supply_low,
            "supply_high": supply_high,
            "invalidation_level": invalidation_level,
            "rationale": cls._optional_text(
                payload.get("rationale"), "rationale"
            ),
            "effective_at": cls._required_utc_datetime(
                payload.get("effective_at"), "effective_at"
            ),
            "change_reason": cls._optional_text(
                payload.get("change_reason"), "change_reason"
            ),
        }
        cls._validate_market_plan_bounds(values)
        return values

    @staticmethod
    def _validate_market_plan_bounds(values: dict) -> None:
        details: dict[str, list[str]] = {}
        accumulation_low = values["accumulation_low"]
        accumulation_high = values["accumulation_high"]
        preferred = values["preferred_accumulation_price"]
        supply_low = values["supply_low"]
        supply_high = values["supply_high"]

        if accumulation_low > accumulation_high:
            details["accumulation_low"] = [
                "Must not exceed accumulation_high"
            ]

        if (
            preferred is not None
            and not (accumulation_low <= preferred <= accumulation_high)
        ):
            details["preferred_accumulation_price"] = [
                "Must lie within the accumulation range"
            ]

        if (supply_low is None) != (supply_high is None):
            if supply_low is None:
                details["supply_low"] = [
                    "Required when supply_high is present"
                ]
            if supply_high is None:
                details["supply_high"] = [
                    "Required when supply_low is present"
                ]
        elif supply_low is not None and supply_low > supply_high:
            details["supply_low"] = ["Must not exceed supply_high"]

        if details:
            raise ResearchValidationError(details)

    @classmethod
    def _build_forecast_revision_values(cls, payload: dict) -> dict:
        unknown = sorted(
            set(payload)
            - _FORECAST_REVISION_FIELDS
            - {"base_revision_id", "lines"}
        )
        if unknown:
            raise ResearchValidationError(
                {field: ["Unknown field"] for field in unknown}
            )

        return {
            "as_of_date": cls._required_date(
                payload.get("as_of_date"), "as_of_date"
            ),
            "assumptions": cls._optional_text(
                payload.get("assumptions"), "assumptions"
            ),
            "change_reason": cls._optional_text(
                payload.get("change_reason"), "change_reason"
            ),
        }

    @classmethod
    def _build_forecast_lines(cls, raw_lines: object) -> list[dict]:
        if not isinstance(raw_lines, list):
            raise ResearchValidationError(
                {"lines": ["Must be a list of ordered lines"]}
            )

        lines = []
        seen_fiscal_years: set[int] = set()
        for index, raw_line in enumerate(raw_lines):
            if not isinstance(raw_line, dict):
                raise ResearchValidationError(
                    {
                        "lines": [
                            f"Line {index} must be an object"
                        ]
                    }
                )
            unknown = sorted(set(raw_line) - _FORECAST_LINE_FIELDS)
            if unknown:
                raise ResearchValidationError(
                    {
                        "lines": [
                            f"Line {index} has unknown fields: "
                            + ", ".join(unknown)
                        ]
                    }
                )

            fiscal_year = cls._required_fiscal_year(
                raw_line.get("fiscal_year"), "fiscal_year"
            )
            if fiscal_year in seen_fiscal_years:
                raise ResearchValidationError(
                    {
                        "fiscal_year": [
                            f"Fiscal year {fiscal_year} is duplicated "
                            "inside this revision"
                        ]
                    }
                )
            seen_fiscal_years.add(fiscal_year)

            currency = raw_line.get("currency", "INR")
            if not isinstance(currency, str) or currency != "INR":
                raise ResearchValidationError(
                    {"currency": ["Must be INR in Milestone 1"]}
                )

            unit = raw_line.get("unit")
            if not isinstance(unit, str) or unit not in _FORECAST_UNITS:
                raise ResearchValidationError(
                    {
                        "unit": [
                            "Must be ABSOLUTE, THOUSAND, LAKH, CRORE, "
                            "or MILLION"
                        ]
                    }
                )

            revenue = cls._optional_fixed_decimal(
                raw_line.get("revenue"), "revenue"
            )
            ebitda = cls._optional_fixed_decimal(
                raw_line.get("ebitda"), "ebitda"
            )
            pat = cls._optional_fixed_decimal(
                raw_line.get("pat"), "pat"
            )
            ebitda_margin_pct = cls._optional_percentage(
                raw_line.get("ebitda_margin_pct"),
                "ebitda_margin_pct",
            )
            eps = cls._optional_fixed_decimal(
                raw_line.get("eps"), "eps"
            )

            line = {
                "fiscal_year": fiscal_year,
                "is_estimate": cls._boolean_value(
                    raw_line.get("is_estimate"), "is_estimate"
                ),
                "revenue": revenue,
                "ebitda": ebitda,
                "pat": pat,
                "ebitda_margin_pct": ebitda_margin_pct,
                "eps": eps,
                "currency": currency,
                "unit": unit,
            }
            lines.append(line)
        return lines

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

    @classmethod
    def _build_ownership_snapshot_values(cls, payload: dict) -> dict:
        unknown = sorted(set(payload) - _OWNERSHIP_SNAPSHOT_FIELDS)
        if unknown:
            raise ResearchValidationError(
                {field: ["Unknown field"] for field in unknown}
            )

        promoter_holding_pct = cls._optional_percentage(
            payload.get("promoter_holding_pct"),
            "promoter_holding_pct",
        )
        promoter_pledge_pct = cls._optional_percentage(
            payload.get("promoter_pledge_pct"),
            "promoter_pledge_pct",
        )
        source_reference = cls._optional_text(
            payload.get("source_reference"), "source_reference"
        )
        if (
            promoter_holding_pct is not None or promoter_pledge_pct is not None
        ) and source_reference is None:
            raise ResearchValidationError(
                {
                    "source_reference": [
                        "Required when either percentage is present"
                    ]
                }
            )

        return {
            "as_of_date": cls._required_date(
                payload.get("as_of_date"), "as_of_date"
            ),
            "promoter_holding_pct": promoter_holding_pct,
            "promoter_pledge_pct": promoter_pledge_pct,
            "notes": cls._optional_text(payload.get("notes"), "notes"),
            "source_reference": source_reference,
        }

    @classmethod
    def _build_governance_flag_values(cls, payload: dict) -> dict:
        unknown = sorted(set(payload) - _GOVERNANCE_FLAG_WRITABLE_FIELDS)
        if unknown:
            raise ResearchValidationError(
                {field: ["Unknown field"] for field in unknown}
            )

        values = {
            field: cls._governance_flag_value(
                field, payload.get(field)
            )
            for field in _GOVERNANCE_FLAG_WRITABLE_FIELDS
        }
        cls._validate_governance_flag_values(values)
        return values

    @classmethod
    def _governance_flag_value(cls, field: str, value: object) -> object:
        if field == "severity":
            return cls._closed_research_value(
                value,
                "severity",
                (
                    GovernanceSeverity.INFO,
                    GovernanceSeverity.LOW,
                    GovernanceSeverity.MEDIUM,
                    GovernanceSeverity.HIGH,
                    GovernanceSeverity.CRITICAL,
                ),
            )
        if field == "status":
            return cls._closed_research_value(
                value,
                "status",
                (
                    GovernanceFlagStatus.OPEN,
                    GovernanceFlagStatus.MONITORING,
                    GovernanceFlagStatus.RESOLVED,
                    GovernanceFlagStatus.DISMISSED,
                ),
            )
        if field in {"observed_on", "resolved_on"}:
            return cls._optional_date(value, field)
        if field in {
            "flag_type",
            "title",
            "factual_evidence",
            "source_url_or_reference",
            "interpretation",
        }:
            return cls._required_text(value, field)
        return cls._optional_text(value, field)

    @staticmethod
    def _validate_governance_flag_values(values: dict) -> None:
        details: dict[str, list[str]] = {}
        status = values["status"]
        resolved_on = values["resolved_on"]
        observed_on = values["observed_on"]

        if status == GovernanceFlagStatus.RESOLVED:
            if resolved_on is None:
                details["resolved_on"] = [
                    "Required when the flag is RESOLVED"
                ]
        elif resolved_on is not None:
            details["resolved_on"] = [
                "Only allowed when the flag is RESOLVED"
            ]

        if (
            resolved_on is not None
            and observed_on is not None
            and resolved_on < observed_on
        ):
            details.setdefault("resolved_on", []).append(
                "Must not precede observed_on"
            )

        if details:
            raise ResearchValidationError(details)

    @classmethod
    def _build_disclosure_values(cls, payload: dict) -> dict:
        unknown = sorted(set(payload) - _DISCLOSURE_WRITABLE_FIELDS)
        if unknown:
            raise ResearchValidationError(
                {field: ["Unknown field"] for field in unknown}
            )

        return {
            "event_type": cls._required_upper_slug(
                payload.get("event_type"), "event_type"
            ),
            "event_date": cls._required_date(
                payload.get("event_date"), "event_date"
            ),
            "title": cls._required_text(
                payload.get("title"), "title"
            ),
            "original_source_url_or_reference": cls._required_text(
                payload.get("original_source_url_or_reference"),
                "original_source_url_or_reference",
            ),
            "exchange_reference": cls._optional_text(
                payload.get("exchange_reference"),
                "exchange_reference",
            ),
            "significance_note": cls._optional_text(
                payload.get("significance_note"), "significance_note"
            ),
            "is_key": cls._boolean_value(
                payload.get("is_key", False), "is_key"
            ),
            "document_id": cls._null_document_id(
                payload.get("document_id")
            ),
        }

    @classmethod
    def _disclosure_value(cls, field: str, value: object) -> object:
        if field == "event_type":
            return cls._required_upper_slug(value, field)
        if field == "event_date":
            return cls._required_date(value, field)
        if field == "title":
            return cls._required_text(value, field)
        if field == "original_source_url_or_reference":
            return cls._required_text(value, field)
        if field == "is_key":
            return cls._boolean_value(value, field)
        if field == "document_id":
            return cls._null_document_id(value)
        return cls._optional_text(value, field)

    @staticmethod
    def _required_upper_slug(value: object, field: str) -> str:
        normalized = ResearchCommandService._required_text(value, field)
        return validate_upper_slug(normalized, field)

    @staticmethod
    def _boolean_value(value: object, field: str) -> bool:
        if not isinstance(value, bool):
            raise ResearchValidationError(
                {field: ["Must be a boolean"]}
            )
        return value

    @staticmethod
    def _null_document_id(value: object) -> None:
        if value is None:
            return None
        raise ResearchValidationError(
            {
                "document_id": [
                    "Must be null until document metadata is available"
                ]
            }
        )

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
    def _required_date(value: object, field: str) -> date:
        if not isinstance(value, date) or isinstance(value, datetime):
            raise ResearchValidationError(
                {field: ["Must be a date"]}
            )
        return value

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
    def _optional_percentage(value: object, field: str) -> Decimal | None:
        if value is None:
            return None
        validate_percentage(value, field)
        return value

    @staticmethod
    def _required_fiscal_year(value: object, field: str) -> int:
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or value < 1000
            or value > 9999
        ):
            raise ResearchValidationError(
                {field: ["Must be a four-digit integer year"]}
            )
        return value

    @staticmethod
    def _optional_fixed_decimal(
        value: object, field: str
    ) -> Decimal | None:
        if value is None:
            return None
        if (
            not isinstance(value, Decimal)
            or not value.is_finite()
        ):
            raise ResearchValidationError(
                {field: ["Must be a finite fixed-precision decimal"]}
            )
        return value

    @staticmethod
    def _required_positive_decimal(value: object, field: str) -> Decimal:
        if (
            not isinstance(value, Decimal)
            or not value.is_finite()
            or value <= 0
        ):
            raise ResearchValidationError(
                {field: ["Must be a finite positive decimal"]}
            )
        return value

    @staticmethod
    def _optional_positive_decimal(
        value: object, field: str
    ) -> Decimal | None:
        if value is None:
            return None
        return ResearchCommandService._required_positive_decimal(
            value, field
        )

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
