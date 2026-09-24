# Peerless and Kestra Live FTW Validation — 2026-09-23

## Outcome

The production FTW browser-agent path is working. Peerless completed automatic company/plan navigation and a single Bring Forward without Retry. Kestra completed upload, extraction, and FTW matching. A repeated ShareFile extraction defect discovered during the test was fixed and deployed to the production worker.

The **Send to FT Williams** action was not used.

## Defect found and fixed

One ShareFile version was being extracted again when its modified timestamp changed slightly. The dedupe comparison depended on version/hash/size, but the optimized Mongo projection did not return those fields. The system therefore fell back to a timestamp-bearing signature and treated the same content as updated.

Fix: include `file_size`, `modified_at`, `version`, and `hash` in the ShareFile index lookup used by webhook and polling intake.

Production release:

- Worker task definition: `erisapros-production-sharefile-worker:25`
- Image digest: `sha256:be8735811035b165fa98c006461f9323563d0b23be8af465cc78e813c2cd4da5`
- CodeBuild: `erisapros-production-backend:21b40ab0-ed29-4a6d-b8d9-dcf5ac50a51f`
- Rollout: completed, 1 running / 0 pending

## Live results

| Test | Result | Evidence |
|---|---|---|
| Peerless upload and extraction | Pass | Filing `6ab395e983fc1c7c3e97f941` reached review with 36/40 fields. |
| Peerless automatic FTW search/navigation | Pass | Agent opened the exact Peerless plan, EIN `03-0294586`, plan `501`, year `2025`, without Retry. |
| Peerless Bring Forward | Pass | Job `6ab396792b50f3176b15ddc7` dispatched at `09:06:11Z`, completed at `09:06:18Z`, and was marked `VERIFIED`; FTW re-query verified the current-year Schedule A. |
| Duplicate FTW click prevention | Pass | The overlapping Peerless job ended `NO_LONGER_REQUIRED` with no operation dispatched. |
| Kestra upload and extraction | Pass | Active filing `6ab3989583fc1c7c3e97fa20` reached review with 37/40 fields. |
| Kestra FTW matching | Pass | Dashboard displays `FTW match: Matched` for EIN `47-1566880`, plan `501`. |
| Blank Organizational Code → 3 | Pass | Kestra broker rows visibly show code `3` and “Defaulted to 3 because organizational code was blank.” |
| Post-fix duplicate prevention | Pass | Validation scan at `09:45:28Z`: 8,052 found, 0 synced, 0 errors. Next scan at `09:46:10Z`: 3,314 found, 0 synced, 0 errors. Active Kestra filing ID stayed unchanged. |
| Send to FT Williams | Not executed | The button was visible but intentionally not clicked. |

Kestra did not run another Bring Forward after the fix because FTW already reported that the current-year record exists. Skipping a second native copy is the correct safe behavior.

## Automated verification

- Focused ShareFile/repository suites: `45 passed`
- Full backend suite: `852 passed, 2 skipped, 1 warning, 64 subtests passed`

## Remaining cleanup

Historical duplicate rows created before this fix remain in the dashboard/database. They were not deleted during this validation because deletion is destructive. They do not indicate a new post-fix duplicate.
