"""Migration tests for the Phase 3 Slice A revision (20261008_01).

Mirrors the prior Slice migration tests' discipline: this revision must add
exactly the one new table and the scenario/origin columns + partial unique
indexes on forecast_revision/valuation_revision, and leave every
already-frozen table untouched.
"""

from __future__ import annotations

import sqlalchemy as sa

from app import db
import app.models  # noqa: F401 - registers the current metadata
from migrations.m1_table_inventory import M1_TABLES
from migrations.research_brain_pilot_table_inventory import (
    RESEARCH_BRAIN_PILOT_TABLES,
)
from migrations.research_brain_extraction_unit_table_inventory import (
    RESEARCH_BRAIN_EXTRACTION_UNIT_TABLES,
)
from migrations.research_coverage_foundation_table_inventory import (
    RESEARCH_COVERAGE_FOUNDATION_TABLES,
)
from migrations.research_coverage_candidate_findings_table_inventory import (
    RESEARCH_COVERAGE_CANDIDATE_FINDINGS_TABLES,
)
from migrations.research_coverage_fact_derivation_table_inventory import (
    RESEARCH_COVERAGE_FACT_DERIVATION_TABLES,
)
from migrations.research_coverage_proposition_linkage_table_inventory import (
    RESEARCH_COVERAGE_PROPOSITION_LINKAGE_TABLES,
)
from migrations.phase3_slice_a_table_inventory import PHASE3_SLICE_A_TABLES
from tests.migrations.helpers import (
    schema_snapshot,
    stamp_database,
    upgrade_database,
    downgrade_database,
)


PROPOSITION_LINKAGE_HEAD = "20261007_05"
SLICE_A_HEAD = "20261008_01"

# M1 tables whose *shape* this revision additively extends -- excluded from
# the "every frozen table is byte-for-byte unchanged" assertion below and
# checked separately by test_scenario_and_origin_columns_added.
_EXTENDED_M1_TABLES = frozenset({"forecast_revision", "valuation_revision"})


def _database_url(tmp_path, name: str) -> str:
    return f"sqlite:///{(tmp_path / name).as_posix()}"


def test_upgrade_leaves_every_frozen_table_untouched_except_the_two_extended(
    tmp_path,
):
    url = _database_url(tmp_path, "slice-a-fresh.db")
    upgrade_database(url, PROPOSITION_LINKAGE_HEAD)

    before_m1 = schema_snapshot(url, M1_TABLES - _EXTENDED_M1_TABLES)
    before_pilot = schema_snapshot(url, RESEARCH_BRAIN_PILOT_TABLES)
    before_extraction_unit = schema_snapshot(
        url, RESEARCH_BRAIN_EXTRACTION_UNIT_TABLES
    )
    before_foundation = schema_snapshot(url, RESEARCH_COVERAGE_FOUNDATION_TABLES)
    before_candidates = schema_snapshot(
        url, RESEARCH_COVERAGE_CANDIDATE_FINDINGS_TABLES
    )
    before_derivation = schema_snapshot(url, RESEARCH_COVERAGE_FACT_DERIVATION_TABLES)
    before_propositions = schema_snapshot(
        url, RESEARCH_COVERAGE_PROPOSITION_LINKAGE_TABLES
    )

    upgrade_database(url, SLICE_A_HEAD)

    assert schema_snapshot(url, M1_TABLES - _EXTENDED_M1_TABLES) == before_m1
    assert schema_snapshot(url, RESEARCH_BRAIN_PILOT_TABLES) == before_pilot
    assert (
        schema_snapshot(url, RESEARCH_BRAIN_EXTRACTION_UNIT_TABLES)
        == before_extraction_unit
    )
    assert (
        schema_snapshot(url, RESEARCH_COVERAGE_FOUNDATION_TABLES)
        == before_foundation
    )
    assert (
        schema_snapshot(url, RESEARCH_COVERAGE_CANDIDATE_FINDINGS_TABLES)
        == before_candidates
    )
    assert (
        schema_snapshot(url, RESEARCH_COVERAGE_FACT_DERIVATION_TABLES)
        == before_derivation
    )
    assert (
        schema_snapshot(url, RESEARCH_COVERAGE_PROPOSITION_LINKAGE_TABLES)
        == before_propositions
    ), "Slice A must not change any Proposition Linkage table's schema"

    slice_a_tables = schema_snapshot(url, PHASE3_SLICE_A_TABLES)
    assert set(slice_a_tables) == PHASE3_SLICE_A_TABLES


