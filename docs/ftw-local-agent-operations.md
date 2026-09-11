# FT Williams Local Agent Operations

## Purpose

The Windows agent performs only FT Williams' native **Bring Forward prior-year data for this plan only** action. Client/plan matching, validation, Schedule A selection, FT Williams updates, and read-back verification remain in the existing ERISAPros backend.

## Safe rollout

1. Keep `FTW_LOCAL_AGENT_ENABLED=false` while installing and pairing the client computer.
2. Create a one-time pairing code from `POST /api/ftwilliams/local-agent/pairing-codes` while signed in as an administrator.
3. Build the production executable with `scripts/build_ftw_local_agent.ps1 -RequireSignature -SigningCertificateThumbprint <thumbprint>`.
4. Run `scripts/install_ftw_local_agent.ps1` on the client's Windows account with the signed executable, HTTPS dashboard URL, and pairing code.
5. Sign in to HighlandTech once in the dedicated browser window. The browser profile and MFA state remain under the client's Windows account.
6. Confirm the dashboard reports **Local FT Williams agent: Connected**.
7. Enable `FTW_LOCAL_AGENT_ENABLED=true` only for the controlled demo test environment, then run the five-plan suite.

## Failure behavior

- Offline or stopped agent: dashboard shows **Action Needed** and keeps **Manual Bring Forward** available.
- Expired FT Williams session or MFA: agent reports **Login required** and never claims further work until the session is ready.
- Wrong account, plan, EIN, plan number, year, or changed FT Williams page: no click occurs.
- Click submitted but ftwLink cannot prove a new record ID: filing stops at **Action Needed**.
- Device loss or replacement: revoke it through `POST /api/ftwilliams/local-agent/devices/{device_id}/revoke`, then pair the replacement once.

## Production gates

- Signed executable and signature verification.
- Five HighlandTech demo plans pass Bring Forward and record-ID verification.
- Restart, offline, login expiry, MFA, duplicate-job, page-change, and timeout tests pass.
- Existing manual Bring Forward and existing FT Williams update workflow regression tests pass.
- Automatic FT Williams updates remain disabled until update permission and update/read-back/restore tests pass.
