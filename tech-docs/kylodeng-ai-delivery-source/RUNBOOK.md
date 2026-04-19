# Operational Runbook — `kylodeng/ai-delivery-source`

> **Version:** 1.0 | **Last updated:** [TODO: insert date] | **Status:** Draft

---

## 1. Service Overview

`kylodeng/ai-delivery-source` is a GitHub Actions–driven AI delivery platform that combines five automated workflow tools — code review, technical documentation generation, business documentation generation, automated test generation, and UAT facilitation — all powered by the Anthropic Claude API (model `claude-sonnet-4-6`). The platform is triggered by standard GitHub events (pull requests, merges to `main`, version tags, release branch creation, and scheduled crons) and orchestrates calls to the Claude API, the GitHub API, and SendGrid to produce outputs written to a companion repository (`ai-delivery-outputs`) and optionally notified via email. The data pipeline component (`src/data_pipeline.py`) runs as an AWS Lambda function, ingesting customer CSV files from an S3 landing bucket (`capco-data-landing-<env>`), validating and transforming them to Parquet, and writing results to a processed bucket (`capco-data-processed-<env>`).

---

## 2. Health Checks

### GitHub Actions Workflows

| Check | How to verify |
|---|---|
| Workflows are enabled | Navigate to **Actions** tab in `kylodeng/ai-delivery-source`; confirm all 5 workflows are listed and not disabled |
| Secrets are present | **Settings → Secrets and variables → Actions**; confirm `ANTHROPIC_API_KEY`, `GH_TOKEN`, `SENDGRID_API_KEY` exist |
| Output repo is accessible | Confirm `ai-delivery-outputs` repo exists under the same owner and the `GH_TOKEN` has write access |
| Scheduled jobs firing | Check the last run timestamps for the Monday 08:00 (Tool 1), Sunday 06:00 (Tool 2), Wednesday 07:00 (Tool 4) crons |

### AWS Lambda (Data Pipeline)

