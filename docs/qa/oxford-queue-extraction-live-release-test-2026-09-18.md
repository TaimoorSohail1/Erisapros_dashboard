# Oxford queue and extraction live release test — 18 September 2026

## Result

The queue/extraction changes are live and verified. Six fresh Oxford TEST uploads completed normal intake, extraction and FTW comparison. Targeted extraction corrections passed. The separate-browser FTW session conflict was reproduced and is **not fixed** by this release. The desktop agent remains paused to avoid further login interference. Automatic sending remains off, as requested.

## Release verification

- Production API revision **29**, worker revision **21**; both completed healthy rollouts.
- API 29 digest: `sha256:c9a3b15547ab05cd0aa8e2b92f36ab2db6ddbccfc620ee21a936f70a08a726e1`.
- Worker 21 digest: `sha256:60eae27491f8a452b6c9a0e433b073e44338fc4c3c1909b81a48896012c04cbb`.
- Initial approved seven-file changes were verified in API 28 / worker 21. Live testing then found an absent action-URL edge case; API 29 changes only `ftwilliams_local_agent_jobs.py` on top of API 28.
- The guard now verifies confirmed customer/plan IDs and expected year instead of requiring a Bring Forward URL that `prepare_review()` intentionally removes once records exist. Wrong-target, unconfirmed-mapping, query-completeness and year checks remain enforced.
- Exact overlay hashes and inherited image layers verified. Non-image task configuration and frontend index unchanged. No installer or credential update made.
- Live health: HTTP 200 / `ok`; unauthenticated filings require authentication (401).
- Local tests: **840 passed, 2 skipped, 64 subtests passed**, one existing GroundX SDK deprecation warning. Focused Oxford regressions: **32 passed**. Candidate-container offline tests: **13 passed**.
- The corrected first-claim/no-action-URL branch has local and cloud-container coverage. No artificial live job was inserted to manufacture an additional live branch pass.

## Live queue and Pause/Resume

- Five pending sibling jobs survived Pause and had their execution windows renewed on Resume.
- Fresh current-year checks updated their reviews; all five jobs ended `EXPIRED / NO_LONGER_REQUIRED`, without a native payload dispatch. The original completed Bring Forward job remained `VERIFIED`, attempts 1; it was not repeated.
- A later poll recovered those jobs even before the absent-action-URL hotfix. That initial extra hold is captured in the evidence, not hidden as a clean first-claim success.
- Existing sibling filings retain previously recorded automation hold reasons; historical extraction was not overwritten or reprocessed. Fresh filings have newly calculated review decisions. Do not interpret old cached hold text as a new Bring Forward attempt.
- Resume opened the dedicated profile and restored sign-in without a new pairing code. Pause subsequently closed only the agent browser and retained the connection.
- Independent FTW read-back after all fresh extraction confirmed five 2025 Schedule A records and unchanged 2024 and 2025 query results.
- Five unrelated terminal/uncertain jobs were unchanged. EP-PF43NS4S was untouched, including its stored version and last-seen timestamp.
- Earlier active-copy Pause evidence is retained in the preceding Oxford test artifacts; this run did not reset the current year or repeat that native copy just to test Pause again.

## Six fresh normal-intake files

Uploaded the original five unique documents plus one explicitly labelled duplicate into the approved Oxford TEST 2025 ShareFile folder. Source hashes were verified; no original was changed. Normal ingestion was used—no manual Query, Retry, Bring Forward or Send button.

| File | Source-confirmed checks | Upload → extraction complete |
| --- | --- | --- |
| BCBSMA XLSX | Lives 304; premium 1,630,230.90; taxes 19,236.72; absent organizational code 3 | 5m 12s |
| Delta Dental PDF | Lives 331; commission 5,007; code 3 | 3m 30s |
| EyeMed PDF | Highest lives count 335, not summed 339; code 3 | 3m 30s |
| UNUM 918412 PDF | Lives 144; commission 5,349.89; separate compensation 617.87 captured; code 3 | 3m 27s |
| UNUM 918413 PDF | Lives 47 (source `047`); commission 1,244.18; separate compensation 103.68 captured; code 3 | 5m 36s |
| Labelled duplicate UNUM 918412 | Same corrected values; no additional native copy | 5m 35s |

All six extraction jobs completed in one attempt, each with 59 total mapped fields including the companion plan worksheet. All six used the approved Oxford TEST FTW IDs, detected current-year records and created zero Bring Forward jobs. All had zero attempted/confirmed FTW updates.

BCBS numeric formatting `1630230.9` preserves the exact amount 1,630,230.90; no cents were discarded. UNUM's source fees are `.00`, with separately labelled additional compensation. The existing compensation-to-fees proposal is retained **with review required**, not certified as an unambiguous customer-approved fee classification.

All six filings correctly remain `NEEDS_REVIEW / ACTION_NEEDED`. Delta's contract identifier mismatch and EyeMed's multiple-contract grouping still require decisions; their complete-comparison flags are false. Other confidence, classification and missing-field decisions were not overridden. These are review outcomes, not automatic-send successes. This test certifies the listed corrections, not every field in every carrier document.

The fresh live review page loads and displays the Send to FT Williams control. No Send action was taken.

## Session conflict — unresolved

The user signed in manually to the independent FTW tab. A refresh while the desktop agent was paused remained authenticated. After Resume restored the dedicated agent login, a fresh reload of the independent tab displayed the FTW login panel. This reproduces the reported logout symptom.

Separate browser profiles/processes were confirmed; the personal browser process remained alive. Runtime inspection found no personal-browser connection, cookie clearing or intentional logout. Saved login is submitted only when the dedicated agent page shows a login form. Competing vendor sessions are the leading explanation; no published simultaneous-session policy was found to confirm the vendor rule.

**Next requirement:** test an independent FTW username for the agent, or obtain FTW confirmation of simultaneous-login support. Existing user administration is documented in the [official FTW user guide](https://help.ftwilliam.com/help/general-navigation-and-company-plan-creation). Creating users/changing permissions or changing saved credentials requires a separate explicit step and user-entered credentials. Do not send passwords in chat.

Keeping the agent paused prevents further agent logins during this investigation. Pause is a workaround, not a session-coexistence fix.

## Evidence and final state

- Release evidence: `tmp/oxford_fix_release_20260918/verification.json`, `tmp/oxford_identity_hotfix_20260918/verification.json`, candidate-verification and build-evidence artifacts.
- Live evidence: `tmp/oxford_fix_release_20260918/live/manifest.json`, timestamped snapshots, `field-checks.json`, `final-checks.json`, `asserted-results.json`, `preservation.json`, `browser-session-evidence.json`.
- Desktop DESKTOP-D9JV7IA: agent 0.4.1, **PAUSED**, browser closed, no active job. Other device untouched.
- Six named QA copies remain in the test folder and dashboard for inspection. Nothing was deleted.
- Automatic sending: **OFF**, deferred; real sending and update read-back were not tested in this run.

Do not report the whole workflow or both issues as fully fixed: queue/extraction release passed; independent-browser login coexistence remains blocked on a separate login/vendor guidance.
