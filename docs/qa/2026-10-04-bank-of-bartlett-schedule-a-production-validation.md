# Bank of Bartlett Schedule A production validation

Date: 2026-10-04  
Client: Bank of Bartlett TEST  
Plan: BANK OF BARTLETT EMPLOYEE HEALTH BENEFIT PLAN  
Plan number: 501  
Sponsor EIN: 62-1072448  
Plan year: 01/01/2025–12/31/2025

## Outcome

All seven Schedule A packages and the newest Plan Worksheet were extracted and re-tested in production. FT Williams current-data queries succeeded for every filing. Each Schedule A now selects the correct existing FT Williams sequence. All 14 broker rows resolve automatically: 13 match existing FT Williams brokers and the John Hancock broker is correctly routed as a new broker.

No FT Williams write was sent. The Plan Worksheet reports 101 participants at the beginning of the year while FT Williams contains 103. This is a real conflict shared by all seven packages, so the automation correctly stopped before any update. CHUBB has two additional genuine source conflicts: carrier EIN 36-2136262 versus FT Williams 36-2136263, and policy end 01/31/2025 versus the worksheet/FT Williams 12/31/2025.

Final automatic-update result: **0 updates sent, 0 updates confirmed, 0 update read-backs attempted**. This is the expected fail-closed behavior until the genuine decisions are resolved.

## Source worksheet verification

Newest worksheet: `5500 Plan Worksheet - Bank of Bartlett 5500 Final 2025 PY 6-9-26.docx`

- Sponsor: BANK OF BARTLETT, INC.
- EIN: 62-1072448
- Plan number: 501
- Effective date: 09/01/1999
- Plan year: 01/01/2025–12/31/2025
- Beginning participants: 101
- Ending active participants: 101
- Retired/COBRA receiving benefits: 0
- Retired/COBRA entitled to future benefits: 0
- FT Williams beginning participants: 103
- Routing: genuine **Needs Decision**; the other blank FT participant fields are **Will Update**.

## Per-Schedule A results

### 1. MetLife — FT sequence 1

- Identity: Metropolitan Life Insurance Company; EIN 13-5581829; NAIC 65978; contract TM05941745.
- Dates: 01/01/2025–12/31/2025.
- Persons covered: 275.
- Premium: $172,421.
- Compensation: $22,943 commissions; $0 fees.
- Broker: PATRICK HOFFMAN, 1910 EXETER RD STE 2, GERMANTOWN TN 38138-2971.
- Commission detail: Life $12,324; Dental $5,567; Long Term Disability $4,460; AD&D $592.
- Broker routing: AUTO_MATCHED by unique broker name.
- FT result: exact contract/EIN/NAIC/carrier/date match; current premium and persons were blank and are **Will Update**.
- Remaining decision: beginning participants 101 versus 103.

### 2. VSP — FT sequence 2

- Identity: Vision Service Plan; EIN 06-1227840; NAIC 39616; contract 30011786.
- Dates: 01/01/2025–12/31/2025.
- Persons covered: 78.
- Premium: $10,335.22.
- Compensation: $761.60 commissions; $0 fees.
- Broker: Patrick Hoffman, 1910 EXETER RD STE 2, GERMANTOWN TN 38138-2971.
- Broker routing: AUTO_MATCHED by unique broker name.
- FT result: exact identity/date match. `VISION SERVICE PLAN (VSP)` is correctly treated as the same carrier as `VISION SERVICE PLAN`.
- Remaining decision: beginning participants 101 versus 103.

### 3. John Hancock — FT sequence 3

- Identity: John Hancock Life Insurance Company; EIN 01-0233346; NAIC 65838; contract 30460.
- Dates: 01/01/2025–12/31/2025.
- Persons covered: 26.
- Premium: $23,650.80.
- Compensation: $313.31 commissions; $0 fees.
- Broker: William Billingsley / Comprehensive Wealth Mgmt, 1910 Exeter Rd. Ste 2, Germantown TN 38138.
- Broker routing: AUTO_NEW; no existing FT Williams broker matched, so it will be added automatically.
- FT result: exact contract/EIN/NAIC/date match. `JOHN HANCOCK` and the full legal carrier name are correctly treated as equivalent.
- Remaining decision: beginning participants 101 versus 103.

### 4. Colonial Life — FT sequence 4

- Identity: Colonial Life & Accident Insurance Company; EIN 57-0144607; NAIC 62049; contract E7551682.
- Dates: 01/01/2025–12/31/2025.
- Persons covered: 2.
- Premium: $716.88.
- Compensation: $23.90 commissions; $0 fees.
- Brokers: Mark Christopher Holland $11.04; J Austin Baker $1.82; Kenneth Carpenter $11.04, with all source addresses extracted.
- Broker routing: all three AUTO_MATCHED.
- FT result: exact identity/date match; persons and premium are **Will Update**.
- Remaining decision: beginning participants 101 versus 103.

### 5. Transamerica — FT sequence 5

