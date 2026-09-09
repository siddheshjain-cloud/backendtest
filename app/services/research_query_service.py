"""Plan 3 Task 4: company list and current-detail query service.

``ResearchQueryService`` composes read-only SQLAlchemy queries over the
frozen M1 domain. List queries return entitlement-safe company summaries
(identity, assigned business group, and the read-only market quote). Detail
queries build the explicit company aggregate consumed by ``ResearchPresenter``;
premium relations are loaded only when ``ResearchAccessPolicy`` authorizes
them for the immutable access context.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TypeVar

import sqlalchemy as sa
from sqlalchemy.orm import selectinload

from app import db
from app.models import (
    Company,
    ForecastLine,
    ForecastRevision,
    GovernanceFlag,
    MarketPlanRevision,
    OwnershipSnapshot,
    ResearchPoint,
    ResearchRevision,
    Ticker,
    ValuationReferenceLine,
    ValuationRevision,
)
from app.models.research_types import (
    ResearchPointKind,
)
from app.policies.research_access import ResearchAccessPolicy
from app.services.entitlement_service import ResearchAccessContext
from app.services.market_price_service import MarketPriceService
from app.utils.research_errors import (
    ResearchNotFoundError,
    ResearchValidationError,
)


@dataclass(frozen=True)
class PageResult:
    """Deterministic pagination result used by list and collection queries."""

    items: list[object]
    page: int
    per_page: int
    total_items: int
    total_pages: int


# Presenter aggregate keys map to ResearchAccessPolicy section names.
_AGGREGATE_SECTION_KEYS = {
    "research": "research",
    "management": "management",
    "governance": "governance",
    "ownership": "ownership",
    "market_plan": "market_plan",
    "forecast": "forecast",
    "valuations": "valuation",
}


def _validate_page_bounds(page: int, per_page: int) -> None:
    details: dict[str, list[str]] = {}
    if not isinstance(page, int) or isinstance(page, bool) or page < 1:
        details["page"] = ["Must be a positive integer"]
    if (
        not isinstance(per_page, int)
        or isinstance(per_page, bool)
        or per_page < 1
        or per_page > 100
    ):
        details["per_page"] = ["Must be an integer between 1 and 100"]
    if details:
        raise ResearchValidationError(details)


def _company_dict(company: Company) -> dict[str, object]:
    return {
        "id": company.id,
        "name": company.display_label,
        "symbol": company.ticker.symbol,
        "exchange": company.ticker.exchange,
        "isin": company.isin,
        "sector": company.sector,
        "industry": company.industry,
    }


def _business_group_dict(
    company: Company,
) -> dict[str, object] | None:
    group = company.business_group
    if group is None:
        return None
    return {
        "name": group.name,
        "notes": group.notes,
        "source_reference": group.source_reference,
    }


def _market_quote_dict(company: Company) -> dict[str, object]:
    return MarketPriceService.project(company.ticker)


def _as_utc(value: datetime) -> datetime:
    """Normalize a stored UTC timestamp, including SQLite naive reloads."""

    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _company_summary(company: Company) -> dict[str, object]:
    """Free company summary: identity, assigned group, and market quote."""

    payload: dict[str, object] = {
        "company": _company_dict(company),
        "market_quote": _market_quote_dict(company),
    }
    group = _business_group_dict(company)
    if group is not None:
        payload["business_group"] = group
    return payload


class ResearchQueryService:
    """Read-only current research queries over the frozen M1 domain."""

    @staticmethod
    def list_companies(
        *,
        q: str | None,
        sector: str | None,
        industry: str | None,
        page: int,
        per_page: int,
        context: ResearchAccessContext,
    ) -> PageResult:
        """Paginated entitlement-safe company summaries."""

        del context
        _validate_page_bounds(page, per_page)

        display_label = sa.func.coalesce(
            Company.display_name, Company.legal_name
        )
        statement = sa.select(Company).join(
            Ticker, Company.ticker_id == Ticker.id
        )

        if sector is not None:
            statement = statement.where(Company.sector == sector)
        if industry is not None:
            statement = statement.where(Company.industry == industry)
        if q:
            term = f"%{q.strip()}%"
            statement = statement.where(
                sa.or_(
                    Company.legal_name.ilike(term),
                    Company.display_name.ilike(term),
                    Company.isin.ilike(term),
                    Ticker.symbol.ilike(term),
                    Ticker.name.ilike(term),
                )
            )

        total_items = db.session.scalar(
            sa.select(sa.func.count()).select_from(statement.subquery())
        )
        statement = (
            statement.distinct()
            .options(
                selectinload(Company.ticker),
                selectinload(Company.business_group),
            )
            .order_by(
                sa.func.lower(display_label),
                Company.id,
            )
            .offset((page - 1) * per_page)
            .limit(per_page)
        )
        companies = db.session.scalars(statement).all()

        return PageResult(
            items=[_company_summary(company) for company in companies],
            page=page,
            per_page=per_page,
            total_items=total_items,
            total_pages=max(1, (total_items + per_page - 1) // per_page),
        )

    @staticmethod
    def get_company_detail(
        company_id: str,
        context: ResearchAccessContext,
    ) -> dict[str, object]:
        """Current company research aggregate for the access context."""

        company = db.session.scalar(
            sa.select(Company)
            .where(Company.id == company_id)
            .options(
                selectinload(Company.ticker),
                selectinload(Company.business_group),
            )
        )
        if company is None:
            raise ResearchNotFoundError(
                "company_not_found", "Company was not found"
            )

        allowed = ResearchAccessPolicy.allowed_sections(context)
        payload: dict[str, object] = {
            "company": _company_dict(company),
            "market_quote": _market_quote_dict(company),
            "access_input": {
                "user_id": context.user_id,
                "is_admin": context.is_admin,
                "tier": context.tier,
            },
        }
        group = _business_group_dict(company)
        if group is not None:
            payload["business_group"] = group

        if any(
            allowed_section in allowed
            for allowed_section in (
                _AGGREGATE_SECTION_KEYS["research"],
                _AGGREGATE_SECTION_KEYS["management"],
                _AGGREGATE_SECTION_KEYS["governance"],
            )
        ):
            revision = ResearchQueryService._current_research_revision(
                company_id
            )
        else:
            revision = None

        if "research" in allowed:
            payload["research"] = (
                None if revision is None else _research_dict(revision)
            )
            payload["management"] = (
                None if revision is None else _management_dict(revision)
            )
        if "governance" in allowed:
            flags = ResearchQueryService._active_governance_flags(
                company_id
            )
            payload["governance"] = {
                "status": (
                    None
                    if revision is None
                    else revision.governance_status
                ),
                "flags": [_governance_flag_dict(flag) for flag in flags],
            }
        if "ownership" in allowed:
            snapshot = ResearchQueryService._latest_ownership_snapshot(
                company_id
            )
            payload["ownership"] = (
                None
                if snapshot is None
                else _ownership_dict(snapshot)
            )
        if "market_plan" in allowed:
            market_plan = ResearchQueryService._current_market_plan(
                company_id
            )
            payload["market_plan"] = (
                None
                if market_plan is None
                else _market_plan_dict(market_plan)
            )
        if "forecast" in allowed:
            forecast = ResearchQueryService._current_forecast(company_id)
            payload["forecast"] = (
                None if forecast is None else _forecast_dict(forecast)
            )
        if _AGGREGATE_SECTION_KEYS["valuations"] in allowed:
            valuations = ResearchQueryService._current_valuations(
                company_id
            )
            payload["valuations"] = [
                _valuation_dict(valuation) for valuation in valuations
            ]

        return payload

    @staticmethod
    def _current_research_revision(
        company_id: str,
    ) -> ResearchRevision | None:
        current_number = (
            sa.select(sa.func.max(ResearchRevision.revision_number))
            .where(ResearchRevision.company_id == company_id)
            .scalar_subquery()
        )
        return db.session.scalar(
            sa.select(ResearchRevision)
            .where(
                ResearchRevision.company_id == company_id,
                ResearchRevision.revision_number == current_number,
            )
            .options(selectinload(ResearchRevision.points))
        )

    @staticmethod
    def _latest_ownership_snapshot(
        company_id: str,
    ) -> OwnershipSnapshot | None:
        return db.session.scalar(
            sa.select(OwnershipSnapshot)
            .where(OwnershipSnapshot.company_id == company_id)
            .order_by(
                OwnershipSnapshot.as_of_date.desc(),
                OwnershipSnapshot.id.desc(),
            )
            .limit(1)
        )

    @staticmethod
    def _active_governance_flags(company_id: str) -> list[GovernanceFlag]:
        return list(
            db.session.scalars(
                sa.select(GovernanceFlag)
                .where(
                    GovernanceFlag.company_id == company_id,
                    GovernanceFlag.archived_at.is_(None),
                )
                .order_by(GovernanceFlag.created_at, GovernanceFlag.id)
            ).all()
        )

    @staticmethod
    def _current_market_plan(
        company_id: str,
    ) -> MarketPlanRevision | None:
        return _current_revision_row(MarketPlanRevision, company_id)

    @staticmethod
    def _current_forecast(company_id: str) -> ForecastRevision | None:
        current_number = (
            sa.select(sa.func.max(ForecastRevision.revision_number))
            .where(ForecastRevision.company_id == company_id)
            .scalar_subquery()
        )
        return db.session.scalar(
            sa.select(ForecastRevision)
            .where(
                ForecastRevision.company_id == company_id,
                ForecastRevision.revision_number == current_number,
            )
            .options(selectinload(ForecastRevision.lines))
        )

    @staticmethod
    def _current_valuations(
        company_id: str,
    ) -> list[ValuationRevision]:
        current_numbers = sa.select(
            ValuationRevision.company_id,
            ValuationRevision.valuation_method,
            sa.func.max(ValuationRevision.revision_number).label(
                "current_number"
            ),
        ).group_by(
            ValuationRevision.company_id,
            ValuationRevision.valuation_method,
        )
        current_subquery = current_numbers.subquery()
        return list(
            db.session.scalars(
                sa.select(ValuationRevision)
                .join(
                    current_subquery,
                    sa.and_(
                        ValuationRevision.company_id
                        == current_subquery.c.company_id,
                        ValuationRevision.valuation_method
                        == current_subquery.c.valuation_method,
                        ValuationRevision.revision_number
                        == current_subquery.c.current_number,
                    ),
                )
                .where(ValuationRevision.company_id == company_id)
                .options(
                    selectinload(ValuationRevision.reference_lines)
                )
                .order_by(
                    ValuationRevision.valuation_method,
                    ValuationRevision.id,
                )
            ).all()
        )


_RevisionRow = TypeVar("_RevisionRow")


def _current_revision_row(
    model: type[_RevisionRow],
    company_id: str,
) -> _RevisionRow | None:
    """Current market-plan row by highest immutable revision number."""

    current_number = (
        sa.select(sa.func.max(model.revision_number))
        .where(model.company_id == company_id)
        .scalar_subquery()
    )
    return db.session.scalar(
        sa.select(model).where(
            model.company_id == company_id,
            model.revision_number == current_number,
        )
    )


def _research_dict(revision: ResearchRevision) -> dict[str, object]:
    return {
        "revision": revision.revision_number,
        "why_selected": revision.why_selected,
        "what_is_changing": revision.what_is_changing,
        "business_journey": revision.business_journey,
        "thesis": revision.thesis,
        "catalysts": [
            _point_dict(point)
            for point in revision.points
            if point.kind == ResearchPointKind.CATALYST
        ],
        "risks": [
            _point_dict(point)
            for point in revision.points
            if point.kind == ResearchPointKind.RISK
        ],
        "thesis_invalidation": revision.thesis_invalidation,
    }


def _point_dict(point: ResearchPoint) -> dict[str, object]:
    return {
        "id": point.id,
        "kind": point.kind,
        "title": point.title,
        "detail": point.detail,
        "status": point.status,
        "target_date": point.target_date,
        "sort_order": point.sort_order,
    }


def _management_dict(revision: ResearchRevision) -> dict[str, object]:
    return {
        "summary": revision.management_summary,
        "quality": revision.management_quality,
        "rationale": revision.management_rationale,
        "evidence": revision.management_evidence,
    }


def _governance_flag_dict(flag: GovernanceFlag) -> dict[str, object]:
    return {
        "id": flag.id,
        "flag_type": flag.flag_type,
        "title": flag.title,
        "severity": flag.severity,
        "status": flag.status,
        "factual_evidence": flag.factual_evidence,
        "source_title": flag.source_title,
        "source_url_or_reference": flag.source_url_or_reference,
        "interpretation": flag.interpretation,
        "observed_on": flag.observed_on,
        "resolved_on": flag.resolved_on,
    }


def _ownership_dict(snapshot: OwnershipSnapshot) -> dict[str, object]:
    return {
        "as_of_date": snapshot.as_of_date,
        "promoter_holding_pct": snapshot.promoter_holding_pct,
        "promoter_pledge_pct": snapshot.promoter_pledge_pct,
        "notes": snapshot.notes,
        "source_reference": snapshot.source_reference,
    }


def _market_plan_dict(
    revision: MarketPlanRevision,
) -> dict[str, object]:
    return {
        "revision": revision.revision_number,
        "currency": revision.currency,
        "accumulation_low": revision.accumulation_low,
        "accumulation_high": revision.accumulation_high,
        "preferred_accumulation_price": (
            revision.preferred_accumulation_price
        ),
        "supply_low": revision.supply_low,
        "supply_high": revision.supply_high,
        "invalidation_level": revision.invalidation_level,
        "rationale": revision.rationale,
        "effective_at": _as_utc(revision.effective_at),
    }


def _forecast_dict(revision: ForecastRevision) -> dict[str, object]:
    return {
        "revision": revision.revision_number,
        "as_of_date": revision.as_of_date,
        "assumptions": revision.assumptions,
        "lines": [
            {
                "fiscal_year": line.fiscal_year,
                "is_estimate": line.is_estimate,
                "revenue": line.revenue,
                "ebitda": line.ebitda,
                "pat": line.pat,
                "ebitda_margin_pct": line.ebitda_margin_pct,
                "eps": line.eps,
                "currency": line.currency,
                "unit": line.unit,
            }
            for line in revision.lines
        ],
    }


def _valuation_dict(revision: ValuationRevision) -> dict[str, object]:
    return {
        "revision": revision.revision_number,
        "valuation_method": revision.valuation_method,
        "justified_multiple": revision.justified_multiple,
        "implied_enterprise_value": revision.implied_enterprise_value,
        "net_debt": revision.net_debt,
        "other_equity_adjustment": revision.other_equity_adjustment,
        "implied_future_equity_value": (
            revision.implied_future_equity_value
        ),
        "required_return_pct": revision.required_return_pct,
        "discount_period_years": revision.discount_period_years,
        "present_value": revision.present_value,
        "current_market_cap": revision.current_market_cap,
        "currency": revision.currency,
        "unit": revision.unit,
        "valuation_notes": revision.valuation_notes,
        "as_of_date": revision.as_of_date,
        "reference_lines": [
            {
                "reference_forecast_revision_id": (
                    line.reference_forecast_revision_id
                ),
                "reference_fiscal_year": line.reference_fiscal_year,
                "reference_metric": line.reference_metric,
                "reference_metric_value": line.reference_metric_value,
                "reference_metric_unit": line.reference_metric_unit,
                "reference_metric_basis": line.reference_metric_basis,
                "sort_order": line.sort_order,
            }
            for line in revision.reference_lines
        ],
    }
