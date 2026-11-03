# V1 feedback verification and release review

Scope: 15 active Feedback/Bug entries, the reported broker match/correction problem, and regression checks for 10 resolved entries. V2 features are excluded. A spreadsheet status is not evidence of a current deployed defect.

## Changes

- Verified worksheet beginning-participant counts take precedence over carried-forward counts. Missing/unreadable values remain protected.
- Descriptive names, city and payment-purpose text follow verified destination limits at serialization. Full source values remain available. Identifiers and structured addresses retain validation; addresses must be split without losing information.
- The exact client-approved VSP policy/contract-year combined-payment label is commission-only. Other combined labels retain review.
- Broker matching rejects contradictory known locations and unrelated names sharing an address. Explicit selections reserve their destinations before automatic matching.
- Reviewers can change an existing match and see destination identity details. Invalid/stale row indexes are rejected before persistence.
- Explicit recipient identity corrections reach the selected row, including deliberate clears. Saved-value comparison retains those clear intentions.
- Positional broker updates occur before duplicate removal. Purpose-only changes are recognized as real updates.
- Count/premium-only sends preserve payment controls when compensation is excluded.
- Complete source rosters may remove absent and zero-payment recipients only when provider and independent document reads agree and both payment totals reconcile. The attestation is tied to source bytes and the exact roster; subsequent edits invalidate it. Incomplete sources retain existing recipients.

No published-rule, routing, provider credential, automatic-update control, schema or production filing changes are part of this code diff.

## Feedback trace

| Feedback | Required result | Verification or remaining boundary |
|---|---|---|
| 1 | Ignore only comma/period name differences; retain real-word review | Existing Aetna requirement tests; full names compared before shortening |
| 2 | Changed EIN requires review | Existing identity/review regressions; no new EIN override |
| 3 | Fit supported text limits | Serialization regressions; unsafe/unsplittable structured addresses remain review cases |
| 5 | Use verified worksheet beginning counts | New count-precedence regression; unreadable-count tests retained |
| 6 | Correct signed payment categories and amount-based purpose | Component and Hartford/Lincoln tests plus purpose-only dispatch regression |
| 7 | Broker payments must reach and be read back from the correct row | Duplicate-index, explicit selection and literal read-back regressions; live write proof is separate |
| 8 | Add current recipients; remove absent/unpaid recipients safely | Source-attested replacement tests; incomplete/uncorroborated rosters cannot authorize deletion |
| 9 | Preserve prior Schedule A attachment count | Outgoing mapping does not calculate or write the numeric attachment count from uploads |
| 10 | New Schedule A inherits correct parent headers | Existing Aetna new-record serialization tests; no sibling inheritance |
| 11 | Decide source-versus-worksheet policy-number precedence | Existing review remains; broader automatic precedence still needs client confirmation |
| 15 | Approved VSP combined label is commissions | New exact-label regression plus unapproved-label negative tests |
| 16 | Extract VSP total-payment premium alias | Labelled premium tests, including a changed amount |
| 26 | Distinguish completed from outstanding review | Dashboard and verified-outcome UI suites |
| 27 | Classification must not discard recipients/payments | Classification/source/recipient regressions and recorded-document replay; the historical failing artifact is not identified in the log |
| 28 | Investigate frequent FTWilliam logout | Existing browser-isolation/readiness tests; no evidence yet proving the reported vendor-session expiry cause |

Resolved Feedback/Bug entries 4, 30, 35–42 remain regression requirements. UX item 12 and date requirement 17 remain constraints, not additional Feedback/Bug counts. Conflicting policy periods continue to require review.

## Release gates and evidence

The private run directory contains red/green logs, source hashes, platform results, image identity and recorded-provider replay evidence. Client PDFs, worksheets, account IDs, credentials and private evidence must not be committed to the public repository.

Before release:

1. Verify the exact committed backend image on Linux and all recorded original/changed layouts. Distinguish replay from fresh provider requests.
2. Review roster deletion boundaries, positional matching, explicit identity edits/clears and source-hold preservation.
3. Record human approval on the exact tested head and complete UI QA.
4. Record current API/worker/frontend rollback artifacts and compare settings before/after deployment.
5. Deploy the verified artifact only, protect queued work and run health/auth checks. Do not resume paused devices or execute real-client filing writes.

This document does not assert that all feedback is closed or that the release is live. The final private report must distinguish implemented/tested behaviour, remaining client/vendor questions, unproven historical cases and actual deployment status.
