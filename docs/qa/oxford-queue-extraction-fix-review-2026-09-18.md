# Oxford queue and extraction fixes — review handoff

Date: 18 September 2026.

Status: implemented and verified locally; not deployed. Production preflight and scoped review diffs are prepared. Human review is required by the feature-delivery-workflow before shipping; approval of the previous plan is not recorded as review of this new code. Live retesting remains pending, not passed.

## Changes

- Require a fresh, conclusive FTW current-year query before issuing a native Bring Forward job. Existing current-year schedules cause a fresh full comparison instead of another copy.
- Preserve a durable dispatch marker across retries. Completed, submitted, and uncertain operations for the same account/browser plan/year cannot silently become a second copy, even if FTW still reports missing during propagation.
- Serialize native work across competing devices, including legacy devices. Recheck pause/revocation before dispatch and mark dispatch atomically against the active claim.
- Preserve pending jobs during Pause/Resume, including temporary fresh-query holds. Transient query holds can retry the read-only check automatically after a bounded backoff. Confirmed pre-click login/layout/target exits can safely retry; uncertain outcomes cannot repeat a native operation.
- Fix BCBS decimal parsing and source-backed reconciliation, including amounts with more than two decimal places.
- Fix UNUM leading-decimal `.00` and broker-table layout parsing. Preserve explicit paid-fee and additional-compensation components in source evidence. Retain established additional-compensation classification in the fee total, but require review rather than certify it automatically.
- Select EyeMed's highest covered-lives row, 335 rather than the sum 339. Handle separated EIN/NAIC columns so NAIC 71870 is not misread as lives. Multiple-contract grouping remains under review.
- Default missing organizational code to 3 without inventing a broker identity; preserve explicitly supplied codes.
- Close the fresh-query client's owned connection pool on both success and failure.

No frontend, installer, credentials, mappings, FTW schedules, or automatic-sending configuration changed in this turn. Existing source-date behavior and selected-field sending logic are preserved.

## Verification

| Check | Result | Evidence / limits |
| --- | --- | --- |
| Full backend regression suite | Passed | 830 passed, 2 skipped, 64 subtests passed; 53.84 seconds. One known GroundX SDK deprecation warning. |
| Oxford targeted regressions | Passed | 29 tests cover source-shaped extraction, stale sibling jobs, unknown outcomes, query errors, timeout, pause races, dispatch ownership and competing devices. FTW responses are simulated locally. |
| Six original source-file replay | Passed for targeted corrections | Five distinct originals plus one duplicate; captured live provider candidates replayed through the actual semantic/canonical pipeline against original PDF/XLSX layouts. Source hashes unchanged; no provider requests or production writes. This is not a fresh production extraction or certification of every field. |
| BCBS source amounts | Passed | Premium 1,630,230.90; taxes 19,236.72; four-decimal claims precision retained. No fabricated broker. |
| EyeMed covered lives | Passed | Highest proposal 335. Multiple source contracts still require a grouping decision. |
| UNUM compensation | Passed with review | 617.87 / 103.68 / 617.87 for original/second/duplicate. Explicit paid fees `.00` and separate additional compensation retained as evidence; classification remains review-required. |
| Missing organizational code | Passed | Code 3 added across all six replays; explicit nonblank codes preserved. |
| Read-only production preflight | Passed | API revision 27, worker revision 20, stable deployments; production automatic sending explicitly false. Seven-file diffs generated against actual running image sources, not a broad dirty-worktree deployment. |
| New fix deployed | Pending | No build/push/ECS rollout performed in this turn. |
| Live pending-job recovery after new guard | Pending | Do not count simulated tests or the earlier live copy as this fix's live recovery test. |
| Fresh live six-file extraction after release | Pending | Requires ordinary new QA intake after deployment; existing old extraction values have not been rewritten to manufacture a pass. |
| Real automatic sending | Deferred | Remains off as requested; not tested or enabled. |

