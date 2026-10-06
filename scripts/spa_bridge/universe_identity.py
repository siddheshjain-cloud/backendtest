"""Canonical Documents<->Universe company identity: the Drive folder id.

Root cause of the bug this replaces: the old join keyed the ``Universe``
tab on its ``Company Key`` column, falling back to ``Company`` only when
``Company Key`` was blank. The ``Documents`` tab has no ``Company Key``
column at all, so for every Universe company that *does* have one set
(78 of 93, confirmed against the live manifest), the join could never
match -- regardless of Ticker/Company seeding. Only the ~15 companies
with a blank ``Company Key`` happened to work, by falling back to a
plain ``Company`` string match on both sides.

The Drive folder id SPA already assigns once per company at onboarding
is not subject to that failure mode: it does not get retyped per
document and is not affected by spelling, legal-suffix ("Ltd" vs
"Limited"), or historical-naming differences the way any name/key
string is. Verified against the full live manifest (2,991 STORED rows,
93 Universe companies): 100% of Documents rows resolve to exactly one
Universe company via this join, with zero cross-company collisions.
``Universe`` carries two folder columns (``Folder ID``, ``Drive Folder
ID``) that are each partially blank but complementary; both are
indexed as one identity set per company.
"""

from __future__ import annotations

MISSING_DRIVE_FOLDER_ID = "MISSING_DRIVE_FOLDER_ID"

_FOLDER_COLUMNS = ("Folder ID", "Drive Folder ID")


def build_universe_identity_index(
    universe_rows: list[dict],
) -> dict[str, tuple[str, str]]:
    """Map each known Drive folder id to its company's (NSE, BSE) codes.

    Fails closed, loudly, if two different Universe companies claim the
    same folder id -- a Universe data error that must never be resolved
    by silently picking one and merging their documents.
    """

    index: dict[str, tuple[str, str]] = {}
    owner: dict[str, str] = {}
    for row in universe_rows:
        company = row.get("Company", "")
        codes = (row.get("NSE Symbol", ""), row.get("BSE Security Code", ""))
        for column in _FOLDER_COLUMNS:
            folder_id = (row.get(column) or "").strip()
            if not folder_id:
                continue
            existing_owner = owner.get(folder_id)
            if existing_owner is not None and existing_owner != company:
                raise RuntimeError(
                    f"Universe folder id {folder_id!r} is claimed by both "
                    f"{existing_owner!r} and {company!r} -- data error, "
                    "refusing to guess which company owns it"
                )
            owner[folder_id] = company
            index[folder_id] = codes
    return index


def resolve_universe_codes(
    document_row: dict, index: dict[str, tuple[str, str]]
) -> tuple[str, str] | str:
    """Look up one ``Documents`` row's (NSE, BSE) codes by Drive folder id.

    Returns the literal ``MISSING_DRIVE_FOLDER_ID`` sentinel when the row
    itself has no Drive folder id to look up at all -- distinct from a
    folder id that simply isn't in Universe yet, which returns blank
    codes and lets the caller's own fail-closed Ticker/Company
    resolution report ``UNRESOLVED_COMPANY`` as it already does.
    """

    drive_folder_id = (document_row.get("Drive Folder ID") or "").strip()
    if not drive_folder_id:
        return MISSING_DRIVE_FOLDER_ID
    return index.get(drive_folder_id, ("", ""))
