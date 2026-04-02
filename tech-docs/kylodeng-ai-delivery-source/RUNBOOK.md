# Operational Runbook — `kylodeng/ai-delivery-source`

> **Last updated:** [TODO: insert date]
> **Runbook owner:** [TODO: fill in team contacts]
> **Version:** 0.1.0

---

## 1. Service Overview

`kylodeng/ai-delivery-source` is a GitHub-Actions-driven AI delivery platform that automates five software delivery tasks: automated code review (Tool 1), technical documentation generation (Tool 2), business documentation generation (Tool 3), automated test generation and coverage gap analysis (Tool 4), and UAT test pack generation and defect analysis (Tool 5). Each tool is implemented as a Python script under `.github/scripts/`, invoked by a corresponding GitHub Actions workflow. At runtime, the tools read source files and/or pull request diffs from GitHub, invoke Anthropic's Claude API (`claude-sonnet-4-6`) to generate outputs, write results as Markdown/CSV files to a companion output repository (`ai-delivery-outputs`), post review comments directly on pull requests, and send notification emails via SendGrid. The core data pipeline (`src/data_pipeline.py`) is deployed as an AWS Lambda function that reads CSV files from an S3 landing bucket (`capco-data-landing-<env>`), validates and transforms them, and writes Parquet output to a processed bucket (`capco-data-processed-<env>`). Infrastructure is managed via Terraform (`infra/main.tf`).

---

## 2. Health Checks

### 2.1 GitHub Actions Workflows

| Check | How to verify |
|---|---|
| All 5 workflows are enabled | GitHub UI → **Actions** tab → confirm no workflows show "disabled" |
| Most recent workflow runs succeeded | GitHub UI → Actions → confirm green ticks on last run of each tool |
| Secrets are present | Repo → **Settings → Secrets → Actions** → confirm `ANTHROPIC_API_KEY`, `GH_TOKEN`, `SENDGRID_API_KEY` exist |
| Output repo is reachable | `curl -H "Authorization: Bearer $GH_TOKEN" https://api.github.com/repos/$OUTPUT_REPO_OWNER/ai-delivery-outputs` → expect HTTP 200 |

### 2.2 AWS Lambda (Data Pipeline)

| Check | How to verify |
|---|---|
| Lambda function exists and is active | `aws lambda get-function --function-name data-ingest-<env>` → `"State": "Active"` |
| Lambda last invocation succeeded | AWS Console → Lambda → **Monitor** → check most recent CloudWatch log stream for no ERROR lines |
| S3 landing bucket exists | `aws s3 ls s3://capco-data-landing-<env>` → no `NoSuchBucket` error |
| S3 processed bucket exists | `aws s3 ls s3://capco-data-processed-<env>` → no `NoSuchBucket` error |
| S3 event notification is configured | `aws s3api get-bucket-notification-configuration --bucket capco-data-landing-<env>` → `LambdaFunctionConfigurations` present |

### 2.3 External API Dependencies

| Check | How to verify |
|---|---|
| Anthropic API reachable | `curl -s -o /dev/null -w "%{http_code}" https://api.anthropic.com` → expect 200/403 (not 5xx) |
| SendGrid API reachable | `curl -s -o /dev/null -w "%{http_code}" https://api.sendgrid.com/v3/user/profile -H "Authorization: Bearer $SENDGRID_API_KEY"` → expect 200 |

---

## 3. Common Failure Scenarios

