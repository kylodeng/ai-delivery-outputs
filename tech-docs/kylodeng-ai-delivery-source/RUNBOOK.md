# Operational Runbook — `kylodeng/ai-delivery-source`

> **Last updated:** [TODO: insert date on each revision]
> **Runbook owner:** [TODO: team/individual name]
> **Version:** 1.0

---

## 1. Service Overview

`kylodeng/ai-delivery-source` is a GitHub Actions–driven AI delivery platform that automates five software development lifecycle tasks using Anthropic's Claude API: automated code review (Tool 1), technical documentation generation (Tool 2), business documentation generation (Tool 3), AI-generated test suite creation and coverage gap analysis (Tool 4), and UAT test pack generation and results analysis (Tool 5). The platform reads source files from this repository, passes them to Claude (`claude-sonnet-4-6`), and writes all outputs (markdown docs, test files, CSV test packs, audit logs) to a companion repository named `ai-delivery-outputs`. Notifications are delivered via SendGrid email to `kylo.deng@capco.com`. The data pipeline component (`src/data_pipeline.py`) is deployed as an AWS Lambda function that ingests customer CSV files from an S3 landing bucket (`capco-data-landing-{env}`), validates and transforms them, and writes Parquet files to a processed bucket (`capco-data-processed-{env}`), triggered by S3 `ObjectCreated` events on the `raw/` prefix.

---

## 2. Health Checks

Run these checks in order to confirm all components are operational.

### 2.1 GitHub Actions Workflows

| Check | How to verify |
|---|---|
| All 5 workflows visible | Navigate to **Actions** tab in `kylodeng/ai-delivery-source` — confirm 5 workflows listed |
| Last run succeeded | Each workflow's last run badge shows ✅ green |
| Output repo is writable | Confirm a recent commit exists in `ai-delivery-outputs` repo |
| Secrets are set | Repo **Settings → Secrets → Actions** — confirm `ANTHROPIC_API_KEY`, `GH_TOKEN`, `SENDGRID_API_KEY` are present (values not visible — check for red ⚠️ indicators) |

### 2.2 AWS Lambda (Data Pipeline)

```bash
# Check Lambda function exists and is active
aws lambda get-function --function-name data-ingest-dev \
  --query 'Configuration.[State,LastModified,Runtime]'

# Check last invocation result (last 1 hour)
aws logs filter-log-events \
  --log-group-name /aws/lambda/data-ingest-dev \
  --start-time $(date -d '1 hour ago' +%s000) \
  --filter-pattern "statusCode"
```

### 2.3 S3 Buckets

```bash
# Confirm buckets exist and are accessible
aws s3 ls s3://capco-data-landing-dev/raw/ --summarize
aws s3 ls s3://capco-data-processed-dev/ --summarize
```

### 2.4 External API Connectivity

| API | Check |
|---|---|
| Anthropic Claude | Workflow logs show `call_claude` returning without HTTP error |
| SendGrid | Confirm delivery receipt in `kylo.deng@capco.com` inbox after any tool run |
| GitHub API | `GET https://api.github.com/repos/{owner}/ai-delivery-outputs` returns HTTP 200 with `GH_TOKEN` |

---

## 3. Common Failure Scenarios

| Symptom | Likely Cause | Resolution Steps |
|---|---|---|
| Workflow fails with `KeyError: 'ANTHROPIC_API_KEY'` | Secret not set or misspelled in repository secrets | Go to repo **Settings → Secrets → Actions** → add/correct `ANTHROPIC_API_KEY` |
| Workflow fails with `KeyError: 'GH_TOKEN'` | `GH_TOKEN` secret missing or token expired/revoked | Regenerate a GitHub PAT with `repo` and `contents:write` scopes; update `GH_TOKEN` secret |
| Workflow fails with `KeyError: 'SENDGRID_API_KEY'` | SendGrid secret missing | Add `SENDGRID_API_KEY` to repository secrets |
| `call_claude` raises HTTP 401 / 403 | Anthropic API key invalid or expired | Rotate the Anthropic API key and update `ANTHROPIC_API_KEY` secret |
| `call_claude` raises HTTP 429 | Anthropic rate limit exceeded | Wait and re-trigger the workflow; consider adding retry logic [TODO: implement exponential backoff in `shared.py`] |
| `write_output_file` returns 404 or 422 | `ai-delivery-outputs` repo does not exist or `GH_TOKEN` lacks write access | Create the repo if missing; confirm PAT has `contents:write` permission on the output repo |
| Claude response fails JSON parse in Tool 1 | Claude returned malformed JSON or wrapped in markdown fences | Check `[DEBUG]` lines in workflow logs; re-trigger — the `extract_json` function handles most fences; if persistent, review prompt in `SYSTEM` constant |
| Lambda invocation timeout | CSV file too large / `process_csv` takes >30s | Increase Lambda `timeout` in `infra/main.tf` (current: 30s); consider chunking large files |
| Lambda returns `statusCode: 500` | Exception in `process_csv` or `validate_customer_record` | Check CloudWatch log group `/aws/lambda/data-ingest-{env}` for traceback |
| S3 trigger not firing Lambda | S3 bucket notification not configured or Lambda permission missing | Run `terraform apply` to reconcile; check `aws lambda get-policy --function-name data-ingest-dev` for S3 invoke permission |
| `get_all_pending_files` returns empty list | No `.csv` files in `raw/` prefix, or bucket name wrong | Confirm files were uploaded to correct bucket and prefix; check `LANDING_BUCKET` env var on Lambda |
| `result_df.to_parquet` fails | `pyarrow`/`fastparquet` not in Lambda deployment package | Rebuild `lambda.zip` with pandas parquet dependencies included |
| Email not received | SendGrid API key invalid, sender domain not verified, or `NOTIFY_EMAIL` wrong | Check SendGrid dashboard for delivery errors; verify `noreply@ai-delivery.capco.com` is an authenticated sender |
| Tool 2 generates empty docs (`_No files found_`) | `get_repo_files` returned nothing — wrong extensions or repo has no matching files | Confirm source repo has `.py`, `.tf`, `.yaml` etc.; check `GH_TOKEN` can read the source repo |
| Tool 5 UAT workflow skips on branch creation | Branch name does not match `refs/heads/release/*` pattern | Ensure release branches are named `release/x.y.z` — the `if:` condition filters on this prefix |
| Terraform apply fails with credential error | AWS credentials not configured in the deploying environment | Export `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` (or use IAM role) before running Terraform |
| Hardcoded AWS keys in `data_pipeline.py` rejected | Boto3 uses env vars or instance role, not hardcoded keys, OR keys are invalid | **Immediately rotate the example keys**; remove hardcoded credentials; use IAM execution role or `AWS_*` environment variables |

