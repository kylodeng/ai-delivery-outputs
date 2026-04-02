# Operational Runbook — kylodeng/ai-delivery-source

> **Last updated:** [TODO: insert date] | **Owner:** [TODO: fill in team contacts] | **Repo:** `kylodeng/ai-delivery-source`

---

## 1. Service Overview

This system is an AWS-hosted customer data ingestion pipeline that processes CSV files uploaded to an S3 landing bucket (`capco-data-landing-{env}`), validates and transforms the records, and writes the results as Parquet files to a processed S3 bucket (`capco-data-processed-{env}`). The core compute runs as an AWS Lambda function (`data-ingest-{env}`, Python 3.12) triggered automatically by S3 `ObjectCreated` events on the `raw/` prefix. Surrounding this pipeline is a suite of five AI-assisted GitHub Actions workflows — powered by the Anthropic Claude API — that automate code review, technical documentation generation, business documentation, test generation, and UAT facilitation; all outputs are written to the `ai-delivery-outputs` GitHub repository and optionally emailed via SendGrid. Infrastructure is provisioned via Terraform (AWS provider `~> 5.0`).

---

## 2. Health Checks

Run these checks to confirm the service is operating normally.

### 2.1 Lambda Function

```bash
# Check Lambda function exists and its state
aws lambda get-function \
  --function-name data-ingest-dev \
  --region us-east-1 \
  --query 'Configuration.[FunctionName,State,LastUpdateStatus,Runtime,Timeout]'

# Verify last invocation succeeded (check recent log stream)
aws logs describe-log-streams \
  --log-group-name /aws/lambda/data-ingest-dev \
  --order-by LastEventTime \
  --descending \
  --max-items 1
```

**Expected:** `State: Active`, `LastUpdateStatus: Successful`

### 2.2 S3 Buckets

```bash
# Confirm landing bucket exists and is accessible
aws s3 ls s3://capco-data-landing-dev/raw/ --region us-east-1

# Confirm processed bucket exists and is receiving output
aws s3 ls s3://capco-data-processed-dev/processed/ --region us-east-1
```

**Expected:** No `NoSuchBucket` or `AccessDenied` errors.

### 2.3 S3 → Lambda Trigger

```bash
# Confirm event notification is configured on the landing bucket
aws s3api get-bucket-notification-configuration \
  --bucket capco-data-landing-dev \
  --region us-east-1
```

**Expected:** A `LambdaFunctionConfigurations` entry with `s3:ObjectCreated:*`, prefix `raw/`, suffix `.csv`.

### 2.4 GitHub Actions Workflows

Navigate to `https://github.com/kylodeng/ai-delivery-source/actions` and confirm:

- `Tool 1 — Code Review` ran successfully on the most recent PR.
- `Tool 2 — Tech Documentation` ran successfully on the most recent push to `main`.
- No workflows are stuck in **Queued** state for more than 10 minutes.

### 2.5 Output Repo

```bash
# Confirm outputs are being written
gh api repos/kylodeng/ai-delivery-outputs/contents/ \
  --header "Accept: application/vnd.github+json"
```

**Expected:** Directories for `tech-docs/`, `code-review/`, etc. with recent commit timestamps.

---

## 3. Common Failure Scenarios

