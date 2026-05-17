# Operational Runbook — `kylodeng/ai-delivery-source`

> **Last updated:** [TODO: insert date]
> **Runbook owner:** [TODO: fill in team contacts]

---

## 1. Service Overview

`kylodeng/ai-delivery-source` is a GitHub Actions–driven AI delivery automation platform built on top of Anthropic's Claude API. It provides five automated workflows: AI-powered code review on pull requests (Tool 1), auto-generated technical documentation on merge to `main` (Tool 2), business/stakeholder documentation on release tags (Tool 3), AI-generated unit test scaffolding and coverage gap analysis on PRs (Tool 4), and UAT test pack generation and defect analysis on release branch creation (Tool 5). All five tools read source and IaC files from this repository, call the `claude-sonnet-4-6` model, and write their outputs (markdown reports, test files, CSV packs) to a companion repository named `ai-delivery-outputs`. Email notifications are dispatched via SendGrid, and results are also posted as PR comments where applicable. The core application payload is an AWS Lambda function (`data_pipeline.lambda_handler`) that ingests customer CSV files from an S3 landing bucket, validates and transforms them, and writes Parquet output to a processed S3 bucket — all provisioned via Terraform in `infra/main.tf`.

---

## 2. Health Checks

### 2.1 GitHub Actions Workflows

| Check | How to verify |
|---|---|
| Workflows are enabled | Navigate to **Actions** tab → confirm all 5 workflows are listed and not disabled |
| Latest run status | Each workflow shows green ✅ on its most recent run |
| Secrets are present | **Settings → Secrets and variables → Actions** → confirm `ANTHROPIC_API_KEY`, `GH_TOKEN`, `SENDGRID_API_KEY` exist |
| Output repo is reachable | `https://github.com/<owner>/ai-delivery-outputs` returns HTTP 200 and `GH_TOKEN` has write access |

### 2.2 AWS Lambda (Data Pipeline)

```bash
# Check Lambda function state
aws lambda get-function --function-name data-ingest-dev \
  --query 'Configuration.[State,LastUpdateStatus]'

# Invoke a smoke test (dry-run style)
aws lambda invoke \
  --function-name data-ingest-dev \
  --payload '{"bucket":"capco-data-landing-dev","key":"raw/smoke-test.csv"}' \
  --cli-binary-format raw-in-base64-out \
  response.json && cat response.json
```

### 2.3 S3 Buckets

```bash
# Confirm landing bucket exists and is reachable
aws s3 ls s3://capco-data-landing-dev/raw/ --summarize

# Confirm processed bucket exists
aws s3 ls s3://capco-data-processed-dev/ --summarize
```

### 2.4 External API Reachability

```bash
# Anthropic API (expects 401, not a connection error)
curl -o /dev/null -s -w "%{http_code}" \
  https://api.anthropic.com/v1/messages \
  -H "x-api-key: invalid" \
  -H "anthropic-version: 2023-06-01"

# SendGrid API (expects 401, not a connection error)
curl -o /dev/null -s -w "%{http_code}" \
  https://api.sendgrid.com/v3/mail/send
```

> **Expected:** HTTP 401 from both confirms endpoints are reachable; a connection error or 5xx indicates an outage.

---

## 3. Common Failure Scenarios

