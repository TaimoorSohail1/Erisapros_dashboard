# PR 59 review follow-up: no merge or deployment

Follow-up to reviewed head `968de82`. Scope is the three locally reproduced review findings; no provider settings, business rules, production services or client filings are changed by this work.

## Fixes and regression evidence

1. **Keep Current and outgoing payloads.** A full update of another field could send the kept field's original replacement because XML was built before the no-write decision was applied. Resolve source/target/current-bound decisions before building either form's XML. The review API also rebuilds its preview from the already-fetched vendor snapshot after saving the decision, without another vendor query. Integration coverage follows the reviewer command through full parent and Schedule A dispatch, preserves the sibling record, and checks that a subsequent explicit edit cancels preservation.
2. **Literal saved-value evidence.** Reconciliation rebuilt the review and lost this attempt's read-back observations. Carry those observations explicitly across the refresh and persist them on the final review. Integration coverage uses detached repository objects and an external-system fake that saves a rounded value different from the submitted value; checks survive storage and refresh. A rejected field remains failed with its actual returned value, and a later successful attempt replaces the previous evidence.
3. **QA retry authorization.** A connection retry reused the initial permission check. Recheck the current QA registry before each retry dispatch. Boundary regressions deny retries after closure or expiry, close their temporary HTTP client, and allow an unchanged authorized retry.

Each original failure was observed in a failing regression before its fix. External vendor/registry/network boundaries are simulated; review, payload construction, verification and repository round-trips run normally. Tests make no live vendor requests.

## Verification and release boundary

- Focused regression suite: 37 tests and 14 subtests passed.
- All six frontend behavior suites, the TypeScript/Vite build and built-bundle render smoke check passed. The build retains the existing large-chunk advisory.
- Full Windows backend regression run: 1,616 tests and 147 subtests passed; two existing legacy-XLS fixture tests skipped because `xlwt` is not installed. One existing GroundX SDK deprecation warning remains. The successful full-suite invocation supplied the repository's required backend import path; an earlier collection-only attempt without that path was not a code-test failure.
- Focused AI re-review found no additional confirmed defect in these changed paths. Existing stale-context, selected-send, sibling, broker, transport and extraction regressions remain green; this is not a claim that every live value or environment has been verified.
- No merge, deployment, production credential change, ShareFile intake or real/disposable FTWilliam update was performed.

The previous immutable Linux image and nine-document fresh-provider evidence belong to the previous head, not this follow-up. Exact-image Linux verification, independent developer review/release sign-off, and the separately authorized disposable live workflow/read-back/restoration gates remain required. This AI re-review does not replace independent human sign-off or clear the previously documented raw-provider/coverage limitations.
