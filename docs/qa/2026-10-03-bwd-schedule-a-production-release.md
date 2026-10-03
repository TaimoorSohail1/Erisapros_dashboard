# BWD Mile Development Schedule A Production Validation

## Release conclusion

BWD Mile Development LLC dba The Beechwood Organization's 2025 Schedule A package was validated end to end against every supplied source document and the newest Plan Worksheet. Eight Schedule A sources were tested. Two completed the fully automatic FT Williams path with successful read-back: Anthem updated existing sequence `7`, and AFLAC policy `NSU79` created new sequence `8`. Both filings are `APPROVED`, automation is `COMPLETED`, verification succeeded, and zero values remain unverified.

Five sources are correctly held only for genuine date or legal-name conflicts: Equitable, AFLAC `NSU61`, AFLAC `NSU76`, Colonial, and AFLAC `P7D52`. The New York Life workbook is correctly held because it contains four independent policies that cannot safely be collapsed into one Schedule A. No unresolved false conflicts remain, and no unsafe update was sent for any held document.

The source folder also contained an older scanned worksheet and a New York Life explanation document. They were classified as non-filings and marked `SUPERSEDED`, preventing duplicate or unsupported FT Williams writes.

## Scope and final outcome

| Category | Count | Result |
|---|---:|---|
| Authoritative Plan Worksheet | 1 | Parsed and used for all comparisons |
| Schedule A sources | 8 | Every source classified, extracted, compared, and routed |
| Fully automatic FT Williams completions | 2 | Anthem sequence `7`; new NSU79 sequence `8` |
| Genuine decision cases | 6 | Five scalar-conflict cases plus one four-policy workbook |
| Non-filing/support documents | 2 | Marked `SUPERSEDED`; no write |
| False conflicts remaining | 0 | AFLAC legal-name false conflicts removed |
| FT Williams read-back failures | 0 | Both automatic writes verified; remaining count `0` |

Client facts:

- Sponsor: `BWD MILE DEVELOPMENT LLC DBA THE BEECHWOOD ORGANIZATION`
- EIN / plan number: `11-2825424` / `501`
- Plan name: `THE BEECHWOOD ORGANIZATION AND ITS AFFILIATES HEALTH AND WELFARE BENEFIT PLAN`
- Plan year: `05/01/2025`–`04/30/2026`
- ShareFile source root: `foe1c263-1b00-4ebe-926b-744b38b11900`
- Final production evidence task: `2222107cc24045038a8e8953595620c1`
- Controlled NSU79 repair/read-back task: `c0511a32126846a29c62ff186397df99`
- Non-filing supersession task: `a49e523626d04da18541b44903e8eac7`
- Final status reconciliation task: `d6935b78299e40f3a7cbb58b7a25aa87`, exit code `0`

## Newest Plan Worksheet

Authoritative file: `5500 Plan Worksheet - BWD Mile Development LLC dba The Beechwood Organization 5500 - PY25.docx`.

Verified extraction:

- Sponsor address: `200 ROBBINS LANE SUITE D1`, `JERICHO`, `NY 11753`
- Phone: `516-935-5555`
- EIN: `11-2825424`; business code: `236110`; plan number: `501`
- Plan year: `05/01/2025`–`04/30/2026`; original effective date: `05/01/2018`
- Collective bargaining: `No`; signer: `JILL ROSENBLATT`
- Participants: beginning `183`; first-day active `170`; other participant counts are blank in the source and remain blank
- Benefit contracts: AFLAC `NSU76`, AFLAC `GLCL0AHMU`, AFLAC `P7D52`, AFLAC `NSU61`, Paul Revere `E4020418`, Equitable `012952`, Anthem `720705`

The older `Schedule A Worksheet BWD Mile Development Completed 9 2026.pdf` is a scanned duplicate, not the authoritative worksheet. It was marked `SUPERSEDED` and did not generate a Schedule A.

## Per-document results

### 1. Anthem — `Form5500_720705_Anthem.pdf`

