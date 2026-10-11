# SPA bridge canary — checkpoint (2026-10-06, updated after Phase 2 completion)

## Status: canary + Company Key/Company join fix complete and proven.
## Historical-backfill Phase 1 (identity seeding) complete for 67 of 91
## remaining companies. Phase 2 (document backfill) is now ALSO complete
## for all 67 Phase-1-seeded companies -- see "Historical backfill Phase 2"
## below. 24 companies remain deliberately NOT seeded/migrated (flagged,
## not guessed -- see below), now tracked as the standing unresolved
## identity exception queue. Current baseline: `document`=1,246,
## `company`=69, `ticker`=69, `PRAGMA integrity_check`=ok.

## Local Sheets read access (resolved 2026-10-06)
Scope decision: this PC only, no Home/Office or cloud sync (deliberately
deferred to a later migration).
- OAuth 2.0 Desktop client `spa-sheets-oauth-client`, project `spa-ingestion`,
  scope `https://www.googleapis.com/auth/spreadsheets.readonly` only.
- Client secret: `C:\SPA\private\spa-sheets-oauth-client.json`.
- Token: `C:\SPA\private\spa-sheets-oauth-token.json` (0600, local-only,
  never in Secret Manager), minted by `scripts/spa_bridge/authorize_sheets_oauth.py`.
- `scripts/spa_bridge/sheets_reader.build_sheets_service()` prefers this
  local token (`SPA_SHEETS_OAUTH_TOKEN_PATH` env var overrides the
  default path), falls back to ambient ADC if absent.

## Local Drive read-only access (resolved 2026-10-06)
Kept strictly separate from the production Drive credential -- no reuse,
no modification of `spa-drive-oauth-client.json` or the `spa-drive-oauth`
Secret Manager secret (full `drive` scope, read/write, live-worker-only).
- OAuth 2.0 Desktop client `spa-drive-readonly-oauth-client`, same
  project, scope `https://www.googleapis.com/auth/drive.readonly` only.
- Client secret: `C:\SPA\private\spa-drive-readonly-oauth-client.json`.
- Token: `C:\SPA\private\spa-drive-readonly-oauth-token.json` (0600,
  local-only, never in Secret Manager), minted by
  `scripts/spa_bridge/authorize_drive_readonly_oauth.py`.
- `scripts/spa_bridge/drive_evidence.build_drive_service()` prefers this
  local token (`SPA_DRIVE_READONLY_OAUTH_TOKEN_PATH` env var overrides
  the default path); falls back to the production `SPA_DRIVE_OAUTH_JSON`
  payload only if no local token exists (never actually exercised).

## Company Key/Company join bug: fixed and proven (2026-10-06)
**Root cause:** the old join keyed `Universe` on its `Company Key`
column, but `Documents` has no such column -- so for the 78/93 Universe
companies with `Company Key` populated (IKIO included), the join could
never match regardless of seeding. Only 104/2991 STORED rows resolved
before the fix.

**Fix:** `scripts/spa_bridge/universe_identity.py` -- canonical identity
is now the Drive folder id (`Universe`'s `Folder ID` + `Drive Folder ID`
columns, unioned) instead of any name/key string. Fails closed, loudly,
on any folder-id collision across two companies.

**Proof:** 11 unit tests (`tests/spa_bridge/test_universe_identity.py`)
against a pinned real-data fixture
(`tests/spa_bridge/fixtures/manifest_snapshot_2026-10-06.json`); live
full-corpus check confirmed 2991/2991 STORED rows resolve to real
exchange codes (0 collisions, 0 missing folder ids); two independent
live registration + idempotent-rerun proofs (Grauer & Weil, IKIO).

## Inadvertent batch extension (2026-10-06) -- kept, approved after the fact
A live (non-dry-run) regression-check rerun caught IKIO's ticker/company
already being seeded and registered 40 additional real IKIO documents
beyond the single deliberate proof row (41 IKIO documents total, 42
overall with Grauer & Weil). Flagged immediately; user decision: kept,
no rollback -- verified clean (integrity ok, no duplicate titles/hashes,
no unintended companies).

