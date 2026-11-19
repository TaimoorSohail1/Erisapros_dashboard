# Optional broker addresses: global delivery plan

## Required behavior

| Address | Workflow |
| --- | --- |
| All address components blank | Keep blank; no address review or separate approval. |
| Street, city, state and ZIP present | Continue automatically; address line 2 is optional. |
| Some address components present | Show a review. The user may complete the address or choose **Save and send as-is**. |
| Organization code blank | Default to 3. Preserve explicit valid codes. |

Apply this policy to every broker and carrier through shared services. Automatic sending still requires valid payments, recipient identity, complete source roster, an editable matched destination, and existing filing checks.

## Implementation

1. Centralize blank/complete/partial address classification and partial-address acknowledgement. Use identical checks in extraction, review readiness and payload generation.
2. Stop requiring address fields in the broker editor and confirmation service. Keep supplied components; never invent missing data or copy a prior address into an intentional blank.
3. Record an explicit **Save and send as-is** acknowledgement against the exact reviewed roster, authenticated reviewer, source and fresh destination. Changes invalidate the acknowledgement. Preserve unrelated source errors.
4. Save the reviewed broker list directly from the editor and resume the existing automatic workflow. Show automatic readiness for verified rosters instead of asking for another approval.
5. Preserve org code 3 defaults, explicit valid codes, payments, sibling schedules, and existing read-back validation.

## Verification and release

- Test blank, complete, partial, completed-after-review, and partial-as-is cases through the shared service and XML builder.
- Reject stale confirmation, forged acknowledgement and unrelated payment/identity errors.
- Test the actual editor behavior, optional HTML inputs, persistence failures, and automatic-ready state.
- Run backend regressions, frontend UI checks and production build.
- Review and merge the exact tested PR. Roll API and worker images, publish the tested dashboard assets, and check health and delivered hashes.
- Verify existing stored filings without reuploading. Use authorized disposable FT Williams test records for wire/read-back checks; retain per-case evidence and report any vendor restriction accurately.

## Local validation

- Backend: 2,039 passed, 2 skipped.
- Frontend review and dashboard regression checks, production build, and production-bundle smoke check passed.
- The live-test dry run verified the blank, complete, partial-as-is and unacknowledged-partial paths without vendor writes. Production read-back and release evidence are recorded separately after deployment.
