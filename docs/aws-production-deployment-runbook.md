# AWS Production Deployment Runbook

This runbook describes how an authorized ERISAPros administrator or deployment agent releases this repository to the existing production AWS environment.

It is for application-code releases. Do not use it to change infrastructure, IAM, networking, credentials, database data, customer filings, or FT Williams records.

## Production environment

| Resource | Value |
| --- | --- |
| AWS region | `eu-north-1` |
| CloudFormation stack | `erisapros-production` |
| Website | `https://d3axcdlq9aydpw.cloudfront.net` |
| ECS cluster | `erisapros-production` |
| API service | `erisapros-production-api` |
| Worker service | `erisapros-production-sharefile-worker` |
| CodeBuild project | `erisapros-production-backend` |
| Frontend bucket | Resolve from the stack output `FrontendBucketName` |
| ECR repository | Resolve from the stack output `RepositoryUri` |
| CloudFront distribution | Resolve from the stack output `DistributionId` |

The AWS Console may open in `us-east-1`. Switch to **Europe (Stockholm), `eu-north-1`** before inspecting regional resources. CloudFront is a global service.

## Mandatory release gates

Do not deploy until all of the following are true:

1. The user or an authorized human explicitly approved the production deployment and its scope.
2. The exact files being released were reviewed. Do not deploy a mixed or unexplained working tree.
3. Tests and the production frontend build pass.
4. The approved commit is pushed to the Git branch configured in the production CloudFormation stack.
5. A rollback target is recorded before changing ECS or S3.
6. Automatic FT Williams sending, feature flags, secrets, and infrastructure remain unchanged unless they are explicitly included in the approved scope.

Never print, copy into documentation, or commit application secrets, FT Williams credentials, browser state, API keys, tokens, or the CloudFront origin-verification token.

## 1. Authenticate and resolve production resources

Run from the repository root in PowerShell:

```powershell
$Region = "eu-north-1"
$Stack = "erisapros-production"
$Cluster = "erisapros-production"
$ApiService = "erisapros-production-api"
$WorkerService = "erisapros-production-sharefile-worker"
$BuildProject = "erisapros-production-backend"

aws login
aws sts get-caller-identity --region $Region
```

Stop if the returned AWS account or role is not the approved production account/role.

Resolve values from CloudFormation instead of copying stale identifiers from an old release report:

```powershell
$ReleaseBranch = aws cloudformation describe-stacks `
  --stack-name $Stack `
  --region $Region `
  --query "Stacks[0].Parameters[?ParameterKey=='GitBranch'].ParameterValue" `
  --output text

$FrontendBucket = aws cloudformation describe-stacks `
  --stack-name $Stack `
  --region $Region `
  --query "Stacks[0].Outputs[?OutputKey=='FrontendBucketName'].OutputValue" `
  --output text

$RepositoryUri = aws cloudformation describe-stacks `
  --stack-name $Stack `
  --region $Region `
  --query "Stacks[0].Outputs[?OutputKey=='RepositoryUri'].OutputValue" `
  --output text

$DistributionId = aws cloudformation describe-stacks `
  --stack-name $Stack `
  --region $Region `
  --query "Stacks[0].Outputs[?OutputKey=='DistributionId'].OutputValue" `
  --output text

"Branch=$ReleaseBranch"
"FrontendBucket=$FrontendBucket"
"RepositoryUri=$RepositoryUri"
"DistributionId=$DistributionId"
```

Stop if any value is blank or `None`.

## 2. Prepare and verify the release commit

Inspect the working tree:

```powershell
git status --short
git diff --check
git diff --stat
```

Generated directories such as `output/`, `outputs/`, and `tmp/` are not production source. Do not stage them. Do not delete or discard pre-existing user changes unless the user explicitly requests it.

Install dependencies when needed, then run the release checks:

```powershell
if (-not (Test-Path backend\.venv\Scripts\python.exe)) {
  python -m venv backend\.venv
}

backend\.venv\Scripts\python -m pip install -r backend\requirements.txt
npm --prefix frontend ci

npm run check
npm --prefix frontend run test:dashboard-ui
npm --prefix frontend run test:field-rules-ui
npm --prefix frontend run test:sharefile-ui
npm --prefix frontend run test:ftw-agent-ui
```

Fix failures before release. A test failure may be waived only by an authorized human, with the reason recorded in the release report.

Commit only the approved files and push the exact commit to the configured release branch:

```powershell
git branch --show-current
git add <approved-files>
git commit -m "Release: <short description>"
git push origin HEAD:$ReleaseBranch

git fetch origin $ReleaseBranch
$LocalCommit = git rev-parse HEAD
$RemoteCommit = git rev-parse "origin/$ReleaseBranch"
"Local=$LocalCommit"
"Remote=$RemoteCommit"
```

