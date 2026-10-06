"""Read-only Drive access for the bridge.

Used two ways:

* steady-state: a metadata-only ``files.get`` call (mime type, size) --
  never downloads the file, trusts SPA's own already Drive-readback-
  verified ``Content SHA256`` column (see
  ``C:\\SPA\\cloudrun_fixed_v7_5\\google_drive_store.py``, which already
  verifies every stored file's hash on write).
* canary (``--verify-hash``): downloads the file and recomputes its
  SHA-256 independently, as an extra trust-building check for the first
  real run -- never the default, because re-downloading every file on
  every steady-state run would defeat the point of trusting SPA's own
  verification.

There is no upload, rename, delete, or app-property write anywhere in
this module -- it cannot mutate Drive.
"""

from __future__ import annotations

import hashlib
import json
import os

_DRIVE_READONLY_SCOPE = "https://www.googleapis.com/auth/drive.readonly"
_DEFAULT_LOCAL_TOKEN_PATH = r"C:\SPA\private\spa-drive-readonly-oauth-token.json"


def _drive_credentials_from_local_file(path: str):
    """Load the refresh-token credential minted by authorize_drive_readonly_oauth.py.

    A separate, read-only-scoped credential from the production Drive
    OAuth secret -- never shared with it.
    """

    from google.oauth2.credentials import Credentials

    with open(path, "r", encoding="utf-8") as handle:
        payload = json.load(handle)

    required = ("client_id", "client_secret", "refresh_token", "token_uri", "scopes")
    for field in required:
        if not payload.get(field):
            raise RuntimeError(f"{path} is missing {field}")

    scopes = payload["scopes"]
    if _DRIVE_READONLY_SCOPE not in scopes:
        raise RuntimeError(f"{path} does not grant the required read-only Drive scope")

    return Credentials(
        token=None,
        refresh_token=payload["refresh_token"],
        token_uri=payload["token_uri"],
        client_id=payload["client_id"],
        client_secret=payload["client_secret"],
        scopes=scopes,
    )


def _drive_credentials_from_environment():
    """Fall back to SPA's own production Drive OAuth payload, if present.

    Reuses the exact credential shape SPA's own
    ``google_authentication.drive_credentials_from_environment`` expects
    (``SPA_DRIVE_OAUTH_JSON``: client_id/client_secret/refresh_token/
    token_uri/scopes) -- the same secret the live worker's Drive access
    uses -- but requests only the read-only Drive scope locally.
    """

    from google.oauth2.credentials import Credentials

    raw = os.environ.get("SPA_DRIVE_OAUTH_JSON", "").strip()
    if not raw:
        raise RuntimeError(
            "No local Drive read-only token found and SPA_DRIVE_OAUTH_JSON is not set"
        )
    payload = json.loads(raw)
    return Credentials(
        token=None,
        refresh_token=payload["refresh_token"],
        token_uri=payload["token_uri"],
        client_id=payload["client_id"],
        client_secret=payload["client_secret"],
        scopes=[_DRIVE_READONLY_SCOPE],
    )


def build_drive_service():
    """Build a read-only Drive API service.

    Prefers this PC's own local read-only OAuth token (see
    ``authorize_drive_readonly_oauth.py``); falls back to the production
    ``SPA_DRIVE_OAUTH_JSON`` payload only if no local token file exists.
    """

    from googleapiclient.discovery import build

    token_path = os.environ.get("SPA_DRIVE_READONLY_OAUTH_TOKEN_PATH", _DEFAULT_LOCAL_TOKEN_PATH)
    if os.path.isfile(token_path):
        credentials = _drive_credentials_from_local_file(token_path)
    else:
        credentials = _drive_credentials_from_environment()
    return build("drive", "v3", credentials=credentials, cache_discovery=False)


def file_metadata(service, file_id: str) -> dict:
    """Mime type and size only -- never downloads the file's bytes."""

    return service.files().get(fileId=file_id, fields="name,mimeType,size").execute()


def download_and_hash(service, file_id: str) -> tuple[bytes, str]:
    """Download the file and return ``(content, sha256_hex)``.

    Canary-only. Mirrors the hashing SPA's own
    ``GoogleDriveEvidenceStore.verify_hash`` already performs on write,
    just run independently here as a read-side check.
    """

    content = service.files().get_media(fileId=file_id).execute()
    return content, hashlib.sha256(content).hexdigest()
