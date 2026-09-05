"""legacy schema baseline

Revision ID: 20260904_01
Revises:
Create Date: 2026-09-04

=============================================================================
DESTRUCTIVE DOWNGRADE WARNING -- READ BEFORE RUNNING ANY DOWNGRADE
=============================================================================

``downgrade()`` DROPS the six legacy application tables:

    trade_tags, trade, telegram_verification, tag, user, ticker

On an existing populated database this DESTROYS ALL user, ticker, trade, tag
and Telegram data. It is NOT a rollback mechanism.

This revision is a baseline: on a verified existing deployment it is applied by
``stamp``, which writes only the ``alembic_version`` marker and creates nothing.
Downgrading it therefore removes tables this revision never created on that
database.

Permitted use of ``downgrade()``:
  - disposable local or CI databases only.

Never run on a shared, staging or production database. To roll back Milestone 1,
downgrade the ADDITIVE revision (``20260904_02``), which drops only the new
Investment Operating System tables and leaves the legacy schema intact. If the
legacy schema itself must be restored, use the verified backup required by
``docs/deployment/investment-operating-system-m1-database-gate.md``.
=============================================================================
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260904_01"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ticker",
        sa.Column("symbol", sa.String(length=20), nullable=False),
        sa.Column("exchange", sa.String(length=20), nullable=False),
        sa.Column("instrument_token", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("last_price", sa.Float(), nullable=False),
        sa.Column("last_updated", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("instrument_token"),
    )
    op.create_index("ix_ticker_name", "ticker", ["name"], unique=False)
    op.create_index("ix_ticker_symbol", "ticker", ["symbol"], unique=True)

    op.create_table(
        "user",
        sa.Column("is_admin", sa.Boolean(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("email", sa.String(length=120), nullable=False),
        sa.Column("phone_number", sa.String(length=20), nullable=True),
        sa.Column("password_hash", sa.String(length=255), nullable=True),
        sa.Column("google_id", sa.String(length=100), nullable=True),
        sa.Column("telegram_chat_id", sa.String(length=100), nullable=True),
        sa.Column("telegram_username", sa.String(length=100), nullable=True),
        sa.Column("telegram_enabled", sa.Boolean(), nullable=True),
        sa.Column("telegram_connected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_user_email", "user", ["email"], unique=True)
    op.create_index("ix_user_google_id", "user", ["google_id"], unique=True)
    op.create_index("ix_user_phone_number", "user", ["phone_number"], unique=True)
    op.create_index(
        "ix_user_telegram_chat_id", "user", ["telegram_chat_id"], unique=True
    )

    op.create_table(
        "tag",
        sa.Column("name", sa.String(length=50), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", "user_id", name="unique_user_tag"),
    )

    op.create_table(
        "telegram_verification",
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("verification_code", sa.String(length=20), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("verified", sa.Boolean(), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_telegram_verification_user_id",
        "telegram_verification",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        "ix_telegram_verification_verification_code",
        "telegram_verification",
        ["verification_code"],
        unique=True,
    )

    op.create_table(
        "trade",
        sa.Column("symbol", sa.String(length=20), nullable=False),
        sa.Column("side", sa.Enum("BUY", "SELL", name="trade_side"), nullable=False),
        sa.Column(
            "type",
            sa.Enum("Crossing Above", "Crossing Below", name="trade_type"),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum("Active", "Entry", "Stop Loss", "Target", name="trade_status"),
            nullable=False,
        ),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.Column("entry", sa.Float(), nullable=False),
        sa.Column("stoploss", sa.Float(), nullable=True),
        sa.Column("target", sa.Float(), nullable=True),
        sa.Column(
            "timeframe",
            sa.Enum("1m", "5m", "15m", "1h", "1D", "1W", "1M", name="trade_timeframe"),
            nullable=False,
        ),
        sa.Column("score", sa.Integer(), nullable=True),
        sa.Column("entry_x", sa.DateTime(timezone=True), nullable=True),
        sa.Column("stoploss_x", sa.DateTime(timezone=True), nullable=True),
        sa.Column("target_x", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "entry_eta",
            sa.Enum(
                "1 Minute",
                "5 Minutes",
                "15 Minutes",
                "1 Hour",
                "1 Day",
                "1 Week",
                "1 Month",
                "Far",
                name="trade_eta",
            ),
            nullable=True,
        ),
        sa.Column(
            "stoploss_eta",
            sa.Enum(
                "1 Minute",
                "5 Minutes",
                "15 Minutes",
                "1 Hour",
                "1 Day",
                "1 Week",
                "1 Month",
                "Far",
                name="trade_eta",
            ),
            nullable=True,
        ),
        sa.Column(
            "target_eta",
            sa.Enum(
                "1 Minute",
                "5 Minutes",
                "15 Minutes",
                "1 Hour",
                "1 Day",
                "1 Week",
                "1 Month",
                "Far",
                name="trade_eta",
            ),
            nullable=True,
        ),
        sa.Column("entry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("stoploss_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("target_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("edited_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("ticker_id", sa.String(length=36), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["ticker_id"], ["ticker.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_trade_symbol", "trade", ["symbol"], unique=False)
    op.create_index("ix_trade_ticker_id", "trade", ["ticker_id"], unique=False)
    op.create_index("ix_trade_user_id", "trade", ["user_id"], unique=False)

    op.create_table(
        "trade_tags",
        sa.Column("trade_id", sa.String(length=36), nullable=False),
        sa.Column("tag_id", sa.String(length=36), nullable=False),
        sa.ForeignKeyConstraint(["tag_id"], ["tag.id"]),
        sa.ForeignKeyConstraint(["trade_id"], ["trade.id"]),
        sa.PrimaryKeyConstraint("trade_id", "tag_id"),
    )


def downgrade() -> None:
    """DESTRUCTIVE: drops all six legacy tables and every row in them.

    Disposable local/CI databases only. See the module docstring. This is not a
    production rollback path; restore from the gate-required backup instead.
    """
    op.drop_table("trade_tags")
    op.drop_table("trade")
    op.drop_table("telegram_verification")
    op.drop_table("tag")
    op.drop_table("user")
    op.drop_table("ticker")
