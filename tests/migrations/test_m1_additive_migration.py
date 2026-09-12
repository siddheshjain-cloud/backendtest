"""Migration tests for the additive M1 schema revision after 20260904_01.

These tests deliberately stay focused on the migration/schema boundary. They do
not exercise application services or P5 behaviour.
"""

from __future__ import annotations

from datetime import datetime

import sqlalchemy as sa

from app import db
import app.models  # noqa: F401 - registers the current M1 metadata
from app.models.document import Document
from scripts.inspect_database_schema import inspect_schema
from tests.migrations.helpers import (
    LEGACY_TABLES,
    schema_snapshot,
    upgrade_database,
)


M1_TABLES = {
    "business_group",
    "company",
    "company_disclosure",
    "document",
    "document_audit_event",
    "document_company_link",
    "document_content",
    "document_storage_location",
    "forecast_line",
    "forecast_revision",
    "governance_flag",
    "institution",
    "institutional_report_metadata",
    "market_plan_revision",
    "ownership_snapshot",
    "research_point",
    "research_revision",
    "user_entitlement",
    "valuation_reference_line",
    "valuation_revision",
}

M1_HEAD = "20260904_02"


def _database_url(tmp_path, name: str) -> str:
    return f"sqlite:///{(tmp_path / name).as_posix()}"


def _model_metadata_database(tmp_path, name: str) -> str:
    url = _database_url(tmp_path, name)
    engine = sa.create_engine(url)
    try:
        db.metadata.create_all(engine)
    finally:
        engine.dispose()
    return url


def _upgraded_database(tmp_path, name: str, *, revision: str = "head") -> str:
    url = _database_url(tmp_path, name)
    upgrade_database(url, revision)
    return url


def _legacy_document_table(metadata: sa.MetaData) -> sa.Table:
    """Build the pre-P5A ``document`` table shape used by intermediate DBs."""

    columns = []
    for column in Document.__table__.columns:
        if column.name == "content_id":
            continue
        columns.append(column.copy())

    columns.extend(
        [
            sa.Column("content_hash_sha256", sa.String(64), nullable=True),
            sa.Column("storage_provider", sa.String(50), nullable=True),
            sa.Column("storage_key", sa.String(500), nullable=True),
        ]
    )
    return sa.Table("document", metadata, *columns)


def _intermediate_legacy_document_database(tmp_path) -> str:
    """Create 20260904_01 plus the pre-P5A document table and legacy rows."""

    url = _database_url(tmp_path, "intermediate-legacy-document.db")
    upgrade_database(url, "20260904_01")

    engine = sa.create_engine(url)
    legacy_metadata = sa.MetaData()
    document_table = _legacy_document_table(legacy_metadata)
    document_table.create(engine)

    sha_a = "a" * 64
    sha_b = "b" * 64
    now = datetime(2026, 9, 10, 10, 0, 0)

    def _row(
        document_id: str,
        fingerprint: str,
        content_hash: str | None,
        provider: str | None,
        storage_key: str | None,
    ) -> dict[str, object]:
        return {
            "id": document_id,
            "created_at": now,
            "document_type": "ANNUAL_REPORT",
            "title": f"Document {document_id}",
            "original_published_at_precision": "UNKNOWN",
            "discovery_source_type": "EXCHANGE",
            "source_access": "PUBLIC",
            "acquisition_method": "MANUAL_REFERENCE",
            "distribution_status": "LINK_ONLY",
            "ingestion_status": "STORED",
            "metadata_fingerprint": fingerprint,
            "is_fingerprint_duplicate": False,
            "created_by_user_id": "legacy-user",
            "updated_at": now,
            "content_hash_sha256": content_hash,
            "storage_provider": provider,
            "storage_key": storage_key,
        }

    with engine.begin() as connection:
        connection.execute(
            document_table.insert(),
            _row("doc-1", "fingerprint-1", sha_a, "s3", "bucket/key-1"),
        )
        connection.execute(
            document_table.insert(),
            _row("doc-2", "fingerprint-2", sha_a, "gcs", "bucket/key-2"),
        )
        connection.execute(
            document_table.insert(),
            _row("doc-3", "fingerprint-3", sha_b, None, None),
        )

    engine.dispose()
    return url


