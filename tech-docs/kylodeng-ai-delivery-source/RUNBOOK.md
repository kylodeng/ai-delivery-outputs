# Operational Runbook — `kylodeng/ai-delivery-source`

> **Last updated:** [TODO: insert date]
> **Runbook owner:** [TODO: fill in team contacts]
> **Repo:** `https://github.com/kylodeng/ai-delivery-source`
> **Output repo:** `https://github.com/kylodeng/ai-delivery-outputs`

---

## 1. Service Overview

`ai-delivery-source` is a GitHub Actions–driven AI automation platform that uses Anthropic's Claude (model: `claude-sonnet-4-6`) to deliver five developer-productivity workflows: automated code review (Tool 1), technical documentation generation (Tool 2), business document drafting (Tool 3), AI-generated test scaffolding (Tool 4), and UAT test pack facilitation (Tool 5). Each tool is implemented as a Python script under `.github/scripts/`, orchestrated by a corresponding GitHub Actions workflow, and backed by an AWS data ingestion pipeline (`src/data_pipeline.py`) that processes customer CSV files from S3, validates them, and writes Parquet output to a processed S3 bucket via a Lambda function. All AI-generated artefacts are written to the `ai-delivery-outputs` repository and optionally emailed via SendGrid to `kylo.deng@capco.com`.

---

## 2. Health Checks

Run these checks to confirm the service is operating correctly.

### GitHub Actions Workflows

| Check | How to verify |
|---|---|
| All 5 workflows are enabled | Navigate to **Actions** tab → confirm no workflows are disabled |
| Latest run of each workflow is green | Actions tab → filter by workflow name → check last run status |
| `tool1_code_review` triggers on PRs | Open a test PR → confirm workflow run appears within ~60 s |
| `tool2_tech_docs` triggers on push to `main` | Push a trivial commit → confirm workflow starts |
| Scheduled runs fired (Mon/Wed/Sun) | Actions → filter by `schedule` event → verify last cron run timestamp |

### Secrets Availability

| Check | How to verify |
|---|---|
| `ANTHROPIC_API_KEY` present | Actions → any workflow run → step "Install dependencies" succeeds; if Claude calls fail with `KeyError`, secret is missing |
| `GH_TOKEN` present | Workflow can read/write `ai-delivery-outputs` repo |
| `SENDGRID_API_KEY` present | Email notification step does not 401 |

### AWS Infrastructure

| Check | How to verify |
|---|---|
| S3 landing bucket exists | `aws s3 ls s3://capco-data-landing-dev` |
| S3 processed bucket exists | `aws s3 ls s3://capco-data-processed-dev` |
| Lambda function deployed | `aws lambda get-function --function-name data-ingest-dev` |
| Lambda has an S3 trigger | `aws s3api get-bucket-notification-configuration --bucket capco-data-landing-dev` |
| Lambda responds to test event | See useful commands section |

### Output Repository

| Check | How to verify |
|---|---|
| Artefacts written after workflow run | Browse `https://github.com/kylodeng/ai-delivery-outputs` → `tech-docs/` and `code-reviews/` prefixes |
| Audit log entries present | [TODO: Where is the audit log written — file in output repo, or external store?] |

---

## 3. Common Failure Scenarios

