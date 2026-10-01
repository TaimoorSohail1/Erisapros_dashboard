# FT Williams automatic update receipt QA — 2026-10-01

## Release decision

**Status: PASS. The clean NFP 2025 production canary completed extraction, automatic FT Williams submission, receipt creation, and FT Williams read-back with zero conflicts. A fresh FT Williams Schedule A PDF was also generated and verified.**

The implementation, complete regression suite, reversible browser check, and live ftwLink canaries pass. The live run used only the approved HighlandTech FGF test plan. It verified an existing Schedule A update and a temporary new Schedule A while preserving all original records and broker rows. The temporary value and test record were removed after verification. PR #55 was merged to `main` at `975d07131486dba0e96b7096f6f87784ea376b9b` and that revision was deployed successfully.

The user explicitly authorized enabling automatic sending before the final post-deployment demo. Both production services run with `FTW_AUTOMATION_AUTO_SEND_ENABLED=true`. The allowlist retains the HighlandTech 2025 test scope and now includes the exact NFP 2025 FT Williams customer/plan target used for this canary. The earlier FGF filing continues to prove fail-closed behavior for conflicts; the NFP canary closes the successful receipt/PDF/read-back gate. The NFP broker-validation correction is commit `4c8c9fa` on branch `codex/nfp-broker-auto-send-fix`; it is deployed for validation but is not yet merged.

## Implemented outcome

- Persist a receipt only after FT Williams read-back verifies every attempted field.
- Record whether the operation updated Form 5500, updated an existing Schedule A, or created a new Schedule A.
- Record the verified year, Schedule A sequence, carrier, EIN, contract, plan identity, verification time, and FT Williams browser link.
- Preserve the same receipt after a current-data refresh.
- Show all five workflow stages as complete after verified read-back.
- Replace the send action with `FT Williams verified` after completion, preventing an accidental repeat send.
- Show updated, kept-current, skipped, and conflict counts.
- Provide an expandable field receipt with `Sent`, `FT Williams returned`, and `Status` columns.
- Provide `Open in FT Williams` and `View verified PDF` evidence actions when available.

## Client-feedback coverage

| Feedback | Automated behavior | Evidence |
|---|---|---|
| Extraction blank | Keep current FT Williams value | `test_comparison_preserves_current_ftw_value_when_extraction_is_blank` |
| FT Williams blank | Use valid extracted value | `test_comparison_marks_valid_extracted_value_for_automatic_update_when_ftw_is_blank` |
| Both blank | Skip field without creating a decision | `test_review_summary_counts_only_conflicts_as_decisions` and comparison tests |
| Both nonblank and different | Stop only that conflict for a decision | `test_different_nonblank_extracted_and_ftw_values_require_a_decision` |
| No commissions or fees | Derive and send `OverrideCommissionsAndFees=1` from the final replacement payload | `test_schedule_a_checks_no_commissions_or_fees_when_final_amounts_are_empty` |
| A payment exists | Clear the no-payment indicator | `test_schedule_a_clears_no_commissions_or_fees_when_a_payment_exists` |
| Zero-value brokers | Exclude automatically | `test_existing_broker_with_explicit_zero_commissions_and_fees_is_excluded` |
| Unlabelled positive broker payment | Classify as Schedule A line 3c fee | `test_unlabelled_positive_broker_payment_is_classified_as_a_fee` |
| Broker match/new row | Match unique existing identity; add unmatched broker as new | `test_broker_with_no_candidate_is_automatically_added_as_new` plus broker matching suite |
| Text updates | Normalize outbound alphabetic text to uppercase | `test_new_outbound_text_updates_are_uppercase` |
| Policy period | Clamp a period longer than 12 months | `test_contract_period_longer_than_twelve_months_is_clamped` |
| Latest worksheet | Use the latest eligible worksheet sibling | `test_changed_schedule_a_selects_latest_unchanged_worksheet_sibling` |
| New Schedule A | Create only when identity is not an existing record; preserve existing records | new-Schedule-A and replace-safety tests |
| Prior-year static rules | Retire the specified prior-year arrangement fields from published extraction rules | `test_prior_year_form_5500_arrangement_fields_are_retired_from_published_rules` |
| Character limits | Split safe broker addresses; reject values that cannot be shortened without data loss | address and length tests in `test_xml_builder.py` |
| Verified completion evidence | Persist and render the FT Williams update receipt | new receipt backend tests and `test-ftw-update-receipt.mjs` |

