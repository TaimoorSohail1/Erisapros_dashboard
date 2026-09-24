# FTW agent browser interference diagnosis — 17 September 2026

## Scope

Read-only diagnosis of reports that FTW plan search/login returns to Home while the local agent is active. No uploads, live FTW mutations, agent restart, login attempts, deployment, or Pause/Resume implementation were performed.

## Confirmed observations

- Local process inspection found separate personal Chrome and ERISAPros browser profiles. The agent browser uses `C:\Users\Hp\AppData\Local\ERISAPros\FTWLocalAgent\BrowserProfile`; personal Chrome uses its Google Chrome profile.
- `PersistentFTWBrowser.start()` launches a dedicated persistent Chromium context, not a connection to personal Chrome.
- `FTWLocalAgentRunner.run_once()` calls `session_ready()` before checking the job queue.
- Except while preserving an MFA page, `session_ready()` navigates the agent's owned page to FTW Home. The default polling delay is 10 seconds, plus cycle execution time.
- An offline probe using the actual runtime methods and existing browser/API test doubles reproduced five Home navigations in five empty-queue cycles. A separate personal-page double remained unchanged. There were zero live vendor requests or executed jobs.
- Existing runtime suite: **15 passed** (`backend/.venv/Scripts/python.exe -m pytest tests/test_ftwilliams_local_agent_runtime.py -q`). Passing existing tests does not mean the reported UX problem is absent.

## Finding and limits

The agent readiness check explains why manual browsing inside its controlled tab is repeatedly displaced by Home navigation, even when idle. This behavior was reproduced offline, not as a fresh live login/browser recording.

Interference with a genuinely separate personal browser was **not reproduced or confirmed**. Separate local profiles do not prove that FTW server-side sessions for the same credentials cannot conflict. Two connected devices in the supplied screenshot alone do not establish a competing-session cause.

No accessible timestamp-correlated client redirect trace was available from these checks. To establish the separate-browser symptom, capture a short recording with the redirect time and identify which browser/profile it occurred in, then correlate agent and FTW session activity. Avoid collecting passwords, cookies, or bearer tokens in the report.

## Recommended next step (not implemented)

Make readiness checks non-disruptive to the agent tab, and design safe Pause/Resume so clients can deliberately suspend automation. Verify personal-browser and multi-device session isolation with a controlled live reproduction before claiming that the separate-browser issue is fixed.
