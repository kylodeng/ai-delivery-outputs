# Operational Runbook — `kylodeng/ai-delivery-source`

> **Last updated:** [TODO: insert date]
> **Runbook owner:** [TODO: fill in team contacts]
> **Repo:** `https://github.com/kylodeng/ai-delivery-source`
> **Output repo:** `https://github.com/kylodeng/ai-delivery-outputs`

---

## 1. Service Overview

`kylodeng/ai-delivery-source` is a GitHub Actions–driven AI automation platform that orchestrates five Claude-powered delivery tools: automated code review (Tool 1), technical documentation generation (Tool 2), business documentation drafting (Tool 3), AI-generated test suites (Tool 4), and UAT facilitation (Tool 5). Each tool is triggered by a corresponding GitHub Actions workflow (PR events, pushes to `main`, release tags, cron schedules, or manual dispatch), calls the Anthropic Claude API (`claude-sonnet-4-6`), and writes its outputs to a companion repository (`ai-delivery-outputs`). The platform also includes a live S3-based customer data ingestion pipeline (`src/data_pipeline.py`) deployed as an AWS Lambda function, which reads raw CSV files from an S3 landing bucket, validates and transforms them, and writes Parquet output to a processed bucket. Notification emails are dispatched via SendGrid upon workflow completion.

---

## 2. Health Checks

Run these checks in order to confirm all components are operational.

### 2.1 GitHub Actions Workflows

```bash
# List recent workflow runs (requires GitHub CLI)
gh run list --repo kylodeng/ai-delivery-source --limit 10

# Check a specific workflow status
gh workflow view "Tool 1 — Code Review" --repo kylodeng/ai-delivery-source
```

All five workflows should show a recent successful run:

| Workflow file | Expected trigger | Cron schedule |
|---|---|---|
| `tool1_code_review.yml` | PR open/sync, Monday 08:00 UTC | `0 8 * * 1` |
| `tool2_tech_docs.yml` | Push to `main`, Sunday 06:00 UTC | `0 6 * * 0` |
| `tool3_business_docs.yml` | `v*` tag push, manual | — |
| `tool4_auto_testing.yml` | PR on `src/**`, Wednesday 07:00 UTC | `0 7 * * 3` |
| `tool5_uat.yml` | `release/*` branch creation, manual | — |

### 2.2 AWS Lambda (Data Pipeline)

```bash
# Check Lambda function state
aws lambda get-function \
  --function-name data-ingest-dev \
  --region us-east-1 \
  --query 'Configuration.[State,LastModified,FunctionName]'

# Invoke with a test event
aws lambda invoke \
  --function-name data-ingest-dev \
  --payload '{"bucket":"capco-data-landing-dev","key":"raw/test.csv"}' \
  --region us-east-1 \
  response.json && cat response.json
```

Expected response: `{"statusCode": 200, "body": {...}}`

### 2.3 S3 Buckets

```bash
# Confirm buckets exist and are accessible
aws s3 ls s3://capco-data-landing-dev/raw/ --region us-east-1
aws s3 ls s3://capco-data-processed-dev/processed/ --region us-east-1
```

### 2.4 External API Connectivity

```bash
# Anthropic API — confirm key is valid (replace with actual key)
curl -s https://api.anthropic.com/v1/models \
  -H "x-api-key: $ANTHROPIC_API_KEY" \
  -H "anthropic-version: 2023-06-01" | jq '.models[0].id'

# SendGrid API — confirm key is valid
curl -s --request GET \
  --url https://api.sendgrid.com/v3/scopes \
  --header "Authorization: Bearer $SENDGRID_API_KEY" | jq '.scopes | length'
```

### 2.5 Output Repo Writability

```bash
# Confirm GH_TOKEN can write to output repo
curl -s -H "Authorization: Bearer $GH_TOKEN" \
  https://api.github.com/repos/kylodeng/ai-delivery-outputs \
  | jq '.permissions.push'
# Expected: true
```

---

## 3. Common Failure Scenarios

