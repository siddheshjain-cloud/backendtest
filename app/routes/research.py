"""Plan 5 Task 5: entitled consumer research read API blueprint."""

from flask import Blueprint, g, jsonify, request

from app.schemas.research_consumer import (
    CompanyListPageSchema,
    CompanyListQuerySchema,
    DisclosureListQuerySchema,
    DisclosurePageSchema,
    HISTORY_PAGE_SCHEMAS,
    HistoryQuerySchema,
    load_consumer_query,
)
from app.services.entitlement_service import EntitlementService
from app.services.research_presenter import ResearchPresenter
from app.services.research_query_service import ResearchQueryService
from app.utils.auth import consumer_required
from app.utils.research_errors import research_error_boundary


research_bp = Blueprint("research", __name__)


def _access_context():
    """Resolve research access from the authenticated consumer user only."""

    return EntitlementService.resolve(g.current_user)


@research_bp.route("/companies", methods=["GET"])
@consumer_required
@research_error_boundary
def list_research_companies():
    query = load_consumer_query(CompanyListQuerySchema(), request.args)
    context = _access_context()
    result = ResearchQueryService.list_companies(
        q=query["q"],
        sector=query["sector"],
        industry=query["industry"],
        page=query["page"],
        per_page=query["per_page"],
        context=context,
    )
    return jsonify(CompanyListPageSchema().dump(result)), 200


@research_bp.route("/companies/<company_id>", methods=["GET"])
@consumer_required
@research_error_boundary
def get_research_company(company_id: str):
    context = _access_context()
    aggregate = ResearchQueryService.get_company_detail(
        company_id, context
    )
    return jsonify(
        ResearchPresenter.company_detail(aggregate, context)
    ), 200


@research_bp.route(
    "/companies/<company_id>/disclosures",
    methods=["GET"],
)
@consumer_required
@research_error_boundary
def list_research_disclosures(company_id: str):
    query = load_consumer_query(
        DisclosureListQuerySchema(), request.args
    )
    context = _access_context()
    result = ResearchQueryService.list_disclosures(
        company_id,
        event_type=query["event_type"],
        is_key=query["is_key"],
        date_from=query["date_from"],
        date_to=query["date_to"],
        newest_first=query["newest_first"],
        page=query["page"],
        per_page=query["per_page"],
        context=context,
    )
    return jsonify(DisclosurePageSchema().dump(result)), 200


@research_bp.route(
    "/companies/<company_id>/history",
    methods=["GET"],
)
@consumer_required
@research_error_boundary
def list_research_history(company_id: str):
    query = load_consumer_query(HistoryQuerySchema(), request.args)
    context = _access_context()
    result = ResearchQueryService.get_history(
        company_id,
        section=query["section"],
        valuation_method=query["valuation_method"],
        page=query["page"],
        per_page=query["per_page"],
        context=context,
    )
    response_schema = HISTORY_PAGE_SCHEMAS[query["section"]]
    return jsonify(response_schema().dump(result)), 200
