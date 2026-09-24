# Schedule A customer rules and automatic sending — delivery plan

Date: 2026-09-18. Status: customer-rule changes deployed and verified following approval. Automatic-send testing and activation explicitly deferred; automatic sending remains disabled on both services.

## Goal and baseline

Default blank organizational codes to 3, choose the highest valid lives-covered value from the relevant column, and activate guarded automatic sending after verified acceptance. Preserve manual selected-field sending, extraction-provider integration, plan matching, Bring Forward, agent controls, and existing client records.

At the planning baseline, code defaulted missing codes only when explicit broker evidence existed and did not implement the requested general column-maximum rule. Automatic-send evaluation/execution existed with FTW_AUTOMATION_AUTO_SEND_ENABLED=false. Current deployment/activation evidence is recorded below rather than treating that initial snapshot as permanent.

“100%” means complete and eligible under the agreed automation checks, not merely dashboard coverage or a claim that model confidence is perfect. Document the actual configured confidence threshold and eligibility reasons. Do not silently replace that policy with a literal 1.0 confidence requirement.

## Slice 1 — blank organizational code

- Introduce one consistent normalization rule for recognized Schedule A recipient/broker rows: None, empty string, or whitespace code becomes "3".
- Apply it through extraction, persisted review data, broker editing, and payload generation so the UI and FTW payload agree. Never create a recipient row merely because a code is blank.
- Preserve nonblank codes, including 0, 03, and valid non-broker codes. Existing canonical zero-padding normalization may continue. Invalid nonblank codes remain visible errors, not silently changed to 3.
- Explain derived values as “Defaulted to 3 because organizational code was blank”; do not fabricate source evidence or confidence.
- Handle existing filings through a controlled normalization path; assess whether cached comparisons/payloads need regeneration. Do not bulk re-extract or send existing records automatically.

Acceptance: all blank recipient codes consistently become 3; explicit codes remain unchanged; excluded rows stay excluded; other validation remains intact.

## Slice 2 — highest lives covered

- Use the supplied terms: Approximate # of Lives Covered, Persons Covered, Group Covered, Lives, Employee Count, Subscribers/Members, plus existing supported spelling/spacing variants.
- Locate the relevant column/label and collect numeric counts only within its Schedule A contract/coverage context. Choose the highest valid count; do not sum counts.
- Exclude currency, premiums, commissions, dates, EINs, NAICs, policy IDs, and page/header numbers. Use column position/context, not the largest number anywhere in the document.
- Keep distinct contracts/clients separate. Do not globally replace every coverage's count with another coverage's maximum. Review genuinely ambiguous grouping rather than guessing.
- Retain candidate values, page/column evidence, selected value, and reason. Equal maxima produce the same count without duplicate updates. Missing or unreadable counts stay missing/reviewable, never invented.
- Start with the supplied Hartford_BRC PDF: inspect its source and expected maximum, then compare local extraction and Eyelevel-based output. Do not upload the PDF to a new destination merely to inspect it.

Acceptance: extracted count matches the relevant column maximum in the source; nearby dollars do not affect it; mixed-contract and OCR ambiguity cases are safely handled.

## Slice 3 — guarded automatic sending

- Audit existing eligibility and execution paths, including shared manual-send helpers, to keep the already removed dashboard approval step removed.
- Require complete required data, acceptable configured confidence/source or derived-rule provenance, no unresolved review/validation decisions, verified unique plan/year/account and Schedule A matching, editability, generated valid payload, and allowed update permissions/routing.
- Inspect both API and worker settings, workspace/demo allowlists, and Schedule A replacement restrictions. A single global toggle is not sufficient proof that every client is enabled.
- Preserve unselected values, unmatched existing broker rows, and sibling Schedule A records. Existing matching data is a no-op, not a duplicate send.
- Verify retry/concurrency protection: repeated uploads/events or several PDFs for one plan must not duplicate sends or overwrite each other's results. After uncertain vendor outcomes, read back/reconcile before retry; never blindly resend.
- Display queued/sending/verified success or exact review/failure reason. Mark success only after FTW read-back matches the intended changes, not just an accepted request.

Acceptance: eligible authorized test filing sends once without a manual click; ineligible filing waits with a clear reason; manual selected-field sending still works independently.

## Tests, review and release gates