---

## 4. Deployment Procedure

### Prerequisites

- AWS CLI configured with sufficient permissions (`iam:*`, `s3:*`, `lambda:*` in the target account)
- Terraform ≥ 1.5 installed
- Python 3.12 installed locally
- `lambda.zip` built and present (see step 3 below)
- GitHub PAT with `repo`, `contents:write` scopes stored as `GH_TOKEN`

---

### 4.1 Infrastructure Deployment (Terraform)

```bash
# Step 1 — Initialise Terraform
cd infra/
terraform init

# Step 2 — Review the plan
terraform plan -var="environment=prod"

# Step 3 — Build Lambda deployment package (from repo root)
cd ..
pip install -r requirements.txt -t lambda_package/
cp src/data_pipeline.py lambda_package/
cd lambda_package && zip -r ../infra/lambda.zip . && cd ..

# Step 4 — Apply infrastructure
cd infra/
terraform apply -var="environment=prod"

# Step 5 — Confirm outputs
terraform output
```

**Expected outputs:**
```
landing_bucket   = "capco-data-landing-prod"
processed_bucket = "capco-data-processed-prod"
```

---

### 4.2 GitHub Workflows Deployment

The workflows deploy automatically when `.github/workflows/*.yml` files are merged to `main`. No manual step is needed beyond merging.

```bash
# Verify all 5 workflows are registered after merge
gh workflow list --repo kylodeng/ai-delivery-source
```

---

### 4.3 Secrets Configuration (one-time / rotation)

```bash
# Set each secret via GitHub CLI
gh secret set ANTHROPIC_API_KEY  --repo kylodeng/ai-delivery-source
gh secret set GH_TOKEN           --repo kylodeng/ai-delivery-source
gh secret set SENDGRID_API_KEY   --repo kylodeng/ai-delivery-source
```

---

### 4.4 Rollback Steps

#### Rollback — Terraform infrastructure

```bash
# Option A: Revert to previous Terraform state (if remote state is used)
# [TODO: confirm whether remote state backend (S3/Terraform Cloud) is configured]
terraform apply -var="environment=prod" -target=<resource_to_revert>

# Option B: Destroy and re-apply from last known good tag
git checkout <last-good-tag>
cd infra/
terraform apply -var="environment=prod"
```

#### Rollback — Lambda function only

```bash
# List available versions
aws lambda list-versions-by-function --function-name data-ingest-prod

# Rollback to previous version (replace VERSION)
aws lambda update-alias \
  --function-name data-ingest-prod \
  --name live \
  --function-version <PREVIOUS_VERSION>
```

> ⚠️ **Note:** Lambda versioning/aliases are not currently configured in `infra/main.tf`. [TODO: add `publish = true` and an alias resource to enable clean rollbacks.]

#### Rollback — GitHub workflow scripts

```bash
# Revert the offending commit on main
git revert <bad-commit-sha>
git push origin main
```

---

## 5. Monitoring & Alerting

### 5.1 GitHub Actions