| Check | How to verify |
|---|---|
| Lambda function deployed | `aws lambda get-function --function-name data-ingest-<env>` returns `State: Active` |
| S3 landing bucket exists | `aws s3 ls s3://capco-data-landing-<env>/` returns without error |
| S3 processed bucket exists | `aws s3 ls s3://capco-data-processed-<env>/` returns without error |
| S3 event trigger configured | `aws s3api get-bucket-notification-configuration --bucket capco-data-landing-<env>` shows `LambdaFunctionConfigurations` |
| Lambda responds to test event | See [Useful Commands](#7-useful-commands) |
| CloudWatch log group active | `aws logs describe-log-groups --log-group-name-prefix /aws/lambda/data-ingest` |

### External API Dependencies

| Dependency | Check |
|---|---|
| Anthropic API | Confirm `ANTHROPIC_API_KEY` is valid and not rate-limited; check [status.anthropic.com](https://status.anthropic.com) |
| SendGrid | Confirm `SENDGRID_API_KEY` is active; check [status.sendgrid.com](https://status.sendgrid.com) |
| GitHub API | `curl -H "Authorization: Bearer $GH_TOKEN" https://api.github.com/rate_limit` — confirm remaining > 100 |

---

## 3. Common Failure Scenarios

| Symptom | Likely Cause | Resolution Steps |
|---|---|---|
| Workflow fails with `KeyError: 'ANTHROPIC_API_KEY'` | Secret not set or not passed to job env | 1. Go to **Settings → Secrets → Actions**. 2. Add/rotate `ANTHROPIC_API_KEY`. 3. Re-run failed workflow. |
| Workflow fails with `KeyError: 'GH_TOKEN'` | PAT missing, expired, or lacks repo write scope | 1. Generate new PAT with `repo` and `workflow` scopes. 2. Update `GH_TOKEN` secret. 3. Re-run workflow. |
| `write_output_file` returns 404 or 422 | `ai-delivery-outputs` repo does not exist or token lacks write access | 1. Create `ai-delivery-outputs` repo under the same owner. 2. Confirm `GH_TOKEN` has `contents:write` on that repo. |
| Claude returns non-JSON / parse error | Model returned markdown-wrapped JSON or hallucinated format | 1. Check workflow logs for `[DEBUG] First 500 chars`. 2. Increase `max_tokens` if truncated. 3. Re-run — transient in most cases. |
| SendGrid email not delivered | Invalid `SENDGRID_API_KEY` or sender domain not verified | 1. Verify sender `noreply@ai-delivery.capco.com` in SendGrid. 2. Rotate `SENDGRID_API_KEY` secret. |
| Tool 1 PR comment not posted | `GH_TOKEN` lacks `pull-requests:write` permission | 1. Confirm token scopes. 2. For fine-grained PAT, add Pull Requests read/write. |
| Lambda returns `500` / `Failed: ...` | Malformed CSV, missing `key` in event, or S3 permissions error | 1. Check CloudWatch logs: `/aws/lambda/data-ingest-<env>`. 2. Verify S3 IAM policy. 3. Test with synthetic event (see commands). |
| Lambda cannot read from S3 | Hardcoded credentials (`AWS_ACCESS_KEY` in code) are invalid | **URGENT** — 1. Rotate exposed credentials immediately. 2. Remove hardcoded keys from `data_pipeline.py`. 3. Attach IAM role with least-privilege S3 access. 4. Redeploy Lambda. |
| `list_objects_v2` returns truncated results | No pagination implemented; bucket has >1000 files | 1. Implement paginator (see commands). 2. As workaround, process files in batches manually. |
| Parquet write fails | `s3fs`/`pyarrow` not installed in Lambda layer, or output path collision | 1. Check Lambda layer includes `pandas`, `pyarrow`, `s3fs`. 2. Confirm `processed/` prefix has write permissions. |
| Scheduled workflow never triggers | Repository is inactive (GitHub disables schedules after 60 days of inactivity) | 1. Make a trivial commit or re-enable the workflow from the Actions tab. |
| Tool 5 UAT workflow skipped on branch creation | Branch name does not match `refs/heads/release/*` | 1. Ensure branch is named `release/<version>` exactly. 2. Use manual `workflow_dispatch` as fallback. |
| `get_repo_files` returns empty dict | Repo tree fetch fails due to large repo or auth error | 1. Check GitHub API rate limit. 2. Reduce `max_files` parameter. 3. Confirm `GH_TOKEN` has read access to source repo. |
| Terraform apply fails | Missing variable values or AWS credentials not configured | 1. Run `terraform plan` first. 2. Export `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` or use assumed role. 3. Review error output. |
| S3 landing bucket data exposed | No public access block and no encryption on `aws_s3_bucket.landing` | **SECURITY ISSUE** — 1. Apply `aws_s3_bucket_public_access_block`. 2. Add `aws_s3_bucket_server_side_encryption_configuration`. 3. Run `terraform apply`. |

---

## 4. Deployment Procedure

### 4.1 Prerequisites

- AWS CLI configured with appropriate credentials
- Terraform ≥ 1.0 installed
- Python 3.12 installed locally (for packaging Lambda)
- GitHub PAT with `repo` + `workflow` scopes set as `GH_TOKEN`

### 4.2 Infrastructure Deployment (Terraform)

```bash
# Step 1 — Package Lambda function
cd src/
zip -r ../infra/lambda.zip data_pipeline.py
cd ../infra/

# Step 2 — Initialise Terraform
terraform init

# Step 3 — Review plan
terraform plan -var="environment=prod"

# Step 4 — Apply
terraform apply -var="environment=prod"

# Step 5 — Confirm outputs
terraform output landing_bucket
terraform output processed_bucket
```

### 4.3 Deploying Updated Lambda Code

```bash
# Step 1 — Repackage
cd src/
zip -r ../infra/lambda.zip data_pipeline.py

# Step 2 — Update function code
aws lambda update-function-code \
  --function-name data-ingest-prod \
  --zip-file fileb://../infra/lambda.zip

# Step 3 — Wait for update to complete
aws lambda wait function-updated \
  --function-name data-ingest-prod

# Step 4 — Smoke test
aws lambda invoke \
  --function-name data-ingest-prod \
  --payload '{"bucket":"capco-data-landing-prod","key":"raw/test.csv"}' \
  response.json && cat response.json
```

### 4.4 Deploying GitHub Actions Workflow Changes

```bash
# Step 1 — Create a feature branch
git checkout -b fix/workflow-update

# Step 2 — Make changes to .github/workflows/ or .github/scripts/

# Step 3 — Open a PR (triggers Tool 1 code review automatically)
git push origin fix/workflow-update
gh pr create --title "Update workflow" --body "Description of change"

# Step 4 — Merge to main after review (triggers Tool 2 doc generation)
gh pr merge --squash
```

### 4.5 Rollback Steps

#### Lambda Rollback

```bash
# List available versions
aws lambda list-versions-by-function --function-name data-ingest-prod

# Roll back to previous version (replace $VERSION)
aws lambda update-alias \
  --function-name data-ingest-prod \
  --name live \
  --function-version $VERSION

# OR re-deploy previous zip from git tag
git checkout tags/v<previous-version> -- src/data_pipeline.py
cd src && zip -r ../infra/lambda.zip data_pipeline.py
aws lambda update-function-code \
  --function-name data-ingest-prod \
  --zip-file fileb://../infra/lambda.zip
```

> **Note:** Lambda versioning is not currently configured in `infra/main.tf`. [TODO: Should Lambda versioning and aliases be enabled for safe rollbacks?]

#### Terraform Rollback

```bash
# Restore previous state from backup (if state versioning enabled)
terraform state pull > backup.tfstate

# Roll back to previous git revision of .tf files
git checkout HEAD~1 -- infra/main.tf
terraform plan -var="environment=prod"
terraform apply -var="environment=prod"
```

#### Workflow Script Rollback

```bash
# Revert the offending commit
git revert <commit-sha>
git push origin main
```

---

## 5. Monitoring & Alerting

### 5.1 AWS Lambda / Data Pipeline

| Metric / Log | Where | Alert threshold |
|---|---|---|
| Lambda invocation errors | CloudWatch Metrics → `AWS/Lambda` → `Errors` | Alert if errors > 0 in any 5-min window |
| Lambda duration | CloudWatch Metrics → `AWS/Lambda` → `Duration` | Alert if P99 > 25,000 ms (timeout is 30 s) |
| Lambda throttles | CloudWatch Metrics → `AWS/Lambda` → `Throttles` | Alert on any throttle |
| Failed record count | Application log: `"failed_rows"` in Lambda response body | [TODO: define acceptable failed-row threshold] |
| S3 `PutObject` errors | CloudWatch Metrics → `AWS/S3` → `5xxErrors` | Alert if > 0 |
| CloudWatch Logs | `/aws/lambda/data-ingest-<env>` | Filter for `ERROR` and `Failed:` patterns |
| Lambda log filter (errors) | `aws logs filter-log-events --log-group /aws/lambda/data-ingest-prod --filter-pattern "ERROR"` | [TODO: connect to PagerDuty or SNS alarm] |

> ⚠️ **No CloudWatch alarms are currently defined in the Terraform.** [TODO: Should CloudWatch alarms and an SNS topic for alerting be added to `infra/main.tf`?]

### 5.2 GitHub Actions Workflows

| Signal | Where to check |
|---|---|
| Workflow failure | **Actions** tab → filter by status `failure`; GitHub sends email to repo admin by default |
| API rate limit exhaustion | Workflow logs — `requests` calls will return 403/429; check `X-RateLimit-Remaining` header |
| Anthropic API errors | Workflow logs — look for `anthropic.APIError` or HTTP 529 (overloaded) |
| SendGrid delivery failures | Workflow logs — look for non-2xx responses from `api.sendgrid.com` |
| Output repo write failures | Workflow logs — look for 4xx from `api.github.com/repos/.../contents/` |

### 5.3 Security-Specific Monitoring

| Risk | Current status | Recommended action |
|---|---|---|
| Hardcoded AWS credentials in `data_pipeline.py` | ⛔ **ACTIVE RISK** | Rotate `AKIAIOSFODNN7EXAMPLE` immediately; enable AWS IAM Access Analyzer |
| Hardcoded `DB_PASSWORD` in Terraform Lambda env | ⛔ **ACTIVE RISK** | Move to AWS Secrets Manager or SSM Parameter Store |
| S3 landing bucket has no encryption | ⛔ **ACTIVE RISK** | Add `aws_s3_bucket_server_side_encryption_configuration` |
| IAM role has `s3:*` on `*` | ⚠️ Overly permissive | Scope to specific bucket ARNs and required actions only |
| No S3 public access block | ⚠️ Risk | Add `aws_s3_bucket_public_access_block` resource |

---

## 6. Escalation Path

```
Level 1 — On-call Engineer
  [TODO: name, Slack handle, phone]

Level 2 — Platform / DevOps Lead
  [TODO: name, Slack handle, phone]

Level 3 — Engineering Manager
  [TODO: name, email, phone]

Security incidents (hardcoded secrets, data exposure):
  [TODO: Security team contact / CISO escalation process]

External vendor support:
  Anthropic Support  → https://support.anthropic.com
  SendGrid Support   → https://support.sendgrid.com
  AWS Support        → https://console.aws.amazon.com/support/

Notify: kylo.deng@capco.com for all P1 incidents
```

---

## 7. Useful Commands

### GitHub API — Check rate limit

```bash
curl -s -H "Authorization: Bearer $GH_TOKEN" \
  https://api.github.com/rate_limit | jq '.rate'
```

### Manually trigger a workflow

```bash
# Trigger Tool 1 (code review) in repo mode
gh workflow run tool1_code_review.yml \
  -f review_mode=repo \
  --repo kylodeng/ai-delivery-source

# Trigger Tool 2 (tech docs)
gh workflow run tool2_tech_docs.yml \
  --repo kylodeng/ai-delivery-source

# Trigger Tool 5 UAT (generate mode)
gh workflow run tool5_uat.yml \
  -f uat_mode=generate \
  -f release_version=1.0.0 \
  --repo kylodeng/ai-delivery-source
```

### Invoke Lambda with a test event

```bash
aws lambda invoke \
  --function-name data-ingest-dev \
  --payload '{"bucket":"capco-data-landing-dev","key":"raw/sample.csv"}' \
  --cli-binary-format raw-in-base64-out \
  response.json
cat response.json
```

### Tail Lambda logs in real time

```bash
aws logs tail /aws/lambda/data-ingest-dev \
  --follow \
  --filter-pattern "ERROR"
```

### Filter Lambda logs for errors (last 1 hour)

```bash
aws logs filter-log-events \
  --