## Automated test evidence

- Backend full suite after the NFP broker correction: **877 passed, 2 skipped, 1 GroundX SDK deprecation warning, 64 subtests passed**.
- FT Williams review suite: **119 passed**.
- Frontend TypeScript check: passed.
- Frontend review, dashboard, field-rules, ShareFile, agent-control, and shared-polling suites: passed.
- Production frontend build: passed.
- Production-bundle smoke render: passed.
- Read-only visual harness: verified all five stages complete, no repeat-send button, action-specific receipt, destination metadata, counts, evidence actions, and expanded sent-versus-returned table.

## Production deployment and activation evidence

### Current NFP canary deployment

- Backend build: CodeBuild `erisapros-production-backend:e61e3fea-1a29-43ce-92ad-ca9016786ccf` succeeded from `codex/nfp-broker-auto-send-fix`.
- Immutable backend image: `sha256:642b4361ccd7f0828707aedc2fe9142d05c4ffcd430d5b38a4f711316f944e09`.
- API task definition: `erisapros-production-api:39`; worker task definition: `erisapros-production-sharefile-worker:29`.
- Both services stabilized at desired `1`, running `1`, pending `0`, with rollout state `COMPLETED`.
- Both services run the immutable image with `FTW_AUTOMATION_AUTO_SEND_ENABLED=true` and `FTW_PDF_AUDIT_ENABLED=true`.
- The exact NFP FT Williams target is allowlisted for 2025; unrelated customer/plan targets remain blocked.

### Original production release

- Backend build: CodeBuild `erisapros-production-backend:31abd977-fbb2-47d1-99c4-d136e0291f96` succeeded.
- Immutable backend image: `sha256:c6c15813e0115b7e58677817a623e537e8b24913a98fa0dee03ef44440785987`.
- API task definition: `erisapros-production-api:36`; worker task definition: `erisapros-production-sharefile-worker:26`.
- Both services stabilized at desired `1`, running `1`, pending `0`, with rollout state `COMPLETED`.
- Both task definitions use the immutable image and have automatic sending enabled with the HighlandTech 2025 allowlist unchanged.
- Production frontend build and bundle smoke render passed; live `index.html` version is `sl2DTmX5K6lNzD73VLBaDXCWZukqNOtn`.
- CloudFront invalidation `I2K50PHI24PIBQDQXXLRUEHO2N` completed, and the live HTML references the new JavaScript and CSS assets.
- `https://d3axcdlq9aydpw.cloudfront.net/api/health` returned HTTP 200 with `status: ok`.
- API and worker error-pattern log scans after deployment returned zero matches.
- The ShareFile worker queue was empty: zero waiting and zero in progress.
- Rollback anchors: API task definition `35`, worker task definition `25`, and frontend `index.html` version `Esch8O7wzMp4h98zumcT9xZcztHVpjjT`.

## Live FT Williams reversible evidence

### Clean NFP production canary — automatic receipt, PDF, and read-back

Target: NFP TEST 2025 ShareFile folder and the exact NFP FT Williams 2025 plan target.