The two commit hashes must match. CodeBuild clones the configured Git branch; uncommitted local changes are not deployed.

## 3. Record rollback targets

Create a release evidence directory outside Git staging:

```powershell
$ReleaseStamp = Get-Date -Format "yyyyMMdd-HHmmss"
$EvidenceDir = Join-Path "tmp" "aws-release-$ReleaseStamp"
New-Item -ItemType Directory -Path $EvidenceDir -Force | Out-Null

$ApiPrevious = aws ecs describe-services `
  --cluster $Cluster `
  --services $ApiService `
  --region $Region `
  --query "services[0].taskDefinition" `
  --output text

$WorkerPrevious = aws ecs describe-services `
  --cluster $Cluster `
  --services $WorkerService `
  --region $Region `
  --query "services[0].taskDefinition" `
  --output text

$PreviousIndexVersion = aws s3api head-object `
  --bucket $FrontendBucket `
  --key index.html `
  --region $Region `
  --query "VersionId" `
  --output text

aws s3api get-object `
  --bucket $FrontendBucket `
  --key index.html `
  --version-id $PreviousIndexVersion `
  --region $Region `
  (Join-Path $EvidenceDir "previous-index.html")

@{
  Commit = $RemoteCommit
  ApiPrevious = $ApiPrevious
  WorkerPrevious = $WorkerPrevious
  PreviousIndexVersion = $PreviousIndexVersion
} | ConvertTo-Json | Set-Content (Join-Path $EvidenceDir "rollback.json")
```

Stop if either task-definition ARN or the index version is blank.

## 4. Build the backend image

Start the existing CodeBuild project:

```powershell
$BuildId = aws codebuild start-build `
  --project-name $BuildProject `
  --region $Region `
  --query "build.id" `
  --output text

do {
  Start-Sleep -Seconds 10
  $BuildStatus = aws codebuild batch-get-builds `
    --ids $BuildId `
    --region $Region `
    --query "builds[0].buildStatus" `
    --output text
  "CodeBuild: $BuildStatus"
} while ($BuildStatus -eq "IN_PROGRESS")

if ($BuildStatus -ne "SUCCEEDED") {
  throw "CodeBuild failed with status $BuildStatus. Do not update ECS."
}
```

Resolve the immutable ECR digest produced by the build:

```powershell
$RepositoryName = $RepositoryUri.Substring($RepositoryUri.IndexOf("/") + 1)
$ImageDigest = aws ecr describe-images `
  --repository-name $RepositoryName `
  --image-ids imageTag=latest `
  --region $Region `
  --query "imageDetails[0].imageDigest" `
  --output text

$ImmutableImage = "${RepositoryUri}@${ImageDigest}"
"ImmutableImage=$ImmutableImage"
```

Stop if the digest does not begin with `sha256:`.

## 5. Release the backend to ECS

Use an immutable image digest so rollback is reliable.

For each service that is in scope:

1. Open **ECS → Task definitions** in `eu-north-1`.
2. Open the task-definition family currently used by the service.
3. Choose **Create new revision**.
4. Change only the relevant container image to `$ImmutableImage`.
5. Keep commands, environment variables, secrets, roles, CPU, memory, networking, logging, and feature flags unchanged.
6. Register the revision.
7. Open **ECS → Clusters → `erisapros-production` → Services**.
8. Update the service to the new task-definition revision and start the deployment.

Deploy the API when API/shared backend code changed. Deploy the worker when ShareFile intake, extraction, repository, review preparation, queue handling, or other worker-imported shared code changed. When uncertain about a shared backend module, deploy both using their own new task-definition revisions.

Before updating the worker, inspect the production SQS queue. Do not purge it. Prefer releasing while waiting/in-progress counts are zero, or coordinate a controlled drain.

Wait for stability:

```powershell
aws ecs wait services-stable `
  --cluster $Cluster `
  --services $ApiService $WorkerService `
  --region $Region

aws ecs describe-services `
  --cluster $Cluster `
  --services $ApiService $WorkerService `
  --region $Region `
  --query "services[].{service:serviceName,desired:desiredCount,running:runningCount,pending:pendingCount,task:taskDefinition}" `
  --output table
```

Expected result: each deployed service has its desired count running, zero pending tasks, and a completed deployment. The CloudFormation deployment circuit breaker is configured to roll back failed ECS deployments, but manual verification is still required.

## 6. Publish the frontend

Build the production frontend from the approved commit:

```powershell
npm --prefix frontend ci
npm --prefix frontend run build
npm --prefix frontend run smoke:build
```

Upload hashed assets first. Publish `index.html` last. Do not use `--delete`; the bucket contains retained assets and downloads that may not be present in `frontend/dist`.

```powershell
aws s3 sync frontend/dist/assets "s3://$FrontendBucket/assets" `
  --cache-control "public,max-age=31536000,immutable" `
  --region $Region

