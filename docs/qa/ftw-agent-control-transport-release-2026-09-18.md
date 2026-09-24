# FTW Agent control request fix — production release and live tests

Date: 2026-09-18. User approved fixing/deploying the dashboard defect and continuing tests. Scope: frontend-only request correction and existing desktop 0.4.1 canary; not a public installer release or automatic-send activation.

## Delivered / deployment verification

- `frontend/src/api.ts`: only `setFTWLocalAgentPaused` now explicitly sends `Content-Type: application/json`. Authorization, body, device encoding, other requests and backend rules remain unchanged.
- Added `frontend/scripts/test-agent-control-transport.mjs` to the agent UI test command. It transpiles and executes the real exported request function with a synthetic fetch/auth boundary, asserting JSON headers, true/false payloads, POST method, encoded device ID and retained authorization. The regression failed before the fix (header null) and passed afterward.
- Agent/review/dashboard/Field Rules/ShareFile/shared-polling frontend checks passed. TypeScript/Vite production build and built-app render smoke passed. Existing >500 kB bundle advisory remains; scoped diff whitespace check passed with Windows line-ending notices.
- Deployed using the existing S3/CloudFront workflow, retaining the previous index and using the preflight ETag to detect concurrent publication. New `assets/index-CLGF18nm.js`; CSS remains `assets/index-nzOXAZKk.css` unchanged. Index version `WyZKyhichuQ2e2wNT9y80TZdWxBqWEip`. Invalidation `I6HZS6RY1LRXADUYSNOO4TM24D` completed.
- Public index and both assets matched the validated local build byte-for-byte. Health HTTP 200; unauthenticated agent-control HTTP 401. Production API remains task definition 26; no API/worker rollout or environment mutation. Automatic send stays false. No agent rebuild/reinstall or new pairing/login entry was required.
- Evidence: `tmp/agent_control_transport_release_20260918/` (preflight, previous index/task, release and exact-asset verification).

## Live acceptance: observed, not simulated

1. Reloaded the signed-in production settings page. Desktop shows 0.4.1, PAUSED with Resume enabled.
2. Resume requested at **12:58:13.835511 UTC** succeeded. The owned BrowserProfile Chromium context opened; agent heartbeat reported CONNECTED and browser_ready true, and settings reported signed in/ready. This proves restored session readiness; it does not prove that the saved password was submitted rather than existing cookies reused.
3. Existing Resume semantics renewed an older pending job, including its expired execution window. It was not visible in the earlier unexpired-only queue count. This was an existing September 16 Advertising Council QA filing, not a newly created test upload. No manual Bring Forward button was clicked and no job was manually queued/replayed.
4. Job `6aaa8c7f2b50f3176b03ee45`, filing `6aaa8bf0035e7531a1ddcbb9`, Advertising Council 2025: automatically claimed **12:58:31.587 UTC**, agent completed SUBMITTED **12:58:40.962 UTC**; final job VERIFIED with message `ftwLink verified the new current-year Schedule A records.` Claim-to-agent completion **9.375 seconds**. Filing updated to NEEDS_REVIEW/ACTION_NEEDED/RESOLVE_ISSUES at **12:58:56.387 UTC**, about **24.800 seconds after claim**. These are stored stage timestamps, not Eyelevel extraction time.
5. Review now has current_year_exists true, bring_forward_required false, status CURRENT_QUERIED. Needs Review is an expected decision stop, not automatic extracted-data sending.
6. Pause requested at **12:58:42.942615 UTC** blocked further claims; agent acknowledged PAUSED, browser_ready false, no active reservation. Zero dedicated-profile Chromium processes remained. The request came about 1.98 seconds **after browser action completion**, while result verification/continuation was still settling. This verifies closure and completion reconciliation; it is **not** proof of pausing mid-vendor-click. Mid-action drain remains covered by synthetic runtime tests.
7. Resume at **12:59:44.153105 UTC** reopened the context and reached CONNECTED/ready. The verified job stayed attempts=1 and was not re-executed. Idle Pause at **13:00:15.936622 UTC** again reached PAUSED and zero dedicated processes.
8. Final Resume acknowledged: primary read at heartbeat **13:01:22.675 UTC** showed desktop 0.4.1 CONNECTED, browser_ready true, pause_requested false, no active job, and zero eligible/expired pending or claimed account jobs. The UI likewise showed Connected and ready with Pause enabled. The dedicated Chromium context was open (eight owned-profile processes at that sample). The completed job remained VERIFIED with attempts=1. Desktop is left resumed and available.

The preflight helper now counts expired-but-pending account jobs separately so later maintenance checks do not confuse an empty unexpired queue with no work that Resume can renew. No pending job was deleted or purged.

## Browser and regression boundaries

- All 15 personal Google Chrome process IDs captured before the live control cycle were still present after dedicated-browser closure. Only the dedicated profile's processes disappeared. Browser-profile/process separation is supported by these observations and existing runtime isolation regressions.
- No controllable personal FTW tab was available through the connected browser surface. Actual personal FTW sign-in/search and server-side simultaneous-login behavior were **not** verified; surviving personal processes do not prove absence of navigation/session interference.
- Other computer EP-PF43NS4S stayed version 0.3.2 with its original last-seen timestamp and control settings unchanged. Its public upgrade remains a separate client-pilot/distribution decision. If it starts an old same-account runtime again, the newer agent intentionally waits rather than compete.
- Immediately preceding desktop-canary regression suite: 795 passed, 2 skipped, 64 subtests in 54.00 seconds; focused update/control/lease/profile cases: 72 passed. Backend source was not changed for this frontend release. Failure/offline/crash/MFA and true mid-action pause cases were not injected into live vendor activity.
- Automatic sending remains disabled; successful Bring Forward/read-back is **not** proof of sending extracted values.

## Remaining acceptance / rollback

Not yet performed: a fresh upload with independent field-by-field Eyelevel accuracy/timing measurement; real login-expiry/password-submit/MFA and personal FTW browsing coexistence; a controlled live pause mid-browser action with queued follow-on work. Confirm a specific test plan and source before starting an additional fresh vendor test. Current settings shows no verified workspace plan mappings; the observed legacy pending job used the existing non-workspace route (mapping_id/workspace_id null). Do not equate its success with verified-workspace routing acceptance.

For frontend rollback, restore retained `previous-index.html` or S3 index version `op_zsMqjQHXe7TMeGG_BBMaWWHByxhsh` in the same frontend bucket, then invalidate `/` and `/index.html`. Old assets are retained. No backend rollback is needed for this one-request header change; the desktop's connection-preserving executable backup remains available as documented in the preceding canary report.
