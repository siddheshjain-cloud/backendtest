# Research Brain Pilot — Snapshot A, Final/Full Research View (frozen 2026-10-07)

**Status: FROZEN.** This is the complete, final Snapshot A artifact — the full
Research View derived across the entire 87-document boundary, all
ambiguities investigated and closed out, ready to serve as the baseline for
Snapshot B. It supersedes nothing: the original pilot record,
`docs/research_brain/snapshot_a_2026-10-07.md` (the 6-document IKIO/UNO
Minda proof-of-mechanism run that came first), is preserved unchanged
alongside this one. This document is the complete picture; that one remains
the historical record of how the mechanism was first proven.

## Boundary — unchanged from the original freeze

Scoped to documents already registered in the M1 Document Library for IKIO
Technologies Limited and UNO Minda Limited only. No Manifest/Drive-only
documents, no newly-discovered documents.

- **IKIO Technologies Limited** (`c8b839a8-5319-498f-b480-0d6971299094`): 41
  registered documents, `document_date` range 2026-06-27 to 2026-09-11.
- **UNO Minda Limited** (`9cbdabe1-3008-45dc-8ef6-dad859946990`): 46
  registered documents, `document_date` range 2026-06-20 to 2026-09-15.
- **Combined document set**: 87 documents.
- **`snapshot_a_document_set_sha256`**: `d1f1a4a7daaeb16be8182be2c2b97eeaea199e5ef93cac2e8fe3b759e100d577`
  — unchanged since the original freeze; this run added Evidence/Facts, it
  never touched the document boundary itself.

**The seven Category-3 disclosures (IKIO: 3, UNO Minda: 4 — Trading Window
closures, a VAT demand disclosure, a securities issuance, a half-yearly NCD
compliance report) identified during reconciliation remain explicitly
OUTSIDE this boundary.** They were never read, never ingested, never
contributed any Evidence or Fact in this Snapshot. They stay held pending
separate, explicit authorization.

## Database state at freeze

| | |
|---|---|
| `PRAGMA integrity_check` | `ok` |
| Alembic revision | `20261007_01` |
| `document` | 1,246 (unchanged since the Phase 2 backfill) |
| `company` | 69 (unchanged) |
| `ticker` | 69 (unchanged) |
| `extraction_run` | 111 |
| `extraction_unit` | 2,115 (100% page-level coverage of all 87 documents) |
| `evidence` | 33 |
| `extracted_fact` | 17 (16 current + 1 superseded/historical) |
| `fact_evidence` | 33 |

## Extraction quality (the mechanical page-text pass, 87/87 documents)

- **Coverage: 87/87 documents, 2,115 pages, zero failures** (no
  `DOWNLOAD_FAILED`/`HASH_MISMATCH`/`EXTRACTION_FAILED`; every document's
  bytes were hash-verified against the already-registered
  `document_content.sha256` before extraction).
- **One document captured zero text**: `IKIO_REG30_SECURITIES_20_07_2026` (1
  page, 192KB, 7 embedded images, no text layer). Diagnosed directly by
  rendering the page: a genuinely scanned/signed SEBI Reg. 31(4) promoter
  declaration. Not a pipeline defect — OCR would be required to read it
  mechanically, which stays explicitly out of scope. The empty
  `ExtractionUnit` row is preserved as the honest record of this; its
  content was separately, manually transcribed for the
  `promoter_pledge_status` fact (see below), with explicit provenance back
  to the scanned original.
- A handful of other documents have a few sparse/near-empty trailing pages
  (blank dividers at the end of the two large Annual Reports — IKIO FY2026:
  292pp, UNO Minda FY2025: 585pp, FY2026: 627pp — and a few chart-heavy
  slides in both Investor Presentations). Expected, not systemic; 86/87
  documents have substantial, healthy text (median ~1,974 chars/page).

## Facts — full lineage (17 rows: 16 current, 1 historical/superseded)

### IKIO Technologies Limited (8 current facts)

| fact_type | period | as_of_date | value_type |
|---|---|---|---|
| `quarterly_total_revenue` | Q1 FY2027 | 2026-08-08 | NUMERIC — 1,693 INR Million |
| `quarterly_ebitda` | Q1 FY2027 | 2026-08-08 | NUMERIC — 220 INR Million |
| `quarterly_pat` | Q1 FY2027 | 2026-08-08 | NUMERIC — 110 INR Million |
| `revenue_definition_discrepancy_note` | Q1 FY2027 | 2026-08-08 | TEXT — closed-out ambiguity, see below |
| `ipo_net_proceeds_utilization_status` | Q1 FY2027 | 2026-08-13 | TEXT |
| `statutory_auditor_governance_event` | Q1 FY2027 | 2026-07-28 | TEXT |
| `business_development_mou` | Q1 FY2027 | 2026-08-10 | TEXT |
| `promoter_pledge_status` | FY2026 | 2026-04-04 | TEXT — manually transcribed, scanned source |

