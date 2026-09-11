# FT Williams Automation Production Canary Report

Date: 2026-09-11  
Branch: `codex/automated-ftw-workflow`  
Production UI: `https://d3axcdlq9aydpw.cloudfront.net`

## Outcome

The guarded FT Williams automation release was deployed successfully. The existing manual workflow remains available. Automatic Bring Forward is enabled only for five explicitly allowlisted HighlandTech demo plans. FT Williams Schedule A updates and automatic sending remain disabled, so the release cannot send filing changes while update permission and the final update/read-back tests are pending.

The live Bring Forward flow was completed successfully for the BTIG demo plan. The local agent opened the exact verified plan, used FT Williams' native plan-only Bring Forward action, and the backend confirmed the new current-year Schedule A records through ftwLink. No Schedule A update or automatic send was performed.

## Release identifiers

| Item | Result |
| --- | --- |
| Git commits | `1f5a408`, `740458a`, `f361277` |
| CloudFormation stack | `erisapros-production` — `UPDATE_COMPLETE` |
| API service | `erisapros-production-api:17` — 1/1 running |
| Worker service | `erisapros-production-sharefile-worker:12` — 1/1 running |
| Container image digest | `sha256:d2bacf702fb12da4139941799a17c8ddf528bbf09b113db4af093b8cd5792304` |
| Backend CodeBuilds | `50a21c93-cc4d-4753-a9da-bdb39cc7ce71`, `ce1b7dc0-e7cb-4717-aee0-9969e4c87d4d`, `581cfb95-f018-40dc-ad74-8327973cbb92` — succeeded |
| CloudFront invalidation | `IC3QKYI9NRWMCZHBPMMGJ1P3ZU` — completed |
| API health | `ok` — `react-python-mongodb` |

## Safety configuration

| Setting | Deployed value | Effect |
| --- | --- | --- |
| Automation engine | Enabled | Runs guarded discovery, matching, validation, and exception handling |
| Automatic Bring Forward | Enabled | Available only to the five allowlisted demo targets |
| Client-local FTW agent | Enabled | Accepts jobs only after device and HighlandTech account verification |
| FTW Schedule A updates | Disabled | Prevents filing updates |
| Automatic send | Disabled | Prevents unattended sending |
| FTW account | `HighlandTech` | Required account identity for the canary |

Plans outside the allowlist continue through the existing manual-review workflow. A missing or malformed allowlist fails closed to manual review. The agent verifies the account, exact plan, EIN, plan number, year, and saved mapping before clicking Bring Forward.

## Defects found and corrected

1. A nonallowlisted filing could be surfaced as an automation exception instead of returning cleanly to the manual workflow. It now returns `DISABLED / MANUAL_REVIEW`.
2. Local-agent completion could outlive the CloudFront request limit. The agent completion timeout is now 115 seconds, below CloudFront's 120-second limit.
3. Pairing expiration dates were serialized as strings in MongoDB, causing valid one-time codes to appear expired. Pairing, device, and job documents now store native BSON dates.
4. Three demo plans use different ftwLink internal IDs and FT Williams browser IDs. The policy now accepts the separately verified browser-ID pair while retaining the internal ftwLink mapping for data queries.
5. Browser state is kept on the user's machine in a persistent encrypted local profile rather than uploaded or repeatedly recreated.

## Automated verification

| Verification | Result |
| --- | --- |
| Backend suite | 608 passed, 2 skipped, 39 subtests passed |
| Backend warning | One GroundX SDK deprecation warning; unrelated to this release |
| Final focused automation tests | 74 passed |
| Frontend typecheck | Passed |
| Frontend performance checks | Passed |
| Review workspace UI checks | Passed |
| Dashboard UI checks | Passed |
| Field Rules UI checks | Passed |
| ShareFile UI checks | Passed |
| Production frontend build and smoke test | Passed |
| CloudFormation template validation | Passed |
| FTW profile verification | 5/5 plans passed before restart and 5/5 after restart |
| Agent idle/idempotency check | Passed; no duplicate Bring Forward job was created |
| Existing manual-flow regression | Passed in automated tests and live UI inspection |

