"""Phase 3 Slice C -- additive table inventory.

Same discipline as every prior frozen-table-inventory constant in this
package: pin to one explicit table list instead of the live SQLAlchemy
metadata. Slice C is purely additive -- four new tables, no column/index
change to any existing table -- so, unlike Slice A, this inventory is the
whole of what the migration creates.
"""

from __future__ import annotations


PHASE3_SLICE_C_CYCLE_INTELLIGENCE_TABLES: frozenset[str] = frozenset(
    {
        "cycle_observation",
        "cycle_assessment",
        "cycle_assessment_observation",
        "company_exposure",
    }
)
