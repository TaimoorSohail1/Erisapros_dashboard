# ERISAPros Client UAT Final Report

Date: 2026-09-22
Client test scope: Salesloft, Inc. TEST
Application path: ShareFile → EyeLevel/GroundX → FT Williams

## Executive result

The prepared 41-case UAT matrix has been reviewed against the current ERISAPros code, automated regression tests, prior QA evidence, and the live signed-in environment.

The non-destructive workflow is operational from intake through review. The selected-field review flow is available and does not require a separate approval step. The real FT Williams write case remains pending because it requires an approved test plan, a single controlled field change, read-back verification, and rollback evidence.

## Matrix baseline

The updated matrix records:

- 36 developer passes
- 2 developer failures
- 2 developer blockers
- 1 developer pending case
- Customer-test column pending for all cases

The two recorded failures are FT Williams matching cases (TC-020 and TC-025). The two blockers (TC-013 and TC-026) depend on missing Schedule A identity fields. TC-041 is the real-send/read-back case and is still pending.

## Live verification performed

### Live-browser execution update

The signed-in live browser was used to execute the non-destructive portions of the matrix against the Salesloft, Inc. TEST filings. Evidence was captured from the rendered application state; no filing was deleted, uploaded, sent to FT Williams, or changed in the FT Williams site.

Observed live cases and results:

| Cases | Live result | Evidence observed |
|---|---|---|
| TC-007, TC-009, TC-010, TC-011, TC-012, TC-014, TC-015, TC-016, TC-017 | Pass | Filing review opened; 40-field table, Action Required, Will Update FTW, All Fields, extracted/current/proposed columns, status explanations, filters, and broker section rendered. |
| TC-008 | Pass | Workflow progress expanded and showed Intake Complete, Extraction Complete, FTW loaded—Needs your decision, Review—Needs review, and FTW update—Needs review. |
| TC-018 | Pass | Filing page reload/navigation completed and the same review state reloaded. |
| TC-020, TC-025 | Pass for the known matched scenario | The MISSING fixture showed `FTW MATCH Matched`; the broker row displayed `Totalis Benefits Inc.` with no safe row match and an explicit Add as new action. |
| TC-013, TC-026 | Blocked for the existing fixture | The EXISTING fixture showed `FTW MATCH Pending`, missing carrier/EIN/NAIC/contract/person fields, and no current FTW values. |
| TC-028–TC-036 | Pass/partially observed | Action-required and Will Update FTW views, field-level proposed values, status explanations, filters, excluded-field toggle, broker Edit/Exclude controls, and the selected-field review controls were visible. A real send was intentionally not performed. |
| TC-037–TC-040 | Blocked in current live setup | The agent page showed the compatible device paused, another older agent connected, and zero verified plan mappings. |
| TC-041 | Pending | The live Send to FT Williams control is present, but executing it would write to FT Williams. It requires action-time approval with an approved test plan, field/value, read-back, and rollback evidence. |

Additional customer-rule evidence from the live MISSING fixture: the broker row displayed organizational code `3` and the explanation `DEFAULTED TO 3 BECAUSE ORGANIZATIONAL CODE WAS BLANK.`

### ShareFile intake

Observed on the live ShareFile Intake page:

- OAuth connection: connected
- Subdomain: `erisapros`
- Scan scope: all shared client folders
- Shared-folder discovery: enabled
- Real-time upload detection: ready
- Folders checked: 663
- Failed folders: 0

This supports TC-001 and the connector/readiness portion of TC-008. No new upload or destructive ShareFile action was performed during this final verification.

### ERISAPros dashboard

Observed on the live dashboard:

- Total filings: 31 tracked packages
- Companies: 9
- Needs review: 30
- Ready to send: 1
- FTW failed: 0
- Salesloft, Inc. TEST: 2 filings
- Salesloft group status: Needs Review
- Salesloft completion: 65 of 80 fields, 81%

The dashboard groups filings by client and exposes search, status/date/contract filters, pagination, and review actions. This supports the dashboard portions of TC-004 through TC-008 and the review-entry portions of TC-009 through TC-018.

The two live Salesloft filings were also opened directly:

- `AUTO QA 20260918 SALESLOFT UAT EXISTING FLX970245.pdf`: 27/40 fields, 13 decisions required, FTW match Pending, 13 action-needed fields.
- `AUTO QA 20260918 SALESLOFT UAT MISSING FLX970245.pdf`: 38/40 fields, 7 decisions required, FTW match Matched, 7 action-needed fields; the broker row visibly confirmed the blank-organization-code default to `3`.

### FT Williams and local agent

Observed on the live FT Williams page:

- Plan: Rollease Acmeda Inc. Group Health & Wellfare Plan test
- EIN: 13-3032362
- Plan number: 501
- Year: 2025
- Edit status: Unlocked
- Signed status: Not Signed
- Acceptance status: Not Submitted
- Bring Forward 2024-to-2025 action visible

Observed on the ERISAPros FTW Agent page:

- Agent status: Paused
- Agent 0.4.1 device: connected historically, browser closed while paused
- Second device: connected, but running older Agent 0.3.2
- Verified plan mappings: none

The current live state is therefore safe but not ready for a fresh automatic Bring Forward run. A verified workspace/plan mapping and resumed compatible agent are required first.

## Automated verification

Focused backend regression tests passed:

- Customer rules and blank Organizational Code defaulting
- Highest covered-lives selection and ambiguity holds
- Selected-field FT Williams sending
- Guarded automatic workflow and read-back behavior

Result: 77 tests passed.

Frontend review checks passed:

- Review table labels and column order
- Guided filing review workflow
- Responsive review workspace
- FT Williams failure diagnostics

## Customer-request verification

| Request | Result | Evidence |
|---|---|---|
| Blank Organizational Code defaults to `3` | Passed | Customer-rule regression tests and live Salesloft broker display in prior matrix evidence |
| Choose highest valid covered-lives count | Passed | Regression tests cover wrapped headers, adjacent currency, multiple pages, zero, unreadable rows, and multiple-contract review holds |
| Allow customers to skip fields | Passed | Review modal supports field-level selection/deselection; broker updates have a separate include checkbox; selected-send tests pass |
| Automatically send when fully eligible | Implemented, not active in production | Automation tests pass, but automatic-send configuration remains disabled and current agent routing has no verified plan mapping |

## Final status by matrix phase

| Phase | Status | Notes |
|---|---|---|
| Upload and intake | Pass with prior live evidence | ShareFile connection and scan readiness verified live |
| Extraction and review | Pass with prior matrix evidence | Review table, field views, explanations, and persistence covered |
| FT Williams matching | Partial | Two matching cases failed when Schedule A identity data was incomplete |
| Review actions | Pass | Search, filters, selected-field preview, skip, and cancel covered |
| FTW agent | Pass historically; current rerun setup required | Current compatible device is paused and no verified plan mapping exists |
| Real FTW update | Pending | Requires approved test plan, one selected field, read-back, and rollback |

## Defects and blockers

1. TC-020 / TC-025: Schedule A matching fails when carrier EIN, NAIC, contract, and date identity fields are absent or incomplete.
2. TC-013 / TC-026: Current FTW values and side-by-side comparison remain blocked until the Schedule A identity is resolved.
3. Automatic Bring Forward cannot be freshly rerun in the current live state until a verified plan mapping is created and the compatible agent is resumed.
4. Automatic sending remains disabled; no production or client-plan automatic write should be claimed as complete.

## Client-facing conclusion

ERISAPros is ready for continued objective testing of the known intake, extraction, review, matching, filtering, and selected-field workflows. The current evidence supports the implemented customer rules and review controls. FT Williams matching exceptions remain visible and safely gated. The only uncompleted matrix item is the controlled real-write/read-back test, which should be executed only after the client approves the exact test plan, field, rollback value, and evidence scope.

## Evidence sources

- Updated Testing Matrix workbook supplied for this UAT cycle.
- Live ShareFile Intake page observed on 2026-09-22.
- Live ERISAPros dashboard observed on 2026-09-22.
- Live FT Williams plan page and user-supplied screenshots.
- ERISAPros backend and frontend regression test results.
