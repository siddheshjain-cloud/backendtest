"""Research Coverage & Fact Intelligence Slice 2 -- additive table inventory.

Shared by the Candidate Findings migration and its regression tests, same
discipline as ``migrations/m1_table_inventory.py``,
``migrations/research_brain_pilot_table_inventory.py``,
``migrations/research_brain_extraction_unit_table_inventory.py``, and
``migrations/research_coverage_foundation_table_inventory.py``: pin to one
explicit table list instead of the live SQLAlchemy metadata.

Separate constant, not an edit to any prior inventory -- this is new,
independently-additive scope (Slice 2 of the approved Research Coverage &
Fact Intelligence design), on top of Slice 1's Coverage Foundation, which
itself never reopened any earlier capability's frozen set.
"""

from __future__ import annotations


RESEARCH_COVERAGE_CANDIDATE_FINDINGS_TABLES: frozenset[str] = frozenset(
    {
        "candidate_finding",
        "candidate_finding_decision",
    }
)
