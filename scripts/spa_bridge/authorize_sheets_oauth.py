"""One-time local browser authorization for read-only Manifest Sheet access.

Mirrors C:\\SPA\\cloudrun_fixed_v7_5\\authorize_drive_oauth.py, scoped to
spreadsheets.readonly instead of Drive, and kept local to this canary --
it does not touch the production SPA Drive OAuth setup or Secret Manager.
"""

import argparse
import json
import os

SHEETS_READONLY_SCOPE = "https://www.googleapis.com/auth/spreadsheets.readonly"


def build_secret_payload(credentials) -> dict:
    if not credentials.refresh_token:
        raise RuntimeError("Google did not return a refresh token; revoke prior consent and retry")
    return {
        "client_id": credentials.client_id,
        "client_secret": credentials.client_secret,
        "refresh_token": credentials.refresh_token,
        "token_uri": credentials.token_uri,
        "scopes": list(credentials.scopes or [SHEETS_READONLY_SCOPE]),
    }


def _run_flow(client_path: str):
    from google_auth_oauthlib.flow import InstalledAppFlow

    flow = InstalledAppFlow.from_client_secrets_file(client_path, scopes=[SHEETS_READONLY_SCOPE])
    return flow.run_local_server(port=0, access_type="offline", prompt="consent")


def _write_private_json(path: str, payload: dict) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, separators=(",", ":"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("client_json")
    parser.add_argument("output_json")
    args = parser.parse_args()
    if not os.path.isfile(args.client_json):
        raise SystemExit("OAuth client JSON file was not found")
    if os.path.isfile(args.output_json):
        raise SystemExit(f"{args.output_json} already exists; remove it first to re-authorize")
    credentials = _run_flow(args.client_json)
    _write_private_json(args.output_json, build_secret_payload(credentials))
    print("Authorization completed; protected read-only credential payload created.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
