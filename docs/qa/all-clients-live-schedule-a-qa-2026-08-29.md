# All-client Schedule A QA report — 2026-08-29

## Executive verdict

**FAIL for unattended all-client extraction; PASS for the tested review/rule regression suite.**

The application workflow is stable under automated regression tests, but the real-document extraction layer is not yet reliable across the client corpus. Only **16 of 62** representative Schedule A documents produced all seven core insurance fields in the local production parser, and only **20 of 62** produced any structured broker row. The live dashboard likewise showed most filings in **Needs Review** rather than Ready.

No FT Williams update was sent during this extraction-focused run. This prevented incomplete or semantically wrong values from reaching FT Williams.

## Scope

- 64 client folders were identified from the ShareFile test area.
- 62 unique representative Schedule A sources were available and assessed.
- 2 indexed sources were unavailable: **AlphaSights TEST** and **Homes for the Homeless, Inc.**
- 48 ShareFile upload attempts reported UI success.
- The last stable dashboard snapshot contained **36 filings across 33 companies**.
- 14 client folders did not expose a usable 2025 Schedule A destination through the tested folder traversal.
- Source PDFs were checked before comparing the parser output.
- Field Rule alias/new-field behavior and review-flow regression tests were run.

## Results summary

| Check | Result | Evidence |
|---|---|---|
| Seven core Schedule A fields present | **16/62 (25.8%)** | Local production parser corpus run |
| Partial core extraction | **32/62 (51.6%)** | At least one core field found, but one or more missing |
| No usable Schedule A fields | **14/62 (22.6%)** | Zero extracted fields |
| At least one broker row | **20/62 (32.3%)** | Structured broker rows |
| Multiple broker rows | **12/62 (19.4%)** | 2–3 rows preserved in parser output |
| Average extracted field count | **9.1** | Across all 62 documents |
| Backend regression suite | **PASS** | 140 tests passed |
| Frontend review-table workflow | **PASS** | Review labels, column order, and guided workflow passed |
| Field Rules client creation | **PASS** | New mapped field creation test passed |
| FT Williams failure diagnostics | **PASS** | Diagnostic UI regression passed |

Core fields are carrier name, carrier EIN, NAIC, contract/policy number, persons covered, policy-year beginning date, and policy-year ending date. A structural pass does **not** prove semantic correctness; several complete-looking results still require source validation.

## Missing-field concentration

| Core field | Missing documents |
|---|---:|
| Carrier EIN | 36 |
| Persons covered | 35 |
| NAIC | 29 |
| Contract/policy number | 26 |
| Carrier name | 20 |
| Policy-year beginning date | 15 |
| Policy-year ending date | 15 |

The failure is therefore systemic. It is not limited to one PDF or one broker layout.

## Live dashboard observations

Last stable live snapshot:

- 33 companies / 36 filings were visible.
- 32 filings were in **Needs Review**.
- Only **Kraft Power Corporation** and **Hyde Group Inc.** were Ready with 57/59 fields and no high-priority issue.
- **Titmouse** was still processing at the snapshot time.
- Examples with severe high-priority misses included Advocates (18), Peerless (13), HMR (10), Advertising Council (10), and several clients with 9.
- Rollease's detail page showed core carrier, contract, persons-covered, broker, commission, and related fields as Not found.
- FT Williams failures were 0 because no send was attempted in this phase.

The production UI did not expose reliable provider provenance for every filing. Consequently, this report attributes failures to the **production extraction pipeline**, not specifically to EyeLevel or the local fallback unless the provider was explicitly shown.

## ShareFile intake findings

The uploader worked for normal `5500 Filing > 2025 Filing > Schedule A's` folders and several naming variants, but folder discovery is not robust enough for all clients.

### Not testable through a valid 2025 destination

1. Camino Health Center — only 2018–2024 visible.
2. Community Legal Aid SoCal — extra wrapper hierarchy prevented the automated traversal from completing.
3. Crest Discount Foods — only 2020–2024 visible.
4. Eastern Panhandle Mental Health Center — no child filing folders visible.
5. Fund for the Public Interest — only 2020–2024 visible.
6. Harold Brothers — only 2022–2024 visible.
7. Ingersoll Cutting Tool — ambiguous repeated `5500 Filing` labels blocked deterministic selection.
8. Lao Family Community Development — only 2023–2024 visible.
9. Midwest Hose & Specialty — only 2020–2024 visible.
10. Nature's Path Foods — only 2020–2024 visible.
11. O'Toole Distribution — only 2023–2024 visible.
12. Onyx Medical — only 2018–2024 visible.
13. OptimizeRX — only 2023–2024 visible.
14. Planters Bank and Trust — only 2020–2024 visible.

Two older-year paths—Inland Valley and New England Life Flight—were briefly reached by the flexible folder fallback before it was hardened to require a 2025 segment. No QA-prefixed file was visible in the Inland Valley target when checked, so no unrelated client file was deleted. These two exact QA artifacts should remain on the cleanup watchlist; broad deletion is not appropriate.

## Per-client extraction matrix

