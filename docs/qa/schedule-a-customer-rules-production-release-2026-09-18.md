# Schedule A customer rules - production release

Date: 2026-09-18, Asia/Karachi. Status: **live and verified**. Automatic sending remains disabled.

Production: https://d3axcdlq9aydpw.cloudfront.net/

## Approved scope

- Blank organizational code defaults to 3 for recognized proposed Schedule A recipient/broker rows. Explicit codes are preserved. No recipient is invented, and current FTW snapshots are not normalized.
- Select the highest valid covered-lives count from the relevant source column, retaining candidates and evidence. Currency and identifiers are excluded; distinct contracts and unreadable/unaligned columns stay reviewable.
- Show a small default-code explanation in the broker editor/review row; no dashboard redesign or extra approval step.
- Automatic-send testing and activation are deferred by the user. **FTW_AUTOMATION_AUTO_SEND_ENABLED remains false on both API and worker.**

Eight application modules are overlaid onto each service's own immutable running-image baseline. API selected-field sending and agent/API/repository implementations are retained. The worker had an older review/sending implementation, so its review module was assembled from that exact baseline with only the customer-default import and row-normalization call added; API manual-send changes were not copied into the worker.

No infrastructure migration, IAM/role change, production credential/key/bucket change, installed-agent update, mapping change, database backfill, historical replay, client upload or FTW write is part of this release. Existing cached filings are not bulk-re-extracted: defaults apply through review preparation/editing and proposed payload generation; lives selection applies on subsequent extraction.

## Acceptance evidence

- Fresh full backend suite: **754 passed, 2 skipped, 64 subtests passed**, 93.17 seconds. One existing GroundX SDK deprecation warning.
- All frontend checks passed: review layout/diagnostics/responsive workspace, agent settings, dashboard responsiveness/grouping, Field Rules, ShareFile controls and shared polling. Production TypeScript/Vite build and built-bundle render smoke passed.
- Both candidate images passed network-isolated API/worker import smoke and **14 customer-rule tests each**, including blank/nonblank/zero preservation, maximum/ties/keyword columns, dollars, mixed contracts, continued tables and provider-failure review holds.
- The initial CodeBuild attempts stopped before image publication or service rollout because the non-root test container could not read a generated mounted test file. Corrected only temporary test-folder permissions; application sources were unchanged. Successful replacement builds are recorded below.
- Prior corrected-account Hartford comparison: actual GroundX structured/X-Ray/local pipeline completed in 127.09 seconds, explicit organizational code 3 retained, highest lives recovered as **208** by the new source-column rule. That count was not returned before semantic recovery and is not attributed to Eyelevel alone. Its confidence 0.92 is below the default 0.95 automatic-send threshold. This was not a live FTW send test.
- Before worker rollout, SQS approximate waiting, in-progress and delayed message counts were all zero. Queue contents were not consumed, purged or replayed by deployment checks.

## Release references

- API: `erisapros-production-api:26`, image `sha256:5fb6199500134f58f60d4fc40ebe4998daf1a170fc0956d7b8c2e4698998e275`.
- Worker: `erisapros-production-sharefile-worker:19`, image `sha256:a54b879b073e1703d58fd17ec8f73f87dddfa004441cdaa2ca452fb0513ccb16`.
- API build: `erisapros-production-backend:23a2731e-55bf-440d-97da-699f52128ca3`, SUCCEEDED.
- Worker build: `erisapros-production-backend:d9b3326c-2d76-4d22-95b0-1039dcf7dca9`, SUCCEEDED.
- Validated frontend assets: `index-CYz2v5gD.js`, `index-nzOXAZKk.css`. CSS remains unchanged from the preceding release.
- Registered task configurations were compared with their baselines: image-only differences; automatic send explicitly false on both. Health returned HTTP 200 during rollout.

Frontend index version: `op_zsMqjQHXe7TMeGG_BBMaWWHByxhsh`. CloudFront invalidation: `I3H5HJZ856RTLGKBF0B0ADIZBO`, Completed. Evidence directory: `C:/Users/Hp/Erisapros_dashboard/tmp/customer_rules_release_20260918/`.

## Final live verification

- API 26 and worker 19 each have one COMPLETED deployment, desired/running count 1, pending count 0. The API load-balancer target is healthy; old tasks drained.
- Deployed immutable image sources match all eight approved candidate hashes. Critical API, repository, automatic-sender and agent modules match each service's retained baseline hashes. Each task definition differs from its baseline only by image; automatic send is false on both.
- Live health: HTTP 200, status ok. Unauthenticated filings and agent-control requests return HTTP 401. Sampled startup/task logs contained no ERROR/CRITICAL/Traceback events; this is a sampled check, not indefinite monitoring.
- Live HTML and both assets match the validated production build byte-for-byte. The new default-code hint is present in the deployed bundle; CDN invalidation completed.
- Signed-in VSP review still displays its fields and an enabled Send to FT Williams button with **12 review items outstanding**. The existing cached broker code was blank. Opening its editor previewed code **3** and the derived-value explanation; selecting **6** removed that explanation; choosing Use default (3) restored it. Cancel discarded the draft. **Save broker row was not clicked.** The persisted cached review was intentionally unchanged; no historical normalization/backfill or re-extraction was performed.
- Default-code editor and labelled fields were checked on the normal desktop viewport (1916 pixels wide), mobile **390 x 844**, and tablet **768 x 1024**. Page widths were respectively 1901, 375 and 753 pixels, with no page-wide horizontal overflow. Mobile code dropdown/help text were visually inspected; viewport override reset afterward.
- Manual selected-field confirmation still shows 13 available fields and unchecked broker changes. Unchecking NAIC changed selection to **12 of 13** while the 12 review items remained. Cancel closed the confirmation. **Send and verify was not clicked.** This checks UI/selection continuity, not a real FTW write/read-back.
- Live browser warning/error list was empty at inspection. Temporary QA tab was closed; the user's original tabs were preserved.

No new live production filing/extraction or FTW write was initiated for deployment verification. Real-source extraction acceptance is the preceding corrected-account Hartford comparison; runtime rule/import tests were run in the exact immutable release images. This separation avoids claiming a new full upload-to-FTW production test.

## Rollback and deferred work

Rollback API to `erisapros-production-api:25` and worker to `erisapros-production-sharefile-worker:18`; exact baseline task definitions and digests are retained under the evidence directory. Restore the retained frontend index or S3 index version `vQs3fxNR.K897.UhkALZpN.wm5gU6GUL` in `erisapros-production-frontendbucket-cakiwvjsgauc`, then invalidate `/` and `/index.html`. Old frontend assets are retained. No database or desktop-agent rollback is required for these scoped changes.

Real automatic sending to an explicitly approved FTW test plan, verified read-back and production automatic-send activation remain deferred. No claim of end-to-end automatic-send certification is made. Further layout acceptance remains appropriate before asserting universal coverage. Existing bundle-size/SDK advisories are non-blocking follow-ups.