Commands run from the backend directory:

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q --junitxml=../tmp/oxford_fix_20260918/pytest.xml
.\.venv\Scripts\python.exe -m pytest tests/test_oxford_regressions.py -q
```

An initial invocation from the repository root failed collection because `app` was not on the import path. The final suite was run from the correct backend directory and passed. A newly added test initially asserted the claim response rather than its `job`; this assertion was corrected. The unknown-outcome retry test now explicitly advances the fresh review timestamp beyond completion instead of depending on Windows clock resolution.

## Live safety snapshot

Read-only observation at 14:43:01 UTC:

- DESKTOP-D9JV7IA, Agent 0.4.1: PAUSED, browser_ready false, no active job.
- Five pending sibling jobs: QUEUED, attempts 0. Their execution windows have elapsed during the intentional pause; Resume's pending-job renewal is covered locally. No pending job was deleted or manually retried.
- The earlier completed native job remains VERIFIED, attempts 1. This turn did not issue another native operation.
- EP-PF43NS4S remains untouched, Agent 0.3.2, with its original stale heartbeat. Stored CONNECTED is not a claim that it is currently online.

Earlier live evidence of one genuine copy, safe pause during active work, unchanged previous-year records, and personal-browser isolation is in `remaining-oxford-agent-test-2026-09-18.md`. It does not substitute for the new guard's post-release verification.

## Review and release scope

Only these backend files are candidates for the API and worker overlays:

1. `app/models.py`
2. `app/repositories.py`
3. `app/services/ftwilliams_local_agent_jobs.py`
4. `app/services/extractor.py`
5. `app/services/schedule_a_semantic_layer.py`
6. `app/services/schedule_a_extraction_pipeline.py`
7. `app/services/schedule_a_customer_rules.py`

Review package: `tmp/oxford_fix_release_20260918/api-scoped.diff`, `worker-scoped.diff`, `candidate-source.json`, and `preflight.json`. The preflight is read-only and records source hashes and rollback baseline revisions; it does not deploy. Changes outside this allowlist belong to existing user work and must not be bundled accidentally.

Reviewer attention: confirm the conservative choice to preserve UNUM additional compensation under the existing fee classification with review, and to treat EyeMed's highest lives as a proposal while grouping remains unresolved. Confirm account-level serialization for legacy devices is acceptable. No claim is made that personal and agent logins cannot compete under vendor session restrictions.

## Next approved-release tests

After human review/approval:

1. Revalidate frozen hashes and current API/worker baselines; build narrow overlays using the established image-only procedure. Keep automatic sending false and preserve all non-image settings. Stop if production moved or a build fails.
2. Deploy API guard first while the desktop remains paused, then deploy worker when no message is in flight. Verify inherited layers, allowlisted source hashes, stable service health, authentication, and unchanged frontend. Roll back to the recorded revision if release checks fail.
3. Resume only the approved desktop. Observe ordinary claiming of the five pending jobs: fresh current-year checks must skip native copying, refresh comparisons, and never repeat the completed operation. If uncertain, pause safely and report the actual reason.
4. Independently read back the Oxford TEST 2025 records and unchanged 2024 baseline. Do not delete schedules or reset the year to create a missing-state test.
5. Upload fresh, clearly labeled copies of the six approved original inputs through normal ShareFile intake. Verify live provider extraction, corrected values, appropriate review flags, duplicate behavior, FTW comparison and agent state. Do not use manual Query/Retry/Send to present a broken automatic flow as passing.
6. Check desktop Pause/Resume and pending recovery, its dedicated browser only, and no mutation to the other device. Confirm automatic sending is still off and updates were not silently sent.
7. Produce a live final report with passed, failed, blocked and deferred results clearly separated. A fresh missing-year native test needs a separate approved target/year; do not rerun Bring Forward on the already-populated plan.

Artifacts: `tmp/oxford_fix_20260918/pytest.xml`, `source-replay.json`, `replay.py`, plus the read-only release review package above.
