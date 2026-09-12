# FT Williams Agent one-file installer release report

Date: 2026-09-12
Release: `ftw-agent-v0.2.1`
Target: Windows client pilot

## Outcome

The FT Williams Agent is now packaged as one downloadable Windows executable. A client can download it from the FTW Agent settings page, open it, paste the one-time connection code, and continue without running PowerShell or manually configuring startup.

The setup executable pairs the computer with the selected ERISAPros workspace, stores the device token with Windows DPAPI, installs the agent for the current Windows user, registers automatic startup, and starts the agent. Existing `pair`, `run`, and `unpair` commands remain available for support and recovery.

## Verification

- Focused FTW Agent/API suite: **44 passed**.
- Full backend regression suite: **605 passed, 2 skipped**.
- Frontend contract, type-check, production build, and build smoke checks: **passed**.
- Packaged executable smoke test: **passed**.
- Packaged setup against an isolated pairing server: **passed**.
- Installed credential contained no plaintext device token: **passed**.
- Existing application flows: no regression detected by the full suite.

## Published artifact

- File: `ERISAProsFTWAgentSetup.exe`
- SHA-256: `1B64A069D3879492679B48E9226F34F5A5535393621EC8D9F838FCF515E0A990`
- Signature status: **Not signed**
- Release: `https://github.com/TaimoorSohail1/Erisapros_dashboard/releases/tag/ftw-agent-v0.2.1`

This pilot build may display a Windows **Unknown publisher** warning. A trusted code-signing certificate is still required before broad client distribution.

## Production deployment verification

- Dashboard: `https://d3axcdlq9aydpw.cloudfront.net/settings/ftw-agent`
- CloudFront invalidation: `I1RL473D49ANBQXINU3WGLKH2P` — **Completed**.
- Live asset: `/assets/index-DsgRoAJo.js` — **HTTP 200**.
- Live bundle contains the `Download FTW Agent` action and release URL: **passed**.
- Live one-page setup guide: **HTTP 200** and updated download step confirmed.
- Public installer redirect: **HTTP 200**, `398,704,263` bytes.
- No FT Williams filing data was read or changed during this frontend/installer deployment.

## Startup hotfix verification

The first pilot reproduced a startup failure after pairing. Windows rejected Task Scheduler registration for the standard user with `Access is denied`, leaving the encrypted pairing in place but not starting the agent.

Version 0.2.1 replaces Task Scheduler with the current user's Windows Run registry entry, launches through a hidden VBS wrapper, replaces an already-running agent safely during reconnect, keeps setup errors visible, and opens the dedicated browser visibly.

Live verification on the pilot computer passed:

- Current-user startup entry: **created without administrator access**.
- Exact packaged 0.2.1 executable: **installed and running**.
- Bundled Chromium: **opened with the persistent FT Williams profile**.
- Production heartbeat: **received**.
- Dashboard device status: **Agent 0.2.1 — Login needed**.
- Remaining action: the user must sign in to FT Williams; credentials are never stored or entered by ERISAPros.
