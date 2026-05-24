# Operational Runbook — `kylodeng/ai-delivery-source`

---

## 1. Service Overview

The `ai-delivery-source` system is an AWS-hosted data ingestion pipeline that reads customer CSV files deposited into an S3 landing bucket (`capco-data-landing-<env>`), validates and transforms each record (checking required fields, email format, and age range), and writes the results as Parquet files to a processed S3 bucket (`capco-data-processed-<env>`). The pipeline is triggered automatically via an S3 event notification whenever a `.csv` file is created under the `raw/` prefix, which invokes an AWS Lambda function (`data-ingest-<env>`) running Python 3.12. Alongside the data pipeline, the repository hosts five AI-assisted GitHub Actions delivery workflows (code review, tech docs, business docs, auto testing, and UAT facilitation), each of which calls the Anthropic Claude API (model `claude-sonnet-4-6`) and publishes outputs to a companion repository (`ai-delivery-outputs`). Results and notifications are delivered via SendGrid email.

> **⚠️ Known critical security issues identified in this codebase (do not promote to production without remediation):**
> - AWS credentials are hardcoded in `src/data_pipeline.py`
> - A database password is hardcoded in `infra/main.tf` as a Lambda environment variable
> - The landing S3 bucket has no encryption and no public access block configured
> - The Lambda IAM role has `s3:*` on `Resource: *` (full S3 access across the account)

---

## 2. Health Checks

Run these checks in order to confirm all components are operational.

### 2.1 AWS Lambda

```bash
# Check Lambda function state
aws lambda get-function --function-name data-ingest-dev \
  --query 'Configuration.[State,LastUpdateStatus]' --output table

# Check recent invocation errors (last 1 hour)
aws cloudwatch get-metric-statistics \
  --namespace AWS/Lambda \
  --metric-name Errors \
  --dimensions Name=FunctionName,Value=data-ingest-dev \
  --start-time $(date -u -d '1 hour ago' +%Y-%m-%dT%H:%M:%SZ) \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%SZ) \
  --period 3600 \
  --statistics Sum
```

**Expected:** `State = Active`, `LastUpdateStatus = Successful`, error count = 0.

### 2.2 S3 Buckets

```bash
# Confirm landing bucket exists and is accessible
aws s3 ls s3://capco-data-landing-dev/raw/ --summarize

# Confirm processed bucket exists
aws s3 ls s3://capco-data-processed-dev/ --summarize
```

**Expected:** Both buckets listed without `AccessDenied` errors.

### 2.3 S3 → Lambda Trigger

```bash
# Confirm the event notification is configured
aws s3api get-bucket-notification-configuration \
  --bucket capco-data-landing-dev
```

**Expected:** Response contains a `LambdaFunctionConfigurations` block referencing `data-ingest-dev`, events `s3:ObjectCreated:*`, prefix `raw/`, suffix `.csv`.

### 2.4 End-to-End Smoke Test

```bash
# Upload a minimal test CSV and check for the parquet output
echo "customer_id,email,age,country_code
SMOKE001,smoke@test.com,30,GB" > /tmp/smoke_test.csv

aws s3 cp /tmp/smoke_test.csv s3://capco-data-landing-dev/raw/smoke_test.csv

# Wait ~15s then check for output
sleep 15
aws s3 ls s3://capco-data-processed-dev/processed/smoke_test.parquet
```

**Expected:** Parquet file present. Lambda logs show `statusCode: 200`.

### 2.5 GitHub Actions Workflows

Navigate to: `https://github.com/kylodeng/ai-delivery-source/actions`

Confirm:
- No workflows in a `failure` state with recent timestamps.
- Secrets `ANTHROPIC_API_KEY`, `GH_TOKEN`, and `SENDGRID_API_KEY` are present under **Settings → Secrets and variables → Actions**.

### 2.6 Output Repository

```bash
# Confirm output repo is accessible
curl -s -H "Authorization: Bearer $GH_TOKEN" \
  https://api.github.com/repos/kylodeng/ai-delivery-outputs \
  | jq '.name, .private'
```

**Expected:** Returns repo name and visibility without a 404 or 403.

---

## 3. Common Failure Scenarios