| Symptom | Likely Cause | Resolution Steps |
|---|---|---|
| Lambda invocation returns `500` / logs show `Failed: ...` | Malformed or missing CSV field; `validate_customer_record` raised `ValueError` | Check CloudWatch logs for the specific row error. Inspect the source CSV for missing `customer_id`, `email`, `age`, or `country_code` fields. Fix the file and re-upload to `raw/`. |
| Lambda not triggered when CSV is uploaded | S3 bucket notification misconfigured or Lambda permission missing | Run `aws s3api get-bucket-notification-configuration`. Re-apply Terraform: `terraform apply`. Check `aws lambda get-policy --function-name data-ingest-dev` for `s3.amazonaws.com` principal. |
| `NoSuchBucket` error in Lambda logs | Bucket name mismatch between `LANDING_BUCKET` env var and actual bucket | Verify `aws lambda get-function-configuration --function-name data-ingest-dev` env vars match Terraform outputs. Re-apply Terraform if drift detected. |
| Parquet write fails (`s3://...` path error) | `pandas` `to_parquet` to S3 requires `s3fs` or `pyarrow`; dependency not in Lambda package | Confirm `s3fs` and `pyarrow` are included in `lambda.zip`. Rebuild and redeploy. |
| `AccessDenied` on S3 read/write | IAM role policy drifted or was manually restricted | Run `aws iam get-role-policy --role-name lambda-ingest-role --policy-name lambda-s3-policy`. Re-apply Terraform to restore the policy. |
| GitHub Actions workflow fails: `ANTHROPIC_API_KEY` missing | Secret not set or expired in repository settings | Go to `Settings → Secrets and variables → Actions`. Verify `ANTHROPIC_API_KEY`, `GH_TOKEN`, and `SENDGRID_API_KEY` are present and non-empty. Rotate if expired. |
| GitHub Actions fails: `Could not parse Claude response as JSON` | Claude returned a non-JSON or markdown-wrapped response | Retry the workflow (transient). If persistent, check `ANTHROPIC_API_KEY` is valid and model `claude-sonnet-4-6` is accessible on the account. |
| GitHub Actions fails: `GH_TOKEN` permission error writing to `ai-delivery-outputs` | Token lacks `contents: write` permission on the output repo | Rotate the `GH_TOKEN` secret with a token that has `repo` scope for `ai-delivery-outputs`. |
| `get_all_pending_files` returns empty list | No `.csv` files in `raw/` prefix, or S3 pagination missed files (no pagination implemented) | Manually verify with `aws s3 ls s3://capco-data-landing-dev/raw/`. Note: `list_objects_v2` returns max 1,000 keys — if >1,000 files exist, implement pagination [TODO: fix missing pagination in `get_all_pending_files`]. |
| Hardcoded AWS credentials causing `InvalidClientTokenId` | `AWS_ACCESS_KEY` / `AWS_SECRET_KEY` in `data_pipeline.py` are example placeholders and will fail in production | **Immediate:** Remove hardcoded credentials from source. Use Lambda execution role (IAM) or environment variables via Secrets Manager. See Security Notes below. |
| `DB_PASSWORD` env var causes unexpected Lambda behaviour | Hardcoded `SuperSecret123!` in Terraform Lambda environment block | Rotate immediately. Replace with `aws_ssm_parameter` or `aws_secretsmanager_secret` reference in Terraform. |
| Tool 2/3/4/5 workflows produce empty output files | `get_repo_files` found no files matching target extensions, or repo has >20 files of that type (cap hit) | Check the `max_files` cap in `get_repo_files` calls. Increase if needed. Verify file extensions match filter lists. |
| SendGrid email not delivered | `SENDGRID_API_KEY` invalid/expired, or sender domain not verified | Check SendGrid dashboard for bounce/block events. Verify the `SENDER_EMAIL` domain is authenticated in SendGrid. |

---

## 4. Deployment Procedure

> **Prerequisites:** AWS CLI configured, Terraform ≥ 1.5, Python 3.12, `lambda.zip` built from `src/`.

### 4.1 Build Lambda Package

```bash
# From repo root
cd src
pip install \
  boto3 \
  pandas \
  pyarrow \
  s3fs \
  -t ./package/

cp data_pipeline.py ./package/
cd package
zip -r ../../infra/lambda.zip .
cd ../..
```

### 4.2 Deploy Infrastructure (Terraform)

```bash
cd infra

# Step 1: Initialise (first time or after provider changes)
terraform init

# Step 2: Review the plan — read ALL output before proceeding
terraform plan -var="environment=prod" -out=tfplan

# Step 3: Apply
terraform apply tfplan

# Step 4: Confirm outputs
terraform output
```

**Expected outputs:**
```
landing_bucket   = "capco-data-landing-prod"
processed_bucket = "capco-data-processed-prod"
```

### 4.3 Update Lambda Function Code Only (no infra change)

```bash
aws lambda update-function-code \
  --function-name data-ingest-prod \
  --zip-file fileb://infra/lambda.zip \
  --region us-east-1

# Wait for update to complete
aws lambda wait function-updated \
  --function-name data-ingest-prod \
  --region us-east-1
```

### 4.4 Smoke Test After Deployment

```bash
# Upload a test CSV to trigger the pipeline
aws s3 cp tests/fixtures/sample_customers.csv \
  s3://capco-data-landing-prod/raw/smoke-test.csv \
  --region us-east-1

# Wait ~10s then check processed output
aws s3 ls s3://capco-data-processed-prod/processed/ \
  --region us-east-1

# Check Lambda logs
aws logs tail /aws/lambda/data-ingest-prod \
  --follow \
  --since 5m
```

### 4.5 Deploy GitHub Actions Secrets

```bash
# Using GitHub CLI — repeat for each secret
gh secret set ANTHROPIC_API_KEY  --repo kylodeng/ai-delivery-source
gh secret set GH_TOKEN           --repo kylodeng/ai-delivery-source
gh secret set SENDGRID_API_KEY   --repo kylodeng/ai-delivery-source
```

---

### 4.6 Rollback Steps

#### Rollback Lambda Code

```bash
# List recent versions
aws lambda list-versions-by-function \
  --function-name data-ingest-prod \
  --region us-east-1 \
  --query 'Versions[*].[Version,LastModified]'

# Publish current as version if not already versioned
aws lambda publish-version \
  --function-name data-ingest-prod \
  --region us-east-1

# Point alias (or direct invocations) back to previous version
aws lambda update-alias \
  --function-name data-ingest-prod \
  --name live \
  --function-version <PREVIOUS_VERSION_NUMBER> \
  --region us-east-1
```

> [TODO: Are Lambda aliases (`live`, `stable`) in use? If not, implement versioning before next production deploy.]

#### Rollback Terraform

