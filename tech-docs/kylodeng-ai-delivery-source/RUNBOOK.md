# Operational Runbook — `kylodeng/ai-delivery-source`

> **Last updated:** [TODO: insert date]
> **Runbook owner:** [TODO: fill in team contacts]
> **Version:** 1.0

---

## 1. Service Overview

`kylodeng/ai-delivery-source` is a GitHub Actions–driven AI automation platform that delivers five Claude-powered developer tools against any target repository: automated code review (Tool 1), technical documentation generation (Tool 2), business documentation drafting (Tool 3), automated test generation and coverage gap analysis (Tool 4), and UAT test pack creation and defect analysis (Tool 5). Each tool is implemented as a Python script under `.github/scripts/`, invoked by a corresponding GitHub Actions workflow, and backed by three external services: the Anthropic Claude API (`claude-sonnet-4-6`), the GitHub REST API (for reading source repos and writing outputs), and SendGrid (for email notifications). All generated artefacts are committed to a companion repository (`ai-delivery-outputs`). The data pipeline (`src/data_pipeline.py`) runs as an AWS Lambda function triggered by S3 `ObjectCreated` events on the `capco-data-landing-{env}` bucket, validating and transforming customer CSV files into Parquet in the `capco-data-processed-{env}` bucket.

---

## 2. Health Checks

Run these checks to confirm all components are operational.

### GitHub Actions Workflows

```bash
# List the last 5 runs for each workflow
gh run list --repo kylodeng/ai-delivery-source --workflow tool1_code_review.yml --limit 5
gh run list --repo kylodeng/ai-delivery-source --workflow tool2_tech_docs.yml     --limit 5
gh run list --repo kylodeng/ai-delivery-source --workflow tool3_business_docs.yml --limit 5
gh run list --repo kylodeng/ai-delivery-source --workflow tool4_auto_testing.yml  --limit 5
gh run list --repo kylodeng/ai-delivery-source --workflow tool5_uat.yml           --limit 5
```

Expected: most recent runs show `completed` / `success`.

### Secrets Availability

Confirm all four required secrets are present in the repo:

| Secret | Used by |
|---|---|
| `ANTHROPIC_API_KEY` | All tools — Claude API |
| `GH_TOKEN` | All tools — GitHub API read/write |
| `SENDGRID_API_KEY` | All tools — email notifications |
| [TODO: Is a `DB_PASSWORD` or AWS credential secret configured?] | Lambda / Terraform |

```bash
gh secret list --repo kylodeng/ai-delivery-source
```

### Output Repository

```bash
# Confirm output repo exists and is writable
gh repo view kylodeng/ai-delivery-outputs

# Check recent commits (artefacts written by the tools)
gh api repos/kylodeng/ai-delivery-outputs/commits?per_page=5 \
  --jq '.[].commit.message'
```

### AWS Lambda (Data Pipeline)

```bash
# Check Lambda function state
aws lambda get-function \
  --function-name data-ingest-dev \
  --query 'Configuration.{State:State,LastStatus:LastUpdateStatus}'

# Check last 20 invocation log lines
aws logs tail /aws/lambda/data-ingest-dev --since 1h
```

Expected: `State: Active`, `LastUpdateStatus: Successful`.

### S3 Buckets

```bash
# Confirm buckets exist and are reachable
aws s3 ls s3://capco-data-landing-dev/
aws s3 ls s3://capco-data-processed-dev/
```

### Claude API Reachability

```bash
curl -s -o /dev/null -w "%{http_code}" \
  https://api.anthropic.com/v1/messages \
  -H "x-api-key: $ANTHROPIC_API_KEY" \
  -H "anthropic-version: 2023-06-01" \
  -H "content-type: application/json" \
  -d '{"model":"claude-sonnet-4-6","max_tokens":10,"messages":[{"role":"user","content":"ping"}]}'
```

Expected: `200`.

---

## 3. Common Failure Scenarios