| Symptom | Likely Cause | Resolution Steps |
|---|---|---|
| Lambda returns `{"statusCode": 500}` with `NoCredentialsError` | Hardcoded AWS credentials in `data_pipeline.py` are invalid or expired | 1. Immediately rotate any exposed keys in AWS IAM. 2. Remove hardcoded keys from source. 3. Attach an IAM execution role to the Lambda and use `boto3` without explicit credentials. 4. Redeploy via `terraform apply`. |
| Lambda returns `{"statusCode": 500}` with `NoSuchBucket` | S3 bucket does not exist or is in wrong region | 1. Run `aws s3 ls` to verify bucket names. 2. Check `var.aws_region` in `infra/main.tf`. 3. Run `terraform plan` and `terraform apply` to recreate missing resources. |
| CSV file uploaded but no parquet output appears | S3 event notification not configured, or Lambda has no trigger permission | 1. Run `aws s3api get-bucket-notification-configuration --bucket capco-data-landing-<env>`. 2. Verify Lambda resource policy allows `s3.amazonaws.com` to invoke the function: `aws lambda get-policy --function-name data-ingest-<env>`. 3. Re-run `terraform apply` to restore the notification. |
| Lambda times out (duration ≥ 30 s) | Large CSV file exceeds 30-second Lambda timeout; no pagination on `list_objects_v2` | 1. Check file size in S3. 2. Increase Lambda `timeout` in `main.tf` (max 900 s). 3. Implement S3 pagination in `get_all_pending_files()` for buckets with >1000 objects. 4. Redeploy. |
| Many rows in `failed_rows` but no alerting raised | Validation failures are silently collected; no downstream alert or dead-letter queue | 1. Review Lambda CloudWatch logs for `failed_rows` counts. 2. [TODO: confirm whether failed-row threshold should trigger an alert]. 3. Consider adding a DLQ or SNS notification when `failed > 0`. |
| GitHub Actions workflow fails with `ANTHROPIC_API_KEY` error | Secret missing or expired | 1. Go to **Settings → Secrets → Actions** in the source repo. 2. Verify `ANTHROPIC_API_KEY` is set and has not expired. 3. Re-run the failed workflow. |
| GitHub Actions workflow fails with `GH_TOKEN` error | PAT expired or lacks permissions to write to `ai-delivery-outputs` | 1. Verify `GH_TOKEN` secret in repo settings. 2. Ensure the PAT has `repo` scope for both the source and output repos. 3. Regenerate the PAT if expired and update the secret. |
| SendGrid email not delivered | `SENDGRID_API_KEY` missing/invalid, or sender domain not verified | 1. Check `SENDGRID_API_KEY` secret. 2. Log into SendGrid dashboard and verify `noreply@ai-delivery.capco.com` sender identity. 3. Check SendGrid Activity Feed for bounce/block events. |
| Claude API returns non-JSON or truncated response | `max_tokens` limit hit, or model returned markdown fences | 1. Check workflow run logs for `[DEBUG] First 500 chars` output. 2. Increase `max_tokens` in `call_claude()` call if truncation suspected. 3. The `extract_json()` and `clean_json()` helpers should handle fences — if failing, inspect raw response in logs. |
| `tool2_tech_docs` generates empty or `_No files found_` docs | Repo file extensions not matched, or `max_files` cap reached | 1. Confirm source files use `.py`, `.tf`, `.yaml`, etc. 2. Increase `max_files` parameter in `get_repo_files()` calls if repo is large. 3. Re-run workflow manually via `workflow_dispatch`. |
| `terraform apply` fails with state lock | Previous `apply` crashed without releasing state lock | 1. `terraform force-unlock <LOCK_ID>` (get lock ID from error message). 2. Verify no other pipeline is running `apply`. 3. Re-run `terraform apply`. [TODO: is remote state (S3 backend + DynamoDB lock) configured? The `main.tf` does not show a `backend` block.] |
| S3 bucket access denied to Lambda | IAM policy not attached or role/policy out of sync | 1. Check: `aws iam get-role-policy --role-name lambda-ingest-role --policy-name lambda-s3-policy`. 2. Re-run `terraform apply` to reconcile. 3. Note: current policy is `s3:*` on `*` — restrict to specific buckets as part of remediation. |

---

## 4. Deployment Procedure

> **Prerequisites:** AWS CLI configured with appropriate credentials, Terraform ≥ 1.x installed, Python 3.12, GitHub PAT with `repo` scope set as `GH_TOKEN`.

### 4.1 Deploy Infrastructure

```bash
# Step 1 – Navigate to infra directory
cd infra/

# Step 2 – Initialise Terraform (first time only, or after provider changes)
terraform init

# Step 3 – Review planned changes
terraform plan -var="environment=dev"

# Step 4 – Apply changes (confirm prompt with 'yes')
terraform apply -var="environment=dev"

# Step 5 – Note outputs
terraform output landing_bucket
terraform output processed_bucket
```

### 4.2 Package and Deploy Lambda

```bash
# Step 6 – Package the Lambda function
cd ../src/
pip install -r requirements.txt -t package/   # [TODO: requirements.txt not found in repo — confirm dependencies]
cp data_pipeline.py package/
cd package/
zip -r ../../infra/lambda.zip .

# Step 7 – Update the Lambda package (if function already exists)
aws lambda update-function-code \
  --function-name data-ingest-dev \
  --zip-file fileb://../infra/lambda.zip

# Step 8 – Confirm update is complete
aws lambda get-function --function-name data-ingest-dev \
  --query 'Configuration.LastUpdateStatus'
# Expected: "Successful"
```

### 4.3 Configure GitHub Secrets

