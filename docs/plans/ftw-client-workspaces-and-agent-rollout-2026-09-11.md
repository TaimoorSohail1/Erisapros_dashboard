# Client Workspaces and FT Williams Agent Rollout Plan

Status: proposed — no multi-client automation is enabled by this plan alone.  
Owner: ERISAPros engineering and operations.  
Baseline: the HighlandTech five-plan Bring Forward canary is deployed; FT Williams updates and automatic sending are disabled.

## 1. Problem

The local FT Williams agent currently proves that a browser profile on a Windows computer can safely run FT Williams' native plan-only Bring Forward action. It is not yet safe to hand that pilot to unrelated clients because the present automation scope is a shared HighlandTech allowlist.

Every client must be able to pair their own computer and use their own FT Williams login without seeing, claiming, or changing another client's devices, plans, filings, or jobs.

## 2. Goal

Give each client administrator a self-service way to connect one or more trusted Windows computers to their ERISAPros workspace. After a plan is verified, only that workspace's agent may run a verified Bring Forward job for that workspace.

The client should only need to:

1. Install the signed ERISAPros FT Williams Agent.
2. Sign in to ERISAPros and create a one-time connection code.
3. Enter the code in the agent.
4. Log in to FT Williams once in the agent's dedicated browser.
5. Ask an ERISAPros administrator to verify the first plan mapping.

After onboarding, Schedule A upload, matching, Bring Forward, query, validation, and exception handling use the normal dashboard flow. Automatic updates/sending remain independently gated.

## 3. Scope and non-goals

### In scope

- Client workspace identity and administrator membership.
- Workspace-scoped FT Williams agent pairing, device status, device revocation, and audit history.
- Workspace-scoped approved FT Williams account and plan mappings.
- Routing Bring Forward jobs to one eligible workspace device only.
- A signed Windows installer and automatic start for the agent.
- Pilot testing with HighlandTech plus two separate demo workspaces.
- Permanent manual fallback for every exception.

### Out of scope

- Saving an FT Williams password, MFA code, cookie, or browser profile in ERISAPros cloud services.
- Bypassing FT Williams login expiry or MFA.
- Enabling automatic Schedule A update or automatic send before vendor permission and read-back/restore testing.
- Changing the existing manual Query FTW, Open FTW, Open FTW Bring Forward, review, correction, approval, or update flows.
- Broad rollout before the pilot gates pass.

## 4. Shared understanding and safety rules

1. A workspace is the security boundary for a client.
2. A pairing code belongs to exactly one workspace and expires after ten minutes or one use.
3. A device belongs to exactly one workspace and stores its token with Windows DPAPI.
4. A job belongs to exactly one workspace, filing, plan mapping, and year.
5. A device can claim only queued jobs for its own workspace and only if it is enabled, connected, browser-ready, and eligible for the mapped plan.
6. Before a click, the agent verifies FT Williams account, client/plan identity, EIN, plan number, year, expected FT Williams browser IDs, and the exact native Bring Forward control.
7. After a click, ftwLink must prove new current-year record IDs. A click by itself never means success.
8. Any uncertainty stops at Action Needed and leaves the manual FT Williams controls available.
9. Updates/sends remain disabled by independent production flags until their dedicated live demo tests pass.

## 5. Architecture

### New records

| Record | Required data | Purpose |
| --- | --- | --- |
| Client workspace | ID, name, status, client admin identities | Security boundary for filings, devices, mappings, and jobs |
| Workspace membership | workspace ID, Cognito user subject, role | Distinguishes client administrators from ERISAPros operations users |
| Agent pairing code | workspace ID, code hash, expiry, creator | One-time pairing; plaintext code is never stored |
| Agent device | workspace ID, encrypted-token hash, status, expected FTW account, version, last seen | Identifies a trusted client computer |
| Workspace plan mapping | workspace ID, EIN, plan number, year, ftwLink IDs, browser IDs, verification evidence, status | Allows only confirmed plans to automate |
| Agent job | workspace ID, filing ID, mapping ID, selected device ID, idempotency key, before/after IDs, result | Provides safe, auditable job routing |

