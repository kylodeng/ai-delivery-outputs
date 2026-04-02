# Operational Runbook — `kylodeng/ai-delivery-source`

> **Last updated:** [TODO: insert date]
> **Owner:** [TODO: fill in team contacts]
> **Repo:** `kylodeng/ai-delivery-source`
> **Output repo:** `kylodeng/ai-delivery-outputs`

---

## 1. Service Overview

`kylodeng/ai-delivery-source` is a GitHub Actions–driven AI delivery platform that automates five software-development lifecycle tasks using Anthropic's Claude API (`claude-sonnet-4-6`): automated code review (Tool 1), technical documentation generation (Tool 2), business documentation drafting (Tool 3), test file generation and coverage-gap analysis (Tool 4), and UAT test pack creation and defect analysis (Tool 5). Each tool is implemented as a Python script under `.github/scripts/` and orchestrated by a corresponding GitHub Actions workflow. All generated artefacts are committed to a companion repository (`ai-delivery-outputs`) and optionally emailed via SendGrid to `kylo.deng@capco.com`. The data plane of the source system is an AWS Lambda function (`data-ingest-<env>`) that reads customer CSV files from an S3 landing bucket (`capco-data-landing-<env>`), validates and transforms them to Parquet, and writes results to a processed bucket (`capco-data-processed-<env>`), triggered automatically by S3 object-creation events.

---

## 2. Health Checks

### 2.1 GitHub Actions Workflows

| Check | How to verify |
|---|---|
| Workflows are enabled | GitHub UI → Actions tab → confirm all 5 workflows are listed and not disabled |
| Most recent run status | GitHub UI → Actions → check last run of each workflow is green |
| Secrets are present | Repo → Settings → Secrets and variables → Actions → confirm `ANTHROPIC_API_KEY`, `GH_TOKEN`, `SENDGRID_API_KEY` exist |
| Output repo is writable | Trigger Tool 2 manually (`workflow_dispatch`) and confirm a commit appears in `ai-delivery-outputs` |

### 2.2 AWS Lambda (Data Pipeline)

```bash
# Check Lambda function exists and is active
aws lambda get-function --function-name data-ingest-dev \
  --query 'Configuration.[State,LastUpdateStatus]'

# Check last invocation metrics (past 1 hour)
aws cloudwatch get-metric-statistics \
  --namespace AWS/Lambda \
  --metric-name Invocations \
  --dimensions Name=FunctionName,Value=data-ingest-dev \
  --start-time $(date -u -d '1 hour ago' +%FT%TZ) \
  --end-time $(date -u +%FT%TZ) \
  --period 3600 \
  --statistics Sum
```

### 2.3 S3 Buckets

```bash
# Confirm buckets exist and are accessible
aws s3 ls s3://capco-data-landing-dev/
aws s3 ls s3://capco-data-processed-dev/

# Check for unprocessed files stuck in raw/
aws s3 ls s3://capco-data-landing-dev/raw/ --recursive | grep '\.csv'
```

### 2.4 External API Connectivity

```bash
# Verify Anthropic API key is valid (from a runner or bastion with env set)
curl -s https://api.anthropic.com/v1/models \
  -H "x-api-key: $ANTHROPIC_API_KEY" \
  -H "anthropic-version: 2023-06-01" | jq '.models[0].id'

# Verify SendGrid key
curl -s --request GET \
  --url https://api.sendgrid.com/v3/scopes \
  --header "Authorization: Bearer $SENDGRID_API_KEY" | jq '.scopes[0]'
```

---

## 3. Common Failure Scenarios

