# FTW Agent connection-preserving update — local verification

Date: 2026-09-18. Scope: local implementation and isolated verification, not production publication or installation on a connected computer.

## Delivered behavior

- Agent version 0.4.1 adds explicit `update` and `rollback` commands. Existing setup detects a saved connection and refuses to silently re-pair; fresh installation keeps its original connection flow.
- Update requires the approved package SHA256. Signed packages are the default; `--allow-unsigned` is a separate restricted-test opt-in, accepting NotSigned but not tampered/invalid signatures.
- Hash-verified backups retain the executable, protected credentials and original launcher. Update never decrypts credentials, rewrites device identity/token, replaces the saved login, changes startup registration, or copies/modifies the active dedicated browser profile.
- New runtimes hold a per-installation OS lock. Maintenance stops new claims, finishes active work, retries only an outstanding result acknowledgement and closes the dedicated context before exiting. Queue/completed-job behavior continues through the existing server implementation.
- Update waits for every process at the exact installed executable path to exit, then uses a staged checksum-verified replacement. No process is force-killed or targeted merely by image name. Personal Chrome/Edge profiles are not used.
- Legacy 0.3.2/0.4.0 processes cannot use the new local drain protocol: a confirmed idle controlled stop is required first. If they keep running, update refuses replacement. This is not an automatic forced upgrade of legacy agents.
- Interrupted maintenance retains a recovery marker referencing a completed backup. Explicit rollback validates the installation and executable digest and restores only the executable, never potentially stale credentials. A timed-out recovery stays closed for inspection.
- Spawner failure can restore the old executable when no new runtime is running. Startup-script launch is not a readiness/heartbeat test; asynchronous startup failures still require inspection.

## Tests and packaging

- Final backend suite: **795 passed, 2 skipped, 64 subtests passed in 56.64 seconds**. One existing GroundX SDK deprecation warning. The suite includes 41 new update/drain/system-helper cases, the existing pause/queue/browser-isolation cases, and existing extraction/review/sending regressions.
- Frontend `test:ftw-agent-ui` and `test:review-ui` passed. No frontend source was edited in this step.
- Focused new cases cover unchanged DPAPI bytes/decryptability for the same Windows user, optional saved login, unsigned opt-in, bad/missing/tampered checksums/signatures, active and idle drain, pending-result retry without re-click, stop during readiness/remote-control checks, legacy-process timeout, concurrent updater/runtime locks, source/protected-file changes while staging, executable restore, interrupted recovery and setup refusing silent re-pairing.
- Scoped `git diff --check` passed with only Windows LF/CRLF notices. An earlier non-default `core.autocrlf=false` check produced CRLF false positives; it was not used as a success signal or to rewrite unrelated files.
- Final local candidate: `output/ftw-local-agent-0.4.1-local/ERISAProsFTWAgentSetup.exe`; 398,726,637 bytes; SHA256 `3327C51C5DB975C2AF1CA175DCB5BDCBD05428264292545CCBE07295AB4C2944`; version recorded as 0.4.1; Authenticode **NotSigned**. Manifest is in the same directory. Build used already installed PyInstaller 6.22.2, Playwright 1.62.0 and httpx 0.28.1; no dependency upgrade was performed.
- Packaged synthetic update/rollback smoke: **passed**. Actual executable reports `ERISAPros FT Williams Agent 0.4.1`; embedded entry point, updater, Windows subprocess helper, installer and browser runtime code match the current tested sources. Actual packaged update and rollback both completed in the isolated installation; encrypted device/login bytes and decryptability, launcher and profile fixture hashes remained unchanged. Startup was not executed; no real credentials or FTW requests were used.

Reproduce source tests with `.venv/Scripts/python.exe -m pytest -q` from `backend/`. Reproduce the isolated executable check with `python backend/scripts/qa_ftw_agent_update_package.py --package output/ftw-local-agent-0.4.1-local/ERISAProsFTWAgentSetup.exe` from the repository root using the build Python environment (includes PyInstaller). The first smoke failure was not counted as success; the final artifact was rebuilt after the system-module-path fix and passed the same harness.

The isolated package harness sets LOCALAPPDATA to a temporary synthetic installation, stores only synthetic DPAPI credentials and cookie bytes, uses `--no-startup`, and exercises actual packaged update/rollback. It compares embedded code objects against current tested source, ignoring only build-specific source filenames. No real connection, browser login or vendor action is used by that harness.

## Packaged-check issue caught before release

The first packaged smoke failed before replacement: Windows PowerShell 5.1 inherited the hosting PowerShell 7 module path, and could not load Microsoft.PowerShell.Security for Get-AuthenticodeSignature. A source probe against a real Windows executable reproduced the same failure, ruling out a frozen-only DLL issue as the root cause. A new regression failed before the fix and passed afterward. System helpers now use their own trusted built-in OS module directory; failure messages retain bounded system diagnostics.

Frozen Windows helper launches also temporarily reset the bundled DLL search path and remove bundled PATH entries, restoring the original path afterward. This is defensive compatibility handling, not the demonstrated root cause. It follows [PyInstaller's external-program guidance](https://pyinstaller.org/en/stable/common-issues-and-pitfalls.html#launching-external-programs-from-the-frozen-application). It is limited to maintenance helpers, not Chromium launch behavior.

## Preserved production state and remaining gates

No public GitHub release/download replacement, AWS deployment, connected-device installation, real credential reading/decryption, production process termination, pairing/revocation, or FTW vendor write was performed. Public latest remains ftw-agent-v0.3.2. Production automatic sending remains outside this work and was not enabled. Existing filing/customer extraction rules were not edited.

Remaining: explicit approval for an unsigned restricted trial on DESKTOP-D9JV7IA; confirmed idle safe stop of its legacy runtime; real updated-runtime version/heartbeat, Pause/Resume, automatic login/MFA, pending-job recovery and personal-browser coexistence acceptance. Confirm an authorized test plan before any real Bring Forward action. Same-account fresh legacy devices intentionally block newer browser leases; coordinate participating devices before accepting mixed-version isolation. FTW server-side simultaneous-login restrictions remain an independent live-test question.

After the desktop trial passes, obtain client-pilot approval and make a separate public-distribution decision. No generic execution approval was treated as permission to publish an unsigned installer or replace a connected runtime. Rollback to legacy also requires server PAUSED/WAITING/lease and ambiguous-outcome reconciliation; restoring an executable alone does not establish safe mixed-version operation.
