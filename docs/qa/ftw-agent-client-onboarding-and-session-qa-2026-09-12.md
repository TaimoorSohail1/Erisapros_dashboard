# FT Williams Agent Client Onboarding and Session QA Report

Date: 2026-09-12
Scope: Client-friendly Windows agent setup, connection verification, browser/session recovery, and regression safety.

## Result

**Code and development-package QA: PASS**
**Client production rollout: BLOCKED until the Windows executable is code-signed**

No client filing, FT Williams plan, Schedule A, or production credential was changed during this work.

## Delivered

1. Simplified installer input:
   - Uses the production ERISAPros URL by default.
   - Finds a co-located `ERISAProsFTWAgent.exe` automatically.
   - Prompts the client only for the one-time connection code.
   - Directs the client to click **Test connection** when setup finishes.
2. Added a clear **Test connection** action to FTW Agent settings.
   - Shows **connected and ready**, **login required**, or **no active computer** in plain language.
   - Limits the result to devices belonging to the selected client workspace.
3. Added a responsive one-page client setup guide:
   - Install the signed agent.
   - Enter the one-time code.
   - Sign in to FT Williams locally.
   - Test the connection.
   - Includes login expiry, closed browser, offline, and wrong-account recovery.
4. Improved agent recovery when the client closes its dedicated FT Williams browser.
   - Stale browser objects are closed.
   - The same persistent local profile is reopened on the next polling cycle.
   - A waiting job resumes automatically after the user signs in; it is not claimed while login is required.

Client guide: `frontend/public/ftw-agent-setup-guide.html`

## Verification evidence

| Check | Result |
|---|---|
| Full application regression suite | PASS — 605 tests, 2 skipped |
| Focused pairing, isolation, routing, runtime, and delivery suite | PASS — 40 tests |
| Frontend typecheck | PASS |
| Production frontend build | PASS |
| Built-app smoke render | PASS |
| FTW Agent settings UI contract | PASS |
| Installer PowerShell syntax | PASS |
| Desktop guide render | PASS |
| 390 px mobile guide render | PASS |
| Closed-browser reopen | PASS |
| Login-required pause and first-cycle resume | PASS |
| Wrong-account and target guards | PASS |
| One-time pairing-code reuse rejection | PASS |
| Workspace/device isolation | PASS |
| Fresh-computer packaged-agent rehearsal | PASS |

### Fresh-computer rehearsal

An isolated temporary Windows credential location was used to simulate a new computer. The development package:

1. Consumed a one-time pairing code.
2. Created a device credential protected with Windows DPAPI.
3. Did not store the test device token in plaintext.
4. Revoked the paired device through its own authenticated endpoint.
5. Removed the local credential during disconnect.

The rehearsal used a local isolated endpoint. It did not connect to or modify a client FT Williams account.

## Development package

- Executable: `output/ftw-local-agent/ERISAProsFTWAgent.exe`
- Size: 398,700,132 bytes
- SHA-256: `E0B45CCB2E384BE3BF00D7EE26AD362DE0FCA91A217D38ACE6566DE947D65803`
- Signature status: **NotSigned**

This package is suitable only for local QA. It must not be sent to a client.

## Required production gates

1. Obtain the approved Windows code-signing certificate and signing tool.
2. Run the production build with signature required and verify the Authenticode signature and published checksum.
3. Publish the signed installer through an authenticated client workspace.
4. Repeat install, pairing, login-required, browser-close/reopen, connection test, update, and uninstall on a clean pilot Windows computer.
5. Use only a permitted demo plan for the first live read/update canary.

FT Williams passwords and MFA must remain local and user-entered. The agent may perform a read-only session check, pause safely, and resume after login, but it must not store or automatically type the client's password.

## Release decision

The application changes are regression-clean and ready to merge. Broad client rollout is a **NO-GO** until a signed installer passes the clean-machine pilot gates above.
