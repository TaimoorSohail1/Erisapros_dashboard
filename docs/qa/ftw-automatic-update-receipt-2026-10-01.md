# FT Williams automatic update receipt QA — 2026-10-01

## Release decision

**Status: PR ready for review; do not merge yet.**

The implementation and local regression gates pass. The reversible FT Williams browser-field check also passes and was restored to its original value. A final live end-to-end run through the deployed ERISAPros application still requires a disposable FTWLink-visible test plan that is safe for both existing-Schedule-A and new-Schedule-A writes.

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

Target: the approved Brandeis 2025 browser test Schedule A, sequence 1. Only `x.OverrideCommissionsAndFees` was changed.

1. Initial read: unchecked.
2. Checked through the field's FT Williams AJAX update handler.
3. Reload read-back: checked (persistence verified).
4. Restored to unchecked through the same handler.
5. Second reload read-back: unchecked (original state restored).

No other FT Williams field was changed.

## Remaining merge gate

Before merging, run the deployed branch against one explicitly approved disposable FTWLink-visible test plan and capture:

1. Existing Schedule A automatic update, FT Williams read-back, receipt, browser verification, and PDF evidence.
2. New Schedule A automatic creation, assigned sequence, FT Williams read-back, receipt, browser verification, and PDF evidence.
3. Equal-score multi-match control, proving the system pauses for a human decision and sends nothing.
4. Final confirmation that the disposable test records may remain or are restored according to the test owner's instruction.

Production client plans must not be used to satisfy this gate.
