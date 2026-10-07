# Research Brain Pilot — Snapshot B (frozen 2026-10-07)

**Status: FROZEN.** Snapshot B is the incremental extension of Snapshot A
(`docs/research_brain/snapshot_a_final_2026-10-07.md`, frozen at commit
`45d4217bb53d125bb0d6cd123400b58f4945bf84`) — five genuinely new,
externally-sourced documents registered and processed through the same
Research Brain mechanism, plus the two Facts the incremental content
analytically warranted. **Snapshot A is preserved exactly as frozen**: its
87-document boundary, its 17 facts, its Evidence, and its own artifact file
are untouched by this commit — Snapshot B is additive, not a replacement.

## Incremental boundary — 5 documents, sourced outside SPA's Manifest

These five were never in SPA's own discovery Manifest (the Category-3
"genuinely undiscovered" finding from the earlier reconciliation). Sourced
directly from NSE's own archive, using the original SPA ingestion worker's
unmodified discovery mechanism (`C:\SPA\cloudrun_fixed_v7_5\discovery.py`,
read-only import, no file changes) — browser User-Agent, matching Referer,
session warm-up, per-host pacing, exponential backoff. Registered via
`DocumentLibraryService.create_document`, the same M1 write path the SPA
bridge itself uses, with `discovery_source_type=EXCHANGE`,
`acquisition_method=PUBLIC_DOWNLOAD`, and an honest
`storage_provider="nse_archive"` (no SPA-Drive copy exists for these, so the
existing `storage_provider="google_drive"` convention would have been
false).

| Document | Company | Date | SHA-256 | Source |
|---|---|---|---|---|
| `IKIO_REG30_TRADING_WINDOW_CLOSURE_24_09_2026` | IKIO | 2026-09-24 | `f70649d3…` | nsearchives.nseindia.com — byte-identical to the independently-fetched BSE copy of the same filing |
| `IKIO_REG30_REGULATION_74_5_CERTIFICATE_06_10_2026` | IKIO | 2026-10-06 | `9713b583…` | nsearchives.nseindia.com |
| `UNO_REG30_TRADING_WINDOW_CLOSURE_24_09_2026` | UNO Minda | 2026-09-24 | `85f0d086…` | nsearchives.nseindia.com |
| `UNO_REG30_VAT_CST_ORDER_PUNE_24_09_2026` | UNO Minda | 2026-09-24 | `12715d48…` | nsearchives.nseindia.com |
| `UNO_REG30_COMMERCIAL_PAPER_ISSUANCE_25_09_2026` | UNO Minda | 2026-09-25 | `44823fb8…` | nsearchives.nseindia.com |

**`snapshot_b_incremental_document_set_sha256`**:
`48da521cc006f6da63e5938a3a7273f02aedfd0e86db624cf14c2849bb4dc500` — SHA-256
of the sorted, newline-joined list of these 5 document ids.

All 5 were content-verified before registration (hash-matched against the
bytes actually downloaded) and cross-checked for company identity during
the prior reconciliation pass (two near-misses — a different listed company
sharing the "Minda" name, and a wrong BSE scrip code — were caught and
rejected before reaching this stage).

## Database state: before → after

| | Before (Snapshot A final) | After (Snapshot B) | Δ |
|---|---|---|---|
| `PRAGMA integrity_check` | `ok` | `ok` | — |
| `document` | 1,246 | 1,251 | +5 |
| `company` | 69 | 69 | 0 |
| `ticker` | 69 | 69 | 0 |
| `extraction_run` | 111 | 118 | +7 (5 registration passes + 2 fact-deriving passes) |
| `extraction_unit` | 2,115 | 2,123 | +8 (pages: 1+3+1+2+1 across the 5 docs) |
| `evidence` | 33 | 35 | +2 |
| `extracted_fact` | 17 | 19 | +2 |
| `fact_evidence` | 33 | 35 | +2 |

## The 2 new Facts — full Evidence provenance

### UNO Minda `tax_dispute_pune_vat_cst` (as_of 2026-09-24)
UML received a Review order from the Joint Commissioner of State Tax—Pune
VAT on Sept 24, 2026, regarding non-submission/short submission of CST
declaration forms, FY2015-16 through FY2017-18. **Net demand Rs
1,13,72,385** (Tax Rs 27,95,784 + Penalty Rs 27,95,784 + Interest Rs
59,55,156, less Rs 1,74,339 already paid) — the precise primary-source
figure, which differs slightly from the ~Rs 1.13 Cr a secondary aggregator
(screener.in) used during Category-3 reconciliation; noted explicitly in
the fact's own text as a precision refinement, not a contradiction between
primary sources. Company intends to contest on merits; states no material
impact expected.
- Evidence: `UNO_REG30_VAT_CST_ORDER_PUNE_24_09_2026`, page 1 (Regulation 30
  disclosure, order details table), citing the persisted `ExtractionUnit`.

