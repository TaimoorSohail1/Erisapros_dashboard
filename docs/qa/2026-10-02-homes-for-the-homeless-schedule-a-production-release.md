# Homes for the Homeless — 2025 Schedule A production validation

Date: 2026-10-02  
Client folder: `Homes for the Homeless, Inc TEST > 5500 Filing > 2025 Filing > Schedule A`  
Result: Four filings completed automatic FT Williams update and read-back. AFLAC stopped safely for one genuine source conflict.

## Test plan executed

1. Inventory every Schedule A and the newest Plan Worksheet.
2. Identify each document layout and compare every extracted identity, date, participant, premium, commission, fee, and broker value with the source document.
3. Query the current 2025 Form 5500 and all Schedule A records from FT Williams.
4. Validate routing outcomes: one-sided values update, two blanks skip, matching values take no action, and only genuine differences require a decision.
5. Validate broker matching, automatic broker creation, organization codes, commissions, fees, purposes, and zero-value handling.
6. Validate the FT Williams no-commissions/no-fees indicator, uppercase outbound text, policy periods no longer than 12 months, and automatic Schedule A creation when no safe EIN/contract match exists.
7. Send only eligible automatic updates, query FT Williams again, and require every sent value to pass read-back verification.
8. For every defect: reproduce, fix, run focused tests and the complete backend suite, deploy both production services, and repeat the exact live case.

## Source inventory and worksheet

The package contained five Schedule As and the newest Plan Worksheet. The worksheet parser returned these five fully insured rows:

| Carrier | Policy | Period |
|---|---:|---|
| Anthem Blue Cross | 300683 | 01/01/2025–12/31/2025 |
| Cigna Health and Life Insurance Company | 3346625 | 01/01/2025–12/31/2025 |
| Vision Service Plan | 30044823 | 01/01/2025–12/31/2025 |
| Prudential Insurance Company of America | 15408 | 01/01/2025–12/31/2025 |
| AFLAC | NBX36 | 01/01/2025–12/31/2025 |

The Plan Worksheet also produced the structured sponsor address `36 COOPER SQUARE`, `3RD FLOOR`, `NEW YORK CITY`, `NY`, `10003`. All policy periods are within 12 months.

## Per-document results

### Cigna — passed, new Schedule A created

- Layout: Cigna certified Schedule A with combined nonexperience-rated sections and two broker rows; identified correctly by the deterministic local fallback after the bounded semantic extractor timed out.
- Extracted: carrier `Cigna Health and Life Insurance Company and affiliates`; EIN `59-1031071`; NAIC `67369`; contract `3346625`; persons `169`; period `01/01/2025–12/31/2025`; premium `$48,020`; commission `$3,534`; fees `$1,600`.
- Brokers: RSC Insurance Brokerage Inc., Boston (`$3,534` commission, `$0` fee, code 3) and RSC Insurance Broker. Inc., New York (`$0` commission, `$1,600` fee, code 3). Both matched automatically after creation.
- Worksheet reconciliation: policy `3346625` matched, so the outbound legal carrier name was safely canonicalized to `CIGNA HEALTH AND LIFE INSURANCE COMPANY`; the original PDF extraction remains preserved as evidence.
- FT Williams: no safe existing EIN/contract match; automatic create-new routing selected. Schedule A sequence `6` was created.
- Update/read-back: 14 attempted, 14 confirmed, 0 remaining, verification passed. Receipt `5d189ccced5748b3a4302b80188bdf27`, action `SCHEDULE_A_CREATED`.
- Final FTW values: carrier `CIGNA HEALTH AND LIFE INSURANCE COMPANY`; EIN `59-1031071`; NAIC `67369`; contract `3346625`; persons `169`; premium `48020`; two broker rows and their zero values confirmed. `OverrideCommissionsAndFees=0`, correctly unchecked because payments exist.
- Decisions: none.

### VSP — passed

- Layout: VSP single-page Schedule A; identified correctly.
- Extracted: carrier `VISION SERVICE PLAN`; EIN `22-2777159`; NAIC `47029`; contract `30044823`; persons `161`; period `01/01/2025–12/31/2025`; premium `$23,784.72`; commission `$1,275.91`; fee `$0`.
- Broker: RSC Insurance Brokerage, Inc.; automatically matched to FTW broker row 1.
- FT Williams: existing Schedule A sequence `3` matched safely.
- Update/read-back: 6 attempted, 6 confirmed, 0 remaining, verification passed. This included the four worksheet participant counts, persons covered, and premium. Receipt `add852feedb247789fad246c5a3fc236`, action `FORM_5500_AND_SCHEDULE_A_UPDATED`.
- Final FTW values include contract `30044823`, persons `161`, premium read back in FTW whole-dollar form as `23785`, commission `1276`, fee `0`, and `OverrideCommissionsAndFees=0`.
- Decisions: none.

### Anthem — passed

- Layout: Anthem multi-page Schedule A with two broker rows; identified correctly.
- Extracted: carrier `ANTHEM BLUE CROSS`; EIN `23-7391136`; NAIC `55093`; contract `300683`; persons `205`; period `01/01/2025–12/31/2025`; premium `$3,170,109`; commission `$104,203.61`; fees `$30,270`.
- Brokers: RSC Insurance Brokerage Inc. and Emerson Rogers LLC; both automatically matched to the correct existing FTW rows despite different row order.
- FT Williams: existing Schedule A sequence `1` matched safely.
- Update/read-back: 2 remaining changes were sent and both confirmed (persons covered and premium); all other extracted values already matched. Receipt `265e68368fe3492383955d02c9d79adb`, action `SCHEDULE_A_UPDATED`.
- Final FTW broker values: commission `104204`, fee `30270`, explicit zero in each opposite amount, codes 3, and `OverrideCommissionsAndFees=0`.
- Decisions: none.

