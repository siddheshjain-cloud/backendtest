"""Research Brain Pilot ExtractionUnit layer -- additive table inventory.

Shared by the ExtractionUnit migration and its regression tests, same
discipline as ``migrations/m1_table_inventory.py`` and
``migrations/research_brain_pilot_table_inventory.py``: pin to one explicit
table list instead of the live SQLAlchemy metadata.

Separate constant, not an edit to either prior inventory -- this is new,
independently-additive scope (the amendment approved 2026-10-07), on top of
the already-independent Research Brain Pilot tables, which themselves never
reopened the frozen Milestone 1 ``M1_TABLES`` set.
"""

from __future__ import annotations


RESEARCH_BRAIN_EXTRACTION_UNIT_TABLES: frozenset[str] = frozenset(
    {
        "extraction_unit",
    }
)
