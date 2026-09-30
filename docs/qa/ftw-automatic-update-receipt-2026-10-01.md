# FT Williams automatic update receipt QA — 2026-10-01

## Release decision

**Status: GO for merge; keep production automatic sending disabled until the post-deployment demo canary passes.**

The implementation, complete regression suite, reversible browser check, and live ftwLink canaries pass. The live run used only the approved HighlandTech FGF test plan. It verified an existing Schedule A update and a temporary new Schedule A while preserving all original records and broker rows. The temporary value and test record were removed after verification.

Merging this branch does not activate unattended production sending. Deployment and activation remain separate release decisions: deploy with the existing safety flags, run the approved demo receipt/PDF smoke test, and only then consider enabling the allowlisted automatic-send flag.

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

- Backend full suite: **874 passed, 2 skipped, 1 GroundX SDK deprecation warning**.
- FT Williams review suite: **119 passed**.
- Frontend TypeScript check: passed.
- Frontend review, dashboard, field-rules, ShareFile, agent-control, and shared-polling suites: passed.
- Production frontend build: passed.
- Production-bundle smoke render: passed.
- Read-only visual harness: verified all five stages complete, no repeat-send button, action-specific receipt, destination metadata, counts, evidence actions, and expanded sent-versus-returned table.

## Live FT Williams reversible evidence

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
| Full backend suite | 874 passed, 2 skipped, 64 subtests passed |
| Frontend release checks | Typecheck, build, bundle smoke, and all UI suites passed |
| GitHub production-image check | Passed on PR #55 |

The branch is safe to merge with the feature controls unchanged. Before enabling unattended production sending:

1. Deploy the merged revision with automatic sending still disabled.
2. Run one approved HighlandTech demo filing through the deployed UI and verify its receipt, FT Williams link, and PDF evidence.
3. Confirm the exact demo allowlist and feature-flag values before enabling automatic sending.
4. Monitor the first allowlisted automatic run and retain the manual fallback.

Production client plans must not be used to satisfy this gate.
