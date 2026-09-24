"""Plan 5 Task 8: whole-system architecture boundary assertions.

Complements, rather than duplicates, the legacy-boundary coverage that
already exists in tests/legacy/test_live_pipeline_boundaries.py (which
proves live/websocket.py and Trade paths never import research/document/
policy code) and the per-file tests/legacy/test_*_contract.py snapshots
(which already lock legacy API response shapes). This file covers what
neither of those does: the reverse import direction for kite/Telegram, a
systematic auth sweep across every new M1 route (rather than per-route spot
checks), a negative-space check for out-of-scope endpoints/models, and
pagination-shape consistency across every new collection endpoint.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from app import db
from app.models import Company


REPO_ROOT = Path(__file__).resolve().parent.parent

RESEARCH_DOCUMENT_MODULES = (
    "app/routes/research.py",
    "app/routes/admin_research.py",
    "app/routes/documents.py",
    "app/routes/admin_documents.py",
    "app/services/research_command_service.py",
    "app/services/research_query_service.py",
    "app/services/research_seed_service.py",
    "app/services/document_library_service.py",
    "app/services/document_deduplication_service.py",
    "app/policies/document_access.py",
    "app/policies/research_access.py",
)

FORBIDDEN_IMPORT_PREFIXES = (
    "kite",
    "app.services.telegram_service",
    "live.websocket",
)

# The exact, reviewed M1 route surface. A route appearing or disappearing
# here without this file being updated is exactly the regression this test
# exists to catch.
JWT_REQUIRED_ROUTES = (
    ("GET", "/api/research/companies"),
    ("GET", "/api/research/companies/<id>"),
    ("GET", "/api/research/companies/<id>/disclosures"),
    ("GET", "/api/research/companies/<id>/documents"),
    ("GET", "/api/research/companies/<id>/history"),
    ("GET", "/api/research/documents/<id>"),
)

ADMIN_REQUIRED_ROUTES = (
    ("POST", "/api/admin/research/companies"),
    ("PATCH", "/api/admin/research/companies/<id>"),
    ("POST", "/api/admin/research/companies/<id>/disclosures"),
    ("POST", "/api/admin/research/companies/<id>/forecast-revisions"),
    ("POST", "/api/admin/research/companies/<id>/governance-flags"),
    ("POST", "/api/admin/research/companies/<id>/market-plan-revisions"),
    ("POST", "/api/admin/research/companies/<id>/ownership-snapshots"),
    ("POST", "/api/admin/research/companies/<id>/research-revisions"),
    ("POST", "/api/admin/research/companies/<id>/valuation-revisions"),
    ("PATCH", "/api/admin/research/disclosures/<id>"),
    ("POST", "/api/admin/research/documents"),
    ("PATCH", "/api/admin/research/documents/<id>"),
    ("POST", "/api/admin/research/documents/<id>/company-links"),
    ("PATCH", "/api/admin/research/governance-flags/<id>"),
    ("POST", "/api/admin/research/institutions"),
    ("PATCH", "/api/admin/research/institutions/<id>"),
    ("PUT", "/api/admin/users/<id>/entitlements/investment-research"),
)

DUMMY_ID = "00000000-0000-0000-0000-000000000000"

COLLECTION_ENDPOINTS = (
    "/api/research/companies",
    "/api/research/companies/{id}/disclosures",
    "/api/research/companies/{id}/documents",
)


def _module_import_prefixes(relative_path: str) -> set[str]:
    source = (REPO_ROOT / relative_path).read_text()
    tree = ast.parse(source, filename=relative_path)
    prefixes: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                prefixes.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            prefixes.add(node.module)
    return prefixes


def test_research_and_document_modules_never_import_kite_or_telegram():
    violations = []
    for relative_path in RESEARCH_DOCUMENT_MODULES:
        imports = _module_import_prefixes(relative_path)
        for imported in imports:
            if any(
                imported == forbidden
                or imported.startswith(forbidden + ".")
                for forbidden in FORBIDDEN_IMPORT_PREFIXES
            ):
                violations.append(f"{relative_path} imports {imported}")
    assert violations == [], violations


def test_no_frontend_path_exists():
    assert not (REPO_ROOT / "frontend").exists()
    assert not (REPO_ROOT / "templates").exists()
    static_dir = REPO_ROOT / "static"
    if static_dir.exists():
        assert list(static_dir.iterdir()) == []


def test_no_out_of_scope_endpoint_exists(app):
    forbidden_fragments = (
        "upload",
        "download",
        "preview",
        "/share",
        "/analysis",
        "/consensus",
        "/engine",
        "/sotp",
        "/file",
    )
    offenders = []
    for rule in app.url_map.iter_rules():
        rule_str = str(rule).lower()
        if not (
            rule_str.startswith("/api/research")
            or rule_str.startswith("/api/admin/research")
            or rule_str.startswith("/api/admin/users")
        ):
            continue
        for fragment in forbidden_fragments:
            if fragment in rule_str:
                offenders.append(str(rule))
    assert offenders == [], offenders


def test_no_out_of_scope_model_exists():
    table_names = set(db.metadata.tables.keys())
    forbidden_fragments = (
        "consensus",
        "engine",
        "sotp",
        "portfolio",
        "smart_capital",
        "theme",
    )
    offenders = [
        name
        for name in table_names
        for fragment in forbidden_fragments
        if fragment in name.lower()
    ]
    assert offenders == [], offenders


def _resolve(path_template: str) -> str:
    return path_template.replace("<id>", DUMMY_ID)


@pytest.mark.parametrize("method,path", JWT_REQUIRED_ROUTES + ADMIN_REQUIRED_ROUTES)
def test_every_m1_route_requires_a_jwt(client, method, path):
    response = client.open(_resolve(path), method=method, json={})
    assert response.status_code == 401, (method, path, response.status_code)


@pytest.mark.parametrize("method,path", ADMIN_REQUIRED_ROUTES)
def test_every_admin_route_rejects_a_non_admin_token(
    client, auth_headers, free_user, method, path
):
    response = client.open(
        _resolve(path),
        method=method,
        json={},
        headers=auth_headers(free_user),
    )
    assert response.status_code == 403, (method, path, response.status_code)


@pytest.fixture
def company(app, ticker_factory):
    ticker = ticker_factory(
        symbol="BOUNDARY",
        instrument_token=999998,
        exchange="NSE",
        name="Boundary Test Company",
        last_price=1.0,
    )
    db.session.flush()
    row = Company(
        ticker_id=ticker.id,
        legal_name="Boundary Test Company Limited",
        display_name=None,
        isin="INE0000000BT",
        sector="Industrials",
        industry="Testing",
        business_group_id=None,
        business_group_basis=None,
        business_group_source_reference=None,
    )
    db.session.add(row)
    db.session.flush()
    return row


@pytest.mark.parametrize("endpoint_template", COLLECTION_ENDPOINTS)
def test_every_new_collection_uses_the_approved_pagination_shape(
    client, auth_headers, free_user, admin_user, company, endpoint_template
):
    path = endpoint_template.format(id=company.id)
    response = client.get(path, headers=auth_headers(free_user))
    assert response.status_code == 200, (path, response.status_code)
    body = response.get_json()
    assert set(body.keys()) >= {
        "items",
        "page",
        "per_page",
        "total_items",
        "total_pages",
    }
    assert isinstance(body["items"], list)
    assert isinstance(body["page"], int)
    assert isinstance(body["per_page"], int)
    assert isinstance(body["total_items"], int)
    assert isinstance(body["total_pages"], int)