1. Add failing regression tests for each slice, implement minimally, and rerun extraction, broker-validation, review/XML, automation, repository/concurrency, and frontend suites.
2. Source-check Hartford and additional representative layouts. Test blank/whitespace/nonblank codes; competing counts/currency; ties, zero, missing/OCR counts; multi-contract PDFs; exclusion; multiple filings per plan; login/MFA waits; timeout and partial/unknown vendor results; no-op and repeated events.
3. Review code and local UI on desktop/tablet/mobile. Show derived-value reasons without large banners obscuring fields. No unrelated UI redesign or agent installation changes.
4. Obtain human review and explicit approval of a named test plan/workspace, the data to send, FTW destination, and snapshot/restore procedure before a real vendor-write canary.
5. Run controlled automatic canary with manual-send buttons unused. Record upload, extraction, queue, agent/Bring Forward where needed, send and read-back timings. Bring Forward and sending are separate capabilities; do not count review pauses as automatic-send success.
6. Deploy compatible API/worker and dashboard through the existing AWS process with rollback references. Initially keep auto-send off; activate only the approved canary scope after permission/config checks. Expand client scope only after canary verification and approval. Do not automatically send historical uploads on activation without an explicit replay decision.
7. If acceptance fails, disable auto-send, retain queue/audit and unknown-outcome holds, fix and retest, then redeploy or restore prior application/index versions. Reconcile vendor data before any retry or restore.
8. Deliver a final report separating implemented, tested, deployed, enabled scopes, expected review pauses, failures, and remaining limits. Include source-versus-extraction comparisons, timings, read-back evidence, and rollback references.

## Decisions needed before live activation

Identify an authorized FTW test plan/workspace and approve its exact write/restore scope. Confirm production clients to enable and whether any historical pending filings may be replayed. Resolve genuinely ambiguous column/contract examples with ERISAPros. These do not block local implementation; they do block unspecific production auto-send activation.

## Implementation update — 2026-09-18

- Blank-code normalization now covers proposed recipient rows during extraction, review/editing and XML generation, preserving explicit codes and current FTW snapshots. UI explains the default. Deterministic customer-rule provenance is validated without inventing an OCR page.
- Covered-lives column selection now uses the supplied labels, retains source candidates, and selects the maximum for an unambiguous contract/table. Continued tables merge only for the same explicit policy identity. Mixed contracts and unreadable rows retain a review hold.
- Hartford source comparison selected 208 on page 2. The initial HTTP 402 came from an old local account/key using bucket 28208, not the production account. Production API and worker use bucket 32930 and match the customer's screenshot key; its displayed ingestion usage was 36%. The two ignored local settings were corrected and independently verified against both services. The corrected-account provider pipeline succeeded in 127.09 seconds with structured extraction, X-Ray and local parsing. It preserved explicit organizational code 3; the new source-column rule recovered the missing lives field as 208 at confidence 0.92. This is combined-pipeline evidence, not a claim that Eyelevel alone extracted that count. No filing was created or FTW data sent. Provider-failure review holds remain enforced.
- Automatic-send and selected-send regression paths passed locally, including guarded read-back and concurrency protections. The automatic sender itself was not rewritten or activated.
- Final frozen-code backend suite: 754 passed, 2 skipped, 64 subtests passed. All frontend regression checks, production build/render smoke, and desktop/tablet/mobile customer-default preview checks passed.
- Configuration-correction retest: 85 customer-rule, semantic-layer and automation tests passed, with 25 subtests, in 1.58 seconds. Prior full-suite and frontend figures were not rerun for this private settings correction.
- Read-only AWS inspection confirmed API task 25 and worker task 18 still have FTW_AUTOMATION_AUTO_SEND_ENABLED=false. Neither service nor the dashboard was deployed by this change.
- Hartford's corrected-account Eyelevel-pipeline comparison is complete. Remaining release gates: human review and additional representative-layout acceptance; approve exact FTW canary write/restore scope; controlled automatic canary and read-back; compatible cloud release and explicitly scoped activation. No production quota restoration is required for this successful retest. No historical replay is approved.
- Detailed evidence: [local implementation report](C:/Users/Hp/Erisapros_dashboard/docs/qa/schedule-a-customer-rules-local-qa-2026-09-18.md).

## Approved scoped release - completed 2026-09-18

The user explicitly deferred real automatic-send testing/activation and approved deploying only the customer rules and default-code UI hint. Fresh backend verification passed 754 tests, 2 skipped, 64 subtests; all frontend checks/build/render smoke passed. Both scoped immutable images passed 14 isolated customer-rule tests each. API 26, worker 19 and the dashboard are live; deployed hashes/configuration, service health, CDN assets and signed-in editor/selected-field confirmation checks passed. Each service retains its prior sending/agent implementation, and automatic send remains false on both. No client changes were saved or FTW send performed by the live browser checks. Historical extraction/backfill/replay was not initiated.

Real automatic-send canary/read-back and activation gates above remain deferred, not prerequisites falsely claimed complete by this customer-rule release. [Production report and rollback references](C:/Users/Hp/Erisapros_dashboard/docs/qa/schedule-a-customer-rules-production-release-2026-09-18.md).
