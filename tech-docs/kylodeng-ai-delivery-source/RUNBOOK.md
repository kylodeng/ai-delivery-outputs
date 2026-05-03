# Operational Runbook — `kylodeng/ai-delivery-source`

> **Last updated:** [TODO: insert date]
> **Runbook owner:** [TODO: insert team/name]
> **Version:** 1.0

---

## 1. Service Overview

`kylodeng/ai-delivery-source` is a GitHub-Actions-driven AI delivery automation platform that orchestrates five Claude-powered workflows: automated code review (Tool 1), technical documentation generation (Tool 2), business documentation generation (Tool 3), AI-assisted test generation and coverage analysis (Tool 4), and UAT facilitation (Tool 5). Each workflow is triggered by repository events (pull requests, merges, tags, branch creation) or on a schedule, invokes the Anthropic Claude API (`claude-sonnet-4-6`) to produce structured outputs, and writes results to a companion output repository (`ai-delivery-outputs`) via the GitHub Contents API. Notification emails are dispatched via SendGrid, and an audit log is maintained for all AI-generated artefacts. The core runtime workload is an AWS Lambda function (`data-ingest-<env>`) that ingests customer CSV files from an S3 landing bucket (`capco-data-landing-<env>`), validates and transforms records, and writes Parquet output to a processed S3 bucket (`capco-data-processed-<env>`).

---

## 2. Health Checks

Run these checks to confirm all components are operational.

### GitHub Actions Workflows

| Check | How to verify |
|---|---|
| All 5 workflows are enabled | `Actions` tab → confirm no workflows show "disabled" badge |
| Last run of each workflow succeeded | Check green tick on most recent run for each `tool*.yml` |
| Scheduled workflows are firing | Verify cron runs appear at expected times (Mon 08:00, Sun 06:00, Wed 07:00 UTC) |
| Artifact uploads present | Each code-review run should produce a `code-review-<run_id>` artifact |

### Output Repository

| Check | How to verify |
|---|---|
| `ai-delivery-outputs` repo is accessible | `https://github.com/<owner>/ai-delivery-outputs` returns HTTP 200 |
| Recent commits from workflows exist | Confirm commits in past 7 days with messages matching workflow commit patterns |
| `tech-docs/` folder is populated | Browse `tech-docs/<owner>-<repo>/` for `README.md`, `ARCHITECTURE.md`, `RUNBOOK.md` |

### AWS Lambda (Data Pipeline)

| Check | How to verify |
|---|---|
| Lambda function exists and is active | `aws lambda get-function --function-name data-ingest-<env>` returns `"State": "Active"` |
| Lambda invocations are succeeding | CloudWatch Metrics → `data-ingest-<env>` → `Errors` count = 0 |
| S3 landing bucket exists | `aws s3 ls s3://capco-data-landing-<env>/` returns without error |
| S3 processed bucket exists | `aws s3 ls s3://capco-data-processed-<env>/` returns without error |
| S3 event trigger is configured | `aws s3api get-bucket-notification-configuration --bucket capco-data-landing-<env>` shows Lambda ARN |

### External API Dependencies

| Check | How to verify |
|---|---|
| Anthropic API reachable | `curl https://api.anthropic.com/v1/models -H "x-api-key: $ANTHROPIC_API_KEY"` returns HTTP 200 |
| SendGrid API reachable | `curl https://api.sendgrid.com/v3/user/profile -H "Authorization: Bearer $SENDGRID_API_KEY"` returns HTTP 200 |
| GitHub API reachable | `curl -H "Authorization: Bearer $GH_TOKEN" https://api.github.com/rate_limit` shows remaining quota > 0 |

---

## 3. Common Failure Scenarios