- ShareFile upload: `CANARY-20261001-NFP-Continental-American-0000024819.pdf`.
- Source PDF SHA-256: `388D11CA4598624AF32A56D24B176F29E5588DA0A75A43E5411E1C470953FE91`.
- Plan worksheet SHA-256: `3D064EAEA9C29B0AB6B034C14EEAC150B6EEE56550E3B7DD52834ECABDAF95AD`.
- Production filing: `6abe3757340793f1e1ea8a81`.
- Extraction found 33 of 37 configured fields and selected existing Schedule A sequence 11 with exact contract, carrier EIN, NAIC, carrier name, and policy-date identity evidence.
- The structured broker row matched FT Williams row 1 by unique exact address. Broker name, address semantics, source evidence, commission/fee column semantics, and organization-code validation all passed; the row decision was `AUTOMATIC`.
- Automatic submission attempted and FT Williams read-back confirmed all three proposed changes:
  1. Schedule A line 10a premiums: sent `943,913.44`; returned `943,913.44`.
  2. Form 5500 active participants at end: sent `6,159`; returned `6,159`.
  3. Schedule A line 1e persons covered: sent `1,777`; returned `1,777`.
- Final database state: filing `APPROVED`, automation `COMPLETED`, review `UPDATE_SENT`, attempted `3`, confirmed `3`, remaining `0`, verification attempted `true`, verification success `true`, active failure `false`.
- A durable update receipt was persisted for plan year 2025, existing Schedule A sequence 11, carrier `CONTINENTAL AMERICAN INSURANCE COMPANY`, and contract `0000024819`.
- FT Williams generated a valid four-page Schedule A PDF after the write. SHA-256: `016a27286f8b5f5e9b9b5f518e60b80d1d9ec7aecd29c3995e41949724faaea2`. Visual and text inspection confirmed the carrier identity, contract, `1,777` covered persons, broker/address, `212,582` rounded commissions, zero fees, organization code `3`, and `943,913` rounded line 10a premiums.
- Result: `Completed — FT Williams update verified`; UI counts were updated `3`, kept current `1`, skipped `3`, conflicts `0`.

Ignored local QA evidence:

- `output/qa/nfp-ftw-schedule-a-seq11-verified.pdf`
- `output/qa/nfp-ftw-schedule-a-seq11-page1.png`
- `output/qa/nfp-ftw-schedule-a-seq11-page4.png`

### Monitored deployed ShareFile canary

Target: approved HighlandTech `FGF LLC TEST` 2025 ShareFile folder.

- Uploaded source: `CANARY-20261001-FGF-Schedule-A.pdf`
- SHA-256: `6550FF8A999611B2BD4CF1EC4B809C8BFDAF13A0F4E43A4AE7B230E08F652852`
- Production filing: `6abd92e2340793f1e1ea8919`
- Intake and extraction completed; 36 of 37 fields were found.
- FT Williams loaded successfully and selected the best matching Schedule A.
- Nine non-conflicting fields were classified as `Will Update FTW`.
- Automatic sending stopped safely on four review items:
  1. Insurance company conflict: extracted `Continental American Insurance Company`; current FT Williams `AFLAC`.
  2. Plan sponsor address conflict: extracted `122 Stribling SAN ANTONIO, TX 78204`; current FT Williams `146 EAST ZAVALLA, SAN ANTONIO TX 78204`.
  3. Plan sponsor name conflict: extracted `FGF, LLC EMPLOYEE BENEFITS PLAN`; current FT Williams `FGF,LLC`.
  4. Broker fee validation: `RICHARD LEE JONES JR` extracted fee `2228.48405`, which exceeds FT Williams' two-decimal precision.
- Result: `Action Needed — Automation paused safely`. No value was forced, no FT Williams update was sent, and therefore no receipt, verified PDF, or FT Williams read-back was produced.

This is positive evidence that the deployed workflow does not silently overwrite conflicting values or send an invalid amount. It is not evidence of a successful fully automatic send.

### Automatic ftwLink existing-record canary

Target: approved HighlandTech FGF test plan, filing year 2025. The baseline contained seven Schedule A records.