### UNO Minda Limited (8 current facts + 1 historical)

| fact_type | period | as_of_date | value_type | status |
|---|---|---|---|---|
| `quarterly_total_revenue` | Q1 FY2027 | 2026-08-04 | NUMERIC — 5,557 INR Crore | current |
| `quarterly_ebitda` | Q1 FY2027 | 2026-08-04 | NUMERIC — 572 INR Crore | current |
| `quarterly_pat` | Q1 FY2027 | 2026-08-04 | NUMERIC — 296 INR Crore | current |
| `capacity_expansion_4w_seating` | Q1 FY2027 | 2026-07-07 | TEXT | current |
| `minda_onkyo_acquisition_and_amalgamation` | Q1 FY2027 | 2026-08-04 | TEXT | current |
| `capacity_expansion_september_2026` | FY2027 | 2026-09-14 | TEXT | current |
| `board_change_independent_director` | FY2027 | 2026-09-01 | TEXT | current |
| `credit_rating_status` (`c3b19b30-…`) | Q1 FY2027 | 2026-08-17 | TEXT | **superseded** — preserved unmodified, queryable by id |
| `credit_rating_status` (`d6fd6c40-…`, supersedes `c3b19b30-…`) | FY2027 | 2026-09-14 | TEXT | **current** |

This is the pilot's point-in-time/supersession mechanism exercised on real
data for the first time: the Aug-17-only credit-rating view was never
edited or deleted — a new fact was appended naming it as superseded, and
`get_company_facts()` correctly surfaces only the current one while the
original stays fully intact and independently queryable.

## Evidence provenance — all 33 rows, every fact traceable to a real document

