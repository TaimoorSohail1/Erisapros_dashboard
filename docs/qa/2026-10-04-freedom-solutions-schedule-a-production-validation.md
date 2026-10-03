# Freedom Solutions Group TEST — Schedule A production validation

Date: 2026-10-04  
Client package: `Freedom Solutions Group TEST > 5500 Filing > 2025 Filing > Schedule A's`  
Result: extraction, FT Williams lookup, routing, broker matching, and fail-closed behavior validated for all eight Schedule A filings and the current Plan Worksheet.

## Executive result

- All eight Schedule A layouts were recognized and extracted.
- FT Williams current data was fetched successfully for every filing.
- Seven filings matched existing FT Williams Schedule A records. The Aetna Indemnity filing correctly routed to `AUTO_NEW_SCHEDULE_A` because no existing record matched its EIN/contract.
- Contract formatting differences were normalized correctly: Aetna leading zero/coverage suffixes, Lincoln zero padding, and the grouped EyeMed contract versus FT Williams `VARIOUS` are not conflicts.
- All structured brokers were automatic: existing Mercer recipients matched by unique exact address, and the Indemnity Mercer recipient routed to `AUTO_NEW`.
- Zero-fee values were retained as numeric zero; no phantom broker was created for layouts with no broker compensation.
- No FT Williams update was sent. This is the correct result because two source-versus-current conflicts affect the package: sponsor name and the policy/plan-year beginning date. The write count and receipt count are both zero.
- Production read-back remained unchanged, proving the conflict gate prevented a partial or unsafe write.
- Final production acceptance: API revision 95 and worker revision 85, image digest `sha256:4c967c69ae82d1ecb5a344d44e4eec7dd0d4fb4fea76eb0a65f2925d0df5e481`, both `COMPLETED`, health response `{"status":"ok","stack":"react-python-mongodb"}`.

## Real decisions still required

These are two underlying client decisions. The UI can display the date in both Form 5500 and Schedule A locations, but those are duplicate representations of the same source conflict.

1. Sponsor name: the newest Plan Worksheet says `FREEDOM SOLUTION GROUP, LLC`; FT Williams says `FREEDOM SOLUTIONS GROUP LLC`.
2. Beginning date: the Plan Worksheet and all Schedule A source documents say `01/01/2025`; FT Williams Form 5500 and existing Schedule A records say `10/01/2025`. Ending date agrees at `12/31/2025`.

Until these are resolved, the automation must not write otherwise automatic values. This behavior was observed in production.

## Filing-by-filing results

### 1. Litera Aetna — Medical

- Filing ID: `6abd483b270e09d0df21c9f0`; final full-package job: `6ac16b3d6317a5d2e5f4c4ee`.
- Extraction: Aetna Life Insurance Company; EIN `06-6033492`; NAIC `60054`; contract `0186483-Medical`; persons `685`; premium `5,693,303.00`; source dates `01/01/2025–12/31/2025`.
- FT Williams: contract `186483`; beginning date `10/01/2025`; ending date `12/31/2025`.
- Routing: contract `NO_CHANGE` after formatting normalization; premium and persons are automatic where FT Williams is blank; beginning date is a real conflict; ending date is `NO_CHANGE`.
- Broker/fee result: no authoritative Part I broker compensation rows; no phantom Schedule C broker fields were copied.
- FT Williams update/read-back: no write attempted; fresh current-data read remained unchanged.

### 2. Litera EyeMed — Vision

- Filing ID: `6abd4831270e09d0df21c9ed`; final production job: `6ac17ce30fe9ba8316aff62c`.
- Extraction: Fidelity Security Life Insurance Company; EIN `43-0949844`; NAIC `71870`; grouped contract `1040989/90-1001`; persons `612`; premium `39,767.92`; source dates `01/01/2025–12/31/2025`.
- FT Williams: Schedule A sequence `2`; contract `VARIOUS`; beginning date `10/01/2025`; ending date `12/31/2025`.
- Routing: grouped source contract versus `VARIOUS` is `NO_CHANGE`; premium and persons are `WILL_UPDATE`; beginning date is `CONFLICT`; ending date is `NO_CHANGE`.
- Broker/fee result: Mercer Health & Benefits LLC, Dallas TX `75373`; commission `3,488.88`; fee `0`; organization code `3`; `AUTOMATIC`; `AUTO_MATCHED` by unique exact address. All broker source/address/name/column validators passed.
- Final regression: `stored_low=[]`; duplicate flat purpose/organization-code fields no longer create false review items.
- FT Williams update/read-back: `update_attempted_count=0`, no receipt, because the real date conflict blocks the package; fresh query succeeded.

