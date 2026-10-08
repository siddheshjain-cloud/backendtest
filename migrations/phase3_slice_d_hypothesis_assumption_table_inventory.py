"""Phase 3 Slice D -- additive table inventory.

Same discipline as every prior frozen-table-inventory constant in this
package: pin to one explicit table list instead of the live SQLAlchemy
metadata. Slice D is purely additive -- three new tables, no column/index
change to any existing table.
"""

from __future__ import annotations


PHASE3_SLICE_D_HYPOTHESIS_ASSUMPTION_TABLES: frozenset[str] = frozenset(
    {
        "investment_hypothesis",
        "investment_hypothesis_citation",
        "forecast_assumption",
    }
)
