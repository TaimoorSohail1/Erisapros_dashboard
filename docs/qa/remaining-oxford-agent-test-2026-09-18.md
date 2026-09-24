# Remaining Oxford TEST / agent verification, 18 September 2026

## Scope and safety

Approved: correct Oxford TEST mapping, narrow policy-date fix, six Oxford inputs (five distinct contracts and one labelled exact duplicate), automatic intake/extraction/current queries/Bring Forward, pending and active Pause/Resume checks. Automatic sending remains explicitly disabled. EP-PF43NS4S is not changed. No manual Query/Retry/Bring Forward/Send or reviewer-decision override is used to make the automatic workflow pass.

Evidence directory: `tmp/oxford_six_20260918/`. Scoped deployment evidence: `tmp/policy_date_release_20260918/`. Earlier Titmouse result: `docs/qa/titmouse-oxford-live-flow-test-2026-09-18.md`.

## Completed preparation

- Reproduced the date defect before implementation: the rules-driven parser selected March 12, 2026 correspondence dates for both policy boundaries, ignoring `Contract Year from 01/2025 - 12/2025`. Five of six regression tests failed before the patch.
- Removed document-wide correspondence-date fallback. Explicit contract-year ranges take precedence; source-backed semantic reconciliation corrects an erroneous provider candidate. Month-only boundaries are derived, annotated and kept review-required; conflicting ranges require review. No claim that exact days are printed in the source.
- New six regression tests passed. Focused parser/customer-rule tests: 24 passed and 25 subtests. Full backend: 801 passed, 2 skipped, 64 subtests, one pre-existing GroundX SDK deprecation warning (103.33 seconds).
- Reran the original Titmouse Kaiser PDF through the parser/semantic/canonical pipeline: 01/01/2025 and 12/31/2025, page 3, review required, explicit original month/year evidence. This is a local original-document regression, not a rerun of live EyeLevel extraction and not a correction of existing FTW values.
- Production-source diff inspected for API and worker: only the four date-related modules overlay the existing production images. Agent, routing, review/send code and frontend are preserved.
- Oxford TEST IDs freshly confirmed through PlanIDs_Batch including PlanLine2 TEST and successful QueryPlan. Target customer 2402926798, plan 2950081973, EIN 87-4619719, PN 501. Worksheet plan name matches this target. The different accessible Oxford Solutions live plan is not selected.
- Corrected only stale mapping 6a91d890924380a93619f616 with compare-and-set, backup and audit. Previous invalid pair 1975777651/2401132428 replaced with the verified TEST API/browser pair. Mapping keyed to the actual TEST-plan name.
- Baseline: Oxford 2024 has five Schedule As; 2025 returns missing-record code 59.
- Six original ShareFile inputs downloaded. Five distinct SHA256 values. Sixth exactly duplicates UNUM 918412 and is explicitly named LABELLED-DUPLICATE. DNU and old QA/UAT files excluded.
- Independent source review: all four distinct PDF pages rendered and inspected, Excel read-only values checked. Detailed source checks retained in source-checks.json. EyeMed has multiple contract/enrollment rows and must not be falsely certified as a single-policy highest-count test. Delta is a scanned PDF, so text extraction alone is not a source check. Excel contains an unrelated cached Payments-header #VALUE!; source not modified.
- Desktop 0.4.1 paused using live dashboard. BrowserProfile process count zero while paused. Sixteen personal Chrome PIDs observed still running. Other device untouched.

## Safety finding to verify before resuming the full queue

Local-agent idempotency includes filing ID and target/year, not just target/year. Claim checks the candidate filing's saved current-year state. Therefore several filings for one missing plan/year can queue separate jobs with stale missing-year snapshots. A later browser action must not be allowed solely because that stale snapshot still says missing. The FTW page continues displaying a Bring Forward link even when current-year schedules exist. This is an identified code-path risk, not evidence that a duplicate live Bring Forward has already occurred.

The test will pause during the first genuine active claim, verify its result and preserved queued jobs, and inspect the remaining state before another native action. A new destructive cleanup or unchecked repeat Bring Forward is not authorized by this test.

## Live results

Completed production observations through approximately 14:20 UTC. This is **not an all-pass certification**.

### Passed