| Symptom | Likely Cause | Resolution Steps |
|---|---|---|
| Workflow fails with `KeyError: 'ANTHROPIC_API_KEY'` | Secret not set or mis-named in repo settings | Navigate to **Settings → Secrets and variables → Actions**. Add/verify `ANTHROPIC_API_KEY`. Re-run failed job. |
| Workflow fails with `KeyError: 'GH_TOKEN'` | `GH_TOKEN` secret missing or expired | Regenerate a GitHub PAT with `repo` and `contents:write` scopes. Update the `GH_TOKEN` secret. Re-run job. |
| `tool1_code_review.py` posts no PR comment | `GH_TOKEN` lacks `pull-requests:write` permission, or PR number env var is empty | Verify PAT scopes include `pull-requests`. Check `PR_NUMBER` is set in the `Set review mode` step log. |
| `json.JSONDecodeError` in any tool script | Claude returned markdown or explanation instead of raw JSON (prompt injection or model change) | Check Actions log for Claude's raw response. Confirm `MODEL = "claude-sonnet-4-6"` in `shared.py` is a valid model ID. Retry; if persistent, add explicit JSON-only enforcement to the system prompt. |
| `write_output_file` returns a 404 or 422 error | Output repo `ai-delivery-outputs` does not exist, or `OUTPUT_REPO_OWNER` env var is wrong | Create the output repo manually. Verify `OUTPUT_REPO_OWNER` matches the GitHub org/user. |
| SendGrid email not delivered | `SENDGRID_API_KEY` invalid, sender domain not verified, or `send_email` payload malformed (truncated `shared.py` suggests incomplete `send_email` function) | Check SendGrid activity feed. Verify sender domain is authenticated. Inspect `shared.py` for truncated `send_email` body — [TODO: confirm complete implementation]. |
| Lambda returns `{"statusCode": 500}` | CSV is malformed, S3 key does not exist, or AWS credentials are hardcoded and rotated | Check CloudWatch Logs (`/aws/lambda/data-ingest-dev`). Verify S3 key exists. **Urgent:** rotate and move `AWS_ACCESS_KEY`/`AWS_SECRET_KEY` out of source code into Secrets Manager (see Security Risks). |
| Lambda cannot access S3 (`AccessDenied`) | IAM role `lambda-ingest-role` policy misconfigured, or Lambda execution role not attached | Verify `aws_iam_role_policy.lambda_policy` is attached. Confirm Lambda's execution role ARN in the console. |
| S3 trigger not firing Lambda | `aws_s3_bucket_notification` not applied, or Lambda resource policy missing `s3:InvokeFunction` | Run `terraform apply` to reconcile state. Add Lambda permission: `aws lambda add-permission --function-name data-ingest-dev --action lambda:InvokeFunction --principal s3.amazonaws.com --statement-id s3-trigger`. |
| Parquet output not written to processed bucket | `s3fs` or `pyarrow` not installed in Lambda layer, or path replacement logic fails if key doesn't contain `raw/` | Check Lambda logs. Ensure `pandas` and `pyarrow` are in the deployment package. Validate S3 key naming convention follows `raw/*.csv`. |
| Tool 5 UAT workflow skips on branch create | Branch name does not match `refs/heads/release/*` pattern | Ensure release branches are named exactly `release/<version>` (e.g. `release/1.2.0`). Check `if:` condition in `tool5_uat.yml`. |
| `get_repo_files` returns empty dict | Repository tree fetch fails (rate limit, bad token, or private repo without access) | Check GitHub API rate limit: `curl -H "Authorization: Bearer $GH_TOKEN" https://api.github.com/rate_limit`. Wait for reset or use a token with higher rate limits. |
| Workflow hits Claude `max_tokens` limit and truncates output | Large repo with many files exceeds context window | Reduce `max_files` in `get_repo_files` calls. Split into smaller batches. Increase `max_tokens` parameter (currently 4096). |
| `get_all_pending_files` only returns first 1000 objects | S3 `list_objects_v2` is not paginated | [TODO: implement pagination using `ContinuationToken` in `data_pipeline.py`]. As a workaround, process files in smaller prefixed batches. |

---

## 4. Deployment Procedure

### Prerequisites

- Terraform ≥ 1.5 installed locally
- AWS CLI configured with appropriate credentials
- GitHub CLI (`gh`) installed
- Python 3.12
- Access to `kylodeng/ai-delivery-source` and `kylodeng/ai-delivery-outputs` repos

### 4.1 First-Time Setup

**Step 1 — Create the output repository**
```bash
gh repo create kylodeng/ai-delivery-outputs --public --description "AI Delivery Bot outputs"
```

**Step 2 — Set GitHub Actions secrets**

Navigate to `https://github.com/kylodeng/ai-delivery-source/settings/secrets/actions` and add:

| Secret Name | Description |
|---|---|
| `ANTHROPIC_API_KEY` | Anthropic API key |
| `GH_TOKEN` | GitHub PAT (scopes: `repo`, `contents:write`, `pull-requests:write`) |
| `SENDGRID_API_KEY` | SendGrid API key |

**Step 3 — Package the Lambda**
```bash
cd src/
pip install boto3 pandas pyarrow -t ./package/
cp data_pipeline.py ./package/
cd package && zip -r ../lambda.zip . && cd ..
mv lambda.zip ../infra/
```

**Step 4 — Deploy infrastructure**
```bash
cd infra/
terraform init
terraform plan -var="environment=dev" -out=tfplan
terraform apply tfplan
```

**Step 5 — Verify deployment**
```bash
# Confirm Lambda is active
aws lambda get-function --function-name data-ingest-dev --query 'Configuration.State'
# Expected: "Active"
```

### 4.2 Updating the Lambda Function Code

