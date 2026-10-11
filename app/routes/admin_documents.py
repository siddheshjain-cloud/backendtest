"""Plan 5 Task 6: administrative document and institution API blueprint."""

from flask import Blueprint, jsonify, request
from flask_jwt_extended import get_jwt_identity

from app.schemas.admin_documents import (
    DocumentCompanyLinkCreateSchema,
    DocumentResponseSchema,
    DocumentUpdateSchema,
)
from app.schemas.admin_research import (
    InstitutionCreateSchema,
    InstitutionPatchSchema,
    InstitutionResponseSchema,
    load_admin_payload,
)
from app.schemas.document import DocumentCreateSchema
from app.services.document_library_service import DocumentLibraryService
from app.utils.auth import admin_required
from app.utils.research_errors import research_error_boundary


admin_documents_bp = Blueprint("admin_documents", __name__)


def _actor_user_id() -> str:
    return get_jwt_identity()


@admin_documents_bp.route("/institutions", methods=["POST"])
@admin_required
@research_error_boundary
def create_institution():
    payload = load_admin_payload(
        InstitutionCreateSchema(), request.get_json(silent=True)
    )
    institution = DocumentLibraryService.create_institution(
        payload, _actor_user_id()
    )
    return jsonify(InstitutionResponseSchema().dump(institution)), 201


@admin_documents_bp.route(
    "/institutions/<institution_id>", methods=["PATCH"]
)
@admin_required
@research_error_boundary
def update_institution(institution_id: str):
    payload = load_admin_payload(
        InstitutionPatchSchema(), request.get_json(silent=True)
    )
    institution = DocumentLibraryService.update_institution(
        institution_id, payload, _actor_user_id()
    )
    return jsonify(InstitutionResponseSchema().dump(institution)), 200


@admin_documents_bp.route("/documents", methods=["POST"])
@admin_required
@research_error_boundary
def create_document():
    raw_payload = request.get_json(silent=True)
    load_admin_payload(DocumentCreateSchema(), raw_payload)
    document = DocumentLibraryService.create_document(
        raw_payload, _actor_user_id()
    )
    return jsonify(DocumentResponseSchema().dump(document)), 201


@admin_documents_bp.route(
    "/documents/<document_id>", methods=["PATCH"]
)
@admin_required
@research_error_boundary
def update_document(document_id: str):
    raw_payload = request.get_json(silent=True)
    load_admin_payload(DocumentUpdateSchema(), raw_payload)
    document = DocumentLibraryService.update_document(
        document_id,
        raw_payload["changes"],
        _actor_user_id(),
        raw_payload.get("reason"),
    )
    return jsonify(DocumentResponseSchema().dump(document)), 200


@admin_documents_bp.route(
    "/documents/<document_id>/company-links", methods=["POST"]
)
@admin_required
@research_error_boundary
def add_document_company_link(document_id: str):
    raw_payload = request.get_json(silent=True)
    payload = load_admin_payload(
        DocumentCompanyLinkCreateSchema(), raw_payload
    )
    document = DocumentLibraryService.add_company_link(
        document_id,
        payload["company_id"],
        payload["is_primary"],
        _actor_user_id(),
        payload["reason"],
    )
    return jsonify(DocumentResponseSchema().dump(document)), 201
