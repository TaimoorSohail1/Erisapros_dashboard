# Automated FT Williams Workflow — Local Implementation Report

Date: 2026-09-11
Branch: `codex/automated-ftw-workflow`
Environment: local only; FT Williams automation flags remain disabled by default

## Outcome

The optional straight-through Schedule A workflow is implemented behind independent feature flags. The established manual dashboard workflow is unchanged whenever automation is disabled or stops safely.

For an eligible, configured demo filing the workflow now:

1. Starts after extraction and the existing FT Williams current-data query.
2. Confirms the allowlisted customer, plan, and year.
3. Uses the existing safe Schedule A match, or selects **Add as new** only when every current record was queried and none has a strong identity match.
4. Stops for missing, low-confidence, unmapped, conflicting, unsupported, or blocking values.
5. Runs FT Williams' native Bring Forward action through a dedicated Playwright browser session when the current-year Schedule A is missing.
6. Performs a fresh ftwLink query after Bring Forward; a browser click is never treated as proof of success.
7. Sends only through the existing guarded `approve_and_update` path with a fresh snapshot, edit checks, payload validation, and no blocker override.
8. Marks the filing complete only after FT Williams read-back confirms every attempted change.
9. Resumes automatically after a reviewer fixes the one exception that stopped the workflow.
10. Re-runs automatic Schedule A selection after Bring Forward, including the guarded **Add as new** decision when every returned current-year record has zero strong identity matches.

## Safety controls

- All automation, Bring Forward, and auto-send flags default to `false`.
- Auto-send is restricted to the configured FT Williams demo customer, plan, and year allowlist.
- The confidence threshold defaults to 95% for required fields.
- Plan/year, Schedule A, contract type, broker rows, source evidence, validation, payload, and editability gates fail closed.
- A partial or ambiguous Schedule A identity match is never automatically added as new.
- Completed runs are idempotent and stale callbacks do not send twice within the running application process.
- A durable MongoDB lease prevents parallel API and ShareFile-worker processes from running the same filing simultaneously.
- A successful Bring Forward submission is stored against the exact browser customer, plan, and year. A delayed ftwLink response cannot trigger a second browser click.
- Unexpected browser-worker and post-Bring-Forward re-query exceptions are converted into an audited **Failed** state with the correct retry action; filings are not left indefinitely in **Processing**.
- Before/after Schedule A sequence IDs and newly detected record IDs are persisted and included in the audit trail.
- Field edits and fresh extraction invalidate the previous automation result and require a new FT Williams comparison.
- Plan discovery requires the normalized plan name, sponsor EIN, and plan number to agree; an EIN/plan-number-only match is rejected.
- Browser customer/plan IDs are stored separately from ftwLink IDs and are the only IDs used to build FT Williams website URLs.
- Straight-through processing now requires a confirmed Plan Registry entry that pairs the ftwLink identity with the separate browser identity. An API-only match can still use the established manual workflow, but it cannot auto-send or run Bring Forward.
- The fallback control accepts the exact FT Williams plan-page URL, validates its HTTPS host and year, extracts the browser IDs, and saves the pairing for later filings.
- Browser navigation is restricted to verified HTTPS `ftwilliam.com` plan URLs containing the confirmed browser customer, browser plan, and year.
- Before clicking Bring Forward, the browser worker verifies the visible plan name, EIN, plan number, and filing year.
- Browser session state is stored outside source control with service-account-only file permissions; the worker host must provide encrypted storage. Credentials are not placed in application settings.
- AWS injects the dedicated browser session from a separate retained Secrets Manager secret; the value is never committed or placed in the CloudFormation template.
- The production container installs Chromium and its runtime dependencies while continuing to run the application as a non-root user.
- A JSON allowlist supports multiple named demo targets and fails closed if it is empty or malformed.
- Bring Forward audit screenshots are excluded from source control because they can contain client data.
- Every decision, Bring Forward attempt, send start, result, and failure is written to the filing audit log.

## Client-facing UI

Automated filings use four plain statuses:

- **Processing** — no user action is required.
- **Action Needed** — only the highlighted exception needs review.
- **Completed** — FT Williams read-back verified the result, or current FT Williams data already matched.
- **Failed** — automation stopped safely and the manual workflow remains available.

Approve/send controls are hidden while automation owns a safe filing. Safe Processing and Completed filings show a compact summary, seven-step progress tracker, and optional **Advanced Review**. Action Needed shows every blocker, a plain-language next step, and one context-aware action instead of the legacy multi-button toolbar. Existing detailed comparison and correction tools remain available for exception handling and manual fallback.

