# FT Williams Schedule A Workflow QA

Date: 2026-08-22
Branch: `codex/fix-ftw-schedule-a-workflow`
Production deployment: not performed

## Outcome

The workflow hardening is implemented and the automated FT Williams tests pass. A controlled end-to-end sandbox write did **not** pass the release gate: FT Williams rejected the Form 5500 update and returned an empty body for the Schedule A update. A read-only query then showed that the current Schedule A record was missing. No retry or automatic Bring Forward was performed.

The failed sandbox run exposed two additional safety gaps. Both are now fixed locally:

- A failed or ambiguous Form 5500 write stops the request chain before any Schedule A write.
- Plan Sponsor Name is review-only because the configured FT Williams endpoint rejected `SPONS_DFE_NAME0` through the update contract.

## Implemented changes

1. Added `UPDATE_UNKNOWN` for HTTP-success responses with an empty or malformed FT body.
2. Ambiguous updates are never reported as successful, never auto-retried, and do not trigger an automatic current-data query.
3. Failed current-data refreshes preserve the last valid snapshot and cannot incorrectly enable Bring Forward.
4. Sending is blocked when the current FT snapshot is incomplete.
5. FT requests follow a bounded sequence: current query/validation, one write per form, then read-back verification only after confirmed acceptance.
6. A failed/ambiguous Form 5500 response now prevents the Schedule A write from starting.
7. Update counts include only forms that were actually sent.
8. Automatic Schedule A matching now requires safe identity evidence. A sole candidate is no longer accepted merely because it is the only candidate.
9. A stale automatic Schedule A match is cleared after a successful refresh; explicit manual/new-record choices are preserved appropriately.
10. The UI requires a Schedule A decision even when one unmatched candidate exists.
11. The main page again shows **Open FTW Bring Forward** when FT confirms the current-year record is missing.
12. Unsupported edited fields show **Review only · not supported**, not **Resolved—not sent**.
13. Rapid duplicate Send clicks are blocked immediately in the frontend.
14. Verification-required outcomes are included in the FT failure queue/history.

## Read-only and extraction evidence

- FT PlanIDs batch: HTTP 200, 3,348 accessible records.
- Known 2024 test plan: Form 5500 returned 52 fields; Schedule A returned 69 fields.
- ShareFile QA package extraction completed locally from its stored source documents.
- Extraction produced 62 mapped review fields, with 35 source values found.
- The QA worksheet contained a plan-year period that did not match the selected FT year, and the send was correctly blocked.
- A separate Kaiser/Principal case reproduced the unsafe single-candidate bug. The extracted Kaiser identity conflicted with FT's Principal contract, EIN, and NAIC. After the fix, the review has no automatic match, is incomplete, and requires an explicit selection or new Schedule A.

## Controlled sandbox write evidence

Test filing: `99. principal duplicate SSUDBURY_KO25040415560.pdf (2 documents)`
Local filing ID: `6a8851534ce37476468274a7`

Pre-send safety checks:

- FT year: 2024
- FT filing: editable
- Current query: complete
- Schedule A records fetched: 1 of 1
- Match: contract + carrier EIN + NAIC, score 25, three strong identity matches
- Planned supported changes: 5 total (3 Form 5500, 2 Schedule A)

FT response:

- Form 5500: rejected with error 60 for `SPONS_DFE_NAME0`.
- Schedule A: HTTP response body was empty/malformed.
- System result: `UPDATE_UNKNOWN`.
- Confirmed fields: 0.
- Automatic retries: 0.
- Automatic verification query after the ambiguous response: 0.

Manual read-only verification:

- The three Form 5500 values remained unchanged.
- FT returned no usable current Schedule A record.
- The filing now requires FT Williams' native Bring Forward/recovery action before another test.

This write occurred before the newly added stop-on-first-failed-form gate. With the current branch, the Form 5500 rejection would stop the chain and the Schedule A write would not be attempted.

## Automated verification

- Backend full suite: **305 passed, 2 skipped**.
- Focused FT review/history suite: **54 passed**.
- Frontend TypeScript: passed.
- Shared polling/performance regression: passed.
- Filing review UI regression: passed.
- Field Rules UI regression: passed.
- Production frontend build: passed.
- Built-app smoke test: passed.
- `git diff --check`: passed (line-ending warnings only).

In-app browser automation could not start because its local kernel assets were unavailable. The ShareFile extraction and FT Williams checks were therefore executed through the project's configured services and direct API integration; no browser screenshot is claimed as evidence.

One unrelated existing dashboard responsive-layout test still fails because `styles.css` does not contain the expected viewport-height rule. This branch does not modify `styles.css`.

## Release recommendation

**Do not deploy this branch yet.** The code protections pass, but the required real FT sandbox write/read-back gate did not succeed. Before release:

1. Restore the missing test Schedule A in FT Williams using the native recovery/Bring Forward flow.
2. Confirm with FT Williams whether blank update responses can still apply replace-style Schedule A changes.
3. Repeat the end-to-end test on a dedicated disposable sandbox plan.
4. Require: complete pre-query, strong identity match, one accepted write per intended form, successful read-back for every sent field, zero duplicates, and zero unexplained missing schedules.
5. Resolve or formally waive the unrelated dashboard responsive test.
