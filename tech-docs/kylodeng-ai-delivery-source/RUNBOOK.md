# Operational Runbook — `kylodeng/ai-delivery-source`

> **Last updated:** [TODO: insert date on each revision]
> **Runbook owner:** [TODO: fill in team contact]
> **Applies to:** Production and non-production environments unless otherwise noted

---

## 1. Service Overview

`kylodeng/ai-delivery-source` is a GitHub Actions–driven AI delivery platform that automates five engineering workflows using the Anthropic Claude API (`claude-sonnet-4-6`): automated code review (Tool 1), technical documentation generation (Tool 2), business documentation generation (Tool 3), AI-generated test scaffolding (Tool 4), and UAT facilitation (Tool 5). Each tool is implemented as a Python script under `.github/scripts/` and invoked by a corresponding GitHub Actions workflow. At runtime, the tools read source files or PR diffs from this repository, call the Claude API, and write their outputs (markdown reports, test files, CSV test packs) to a companion repository `ai-delivery-outputs`. Notification emails are dispatched via SendGrid. The underlying data ingestion workload (`src/data_pipeline.py`) runs as an AWS Lambda function (`data-ingest-{env}`) triggered by S3 `ObjectCreated` events on the `capco-data-landing-{env}` bucket, processing customer CSV files and writing validated Parquet output to `capco-data-processed-{env}`.

---

## 2. Health Checks

### 2.1 GitHub Actions Workflows

| Check | How to verify | Expected result |
|---|---|---|
| Workflows are enabled | GitHub UI → Actions tab | All 5 workflows listed and not disabled |
| Tool 1 last run | Actions → "Tool 1 — Code Review" | Green on most recent PR or Monday 08:00 UTC run |
| Tool 2 last run | Actions → "Tool 2 — Tech Documentation" | Green on most recent `main` push or Sunday 06:00 UTC run |
| Tool 3 last run | Actions → "Tool 3 — Business Documentation" | Green on most recent `v*` tag push |
| Tool 4 last run | Actions → "Tool 4 — Auto Testing" | Green on most recent `src/**` PR or Wednesday 07:00 UTC run |
| Tool 5 last run | Actions → "Tool 5 — UAT Facilitation" | Green on most recent `release/*` branch creation |
| Secrets present | Repo Settings → Secrets and variables → Actions | `ANTHROPIC_API_KEY`, `GH_TOKEN`, `SENDGRID_API_KEY` all present |

### 2.2 AWS Lambda (Data Pipeline)

```bash
# Check Lambda function exists and is active
aws lambda get-function \
  --function-name data-ingest-dev \
  --region us-east-1 \
  --query 'Configuration.[State,LastUpdateStatus]'

# Invoke Lambda with a test event
aws lambda invoke \
  --function-name data-ingest-dev \
  --payload '{"bucket":"capco-data-landing-dev","key":"raw/test.csv"}' \
  --cli-binary-format raw-in-base64-out \
  response.json && cat response.json
```

Expected: `{"statusCode": 200, ...}`

### 2.3 S3 Buckets

```bash
# Confirm landing and processed buckets exist
aws s3 ls s3://capco-data-landing-dev/
aws s3 ls s3://capco-data-processed-dev/
```

### 2.4 Output Repository

- Navigate to `https://github.com/{OUTPUT_REPO_OWNER}/ai-delivery-outputs`
- Confirm recent commits exist under `tech-docs/`, `code-review/`, `uat/`, `auto-tests/`

### 2.5 External API Connectivity

```bash
# Claude API reachability (replace $KEY with actual key)
curl -s -o /dev/null -w "%{http_code}" \
  https://api.anthropic.com/v1/messages \
  -H "x-api-key: $ANTHROPIC_API_KEY" \
  -H "anthropic-version: 2023-06-01" \
  -d '{"model":"claude-sonnet-4-6","max_tokens":10,"messages":[{"role":"user","content":"ping"}]}' \
  -X POST -H "content-type: application/json"
# Expected: 200

# SendGrid reachability
curl -s -o /dev/null -w "%{http_code}" \
  https://api.sendgrid.com/v3/mail/send \
  -H "Authorization: Bearer $SENDGRID_API_KEY"
# Expected: 405 (method not allowed on GET — confirms endpoint is reachable)
```

---

## 3. Common Failure Scenarios

