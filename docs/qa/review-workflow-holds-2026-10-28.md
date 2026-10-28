# Review workflow holds: fix and verification

## Reproduction and cause

A filing with no unresolved scalar fields still displayed "1 field need your
decision" and an empty Decisions Needed table. Its actual remaining action was
a plan-year choice between a January 1 worksheet period and an October 1 current
FTWilliam period. The two date choices were hidden inside the closed status
drawer, and Resolve scrolled to the scalar table.

A second source hold was hidden: a broker's five-digit ZIP had been explicitly
saved, but the original `source_zip_shape` error still marked the row
`REVIEW_REQUIRED`. Frontend validation ignored these saved source errors, and
backend decision counts included unresolved broker matches but omitted broker
validation holds.

## Narrow change

- Label mixed workflow actions as decisions; show the date choices inline and
  navigate Resolve to the pending workflow action.
- Show saved broker source errors and unexplained review holds, with a direct
  link to the broker editor. Draft validation checks the submitted field shapes,
  while the saved source hold remains visible until a successful save.
- An explicit indexed broker save with a valid five- or nine-digit ZIP can
  acknowledge only its ZIP source hold. Retain printed source evidence, confidence,
  payment values, unrelated source errors, and all unedited rows. Record the
  manual ZIP confirmation separately; never report it as automatic extraction.
- Include broker source holds in decision counts, deduplicated against broker
  matching decisions for the same row.

Source ingestion still retains malformed printed ZIPs for review. No period is
chosen automatically. Routing, update eligibility and filing matching are unchanged.

## Verification

Failing regressions were observed before the fix. After the fix:

- Six review-hold backend regressions pass, including explicit ZIP confirmation,
  metadata omission, untouched rows, invalid/blank ZIPs, and decision-count deduplication.
- All 157 existing FTWilliam review tests pass.
- All 51 ZIP-preservation, broker-matching and OCR-recipient tests pass.
- Frontend review and dashboard suites, TypeScript check, production build and
  built-app smoke render pass. A local DOM test exercises date choices, busy-state
  disabling, source-hold visibility and workflow-aware navigation.
- Offline replay of the captured filing through the real review service uses an
  in-memory repository with all vendor requests forbidden. It reports two holds
  before an explicit ZIP save, then one unresolved date choice afterward. Existing
  Keep Current sponsor choice, fetched FTWilliam values, source evidence and
  payments remain unchanged. The plan-year send guard remains active and automatic
  sending stays ineligible.

Replay evidence is retained privately outside Git. The production bundle has an
existing size warning; compilation and smoke rendering succeed.

## Manual QA / release

This change has not been deployed. Human review and browser QA remain pending:

1. Open a test filing with a date conflict and no scalar conflicts. Check the
   visible worksheet/current periods and both date-choice buttons without selecting
   either. Resolve must land at those controls.
2. Check a saved broker source error with a structurally valid ZIP. Its warning
   and row edit action must be visible, and the count must include the broker once.
3. On an isolated test fixture, explicitly save a complete ZIP. Check that its
   ZIP error resolves while unrelated source errors, other brokers, and any
   unresolved date choice remain protected.
4. Check ordinary scalar-conflict and no-conflict filings on desktop/mobile widths.

No production records, FTWilliam filings, dates, broker values, or automatic-update
settings were changed during this diagnosis and verification. This filing's new
contract is outside the earlier disposable Medical/Dental test approval; do not
select dates or create its Schedule A as part of UI QA.
