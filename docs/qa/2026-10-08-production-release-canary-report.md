# Production release and canary report — 2026-10-08

## Final decision

**GO. The broker-reconciliation fix is live and all five approved single-Schedule-A production canaries passed.**

The release corrected the remaining Guardian and JTB broker-row defects without changing the dashboard, database schema, frontend, FT Williams agent code, or FT Williams field-update workflow. Multiple Schedule As in one PDF were not tested because that feature is out of scope until Version 2.

## Released change

- Local implementation branch: `codex/broker-row-deduplication`
- Production build source ref: `codex/automated-ftw-workflow`
- Deployed code commit: `b90f352526a873c9b5e5d12a75386cc1fd5f922f`
- Commit message: `Fix Schedule A broker reconciliation`
- CodeBuild: `erisapros-production-backend:5671c4cc-2f3f-4352-980b-b880c2015178` — **SUCCEEDED**
- Image digest: `sha256:28caacca00036679139741889f3e670f3981c3adc033feb221149738c72fb8f5`
- Production task revisions:
  - API: `erisapros-production-api:120` → `erisapros-production-api:123`
  - ShareFile worker: `erisapros-production-sharefile-worker:110` → `erisapros-production-sharefile-worker:113`
- GroundX credential version `06b50e78-3367-475d-86e3-9e43846229c8` is `AWSCURRENT` and authenticated to bucket `32927`.

Only these code files changed:

- `backend/app/services/extractor.py`
- `backend/tests/test_extractor_schedule_a.py`
- `backend/tests/test_schedule_a_semantic_layer.py`

## What was fixed

- Broker rows differing only by a common one-character OCR suffix are deduplicated when their address, ZIP code, coverage, commission, and fee agree.
- The shorter clean broker name is preserved.
- Guardian fee rows without a recipient name are attached to the correct broker by contract ID.
- Conflicting or uncertain fields continue to require review and cannot be silently sent.

## Test evidence before deployment

- Focused Schedule A extraction suite: **189 passed**.
- FT Williams and broker suite: **250 passed plus 10 subtests**.
- Full backend suite: **1,072 passed, 2 skipped, 74 subtests**.
- Actual Guardian PDF: one Gallagher row; commission `$10,483.70`; fee `$10,713.11`.
- Actual JTB PDF: one Alliance 360 row; commission `$3,238.63` counted once.
- Read-only production-image smoke:
  - JTB deduplication passed;
  - Guardian fee reconciliation passed;
  - GroundX returned HTTP `200`, bucket `32927`, and `20` documents.

## Five production canaries

### 1. Pomerene / Reliance Standard

- Filing: `6ac65a86d414a59ff4d6204f`
- Broker: Gallagher Benefit Services Inc.
- Commission `$1,522.94` and fee `$384.40` were extracted and validated.
- Broker compensation was not incorrectly mapped to total retention.
- FT Williams matched; no update was required; automation completed safely.

### 2. Guardian Dental

- Filing: `6ab3c095270e09d0df21a720`
- Classification: non-experience-rated, confidence `0.90`.
- Exactly one Gallagher row was produced.
- Commission `$10,483.70` and fee `$10,713.11` passed broker validation.
- FT Williams matched; no new update was required after this release.
- The earlier approved two-field update remains verified by FT Williams read-back: attempted `2`, confirmed `2`, remaining `0`.

### 3. JTB Life

- Filing: `6ab367edf9dfc231af394034`
- Classification: non-experience-rated, confidence `0.90`.
- Exactly one Alliance 360 broker row was produced.
- Commission `$3,238.63` was counted once and broker validation passed.
- A real policy-year beginning-date conflict correctly changed automation to `ACTION_NEEDED`; no FT Williams update was sent.

### 4. NFP CA_S

- Filing: `6ac5043dd414a59ff4d61b50`
- Source: `1. CA_S_NFP_2025 Schedule A 5500 (1).pdf`.
- Classification: non-experience-rated.
- Blocked fields `0`; decision-required fields `0`.
- FT Williams matched; no update was required; automation completed.

### 5. NFP CA_N

- Filing: `6ac503d9d414a59ff4d61afe`
- Source: `2. CA_N_NFP_2025 Schedule A 5500 (1).pdf`.
- Classification: non-experience-rated, confidence `0.90`.
- Blocked fields `0`; decision-required fields `0`.
- FT Williams matched; no update was required; automation completed.

The obsolete CareQuest filing ID was no longer present in production, so it was not processed. It was replaced with the two current, approved NFP single-Schedule-A cases above.

## Final production verification

- API: desired `1`, running `1`, pending `0`, rollout `COMPLETED`.
- ShareFile worker: desired `1`, running `1`, pending `0`, rollout `COMPLETED`.
- API target: `healthy`.
- Health endpoint: HTTP `200`, `{"status":"ok","stack":"react-python-mongodb"}`.
- ShareFile queue: visible `0`, in progress `0`, delayed `0`.
- Current API log stream: `0` error/exception/traceback hits.
- Current worker log stream: `0` error/exception/traceback hits.
- The five canaries created no new FT Williams agent jobs.
- Frontend, database schema, and FT Williams agent code: unchanged.

## Safety and rollback

If a post-release defect appears, restore:

- API task definition `erisapros-production-api:120`;
- worker task definition `erisapros-production-sharefile-worker:110`;
- image digest `sha256:a8e67b56184f484ebc47c2b82f5c4d03c4fac57b39e23285f2e9d4742232efa2`;
- GroundX secret version `f1bd8429-f939-4be6-9394-3e22a0dad430` from `AWSPREVIOUS` to `AWSCURRENT` if credential rollback is also required.

No rollback is currently required.