| Symptom | Likely Cause | Resolution Steps |
|---|---|---|
| Workflow fails with `KeyError: 'ANTHROPIC_API_KEY'` | Secret not set or name mismatch in repo settings | 1. Go to Repo → Settings → Secrets. 2. Add/rename secret to exactly `ANTHROPIC_API_KEY`. 3. Re-run failed workflow. |
| Workflow fails with `KeyError: 'GH_TOKEN'` | `GH_TOKEN` secret missing or PAT expired | 1. Generate a new PAT with `repo` and `contents:write` scopes. 2. Update `GH_TOKEN` secret. 3. Re-run workflow. |
| Tool 1/2/3/4/5 — `Could not parse Claude response as JSON` | Claude returned a non-JSON or markdown-wrapped response | 1. Check workflow logs for `[DEBUG] First 500 chars`. 2. Retry the workflow (transient Claude formatting variance). 3. If persistent, check if `MODEL=claude-sonnet-4-6` is still a valid model identifier against [Anthropic model docs](https://docs.anthropic.com/). |
| `write_output_file` fails with 404 | `ai-delivery-outputs` repo does not exist or `GH_TOKEN` lacks write access | 1. Confirm `ai-delivery-outputs` repo exists under `OUTPUT_REPO_OWNER`. 2. Verify PAT has `contents:write` permission to that repo. 3. Create repo if missing. |
| Tool 2 runs but no files are fetched | Repo has no files matching `.py .js .ts .go .tf .yaml .yml` or `max_files` exhausted | 1. Check `get_repo_files` call in logs. 2. Confirm source repo has code files. 3. Adjust `max_files` cap in `shared.py` if needed. |
| Lambda returns `statusCode: 500` | Malformed CSV, missing `key` in event, or S3 permission error | 1. Check CloudWatch Logs for the Lambda function (log group `/aws/lambda/data-ingest-{env}`). 2. Confirm the S3 key exists and the IAM role has access. 3. Test with a known-good CSV file. |
| Lambda not triggered by S3 upload | S3 notification not configured or Lambda permission missing | 1. Run `aws s3api get-bucket-notification-configuration --bucket capco-data-landing-dev`. 2. Confirm Lambda notification exists with prefix `raw/` and suffix `.csv`. 3. Re-apply Terraform: `terraform apply`. 4. Check `aws lambda get-policy --function-name data-ingest-dev` for S3 invoke permission. |
| `get_all_pending_files` returns empty list | No `.csv` files in `raw/` prefix, or >1000 objects (no pagination) | 1. Confirm files exist: `aws s3 ls s3://capco-data-landing-dev/raw/`. 2. If >1000 objects, the current code lacks pagination — implement `list_objects_v2` with `ContinuationToken` as a hotfix. |
| SendGrid email not received | `SENDGRID_API_KEY` invalid, sender not verified, or spam filtering | 1. Check SendGrid Activity Feed for the send attempt. 2. Verify `SENDER_EMAIL` (`noreply@ai-delivery.capco.com`) is an authenticated sender in SendGrid. 3. Check recipient spam folder. |
| Tool 4 generates tests for wrong framework | File extension unrecognised | 1. Check `detect_framework()` in `tool4_auto_testing.py`. 2. Ensure source files have standard extensions (`.py`, `.js`, `.ts`, `.go`). |
| Tool 5 UAT workflow doesn't trigger on branch creation | Branch name doesn't match `refs/heads/release/*` pattern | 1. Confirm branch is named `release/x.y.z`. 2. Manually trigger via `workflow_dispatch` with `uat_mode=generate`. |
| Terraform apply fails | AWS credentials not configured, state drift, or provider version mismatch | 1. Run `terraform plan` first to identify drift. 2. Confirm AWS credentials: `aws sts get-caller-identity`. 3. Check `required_providers` version constraint (`~> 5.0`). |
| S3 data is unencrypted / accessible publicly | `aws_s3_bucket.landing` has no SSE or public access block configured (known gap in IaC) | **Security incident — treat as P1.** 1. Immediately apply bucket policy to block public access: `aws s3api put-public-access-block ...`. 2. Enable SSE-S3 or SSE-KMS. 3. Update `infra/main.tf` to add encryption and public access block resources. 4. Escalate to security team. |
| Hardcoded AWS credentials in `data_pipeline.py` detected | `AWS_ACCESS_KEY` / `AWS_SECRET_KEY` committed to source | **Security incident.** 1. Rotate the exposed keys immediately in AWS IAM. 2. Remove hardcoded values from code. 3. Switch to IAM role–based auth (Lambda execution role already exists). 4. Scan git history with `git-secrets` or `trufflehog`. |

---

## 4. Deployment Procedure

### 4.1 Prerequisites

- AWS CLI configured with appropriate credentials
- Terraform ≥ 1.5 installed
- Python 3.12
- Access to `kylodeng/ai-delivery-source` and `ai-delivery-outputs` GitHub repos
- GitHub PAT with `repo` + `contents:write` scopes stored as `GH_TOKEN`
- Anthropic API key stored as `ANTHROPIC_API_KEY`
- SendGrid API key stored as `SENDGRID_API_KEY`
- [TODO: What AWS account/profile should be used for each environment (dev/prod)?]
- [TODO: Is there a Terraform remote state backend configured (S3/Terraform Cloud)?]

### 4.2 Deploy Infrastructure (Terraform)

```bash
# Step 1 — Navigate to infra directory
cd infra/

# Step 2 — Initialise providers
terraform init

# Step 3 — Review planned changes
terraform plan -var="environment=dev"

# Step 4 — Apply (type 'yes' when prompted)
terraform apply -var="environment=dev"

# Step 5 — Note outputs
terraform output landing_bucket
terraform output processed_bucket
```

> ⚠️ **Before applying to production**, ensure you have resolved the known security gaps:
> - Add S3 encryption (`aws_s3_bucket_server_side_encryption_configuration`)
> - Add S3 public access block (`aws_s3_bucket_public_access_block`)
> - Restrict IAM policy from `s3:*` on `*` to specific bucket ARNs
> - Remove `DB_PASSWORD` from Lambda environment variables — use AWS Secrets Manager

### 4.3 Deploy Lambda Function Code

```bash
# Step 1 — Package Lambda
zip -j lambda.zip src/data_pipeline.py

# Step 2 — Upload via Terraform (rerun apply) OR directly:
aws lambda update-function-code \
  --function-name data-ingest-dev \
  --zip-file fileb://lambda.zip \
  --region us-east-1

# Step 3 — Verify deployment
aws lambda get-function \
  --function-name data-ingest-dev \
  --query 'Configuration.[LastModified,CodeSize,State]'
```

### 4.4 Deploy / Update GitHub Actions Workflows

Workflow changes deploy automatically when merged to `main`. To update secrets:

```bash
# Using GitHub CLI
gh secret set ANTHROPIC_API_KEY --repo kylodeng/ai-delivery-source
gh secret set GH_TOKEN          --repo kylodeng/ai-delivery-source
gh secret set SENDGRID_API_KEY  --repo kylodeng/ai-delivery-source
```

### 4.5 Rollback Steps

#### Rollback Infrastructure (Terraform)

```bash
# Option A — Revert to previous Terraform state (if remote state is used)
# [TODO: Confirm whether remote state backend exists]
terraform state list   # review current state

# Option B — Destroy and re-apply previous version
git checkout <previous-commit> -- infra/main.tf
terraform plan -var="environment=dev"
terraform apply -var="environment=dev"
```

#### Rollback Lambda Code

```bash
# List available versions
aws lambda list-versions-by-function \
  --function-name data-ingest-dev \
  --query 'Versions[*].[Version,LastModified]'

# Roll back to a specific published version
aws lambda update-alias \
  --function-name data-ingest-dev \
  --name live \
  --function-version <previous-version>

# Or redeploy from a previous Git commit
git checkout <previous-commit> -- src/data_pipeline.py
zip -j lambda.zip src/data_pipeline.py
aws lambda update-function-code \
  --function-name data-ingest-dev \
  --zip-file fileb://lambda.zip
```

> [TODO: Are Lambda versions/aliases currently published? If not, implement versioning before relying on this rollback path.]

#### Rollback GitHub Actions Workflow

```bash
# Revert workflow YAML to previous commit
git revert <bad-commit-sha>
git push origin main
```

---

## 5. Monitoring & Alerting

### 5.1 AWS Lambda Metrics (CloudWatch)

| Metric | Namespace | Recommended Alarm Threshold |
|---|---|---|
| `Errors` | `AWS/Lambda` | > 0 in any 5-minute window |
| `Duration` | `AWS/Lambda` | > 25,000 ms (approaching 30s timeout) |
| `Throttles` | `AWS/Lambda` | > 0 in any 5-minute window |
| `ConcurrentExecutions` | `AWS/Lambda` | [TODO: set based on account limits] |
| `Invocations` | `AWS/Lambda` | Drop to 0 for >24 hours (dead-letter indicator) |

```bash
# View recent Lambda errors in CloudWatch
aws logs filter-log-events \
  --log-group-name /aws/lambda/data-ingest-dev \
  --filter-pattern "ERROR" \
  --start-time $(date -d '1 hour ago' +%s000) \
  --region us-east-1
```

### 5.2 S3 Metrics

| Check | How |
|---|---|
| Files accumulating in `raw/` without processing | `aws s3 ls s3://capco-data-landing-dev/raw/ \| wc -l` — alert if growing without corresponding `processed/` output |
| Unexpected public access | AWS Config rule `s3-bucket-public-read-prohibited` |
| Encryption disabled | AWS Config rule `s3-bucket-server-side-encryption-enabled` |

### 5.3 GitHub Actions Workflow Monitoring

| What to watch | Where |
|---|---|
| Workflow failure notifications | GitHub → Settings → Notifications (configure email/Slack) |
| Scheduled workflow skipped | GitHub Actions UI — check if cron jobs ran at expected times |
| Audit log of outputs | `ai-delivery-outputs` repo commit history |

> [TODO: Is there a Slack/Teams webhook integration for workflow failure alerts?]

### 5.4 Key Log Locations

| Component | Log location |
|---|---|
| Lambda execution logs | CloudWatch Log Group: `/aws/lambda/data-ingest-{env}` |
| GitHub Actions workflow logs | GitHub UI → Actions → select run → view step logs |
| Claude API call results | GitHub Actions step output (stdout from Python scripts) |
| Audit