| What to watch | Where |
|---|---|
| Workflow run status (pass/fail) | **Actions** tab → each workflow's run history |
| Failed step logs | Click failed run → expand failed step → download raw logs |
| Weekly code review cron (Mondays 08:00 UTC) | Tool 1 workflow scheduled run |
| Weekly docs cron (Sundays 06:00 UTC) | Tool 2 workflow scheduled run |
| Wednesday test gen cron (07:00 UTC) | Tool 4 workflow scheduled run |

[TODO: configure GitHub Actions notifications or webhook to a Slack/Teams channel for workflow failures]

### 5.2 AWS CloudWatch — Lambda

```bash
# Key metrics to create alarms on:
# - Errors > 0 in 5-minute window
# - Duration approaching timeout (>25000ms out of 30000ms limit)
# - Throttles > 0

aws cloudwatch put-metric-alarm \
  --alarm-name "data-ingest-errors-prod" \
  --namespace AWS/Lambda \
  --metric-name Errors \
  --dimensions Name=FunctionName,Value=data-ingest-prod \
  --statistic Sum \
  --period 300 \
  --threshold 1 \
  --comparison-operator GreaterThanOrEqualToThreshold \
  --evaluation-periods 1 \
  --alarm-actions <SNS_TOPIC_ARN>
```

**Recommended CloudWatch alarms:**

| Metric | Threshold | Action |
|---|---|---|
| `Lambda/Errors` | ≥ 1 in 5 min | Page on-call |
| `Lambda/Duration` | > 25,000 ms | Warn — approaching timeout |
| `Lambda/Throttles` | ≥ 1 | Warn — increase concurrency |
| `Lambda/ConcurrentExecutions` | [TODO: set based on expected load] | Scale alert |

### 5.3 CloudWatch Log Queries

```bash
# Find all failed pipeline runs in the last 24 hours
aws logs filter-log-events \
  --log-group-name /aws/lambda/data-ingest-prod \
  --start-time $(date -d '24 hours ago' +%s000) \
  --filter-pattern "statusCode: 500"

# Find validation failures
aws logs filter-log-events \
  --log-group-name /aws/lambda/data-ingest-prod \
  --start-time $(date -d '24 hours ago' +%s000) \
  --filter-pattern "Failed"
```

### 5.4 S3 Monitoring

- Monitor `capco-data-landing-{env}/raw/` for files that are older than [TODO: define SLA age, e.g., 1 hour] and have not moved to `processed/` — this indicates a stuck or failed pipeline.
- [TODO: add S3 CloudWatch metrics or S3 Storage Lens for object count monitoring]

### 5.5 Key Log Strings to Alert On

| Log string | Meaning |
|---|---|
| `statusCode": 500` | Lambda invocation failed |
| `Failed:` | Caught exception in `lambda_handler` |
| `Could not parse Claude response as JSON` | Tool 1 JSON parsing failed |
| `[DEBUG] JSON parse error` | Claude returned unexpected format |
| `Missing required field` | Customer record validation failure |

### 5.6 Audit Log

[TODO: confirm where `write_audit_entry` writes its output — the function is referenced in scripts but not shown in the provided `shared.py` snippet. Identify the audit log destination (file, S3, CloudWatch Logs) and add monitoring.]

---

## 6. Escalation Path

| Level | Who | When to escalate | Contact |
|---|---|---|---|
| L1 — First response | On-call engineer | Workflow failure, Lambda errors, S3 issues | [TODO: on-call roster / PagerDuty link] |
| L2 — Engineering lead | Platform team lead | Repeated failures, data loss, security events | [TODO: name & contact] |
| L3 — Security | Security team | Hardcoded credential exposure, IAM breach, S3 data leak | [TODO: security team contact / incident email] |
| L4 — Vendor support | Anthropic | Claude API outage (check https://status.anthropic.com) | [TODO: Anthropic support contract details] |
| L4 — Vendor support | SendGrid | Email delivery failures persisting >30 min | [TODO: SendGrid support link] |
| Business escalation | Product owner / Capco delivery lead | Go/no-go decisions, release blockers | [TODO: name & contact] |

> ⚠️ **Security note:** Hardcoded AWS credentials exist in `src/data_pipeline.py` and a plaintext `DB_PASSWORD` is set in `infra/main.tf`. If these are real credentials, treat this as a **P1 security incident** and rotate immediately. Notify the security team.

---

## 7. Useful Commands

### GitHub Actions — Trigger Workflows Manually

```bash
# Tool 1 — Code review on a specific PR
gh workflow run tool1_code_review.yml \
  --repo kylodeng/ai-delivery-source \
  -f review_mode=pr \
  -f pr_number=42

# Tool 1 — Full repo review
gh workflow run tool1_code_review.yml \
  --repo kylodeng/ai-delivery-source \
  -f review_mode=repo

# Tool 2 — Regenerate tech docs
gh workflow run tool2_tech_docs.yml \
  --repo kylodeng/ai-delivery-source

# Tool 3 — Generate business docs for a release
gh workflow run tool3_business_docs.yml \
  --repo kylodeng/ai-delivery-source \
  -f project_name="Data Ingestion Pipeline" \
  -f release_version="1.2.0"

# Tool 4 —