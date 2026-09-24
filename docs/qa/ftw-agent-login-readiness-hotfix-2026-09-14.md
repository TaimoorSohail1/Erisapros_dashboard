# FT Williams login-readiness hotfix — 2026-09-14

## Finding

Agent 0.3.1 correctly found the FT Williams **Log In** button, but it could stop before clicking it. The readiness check found the expected account name `HighlandTech` inside the prefilled username `highlandtech.test` and incorrectly treated the expired login page as an authenticated session.

## Fix

- Agent 0.3.2 treats a visible FT Williams password form as authoritative proof that login is required.
- Automatic login now runs before account-name readiness matching.
- MFA/manual-verification detection also runs before account-name matching.
- Hidden and invisible form values are excluded from session-identity text.
- Existing retry limits, FT Williams domain restriction, Windows-encrypted credential storage, and post-login account verification remain unchanged.

## Verification

- The exact regression test failed on 0.3.1 and passes on 0.3.2.
- Focused agent runtime, setup, and delivery suite: **27 passed**.
- Full backend suite: **679 passed, 2 skipped, 39 subtests passed**.
- Packaged executable command-line smoke test: **passed**.

## Artifact

- Version: `0.3.2`
- File: `ERISAProsFTWAgentSetup.exe`
- SHA-256: `BEB782DD26A25F6ABB60145CCFA31630AEBDA78DD75CA1934AD681F8647DD7BE`
- Size: `398,709,947` bytes
- Signature: unsigned pilot build.

## Live verification

- GitHub release `ftw-agent-v0.3.2` was published from commit `b9153c7`.
- The remote executable is `398,709,947` bytes and reports the tested SHA-256 digest.
- The dashboard's stable `releases/latest` URL resolves to `ftw-agent-v0.3.2` and returns HTTP 200.
