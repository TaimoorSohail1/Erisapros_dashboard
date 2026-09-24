# ERISAPros System Context

## Purpose

ERISAPros helps reviewers process Form 5500 Schedule A documents. It extracts document data, compares it with FT Williams, highlights uncertain fields, and supports controlled updates after human review.

## Main users

- ERISAPros reviewers who validate extracted Schedule A data.
- Administrators who manage field rules, ShareFile intake, and FT Williams agent connections.
- Authorized release operators who test and deploy approved versions.

## Main workflow

1. A Schedule A document arrives through manual upload or ShareFile intake.
2. The backend stores the filing and extracts structured values using the configured provider.
3. The extraction and semantic layers normalize fields, policy periods, carrier details, broker rows, fees, and commissions.
4. The review service queries current FT Williams values and prepares a field-by-field comparison.
5. Missing, conflicting, or low-confidence values stay in Action Needed for human review.
6. Approved values can be sent through the guarded FT Williams update flow.
7. FT Williams edit-check results are returned to the reviewer for any remaining corrections.
8. When a current-year Schedule A is missing, a connected Windows agent can perform a verified Bring Forward operation in its isolated browser.

## Components

- `frontend/`: React, TypeScript, and Vite review dashboard.
- `backend/`: FastAPI API, extraction/review services, repositories, ShareFile worker, and FT Williams services.
- `deploy/aws/`: AWS CloudFormation infrastructure template.
- `scripts/`: local analysis, QA, packaging, and agent scripts.
- `docs/`: architecture plans, operating instructions, QA evidence, and deployment guidance.

## External systems

- MongoDB stores filing, review, automation, and audit state.
- Amazon S3 stores uploaded source documents.
- GroundX or EyeLevel provides document extraction.
- ShareFile provides optional automated intake.
- FT Williams provides current filing data, updates, edit checks, and browser-based Bring Forward work.
- AWS hosts the production frontend, API, worker, queues, secrets, and logs.

## Safety boundaries

- Credentials and browser session state must never be committed to Git.
- Real secrets belong in local protected storage or AWS Secrets Manager.
- Automatic FT Williams sending and Bring Forward remain off unless explicitly enabled for an approved target.
- Uncertain extracted values require review; missing extraction must not erase a valid current FT Williams value.
- A production deployment requires explicit authorization, tests, rollback references, and post-release verification.

## Key documentation

- `HANDOVER.md`: handover setup and verification.
- `docs/aws-production-deployment-runbook.md`: controlled AWS release and rollback process.
- `docs/ftw-local-agent-operations.md`: FT Williams agent operation.
- `docs/plans/`: implementation and architecture plans.
- `docs/qa/`: historical QA and validation reports.
- `docs/agents/`: repository domain and issue-tracking guidance.
