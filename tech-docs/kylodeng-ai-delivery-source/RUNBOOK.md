# Operational Runbook — `kylodeng/ai-delivery-source`

---

## 1. Service Overview

`kylodeng/ai-delivery-source` is a **GitHub Actions-driven AI delivery automation platform** built on top of Anthropic's Claude API. It provides five automated workflows: AI-powered code review on pull requests (Tool 1), technical documentation generation on merge to `main` (Tool 2), business documentation generation on release tags (Tool 3), automated test generation and coverage gap analysis on PRs (Tool 4), and UAT test pack generation and defect analysis on release branches (Tool 5). At its core, the platform runs Python scripts that call Claude (`claude-sonnet-4-6`), interact with the GitHub API, send email notifications via SendGrid, and persist all outputs to a companion repository (`ai-delivery-outputs`). The primary application payload is an AWS Lambda-backed **customer CSV ingestion pipeline** (`src/data_pipeline.py`) that reads raw CSV files from an S3 landing bucket, validates and transforms them to Parquet, and writes results to a processed S3 bucket — infrastructure provisioned via Terraform in `infra/main.tf`.

---

## 2. Health Checks

### GitHub Actions Workflows

| Check | How to verify |
|---|---|
| All 5 workflows enabled | Navigate to **Actions** tab → confirm `Tool 1–5` workflows are listed and not disabled |
| Latest workflow runs passing | Actions tab → each workflow → last run shows green ✅ |
| Secrets configured | **Settings → Secrets and variables → Actions** → confirm `ANTHROPIC_API_KEY`, `GH_TOKEN`, `SENDGRID_API_KEY` all present |
| Output repo accessible | Visit `github.com/<owner>/ai-delivery-outputs` → confirm repo exists and is writable by the bot token |

### Lambda / Data Pipeline (AWS)

| Check | How to verify |
|---|---|
| Lambda function exists | `aws lambda get-function --function-name data-ingest-<env>` returns HTTP 200 |
| Lambda last invocation healthy | AWS Console → Lambda → `data-ingest-<env>` → Monitor → check for recent errors |
| S3 landing bucket reachable | `aws s3 ls s3://capco-data-landing-<env>/raw/` returns without error |
| S3 processed bucket reachable | `aws s3 ls s3://capco-data-processed-<env>/processed/` returns without error |
| S3 event trigger active | `aws s3api get-bucket-notification-configuration --bucket capco-data-landing-<env>` returns a `LambdaFunctionConfigurations` block |

### External API Dependencies

| Dependency | Health check |
|---|---|
| Anthropic API | `curl https://api.anthropic.com/v1/models -H "x-api-key: $ANTHROPIC_API_KEY"` returns HTTP 200 |
| SendGrid API | `curl https://api.sendgrid.com/v3/user/profile -H "Authorization: Bearer $SENDGRID_API_KEY"` returns HTTP 200 |
| GitHub API | `curl -H "Authorization: Bearer $GH_TOKEN" https://api.github.com/rate_limit` returns remaining > 0 |

---

## 3. Common Failure Scenarios

| Symptom | Likely Cause | Resolution Steps |
|---|---|---|
| Workflow fails with `KeyError: 'ANTHROPIC_API_KEY'` | Secret not set in GitHub repository | Go to Settings → Secrets → Actions → add `ANTHROPIC_API_KEY` |
| Workflow fails with `KeyError: 'GH_TOKEN'` | `GH_TOKEN` secret missing or expired | Regenerate a GitHub PAT with `repo` + `contents:write` scopes; update secret |
| Workflow fails with `KeyError: 'SENDGRID_API_KEY'` | SendGrid secret not configured | Add `SENDGRID_API_KEY` to repository secrets |
| Claude returns non-JSON and `ValueError: Could not parse Claude response as JSON` | Model returned markdown-wrapped or malformed JSON | Re-run the workflow; if persistent, check if `MODEL = claude-sonnet-4-6` is a valid model name — [TODO: confirm correct model identifier with Anthropic docs] |
| `write_output_file` fails with 404 | `ai-delivery-outputs` repo does not exist or `GH_TOKEN` lacks write access | Create the output repo; ensure PAT has `contents:write` on that repo |
| Lambda returns `{"statusCode": 500}` | CSV malformed, S3 permissions denied, or unhandled exception swallowed by bare `except` | Check CloudWatch Logs group `/aws/lambda/data-ingest-<env>`; look for the logged error string |
| Lambda processes 0 rows / empty output Parquet | All rows fail `validate_customer_record` | Inspect CloudWatch logs for `ValueError` messages; validate source CSV schema matches `[customer_id, email, age, country_code]` |
| S3 trigger not firing Lambda | Notification config missing or Lambda invoke permission not granted | Run `aws s3api get-bucket-notification-configuration`; check Lambda resource policy allows `s3.amazonaws.com` to invoke |
| `get_all_pending_files` returns truncated list (>1000 files) | `list_objects_v2` has no pagination — returns max 1000 objects | Manually paginate using `--continuation-token`; [TODO: fix pagination in code before production] |
| Hardcoded AWS credentials rejected | Credentials in `data_pipeline.py` are example/placeholder keys (not real) | Replace with IAM role-based auth; remove hardcoded keys immediately — **this is a critical security issue** |
| `DB_PASSWORD` in Lambda env var rejected by downstream service | Hardcoded `SuperSecret123!` in Terraform is placeholder | Rotate credential; migrate to AWS Secrets Manager or SSM Parameter Store |
| Tool 2 docs not generated after merge to `main` | Workflow skipped because changed files matched `paths-ignore` (`docs/**` or `**.md`) | Trigger manually via `workflow_dispatch`; or push a non-doc file to `main` |
| Tool 5 UAT workflow not triggering on branch create | Branch name does not start with `release/` | Rename branch to `release/<version>` format e.g. `release/1.2.0` |
| GitHub API rate limit hit mid-workflow | PAT used across too many concurrent runs | Check `X-RateLimit-Remaining` header; add `time.sleep()` between API calls; [TODO: implement exponential backoff in `shared.py`] |
| Email notifications not delivered | SendGrid API key invalid or sender domain not verified | Verify `kylo.deng@capco.com` / `noreply@ai-delivery.capco.com` in SendGrid sender authentication |
| S3 landing bucket not encrypted | Terraform provisions bucket without SSE enabled | Apply `aws_s3_bucket_server_side_encryption_configuration` resource — **critical gap** |

