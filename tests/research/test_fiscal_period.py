"""Phase 3 Slice A: the FiscalPeriod grammar/resolver contract.

Pure unit tests -- app/services/fiscal_period.py has no DB dependency.
"""

from __future__ import annotations

from datetime import date

import pytest

from app.services.fiscal_period import (
    FiscalPeriod,
    FiscalPeriodError,
    FiscalPeriodType,
    resolve_period_label,
)


@pytest.mark.parametrize(
    "label,expected_year,expected_type",
    [
        ("FY2024", 2024, FiscalPeriodType.ANNUAL),
        ("FY2024Q1", 2024, FiscalPeriodType.Q1),
        ("FY2024Q4", 2024, FiscalPeriodType.Q4),
        ("FY2024H1", 2024, FiscalPeriodType.H1),
        ("FY2024H2", 2024, FiscalPeriodType.H2),
        ("FY2024TTM", 2024, FiscalPeriodType.TTM),
    ],
)
def test_parse_accepts_the_controlled_grammar(label, expected_year, expected_type):
    period = FiscalPeriod.parse(label)
    assert period.fiscal_year == expected_year
    assert period.period_type == expected_type
    assert period.label() == label


@pytest.mark.parametrize(
    "label",
    [
        "2024",
        "FY24",
        "FY2024Q5",
        "FY2024H3",
        "fy2024",
        "FY2024-Q1",
        "FY2024TTM ",
        "",
        "FY2024Q1Q2",
    ],
)
def test_parse_rejects_anything_outside_the_grammar(label):
    with pytest.raises(FiscalPeriodError):
        FiscalPeriod.parse(label)


def test_parse_rejects_non_string_input():
    with pytest.raises(FiscalPeriodError):
        FiscalPeriod.parse(None)  # type: ignore[arg-type]


def test_resolve_annual_default_march_fiscal_year_end():
    # India's convention, the one every company in this system uses today:
    # FY2024 = 2023-04-01 .. 2024-03-31.
    start, end = FiscalPeriod.parse("FY2024").resolve()
    assert start == date(2023, 4, 1)
    assert end == date(2024, 3, 31)


def test_resolve_quarters_tile_the_fiscal_year_with_no_gaps_or_overlap():
    fiscal_year = 2024
    quarters = [
        FiscalPeriod.parse(f"FY{fiscal_year}Q{q}").resolve() for q in range(1, 5)
    ]
    annual_start, annual_end = FiscalPeriod.parse(f"FY{fiscal_year}").resolve()

    assert quarters[0][0] == annual_start
    assert quarters[-1][1] == annual_end
    for (prev_start, prev_end), (next_start, _) in zip(quarters, quarters[1:]):
        assert prev_start < prev_end
        # no gap, no overlap: the next quarter starts the day after the
        # previous one ends
        assert (next_start - prev_end).days == 1


def test_resolve_halves_tile_the_fiscal_year():
    start_h1, end_h1 = FiscalPeriod.parse("FY2024H1").resolve()
    start_h2, end_h2 = FiscalPeriod.parse("FY2024H2").resolve()
    annual_start, annual_end = FiscalPeriod.parse("FY2024").resolve()

    assert start_h1 == annual_start
    assert end_h2 == annual_end
    assert (start_h2 - end_h1).days == 1


def test_resolve_ttm_matches_the_full_fiscal_year():
    annual = FiscalPeriod.parse("FY2024").resolve()
    ttm = FiscalPeriod.parse("FY2024TTM").resolve()
    assert annual == ttm


def test_resolve_december_fiscal_year_end_is_the_calendar_year():
    """Proves the resolver is genuinely parameterized, not hardcoded to
    March -- this is the exact seam that will let a non-March company
    (e.g. a future US company) be onboarded with only a Company-level
    value change, no change to this module. Not exercised by any real
    call site in Slice A, since no such company exists yet.
    """

    start, end = FiscalPeriod.parse("FY2024").resolve(fiscal_year_end_month=12)
    assert start == date(2024, 1, 1)
    assert end == date(2024, 12, 31)


def test_resolve_rejects_invalid_fiscal_year_end_month():
    with pytest.raises(FiscalPeriodError):
        FiscalPeriod.parse("FY2024").resolve(fiscal_year_end_month=13)
    with pytest.raises(FiscalPeriodError):
        FiscalPeriod.parse("FY2024").resolve(fiscal_year_end_month=0)


def test_resolve_period_label_convenience_wrapper_matches_two_step_call():
    assert resolve_period_label("FY2024Q3") == FiscalPeriod.parse(
        "FY2024Q3"
    ).resolve()
