# NFP 2025 Schedule A production validation

Date: 2026-10-06  
Environment: production (`eu-north-1`)  
Client: NFP Corp. Group Health and Welfare Plan, plan 511  
Scope: 14 Schedule A source packages and the shared newest Plan Worksheet

## Executive result

- All 14 source packages were classified correctly as one Schedule A plus the shared `5500 Plan Worksheet - NFP 5500 - PY25.docx`.
- The shared Plan Worksheet produced 20 protected FT Williams comparison fields: 17 matched FT Williams and 3 were blank on both sides and correctly skipped.
- Eight Schedule As sent 38 automatic field updates to FT Williams. All 38 were confirmed by FT Williams read-back with zero mismatches.
- Three Schedule As already matched FT Williams and correctly sent no update.
- Three Schedule As were stopped safely: Ansel has no safe existing-record match and an incomplete current query; Tuned and CuraLinc contain invalid six-digit source values in the NAIC position. No unsafe FT Williams write was attempted.
- No real value conflict or ambiguous broker match remains. There are zero decision-required conflicts across the 14 final reviews.
- Broker result: 24 existing broker rows auto-matched, one CuraLinc broker row was correctly staged as `AUTO_NEW`, and zero broker decisions were required.
- Zero-compensation behavior was confirmed for the matched BCBS and two Kaiser records: the FT Williams no-commissions/no-fees checkbox is preserved and hidden stale broker data is not copied.

## Shared Plan Worksheet

File: `5500 Plan Worksheet - NFP 5500 - PY25.docx`

| Value | Extracted | FT Williams | Result |
|---|---:|---:|---|
| Plan name | NFP CORP. GROUP HEALTH AND WELFARE PLAN | Same | No change |
| Plan number | 511 | 511 | No change |
| Effective date | 01-01-2022 | 01/01/2022 | Equivalent; no change |
| Sponsor | NFP CORP. | Same | No change |
| Sponsor EIN | 13-4029115 | Same | No change |
| Sponsor address | 200 PARK AVE SUITE 3202 NEW YORK NY 10166 | Same normalized address | No change |
| Business code | 523900 | 523900 | No change |
| Plan year | 01-01-2025 through 12-31-2025 | Same | No change; 12 months |
| Participants at beginning | 6,034 | 6,034 | No change |
| Active participants at beginning | 5,768 | 5,768 | No change |
| Active participants at end | 6,159 | 6,159 | No change |
| Three unprovided participant fields | Blank | Blank | `SKIP_EMPTY` |

The production FT Williams query was complete. No Plan Worksheet decision or write was required.

## Per-Schedule-A results