- Layout: Anthem Schedule A; identified and extracted correctly.
- Extracted values: Anthem Blue Cross; EIN `23-7391136`; NAIC `55093`; contract `720705`; persons `131`; dates `05/01/2025`–`04/30/2026`; premium `1,581,408.00`.
- Brokers: Centerstone — commission `0`, fee `19,726.33`; World Insurance — commission `63,683.00`, fee `569.13`.
- FT Williams: matched existing sequence `7` by identity. Blank/current differences routed to `WILL_UPDATE`; no real conflicts.
- Automatic result: update sent and read back successfully. FT Williams stores whole-dollar premium `1,581,408`; broker compensation and uppercase text were verified.
- No-commission/no-fee flag: `OverrideCommissionsAndFees=0`, correctly unchecked because commission/fee compensation exists.
- Final state: filing `APPROVED`; automation `COMPLETED`; verification `true`; remaining `0`.

### 2. AFLAC NSU79 — `NSU79.pdf`

- Layout: AFLAC New York Schedule A; identified and extracted correctly after OCR-rule repair.
- Extracted values: American Family Life Assurance Company of New York; EIN `52-0807803`; NAIC `60380`; contract `NSU79`; persons `1`; dates `01/01/2025`–`12/31/2025`; premium `315.12`.
- Brokers: Jennifer Lubelsky `13.44`; Jason Sallemi `7.32`; World Insurance Associates LLC `5.76`; Shaun Konior `3.36`; Alvaro Montenegro `3.12`; Angela Montenegro `1.44`; all fees `0`.
- FT Williams: no existing EIN/contract match. The create-new rule correctly created sequence `8` automatically.
- Defect/re-test: the superseded parser initially wrote commission total `34.44` as premium. The defect was explained, fixed, deployed, and the exact live record was corrected from `34.44` to source premium `315.12`.
- Read-back: FT Williams returned premium `315`, which is its expected whole-dollar representation of `315.12`; comparison is `NO_CHANGE`. Carrier, EIN, NAIC, contract, persons, dates, and six broker rows match.
- No-commission/no-fee flag: `OverrideCommissionsAndFees=0`, correctly unchecked because commissions exist.
- Final state: filing `APPROVED`; automation `COMPLETED`; verification `true`; remaining `0`.

### 3. Equitable — `SCHEDULE A 5500 Report-BWD Mile Development LLC Equitable.pdf`

- Layout: Equitable Schedule A; identified correctly.
- Extracted values: Equitable Financial Life Insurance Company; EIN `13-5570651`; NAIC `62944`; contract `012952`; persons `168`; dates `05/01/2025`–`05/31/2025`; premium `-20.30`.
- Compensation totals: commission `773.36`; fee `418.37`. The source did not yield a reliable named broker row, so no broker identity was invented.
- FT Williams: matched sequence `6`. EIN, NAIC, contract, persons, and source sign are correct. Current policy period ends `04/30/2026`.
- Routing: held for two genuine conflicts only: carrier legal-name wording and policy ending date `05/31/2025` versus `04/30/2026`. Blank FT Williams premium and other safe changes would be `WILL_UPDATE`, but were withheld while these conflicts remain.
- Final result: no FT Williams write sent; decision required.

### 4. AFLAC NSU61 — `NSU61.pdf`

- Layout: AFLAC New York Schedule A; identified correctly.
- Extracted values: EIN `52-0807803`; NAIC `60380`; contract `NSU61`; persons `16`; dates `01/01/2025`–`12/31/2025`; premium `17,794.78`.
- Brokers: Jennifer Lubelsky `661.32` / fee `30.34`; Jason Sallemi `644.31` / `12.33`; World `317.15` / `0`; Christopher `221.80` / `0`; Shaun Konior `215.24` / `10.15`; Alvaro Montenegro `145.74` / `1.52`; Linda `126.22` / `0`; Angela Montenegro `61.37` / `0`; GROF `47.60` / `1.52`.
- FT Williams: matched sequence `4`. AFLAC shorthand and the full legal company name now compare as equivalent.
- Routing: held only for the genuine start/end date conflicts: source calendar year versus FT Williams/worksheet plan year `05/01/2025`–`04/30/2026`.
- Final result: no write sent. Once dates are decided, the premium, nine brokers, and compensation flag can update automatically.

