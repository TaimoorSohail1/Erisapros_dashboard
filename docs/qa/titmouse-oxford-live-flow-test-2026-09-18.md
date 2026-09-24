# Titmouse and Oxford live flow test

Date: 18 September 2026. Filing year: 2025. Result: **partial pass; the six-file missing-year Bring Forward test was not completed.**

## Outcome

| Check | Result | Evidence |
| --- | --- | --- |
| Titmouse ShareFile upload and automatic intake | Passed | Fresh labelled copy, discovered by SHAREFILE_WEBHOOK_FILE in 12.59 seconds |
| Extraction completion | Passed | GroundX structured extract + X-Ray + local parser, paired automatically with the existing worksheet; 113.07 seconds |
| Checked Schedule A source accuracy | Partial failure | Covered count, identifiers, amounts and broker matched; both policy dates were wrong |
| Blank organizational code defaults to 3 | Passed for this source | Scalar and structured broker both contain 3; structured row marks organization_code_defaulted=true |
| Highest lives covered across multiple candidates | Not exercised | This source provides one covered-person count, 36; this does not prove the multi-row maximum rule |
| Existing current-year FTW query | Passed | Current query complete/successful, correct Titmouse IDs, three 2025 records; no Bring Forward needed |
| Pause/Resume through live dashboard | Passed while agent idle and cloud extraction active | Agent browser closed, resumed signed-in readiness; all 14 baseline personal Chrome processes survived |
| Real pending/active Bring Forward pause and duplicate prevention | Not exercised | No local-agent job was needed for Titmouse; Oxford uploads were stopped at preflight |
| Packaged connection-preserving update/rollback | Passed in isolated installation | Actual public 0.4.1 executable; protected file/profile preservation and rollback checks passed, no real credential/vendor requests |
| Focused agent regression suite | Passed | 72 tests passed in 8.43 seconds |
| Six-file Oxford Bring Forward | Blocked before upload | Old saved mapping is not the approved TEST plan; read-only query using saved IDs returned error 55 |
| Automatic sending / real field updates | Not tested; deliberately off | No Send, approval, field-decision override or vendor field update performed |

## Boundaries and preparation

User approved live testing through the automatic flow, including the existing-year and missing-year cases. No product code or production configuration was changed. Diagnostic FTW queries were read-only and did not advance filings. No manual Query, Bring Forward, Retry, Resolve & Continue or Send button was used. Pause/Resume applied only to DESKTOP-D9JV7IA on 0.4.1. EP-PF43NS4S remained unchanged on 0.3.2 with the same last-seen timestamp. Automatic sending remained disabled.

Live ShareFile contents were checked through the service API instead of assuming old index records or screenshots were current. Oxford has five distinct usable source contracts (BCBSMA spreadsheet, Delta Dental, EyeMed, UNUM 918412 and UNUM 918413). It also contains a duplicate UNUM 918412 PDF, prior QA copies and a DNU-marked 918414 file. Those were not uploaded. A six-input test needs the sixth input explicitly defined as another valid source or a deliberate duplicate-idempotency case; it must not be described as six distinct contracts if a duplicate is used.

## FTW baselines and Oxford blocker

Read-only 2024/2025 baselines established:

- Titmouse: customer `2400886723`, plan `2947075991`, EIN `95-4796640`, plan number 501. Three records in both 2024 and 2025.
- Approved Oxford TEST plan: customer `2402926798`, plan `2950081973`, EIN `87-4619719`, plan number 501. Five records in 2024; 2025 query returned missing-schedule code 59.
- FTW PlanIDs_Batch also returned another Oxford plan with the same EIN/plan number: customer `2382315514`, plan `2922871225`, named Oxford Biomedica SolutionsL LC Health and Welfare Benefit Plan. EIN/plan number alone is therefore not a unique target.
- The saved 2025 Oxford mapping points to different, old IDs: customer `1975777651`, plan `2401132428`, and the Solutions plan name rather than the approved TEST plan. Read-only query against those saved IDs returned success=false/error code 55.

The invalid saved mapping and multiple live candidates are confirmed preflight findings. This run does not claim that a fresh automatic lookup actually selected the wrong plan: Oxford was not uploaded or advanced, and no route was overridden. Correcting/verifying the target mapping is required before safely certifying the missing-year test. A mapping correction would change production routing and was not silently included in this testing-only request.

## Fresh Titmouse execution

Original source: `2. Kaiser.pdf`, ShareFile item `fie08d28-e7ee-1199-f5bf-4ff4530e5ff1`.

Uploaded byte-identical test copy: `AUTO-QA-20260918-EXISTING-Titmouse-Kaiser-Schedule-A.pdf`, item `fi31b869-5bfa-18aa-ba71-70fa4dd40109`, in the approved Titmouse Test Schedule A folder. The file was automatically paired with `5500 Plan Worksheet  Titmouse, Inc. 5500 - PY25.docx`.

