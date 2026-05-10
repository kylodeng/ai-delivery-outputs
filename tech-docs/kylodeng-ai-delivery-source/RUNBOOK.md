# Operational Runbook — `kylodeng/ai-delivery-source`

---

## 1. Service Overview

The `ai-delivery-source` system is a GitHub Actions–driven AI automation platform that integrates Anthropic Claude (`claude-sonnet-4-6`) with a data ingestion pipeline deployed on AWS Lambda. The platform exposes five automated workflows: Claude-powered code review on pull requests (Tool 1), technical documentation generation (Tool 2), business documentation generation (Tool 3), AI-generated test file generation and coverage gap analysis (Tool 4), and UAT test pack facilitation and defect analysis (Tool 5). Underlying these workflows is a Python-based customer CSV data ingestion pipeline (`src/data_pipeline.py`) that reads raw CSV files from an S3 landing bucket (`capco-data-landing-{env}`), validates and transforms them to Parquet, and writes outputs to a processed S3 bucket (`capco-data-processed-{env}`), all triggered via an S3 event notification to an AWS Lambda function (`data-ingest-{env}`). All AI-generated artefacts are written to a companion GitHub repository (`ai-delivery-outputs`) and optionally emailed via SendGrid.

---

## 2. Health Checks

### 2.1 AWS Lambda — Data Ingestion Pipeline

| Check | How to verify |
|---|---|
| Lambda function exists and is active | `aws lambda get-function --function-name data-ingest-dev` — `State` must be `Active` |
| Lambda last invocation succeeded | Check CloudWatch log group `/aws/lambda/data-ingest-dev` for recent `INFO` entries |
| S3 landing bucket accessible | `aws s3 ls s3://capco-data-landing-dev/raw/` returns without error |
| S3 processed bucket accessible | `aws s3 ls s3://capco-data-processed-dev/processed/` returns without error |
| S3 event notification wired | `aws s3api get-bucket-notification-configuration --bucket capco-data-landing-dev` — confirms Lambda ARN is present |

### 2.2 GitHub Actions Workflows

| Check | How to verify |
|---|---|
| All 5 workflows are enabled | Navigate to `Actions` tab in the repo; confirm no workflow is disabled |
| Required secrets are set | `Settings → Secrets → Actions` — confirm `ANTHROPIC_API_KEY`, `GH_TOKEN`, `SENDGRID_API_KEY` all present |
| `ai-delivery-outputs` repo exists and is writable | `curl -H "Authorization: Bearer $GH_TOKEN" https://api.github.com/repos/{owner}/ai-delivery-outputs` returns `200` |
| Most recent workflow run is green | Check each workflow's last run status in the Actions tab |

### 2.3 External API Dependencies

| Dependency | Check |
|---|---|
| Anthropic API reachable | `curl https://api.anthropic.com/v1/models -H "x-api-key: $ANTHROPIC_API_KEY"` returns `200` |
| SendGrid API reachable | `curl https://api.sendgrid.com/v3/user/profile -H "Authorization: Bearer $SENDGRID_API_KEY"` returns `200` |
| GitHub API reachable | `curl https://api.github.com/rate_limit -H "Authorization: Bearer $GH_TOKEN"` — confirm `remaining` > 0 |

---

## 3. Common Failure Scenarios

| Symptom | Likely Cause | Resolution Steps |
|---|---|---|
| GitHub Actions workflow fails with `KeyError: 'ANTHROPIC_API_KEY'` | Secret not set or misnamed in repository settings | Go to `Settings → Secrets → Actions`, add/rename `ANTHROPIC_API_KEY` |
| GitHub Actions workflow fails with `KeyError: 'GH_TOKEN'` | `GH_TOKEN` secret missing or expired | Regenerate a GitHub PAT with `repo` and `contents:write` scope; update the secret |
| GitHub Actions workflow fails with `KeyError: 'SENDGRID_API_KEY'` | SendGrid secret missing | Add `SENDGRID_API_KEY` to repository secrets |
| `tool1_code_review.py` posts no PR comment | PR diff is empty, or Claude returned non-JSON | Check Actions logs for `[DEBUG]` output; re-run the workflow manually |
| `write_output_file` fails with `404` | `ai-delivery-outputs` repo does not exist or `GH_TOKEN` lacks write access | Create the output repo; ensure PAT has `contents:write` on that repo |
| Lambda returns `statusCode: 500` | Malformed CSV, missing `key` in event, or S3 permission denied | Check CloudWatch `/aws/lambda/data-ingest-{env}` for the error message; verify IAM role and bucket policy |
| Lambda returns `statusCode: 500` with `NoCredentialsError` | Hardcoded AWS keys in `data_pipeline.py` are invalid or rotated | **Immediately** move credentials to AWS Secrets Manager or IAM role; update `get_s3_client()` to use instance role |
| S3 landing bucket not triggering Lambda | S3 event notification misconfigured or Lambda permission missing | Run `aws s3api get-bucket-notification-configuration`; check Lambda resource-based policy allows `s3.amazonaws.com` |
| `get_all_pending_files` returns empty list | S3 list truncated (>1000 objects) or wrong prefix | Implement pagination using `ContinuationToken`; verify `raw/` prefix has `.csv` files |
| Claude returns non-JSON response causing `ValueError` | Model returned markdown-wrapped or incomplete JSON | Check Actions logs for `[DEBUG]` output; retry the workflow — transient model behaviour |
| GitHub API rate limit exceeded | Too many API calls in short period (GH_TOKEN is shared) | Check `X-RateLimit-Remaining` header; wait for reset or use a dedicated service account PAT |
| Anthropic API `529 Overloaded` | Claude API capacity constraint | Retry with exponential back-off; [TODO: is there a retry policy implemented in `call_claude()`?] |
| SendGrid email not delivered | Invalid `SENDER_EMAIL` domain, or SendGrid account suspended | Check SendGrid Activity Feed; verify sender domain is verified |
| Tool 2/3/4/5 writes to wrong path in output repo | `OUTPUT_REPO_OWNER` env var not set | Ensure `OUTPUT_REPO_OWNER` is set; defaults to `GITHUB_REPOSITORY_OWNER` which may be wrong on forks |
| Parquet write fails silently | `pandas` not installed in Lambda package, or S3 write permission denied | Confirm Lambda deployment package includes `pandas` and `pyarrow`; check IAM policy |
| Tool 5 UAT workflow does not trigger on branch create | Branch name does not match `refs/heads/release/*` pattern | Ensure release branches are named `release/x.y.z` exactly |

