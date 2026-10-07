"""Research Coverage & Fact Intelligence Slice 3 -- additive table inventory.

Shared by the Derived Facts migration and its regression tests, same
discipline as the five prior frozen-table-inventory constants in this
package: pin to one explicit table list instead of the live SQLAlchemy
metadata.

Separate constant, not an edit to any prior inventory -- this is new,
independently-additive scope (Slice 3 of the approved Research Coverage &
Fact Intelligence design), on top of Slices 1-2, neither of which this
revision reopens.
"""

from __future__ import annotations


RESEARCH_COVERAGE_FACT_DERIVATION_TABLES: frozenset[str] = frozenset(
    {
        "fact_derivation",
        "fact_derivation_input",
    }
)
