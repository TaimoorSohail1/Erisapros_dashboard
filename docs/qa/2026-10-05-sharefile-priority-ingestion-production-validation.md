# ShareFile Priority Ingestion — Production Validation

Date: 2026-10-05  
Client/folder: NFP TEST / 5500 Filing / 2025 Filing / Schedule A's  
Production dashboard: `https://d3axcdlq9aydpw.cloudfront.net/`

## Outcome

The ShareFile-to-dashboard delay was reproduced, diagnosed, fixed, regression-tested, deployed, and re-tested in production.

- All 14 supplied files were accepted into production and have dashboard filings.
- The earliest filing recorded during the controlled re-test window appeared 20.5 seconds after the batch upload began.
- All 14 file names had a production filing by 18:03:03 UTC, 8 minutes 41 seconds after the batch began and about 7 minutes 31 seconds after ShareFile finished transferring the batch.
- All 14 completed extraction/routing by 18:06:48 UTC.
- Final routing: 13 `NEEDS_REVIEW`, 1 `APPROVED`, 0 extraction failures.
- Every filing was packaged with the newest `5500 Plan Worksheet - NFP 5500 - PY25.docx`.
- Production health returned `{"status":"ok","stack":"react-python-mongodb"}`.
- No worker `ERROR` entries were found after the final deployment/re-test window.

The 14-file batch was intentionally uploaded more than once while validating two successive fixes. The dashboard therefore contains additional test versions for some filenames; this is test data from the repeated controlled runs, not missing intake.

## Root cause and fixes

1. The ShareFile worker awaited long scheduled polling/full-scan maintenance before receiving later upload webhook messages.
   - Added a separate maintenance queue/worker so webhook uploads remain on the priority path.
   - Added receipt-heartbeat handling while maintenance work waits.
   - Added a deletion cutoff so a deep scan cannot delete newer webhook-observed files.

2. Live re-test exposed transient MongoDB timeouts while matching ShareFile items to filings.
   - Added one bounded retry with a fresh MongoDB pool for transient PyMongo failures.

3. The intake read-after-write lookup inherited `SECONDARY_PREFERRED`, routing a latency-critical consistency check to slow/stale secondary nodes.
   - Targeted `list_filings_by_sharefile_item_ids` to `ReadPreference.PRIMARY`.
   - Ordinary dashboard reads remain secondary-preferred.

Commits:

- `099e93c` — Fix ShareFile uploads blocked by maintenance scans
- `d06eb8b` — Retry transient Mongo errors during ShareFile intake
- `e7b6f0c` — Use primary reads for ShareFile intake

## Regression verification

- Focused ShareFile/Mongo tests: 32 passed.
- Full backend suite: 1,014 passed, 2 skipped, 68 subtests passed.
- Final backend image digest: `sha256:add9664e48c3a77c24f04b72c82574905113ae9882adee082a48541a4e9b745f`
- API task definition: `erisapros-production-api:103`
- ShareFile worker task definition: `erisapros-production-sharefile-worker:93`
- Both ECS services: rollout `COMPLETED`, running 1, pending 0.

## Controlled production batch timing

- Upload began: 17:54:22.664 UTC
- ShareFile transfer dialog completed: 17:55:27.896 UTC
- Transfer progress closed: 17:55:33.311 UTC
- Earliest filing recorded in the controlled window: 17:54:43.206 UTC (20.5 seconds)
- Last of the 14 current file versions registered: 18:03:03.922 UTC (8 minutes 41 seconds)
- Last extraction/routing update: 18:06:48.447 UTC

Because earlier queued events and the clean re-upload were both draining after deployment, events interleaved. The table below records the newest production filing for each supplied filename.

## Per-file production result

| File | Dashboard filing created (UTC) | Time from batch start | Final status | Filing ID |
|---|---:|---:|---|---|
| 16. National Financial Partners 202501 - 202512 Schedule A BCBS.pdf | 18:02:18.074 | 7m 55s | NEEDS_REVIEW | `6ac3e62abd1959bb15c24827` |
| Re_ NFP - Ansel Schedule A.msg | 17:58:24.174 | 4m 02s | NEEDS_REVIEW | `6ac3e540bd1959bb15c244bf` |
| 15. 2025-schedule-a-Tuned-NFP.pdf | 18:01:22.214 | 7m 00s | NEEDS_REVIEW | `6ac3e5f2bd1959bb15c24760` |
| 14. SCHEDULE A DATA FOR IDI (PROVIDENT CASUALTY) 160040 01-01-2025.pdf | 17:57:59.825 | 3m 37s | NEEDS_REVIEW | `6ac3e527bd1959bb15c244af` |
| 13. SCHEDULE A DATA FOR IDI (PROVIDENT ACCIDENT) 169225 01-01-2025.pdf | 17:57:34.599 | 3m 12s | NEEDS_REVIEW | `6ac3e50ebd1959bb15c244a4` |
| 12. SCHEDULE A DATA FOR IDI (PROVIDENT ACCIDENT) 160171 01-01-2025.pdf | 17:58:56.428 | 4m 34s | NEEDS_REVIEW | `6ac3e560bd1959bb15c245ac` |
| 9. SCHEDULE A DATA FOR IDI (PROVIDENT ACCIDENT) 160168 01-01-2025.pdf | 17:58:01.168 | 3m 39s | NEEDS_REVIEW | `6ac3e529bd1959bb15c244b2` |
| 8. 203812.SCH.A...2025.pdf | 18:00:51.586 | 6m 29s | NEEDS_REVIEW | `6ac3e5d3bd1959bb15c24755` |
| 7. ScheduleDocument_20260213.pdf | 18:02:42.181 | 8m 20s | NEEDS_REVIEW | `6ac3e642bd1959bb15c248a7` |
| 6. NFP - Schedule A - 2025.pdf | 18:01:51.671 | 7m 29s | NEEDS_REVIEW | `6ac3e60fbd1959bb15c247db` |
| 3. YI 753370 NFP CORP 202512 PIR.PDF | 18:00:09.224 | 5m 47s | NEEDS_REVIEW | `6ac3e5a9bd1959bb15c24706` |
| 2. CA_N_NFP_2025 Schedule A 5500.pdf | 17:59:37.827 | 5m 15s | NEEDS_REVIEW | `6ac3e589bd1959bb15c245c9` |
| 1. CA_S_NFP_2025 Schedule A 5500.pdf | 18:03:03.922 | 8m 41s | NEEDS_REVIEW | `6ac3e657bd1959bb15c248b0` |
| 1.1.2025 - 12.31.2025 Nfp Schedule A (1) (1).pdf | 17:56:36.627 | 2m 14s | APPROVED | `6ac3e4d4bd1959bb15c24487` |

## Live UI and infrastructure evidence

- Dashboard visibly showed the expanded NFP TEST group with latest upload time 18:03 and the new rows.
- Production dashboard summary showed 82 tracked filings, zero FTW failures, and the NFP TEST group at the top of the table.
- SQS after processing: 0 visible messages; 8 leased/in-flight receipts remaining at the observation time.
- API and worker services were stable on the final task definitions.

## Interpretation

The original failure mode—new uploads waiting behind a long full scan and then timing out on a secondary read—has been removed. The dashboard now starts receiving work promptly. A 14-document batch still drains progressively through the single intake/extraction path; this production run took 8 minutes 41 seconds to register every current file and about 12 minutes 26 seconds to finish all extraction/routing. That is materially different from the previous behavior where the batch could remain absent indefinitely, but further throughput work would be needed if the requirement is for all 14 rows to appear within one to two minutes.
