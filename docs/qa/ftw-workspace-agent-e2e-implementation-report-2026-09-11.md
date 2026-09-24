# FT Williams Workspace Agent End-to-End Implementation Report

Date: 2026-09-11
Branch: `codex/automated-ftw-workflow`
Release decision: **DO NOT DEPLOY YET**

## Executive summary

The client-workspace isolation, verified plan mapping, workspace-only job routing, agent revocation, and installer integrity work is implemented locally. The existing legacy/manual routing remains available behind feature flags.

Automated backend and frontend testing passed. A controlled live test against the HighlandTech demo account proved that Form 5500 can be read, updated, verified, and restored. The same test exposed an unsafe FT Williams Schedule A replacement behavior: FT Williams accepted the request, but the resulting Schedule A did not preserve the original record and broker-row structure, and the attempted restore was not byte-for-byte equivalent to the baseline.

For that reason, Schedule A updates and production rollout remain blocked. No production account update test was performed, and the production Schedule A update flag remains disabled.

## Implemented work

### 1. Client workspace isolation

- Added a workspace-scoped FT Williams plan mapping model.
- Added mapping states for pending verification, verified, disabled, and needs review.
- Bound filings, plan mappings, devices, and automation jobs to a client workspace.
- Added database uniqueness and routing indexes.
- Prevented one workspace from listing, claiming, or revoking another workspace's devices and jobs.

### 2. Verified plan mappings

- Added authenticated APIs to create, list, verify, and disable plan mappings.
- Required matching EIN, plan number, year, plan name, FT Williams identifiers, browser identifiers, account name, and verification evidence.
- Prevented a filing from being reassigned across client workspaces.
- Added a Settings interface for managing verified plan mappings.

### 3. Safe job routing

- Jobs are created only when the filing has an enabled workspace and an exact verified mapping.
- Jobs are assigned to a ready, non-revoked device in the same workspace and FT Williams account.
- Devices cannot claim unassigned jobs, jobs for another workspace, or jobs for another device.
- The existing manual/HighlandTech flow is preserved while workspace routing is disabled.

### 4. Agent installation and removal

- Added installer SHA-256 verification.
- Added an option to require a valid Windows signature.
- Added a release manifest containing checksum, signature status, and build time.
- Added clean unpair/uninstall behavior that revokes the device before deleting local credentials.
- Added path validation so uninstall can remove only the expected per-user agent directory.

### 5. Schedule A XML construction

- Corrected nested broker-row handling so FT Williams vendor fields may restart their numeric suffixes inside each broker subpart.
- Preserved vendor-returned broker fields that are newer than the application's editable field map instead of silently dropping them.
- Made unsupported, non-convertible broker fields fail closed before any replacement is sent.
- Changed post-update verification to scan the complete Schedule A set and reject added, duplicated, leftover, or missing records.
- Added regression coverage for multiple broker rows using this vendor format.

### 6. Human-decision-only automation

- Applied confidence and source-evidence gates to every changed field, not only high-priority fields.
- Kept automatic Schedule A sending blocked while the live replacement canary is unresolved.
- Added a separate manual Schedule A next action instead of allowing the automatic sender to fail later.
- Added explicit Bring Forward confirmation bound to the exact FT Williams plan and year.
- Invalidated that confirmation when extracted source data changes; a different plan or year also requires a new confirmation.
- Added a dashboard confirmation dialog that shows the client, plan, plan number, and target year before the local agent can run.

### 7. Reusable live demo canary

- Added a demo-only verification command that requires explicit confirmation of the HighlandTech account.
- It reads a baseline, changes one harmless numeric value, checks the read-back, restores the baseline in a `finally` block, and produces machine-readable results.
- It does not permit an accidental production-account run through the normal invocation path.

## Verification results

| Check | Result | Notes |
|---|---|---|
| Backend full suite | PASS | 628 passed, 2 skipped |
| Workspace/device isolation | PASS | Cross-workspace and unassigned-device claims rejected |
| Mapping and filing API scope | PASS | Ownership and exact mapping checks covered |
| Agent delivery safeguards | PASS | Build, install, and uninstall safeguards covered |
| PowerShell syntax | PASS | Build, install, and uninstall scripts parsed successfully |
| Frontend typecheck | PASS | No TypeScript errors |
| Frontend production build | PASS | Build completed; existing bundle-size warning remains |
| Frontend UI checks | PASS | Dashboard, review, field rules, ShareFile, agent settings, performance, and bundle smoke checks passed |
| Bring Forward explicit confirmation | PASS | API, policy, audit, and dashboard confirmation covered |
| Human-decision-only policy | PASS | Low-confidence changed fields at every priority stop automation |
| Full Schedule A read-back scan | PASS | Extra or leftover records are structural failures |
| Demo Form 5500 read | PASS | 2024 data returned successfully |
| Demo Form 5500 update/read-back/restore | PASS | Canary confirmed and exact baseline restored |
| Demo Schedule A read | PASS | 2024 data returned successfully |
| Demo Schedule A update/read-back/restore | **FAIL** | Request returned success, but original record/broker structure was not preserved and exact restoration failed |
| Demo 2025 API read | **FAIL / investigate** | API returned FT Williams "could not locate" although the browser displayed a 2025 filing |
| Signed production installer | NOT TESTED | Requires the real signing certificate and release environment |
| Two-client physical pilot | NOT TESTED | Requires two approved client workspaces/devices and verified mappings |

## Live demo finding requiring recovery

The controlled Schedule A canary ran only against the HighlandTech demo account. After FT Williams accepted a replacement request, the Schedule A record identity and broker-row structure changed. The restore request also returned success but did not reproduce the exact original baseline. A current read shows a single Schedule A record with seven broker subparts; the original baseline had at least eight broker rows.

This means a successful FT Williams response is not enough to prove a safe Schedule A update. The demo Schedule A should be restored from a known-good saved/final copy or with FT Williams support before further mutable Schedule A testing.

## Release gates

Deployment must remain blocked until all of the following are complete:

1. Restore and independently verify the affected HighlandTech demo Schedule A.
2. Resolve why the FT Williams API cannot locate the browser-visible 2025 filing.
3. Re-run the live Schedule A update/read-back/restore canary successfully with the new full-field preservation and reconciliation controls.
4. Build and verify the signed Windows installer.
5. Complete a two-client isolation pilot using approved demo data only.
6. Obtain release approval before enabling workspace routing, Bring Forward, automatic sending, or any Schedule A update flag.

## Safety status

- No production account mutation test was performed.
- No production deployment was performed.
- Automatic sending remains disabled.
- Schedule A updates remain disabled.
- Automatic Bring Forward remains disabled.
- Workspace routing remains behind its rollout flag.
- Existing unrelated working-tree files were preserved.
