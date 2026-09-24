# All-client live EyeLevel semantic issues

Date: 2026-08-29
Scope: 50 live browser uploads through the deployed async EyeLevel QA flow

## Summary

- Transport/runtime defect: **fixed** by asynchronous submission and polling.
- Live jobs: **50 / 50 completed** after one transient authentication retry.
- Confirmed source-semantic defects: **30 client PDFs** in the controlled comparison.
- Source-limited PDFs: **2**.
- New nondeterministic semantic regressions surfaced in the live rerun.
- Automatic FT Williams release: **blocked**.

## Release-blocking live examples

| Client | Live output | Why it is unsafe |
|---|---|---|
| New York Yankees | Carrier `of America.`; dates `04/09/2026` and `12/01/25` | Header fragments/unrelated dates passed as fields |
| Preferred Pump | Commission `2,170.33`; fee `2,170.33` | One combined value duplicated into two columns |
| Red Thread | Carrier includes an address/NAIC label; commission and fee both about `2,910` | Cross-column and cross-label contamination |
| Rhythm Pharmaceuticals | Carrier `SERVICE OR OTHER ORGANIZATION`; dates `03/18/2026` and `01-01-2025` | Header text and impossible date ordering passed validation |
| Socure | Broker contains plan/header text; persons covered `3345593`; policy `Policy`; tax is prose | Multiple wrong-section values received high confidence |
| Special Service for Groups | Both policy dates `03/12/2026` | Document preparation date substituted for coverage period |
| Advertising Council | Tax field contains Affordable Care Act prose | Numeric field accepted nonnumeric narrative text |
| The Boston Home | Carrier contaminated with address/NAIC text; combined amount duplicated | Row boundaries not respected |
| The International Group | Carrier `EIN (Insurance Carrier)`; policy `SEE` | Labels/placeholders accepted as final values |
| Tilt Holdings | Carrier contaminated with address/NAIC text; combined amount duplicated | Row boundaries not respected |
| Titmouse | Both policy dates `03/12/2026` | Preparation date substituted for coverage period |
| Tower Health | Other-expense field contains prose; commission copied into fee | Type validation and column reconciliation failed |

## Root causes

1. A Field Rule match is treated as evidence of correctness even when the source coordinates are from another section.
2. Semantic validation checks basic shape but does not always enforce field type, date ordering, table row, or column ownership.
3. Combined commission/fee cells are guessed into both separate fields.
4. Repeating carrier and broker groups can be collapsed or partially omitted.
5. Header labels, addresses, preparation dates, placeholders, and legal prose can be accepted as values.
6. Provider output is nondeterministic: a value can pass one run and fail differently on the next.

## Required global fixes

1. Require page, bounding box, table identity, row, column, and supporting text for every candidate value.
2. Segment carrier/contract/coverage groups before mapping any Schedule A field.
3. Enforce strict semantic types: numeric amounts, valid EIN/NAIC, reasonable persons counts, and ordered policy dates.
4. Reject values whose evidence overlaps labels, headers, addresses, preparation dates, or narrative instructions.
5. Preserve combined commission/fee values as one ambiguous source value; never duplicate them automatically.
6. Reconcile broker rows against totals without forcing equality across different compensation types.
7. Reject unresolved placeholders such as `SEE`, `SEE ABOVE`, or `Policy`.
8. Compare repeat runs and fail closed when material fields change.
9. Keep all 50 PDFs and their approved row-level expected results as permanent regression fixtures.

## Acceptance gate

Do not release automatic FT Williams updates until:

- all clear values map to the correct group, row, and column;
- every ambiguous or conflicting value enters Review;
- repeated runs are materially stable;
- all 50 approved expectations pass;
- completely new layouts fail safely rather than guessing;
- Field Rule and alias additions work without client-specific code;
- a selected FT Williams Schedule A update leaves every unrelated Schedule A unchanged.

No FT Williams record was changed during this extraction QA run.
