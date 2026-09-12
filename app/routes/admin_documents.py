"""Administrative document and institution API blueprint.

Institution identity commands are exposed here because they are owned by the
common Document Library, not the research fact command service.
"""

from flask import Blueprint, jsonify, request
from flask_jwt_extended import get_jwt_identity

from app.schemas.admin_research import (
    InstitutionCreateSchema,
    InstitutionPatchSchema,
    InstitutionResponseSchema,
    load_admin_payload,
)
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
