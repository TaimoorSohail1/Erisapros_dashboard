# CareQuest Schedule A Production Validation

## Release conclusion

CareQuest Institute for Oral Health, Inc. 2025 Schedule A automation was tested end to end against all seven supplied Schedule A documents and the newest Plan Worksheet. Production is running release commit `6fcc2c2618ac18ccedc05b88970ed1d2c8475a42` on API task definition `:73` and ShareFile worker `:63`.

The automatic path is verified for UnitedHealthcare, VSP, and AllOne. Hartford is correctly held for a real worksheet/source policy-number decision. The MetLife source is correctly held because it contains two independent Schedule A records without contract numbers. The two image-only PDFs are duplicate scan copies of the corresponding native MetLife and Hartford documents; the OCR provider timed out, so they are safely prevented from producing duplicate FT Williams writes.

No unresolved false conflicts remain. Three duplicate AllOne rows created while reproducing the create-new reconciliation defect were removed by an exact FT Williams six-record replacement and verified read-back. FT Williams now contains sequences 1–6 only.

## Scope and evidence

- Client: CareQuest Institute for Oral Health, Inc.
- Filing year: 2025
- Plan sponsor EIN: `38-4016550`
- Plan number: `506`
- Plan year: `01/01/2025`–`12/31/2025`
- Newest worksheet selected: `UPDATED 2025 CareQuest Institute for Oral Health Plan Worksheet (1).docx`
- Final ShareFile filing IDs:
  - UHC: `6abffa8eb4108d9fb603afd4`
  - Hartford native: `6abffa9cb4108d9fb603afd8`
  - AllOne: `6abffaabb4108d9fb603afdc`
  - MetLife native: `6abffab8b4108d9fb603afe0`
  - MetLife scanned duplicate: `6abffac5b4108d9fb603afe4`
  - Hartford scanned duplicate: `6abffad1b4108d9fb603afe8`
  - VSP: `6abffadcb4108d9fb603afec`
- Final policy audit ECS task: `0687aedf643d47df8c2ad492150c2396`, exit code 0.
- AllOne live write/read-back task: `920e997aafae41398966a6d01e642c0b`, exit code 0.
- Duplicate cleanup task: `429bc4a9bf9740e78fa9467a37a48871`, exit code 0 and verified replacement.
- Regression suite: `949 passed, 2 skipped, 1 third-party deprecation warning`.

## Newest Plan Worksheet verification

The production package selector used ShareFile item `fif60ae0-f041-54dd-621e-25f893ea755e`, the explicitly named updated worksheet, for every Schedule A package.

Verified worksheet values:

- Plan name: CareQuest Institute for Oral Health, Inc. Health & Welfare Plan
- Effective date: `01/01/2022`
- Sponsor EIN / plan number: `38-4016550` / `506`
- Plan year: `01/01/2025`–`12/31/2025`
- Participants: beginning `146`; end `152`; active end `149`; retired receiving benefits `3`; other entitled `0`
- Benefit rows: UHC `913136`; Hartford `922556`, `922557`, `922558`; VSP `40152233`; AllOne `N/A`; MetLife Legal `N/A`; MetLife Accident `TBD`

Date formatting differences such as `1/1/2022` and `01/01/2022` now compare as the same date. All policy periods are within the permitted 12-month plan year.

## Per-document results

### 1. VSP — `1. CareQuest Institute for Oral Health, Inc. 40152233 2025 Schedule A 5500 (VSP).pdf`

- Layout: VSP labeled carrier report; verified local fallback. The duplicated identical pages are deduplicated before broker aggregation.
- Extraction: Vision Service Plan; EIN `06-1227840`; NAIC `39616`; contract `40152233`; persons `118`; premium `21,711.02`; dates `01/01/2025`–`12/31/2025`.
- Broker: NFP Corporate Services NY LLC, New York NY `10166`; commission `972.09`; fee `0`.
- FT Williams: matched sequence `1`, contract `40152233`; broker auto-matched to row 1.
- Routing: `COMPLETED`; no update needed because current FT Williams values match.
- Final evidence: query success/complete `true/true`; no conflict; no pending update.

### 2. UnitedHealthcare — `913136_Schedule A.pdf`