| Symptom | Likely Cause | Resolution Steps |
|---|---|---|
| GitHub Actions workflow fails at "Install dependencies" | `pip install anthropic requests` network timeout or PyPI outage | Re-run the job; if persistent, pin package versions in the `run` step |
| `KeyError: 'ANTHROPIC_API_KEY'` in workflow logs | Secret not set or mis-named in repo settings | Go to Settings → Secrets → Actions → verify/re-add `ANTHROPIC_API_KEY` |
| `KeyError: 'GH_TOKEN'` | `GH_TOKEN` secret missing or expired token | Rotate PAT with `repo` scope; update secret in repo settings |
| Claude returns non-JSON response, `ValueError: Could not parse Claude response as JSON` | Model returned markdown-fenced or truncated output | Increase `max_tokens` in `call_claude()`; check `MODEL` value is valid; retry the workflow run |
| Tool 1 posts no PR comment | `GH_TOKEN` lacks `pull-requests: write` permission, or PR number not passed correctly | Verify token scopes; check `PR_NUMBER` env var is set; inspect Actions logs for HTTP 403 |
| Tool 2/3/4/5 fails to write to `ai-delivery-outputs` | `OUTPUT_REPO_OWNER` is empty; token lacks write access to output repo | Confirm `OUTPUT_REPO_OWNER` env var resolves correctly; grant `GH_TOKEN` write access to the output repo |
| Lambda: `errorMessage: 'key'` / missing `key` in event | S3 event notification malformed or manually triggered event missing `key` field | Verify S3 bucket notification config in Terraform; test with a correctly structured event JSON |
| Lambda: `NoCredentialsError` | Hardcoded AWS credentials in `data_pipeline.py` are invalid/revoked | **Immediately** rotate and revoke `AKIAIOSFODNN7EXAMPLE` credentials; replace with IAM role-based auth (remove hardcoded keys) |
| Lambda: CSV files not triggering Lambda | S3 notification not applied; Lambda permission missing | Run `terraform apply`; verify `aws_lambda_permission` resource exists for S3 [TODO: this resource is absent from `main.tf` — was it intentionally omitted?] |
| Lambda: `ParquetError` or `pd.read_csv` exception | Malformed CSV file in landing bucket | Inspect the failing file: `aws s3 cp s3://capco-data-landing-dev/raw/<file>.csv .`; fix or quarantine the file |
| Lambda: `list_objects_v2` returns truncated results | More than 1,000 objects in `raw/` prefix; pagination not implemented | Paginate manually (see Useful Commands); implement `ContinuationToken` pagination in `get_all_pending_files()` |
| Email notification not received | SendGrid key invalid, sender domain not verified, or `NOTIFY_EMAIL` wrong | Check SendGrid activity feed; verify sender domain; confirm `SENDER_EMAIL` is an authorised sender |
| Tool 5 UAT workflow does not trigger on branch creation | Branch name does not match `release/*` pattern | Ensure release branches are named `release/<version>` e.g. `release/1.2.0` |
| Workflow scheduled runs not firing | Repo has had no activity for 60 days (GitHub disables cron on inactive repos) | Trigger any workflow manually to re-enable; commit a no-op change |

---

## 4. Deployment Procedure

### 4.1 GitHub Actions / Python Scripts

The CI/CD tooling is deployed by simply merging to `main`; there is no separate deployment step. Changes take effect on the next workflow trigger.

**To update a script or workflow:**

```bash
# 1. Clone the repo
git clone https://github.com/kylodeng/ai-delivery-source.git
cd ai-delivery-source

# 2. Create a feature branch
git checkout -b fix/my-change

# 3. Make changes, then commit
git add .github/scripts/tool1_code_review.py
git commit -m "fix: improve JSON parsing robustness"

# 4. Push and open a PR
git push origin fix/my-change
# → Tool 1 (Code Review) will trigger automatically on PR open

# 5. Merge to main after review
# → Tool 2 (Tech Docs) will trigger automatically on merge to main
```

**Rollback steps (workflow/script change):**

```bash
# Option A: revert the merge commit
git revert <merge-commit-sha>
git push origin main

# Option B: re-run the last known-good workflow run
# GitHub UI → Actions → select the last green run → Re-run all jobs
```

---

### 4.2 AWS Infrastructure (Terraform)

**Prerequisites:**

- AWS CLI configured with credentials that have sufficient IAM permissions
- Terraform ≥ 1.0 installed
- Lambda deployment package `lambda.zip` built from `src/data_pipeline.py`

**Deploy:**

```bash
# 1. Build the Lambda zip
cd src
pip install boto3 pandas pyarrow -t ./package
cp data_pipeline.py ./package/
cd package && zip -r ../../infra/lambda.zip . && cd ../..

# 2. Initialise Terraform (first time only)
cd infra
terraform init

# 3. Review the plan
terraform plan -var="environment=dev"

# 4. Apply
terraform apply -var="environment=dev"
# Type 'yes' when prompted

# 5. Confirm Lambda is live
aws lambda get-function --function-name data-ingest-dev
```

**Rollback steps (infrastructure):**

```bash
# Option A: destroy and re-apply previous version
cd infra
git checkout <last-known-good-commit> -- main.tf
terraform apply -var="environment=dev"

# Option B: destroy the environment entirely (CAUTION: deletes S3 buckets)
terraform destroy -var="environment=dev"
# Note: S3 buckets must be emptied before destroy will succeed
aws s3 rm s3://capco-data-landing-dev --recursive
aws s3 rm s3://capco-data-processed-dev --recursive
terraform destroy -var="environment=dev"
```

> ⚠️ **Before any production deployment:** resolve the hardcoded credentials (`AWS_ACCESS_KEY`, `AWS_SECRET_KEY` in `data_pipeline.py`) and the hardcoded `DB_PASSWORD` in `main.tf`. These are critical security defects.

---

## 5. Monitoring & Alerting

### 5.1 GitHub Actions

| What to watch | Where |
|---|---|
| Workflow run failures | GitHub UI → Actions tab; optionally set up GitHub email/Slack notifications via repository webhooks |
| Workflow run duration (regression in Claude latency) | Actions → workflow → job timings |
| Artifact retention | `code-review-<run_id>` artifacts expire per repo retention policy [TODO: what is the configured retention period?] |
| Rate-limit errors from Anthropic or GitHub APIs | Grep workflow logs for `429`, `RateLimitError` |

