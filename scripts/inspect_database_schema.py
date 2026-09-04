import argparse
import json
import os
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy.engine import Engine
from sqlalchemy.engine.reflection import Inspector


def inspect_schema(database_url: str) -> dict[str, object]:
    engine = sa.create_engine(database_url)
    try:
        inspector = sa.inspect(engine)
        tables = {
            name: inspect_table(inspector, name)
            for name in sorted(inspector.get_table_names())
        }
        return {
            "dialect": engine.dialect.name,
            "driver": engine.dialect.driver,
            "tables": tables,
            "alembic_version": inspect_alembic_version(engine, tables),
        }
    finally:
        engine.dispose()


def inspect_table(inspector: Inspector, name: str) -> dict[str, object]:
    columns = [
        {
            "name": column["name"],
            "type": str(column["type"]),
            "nullable": column["nullable"],
            "default": (
                str(column["default"]) if column.get("default") is not None else None
            ),
            "primary_key": bool(column.get("primary_key")),
        }
        for column in inspector.get_columns(name)
    ]
    columns.sort(key=lambda column: column["name"])

    primary_key = inspector.get_pk_constraint(name)
    foreign_keys = [
        {
            "name": foreign_key.get("name"),
            "columns": sorted(foreign_key.get("constrained_columns") or []),
            "referred_schema": foreign_key.get("referred_schema"),
            "referred_table": foreign_key.get("referred_table"),
            "referred_columns": sorted(foreign_key.get("referred_columns") or []),
        }
        for foreign_key in inspector.get_foreign_keys(name)
    ]
    unique_constraints = [
        {
            "name": constraint.get("name"),
            "columns": sorted(constraint.get("column_names") or []),
        }
        for constraint in inspector.get_unique_constraints(name)
    ]
    indexes = [
        {
            "name": index.get("name"),
            "columns": sorted(index.get("column_names") or []),
            "unique": bool(index.get("unique")),
        }
        for index in inspector.get_indexes(name)
    ]

    foreign_keys.sort(key=_metadata_sort_key)
    unique_constraints.sort(key=_metadata_sort_key)
    indexes.sort(key=_metadata_sort_key)

    return {
        "columns": columns,
        "primary_key": {
            "name": primary_key.get("name"),
            "columns": sorted(primary_key.get("constrained_columns") or []),
        },
        "foreign_keys": foreign_keys,
        "unique_constraints": unique_constraints,
        "indexes": indexes,
    }


def inspect_alembic_version(
    engine: Engine, tables: dict[str, object]
) -> dict[str, object]:
    if "alembic_version" not in tables:
        return {"present": False, "versions": []}

    with engine.connect() as connection:
        versions = sorted(
            str(version)
            for version in connection.execute(
                sa.text("SELECT version_num FROM alembic_version")
            ).scalars()
        )
    return {"present": True, "versions": versions}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Inspect database schema metadata")
    parser.add_argument("--database-url-env", required=True)
    parser.add_argument("--output", required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    database_url = os.environ[args.database_url_env]
    payload = inspect_schema(database_url)
    Path(args.output).write_text(
        json.dumps(payload, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return 0


def _metadata_sort_key(item: dict[str, object]) -> str:
    return json.dumps(item, sort_keys=True)


if __name__ == "__main__":
    raise SystemExit(main())
