# FT Williams legacy login-button hotfix — 2026-09-14

## Finding

The automatic-login credentials were filled correctly, but Agent 0.3.0 submitted the password field with Enter. The legacy FT Williams login page uses its visible **Log In** control for the complete submission behavior, so Enter could leave the client on the login page.

## Fix

- Agent 0.3.1 fills the same Windows-encrypted credentials and clicks the visible, exact **Log In** button or link.
- Enter remains a fallback only when FT Williams does not expose a matching control.
- The existing `ftwilliam.com` domain restriction, retry backoff, account verification, MFA pause/resume, and job-routing gates remain unchanged.

## Verification

- Reproduction test failed with Enter-only submission and passed with the visible-button click.
- Focused agent runtime, setup, delivery, and security suite: **27 passed**.
- Full backend suite: **679 passed, 2 skipped, 39 subtests passed**.
- Packaged executable smoke test: **passed**.

## Artifact

- Version: `0.3.1`
- File: `ERISAProsFTWAgentSetup.exe`
- SHA-256: `4B42E36654439F524F556AEC2235EB5B715134C750C943AF7DA8A670ABD8E9F5`
- Size: `398,709,731` bytes
- Signature: unsigned pilot build.

## Live verification

- GitHub release `ftw-agent-v0.3.1`: published from commit `7a219ae`.
- Remote executable digest and size match the tested local artifact.
- The dashboard's stable `releases/latest` URL resolves to `ftw-agent-v0.3.1` and returns HTTP 200.