- Layout: UnitedHealthcare Schedule A report; verified local fallback.
- Extraction: UnitedHealthcare Insurance Company; EIN `36-2739571`; NAIC `79413`; contract `913136`; persons `358`; premium `5,045,427.62`; dates `01/01/2025`–`12/31/2025`; fail-to-provide `No`.
- Broker: NFP Corporate Services (MA) LLC, Norwell MA `02061-1620`; commission `100,901.12`; fee `0`.
- FT Williams: matched sequence `2`; broker auto-matched to row 1. Brand spacing (`UnitedHealthcare` vs `United Healthcare`) is treated as the same legal identity.
- Routing: `COMPLETED`; no update needed in the final audit.
- Final evidence: query success/complete `true/true`; no conflict; current FT Williams premium read-back `5,045,428` after FT Williams whole-dollar rounding.

### 3. Hartford native — `4. Carequest - Schedule A 2025.pdf`

- Layout: Hartford annual statement; verified local fallback.
- Extraction: Hartford Life and Accident; EIN `06-0838648`; NAIC `70815`; policy `922556G`; persons `171`; premium `173,344.57`; dates `01/01/2025`–`12/31/2025`.
- Brokers:
  - Marsh & McLennan, Boston: commission `3.43`, fee `0`.
  - NFP, New York: commission `11,158.40`, fee `0`.
  - NFP, Philadelphia: commission `0`, fee `4,324.75`.
  - Marsh & McLennan, King of Prussia: commission `0`, fee `6.72`.
- FT Williams: matched sequence `4`; all four broker rows auto-matched to the correct existing rows.
- Routing: `ACTION_NEEDED` for one real conflict only: worksheet policy `922556` vs Schedule A/FT Williams policy `922556G`.
- Final evidence: query success/complete `true/true`; no automatic write attempted while the policy decision is unresolved.

### 4. AllOne — `5. CareQuest AllOne Health EAP.docx Schedule A Data 2025 PY.docx`

- Layout: AllOne EAP Schedule A data document; deterministic DOCX/local parser.
- Extraction: AllOne Health; persons `178`; administrative fees/premium total `5,938.08`; dates `01/01/2025`–`12/31/2025`; carrier EIN, NAIC, contract, and broker disclosures are blank in the source.
- FT Williams: safely matched original sequence `5` by unique exact carrier plus policy dates. Blank source identifiers were not treated as mismatches and did not create a new Schedule A.
- No-commission/no-fee: FT Williams `OverrideCommissionsAndFees=1` is preserved for this no-broker/no-compensation case.
- Automatic update: sequence 5 updated; persons `178` and total `5,938` confirmed by FT Williams read-back.
- Routing: `COMPLETED`.
- Final evidence: verification attempted `true`; success `true`; remaining `0`; receipt `0f87f0d5124049b1b6820b6555335895`, action `SCHEDULE_A_UPDATED`.

### 5. MetLife native — `3. 2025 Carequest Institute Sch A data for 5500.pdf`

- Layout: MetLife Bay Bridge combined report; correctly recognized as a multi-record source and intentionally held for review.
- Legal plan record: MetLife Legal Plan; EIN `34-1650967`; persons `31`; premium `3,149.97`; Marsh commission `167.76`; NFP commission `109.28`; total commission `277.04`.
- Accident record: MetLife Insurance Company; EIN `13-5581829`; NAIC `65978`; persons `33`; premium `6,528.09`; Marsh commission `1,179.87`; NFP commission `125.95`; total commission `1,305.82`.
- FT Williams: current sequences `3` (Legal) and `6` (Accident) exist. The uploaded document has two independent records and no usable contract numbers, so no single-record match is safe.
- Brokers: extracted rows are structurally valid, but assignment belongs to the two separate Schedule As; they are not sent automatically as one combined record.
- Routing: `ACTION_NEEDED`; this is a genuine multi-record decision, not a scalar-value false conflict.
- Final evidence: current query sent successfully but deliberately incomplete for a single selected Schedule A; no FT Williams write was attempted.

### 6. MetLife scanned duplicate — `3. 2025 Carequest Institute Sch A data for 5500 (1).pdf`

- Layout: image-only scan copy of the MetLife native report. Visual comparison confirmed it is the same source content.
- Extraction: the external OCR/AI request timed out; no independent scalar values were accepted.
- FT Williams: no record was selected and no write was attempted.
- Routing: `ACTION_NEEDED` / safe manual hold. This prevents a duplicate of the two-record MetLife source from being written.
- Final evidence: provider recorded `Unrecognized layout - manual review required (AI extraction failed: TimeoutError)`; current Schedule A sequences remained 1–6.

### 7. Hartford scanned duplicate — `4. HARTFORD.pdf`