def _inventory(database_url: str) -> dict:
    return inspect_schema(database_url)


def test_baseline_upgrades_to_new_additive_head(tmp_path):
    url = _upgraded_database(tmp_path, "baseline-to-head.db")

    inventory = _inventory(url)

    assert inventory["alembic_version"] == {
        "present": True,
        "versions": [M1_HEAD],
    }
    assert M1_TABLES <= set(inventory["tables"])
    assert LEGACY_TABLES <= set(inventory["tables"])


def test_additive_head_has_current_m1_tables(tmp_path):
    url = _upgraded_database(tmp_path, "additive-tables.db")

    tables = set(_inventory(url)["tables"])

    assert M1_TABLES <= tables
    assert LEGACY_TABLES <= tables


def test_document_content_and_storage_location_constraints_exist(tmp_path):
    url = _upgraded_database(tmp_path, "content-constraints.db")
    inventory = _inventory(url)

    document_content = inventory["tables"]["document_content"]
    assert any(
        set(constraint["columns"]) == {"sha256"}
        for constraint in document_content["unique_constraints"]
    )

    document = inventory["tables"]["document"]
    assert any(
        set(foreign_key["columns"]) == {"content_id"}
        and foreign_key["referred_table"] == "document_content"
        for foreign_key in document["foreign_keys"]
    )

    storage_location = inventory["tables"]["document_storage_location"]
    assert any(
        set(foreign_key["columns"]) == {"content_id"}
        and foreign_key["referred_table"] == "document_content"
        for foreign_key in storage_location["foreign_keys"]
    )
    assert {
        index["name"] for index in storage_location["indexes"]
    } >= {"ix_document_storage_location_content_id"}


def test_legacy_document_hash_and_storage_data_migrate_without_loss(tmp_path):
    url = _intermediate_legacy_document_database(tmp_path)

    upgrade_database(url, "head")

    inventory = _inventory(url)
    document_columns = {
        column["name"] for column in inventory["tables"]["document"]["columns"]
    }
    assert "content_id" in document_columns
    assert not {
        "content_hash_sha256",
        "storage_provider",
        "storage_key",
    } & document_columns

    engine = sa.create_engine(url)
    try:
        with engine.connect() as connection:
            documents = dict(
                connection.execute(
                    sa.text(
                        "SELECT id, content_id FROM document "
                        "ORDER BY id"
                    )
                ).fetchall()
            )
            content_rows = connection.execute(
                sa.text(
                    "SELECT id, sha256 FROM document_content "
                    "ORDER BY sha256"
                )
            ).fetchall()
            location_rows = connection.execute(
                sa.text(
                    "SELECT content_id, provider, storage_key "
                    "FROM document_storage_location ORDER BY provider"
                )
            ).fetchall()
    finally:
        engine.dispose()

    content_by_sha = {sha256: content_id for content_id, sha256 in content_rows}

    # Identical hashes deduplicate to one DocumentContent row.
    assert len(content_rows) == 2
    assert set(content_by_sha) == {"a" * 64, "b" * 64}

    # Document.content_id is populated and both "a" documents share the same
    # content identity.
    content_id_a = content_by_sha["a" * 64]
    assert documents["doc-1"] == content_id_a
    assert documents["doc-2"] == content_id_a
    assert documents["doc-3"] == content_by_sha["b" * 64]

    # One content identity can have multiple storage/provenance locations.
    assert {row[0] for row in location_rows} == {content_id_a}
    assert {
        (row[1], row[2]) for row in location_rows
    } == {("s3", "bucket/key-1"), ("gcs", "bucket/key-2")}


def test_fresh_baseline_upgrade_is_equivalent_to_current_metadata(tmp_path):
    model_url = _model_metadata_database(tmp_path, "m1-model-metadata.db")
    migration_url = _upgraded_database(tmp_path, "m1-migration-fresh.db")

    assert schema_snapshot(migration_url, M1_TABLES) == schema_snapshot(
        model_url, M1_TABLES
    )


def test_alembic_head_is_the_new_revision(tmp_path):
    url = _upgraded_database(tmp_path, "head-version.db")

    inventory = _inventory(url)

    assert inventory["alembic_version"] == {
        "present": True,
        "versions": [M1_HEAD],
    }