- Identity: Transamerica Life Insurance Company; EIN 39-0989781; NAIC 86231; contract ER00000636.
- Dates: 01/01/2025–12/31/2025.
- Persons covered: not supplied by the source and correctly skipped because FT Williams is also blank.
- Premium: $5,843.28.
- Compensation: $165.60 commissions; $0 fees.
- Brokers: Bank of Bartlett $24.00; Gregory L Voges $57.64; Millennium Benefits Insurance Group Inc $83.96, with all source addresses extracted.
- Broker routing: all three AUTO_MATCHED.
- FT result: exact identity/date match; premium is **Will Update**.
- Remaining decision: beginning participants 101 versus 103.

### 6. Combined Insurance / CHUBB — FT sequence 6

- Identity: Combined Insurance Company of America; source EIN 36-2136262; NAIC 62146; grouped contracts 901937294 and 901937295.
- Dates: source 01/01/2025–01/31/2025.
- Persons covered: 99.
- Premium: $34,100.37.
- Compensation: $2,961.75 commissions; $0 fees.
- Brokers: AON Consulting Inc $60.39; Elizabeth Blair $103.47; Comprehensive Wealth Mgmt $1,199.32; Charles Summers $1,598.57.
- Broker routing: all four AUTO_MATCHED by unique broker name.
- FT result: selected sequence 6 using the grouped contract member, NAIC, carrier, and dates. The grouped source contract correctly matches FT contract 901937294 rather than creating a duplicate Schedule A.
- Genuine decisions: beginning participants 101 versus 103; source EIN 36-2136262 versus FT 36-2136263; source end date 01/31/2025 versus worksheet/FT 12/31/2025.

### 7. Cigna — FT sequence 7

- Identity: Cigna Health and Life Insurance Company; EIN 59-1031071; NAIC 67369; contract 00628743.
- Dates: 01/01/2025–12/31/2025.
- Persons covered: 71.
- Premium: $910,067.18.
- Compensation: $0 commissions; $45,503.24 benefit-advisor fees.
- Broker: Patrick W Hoffman, 1910 EXETER ROAD STE 2, GERMANTOWN TN 38138-0000.
- Broker routing: AUTO_MATCHED by unique broker name.
- FT result: exact identity/date match; persons and premium are **Will Update**.
- Remaining decision: beginning participants 101 versus 103.

## Rule and payload checks

- One extracted value with blank FT Williams value routes to **Will Update**.
- Both blank routes to **Skip Empty**; Transamerica persons-covered is the live example.
- Different nonblank values route to **Conflict** only for the genuine participant and CHUBB discrepancies listed above.
- All broker matches are resolved automatically; no ambiguous broker decision remains.
- All seven target Schedule A payloads derive `OverrideCommissionsAndFees=0`, which is correct because every target has a positive commission or fee. There is no all-zero commission-and-fee Schedule A in this client set.
- Broker outbound text normalization is uppercase.
- All extracted policy periods are at most 12 months; CHUBB is a one-month source period and is deliberately preserved as a conflict rather than silently expanded.
- No client document has both a nonmatching EIN and a nonmatching contract after final matching, so no new Schedule A is required. The create-new rule remains covered by the regression suite.

## Defects fixed

1. Added deterministic parsers for Transamerica, Combined/CHUBB, John Hancock, and standard MetLife layouts.
2. Re-ran all pages of positively identified standard MetLife packets in table-preserving OCR mode.
3. Corrected MetLife persons covered, commission/fee totals, broker address, and four commission detail rows.
4. Generalized Colonial detection and fixed its covered-person count, grand commission total, and broker addresses.
5. Added the scanned VSP broker-row layout and normalized VSP carrier aliases.
6. Added Cigna broker-address extraction and ZIP+4 normalization.
7. Added John Hancock carrier alias handling.
8. Matched explicit multi-contract source bundles to an existing FT contract member while retaining real EIN/date conflicts.

## Regression and deployment evidence

- Focused Schedule A/FT Williams tests: 250 passed plus 4 subtests.
- Full backend suite: 999 passed, 2 intentionally skipped, 68 subtests passed.
- Commits: `fae5c7b`, `9ac6041`.
- Production ECR digest: `sha256:1d0018f0b3e83e536446b2d5447cf9c80491a4d8585c26ac993cc3eeda47b9fd`.
- Production ECS task definitions: API revision 97; ShareFile worker revision 87.
- Both ECS services reached `COMPLETED` rollout with one running task and zero pending tasks.
- Production health: `{"status":"ok","stack":"react-python-mongodb"}`.
- Final live extraction jobs: CHUBB `6ac194a38708a3b979d7cc71`; John Hancock `6ac194a38708a3b979d7cc72`; VSP `6ac194a38708a3b979d7cc73`; MetLife `6ac194a48708a3b979d7cc74`.
- All seven final FT Williams current queries returned success and complete current data.

## Required client decisions before send

1. Confirm beginning participant count: worksheet 101 or FT Williams 103.
2. For CHUBB, confirm carrier EIN: source 36-2136262 or FT Williams 36-2136263.
3. For CHUBB, confirm policy end date: source 01/31/2025 or worksheet/FT Williams 12/31/2025.

After those decisions are resolved, the queued automatic fields and broker changes can be sent, followed by the required FT Williams read-back verification.