### Prudential — passed

- Layout: Prudential multi-page experience-rated Schedule A; identified correctly and aggregated across benefit sections.
- Extracted: carrier `PRUDENTIAL INSURANCE COMPANY OF AMERICA`; EIN `22-1211670`; NAIC `68241`; contract `15408`; persons `356`; period `01/01/2025–12/31/2025`; premium `$59,189`; commission `$8,855`; fees `$3,014`.
- Brokers: RSC Insurance Brokerage Inc., Selman & Company LLC, and IMG; all three matched automatically to their existing FTW rows, including different source/FTW row ordering.
- FT Williams: existing Schedule A sequence `4` matched safely.
- Update/read-back: 2 remaining changes were sent and confirmed (persons covered and premium); 0 remaining. Receipt `2e81d42db1ab4ea98a1b8238be7066e8`, action `SCHEDULE_A_UPDATED`.
- Final FTW broker values: RSC commission `8855`; Selman fee `2938`; IMG fee `76`; opposite amounts explicitly zero; organization codes 3, 3, and 5; `OverrideCommissionsAndFees=0`.
- Decisions: none.

### AFLAC — correctly stopped for one decision

- Layout: AFLAC multi-page Schedule A with 30 compensation rows; identified correctly. Exactly 30 broker rows were extracted and every row resolved automatically as a unique new broker row (`AUTO_NEW`).
- Extracted: carrier `AFLAC`; carrier EIN blank; NAIC `60380`; Schedule A contract `52-0807803`; persons `57`; period `01/01/2025–12/31/2025`; premium `$44,327.27`; commission `$10,875.30`; fees `$1,204.44`.
- Worksheet conflict: the newest Plan Worksheet says policy `NBX36`, while the Schedule A explicitly says `52-0807803`. The corrected decision engine now reports exactly one real conflict with both values shown.
- FT Williams: current-year records were returned, but none safely matched the extracted carrier/EIN/NAIC/contract identity. No existing record was selected and no new record was created while the source conflict remains unresolved.
- Update/read-back: no write attempted, no receipt, and no partial update. This is the required fail-closed result.
- Decisions: one — choose the authoritative AFLAC policy number (`NBX36` or `52-0807803`).

## Routing and component verification

- One valid value with FTW blank: routed to `WILL_UPDATE` and sent automatically for eligible filings.
- Both values blank: routed to `SKIP_EMPTY`; no blank-clearing write was sent.
- Both values present and equal: routed to `NO_CHANGE`.
- Real conflict: only AFLAC's worksheet/Schedule A policy mismatch is `CONFLICT`; no false conflicts remain.
- Brokers: Cigna 2/2, VSP 1/1, Anthem 2/2, Prudential 3/3, and AFLAC 30/30 resolved automatically. No ambiguous broker decision remains.
- No-commission/no-fee indicator: all five source documents contain at least one payment, so the correct live value for updated filings is `0` (unchecked). Regression tests separately prove an all-empty/all-zero broker set emits `1` and any nonzero payment emits `0`.
- Uppercase: final FTW text and broker names were read back uppercase.
- Dates: every worksheet and Schedule A period is 01/01/2025–12/31/2025, within the 12-month limit.
- New Schedule A behavior: Cigna proved automatic creation when no existing EIN/contract identity matched. AFLAC was not created because two authoritative sources conflict, which correctly takes precedence over automatic creation.

## Issues fixed

1. Added deterministic extraction support for all five client layouts, including Cigna's combined sections, Prudential aggregation, AFLAC's 30 rows, VSP address parsing, Anthem brokers, and the five-row worksheet.
2. Bounded semantic extraction latency and retained verified local fallback behavior.
3. Hardened the production browser-image build download.
4. Added `SQUARE`/`SQ` to safe sponsor-address parsing, producing valid FTW address components instead of rejecting the combined address as longer than 35 characters.
5. Added worksheet-confirmed carrier identity reconciliation so Cigna's source phrase remains evidence while FTW receives the exact legal carrier name.
6. Added worksheet-vs-Schedule-A policy/date conflict detection; this exposed the AFLAC policy mismatch as a real decision.
7. Fixed the final approval guard so it reuses the worksheet-confirmed proposal instead of revalidating Cigna's raw affiliate phrase.

## Regression and production evidence

- Focused regression cases: passed.
- Complete backend suite after the final fix: `931 passed, 2 skipped`; the only warning is a pre-existing GroundX SDK deprecation notice.
- Release commits: `60c0eac`, `06e2088`, `911c31e`, `21b7af0`, `23a3af3`.
- Final CodeBuild: `erisapros-production-backend:4b36e987-0cd6-4267-a066-bff159c8267b` — succeeded.
- Final image: `sha256:e2bac040d71fac0defa90eda82ee1e792257ea7c06e4fedbd481f6a10262685c`.
- Production ECS: API revision `66` and ShareFile worker revision `56`; both rollout states `COMPLETED`, each 1 running / 0 pending.
- Public health: HTTP 200, `{"status":"ok","stack":"react-python-mongodb"}`.
- Production logs after final deployment: no matching `ERROR`, `Traceback`, or `Exception` events.
- Main ShareFile queue: 0 visible / 0 in flight. The DLQ contains one pre-existing retained message; it was not changed by this release.
- Frontend: unchanged, so no frontend artifact or CloudFront invalidation was required for these backend-only fixes.

## Final disposition

The automation is production-verified for Cigna, VSP, Anthem, and Prudential, including FT Williams read-back. AFLAC is intentionally and correctly held for one client decision; all other extraction, routing, broker, formatting, creation, update, and verification paths for this client passed.