### 5.2 AWS Lambda & S3

**CloudWatch Metrics to watch:**

| Metric | Namespace | Threshold / Action |
|---|---|---|
| `Errors` | `AWS/Lambda` | > 0 in 5 min → alert |
| `Duration` | `AWS/Lambda` | > 25,000 ms (close to 30 s timeout) → alert |
| `Throttles` | `AWS/Lambda` | > 0 → alert |
| `ConcurrentExecutions` | `AWS/Lambda` | [TODO: set based on expected load] |
| S3 `NumberOfObjects` (raw/) | `AWS/S3` | Rising without corresponding processed/ growth → stuck files |
| S3 `BucketSizeBytes` | `AWS/S3` | [TODO: set size threshold] |

**Log groups to watch:**

```
/aws/lambda/data-ingest-dev    ← Lambda execution logs
```

Key log patterns to alert on:

```
"Failed:"                  # Lambda error path
"Missing required field"   # Validation failures
"Invalid email"            # Data quality issues
"Age out of range"         # Data quality issues
"errorMessage"             # Unhandled exceptions
```

**Suggested CloudWatch alarm (example):**

```bash
aws cloudwatch put-metric-alarm \
  --alarm-name "lambda-ingest-errors-dev" \
  --metric-name Errors \
  --namespace AWS/Lambda \
  --dimensions Name=FunctionName,Value=data-ingest-dev \
  --statistic Sum \
  --period 300 \
  --evaluation-periods 1 \
  --threshold 1 \
  --comparison-operator GreaterThanOrEqualToThreshold \
  --alarm-actions [TODO: SNS topic ARN] \
  --treat-missing-data notBreaching
```

### 5.3 Audit Log

[TODO: `write_audit_entry()` is imported in all scripts but its implementation is not present in the provided files — confirm where audit logs are written (local file? S3? CloudWatch?) and add the log location here.]

---

## 6. Escalation Path

| Level | Condition | Contact |
|---|---|---|
| L1 — On-call engineer | Workflow failure, Lambda errors, stuck files in `raw/` | [TODO: on-call engineer name / PagerDuty rotation] |
| L2 — Platform/DevOps lead | Persistent infrastructure failures, IAM/secrets issues, data loss risk | [TODO: DevOps lead name and contact] |
| L3 — Solution owner | Security incident (hardcoded credentials exposed), data breach, service unavailable > 1 hour | [TODO: solution owner name and contact] |
| External — Anthropic support | Claude API outage or unexpected model behaviour | https://status.anthropic.com · support@anthropic.com |
| External — AWS support | S3 / Lambda service disruption | https://health.aws.amazon.com · [TODO: AWS support plan tier and case URL] |
| External — SendGrid support | Email delivery failures | https://status.sendgrid.com · [TODO: SendGrid account support link] |

---

## 7. Useful Commands

### GitHub Actions — Trigger workflows manually

```bash
# Trigger Tool 1: Code Review on a specific PR
gh workflow run tool1_code_review.yml \
  -f review_mode=pr \
  -f pr_number=42

# Trigger Tool 2: Tech Docs regeneration
gh workflow run tool2_tech_docs.yml

# Trigger Tool 3: Business docs for a release
gh workflow run tool3_business_docs.yml \
  -f project_name="Data Ingestion Pipeline" \
  -f release_version="1.0.0"

# Trigger Tool 4: Generate tests
gh workflow run tool4_auto_testing.yml \
  -f test_mode=generate

# Trigger Tool 5: UAT pack generation
gh workflow run tool5_uat.yml \
  -f uat_mode=generate \
  -f release_version="1.0.0"

# List recent workflow runs
gh run list --limit 20

# Watch a run in real time
gh run watch <run-id>

# Download artifacts from a run
gh run download <run-id>
```

### AWS Lambda

```bash
# Invoke Lambda manually with a test event
aws lambda invoke \
  --function-name data-ingest-dev \
  --payload '{"bucket":"capco-data-landing-dev","key":"raw/test.csv"}' \
  --cli-binary-format raw-in-base64-out \
  output.json && cat output.json

# Tail Lambda logs live
aws logs tail /aws/lambda/data-ingest-dev --follow

# Get last 50 log events
aws logs get-log-events \
  --log-group-name /aws/lambda/data-ingest-dev \
  --log-stream-name $(aws logs describe-log-streams \
    --log-group-name /aws/lambda/data-ingest-dev \
    --order-by LastEventTime --descending \
    --query 'logStreams[0].logStreamName' --output text) \
  --limit 50

# Check Lambda configuration
aws lambda get-function-configuration \
  --function-name data-ingest-dev \
  --query '[Timeout,MemorySize,Runtime,LastModified,State]'
```

### S3 — Inspect pipeline state

```bash
# List all unprocessed CSVs in landing bucket
aws s3 ls s3://capco-data-landing-dev/raw/ --recursive | grep '\.csv'

# List all processed Parquet files
aws s3 