---

## 4. Deployment Procedure

### Pre-Deployment Checklist

- [ ] All three secrets (`ANTHROPIC_API_KEY`, `GH_TOKEN`, `SENDGRID_API_KEY`) are set
- [ ] `ai-delivery-outputs` repository exists under the same owner
- [ ] AWS CLI configured with credentials that can deploy Terraform (`AdministratorAccess` or scoped equivalent)
- [ ] Terraform state backend configured [TODO: is a remote state backend (S3/Terraform Cloud) in use, or is state local only?]
- [ ] Lambda deployment package (`lambda.zip`) built and available

### Infrastructure Deployment (Terraform)

```bash
# Step 1 — Navigate to infra directory
cd infra/

# Step 2 — Initialise Terraform (first time or after provider changes)
terraform init

# Step 3 — Preview changes
terraform plan -var="environment=dev"

# Step 4 — Apply
terraform apply -var="environment=dev" -auto-approve

# Step 5 — Note outputs
terraform output landing_bucket
terraform output processed_bucket
```

### Lambda Code Deployment

```bash
# Step 1 — Package the Lambda function
cd src/
zip -r ../lambda.zip data_pipeline.py

# Step 2 — Upload to Lambda
aws lambda update-function-code \
  --function-name data-ingest-dev \
  --zip-file fileb://../lambda.zip \
  --region us-east-1

# Step 3 — Wait for update to complete
aws lambda wait function-updated \
  --function-name data-ingest-dev \
  --region us-east-1

# Step 4 — Verify deployment
aws lambda get-function --function-name data-ingest-dev \
  --query 'Configuration.LastModified'
```

### GitHub Workflows Deployment

The workflows are version-controlled in `.github/workflows/`. Changes are effective immediately on push to `main`.

```bash
# Step 1 — Make changes to workflow YAML or scripts
git checkout -b feature/workflow-update

# Step 2 — Commit and push
git add .github/
git commit -m "chore: update workflow configuration"
git push origin feature/workflow-update

# Step 3 — Open PR and verify Tool 1 code review fires automatically
# Step 4 — Merge to main — Tool 2 doc generation fires automatically
```

### Rollback Steps

#### Terraform Rollback

```bash
# Option A — Revert to previous Terraform state snapshot
# [TODO: confirm if remote state with versioning is configured]

# Option B — Destroy and re-apply previous version
git checkout <previous-commit> -- infra/main.tf
cd infra/
terraform apply -var="environment=dev" -auto-approve
```

#### Lambda Rollback

```bash
# List available versions
aws lambda list-versions-by-function \
  --function-name data-ingest-dev \
  --region us-east-1

# Roll back to a specific version by publishing an alias
aws lambda update-alias \
  --function-name data-ingest-dev \
  --name live \
  --function-version <previous-version-number> \
  --region us-east-1
```
> [TODO: Are Lambda versions/aliases in use? If not, the only rollback is redeploying a prior `lambda.zip`]

#### Workflow Script Rollback

```bash
# Revert a specific script to a known-good commit
git revert <bad-commit-sha>
git push origin main
```

---

## 5. Monitoring & Alerting

### Lambda Metrics (AWS CloudWatch)

| Metric | Namespace | Alert Threshold | Action |
|---|---|---|---|
| `Errors` | `AWS/Lambda` | > 0 in 5 min window | Investigate CloudWatch Logs |
| `Duration` | `AWS/Lambda` | > 25,000 ms (near 30s timeout) | Optimise CSV size or increase timeout |
| `Throttles` | `AWS/Lambda` | > 0 | Request concurrency limit increase |
| `ConcurrentExecutions` | `AWS/Lambda` | Near account limit | Scale or add reserved concurrency |

