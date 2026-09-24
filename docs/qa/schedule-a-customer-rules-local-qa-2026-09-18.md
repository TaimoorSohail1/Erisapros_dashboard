# Schedule A customer rules — implementation and local QA

Date: 2026-09-18, Asia/Karachi. Local implementation evidence below; customer-rule changes were subsequently deployed and verified on the same date. Live automatic sending remains disabled. See the [production release report](C:/Users/Hp/Erisapros_dashboard/docs/qa/schedule-a-customer-rules-production-release-2026-09-18.md).

## Delivered

1. Blank organizational codes now default to `3` on proposed Schedule A recipient/broker rows in extraction, review preparation/editing, and XML payload generation. Explicit codes remain unchanged by this default; invalid nonblank codes still use existing validation. No recipient is created merely because a code is blank. Current FTW snapshots and unmatched preserved broker slots are not normalized.
2. The review UI identifies derived codes with “Defaulted to 3 because organizational code was blank.” Editing a blank code starts with 3; choosing a specific code clears the derived-value badge. Choosing “Use default (3)” restores the default. No new large banner, approval step, or review-wide sending gate was introduced.
3. Highest covered-lives selection uses preserved column positions and the supplied terms: Approximate # of Lives Covered, Persons Covered, Group Covered, Lives, Employee Count, Subscribers/Members. Only integer counts in the identified column are eligible; nearby money, IDs and dates are not candidates. Counts are not summed. Same-policy continued tables can share a maximum; distinct policies and partially or fully unreadable rows are held for review rather than globally merged. Recognized unaligned headers with competing nearby numbers also retain a review hold instead of trusting an unverifiable column maximum. Hartford has now passed the corrected-account provider-pipeline comparison; this is not a claim of universal OCR layout support.
4. Evidence retains source page, counts and selection reason. Customer defaults use explicit rule provenance, not fabricated OCR pages. The default's confidence represents deterministic application of that customer rule, not provider accuracy. A narrow provenance validator applies only to organizational code 3; other fields still require source evidence.
5. An unvalidated provider-failure review hold survives semantic recovery of a lives count. Source-backed values can remain visible without being promoted to trusted automatic updates.

Existing filings are normalized through review preparation/editing, not a bulk database migration or automatic re-extraction/replay. Cached historical payloads were not rewritten outside this path.

## Hartford source comparison

Source: `D:/Users/Hp/Downloads/1. Hartford_BRC Schedule A (1).pdf`, SHA256 `171a2f5e5a43467a61dc5bd66dcb9f13c2bfa7af1f48e7b2101ef6ba79732ffe`.

Page 2, policy 803154G, “Approximate # of Lives Covered” column: **17, 208, 22, 16, 17, 208, 20, 22, 195, 28**. Expected maximum and the new local result are **208**. The separate premium total is 161,956.30 and is not a count. The source page was rendered and visually inspected.

Local parser before the semantic rule had no 1e field. The new rule recovers 1e = 208, source page 2, evidence provider “Schedule A covered-lives column.” A measured local source-comparison run took **3.68 seconds**. This is local parsing/rule processing only, not Eyelevel, queue, agent or FTW send timing. The file is not a complete automatic-send acceptance fixture: this local extraction did not recover broker rows or establish a verified FTW target.

This newly recovered field has confidence 92%, below the existing default 95% automatic-send threshold. It is not made automatically eligible merely because a value was found; this rule does not inflate confidence to bypass the sender's checks.

Evidence: [local comparison JSON](C:/Users/Hp/Erisapros_dashboard/tmp/customer-rules/hartford-local.json).

## Eyelevel account correction and provider retest

The initial PDF attempt used the old local configuration: bucket **28208** and a different API key. That account rejected ingestion with **HTTP 402**, reporting its monthly maximum of **5,000,000 ingested tokens**. The attempt plus local fallback took **15.06 seconds**. This was **not evidence that the customer's production account was exhausted**; the earlier quota diagnosis is corrected.

The fallback recovered 208, but **Eyelevel extraction itself was not successful**. Do not count this as a successful Eyelevel comparison or an end-to-end automatic-send run. The provider-attempt JSON records the initial observation before the additional fallback-review regression was implemented; the final code separately verifies recovered counts remain REVIEW_REQUIRED at confidence capped to 0.5 when an unvalidated fallback hold is present. No second provider request was made merely to repeat the unchanged quota error.

Evidence: [provider-attempt JSON](C:/Users/Hp/Erisapros_dashboard/tmp/customer-rules/hartford-eyelevel.json). Reusable source-comparison script: [qa_schedule_a_customer_rules.py](C:/Users/Hp/Erisapros_dashboard/backend/scripts/qa_schedule_a_customer_rules.py).

Read-only AWS verification confirmed the production API and ShareFile worker both use bucket **32930** and the key shown in the customer's account screenshot. The screenshot showed ingestion usage of **36%**. Only the two local GroundX settings in ignored `backend/.env.local` were corrected; fresh settings were independently confirmed to match both production services. No keys are included in this report, and no production credential, subscription or quota was changed.

### Corrected-account result

The actual existing extraction pipeline completed in **127.09 seconds**, using **GroundX structured extract + X-Ray + local parser**, with no provider-failure manual-review hold. The corrected account accepted ingestion; a read-only document-status check returned HTTP 200 and confirmed the new Hartford document complete in bucket 32930. The source hash remained unchanged.