### 3. Litera Aetna — Dental

- Filing ID: `6abd47ec270e09d0df21c9de`; final full-package job: `6ac16c486317a5d2e5f4c574`.
- Extraction: Aetna Life Insurance Company; contract `0186483-Dental`; persons `710`; premium `331,043.76`; source dates `01/01/2025–12/31/2025`.
- FT Williams: contract `186483`; beginning date `10/01/2025`; ending date `12/31/2025`.
- Routing: contract `NO_CHANGE` after normalization; automatic blank-field updates identified; beginning date is the only Schedule A value conflict; ending date matches.
- Broker/fee result: no authoritative Part I broker compensation rows; no phantom broker fields.
- FT Williams update/read-back: no write attempted; current data remained unchanged.

### 4. Litera Lincoln — LTD

- Filing ID: `6abd4824270e09d0df21c9ea`; final full-package job: `6ac16cda6317a5d2e5f4c5b7`.
- Extraction: Lincoln; EIN `35-0472300`; NAIC `65676`; contract `000010233867 00000`; persons `422`; premium `126,256.23`.
- FT Williams: contract `10233867`.
- Routing: contract `NO_CHANGE` after zero-padding/suffix normalization; automatic blank-field values identified; source beginning date conflicts with FT Williams.
- Broker/fee result: commission `6,843.74`; fee `1,692.47`; broker row `AUTOMATIC` and `AUTO_MATCHED`; no validation errors.
- FT Williams update/read-back: no write attempted; current data remained unchanged.

### 5. Litera Lincoln — STD

- Filing ID: `6abd47d8270e09d0df21c9db`; final full-package job: `6ac16d596317a5d2e5f4c5fa`.
- Extraction: contract `000010233868 00000`; persons `422`; premium `72,983.81`.
- FT Williams: contract `10233868`.
- Routing: contract `NO_CHANGE` after normalization; automatic blank-field values identified; source beginning date conflicts with FT Williams.
- Broker/fee result: commission `3,930.46`; fee `994.56`; broker row `AUTOMATIC` and `AUTO_MATCHED`; no validation errors.
- FT Williams update/read-back: no write attempted; current data remained unchanged.

### 6. Litera Lincoln — Voluntary Life

- Filing ID: `6abd47ff270e09d0df21c9e1`; final full-package job: `6ac16dce6317a5d2e5f4c63d`.
- Extraction: contract `000400001000 23109`; persons `88`; premium `39,588.17`.
- FT Williams: contract `40000100023109`.
- Routing: contract `NO_CHANGE` after normalization; automatic blank-field values identified; source beginning date conflicts with FT Williams.
- Broker/fee result: commission `5,938.27`; fee `549.77`; broker row `AUTOMATIC` and `AUTO_MATCHED`; no validation errors.
- FT Williams update/read-back: no write attempted; current data remained unchanged.

### 7. Litera Lincoln — Life

- Filing ID: `6abd480b270e09d0df21c9e4`; final full-package job: `6ac16e456317a5d2e5f4c680`.
- Extraction: contract `000010233866 00000`; persons `422`; premium `82,185.05`.
- FT Williams: contract `10233866`.
- Routing: contract `NO_CHANGE` after normalization; automatic blank-field values identified; source beginning date conflicts with FT Williams.
- Broker/fee result: commission `4,035.74`; fee `1,133.09`; broker row `AUTOMATIC` and `AUTO_MATCHED`; no validation errors.
- FT Williams update/read-back: no write attempted; current data remained unchanged.

### 8. Litera Indemnity

- Filing ID: `6abd481b270e09d0df21c9e7`; final full-package job: `6ac16ec36317a5d2e5f4c6c3`.
- Extraction: Aetna; contract `803136`; persons `211`; premium `45,873.97`; source dates `01/01/2025–12/31/2025`.
- FT Williams: no matching Schedule A record.
- Routing: `AUTO_NEW_SCHEDULE_A`, proving EIN/contract mismatch creates a new Schedule A instead of overwriting an unrelated record.
- Broker/fee result: Mercer Health & Benefits Administration LLC; commission `21,871.85`; fee `0`; `AUTOMATIC`; broker match status `AUTO_NEW`.
- FT Williams update/read-back: creation was not sent because the global sponsor/date conflicts block the package; no partial Schedule A was created.