| Symptom | Likely Cause | Resolution Steps |
|---|---|---|
| Workflow fails with `KeyError: 'ANTHROPIC_API_KEY'` | Secret not set in repo settings | Go to **Settings → Secrets and variables → Actions** → add `ANTHROPIC_API_KEY` |
| Workflow fails with `KeyError: 'GH_TOKEN'` | `GH_TOKEN` secret missing or lacks write permissions to output repo | Add/regenerate PAT with `repo` scope; store as `GH_TOKEN` secret |
| Claude returns non-JSON and `extract_json` raises `ValueError` | Claude wrapped output in unexpected markdown or hit a content-filter edge case | Check workflow logs for `[DEBUG] First 500 chars`; re-run the workflow (transient); if persistent, review the prompt in the relevant script |
| `tool1_code_review` posts no PR comment | `GH_TOKEN` lacks `pull-requests: write` permission, or PR number env var not set | Verify `REVIEW_MODE=pr` and `PR_NUMBER` are set in workflow env; confirm token scopes |
| `tool2_tech_docs` / `tool3_business_docs` writes empty output file | `get_repo_files()` returned no matching files (wrong extensions or empty repo) | Confirm source files match extensions `[.py, .js, .ts, .go, .tf, .yaml, .yml, .json]`; check `max_files` cap (20) isn't silently truncating |
| Lambda function times out (30 s limit) | Very large CSV uploaded; `pd.read_csv` blocks on oversized file | Increase Lambda `timeout` in `infra/main.tf`; implement streaming/chunked reads in `data_pipeline.py`; add S3 object size pre-check |
| Lambda returns `500` with S3 access error | IAM role `lambda-ingest-role` missing permissions, or hardcoded `AWS_ACCESS_KEY` in code has expired/been rotated | **Short-term:** rotate key in Secrets Manager. **Long-term:** remove hardcoded keys from `data_pipeline.py` — use instance role instead; see security notes |
| `get_all_pending_files` silently misses files | S3 `list_objects_v2` is not paginated; >1000 files in `raw/` prefix | Implement paginator: `client.get_paginator('list_objects_v2')` |
| SendGrid email not delivered | `SENDGRID_API_KEY` invalid, or `SENDER_EMAIL` not verified in SendGrid | Check SendGrid dashboard for bounce/block; verify sender domain; confirm secret value is current |
| `tool5_uat` workflow never triggers on branch create | Branch name does not match `refs/heads/release/*` pattern | Ensure release branches are named `release/x.y.z` exactly |
| Terraform apply fails with `BucketAlreadyExists` | Bucket names are globally unique; another account owns the name | Change `bucket` name in `infra/main.tf` or import existing resource: `terraform import aws_s3_bucket.landing <bucket-name>` |
| Terraform apply succeeds but Lambda can't write to processed bucket | IAM policy uses `s3:*` on `Resource: "*"` — should work, but SCP/org-level policy may override | Check AWS Organizations SCPs; confirm Lambda role has no explicit deny; review CloudTrail for `AccessDenied` events |
| Workflow scheduled run did not fire | GitHub Actions drops scheduled runs if the repo has no activity for 60 days | Trigger a manual `workflow_dispatch` to re-activate; consider a keep-alive commit strategy |
| `tool4_auto_testing` generates tests with real AWS calls (not mocked) | Claude did not follow mock instruction for that file | Manually review generated test file; add explicit mock annotation to prompt or source file docstring |

---

## 4. Deployment Procedure

### 4.1 Prerequisites

- AWS CLI configured with credentials for target environment
- Terraform ≥ 1.5 installed
- GitHub PAT (`GH_TOKEN`) with `repo` scope stored in repo secrets
- Anthropic API key stored as `ANTHROPIC_API_KEY` secret
- SendGrid API key stored as `SENDGRID_API_KEY` secret

### 4.2 Deploy Infrastructure (Terraform)

```bash
# Step 1 — Navigate to infra directory
cd infra/

# Step 2 — Initialise Terraform (first time or after provider changes)
terraform init

# Step 3 — Review planned changes (ALWAYS do this before apply)
terraform plan -var="environment=dev"

# Step 4 — Apply to dev environment
terraform apply -var="environment=dev"

# Step 5 — Confirm outputs
terraform output
# Expected:
#   landing_bucket   = "capco-data-landing-dev"
#   processed_bucket = "capco-data-processed-dev"

# Step 6 — Package and deploy Lambda code
zip -r lambda.zip src/data_pipeline.py
aws lambda update-function-code \
  --function-name data-ingest-dev \
  --zip-file fileb://lambda.zip

# Step 7 — Verify Lambda is active
aws lambda get-function-configuration \
  --function-name data-ingest-dev \
  --query 'State'
# Expected: "Active"
```

### 4.3 Deploy to Production

```bash
# Repeat steps above with environment=prod
terraform plan -var="environment=prod"
terraform apply -var="environment=prod"

aws lambda update-function-code \
  --function-name data-ingest-prod \
  --zip-file fileb://lambda.zip
```

### 4.4 Deploy GitHub Actions Workflows

GitHub Actions workflows deploy automatically when `.github/workflows/*.yml` files are merged to `main`. No manual step required. To force re-registration:

```bash
# Trigger any workflow manually to confirm it is registered
gh workflow run tool2_tech_docs.yml --repo kylodeng/ai-delivery-source
```

### 4.5 Rollback Steps

#### Rollback Lambda code

```bash
# List recent versions
aws lambda list-versions-by-function --function-name data-ingest-dev

# Roll back to a previous version (replace VERSION_NUMBER)
aws lambda update-alias \
  --function-name data-ingest-dev \
  --name live \
  --function-version VERSION_NUMBER

# OR re-upload the previous zip if versions aren't published
zip -r lambda.zip src/data_pipeline.py  # from previous git tag
aws lambda update-function-code \
  --function-name data-ingest-dev \
  --zip-file fileb://lambda.zip
```

#### Rollback Terraform infrastructure

```bash
# Revert to previous Terraform state snapshot
# [TODO: Is remote state (S3 backend + DynamoDB lock) configured? Currently not visible in main.tf]

git checkout <previous-commit> -- infra/main.tf
terraform plan -var="environment=dev"
terraform apply -var="environment=dev"
```

#### Rollback a GitHub Actions workflow change

```bash
# Revert the workflow YAML commit and push to main
git revert <commit-sha>
git push origin main
```

---

## 5. Monitoring & Alerting

### 5.1 GitHub Actions

