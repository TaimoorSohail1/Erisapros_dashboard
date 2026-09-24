# All-client Schedule A QA report — 2026-08-28

## Executive result

**Release verdict: FAIL / live FT Williams updates paused.**

The system is not yet safe for an all-client live update run. The first controlled live upload exposed two release-blocking defects:

1. The AI extraction provider failed and production silently used the local PDF fallback, which returned materially incorrect values.
2. When no direct FT Williams plan match was found, the worker began probing all 3,352 accessible plans. Two duplicate pilot records caused roughly 6,704 potential FT Williams requests and left both filings stuck at `QUERYING_FTW_CURRENT`.

Further uploads and FT Williams sends were stopped to avoid multiplying incorrect data or excessive requests. No client Schedule A was updated in FT Williams during this run.

## Scope and evidence

- 64 eligible client packages were identified from the live ShareFile index.
- One representative Schedule A source was selected per client.
- 62 sources were downloaded and audited locally.
- 2 indexed ShareFile sources returned HTTP 404 and were unavailable.
- First pages of the 62 available PDF sources were rendered and visually reviewed across four contact sheets.
- One Affinity Group Schedule A was uploaded as a controlled live pilot.
- A new comparison-only Field Rule and aliases were tested as an unpublished draft.
- Keep Extracted, Keep Current, manual edit, best-match/manual-match, multiple-broker review, FT Williams send, and selected-Schedule-A isolation could not be completed because the pilot never reached Review.

This audit covered PDFs, insurer letters, carrier-specific worksheets, table-heavy reports, and one DOCX-backed source. The formats vary substantially.

## Controlled live pilot

Client: **Affinity Group, Inc. TEST**

Source evidence:

- Persons covered: **171**
- Premium: **$6,611.92**
- Broker: **NFP Corporate Services NY**
- Broker amount: **$1,932.21**

Production result:

- Provider: `Local PDF parser fallback`
- Persons covered: **143,740** — incorrect
- Premium: **6,612** — rounded
- Broker rows: **0** — missing
- Filing status: stuck at `QUERYING_FTW_CURRENT`
- FT Williams lookup: fallback scan across **3,352 plans**

The current local working-tree parser did better on persons covered (`171`) but still returned zero broker rows, so this is not only a deployment-version issue.

## All-client preliminary extraction audit

Legend:

- **Candidate**: core local fallback values looked plausible; live confirmation was blocked.
- **Partial**: some useful values were found, but important fields or broker rows were missing.
- **Fail**: no usable values or clear semantic/layout mis-extraction.
- **Unavailable**: stale ShareFile index entry returned 404.