## Plan Worksheet validation

- The current DOCX was inspected directly, including its XML text rather than relying on filename or OCR.
- Plan sponsor: `FREEDOM SOLUTION GROUP, LLC`.
- Plan year: `01/01/2025–12/31/2025`.
- Text normalization/uppercase rules are applied before FT Williams payload generation.
- The 12-month date constraint is satisfied by the source, but the source beginning date conflicts with the existing FT Williams beginning date.

## Routing-rule validation

- Extracted value present and FT Williams blank: `WILL_UPDATE` was observed for EyeMed premium and persons and across the other blank FT Williams values.
- Both blank: `SKIP_EMPTY`; covered by regression tests and no blank deletion was staged.
- Both present and equivalent after normalization: `NO_CHANGE`; observed for Aetna, EyeMed, and Lincoln contract formats and matching ending dates.
- Both present and genuinely different: `CONFLICT`; observed only for the sponsor name/beginning-date facts described above.
- Structured broker exact match: automatic; observed for existing Mercer rows.
- New unambiguous broker: `AUTO_NEW`; observed for Indemnity.
- New Schedule A: `AUTO_NEW_SCHEDULE_A`; observed for Indemnity.
- No-commission/no-fee: explicit zero is retained and routed without inventing a payment; layouts with no authoritative broker rows do not create phantom brokers.

## Issues found and fixed

1. OCR spelling variant `carriel` prevented recognition of an Aetna financial section.
2. Lincoln broker-row totals did not populate the scalar commission/fee totals used by reconciliation.
3. Contract formatting differences produced false conflicts.
4. Automatic status/confidence changes were not persisted by the repository update path.
5. New Schedule A broker rows used a confirmation-only status instead of `AUTO_NEW`.
6. Authoritative Aetna, EyeMed, and Lincoln layouts retained generic OCR noise, including phantom broker/experience-rated/question-11 fields.
7. EyeMed fields did not consistently retain page-level evidence.
8. EyeMed purpose and organization code were duplicated as filing-level scalar review fields even though the structured broker row was authoritative.

Fix commits, in order:

- `acdc1e2` — Fix Freedom Solutions Schedule A extraction
- `5333944` — Fix broker and aggregate Schedule A routing
- `0b3af8d` — Eliminate false Schedule A review conflicts
- `93e486c` — Remove authoritative layout OCR noise
- `7f9cfb0` — Preserve EyeMed source evidence
- `75522dc` — Remove duplicate EyeMed broker review fields
- `d5e23c9` — Suppress duplicate broker scalar review fields

## Regression and production evidence

- Final focused regression: `256 passed, 4 subtests passed`.
- Final full backend regression: `993 passed, 2 skipped, 68 subtests passed`; one third-party GroundX SDK deprecation warning only.
- Full eight-filing production reprocess task: `6560bcadf3234db2b28f0e7ad3f3ed79`; all eight jobs logged as reprocessed.
- Eight-filing evidence audit task: `2dce25a11a33407f80dbd634341b85e6`; exit code `0`.
- Final EyeMed acceptance task: `c4ba20e726074d679ac4aa45156eb022`; worker revision `85`; exit code `0`; FT Williams query success `true`; `stored_low=[]`; write attempts `0`.
- Production build: `erisapros-production-backend:b292bdf4-718a-4117-94d4-b8c59026534e`; `SUCCEEDED`.
- ECS: API `erisapros-production-api:95` and worker `erisapros-production-sharefile-worker:85`; both one running, zero pending, rollout `COMPLETED`.
- Health: `https://d3axcdlq9aydpw.cloudfront.net/api/health` returned `{"status":"ok","stack":"react-python-mongodb"}`.

## Final disposition

The client flow is validated and the fixes are live. The automation is ready to apply the staged automatic values after a human resolves the two genuine source-versus-FT-Williams facts. Current automatic writes applied: **0**, intentionally. This is not a missed update; it is the verified fail-closed result required to prevent a partial update while the sponsor name and beginning date are unresolved.