| Symptom | Likely Cause | Resolution Steps |
|---|---|---|
| GitHub Actions workflow fails with `KeyError` on env var | A required secret (`ANTHROPIC_API_KEY`, `GH_TOKEN`, or `SENDGRID_API_KEY`) is missing or mis-named | 1. Go to Repo → Settings → Secrets → Actions. 2. Confirm all three secrets exist with exact names. 3. Re-run the failed workflow. |
| Tool 1 does not post a PR comment | `GH_TOKEN` lacks `pull-requests: write` permission, or token scope is too narrow | 1. Check token scopes: `curl -sI -H "Authorization: Bearer $GH_TOKEN" https://api.github.com \| grep x-oauth-scopes`. 2. Re-generate token with `repo` scope. 3. Update the `GH_TOKEN` secret. |
| Claude API call returns HTTP 429 (rate limit) | Too many concurrent workflow runs hitting Anthropic rate limits | 1. Check Anthropic usage dashboard for quota. 2. Re-run the failed job manually after ~60 s. 3. Consider staggering the scheduled cron times across the 5 workflows. |
| Claude API call returns HTTP 401 / `authentication_error` | `ANTHROPIC_API_KEY` is expired, revoked, or incorrect | 1. Log in to Anthropic console and verify key status. 2. Rotate the key. 3. Update `ANTHROPIC_API_KEY` in GitHub Secrets. 4. Re-run the workflow. |
| `write_output_file` fails with HTTP 404 | Output repo (`ai-delivery-outputs`) does not exist or `OUTPUT_REPO_OWNER` is wrong | 1. Confirm repo exists: `gh repo view $OUTPUT_REPO_OWNER/ai-delivery-outputs`. 2. Create it if missing: `gh repo create $OUTPUT_REPO_OWNER/ai-delivery-outputs --private`. 3. Ensure `GH_TOKEN` has write access to that repo. |
| Lambda returns `statusCode: 500` | CSV is malformed, S3 key does not exist, or AWS credentials in `data_pipeline.py` have expired | 1. Check CloudWatch Logs for the error message. 2. Confirm the S3 key exists: `aws s3 ls s3://<bucket>/<key>`. 3. **Rotate the hardcoded credentials** (see security note §7). 4. Re-upload the file to trigger re-processing. |
| Lambda times out (timeout = 30 s) | CSV file is too large or S3 download is slow | 1. Check file size: `aws s3api head-object --bucket <bucket> --key <key>`. 2. Increase Lambda timeout in Terraform (`timeout` attribute) and redeploy. 3. Consider chunking large files. |
| S3 event notification does not trigger Lambda | Lambda resource-based permission missing, or notification filter mismatch | 1. Check notification config: `aws s3api get-bucket-notification-configuration --bucket capco-data-landing-<env>`. 2. Verify Lambda has invoke permission for S3: `aws lambda get-policy --function-name data-ingest-<env>`. 3. Add missing permission if absent (see §7). 4. Confirm file is uploaded under `raw/` prefix with `.csv` suffix. |
| `get_all_pending_files` returns incomplete list | S3 `list_objects_v2` is not paginated; truncated at 1,000 objects | 1. Manually list full contents: `aws s3 ls s3://capco-data-landing-<env>/raw/ --recursive`. 2. Apply code fix in §7 to add pagination. 3. Reprocess any missed files manually. |
| `tool2_tech_docs.py` workflow triggers on every push to `main` even for doc-only changes | `paths-ignore` in workflow YAML may not match exact path patterns | 1. Review `.github/workflows/tool2_tech_docs.yml` `paths-ignore`. 2. Test path filter with `gh workflow run` dry run. 3. Add more specific ignore paths if needed. |
| Tool 5 UAT workflow does not trigger on release branch creation | Branch name does not match `refs/heads/release/*` pattern, or `create` event fires for tags too | 1. Confirm branch is named `release/<version>` exactly. 2. Check workflow run history for an unexpected skip. 3. Run manually via `workflow_dispatch` as a workaround. |
| Email notification not received | SendGrid key invalid, sender domain not verified, or recipient address is spam-filtered | 1. Check SendGrid activity feed for the send attempt. 2. Verify sender `noreply@ai-delivery.capco.com` is an authenticated sender in SendGrid. 3. Check recipient spam folder. 4. Confirm `SENDGRID_API_KEY` secret is current. |
| Terraform apply fails: `BucketAlreadyExists` | S3 bucket name globally conflicts | 1. `terraform state list` to check if bucket is already in state. 2. If in state, run `terraform refresh`. 3. If not, import: `terraform import aws_s3_bucket.landing capco-data-landing-<env>`. |

---

## 4. Deployment Procedure

### Prerequisites

- AWS CLI configured with credentials for the target account and region (`us-east-1`)
- Terraform ≥ 1.5 installed
- Python 3.12 installed locally
- `lambda.zip` build artifact containing `src/data_pipeline.py` and its dependencies
- GitHub repository secrets configured (see §2.1)

### Step-by-Step Deployment

**Step 1 — Build Lambda package**

```bash
# Create deployment package
cd src
pip install boto3 pandas pyarrow -t ./package
cp data_pipeline.py ./package/
cd package
zip -r ../../infra/lambda.zip .
cd ../..
```

**Step 2 — Initialise Terraform**

```bash
cd infra
terraform init
```

**Step 3 — Plan and review**

```bash
terraform plan -var="environment=prod" -out=tfplan.out
# Review output carefully — check for security group changes, IAM changes, bucket recreation
```

**Step 4 — Apply infrastructure**

```bash
terraform apply tfplan.out
```

Note the outputs:

```
landing_bucket  = "capco-data-landing-prod"
processed_bucket = "capco-data-processed-prod"
```

**Step 5 — Verify Lambda deployment**

```bash
aws lambda get-function --function-name data-ingest-prod
# Expect: "State": "Active", "LastUpdateStatus": "Successful"
```

**Step 6 — Smoke test**

```bash
# Upload a test CSV to trigger the Lambda
aws s3 cp tests/sample.csv s3://capco-data-landing-prod/raw/smoke-test.csv

# Watch Lambda logs
aws logs tail /aws/lambda/data-ingest-prod --follow
# Expect: "Processed smoke-test.csv: {...}" with no ERROR lines
```

**Step 7 — Confirm output**

```bash
aws s3 ls s3://capco-data-processed-prod/processed/
# Expect: smoke-test.parquet present
```

**Step 8 — Enable GitHub Actions workflows**

All 5 workflows activate automatically on the trigger conditions defined in their YAML files. Manually trigger Tool 2 to regenerate documentation post-deployment:

