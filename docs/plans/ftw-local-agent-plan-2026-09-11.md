# FT Williams Local Agent Plan

## Goal

Run the native FT Williams Bring Forward action from the client's Windows computer so ERISAPros can reuse a dedicated local browser profile instead of copying short-lived login cookies to the cloud.

## User outcome

- The client signs in to FT Williams once in a dedicated ERISAPros browser profile.
- The local agent stays running and processes verified Bring Forward jobs automatically.
- Login cookies and MFA state remain on the client's computer.
- The dashboard continues automatically after ftwLink confirms the new current-year records.
- When the agent is offline or FT Williams requires login, the existing **Open FTW Bring Forward** button remains available.

## Architecture

1. The dashboard creates a Bring Forward job only after client, plan, EIN, plan number, year, and browser mapping pass the existing safety policy.
2. A Windows local agent uses outbound HTTPS polling to claim one job. No inbound port or firewall rule is required.
3. The job contains only the filing ID, exact FT Williams target URL, expected identity, expiry time, and a one-time job token.
4. The agent opens the target in a dedicated persistent Chromium profile stored under the Windows user account.
5. The agent verifies HighlandTech, plan name, EIN, plan number, and year before clicking.
6. The agent clicks only the native **Bring Forward prior-year data for this plan only** action.
7. The agent reports submitted, login-required, invalid-target, page-changed, or failed. It never reports the click as proof of success.
8. The dashboard re-queries ftwLink, compares record IDs before and after, matches the new Schedule A, validates fields, and continues the existing workflow.

## Security and reliability

- Browser profile, cookies, and MFA state never leave the client computer.
- Pair each installation with a revocable, device-specific token stored with Windows DPAPI or Credential Manager.
- Use pull-only jobs, TLS, short-lived job tokens, idempotency keys, and one active job per device.
- Keep the browser context alive between jobs and reuse the persistent profile after agent restarts.
- Keep the same headed browser identity across runs. The packaged agent may minimize the window but must not switch between headed and headless modes because FT Williams binds the session to browser identity.
- Send a heartbeat and version number so the dashboard can show Connected, Offline, Login Required, or Update Required.
- Fail closed on account, plan, year, layout, duplicate-job, or confirmation uncertainty.
- Retain the current manual flow as the permanent fallback.
- Keep automatic FT Williams data updates disabled until update permission and update/read-back tests pass.

## Delivery slices

### Slice 1 — profile proof of concept

- Launch a dedicated persistent browser profile locally.
- Let the user sign in once.
- Verify the five demo plan targets without clicking anything.
- Close and reopen the browser with the same profile.
- Verify all targets again without another login.

### Slice 2 — local agent and dashboard pairing

- Add device registration, revocation, heartbeat, and connection status.
- Add a pull-only Bring Forward job queue and one-time job claims.
- Package the agent as a signed Windows installer with automatic startup.

### Slice 3 — guarded Bring Forward

- Reuse the proven identity checks and click logic locally.
- Report sanitized evidence and failure states.
- Re-query ftwLink in the dashboard and verify record IDs before continuing.

### Slice 4 — production hardening

- Test restart recovery, session expiry, MFA, offline mode, duplicate jobs, page changes, timeouts, and upgrades.
- Run the five-plan HighlandTech demo suite.
- Complete security review, human review, and manual QA.
- Enable only after every gate passes.

## Acceptance criteria

- One login survives closing and reopening the local agent while FT Williams keeps the session valid.
- The local agent uses one consistent headed/minimized browser mode across every run.
- All five demo targets verify the exact account, plan, EIN, plan number, and year.
- No proof-of-concept test clicks Bring Forward or changes FT Williams data.
- Credentials and browser state are never uploaded or logged.
- An expired session produces Login Required and preserves the manual fallback.
- The existing FT Williams query, comparison, approval, update, and manual Bring Forward flows remain unchanged.

## Known limitation

FT Williams can still force session expiry or MFA. No client-side design can bypass that safely. The local persistent profile minimizes repeated login but cannot eliminate a login that FT Williams itself requires.
