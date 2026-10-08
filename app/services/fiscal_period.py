"""Phase 3 Slice A -- the ``FiscalPeriod`` grammar/resolver contract.

Parses and validates the controlled reporting-period label every Slice B
``fin.*`` Fact write path (not built yet) will be required to use for
``ExtractedFact.period``, and resolves a label to real calendar dates.

Deliberately parameterized by ``fiscal_year_end_month`` rather than reading
it from ``Company`` -- that column does not exist yet, and every company in
this system today uses India's April-March fiscal year implicitly, so the
default (``3`` = March) is correct for every call site that exists right
now. When a company with a different fiscal year end is onboarded later,
exactly one change is needed: add the nullable ``Company.fiscal_year_end_month``
column (zero backfill risk) and have call sites pass
``company.fiscal_year_end_month or 3`` instead of relying on this module's
default. Nothing in this grammar, parser, or resolver needs to change.

Grammar: ``FY<year>``, ``FY<year>Q<1-4>``, ``FY<year>H<1-2>``,
``FY<year>TTM``. ``<year>`` is the calendar year the fiscal year *ends* in
(the Indian convention already implicit everywhere in this codebase).
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date
from enum import Enum


class FiscalPeriodType(Enum):
    ANNUAL = "ANNUAL"
    Q1 = "Q1"
    Q2 = "Q2"
    Q3 = "Q3"
    Q4 = "Q4"
    H1 = "H1"
    H2 = "H2"
    TTM = "TTM"


_GRAMMAR = re.compile(
    r"^FY(?P<year>\d{4})(?P<suffix>Q[1-4]|H[1-2]|TTM)?$"
)

_DEFAULT_FISCAL_YEAR_END_MONTH = 3


class FiscalPeriodError(ValueError):
    """Raised when a period label does not match the controlled grammar."""


@dataclass(frozen=True)
class FiscalPeriod:
    """A parsed, typed reporting period label."""

    fiscal_year: int
    period_type: FiscalPeriodType

    @classmethod
    def parse(cls, label: str) -> "FiscalPeriod":
        if not isinstance(label, str):
            raise FiscalPeriodError(
                f"Period label must be a string, got {type(label).__name__}"
            )
        match = _GRAMMAR.match(label)
        if not match:
            raise FiscalPeriodError(
                f"Period label {label!r} does not match the controlled "
                "grammar FY<year>[Q<1-4>|H<1-2>|TTM]"
            )
        suffix = match.group("suffix")
        period_type = (
            FiscalPeriodType(suffix) if suffix else FiscalPeriodType.ANNUAL
        )
        return cls(
            fiscal_year=int(match.group("year")), period_type=period_type
        )

    def label(self) -> str:
        suffix = (
            "" if self.period_type is FiscalPeriodType.ANNUAL
            else self.period_type.value
        )
        return f"FY{self.fiscal_year}{suffix}"

    def resolve(
        self, fiscal_year_end_month: int = _DEFAULT_FISCAL_YEAR_END_MONTH
    ) -> tuple[date, date]:
        """Resolve this label to its real ``(period_start, period_end)``
        calendar dates, given the company's fiscal-year-end month
        (1-12; defaults to March, the one convention every company in
        this system uses today).

        ``TTM`` resolves to the same full-fiscal-year window as
        ``ANNUAL`` -- a fiscal year is itself a trailing-twelve-month
        window ending at its own fiscal year end. A TTM window anchored
        to an arbitrary mid-year quarter end is not supported by this
        grammar and is out of scope for Slice A.
        """

        if not 1 <= fiscal_year_end_month <= 12:
            raise FiscalPeriodError(
                "fiscal_year_end_month must be between 1 and 12, got "
                f"{fiscal_year_end_month}"
            )

        fy_start_year, fy_start_month = _fiscal_year_start(
            self.fiscal_year, fiscal_year_end_month
        )

        if self.period_type in (FiscalPeriodType.ANNUAL, FiscalPeriodType.TTM):
            start = date(fy_start_year, fy_start_month, 1)
            end = _month_end(self.fiscal_year, fiscal_year_end_month)
            return start, end

        if self.period_type is FiscalPeriodType.H1:
            start = date(fy_start_year, fy_start_month, 1)
            end_year, end_month = _add_months(fy_start_year, fy_start_month, 5)
            return start, _month_end(end_year, end_month)

        if self.period_type is FiscalPeriodType.H2:
            start_year, start_month = _add_months(
                fy_start_year, fy_start_month, 6
            )
            start = date(start_year, start_month, 1)
            end = _month_end(self.fiscal_year, fiscal_year_end_month)
            return start, end

        quarter_index = {
            FiscalPeriodType.Q1: 0,
            FiscalPeriodType.Q2: 1,
            FiscalPeriodType.Q3: 2,
            FiscalPeriodType.Q4: 3,
        }[self.period_type]

        start_year, start_month = _add_months(
            fy_start_year, fy_start_month, quarter_index * 3
        )
        start = date(start_year, start_month, 1)
        end_year, end_month = _add_months(start_year, start_month, 2)
        return start, _month_end(end_year, end_month)


def resolve_period_label(
    label: str, fiscal_year_end_month: int = _DEFAULT_FISCAL_YEAR_END_MONTH
) -> tuple[date, date]:
    """Convenience wrapper: parse and resolve in one call."""

    return FiscalPeriod.parse(label).resolve(fiscal_year_end_month)


def _fiscal_year_start(fiscal_year: int, fiscal_year_end_month: int) -> tuple[int, int]:
    if fiscal_year_end_month == 12:
        return fiscal_year, 1
    return fiscal_year - 1, fiscal_year_end_month + 1


def _add_months(year: int, month: int, offset: int) -> tuple[int, int]:
    zero_based = (month - 1) + offset
    return year + zero_based // 12, zero_based % 12 + 1


def _month_end(year: int, month: int) -> date:
    return date(year, month, calendar.monthrange(year, month)[1])
