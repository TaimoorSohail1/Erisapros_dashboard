# ANSEL NAIC and FT Williams production verification

Date: 2026-10-06  
Production scope: NAIC extraction/validation regression coverage, orphan Schedule A broker-code safety guard, and the approved ANSEL Schedule A #10 update/read-back.

## Authorization and release scope

The user authorized the ANSEL update, production fix, deployment, live test, and final report. The release changed only the backend Schedule A review safety logic and its tests. No frontend asset, infrastructure, secret, credential, automatic-send flag, or database configuration was changed.

## Source and validation findings

- The supplied CuraLinc/NFP source PDF literally contains `Carrier NAIC 624190`.
- Extraction preserves that six-digit source value exactly; it does not guess or silently truncate it.
- FT Williams requires exactly five digits. The existing validation correctly marks `624190` as invalid and requires review.
- A regression test now locks this fail-closed behavior in place.

## ANSEL live FT Williams result

- Filing: `6ac4a2806cd580ce1d3cd9c8` (`Re NFP Ansel Schedule A.msg`).
- Match: 2025 Schedule A #10, `ANSEL SERVICES, INC.`, contract `LB-10000116`.
- The approved update was sent and FT Williams read-back completed at 2026-10-06 08:34:38.
- Verified returned values:
  - `1c. NAIC Code`: `71870`
  - `1e. Persons Covered (End of Policy Year)`: `2180`
  - `3e. Organizational Code`: `3`
  - `10a. Total premiums or subscription charges paid to carrier`: `737,398.05`
- The Form 5500 active participant value was already current at `6,159`, so it was retained rather than rewritten.
- Final comparison: 4 updated, 3 kept current, 10 skipped, 0 conflicts.

FT Williams resolved two prior edit checks through this update:

- `FW-117`: Number of Persons Covered may not be blank.
- `FW-410`: Line 10a must be completed.

FT Williams then reported one new edit check:

- `FW-001`: Line 3 contains partial broker information. The source supplied organizational code `3` but no broker name/address row, so sending the code alone created an incomplete Line 3 record.

## Global fix

The review safety gate now refuses to send Schedule A organizational code `3e` when neither the current FT Williams record nor the extracted filing contains a broker name. The field is shown as blocked with the reason `organization code cannot be sent without a broker name`. Complete structured broker rows continue through the existing broker-row workflow.

This prevents future filings from creating `FW-001` by sending an orphan broker code. The existing ANSEL record still contains the already-written code and therefore retains the vendor warning until the code is cleared or a complete broker row is supplied. No broker identity or address was invented.

## Verification

- Focused red/green regression: passed.
- Relevant backend suites: 280 passed.
- Full backend suite: 1,050 passed, 2 skipped; one existing GroundX SDK deprecation warning.
- Frontend performance, review UI, responsive UI, update receipt, workflow step, typecheck, production build, smoke build, dashboard, field rules, ShareFile, and FTW agent checks: passed.
- Exact deployed-image smoke task: exit 0 with `ORPHAN_CODE_GUARD_PASS`.
- Production `/api/health`: HTTP 200, `status: ok`.
- ECS API and worker: one running, zero pending, rollout complete.
- Load-balancer target: healthy.
- Post-release API/worker error scan: zero `ERROR`, `CRITICAL`, `Traceback`, or `Exception` events.
- Signed-in live filing reload still shows `FT Williams verified`, Schedule A #10 matched, and the 4-field read-back receipt.

## Release evidence

- Commit: `c873d7dfc6cb73f1369f9b3e5b7c27f3d25a1990`
- Release branch: `codex/automated-ftw-workflow`
- CodeBuild: `erisapros-production-backend:06255141-f4b0-4548-bb67-b1a8739b5330` — succeeded
- Immutable image: `sha256:5045b22173984ee3231c4d5d0b4a56cb6c9fb5f18da52d0b1e02ac060c18aee0`
- API: `erisapros-production-api:118` → `erisapros-production-api:119`
- ShareFile worker: `erisapros-production-sharefile-worker:108` → `erisapros-production-sharefile-worker:109`
- Task-definition comparison confirmed that only the container image changed.
- Worker queue was allowed to drain to zero before its service update.

Rollback: restore API `:118` and ShareFile worker `:108`. No frontend or database rollback is required for this release.