```bash
gh workflow run "Tool 2 — Tech Documentation" --repo kylodeng/ai-delivery-source
```

---

### Rollback Steps

**Lambda rollback (to previous version):**

```bash
# List published versions
aws lambda list-versions-by-function --function-name data-ingest-prod

# Rollback by pointing alias to previous version
aws lambda update-alias \
  --function-name data-ingest-prod \
  --name live \
  --function-version <PREVIOUS_VERSION>
```

> [TODO: Are Lambda versioning and aliases configured? Currently not visible in Terraform — add `publish = true` and an alias resource to enable this.]

**Infrastructure rollback (Terraform):**

```bash
cd infra
# Restore previous state snapshot if available
terraform apply -var="environment=prod" -target=aws_lambda_function.ingest
```

> [TODO: Is Terraform remote state (S3 backend + DynamoDB lock) configured? Currently not present in `main.tf`. Without it, state rollback is manual.]

**GitHub Actions rollback:**

```bash
# Revert a bad workflow file change
git revert <commit-sha>
git push origin main
```

---

## 5. Monitoring & Alerting

### 5.1 AWS Lambda & S3

| Metric / Log | Where to find | Alert threshold |
|---|---|---|
| Lambda invocation errors | CloudWatch → Metrics → Lambda → `data-ingest-<env>` → `Errors` | Alert if > 0 errors in 5 min window |
| Lambda duration | CloudWatch → `Duration` metric | Alert if P99 > 25,000 ms (near 30 s timeout) |
| Lambda throttles | CloudWatch → `Throttles` metric | Alert if > 0 throttles |
| Lambda log stream | CloudWatch Logs → `/aws/lambda/data-ingest-<env>` | Filter for `ERROR` and `Failed:` patterns |
| S3 landing bucket size | CloudWatch → S3 → `BucketSizeBytes` | [TODO: define expected max size / growth rate alert] |
| Failed validation rows | Application log: search for `"failed":` in Lambda response body | [TODO: define acceptable failure rate threshold] |

> **⚠️ Security note:** CloudWatch log streams will contain the hardcoded `AWS_ACCESS_KEY` (`AKIAIOSFODNN7EXAMPLE`) and `AWS_SECRET_KEY` values from `data_pipeline.py`. **Rotate these credentials immediately** (see §7). Enable CloudTrail to detect misuse.

### 5.2 GitHub Actions Workflows

| Signal | How to monitor |
|---|---|
| Workflow failure notifications | GitHub → Repo → Settings → Notifications → enable email on failed runs |
| Scheduled job missed | Check Actions tab every Monday/Wednesday/Sunday that the relevant tool ran |
| Claude API cost spike | Monitor Anthropic usage dashboard — each repo scan can consume ~8,000–16,000 tokens |
| Output repo disk usage | `gh api repos/$OUTPUT_REPO_OWNER/ai-delivery-outputs --jq '.size'` |

### 5.3 Key Log Patterns to Watch

```
# Lambda errors
aws logs filter-log-events \
  --log-group-name /aws/lambda/data-ingest-prod \
  --filter-pattern "ERROR"

# Lambda validation failures (high failed row count)
aws logs filter-log-events \
  --log-group-name /aws/lambda/data-ingest-prod \
  --filter-pattern '"failed"'

# GitHub Actions — Claude API errors (in Actions run logs)
# Search for: "authentication_error", "rate_limit_error", "overloaded_error"
```

### 5.4 Recommended Alarms (to create)

> [TODO: Are CloudWatch alarms or SNS topics currently provisioned? None found in `infra/main.tf` — add the following:]

- `data-ingest-prod-errors` — Lambda `Errors` > 0 for 5 min → SNS → email
- `data-ingest-prod-duration` — Lambda `Duration` P99 > 25000 ms → SNS → email
- `landing-bucket-object-count` — S3 object count in `raw/` prefix exceeding expected daily batch size

---

## 6. Escalation Path

| Level | Condition | Contact |
|---|---|---|
| L1 — On-call engineer | Any Lambda error, workflow failure, failed file processing | [TODO: on-call engineer name / PagerDuty rotation] |
| L2 — Platform/DevOps team | Persistent failures after L1 resolution steps, IAM/infrastructure issues, Terraform state corruption | [TODO: platform team Slack channel / email] |
| L3 — Solution owner / Tech lead | Security incident (exposed credentials, unauthorised S3 access), data loss, complete service outage | [TODO: tech lead name and contact] |
| External — Anthropic support | Sustained Claude API outage (check https://status.anthropic.com) | https://support.anthropic.com |
| External — AWS support | S3 or Lambda service incident (check https://health.aws.amazon.com) | [TODO: AWS support plan tier and case URL] |
| External — SendGrid support | Email delivery failures not resolved by key rotation | [TODO: SendGrid account contact] |

> **⚠️ Critical security escalation:** The file `src/data_pipeline.py` contains hardcoded AWS credentials (`AKIAIOSFODNN7EXAMPLE` / `wJalrXUtnFEMI/...`). If these are real credentials, treat this as a **P1 security incident**: immediately revoke them in AWS IAM,