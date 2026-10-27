# Nine-layout audit corrections — implementation record

Baseline: `4617b337eb0d3b7e06ac0538a34319dff5e953aa`.
Private source audit: `outputs/carrier-dashboard-audit-2026-10-27` in the primary checkout.
New evidence: `outputs/nine-layout-fixes-2026-10-27` in the primary checkout.

## Implemented; not deployed

- Worksheet beginning counts accept an optional instruction note without crossing into another row. Blank and zero remain distinct.
- One visible checkbox binds only to its nearest benefit label; parenthetical wording cannot reuse it for Other.
- Declared comma-separated FLEX benefits can continue across physical lines, bounded before another section.
- Benefit-level lives tables stop before experience/nonexperience financial sections.
- A broker-table boundary accepts OCR Roman numeral variants, preventing a later premium from becoming commission.
- An Aetna Part I table with no readable payment retains unsupported provider guesses privately but withholds commission/fee values and holds recipient updates. No printed zero is inferred.
- Exact URL-encoded X-Ray filenames qualify only with the existing literal completed-document name, matching process and matching source URL. This enables existing reconciled Hartford recovery without weakening cross-document checks.
- Newly encountered image-only PDFs can be content-classified using the first three pages of bounded local OCR. Native-text classification and extraction-quality OCR retain their existing paths.

## Verification so far

- Each new regression was observed failing before its correction.
- Windows backend suite: **1,687 passed; 2 skipped; 166 subtests passed**.
- Offline source slices: all nine actual worksheets plus three targeted captured-source checks passed (12 checks). This is not fresh-provider evidence.
- Exploratory captured-provider/OCR replay recovers all four Hartford recipients, signed components and the previously omitted bonus fees. It does not prove fresh-provider or live-write success.

## Required remaining work

- Verify the frozen candidate inside the Linux image and actual scanned intake under Linux OCR.
- Finish source-backed checks of Hartford component identifiers, full Aetna visual/financial output and all nine mapped results.
- Diagnose Lincoln completion and add restart/timeout recovery without duplicate ingestion or dispatch.
- Recheck missing-source review holds and freshness of current FTWilliam comparisons.
- Read-only AWS inspection found repeated worker exit 137/OutOfMemoryError. Its relationship to the original Lincoln stall is not established. Reproduce and bound resource use before release; do not attribute it to a specific parser without evidence.
- Regress original and changed-value layouts and the unrelated working corpus.
- Review the final exact commit, then image-only controlled rolling release and disposable-only live verification/restoration.

## Preserved constraints

No production changes or filing writes have been made in this correction run. Keep real-client filings, published rules, account configuration, routing and automatic controls unchanged. Real source/date/name/contract conflicts remain review-required.

The user has additionally approved the actual Unum/Apollo sequence 5 and Lincoln STD/Freedom sequence 5 as disposable for update/read-back/restoration. Medical sequence 1 retains its separate date/broker restrictions and its two intentionally saved values; Dental sequence 3 is outside that Medical test.

Do not claim all fields or all automatic updates passed until new source comparisons, correct matches, dispatch evidence and independent saved-value queries prove that claim.