```bash
cd infra
# Revert to previous committed .tf files via git
git checkout <PREVIOUS_COMMIT> -- infra/

terraform plan -var="environment=prod" -out=rollback-plan
terraform apply rollback-plan
```

#### Rollback GitHub Actions Workflow

```bash
# Revert workflow YAML change
git revert <BAD_COMMIT_SHA>
git push origin main
```

---

## 5. Monitoring & Alerting

### 5.1 Key CloudWatch Metrics

| Metric | Namespace | Recommended Alarm Threshold |
|---|---|---|
| `Errors` | `AWS/Lambda` (function: `data-ingest-{env}`) | > 0 in any 5-min window |
| `Duration` | `AWS/Lambda` | > 25,000 ms (approaching 30s timeout) |
| `Throttles` | `AWS/Lambda` | > 0 in any 5-min window |
| `ConcurrentExecutions` | `AWS/Lambda` | > 80 (if account limit is 100) |
| `NumberOfObjects` | `AWS/S3` (bucket: `capco-data-landing-{env}`) | [TODO: define SLA for files not processed within X minutes] |
| `5xxError` | Any API Gateway in front of Lambda | > 0 [TODO: confirm whether API Gateway is used] |

### 5.2 CloudWatch Log Groups

```
/aws/lambda/data-ingest-dev
/aws/lambda/data-ingest-prod
```

**Key log patterns to alert on:**

```
# Errors from the pipeline
"Failed:"
"statusCode\": 500"
"ValueError"
"AccessDenied"
"NoSuchBucket"
```

**Create a metric filter:**

```bash
aws logs put-metric-filter \
  --log-group-name /aws/lambda/data-ingest-prod \
  --filter-name PipelineErrors \
  --filter-pattern "Failed:" \
  --metric-transformations \
    metricName=PipelineErrorCount,metricNamespace=DataPipeline,metricValue=1
```

### 5.3 GitHub Actions Monitoring

- Monitor workflow run status at: `https://github.com/kylodeng/ai-delivery-source/actions`
- Workflow schedules to watch:

| Workflow | Schedule |
|---|---|
| Tool 1 — Code Review | Every Monday 08:00 UTC |
| Tool 2 — Tech Docs | Every Sunday 06:00 UTC |
| Tool 4 — Auto Testing | Every Wednesday 07:00 UTC |

- [TODO: Set up GitHub Actions status notifications to a Slack channel or email distribution list.]

### 5.4 Data Quality Monitoring

Check the pipeline's own output for failed row counts:

```bash
# Parse Lambda return body for failed row counts
aws logs filter-log-events \
  --log-group-name /aws/lambda/data-ingest-prod \
  --filter-pattern "\"failed\": [^0]" \
  --start-time $(date -d '1 hour ago' +%s000)
```

> [TODO: Define acceptable `failed` row rate threshold (e.g. alert if >5% of rows fail validation).]

### 5.5 Cost Monitoring

```bash
# Check Lambda invocation costs
aws ce get-cost-and-usage \
  --time-period Start=$(date -d '7 days ago' +%Y-%m-%d),End=$(date +%Y-%m-%d) \
  --granularity DAILY \
  --filter '{"Dimensions":{"Key":"SERVICE","Values":["AWS Lambda"]}}' \
  --metrics BlendedCost
```

---

## 6. Escalation Path

| Level | Role | Contact | When to Escalate |
|---|---|---|---|
| L1 | On-call Engineer | [TODO: fill in name / PagerDuty rotation] | Lambda errors, S3 access failures, pipeline not processing files |
| L2 | Platform / DevOps Lead | [TODO: fill in name and contact] | Infrastructure drift, IAM issues, Terraform failures |
| L3 | Security Team | [TODO: fill in name and contact] | Hardcoded credential exposure, S3 bucket misconfiguration, suspected data breach |
| L3 | Tech Lead | [TODO: fill in name and contact] | Architecture decisions, critical data loss |
| Vendor | AWS Support | [TODO: confirm support tier and case URL] | AWS service outages, S3/Lambda service errors |
| Vendor | Anthropic Support | [TODO: confirm support contact] | Claude API outages, model access issues |
| Vendor | SendGrid Support | [TODO: confirm support contact] | Email delivery failures |

> **Security incident note:** The codebase currently contains hardcoded AWS credentials (`AKIAIOSFODNN7EXAMPLE` in `data_pipeline.py`) and a hardcoded `DB_PASSWORD` in Terraform. If these are real credentials, treat this as a **P1 security incident** — rotate immediately and notify the Security Team.

---

## 7. Useful Commands

### Lambda

```bash
# Tail Lambda logs live
aws logs tail /aws/lambda/data-ingest-prod --follow --region us-east-1

# Manually invoke Lambda with a test event
aws lambda invoke \
  --function-name data-ingest-prod \
  --region us-east-1 \
  --payload '{"bucket":"capco-data-landing-prod","key":"raw/test.csv"}' \
  --cli-binary-format raw-in-base64-out \