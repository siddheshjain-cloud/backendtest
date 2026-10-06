"""Regression tests for the Drive-folder-id Documents<->Universe join.

Fixture is a trimmed, real snapshot of the live SPA manifest taken
2026-10-06 (tests/spa_bridge/fixtures/manifest_snapshot_2026-10-06.json),
not fabricated data -- pinned so these tests don't depend on Sheets
access or drift if the live sheet changes later.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.spa_bridge.universe_identity import (
    MISSING_DRIVE_FOLDER_ID,
    build_universe_identity_index,
    resolve_universe_codes,
)

FIXTURE_PATH = (
    Path(__file__).parent / "fixtures" / "manifest_snapshot_2026-10-06.json"
)


@pytest.fixture(scope="module")
def manifest():
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def universe_index(manifest):
    return build_universe_identity_index(manifest["universe"])


@pytest.fixture(scope="module")
def stored_documents(manifest):
    return [
        row
        for row in manifest["documents"]
        if (row.get("Ingestion Status") or "").strip().upper() == "STORED"
    ]


def test_fixture_has_the_expected_shape(manifest, stored_documents):
    assert len(manifest["universe"]) == 93
    assert len(stored_documents) == 2991


def test_every_stored_row_has_a_drive_folder_id(stored_documents):
    assert all((row.get("Drive Folder ID") or "").strip() for row in stored_documents)


def test_index_build_does_not_raise_on_the_real_manifest(manifest):
    # build_universe_identity_index raises on a folder-id collision; a
    # clean build is itself proof there are none in the live data.
    build_universe_identity_index(manifest["universe"])


def test_every_stored_row_resolves_to_exactly_one_universe_company(
    stored_documents, universe_index
):
    unresolved = [
        row
        for row in stored_documents
        if resolve_universe_codes(row, universe_index) == ("", "")
    ]
    assert unresolved == []


def test_every_resolved_company_has_at_least_one_exchange_code(
    stored_documents, universe_index
):
    for row in stored_documents:
        nse, bse = resolve_universe_codes(row, universe_index)
        assert nse or bse, row


def test_ikio_now_resolves_via_drive_folder_id_not_company_key(
    stored_documents, universe_index
):
    # This is the row the old Company-Key join silently failed on: the
    # Documents tab has no Company Key column, so IKIO's dict key
    # ("IKIO Technologies Ltd") never matched Universe's populated
    # Company Key ("IKIO_TECHNOLOGIES_LTD").
    ikio_rows = [
        row for row in stored_documents if row.get("Company") == "IKIO Technologies Ltd"
    ]
    assert len(ikio_rows) == 86
    for row in ikio_rows:
        assert resolve_universe_codes(row, universe_index) == ("IKIO", "543923")


def test_previously_working_company_is_unaffected(stored_documents, universe_index):
    # Grauer & Weil resolved even under the old buggy join (its
    # Universe Company Key happens to be blank); the fix must not
    # regress it.
    rows = [
        row
        for row in stored_documents
        if row.get("Company") == "Grauer & Weil (India) Ltd"
    ]
    assert rows
    for row in rows:
        assert resolve_universe_codes(row, universe_index) == ("GRAUWEIL", "505710")


def test_blank_drive_folder_id_is_distinguished_from_unresolved_company(universe_index):
    row = {"Company": "Some New Company", "Drive Folder ID": ""}
    assert resolve_universe_codes(row, universe_index) == MISSING_DRIVE_FOLDER_ID


def test_unknown_drive_folder_id_falls_through_to_unresolved_company(universe_index):
    row = {"Company": "Not Yet In Universe", "Drive Folder ID": "not-a-real-folder-id"}
    assert resolve_universe_codes(row, universe_index) == ("", "")


def test_folder_id_collision_across_two_companies_fails_closed():
    universe_rows = [
        {
            "Company": "Company A",
            "Folder ID": "shared-folder",
            "Drive Folder ID": "",
            "NSE Symbol": "AAA",
            "BSE Security Code": "",
        },
        {
            "Company": "Company B",
            "Folder ID": "",
            "Drive Folder ID": "shared-folder",
            "NSE Symbol": "BBB",
            "BSE Security Code": "",
        },
    ]
    with pytest.raises(RuntimeError, match="shared-folder"):
        build_universe_identity_index(universe_rows)


def test_two_folder_columns_for_one_company_are_both_indexed():
    universe_rows = [
        {
            "Company": "Same Company",
            "Folder ID": "folder-a",
            "Drive Folder ID": "folder-b",
            "NSE Symbol": "SAME",
            "BSE Security Code": "999999",
        }
    ]
    index = build_universe_identity_index(universe_rows)
    assert index["folder-a"] == ("SAME", "999999")
    assert index["folder-b"] == ("SAME", "999999")
