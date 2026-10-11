"""Phase 3 Slice A -- additive table inventory.

Same discipline as every prior frozen-table-inventory constant in this
package: pin to one explicit table list instead of the live SQLAlchemy
metadata. Slice A creates exactly one new table; the ``scenario``/``origin``
additions to ``forecast_revision``/``valuation_revision`` are column/index
changes to already-frozen tables, not new tables, and are handled directly
in the migration's ``upgrade()``/``downgrade()``, not via this inventory's
``create_all`` helper.
"""

from __future__ import annotations


PHASE3_SLICE_A_TABLES: frozenset[str] = frozenset({"financial_metric_definition"})
