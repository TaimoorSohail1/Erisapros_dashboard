# FT Williams Local Agent Proof-of-Concept Report

Date: September 11, 2026

## Outcome

Passed. A dedicated client-local Chromium profile preserved the HighlandTech FT Williams login after the browser was closed and reopened.

## Scope

- Read-only identity verification on the HighlandTech demo account.
- Five approved demo plans.
- Initial browser session and a full browser restart using the same persistent profile.
- No Bring Forward click, FT Williams update, approval, send, or production-account action.
- Browser profile and authenticated state remained on the local computer and were not uploaded.

## Results

| Demo plan | Interactive initial | Interactive restart | New-process initial | New-process restart |
| --- | --- | --- | --- | --- |
| American Securities | Passed | Passed | Passed | Passed |
| BTIG | Passed | Passed | Passed | Passed |
| FGF | Passed | Passed | Passed | Passed |
| Barry Robinson | Passed | Passed | Passed | Passed |
| Special Service | Passed | Passed | Passed | Passed |

Total: 20 of 20 headed-browser identity checks passed. The new-process run required no additional login.

Every check required the expected account, plan name, EIN, plan number, and 2025 filing year to appear on the FT Williams page.

An additional negative test intentionally switched the profile to a headless browser identity. FT Williams rejected that identity, confirming the agent must consistently use the same headed/minimized browser mode. Headless mode was removed from the proof-of-concept launcher.

## Safety evidence

- `mutation_attempted`: `false`
- `profile_state_uploaded`: `false`
- The test navigated and read page identity only.
- Existing cloud automation and manual dashboard behavior were not changed or enabled by this proof of concept.
- Automatic FT Williams updates remain disabled.

The sanitized machine-readable report is stored outside the repository at:

`C:\Users\Hp\.erisapros-secure\ftw-local-agent-profile-report.json`

## Automated tests

- Local-agent target and identity tests: 5 passed.
- Full backend regression suite: 588 passed, 2 skipped, 39 subtests passed.
- Covered safe FT Williams URL validation, required year, duplicate target rejection, login detection, account verification, and exact plan identity checks.

## Remaining implementation

This validates the most important technical assumption but is not yet a deployable client agent. Remaining work:

1. Add dashboard device pairing, revocation, and heartbeat status.
2. Add the outbound-only Bring Forward job queue and one-time job claims.
3. Connect the proven guarded Bring Forward click to the local persistent profile.
4. Re-query ftwLink after every click and compare record IDs before continuing.
5. Package and sign a Windows installer with automatic startup and secure token storage.
6. Test offline recovery, FT Williams session expiry/MFA, duplicate jobs, page changes, and agent upgrades.
7. Run the five-plan live Bring Forward suite in HighlandTech before enabling the feature.

## Conclusion

The client-local persistent-profile architecture is viable and materially more stable than copying browser cookies into the cloud. It minimizes repeated login and survives normal agent/browser restarts. A user must still log in again whenever FT Williams itself expires the session or requires MFA.
