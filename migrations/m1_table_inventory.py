"""Canonical Milestone 1 additive table inventory.

Shared by the ``20260904_02`` additive migration and its regression tests so
both sides pin to one explicit table list instead of the live SQLAlchemy
metadata, which silently grows as later, unrelated models are added to the
codebase.

Plan 5 Task 1 (docs/superpowers/plans/2026-09-04-investment-operating-system-
m1-api-seed-regression.md) froze 18 table names. ``document_content`` and
``document_storage_location`` are not in that list because they did not exist
when the plan was written: the later content-addressed storage refactor
introduced them, and this migration's own legacy-column migration step
(``_migrate_legacy_document_fields``) requires both to exist so it can move
``document.content_hash_sha256`` / ``storage_provider`` / ``storage_key`` data
into them before dropping the legacy columns. They are included here as an
explicit, approved superseding change to the frozen 18-table list -- the
Plan 4 content-addressed storage refactor they belong to was already
implemented and independently reviewed before Plan 5 Task 1's migration was
written -- not accidental scope drift.
"""

from __future__ import annotations


M1_TABLES: frozenset[str] = frozenset(
    {
        "business_group",
        "company",
        "user_entitlement",
        "research_revision",
        "research_point",
        "ownership_snapshot",
        "governance_flag",
        "company_disclosure",
        "market_plan_revision",
        "forecast_revision",
        "forecast_line",
        "valuation_revision",
        "valuation_reference_line",
        "institution",
        "document",
        "document_content",
        "document_storage_location",
        "document_company_link",
        "institutional_report_metadata",
        "document_audit_event",
    }
)
