# Research Brain Pilot — Snapshot A (frozen 2026-10-07)

## Boundary definition

Snapshot A is the Research Brain Pilot's Task 5 real run, deliberately scoped
to **only** documents already registered in the M1 Document Library for IKIO
Technologies Limited and UNO Minda Limited as of this freeze. No
Manifest/Drive-only (STORED-but-unregistered) rows and no newly-discovered
documents were used. This is the exact, reproducible data-through boundary
this snapshot is pinned to -- any later snapshot's delta is measured against
this one.

- **IKIO Technologies Limited** (`c8b839a8-5319-498f-b480-0d6971299094`): 41
  registered documents, `document_date` range 2026-06-27 to 2026-09-11.
- **UNO Minda Limited** (`9cbdabe1-3008-45dc-8ef6-dad859946990`): 46
  registered documents, `document_date` range 2026-06-20 to 2026-09-15.
- **Combined document set**: 87 documents.
- **`snapshot_a_document_set_sha256`**: `d1f1a4a7daaeb16be8182be2c2b97eeaea199e5ef93cac2e8fe3b759e100d577`
  -- SHA-256 of the sorted, newline-joined list of all 87 document ids.
  Reproducible by re-running the same query against `document`/
  `document_company_link` for these two `company_id`s; an unchanged hash
  proves the boundary hasn't silently drifted.

## What was extracted

Both facts use documents already registered pre-freeze; each source PDF's
downloaded bytes were hash-verified against the already-registered
`document_content.sha256` before extraction (see table below) -- proving the
extraction read the exact already-registered content, not anything
freshly fetched or substituted.

### IKIO: `quarterly_total_revenue`, Q1 FY2027

- **Fact**: `de741f33-52eb-42de-a1f6-53b0a57909d5` -- value `1693`, unit
  `INR Million`, `as_of_date` 2026-08-08 (the results-announcement date;
  distinct from `created_at`, which is the pilot-run timestamp).
- **Evidence 1** (`31312902-eff7-4f27-ba26-7c2b68eb1ab1`, from
  `IKIO_CONCALL_14_08_2026`): "The revenue from operations grew 41%
  year-on-year to Rs.169 crores in Q1 FY'27..."
- **Evidence 2** (`dae87a0a-f14c-4795-8962-23991e346700`, from
  `IKIO_PRESENTATION_Q1_FY2027`): Revenue chart, Total 1,201 -> 1,693 (Rs
  Mn), Q1FY26 -> Q1FY27, annotated 41% YoY.
- Independently stated in two separate documents (earnings call transcript
  and investor presentation), corroborating the same figure.

**Noted but deliberately not recorded as a fact**: the Quarterly Results
filing's own consolidated "Revenue from operations" statutory line for the
same quarter reads 1,602.89 (INR Million) -- about 90 Mn below the 1,693 Mn
total the concall and presentation both state. This is very likely a
scope/definition difference (the presentation's "Total" appears to sum only
the two named business segments charted, Other Business + Home Lighting
ODM, which may not be the complete statutory revenue line), not a
transcription error, since the segment-level figures (Other Business Rs
1,244 Mn/53% YoY; Home Lighting ODM Rs 448 Mn/16% YoY) independently
corroborate cleanly between the concall and presentation too. Left
unresolved rather than guessed at -- exactly the kind of discrepancy this
pilot exists to surface, not paper over.

### UNO Minda: `quarterly_total_revenue`, Q1 FY2027 (portability check)

- **Fact**: `2a2fa562-c1ce-4711-b3f7-8685237b2908` -- value `5557`, unit
  `INR Crore`, `as_of_date` 2026-08-04.
- **Evidence 1** (`f69fdb2c-710c-4a86-9166-32136c9d744a`, from
  `UNOMINDA_CONCALL_Q1_FY2027`): "Our consolidated revenue from operations
  for Q1 FY27 stood at INR 5,557 crores, representing a robust 26%
  year-on-year growth as against INR 4,420 crores..."
- **Evidence 2** (`cba7589b-5b5c-4d95-ab3c-11a5c6374ba7`, from
  `UNOMINDA_PRESENTATION_Q1_FY2027`): "Consolidated Revenues increased by
  26% Y-o-Y to Rs 5,557 Cr for the quarter"; chart 4,420 -> 5,557 (Rs Cr).
- Confirms the mechanism generalizes beyond IKIO with no schema or service
  change, per the approved design proposal's portability-check scope.

## Source content verification

| Document | Registered `document_content.sha256` | Downloaded-bytes SHA-256 |
|---|---|---|
| IKIO_RESULTS_Q1_FY2027 (not used as evidence; read only for context) | `089c59c2...` | matches |
| IKIO_CONCALL_14_08_2026 | `d8198720...` | matches |
| IKIO_PRESENTATION_Q1_FY2027 | `e9d8c88d...` | matches |
| UNOMINDA_RESULTS_Q1_FY2027 (not used as evidence; read only for context) | `8f15a158...` | matches |
| UNOMINDA_CONCALL_Q1_FY2027 | `c6c5ead4...` | matches |
| UNOMINDA_PRESENTATION_Q1_FY2027 | `c9a327ed...` | matches |

(Full 64-character hashes recorded in the extraction run; truncated here for
readability.)

## Database state at freeze

- `PRAGMA integrity_check` = `ok`
- `document` = 1,246, `company` = 69, `ticker` = 69 -- all unchanged by this
  snapshot, confirming the Research Brain Pilot tables are purely additive.
- `extraction_run` = 4, `evidence` = 4, `extracted_fact` = 2,
  `fact_evidence` = 4.
- Alembic revision: `20261006_01` (applied to `instance/trading_app.db` for
  the first time as part of this run; was previously only applied to test
  databases).

## Explicitly out of scope for Snapshot A

No STORED-in-Manifest-but-not-registered-in-M1 document was read or used.
No new document discovery (NSE/BSE archives, company IR pages, or any other
external source) was performed. Both are reserved for the reconciliation
step that follows this freeze, and any resulting registration/ingestion is
withheld pending separate explicit authorization.
