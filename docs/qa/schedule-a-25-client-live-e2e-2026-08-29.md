# Schedule A — 25-client live end-to-end QA

Date: 2026-08-29
Environment: live ERISAPros application, live EyeLevel/GroundX extraction, FT Williams test account
Corpus: 25 different Schedule A PDFs from 25 ShareFile client test folders

## Executive result

- Uploaded to live ShareFile/ERISAPros: **25 / 25**.
- EyeLevel jobs completed: **25 / 25**.
- Filings safely routed to Review: **25 / 25**.
- Filings with confirmed semantic extraction defects: **at least 16 / 25**.
- Automatic Schedule A selections returned: **10 / 25**; only **6** were editable.
- Fully executed FT Williams updates with readback: **1** (Control Associates / Guardian).
- Fields confirmed by FT Williams readback in that update: **7 / 7**.
- Unrelated Schedule A records changed in the verified update: **0**.
- Manual/no-match scenario: **blocked safely** because the UI only offered an unrelated Guardian record and had no working “Create new Schedule A” choice.

Release verdict: **the review and FT Williams update safety controls work, but global automatic extraction is not yet production-safe.** Keep automatic sends disabled unless a reviewer confirms the source values and target Schedule A.

## Verified flows

### 1. Source-first extraction review

Each PDF was copied into an isolated QA corpus, rendered for visual inspection, uploaded, and compared with the EyeLevel result. EyeLevel completed every job, and every ambiguous case entered Review rather than being sent automatically.

### 2. Review action behavior

On the Kaiser test filing:

- `Keep Extracted` accepted only the selected row.
- A manual premium edit moved exactly one field to **Will Update FTW**.
- Restoring the extracted value removed that single update when it normalized to the FT Williams current value.
- `Keep Current` resolved only the selected field.
- No unrelated field moved between Action Required and Will Update.

Result: **passed**.

### 3. Automatic match and FT Williams update

Control Associates / Guardian was automatically matched to Schedule A sequence 2. The source values were reviewed, the first broker was matched to row 1, and the second broker was explicitly added as a new row.

FT Williams result:

- Update status: `UPDATE_SENT`.
- Attempted: **7** fields.
- Confirmed: **7** fields.
- Verification success: **true**.
- Verification mismatches: **none**.
- Persons covered read back as **173**.
- Policy year read back as **09/01/2024–08/31/2025**.
- Premium read back as **186243** (FT Williams whole-dollar normalization of 186242.73).
- Broker commission and fee rows were present after readback.
- Existing Cigna Schedule A sequence 1 remained byte-for-byte unchanged in the comparison snapshot.

Result: **passed**.

### 4. Manual/no-match safety

Elyria Foundry’s source is HCC Life Insurance Company. FT Williams returned one possible record: an unrelated Guardian Schedule A with match score 2. The system correctly did not auto-select it, but the UI only offered that record and did not provide a usable **Create new Schedule A** action.

Result: **safe block, product defect**. No incorrect FT Williams record was changed.

## Client-by-client report