| # | Client | Result | Main observation |
|---:|---|---|---|
| 1 | ALL Erection & Crane Rental Corp. | Fail | Guardian letter parsed carrier/date text incorrectly. |
| 2 | Advocates for Human Potential, Inc. | Fail | No key Schedule A fields extracted. |
| 3 | Affinity Group, Inc. | Fail | Live persons covered was wrong; visible broker row was missed. |
| 4 | AlphaSights TEST | Unavailable | Indexed ShareFile source returned 404. |
| 5 | American Securities LLC | Fail | Contract and policy dates were misread. |
| 6 | BTIG LLC | Candidate | Core values and one broker row looked plausible. |
| 7 | Barry L. Price Rehabilitation Center, Inc. | Fail | Generic carrier/date text was misclassified. |
| 8 | Brandeis University | Partial | Core data looked plausible; broker rows were missing. |
| 9 | Byrna Technologies, Inc. | Candidate | Core values looked plausible; live confirmation blocked. |
| 10 | Camino Health Center | Fail | Anthem letter headings were extracted as values. |
| 11 | Community Legal Aid SoCal | Candidate | Structured values found; commission/fee interpretation needs source confirmation. |
| 12 | Control Associates, Inc. | Partial | Premium/fee/dates found; important carrier/contract fields missing. |
| 13 | Crest Discount Foods, Inc. | Fail | No key Schedule A fields extracted. |
| 14 | ERH LLC, formerly JM Texas | Fail | Sun Life letter labels were extracted as values. |
| 15 | Eastern Panhandle Mental Health Center, Inc. | Fail | No key Schedule A fields extracted. |
| 16 | Elyria Foundry Company LLC | Fail | Guardian carrier and preparation date were misread. |
| 17 | Framestore, Inc. | Fail | Important fields were incomplete or misclassified. |
| 18 | Fund for the Public Interest, Inc. | Candidate | Core data looked plausible; source period is older and needs confirmation. |
| 19 | HMR Veteran Services, Inc. | Fail | Sun Life letter labels were extracted as values. |
| 20 | Harold Brothers Mechanical Contractors | Candidate | Structured core values looked plausible. |
| 21 | Homes for the Homeless, Inc. | Unavailable | Indexed ShareFile source returned 404. |
| 22 | Housing Counseling Services, Inc. | Fail | No key Schedule A fields extracted. |
| 23 | Hyde Group, Inc. | Candidate | Core data and multiple broker rows looked plausible. |
| 24 | Hyland Software, Inc. | Partial | Important carrier/contract fields were incomplete. |
| 25 | Ideal Clamp Products, Inc. | Partial | Core data looked plausible; broker rows were missing. |
| 26 | Ingersoll Cutting Tool Company, Inc. | Fail | No key Schedule A fields extracted. |
| 27 | Inland Valley Drug & Alcohol Recovery Services | Fail | Anthem letter headings were extracted as values. |
| 28 | JTB Americas LTD | Candidate | Core values and two broker rows looked plausible. |
| 29 | Jane Street Group LLC | Fail | Guardian carrier/date text was misread. |
| 30 | Kestra Financial, Inc. | Candidate | Core values and two broker rows looked plausible. |
| 31 | Kraft Power Corporation | Candidate | Core values and three broker rows looked plausible. |
| 32 | Lao Family Community Development | Fail | Preparation date was used for both policy dates. |
| 33 | Levain Bakery | Partial | DOCX-backed source; core values found but broker rows missing. |
| 34 | Mastery Logistics Systems, Inc. | Fail | No key Schedule A fields extracted. |
| 35 | Microbest, Inc. | Partial | Core values found; broker rows missing. |
| 36 | Midwest Hose & Specialty | Partial | Core values found; broker rows missing. |
| 37 | Nature's Path Foods USA, Inc. | Fail | No key Schedule A fields extracted. |
| 38 | New England Life Flight / Boston MedFlight | Fail | Policy dates were misread. |
| 39 | New York Yankees Partnership | Fail | Persons/premium and preparation date were misclassified. |
| 40 | O'Toole Distribution / Pella Windows | Candidate | Core values and three broker rows looked plausible. |
| 41 | Ohio Valley Stamping & Assemblies, Inc. | Fail | No key Schedule A fields extracted. |
| 42 | Onyx Medical / Elos Medtech | Candidate | Structured core values looked plausible. |
| 43 | OptimizeRX Corporation | Candidate | Core values and two broker rows looked plausible. |
| 44 | Oxford Biomedica US LLC | Fail | No key Schedule A fields extracted. |
| 45 | Path Robotics | Fail | Anthem letter headings were extracted as values. |
| 46 | Peerless Clothing International | Fail | No key Schedule A fields extracted. |
| 47 | Planters Bank and Trust Company | Candidate | Structured core values looked plausible. |
| 48 | Preferred Pump & Equipment, L.P. | Fail | No key Schedule A fields extracted. |
| 49 | R. H. White Companies, Inc. | Candidate | Core values and three broker rows looked plausible. |
| 50 | Red Thread | Fail | VSP layout concatenated carrier, address, and broker headings. |
| 51 | Rhythm Pharmaceuticals, Inc. | Fail | Generic carrier and report dates were misclassified. |
| 52 | Rollease, Inc. | Fail | No key Schedule A fields extracted. |
| 53 | Saint Elizabeth Community | Partial | Useful values found; carrier EIN/contract fields missing. |
| 54 | Salesloft, Inc. | Fail | No key Schedule A fields extracted. |
| 55 | Socure, Inc. | Fail | Carrier/NAIC/contract/persons missing; amount mapping was suspicious. |
| 56 | Special Service for Groups, Inc. | Fail | Preparation date was used for both policy dates. |
| 57 | The Advertising Council | Fail | Policy dates were incorrect/reversed. |
| 58 | The Boston Home, Inc. | Fail | VSP layout concatenated carrier and broker text. |
| 59 | The International Group | Fail | Sun Life letter labels were extracted as values. |
| 60 | Tilt Holdings, Inc. | Fail | VSP layout concatenated names and headings. |
| 61 | Titmouse, Inc. | Fail | Contract was parsed as `01`; broker rows were missing. |
| 62 | Tower Health | Fail | Only limited data was found and dates were wrong. |
| 63 | Worcester Community Action Council, Inc. | Fail | VSP layout concatenated names and headings. |
| 64 | WorldSprings Holdings LLC | Candidate | Structured Principal data and one broker row looked plausible. |