| Symptom | Likely Cause | Resolution Steps |
|---|---|---|
| GitHub Actions workflow fails at "Install dependencies" step | `pip install anthropic requests` fails; network issue or PyPI outage | Re-run the job; if persistent, pin versions explicitly in the `run:` step and check runner internet access |
| Tool 1/2/3/4/5 fails with `KeyError: 'ANTHROPIC_API_KEY'` | Secret not set in repository settings | Go to **Settings → Secrets → Actions**, add `ANTHROPIC_API_KEY` with the correct value |
| Tool 1/2/3/4/5 fails with `KeyError: 'GH_TOKEN'` | `GH_TOKEN` secret missing or token lacks write access to `ai-delivery-outputs` | Verify `GH_TOKEN` is set; confirm the token has `repo` scope on the output repository |
| `write_output_file` returns 404 or 422 | `ai-delivery-outputs` repo does not exist, or `OUTPUT_REPO_OWNER` is wrong | Create the output repo; verify `OUTPUT_REPO_OWNER` env var matches the correct GitHub org/user |
| Claude returns non-JSON response; `extract_json` raises `ValueError` | Model returned markdown-wrapped or malformed JSON; prompt injection edge case | Check the raw response in the Actions log (`[DEBUG]` lines); re-run the workflow; if recurring, review prompt in the relevant `SYSTEM` constant |
| `claude-sonnet-4-6` model not found / API 404 | Model name is incorrect or not available in your Anthropic tier | Verify the model name at [console.anthropic.com](https://console.anthropic.com); update `MODEL` in `shared.py` |
| Anthropic API rate limit (429) | Too many concurrent workflow runs or token quota exceeded | Stagger workflow schedules; check Anthropic dashboard for quota; add exponential back-off to `call_claude()` [TODO: back-off not currently implemented] |
| SendGrid email not delivered | `SENDGRID_API_KEY` invalid, sender domain not verified, or recipient in suppression list | Check SendGrid Activity Feed; verify sender `noreply@ai-delivery.capco.com` is authenticated; remove recipient from suppression list |
| Lambda `statusCode: 500` on CSV ingest | Malformed CSV, missing required columns, or S3 access denied | Check CloudWatch Logs `/aws/lambda/data-ingest-<env>`; validate CSV schema matches `required` fields in `validate_customer_record` |
| Lambda fails with `NoCredentialsError` or `AccessDenied` | Hardcoded AWS credentials in `data_pipeline.py` are expired or invalid | **Immediate:** rotate/remove hardcoded keys (`AWS_ACCESS_KEY`, `AWS_SECRET_KEY` in `data_pipeline.py`); use IAM execution role instead (see Security Note §4) |
| S3 `list_objects_v2` returns incomplete file list | `get_all_pending_files` does not implement pagination; >1000 objects in `raw/` | Implement paginator using `client.get_paginator('list_objects_v2')` [TODO: not yet implemented] |
| Lambda timeout (30 s) on large CSV files | Large file takes >30 s to download, validate, and write Parquet | Increase `timeout` in `infra/main.tf`; consider chunked processing or Step Functions for large files |
| Terraform `apply` fails with `BucketAlreadyExists` | Bucket name `capco-data-landing-<env>` already taken globally | Change `bucket` name in `infra/main.tf` to include a unique suffix (e.g., account ID) |
| Tool 2 generates empty or truncated docs | `get_repo_files` hit `max_files` limit (15/10) before finding key files | Increase `max_files` limits in `tool2_tech_docs.py`; ensure key files have supported extensions |
| Tool 5 UAT workflow does not trigger on branch creation | Branch name does not match `refs/heads/release/*` prefix | Ensure release branches are named `release/<version>` exactly (e.g., `release/1.2.0`) |
| PR comment not posted by Tool 1 | `GH_TOKEN` lacks `pull-requests: write` permission, or PR is from a fork | Grant correct permission; for fork PRs, use `pull_request_target` event with caution [TODO: security review needed] |

---

## 4. Deployment Procedure

> **⚠️ Security prerequisite before any deployment:** Remove hardcoded AWS credentials from `src/data_pipeline.py` and the hardcoded `DB_PASSWORD` from `infra/main.tf`. Store them in AWS Secrets Manager / SSM Parameter Store. See §5 for monitoring guidance.

### 4.1 Prerequisites

- AWS CLI configured with credentials for the target environment
- Terraform ≥ 1.x installed
- `GH_TOKEN`, `ANTHROPIC_API_KEY`, `SENDGRID_API_KEY` added to GitHub repository secrets
- `ai-delivery-outputs` repository created under the same GitHub owner

### 4.2 First-time Infrastructure Deployment

```bash
# 1. Clone the repository
git clone https://github.com/kylodeng/ai-delivery-source.git
cd ai-delivery-source

# 2. Package the Lambda function
cd src
zip -r ../infra/lambda.zip data_pipeline.py
cd ..

# 3. Initialise Terraform
cd infra
terraform init

# 4. Review the plan — inspect for security issues before applying
terraform plan -var="environment=dev"

# 5. Apply (type 'yes' when prompted)
terraform apply -var="environment=dev"

# 6. Note the outputs
terraform output landing_bucket
terraform output processed_bucket
```

### 4.3 Updating the Lambda Function

```bash
# 1. Make code changes in src/data_pipeline.py

# 2. Repackage
cd src
zip -r ../infra/lambda.zip data_pipeline.py
cd ..

# 3. Update via Terraform (preferred — maintains state)
cd infra
terraform apply -var="environment=dev"

# OR update directly via AWS CLI (hotfix only)
aws lambda update-function-code \
  --function-name data-ingest-dev \
  --zip-file fileb://infra/lambda.zip
```

### 4.4 Promoting to Production

```bash
# 1. Apply with production environment variable
cd infra
terraform apply -var="environment=prod"

# 2. Tag the release to trigger Tool 3 (business docs)
git tag v1.0.0
git push origin v1.0.0

# 3. Create release branch to trigger Tool 5 (UAT pack)
git checkout -b release/1.0.0
git push origin release/1.0.0
```

### 4.5 Updating GitHub Actions Workflows

```bash
# Workflows are live immediately on push to main — no separate deploy step needed.
# To test a workflow change without merging:
git checkout -b fix/workflow-change
git push origin fix/workflow-change
# Open a PR to trigger Tool 1 (code review) and Tool 4 (test generation)
```

### 4.6 Rollback Steps

#### Lambda Rollback

```bash
# List available versions
aws lambda list-versions-by-function --function-name data-ingest-dev \
  --query 'Versions[*].[Version,LastModified]' --output table

# Roll back to a previous version by publishing an alias
aws lambda update-alias \
  --function-name data-ingest-dev \
  --name live \
  --function-version <previous-version-number>

# OR re-apply previous Terraform state
cd infra
git checkout <previous-commit> -- .
terraform apply -var="environment=dev"
```

#### Terraform Infrastructure Rollback

```bash
# Revert infra/main.tf to previous commit
git revert HEAD  # or git checkout <sha> -- infra/main.tf
git push origin main

# Re-apply
cd infra
terraform apply -var="environment=dev"
```

#### GitHub Actions Workflow Rollback

```bash
# Revert the workflow file change
git revert HEAD  # reverts last commit
git push origin main
# Workflows update immediately
```

---

## 5. Monitoring & Alerting

### 5.1 AWS CloudWatch — Lambda

| Metric | Threshold to alert on | Suggested action |
|---|---|---|
| `Errors` | > 0 in any 5-minute window | Check CloudWatch Logs; inspect CSV for schema issues |
| `Duration` | P99 > 25 000 ms (near 30 s timeout) | Optimise processing or increase Lambda timeout |
| `Throttles` | > 0 | Request Lambda concurrency increase |
| `Invocations` | 0 for > 24 h during business hours | Check S3 trigger is still configured; check S3 bucket notification |
| Log group: `/aws/lambda/data-ingest-<env>` | ERROR or Exception log lines | Triage via CloudWatch Log Insights (see §7) |

> [TODO: Are CloudWatch alarms and SNS topics already configured, or do they need to be created?]

### 5.2 GitHub Actions Workflows

| What to watch | How |
|---|---|
| Failed workflow runs | GitHub **Actions** tab → filter by status "Failure"; enable email notifications in **Settings → Notifications** |
| Tool 1 code review score < 50 | Review the posted PR comment; flag for manual review before merge |
| Repeated `extract_json` / `clean_json` errors | Indicates Claude model behaviour change; review raw output in Actions logs |
| Workflow run duration > 10 minutes | May indicate Claude API slowness or rate limiting |

> [TODO: Is there a centralized alerting system (PagerDuty, Opsgenie, Slack webhook) where GitHub Actions failures should be routed?]

### 5.3 Security-Critical Log Patterns to Watch

```
# In Lambda logs — watch for these strings:
"NoCredentialsError"        # IAM/credentials issue
"AccessDenied"              # Over-broad or mis-scoped IAM
"Missing required field"    # Data quality degradation
"Failed:"                   # Bare-except catch-all in lambda_handler
```

> **⚠️ Active security risk:** The hardcoded `AWS_ACCESS_KEY` / `AWS_SECRET_KEY` in `src/data_pipeline.py` and `DB_PASSWORD = "SuperSecret123!"` in `infra/main.tf` should be treated as **compromised**. Rotate these credentials immediately and set up AWS Config / GitHub secret scanning to alert on future credential leaks.

### 5.4 S3 Data Pipeline Health

| Check | Command |
|---|---|
| Files accumulating unprocessed in `raw/` | `aws s3 ls s3://capco-data-landing-dev/raw/ --recursive \| wc -l` |
| Parquet files appearing in `processed/` | `aws s3 ls s3://capco-data-processed-dev/ --recursive \| tail -10` |
| S3 bucket notification is attached | `aws s3api get-bucket-notification-configuration --bucket capco-data-landing-dev` |

> [TODO: Is there a dead-letter queue (DLQ) configured for the Lambda? If not, failed invocations are silently dropped.]

---

## 6. Escalation Path

> [TODO: Fill in team contacts for each tier below]

| Level | Role | Contact | When to escalate |
|---|---|---|---|
| L1 | On-call engineer | [TODO: name / Slack / PagerDuty handle] | Workflow failure, Lambda error, S3 inaccessible |
| L2 | Platform / DevOps lead | [TODO: name / email] | Repeated failures after L1 resolution attempt, IAM/security incidents |
| L3 | Security team | [TODO: name / email / ticket queue] | Confirmed credential leak, unauthorised data access, `AccessDenied` on production data |
| L3 | Anthropic support | [support.anthropic.com](https://support.anthropic.com) | Persistent model API failures, quota issues, unexpected model behaviour |
| L3 | SendGrid support | [support.sendgrid.com](https://support.sendgrid.com) | Email delivery failures, domain authentication issues |
| Business | Solution owner | [TODO: name] | Data quality failures affecting downstream consumers |
| Business | kylo.deng@capco.com | kylo.deng@capco.com | Notify on all AI-generated output delivery (already wired as `NOTIFY_EMAIL`) |

---

## 7. Useful Commands

### GitHub Actions — Trigger Workflows Manually

```bash
# Trigger Tool 1 (code review) on a specific PR
gh workflow run tool1_code_review.yml \
  -f review_mode=pr \
  -f pr_number=42

# Trigger Tool 