| Symptom | Likely Cause | Resolution Steps |
|---|---|---|
| Workflow fails with `KeyError: 'ANTHROPIC_API_KEY'` | Secret not set or misspelled in repository secrets | 1. Go to repo **Settings → Secrets and variables → Actions**. 2. Verify `ANTHROPIC_API_KEY`, `GH_TOKEN`, `SENDGRID_API_KEY` are all present. 3. Re-run the failed workflow. |
| Claude returns non-JSON / `ValueError: Could not parse Claude response as JSON` | Model returned markdown-wrapped or truncated JSON; prompt regression | 1. Check raw response in workflow logs (DEBUG lines printed before exception). 2. Increase `max_tokens` in `call_claude()` if truncation suspected. 3. Re-run workflow — transient model behaviour is common. 4. If persistent, inspect prompt changes in recent commits. |
| `tool1_code_review` posts no PR comment | `GH_TOKEN` lacks `pull-requests: write` permission, or PR number env var not set | 1. Verify token scope: `curl -H "Authorization: Bearer $GH_TOKEN" https://api.github.com/rate_limit` — check `x-oauth-scopes` header. 2. Check `PR_NUMBER` env var is populated in workflow logs. 3. Confirm token owner has write access to the repo. |
| `write_output_file` fails with 404 | `ai-delivery-outputs` repo does not exist or `OUTPUT_REPO_OWNER` is wrong | 1. Confirm `ai-delivery-outputs` repo exists under the correct owner. 2. Check `OUTPUT_REPO_OWNER` env var in workflow. 3. Verify `GH_TOKEN` has `contents: write` on the output repo. |
| GitHub API rate limit exceeded (HTTP 403 / 429) | Too many API calls within 1-hour window (5000 req/hr for PAT) | 1. Check rate limit: `curl -H "Authorization: Bearer $GH_TOKEN" https://api.github.com/rate_limit`. 2. Wait until `reset` timestamp. 3. Reduce `max_files` in `get_repo_files()` calls if this is chronic. 4. Consider using a GitHub App token with higher limits. |
| Lambda function returns `statusCode: 500` | Malformed CSV, missing `key` in event payload, or S3 permission error | 1. Check CloudWatch Logs: `/aws/lambda/data-ingest-<env>`. 2. Verify the triggering S3 object key is under `raw/` and ends with `.csv`. 3. Confirm Lambda IAM role has `s3:GetObject` on the landing bucket. 4. Test with a known-good CSV via manual invocation (see Useful Commands). |
| Lambda cannot read from / write to S3 | IAM policy detached, or bucket name mismatch | 1. `aws iam get-role-policy --role-name lambda-ingest-role --policy-name lambda-s3-policy` — verify `s3:*` on `*` is present (note: **reduce this scope as a security improvement**). 2. Confirm `LANDING_BUCKET` env var on Lambda matches actual bucket name. |
| S3 landing bucket trigger not firing Lambda | Notification configuration missing or Lambda invoke permission absent | 1. `aws s3api get-bucket-notification-configuration --bucket capco-data-landing-<env>`. 2. `aws lambda get-policy --function-name data-ingest-<env>` — verify S3 has `lambda:InvokeFunction` permission. 3. Re-apply Terraform: `terraform apply`. |
| SendGrid email not delivered | Invalid `SENDGRID_API_KEY`, sender not verified, or recipient bounced | 1. Check SendGrid Activity Feed for delivery status. 2. Verify `SENDER_EMAIL` (`noreply@ai-delivery.capco.com`) is a verified sender in SendGrid. 3. Rotate key if authentication errors appear. |
| `tool2_tech_docs` skips on push | Push was to a `docs/**` or `**.md` path (excluded in `paths-ignore`) | Expected behaviour. Push a non-doc file change to `main` to trigger, or use `workflow_dispatch`. |
| `tool5_uat` does not trigger on branch creation | Branch name does not match `release/*` pattern | 1. Verify branch was created as `release/<version>` (e.g. `release/1.2.0`). 2. Check the `if:` condition in `tool5_uat.yml`. |
| Terraform apply fails: bucket already exists | S3 bucket names are globally unique; name collision | 1. `terraform state list` to check if resource is tracked. 2. If orphaned: `terraform import aws_s3_bucket.landing capco-data-landing-<env>`. 3. If name truly conflicts, change `var.environment` or bucket naming convention. |
| Hardcoded AWS credentials in `data_pipeline.py` cause auth failures after rotation | Credentials hardcoded in source; rotation breaks them | **⚠️ Security issue.** 1. Immediately rotate `AKIAIOSFODNN7EXAMPLE` credentials in AWS IAM. 2. Migrate to IAM role-based auth (Lambda execution role already exists — remove hardcoded keys). 3. Remove `AWS_ACCESS_KEY` / `AWS_SECRET_KEY` from `data_pipeline.py`. |

---

## 4. Deployment Procedure

### Prerequisites

- AWS CLI configured with appropriate permissions
- Terraform ≥ 1.5 installed
- Python 3.12 installed locally
- GitHub PAT (`GH_TOKEN`) with `repo` and `contents:write` scopes
- All secrets configured in GitHub repository settings

---

### 4.1 Infrastructure Deployment (Terraform)

```bash
# Step 1: Navigate to infra directory
cd infra/

# Step 2: Initialise Terraform
terraform init

# Step 3: Review planned changes
terraform plan -var="environment=dev"

# Step 4: Apply (type 'yes' when prompted)
terraform apply -var="environment=dev"

# Step 5: Note outputs
terraform output landing_bucket
terraform output processed_bucket
```

### 4.2 Lambda Code Deployment

```bash
# Step 1: Package the Lambda function
cd src/
zip ../infra/lambda.zip data_pipeline.py

# Step 2: Update Lambda via Terraform (preferred)
cd ../infra/
terraform apply -var="environment=dev"

# OR update directly via AWS CLI
aws lambda update-function-code \
  --function-name data-ingest-dev \
  --zip-file fileb://lambda.zip
```

### 4.3 GitHub Actions Workflows

Workflows are deployed automatically on push to `main` — no manual step required. To enable a new workflow:

```bash
# Step 1: Ensure all secrets are set (one-time setup)
# Settings → Secrets and variables → Actions → New repository secret
# Required: ANTHROPIC_API_KEY, GH_TOKEN, SENDGRID_API_KEY

# Step 2: Push workflow YAML to .github/workflows/
git add .github/workflows/
git commit -m "chore: add/update workflow"
git push origin main
```

---

### 4.4 Rollback Steps