---

## 4. Deployment Procedure

### 4.1 Prerequisites

- AWS CLI configured with sufficient IAM permissions
- Terraform >= 1.5 installed
- GitHub PAT with `repo`, `contents:write`, `actions:write` scopes stored as `GH_TOKEN`
- Python 3.12 available locally for packaging

### 4.2 Infrastructure Deployment (Terraform)

```bash
# Step 1: Navigate to infra directory
cd infra/

# Step 2: Initialise Terraform
terraform init

# Step 3: Review the plan
terraform plan -var="environment=dev"

# Step 4: Apply
terraform apply -var="environment=dev" -auto-approve

# Step 5: Confirm outputs
terraform output
```

> ⚠️ **Before applying to production**, resolve the following known issues in `infra/main.tf`:
> - Enable S3 bucket encryption (`aws_s3_bucket_server_side_encryption_configuration`)
> - Add S3 public access block (`aws_s3_bucket_public_access_block`)
> - Replace `s3:*` on `*` IAM policy with least-privilege resource-scoped actions
> - Move `DB_PASSWORD` from Lambda env var to AWS Secrets Manager
> - Add resource tags (`environment`, `owner`, `project`)

### 4.3 Lambda Function Deployment

```bash
# Step 1: Install dependencies into package directory
pip install pandas pyarrow boto3 -t package/

# Step 2: Copy source
cp src/data_pipeline.py package/

# Step 3: Zip the package
cd package && zip -r ../lambda.zip . && cd ..

# Step 4: Deploy via Terraform (lambda.zip is referenced in main.tf)
cd infra/ && terraform apply -var="environment=dev" -auto-approve

# OR update directly with AWS CLI
aws lambda update-function-code \
  --function-name data-ingest-dev \
  --zip-file fileb://lambda.zip
```

### 4.4 GitHub Actions — Secrets Setup

```bash
# Using GitHub CLI (gh)
gh secret set ANTHROPIC_API_KEY   --repo kylodeng/ai-delivery-source
gh secret set GH_TOKEN            --repo kylodeng/ai-delivery-source
gh secret set SENDGRID_API_KEY    --repo kylodeng/ai-delivery-source
```

### 4.5 Rollback Procedure

```bash
# --- Terraform rollback ---
# Step 1: Identify previous state (if using remote state with versioning)
# [TODO: Is Terraform remote state (S3 backend + DynamoDB locking) configured?]

# Step 2: Revert to previous Lambda version
aws lambda update-alias \
  --function-name data-ingest-dev \
  --name live \
  --function-version <previous_version_number>

# --- OR destroy and re-apply previous tag ---
git checkout <previous-tag>
cd infra/ && terraform apply -var="environment=dev" -auto-approve

# --- GitHub Actions rollback ---
# Disable a broken workflow via UI: Actions → <Workflow> → ⋯ → Disable workflow
# OR via CLI:
gh workflow disable "Tool 1 — Code Review" --repo kylodeng/ai-delivery-source

# Re-enable after fix:
gh workflow enable "Tool 1 — Code Review" --repo kylodeng/ai-delivery-source
```

---

## 5. Monitoring & Alerting

### 5.1 AWS CloudWatch — Lambda

| Metric | Namespace | Alarm Threshold | Action |
|---|---|---|---|
| `Errors` | `AWS/Lambda` | > 0 in 5 min | Page on-call |
| `Duration` | `AWS/Lambda` | > 25,000 ms (timeout is 30 s) | Investigate slow S3 reads |
| `Throttles` | `AWS/Lambda` | > 0 | Review concurrency limits |
| `ConcurrentExecutions` | `AWS/Lambda` | Approaching account limit | Request limit increase |

