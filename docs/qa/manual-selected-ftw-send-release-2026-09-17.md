# Selected-field sending — final production report

**Status: live and verified.** Released 17 September 2026, Asia/Karachi (AWS timestamps use 16 September UTC).

Production: https://d3axcdlq9aydpw.cloudfront.net/

## Delivered

- Removed manual Approve Filing/Unapprove controls and the Approval workflow stage. Historical approval records are preserved; retired public approval endpoints return HTTP 410 by the tested contract.
- Send to FT Williams is always shown, apart from temporary disabling during an active interaction. Unrelated Action Required/review items do not gate it.
- Confirmation sends explicit selected changed-field IDs only. Deselecting keeps the current value. Missing selection does not fall back to the whole-filing preview.
- Prepared broker changes are opt-in; unchecked preserves current broker rows. Invalid selected values and actual FTW target/write constraints still produce clear errors.
- Manual sending cannot immediately dispatch a second automatic write of unselected changes. Extraction, matching, Bring Forward, scheduled automation settings and the worker were not changed.
- Fixed workflow spacing: five cards, no obsolete empty sixth card, and five loading placeholders. Phone layout keeps the workflow horizontally scrollable within its strip and fields as labelled cards.

## Verification

- Full backend suite rerun before deployment: **708 passed, 2 skipped**, 51.55 seconds; one existing GroundX SDK deprecation warning.
- Selected-send/API tests: 24 passed. Review UI, responsive workspace, FTW diagnostics, production build and built-bundle rendering passed. Earlier dashboard responsive/grouping checks also passed.
- Image build succeeded. Pre-release hashes of the three overlaid API files matched the exact running production image before building. No other source modules were overlaid.
- API rollout completed: one running task, no pending tasks, current load-balancer target healthy; old deployment drained.
- Live health endpoint: HTTP 200 / status ok. Unauthenticated filings API remains HTTP 401.
- Live HTML and both frontend assets match the validated local production build byte-for-byte. CDN invalidation completed.
- Live browser on the VSP filing: twelve review items remain, Send is enabled, no approval stage, and five equal workflow tracks. The last-card edge is within 0.016 pixels of the grid edge (normal fractional-pixel rounding), with no blank card.
- Live confirmation initially shows 13 selected / 13 available. Unchecking NAIC changes it to 12 selected / 13 available. Broker updates are unchecked. Cancel closed the dialog; **Send and verify was not clicked**.
- Live phone check, 375 x 812: five 118-pixel workflow tracks, Send available, forty labelled comparison cells, page width 360 pixels (no horizontal page overflow). Desktop viewport restored afterward.
- Live browser console error check: empty.

## Deployment and unchanged configuration

API task: `erisapros-production-api:24`.

Image: `sha256:f7beaa65c262bdd1844de01310b1e9003991c6683952eb7012cb7bb4177ec15a`.

Build: `erisapros-production-backend:a2c6ad12-0791-4fcd-a990-4956236f37c7`, SUCCEEDED.

Frontend: `assets/index-BDB-ypzR.js`, `assets/index-CqzWn4N0.css`.

Frontend index version: `y6LC4vDVpiExgWjKdDzl7TiAae_ZTcIp`. Invalidation: `I810LQCJZSB74X11SCHTK8CHGY`, Completed.

API task configuration was compared with the previous revision and is identical except for the image. Worker remains `erisapros-production-sharefile-worker:17`, with one running task. No worker rollout, automatic-send configuration change, database migration/backfill, upload, or real FT Williams client write was performed by this release workflow.

## Limits and rollback

Selected-write behavior and read-back protections were exercised against fake vendor responses in integration tests. No real vendor-write smoke test was performed during deployment; an enabled Send button still cannot bypass missing/locked targets or invalid selected data. The existing >500 kB frontend bundle advisory and GroundX SDK deprecation are non-blocking follow-ups.

Rollback artifacts and verification evidence are retained in `tmp/manual_selected_send_release_20260917/`. Roll back API to `erisapros-production-api:23` and restore the retained previous frontend index (S3 version `fg82K2f4fe9eDJxHlJiwqG0dJ8_hLH6i`), then invalidate `/` and `/index.html`. Old frontend assets remain available. No database or worker rollback is required. Coordinate API and UI rollback together so selection semantics stay consistent.