#### Lambda rollback

```bash
# List available versions
aws lambda list-versions-by-function --function-name data-ingest-dev

# Rollback to a previous version (publish versions first — currently not configured)
# [TODO: Is Lambda versioning/aliases enabled? If not, enable it before next deployment]

# Immediate rollback via Terraform with previous zip
git checkout <previous-commit> -- src/data_pipeline.py
cd src/ && zip ../infra/lambda.zip data_pipeline.py
cd ../infra/ && terraform apply -var="environment=dev"
```

#### Infrastructure rollback

```bash
# Revert to previous Terraform state
git checkout <previous-commit> -- infra/main.tf
cd infra/
terraform plan -var="environment=dev"   # Review carefully
terraform apply -var="environment=dev"
```

#### Workflow rollback

```bash
# Disable a broken workflow immediately via GitHub UI:
# Actions → <workflow> → ⋯ → Disable workflow

# OR revert the workflow YAML
git revert <commit-sha>
git push origin main
```

---

## 5. Monitoring & Alerting

### GitHub Actions

| What to watch | Where | Alert threshold |
|---|---|---|
| Workflow failure rate | Actions tab / GitHub API | Any failure on `main`-branch-triggered runs |
| Workflow duration | Actions tab | [TODO: What is the acceptable max runtime per tool?] |
| API rate limit remaining | Workflow logs or `/rate_limit` endpoint | Alert if remaining < 500 requests |

### AWS Lambda & S3

| Metric | Source | Alert threshold |
|---|---|---|
| `Errors` | CloudWatch Metrics → Lambda → `data-ingest-<env>` | > 0 in any 5-minute window |
| `Duration` | CloudWatch Metrics → Lambda | > 25,000 ms (approaching 30s timeout) |
| `Throttles` | CloudWatch Metrics → Lambda | > 0 sustained over 10 minutes |
| `ConcurrentExecutions` | CloudWatch Metrics → Lambda | [TODO: Set based on expected load] |
| S3 `5xxErrors` | CloudWatch Metrics → S3 | > 0 |
| S3 `NumberOfObjects` (raw/) | CloudWatch Metrics → S3 Storage Lens | Spike above [TODO: expected daily volume] |
| Failed row count in Lambda response | CloudWatch Logs | [TODO: Define acceptable failure % threshold] |

### CloudWatch Log Groups

```
/aws/lambda/data-ingest-dev      # Lambda execution logs
/aws/lambda/data-ingest-prod     # [TODO: confirm prod function name]
```

**Key log patterns to alert on:**

```
# Lambda errors
"Failed:"
"statusCode\": 500"

# Validation failures at scale
"failed_rows"

# AWS credential issues
"InvalidClientTokenId"
"AccessDenied"
```

### External APIs

| Service | What to monitor | Alert |
|---|---|---|
| Anthropic API | HTTP 429 (rate limit), HTTP 5xx in workflow logs | Any 5xx; sustained 429s |
| SendGrid | Bounce/block events in SendGrid Activity Feed | Delivery failures to `kylo.deng@capco.com` |
| GitHub API | `X-RateLimit-Remaining` header in logs | < 500 remaining |

### ⚠️ Security Monitoring (High Priority)

- **Alert immediately** if `AKIAIOSFODNN7EXAMPLE` IAM credentials are used anywhere — these are hardcoded example keys and should be rotated/removed.
- **Alert** on any use of the hardcoded `DB_PASSWORD = "SuperSecret123!"` Lambda environment variable.
- **Monitor** S3 bucket `capco-data-landing-<env>` for public access (currently no public access block is configured in Terraform).
- [TODO: Is AWS GuardDuty enabled on this account? If not, enable it.]
- [TODO: Is AWS CloudTrail enabled? All S3 and Lambda API calls should be logged.]

---

## 6. Escalation Path

| Level | Who | When to escalate | Contact |
|---|---|---|---|
| L1 — First response | On-call DevOps Engineer | Workflow failure, Lambda errors, S3 unavailability | [TODO: insert on-call rotation / PagerDuty link] |
| L2 — Platform team | Platform / Cloud Engineering | IAM issues, Terraform state corruption, multi-service outage | [TODO: insert team contact / Slack channel] |
| L3 — Security team | Information Security | Hardcoded credential exposure, unauthorised S3 access, GuardDuty findings | [TODO: insert security team contact / incident process] |
| L4 — Vendor support | Anthropic / AWS / SendGrid | API outage confirmed on vendor status page | [TODO: Anthropic support tier; AWS support plan level; SendGrid plan] |
| Business escalation | Project owner / Delivery lead | SLA breach, data loss, production data exposure | [TODO: insert name and contact] |

**Slack channels:** [TODO: insert relevant channels e.g. `#platform-alerts`, `#data-pipeline`]
**Incident management tool:** [TODO: insert e.g. PagerDuty, Opsgenie, Jira Service Desk]

---

## 7. Useful Commands

### GitHub Actions

```bash
# List recent workflow runs via GitHub API
curl -s -H "Authorization: Bearer $GH_TOKEN" \
  "https://api.github.com/repos/kylodeng/ai-delivery-