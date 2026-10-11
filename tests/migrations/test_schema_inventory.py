import json

import sqlalchemy as sa

from scripts.inspect_database_schema import inspect_schema, main


def _create_test_database(tmp_path, *, with_alembic_version=False):
    database_path = tmp_path / "inventory-secret-value.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    engine = sa.create_engine(database_url)
    metadata = sa.MetaData()
    parent = sa.Table(
        "parent",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("code", sa.String(20), nullable=False),
        sa.UniqueConstraint("code", name="uq_parent_code"),
    )
    sa.Index("ix_parent_code", parent.c.code)
    child = sa.Table(
        "child",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "parent_id",
            sa.Integer,
            sa.ForeignKey("parent.id", name="fk_child_parent"),
            nullable=False,
        ),
        sa.Column("label", sa.String(40), nullable=False),
        sa.UniqueConstraint("parent_id", "label", name="uq_child_parent_label"),
    )
    sa.Index("ix_child_label", child.c.label)
    if with_alembic_version:
        sa.Table(
            "alembic_version",
            metadata,
            sa.Column("version_num", sa.String(32), primary_key=True),
        )
    metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(parent.insert(), {"id": 1, "code": "TOP-SECRET-ROW"})
        connection.execute(
            child.insert(), {"id": 2, "parent_id": 1, "label": "PRIVATE-DATA"}
        )
        if with_alembic_version:
            connection.execute(
                metadata.tables["alembic_version"].insert(),
                {"version_num": "20260904_01"},
            )
    engine.dispose()
    return database_url


def test_inspect_schema_returns_sorted_metadata_without_row_data(tmp_path):
    database_url = _create_test_database(tmp_path)

    payload = inspect_schema(database_url)

    assert set(payload) == {
        "dialect",
        "driver",
        "tables",
        "alembic_version",
        "native_enums",
    }
    assert payload["dialect"] == "sqlite"
    assert payload["driver"] == "pysqlite"
    assert list(payload["tables"]) == ["child", "parent"]
    assert payload["alembic_version"] == {"present": False, "versions": []}

    child = payload["tables"]["child"]
    assert set(child) == {
        "columns",
        "primary_key",
        "foreign_keys",
        "unique_constraints",
        "indexes",
        "check_constraints",
    }
    assert [column["name"] for column in child["columns"]] == [
        "id",
        "label",
        "parent_id",
    ]
    assert child["primary_key"] == {"name": None, "columns": ["id"]}
    assert child["foreign_keys"] == [
        {
            "name": "fk_child_parent",
            "columns": ["parent_id"],
            "referred_schema": None,
            "referred_table": "parent",
            "referred_columns": ["id"],
        }
    ]
    assert child["unique_constraints"] == [
        {"name": "uq_child_parent_label", "columns": ["label", "parent_id"]}
    ]
    assert child["indexes"] == [
        {"name": "ix_child_label", "columns": ["label"], "unique": False}
    ]

    serialized = json.dumps(payload, sort_keys=True)
    assert database_url not in serialized
    assert "inventory-secret-value" not in serialized
    assert "TOP-SECRET-ROW" not in serialized
    assert "PRIVATE-DATA" not in serialized


def test_inspect_schema_is_deterministic(tmp_path):
    database_url = _create_test_database(tmp_path)

    first = json.dumps(inspect_schema(database_url), sort_keys=True)
    second = json.dumps(inspect_schema(database_url), sort_keys=True)

    assert first == second


def test_inspect_schema_reports_only_alembic_versions(tmp_path):
    database_url = _create_test_database(tmp_path, with_alembic_version=True)

    payload = inspect_schema(database_url)

    assert payload["alembic_version"] == {
        "present": True,
        "versions": ["20260904_01"],
    }


def _create_constraint_database(tmp_path):
    """A database whose enum representation lives in CHECK constraints."""
    database_path = tmp_path / "constraint-secret-value.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    engine = sa.create_engine(database_url)
    metadata = sa.MetaData()
    graded = sa.Table(
        "graded",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("secret_note", sa.String(40), nullable=False),
        sa.Column("score", sa.Integer, nullable=False),
        sa.Column(
            "grade",
            sa.Enum("PASS", "FAIL", name="grade_enum", native_enum=False),
            nullable=False,
        ),
        sa.CheckConstraint("score >= 0 AND score <= 100", name="ck_graded_score_range"),
    )
    metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(
            graded.insert(),
            {
                "id": 1,
                "secret_note": "CONSTRAINT-SECRET-ROW",
                "score": 50,
                "grade": "PASS",
            },
        )
    engine.dispose()
    return database_url


