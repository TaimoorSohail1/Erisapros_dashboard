# FTW Agent controls — authorized cloud release

Date: 2026-09-17 (Asia/Karachi). Cloud rollout complete and verified; desktop activation and real-vendor acceptance are not complete.

## Scope and deployment process

Used the established scoped AWS release process with named profile `erisapros`, account 427925098650, region eu-north-1. No infrastructure migration or IAM policy changes. Baselines, immutable source hashes, task definitions, and frontend index version were captured before deployment. Existing unrelated working-tree edits were preserved.

API overlay: `models.py`, `repositories.py`, `api/ftwilliams.py`, `services/ftwilliams_local_agent_jobs.py`. API task 25, image digest `sha256:62f53caddecf34f34eefefec63118abf3ced85d6553998d3541ad4817d5fa3fc`. Source inspection confirmed selected-send API/review implementations match the preceding deployed API.

Worker compatibility overlay is limited to `models.py`, `repositories.py`, and `services/ftwilliams_local_agent_jobs.py`. The prior worker could not parse PAUSED/WAITING device states. Queue, extraction, sending settings, environment, roles, and container command remain identical, with only the image changing. Worker cloud rollout completed after the network-isolated import/schema test.

Candidate worker import/schema smoke passed in CodeBuild `erisapros-production-backend:3e7b3671-6da4-463a-9112-85615057234c`, using `docker run --network none`. Worker task 18 rollout started with immutable image digest `sha256:05c1d63d281dd935c7e5ae38682259213eef46930daae0c848fc5ec65f44c6f6`.

Final read-only verifiers passed: API 25 and worker 18 each have one COMPLETED deployment with desired/running count 1 and pending count 0. API load-balancer targets are healthy. API/task configuration comparisons confirm image-only changes. Health endpoint returned 200; unauthenticated filings and agent-control requests returned 401. Frontend index and both assets match the locally tested production build byte-for-byte; CloudFront invalidation completed. Evidence: `tmp/ftw_agent_control_release_20260917/verification.json` and `tmp/ftw_agent_worker_release_20260917/verification.json`.

Frontend published assets: `index-CUorycgB.js`, `index-nzOXAZKk.css`. Index version `vQs3fxNR.K897.UhkALZpN.wm5gU6GUL`; CloudFront invalidation `I4G4XF1XBAETJ3ZOZ450SA1QIG`. Prior hashed assets were retained.

## Verification

- Final backend suite: 740 passed, 2 skipped, 39 subtests passed. Added legacy-agent compatibility regressions.
- Frontend agent-settings, review, dashboard, production build and build-render smoke checks passed.
- Legacy 0.3.2 agents retain their existing claim path; updated agents wait while a fresh legacy agent shares their FTW account. This avoids treating mixed-version rollout as safe browser exclusivity.
- Live authenticated settings UI showed both original computers Connected at 0.3.2. Pause buttons were disabled with explicit 0.4.0+ update instructions.
- Live review page retained visible fields and Send to FT Williams despite outstanding review items. The Send button was not clicked.
- Agent-settings browser console warning/error list was empty at inspection.
- No client uploads, agent-control mutations, connection-code creation, credential replacement, or FTW vendor writes were performed for acceptance testing.

The first independent worker smoke attempt failed because the existing CodeBuild role lacks ECR BatchGetImage. Retried using the established scoped, hash-verified image loader without expanding permissions. A subsequent smoke exposed an incomplete synthetic device fixture (missing required expected_account); corrected the test fixture without changing the application image.

## Desktop activation gate

Both production desktop agents remain 0.3.2; no executable replacement, process restart, pairing, public installer release, or download-link replacement occurred. The staged 0.4.0 package remains unsigned. SHA256: `1C3C315905A0085D5B7715A12AE8360EA6DE67742DE77DC78C0069E2B6E322FF`.

Remaining: approved signed package, or explicit approval of the unsigned package for the restricted desktop canary; connection-preserving update of participating computers; confirmed old runtimes stopped; real FTW login/MFA, Bring Forward/read-back, pause/resume and personal-browser coexistence acceptance. Synthetic tests and cloud health do not establish the exact cause of the previously reported live browser interference. FTW server-side session restrictions may require a separate agent account.

## Rollback references

- API: preceding `erisapros-production-api:24`, exact previous task definition retained under `tmp/ftw_agent_control_release_20260917/`.
- Worker: preceding `erisapros-production-sharefile-worker:17`, immutable digest `sha256:dcad9df62cf4e27e6d2a15c692b9bd75f2b4a8f22b34a513841191cbc2f2a25e`; exact task definition under `tmp/ftw_agent_worker_release_20260917/`.
- Frontend: previous index version `y6LC4vDVpiExgWjKdDzl7TiAae_ZTcIp` in frontend bucket `erisapros-production-frontendbucket-cakiwvjsgauc`; restore index then invalidate `/` and `/index.html`, retain old assets.
- Local desktop backup: `C:/Users/Hp/AppData/Local/ERISAPros/FTWLocalAgentBackups/pause-resume-preflight-20260917-015750`. Protected credentials were backed up without decryption and active browser profile remains in place.

Do not downgrade state readers after activating newer agents without first stopping the affected agents and reconciling persisted PAUSED/WAITING states. Current desktop activation has not happened.
