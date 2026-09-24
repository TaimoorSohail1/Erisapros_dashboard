# Desktop FTW Agent 0.4.1 canary

Date: 2026-09-18. Scope: user-approved unsigned update on DESKTOP-D9JV7IA only. Result: **installation passed; live control acceptance failed; end-to-end vendor testing is incomplete**.

Follow-up: the user subsequently approved the dashboard fix/deployment. The control defect was corrected and live tests continued; see [production control-request release and acceptance](ftw-agent-control-transport-release-2026-09-18.md). The paused handoff below records the earlier stage, not the final current state.

## Installation and safety evidence

- Before maintenance, primary MongoDB reads showed desktop device `6aad1aa44c41c6eb8f5ddd9c`, version 0.3.2, no active reservation and no jobs associated with that device. Account-wide eligible pending and claimed jobs were subsequently verified as zero.
- Legacy 0.3.2 cannot acknowledge the new runtime maintenance marker. A narrowly scoped operator migration hold atomically set `pause_requested=true` only where the desktop was unrevoked, version 0.3.2, unpaused and `active_job_id=null`. This uses the same exclusion predicate as Mongo claim reservation: an already reserved job would make the hold fail. The version/capability gate was not falsified. The hold is not proof of legacy browser closure; closure was separately checked.
- After a verified updater backup existed and primary reads reconfirmed no work, the exact baseline idle runtime child PID 10964 was stopped. Its PyInstaller parent exited and its dedicated Chromium processes disappeared. No image-name-wide kill, personal-browser termination, pairing, credential replacement, or other computer mutation was performed.
- The first updater attempt encountered WinError 5 at atomic executable replacement. It failed without replacing the old executable; its previous digest was verified intact. A retry after shutdown completed succeeded. A transient shutdown/file lock is consistent with this outcome, but no handle trace established the exact lock owner.
- Final installed SHA256: `3327C51C5DB975C2AF1CA175DCB5BDCBD05428264292545CCBE07295AB4C2944`; Authenticode NotSigned, restricted approval already supplied. Installed executable `--version` reports `ERISAPros FT Williams Agent 0.4.1`.
- Successful backup: `C:/Users/Hp/AppData/Local/ERISAPros/FTWLocalAgentBackups/update-f3bfe180f33b42399603e991131047c5`. The first failed attempt's independent verified backup also remains available.
- `device.credential`, `ftw-login.credential` and `start-agent.vbs` remain byte-for-byte hash-identical to the backup. The dedicated BrowserProfile directory was retained, not replaced. No real credentials were decrypted by QA scripts or printed.
- Live heartbeat reports the same device ID, version 0.4.1, PAUSED, `pause_requested=true`, `browser_ready=false`, no active job. The UI shows 0.4.1 and enables Resume. There are zero processes whose command line uses the dedicated BrowserProfile.
- EP-PF43NS4S remains unchanged at 0.3.2, with its original last-seen timestamp. Public installer publication and AWS service/configuration deployment were not performed. Production API task definition 26 explicitly retains automatic sending false.

## Failure found by live acceptance

Clicking the desktop's enabled **Resume Agent** button produced `Value is not valid for FT Williams.` The device stayed PAUSED and no dedicated browser opened. This is a failed live acceptance test, not automatic success.

The dashboard's `setFTWLocalAgentPaused` passes a JSON-string body without a Content-Type header. The shared request helper creates Headers but does not supply a JSON Content-Type. Browser fetch uses text/plain for this string body. The API requires a structured `FTWLocalAgentControlRequest`.

An isolated differential against the actual local FastAPI app with a synthetic MemoryRepository/device reproduced:

| Transport | Result |
| --- | --- |
| Dashboard-equivalent string body, text/plain | HTTP 422 |
| Identical body, application/json, Pause | HTTP 200 |
| application/json, Resume | HTTP 200 |

The frontend also maps validation detail arrays to its generic invalid-FTW-value error, hiding the transport problem. The live UI symptom plus this deterministic API differential establishes the request-format defect. A raw production HTTP response capture was not collected.

Minimal next change: specify `Content-Type: application/json` for this device-control request, add a transport-level regression, validate/build the frontend and deploy only the accepted frontend change. No agent rebuild, reconnect, FTW credential change or automatic-send activation is required for that defect. This report does not imply deployment approval or claim that the change is already implemented.

Reproduction helper: `tmp/desktop_agent_canary_20260918/probe_control_content_type.py`. Maintenance/preflight helpers in that directory intentionally avoid logging connection strings, tokens, passwords or secret payloads.

## Test classification

- Focused update/drain/Pause/Resume/Mongo/browser-isolation regressions: **72 passed in 10.75 seconds**. These include synthetic active-work completion, pending retention, acknowledgement retry, duplicate exclusion, offline/lease and profile boundaries; they are not live pending-job or vendor tests.
- Frontend agent-settings and review layout/diagnostic/responsive checks: **passed**. Existing UI checks did not detect this missing transport header; their pass is not control integration evidence.
- Fresh full backend suite: **795 passed, 2 skipped, 64 subtests passed in 54.00 seconds**. One existing GroundX SDK deprecation warning. This is regression evidence, not proof of successful live control or a new live extraction/vendor run.
- Real idle paused-runtime acknowledgement/browser absence: **passed**.
- Live Resume/reopen: **failed**, due to the request-format issue above.
- Live Pause after running, automatic login, MFA, personal FTW sign-in/search coexistence: **not tested**, blocked by live Resume failure. Profile separation alone does not certify FTW's simultaneous-login behavior.
- Fresh upload, real Eyelevel extraction accuracy/time, automatic FTW Bring Forward/readback: **not tested in this canary**. Settings shows no verified plan mappings. A specific authorized test plan/source and eligible routing must be confirmed before vendor writes; no manual Bring Forward substitution or historical replay was used.
- Real automatic sending: **intentionally excluded/disabled**, per the user's earlier deferral.

## Handoff / recovery

Desktop remains safely PAUSED with its browser closed. There is no queued/claimed device work to replay. This changes availability intentionally during maintenance; normal live agent operation has **not** been certified or resumed. Other computer and filing processing rules remain untouched.

Proceed only with approval for the narrowly scoped dashboard fix/deployment, then repeat live Resume/Pause and browser tests. Whole-flow vendor acceptance still requires confirming the test target. Executable rollback is available from the validated backup; restoring legacy software alone does not reconcile server pause/lease states and must not be counted as safe runtime recovery.
