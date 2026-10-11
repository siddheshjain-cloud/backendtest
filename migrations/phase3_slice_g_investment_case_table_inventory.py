"""Phase 3 Slice G -- additive table inventory.

Same discipline as every prior frozen-table-inventory constant in this
package: pin to one explicit table list instead of the live SQLAlchemy
metadata. Slice G is purely additive -- five new tables, no column/index
change to any existing table.
"""

from __future__ import annotations


PHASE3_SLICE_G_INVESTMENT_CASE_TABLES: frozenset[str] = frozenset(
    {
        "investment_case",
        "investment_case_forecast",
        "investment_case_valuation",
        "investment_case_kill_switch",
        "investment_case_gate_result",
    }
)