### Authentication and permissions

- Cognito users receive a stable workspace membership from a server-controlled record or a verified custom claim.
- Client administrators can manage only their workspace's agents and mappings.
- ERISAPros operations administrators can create/disable workspaces and review all audit evidence, but cannot use a client device token.
- Device APIs authenticate with the local device token only; dashboard APIs authenticate with Cognito only.
- Every list, read, mutation, job claim, and audit query filters by workspace ID on the server. The browser never supplies a workspace ID that is trusted by itself.

### Job routing

1. A filing receives a workspace ID from its ShareFile intake mapping or a manual workspace selection.
2. The automation service finds the verified workspace plan mapping.
3. When Bring Forward is required, it selects one connected, browser-ready, non-revoked eligible device in that workspace.
4. It queues a job assigned to that device and workspace. The job includes a short-lived claim token and idempotency key.
5. The assigned device can claim it; every other device receives no job.
6. The device verifies the FT Williams page and clicks only the native Bring Forward action.
7. ERISAPros re-queries ftwLink, compares record IDs, runs current matching/validation, and records evidence.
8. If any check fails, the filing becomes Action Needed; it never falls through to update/send.

## 6. Dashboard experience

### Client administrator

`Settings → FT Williams Agent` shows:

- Connection state: Connected, Offline, Login Needed, or Update Needed.
- Connect this computer button and one-time pairing code.
- Installer version and signed-download link after the signed artifact is published.
- Trusted computers list, last-seen time, FT Williams login readiness, and Disconnect action.
- Verified plan mappings and their status: Pending Verification, Verified, Disabled, or Needs Review.
- Activity list limited to the client workspace.

The screen must never reveal another workspace's computer, account name, plan, filing, activity, or pairing code.

### Filing user

The normal filing screen remains simple:

- **Processing** while the safe workflow is running.
- **Completed** after matching and read-back verification.
- **Action Needed** only for missing values, uncertain matches, expired login/MFA, unavailable agent, missing mapping, locked filing, or verification failure.
- **Failed** for a completed attempt that requires operations investigation.

Open FTW, Open FTW Bring Forward, Query FTW, comparison, and manual correction remain available in Action Needed.

## 7. Delivery slices

### Slice A — workspace foundation

Create workspace, membership, filing ownership, and workspace audit records. Add a migration that assigns all existing HighlandTech demo data to the HighlandTech workspace. Keep existing manual flow operating for records that do not yet have a workspace.

**Acceptance:** a user can access only their workspace data; a pre-existing manual filing remains usable.

### Slice B — scoped pairing and settings

Bind pairing codes, devices, status, and revocation to a workspace. Update the existing Settings page to read the workspace from authenticated server state. Add server-side tests for cross-workspace list, status, and revoke denial.

**Acceptance:** Workspace A cannot see, pair against, or revoke Workspace B's device.

### Slice C — verified workspace plan mapping

Add a client-admin/operations workflow to associate a workspace with an FT Williams account and plan mapping. Require EIN, plan number, plan year, ftwLink IDs, browser IDs, evidence date, and verifier identity. Only `VERIFIED` mappings can be used for automation.

**Acceptance:** an unverified or mismatched plan stops at Action Needed and retains manual controls.

### Slice D — scoped job routing

Add workspace ID, mapping ID, and selected device ID to local-agent jobs. Modify claim queries so a device can claim only jobs assigned to its own workspace and ID. Preserve idempotency and read-back verification.

**Acceptance:** a simulated Workspace B device cannot claim a Workspace A job, even with the same expected FT Williams account name.

### Slice E — signed Windows delivery