### 5. AFLAC NSU76 — `NSU76.pdf`

- Layout: AFLAC New York Schedule A; identified correctly.
- Extracted values: EIN `52-0807803`; NAIC `60380`; contract `NSU76`; persons `4`; dates `01/01/2025`–`12/31/2025`; premium `3,937.44`.
- Brokers: Christopher `376.41`; Jason Sallemi `161.75` / fee `9.21`; Jennifer Lubelsky `117.48`; Shaun Konior `92.88` / `9.21`; Jacqueline `56.45` / `5.99`; World `50.16`; Alvaro Montenegro `26.76`; Angela Montenegro `12.60`; unspecified fees are `0`.
- FT Williams: matched sequence `1`; identifiers are correct and broker rows are unambiguous.
- Routing: held only for the genuine start/end date conflicts: source calendar year versus current/worksheet plan year.
- Final result: no write sent pending date decision.

### 6. Colonial / Paul Revere — `Schedule A 5500 Colonial Life.pdf`

- Layout: Colonial/Paul Revere Schedule A; identified correctly after the labeled-field parser repair.
- Extracted values: Paul Revere Life Insurance Company; EIN `04-1590994`; NAIC `67598`; contract `E4020418`; persons `2`; dates `05/01/2025`–`04/30/2026`; premium `1,722.37`.
- Brokers: Julie Ann Klimchak `15.90`; Jnaz Inc `3.81`; fees `0`.
- FT Williams: matched sequence `5`; identity fields match. Current policy dates are `01/01/2025`–`12/31/2025`.
- Routing: held only for the two genuine date conflicts. The earlier false persons-covered value `2026` was repaired; the labeled source value `2` is now used.
- Final result: no write sent pending date decision.

### 7. AFLAC P7D52 — `P7D52.pdf`

- Layout: AFLAC New York Schedule A; identified correctly.
- Extracted values: EIN `82-2723296`; NAIC `60380`; contract `P7D52`; persons `3`; dates `01/01/2025`–`12/31/2025`; premium `6,310.80`.
- Brokers: Jason Sallemi `778.67` / fee `94.86`; Jennifer Lubelsky `520.32` / `123.79`; Shaun Konior `183.83` / `35.19`.
- FT Williams: matched sequence `3`; identifiers and broker ownership are unambiguous.
- Routing: held only for the genuine source-calendar-year versus FT Williams/worksheet-plan-year date conflicts.
- Final result: no write sent pending date decision.

### 8. New York Life — `BWD_Mile_Development_PaidPremium_05-01-2025_to_04-30-2026 NYL.xlsx`

- Layout: deterministic New York Life paid-premium workbook; identified and extracted correctly without depending on a remote OCR timeout.
- Extracted aggregate: New York Life Group Insurance Company of New York; EIN `13-2556568`; NAIC `64548`; dates `06/01/2025`–`04/30/2026`; premium `104,452.57`.
- Policies: `SGN0600973` `45,012.20`; `SYK0600579` `4,611.90`; `VDY0600189` `19,840.48`; `VDY0600190` `34,987.99`.
- Brokers: BenefitMall commission `5,141.04`; World Insurance Associates commission `8,210.57`; fees `0`.
- FT Williams: no matching New York Life Schedule A exists for this client.
- Routing: `ACTION_NEEDED`. The workbook contains four distinct policy numbers and must be split/mapped to four Schedule A records; collapsing it into one record would be unsafe. No write was sent.
- Supporting file: `World Insurance Info New York Life Spreadsheet Schedule A Explanation.docx` is explanatory material, not an independent Schedule A. It was marked `SUPERSEDED` and did not create a filing.

## Routing, broker, formatting, and checkbox verification