- Organizational code **3** was returned by structured extraction at confidence **0.97**. One broker row had code 3, `defaulted=false`: this PDF tests preservation of an explicit code, not the blank-code edge case.
- Before the new semantic rule, the normalized provider/local pipeline had **no 1e lives-covered field**. After the rule, it had **208**, page 2, confidence **0.92**, with all ten source-column candidates and policy 803154G retained. The count was recovered by the source-column rule, **not attributed to Eyelevel alone**.
- Semantic resolution was RESOLVED; canonical validation remained shadow-only, not authoritative. This source comparison does not establish automatic-send eligibility; 92% is below the default 95% threshold and no full filing/target check was run.
- **FTW writes: 0. Dashboard filings created: 0. Automatic sending was not enabled.** One PDF was ingested into the existing GroundX bucket for this authorized retest; no historical documents were deleted or bulk-replayed.

Evidence: [corrected-account comparison JSON](C:/Users/Hp/Erisapros_dashboard/tmp/customer-rules/hartford-eyelevel-correct-account.json). The original failed-attempt JSON is retained for audit. The measured 127.09 seconds is the complete extraction pipeline time, not agent or FTW sending time; no individual-stage latency claim is made.

## Tests and UI verification

- Final full backend suite on frozen implementation: **754 passed, 2 skipped, 64 subtests passed**, in **106.65 seconds**. Latest focused customer-rule/semantic/canonical/automation suite: 111 passed, 25 subtests passed.
- After correcting the local account settings, customer-rule, semantic-layer and automation regressions were rerun: **85 passed, 25 subtests passed**, in **1.58 seconds**. The full backend/frontend suite figures above are the prior implementation verification, not additional reruns during the configuration correction.
- New regressions cover blank/whitespace codes, explicit zero/nonblank preservation, raw XML and unmatched-slot preservation, rule provenance without invented pages, no provenance bypass for lives fields, all supplied keyword columns, currency/ID avoidance, zero/missing counts, mixed policies, same-policy continued tables, flat OCR, unreadable first rows, and provider-failure review holds.
- Existing automatic-send policy/execution tests use synthetic review services and fake vendor outcomes. They cover eligible guarded sending, read-back mismatch without blind resend, required-field/confidence failures, target/account restrictions and worker lease protection. They are **not real FTW writes**.
- Existing manual selected-field send, broker matching, review/XML, API, agent pause/resume, browser-isolation and repository regressions pass in the full suite. Their tests do not establish fresh live desktop/vendor acceptance.
- All frontend checks passed: review layout/diagnostics/responsive workspace, FTW agent settings, dashboard responsiveness/grouping, ShareFile sync controls, Field Rules creation and shared-polling performance.
- Production TypeScript/Vite build and built-bundle render smoke passed. Assets: `index-CYz2v5gD.js`, `index-nzOXAZKk.css`. Existing bundle-size and SDK-deprecation warnings remain non-blocking; skipped tests are not counted as passes.
- Local read-only synthetic review preview was inspected at **390×844**, **768×1024**, and **1440×900**. Default code 3, the explanatory badge/hint, explicit code 6, and restoring the default were verified. No page-wide horizontal overflow was observed; tablet tables retain their existing internal horizontal scroll. Save/vendor actions were not used in the read-only preview. Viewport override was reset.

## Automatic sending and production state

Read-only AWS inspection on 2026-09-18, named profile `erisapros`, region eu-north-1:

- API: `erisapros-production-api:25`; automation enabled, Schedule A updates enabled, **automatic send false**.
- Worker: `erisapros-production-sharefile-worker:18`; automation enabled, Schedule A updates enabled, **automatic send false**.

During the local implementation/account-correction stage, no production flags, task definitions, images, frontend objects, installed agents, credentials, mappings or vendor data were changed. The subsequent authorized customer-rule release updated API/worker images and the dashboard only; its exact scope and live verification are in the linked production report. No automatic-send code rewrite was needed: the existing guarded sender remains in place. Its default configured confidence threshold is 95%; “100% ready” must mean fully eligible under all checks, not simply 100% dashboard field coverage. Missing required data, unresolved decisions, invalid data, uncertain matching, permission restrictions or inadequate evidence/confidence still block automatic sending. Manual selected-field sending remains independently available.

## Deferred automatic-send activation and further acceptance

1. Human review/approval for the scoped customer-rule release was obtained; the release is now live. Real automatic-send testing and activation were explicitly deferred by the user.
2. Hartford's corrected-account provider-backed source comparison is complete. Additional representative layouts still require acceptance coverage; no production quota restoration is necessary for the successful Hartford retest.
3. Approve a named FTW test plan/workspace, exact data/destination, snapshot/restore scope and permitted automatic-send canary. No unspecified client plan should be written just to test this.
4. Run the actual automatic path (manual send button unused), recording extraction, queue, agent/Bring Forward where needed, send and confirmed read-back timing. Verify sibling schedules, existing broker rows, no-op/retry behavior and review pauses.
5. Compatible API/worker/dashboard customer-rule release is complete with rollback references. Global auto-send remains off. Enable only an explicitly approved scope after canary acceptance. Historical pending uploads must not be replayed without an explicit decision.

These limits do not invalidate the local customer-rule tests; they mean this report is **not a claim that production automatic sending is active or end-to-end certified**.
