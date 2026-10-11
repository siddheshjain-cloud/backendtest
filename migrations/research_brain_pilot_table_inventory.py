"""Research Brain Pilot additive table inventory.

Shared by the Research Brain Pilot migration and its regression tests so both
sides pin to one explicit table list instead of the live SQLAlchemy metadata,
exactly the same discipline ``migrations/m1_table_inventory.py`` already
uses for the frozen Milestone 1 set.

This is a deliberately *separate* constant from ``M1_TABLES``, not an edit to
it. The Research Brain Pilot is new, independently-additive scope approved
after Milestone 1 was frozen; it does not reopen the Milestone 1 revision or
its table set.
"""

from __future__ import annotations


RESEARCH_BRAIN_PILOT_TABLES: frozenset[str] = frozenset(
    {
        "extraction_run",
        "evidence",
        "extracted_fact",
        "fact_evidence",
    }
)
