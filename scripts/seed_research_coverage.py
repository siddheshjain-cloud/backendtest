"""Research Coverage & Fact Intelligence, Slice 1: seed the 11
``ResearchDimension`` rows and the per-document-type ``CoverageProfile``/
``CoverageProfileDimension`` contracts.

Idempotent, same spirit as ``scripts/seed_research.py``: every dimension is
checked for an existing row by ``code`` before insert, and every profile is
checked via ``ResearchCoverageService.get_active_profile`` before insert, so
re-running this script against an already-seeded database is a no-op.

Usage:
    python scripts/seed_research_coverage.py --actor-user-id <admin-user-id>
    python scripts/seed_research_coverage.py --actor-user-id <admin-user-id> --dry-run
"""

from __future__ import annotations

import argparse
import sys
from datetime import date

from dotenv import load_dotenv

load_dotenv()

import sqlalchemy as sa  # noqa: E402

from app import create_app, db  # noqa: E402
from app.models.research_coverage import ResearchDimension  # noqa: E402
from app.services.research_coverage_service import (  # noqa: E402
    ResearchCoverageService,
)


DIMENSIONS: list[tuple[str, str, str]] = [
    (
        "GOVERNANCE_RPT",
        "Governance & Related-Party Transactions",
        "Related-party transaction disclosures, KMP/promoter remuneration "
        "ratios, RPT amounts and counterparties.",
    ),
    (
        "AUDITOR_CONTROLS",
        "Auditor & Internal Controls",
        "Auditor appointments/resignations, audit-trail/internal-control "
        "notes, CARO qualifications.",
    ),
    (
        "CORPORATE_STRUCTURE_MA",
        "Corporate Structure & M&A",
        "Acquisitions, subsidiary/JV formation or liquidation, mergers, "
        "schemes of arrangement, corporate restructuring.",
    ),
    (
        "SEGMENT_MIX",
        "Business Segment Mix",
        "Business/revenue segment composition and mix disclosures.",
    ),
    (
        "CAPACITY_CAPEX",
        "Capacity & Capex",
        "Capacity expansion, capex plans and spend, plant commissioning "
        "and utilization.",
    ),
    (
        "MANAGEMENT_GUIDANCE",
        "Management Guidance",
        "Forward-looking guidance, margin/growth targets, management "
        "outlook commentary.",
    ),
    (
        "JV_EXECUTION_RISK",
        "JV & Execution Risk",
        "Joint-venture status, execution delays, regulatory or partner "
        "risk to committed plans.",
    ),
    (
        "CREDIT_DEBT",
        "Credit & Debt",
        "Credit ratings, borrowings, debt instruments (NCD/CP), loan "
        "terms and counterparties.",
    ),
    (
        "OWNERSHIP_SHAREHOLDING",
        "Ownership & Shareholding",
        "Promoter/institutional shareholding, pledge status, ownership "
        "changes.",
    ),
    (
        "LITIGATION_REGULATORY",
        "Litigation & Regulatory",
        "Litigation, tax disputes, regulatory penalties/fines and their "
        "resolution.",
    ),
    (
        "COMPUTED_METRIC_OPPORTUNITY",
        "Computed Metric Opportunity",
        "Raw financial line items present that could be combined into a "
        "useful metric not yet stated or computed.",
    ),
]


