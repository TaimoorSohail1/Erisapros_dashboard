# FT Williams End-to-End QA and Release Report

Date: 2026-09-12
Branch: `codex/automated-ftw-workflow`
Decision: **NO-GO — Schedule A replacement defect found; do not deploy**

## Executive result

The safety, workspace isolation, Bring Forward confirmation, human-review, Schedule A preservation, and dashboard presentation changes are implemented locally. The complete backend suite and all frontend build/UI checks pass.

Form 5500 update/read-back/restore passed on BTIG. The dedicated HighlandTech browser profile and API heartbeat were verified. American Securities Bring Forward completed, and the corrected full-slot follow-up found four current-year Schedule A records; however, the original preflight checked only the default slot, so the exact before-versus-after record set cannot be proven from that run.

The Schedule A canary exposed a critical test and replacement hazard. Its original baseline query read only FT Williams' default Schedule A slot even though the Special Service plan had 11 visible schedules. Sending that incomplete replacement set reduced the demo plan to one API-visible record and four UI entries, three of them blank. The reported “exact restore” therefore compared against an incomplete baseline and is invalid. Further Schedule A mutations and deployment were stopped immediately.

Production FT Williams data was not changed. The affected plan is in the HighlandTech demo account, but it must be reset from a verified FT Williams backup before testing resumes.

## Changes implemented

1. Saved FT Williams plan mappings are revalidated before a live current-data query. A stale mapping is repaired through the authoritative plan lookup or fails closed.
2. Workspace-scoped pairings, devices, verified plan mappings, and Bring Forward jobs prevent cross-client routing.
3. Bring Forward requires an explicit confirmation bound to the exact client, plan, plan number, and year.
4. Schedule A replacement preserves all current records and vendor broker fields, scans the complete read-back set, and rejects added, missing, or leftover records.
5. FT Williams broker field fragments are normalized into real business rows before matching, XML generation, and verification.
6. Nested vendor broker fields are no longer duplicated as top-level Schedule A fields.
7. The reversible demo canary now requires exact plan name, EIN, plan number, year, and HighlandTech confirmation; reads all 20 FT Williams Schedule A slots before writing; saves the complete baseline; runs a preservation preflight; and compares semantic data rather than fragile raw response ordering.
8. The default review view shows only fields or broker rows that need a human decision. Workflow-only blockers use a clear empty-state message.

## Scenario matrix

| Scenario | Result | Evidence / remaining action |
|---|---|---|
| HighlandTech browser account | PASS | FT Williams browser visibly showed the HighlandTech account. |
| Production write isolation | PASS for this run | Mutations were limited to confirmed HighlandTech demo plans; no production FT Williams record was changed. |
| Demo credential safety | PASS for canaries / deployment configuration pending | The confirmed HighlandTech KeyID was transferred only through a one-shot localhost bridge, was never printed or written to the repository, and the bridge was removed after testing. Production configuration must still be replaced through the approved secret store before release. |
| Exact plan/year guard | PASS | Canary now stops before writing unless plan name, EIN, plan number, IDs, account confirmation, and four-digit year match. |
| Existing Schedule A matching | PASS (automated) | Strong, ambiguous, duplicate, and identity-conflict paths are covered. Ambiguous matches require review. |
| New Schedule A creation | PASS (automated) / BLOCKED LIVE | Duplicate prevention and explicit new-record selection pass locally. Live testing stopped after the replacement defect. |
| Schedule A update/read-back/restore | **FAIL LIVE (demo)** | The canary used an incomplete one-slot baseline for a plan with 11 schedules. Its restore result is invalid and the demo plan requires recovery. |
| Multiple Schedule A preservation | **FAIL LIVE (demo)** | Special Service decreased from 11 visible schedules to four UI entries/one API-visible record after the incomplete replacement. |
| Broker row preservation | PASS (automated) / LIVE RESULT INVALID | Broker normalization tests pass, but the Special Service live result cannot prove preservation because sibling schedules were omitted. |
| Bring Forward confirmation | PASS | Confirmation is target-bound and invalidates when source, plan, or year changes. |
| Bring Forward happy path | **PARTIAL PASS LIVE (demo)** | The American Securities action completed and a corrected full-slot scan now finds sequences `1`–`4`. The original preflight checked only the default slot, so an exact full-set before/after comparison requires a clean demo plan retest. |
| Bring Forward failure paths | PASS (automated) | Offline, expired job, revoked device, wrong target, delayed re-query, duplicate job, and already-complete paths fail safely. |
| Current live agent | **PASS** | Five approved demo plans verified before and after browser restart; the paired device heartbeat returned `CONNECTED`. |
| Human-decision-only review | PASS | Matching unchanged fields do not require review; invalid, changed low-confidence, ambiguous, broker, and vendor mismatch cases do. |
| Decision-only dashboard UI | PASS locally | Source checks and production build pass. The deployed site still shows the prior UI because this work has not been deployed. |
| Desktop/responsive UI checks | PASS (automated) | Review and dashboard responsive-layout checks pass. |
| Physical Chrome/Edge/mobile matrix | NOT TESTED | Requires the corrected build in an approved preview/test environment. |
| Form 5500 update/read-back/restore | **PASS LIVE (demo)** | The BTIG canary updated one numeric participant field, read it back, and restored the original value exactly. |
| Signed Windows installer | NOT TESTED | Requires the production signing certificate and release environment. |
| Two-client physical isolation pilot | NOT TESTED | Requires two approved demo workspaces, devices, and verified mappings. |