```bash
# Create a basic error alarm
aws cloudwatch put-metric-alarm \
  --alarm-name "data-ingest-dev-errors" \
  --metric-name Errors \
  --namespace AWS/Lambda \
  --dimensions Name=FunctionName,Value=data-ingest-dev \
  --statistic Sum \
  --period 300 \
  --evaluation-periods 1 \
  --threshold 1 \
  --comparison-operator GreaterThanOrEqualToThreshold \
  --alarm-actions arn:aws:sns:us-east-1:<account_id>:on-call-alerts
  # [TODO: Replace SNS ARN with actual on-call topic]
```

### 5.2 CloudWatch Log Insights Queries

```bash
# View recent Lambda errors
aws logs filter-log-events \
  --log-group-name /aws/lambda/data-ingest-dev \
  --filter-pattern "ERROR" \
  --start-time $(date -d '1 hour ago' +%s000)
```

### 5.3 Key Log Patterns to Watch

| Log pattern | Meaning |
|---|---|
| `INFO Processed {key}` | Successful ingestion |
| `ERROR Failed:` | Lambda handler caught an exception — check full message |
| `Missing required field` | Incoming CSV has schema drift |
| `Age out of range` / `Invalid email` | Data quality issue in source system |
| `NoCredentialsError` | IAM role not attached or hardcoded keys expired |

### 5.4 GitHub Actions Monitoring

| What to watch | Where |
|---|---|
| Workflow run failures | `Actions` tab → filter by status `Failure` |
| API rate limit consumption | Workflow logs — look for `403` or `rate limit` in output |
| Audit log entries | `ai-delivery-outputs` repo — [TODO: confirm `write_audit_entry()` target path] |

### 5.5 S3 Metrics

```bash
# Check how many files are pending processing
aws s3 ls s3://capco-data-landing-dev/raw/ --recursive | grep ".csv" | wc -l

# Check processed output count
aws s3 ls s3://capco-data-processed-dev/processed/ --recursive | grep ".parquet" | wc -l
```

> [TODO: Are S3 server access logs or AWS Config rules enabled for the landing and processed buckets?]

---

## 6. Escalation Path

| Level | Condition | Contact |
|---|---|---|
| L1 — Self-service | Workflow failure visible in Actions logs; retry resolves it | Re-run failed job via Actions UI |
| L2 — On-call Engineer | Lambda errors > 3 in 30 min; data not flowing; secrets expired | [TODO: Add on-call engineer name, Slack handle, PagerDuty rotation] |
| L3 — Platform / DevOps Lead | Terraform state corruption; IAM policy change required; AWS account-level issue | [TODO: Add Platform Lead name and contact] |
| L4 — Security Team | Hardcoded credentials exposed; S3 bucket public access confirmed; IAM privilege escalation | [TODO: Add Security team Slack channel and incident response process] |
| External — Anthropic Support | Persistent Claude API 5xx errors not resolved by retry | https://status.anthropic.com / support@anthropic.com |
| External — AWS Support | Lambda service degradation; S3 unavailability | AWS Support console — [TODO: specify support tier] |
| External — SendGrid Support | Email deliverability failures persisting > 1 hour | https://status.sendgrid.com |

> ⚠️ **Security note:** The hardcoded AWS access keys in `src/data_pipeline.py` and `DB_PASSWORD` in `infra/main.tf` represent **active security risks**. If either is suspected to be exposed, immediately escalate to L4 and rotate the credentials — do not wait for L2/L3.

---

## 7. Useful Commands

### AWS Lambda

```bash
# Invoke Lambda manually with a test event
aws lambda invoke \
  --function-name data-ingest-dev \
  --payload '{"bucket": "capco-data-landing-dev", "key": "raw/test.csv"}' \
  --cli-binary-format raw-in-base64-out \
  output.json && cat output.json

# Get Lambda configuration
aws lambda get-function-configuration --function-name data-ingest-dev

# Tail Lambda logs in real time
aws logs tail /aws/lambda/data-ingest-dev --follow

# List recent Lambda invocations with errors
aws logs filter-log-events \
  --log-group-name /aws/lambda/data-ingest-dev \
  --filter-pattern "[level=ERROR]" \
  --start-time $(date -d '2 hours ago' +%s000)

# Check Lambda concurrency
aws lambda get-function-concurrency --function-name data-ingest-dev
```

### S3 Operations

```bash
# Upload a test CSV to trigger the pipeline
aws s3 cp test_customers.csv s3://capco-data-landing-dev/raw/test_customers.csv

# List all pending raw files
aws s3 ls s3://capco-data-landing-dev/raw/ --recursive

# List processed outputs
aws s3 ls s3://capco-data-processed-dev/processed/ --recursive

# Move a file back to raw for reprocessing
aws s3 mv \
  s3://capco-data-processed-dev/processed/test_customers.parquet \
  s3://capco-data-landing-dev/raw/test_customers_retry.csv

# Check bucket encryption status
aws s3api get-bucket