| Symptom | Likely Cause | Resolution Steps |
|---|---|---|
| Workflow fails with `KeyError: 'ANTHROPIC_API_KEY'` | Secret not set or mis-named in repo settings | 1. Go to **Settings → Secrets → Actions**. 2. Add/re-add `ANTHROPIC_API_KEY`. 3. Re-run workflow. |
| Workflow fails with `KeyError: 'GH_TOKEN'` | PAT expired or never set | 1. Generate a new PAT with `repo` + `contents:write` scopes. 2. Update `GH_TOKEN` secret. 3. Re-run workflow. |
| Tool writes no file to `ai-delivery-outputs` | `OUTPUT_REPO_OWNER` env var empty; PAT lacks write access to output repo | 1. Confirm `OUTPUT_REPO_OWNER` is set (defaults to `GITHUB_REPOSITORY_OWNER`). 2. Confirm PAT has write access to `ai-delivery-outputs`. |
| `json.JSONDecodeError` in tool1/tool4/tool5 | Claude returned markdown fences or explanation instead of raw JSON | 1. Check workflow logs for raw Claude response. 2. Retry — usually transient. 3. If persistent, check if model name `claude-sonnet-4-6` is valid and accessible on the account. |
| `anthropic.APIStatusError: 529 Overloaded` | Claude API rate limit / overload | 1. Wait 60 s and re-run workflow. 2. Implement exponential back-off in `shared.py` [TODO: back-off not currently implemented]. |
| SendGrid email not received | `SENDGRID_API_KEY` invalid; sender domain not verified; `SENDER_EMAIL` domain not authenticated | 1. Log into SendGrid dashboard, check Activity Feed. 2. Verify sender identity for `noreply@ai-delivery.capco.com`. 3. Rotate API key if needed. |
| Lambda returns `500` / `Failed: ...` | CSV malformed; S3 key missing; hardcoded AWS credentials expired | 1. Check CloudWatch logs: `aws logs tail /aws/lambda/data-ingest-dev --since 1h`. 2. If credential error, rotate keys — **and** migrate to IAM role (see Security Notes). 3. Validate CSV format manually. |
| Lambda not triggered when CSV uploaded | S3 → Lambda notification not configured; Lambda permission for S3 missing | 1. `aws s3api get-bucket-notification-configuration --bucket capco-data-landing-dev`. 2. Confirm `aws_s3_bucket_notification` resource is applied. 3. Check Lambda resource-based policy allows `s3.amazonaws.com` to invoke. |
| S3 `list_objects_v2` returns incomplete results (>1000 files) | Pagination not implemented in `get_all_pending_files` | 1. Process is silent — files beyond 1000 are silently skipped. 2. Implement paginator: `client.get_paginator('list_objects_v2')` [TODO: fix in backlog]. |
| Tool 1 PR comment not posted | `GH_TOKEN` lacks `pull-requests: write` permission; PR number not passed correctly | 1. Confirm token scope includes PR comment write. 2. Check `PR_NUMBER` env var in workflow logs. |
| Terraform plan/apply fails | State file lock; provider version drift; missing `aws_region` variable | 1. `terraform force-unlock <lock-id>` if state locked. 2. Run `terraform init -upgrade`. 3. Pass `-var="aws_region=us-east-1"` explicitly. |
| Scheduled workflow never fires | Cron syntax correct but repo has been inactive >60 days (GitHub disables schedules) | 1. Trigger any manual `workflow_dispatch` to re-activate. 2. Consider adding a keep-alive workflow. |
| `get_repo_files` returns empty dict | Repo is private and `GH_TOKEN` lacks read access; wrong owner/repo name | 1. Confirm token has `repo` scope. 2. Verify `SOURCE_REPO_OWNER` and `SOURCE_REPO_NAME` env vars. |

---

## 4. Deployment Procedure

### 4.1 Prerequisites

- AWS CLI configured with sufficient IAM permissions
- Terraform ≥ 1.5 installed
- `gh` CLI authenticated
- Python 3.12 available locally for smoke-testing scripts

### 4.2 Infrastructure Deployment (Terraform)

```bash
cd infra/

# Step 1: Initialise
terraform init

# Step 2: Review plan — ALWAYS inspect before applying
terraform plan -var="environment=prod" -out=tfplan.out

# Step 3: Apply
terraform apply tfplan.out
```

> ⚠️ **Security gates before applying to production:**
> - Remove hardcoded `DB_PASSWORD` from `aws_lambda_function` environment block; replace with SSM Parameter Store reference.
> - Add `aws_s3_bucket_server_side_encryption_configuration` to both buckets.
> - Add `aws_s3_bucket_public_access_block` to both buckets.
> - Scope the IAM policy from `s3:*` / `Resource: *` to specific bucket ARNs and minimum required actions.
> - Add resource tags (`var.environment`, `Owner`, `CostCentre`).

### 4.3 Lambda Deployment

```bash
# Step 1: Package
cd src/
zip -r ../infra/lambda.zip data_pipeline.py

# Step 2: Deploy via Terraform (preferred) — re-run apply after packaging
cd ../infra/
terraform apply -var="environment=prod"

# OR deploy directly (hotfix only)
aws lambda update-function-code \
  --function-name data-ingest-prod \
  --zip-file fileb://lambda.zip
```

### 4.4 GitHub Actions Workflows

Workflows are deployed automatically when `.github/workflows/*.yml` files are merged to `main`. No manual step required.

```bash
# Confirm workflows are visible after merge
gh workflow list --repo kylodeng/ai-delivery-source
```

### 4.5 Secrets Bootstrap (first-time or rotation)

```bash
gh secret set ANTHROPIC_API_KEY  --repo kylodeng/ai-delivery-source
gh secret set GH_TOKEN           --repo kylodeng/ai-delivery-source
gh secret set SENDGRID_API_KEY   --repo kylodeng/ai-delivery-source
```

### 4.6 Rollback Steps

#### Rollback Lambda

