# FT Williams Agent automatic-login release — 2026-09-14

## Outcome

Agent `0.3.0` adds optional automatic FT Williams sign-in. During one-time setup, the client may enter the FT Williams company code, username, and password. The values are encrypted with Windows DPAPI for the current Windows user and are never sent to the ERISAPros API.

## Safety behavior

- Credentials are entered only when the top-level page is `ftwilliam.com` or one of its subdomains.
- The expected FT Williams account must still appear before the agent claims any work.
- A rejected automatic login waits five minutes before another attempt and stops after two failed attempts in the running agent.
- MFA, CAPTCHA, and verification-code pages remain open for the client; the poller does not navigate away from them.
- Waiting work stays paused while login or MFA is incomplete and resumes after the expected account is verified.
- The existing client/workspace/plan/year/Schedule A routing and post-action verification gates are unchanged.

## Verification

- Focused automatic-login, DPAPI, pairing, browser-restart, retry, MFA, domain-boundary, and resume tests: **20 passed**.
- Full backend regression suite: **676 passed, 2 skipped, 39 subtests passed**.
- Frontend settings contract, review UI, polling performance, TypeScript, production build, and bundle smoke tests: **passed**.
- Packaged Windows executable `--help` smoke test: **passed**.

## Artifact

- File: `ERISAProsFTWAgentSetup.exe`
- Version: `0.3.0`
- SHA-256: `73B665328CC03FB10576F72318C40D764404CE887C3A522D9250B72867209F34`
- Size: approximately 399 MB
- Signature: unsigned pilot build; Windows can show an Unknown publisher warning until a production code-signing certificate is supplied.

## Live acceptance still requiring the client

Automated tests use synthetic credentials and never access a client's secret. Final acceptance on a client computer is: generate a fresh pairing code, install `0.3.0`, enter the client's FT Williams login in the installer, complete MFA if requested, and confirm that **Test connection** reports ready. A later forced FT Williams logout should then be recovered by the agent without another password entry.
