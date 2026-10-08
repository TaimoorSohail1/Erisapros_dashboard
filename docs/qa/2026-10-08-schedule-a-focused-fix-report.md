# Schedule A focused extraction fix report

Date: 2026-10-08
Branch: `codex/sharefile-priority-ingestion`
Decision: **Ready for human review; not deployed**

## Scope

This pass fixed and retested only the previously affected, single-Schedule-A files. Multiple Schedule As in one document remain out of scope for Version 1 and were not tested.

No dashboard records, database records, or FT Williams values were written during this work.

## What changed

- Allowed the full Schedule A GroundX operation more time while keeping the existing ingestion timeout.
- Added safe retries when the GroundX X-Ray artifact is temporarily not ready.
- Improved Lincoln layout recognition for joined OCR headings and optional labels.
- Preserved full numeric policy identifiers when the source evidence contains a longer exact value.
- Kept local parsing as a fallback and retained the existing validation and FT Williams update flow.
- Added a focused `--item-id` shadow-test option so only affected files can be rerun.

## Focused test set

| Client | File | Result | Extraction route |
|---|---|---:|---|
| Apollo Global Management TEST | 7. 2025 Apollo Management Holdings, L.P. Schedule A - Form 5500.pdf | Pass | Local deterministic parser |
| Bank of Bartlett TEST | 2. VSP Schedule A 1-2025 to 12-31-2025 BOB.pdf | Pass | GroundX structured extract + X-Ray |
| Bank of Bartlett TEST | 4. Colonial.pdf | Pass | GroundX + X-Ray + local parser |
| CareQuest Institute for Oral Health TEST | 4. HARTFORD.pdf | Pass | GroundX structured extract + X-Ray |
| CareQuest Institute for Oral Health TEST | 913136_Schedule A.pdf | Pass | GroundX + X-Ray + local parser |
| Freedom Solutions Group TEST | 4. Litera Lincoln - LTD.pdf | Pass | GroundX structured extract + X-Ray |
| Freedom Solutions Group TEST | 5. Litera Lincoln - STD.pdf | Pass | GroundX structured extract + X-Ray |
| Freedom Solutions Group TEST | 6. Litera Lincoln - VLife.pdf | Pass | GroundX structured extract + X-Ray |
| Freedom Solutions Group TEST | 7. Litera Lincoln - Life.pdf | Pass | GroundX structured extract + X-Ray |
| NFP TEST | 15. 2025-schedule-a-Tuned-NFP.pdf | Pass: safe manual review | Unfilled Schedule A template |
| Saint Elizabeth TEST | St Elizabeth 5500.pdf | Pass: safe manual review | Not a Schedule A; no automatic update |

Summary: **11/11 focused files completed without errors.** Eight used the normal GroundX extraction route, one used the deterministic local parser, and two safely required manual review.

## Accuracy evidence

The automated source audit across the focused set checked 92 extracted values:

- 90/92 values matched directly printed source text: **97.83%**.
- The two automated flags were manually verified:
  - Apollo's premium total is a correct arithmetic total derived from the printed monthly amounts.
  - Hartford's broker name is present in the PDF; OCR spacing caused the automated audit miss.
- One broker component was a safe zero default not printed in the VSP source; it was not invented financial data and does not create an FT Williams update.

After the final Lincoln fix and rerun:

- Extracted values supported by source: **43/43 (100%)**.
- Independent required facts captured: **12/12 (100%)**.
- Broker components supported by source: **64/64 (100%)**.
- Full Lincoln policy identifiers were preserved for LTD, STD, VLife, and Life.
- All four were correctly classified as non-experience-rated.

## Automated tests

- Full backend suite: **1,069 passed, 2 skipped, 74 subtests passed**.
- Relevant focused tests: **7 passed**.
- Python compilation: passed.
- `git diff --check`: passed.

The only test warning was an existing GroundX SDK deprecation warning.

## Safety and release status

- Dashboard writes: **0**
- Database writes: **0**
- FT Williams writes: **0**
- Production deployment: **not performed**
- Multiple-Schedule-A behavior: **excluded for Version 2**

## Recommendation

The affected Version 1 extraction cases are ready for code review and a controlled release. Do not deploy the dirty working tree directly: isolate the approved extraction files, review the diff, commit them, and use the existing gated deployment and rollback process.