```bash
# List published versions
aws lambda list-versions-by-function --function-name data-ingest-prod \
  --query 'Versions[*].{Version:Version,Modified:LastModified}'

# Point alias (or direct invocation) to previous version
aws lambda update-alias \
  --function-name data-ingest-prod \
  --name live \
  --function-version <previous-version-number>

# OR redeploy previous zip
aws lambda update-function-code \
  --function-name data-ingest-prod \
  --zip-file fileb://lambda-previous.zip
```

> [TODO: Are Lambda versioning and aliases currently configured? They are not defined in `main.tf`.]

#### Rollback Terraform Infrastructure

```bash
cd infra/

# Restore previous state snapshot (if remote state with versioning)
# [TODO: Is S3 remote state configured? Not found in main.tf — using local state is a risk.]

# Destroy a specific resource and re-apply previous config
terraform destroy -target=aws_lambda_function.ingest
git checkout <previous-commit> -- infra/main.tf
terraform apply -var="environment=prod"
```

#### Rollback a GitHub Actions Workflow Change

```bash
# Revert the merge commit that changed the workflow
git revert <commit-sha>
git push origin main
```

---

## 5. Monitoring & Alerting

### 5.1 GitHub Actions

| What to watch | Where |
|---|---|
| Workflow failure rate | `https://github.com/kylodeng/ai-delivery-source/actions` |
| Consecutive failures on scheduled runs | GitHub email notifications (repo **Settings → Notifications**) |
| Artifact upload failures | Workflow logs, step `Upload review JSON artifact` |

```bash
# List failed runs in last 24h
gh run list --repo kylodeng/ai-delivery-source --status failure --limit 20
```

### 5.2 AWS Lambda & S3

| Metric | Service | Threshold (recommended) |
|---|---|---|
| `Errors` | CloudWatch / Lambda | Alert if > 0 per 5 min window |
| `Duration` | CloudWatch / Lambda | Alert if p99 > 25 s (timeout = 30 s) |
| `Throttles` | CloudWatch / Lambda | Alert if > 0 |
| `ConcurrentExecutions` | CloudWatch / Lambda | Alert if approaching account limit |
| S3 `5xxErrors` | CloudWatch / S3 | Alert if > 0 |
| S3 `NumberOfObjects` in `raw/` | CloudWatch / S3 | Alert if growing unboundedly (stuck pipeline) |

> [TODO: Are CloudWatch alarms or dashboards currently configured? None found in `main.tf`.]

```bash
# View Lambda error metric (last 1h)
aws cloudwatch get-metric-statistics \
  --namespace AWS/Lambda \
  --metric-name Errors \
  --dimensions Name=FunctionName,Value=data-ingest-dev \
  --start-time $(date -u -v-1H +%Y-%m-%dT%H:%M:%SZ) \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%SZ) \
  --period 300 \
  --statistics Sum
```

### 5.3 Log Locations

| Component | Log Location |
|---|---|
| Lambda execution | CloudWatch Log Group: `/aws/lambda/data-ingest-{env}` |
| GitHub Actions | `https://github.com/kylodeng/ai-delivery-source/actions/runs/{run_id}` |
| Output artefacts | `https://github.com/kylodeng/ai-delivery-outputs` |
| Audit log | [TODO: `write_audit_entry` is imported in all scripts but its implementation is truncated in `shared.py` — confirm where audit logs are written.] |
| SendGrid delivery | SendGrid dashboard → Activity Feed |

### 5.4 Key Log Patterns to Watch

```
# Lambda — successful processing
"Processed .* {"processed": <n>, "failed": 0 ..."

# Lambda — partial failures (rows failed validation — may be normal)
"\"failed\": [^0]"

# Lambda — hard failure
"Failed: .*"

# GitHub Actions — Claude API error
"anthropic.APIStatusError"
"json.JSONDecodeError"
```

---

## 6. Escalation Path

| Level | Role | Contact | When to escalate |
|---|---|---|---|
| L1 | On-call engineer | [TODO: fill in] | Workflow failure; Lambda error rate > 5% |
| L2 | Platform / DevOps lead | [TODO: fill in] | Repeated failures; secrets rotation needed; infra change required |
| L3 | Security team | [TODO: fill in] | Hardcoded credentials detected in logs or code; S3 public exposure suspected |
| L4 | Anthropic support | [TODO: fill in — see Anthropic support portal] | Sustained Claude API outage (check https://status.anthropic.com) |
| L4 | SendGrid support | [TODO: fill in] | Bulk email delivery failure |
| Business owner | Kylo Deng | kylo.deng@capco.com | Business-impacting outage; data loss; compliance concern |

> ⚠️ **Immediate action required (pre-production):** Hardcoded AWS credentials (`AKIAIOSFODNN7EXAMPLE`) and a hardcoded `DB_PASSWORD` (`SuperSecret123!`) exist in the codebase. Escalate to the security team before any production deployment.

---

## 7. Useful Commands

### GitHub Actions

```bash
# Manually trigger Tool 1 (full repo review)
gh