| Fact | Evidence source document(s) |
|---|---|
| IKIO `quarterly_total_revenue` | IKIO_CONCALL_14_08_2026; IKIO_PRESENTATION_Q1_FY2027 |
| IKIO `quarterly_ebitda` | IKIO_CONCALL_14_08_2026; IKIO_PRESENTATION_Q1_FY2027 (visually verified) |
| IKIO `quarterly_pat` | IKIO_CONCALL_14_08_2026; IKIO_PRESENTATION_Q1_FY2027 (visually verified) |
| IKIO `revenue_definition_discrepancy_note` | IKIO_RESULTS_Q1_FY2027; IKIO_PRESENTATION_Q1_FY2027 |
| IKIO `ipo_net_proceeds_utilization_status` | IKIO_RESULTS_Q1_FY2027; IKIO_REG30_MONITORING_AGENCY_REPORT_13_08_2026 |
| IKIO `statutory_auditor_governance_event` | IKIO_REG30_REGULATIONS_RESIGNATION_M_S_BGJC_ASSOCIATES_28_07_2026 (×2, main letter + auditor's own letter) |
| IKIO `business_development_mou` | IKIO_REG30_EXECUTION_MEMORANDUM_UNDERSTANDING_MOU_BETWEEN_ROYALUX_10_08_2026 |
| IKIO `promoter_pledge_status` | IKIO_REG30_SECURITIES_20_07_2026 (manually transcribed from rendered scan) |
| UNOMINDA `quarterly_total_revenue` | UNOMINDA_CONCALL_Q1_FY2027; UNOMINDA_PRESENTATION_Q1_FY2027 |
| UNOMINDA `quarterly_ebitda` | UNOMINDA_CONCALL_Q1_FY2027; UNOMINDA_PRESENTATION_Q1_FY2027 (visually verified) |
| UNOMINDA `quarterly_pat` | UNOMINDA_CONCALL_Q1_FY2027; UNOMINDA_PRESENTATION_Q1_FY2027 (visually verified) |
| UNOMINDA `capacity_expansion_4w_seating` | UNO_REG30_CAPACITY_ADDITION_07_07_2026; UNOMINDA_PRESS_RELEASE_2026-07-07_4W_SEATING_GREENFIELD_320CR |
| UNOMINDA `minda_onkyo_acquisition_and_amalgamation` | UNO_REG30_UPDATE_ACQUISITION_30_07_2026; UNO_REG30_CORPORATE_ACTION_AMALGAMATION_MERGER_DEMERGER_04_08_2026 (×2) |
| UNOMINDA `capacity_expansion_september_2026` | Uno Minda Ltd_REG30_2026-09-14 (×2); Uno Minda Ltd_PRESS_RELEASE_2026-09-15 |
| UNOMINDA `credit_rating_status` (superseded) | UNO_REG30_CREDIT_RATING_17_08_2026 |
| UNOMINDA `credit_rating_status` (current) | UNO_REG30_CREDIT_RATING_14_09_2026 (×2); UNO_REG30_CREDIT_RATING_17_08_2026 (re-cited for continuity) |
| UNOMINDA `board_change_independent_director` | UNO_REG30_CHANGE_DIRECTORATE_01_09_2026 |

29 of 33 Evidence rows cite a specific `ExtractionUnit` (persisted
page-level content) via `source_extraction_unit_id`; the 4 that don't are
the original Snapshot A pilot's `quarterly_total_revenue` evidence for both
companies, created before the `ExtractionUnit` layer existed (Task 5,
2026-10-07, pre-Task-6) — correctly null, no backfill implied or performed.

## The four ambiguity closeouts

1. **IKIO EBITDA/Cash PAT column swap — classification: resolved extraction
   error.** Caught by rendering the actual presentation slide before
   committing anything; the garbled chart-text extraction had the two
   metrics' columns in a misleading order. No residual risk in the
   committed facts.

2. **IKIO Revenue from Operations (₹1,602.89m, statutory) vs. management
   Total Revenue (₹1,693m) — classification: genuine source/definition
   discrepancy, not reconcilable from the available corpus.** Investigated,
   not guessed: the statutory quarterly filing discloses exactly one Ind AS
   108 segment ("Manufacturing of LED Lighting"), with no segment-level
   revenue breakdown and no "Other income" composition note anywhere in the
   corpus. The "Other Business"/"Home Lighting ODM" split underlying the
   ₹1,693m figure is a management-reporting construct with no formal bridge
   back to the audited line. Both figures are preserved; the discrepancy
   itself is now a queryable fact
   (`revenue_definition_discrepancy_note`), not just external commentary.

3. **UNO Minda's remaining September credit-rating disclosures —
   classification: incomplete corpus review, now completed within the
   boundary.** Reviewing them surfaced two things: (a) India Ratings
   assigned a fresh IND A1+ to UNO Minda's Commercial Paper programme on
   Sept 14, continuing coverage after ICRA's own CP rating was reaffirmed
   *and withdrawn* on Aug 17 — not a downgrade or a coverage gap, recorded
   as a proper point-in-time supersession; and (b) a significant capex
   announcement (4 DPRs, ₹1,415 crore combined) bundled into the same
   generically-named Sept 14 filing, missed in the original pass because
   its filename carried no topical hint (unlike the descriptively-named
   July capacity filing) — now recorded as `capacity_expansion_september_2026`,
   corroborated by the already-in-corpus Sept 15 press release.

4. **NSE Sustainability ESG score (68/100, Sept 3) — classification: valid
   single-source fact, deliberately excluded from `credit_rating_status`.**
   A different rating type (sustainability score, unsolicited by the
   company) — noted explicitly in the superseding credit-rating fact's own
   text so the exclusion is documented, not silent, but not recorded as its
   own Fact (judged not to carry comparable research weight to the other 16
   facts; revisit if a future snapshot finds ESG scoring materially
   relevant).

## Genuine unresolved items, preserved as such (not forced)

- **IKIO's ₹1,602.89m vs ₹1,693m revenue discrepancy** — confirmed
  unreconcilable from this corpus; both figures stand, flagged, not
  collapsed into one.
- **IKIO's promoter pledge status** — single-sourced (the one scanned
  declaration); the underlying share count is corroborated elsewhere, but
  the pledge claim itself is not. Stated explicitly in the fact's own text.
- **UNO Minda's credit rating** — current as of Sept 14, 2026 within the
  87-document boundary; no claim is made about anything after that date or
  outside this boundary.
- **IKIO's business development MoU (Saudi Arabia)** — single-sourced, no
  corroborating document exists in the corpus; recorded as such.

## Relationship to the seven held Category-3 disclosures

Explicitly reconfirmed at this freeze: none of the seven — IKIO's Trading
Window closure (Sept 24), Trading Window intimation (Oct 5), and Reg. 74(5)
DP compliance certificate (Oct 6); UNO Minda's VAT demand disclosure (Sept
24), Trading Window closure (Sept 24), Commercial Paper issuance (Sept 25),
and half-yearly NCD compliance report (Oct 6) — were read, ingested, or used
as source for any Evidence or Fact in this Snapshot. They remain the
Category-3 inventory, held pending separate explicit authorization, entirely
outside this freeze's boundary and database footprint.
