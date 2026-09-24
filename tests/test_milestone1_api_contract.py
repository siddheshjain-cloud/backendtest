"""Plan 5 Task 8: locked M1 API contract.

Snapshots the exact reviewed set of research/admin-research/admin-users
routes and their methods. A route silently added, removed, or changed here
is exactly the kind of drift this file exists to catch -- update this
snapshot deliberately, in the same change that adds/removes/renames a route,
never as an incidental side effect of something else.
"""

from __future__ import annotations


APPROVED_M1_ROUTES = {
    ("GET", "/api/research/companies"),
    ("GET", "/api/research/companies/<company_id>"),
    ("GET", "/api/research/companies/<company_id>/disclosures"),
    ("GET", "/api/research/companies/<company_id>/documents"),
    ("GET", "/api/research/companies/<company_id>/history"),
    ("GET", "/api/research/documents/<document_id>"),
    ("POST", "/api/admin/research/companies"),
    ("PATCH", "/api/admin/research/companies/<company_id>"),
    ("POST", "/api/admin/research/companies/<company_id>/disclosures"),
    (
        "POST",
        "/api/admin/research/companies/<company_id>/forecast-revisions",
    ),
    (
        "POST",
        "/api/admin/research/companies/<company_id>/governance-flags",
    ),
    (
        "POST",
        "/api/admin/research/companies/<company_id>/market-plan-revisions",
    ),
    (
        "POST",
        "/api/admin/research/companies/<company_id>/ownership-snapshots",
    ),
    (
        "POST",
        "/api/admin/research/companies/<company_id>/research-revisions",
    ),
    (
        "POST",
        "/api/admin/research/companies/<company_id>/valuation-revisions",
    ),
    ("PATCH", "/api/admin/research/disclosures/<disclosure_id>"),
    ("POST", "/api/admin/research/documents"),
    ("PATCH", "/api/admin/research/documents/<document_id>"),
    (
        "POST",
        "/api/admin/research/documents/<document_id>/company-links",
    ),
    ("PATCH", "/api/admin/research/governance-flags/<flag_id>"),
    ("POST", "/api/admin/research/institutions"),
    ("PATCH", "/api/admin/research/institutions/<institution_id>"),
    (
        "PUT",
        "/api/admin/users/<user_id>/entitlements/investment-research",
    ),
}

M1_URL_PREFIXES = (
    "/api/research",
    "/api/admin/research",
    "/api/admin/users",
)


def _actual_m1_routes(app) -> set[tuple[str, str]]:
    routes = set()
    for rule in app.url_map.iter_rules():
        rule_str = str(rule)
        if not rule_str.startswith(M1_URL_PREFIXES):
            continue
        for method in rule.methods:
            if method in ("HEAD", "OPTIONS"):
                continue
            routes.add((method, rule_str))
    return routes


def test_m1_route_surface_matches_the_approved_contract(app):
    actual = _actual_m1_routes(app)

    missing = APPROVED_M1_ROUTES - actual
    unexpected = actual - APPROVED_M1_ROUTES

    assert not missing, f"Approved routes missing from the app: {missing}"
    assert not unexpected, f"Undocumented routes registered: {unexpected}"
