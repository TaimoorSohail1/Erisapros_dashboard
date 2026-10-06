# FT Williams agent status production fix — 2026-10-06

## Scope and authorization

The user explicitly requested that the incorrect `Local FT Williams agent: Offline` indicator be fixed. The approved scope was application code only: correct local-agent device selection, improve status refresh and labeling, deploy the backend and frontend to production, and verify the authenticated filing UI. No infrastructure, credentials, client documents, filing decisions, or FT Williams values were changed by this release.

## Root cause

The backend selected the device with the newest heartbeat. A paused secondary computer could heartbeat more recently than the computer available for work, causing the account-level status endpoint to return `PAUSED`. The filing UI then collapsed most non-connected states to `Offline` and refreshed the status only every 30 seconds.

## Fix

- Rank fresh, ready devices first; then fresh non-paused devices; then other fresh devices; then stale devices.
- Preserve the selected device's real state when no device is ready.
- Refresh local-agent status on filing pages every 5 seconds.
- Render distinct labels for `Login required`, `Paused`, `Pausing`, `Resuming`, and `Waiting for account` instead of labeling all of them `Offline`.
- Add backend regression tests for a ready or login-required computer competing with a newer paused computer.
- Add frontend contract checks for the status labels and five-second polling interval.

## Source release

- Working branch: `codex/sharefile-priority-ingestion`
- Production release branch: `codex/automated-ftw-workflow`
- Commit: `6f5ddec0a421c3a4fff9ecd53f60605ec09719d7`

## Verification before deployment

- Targeted backend local-agent and automation tests: **100 passed**.
- Full backend suite: **1,052 passed, 2 skipped, 74 subtests passed**.
- Frontend review UI, performance, dashboard, field-rules, ShareFile, and FTW-agent checks: **passed**.
- Frontend typecheck, production build, and production bundle smoke test: **passed**.
- Production build emitted a pre-existing large-chunk advisory; it did not fail the build.

## Production deployment

- AWS profile/region: `erisapros`, `eu-north-1`
- AWS account/principal: `427925098650`, `erisapros-deployer`
- CodeBuild: `erisapros-production-backend:25400e68-3736-45ae-834f-c8ad0248c253` — **SUCCEEDED**
- Immutable backend image digest: `sha256:a8e67b56184f484ebc47c2b82f5c4d03c4fac57b39e23285f2e9d4742232efa2`
- API rollback target: `erisapros-production-api:119`
- API deployed target: `erisapros-production-api:120`
- Worker rollback target: `erisapros-production-sharefile-worker:109`
- Worker deployed target: `erisapros-production-sharefile-worker:110`
- Task-definition comparison: only the container image changed.
- ShareFile queue before the worker rollout: 0 waiting, 0 in flight, 0 delayed.
- Frontend bucket: `erisapros-production-frontendbucket-cakiwvjsgauc`
- Previous `index.html` version: `U7RfQ6UYzON5XUiEvszNStgafknWYuvi`
- New `index.html` version: `pKbLpnb91bCo4lPrexyNsV8aK4DzgrOn`
- Published JavaScript asset: `assets/index-r3GOnlqd.js`
- CloudFront invalidation: `I6C3LSCYJYXAVPXOJ2CZIFM8AG` — **Completed**

## Production validation

- API and worker deployments: **COMPLETED**, 1 desired / 1 running / 0 pending each.
- Load-balancer target: **healthy** after the prior target finished draining.
- `https://d3axcdlq9aydpw.cloudfront.net/api/health`: HTTP **200**, status `ok`.
- New API and worker `ERROR`, `CRITICAL`, `Traceback`, or `Exception` log events: **0**.
- Production HTML references the new JavaScript asset.
- Authenticated filing `6ac4a2806cd580ce1d3cd9c8`: status now renders **Local FT Williams agent: Login required**, not `Offline`.
- The same filing displays **FT Williams verified**, `FTW update Complete`, and read-back evidence for 4 saved fields on ANSEL Schedule A #10.

`Login required` means the local agent software is reporting, but the selected computer's FT Williams browser session requires sign-in. It is intentionally distinct from an offline agent.

## Actions deliberately not performed

- No client file was uploaded or deleted.
- No filing was retried or reprocessed.
- No FT Williams update was sent during this release verification.
- No MongoDB document, credential, feature flag, IAM policy, network resource, or CloudFormation resource was changed.

## Rollback

- Backend: restore API task definition `erisapros-production-api:119` and worker task definition `erisapros-production-sharefile-worker:109`.
- Frontend: restore S3 `index.html` version `U7RfQ6UYzON5XUiEvszNStgafknWYuvi`, then invalidate `/`, `/index.html`, `/filings/*`, and `/settings/*` in distribution `E38OL183OOAS7A`.
