import re
from decimal import Decimal

from app.utils.research_errors import ResearchValidationError


UPPER_SLUG_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")


def validate_upper_slug(value: str, field: str) -> str:
    if not isinstance(value, str) or not UPPER_SLUG_PATTERN.fullmatch(value):
        raise ResearchValidationError(
            {field: ["Must be an uppercase slug of at most 64 characters"]}
        )
    return value


def validate_percentage(value: Decimal | None, field: str) -> None:
    if value is None:
        return
    if not isinstance(value, Decimal) or not value.is_finite() or not Decimal("0") <= value <= Decimal("100"):
        raise ResearchValidationError(
            {field: ["Must be a finite decimal from 0 through 100"]}
        )
