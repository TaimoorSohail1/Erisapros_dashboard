# Salesloft TEST: 40-case live UAT

Test date: 18 September 2026. Result: **36 Pass, 2 Fail, 2 Blocked**. No all-pass workbook generated because the requested gate was not met. Customer tests remain Pending.

## Scope and setup

- Live ShareFile intake, extraction, automatic FTW matching, review controls, Send preview/cancel, idle Pause/Resume and one automatic Bring Forward.
- Approved target: Salesloft, Inc. Health And Welfare Benefits Plan test. EIN 45-3274471, plan number 501, FTW customer 2435381553, plan 2994705147, year 2025.
- Desktop: DESKTOP-D9JV7IA, Agent 0.4.1. Other connected computer untouched.
- Source: `2. SalesLoft, Inc_FLX970245_GBA APIR_2025.pdf`. Both labelled uploads used identical bytes, SHA256 `0175419dfba5549ccf4382a999c1b3cff4bcba268128423491500157f2e866cb`.
- Before testing: two 2024 Schedule A records, no 2025 Schedule A records, agent paused, no runnable account jobs, automatic field sending disabled.
- No approval action, real FTW field send, forced Schedule A selection, manual Bring Forward, extraction retry, credential change or application deployment.

## Runs and finding

1. Missing-year copy uploaded at 15:33:59 UTC. Filing `6aad5a0df9dfc231af393299`. Extraction completed at 15:37:25 UTC on its first attempt. UI reported 38/40 fields found, with ordinary review decisions remaining. Correct existing contract `FLX0970245` selected after Bring Forward.
2. Bring Forward job `6aad5ad52b50f3176b05374a` queued while paused, ran after Resume, attempts **1**, status **VERIFIED**. Independent FTW query confirmed **two 2025 records**. The complete 2024 response and parsed statuses remained unchanged. This was a native year copy, not reviewed field sending.
3. Existing-year copy uploaded at 15:41:58 UTC. Filing `6aad5bd5f9dfc231af3932e7`. Extraction completed at 15:46:44 UTC on its first attempt. UI reported 27/40 fields found. **Carrier EIN, NAIC, contract number and coverage dates were blank in extracted output.** The automatic query returned two 2025 candidates but could not safely select either. UI showed FTW Match Pending and “none passed the safe identity match.” No duplicate Bring Forward job was created.

The observed failure is inconsistent extraction of the same source bytes, followed by the correct protective matching pause. The underlying reason for the extraction difference is not yet determined. This is not evidence that FTW IDs or the Bring Forward operation failed. No manual selection was used to turn the result into a pass.

## Results

Positive results were exercised on the first fresh filing, with the second fresh filing additionally checking existing-year intake and automatic matching. Matching-dependent checks are marked Fail/Blocked when the second run could not meet the expected happy-path outcome. A pass here does not certify every extracted value, real sending, or all possible client setups.

| # | Check | Result |
|---|---|---|
| 1 | Open ShareFile Intake | Pass |
| 2 | Upload a test Schedule A | Pass |
| 3 | Find uploaded file in ShareFile | Pass |
| 4 | Find new filing in ERISAPros | Pass |
| 5 | Correct client group | Pass |
| 6 | Plan Worksheet linked automatically | Pass |
| 7 | Open filing | Pass |
| 8 | View workflow progress | Pass |
| 9 | Processing finishes | Pass |
| 10 | Review table loads | Pass |
| 11 | Open All Fields | Pass |
| 12 | Extracted values visible | Pass |
| 13 | Matched Current FTW values visible | Blocked: second run has no safe Schedule A selection |
| 14 | Proposed values visible | Pass |
| 15 | Action Required items visible | Pass |
| 16 | Will Update FTW changes visible | Pass |
| 17 | Broker information visible | Pass: first run |
| 18 | Refresh preserves extracted results | Pass |
| 19 | Automatic FTW query loads data | Pass: returned candidates without manual query |
| 20 | FTW Match shows Matched | Fail: second run stayed Pending |
| 21 | Correct FTW client | Pass |
| 22 | Correct FTW plan | Pass |
| 23 | Correct year | Pass |
| 24 | Existing Schedule A records available | Pass: two candidates |
| 25 | Expected Schedule A selected automatically | Fail: second run could not safely select |
| 26 | Matched current and extracted values side by side | Blocked: second run has no safe Schedule A selection |
| 27 | Refresh current FTW data, same target retained | Pass: first run, current button is Refresh FTW Data under Workflow progress → FTW loaded |
| 28 | Review explanations visible | Pass |
| 29 | Proposed changes listed | Pass |
| 30 | Unchanged values stay unchanged | Pass: first run, SAME filter showed contract and dates |
| 31 | Search for a field | Pass |
| 32 | Apply and clear filter | Pass |
| 33 | Send available without approval | Pass |
| 34 | Open Send confirmation without sending | Pass |
| 35 | Select/unselect changed field | Pass: selected count 4 → 3 → 4 |
| 36 | Cancel confirmation without sending | Pass |
| 37 | Agent connection status visible | Pass |
| 38 | Pause idle agent | Pass: browser closed, connection retained |
| 39 | Resume agent | Pass: dedicated browser reopened, Connected and ready shown |
| 40 | Single missing-year Bring Forward | Pass: one attempt, independently verified |

## Safety and final state

- Both filings had zero FTW update attempts and zero confirmed field updates. Automatic sending remains disabled.
- Agent restored to **Paused · browser closed**, with connection retained. Dedicated BrowserProfile Chrome process count was zero at 15:46:40 UTC. Other connected device untouched.
- Original ShareFile documents unchanged. The two labelled QA copies and test filings remain for investigation. The approved native copy created the two current-year Schedule A records; no year reset/deletion was performed.
- Same-username personal-browser login conflict remains unresolved and was excluded. Dedicated-profile reopening is not proof that concurrent FTW logins are safe.
- Idle Pause/Resume and a queued job waiting through pause were tested. Pausing during an active Bring Forward and recovery after interruption were not tested in this 40-case happy-path suite.
- Case 40 needs a freshly prepared missing-year test plan/year for any client rerun. This Salesloft 2025 plan now already has records and cannot reproduce the original missing-year starting state without a separate approved setup.

## Next step

Investigate why the second extraction omitted Schedule A identity fields, then retest a fresh existing-year upload. Generate the single-tab Testing Matrix `.xlsx` only after the remaining four outcomes pass. Real sending and production automatic-send activation remain deferred.

Evidence: `tmp/salesloft_uat_20260918/missing-manifest.json`, `existing-manifest.json`, `observations.json`, `ftw-baseline.json`, `ftw-readback.json`, `resume-preflight.json` and `test-results.json`. UI actions and state observations are recorded in the task history. No secrets are included in this report.
