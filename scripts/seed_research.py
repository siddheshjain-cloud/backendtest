"""Plan 5 Task 7 CLI: run the IKIO research seed.

Usage:
    python scripts/seed_research.py --payload seed_data/ikio_research.json --actor-user-id <admin-user-id>
    python scripts/seed_research.py --payload seed_data/ikio_research.json --actor-user-id <admin-user-id> --dry-run
"""

import argparse
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from app import create_app  # noqa: E402
from app.services.research_seed_service import seed_ikio  # noqa: E402
from app.utils.research_errors import (  # noqa: E402
    ResearchForbiddenError,
    ResearchNotFoundError,
    ResearchValidationError,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--payload",
        type=Path,
        default=Path("seed_data/ikio_research.json"),
        help="Path to the seed payload JSON (default: seed_data/ikio_research.json)",
    )
    parser.add_argument(
        "--actor-user-id",
        required=True,
        help="ID of the existing administrator running this seed",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate preconditions without writing anything",
    )
    args = parser.parse_args()

    app = create_app()
    with app.app_context():
        try:
            outcome = seed_ikio(
                args.payload,
                args.actor_user_id,
                dry_run=args.dry_run,
            )
        except ResearchValidationError as error:
            print(
                f"SEED FAILED: validation_error: {error.details}",
                file=sys.stderr,
            )
            return 1
        except (ResearchForbiddenError, ResearchNotFoundError) as error:
            print(f"SEED FAILED: {error.code}: {error}", file=sys.stderr)
            return 1

        if outcome.dry_run:
            print("DRY RUN OK: preconditions satisfied, nothing was written.")
        else:
            print(
                "SEED OK: company_id="
                f"{outcome.company_id} "
                "quarterly_results_document_id="
                f"{outcome.quarterly_results_document_id}"
            )
        return 0


if __name__ == "__main__":
    sys.exit(main())