Legend: **Complete** = all seven core fields structurally present; **Partial** = some fields present; **Fail** = zero extracted fields. `1a`–`1g` identify missing core fields. Broker count is the number of structured broker rows.

| Client | Result | Fields | Brokers | Missing core / observation |
|---|---:|---:|---:|---|
| Affinity Group | Complete | 13 | 1 | Structural pass |
| ALL Erection & Crane Rental | Partial | 7 | 1 | 1d, 1e; Guardian semantics need validation |
| Advocates for Human Potential | Fail | 0 | 0 | All core fields missing |
| American Securities | Partial | 8 | 0 | 1b, 1e |
| BTIG | Complete | 13 | 1 | Structural pass |
| Barry L. Price Rehabilitation Center | Partial | 6 | 0 | 1b, 1c; generic carrier/date semantics need validation |
| Brandeis University | Partial | 10 | 0 | 1e; broker rows missing |
| Byrna Technologies | Complete | 10 | 0 | Core complete; broker source still needs validation |
| Camino Health Center | Partial | 7 | 1 | 1b, 1c, 1d, 1e; Anthem headings were misread |
| Community Legal Aid SoCal | Complete | 14 | 2 | Structural pass; live 2025 upload blocked by wrapper hierarchy |
| Control Associates | Partial | 4 | 2 | 1a–1e missing |
| Crest Discount Foods | Fail | 0 | 0 | All core fields missing |
| ERH / formerly JM Texas | Partial | 7 | 0 | 1b, 1c, 1e; Sun Life labels were misread |
| Eastern Panhandle Mental Health Center | Fail | 0 | 0 | All core fields missing |
| Elyria Foundry | Partial | 7 | 1 | 1d, 1e; Guardian semantics need validation |
| Framestore | Partial | 6 | 0 | 1b, 1c, 1e–1g missing |
| Fund for the Public Interest | Partial | 12 | 1 | 1e; source period needs validation |
| HMR Veteran Services | Partial | 7 | 0 | 1b, 1c, 1e; Sun Life labels were misread |
| Harold Brothers | Complete | 10 | 0 | Structural pass; no valid 2025 upload path found |
| Housing Counseling Services | Fail | 0 | 0 | All core fields missing |
| Hyde Group | Complete | 32 | 2 | Live Ready; multiple brokers preserved |
| Hyland Software | Partial | 4 | 2 | 1a–1e missing |
| Ideal Clamp Products | Partial | 26 | 0 | 1e; broker rows missing |
| Ingersoll Cutting Tool | Fail | 0 | 0 | All core fields missing; folder selector ambiguity also found |
| Inland Valley Drug & Alcohol Recovery | Partial | 7 | 1 | 1b–1e missing; Anthem headings were misread |
| JTB Americas | Partial | 13 | 2 | 1e missing; two brokers preserved |
| Jane Street Group | Partial | 7 | 2 | 1d, 1e; Guardian semantics need validation |
| Kestra Financial | Partial | 11 | 2 | 1e missing; two brokers preserved |
| Kraft Power | Complete | 32 | 3 | Live Ready; three brokers preserved |
| Lao Family Community Development | Complete | 13 | 0 | Preparation date was previously mistaken for policy dates |
| Levain Bakery | Fail | 0 | 0 | DOCX-backed content with PDF extension; all core fields missing |
| Mastery Logistics | Fail | 0 | 0 | All core fields missing |
| Microbest | Complete | 14 | 0 | Core complete; broker rows missing |
| Midwest Hose & Specialty | Complete | 14 | 0 | Core complete; broker rows missing |
| Nature's Path Foods | Fail | 0 | 0 | All core fields missing |
| New England Life Flight / Boston MedFlight | Partial | 4 | 0 | 1a–1c missing; date semantics need validation |
| New York Yankees Partnership | Partial | 5 | 0 | 1a, 1b, 1d missing; persons/premium semantics suspicious |
| O'Toole Distribution / Pella Windows | Complete | 32 | 3 | Structural pass; three brokers preserved |
| Ohio Valley Stamping & Assemblies | Fail | 0 | 0 | All core fields missing |
| Onyx Medical / Elos Medtech | Partial | 26 | 0 | 1e; broker rows missing |
| OptimizeRX | Complete | 32 | 2 | Structural pass; two brokers preserved |
| Oxford Biomedica | Fail | 0 | 0 | All core fields missing |
| Path Robotics | Partial | 4 | 0 | 1b–1e missing; Anthem headings were misread |
| Peerless Clothing | Fail | 0 | 0 | All core fields missing |
| Planters Bank and Trust | Complete | 10 | 0 | Structural pass; no valid 2025 upload path found |
| Preferred Pump & Equipment | Fail | 0 | 0 | All core fields missing |
| R. H. White Companies | Complete | 17 | 3 | Structural pass; three brokers preserved |
| Red Thread | Partial | 11 | 0 | 1b missing; VSP text concatenation |
| Rhythm Pharmaceuticals | Partial | 6 | 0 | 1b, 1c; generic report-date semantics suspicious |
| Rollease | Fail | 0 | 0 | All core fields missing; confirmed severe live review result |
| Saint Elizabeth Community | Partial | 7 | 0 | 1b, 1d missing |
| Salesloft | Fail | 0 | 0 | All core fields missing |
| Socure | Partial | 5 | 0 | 1a, 1c–1e missing; amount mapping suspicious |
| Special Service for Groups | Complete | 13 | 0 | Preparation-date semantics require validation |
| The Advertising Council | Partial | 12 | 3 | 1b, 1c; policy dates previously reversed |
| The Boston Home | Partial | 11 | 0 | 1b missing; VSP text concatenation |
| The International Group | Partial | 7 | 0 | 1b, 1c, 1e; Sun Life labels were misread |
| Tilt Holdings | Partial | 11 | 0 | 1b missing; VSP text concatenation |
| Titmouse | Partial | 10 | 0 | 1b, 1e; contract/broker semantics need validation |
| Tower Health | Partial | 3 | 0 | 1a–1d missing |
| Worcester Community Action Council | Partial | 11 | 0 | 1b missing; VSP text concatenation |
| WorldSprings Holdings | Complete | 13 | 1 | Structural pass |
| AlphaSights | Unavailable | — | — | Indexed source unavailable |
| Homes for the Homeless | Unavailable | — | — | Indexed source unavailable |