## Live demo evidence

- BTIG Form 5500: identity check, update, read-back, and exact restore passed.
- American Securities Bring Forward: exact identity passed and the agent submitted once. A corrected full-slot follow-up found current-year sequences `1`–`4` and safely refused a second click. Because the original preflight checked only the default slot, the exact created set is not provable from this run.
- Special Service Schedule A: **failed** because the original canary queried only the default slot before a replace-style update. The later full-slot/UI audit proved the baseline was incomplete.
- The canary was corrected to query all 20 Schedule A slots before any future replacement. Focused safety tests now pass.
- Temporary credential handoff was deleted after the run, and a repository scan found no 64-character KeyID.

Evidence: `output/qa/ftw-demo-btig-canary-result-2026-09-11-v4.json`, `output/qa/ftw-demo-special-service-canary-result-2026-09-11-v1.json`, and `output/qa/ftw-demo-american-bring-forward-result-2026-09-11-v1.json`.

## Read-only demo observations

- American Securities returned no record in FT Williams' default slot before Bring Forward. The later corrected scan found four 2025 Schedule A sequences (`1`–`4`); therefore, the original one-slot observation cannot prove the complete before/after set.
- BTIG and Special Service returned current-year Schedule A data successfully.
- Special Service has multiple same-EIN/plan-number candidates, so exact verified mapping or human selection is required.
- Before the faulty canary, FT Williams visibly showed 11 Special Service Schedule A entries. The post-canary audit found one named entry plus three blank UI entries, while a full 20-slot API scan found one usable record.
- Barry Robinson remains excluded from mutable testing because a previous demo canary did not restore its original Schedule A structure conclusively.

## Automated verification

| Check | Result |
|---|---|
| Complete backend suite | **642 passed, 2 skipped, 39 subtests passed** |
| Backend warning | One existing GroundX SDK deprecation warning |
| New broker/canary safety tests | **70 passed** |
| Python compile check | **PASS** |
| Frontend TypeScript production build | **PASS** |
| Built-bundle smoke check | **PASS** |
| Review workflow UI checks | **PASS** |
| FT Williams failure diagnostics UI | **PASS** |
| Dashboard responsive/grouping checks | **PASS** |
| Field Rules UI check | **PASS** |
| ShareFile UI check | **PASS** |
| FT Williams agent settings UI check | **PASS** |
| Local-agent PowerShell syntax | **PASS** |
| Frontend warning | Existing JavaScript bundle-size warning only |

## Blocking defects and actions

1. **Recover the Special Service demo plan.** Ask FT Williams to reset/restore its 2025 Schedule A records from a known-good backup. Do not reconstruct the missing records from an old local snapshot without FT Williams confirmation.
2. **Prove full-slot preservation read-only.** After recovery, query all 20 slots and compare the count and identities against the FT Williams UI before allowing any update.
3. **Rerun Schedule A update/read-back/restore.** Save the complete multi-record baseline, require equal record and broker counts, change one safe value, read it back, restore all records, and independently re-query all slots.
4. **Run new Schedule A creation only after recovery.** Add one clearly identified demo record, verify exactly one addition, then restore the complete baseline and prove the test record is gone.
5. **Replace deployment credentials safely.** Remove the production KeyID from the application environment and install only the confirmed HighlandTech demo KeyID through the approved secret store.
6. **Complete physical UI/device gates.** Validate Chrome/Edge widths, signed installer lifecycle, and the two-client isolation pilot.
7. **Keep Barry Robinson excluded.** Do not mutate it until a known-good Schedule A baseline or FT Williams confirmation exists.

## Release gate

The corrected build may be deployed only after every item above passes with evidence, the demo data is restored, no high/critical defect remains, and an authorized reviewer records a **GO** decision. Until then, keep Schedule A updates, automatic sending, and workspace routing disabled and retain the existing manual fallback.