def test_inspect_schema_reports_check_constraints(tmp_path):
    database_url = _create_constraint_database(tmp_path)

    payload = inspect_schema(database_url)

    check_constraints = payload["tables"]["graded"]["check_constraints"]
    names = [constraint["name"] for constraint in check_constraints]
    assert "ck_graded_score_range" in names

    by_name = {constraint["name"]: constraint for constraint in check_constraints}
    assert "score" in by_name["ck_graded_score_range"]["sqltext"]
    assert all(set(c) == {"name", "sqltext"} for c in check_constraints)


def test_check_constraints_are_deterministic_and_leak_no_row_data(tmp_path):
    database_url = _create_constraint_database(tmp_path)

    first = json.dumps(inspect_schema(database_url), sort_keys=True)
    second = json.dumps(inspect_schema(database_url), sort_keys=True)

    assert first == second
    assert "CONSTRAINT-SECRET-ROW" not in first
    assert "constraint-secret-value" not in first
    assert database_url not in first


def test_native_enum_support_is_reported_explicitly_without_inventing_values(tmp_path):
    """SQLite has no native enum catalogue; say so rather than guessing."""
    database_url = _create_constraint_database(tmp_path)

    native_enums = inspect_schema(database_url)["native_enums"]

    assert set(native_enums) == {"supported", "enums"}
    assert native_enums["supported"] is False
    assert native_enums["enums"] == []


def test_legacy_baseline_reports_check_constraints_for_every_table(tmp_path):
    """Every legacy table carries a check-constraint section in the gate evidence."""
    from tests.migrations.helpers import LEGACY_TABLES, upgrade_database

    database_url = f"sqlite:///{(tmp_path / 'legacy-enums.db').as_posix()}"
    upgrade_database(database_url, "20260904_01")

    tables = inspect_schema(database_url)["tables"]

    for table_name in LEGACY_TABLES:
        assert isinstance(tables[table_name]["check_constraints"], list)


def test_legacy_enum_values_are_not_represented_in_the_sqlite_schema(tmp_path):
    """Records the dialect-dependence the deployment gate must reason about.

    The legacy models declare ``sa.Enum`` without ``native_enum=False``, and
    SQLAlchemy 2.0 defaults ``create_constraint`` to False. On SQLite the enum
    columns are therefore plain VARCHAR with no CHECK constraint, so the allowed
    values appear nowhere in the reflected schema. On a dialect with a native
    enum catalogue the same models create real types, which ``native_enums``
    reports. Enum drift review must therefore run against the deployment dialect,
    not against SQLite.
    """
    from tests.migrations.helpers import upgrade_database

    database_url = f"sqlite:///{(tmp_path / 'legacy-enum-shape.db').as_posix()}"
    upgrade_database(database_url, "20260904_01")

    payload = inspect_schema(database_url)
    trade = payload["tables"]["trade"]

    assert trade["check_constraints"] == []
    assert payload["native_enums"] == {"supported": False, "enums": []}

    columns = {column["name"]: column for column in trade["columns"]}
    assert columns["side"]["type"].startswith("VARCHAR")
    assert columns["status"]["type"].startswith("VARCHAR")


def test_cli_reads_named_environment_variable_and_writes_redacted_json(
    tmp_path, monkeypatch, capsys
):
    database_url = _create_test_database(tmp_path)
    output_path = tmp_path / "schema-inventory.json"
    monkeypatch.setenv("TASK4_TEST_DATABASE_URL", database_url)

    result = main(
        [
            "--database-url-env",
            "TASK4_TEST_DATABASE_URL",
            "--output",
            str(output_path),
        ]
    )

    output_text = output_path.read_text(encoding="utf-8")
    output_payload = json.loads(output_text)
    captured = capsys.readouterr()
    assert result == 0
    assert captured.out == ""
    assert captured.err == ""
    assert output_payload == inspect_schema(database_url)
    assert database_url not in output_text
    assert "inventory-secret-value" not in output_text
    assert "TOP-SECRET-ROW" not in output_text
    assert "PRIVATE-DATA" not in output_text
