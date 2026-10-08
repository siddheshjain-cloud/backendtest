"""Phase 3 Slice E -- additive table inventory.

Same discipline as every prior frozen-table-inventory constant in this
package: pin to one explicit table list instead of the live SQLAlchemy
metadata. Slice E's schema change is this one join table; the
``create_forecast_revision``/``_current_forecast_revision_locked``
service-layer correction (actually using the ``scenario`` column Slice
A's migration already added) touches no schema at all.
"""

from __future__ import annotations


PHASE3_SLICE_E_FORECAST_REVISION_ASSUMPTION_TABLES: frozenset[str] = frozenset(
    {"forecast_revision_assumption"}
)
