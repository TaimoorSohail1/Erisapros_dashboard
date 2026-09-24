# FTW Agent Pause/Resume and Browser Isolation — Local QA

Date: 2026-09-17 (Asia/Karachi). Initial status: implemented and locally verified, not deployed at the time of the local QA below. Subsequent cloud rollout is recorded separately in the follow-up release report; desktop runtime activation and live-vendor acceptance remain pending.

## Scope and preserved behavior

Added authenticated, owner/workspace-scoped per-computer Pause/Resume controls, persistent requested state, acknowledged browser closure, pending-job retention, account browser leases, and guarded recovery. Windows runtime version is 0.4.0. Existing extraction, review/selected-field sending, verified plan routing, vendor read-back, and update permissions were not intentionally changed. Existing unrelated worktree edits were preserved.

The feature-delivery workflow guided failing tests first, implementation, review, local QA, and the deployment approval gate. Browser-verification tooling fell back to the in-app browser because the optional CLI was unavailable. React review led to polling cleanup, overlapping/stale-response protection, and device-local errors that polling does not erase.

## Behavior verified

| Scenario | Local result |
| --- | --- |
| Idle Pause / Resume | Dedicated context closes; Resume opens a new context and restores a persistent session cookie. |
| Active-operation Pause | Finish/report once, then close; no new claim while pause is requested. |
| Ten pending jobs, prolonged pause | Pending work remains eligible after Resume; completed work is excluded. |
| Offline pause / process restart | Server-requested state persists; UI distinguishes request from confirmed closure. |
| Completion network failure | Retry the result acknowledgement, not the browser click. |
| Crash or unconfirmed action | Hold UNKNOWN_OUTCOME as Action Needed; require a successful newer current-data query before retry. |
| Duplicate completion / verification | Same-token result replay is idempotent; downstream verification starts once. |
| Two updated agents, same FTW account | Exclusive account lease, release-after-closure acknowledgement, idle-owner yield to assigned queued work. |
| Revocation, stale claim/heartbeat, old version | Fresh control state enforced; revoked device denied; 0.3.2 controls disabled with update instruction. |
| Resume with saved credentials | Real runtime performs bounded automatic login against intercepted synthetic HTML; login budget resets on explicit Resume. |
| Personal-profile safety | Personal Chrome/Edge profile paths rejected; distinct profile cookies not shared. |
| Other browser activity | Separate Chromium profile's URL and search unchanged across readiness checks, Pause, and Resume. |
| Vendor-session/MFA wait | No work claimed until ready; clear login/MFA guidance. |
| Rollout disabled | Dedicated browser stays closed. |

Real Chromium regression tests used temporary persistent profiles and intercepted **all** requests with synthetic responses. No FTW requests, real credentials, client documents, uploads, or vendor writes were used. Mongo-specific tests exercise atomic selectors and duplicate-key behavior using repository mocks, not a live database cluster.

## Final automated verification

Backend: `.venv/Scripts/python.exe -m pytest -q`

Result: **738 passed, 2 skipped, 39 subtests passed** in 51.18 seconds. One existing GroundX SDK deprecation warning. Initial eight new behavior tests failed before implementation. Focused new pause/browser/Mongo cases: 29 passed.

Frontend checks passed:

- `npm run test:ftw-agent-ui`
- `npm run test:review-ui`
- `npm run test:dashboard-ui`
- `npm run test:performance`
- `npm run test:sharefile-ui`
- `npm run test:field-rules-ui`
- `npm run build` (TypeScript and production bundling)
- `npm run smoke:build` (production bundle renders)

Final assets: `index-CUorycgB.js`, `index-nzOXAZKk.css`. The existing over-500-kB bundle-size warning remains non-blocking. `git diff --check` passed; Git emitted only Windows LF/CRLF notices.

## Local UI QA

Preview: http://127.0.0.1:5175/scripts/qa-agent.html