## Field Rules and aliases

**PASS in controlled draft/test scope.**

- An unpublished comparison-only `Broker` rule draft matched the aliases `Broker`, `Agent/Broker`, and `Insurance Broker`.
- The backend alias/new-field QA tests passed, including deterministic extraction from a newly added mapped alias and extraction-only handling for an unsupported FT Williams field.
- The frontend new Field Rule creation test passed.
- The draft was intentionally not published, so this QA run did not alter production mapping behavior.

This proves the rule infrastructure works. It does not compensate for missing source text/table detection; aliases can map detected text, but they cannot recover rows the document parser never segments correctly.

## Review/update workflow

Automated tests passed for:

- Keep Extracted and Keep Current controls being present in the intended review flow.
- Structured multiple-broker rows being shown below the flat field comparison and duplicate flat broker fields being hidden.
- Field Rule creation and extraction-only field behavior.
- FT Williams failure diagnostics.

A fresh live FT Williams send was deliberately not performed because the extraction corpus failed the release gate. Therefore selected-Schedule-A isolation and post-send comparison remain a later controlled FT Williams test, not a Pass in this report.

## Exact issues

### P0 — Layout-independent extraction is incomplete

Carrier letters, carrier worksheets, narrative reports, VSP layouts, Sun Life layouts, Anthem layouts, Guardian layouts, and office-document-backed files do not consistently map into the canonical Schedule A schema.

**Required solution:** route every document through format sniffing, layout/table segmentation, canonical field mapping, typed validation, and source-span/confidence evidence. Unknown layouts must fail closed to review rather than returning plausible-looking values.

### P0 — Semantic validation is too weak

The parser can structurally populate a field with a heading, preparation date, narrative number, or concatenated block.

**Required solution:** validate EIN, NAIC, contract pattern, date order/period, persons-covered range, premiums, and broker totals. Reconcile section totals with the sum of structured broker rows.

### P1 — Broker extraction coverage is low

Only 20/62 documents produced any broker row, although many sources visibly contain broker/agent data.

**Required solution:** detect repeating broker groups spatially and by table structure; preserve each broker independently with address, commissions, fees, purpose, and organization code.

### P1 — Intake folder discovery is brittle

Year and Schedule A folder names vary, and wrapper folders can appear. Exact label selection also fails when breadcrumb, heading, and folder share the same text.

**Required solution:** discover folder structure by item identity and parent ID, require a verified 2025 ancestor, support known naming variants, and stop safely when no valid 2025 destination exists.

### P1 — Extraction provenance is not visible enough

The reviewer cannot always see whether EyeLevel, local parsing, OCR, or fallback logic produced a value.

**Required solution:** show provider, source page/span, confidence, fallback reason, and validation warnings per field.

## Recommended next run

1. Convert all 62 sources into a permanent golden-fixture corpus with approved expected JSON.
2. Fix layout segmentation and semantic validation by failure family, not by filename/client.
3. Require every code change to pass the 62-document golden corpus plus the existing 140 regression tests.
4. Re-run the 14 missing-path clients after valid 2025 ShareFile folders are confirmed.
5. Pilot one structurally complete filing and one multiple-broker filing through Review.
6. Send only the selected Schedule A in the FT Williams test account.
7. Re-query FT Williams and prove that the selected Schedule A changed and all other Schedule A records remained byte-for-byte equivalent in mapped business fields.

## Final conclusion

The system is **not ready for unattended all-client processing**. The review and Field Rules code paths are healthy under tests, but real-document extraction remains the release blocker. The correct next engineering move is a golden-corpus, schema-driven extraction program with semantic validation and broker-row reconciliation, followed by a small controlled FT Williams isolation test.
