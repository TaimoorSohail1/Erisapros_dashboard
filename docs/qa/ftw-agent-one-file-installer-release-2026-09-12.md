# FT Williams Agent one-file installer release report

Date: 2026-09-12  
Release: `ftw-agent-v0.2.0`  
Target: Windows client pilot

## Outcome

The FT Williams Agent is now packaged as one downloadable Windows executable. A client can download it from the FTW Agent settings page, open it, paste the one-time connection code, and continue without running PowerShell or manually configuring startup.

The setup executable pairs the computer with the selected ERISAPros workspace, stores the device token with Windows DPAPI, installs the agent for the current Windows user, registers automatic startup, and starts the agent. Existing `pair`, `run`, and `unpair` commands remain available for support and recovery.

## Verification

- Focused FTW Agent/API suite: **43 passed**.
- Full backend regression suite: **605 passed, 2 skipped**.
- Frontend contract, type-check, production build, and build smoke checks: **passed**.
- Packaged executable smoke test: **passed**.
- Packaged setup against an isolated pairing server: **passed**.
- Installed credential contained no plaintext device token: **passed**.
- Existing application flows: no regression detected by the full suite.

## Published artifact

- File: `ERISAProsFTWAgentSetup.exe`
- SHA-256: `78B1DEC994472B6FFA4966F8D999E3C8E0DE514B065BA12744364FD96B2A13A8`
- Signature status: **Not signed**

This pilot build may display a Windows **Unknown publisher** warning. A trusted code-signing certificate is still required before broad client distribution.

## Remaining pilot validation

The scheduled-task startup command was reviewed and covered by code tests, but an isolated Windows scheduled-task creation/removal test could not be executed in this environment. The first client pilot should therefore confirm that the agent restarts after Windows sign-out/sign-in and reconnects after an FT Williams login expires.