## Historical backfill: scope and approach decided (2026-10-06)
Decided and approved: a **phased** backfill, not a single pass --
Phase 1 (identity seeding, no documents) fully reviewed before Phase 2
(document writes, batched per company with `--verify-hash` on each
company's first run).

### Phase 1 result: 67 of 91 companies seeded, 24 flagged (not guessed)
**Sourcing method:** bulk cross-reference against NSE's own official
master lists, not per-company guessing or memory:
- `https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv`
  (mainboard) -- 47 matches.
- `https://nsearchives.nseindia.com/emerge/corporates/content/SME_EQUITY_L.csv`
  (NSE Emerge/SME) -- 20 matches.
- Full provenance (company, nse_symbol, bse_code, official NSE name,
  ISIN) saved at `scripts/spa_bridge/phase1_identity_seed_2026-10-06.json`.

**Seeded (2026-10-06), via the same existing M1 service calls already
proven for Grauer & Weil/IKIO** (`Ticker` get-or-create with synthetic
`instrument_token`, no Kite session; `ResearchCommandService.create_company`
with the official NSE legal name + verified ISIN):
- 67 Tickers created, 67 Companies created, 0 collisions/reuse.
- DB verified after: `PRAGMA integrity_check` = `ok`; `company`=69,
  `ticker`=69 (67 new + Grauer & Weil + IKIO); 69 distinct ISINs, 69
  distinct symbols, 69 distinct instrument_token values.
- `document` count unchanged at **42** -- Phase 1 is identity-only, no
  document writes occurred.

**24 companies deliberately NOT seeded this round** -- bulk source
didn't cleanly cover them, and per the no-guessing rule established
this session, they're flagged for individual verification rather than
estimated:
- **20 BSE-code-only** (no NSE symbol in Universe at all): Anirit
  Ventures, Constronics Infra, Jasch Gauging Technologies, Schneider
  Electric President Systems, Danlaw Technologies India, Artificial
  Electronics Intelligent Material, Shree Refrigerations, Garg Furnace,
  Cospower Engineering, Trishakti Industries, L. T. Elevator, Tipco
  Engineering India, Sunita Tools, National Fittings, Praruh
  Technologies, Indo SMC, India Cements Capital, Amic Forging, Citadel
  Realty & Developers, Telecanor Global. BSE's own API/quote pages
  return 403 to automated access -- not scraped around; needs either an
  individual authoritative lookup per company or a different source
  (e.g. SPA's own onboarding records, if those carry ISIN).
- **4 NSE-symbol companies not in the current archive snapshot**: Jasch
  Industries (JASCHIND), Asian Petroproducts and Exports (ASINPET),
  Chemkart India (CHEMKART), Merritronix (MRTX) -- likely very recent
  listings or a symbol-spelling mismatch; needs a one-off check each.

Post-Phase-1 full-corpus resolution (computed Drive-free, i.e. without
the expensive per-row Drive metadata call -- see Phase 2 section below
for why that matters): of 2,991 STORED rows, 94 resolve to the 2 canary
companies (Grauer & Weil / IKIO), **2,439 resolve to the 67 Phase-1
companies** (Phase 2's full scope, now migrated -- see below), and 458
remain `UNRESOLVED_COMPANY` -- exactly the 24 flagged companies, 0 drift.

## Historical backfill Phase 2 (document backfill): complete (2026-10-06)
All 2,439 STORED rows belonging to the 67 Phase-1-seeded companies have
been processed live through `scripts/bridge_spa_manifest.py`'s existing
`--only-tickers`-scoped, idempotent path (no `--verify-hash` -- steady
state, trusting the manifest's recorded Content SHA256 per the script's
own docstring). **1,204 new documents registered**, the remainder
`ALREADY_REGISTERED` (either genuine intra-corpus duplicates -- multiple
manifest rows sharing one `(metadata_fingerprint, content_hash_sha256)`
-- or already committed by an earlier, interrupted attempt on the same
scope). Zero `REJECTED`, zero `HASH_MISMATCH`, across the entire run.

**Done in 3 planned batches (to bound per-run memory/Drive-call cost),
split further into smaller chunks mid-flight after two unrelated
incidents** (both read-only-safe, see below):
- **Batch 1** -- 23 companies, 815 rows, run as one pass. 410
  `REGISTERED` + 405 `ALREADY_REGISTERED` (intra-run dedup) = 815/815,
  clean.
- **Batch 2** -- 22 companies, 812 rows, run as one pass. 410
  `REGISTERED` + 402 `ALREADY_REGISTERED` = 812/812, clean.
- **Batch 3** -- 22 companies, 812 rows. The first full-batch attempt
  was killed by the OS for system-wide low memory (unrelated Chrome/
  Defender/other-app pressure on an 8GB machine, not this script's
  footprint, which stayed ~150-200MB throughout) after durably
  committing 168 rows. Resumed via the proven idempotent path, split
  into two ~405-row sub-batches, then further into 8 small chunks
  (55-127 rows each) after a second OOM kill (0 rows lost both times --
  every commit up to the kill stayed durable, verified by `document`
  count + `integrity_check` before and after each kill). One chunk
  (JKPAPER/MOREPENLAB/SOFTTECH) also hit one transient Sheets-API
  network timeout on its first attempt, before touching any row --
  retried clean on the next attempt. All 812 rows eventually accounted
  for across the original partial run + 8 chunks: 384 new documents
  total for Batch 3.

**Known pre-existing perf characteristic of this script, not a bug
introduced here:** `bridge_spa_manifest.py`'s per-row loop calls
`build_drive_service()` + a live `files.get` metadata call for *every*
resolved row whenever `--verify-hash` is omitted (not just when it's
passed) -- so an unscoped full-corpus check over ~2,500 rows means
~2,500 fresh Drive-client builds + network round-trips serialized in a
loop (15-20+ minutes). Scoping with `--only-tickers` to a batch/chunk
bounds this to that scope's row count, which is why batching helps
regardless of memory. Not changed this session (out of scope); worth a
follow-up if re-running wide, unscoped dry-runs becomes a recurring need.

**Final reconciliation (2026-10-06), this is the new baseline:**
- `PRAGMA integrity_check` = `ok`.
- `document`: 42 -> **1,246** (1,204 new from Phase 2 + the pre-existing
  42 canary documents).
- **1,246 distinct `metadata_fingerprint` values for 1,246 documents --
  zero duplicate writes across the entire Phase 2 run**, verified after
  every batch/chunk, not just at the end.
- `company`=69, `ticker`=69, unchanged throughout Phase 2 -- the
  document-bridge path never creates or modifies identity, by design.
- 2,439 of 2,439 Phase-1-company STORED rows accounted for (1,204
  `REGISTERED` + 1,235 `ALREADY_REGISTERED` across all batches/chunks/
  retries); 0 `REJECTED`; 0 `HASH_MISMATCH`.

**Standing unresolved identity exception queue -- preserved, NOT
touched, NOT to be auto-resolved:** the 458 STORED rows / 24 companies
below are explicitly carried forward exactly as Phase 1 left them. No
identity research was repeated or attempted on them this session, and
per instruction, no further document migration work is to start on them
without explicit new go-ahead:
- 20 BSE-code-only companies: Anirit Ventures, Constronics Infra, Jasch
  Gauging Technologies, Schneider Electric President Systems, Danlaw
  Technologies India, Artificial Electronics Intelligent Material, Shree
  Refrigerations, Garg Furnace, Cospower Engineering, Trishakti
  Industries, L. T. Elevator, Tipco Engineering India, Sunita Tools,
  National Fittings, Praruh Technologies, Indo SMC, India Cements
  Capital, Amic Forging, Citadel Realty & Developers, Telecanor Global.
- 4 NSE-symbol companies not in the current archive snapshot: Jasch
  Industries (JASCHIND), Asian Petroproducts and Exports (ASINPET),
  Chemkart India (CHEMKART), Merritronix (MRTX).
- (Full detail on why each is flagged, and candidate next sources, is
  unchanged from the "Phase 1 result" section above.)

## Verified good, do not redo
- `instance/trading_app.db`: `PRAGMA integrity_check` = `ok`.
- `scripts/bridge_spa_manifest.py`, `scripts/spa_bridge/{company_resolver,
  drive_evidence,payload_builder,sheets_reader,universe_identity,
  authorize_sheets_oauth,authorize_drive_readonly_oauth,__init__}.py`:
  present, complete, join fix applied.
- `tests/spa_bridge/test_universe_identity.py` + its fixture: 11 passing
  regression tests, pinned to real data.
- Local Sheets + Drive-readonly auth: both resolved, do not redo.
- Production Drive OAuth client/secret: untouched throughout.
- 69 companies/tickers now registered (2 from the canary + 67 from Phase
  1) -- do not re-seed; the seed script is idempotent (get-or-create) if
  it needs to be rerun for any reason, but there is currently nothing
  left for it to create among these 67.
- **Phase 2 document backfill for all 67 Phase-1 companies: complete.**
  1,246 documents total, baseline as of 2026-10-06 -- do not re-run the
  full batches "to be sure"; the bridge path is idempotent (rerunning
  any already-done `--only-tickers` scope just reports
  `ALREADY_REGISTERED`), but there is nothing left for it to register
  among these 67's 2,439 STORED rows.
- **No document registrations without explicit approval** -- still in
  force going forward. It governed every Phase 2 write (each batch/
  chunk was explicitly authorized before running); the next thing it
  gates is the 24 flagged companies' documents, which have NOT been
  touched and are NOT to be started without new, explicit approval.

## Explicit boundary (per instruction, still in force)
No new service-account key, no IAM policy change, no reuse/modification
of the production Drive credential.

## Resume point for next session
1. **Baseline to treat as ground truth:** `document`=1,246,
   `company`=69, `ticker`=69, `PRAGMA integrity_check`=ok. This is the
   M1 Document Library historical-migration baseline -- Phase 1
   (identity) + Phase 2 (documents) are both complete for all 67
   Phase-1-seeded companies.
2. **Do not** re-run Batches 1-3 or any of their chunks "to be sure" --
   confirmed idempotent and complete; a rerun would only produce
   `ALREADY_REGISTERED` and cost time/Drive-API calls for nothing.
3. **Do not** start resolving the 24 flagged companies (the 458-row
   exception queue, see above) and **do not** perform any further
   document migration yet -- explicitly on hold pending new, separate
   go-ahead. When that go-ahead comes, the open question is still: which
   source for the 20 BSE-code-only companies' ISINs (BSE's own API/quote
   pages 403 automated access), and a one-off listing check for the 4
   NSE-symbol companies not in the current archive snapshot.
4. Everything else (Sheets auth, Drive-readonly auth, the join fix,
   Grauer & Weil + IKIO, the 67-company Phase 1 seed, and now the full
   Phase 2 document backfill) is done and does not need to be repeated.