| # | Source and extracted identity | Extraction / broker result | FT Williams result | Final routing and evidence |
|---:|---|---|---|---|
| 16 | BCBS Vermont; EIN `03-0277307`; NAIC `53295`; contract `369027555`; 116 persons; premium `1,096,314` | Layout passed. Zero commission/fee; no broker rows. | Matched sequence 4. | 2/2 updates verified: persons and premium. Receipt `917f4b5de8d649bbade6991a71d54642`. No-compensation checkbox preserved. |
| 15 | Tuned Care; EIN `85-3889665`; source NAIC-position value `525120`; no contract; 8,625 persons; premium `98,796.44` | Image/OCR layout passed; no hidden broker artifacts. Zero commission/fee. | Current query returned records but no safe match. | No write. Six-digit NAIC-position value is invalid and remains blocked for source correction. This is a safety issue, not an extraction miss. |
| 14 | Provident Life and Casualty; EIN `62-0506281`; NAIC `68209`; contract `0000160040`; 775 persons; premium `758,204.74` | Layout passed; all 5 brokers auto-matched. | Matched sequence 14. | 2/2 updates verified. Receipt `b55055cf67f64558b26cab5903f84197`. |
| 13 | Provident Life and Accident; EIN `62-0331200`; NAIC `68195`; contract `0000169225`; 1 person; premium `3,825.12` | Layout passed; all 4 brokers auto-matched. | Matched sequence 13. | Already matched; no update needed and no decision. |
| 12 | Provident Life and Accident; EIN `62-0331200`; NAIC `68195`; contract `0000160171`; 1 person; premium `0.00` | Layout passed; all 5 brokers auto-matched. | Matched sequence 12. | Already matched; no update needed and no decision. |
| 9 | Provident Life and Accident; EIN `62-0331200`; NAIC `68195`; contract `0000160168`; 50 persons; premium `50,578.96` | Layout passed; all 5 brokers auto-matched. | Matched sequence 9. | Already matched; no update needed and no decision. |
| 8 | LegalShield; EIN `73-1016728`; NAIC `00000`; contract `203812`; 1,252 persons; premium `195,256.06` | Layout passed; one broker auto-matched. Equivalent DBA/capitalization carrier text is normalized correctly. | Matched sequence 8. | 5/5 updates verified. Receipt `3b9b0074294b425cb29e124e200b4287`. Final blocked count 0 and decision count 0. |
| 7 | Ameritas Life Insurance Corp. of New York; EIN `13-3758127`; NAIC `60033`; contract `026-202629`; 9,574 persons; premium `657,749`; fee `6,289` | Layout passed; one broker auto-matched. | Matched sequence 7 using the verified carrier alias. | 1/1 update verified. Receipt `3c2676add47f4e1db1046019cc5a8e0d`. |
| 6 | CuraLinc LLC; EIN `33-1206383`; source NAIC-position value `624190`; contract `01804`; 6,045 persons; dates 01/01-04/30/2025; fee `21,762` | Layout passed; broker correctly classified `AUTO_NEW`. | No existing FT Williams sequence was selected for writing. | No write. Six-digit NAIC-position value is invalid and remains blocked for source correction. |
| 3 | Standard Life / The Standard; EIN `13-4119477`; NAIC `89009`; contract `753370`; 6,159 persons; reported premium `1,675,417.88` | Multi-summary layout passed; both broker rows auto-matched. | Matched sequence 3 using the verified carrier alias. | 21/21 updates verified. Receipt `e3cac41f6ffe401ab69bc27ecebe57dd`. |
| 2 | Kaiser Foundation Health Plan; EIN `94-1340523`; NAIC `00000`; contract `603977`; 100 persons; premium `1,018,137.29` | Layout passed. Zero commission/fee; no broker rows. | Matched sequence 2. | 2/2 updates verified. Receipt `7e6aef7b8cc749e4b9ef9f91140da73f`. No-compensation checkbox confirmed. |
| 1 | Kaiser Foundation Health Plan; EIN `94-1340523`; NAIC `00000`; contract `231464`; 213 persons; premium `1,869,374.97` | Layout passed. Zero commission/fee; no broker rows. | Matched sequence 1. | 2/2 updates verified. Receipt `90d3c07eac3d4501813d60c064c8224d`. No-compensation checkbox confirmed. |
| Email | Ansel Services, Inc.; EIN `84-4726657`; NAIC `71870`; contract `LB-10000116`; 2,180 persons; premium `737,398.05` | MSG email body was classified and extracted correctly. | Current FT Williams query was incomplete and no record safely matched carrier/EIN/NAIC/contract. | No write. User must select the correct existing record or explicitly confirm creation; the automation did not guess. |
| Other | Continental American Insurance; EIN `57-0514130`; NAIC `71730`; contract `0000024819`; 1,777 persons; premium `943,913.44` | Layout passed; broker auto-matched by exact address. | Matched sequence 11. | 3/3 updates verified. Receipt `17fd7fc6906e4d4fbdbce20a16ae62a5`. |

## Routing-rule verification

- One value available: valid single-sided values route to `WILL_UPDATE`; verified by the 38 production updates.
- Both values blank: Plan Worksheet blank pairs route to `SKIP_EMPTY` and are not sent.
- Both values present: equivalent formatting, aliases, punctuation, capitalization, addresses, dates, and rounded monetary values route to `NO_CHANGE`. Only meaningful differences can become conflicts.
- No decision was created for the three safety-stopped cases because none is a genuine competing-value conflict.
- Contract/carrier matching uses contract, EIN/NAIC, normalized carrier name, and policy dates. The live records never guessed when identity evidence was insufficient.
- New-record creation is covered by regression tests, but no NFP live source was eligible for a safe automatic creation: Ansel had an incomplete query; Tuned and CuraLinc had invalid NAIC-position values.

## Fixes made and deployed

| Commit | Fix |
|---|---|
| `f1f3edd` | NFP Schedule A layout extraction |
| `ed1d251` | Image-only Schedule A OCR |
| `34dc9bf` | Hidden OCR artifact removal |
| `938dc2f` | Visible OCR across Schedule A pages |
| `f007dc2` | NFP carrier equivalence and BCBS premium extraction |
| `00788d6` | Safe replacement of no-compensation Schedule As without copying hidden stale brokers |
| `29e8ee3` | Equivalent excluded fields display as `NO_CHANGE`, eliminating LegalShield's false blocked counter |

## Final production verification

- Backend tests: `1,032 passed, 2 skipped`.
- Deployed commit: `29e8ee3`.
- Container digest: `sha256:e9b35cfe1a937335c5b0099af4a26e54498617f27d941030d8932533f3b169a0`.
- ECS: API revision 110; ShareFile worker revision 100.
- Public health response: `{"status":"ok","stack":"react-python-mongodb"}`.
- API load-balancer target: healthy.
- Recent worker error-log query: no `ERROR` events.
- Frontend was not changed or redeployed.

## Remaining client/source actions

1. Confirm the intended FT Williams record or authorize a new record for Ansel after the current-query gap is resolved.
2. Correct/confirm the six-digit values supplied in the NAIC position for Tuned (`525120`) and CuraLinc (`624190`).

These are the only remaining action-needed items; they were not automatically written because doing so would be unsafe.
