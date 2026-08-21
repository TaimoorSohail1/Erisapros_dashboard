# ShareFile → Extraction → FT Williams End-to-End QA

Date: 2026-08-21  
Branch: `codex/redesign-filing-details`  
Production deployment: not performed

## Controlled test package

- Schedule A: `QA-E2E-20260821-501-1-3780490.pdf`
- Local evidence: `C:\Users\Hp\Erisapros_dashboard\tmp\qa_sharefile\QA-E2E-20260821-501-1-3780490.pdf`
- SHA-256: `D1A805C0723ED0ED9F7C261817889D1942D288FF13A09706B7F6A41C4380A9AF`
- Size: 72,171 bytes
- ShareFile destination: `2025 Filing / Schedule A's / ERISAPros E2E Harold 20260616-095048 / 2024 Filing SPY / Schedule A's`
- Live filing: `https://d3axcdlq9aydpw.cloudfront.net/filings/6a87ff68855364765cd67958`
- Package pairing: passed; Schedule A and Plan Worksheet were paired as two ShareFile documents.

The uploaded QA document and generated filing remain available for visual review. No FT Williams update was sent.

## Source and extraction verification

| Field | Schedule A source | Live extraction | Corrected local extraction |
|---|---:|---:|---:|
| Carrier | Equitable Financial Life Insurance Company of America | Passed | Passed |
| Carrier EIN | 86-0222062 | Passed | Passed |
| NAIC | 78077 | Passed | Passed |
| Contract | 011335 | Passed | Passed |
| Persons covered | 279 | Missing | **279** |
| Policy from | 10/01/2023 | Missing | **10/01/2023** |
| Policy to | 09/30/2024 | Missing | **09/30/2024** |
| Health | unchecked / No | Missing | **No** |
| Vision | unchecked / No | Missing | **No** |
| Life insurance | checked / Yes | Passed | Passed |
| Temporary disability | checked / Yes | Passed | Passed |
| Commissions | 4,297.00 | Passed | Passed |
| Fees | 1,775.76 | Passed | Passed |
| Nonexperience premiums (10a) | 35,559.59 | Passed | Passed |

The Plan Worksheet parser also returned the sponsor, EIN, address, plan name/number, administrator, dates, and available participant counts. End-of-year/retired participant values were blank in the source worksheet, so their missing state was correct.

## FT Williams read-only verification

PlanIDs_Batch succeeded over the configured production integration:

- HTTP 200; FTW status code 0
- 3,348 accessible records
- two internal FTW plan identifiers matched EIN `26-3189470` and plan `501`

Both matching identifiers returned the same 2024 plan. The primary exact read returned:

- Form 5500: HTTP 200, 52 fields, plan year 2024
- Schedule A: HTTP 200, 69 fields, sequence 1
- Plan: HAROLD BROTHERS MECHANICAL CONTRACTORS INC. LIFE AND DISABILITY PLAN
- Carrier: EQUITABLE FINANCIAL LIFE INSURANCE COMPANY OF AMERICA
- Carrier EIN: 86-0222062
- NAIC: 78077
- Contract: 011335
- Persons covered: 279
- Health: 0; Vision: 0; Life: 1; Temporary disability: 1
- Current FTW policy period: 01/01/2024–09/30/2024
- Current FTW fees: 1,776

The source has policy start `10/01/2023` and fees `1,775.76`. These are real comparison differences and should appear as proposed updates; they are not FTW fetch errors.

## Defects found and fixed locally

1. **Wrong filing year and company grouping**  
   The scanner used the first ancestor year/folder, assigning this 2024 package to 2025 and grouping it under `HighlandTech AI Test Folder`. The path resolver now uses the deepest filing-year segment and its client folder.

2. **Schedule A table-layout omissions**  
   The parser did not recognize Equitable's `(E)`, `(F)`, and `(G)` table cells or unchecked benefit boxes. It now extracts covered persons, ISO policy dates, and explicit No values for configured benefit fields.

3. **Reviewer decision count stayed stale**  
   `Use proposed` saved the value but the field stayed in the global Action Required count. Reviewer-confirmed `EDITED` fields now leave the blocker count immediately; FTW-rejected fields remain blockers.

4. **Misleading ShareFile scan result**  
   The async scan displayed zero files while work continued in the background. The UI now states that the scan started and that new filings appear automatically.

5. **Older filing payload could crash the details page**  
   Optional jobs, audit logs, fields, and status values are now handled safely.

6. **Contradictory processing UI**  
   A processing filing could say both `Extraction Is Running` and `Ready for approval`. Processing now has priority in the stepper/guidance and keeps approval locked.

## Test evidence

- Backend full suite: **304 passed, 2 skipped; 37 subtests passed**
- Frontend TypeScript: passed
- Guided review UI regression: passed
- Shared polling/performance regression: passed
- Dashboard responsive layout and company grouping: passed
- Field Rules client creation: passed
- Production frontend build: passed
- Built-app smoke test: passed
- Fresh local browser console: **0 errors**
- Live ShareFile processing: passed
- Live FTW transport/read: passed; no connection errors
- FTW writes: intentionally not performed

## Release status

All fixes are implemented and verified locally. They are **not deployed**, so the current live filing still shows the pre-fix 2025 grouping/year and omissions. A deployment is required before repeating the same ShareFile upload as a live regression confirmation.
