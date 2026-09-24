# Oxford current-year identity hotfix review

Scope: one API module, `backend/app/services/ftwilliams_local_agent_jobs.py`, layered on the already verified API revision 28. Worker revision 21, frontend, installer, credentials, mappings and automatic-send settings are unchanged.

## Cause and correction

Live recovery revealed that `prepare_review()` deliberately clears `ftw_plan_url` when current-year records exist. The fresh-copy guard incorrectly treated that absent action URL as an identity failure. It now requires the confirmed browser customer/plan IDs and the expected year instead. Existing API IDs, query-success/completeness, current-year existence and no-copy-required checks remain mandatory.

## Review and tests

- Source-shaped stale-sibling regression failed before the fix (`CURRENT_QUERY_REQUIRED` instead of `NO_LONGER_REQUIRED`).
- Updated 32-case Oxford regression module passes, including wrong browser plan, unconfirmed mapping and wrong-year rejection.
- Full backend suite: 840 passed, 2 skipped, 64 subtests passed; one existing GroundX SDK deprecation warning.
- Candidate container smoke suite: 13 offline tests passed, including the real no-action-URL shape. No provider or native FTW mutation occurs in these tests.
- Image-only release freezes source hashes, compares the expected live baseline, verifies exact overlay scope and requires the desktop paused before rollout.

The old live sibling jobs already recovered by a later poll without another native copy. This exact corrected first-claim branch is covered locally and in the cloud candidate; no artificial live job was inserted to manufacture coverage.

## Separate unresolved session issue

A user-completed FTW login survived a fresh reload while the desktop agent was paused. After Resume restored the agent session, the separate FTW tab reloaded to the login panel. Separate browser profiles/processes were confirmed. Runtime source does not intentionally log out or navigate the personal browser; saved-login submission occurs only when the dedicated page displays a login form. Competing vendor sessions remain the leading explanation, not a confirmed published FTW policy.

An independent FTW username or vendor confirmation is needed to test coexistence. Do not copy personal-browser cookies, disable authentication, change credentials automatically, or claim a session-coexistence fix based on this queue patch. The desktop remains paused for now.