- Layout: image-only scan copy of the Hartford native statement. Visual comparison confirmed it is the same source content.
- Extraction: the external OCR/AI request timed out; no independent scalar values were accepted.
- FT Williams: no record was selected and no write was attempted.
- Routing: `ACTION_NEEDED` / safe manual hold. It cannot create a duplicate of sequence 4.
- Final evidence: provider recorded `Unrecognized layout - manual review required (AI extraction failed: TimeoutError)`; current Schedule A sequences remained 1–6.

## Routing, formatting, and broker controls

- One extracted value with FT Williams blank: classified `WILL_UPDATE` and included only when mapped, valid, and supported.
- Both values blank: classified `SKIP_EMPTY`; no blank overwrite is sent.
- Both present and equivalent: classified `NO_CHANGE`, including case, punctuation, brand spacing, whole-dollar FT Williams rounding, and zero-padded date differences.
- Both present and materially different: classified `CONFLICT`; Hartford's policy mismatch is the confirmed live example.
- Structured brokers: UHC row 1, VSP row 1, and all four Hartford rows auto-match. Only unresolved multi-record ownership remains a decision for MetLife.
- Text: outbound text is normalized to uppercase; FT Williams read-back shows uppercase carrier, plan, sponsor, and broker values.
- Dates: `01/01/2025`–`12/31/2025`, within 12 months.
- No-fee/no-commission: zero fee values are retained on commission-bearing VSP/UHC/Hartford rows; AllOne's no-compensation checkbox remains enabled.
- New Schedule A creation: only allowed after safe identity evaluation. A missing source EIN/contract is not treated as a mismatch; a deterministic create-new decision reconnects to the exact `ScheduleDesc` FT Williams assigns so retries cannot create duplicates.

## Defects found and fixed

1. Short worksheet dates did not normalize consistently.
2. Native DOCX worksheet tables lost multiple benefit rows.
3. VSP duplicate pages doubled broker compensation.
4. Hartford layout parsing missed identity, lives, premiums, fees, and four broker rows.
5. AllOne DOCX layout was not mapped deterministically.
6. MetLife combined report did not preserve two carrier records and four broker disclosures.
7. UHC nonexperience fields and structured broker row needed deterministic handling.
8. Scanned-PDF OCR text was not reused when the external extraction provider returned page text.
9. Worksheet selection used timestamps instead of preferring `UPDATED`/`REVISED`/`FINAL` filenames.
10. Targeted recovery could skip an unchanged restored package.
11. Equivalent short/zero-padded dates created false conflicts.
12. Worksheet carrier shorthand (`VSP`, `United Healthcare`) overwrote exact Schedule A legal names.
13. Carrier brand spacing created a false UHC conflict.
14. Form-only updates incorrectly required Schedule A replacement XML.
15. Create-new read-back did not reconnect to the exact assigned Schedule A description.
16. Unique carrier-and-policy-date matches with blank source IDs were blocked by the automation policy.
17. One-character worksheet/source policy suffix differences were not surfaced when a carrier had multiple worksheet rows.

All fixes have focused regression coverage. The final full suite result is 949 passed, 2 skipped.

## Production deployment

- Commit: `6fcc2c2618ac18ccedc05b88970ed1d2c8475a42`
- CodeBuild: `erisapros-production-backend:dbd0a44e-ee3d-4854-90b4-e2e0c610c76a`
- ECR digest: `sha256:8f50ef93d99f410d130303f948d7b1a4052366d9fbc4e0c18b4f0d1207673338`
- API task definition: `erisapros-production-api:73`
- Worker task definition: `erisapros-production-sharefile-worker:63`
- Rollback: API `:72`, worker `:62`
- Stability: desired `1`, running `1`, pending `0`, rollout completed for both services.
- Health: HTTP 200, `{"status":"ok","stack":"react-python-mongodb"}`.
- Error scan: no `ERROR`, `CRITICAL`, or `Traceback` matches in recent API/worker logs.

## Final decision queue

Only these cases require human input:

1. Hartford native: decide whether worksheet `922556` and Schedule A/FT Williams `922556G` are intended to be the same policy.
2. MetLife native: confirm the two extracted records should map to existing Legal sequence 3 and Accident sequence 6, including broker-row ownership.
3. The two image-only duplicate PDFs remain held because the OCR provider timed out. They contain no independent business data and must not be separately sent.

All other CareQuest automatic actions are complete or confirmed as no-change by current FT Williams read-back.