```bash
# Step 1 — Rebuild the package
cd src/
pip install boto3 pandas pyarrow -t ./package/
cp data_pipeline.py ./package/
cd package && zip -r ../lambda.zip . && cd ..
mv lambda.zip ../infra/

# Step 2 — Apply Terraform
cd infra/
terraform plan -var="environment=dev" -out=tfplan
terraform apply tfplan

# Step 3 — Confirm new version is live
aws lambda get-function --function-name data-ingest-dev \
  --query 'Configuration.LastModified'
```

### 4.3 Updating Workflow Scripts

```bash
# Edit scripts in .github/scripts/
# Commit and push to main — tool2_tech_docs workflow will auto-trigger

git add .github/scripts/
git commit -m "fix: update shared.py"
git push origin main
```

### 4.4 Rollback Steps

**Lambda rollback (to previous version):**
```bash
# List available versions
aws lambda list-versions-by-function --function-name data-ingest-dev \
  --query 'Versions[*].[Version,LastModified]' --output table

# [TODO: confirm Lambda versioning is enabled — not present in current Terraform config]
# Publish a version before each deploy to enable rollback:
aws lambda publish-version --function-name data-ingest-dev

# Rollback by updating alias or re-deploying previous lambda.zip
aws lambda update-function-code \
  --function-name data-ingest-dev \
  --zip-file fileb://lambda-previous.zip
```

**Terraform rollback:**
```bash
# Revert to previous Terraform state
git log --oneline infra/    # find previous commit
git checkout <previous-sha> -- infra/
cd infra/
terraform plan -out=rollback.tfplan
terraform apply rollback.tfplan
```

**Workflow script rollback:**
```bash
# Revert script changes via git
git revert <commit-sha>
git push origin main
```

---

## 5. Monitoring & Alerting

### 5.1 AWS CloudWatch — Lambda

| Metric | Namespace | Recommended Alarm Threshold |
|---|---|---|
| `Errors` | `AWS/Lambda` | > 0 in 5-minute window |
| `Duration` | `AWS/Lambda` | P95 > 25,000 ms (Lambda timeout is 30s) |
| `Throttles` | `AWS/Lambda` | > 0 sustained over 15 minutes |
| `ConcurrentExecutions` | `AWS/Lambda` | > 80% of regional limit |
| `DestinationDeliveryFailures` | `AWS/Lambda` | > 0 |

```bash
# View Lambda error logs (last 1 hour)
aws logs filter-log-events \
  --log-group-name /aws/lambda/data-ingest-dev \
  --start-time $(date -d '1 hour ago' +%s000) \
  --filter-pattern "ERROR"
```

### 5.2 AWS CloudWatch — S3

- Monitor `NumberOfObjects` and `BucketSizeBytes` on `capco-data-landing-dev/raw/` for unexpected growth (files not being consumed)
- [TODO: set up S3 event notification or CloudWatch metric filter for failed object puts]

### 5.3 GitHub Actions

```bash
# Monitor failed workflow runs (run daily or set up GitHub webhook)
gh run list --repo kylodeng/ai-delivery-source \
  --status failure --limit 20
```

Key log patterns to search for in Actions run output:

| Log Pattern | Meaning |
|---|---|
| `JSONDecodeError` | Claude returned non-JSON; tool output lost |
| `rate limit` | GitHub or Anthropic API rate-limited |
| `401` / `403` | Expired or insufficient-scope token |
| `KeyError` | Missing environment variable / secret |
| `Failed:` | Lambda pipeline error (bare except caught) |

### 5.4 SendGrid

- Monitor **Bounces** and **Blocks** in the SendGrid dashboard for `kylo.deng@capco.com`
- [TODO: confirm sender domain `noreply@ai-delivery.capco.com` is DKIM/SPF authenticated]

### 5.5 Known Gaps (Action Required)

> ⚠️ **No dedicated monitoring infrastructure (CloudWatch dashboards, alarms, SNS topics) is deployed by the current Terraform configuration.** The following gaps exist:

- No S3 server-side encryption on `capco-data-landing-dev` (landing bucket) — data at rest is unprotected
- No CloudWatch alarm configured for Lambda errors or duration
- No dead-letter queue (DLQ) on the Lambda function — failed invocations from S3 triggers are silently dropped
- `get_all_pending_files` lacks pagination — files beyond the first 1,000 will never be processed
- [TODO: add `aws_cloudwatch_metric_alarm` resources to `infra/main.tf`]

---

## 6. Escalation Path

| Level | Condition | Contact | Method |
|---|---|---|---|
| L1 — On-call engineer | Workflow failure, Lambda error rate > 1% | [TODO: on-call engineer name/alias] | [TODO: PagerDuty / Slack channel] |
| L2 — Platform team | Persistent API failures, secret rotation needed, IAM issues | [TODO: platform team lead] | [TODO: contact method] |
| L3 — Security team | Hardcoded credentials exposed (`AWS_ACCESS_KEY` in `data_pipeline.py`, `DB_PASSWORD` in Terraform), S3 public exposure | [TODO: security team contact] | [TODO: incident management tool] |
| L4 —