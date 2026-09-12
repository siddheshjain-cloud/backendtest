"""Plan 5 Task 7: deterministic, idempotent IKIO real-data reference seed.

The seed composes the existing M1 command services into one safe entry point.
It does not fabricate binary document content, does not append duplicate
revision streams, and preserves any pre-existing matching rows instead of
overwriting them. Revisioned aggregates are created only when their stream is
absent for the IKIO company.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

import sqlalchemy as sa

from app import db
from app.models import (
    BusinessGroup,
    Company,
    CompanyDisclosure,
    Document,
    DocumentCompanyLink,
    ForecastRevision,
    GovernanceFlag,
    MarketPlanRevision,
    OwnershipSnapshot,
    ResearchRevision,
    Ticker,
    User,
    UserEntitlement,
    ValuationRevision,
)
from app.models.document import (
    AcquisitionMethod,
    DiscoverySourceType,
    DistributionStatus,
    DocumentType,
    IngestionStatus,
    SourceAccess,
)
from app.models.research_types import (
    EntitlementStatus,
    GovernanceFlagStatus,
    GovernanceSeverity,
    GovernanceStatus,
    ManagementQuality,
    ResearchPointKind,
    ResearchTier,
    ValuationMethod,
)
from app.models.entitlement import INVESTMENT_RESEARCH_PRODUCT_CODE
from app.services.document_library_service import DocumentLibraryService
from app.services.research_command_service import ResearchCommandService


UTC = timezone.utc

EFFECTIVE_AT = datetime(2026, 9, 10, 12, 0, tzinfo=UTC)
AS_OF_DATE = date(2026, 9, 4)

IKIO_SYMBOL = "IKIO"
IKIO_EXCHANGE = "NSE"
IKIO_TICKER_NAME = "IKIO Technologies Limited"
IKIO_LEGAL_NAME = "IKIO Lighting Limited"
IKIO_ISIN = "INE0LOJ01019"
IKIO_SECTOR = "Industrials"
IKIO_INDUSTRY = "LED lighting"

IKIO_GROUP_NAME = "IKIO Promoter Group"
IKIO_GROUP_NOTES = "public group context"
IKIO_GROUP_SOURCE_REFERENCE = "https://example.in/group/source"

OWNERSHIP_SOURCE_URL = "https://example.in/ownership/source"
GOVERNANCE_SOURCE_URL = "https://example.in/governance"
DISCLOSURE_SOURCE_URL = "https://exchange.example/ref/key"

SEED_ADMIN_EMAIL = "ikio-seed-admin@example.com"
SEED_PREMIUM_EMAIL = "ikio-seed-premium@example.com"
SEED_PROVIDER_EMAIL = "ikio-seed-provider@example.com"


class ResearchSeedService:
    """One safe, repeatable IKIO reference-graph seeder."""

    @classmethod
    def run(cls) -> dict[str, object]:
        """Create the frozen IKIO reference graph once and return its IDs."""

        admin = cls._get_or_create_user(
            SEED_ADMIN_EMAIL,
            "IKIO Seed Administrator",
            is_admin=True,
        )
        premium = cls._get_or_create_user(
            SEED_PREMIUM_EMAIL,
            "IKIO Seed Premium Consumer",
            is_admin=False,
        )
        provider = cls._get_or_create_user(
            SEED_PROVIDER_EMAIL,
            "IKIO Seed Document Provider",
            is_admin=False,
        )

        ticker = cls._get_or_create_ticker()
        group = cls._get_or_create_business_group()
        company = cls._get_or_create_company(ticker, group, admin.id)

        cls._ensure_premium_entitlement(premium.id, admin.id)

        shareholding_document = cls._ensure_document(
            company.id,
            admin.id,
            cls._shareholding_document_payload(),
        )
        governance_document = cls._ensure_document(
            company.id,
            admin.id,
            cls._governance_document_payload(),
        )
        disclosure_document = cls._ensure_document(
            company.id,
            admin.id,
            cls._disclosure_document_payload(),
        )
        restricted_document = cls._ensure_document(
            company.id,
            admin.id,
            cls._restricted_document_payload(provider.id),
        )

        research = cls._ensure_research_revision(company.id, admin.id)
        forecast = cls._ensure_forecast_revision(company.id, admin.id)
        valuation = cls._ensure_valuation_revision(
            company.id,
            admin.id,
            forecast,
        )
        market_plan = cls._ensure_market_plan_revision(
            company.id, admin.id
        )
        ownership = cls._ensure_ownership_snapshot(
            company.id, admin.id
        )
        governance_flag = cls._ensure_governance_flag(
            company.id, admin.id
        )
        disclosure = cls._ensure_disclosure(
            company.id, admin.id
        )

        return {
            "effective_at": EFFECTIVE_AT,
            "ticker_id": ticker.id,
            "business_group_id": group.id,
            "company_id": company.id,
            "admin_user_id": admin.id,
            "premium_user_id": premium.id,
            "provider_user_id": provider.id,
            "shareholding_document_id": shareholding_document.id,
            "governance_document_id": governance_document.id,
            "disclosure_document_id": disclosure_document.id,
            "restricted_document_id": restricted_document.id,
            "research_revision_id": research.id,
            "forecast_revision_id": forecast.id,
            "valuation_revision_id": valuation.id,
            "market_plan_revision_id": market_plan.id,
            "ownership_snapshot_id": ownership.id,
            "governance_flag_id": governance_flag.id,
            "disclosure_id": disclosure.id,
        }

    # ------------------------------------------------------------------
    # Stable identity lookups
    # ------------------------------------------------------------------

    @staticmethod
    def _get_or_create_user(
        email: str,
        name: str,
        *,
        is_admin: bool,
    ) -> User:
        existing = db.session.scalar(
            sa.select(User).where(User.email == email)
        )
        if existing is not None:
            return existing

        user = User(name=name, email=email, is_admin=is_admin)
        user.set_password("ikio-seed-password")
        db.session.add(user)
        db.session.commit()
        return user

    @staticmethod
    def _get_or_create_ticker() -> Ticker:
        existing = db.session.scalar(
            sa.select(Ticker).where(Ticker.symbol == IKIO_SYMBOL)
        )
        if existing is not None:
            return existing

        instrument_token = 1001
        while (
            db.session.scalar(
                sa.select(Ticker.id).where(
                    Ticker.instrument_token == instrument_token
                )
            )
            is not None
        ):
            instrument_token += 1

        ticker = Ticker(
            symbol=IKIO_SYMBOL,
            exchange=IKIO_EXCHANGE,
            instrument_token=instrument_token,
            name=IKIO_TICKER_NAME,
            last_price=123.45,
            last_updated=EFFECTIVE_AT,
        )
        db.session.add(ticker)
        db.session.commit()
        return ticker

    @staticmethod
    def _get_or_create_business_group() -> BusinessGroup:
        existing = db.session.scalar(
            sa.select(BusinessGroup).where(
                BusinessGroup.name == IKIO_GROUP_NAME
            )
        )
        if existing is not None:
            return existing

        group = BusinessGroup(
            name=IKIO_GROUP_NAME,
            notes=IKIO_GROUP_NOTES,
            source_reference=IKIO_GROUP_SOURCE_REFERENCE,
        )
        db.session.add(group)
        db.session.commit()
        return group

    @staticmethod
    def _get_or_create_company(
        ticker: Ticker,
        group: BusinessGroup,
        actor_user_id: str,
    ) -> Company:
        existing = db.session.scalar(
            sa.select(Company).where(Company.isin == IKIO_ISIN)
        )
        if existing is None:
            existing = db.session.scalar(
                sa.select(Company).where(Company.ticker_id == ticker.id)
            )
        if existing is not None:
            return existing

        return ResearchCommandService.create_company(
            {
                "ticker_id": ticker.id,
                "legal_name": IKIO_LEGAL_NAME,
                "display_name": None,
                "isin": IKIO_ISIN,
                "sector": IKIO_SECTOR,
                "industry": IKIO_INDUSTRY,
                "business_group_id": group.id,
                "business_group_basis": "CONSOLIDATED_FINANCIALS",
                "business_group_source_reference": (
                    IKIO_GROUP_SOURCE_REFERENCE
                ),
            },
            actor_user_id,
        )

    @staticmethod
    def _ensure_premium_entitlement(
        premium_user_id: str,
        actor_user_id: str,
    ) -> None:
        existing = db.session.scalar(
            sa.select(UserEntitlement).where(
                UserEntitlement.user_id == premium_user_id,
                UserEntitlement.product_code
                == INVESTMENT_RESEARCH_PRODUCT_CODE,
            )
        )
        if existing is not None:
            return

        ResearchCommandService.upsert_entitlement(
            premium_user_id,
            {
                "tier": ResearchTier.PREMIUM,
                "status": EntitlementStatus.ACTIVE,
                "valid_from": None,
                "valid_until": None,
            },
            actor_user_id,
        )

    # ------------------------------------------------------------------
    # Document evidence
    # ------------------------------------------------------------------

    @staticmethod
    def _document_payload(document_type: str, title: str) -> dict:
        return {
            "document_type": document_type,
            "title": title,
            "document_date": AS_OF_DATE.isoformat(),
            "publisher_name": IKIO_LEGAL_NAME,
            "discovery_source_type": DiscoverySourceType.EXCHANGE,
            "source_access": SourceAccess.PUBLIC,
            "acquisition_method": AcquisitionMethod.MANUAL_REFERENCE,
            "distribution_status": DistributionStatus.LINK_ONLY,
            "ingestion_status": IngestionStatus.DISCOVERED,
        }

    @classmethod
    def _shareholding_document_payload(cls) -> dict:
        return {
            "document": {
                **cls._document_payload(
                    DocumentType.OTHER,
                    "IKIO Shareholding Pattern",
                ),
                "reporting_period": "Q1 FY27",
                "original_source_url": OWNERSHIP_SOURCE_URL,
                "discovery_source_reference": (
                    "NSE shareholding pattern reference"
                ),
            }
        }

    @classmethod
    def _governance_document_payload(cls) -> dict:
        return {
            "document": {
                **cls._document_payload(
                    DocumentType.REG30_ATTACHMENT,
                    "IKIO Promoter Pledge Governance Source",
                ),
                "original_source_url": GOVERNANCE_SOURCE_URL,
                "discovery_source_reference": "NSE:IKIO governance source",
            }
        }

    @classmethod
    def _disclosure_document_payload(cls) -> dict:
        return {
            "document": {
                **cls._document_payload(
                    DocumentType.REG30_ATTACHMENT,
                    "IKIO Key Disclosure",
                ),
                "original_source_url": DISCLOSURE_SOURCE_URL,
                "discovery_source_reference": "NSE:IKIO key disclosure",
            }
        }

    @staticmethod
    def _restricted_document_payload(provider_user_id: str) -> dict:
        return {
            "document": {
                "document_type": DocumentType.OTHER,
                "title": "IKIO Restricted Evidence",
                "document_date": AS_OF_DATE.isoformat(),
                "publisher_name": IKIO_LEGAL_NAME,
                "discovery_source_type": DiscoverySourceType.USER,
                "source_access": SourceAccess.RESTRICTED,
                "acquisition_method": AcquisitionMethod.MANUAL_REFERENCE,
                "distribution_status": DistributionStatus.PRIVATE_LIBRARY,
                "ingestion_status": IngestionStatus.DISCOVERED,
                "provided_by_user_id": provider_user_id,
            }
        }

    @staticmethod
    def _existing_company_document(
        company_id: str,
        document_type: str,
        title: str,
    ) -> Document | None:
        return db.session.scalar(
            sa.select(Document)
            .join(
                DocumentCompanyLink,
                DocumentCompanyLink.document_id == Document.id,
            )
            .where(
                DocumentCompanyLink.company_id == company_id,
                DocumentCompanyLink.is_primary.is_(True),
                Document.document_type == document_type,
                Document.title == title,
            )
        )

    @classmethod
    def _ensure_document(
        cls,
        company_id: str,
        actor_user_id: str,
        payload: dict[str, object],
    ) -> Document:
        document_fields = payload["document"]
        existing = cls._existing_company_document(
            company_id,
            document_fields["document_type"],
            document_fields["title"],
        )
        if existing is not None:
            return existing

        complete_payload = dict(payload)
        complete_payload["company_links"] = [
            {"company_id": company_id, "is_primary": True}
        ]
        return DocumentLibraryService.create_document(
            complete_payload,
            actor_user_id,
        )

    # ------------------------------------------------------------------
    # Revisioned aggregates
    # ------------------------------------------------------------------

    @staticmethod
    def _current_revision(model, company_id: str):
        return db.session.scalar(
            sa.select(model)
            .where(model.company_id == company_id)
            .order_by(model.revision_number.desc())
            .limit(1)
        )

    @classmethod
    def _ensure_research_revision(
        cls,
        company_id: str,
        actor_user_id: str,
    ) -> ResearchRevision:
        existing = cls._current_revision(ResearchRevision, company_id)
        if existing is not None:
            return existing

        return ResearchCommandService.create_research_revision(
            company_id,
            actor_user_id,
            {
                "why_selected": (
                    "IKIO Lighting Limited is the first real-company "
                    "reference identity in the SPA M1 Research Brain."
                ),
                "what_is_changing": None,
                "business_journey": (
                    "Listed Indian LED lighting company used as the "
                    "frozen M1 reference case."
                ),
                "thesis": (
                    "LED lighting demand and product mix are representative "
                    "seed hypotheses for the M1 research projection."
                ),
                "thesis_invalidation": (
                    "The seed hypothesis is invalid if the reference growth "
                    "or customer-concentration assumptions do not hold."
                ),
                "management_summary": (
                    "Frozen M1 seed uses the WATCH management-quality "
                    "default; it does not assert an external management rating."
                ),
                "management_quality": ManagementQuality.WATCH,
                "management_rationale": (
                    "No external management evidence is supplied by the "
                    "frozen seed."
                ),
                "management_evidence": (
                    "M1 seed evidence policy: absent external evidence, "
                    "the reference assessment remains WATCH."
                ),
                "governance_status": GovernanceStatus.WATCH,
                "change_reason": None,
                "effective_at": EFFECTIVE_AT,
                "points": [
                    {
                        "kind": ResearchPointKind.CATALYST,
                        "title": "LED lighting demand",
                        "detail": (
                            "Reference catalyst hypothesis; not a verified "
                            "operating fact."
                        ),
                        "status": "OPEN",
                        "target_date": date(2027, 3, 31),
                        "sort_order": 0,
                    },
                    {
                        "kind": ResearchPointKind.RISK,
                        "title": "Customer concentration",
                        "detail": (
                            "Reference risk hypothesis; not a verified "
                            "operating fact."
                        ),
                        "status": None,
                        "target_date": None,
                        "sort_order": 0,
                    },
                ],
            },
        )

    @classmethod
    def _ensure_forecast_revision(
        cls,
        company_id: str,
        actor_user_id: str,
    ) -> ForecastRevision:
        existing = cls._current_revision(ForecastRevision, company_id)
        if existing is not None:
            return existing

        return ResearchCommandService.create_forecast_revision(
            company_id,
            actor_user_id,
            {
                "as_of_date": AS_OF_DATE,
                "assumptions": (
                    "Reference M1 forecast values from the approved seed "
                    "fixture; these are estimates, not historical facts."
                ),
                "change_reason": None,
                "lines": [
                    {
                        "fiscal_year": 2027,
                        "is_estimate": True,
                        "revenue": Decimal("1234.5000"),
                        "ebitda": Decimal("250.7500"),
                        "pat": Decimal("10.0000"),
                        "ebitda_margin_pct": Decimal("20.3100"),
                        "eps": Decimal("5.0000"),
                        "currency": "INR",
                        "unit": "CRORE",
                    }
                ],
            },
        )

    @classmethod
    def _ensure_valuation_revision(
        cls,
        company_id: str,
        actor_user_id: str,
        forecast: ForecastRevision,
    ) -> ValuationRevision:
        existing = db.session.scalar(
            sa.select(ValuationRevision)
            .where(
                ValuationRevision.company_id == company_id,
                ValuationRevision.valuation_method == ValuationMethod.PE,
            )
            .order_by(ValuationRevision.revision_number.desc())
            .limit(1)
        )
        if existing is not None:
            return existing

        return ResearchCommandService.create_valuation_revision(
            company_id,
            actor_user_id,
            {
                "valuation_method": ValuationMethod.PE,
                "justified_multiple": Decimal("12.5000"),
                "implied_enterprise_value": None,
                "net_debt": Decimal("998877.5000"),
                "other_equity_adjustment": None,
                "implied_future_equity_value": Decimal("999999.0000"),
                "required_return_pct": None,
                "discount_period_years": None,
                "present_value": None,
                "current_market_cap": Decimal("900.0000"),
                "currency": "INR",
                "unit": "CRORE",
                "valuation_notes": (
                    "Reference PE valuation from the approved M1 seed "
                    "fixture; it is an administrator-supplied conclusion."
                ),
                "as_of_date": AS_OF_DATE,
                "change_reason": None,
                "reference_lines": [
                    {
                        "reference_forecast_revision_id": forecast.id,
                        "reference_fiscal_year": 2027,
                        "reference_metric": "EPS",
                        "reference_metric_value": Decimal("5.0000"),
                        "reference_metric_unit": "INR_PER_SHARE",
                        "reference_metric_basis": (
                            "Reference EPS line from the IKIO forecast "
                            "revision."
                        ),
                        "sort_order": 0,
                    }
                ],
            },
        )

    @classmethod
    def _ensure_market_plan_revision(
        cls,
        company_id: str,
        actor_user_id: str,
    ) -> MarketPlanRevision:
        existing = cls._current_revision(MarketPlanRevision, company_id)
        if existing is not None:
            return existing

        return ResearchCommandService.create_market_plan_revision(
            company_id,
            actor_user_id,
            {
                "currency": "INR",
                "accumulation_low": Decimal("100.0000"),
                "accumulation_high": Decimal("130.0000"),
                "preferred_accumulation_price": None,
                "supply_low": None,
                "supply_high": None,
                "invalidation_level": Decimal("90.0000"),
                "rationale": (
                    "Reference accumulation and invalidation levels from "
                    "the approved M1 seed fixture."
                ),
                "effective_at": EFFECTIVE_AT,
                "change_reason": None,
            },
        )

    # ------------------------------------------------------------------
    # Sourced actual facts
    # ------------------------------------------------------------------

    @classmethod
    def _ensure_ownership_snapshot(
        cls,
        company_id: str,
        actor_user_id: str,
    ) -> OwnershipSnapshot:
        existing = db.session.scalar(
            sa.select(OwnershipSnapshot).where(
                OwnershipSnapshot.company_id == company_id,
                OwnershipSnapshot.as_of_date == AS_OF_DATE,
            )
        )
        if existing is not None:
            return existing

        return ResearchCommandService.add_ownership_snapshot(
            company_id,
            actor_user_id,
            {
                "as_of_date": AS_OF_DATE,
                "promoter_holding_pct": Decimal("64.5000"),
                "promoter_pledge_pct": None,
                "notes": "Reference IKIO ownership snapshot from seed.",
                "source_reference": OWNERSHIP_SOURCE_URL,
            },
        )

    @classmethod
    def _ensure_governance_flag(
        cls,
        company_id: str,
        actor_user_id: str,
    ) -> GovernanceFlag:
        existing = db.session.scalar(
            sa.select(GovernanceFlag).where(
                GovernanceFlag.company_id == company_id,
                GovernanceFlag.flag_type == "PROMOTER_PLEDGE",
                GovernanceFlag.title
                == "IKIO Promoter Pledge Governance Source",
            )
        )
        if existing is not None:
            return existing

        return ResearchCommandService.create_governance_flag(
            company_id,
            actor_user_id,
            {
                "flag_type": "PROMOTER_PLEDGE",
                "title": "IKIO Promoter Pledge Governance Source",
                "severity": GovernanceSeverity.HIGH,
                "status": GovernanceFlagStatus.OPEN,
                "factual_evidence": (
                    "Promoter pledge disclosure represented by the "
                    "approved seed source."
                ),
                "source_title": "IKIO Promoter Pledge Governance Source",
                "source_url_or_reference": GOVERNANCE_SOURCE_URL,
                "interpretation": (
                    "Reference governance interpretation; not an external "
                    "rating."
                ),
                "observed_on": AS_OF_DATE,
                "resolved_on": None,
            },
        )

    @classmethod
    def _ensure_disclosure(
        cls,
        company_id: str,
        actor_user_id: str,
    ) -> CompanyDisclosure:
        existing = db.session.scalar(
            sa.select(CompanyDisclosure).where(
                CompanyDisclosure.company_id == company_id,
                CompanyDisclosure.event_type == "REG30",
                CompanyDisclosure.title == "IKIO Key Disclosure",
            )
        )
        if existing is not None:
            return existing

        return ResearchCommandService.create_disclosure(
            company_id,
            actor_user_id,
            {
                "event_type": "REG30",
                "event_date": AS_OF_DATE,
                "title": "IKIO Key Disclosure",
                "original_source_url_or_reference": DISCLOSURE_SOURCE_URL,
                "exchange_reference": "NSE:IKIO",
                "significance_note": (
                    "Reference disclosure from the approved M1 seed fixture."
                ),
                "is_key": True,
                "document_id": None,
            },
        )
