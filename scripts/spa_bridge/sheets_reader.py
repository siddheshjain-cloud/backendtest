"""Read-only Google Sheets access to the live SPA manifest.

Deliberately read-only: this module only ever calls
``spreadsheets().values().get`` (there is no append/update/clear method
here at all, unlike SPA's own ``GoogleSheetsApiClient``). The SPA
ingestion worker remains the manifest's only writer.

Authentication mirrors how the live worker reads the Sheet (see
``C:\\SPA\\cloudrun_fixed_v7_5\\google_api_clients.py``: "Cloud Run
supplies application-default credentials from its attached service
account") -- this uses the same application-default credential
resolution, just scoped read-only, via ``GOOGLE_APPLICATION_CREDENTIALS``
or whatever ADC source is already configured in the environment.
"""

from __future__ import annotations

import os

_SHEETS_READONLY_SCOPE = "https://www.googleapis.com/auth/spreadsheets.readonly"


class ManifestSheetsReader:
    """Narrow read-only surface over one spreadsheet."""

    def __init__(self, service, spreadsheet_id: str):
        self._service = service
        self._spreadsheet_id = spreadsheet_id

    def read_tab(self, tab: str) -> list[dict[str, str]]:
        """Return every data row of ``tab`` as a header-keyed dict.

        Mirrors SPA's own ``GoogleSheetsApiClient.read_tab`` row shape
        (including the synthetic ``__row_number`` key) so the mapping
        code downstream can use the exact same header names the live
        worker's own repository code does.
        """

        response = (
            self._service.spreadsheets()
            .values()
            .get(spreadsheetId=self._spreadsheet_id, range=f"{tab}!A:ZZ")
            .execute()
        )
        values = response.get("values", [])
        if not values:
            return []

        headers = values[0]
        rows: list[dict[str, str]] = []
        for number, cells in enumerate(values[1:], start=2):
            row = {
                header: cells[index] if index < len(cells) else ""
                for index, header in enumerate(headers)
                if header
            }
            row["__row_number"] = number
            rows.append(row)
        return rows


_DEFAULT_LOCAL_TOKEN_PATH = r"C:\SPA\private\spa-sheets-oauth-token.json"


def _sheets_credentials_from_local_file(path: str):
    """Load the refresh-token credential minted by authorize_sheets_oauth.py."""

    import json

    from google.oauth2.credentials import Credentials

    with open(path, "r", encoding="utf-8") as handle:
        payload = json.load(handle)

    required = ("client_id", "client_secret", "refresh_token", "token_uri", "scopes")
    for field in required:
        if not payload.get(field):
            raise RuntimeError(f"{path} is missing {field}")

    scopes = payload["scopes"]
    if _SHEETS_READONLY_SCOPE not in scopes:
        raise RuntimeError(f"{path} does not grant the required read-only Sheets scope")

    return Credentials(
        token=None,
        refresh_token=payload["refresh_token"],
        token_uri=payload["token_uri"],
        client_id=payload["client_id"],
        client_secret=payload["client_secret"],
        scopes=scopes,
    )


def build_sheets_service():
    """Build a read-only Sheets API service.

    Prefers this PC's own local read-only OAuth token (see
    ``authorize_sheets_oauth.py``); falls back to ambient ADC credentials
    if no local token file is present.
    """

    from googleapiclient.discovery import build

    token_path = os.environ.get("SPA_SHEETS_OAUTH_TOKEN_PATH", _DEFAULT_LOCAL_TOKEN_PATH)
    if os.path.isfile(token_path):
        credentials = _sheets_credentials_from_local_file(token_path)
    else:
        from google.auth import default as google_auth_default

        credentials, _ = google_auth_default(scopes=[_SHEETS_READONLY_SCOPE])
    return build("sheets", "v4", credentials=credentials, cache_discovery=False)


def manifest_spreadsheet_id() -> str:
    """The manifest spreadsheet ID, from the same env var the live worker uses."""

    value = os.environ.get("SPA_MANIFEST_SHEET_ID", "").strip()
    if not value:
        raise RuntimeError("SPA_MANIFEST_SHEET_ID is required")
    return value