def test_financial_metric_definition_table_shape(tmp_path):
    url = _database_url(tmp_path, "slice-a-fmd-shape.db")
    upgrade_database(url, SLICE_A_HEAD)

    snapshot = schema_snapshot(url, {"financial_metric_definition"})[
        "financial_metric_definition"
    ]
    column_names = {column["name"] for column in snapshot["columns"]}
    assert column_names == {
        "id",
        "created_at",
        "slug",
        "label",
        "statement_section",
        "namespace",
        "standard_unit",
        "description",
    }

    unique_columns = {
        tuple(constraint["columns"]) for constraint in snapshot["unique_constraints"]
    }
    assert ("slug",) in unique_columns


def test_scenario_and_origin_columns_added(tmp_path):
    url = _database_url(tmp_path, "slice-a-columns.db")
    upgrade_database(url, SLICE_A_HEAD)

    for table_name in ("forecast_revision", "valuation_revision"):
        snapshot = schema_snapshot(url, {table_name})[table_name]
        columns = {column["name"]: column for column in snapshot["columns"]}
        assert "scenario" in columns and columns["scenario"]["nullable"] is True
        assert "origin" in columns and columns["origin"]["nullable"] is False

        # the old composite UniqueConstraint must be gone, replaced by two
        # unique indexes -- not asserted as unique_constraints, since the
        # replacements are unique Index objects, not UniqueConstraints.
        unique_constraint_columns = {
            tuple(sorted(c["columns"])) for c in snapshot["unique_constraints"]
        }
        assert not unique_constraint_columns, (
            f"{table_name} must have zero composite UniqueConstraints after "
            "Slice A -- scenario uniqueness is enforced by partial indexes "
            "instead"
        )

        # the pre-existing CheckConstraint(s) must survive the batch rebuild
        check_names = {c["name"] for c in snapshot["check_constraints"]}
        if table_name == "forecast_revision":
            assert "ck_forecast_revision_number_positive" in check_names
        else:
            assert "ck_valuation_revision_number_positive" in check_names
            assert "ck_valuation_revision_discount_positive" in check_names


_PARTIAL_UNIQUE_INDEXES = (
    "uq_forecast_revision_company_number_legacy",
    "uq_forecast_revision_company_scenario_number",
    "uq_valuation_revision_company_method_number_legacy",
    "uq_valuation_revision_company_method_scenario_number",
)


def test_scenario_uniqueness_indexes_are_partial(tmp_path):
    """Mirrors tests/migrations/helpers.py's assert_m1_partial_index_predicates:

    SQLAlchemy's generic Inspector.get_indexes() does not reflect partial/
    filtered index WHERE predicates on any dialect, so a plain uniqueness
    check cannot tell a correctly filtered unique index from an
    accidentally unconditional one. Read the predicate directly from
    sqlite_master instead.
    """

    url = _database_url(tmp_path, "slice-a-partial-index.db")
    upgrade_database(url, SLICE_A_HEAD)

    engine = sa.create_engine(url)
    try:
        with engine.connect() as connection:
            rows = dict(
                connection.execute(
                    sa.text(
                        "SELECT name, sql FROM sqlite_master WHERE type = "
                        "'index' AND name IN "
                        "(:i1, :i2, :i3, :i4)"
                    ),
                    {
                        "i1": _PARTIAL_UNIQUE_INDEXES[0],
                        "i2": _PARTIAL_UNIQUE_INDEXES[1],
                        "i3": _PARTIAL_UNIQUE_INDEXES[2],
                        "i4": _PARTIAL_UNIQUE_INDEXES[3],
                    },
                ).fetchall()
            )
    finally:
        engine.dispose()

    missing = set(_PARTIAL_UNIQUE_INDEXES) - set(rows)
    assert not missing, f"Missing partial unique indexes: {sorted(missing)}"
    for name in _PARTIAL_UNIQUE_INDEXES:
        assert "WHERE" in (rows[name] or "").upper(), (
            f"{name} is not a filtered/partial index: {rows[name]!r}"
        )


