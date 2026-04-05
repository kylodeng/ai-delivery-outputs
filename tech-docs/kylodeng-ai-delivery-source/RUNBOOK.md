# Operational Runbook — `kylodeng/ai-delivery-source`

> **Last updated:** [TODO: insert date]
> **Owner:** [TODO: fill in team contacts]
> **Runbook version:** 1.0

---

## 1. Service Overview

`kylodeng/ai-delivery-source` is a GitHub Actions–driven AI delivery platform that orchestrates five Claude-powered automation tools: automated code review (Tool 1), technical documentation generation (Tool 2), business documentation generation (Tool 3), AI-assisted test generation and coverage gap analysis (Tool 4), and UAT test pack facilitation with defect analysis (Tool 5). Each tool is implemented as a Python script under `.github/scripts/`, invoked by a dedicated GitHub Actions workflow, and relies on three external API dependencies — Anthropic Claude (`claude-sonnet-4-6`), the GitHub REST API, and SendGrid for email delivery. Outputs (reports, docs, test files) are persisted to a companion repository named `ai-delivery-outputs` under the same GitHub organisation. The repository also contains a data ingestion pipeline (`src/data_pipeline.py`) deployed as an AWS Lambda function, triggered by S3 `ObjectCreated` events, which validates and transforms customer CSV files from a landing bucket into Parquet in a processed bucket. Infrastructure is managed via Terraform (`infra/main.tf`) targeting AWS `us-east-1`.

---

## 2. Health Checks

### 2.1 GitHub Actions Workflows

| Check | How to verify |
|---|---|
| All 5 workflows visible and enabled | GitHub UI → **Actions** tab → confirm `Tool 1` through `Tool 5` workflows are listed and not disabled |
| Last run status for each workflow | GitHub UI → Actions → select workflow → confirm most recent run shows ✅ |
| Workflow run logs accessible | Click any run → confirm steps complete without `exit code 1` |

### 2.2 AWS Lambda (Data Pipeline)

| Check | How to verify |
|---|---|
| Lambda function exists and is active | `aws lambda get-function --function-name data-ingest-<env>` → `State: Active` |
| Lambda last invocation succeeded | AWS Console → Lambda → Monitor tab → check Invocation errors = 0 |
| S3 event trigger is configured | `aws s3api get-bucket-notification-configuration --bucket capco-data-landing-<env>` → confirm `LambdaFunctionConfigurations` present |
| Landing bucket exists and is reachable | `aws s3 ls s3://capco-data-landing-<env>/` → returns without `NoSuchBucket` |
| Processed bucket exists | `aws s3 ls s3://capco-data-processed-<env>/` → returns without `NoSuchBucket` |

### 2.3 External API Dependencies

| Check | How to verify |
|---|---|
| Anthropic API key valid | `curl https://api.anthropic.com/v1/models -H "x-api-key: $ANTHROPIC_API_KEY"` → HTTP 200 |
| GitHub token has required scopes | `curl -H "Authorization: Bearer $GH_TOKEN" https://api.github.com/user` → HTTP 200, token has `repo` and `write:discussion` scopes |
| SendGrid key valid | `curl -H "Authorization: Bearer $SENDGRID_API_KEY" https://api.sendgrid.com/v3/scopes` → HTTP 200 |
| Output repo accessible | `curl -H "Authorization: Bearer $GH_TOKEN" https://api.github.com/repos/<owner>/ai-delivery-outputs` → HTTP 200 |

### 2.4 GitHub Actions Secrets

Confirm the following secrets are set under **Settings → Secrets and variables → Actions**:

- `ANTHROPIC_API_KEY`
- `GH_TOKEN`
- `SENDGRID_API_KEY`

---

## 3. Common Failure Scenarios