- Narrow date fix deployed successfully to production API task 27 and ShareFile-worker task 20. Running image digests and the four source hashes independently verified. Both rollouts healthy; `/api/health` 200 and unauthenticated filings access 401. Existing image layers and non-image service configuration preserved. Frontend unchanged; automatic sending false. Deployment evidence: `tmp/policy_date_release_20260918/verification.json`.
- All six approved uploads automatically discovered and packaged with the original worksheet, without a manual trigger or retry. All six extraction jobs completed with 59 stored fields each. Providers identify GroundX structured extraction, with X-Ray/local parsing where applicable. Discovery took 18.7–73.9 seconds after each upload; extraction execution took 114.7–218.1 seconds. These timings include normal processing differences, not a promised future SLA.
- Every filing resolved to the verified Oxford TEST API/browser ID pair, not the accessible live Oxford Solutions plan. All six extracted policy boundaries match the independently inspected sources: BCBSMA/Delta/EyeMed 01/01/2025–12/31/2025; both UNUM contracts and the duplicate 01/01/2025–01/01/2026. Report/correspondence dates did not replace these policy dates.
- Pending Pause: before Resume, real Bring Forward jobs stayed QUEUED with attempts 0, desktop PAUSED and browser closed. Resume renewed pending execution windows and reopened/restored the dedicated agent session without re-pairing or entering a new one-time code.
- Active Pause: job `6aad47b22b50f3176b053103` was genuinely CLAIMED at 14:17:29.127 UTC, observed CLAIMED at 14:17:29.728, and Pause persisted at **14:17:34.331**, before completion at **14:17:35.926**. The active operation finished safely; no second claim occurred. Live dashboard acknowledged PAUSED/browser closed afterward. Audit events independently confirm Resume and Pause request times.
- That single native Bring Forward operation was submitted once (attempts 1), then VERIFIED by the application. Browser-operation claim-to-submit: **6.8 seconds**. The job's filing refreshed to MATCHED/current year exists, successful complete current query, with final observed update at 14:17:52.572. Native Bring Forward copied all five prior-year Schedule As into 2025; it does not run once per uploaded document.
- Independent read-back: Oxford 2024 still has five records and **all returned QueryResults are unchanged** against the saved baseline. Oxford 2025 changed from missing-record code 59 to success code 0 with five records. No source-period record was edited and no selected-field/automatic send update was attempted (all six review update counters 0).
- Isolation observation: after Pause, agent BrowserProfile process count 0; personal Chrome still running (14 processes, original browser PID 14624 survives). The separate in-app FTW tab remained signed in on its original Titmouse 2025 plan and unchanged URL, not redirected to Oxford/home. EP-PF43NS4S device/version/last-seen remained unchanged. This limited observation does not certify every client's vendor simultaneous-login behavior or every personal tab.

### Failed / requires a new fix

**Cross-filing Bring Forward state does not reconcile automatically.** After the first verified copy, five sibling filings still say SCHEDULE_A_MISSING/current year false and each retains a QUEUED Bring Forward job, attempts 0, targeting the same Oxford TEST/year. Two jobs were even created after the first browser operation completed. An independent fresh FTW read-back already proves the five current-year records exist. Therefore queue safety cannot be certified as correct: claim eligibility uses those stale filing reviews, and filing-scoped idempotency does not prevent another native Bring Forward for the same target/year. No repeat operation was deliberately allowed to demonstrate corruption.

The completed job remained VERIFIED/attempts 1; the five pending jobs remained untouched. **Desktop is intentionally left PAUSED. Do not Resume this queue until target-level deduplication and fresh pre-action state reconciliation are fixed and tested.** The user needs to approve this additional implementation/deployment scope; this testing request did not authorize unchecked duplicate actions, deletion of jobs or FTW cleanup.

### Extraction findings — not a 100%-accuracy pass

- Independently checked carrier EIN, NAIC, contract and policy dates agree for the unambiguous inputs. Lives: BCBSMA 304, Delta 331, UNUM 144/047 (47), duplicate 144. Low-confidence/review flags are retained and not overridden to pass automation.
- **UNUM fee discrepancy:** source PDFs page 1 print Fees Paid `.00`; extracted Part I 3c is `1` for both contracts and the duplicate, marked LOW_CONFIDENCE at 50%. Actual extraction accuracy for this field fails even though the safe review behavior is correct. Premiums and sales commissions match the unambiguous printed values. Supplemental additional compensation needs its own semantic review; it is not silently certified as sales commission.
- EyeMed combines four contract rows and extracts 339 lives (335 + 4), marked LOW_CONFIDENCE/review. This is not proof that the requested per-column/per-policy highest-count rule chose 335 correctly. Policy/COBRA grouping needs confirmation before declaring the value correct. No decision was imposed during testing.
- BCBSMA is EXPERIENCE_RATED: premiums belong in Part III 9a, not nonexperience-rated 10a. Source premium 1,630,230.90 is stored as 1,630,230 with LOW_CONFIDENCE/review; precision/rounding is not certified. Retention commission is 35,175; an aggregate workbook compensation figure must not be mistaken for a Part I broker commission. Part I broker organizational-code scalar remains blank because no broker recipient row was extracted; an unconditional-every-blank default is not established by this input. Other five inputs show organizational code 3. No broker recipient was fabricated.
- Evidence: independently reviewed originals `tmp/oxford_six_20260918/Oxford-{1..6}` (xlsx/PDF), rendered PDF page-1 images, `source-checks.json`, and field-level `source-comparison.json`. Source files were not edited. The comparison only covers enumerated independently reviewed fields, not every extracted field.

### Deferred / not tested

- Full pending-queue recovery through another Resume without duplicate native operations: blocked by the confirmed stale-sibling state above. Pending retention and renewal passed; full recovery did not.
- Real automatic sending and production enablement: explicitly deferred, still OFF. No Send button, manual field-update submission, filing sign/submit or new enablement was used.
- Fresh live rerun of the original Titmouse month-only policy document after deployment: not performed. Its local original-PDF regression passed; Oxford live date extraction passed. Existing wrong historical FTW values were not changed by this release.
- New client installer/fresh pairing/connection-preserving installer migration was not repeated in this Oxford test. The desktop already reports 0.4.1; this test covers that running agent's Resume/Pause and Bring Forward behavior.

### Handoff

Live date release and corrected TEST mapping are complete. Six QA inputs/filings are retained, one TEST-plan native copy is verified, prior-year data preserved, five stale queued jobs retained, desktop paused, other device untouched, automatic sending off. Next safe action: approve a narrowly scoped target/year Bring Forward deduplication and fresh-state reconciliation fix, plus the independently identified extraction fixes; regression-test locally, deploy narrowly, then re-test pending recovery without forcing manual success.
