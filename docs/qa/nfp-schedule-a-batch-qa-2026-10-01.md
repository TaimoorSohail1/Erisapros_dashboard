# NFP Schedule A batch QA and production-sandbox verification

**Date:** 2026-10-01

**Branch:** `codex/nfp-broker-auto-send-fix`
**Result:** **PASS WITH SOURCE-DEPENDENT REVIEW ITEMS.** The implemented feedback fixes are deployed and verified. Clean, unambiguous NFP Schedule A records now update FT Williams automatically and produce a receipt, read-back verification, and a downloadable FT Williams PDF. Records with real source/current conflicts still pause safely for a decision.

## Release under test

- Head commit: `e7a6765` (`Correct Schedule A rating classification`).
- Other batch fixes on this branch: `9415445`, `8852be4`, `b5b7850`, `cb4d0e0`, and `42c8974`.
- Production image: `sha256:9707077595dac57d6df80b7b45427f40a465f3a054aa48087221ee8f1734c06e`.
- ECS API task definition: revision `45`, rollout `COMPLETED`, 1/1 running.
- ECS ShareFile worker task definition: revision `35`, rollout `COMPLETED`, 1/1 running.
- `FTW_AUTOMATION_AUTO_SEND_ENABLED=true` on both services.
- Production health endpoint returned `{"status":"ok","stack":"react-python-mongodb"}`.

## Automated verification

- Backend full suite: **895 passed, 2 skipped, 1 GroundX SDK deprecation warning, 64 subtests passed**.
- Focused Schedule A classification/automation/XML suite: **142 passed**.
- Frontend TypeScript check: passed.
- Filing review UI tests: passed, including the verified update receipt contract.
- Dashboard UI tests: passed.
- Production frontend build and rendered-bundle smoke test: passed.
- Vite reported a non-blocking bundle-size advisory; it did not affect the build or smoke test.

## Plan worksheet package verification

The live R7 Provident Casualty filing packaged the newly uploaded Schedule A with `5500 Plan Worksheet - NFP 5500 - PY25.docx`; the filing contained exactly those two document types. The plan worksheet values agreed with FT Williams and were classified `NO_CHANGE`:

| Field | Extracted | FT Williams |
|---|---|---|
| Plan name | NFP CORP. GROUP HEALTH AND WELFARE PLAN | Same |
| Plan number | 511 | 511 |
| Sponsor EIN | 13-4029115 | Same |
| Plan year start | 01-01-2025 | 01/01/2025 |
| Plan year end | 12-31-2025 | 12/31/2025 |

This proves the current package flow is using the NFP PY25 plan worksheet and is not inventing a second set of plan identifiers.

## Thirteen-file NFP batch outcome

| # | Source | Final result | Evidence / correct behavior |
|---:|---|---|---|
| 1 | Continental American canary | Automatic pass | Filing `6abe3757340793f1e1ea8a81`; exact existing Schedule A match; 3/3 values read-back verified; verified FT Williams PDF produced. |
| 2 | CA-S | Safe review | One real carrier-name conflict; five non-conflicting values remain staged. No unsafe send. |
| 3 | CA-N | Safe review | One real carrier-name conflict; five non-conflicting values remain staged. No unsafe send. |
| 4 | YI 753370 PIR | Regression passed; safe review | R7 filing `6abe6cc14948f88632225c03`; no extracted or proposed `DUE` fragment; four genuine extracted/current conflicts remain, so it correctly pauses. |
| 5 | Schedule A 2025 / CuraLinc | Source blocker retained | Source contains an invalid six-digit NAIC value. The system blocks instead of truncating or guessing it. |
| 6 | ScheduleDocument / Ameritas | Safe review | Carrier-name conflict remains; safe values and broker matching are preserved. |
| 7 | 203812 template stress file | Source blocker retained | Placeholder/incomplete source causes 14 blocking validations. It is not safe to auto-send. |
| 8 | Provident Accident 160168 | Automatic pass | Filing `6abe5f561f133f8e384d0c84`; sequence 9; 2/2 read-back verified. |
| 9 | Provident Accident 160171 | Automatic pass | Filing `6abe6368ce680c643c3f227e`; sequence 12; 2/2 read-back verified. |
| 10 | Provident Accident 169225 | Automatic pass | Filing `6abe64c0ce680c643c3f22cc`; sequence 13; 2/2 read-back verified. |
| 11 | Provident Casualty 160040 | Automatic pass after fix | R7 filing `6abe6b324948f88632225b81`; sequence 14; correct nonexperience classification; 2/2 read-back verified. |
| 12 | Tuned template | Source blocker retained | Blank/template values cause 14 blocking validations. It is not safe to auto-send. |
| 13 | BCBS | Regression passed; safe review | R7 filing `6abe6ccd4948f88632225c08`; NAIC is correctly normalized to `53295` and matches FT Williams; two genuine carrier/purpose conflicts remain. |

## Live automatic FT Williams evidence