| # | Client / source | EyeLevel source verdict | FT Williams result |
|---:|---|---|---|
| 1 | Advocates for Human Potential — Kaiser | Core carrier, identifiers, dates, persons, premium, and broker values were source-consistent; premium was correctly review-gated. | Auto-selected seq 1, editable. UI edit/Keep Extracted/Keep Current isolation passed; no send required for this tracer. |
| 2 | Affinity Group — Fidelity Security Life | Extraction completed; persons-covered interpretation remains uncertain and requires source review. | Plan lookup returned multiple/invalid identifiers; no safe update. |
| 3 | ALL Erection — Guardian Dental | **Defect:** carrier reduced to `of America.`; preparation/unrelated dates used instead of 01/01/2025–12/31/2025. | Auto-selected seq 1, editable, but broker confirmation and corrected extraction required. |
| 4 | American Securities — Guardian | **Defect:** carrier reduced to `Guardian`; date 03/16/2026 used as policy date. | Auto-selected seq 2, editable, but source correction required. |
| 5 | Barry L. Price — Unum | **Defect:** header label extracted as carrier, preparation date used, and fee/additional compensation rows were incomplete. | Auto-selected seq 2, editable, but not safe to send uncorrected. |
| 6 | Brandeis — Zurich | Main identity/dates were found; compensation grouping remains review-sensitive. | Current-year 2025 FT Williams filing missing; Bring Forward required. |
| 7 | BTIG — MetLife | Extraction completed but premium/compensation classification requires reviewer confirmation. | Auto-selected seq 7; FT Williams filing locked/signed/accepted. |
| 8 | Byrna — Equitable | Extraction completed; amount interpretation remains uncertain. | Sponsor EIN/plan number missing for plan lookup. |
| 9 | Camino Health Center — Anthem | **Defect:** carrier contaminated by labels, preparation date used, premium overstated, and multiple coverage groups collapsed. | No safe auto-match. Highest score is not sufficient evidence; do not send collapsed data. |
| 10 | Community Legal Aid SoCal — MetLife | **Defect:** fees copied from commissions (17,027 instead of source total 2,500). | Required 2024 FT Williams filing missing; Bring Forward required. |
| 11 | Control Associates — Guardian | Source values and two broker rows were confirmed. | **Full E2E passed:** seq 2 updated; 7/7 fields verified; sibling seq 1 unchanged. |
| 12 | Crest Discount Foods — Lincoln | Core identity and 10/01/2024–09/30/2025 dates were extracted; review still required. | Current-year Schedule A unavailable even though plan was editable. |
| 13 | Kraft Power — Standard | **Defect:** three coverage/contract groups were collapsed into one result. | No safe match and FT Williams filing locked. |
| 14 | Elyria Foundry — HCC Life | **Defect:** clear 01/2025–12/2025 report dates were omitted. Other key values were present. | Only unrelated Guardian seq 1 offered (score 2); create-new path missing. No mutation performed. |
| 15 | ERH / JM Texas — MetLife | Extraction completed but identity and repeating compensation rows require confirmation. | No safe match; FT Williams filing locked. |
| 16 | Framestore — Optum | **Defect:** carrier and policy dates were not reliably mapped from the source package. | Candidate UHC record had different EIN/contract; unsafe to select. |
| 17 | Mastery Logistics — Unum plus old Cigna pages | **Defect:** mixed current and unrelated historical pages contaminated document semantics; core output incomplete. | Required current-year FT Williams filing missing. |
| 18 | Microbest — UHC | **Defect:** core Schedule A identity values were incomplete in the mapped output. | Plan lookup returned multiple/invalid identifiers. |
| 19 | HMR Veteran Services — Sun Life | **Defect:** `EIN (Insurance Carrier)` used as carrier, `SEE` used as contract, and broker/amount groups were misclassified. | Multiple plan matches; no safe update. |
| 20 | Housing Counseling Services — Lincoln | Core carrier and dates were found, but source/plan identifiers were insufficient for deterministic lookup. | Sponsor EIN/plan number missing. |
| 21 | Hyland Software — Guardian | **Defect:** core mapped result was incomplete; supplemental compensation needs explicit row handling. | Auto-selected seq 1; FT Williams filing locked. |
| 22 | Ideal Clamp — BlueRe | **Defect:** source broker commission of 81,423.75 was returned as zero. | Auto-selected seq 2; FT Williams filing locked. |
| 23 | Ingersoll Cutting Tool — New York Life | **Defect:** persons-covered and compensation aggregation were incomplete/ambiguous. | Auto-selected seq 5; FT Williams filing locked. |
| 24 | Inland Valley Drug — Anthem | **Defect:** carrier contaminated, preparation date substituted, and multiple coverage groups collapsed. | No safe match, plan-year conflict, and FT Williams filing locked. |
| 25 | Jane Street — Guardian | **Defect:** carrier reduced to `of America.` and preparation/unrelated dates used. | Auto-selected seq 3, editable, but not safe until source corrections and broker confirmation. |

## Automated regression evidence

- Backend semantic extraction, Field Rule/alias, QA, and FT Williams review tests: **133 passed**.
- Frontend review table and guided workflow: **passed**.
- FT Williams failure diagnostics: **passed**.
- Field Rules client/new-field flow: **passed**.
- TypeScript check: **passed**.

These tests prove the configured rule and UI plumbing works. They do not prove that EyeLevel chose the correct table row or section in every layout; the live corpus exposed that remaining semantic gap.

## Final assessment

The update transaction itself is reliable when the reviewer supplies source-correct values and the target Schedule A identity is safe. The main unresolved issue is upstream semantic extraction: labels, preparation dates, combined amounts, multiple coverage groups, and repeating broker rows are still sometimes mapped to the wrong schema fields.

The separate defect register is `schedule-a-25-client-live-e2e-defects-2026-08-29.md`.

## Evidence artifacts

- `tmp/schedule_a_qa_25_20260829/corpus_manifest.json`
- `tmp/schedule_a_qa_25_20260829/source_audit.json`
- `tmp/schedule_a_qa_25_20260829/upload_results.json`
- `tmp/schedule_a_qa_25_20260829/live_core_summary.json`
- `tmp/schedule_a_qa_25_20260829/live_match_summary.json`
- `tmp/schedule_a_qa_25_20260829/live_results_before_updates.json`
- `tmp/schedule_a_qa_25_20260829/live_results.json`
