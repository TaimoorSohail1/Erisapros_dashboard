# ERISAPros Source Handover

This repository contains the complete application source, tests, infrastructure template, operating documentation, implementation plans, and QA records for the ERISAPros Schedule A / Form 5500 workflow.

## Repository contents

- `frontend/`: React and TypeScript dashboard.
- `backend/`: FastAPI application, background worker, extraction pipeline, FT Williams workflow, and automated tests.
- `deploy/aws/`: AWS CloudFormation template.
- `scripts/`: QA, analysis, local-agent, and packaging scripts.
- `docs/`: operations, plans, QA evidence, and the production deployment runbook.
- `CONTEXT.md`: system purpose, boundaries, components, and terminology.
- `.env.example`: safe configuration template with no credentials.

## Local setup

Requirements:

- Python 3.11
- Node.js and npm
- MongoDB for local persistence
- Docker when validating the production backend image

Create local configuration from `.env.example`. Store real credentials only in an untracked `.env.local`, protected Windows storage, or the approved secret manager.

Backend:

```powershell
python -m venv backend\.venv
backend\.venv\Scripts\python -m pip install -r backend\requirements.txt
$env:PYTHONPATH = "backend"
backend\.venv\Scripts\python -m uvicorn app.main:app --host 0.0.0.0 --port 8001
```

Frontend:

```powershell
npm --prefix frontend ci
npm --prefix frontend run dev
```

## Verification

Run the standard local checks:

```powershell
npm --prefix frontend ci
npm --prefix frontend run check
npm --prefix frontend run test:dashboard-ui
npm --prefix frontend run test:field-rules-ui
npm --prefix frontend run test:sharefile-ui
npm --prefix frontend run test:ftw-agent-ui
$env:SHAREFILE_CLIENT_ID = "test-client"
$env:SHAREFILE_CLIENT_SECRET = "test-secret"
$env:SHAREFILE_SHARED_ROOT_FOLDER_ID = "allshared"
$env:SHAREFILE_DISCOVER_SHARED_FOLDERS = "false"
$env:PYTHONPATH = "backend"
backend\.venv\Scripts\python -m unittest discover -s backend\tests -p "test_*.py"
docker build --pull --tag erisapros-backend:handover backend
```

The placeholder ShareFile values make mocked scan tests independent of a developer's private `.env.local`; they do not connect to ShareFile.

## Deployment

No deployment is performed as part of the source handover. Follow `docs/aws-production-deployment-runbook.md` only after explicit production authorization. The runbook includes test gates, resource discovery, release steps, verification, and rollback.

## Security notes

- This handover repository was created with a fresh Git history so historical credentials are not transferred.
- The handover snapshot was scanned for secrets before publication.
- Do not commit `.env.local`, API keys, passwords, browser state, customer documents, generated outputs, or local agent credentials.
- Rotate any credential that was previously committed to another repository, even if it has since been removed.
