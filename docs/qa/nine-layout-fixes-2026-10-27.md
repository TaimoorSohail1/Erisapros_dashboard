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
- A present but unreadable worksheet beginning-count cell retains a review hold even when FTWilliam already contains a value.
- Bulk ShareFile intake queues existing stored-file references instead of retaining all source bytes. Extraction loads the current document within its existing slot and verifies its source hash.
- Provider submissions are journaled in existing audit storage with job/rule/source/bucket binding. Recovery polls a known process; ambiguous submissions or changed rules stop for investigation. Legacy interrupted jobs without checkpoints cannot silently re-upload.
- Uncertain provider submissions are propagated for both Schedule A and unreadable-worksheet OCR; neither route hides them as an empty successful extraction.
- The installed SDK's hidden ingestion POST retries are disabled for this request. Offline transport tests reproduced three submissions after HTTP 500 before the correction, and one submission afterward for server, rate-limit and protocol failures.
- Cancellation cannot downgrade an already completed extraction and cause duplicate dispatch on redelivery. Interrupted post-processing remains review-required.
- The existing dashboard retry action cannot bypass a prior submission by creating a fresh job: active jobs are rejected, verified failed jobs resume with their original journal, and ambiguous, changed-source or changed-rule retries remain held. Its response shape and explicit re-extraction of completed work remain unchanged.
- Degraded Lincoln OCR cannot promote a fragmented premium or a partial fee-only row over a complete provider recipient. Readable header totals remain distinct from incomplete recipient allocations. One current completed document can recover a degraded page only when contract/scalars and printed header totals agree and all alternate recipient subtotals reconcile; readable conflicts remain untouched.
- Weekly Income is recognized only in a declared benefit context as temporary disability. Spaced field markers in a bound source read retain their report identity. A worksheet date without a year cannot invent a one-digit year; valid explicit filename-year handling remains intact.
- A premium component's one-glyph contract discrepancy is corrected only when a current-document provider row agrees on page, coverage, amount and persons, and its contract equals the primary contract. The original literal and corroborating evidence remain available.

## Verification so far

- Each new regression was observed failing before its correction.
- Windows backend suite: **1,687 passed; 2 skipped; 166 subtests passed**.
- Offline source slices: all nine actual worksheets plus three targeted captured-source checks passed (12 checks). This is not fresh-provider evidence.
- Exploratory captured-provider/OCR replay recovers all four Hartford recipients, signed components and the previously omitted bonus fees. It does not prove fresh-provider or live-write success.
- First commit `34c1d25`: Linux image backend suite **1,676 passed; 13 skipped; 166 subtests passed**. Image source hashes and archive identity verified; frontend compatibility checks/build passed. QA compute is terminal and its exact temporary object grant was removed.
- Follow-up full Windows suite: **1,707 passed; 2 skipped; 166 subtests passed**. Two additional ambiguous-submission cases then passed in the focused 77-test run. The follow-up image still requires Linux verification.
- Head `f421cf2`: full Windows **1,716 passed / 2 skipped / 166 subtests**; Linux **1,705 passed / 13 skipped / 166 subtests**, plus frontend compatibility tests and build. Exact image/source identity verified. A private 27-PDF Linux wire replay uses real OCR and a 1,792 MiB container limit; the initial harness attempt was stopped for expectation-schema errors and corrected without altering the application image.
- Subsequent API-path review reproduced six retry/journal bypass failures before correction. The corrected retry and recovery tests pass (24 focused checks); the final API follow-up requires its own full-suite/image verification.
- Retry head `0b6129f`: Windows **1,724 passed / 2 skipped / 166 subtests**; Linux **1,713 passed / 13 skipped / 166 subtests**, with frontend compatibility/build checks passing and source hashes unchanged.
- The 27-PDF Linux replay completed every package without a container memory kill. Original examples passed their comparator. It exposed a real degraded-OCR Lincoln defect and source-approval differences on changed PDFs; the first verdict is retained rather than rewritten. Separately, the expanded harness used a generic worksheet filename and lost its explicit year; later inputs preserve the original filename without changing source bytes. Bonus/Bonus Paid wording follows the already documented equivalence.
- Lincoln follow-up: new failures were observed before correction, recipient-preservation regressions were corrected without relaxing those tests, and the full Windows suite now passes **1,734 tests / 2 skipped / 166 subtests**. Captured actual-source replay recovers the correct monetary values, covered count and Weekly Income benefit. Its source-specific contract/recipient interpretation remains review-required; no new business approval was manufactured.

## Required remaining work

- Verify the frozen candidate inside the Linux image and actual scanned intake under Linux OCR.
- Finish source-backed checks of full Aetna visual/financial output, component metadata and all nine mapped results.
- Complete Linux resource/recovery testing and recover the specific Lincoln job from its independently verified existing process without resubmission.
- Verify current FTWilliam comparisons are refreshed using the existing fresh-before-send path.
- Read-only AWS inspection found repeated worker exit 137/OutOfMemoryError. A bulk-intake regression reproduced retained source bytes (approximately 25 MB for 24 one-MiB files); the corrected queue passes a six-MiB retained-memory bound. This does not alone prove the cause of the original Lincoln stall or resolve every possible memory spike. Run Linux stress checks before release.
- Regress original and changed-value layouts and the unrelated working corpus.
- Review the final exact commit, then image-only controlled rolling release and disposable-only live verification/restoration.

## Preserved constraints

No production changes or filing writes have been made in this correction run. Keep real-client filings, published rules, account configuration, routing and automatic controls unchanged. Real source/date/name/contract conflicts remain review-required.

The user has additionally approved the two newly distinguished disposable destinations for the actual Unum and Lincoln STD uploads. Exact client/plan/sequence identities and approvals remain in private QA evidence. Medical retains its separate date/broker restrictions and its two intentionally saved values; the Dental destination is outside that Medical test.

Do not claim all fields or all automatic updates passed until new source comparisons, correct matches, dispatch evidence and independent saved-value queries prove that claim.
