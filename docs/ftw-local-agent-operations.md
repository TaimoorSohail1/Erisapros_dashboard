# FT Williams Local Agent Operations

## Purpose

The Windows agent performs only FT Williams' native **Bring Forward prior-year data for this plan only** action. Client/plan matching, validation, Schedule A selection, FT Williams updates, and read-back verification remain in the existing ERISAPros backend.

## Safe rollout

1. Keep `FTW_LOCAL_AGENT_ENABLED=false` while installing and pairing the client computer.
2. Create a one-time pairing code from `POST /api/ftwilliams/local-agent/pairing-codes` while signed in as an administrator.
3. Build the production executable with `scripts/build_ftw_local_agent.ps1 -RequireSignature -SigningCertificateThumbprint <thumbprint>`.
4. Run `scripts/install_ftw_local_agent.ps1` on the client's Windows account with the signed executable, HTTPS dashboard URL, and pairing code.
5. Sign in to HighlandTech once in the dedicated browser window. The browser profile and MFA state remain under the client's Windows account.
6. Confirm the dashboard reports **Local FT Williams agent: Connected**.
7. Enable `FTW_LOCAL_AGENT_ENABLED=true` only for the controlled demo test environment, then run the five-plan suite.

## Failure behavior

- Offline or stopped agent: dashboard shows **Action Needed** and keeps **Manual Bring Forward** available.
- Expired FT Williams session or MFA: agent reports **Login required** and never claims further work until the session is ready.
- Wrong account, plan, EIN, plan number, year, or changed FT Williams page: no click occurs.
- Click submitted but ftwLink cannot prove a new record ID: filing stops at **Action Needed**.
- Device loss or replacement: revoke it through `POST /api/ftwilliams/local-agent/devices/{device_id}/revoke`, then pair the replacement once.

## Disconnected-computer reconnect (Agent 0.4.5)

When ERISAPros has already disconnected/revoked a computer, the client may download the latest setup, open it, and enter a new one-time connection code. Setup verifies the saved token against ERISAPros before changing anything. Only a definitive revoked-token response enables automatic reconnect; an active token, network outage, unexpected server response, or unreadable protected credential fails closed without deleting the saved connection.

For a verified revoked token, setup removes the old startup entry, stops only processes whose executable path exactly matches the installed FT Williams Agent, installs the new package, replaces the device pairing, and restores startup. The encrypted FT Williams login and dedicated browser profile are preserved. Clients do not use Task Manager or delete `%LOCALAPPDATA%\ERISAPros` folders for this flow.

## Production gates

- Signed executable and signature verification.
- Five HighlandTech demo plans pass Bring Forward and record-ID verification.
- Restart, offline, login expiry, MFA, duplicate-job, page-change, and timeout tests pass.
- Existing manual Bring Forward and existing FT Williams update workflow regression tests pass.
- Automatic FT Williams updates remain disabled until update permission and update/read-back/restore tests pass.

## Pause/Resume and isolated-browser rollout (Agent 0.4.0)

### Connection-preserving update (Agent 0.4.1)

Use the packaged `update --expected-sha256 <approved-release-sha256>` command for an existing computer. Do not run setup/pair again to upgrade. Double-click setup detects an existing device credential and refuses to silently re-pair or clear the saved login. New-computer installation still uses the original pairing flow.

The updater validates the checksum and Authenticode signature, makes a hash-verified backup of the executable, encrypted credentials and startup launcher, then requests a safe runtime drain. It never decrypts saved credentials or copies the active browser profile. Existing startup registration, device identity/token, encrypted FTW login and dedicated profile stay in place.

Updated 0.4.1 runtimes hold a per-installation OS lock, prevent duplicate runtimes, stop claiming work on a local maintenance request, finish active work and retry only an outstanding result acknowledgement before exiting. The updater waits for **all processes at the exact installed executable path** to exit and refuses replacement on timeout. It does not kill processes by image name or close personal Chrome/Edge browsers. Older 0.3.2/0.4.0 runtimes cannot acknowledge this local drain protocol: first arrange a confirmed idle controlled stop; an update attempt while they are still running refuses replacement. Do not force-stop active work merely to pass upgrade QA.