### UNO Minda `commercial_paper_issuance` (as_of 2026-09-25)
UML issued and allotted Rs 100 Cr Commercial Papers on Sept 25, 2026 (ISIN
INE405E14307), private placement, unsecured, 90-day tenor maturing Dec 24,
2026, coupon 6.30%, sole investor Kotak Mahindra Bank. Rated IND A1+.
- Evidence: `UNO_REG30_COMMERCIAL_PAPER_ISSUANCE_25_09_2026`, page 1
  (Regulation 30 disclosure, CP issuance details table), citing the
  persisted `ExtractionUnit`.

## Classification against Snapshot A

- **Added**: the 2 facts above, plus 5 registered documents (3 of which —
  both Trading Window closures and the Reg. 74(5) certificate — deliberately
  produced no Fact; routine compliance filings, exactly per instruction not
  to generate artificial Facts merely because a document is new).
- **Corroborated**: the CP issuance's IND A1+ rating is the *same* India
  Ratings assignment already captured in Snapshot A's current
  `credit_rating_status` fact — Snapshot B shows that rating in genuine
  real-world use by an actual institutional lender, not just on paper.
- **Contradicted**: none. The VAT figure's aggregator-vs-primary-source
  variance is a precision refinement, not a disagreement between sources.
- **Superseded**: none newly superseded in this pass (the one supersession
  in the corpus — `credit_rating_status` — happened during Snapshot A's own
  closeout and is unchanged here).
- **Unchanged**: **IKIO's Research View is identical to Snapshot A's
  frozen state** — 8 facts, nothing added, nothing altered. Both new IKIO
  documents were judged routine and correctly excluded from Fact
  derivation.

## Updated Research Views

### IKIO Technologies Limited — 8 current facts, unchanged from Snapshot A
`quarterly_total_revenue`, `quarterly_ebitda`, `quarterly_pat`,
`revenue_definition_discrepancy_note`, `ipo_net_proceeds_utilization_status`,
`statutory_auditor_governance_event`, `business_development_mou`,
`promoter_pledge_status`. See `snapshot_a_final_2026-10-07.md` for full
detail — nothing here changed.

### UNO Minda Limited — 10 current facts (8 from Snapshot A + 2 new), 1 historical
Snapshot A's 8 (`quarterly_total_revenue`, `quarterly_ebitda`,
`quarterly_pat`, `capacity_expansion_4w_seating`,
`minda_onkyo_acquisition_and_amalgamation`,
`capacity_expansion_september_2026`, `board_change_independent_director`,
`credit_rating_status` [current, superseding the Aug-17-only historical
fact]) **plus**:
- `tax_dispute_pune_vat_cst` (new)
- `commercial_paper_issuance` (new)

## Snapshot A → B research/thesis delta

- **Financial materiality of both new facts is low** relative to UML's
  scale (turnover in the thousands of crores): the VAT net demand (~Rs 1.14
  Cr) and the CP issuance (Rs 100 Cr, standard working-capital financing)
  are each immaterial on their own.
- **Thesis-relevant signal, not from the amounts but from what they
  confirm**: the CP issuance is a genuine real-world test of the India
  Ratings IND A1+ assignment already on record — an institutional lender
  (Kotak Mahindra Bank) actually transacted against it. The VAT order adds
  one data point to UML's compliance history; on its own it does not
  establish a pattern, but it is now the kind of fact this mechanism is
  built to accumulate and would flag if similar items recur.
- **The three routine filings correctly added zero thesis signal** — this
  is the mechanism working as designed, not a gap: Trading Window closures
  and DP compliance certificates are process housekeeping, and forcing a
  Fact from them would have been exactly the "artificial Fact from a
  routine filing" the instruction warned against.
- **IKIO's thesis picture is entirely unchanged** by this snapshot.

## The sixth genuine Category-3 item — explicitly still outside this boundary

**UNO Minda's Oct 6, 2026 half-yearly NCD/ISIN compliance report remains
unavailable and is NOT part of Snapshot B.** Checked during the prior
reconciliation pass: NSE's corporate-announcements API accepts `index=debt`
as a valid parameter (returns a clean empty list, not an error) but carries
nothing for UNOMINDA in this window. This is a genuine coverage gap in the
existing discovery route, not a transient block — `discovery.py` itself
scopes its design to equities (mainboard) and SME only; half-yearly
debenture-trustee compliance certificates live in NSE's separate,
ISIN-keyed debt-securities disclosure system, which this pipeline was never
built to query. Distinct from IKIO's "Oct 5" item, which was confirmed to
be a duplicate/artifact (not a real filing) and is correctly absent from
both the Category-3 inventory and this snapshot. Acquiring this sixth item
would require either a different, debt-specific discovery path (not yet
built) or manual sourcing — a decision for a future, separately-authorized
pass, not assumed here.
