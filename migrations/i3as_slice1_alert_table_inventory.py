"""I3AS Slice 1 -- additive table inventory.

Same discipline as every Phase 3 frozen-table-inventory constant in
this package: pin to one explicit table list instead of the live
SQLAlchemy metadata. Slice 1 is purely additive -- two new tables, no
column/index change to any existing table.
"""

from __future__ import annotations


I3AS_SLICE1_ALERT_TABLES: frozenset[str] = frozenset(
    {
        "alert_event",
        "alert_delivery",
    }
)