- One side populated and the other blank: `WILL_UPDATE`, provided the field is mapped, supported, and not blocked by a real conflict.
- Both blank: skipped; blank data is not written back.
- Both populated and equivalent: `NO_CHANGE`, including casing, punctuation, AFLAC legal/trade-name normalization, and FT Williams whole-dollar rounding.
- Both populated and materially different: `CONFLICT`. Only the date/legal-name cases documented above remain.
- Brokers: exact/unambiguous broker rows auto-match or auto-create. NSU79's six brokers were created automatically; Anthem's two brokers were updated and verified. No ambiguous broker decision remains in those completed cases.
- Text: outbound carrier and broker names are uppercase in FT Williams read-back.
- Dates: every extracted coverage period is 12 months or less. No over-12-month range was sent.
- New Schedule A: NSU79 proved the live create-new path when EIN and contract did not match an existing record.
- Compensation checkbox: `OverrideCommissionsAndFees=0` was verified for Anthem and NSU79 because compensation exists. The held sources also contain compensation, so their current FT Williams `1` flags must be cleared when their conflicts are resolved and updates are sent.
- True zero-commission/zero-fee client case: none exists in this package. The checked-state branch is covered by regression tests but could not be exercised using this client's real source documents.

## Defects found, fixed, and re-tested

1. Ambiguous support documents could be treated as Schedule A filings instead of being classified by content.
2. Corrected ShareFile document types were not persisted reliably.
3. Duplicate worksheet/support items required lightweight supersession handling.
4. Scanned AFLAC/Colonial documents and the NYL workbook needed deterministic recovery paths.
5. Broker-only resolved changes were not always counted as automatic updates.
6. AFLAC OCR rule separators caused commission totals to replace premiums (`34.44` instead of NSU79 `315.12`, plus incorrect totals for NSU61, NSU76, and P7D52).
7. Deterministic fallback identity fields were downgraded to confidence `0.5`, blocking safe create-new behavior.
8. AFLAC shorthand and its full New York legal name created false carrier conflicts.
9. Colonial persons covered was parsed from the year `2026` instead of the labeled value `2`.
10. Negative Equitable premium `-20.30` could lose its sign.
11. A previously verified review could mask a later source correction when the new review contained conflicts.

All fixes were deployed before the final live replays. Final focused regression results:

- Extraction, classification, document recovery, and mapping suites: `358 passed`.
- FT Williams review, routing, automation, broker, and verification suites: `197 passed`.

## Production deployment and evidence

- Production code commit: `d071fe4` (`fix: invalidate stale verified reviews on conflicts`), including the preceding BWD fixes on the same branch.
- CodeBuild: `erisapros-production-backend:323ab8ac-a02c-49a7-852f-32caf7e22dac` — `SUCCEEDED`.
- Immutable ECR image digest: `sha256:f23ca3e911271678db6a87a0b4ec81a2cb8cf2f8c56c35fda270c13e670d7fd3`.
- API task definition: `erisapros-production-api:88` — desired `1`, running `1`, pending `0`, rollout `COMPLETED`.
- Worker task definition: `erisapros-production-sharefile-worker:78` — desired `1`, running `1`, pending `0`, rollout `COMPLETED`.
- Health: HTTP 200, `{"status":"ok","stack":"react-python-mongodb"}`.
- Final batch replay: `d1bf2ea559bc40ea81eba966782dca12`.
- Final evidence/read-back: `2222107cc24045038a8e8953595620c1`.
- NSU79 controlled correction/read-back: `c0511a32126846a29c62ff186397df99`.
- Verified-status reconciliation: `d6935b78299e40f3a7cbb58b7a25aa87`; changed filing presentation state only and made no FT Williams data change.

## Final decision queue

1. Equitable: confirm carrier legal-name wording and whether the policy ends `05/31/2025` or `04/30/2026`.
2. NSU61: choose source calendar-year dates or worksheet/FT Williams plan-year dates.
3. NSU76: choose source calendar-year dates or worksheet/FT Williams plan-year dates.
4. Colonial: choose source plan-year dates or current FT Williams calendar-year dates.
5. P7D52: choose source calendar-year dates or worksheet/FT Williams plan-year dates.
6. New York Life: approve the mapping/splitting of the four workbook policies into four distinct Schedule A records.

All other client-specific automatic work is complete and confirmed by live FT Williams read-back.