Use `--allow-unsigned` only with explicit approval of a restricted unsigned canary. It permits NotSigned packages only, not invalid/tampered signatures. It does not grant approval for public distribution. The staged test package is 0.4.1; production download remains 0.3.2 until a separately accepted release.

Run maintenance from a separately downloaded installer, not the installed executable. Preserve the supplied checksum from the approved release manifest; never substitute an arbitrary checksum to bypass validation. Default drain timeout is 120 seconds; `--stop-timeout-seconds` can extend a reviewed active-operation window. `--no-startup` is reserved for isolated QA or controlled maintenance.

Interrupted updates leave `update-stop.request` in the installation directory with the verified backup path; new runtimes stay closed. Recover explicitly with `rollback --backup-directory <verified-update-backup-directory>`. Rollback validates the backup's installation and executable checksum, drains/waits again, and restores only the executable. It never overwrites credentials with potentially stale backup copies. If a recovery times out, the maintenance marker remains for inspection. A failed start can restore the previous executable; the operator must confirm actual readiness/version, because launching a startup script is not a connection test.

For a 0.4.1-to-older rollback, first reconcile server PAUSED/WAITING states and same-account lease ownership; an executable rollback is not proof that mixed-version browser operation is safe. Keep automatic sending disabled and use an authorized test plan for live acceptance.

Deploy the compatible API before updating the Windows agents and exposing their controls. Agent 0.3.2 does not understand Pause/Resume; its controls remain disabled with an update instruction. Update every connected computer for the same FTW account before enabling the new account-wide browser arbitration. Do not run the old and new executables simultaneously on one computer.

- Pause applies to one computer. Pause all connected computers to stop all agents. It is not permanent Disconnect.
- A requested pause blocks new claims immediately. An already claimed operation finishes and reports its outcome before the dedicated browser closes. The UI says **Pause requested** until the agent acknowledges closure; an offline device cannot confirm immediate closure.
- Pending queued/login-waiting jobs are retained. Resume extends their execution window, restores the dedicated profile or attempts bounded saved-credential sign-in, and continues only pending work. MFA remains a manual step when requested.
- Completed jobs are not requeued by Resume. Completion acknowledgements and downstream verification have replay guards. A crash or unconfirmed browser operation is held as **Action Needed**; a successful, newer current-data query is required before retrying that operation.
- Updated agents serialize dedicated browser access for the same normalized FTW account. A waiting device keeps its browser closed; an idle owner yields to eligible work assigned to another device. An abandoned lease eventually expires. Device/workspace job ownership stays enforced.
- During a staged upgrade, legacy 0.3.2 claims do not acquire the new browser lease. An updated agent reports a clear waiting reason while an older same-account agent is fresh; stop/update all older runtimes before accepting browser-isolation QA. Deploy compatible worker-side device readers before allowing PAUSED/WAITING states in the shared database.
- The agent launches only its own persistent profile and rejects personal Chrome/Edge profile paths. Readiness checks no longer reset an existing FTW search to Home on every poll. The personal browser is never attached to or navigated by this automation.

Browser-profile isolation does not prove that FTW permits multiple simultaneous logins with the same vendor credentials. Before production approval, use an authorized test plan to check personal-browser sign-in/search while the updated agent runs. If FTW invalidates concurrent sessions, agree a separate authorized vendor login or non-concurrent use; do not bypass vendor restrictions.

Local QA and release-artifact evidence: `docs/qa/ftw-agent-pause-resume-local-qa-2026-09-17.md`. The development executable is staged separately, not installed or published. Retain the existing signed-release gate and obtain deployment approval after local review and the controlled canary.