## Five-plan canary results

| Demo plan | Browser IDs | ftwLink IDs | Result |
| --- | --- | --- | --- |
| American Securities LLC Health And Welfare Plan | `2433896470 / 2992990799` | Same mapping | Existing current-year records found and matched. Validation correctly stopped for fields below the 95% automation threshold. |
| BTIG, LLC Health And Welfare Plan | `2402914769 / 2950067216` | Same mapping | Missing current-year Schedule A detected. Automatic Bring Forward completed and ftwLink verified records `1-6`. Validation then stopped safely for organization code/low-confidence data. |
| FGF, LLC Employee Benefits Plan test | `2429100964 / 2986383641` | `1870755347 / 2262415502` | Current-year records found and matched. Browser/internal mapping separation verified. Two broker rows matched independently. |
| The Barry Robinson Center Health And Welfare Plan | `2449411222 / 3012303660` | `2424918262 / 2980764197` | Current-year records found and the correct record matched. Six missing fields correctly remain Action Needed. Existing query and manual actions remain available. |
| Special Service for Groups Health and Welfare Plan | `2446910296 / 3009073880` | `1306908650 / 1599332448` | Current-year records found and matched. Existing blocking premium variance remains visible and prevents unsafe automation. |

## BTIG live Bring Forward evidence

- Before Bring Forward: no current-year Schedule A record IDs.
- The queued job contained the HighlandTech account, exact BTIG plan, EIN `04-3695739`, plan number `501`, year `2025`, and verified plan URL.
- The local agent clicked FT Williams' native plan-only Bring Forward action once.
- Job status: `VERIFIED`; attempts: `1`.
- ftwLink verification message: the new current-year Schedule A records were verified.
- After Bring Forward: record IDs `1, 2, 3, 4, 5, 6`.
- The dashboard selected the best match and matched the broker to row 1 by unique broker name.
- A second idle run made no further change; the job remained at one attempt and the IDs were unchanged.
- Validation stopped at Action Needed because the organization code and some confidence values were not safe for unattended sending. This is expected safety behavior, not an automation failure.

## User experience verified

The deployed review screen presents the simplified automation states: Processing, Completed, Action Needed, and Failed. It shows the seven workflow stages and a clear next action. When the local agent is available, the UI reports `Local FT Williams agent: Connected`. When it is unavailable or the saved login needs attention, the existing `Open FTW` and `Open FTW Bring Forward` manual controls remain available.

The detailed comparison, candidate records, broker matching, technical mapping details, Query FTW, and manual fallback actions remain available for exceptions. Safe filings require no dashboard approval once automatic sending is eventually enabled; uncertain matches and validation issues continue to stop at Action Needed.

## Current local-agent state

- Paired to the deployed production API for the HighlandTech demo canary.
- Persistent browser profile: `C:\Users\Hp\.erisapros-secure\ftw-local-agent-profile-v1`.
- Device credential: protected locally with Windows DPAPI.
- Agent is running in the background and the dashboard reports it as connected.
- The current canary runner is source-based on this test computer. It is not yet a signed, auto-starting Windows installer and will need to be restarted after a computer reboot.

## Remaining work before full unattended sending

1. FT Williams must grant update permission to the HighlandTech demo KeyID.
2. Enable updates only in the demo canary and run update, read-back, sibling-preservation, and controlled restore tests.
3. Resolve every failed test, then rerun the complete backend, frontend, manual-flow, and five-plan regression suites.
4. Enable automatic sending only after the live demo update/read-back/restore suite passes and the result is approved.
5. Obtain a Windows code-signing certificate, build the signed client installer, and verify install, auto-start, upgrade, logout, and uninstall behavior before broad client rollout.
6. Optional polish: suppress the harmless Playwright cleanup traceback when the development runner is stopped with Ctrl+C.

No production customer filing was updated or sent during this release. The only FT Williams mutation was the authorized native Bring Forward on the BTIG HighlandTech demo plan.