Test filing: https://d3axcdlq9aydpw.cloudfront.net/filings/6aad3fa7937e95e71bbb88ba

| Event | UTC time / elapsed |
| --- | --- |
| Upload complete | 13:41:46.803645 |
| Filing created | 13:41:59.395142; 12.59 seconds after upload |
| Extraction started | 13:42:01.878 |
| Extraction completed | 13:43:54.947; 113.07 seconds after start |
| Final review updated | 13:44:08.442; 141.64 seconds after upload |

Final filing state: NEEDS_REVIEW / ACTION_NEEDED. Review matched the correct Titmouse plan and year 2025; current_query_complete=true, current_query_success=true, current_year_exists=true, bring_forward_required=false. No local-agent Bring Forward jobs were created, as expected for this existing-year case. update_attempted_count=0 and update_confirmed_count=0.

Live UI showed FTW Match: Matched, 36/40 fields found and seven attention fields. It showed the enabled Send button; it was not clicked. The seven attention fields included missing participant data, uncertain nonexperience-rated derived values and a premium difference versus current FTW. These review decisions are acceptable outcomes, not automatic-send success or evidence that extraction is perfect. There were 58 mapped fields before contract-specific exclusions; that raw count is not the final UI applicable-field total.

## Independent source checks

The actual downloaded PDF's relevant pages 3, 4 and 5 were rendered and visually reviewed, alongside text extraction. Worksheet-only fields were not independently scored; no overall accuracy percentage is claimed.

| Source item | Output | Result |
| --- | --- | --- |
| Carrier | Kaiser Foundation Health Plan, Inc. | Matched, allowing punctuation differences |
| Carrier EIN | 94-1340523 | Matched |
| NAIC | 00000 | Matched; leading zeros preserved |
| Policy number | 271131 | Matched |
| Persons covered | 36 | Matched |
| Premium | 291,327.52 | Matched |
| Commission | 14,404.20 | Matched |
| Fees | 0.00 | Matched |
| Broker | Nth Insurance Agency; 10833 VALLEY VIEW ST STE 550, CYPRESS, CA 90630-5056 | Matched |
| Empty broker slots | None / zero template rows | Correctly ignored; one real structured broker |
| Organization code | Not supplied in source | Correct customer-rule default 3 |
| Policy beginning and ending dates | Both output 03/12/2026 | **Wrong: source states contract period 01/2025–12/2025. March 12, 2026 is the letter/certification date. Exact policy days are not printed.** |

The wrong dates have confidence 50%. Existing FTW already has the same wrong dates, so comparison reports changed=false and these dates do not appear among the seven default attention fields. Matching existing data does not establish source accuracy. This run did not send or correct those values.

## Agent controls and update checks

Pause was clicked during cloud extraction, with no active local-agent job. The desktop acknowledged PAUSED/browser_ready=false at 13:42:24.992 UTC. Dedicated-profile browser process count was zero; all 14 observed personal Chrome baseline PIDs remained alive. Cloud extraction continued. Resume reopened the dedicated profile and acknowledged CONNECTED/browser_ready=true/pause_requested=false at 13:43:27.320 UTC, using the same device identity and saved session.

The actual published installer, SHA256 `3327C51C5DB975C2AF1CA175DCB5BDCBD05428264292545CCBE07295AB4C2944`, passed the isolated packaged smoke: version 0.4.1, embedded sources match, isolated update, DPAPI preservation, profile preservation and packaged rollback. Synthetic startup was not executed, and this harness made no real credential or vendor requests. The real desktop was already updated and was not reinstalled during this run. Prior actual desktop upgrade evidence is in `ftw-agent-public-installer-release-2026-09-18.md`.

Personal-process survival proves the agent did not close those browsers, not that simultaneous FTW logins in personal browsers cannot be invalidated by vendor session restrictions. Live mid-click pause and queued-job resume were not exercised here.

## Preservation and next required steps

Read-only FTW read-back at 13:44:38–13:44:41 UTC confirmed canonical 2024 and 2025 records unchanged for both clients. Original ShareFile files were not changed or deleted. The new Titmouse QA copy and filing remain available for review; no cleanup was performed.

To complete the requested six-file test:

1. With explicit routing-change approval, verify/correct Oxford's 2025 mapping to the approved TEST customer/plan, excluding the other live Oxford plan.
2. Agree on the sixth valid input or an explicitly labelled duplicate case; exclude DNU and old QA copies.
3. Upload the batch, observe automatic Bring Forward once for the shared target, test pause with genuinely queued/active work, verify every filing and prior-year preservation, and report measured queue/browser/read-back timings.
4. Investigate the policy-period versus certification-date extraction error separately. Do not equate unchanged FTW comparison values with correct extraction.

Real automatic sending remains deferred and off. This report is intentionally a partial result, not certification of the complete six-file or automatic-send flow.

Supporting sanitized snapshots and test scripts: `tmp/automation_six_20260918/`.