def test_legacy_null_scenario_uniqueness_is_actually_enforced(tmp_path):
    """The specific regression this revision exists to prevent: a plain
    composite UniqueConstraint(company_id, scenario, revision_number) with
    nullable scenario would silently NOT enforce uniqueness among NULL rows
    (SQL NULL != NULL). Proves the partial-index replacement actually does.
    """

    url = _database_url(tmp_path, "slice-a-null-uniqueness.db")
    upgrade_database(url, SLICE_A_HEAD)

    engine = sa.create_engine(url)
    try:
        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO forecast_revision "
                    "(id, company_id, revision_number, scenario, as_of_date, "
                    "created_by_user_id, created_at) VALUES "
                    "('r1', 'C1', 1, NULL, '2026-01-01', 'u1', "
                    "'2026-01-01T00:00:00+00:00')"
                )
            )

        # Two legacy (scenario IS NULL) rows at the same (company_id,
        # revision_number) must collide.
        try:
            with engine.begin() as conn:
                conn.execute(
                    sa.text(
                        "INSERT INTO forecast_revision "
                        "(id, company_id, revision_number, scenario, "
                        "as_of_date, created_by_user_id, created_at) VALUES "
                        "('r2', 'C1', 1, NULL, '2026-01-01', 'u1', "
                        "'2026-01-01T00:00:00+00:00')"
                    )
                )
            raise AssertionError(
                "Two legacy (scenario IS NULL) rows at the same "
                "(company_id, revision_number) were allowed to coexist"
            )
        except sa.exc.IntegrityError:
            pass

        # Named-scenario rows at the same (company_id, revision_number)
        # must coexist across different scenarios...
        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO forecast_revision "
                    "(id, company_id, revision_number, scenario, as_of_date, "
                    "created_by_user_id, created_at) VALUES "
                    "('r3', 'C1', 1, 'BULL', '2026-01-01', 'u1', "
                    "'2026-01-01T00:00:00+00:00')"
                )
            )
            conn.execute(
                sa.text(
                    "INSERT INTO forecast_revision "
                    "(id, company_id, revision_number, scenario, as_of_date, "
                    "created_by_user_id, created_at) VALUES "
                    "('r4', 'C1', 1, 'BEAR', '2026-01-01', 'u1', "
                    "'2026-01-01T00:00:00+00:00')"
                )
            )

        # ...but must collide within the same named scenario.
        try:
            with engine.begin() as conn:
                conn.execute(
                    sa.text(
                        "INSERT INTO forecast_revision "
                        "(id, company_id, revision_number, scenario, "
                        "as_of_date, created_by_user_id, created_at) VALUES "
                        "('r5', 'C1', 1, 'BULL', '2026-01-01', 'u1', "
                        "'2026-01-01T00:00:00+00:00')"
                    )
                )
            raise AssertionError(
                "Two 'BULL' rows at the same (company_id, revision_number) "
                "were allowed to coexist"
            )
        except sa.exc.IntegrityError:
            pass
    finally:
        engine.dispose()


def test_downgrade_removes_slice_a_additions(tmp_path):
    url = _database_url(tmp_path, "slice-a-downgrade.db")
    upgrade_database(url, SLICE_A_HEAD)

    before_propositions = schema_snapshot(
        url, RESEARCH_COVERAGE_PROPOSITION_LINKAGE_TABLES
    )

    downgrade_database(url, PROPOSITION_LINKAGE_HEAD)

    engine = sa.create_engine(url)
    try:
        remaining_tables = set(sa.inspect(engine).get_table_names())
    finally:
        engine.dispose()

    assert not (remaining_tables & PHASE3_SLICE_A_TABLES)

    forecast_snapshot = schema_snapshot(url, {"forecast_revision"})[
        "forecast_revision"
    ]
    forecast_columns = {c["name"] for c in forecast_snapshot["columns"]}
    assert "scenario" not in forecast_columns
    assert "origin" not in forecast_columns
    assert {("company_id", "revision_number")} == {
        tuple(c["columns"]) for c in forecast_snapshot["unique_constraints"]
    }

    valuation_snapshot = schema_snapshot(url, {"valuation_revision"})[
        "valuation_revision"
    ]
    valuation_columns = {c["name"] for c in valuation_snapshot["columns"]}
    assert "scenario" not in valuation_columns
    assert "origin" not in valuation_columns

    after_propositions = schema_snapshot(
        url, RESEARCH_COVERAGE_PROPOSITION_LINKAGE_TABLES
    )
    assert after_propositions == before_propositions