1. Exact plan name, sponsor EIN, plan number, year, and demo account identity matched.
2. All 20 FT Williams Schedule A slots were queried before the update; seven records were present.
3. The replacement preflight contained seven records and reported zero preservation gaps.
4. One controlled numeric field was increased by one and accepted by FT Williams.
5. Read-back confirmed the controlled value and retained all seven Schedule A identities and broker rows.
6. The numeric value was restored to its original value.
7. The only persistent differences from the pre-release baseline were the intended `OverrideCommissionsAndFees` values: the paid record is unchecked and the six records with no commissions or fees are checked. No other field or broker value changed.

The first strict comparison intentionally stopped before the new-record test because those newly derived indicators differed from the historical blank values. Read-only diagnosis proved that they were the only differences and that each value matched the approved no-payment rule.

### Automatic ftwLink new-Schedule-A canary

1. The normalized seven-record baseline passed identity, record-count, broker, and no-payment-indicator checks.
2. The new-record payload contained the seven preserved records plus one uniquely identified `CODEXQA` Schedule A, with zero preservation gaps.
3. FT Williams accepted the payload and read-back returned exactly eight records.
4. Exactly one record matched the test contract, its `No commissions or fees paid` value was checked, and the seven original records were unchanged.
5. The temporary test record was removed by restoring the complete seven-record normalized baseline.
6. Final read-back returned exactly seven records and matched that baseline exactly.

Machine-readable local evidence is stored in ignored QA artifacts:

- `output/qa/ftw-fgf-automatic-decision-live-e2e-2026-10-01.json`
- `output/qa/ftw-fgf-new-schedule-live-e2e-2026-10-01.json`
- `output/qa/ftw-fgf-automatic-decision-baseline-2026-10-01.json`
- `output/qa/ftw-fgf-normalized-baseline-2026-10-01.json`

### Browser-field canary

Target: the approved Brandeis 2025 browser test Schedule A, sequence 1. Only `x.OverrideCommissionsAndFees` was changed.

1. Initial read: unchecked.
2. Checked through the field's FT Williams AJAX update handler.
3. Reload read-back: checked (persistence verified).
4. Restored to unchecked through the same handler.
5. Second reload read-back: unchecked (original state restored).

No other FT Williams field was changed during the browser-field canary.

## Merge and activation gates

| Gate | Result |
|---|---|
| Existing Schedule A update and read-back | Passed live against the HighlandTech FGF test plan |
| Original Schedule A and broker preservation | Passed; seven original records retained |
| New Schedule A creation and assigned record | Passed live; exactly one temporary test record appeared |
| No-commissions-or-fees derivation | Passed live on existing and new records |
| Temporary-record cleanup | Passed; exact normalized seven-record baseline restored |
| Equal-score multi-match control | Passed in automated coverage; pauses for a decision and sends nothing |
| Verified receipt UI | Passed backend receipt tests and frontend rendered-contract checks |
| Full backend suite | 877 passed, 2 skipped, 64 subtests passed |
| Frontend release checks | Typecheck, build, bundle smoke, and all UI suites passed |
| GitHub production-image check | Passed on PR #55 |
| Production backend rollout | Passed; API revision 39 and worker revision 29 completed on immutable image `642b4361...` |
| Production frontend rollout | Passed; new assets are live and CloudFront invalidation completed |
| Production health and logs | Passed; health returned 200 and post-deployment error scans were clear |
| Runtime automatic-send flag | Enabled on both services; HighlandTech 2025 scope retained and exact NFP 2025 canary target added |
| Runtime PDF-evidence flag | Enabled on both services; fresh NFP FT Williams PDF generated and verified |
| First deployed filing intake/extraction/match/safety gate | Passed on production filing `6abd92e2340793f1e1ea8919` |
| Clean deployed filing receipt/PDF/read-back | Passed on NFP filing `6abe3757340793f1e1ea8a81`: receipt persisted, PDF verified, 3/3 values confirmed by read-back |

The NFP clean-canary gate is closed. The branch is ready for normal code review and merge; the earlier FGF conflicts remain intentionally unresolved and continue to demonstrate fail-closed behavior rather than a release blocker.
