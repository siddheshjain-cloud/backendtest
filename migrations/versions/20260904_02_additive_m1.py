"""add current M1 tables and migrate legacy document storage

Revision ID: 20260904_02
Revises: 20260904_01
Create Date: 2026-09-12

This revision upgrades the frozen 20260904_01 legacy baseline to the current
Milestone 1 SQLAlchemy metadata. It is deliberately additive and SQLite-safe.

The document content-addressable transition is performed before the legacy
document-owned storage columns are removed. If a row still holds provider/key
data without a usable content identity, the migration fails closed rather than
silently dropping provenance.
"""

from __future__ import annotations

from datetime import datetime, timezone
import uuid

import sqlalchemy as sa
from alembic import op
from alembic.util.exc import CommandError

from app import db
import app.models  # noqa: F401 - registers the current M1 metadata


revision: str = "20260904_02"
down_revision: str = "20260904_01"
branch_labels = None
depends_on = None


LEGACY_TABLES = {
    "user",
    "ticker",
    "trade",
    "tag",
    "telegram_verification",
    "trade_tags",
}

LEGACY_DOCUMENT_COLUMNS = (
    "content_hash_sha256",
    "storage_provider",
    "storage_key",
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _document_columns(bind) -> set[str]:
    inspector = sa.inspect(bind)
    return {column["name"] for column in inspector.get_columns("document")}


def _normalise_sha256(value: object | None) -> str | None:
    if value is None:
        return None

    digest = str(value).strip().lower()
    if len(digest) != 64 or any(
        character not in "0123456789abcdef" for character in digest
    ):
        raise CommandError(
            "Cannot migrate legacy document content_hash_sha256: "
            f"value is not a 64-character SHA-256 hex digest: {value!r}"
        )
    return digest


def _get_or_create_content_id(bind, sha256: str) -> str:
    existing = bind.execute(
        sa.text(
            "SELECT id FROM document_content WHERE sha256 = :sha256"
        ),
        {"sha256": sha256},
    ).scalar()
    if existing is not None:
        return existing

    content_id = str(uuid.uuid4())
    bind.execute(
        sa.text(
            "INSERT INTO document_content (id, sha256, created_at) "
            "VALUES (:id, :sha256, :created_at)"
        ),
        {
            "id": content_id,
            "sha256": sha256,
            "created_at": _utcnow(),
        },
    )
    return content_id


def _add_document_content_id(bind) -> None:
    if "content_id" in _document_columns(bind):
        return

    if bind.dialect.name == "sqlite":
        bind.execute(
            sa.text(
                "ALTER TABLE document ADD COLUMN content_id "
                "VARCHAR(36) REFERENCES document_content(id)"
            )
        )
        return

    op.add_column(
        "document",
        sa.Column(
            "content_id",
            sa.String(36),
            sa.ForeignKey("document_content.id"),
            nullable=True,
        ),
    )


def _migrate_legacy_document_fields(bind) -> None:
    document_columns = _document_columns(bind)
    legacy_present = [
        column
        for column in LEGACY_DOCUMENT_COLUMNS
        if column in document_columns
    ]
    if not legacy_present:
        return

    select_columns = ["id"]
    if "content_id" in document_columns:
        select_columns.append("content_id")
    select_columns.extend(column for column in legacy_present)

    select_sql = (
        "SELECT "
        + ", ".join(select_columns)
        + " FROM document"
    )
    rows = bind.execute(sa.text(select_sql)).mappings().all()

    seen_locations: set[tuple[str, object, object]] = set()
    for row in rows:
        document_id = row["id"]
        existing_content_id = (
            row["content_id"] if "content_id" in row else None
        )
        raw_sha256 = (
            row["content_hash_sha256"]
            if "content_hash_sha256" in row
            else None
        )
        provider = (
            row["storage_provider"] if "storage_provider" in row else None
        )
        storage_key = (
            row["storage_key"] if "storage_key" in row else None
        )

        sha256 = _normalise_sha256(raw_sha256)
        if sha256 is not None:
            content_id = _get_or_create_content_id(bind, sha256)
            if existing_content_id is None:
                bind.execute(
                    sa.text(
                        "UPDATE document SET content_id = :content_id "
                        "WHERE id = :document_id"
                    ),
                    {
                        "content_id": content_id,
                        "document_id": document_id,
                    },
                )
            elif existing_content_id != content_id:
                raise CommandError(
                    "Cannot migrate legacy document content: document "
                    f"{document_id!r} already has content_id "
                    f"{existing_content_id!r}, but legacy hash maps to "
                    f"{content_id!r}"
                )
        else:
            content_id = existing_content_id

        if provider is None and storage_key is None:
            continue

        if content_id is None:
            raise CommandError(
                "Cannot migrate legacy document storage location for "
                f"document {document_id!r}: provider/key data exists "
                "without a usable SHA-256 content identity"
            )

        location_key = (content_id, provider, storage_key)
        if location_key in seen_locations:
            continue

        bind.execute(
            sa.text(
                "INSERT INTO document_storage_location "
                "(id, content_id, provider, storage_key, "
                "created_at, updated_at) "
                "VALUES (:id, :content_id, :provider, :storage_key, "
                ":created_at, :updated_at)"
            ),
            {
                "id": str(uuid.uuid4()),
                "content_id": content_id,
                "provider": provider,
                "storage_key": storage_key,
                "created_at": _utcnow(),
                "updated_at": _utcnow(),
            },
        )
        seen_locations.add(location_key)


def _drop_legacy_document_columns(bind, legacy_present) -> None:
    if not legacy_present:
        return

    if bind.dialect.name == "sqlite":
        for column in legacy_present:
            bind.execute(
                sa.text(f"ALTER TABLE document DROP COLUMN {column}")
            )
        return

    for column in legacy_present:
        op.drop_column("document", column)


def upgrade() -> None:
    bind = op.get_bind()

    # Create every missing current M1 table in dependency order. On a fresh
    # baseline this creates all M1 tables, including the content-addressed
    # document tables. On an intermediate database it only fills in tables
    # that do not already exist, so existing M1 work is preserved.
    db.metadata.create_all(bind=bind)

    if "document" not in set(sa.inspect(bind).get_table_names()):
        raise CommandError("Migration could not locate the document table")

    _add_document_content_id(bind)
    _migrate_legacy_document_fields(bind)

    document_columns = _document_columns(bind)
    legacy_present = [
        column
        for column in LEGACY_DOCUMENT_COLUMNS
        if column in document_columns
    ]
    _drop_legacy_document_columns(bind, legacy_present)


def downgrade() -> None:
    """Drop the additive M1 tables and return to the 20260904_01 baseline."""

    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # Reverse SQLAlchemy's dependency-sorted order so child tables drop before
    # their parents. SQLite is the supported test/development dialect and does
    # not enforce the foreign-key constraints during this additive rollback.
    m1_tables = [
        table
        for table in reversed(db.metadata.sorted_tables)
        if table.name not in LEGACY_TABLES
    ]

    for table in m1_tables:
        if not inspector.has_table(table.name):
            continue
        if bind.dialect.name == "sqlite":
            bind.execute(sa.text(f"DROP TABLE IF EXISTS {table.name}"))
        else:
            op.drop_table(table.name)