## Local verification

- Dedicated automation decision/orchestration suite: 33 passed.
- Full backend suite: 578 passed, 2 skipped, 39 subtests passed.
- Frontend TypeScript check: passed.
- Frontend production build and smoke render: passed.
- Dashboard responsive/grouping, filing-review, failure-diagnostics, ShareFile, Field Rules, and shared-polling checks: passed.
- Playwright Chromium worker launch: passed.
- Local dashboard browser check: passed at desktop and 390x844 mobile widths; meaningful content rendered with no error overlay or console errors.
- Existing manual review workspace browser check: passed; query, plan-year, validation, field, broker, and pagination controls rendered correctly.
- Local ngrok test tunnel: passed with HTTP 200 after restricting Vite to the configured ngrok hostname.
- Python application compilation and `git diff --check`: passed; only expected Windows line-ending notices were reported.

No FT Williams record was changed during this implementation or test run.

## Scenario report

| Scenario | Local result |
| --- | --- |
| Safe existing Schedule A match | Passed |
| No credible match / Add as new | Passed |
| Ambiguous or close Schedule A match | Passed — Action Needed |
| Missing current-year record / Bring Forward orchestration | Passed with simulated browser result |
| Post-Bring-Forward re-query and new-record detection | Passed |
| Post-Bring-Forward Add as new | Passed |
| Delayed ftwLink convergence / duplicate Bring Forward prevention | Passed |
| Browser-worker exception / safe failure state | Passed — Failed with retry |
| Post-Bring-Forward re-query timeout / safe failure state | Passed — Failed with re-query action |
| Missing or low-confidence required field | Passed — Action Needed |
| Broker mismatch and broker read-back | Passed |
| Locked filing | Passed — Action Needed |
| Incorrect or missing Plan Registry mapping | Passed — Action Needed |
| Expired login and unsafe FTW URL | Passed — Action Needed / rejected |
| Duplicate processing and duplicate send prevention | Passed |
| Guarded send and successful read-back | Passed with simulated FT Williams response |
| Read-back mismatch and restoration behavior | Passed with simulated response and existing integration tests |
| Sibling Schedule A preservation | Passed through existing update/read-back regression tests |
| Existing manual workflow | Passed through full regression suite and browser QA |
| Live demo Bring Forward | Pending controlled demo execution |
| Live demo send/read-back/restore | Pending FT Williams update permission |

## Live validation dependencies

1. FT Williams currently returns error 53 for update attempts because the Highland demo KeyID has query-only permission. Implementation and simulated testing are complete; the live send/read-back proof waits for update permission.
2. The FGF test plan visible in the browser as `Fgf,Llc Employee Benefits Plan test` is not present under that exact name in the current ftwLink `PlanIDs_Batch` inventory. The similarly named non-test plan is now rejected. FT Williams must expose the test plan through ftwLink (or provide its exact ftwLink IDs) before post-Bring-Forward verification and updates can run.
3. The Barry Robinson website plan has no saved Plan Registry entry. ftwLink returned a same-name/EIN/PN record that already contains a 2025 Schedule A while the selected website plan does not. Automatic processing now stops until an operator confirms the exact ftwLink-to-browser pairing once.
4. The dedicated Playwright session must be refreshed or re-verified before a live Bring Forward test. The user's current in-app FT Williams session is logged into HighlandTech, but it is intentionally separate from the headless worker session.

The currently open `American Securities Llc Health And Welfare Plan` page was inspected read-only. Its 2025 page displays the native Bring Forward action and no Schedule A. No click was made because this website plan is not yet paired with a specific ERISAPros filing and ftwLink identity for a controlled test.

The dedicated demo-account browser storage state remains outside the repository and was not copied into source control.

## Controlled local activation order

1. Configure the exact Highland demo customer, plan, and year allowlist.
2. Save the dedicated demo browser session outside the repository.
3. Enable evaluation only with `FTW_AUTOMATION_ENABLED=true`; keep auto-send and Bring Forward off.
4. Confirm safe filings reach `SAFE_TO_SEND` and exceptions reach `ACTION_NEEDED` without writes.
5. Enable Bring Forward locally and test a missing-current-year demo filing, confirming the mandatory post-click re-query.
6. After FT Williams grants demo update permission, enable auto-send locally and run the demo update-and-restore test.
7. Review audit events and read-back results with the user.

Deployment remains out of scope until the user explicitly confirms the local results.
