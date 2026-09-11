# FT Williams Local Agent Implementation Report

## Outcome

The client-local FT Williams Bring Forward architecture is implemented on `codex/automated-ftw-workflow` and remains disabled by default. No deployment was performed for this change.

The agent performs only the native FT Williams Bring Forward action. The existing backend still owns plan lookup, Schedule A matching, validation, add-as-new decisions, update permission checks, sending, read-back, and restoration controls.

## Implemented

- One-time administrator-created pairing codes with expiry.
- Revocable device tokens stored only as SHA-256 hashes on the server.
- Windows DPAPI-encrypted device credential storage on the client.
- Device heartbeat with Connected, Offline, and Login Required states.
- Pull-only job delivery over outbound HTTPS; no client inbound port.
- Expiring, one-time, account-routed job claims.
- Idempotent Bring Forward job creation.
- Dedicated persistent headed Chromium profile kept on the client computer.
- Exact verification of FT Williams account, plan name, EIN, plan number, and year before any click.
- Exactly-one-action requirement for the Bring Forward control.
- Post-click ftwLink re-query and new-record-ID proof before the existing workflow resumes.
- Professional dashboard connection state and permanent Manual Bring Forward fallback.
- Windows build, optional mandatory code-signing, pairing/install, and automatic-start scripts.

## Safety results

- The local-agent feature flag defaults to off.
- A wrong account, wrong plan identity, changed page, expired login, expired claim, revoked token, or missing new record ID fails closed.
- A workstation paired to one FT Williams account cannot claim another account's jobs.
- Browser cookies, MFA state, and the persistent profile remain on the client computer.
- No new FT Williams click or data mutation was performed during this implementation verification.
- The unrelated `frontend/vite.config.ts` working-tree change was not modified or included.

## Verification completed

- Backend full suite: **605 passed, 2 skipped, 1 third-party deprecation warning, 39 subtests passed**.
- Focused local-agent/API/automation/repository suite: **68 passed** after the final security and fallback-recovery additions.
- Frontend TypeScript: passed.
- Review UI, dashboard UI, shared polling, field-rules UI, and ShareFile UI checks: passed.
- Production frontend build and rendered-bundle smoke test: passed.
- PowerShell build and installer syntax validation: passed.
- HighlandTech persistent profile startup readiness: passed.
- Five-plan read-only identity test: **10/10 passed** across initial and browser-restart passes:
  - American Securities demo
  - BTIG demo
  - FGF demo
  - Barry Robinson demo
  - Special Service demo

## Live gates still required

1. Obtain the production Windows code-signing certificate and build/verify the signed executable.
2. Pair the intended HighlandTech client workstation against the deployed API.
3. Enable `FTW_LOCAL_AGENT_ENABLED=true` only in the controlled demo environment.
4. Run automatic Bring Forward for the five demo plans and prove before/after record IDs.
5. Test session expiry/MFA, offline recovery, restart recovery, duplicate delivery, page change, and timeout behavior on the installed agent.
6. After FT Williams grants KeyID update permission, run update, read-back, sibling-preservation, and restoration tests.
7. Obtain human approval before any production enablement.

## Deployment status

Not deployed. Existing production behavior is unchanged, and automatic FT Williams updates remain disabled.
