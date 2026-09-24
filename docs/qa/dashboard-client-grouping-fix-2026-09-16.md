# Dashboard client grouping — 16 September 2026

## Scope

Community Legal Aid filings were split because the compact dashboard API preferred the extracted sponsor name for some filings and the ShareFile client name for others. The frontend then grouped by that display name. ShareFile has one client tree, not two unrelated clients.

The approved fix is limited to dashboard presentation. No extracted XML, EIN, plan mapping, ShareFile file, filing decision, approval, FTW record, worker configuration or automation policy is modified.

## Implementation

- The dashboard repository reads only the small client-identity fields from package metadata, not full worksheet or extraction payloads.
- It derives a canonical ShareFile company key and returns the ShareFile client name consistently. Original sponsor names remain unchanged in the underlying filing.
- Client folder ID is preferred when present. Legacy packages do not contain that ID, so their full client folder path is the fallback. Filing year and carrier/policy subfolders are excluded. Workspace is included to avoid cross-workspace grouping.
- Conflicting package client identities are not guessed or combined. Manual/legacy filings with no usable ShareFile identity keep their existing name/EIN fallback.
- The frontend uses the canonical key before the display-name fallback.
- Existing filings are corrected at read time; no production backfill or data rewrite is needed.

Folder renaming across old and new packages is not certified by this release: legacy path-based identities would need a true client-folder-ID migration/alias record to remain stable across such renames. Same-name clients with no folder path likewise cannot be distinguished beyond their workspace by missing metadata alone.

## Regression checks

The original issue failed the new repository and frontend behavior tests before the fix. Those same tests passed afterward.

Verified behaviors:

- Sponsor-name variants for one ShareFile client resolve to one group.
- Different filing years and carrier subfolders remain in the same company group.
- Different client folder paths or workspaces do not merge even if names/EINs match.
- Conflicting metadata does not silently merge clients.
- Manual grouping fallback remains unchanged.
- The API still returns every active filing, reads the primary, and excludes large package payloads.

Full backend suite: **698 passed, 2 skipped, 39 subtests passed**, one existing GroundX SDK deprecation warning. Frontend dashboard responsive/grouping tests, typecheck, production build, polling/performance tests, review UI checks and built-app rendering smoke test passed. Existing non-blocking bundle-size warning remains.

Read-only pre-release verification against live data: **36 active filings**, including **5 currently active Community Legal Aid filings**, resolve to one canonical company key. The earlier screenshot had a different active filing count; this change does not restore, delete or supersede filings.

## Release status

**Live and verified on 16 September 2026.** API task definition `erisapros-production-api:22` completed its rollout with one running task and no pending tasks. The production `/api/health` endpoint returns HTTP 200 and `status: ok`.

Live browser verification after reloading the deployed frontend confirmed one `Community Legal Aid SoCal (CLA SoCal)` company group. Expanding it shows all five active filings: Metlife CI, Metlife Health 0239026, Aetna EAP, Metlife LTD Life Vision ADD Dental, and Kaiser. Their NEEDS REVIEW states remain unchanged. The dashboard total remains 36 filings.

Release references:

- API image digest: `sha256:8010d12be569a3a08e03e608be8cdc734605f1965705d35da89cf4e1cab9b19d`.
- Frontend bundle: `assets/index-Jk_tPdrW.js`; existing CSS remains `assets/index-BZVn3aj8.css`.
- Frontend index S3 version: `fg82K2f4fe9eDJxHlJiwqG0dJ8_hLH6i`.
- CloudFront invalidation `IB7QH9MZAV7U2VRQWNR20SMBDF` completed.
- The ShareFile worker remains on task definition `erisapros-production-sharefile-worker:17`, with one running task and no pending tasks.

Rollback references and deployment evidence are recorded in `tmp/dashboard_grouping_release_20260916/`. Old frontend assets and the prior index/task definition are retained. If rollback is needed, restore API task definition `erisapros-production-api:21` and the saved previous frontend index (S3 version `ZzwlZKJPEZtQsnbJEQ1mNQts4PZBifvB`), then invalidate `/` and `/index.html`. No worker rollback or database reversal is required for this read-only dashboard change.