Acquire a code-signing certificate. Produce a signed installer that validates its own signature, pairs the agent, stores credentials with DPAPI, creates a current-user startup task, and exposes an explicit uninstaller. Publish the signed version and checksum through the authenticated workspace settings page.

**Acceptance:** a clean Windows user can install, pair, restart, login, upgrade, and uninstall without administrator access beyond normal signed-install policy.

### Slice F — pilot and production gates

Run the complete matrix below with HighlandTech plus two demo workspaces. Do not enable Schedule A updates or automatic send. After update permission is granted, run the update/read-back/sibling/restore suite separately.

**Acceptance:** every required scenario passes and the manual-flow regression suite is green.

## 8. Test matrix

| Scenario | Expected result |
| --- | --- |
| Workspace A opens Settings | Sees only A's devices, mappings, and activity |
| Workspace B opens Settings | Sees only B's devices, mappings, and activity |
| A tries B's device ID | Server returns not found/forbidden; no details leak |
| Code reused or expired | Pairing fails; no device is created |
| Lost/revoked device | Heartbeat and job claim fail immediately |
| Valid A device + A verified plan | Receives only A's job |
| Valid B device + A job | Cannot claim the job |
| Missing current-year Schedule A | Exact device performs one Bring Forward; ftwLink proves new IDs |
| Existing safe match | No Bring Forward; best match selected |
| Ambiguous match or unverified mapping | Action Needed; no click/update/send |
| FT Williams login expiry/MFA | Login Needed; manual fallback remains usable |
| Browser layout changes | No click; Action Needed with evidence |
| Device offline/restart | Job waits/expires safely; no duplicate click |
| Duplicate retry | One job and one native action only |
| Signed installer install/upgrade/uninstall | Agent starts, persists profile, verifies signature, then cleanly revokes/removes |
| Existing manual workflow | Query, Open FTW, Open FTW Bring Forward, review, approval, and update paths behave unchanged |
| Update permission test | Only after FT Williams permission: send, read-back, sibling protection, and controlled restore pass |

## 9. Deployment sequence

1. Deploy Slice A and B with local-agent job routing disabled.
2. Create HighlandTech workspace and migrate the five approved demo mappings.
3. Deploy Slice C and D behind `workspace_agent_routing_enabled=false`.
4. Run automated and manual two-workspace isolation tests in a demo environment.
5. Publish signed installer; install on HighlandTech pilot machine.
6. Enable workspace routing for HighlandTech only; repeat the five-plan Bring Forward suite.
7. Add two demo workspaces; repeat cross-workspace and outage tests.
8. Obtain FT Williams update permission and run update/read-back/restore tests in demo only.
9. Enable production automation one workspace at a time after written acceptance.
10. Keep automatic sending disabled globally until the live update suite passes.

## 10. Release gates

The feature cannot advance if any item below is false:

- A production code-signing certificate is available and the installer signature verifies.
- Every agent API is workspace-scoped and covered by automated authorization tests.
- A job cannot be claimed by an agent outside its workspace.
- Client credentials/browser state are never stored or logged in ERISAPros cloud systems.
- HighlandTech five-plan suite passes after the workspace migration.
- Two-workspace isolation suite passes.
- Offline, login/MFA, browser-change, retry, timeout, and revocation scenarios pass.
- Existing manual workflow regression suite passes.
- FT Williams update permission tests pass before any update/send flag changes.

## 11. Inputs required from operations

1. The initial client workspace list and which Cognito users administer each workspace.
2. The approved FT Williams account name and first verified plan mapping for each pilot client.
3. A Windows code-signing certificate and approved artifact-distribution location.
4. FT Williams update permission for the HighlandTech demo KeyID when ready to test update/read-back/restore.

## 12. Success criteria

The feature is complete when a pilot client can pair a signed local agent, log in once to FT Williams, upload a Schedule A, and have a verified current-year missing-record case complete Bring Forward automatically—while another pilot client cannot view or claim any part of that work. All unsafe cases must stop at Action Needed with the existing manual flow intact.