| Contract | Filing / sequence | Verified FT Williams values | Receipt | PDF SHA-256 |
|---|---|---|---|---|
| Provident Accident 160168 | `6abe5f561f133f8e384d0c84` / 9 | Covered persons `50`; line 10a `50,578.96` | `b2809cb0da8e4ad7b8406a6cf5ddd480` | `47345f4447f57634ddff39536f89cefda5bf61b93f5e99c6a3756f19ce3fcb9c` |
| Provident Accident 160171 | `6abe6368ce680c643c3f227e` / 12 | Line 10a `0.00`; covered persons `01` | `4d420fc6035e4088a34aa9b872025cbb` | `497da21d08d82de80b8922a6f0e7db6fbceb51c56da067d77475392b2d082c33` |
| Provident Accident 169225 | `6abe64c0ce680c643c3f22cc` / 13 | Covered persons `1`; line 10a `3,825.12` | `db50cd5256cd4c92bd8e3c1fc8f90bea` | `ed7b6bc349da541b2774bdb7d961b36df705f42edb837097353c11a5dc4f8e70` |
| Provident Casualty 160040 | `6abe6b324948f88632225b81` / 14 | Covered persons `775`; line 10a `758,204.74` | `ee152ee095f44a31b96aa80237a075f3` | `ca9ec6111317b8fb5c3b0bfe16aa4e08475695910208d4fc3aa919ad7b5e79b5` |

Every row above finished with filing `APPROVED`, review `UPDATE_SENT`, zero decisions, zero blockers, zero remaining updates, and `update_verification_success=true`.

Local evidence copies:

- `output/qa/nfp-batch-2026-10-01/evidence/08-provident-160168-ftw-verified.pdf`
- `output/qa/nfp-batch-2026-10-01/evidence/09-provident-160171-ftw-verified.pdf`
- `output/qa/nfp-batch-2026-10-01/evidence/10-provident-169225-ftw-verified.pdf`
- `output/qa/nfp-batch-2026-10-01/evidence/11-provident-casualty-160040-ftw-verified.pdf`

The local PDF hashes match the stored production evidence hashes. The Provident Casualty PDF text also confirms contract `0000160040`, 775 covered persons, the expected NFP broker rows, and rounded FT Williams line 10a output `758,205`; the API read-back preserves and confirms the exact value `758,204.74`.

## Fixes proven by this run

1. **Correct nonexperience classification.** A source headed `NON-PARTICIPATING CONTRACTS (PREMIUMS)` now wins over a spurious line 9 value and maps the sourced premium to line 10a.
2. **Safe experience totals.** Calculated experience totals receive source evidence only when every component is page-backed and numeric.
3. **FT Williams zero formatting.** Values such as `$ .00` normalize safely to zero.
4. **No unsourced zero updates.** Parser defaults without source evidence are skipped instead of becoming FT Williams writes.
5. **Broker replacement safety.** Existing matched broker rows are preserved; genuinely unmatched brokers can be added without duplicating matched rows.
6. **Long broker names.** Deterministic standard abbreviations are applied only at the FT Williams boundary; values that still cannot fit fail closed.
7. **NAIC normalization.** BCBS produces `53295`, not the earlier malformed value.
8. **Invalid text fragment rejection.** `DUE` is absent from YI extracted/proposed update values.
9. **Professional completion UI.** The dashboard reports completed verified updates; the filing page shows the destination, counts, receipt state, field-by-field sent/read-back values, a direct FT Williams action, and `View verified PDF`.

## Expected remaining review items

The branch does not auto-resolve factual disagreements. CA-S, CA-N, YI, Ameritas, and BCBS still pause where a valid extracted value differs from a populated FT Williams value. CuraLinc, 203812, and Tuned remain blocked by invalid or incomplete source material. These are correct safety outcomes, not regressions.

## Merge recommendation

The implementation, regression tests, deployed runtime, automatic ShareFile intake, plan worksheet packaging, FT Williams write, receipt, read-back, and PDF evidence gates all passed. The branch was approved for merge and subsequently completed the post-merge verification below.

## Post-merge production verification

- The branch was merged into `main` as commit `724abb1` and pushed to GitHub.
- The merged backend image is `sha256:6956c58f018e743dafa48c92e7f1639892bea02d22f17c538b8c8dede82b8719`.
- Production API revision `46` and ShareFile worker revision `36` both completed rollout with 1/1 tasks running.
- The merged frontend was uploaded and CloudFront invalidation `I3LL2BK764K74GKQ6BSJ8NBEOF` completed.
- Post-merge ShareFile upload `POSTMERGE-QA-20261001-R8-NFP-Provident-Accident-160168.pdf` was automatically discovered and processed as filing `6abe7e7a96eb8dc34f9f2ea9`.
- The package used the NFP PY25 plan worksheet and the uploaded Schedule A, selected existing Schedule A sequence `9`, confirmed nonexperience rating and complete broker matching, and finished automation with zero decisions, zero blockers, and zero pending updates.
- The no-op comparison result was 49 unchanged fields, 3 kept-current fields, and 3 safely skipped blank fields. No duplicate FT Williams write was sent because current values already matched the previously verified source.
- This run exposed and corrected a dashboard-only inconsistency: completed no-change automation is no longer counted in the Needs Review KPI or company summary, and its row now says that no FT Williams changes were needed because current values already match.