# document_type_code -> (required dimension codes, optional dimension codes)
PROFILES: dict[str, tuple[list[str], list[str]]] = {
    "ANNUAL_REPORT": (
        [
            "GOVERNANCE_RPT",
            "AUDITOR_CONTROLS",
            "CORPORATE_STRUCTURE_MA",
            "OWNERSHIP_SHAREHOLDING",
            "LITIGATION_REGULATORY",
            "CREDIT_DEBT",
            "COMPUTED_METRIC_OPPORTUNITY",
            "JV_EXECUTION_RISK",
        ],
        ["SEGMENT_MIX", "CAPACITY_CAPEX", "MANAGEMENT_GUIDANCE"],
    ),
    "QUARTERLY_RESULTS": (
        ["COMPUTED_METRIC_OPPORTUNITY", "CORPORATE_STRUCTURE_MA"],
        ["SEGMENT_MIX", "CAPACITY_CAPEX", "GOVERNANCE_RPT"],
    ),
    "INVESTOR_PRESENTATION": (
        ["SEGMENT_MIX", "CAPACITY_CAPEX"],
        ["MANAGEMENT_GUIDANCE", "CREDIT_DEBT"],
    ),
    "CONCALL": (
        ["MANAGEMENT_GUIDANCE", "JV_EXECUTION_RISK"],
        ["CAPACITY_CAPEX", "CORPORATE_STRUCTURE_MA", "SEGMENT_MIX"],
    ),
    "REG30_ATTACHMENT": (
        ["CORPORATE_STRUCTURE_MA", "CAPACITY_CAPEX", "LITIGATION_REGULATORY"],
        ["CREDIT_DEBT", "OWNERSHIP_SHAREHOLDING", "GOVERNANCE_RPT"],
    ),
    "CREDIT_RATING_REPORT": (
        ["CREDIT_DEBT"],
        ["GOVERNANCE_RPT", "OWNERSHIP_SHAREHOLDING"],
    ),
    "DRHP": (
        [
            "CORPORATE_STRUCTURE_MA",
            "SEGMENT_MIX",
            "CAPACITY_CAPEX",
            "GOVERNANCE_RPT",
            "OWNERSHIP_SHAREHOLDING",
            "LITIGATION_REGULATORY",
            "CREDIT_DEBT",
            "COMPUTED_METRIC_OPPORTUNITY",
        ],
        ["MANAGEMENT_GUIDANCE", "AUDITOR_CONTROLS", "JV_EXECUTION_RISK"],
    ),
    "RHP": (
        [
            "CORPORATE_STRUCTURE_MA",
            "SEGMENT_MIX",
            "CAPACITY_CAPEX",
            "GOVERNANCE_RPT",
            "OWNERSHIP_SHAREHOLDING",
            "LITIGATION_REGULATORY",
            "CREDIT_DEBT",
            "COMPUTED_METRIC_OPPORTUNITY",
        ],
        ["MANAGEMENT_GUIDANCE", "AUDITOR_CONTROLS", "JV_EXECUTION_RISK"],
    ),
    "LETTER_OF_OFFER": (
        [
            "CAPACITY_CAPEX",
            "CREDIT_DEBT",
            "LITIGATION_REGULATORY",
            "OWNERSHIP_SHAREHOLDING",
        ],
        [],
    ),
    "MERGER_SCHEME_DOCUMENT": (
        [
            "CORPORATE_STRUCTURE_MA",
            "OWNERSHIP_SHAREHOLDING",
            "CREDIT_DEBT",
            "LITIGATION_REGULATORY",
            "GOVERNANCE_RPT",
        ],
        [],
    ),
    "SHAREHOLDING_FILING": (
        ["OWNERSHIP_SHAREHOLDING"],
        [],
    ),
}


def seed_dimensions(dry_run: bool) -> dict[str, str]:
    """Insert any missing dimension; return code -> id for all 11."""

    code_to_id: dict[str, str] = {}
    for code, name, description in DIMENSIONS:
        existing = db.session.scalars(
            sa.select(ResearchDimension).where(ResearchDimension.code == code)
        ).first()
        if existing is not None:
            code_to_id[code] = existing.id
            continue

        print(f"  dimension {code}: {'would create' if dry_run else 'creating'}")
        if dry_run:
            continue
        dimension = ResearchCoverageService.create_dimension(
            code=code, name=name, description=description
        )
        code_to_id[code] = dimension.id
    return code_to_id


def seed_profiles(
    dry_run: bool, actor_user_id: str, code_to_id: dict[str, str]
) -> None:
    for document_type_code, (required_codes, optional_codes) in PROFILES.items():
        existing_profile = ResearchCoverageService.get_active_profile(
            document_type_code
        )
        if existing_profile is not None:
            print(f"  profile {document_type_code}: already seeded, skipping")
            continue

        print(
            f"  profile {document_type_code}: "
            f"{'would create' if dry_run else 'creating'} "
            f"({len(required_codes)} required, {len(optional_codes)} optional)"
        )
        if dry_run:
            continue

        dimension_requirements: list[tuple[str, bool, str | None]] = []
        for code in required_codes:
            dimension_requirements.append((code_to_id[code], True, None))
        for code in optional_codes:
            dimension_requirements.append((code_to_id[code], False, None))

        ResearchCoverageService.create_coverage_profile(
            document_type_code=document_type_code,
            dimension_requirements=dimension_requirements,
            effective_from=date.today(),
            created_by_user_id=actor_user_id,
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--actor-user-id",
        required=True,
        help="ID of the existing administrator running this seed",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would be created without writing anything",
    )
    args = parser.parse_args()

    app = create_app(register_research=True)
    with app.app_context():
        print("Seeding ResearchDimension rows...")
        code_to_id = seed_dimensions(args.dry_run)

        if args.dry_run:
            # In dry-run mode no ids exist yet; resolve any already-existing
            # ones so profile seeding can still report sensibly.
            for code, _, _ in DIMENSIONS:
                code_to_id.setdefault(code, "")

        print("Seeding CoverageProfile/CoverageProfileDimension rows...")
        seed_profiles(args.dry_run, args.actor_user_id, code_to_id)

        if args.dry_run:
            print("DRY RUN OK: nothing was written.")
        else:
            print("SEED OK.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
