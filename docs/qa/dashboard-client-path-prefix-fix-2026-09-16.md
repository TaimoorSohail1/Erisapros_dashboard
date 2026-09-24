# Dashboard root-prefix grouping fix — 16 September 2026

## Problem and scope

The previous name-variant grouping release did not cover mixed absolute and relative ShareFile paths. Four active Barry Robinson packages included `Folders > ERISA Pros` before the client folder; two omitted that account-root breadcrumb. This produced two different canonical grouping keys despite identical client names.

Read-only production inspection confirmed the six packages share the same worksheet item ID, `fi40878a-60b7-2e79-543d-78fe9b62bc71`, proving the observed split is not two unrelated worksheet sources.

This change normalizes only that known account-root prefix in legacy path-based dashboard identities. It retains nested client ancestors and workspace boundaries. Explicit client folder IDs remain preferred. No filing, XML, extraction, approval, ShareFile item, FTW record, automation policy or worker configuration is edited. Other root names are not blindly stripped. Folder renames and arbitrary path aliases remain outside this narrow release.

## Tests and review

- Added a dashboard repository regression reproducing the actual relative/discovery-root split; it failed before the fix and passed after it.
- Mongo dashboard query regression covers nested Community Legal Aid paths with and without the prefix.
- Safety checks cover different parent folders/accounts, filing years, whitespace/case, workspaces and explicit folder IDs.
- Full backend suite: 700 passed, 2 skipped; one existing GroundX SDK deprecation warning.
- Frontend dashboard responsive and company grouping tests passed. No frontend code changes are required by this release.
- Diff reviewed: normalization occurs only in the dashboard read identity helper; original source paths remain intact. Existing unrelated worktree changes are preserved.

Read-only pre-release verification: six active Barry Robinson filings resolve to one group, and five Community Legal Aid filings still resolve to one group. Total active filings changed externally from the earlier screenshot to 17 before deployment; this workflow does not delete or supersede filings. Hartford was already APPROVED during this check; the other five Barry Robinson filings were NEEDS_REVIEW.

## Release

**Live and verified.** The new API is serving production traffic, live browser QA passed, and ECS rollout completed with one running task and no pending tasks. The old API deployment has drained.

Live dashboard verification after reload: **7 companies, 17 filings**, compared with **8 companies, 17 filings** immediately before rollout. There is one expanded Barry Robinson group containing VSP, Legal, Symetra, Sentara EAP, Hartford and Delta Dental. Community Legal Aid remains one group. Production health returns HTTP 200 / `status: ok`, and the new load-balancer target is healthy.

API task definition: `erisapros-production-api:23`. Image digest: `sha256:180debca7cb9c0475a55fa78e6442f540be1b672e52376d500c0486a1983442d`. Build: `erisapros-production-backend:ebc65561-db6f-43d6-9beb-92e405f8dbbd` (SUCCEEDED).

Task configuration was compared with the previous deployment and is identical except for the image. The frontend is unchanged; the ShareFile worker remains on `erisapros-production-sharefile-worker:17`, with one running task and no pending tasks.

Evidence and rollback task definition are retained in `tmp/dashboard_grouping_prefix_release_20260916/`. The build overlays the two dashboard modules on the exact currently deployed API image, verifies their pre-release source hashes, and preserves the rest of the image. To roll back this release, restore the API service to `erisapros-production-api:22`. No frontend, worker or database rollback is needed.
