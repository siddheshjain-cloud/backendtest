from decimal import Decimal

import pytest

from app.utils.research_errors import (
    ResearchConflictError,
    ResearchForbiddenError,
    ResearchNotFoundError,
    ResearchValidationError,
)
from app.utils.research_validation import validate_percentage, validate_upper_slug


def test_upper_slug_accepts_an_extensible_metric_slug():
    assert validate_upper_slug("EBITDA_PER_TON", "reference_metric") == "EBITDA_PER_TON"


@pytest.mark.parametrize(
    "value",
    ["ebitda", " EBITDA", "EBITDA ", "EBITDA-PER-TON", "", "A" * 65],
)
def test_upper_slug_rejects_invalid_values_with_field_keyed_details(value):
    with pytest.raises(ResearchValidationError) as exc_info:
        validate_upper_slug(value, "reference_metric")

    assert exc_info.value.code == "validation_error"
    assert exc_info.value.details == {"reference_metric": ["Must be an uppercase slug of at most 64 characters"]}


@pytest.mark.parametrize("value", [None, Decimal("0"), Decimal("42.125"), Decimal("100")])
def test_percentage_accepts_none_and_finite_decimal_values_in_the_closed_range(value):
    assert validate_percentage(value, "promoter_holding_pct") is None


@pytest.mark.parametrize(
    "value",
    [Decimal("-0.0001"), Decimal("100.0001"), Decimal("NaN"), Decimal("Infinity")],
)
def test_percentage_rejects_out_of_range_or_non_finite_decimals(value):
    with pytest.raises(ResearchValidationError) as exc_info:
        validate_percentage(value, "promoter_holding_pct")

    assert exc_info.value.details == {"promoter_holding_pct": ["Must be a finite decimal from 0 through 100"]}


def test_percentage_rejects_float_to_preserve_decimal_semantics():
    with pytest.raises(ResearchValidationError) as exc_info:
        validate_percentage(12.5, "promoter_holding_pct")

    assert exc_info.value.details == {"promoter_holding_pct": ["Must be a finite decimal from 0 through 100"]}


def test_validation_error_has_stable_message_code_and_details():
    error = ResearchValidationError({"field": ["is invalid"]})

    assert str(error) == "Request validation failed"
    assert error.code == "validation_error"
    assert error.details == {"field": ["is invalid"]}


@pytest.mark.parametrize(
    ("error_type", "code", "message"),
    [
        (ResearchConflictError, "revision_conflict", "Research revision changed"),
        (ResearchNotFoundError, "company_not_found", "Company was not found"),
        (ResearchForbiddenError, "research_forbidden", "Research access is forbidden"),
    ],
)
def test_translatable_domain_errors_expose_only_code_and_message(error_type, code, message):
    error = error_type(code, message)

    assert str(error) == message
    assert error.code == code
    assert error.message == message