This dev-only fixture renders the actual settings component with synthetic fetch responses and is not a production entry point. Verified desktop Ready/Pause/Resume, mobile 375×812, tablet 768×1024, active-operation message, offline closure acknowledgement, MFA/login guidance, old-agent update guard, and network errors adjacent to the affected device. Corrected a tablet setup-button overflow; document width is now 753 px within the 768 px viewport. Mobile controls fit without horizontal overflow. Browser console errors: none. Temporary viewport override reset; only the local preview tab retained.

## Windows development artifact

Staged: `output/ftw-local-agent-0.4.0-local/ERISAProsFTWAgentSetup.exe`

- PyInstaller one-file build with bundled Playwright completed.
- Size: 398,711,571 bytes.
- SHA256: `1C3C315905A0085D5B7715A12AE8360EA6DE67742DE77DC78C0069E2B6E322FF`.
- `--version`: `ERISAPros FT Williams Agent 0.4.0`.
- `--help`: install/pair/run/unpair commands displayed successfully.
- Authenticode: **NotSigned**; development artifact only, not release-approved.
- No install, pairing, service restart, production download replacement, or GitHub release was performed. The existing installed agents remain untouched.

## Release gate / remaining limits

Local tests are green, but this is not a completed live-vendor acceptance test. A controlled, authorized FTW canary must check real login/MFA, Bring Forward/read-back, and personal-browser coexistence. FTW may restrict concurrent logins at the server; browser isolation cannot remove that policy. Do not claim the previously reported live interference's exact cause is conclusively established by synthetic tests.

After user review, deploy the compatible API, distribute the approved signed 0.4.0 agent to every participating computer, confirm all old runtimes stopped, then expose/use the controls and complete the canary. Keep existing send/update permissions unchanged. Deployment requires further approval; production has not been changed by this task.

## Follow-up release preflight

The user requested completion of the remaining canary/signing checks. Read-only inspection found no code-signing certificate in either Windows CurrentUser/My or LocalMachine/My. The installed executable is still the earlier September 15 build, with two running agent processes; it was not replaced or restarted. The new 0.4.0 development artifact therefore remains unsigned, and no live-vendor acceptance test of the updated runtime was performed.

Proceed only with an approved signing certificate or approved signed package, and an identified authorized FTW test plan/workspace for the canary. Do not manufacture a self-signed certificate, replace a running production agent merely to claim QA success, or count testing the old installed runtime as acceptance of the new implementation. Production remains unchanged.

### Authorized rollout attempt — preflight only

The user approved the proposed rollout/testing plan. Local hostname confirms the test computer is `DESKTOP-D9JV7IA`; identification does not rely on dashboard row order. The staged package's hash still matches the manifest and Authenticode remains NotSigned. Certificate inspection still finds no code-signing certificate in either Windows certificate store. Both existing agent processes were left running, with no installer execution or credential replacement.

Rollback backup was created at `C:/Users/Hp/AppData/Local/ERISAPros/FTWLocalAgentBackups/pause-resume-preflight-20260917-015750`. The executable, protected device credential, protected FTW-login credential, and startup launcher were copied and hash-verified (four files). No credentials were decrypted or transmitted. The active browser profile was left in place, not copied while open.

A read-only AWS ECS cluster-list preflight returned `NoCredentials` for eu-north-1. The prior release's rollback files remain present, but the current production baseline could not be freshly confirmed. No AWS write, release publish, agent replacement, client upload, or live FTW canary occurred. Further work requires restored AWS sign-in and either a signed package or explicit approval of an unsigned package for the restricted canary; such approval must not be inferred from generic rollout approval.

### AWS access correction and authorized cloud rollout

The preceding credential check used the default AWS profile. The established release uses the named `erisapros` profile, which was verified against account 427925098650 and worked. A blanket AWS-access blocker was therefore incorrect. The user authorized the established AWS release process, and the compatible cloud API/dashboard rollout proceeded using that profile in eu-north-1. See `ftw-agent-control-cloud-release-2026-09-17.md` for final cloud verification and rollback references. The existing 0.3.2 desktop agents, protected credentials, and production installer download were not replaced. Signing/explicit unsigned restricted-test approval and updated-runtime live acceptance remain separate gates.
