"""Research Coverage & Fact Intelligence, Slice 1 -- additive table inventory.

Shared by the Slice 1 (Coverage Foundation) migration and its regression
tests, same discipline as ``migrations/m1_table_inventory.py``,
``migrations/research_brain_pilot_table_inventory.py``, and
``migrations/research_brain_extraction_unit_table_inventory.py``: pin to one
explicit table list instead of the live SQLAlchemy metadata.

Separate constant, not an edit to any prior inventory -- this is new,
independently-additive scope (Slice 1 of the approved Research Coverage &
Fact Intelligence design). It never reopens ``M1_TABLES``,
``RESEARCH_BRAIN_PILOT_TABLES``, or ``RESEARCH_BRAIN_EXTRACTION_UNIT_TABLES``.
"""

from __future__ import annotations


RESEARCH_COVERAGE_FOUNDATION_TABLES: frozenset[str] = frozenset(
    {
        "research_dimension",
        "coverage_document_subtype",
        "coverage_profile",
        "coverage_profile_dimension",
        "coverage_review_pass",
        "coverage_record",
    }
)