> [TODO: Are CloudWatch alarms and SNS notifications configured for these metrics?]

### Lambda Logs

```
Log group: /aws/lambda/data-ingest-<env>
Key log patterns to watch:
  - "Failed:" — indicates an unhandled exception in lambda_handler
  - "Processed X: " — confirms successful execution, check processed count vs failed count
  - "Missing required field" — data quality issues in source CSVs
  - "Invalid email" / "Age out of range" — validation failure rate increasing
```

### GitHub Actions Monitoring

| What to watch | Where |
|---|---|
| Workflow failure rate | GitHub Actions tab → filter by status `failure` |
| Claude API errors | Workflow logs — search for `anthropic` exceptions |
| GitHub API rate limit | Workflow logs — search for `403` or `rate limit` |
| Output repo commit history | `github.com/<owner>/ai-delivery-outputs/commits/main` |

### S3 Metrics

```
Enable S3 request metrics on:
  - capco-data-landing-<env>  — watch for 4xx/5xx errors on PutObject
  - capco-data-processed-<env> — watch for PutObject failures from Lambda
```

> [TODO: Are S3 access logs and CloudTrail enabled for audit purposes?]

### Key Log Strings to Alert On

| Log string | Source | Severity |
|---|---|---|
| `"statusCode": 500` | Lambda response | High |
| `Could not parse Claude response as JSON` | GitHub Actions logs | Medium |
| `rate limit` | GitHub Actions logs | Medium |
| `Failed:` | Lambda CloudWatch | High |
| `403` from GitHub API | GitHub Actions logs | High |

---

## 6. Escalation Path

| Level | Role | Contact | When to escalate |
|---|---|---|---|
| L1 | On-call Engineer | [TODO: fill in on-call rotation contact] | Initial triage — workflow failures, Lambda errors |
| L2 | Platform / DevOps Lead | [TODO: fill in DevOps lead contact] | Persistent failures, infrastructure issues, secret rotation |
| L3 | Repository Owner | kylo.deng@capco.com | Security incidents (exposed credentials), data loss, billing anomalies |
| External | Anthropic Support | [TODO: fill in Anthropic support URL/email] | Claude API outage or model availability issues |
| External | AWS Support | [TODO: fill in AWS support plan contact] | Lambda/S3 service disruptions |
| External | SendGrid Support | [TODO: fill in SendGrid support contact] | Email delivery failures affecting notifications |

> ⚠️ **Immediate escalation required** for:
> - Any exposure of the hardcoded credentials in `data_pipeline.py` or `infra/main.tf`
> - S3 bucket data exfiltration (overly permissive IAM policy `s3:*` on `*` is a live risk)

---

## 7. Useful Commands

### GitHub Actions — Trigger Workflows Manually

```bash
# Trigger Tool 1 Code Review on a specific PR
gh workflow run tool1_code_review.yml \
  -f review_mode=pr \
  -f pr_number=42

# Trigger Tool 2 Tech Docs generation
gh workflow run tool2_tech_docs.yml

# Trigger Tool 3 Business Docs for a release
gh workflow run tool3_business_docs.yml \
  -f project_name="Data Ingestion Pipeline" \
  -f release_version="1.0.0"

# Trigger Tool 4 Auto Testing in gap-analysis mode
gh workflow run tool4_auto_testing.yml \
  -f test_mode=gap-analysis

# Trigger Tool 5 UAT test pack generation
gh workflow run tool5_uat.yml \
  -f uat_mode=generate \
  -f release_version="1.0.0"

# Trigger Tool 5 UAT result analysis
gh workflow run tool5_uat.yml \
  -f uat_mode=analyse \
  -f release_version="1.0.0" \
  -f uat_results_path="uat/owner-repo/v1.0.0/UAT_RESULTS_SHEET.csv"

# List recent workflow runs
gh run list --limit 20

# Watch a specific run in real time
gh run watch <run-id>

# View logs for a failed run
gh run view <run-id> --log-failed
```

### AWS Lambda

```bash
# Invoke Lambda manually with a test event
aws lambda invoke \
  --function-name data-ingest-dev \
  --payload '{"bucket":"capco-data-landing-dev","key":"raw/test.csv"}' \
  --cli-binary-format raw-in-base64-out \
  output.json && cat output.json

# View recent Lambda logs (last 30 minutes)
aws logs filter-log-events \
  --log-group-name /aws/lambda/data-ingest-dev \
  --start-time $(date -d '30 minutes ago' +%s000) \
  --filter-pattern "Failed"

# Get Lambda configuration
aws lambda get-function-configuration \
  --function-name data-ingest-dev \
  --region us-east-1

# Check Lambda environment variables (redacts values)
aws lambda get-function-configuration \
  --function-name data-ingest-dev \
  --query 'Environment.Variables'
```

### AWS S3

```bash
# List pending files in landing bucket
aws s3 ls s3://capco-data-landing-dev/raw/ --recursive