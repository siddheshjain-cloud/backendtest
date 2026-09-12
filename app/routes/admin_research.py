"""Administrative research API blueprints.

``admin_research_bp`` owns the /api/admin/research routes and
``admin_entitlements_bp`` owns the /api/admin/users entitlement route.
"""

from flask import Blueprint, jsonify, request
from flask_jwt_extended import get_jwt_identity

from app.schemas.admin_research import (
    CompanyCreateSchema,
    CompanyPatchSchema,
    CompanyResponseSchema,
    DisclosureCreateSchema,
    DisclosurePatchSchema,
    DisclosureResponseSchema,
    EntitlementPutSchema,
    EntitlementResponseSchema,
    GovernanceFlagCreateSchema,
    GovernanceFlagPatchSchema,
    GovernanceFlagResponseSchema,
    OwnershipSnapshotCreateSchema,
    OwnershipSnapshotResponseSchema,
    load_admin_payload,
)
from app.services.research_command_service import ResearchCommandService
from app.utils.auth import admin_required
from app.utils.research_errors import research_error_boundary


admin_research_bp = Blueprint("admin_research", __name__)
admin_entitlements_bp = Blueprint("admin_entitlements", __name__)


def _actor_user_id() -> str:
    """Return the authenticated actor identity established by admin_required."""

    return get_jwt_identity()


def _request_payload(schema) -> dict:
    return load_admin_payload(schema, request.get_json(silent=True))


@admin_research_bp.route("/companies", methods=["POST"])
@admin_required
@research_error_boundary
def create_research_company():
    payload = _request_payload(CompanyCreateSchema())
    company = ResearchCommandService.create_company(
        payload, _actor_user_id()
    )
    return jsonify(CompanyResponseSchema().dump(company)), 201


@admin_research_bp.route("/companies/<company_id>", methods=["PATCH"])
@admin_required
@research_error_boundary
def update_research_company(company_id: str):
    payload = _request_payload(CompanyPatchSchema())
    company = ResearchCommandService.update_company(
        company_id, payload, _actor_user_id()
    )
    return jsonify(CompanyResponseSchema().dump(company)), 200


@admin_research_bp.route(
    "/companies/<company_id>/ownership-snapshots",
    methods=["POST"],
)
@admin_required
@research_error_boundary
def create_ownership_snapshot(company_id: str):
    payload = _request_payload(OwnershipSnapshotCreateSchema())
    snapshot = ResearchCommandService.add_ownership_snapshot(
        company_id, _actor_user_id(), payload
    )
    return jsonify(OwnershipSnapshotResponseSchema().dump(snapshot)), 201


@admin_research_bp.route(
    "/companies/<company_id>/governance-flags",
    methods=["POST"],
)
@admin_required
@research_error_boundary
def create_governance_flag(company_id: str):
    payload = _request_payload(GovernanceFlagCreateSchema())
    flag = ResearchCommandService.create_governance_flag(
        company_id, _actor_user_id(), payload
    )
    return jsonify(GovernanceFlagResponseSchema().dump(flag)), 201


@admin_research_bp.route(
    "/governance-flags/<flag_id>",
    methods=["PATCH"],
)
@admin_required
@research_error_boundary
def update_governance_flag(flag_id: str):
    payload = _request_payload(GovernanceFlagPatchSchema())
    flag = ResearchCommandService.update_governance_flag(
        flag_id, _actor_user_id(), payload
    )
    return jsonify(GovernanceFlagResponseSchema().dump(flag)), 200


@admin_research_bp.route(
    "/companies/<company_id>/disclosures",
    methods=["POST"],
)
@admin_required
@research_error_boundary
def create_company_disclosure(company_id: str):
    payload = _request_payload(DisclosureCreateSchema())
    disclosure = ResearchCommandService.create_disclosure(
        company_id, _actor_user_id(), payload
    )
    return jsonify(DisclosureResponseSchema().dump(disclosure)), 201


@admin_research_bp.route(
    "/disclosures/<disclosure_id>",
    methods=["PATCH"],
)
@admin_required
@research_error_boundary
def update_company_disclosure(disclosure_id: str):
    payload = _request_payload(DisclosurePatchSchema())
    disclosure = ResearchCommandService.update_disclosure(
        disclosure_id, _actor_user_id(), payload
    )
    return jsonify(DisclosureResponseSchema().dump(disclosure)), 200


@admin_entitlements_bp.route(
    "/<user_id>/entitlements/investment-research",
    methods=["PUT"],
)
@admin_required
@research_error_boundary
def upsert_research_entitlement(user_id: str):
    payload = _request_payload(EntitlementPutSchema())
    entitlement = ResearchCommandService.upsert_entitlement(
        user_id, payload, _actor_user_id()
    )
    return jsonify(EntitlementResponseSchema().dump(entitlement)), 200
