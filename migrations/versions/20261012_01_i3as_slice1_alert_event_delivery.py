"""I3AS Slice 1 -- Integrated Investment Intelligence Alert System
(alert_event, alert_delivery)

Revision ID: 20261012_01
Revises: 20261008_05
Create Date: 2026-10-12

Purely additive. Creates exactly the two I3AS Slice 1 tables named in
``migrations/i3as_slice1_alert_table_inventory.py``'s
``I3AS_SLICE1_ALERT_TABLES`` set, using the same ``db.metadata`` /
``checkfirst`` approach every Phase 3 revision in this chain uses for
its own frozen set -- including ``alert_event``'s natural-key unique
constraint and ``alert_delivery``'s event/recipient/channel unique
constraint, which are declared directly on the model (see
``app/models/alert.py``) and so are created by ``create_all`` with no
separate DDL needed.

This revision does not touch any of the eleven frozen Phase 3/Research
Brain/Research Coverage table sets from prior revisions in any way, and
does not reopen any prior revision. No column is added to any existing
table (``trade``, ``user``, ``company`` included) -- the integration
surface is entirely additional service calls, not schema changes to
tables other services already own.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app import db
import app.models  # noqa: F401 - registers the current metadata, including alert_event/alert_delivery
from migrations.i3as_slice1_alert_table_inventory import I3AS_SLICE1_ALERT_TABLES


revision: str = "20261012_01"
down_revision: str = "20261008_05"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()

    db.metadata.create_all(
        bind=bind,
        tables=[
            db.metadata.tables[name]
            for name in sorted(I3AS_SLICE1_ALERT_TABLES)
        ],
        checkfirst=True,
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    for table in reversed(db.metadata.sorted_tables):
        if table.name not in I3AS_SLICE1_ALERT_TABLES:
            continue
        if not inspector.has_table(table.name):
            continue
        bind.execute(sa.text(f"DROP TABLE IF EXISTS {table.name}"))