These are preliminary fallback-parser findings. A Candidate result is not a production pass until it completes live Review and FT Williams verification.

## Field Rules and alias test

**Pass, with no production mapping change.**

- Created an unpublished comparison-only draft for the discovered FTW `Broker` field.
- Added aliases: `Broker`, `Agent/Broker`, and `Insurance Broker`.
- Sample `Agent/Broker` matched the rule successfully.
- The rule remains Draft v1 and was not published, so it cannot change live extraction or FT Williams updates.
- An existing unrelated draft for `2a. Plan Administrator Name` was preserved.

## Checks not completed

The following must remain **Not Tested / Blocked**, not Pass:

- Keep Extracted moves only the selected field to Will Update.
- Keep Current does not create unrelated updates.
- Manual edits become Will Update when the field supports FT Williams updates.
- Best-match selection and manual Schedule A selection.
- Multiple broker/agent row matching, add-new, and preservation.
- FT Williams update of only the selected Schedule A.
- Post-send FT Williams verification and proof that other Schedule A records were unchanged.

The pilot never reached Review, so continuing these checks would have created more runaway FT Williams traffic.

## Exact defects and recommended fixes

### P0 — Unbounded FT Williams plan fallback

When the direct match fails, `_try_plan_ids_batch_lookup` iterates through all 3,352 accessible plans. This turns one filing into thousands of network calls.

Required fix:

- Build a bounded candidate list from EIN, plan number, year, carrier EIN, NAIC, contract, and policy dates.
- Stop after a small maximum candidate count and time budget.
- Add a circuit breaker, request-level timeout, cancellation, and single-flight/cache for shared plan lookups.
- Return an explicit manual-selection state instead of scanning every plan.

### P0 — AI extraction failure silently falls back to unsafe values

The live extraction provider failed, but the fallback output was treated as usable even though it converted `171` persons covered into `143,740` and omitted the broker.

Required fix:

- Surface provider failure and mark the filing for review.
- Fail closed when required-field confidence or semantic checks fail.
- Never send fallback data automatically without source validation.

### P0 — Layout-independent validation is insufficient

The parser commonly treats headings, preparation dates, NAIC values, and narrative text as field values.

Required fix:

- Extract into a canonical Schedule A schema with source spans and confidence per field.
- Use field-type constraints and cross-field validation: EIN/NAIC formats, date ordering, totals vs broker rows, persons covered ranges, and contract patterns.
- Add layout-family fixtures for insurer letters and table reports, while keeping the schema and aliases data-driven.
- Require human review when source evidence is ambiguous.

### P1 — Broker rows are inconsistently extracted

Visible broker rows were frequently missed, including the live pilot.

Required fix:

- Detect repeating row groups by spatial/table structure, not only labels.
- Reconcile section totals against broker row totals.
- Preserve each broker as a separate row with its own name, address, commission, fee, purpose, and organization code.

### P1 — Stale ShareFile index entries

Two selected indexed sources returned 404.

Required fix:

- Mark 404 items deleted during scan/download.
- Exclude unavailable items from candidate selection and surface a repair action.

## Safety cleanup and final operational state

- The runaway worker was temporarily scaled to zero.
- The unique QA-prefixed ShareFile upload was moved to Recycle Bin and is recoverable.
- Only the manual QA file version from 28 Aug was deleted; the client's two earlier versions from 26 Aug were preserved.
- Both stuck QA filings were logically removed from the ERISAPros dashboard.
- The ShareFile worker was restored to desired/running `1/1`.
- Final FT Williams request count observed for the last monitoring interval: `0`.
- No product code was changed or deployed during this QA run.

## Release gates

Before resuming the all-client live run:

1. Fix and regression-test the bounded FT Williams matching flow.
2. Make extraction-provider failure explicit and fail closed on unsafe fallback results.
3. Add semantic validation and broker-row reconciliation tests using the 62 downloaded representative sources.
4. Re-run one controlled live pilot through Review.
5. Verify Keep Extracted, Keep Current, manual edit, match selection, multiple brokers, and selected-Schedule-A isolation.
6. Only then expand to the remaining clients in small batches with request-volume monitoring.
