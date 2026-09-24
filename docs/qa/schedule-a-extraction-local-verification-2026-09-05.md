# Schedule A extraction local verification — 2026-09-05

## Scope

This change improves only the extraction and canonical review inputs. FT Williams query, matching, send/update, read-back verification, status UI, and error handling were not changed. Nothing was deployed.

## Verified source cases

| Case | SHA-256 | Verified fields | Verified broker result |
| --- | --- | ---: | --- |
| Guardian / FGF 2025 | `CA5136E489F6B7C015A05830CDF425841A17C0F6849785917EC806BFCFCF7DA8` | 8 | NFP; commission 122,729.20; fees 17,582.30 |
| Federal / Vanbridge 2024 | `B5FF5A61B6584BE102946E6BF45F7F1A789B7B58409D0DEED4FF3A4026B33E92` | 7 | Vanbridge; commission 594.90; fees 0 |

The expected answers and representative page-layout excerpts are stored in `backend/tests/fixtures/schedule_a_verified_cases.json`.

## Before and after

| Check on these two verified cases | Before | After |
| --- | ---: | ---: |
| Exact scalar fields | 6 / 15 (40%) | 15 / 15 (100%) |
| Exact broker rows | 0 / 2 | 2 / 2 |
| Wrong letter/identifier dates | 2 cases | 0 |
| Commission/fee column errors | 2 cases | 0 |
| Duplicate/address-fragment broker rows | 1 case | 0 |
| Canonical validation errors on expected extracted values | Present | 0 |

These figures describe only the two manually verified PDFs; they are not a claim of corpus-wide accuracy.

## Implemented safeguards

- Explicit policy-period rows outrank nearby letter dates and contract identifiers.
- Candidate validity and position evidence are evaluated before confidence.
- Page, row, column, bounding-box, and source-text evidence are retained when available.
- Straight and contingent commissions are summed as commissions; actual fees remain fees.
- Explicit position-backed broker rows replace provider amount errors and address fragments.
- Broker totals reconcile with the scalar commission and fee fields.
- Distinct explicit contracts in one document are treated as separate semantic groups and forced to Review instead of being automatically mixed.
- Missing or uncertain values remain reviewable; no value is invented for “To be provided by Plan Administrator.”

## Verification results

- Backend: `531 passed, 2 skipped, 39 subtests passed`.
- Targeted extraction/semantic/GroundX regression tests: passed.
- Frontend review and FT Williams diagnostics tests: passed.
- Frontend TypeScript check: passed.
- Frontend production build and smoke render: passed.
- Python compile check and Git whitespace check: passed.

## Release status

Not deployed. The next gate is review of this local report and, if approved, a staging extraction run before any production release.