| What to watch | Where to look |
|---|---|
| Workflow failure rate | **Actions** tab → filter by status = `failure`; set up GitHub notification for workflow failures in **Settings → Notifications** |
| Workflow run duration (p95 > 5 min = investigate) | Actions tab → individual run timestamps |
| Skipped scheduled runs | Actions → `schedule` event filter → gap in run history |
| Artifact upload failures | Step `Upload review JSON artifact` in tool1 logs |

### 5.2 AWS Lambda

| Metric | Recommended threshold | Where |
|---|---|---|
| `Errors` | Alert if > 0 in any 5-min window | CloudWatch → Lambda → `data-ingest-{env}` |
| `Duration` | Alert if p99 > 25,000 ms (near 30 s timeout) | CloudWatch |
| `Throttles` | Alert if > 0 | CloudWatch |
| `ConcurrentExecutions` | Watch for unexpected spikes | CloudWatch |
| Lambda logs | All `logger.error(...)` output | CloudWatch Logs → `/aws/lambda/data-ingest-{env}` |

[TODO: Are CloudWatch alarms or a monitoring tool (Datadog, Grafana, etc.) configured? None found in the Terraform.]

### 5.3 S3

| What to watch | Indicator |
|---|---|
| Files stuck in `raw/` prefix | S3 object age > [TODO: define SLA]; no corresponding file in `processed/` |
| Unexpected objects in root or other prefixes | S3 bucket policy / access logging |
| Bucket size growth | CloudWatch `BucketSizeBytes` metric per bucket |

### 5.4 Application Logs

| Log | Location | Key patterns to alert on |
|---|---|---|
| Lambda execution log | CloudWatch Logs `/aws/lambda/data-ingest-{env}` | `"Failed:"`, `"statusCode": 500`, `"Missing required field"` |
| GitHub Actions step logs | GitHub UI / Actions API | `JSON parse error`, `ValueError`, `403`, `401` |
| Audit log | [TODO: Where does `write_audit_entry()` write? File path or external store not determinable from code] | All entries should have a corresponding output artefact |

### 5.5 Email Delivery (SendGrid)

- Monitor SendGrid dashboard for bounces, spam reports, or delivery failures to `kylo.deng@capco.com`
- [TODO: Are email delivery failures surfaced back into the GitHub Actions workflow as a step failure?]

---

## 6. Escalation Path

| Level | Condition | Contact |
|---|---|---|
| L1 — On-call engineer | Workflow failure, Lambda error, S3 access issue | [TODO: On-call rotation or Slack channel] |
| L2 — Platform/DevOps team | Infrastructure issue (Terraform, IAM, AWS service outage) | [TODO: Team contact or PagerDuty policy] |
| L3 — Anthropic API issues | Claude API 5xx, model unavailable, rate limit exhaustion | Check [https://status.anthropic.com](https://status.anthropic.com); contact Anthropic support |
| L3 — SendGrid issues | Email delivery failure at scale | Check [https://status.sendgrid.com](https://status.sendgrid.com); contact SendGrid support |
| Security incident | Hardcoded credentials exposed (see `data_pipeline.py` and `main.tf`) | [TODO: Security team contact / CISO]; rotate `AWS_ACCESS_KEY` and `DB_PASSWORD` immediately |

> ⚠️ **CRITICAL SECURITY NOTE:** Hardcoded AWS credentials exist in `src/data_pipeline.py` and a hardcoded `DB_PASSWORD` exists in `infra/main.tf`. These must be rotated and moved to AWS Secrets Manager / SSM Parameter Store before any production deployment. Treat any exposure of these values as a **P1 security incident**.

---

## 7. Useful Commands

### GitHub Actions

```bash
# List all workflow runs (requires GitHub CLI)
gh run list --repo kylodeng/ai-delivery-source

# Watch a specific run in real time
gh run watch <run-id> --repo kylodeng/ai-delivery-source

# Manually trigger tech docs generation
gh workflow run tool2_tech_docs.yml \
  --repo kylodeng/ai-delivery-source

# Manually trigger code review on a specific PR
gh workflow run tool1_code_review.yml \
  --repo kylodeng/ai-delivery-source \
  -f review_mode=pr \
  -f pr_number=42

# Manually trigger UAT test pack generation
gh workflow run tool5_uat.yml \
  --repo kylodeng/ai-delivery-source \
  -f uat_mode=generate \
  -f release_version=1.0.0

# Download workflow artifacts
gh run download <run-id> --repo kylodeng/ai-delivery-source
```

### AWS Lambda

```bash
# Invoke Lambda manually with a test event
aws lambda invoke \
  --function-name data-ingest-dev \
  --payload '{"bucket":"capco-data-landing-dev","key":"raw/test.csv"}' \
  --cli-binary-format raw-in-base64-out \
  response.json && cat response.json

# Tail Lambda logs in real time
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
  --function-name data-ingest-dev

# Check Lambda