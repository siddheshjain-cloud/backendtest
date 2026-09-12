"""P5 Task 2: research blueprint registration and error mapping.

These tests pin the integration scaffolding only: the five blueprints must be
registered under the required prefixes, and the research domain errors must
survive the administrative decorator stack as the stable payload
``{"error": code, "message": message, "details": details}``.
"""

import pytest
from flask import Blueprint, Flask

from app import create_app, db
from app.models.user import User
from app.routes.admin_documents import admin_documents_bp
from app.routes.admin_research import admin_entitlements_bp, admin_research_bp
from app.routes.documents import documents_bp
from app.routes.research import research_bp
from app.utils.auth import admin_required
from app.utils.research_errors import (
    ResearchConflictError,
    ResearchForbiddenError,
    ResearchNotFoundError,
    ResearchValidationError,
    research_error_boundary,
)
from config import Config


BLUEPRINTS_BY_NAME = {
    "research": research_bp,
    "admin_research": admin_research_bp,
    "admin_entitlements": admin_entitlements_bp,
    "documents": documents_bp,
    "admin_documents": admin_documents_bp,
}

EXPECTED_PREFIXES = {
    "research": "/api/research",
    "documents": "/api/research",
    "admin_research": "/api/admin/research",
    "admin_documents": "/api/admin/research",
    "admin_entitlements": "/api/admin/users",
}


class _PrefixProbeConfig(Config):
    TESTING = True
    JWT_SECRET_KEY = "test-jwt-secret"
    ELASTICSEARCH_URL = None
    SQLALCHEMY_DATABASE_URI = "sqlite://"


def test_research_blueprints_are_registered(app):
    for name, blueprint in BLUEPRINTS_BY_NAME.items():
        assert app.blueprints.get(name) is blueprint


def test_create_app_registers_each_blueprint_with_its_required_prefix(monkeypatch):
    recorded: list[tuple[str, str | None]] = []
    original_register_blueprint = Flask.register_blueprint

    def spying_register_blueprint(self, blueprint, **options):
        recorded.append((blueprint.name, options.get("url_prefix")))
        return original_register_blueprint(self, blueprint, **options)

    monkeypatch.setattr(Flask, "register_blueprint", spying_register_blueprint)
    create_app(_PrefixProbeConfig)

    registered_prefixes = dict(recorded)
    for name, expected_prefix in EXPECTED_PREFIXES.items():
        assert registered_prefixes[name] == expected_prefix


@pytest.fixture
def probe_client(app):
    """A test-only blueprint that exercises the real error boundary."""

    blueprint = Blueprint("research_error_probe", __name__)

    @blueprint.route("/validation")
    @research_error_boundary
    def probe_validation_error():
        raise ResearchValidationError({"reference_metric": ["Must be an uppercase slug"]})

    @blueprint.route("/conflict")
    @research_error_boundary
    def probe_conflict_error():
        raise ResearchConflictError("revision_conflict", "Research revision changed")

    @blueprint.route("/missing")
    @research_error_boundary
    def probe_not_found_error():
        raise ResearchNotFoundError("company_not_found", "Company was not found")

    @blueprint.route("/forbidden")
    @research_error_boundary
    def probe_forbidden_error():
        raise ResearchForbiddenError("research_forbidden", "Research access is forbidden")

    @blueprint.route("/unexpected")
    @research_error_boundary
    def probe_unexpected_error():
        db.session.add(User(name="Rollback Probe", email="rollback-probe@example.com"))
        db.session.flush()
        raise RuntimeError("sensitive internal detail")

    @blueprint.route("/unbound/conflict")
    def probe_unbound_conflict_error():
        raise ResearchConflictError("revision_conflict", "Research revision changed")

    @blueprint.route("/admin/conflict")
    @admin_required
    @research_error_boundary
    def probe_admin_conflict_error():
        raise ResearchConflictError("revision_conflict", "Research revision changed")

    app.register_blueprint(blueprint, url_prefix="/api/__research_probe")
    return app.test_client()


def test_validation_error_maps_to_stable_400_payload(probe_client):
    response = probe_client.get("/api/__research_probe/validation")

    assert response.status_code == 400
    assert response.get_json() == {
        "error": "validation_error",
        "message": "Request validation failed",
        "details": {"reference_metric": ["Must be an uppercase slug"]},
    }


def test_conflict_error_maps_to_stable_409_payload(probe_client):
    response = probe_client.get("/api/__research_probe/conflict")

    assert response.status_code == 409
    assert response.get_json() == {
        "error": "revision_conflict",
        "message": "Research revision changed",
        "details": {},
    }


def test_not_found_error_maps_to_stable_404_payload(probe_client):
    response = probe_client.get("/api/__research_probe/missing")

    assert response.status_code == 404
    assert response.get_json() == {
        "error": "company_not_found",
        "message": "Company was not found",
        "details": {},
    }


def test_forbidden_error_maps_to_stable_403_payload(probe_client):
    response = probe_client.get("/api/__research_probe/forbidden")

    assert response.status_code == 403
    assert response.get_json() == {
        "error": "research_forbidden",
        "message": "Research access is forbidden",
        "details": {},
    }


def test_registered_error_handler_maps_domain_error_without_boundary(probe_client):
    response = probe_client.get("/api/__research_probe/unbound/conflict")

    assert response.status_code == 409
    assert response.get_json() == {
        "error": "revision_conflict",
        "message": "Research revision changed",
        "details": {},
    }


def test_unexpected_error_hides_exception_text_and_rolls_back(probe_client, app):
    response = probe_client.get("/api/__research_probe/unexpected")

    assert response.status_code == 500
    assert response.get_json() == {
        "error": "internal_error",
        "message": "Internal server error",
        "details": {},
    }
    assert "sensitive internal detail" not in response.get_data(as_text=True)

    with app.app_context():
        assert (
            db.session.query(User)
            .filter_by(email="rollback-probe@example.com")
            .first()
            is None
        )


def test_admin_required_above_boundary_keeps_domain_conflict_as_409(
    probe_client, admin_user, auth_headers
):
    response = probe_client.get(
        "/api/__research_probe/admin/conflict",
        headers=auth_headers(admin_user),
    )

    assert response.status_code == 409
    assert response.get_json() == {
        "error": "revision_conflict",
        "message": "Research revision changed",
        "details": {},
    }


def test_admin_required_above_boundary_keeps_missing_token_as_401(probe_client):
    response = probe_client.get("/api/__research_probe/admin/conflict")

    assert response.status_code == 401
    assert response.get_json() == {"error": "Token is invalid"}


def test_admin_required_above_boundary_keeps_invalid_token_as_401(probe_client):
    response = probe_client.get(
        "/api/__research_probe/admin/conflict",
        headers={"Authorization": "Bearer not-a-valid-token"},
    )

    assert response.status_code == 401
    assert response.get_json() == {"error": "Token is invalid"}


def test_admin_required_above_boundary_keeps_non_admin_as_403(
    probe_client, free_user, auth_headers
):
    response = probe_client.get(
        "/api/__research_probe/admin/conflict",
        headers=auth_headers(free_user),
    )

    assert response.status_code == 403
    assert response.get_json() == {"error": "Admin access required"}


def test_legacy_not_found_payload_is_unchanged(probe_client):
    response = probe_client.get("/api/__research_probe/unknown-location")

    assert response.status_code == 404
    assert response.get_json() == {"error": "Resource not found"}
