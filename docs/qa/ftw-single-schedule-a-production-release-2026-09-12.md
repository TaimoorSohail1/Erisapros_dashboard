# FT Williams Single Schedule A Production Release Report

Date: 2026-09-12  
Environment: ERISAPros production application connected only to the HighlandTech FT Williams demo account

## Release decision

**Limited release deployed; full end-to-end acceptance remains NO-GO.**

The production application is deployed with Schedule A updates enabled and the single-record fail-closed guard enabled. Automatic unattended sending remains disabled. The guard correctly refused all tested targets that did not have exactly one API-visible current Schedule A.

Live update, new-Schedule-A, and Bring Forward acceptance are still blocked because the approved demo target set does not currently contain a clean, verified plan in each required state. No live client FT Williams data was used or changed.

## Deployed configuration

- Application URL: `https://d3axcdlq9aydpw.cloudfront.net`
- CloudFormation stack: `erisapros-production` — `UPDATE_COMPLETE`
- API task: `erisapros-production-api:18` — 1/1 running, rollout completed
- Worker task: `erisapros-production-sharefile-worker:13` — 1/1 running, rollout completed
- Schedule A updates: enabled
- Single Schedule A only: enabled
- Automatic unattended send: disabled
- FT Williams account secret: confirmed to match the HighlandTech demo KeyID
- Production FT Williams credentials: not configured in the deployed application secret

## Implemented controls

- Existing updates require exactly one current Schedule A.
- New Schedule A creation requires zero current Schedule As.
- Multiple Schedule As stop before any update payload is sent.
- Final send repeats the record-count check to prevent stale-review bypasses.
- Empty current snapshots can build a new Schedule A payload only through the explicit create-new path.
- The demo canary now stops before any Form 5500 or Schedule A write unless exactly one current Schedule A is found.
- The FT Williams local agent is paired to the current administrator and reports `Connected and ready` against HighlandTech.
- Pairing expiry and last-seen times now interpret API timestamps as UTC, preventing valid codes from appearing expired in non-UTC browsers.

## Automated verification

- Backend: **643 passed, 2 skipped, 1 dependency warning, 39 subtests passed**
- Frontend typecheck: passed
- Frontend production build: passed
- Built-app smoke test: passed
- Shared polling/performance checks: passed
- Review UI and FT Williams failure diagnostics: passed
- Dashboard responsive and company-grouping checks: passed
- Field Rules UI: passed
- ShareFile UI: passed
- FT Williams Agent UI: passed
- Known non-blocking warning: the main frontend bundle is slightly above 500 kB

## Live demo verification

### Passed

- Production health endpoint returned healthy.
- API and worker services completed rollout and remained healthy.
- No new application `ERROR` log entries were found after deployment.
- HighlandTech demo credentials were confirmed without displaying the KeyID.
- Agent pairing, protected local credential storage, heartbeat, browser-session identity check, and dashboard connected state passed.
- Fresh one-time pairing code displayed correctly after the UTC fix.
- American Securities identity matched the confirmed demo plan.
- American Securities returned four current Schedule As; the canary stopped before any write with: `found 4. No update was sent.`
- FGF and Barry Robinson returned zero API-visible current Schedule As; both stopped before any write.

### Not accepted yet

- Reversible single-Schedule-A update/read-back/restore: no approved target with exactly one API-visible current Schedule A.
- New Schedule A creation: no approved clean target with the required Form 5500 and zero current Schedule As.
- Bring Forward: no approved clean target with a verified prior-year Schedule A and empty current year.
- Expired-login/offline/wrong-plan/API-failure production probes: covered by automated tests, but not all repeated as live fault injections after this deployment.
- Multi-browser and signed-installer pilot: not completed.

## Data-change statement

No live client FT Williams data was queried for mutation or changed. The three live probes in this release were read-only and all stopped before sending an update.

## Required next step

FT Williams or the demo-account owner must provide three disposable HighlandTech demo plans:

1. One plan with exactly one current Schedule A.
2. One plan with a current Form 5500 and no Schedule A.
3. One plan with prior-year Schedule A data and an empty current year.

After their identities and starting state are verified, run update/read-back/restore, create/read-back/restore, Bring Forward, duplicate-attempt blocking, and the remaining live failure probes. Full acceptance can move to GO only when every mutation is verified and restored with no unexpected changes.
