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

- Focused automatic-login, DPAPI, pairing, browser-restart, retry, MFA, domain-boundary, credential-removal, and resume tests: **22 passed**.
- Full backend regression suite: **678 passed, 2 skipped, 39 subtests passed**.
- Frontend settings contract, review UI, polling performance, TypeScript, production build, and bundle smoke tests: **passed**.
- Packaged Windows executable `--help` smoke test: **passed**.

## Artifact

- File: `ERISAProsFTWAgentSetup.exe`
- Version: `0.3.0`
- SHA-256: `8514885CA8E45CF92D31D452A501576B5C4E4718E487EA6305E4C6DC7D7DA371`
- Size: `398,708,301` bytes
- Signature: unsigned pilot build; Windows can show an Unknown publisher warning until a production code-signing certificate is supplied.

## Production checks

- GitHub release: `ftw-agent-v0.3.0`; the stable `releases/latest` download resolves to this release and returns HTTP 200.
- Production setup guide: HTTP 200 and contains the automatic-login and FT Williams domain-boundary instructions.
- Production dashboard bundle: HTTP 200 and contains the automatic-sign-in and safe-retry UI.
- CloudFront invalidation: `I1XGKVKB1LMN2ZU873ISHSGY3Y` — completed.

## Live acceptance still requiring the client

Automated tests use synthetic credentials and never access a client's secret. Final acceptance on a client computer is: generate a fresh pairing code, install `0.3.0`, enter the client's FT Williams login in the installer, complete MFA if requested, and confirm that **Test connection** reports ready. A later forced FT Williams logout should then be recovered by the agent without another password entry.
