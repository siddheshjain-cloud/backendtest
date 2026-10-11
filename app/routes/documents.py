"""Plan 5 Task 6: entitled consumer document read API blueprint."""

from flask import Blueprint, g, jsonify, request

from app.schemas.document_consumer import (
    ConsumerDocumentSchema,
    DocumentListQuerySchema,
    DocumentPageSchema,
    load_consumer_query,
)
from app.services.document_library_service import DocumentLibraryService
from app.services.entitlement_service import EntitlementService
from app.utils.auth import consumer_required
from app.utils.research_errors import research_error_boundary


documents_bp = Blueprint("documents", __name__)


def _access_context():
    """Resolve document access from the authenticated consumer user only."""

    return EntitlementService.resolve(g.current_user)


@documents_bp.route(
    "/companies/<company_id>/documents", methods=["GET"]
)
@consumer_required
@research_error_boundary
def list_company_documents(company_id: str):
    query = load_consumer_query(DocumentListQuerySchema(), request.args)
    context = _access_context()
    result = DocumentLibraryService.list_company_documents(
        company_id,
        {
            "document_type": query["document_type"],
            "institution_id": query["institution_id"],
            "report_type": query["report_type"],
            "date_from": query["date_from"],
            "date_to": query["date_to"],
        },
        query["page"],
        query["per_page"],
        context,
    )
    return jsonify(DocumentPageSchema().dump(result)), 200


@documents_bp.route("/documents/<document_id>", methods=["GET"])
@consumer_required
@research_error_boundary
def get_document(document_id: str):
    context = _access_context()
    projection = DocumentLibraryService.get_document(
        document_id, context
    )
    return jsonify(ConsumerDocumentSchema().dump(projection)), 200