| Symptom | Likely Cause | Resolution Steps |
|---|---|---|
| Workflow fails at `Run Claude code review` / `Generate tech documentation` / etc. with `KeyError: 'ANTHROPIC_API_KEY'` | `ANTHROPIC_API_KEY` secret not set or expired | 1. Go to repo **Settings → Secrets → Actions**. 2. Verify `ANTHROPIC_API_KEY` exists. 3. Rotate key in Anthropic console and update secret. 4. Re-run workflow. |
| Workflow fails with `401 Unauthorized` on GitHub API calls | `GH_TOKEN` expired, revoked, or lacks required permissions | 1. Generate a new PAT with `repo`, `write:packages`, and `pull_request` scopes. 2. Update `GH_TOKEN` secret. 3. Re-run workflow. |
| `write_output_file` fails with HTTP 404 | `ai-delivery-outputs` repo does not exist or `OUTPUT_REPO_OWNER` env var is wrong | 1. Confirm repo `<owner>/ai-delivery-outputs` exists. 2. Create it if missing (`gh repo create <owner>/ai-delivery-outputs --public`). 3. Confirm `OUTPUT_REPO_OWNER` in workflow env matches the actual GitHub org/user. |
| Claude returns non-JSON response; workflow fails at `extract_json` / `json.loads` | Claude model returned markdown or prose instead of raw JSON; or model API changed | 1. Check workflow logs for `[DEBUG] First 500 chars`. 2. Verify `MODEL = "claude-sonnet-4-6"` in `shared.py` is still a valid model identifier. 3. Retry run (transient). 4. If persistent, review prompt or increase `max_tokens`. |
| SendGrid email not received | Invalid sender domain, `SENDGRID_API_KEY` not set, or recipient spam filter | 1. Check SendGrid activity feed for delivery status. 2. Verify `SENDER_EMAIL` domain is verified in SendGrid. 3. Confirm `SENDGRID_API_KEY` secret is set correctly. 4. Check `NOTIFY_EMAIL` is correct (`kylo.deng@capco.com`). |
| Lambda fails with `NoCredentialsError` or `ClientError: InvalidClientTokenId` | Hardcoded AWS credentials in `data_pipeline.py` are invalid or rotated | **Immediate:** 1. Rotate the exposed IAM credentials in AWS IAM console. 2. Revoke `AKIAIOSFODNN7EXAMPLE` if it exists as a real key. **Fix:** Migrate credentials to AWS Secrets Manager or use Lambda execution role (IAM role is already attached — remove hardcoded keys). |
| Lambda times out (status 504 / `Task timed out after 30.00 seconds`) | Large CSV file; S3 download or Parquet write exceeds 30s timeout | 1. Check CloudWatch Logs for timeout message. 2. Increase Lambda timeout in Terraform (`timeout = 60`). 3. Apply: `terraform apply`. 4. For very large files, consider chunked processing or Step Functions. |
| Lambda returns `500` with `pandas` or missing module error | Lambda deployment package (`lambda.zip`) missing Python dependencies | 1. Rebuild `lambda.zip` including `pandas` and `pyarrow`. 2. Re-deploy: `terraform apply`. 3. [TODO: Is there a build script for lambda.zip?] |
| `get_all_pending_files` returns truncated list (>1000 files) | `list_objects_v2` returns max 1000 objects; pagination not implemented | 1. If >1000 files in `raw/`, only first 1000 will be processed. 2. Implement paginator: use `list_objects_v2` with `ContinuationToken`. 3. [TODO: confirm max expected file volume] |
| S3 event trigger not firing Lambda | Lambda resource-based policy missing; bucket notification mis-configured | 1. `aws lambda get-policy --function-name data-ingest-<env>` — confirm `s3.amazonaws.com` principal is allowed. 2. Check `aws s3api get-bucket-notification-configuration`. 3. Re-apply Terraform if config has drifted. |
| Tool 4 generates tests that import real AWS services | `tool4_auto_testing.py` prompt instructs mocking but Claude may miss it | 1. Review generated test file in `ai-delivery-outputs`. 2. Manually add `@mock.patch('boto3.client')` stubs. 3. Improve prompt if persistent. |
| Tool 5 UAT workflow triggers on every branch creation | `on: create` event fires for all new branches, not just `release/*` | The `if:` guard in `tool5_uat.yml` filters to `refs/heads/release/` — confirm this is not being bypassed. If unexpected runs occur, review `if:` condition logic. |
| Terraform plan shows S3 buckets without encryption | `aws_s3_bucket` resources in `main.tf` have no `server_side_encryption_configuration` | 1. Add `aws_s3_bucket_server_side_encryption_configuration` resource for both buckets. 2. `terraform plan` to verify. 3. `terraform apply`. |

---

## 4. Deployment Procedure

### 4.1 Prerequisites

- AWS CLI configured with sufficient IAM permissions
- Terraform ≥ 1.5 installed
- Python 3.12 installed locally
- `lambda.zip` built with all dependencies (see step 2)
- GitHub secrets configured (see §2.3)

---

### 4.2 Step-by-Step Deployment

**Step 1 — Clone the repository**

```bash
git clone https://github.com/kylodeng/ai-delivery-source.git
cd ai-delivery-source
```

**Step 2 — Build the Lambda deployment package**

```bash
# [TODO: Is there an existing build script for lambda.zip?]
mkdir -p build/python
pip install pandas pyarrow boto3 -t build/python/
cp src/data_pipeline.py build/
cd build && zip -r ../lambda.zip . && cd ..
mv lambda.zip infra/lambda.zip
```

**Step 3 — Initialise and validate Terraform**

```bash
cd infra
terraform init
terraform validate
terraform plan -var="environment=dev" -out=tfplan
```

> Review plan output carefully. Confirm no unintended resource deletions.

**Step 4 — Apply Terraform**

```bash
terraform apply tfplan
```

Note the outputs:

```
landing_bucket  = "capco-data-landing-dev"
processed_bucket = "capco-data-processed-dev"
```

**Step 5 — Set required GitHub Actions secrets**

```bash
gh secret set ANTHROPIC_API_KEY  --repo kylodeng/ai-delivery-source
gh secret set GH_TOKEN           --repo kylodeng/ai-delivery-source
gh secret set SENDGRID_API_KEY   --repo kylodeng/ai-delivery-source
```

**Step 6 — Verify Lambda deployment**