if (Test-Path frontend\dist\ftw-agent-setup-guide.html) {
  aws s3 cp frontend/dist/ftw-agent-setup-guide.html `
    "s3://$FrontendBucket/ftw-agent-setup-guide.html" `
    --cache-control "no-cache" `
    --content-type "text/html" `
    --region $Region
}

aws s3 cp frontend/dist/index.html "s3://$FrontendBucket/index.html" `
  --cache-control "no-cache,no-store,must-revalidate" `
  --content-type "text/html" `
  --region $Region
```

Invalidate CloudFront and wait for completion:

```powershell
$InvalidationId = aws cloudfront create-invalidation `
  --distribution-id $DistributionId `
  --paths "/" "/index.html" "/filings/*" "/settings/*" `
  --query "Invalidation.Id" `
  --output text

aws cloudfront wait invalidation-completed `
  --distribution-id $DistributionId `
  --id $InvalidationId
```

If the release has no frontend changes, do not rebuild, upload, or invalidate the frontend.

## 7. Production verification

Run the following read-only checks before declaring success:

```powershell
$Health = Invoke-WebRequest `
  -Uri "https://d3axcdlq9aydpw.cloudfront.net/api/health" `
  -UseBasicParsing

"HealthStatus=$($Health.StatusCode)"

aws logs tail /ecs/erisapros-production-api `
  --since 15m `
  --region $Region

aws logs tail /ecs/erisapros-production-sharefile-worker `
  --since 15m `
  --region $Region
```

Required checks:

- `/api/health` returns HTTP 200 and reports healthy status.
- ECS shows the expected task-definition revisions, desired/running counts match, and pending is zero.
- The load-balancer target is healthy.
- Recent API and worker logs contain no new `ERROR`, `CRITICAL`, or traceback events.
- The production HTML references the newly published frontend assets when the frontend was deployed.
- Authentication and the dashboard load correctly.
- Perform a scoped manual smoke test of the changed behavior.
- Do not upload a real client document, click Retry, Bring Forward, or send data to FT Williams unless those actions are explicitly authorized in the release scope.

## 8. Rollback

Rollback should restore the saved task definitions and/or frontend entrypoint. Do not delete database records, S3 objects, queues, or secrets as part of an application rollback.

Backend rollback:

```powershell
aws ecs update-service `
  --cluster $Cluster `
  --service $ApiService `
  --task-definition $ApiPrevious `
  --region $Region

aws ecs update-service `
  --cluster $Cluster `
  --service $WorkerService `
  --task-definition $WorkerPrevious `
  --region $Region

aws ecs wait services-stable `
  --cluster $Cluster `
  --services $ApiService $WorkerService `
  --region $Region
```

Only roll back a service that was changed by the release. Coordinate API and worker rollback when their contracts changed together.

Frontend rollback:

```powershell
aws s3 cp (Join-Path $EvidenceDir "previous-index.html") `
  "s3://$FrontendBucket/index.html" `
  --cache-control "no-cache,no-store,must-revalidate" `
  --content-type "text/html" `
  --region $Region

$RollbackInvalidationId = aws cloudfront create-invalidation `
  --distribution-id $DistributionId `
  --paths "/" "/index.html" "/filings/*" "/settings/*" `
  --query "Invalidation.Id" `
  --output text

aws cloudfront wait invalidation-completed `
  --distribution-id $DistributionId `
  --id $RollbackInvalidationId
```

Old hashed frontend assets are intentionally retained, allowing the restored `index.html` to reference them.

Repeat the production verification checks after rollback.

## 9. Release record

Create a dated report under `docs/qa/` containing:

- approved scope and explicit production authorization;
- Git branch and commit hash;
- tests and manual QA performed;
- CodeBuild ID and status;
- immutable ECR image digest;
- previous and new API/worker task-definition revisions;
- frontend bucket, previous index version, and CloudFront invalidation ID;
- health, ECS, target, log, authentication, and smoke-test results;
- actions deliberately not performed;
- exact rollback references.

Do not claim the deployment is successful until the service rollout, health checks, log review, and scoped production smoke test all pass.

## Infrastructure changes

The infrastructure template is `deploy/aws/cloudformation.yaml`. Normal application releases must not redeploy it.

If that template changes, create and review a CloudFormation change set first. Preserve existing parameters and secrets, inspect IAM/networking/data-resource replacements, and obtain separate human approval before execution. Never pass secret values on a command line that may be logged.