# This project's migration tests replay every revision against the *live*
# db.metadata, so once app/models/forecast.py and app/models/valuation.py
# were edited in place for Slice A, replaying the chain from scratch makes
# even the *original* 20260904_02 M1 migration create these two tables in
# their already-Slice-A shape -- by the time 20261008_01 runs in that
# replay, there is no old uq_forecast_revision_company_number /
# uq_valuation_revision_company_method_number constraint left to drop. The
# tests above exercise the *end shape*, which is correct either way, but
# none of them actually prove the real ALTER path (DROP the old
# constraint, ADD the new columns) works -- a genuine production database
# upgrading from 20261007_05 has the true pre-Slice-A shape on disk,
# decoupled from today's Python model code. This test builds that true
# historical shape by hand (the exact DDL captured from a live pre-Slice-A
# database) and stamps the DB at 20261007_05 without replaying it, so the
# upgrade below exercises the real drop_constraint/add_column/create_index
# sequence, not the defensive no-op path.
_HISTORICAL_FORECAST_REVISION_DDL = """
CREATE TABLE forecast_revision (
    company_id VARCHAR(36) NOT NULL,
    revision_number INTEGER NOT NULL,
    supersedes_revision_id VARCHAR(36),
    as_of_date DATE NOT NULL,
    assumptions VARCHAR(4000),
    change_reason VARCHAR(2000),
    created_by_user_id VARCHAR(36) NOT NULL,
    id VARCHAR(36) NOT NULL,
    created_at DATETIME NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_forecast_revision_company_number UNIQUE (company_id, revision_number),
    CONSTRAINT ck_forecast_revision_number_positive CHECK (revision_number > 0)
)
"""

_HISTORICAL_VALUATION_REVISION_DDL = """
CREATE TABLE valuation_revision (
    company_id VARCHAR(36) NOT NULL,
    valuation_method VARCHAR(11) NOT NULL,
    revision_number INTEGER NOT NULL,
    supersedes_revision_id VARCHAR(36),
    justified_multiple NUMERIC(20, 4),
    implied_enterprise_value NUMERIC(20, 4),
    net_debt NUMERIC(20, 4),
    other_equity_adjustment NUMERIC(20, 4),
    implied_future_equity_value NUMERIC(20, 4),
    required_return_pct NUMERIC(20, 4),
    discount_period_years NUMERIC(20, 4),
    present_value NUMERIC(20, 4),
    current_market_cap NUMERIC(20, 4),
    currency VARCHAR(3),
    unit VARCHAR(20),
    valuation_notes VARCHAR(4000),
    as_of_date DATE NOT NULL,
    change_reason VARCHAR(2000),
    created_by_user_id VARCHAR(36) NOT NULL,
    id VARCHAR(36) NOT NULL,
    created_at DATETIME NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_valuation_revision_company_method_number UNIQUE (company_id, valuation_method, revision_number),
    CONSTRAINT ck_valuation_revision_number_positive CHECK (revision_number > 0),
    CONSTRAINT ck_valuation_revision_discount_positive CHECK (discount_period_years IS NULL OR discount_period_years > 0)
)
"""


def test_upgrade_against_a_genuinely_historical_pre_slice_a_shape(tmp_path):
    url = _database_url(tmp_path, "slice-a-real-alter-path.db")
    engine = sa.create_engine(url)
    try:
        with engine.begin() as conn:
            conn.execute(sa.text(_HISTORICAL_FORECAST_REVISION_DDL))
            conn.execute(sa.text(_HISTORICAL_VALUATION_REVISION_DDL))
            conn.execute(
                sa.text(
                    "INSERT INTO forecast_revision "
                    "(id, company_id, revision_number, as_of_date, "
                    "created_by_user_id, created_at) VALUES "
                    "('r1', 'C1', 1, '2026-01-01', 'u1', "
                    "'2026-01-01T00:00:00+00:00')"
                )
            )
    finally:
        engine.dispose()

    stamp_database(url, "20261007_05")
    upgrade_database(url, SLICE_A_HEAD)

    engine = sa.create_engine(url)
    try:
        insp = sa.inspect(engine)
        fr_constraints = {c["name"] for c in insp.get_unique_constraints("forecast_revision")}
        assert "uq_forecast_revision_company_number" not in fr_constraints
        fr_columns = {c["name"] for c in insp.get_columns("forecast_revision")}
        assert {"scenario", "origin"} <= fr_columns
        fr_indexes = {i["name"] for i in insp.get_indexes("forecast_revision")}
        assert {
            "uq_forecast_revision_company_number_legacy",
            "uq_forecast_revision_company_scenario_number",
        } <= fr_indexes

        vr_constraints = {
            c["name"] for c in insp.get_unique_constraints("valuation_revision")
        }
        assert "uq_valuation_revision_company_method_number" not in vr_constraints
        vr_columns = {c["name"] for c in insp.get_columns("valuation_revision")}
        assert {"scenario", "origin"} <= vr_columns

        with engine.connect() as conn:
            rows = conn.execute(
                sa.text(
                    "SELECT company_id, revision_number, scenario, origin "
                    "FROM forecast_revision WHERE id = 'r1'"
                )
            ).fetchall()
        assert rows == [("C1", 1, None, "HUMAN_AUTHORED")], (
            "the pre-existing row must survive the batch rebuild with its "
            "data intact and scenario/origin correctly defaulted"
        )
    finally:
        engine.dispose()
