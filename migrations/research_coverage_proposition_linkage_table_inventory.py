"""Research Coverage & Fact Intelligence Slice 4 -- additive table inventory.

Shared by the Proposition Linkage migration and its regression tests,
same discipline as the six prior frozen-table-inventory constants in this
package: pin to one explicit table list instead of the live SQLAlchemy
metadata.

Separate constant, not an edit to any prior inventory -- this is new,
independently-additive scope (Slice 4 of the approved Research Coverage &
Fact Intelligence design), on top of Slices 1-3, none of which this
revision reopens.
"""

from __future__ import annotations


RESEARCH_COVERAGE_PROPOSITION_LINKAGE_TABLES: frozenset[str] = frozenset(
    {
        "research_proposition",
        "proposition_stage_type",
        "proposition_link",
    }
)
