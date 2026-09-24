# FTW Agent installer release preflight

Date: 2026-09-18. Status: local preflight completed; publication and desktop upgrade not performed.

Follow-up: the approved connection-preserving updater implementation and local verification are recorded in [ftw-agent-connection-preserving-update-2026-09-18.md](ftw-agent-connection-preserving-update-2026-09-18.md). The observations below describe the earlier 0.4.0 preflight, not the new 0.4.1 update command.

## Confirmed cause of disabled Pause

The user's downloaded `ERISAProsFTWAgentSetup (1).exe` has SHA256 `BEB782DD26A25F6ABB60145CCFA31630AEBDA78DD75CA1934AD681F8647DD7BE`. The current GitHub latest release is `ftw-agent-v0.3.2`; its installer asset digest matches this file exactly. The screenshot shows DESKTOP-D9JV7IA reporting Agent 0.3.2. Pause requires 0.4.0+. Downloading the latest published release therefore did not obtain the staged newer implementation.

## Verification performed

- Re-ran pause/resume, browser-isolation, Mongo selector, runtime, and local-agent API tests: **55 passed in 6.93 seconds**.
- Staged `output/ftw-local-agent-0.4.0-local/ERISAProsFTWAgentSetup.exe` still matches the previously tested SHA256 `1C3C315905A0085D5B7715A12AE8360EA6DE67742DE77DC78C0069E2B6E322FF`.
- Its Authenticode status is **NotSigned**. Neither CurrentUser/My nor LocalMachine/My contains a code-signing certificate.
- Reviewed the installer and entry point without executing an install or accessing saved credential contents.

## Release gates

1. **Signing:** provide an approved signed package or approved code-signing facility. Generic approval to execute the release plan does not waive its signing gate. An explicitly approved unsigned restricted canary would not constitute approval for unsigned public distribution.
2. **Connection-preserving upgrade:** the current ordinary installation path requires a pairing code, calls `pair_device`, rewrites the device credential, and force-stops processes by image name before copying the executable. Interactive setup can also clear saved FTW login credentials when the user skips login setup. This is not proof of the promised connection-preserving, safely drained upgrade. Do not use ordinary reinstall as that upgrade; implement and regression-test an explicit safe update path first, or agree a separately reviewed controlled update procedure.
3. **Updated-runtime canary:** real FTW login/MFA, safe active-work stop, queue recovery, read-back, and personal-browser coexistence remain unverified on 0.4.0. Synthetic regression tests do not establish whether FTW permits simultaneous logins. Fresh legacy same-account agents can intentionally block a newer agent's browser lease; reconcile participating devices before canary acceptance.

## Preserved state

No installer execution, process termination, pairing/revocation, credential replacement, public release mutation, production code deployment, or FTW vendor write occurred in this preflight. The public installer remains 0.3.2. Existing filing rules and production automatic-sending settings were not changed.

## Next sequence

Resolve the signing dependency; complete and test a safe update path; build and verify the final signed candidate and manifest; perform the authorized desktop canary with a confirmed test plan and rollback package; publish only after required acceptance checks pass; verify the latest-download asset digest/version and rollout to other participating computers. Do not label this preflight a completed release.