```bash
aws lambda get-function --function-name data-ingest-dev
aws lambda invoke \
  --function-name data-ingest-dev \
  --payload '{"bucket":"capco-data-landing-dev","key":"raw/test.csv"}' \
  response.json
cat response.json
```

**Step 7 — Trigger a workflow smoke test**

```bash
# Manually trigger Tool 2 to confirm end-to-end AI pipeline works
gh workflow run tool2_tech_docs.yml --repo kylodeng/ai-delivery-source
gh run list --repo kylodeng/ai-delivery-source --limit 5
```

---

### 4.3 Rollback Steps

**Terraform rollback (infrastructure)**

```bash
cd infra
# Revert to previous Terraform state
terraform plan -var="environment=dev" -target=aws_lambda_function.ingest -out=rollback.tfplan
terraform apply rollback.tfplan

# Or: restore a previous state file from remote backend
# [TODO: Is a remote Terraform state backend (S3/Terraform Cloud) configured?]
terraform state pull > backup.tfstate   # take backup before any rollback
```

**Lambda function rollback (code only)**

```bash
# List available versions
aws lambda list-versions-by-function --function-name data-ingest-dev

# Roll back to a specific published version
aws lambda update-alias \
  --function-name data-ingest-dev \
  --name live \
  --function-version <previous-version-number>
# [TODO: Lambda versioning and aliases are not configured in main.tf — add them]
```

**GitHub Actions workflow rollback**

```bash
# Revert the workflow file to a previous commit
git log --oneline .github/workflows/
git revert <bad-commit-sha>
git push origin main
```

---

## 5. Monitoring & Alerting

### 5.1 AWS Lambda Metrics (CloudWatch)

| Metric | Namespace | Alert Threshold | Action |
|---|---|---|---|
| `Errors` | `AWS/Lambda` | > 0 in 5 min | Investigate CloudWatch Logs; check input CSV format |
| `Duration` | `AWS/Lambda` | > 25,000 ms (p99) | Risk of timeout; increase `timeout` in Terraform |
| `Throttles` | `AWS/Lambda` | > 5 in 5 min | Request Lambda concurrency increase |
| `Invocations` | `AWS/Lambda` | 0 for > 24h (if files expected) | Check S3 trigger configuration |
| `ConcurrentExecutions` | `AWS/Lambda` | > 80% of account limit | Scale or request limit increase |

> [TODO: CloudWatch alarms are not defined in `main.tf` — add `aws_cloudwatch_metric_alarm` resources]

### 5.2 CloudWatch Logs

| Log Group | What to Watch |
|---|---|
| `/aws/lambda/data-ingest-dev` | `ERROR`, `Failed:`, `Task timed out`, `ValidationError`, `ClientError` |
| `/aws/lambda/data-ingest-dev` | Monitor `failed_rows` count in structured log output — high values indicate data quality issues |

```bash
# Tail Lambda logs in real time
aws logs tail /aws/lambda/data-ingest-dev --follow

# Search for errors in last hour
aws logs filter-log-events \
  --log-group-name /aws/lambda/data-ingest-dev \
  --start-time $(date -d '-1 hour' +%s000) \
  --filter-pattern "ERROR"
```

### 5.3 GitHub Actions Workflow Monitoring

| What to watch | How |
|---|---|
| Failed workflow runs | GitHub Actions UI → filter by ❌; or set up GitHub notification subscriptions |
| Workflow run duration trending up | Actions → select workflow → view run history durations |
| Claude API errors (`AuthenticationError`, `RateLimitError`) | Workflow logs → search for `anthropic` exceptions |
| Output repo write failures | Workflow logs → `write_output_file` step; check HTTP response codes |

> [TODO: Set up GitHub Actions status badge in README for each workflow]
> [TODO: Configure Slack or Teams notifications for failed workflow runs via `actions/slack-notify` or equivalent]

### 5.4 S3 Data Quality Monitoring

| Metric | Check |
|---|---|
| Failed row rate > 10% | Monitor `failed` count in Lambda return value via CloudWatch Logs Insights |
| No new files processed in 24h | CloudWatch alarm on `Invocations = 0` |
| Processed bucket growth anomaly | S3 Storage Lens or CloudWatch `BucketSizeBytes` metric |

```bash
# CloudWatch Logs Insights — failed row rate
# Run in AWS Console under CloudWatch → Logs Insights
# Log group: /aws/lambda/data-ingest-dev
fields @timestamp, @message
| filter @message like /failed/
| stats sum(failed) as total_failed by bin(1h)
```

### 5.5 Security Monitoring

> ⚠️ **Critical:** Hardcoded AWS credentials exist in `src/data_pipeline.py` and a hardcoded `DB_PASSWORD` in `infra/main.tf`. These must be treated as compromised.

| Check | Action |
|---|---|
| AWS IAM credential exposure | Enable AWS GuardDuty; set up alert on `UnauthorizedAccess:IAMUser/*` findings |
| Overly permissive IAM policy (`s3:*` on `*`) | Restrict to specific bucket ARNs immediately; monitor via AWS Access Analyzer |
| S3 buckets have no encryption | Add SSE-S3 or SS