```bash
# Step 9 – Set required secrets via GitHub CLI
gh secret set ANTHROPIC_API_KEY  --repo kylodeng/ai-delivery-source
gh secret set GH_TOKEN           --repo kylodeng/ai-delivery-source
gh secret set SENDGRID_API_KEY   --repo kylodeng/ai-delivery-source
```

### 4.4 Verify Deployment

```bash
# Step 10 – Run smoke test (see Section 2.4)
# Step 11 – Trigger a workflow manually to confirm AI tools are working
gh workflow run tool2_tech_docs.yml --repo kylodeng/ai-delivery-source
```

---

### 4.5 Rollback Procedure

#### Infrastructure Rollback (Terraform)

```bash
# Option A – Revert to previous Terraform state revision
# [TODO: confirm whether versioned remote state backend is configured]
terraform state list   # review current state

# Option B – Destroy and re-apply previous version
git checkout <previous-commit> -- infra/main.tf
cd infra/
terraform plan -var="environment=dev"
terraform apply -var="environment=dev"
```

#### Lambda Code Rollback

```bash
# List available Lambda versions
aws lambda list-versions-by-function --function-name data-ingest-dev

# Roll back to a specific published version
aws lambda update-alias \
  --function-name data-ingest-dev \
  --name live \
  --function-version <PREVIOUS_VERSION_NUMBER>

# [TODO: Lambda versioning and aliases are not configured in main.tf — 
# add aws_lambda_alias and aws_lambda_function.publish_version before relying on this]
```

#### GitHub Actions Rollback

```bash
# Re-run last successful workflow run
gh run list --repo kylodeng/ai-delivery-source --workflow tool1_code_review.yml --limit 5
gh run rerun <RUN_ID> --repo kylodeng/ai-delivery-source
```

---

## 5. Monitoring & Alerting

> [TODO: No CloudWatch alarms, SNS topics, or monitoring configuration was found in `infra/main.tf`. The following are the recommended metrics to instrument.]

### 5.1 Key Lambda Metrics (CloudWatch)

| Metric | Namespace | Recommended Threshold | Action |
|---|---|---|---|
| `Errors` | `AWS/Lambda` | > 0 in 5 min | Page on-call engineer |
| `Duration` | `AWS/Lambda` | > 25,000 ms (approaching 30 s timeout) | Investigate large files; increase timeout |
| `Throttles` | `AWS/Lambda` | > 5 in 5 min | Check concurrency limits; request limit increase |
| `ConcurrentExecutions` | `AWS/Lambda` | > 80% of account limit | Scale review |
| `Invocations` | `AWS/Lambda` | 0 for > 24 h (if files expected) | Check S3 trigger configuration |

### 5.2 S3 Metrics

| Metric | What to Watch |
|---|---|
| `NumberOfObjects` on `raw/` prefix | Rising count without corresponding rise in `processed/` indicates Lambda failures |
| `4xxErrors` / `5xxErrors` on buckets | Permissions issues |

### 5.3 Lambda Logs (CloudWatch Logs)

**Log group:** `/aws/lambda/data-ingest-<env>`

Key log patterns to alert on:

```
# Errors to alert on
"Failed:"
"statusCode\": 500"
"MissingRequiredField"
"AccessDenied"
"NoCredentialsError"

# Info patterns to track volume
"Processed"
"failed_rows"
```

```bash
# Tail Lambda logs live
aws logs tail /aws/lambda/data-ingest-dev --follow

# Search for errors in last 1 hour
aws logs filter-log-events \
  --log-group-name /aws/lambda/data-ingest-dev \
  --start-time $(date -d '1 hour ago' +%s)000 \
  --filter-pattern "Failed"
```

### 5.4 GitHub Actions Workflow Monitoring

- **Where:** `https://github.com/kylodeng/ai-delivery-source/actions`
- Watch for workflows with `failure` status, particularly after:
  - Push to `main` (triggers Tool 2)
  - PR open/sync (triggers Tool 1 and Tool 4)
  - Release tag push (triggers Tool 3)
  - Release branch creation `release/*` (triggers Tool 5)
- **Scheduled runs to monitor:**
  - Tool 1: Every Monday 08:00 UTC
  - Tool 2: Every Sunday 06:00 UTC
  - Tool 4: Every Wednesday 07:00 UTC

### 5.5 Alerting Gaps (Remediation Required)

- [TODO: No CloudWatch alarms are defined in Terraform — add `aws_cloudwatch_metric_alarm` resources for Lambda Errors and Duration]
- [TODO: No SNS topic exists for alarm notifications — define one and subscribe the on-call email]
- [TODO: No dead-letter queue (SQS/SNS) is configured for the Lambda — failed invocations are silently lost]
- [TODO: Failed validation rows (`failed_rows`) are logged but no alert is raised when the count exceeds a threshold]

---

## 6. Escalation Path

| Level | Who | When to Escalate | Contact |
|---|---|---|---|
| L1 — First