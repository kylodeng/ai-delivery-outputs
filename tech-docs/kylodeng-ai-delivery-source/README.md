# ai-delivery-source

## 1. Project Overview

This repository contains a customer data ingestion pipeline (a Python AWS Lambda function) alongside five AI-powered GitHub Actions workflows that automate software delivery tasks — code review, technical documentation, business documentation, test generation, and UAT facilitation. Each workflow reads source files from this repository, calls the Anthropic Claude API, and writes outputs (reports, docs, generated tests) to a companion repository (`ai-delivery-outputs`). The data pipeline itself ingests customer CSV files from S3, validates and transforms them, and writes Parquet output to a processed S3 bucket.

---

## 2. Tech Stack

| Component | Technology | Version/Notes |
|---|---|---|
| AI Model | Anthropic Claude | `claude-sonnet-4-6` |
| AI SDK | `anthropic` Python SDK | Latest (pip install) |
| Pipeline Runtime | Python | 3.12 |
| Data Processing | pandas | Latest (pip install) |
| Cloud Provider | AWS | us-east-1 (default) |
| Compute | AWS Lambda | python3.12 runtime |
| Storage | AWS S3 | Landing + processed buckets |
| IaC | Terraform | AWS provider `~> 5.0` |
| CI/CD | GitHub Actions | ubuntu-latest runners |
| Email Notifications | SendGrid | Via REST API |
| HTTP Client | `requests` | Latest (pip install) |

---

## 3. Architecture

The repository has two distinct concerns:

**Data Pipeline (`src/data_pipeline.py` + `infra/main.tf`)**
An S3 event triggers the Lambda function (`data-ingest-{env}`) whenever a `.csv` file is uploaded to the `raw/` prefix of the landing bucket (`capco-data-landing-{env}`). The Lambda downloads the file, validates each row (required fields, email format, age range), and writes valid rows as Parquet to the processed bucket (`capco-data-processed-{env}`) under the `processed/` prefix. Terraform provisions both S3 buckets, the Lambda function, and the IAM role/policy.

**AI Delivery Workflows (`.github/workflows/` + `.github/scripts/`)**
Five GitHub Actions workflows share a common Python utility layer (`shared.py`) that wraps the GitHub API, Claude API, and SendGrid. Each tool workflow checks out this repo, installs dependencies, and runs its corresponding script. Scripts read source/IaC files from the GitHub API, call Claude with a structured system prompt, and write Markdown/CSV outputs back to the `ai-delivery-outputs` repository via the GitHub Contents API. Email notifications are sent via SendGrid on completion. All five tools follow the same pattern:

```
Trigger → GitHub Actions runner → shared.py utilities → Claude API → output repo + email
```

| Tool | Trigger | Output |
|---|---|---|
| Tool 1: Code Review | PR open/sync, Monday cron, manual | PR comment + Markdown report |
| Tool 2: Tech Docs | Push to main, Sunday cron, manual | README, ARCHITECTURE, RUNBOOK |
| Tool 3: Business Docs | Version tag push, manual | Solution overview + gap questionnaire |
| Tool 4: Auto Testing | PR open/sync on src/, Wednesday cron, manual | Generated test files + coverage report |
| Tool 5: UAT | `release/*` branch creation, manual | UAT test pack CSV or defect report |

---

## 4. Local Development Setup

1. **Clone the repository**
   ```bash
   git clone https://github.com/kylodeng/ai-delivery-source.git
   cd ai-delivery-source
   ```

2. **Create and activate a Python virtual environment**
   ```bash
   python3.12 -m venv .venv
   source .venv/bin/activate
   ```

3. **Install Python dependencies**
   ```bash
   pip install anthropic requests pandas boto3
   ```

4. **Set required environment variables** (see [Environment Variables](#5-environment-variables) below)
   ```bash
   export ANTHROPIC_API_KEY="your-anthropic-key"
   export GH_TOKEN="your-github-pat"
   export SENDGRID_API_KEY="your-sendgrid-key"
   export OUTPUT_REPO_OWNER="kylodeng"
   export OUTPUT_REPO="ai-delivery-outputs"
   ```

5. **Run a tool script manually** (example: code review in repo mode)
   ```bash
   export REVIEW_MODE=repo
   export SOURCE_REPO_OWNER=kylodeng
   export SOURCE_REPO_NAME=ai-delivery-source
   export GITHUB_RUN_URL="https://github.com/kylodeng/ai-delivery-source/actions/runs/local"
   python .github/scripts/tool1_code_review.py
   ```

6. **Install and initialise Terraform** (for infrastructure changes)
   ```bash
   cd infra
   terraform init
   terraform plan
   ```

---

## 5. Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | — | API key for Anthropic Claude |
| `GH_TOKEN` | Yes | — | GitHub Personal Access Token with repo read/write permissions |
| `SENDGRID_API_KEY` | Yes | — | SendGrid API key for email notifications |
| `OUTPUT_REPO` | No | `ai-delivery-outputs` | Name of the GitHub repo where outputs are written |
| `OUTPUT_REPO_OWNER` | No | `GITHUB_REPOSITORY_OWNER` | GitHub owner/org of the output repo |
| `NOTIFY_EMAIL` | No | `kylo.deng@capco.com` | Recipient email address for notifications |
| `SENDER_EMAIL` | No | `kylo.deng@capco.com` | Sender email address (must be verified in SendGrid) |
| `LANDING_BUCKET` | No | — | S3 bucket name for raw CSV uploads (used by Lambda; can also be passed in the event payload) |
| `REVIEW_MODE` | No | `repo` | Set by workflow: `pr` or `repo` (Tool 1 only) |
| `PR_NUMBER` | No | — | PR number to review (Tool 1, when `REVIEW_MODE=pr`) |
| `TEST_MODE` | No | `generate` | `generate` or `gap-analysis` (Tool 4 only) |
| `UAT_MODE` | No | `generate` | `generate` or `analyse` (Tool 5 only) |
| `RELEASE_VERSION` | No | — | Version string e.g. `1.0.0` (Tools 3 and 5) |
| `PROJECT_NAME` | No | — | Human-readable project name (Tool 3) |
| `USER_STORIES` | No | — | Acceptance criteria / user stories pasted as text (Tool 5 generate mode) |
| `UAT_RESULTS_PATH` | No | — | Path in output repo to completed UAT results CSV (Tool 5 analyse mode) |
| `SOURCE_REPO_OWNER` | No | `GITHUB_REPOSITORY_OWNER` | Owner of the source repo being analysed |
| `SOURCE_REPO_NAME` | No | — | Name of the source repo being analysed |
| `GITHUB_RUN_URL` | No | — | URL of the current Actions run, included in reports |

> **Note:** In GitHub Actions, `ANTHROPIC_API_KEY`, `GH_TOKEN`, and `SENDGRID_API_KEY` must be set as [repository secrets](https://docs.github.com/en/actions/security-guides/encrypted-secrets).

---

## 6. Running Tests

[TODO: Are there existing test files in this repository, or are tests only generated as outputs by Tool 4?]

To generate tests for the data pipeline using Tool 4 locally:

```bash
export TEST_MODE=generate
export SOURCE_REPO_OWNER=kylodeng
export SOURCE_REPO_NAME=ai-delivery-source
python .github/scripts/tool4_auto_testing.py
```

Generated test files are written to the `ai-delivery-outputs` repository under `auto-tests/`.

To run a coverage gap analysis on existing tests:

```bash
export TEST_MODE=gap-analysis
python .github/scripts/tool4_auto_testing.py
```

---

## 7. Deployment

### Infrastructure (Terraform)

```bash
cd infra
terraform init
terraform plan -var="environment=dev" -var="aws_region=us-east-1"
terraform apply -var="environment=dev" -var="aws_region=us-east-1"
```

Package and deploy the Lambda function (after Terraform has created the role):

```bash
# From repo root
zip lambda.zip src/data_pipeline.py
aws lambda update-function-code \
  --function-name data-ingest-dev \
  --zip-file fileb://lambda.zip
```

### GitHub Actions Workflows

The five AI delivery workflows deploy automatically on their configured triggers. To trigger any workflow manually:

1. Go to **Actions** → select the workflow → **Run workflow**
2. Fill in the required inputs (e.g. `project_name`, `release_version`, `uat_mode`)

To trigger Tool 5 (UAT) automatically, create a `release/` branch:

```bash
git checkout -b release/1.0.0
git push origin release/1.0.0
```

To trigger Tool 3 (Business Docs) automatically, push a version tag:

```bash
git tag v1.0.0
git push origin v1.0.0
```

---

## 8. Known Issues / TODOs

The following issues were extracted directly from code comments and IaC:

| Location | Issue |
|---|---|
| `src/data_pipeline.py` line 8–9 | **CRITICAL — Hardcoded AWS credentials.** `AWS_ACCESS_KEY` and `AWS_SECRET_KEY` are in plain text. Comment says: `# TODO: move this to secrets manager` |
| `infra/main.tf` — `aws_s3_bucket.landing` | **S3 landing bucket has NO server-side encryption and NO public access block configured.** |
| `infra/main.tf` — `aws_iam_role_policy.lambda_policy` | **Overly permissive IAM policy:** `Action: s3:*` on `Resource: *` grants full S3 access to all buckets. |
| `infra/main.tf` — `aws_lambda_function.ingest` | **Hardcoded secret in Lambda environment variable:** `DB_PASSWORD = "SuperSecret123!"`. Comment says: `# Hardcoded secret - should use SSM or Secrets Manager` |
| `infra/main.tf` — `aws_s3_bucket.landing` | Missing resource tags. Comment says: `# TODO: add tags` |
| `src/data_pipeline.py` — `get_all_pending_files` | S3 `list_objects_v2` has no pagination implemented; will silently truncate results beyond 1,000 objects. |
| `src/data_pipeline.py` — `process_csv` | No error handling for malformed CSV files (comment: `# No error handling if CSV is malformed`). |
| `src/data_pipeline.py` — `lambda_handler` | Bare `except Exception` in the handler swallows all error types without re-raising. |
| `shared.py` — `send_email` | Source file is truncated; `send_email` function body is incomplete in the provided files. [TODO: Is the SendGrid payload complete?] |
| `tool2_tech_docs.py` — `build_index` | Source file is truncated mid-function; `build_index` body is incomplete. [TODO: What is the full index format?] |
| `tool3_business_docs.py` — `build_full_output` | Source file is truncated; return value and any secondary outputs are unclear. [TODO: What files does `build_full_output` write?] |
| `tool4_auto_testing.py` — `build_test_report` | Source file is truncated; report format beyond the summary table is unknown. [TODO: What additional sections does the test report contain?] |
| `tool5_uat.py` — `build_test_pack_csv` | Source file is truncated; CSV schema is not fully visible. [TODO: What columns does the UAT test pack CSV contain?] |
| All tools | No DR (disaster recovery) or monitoring/alerting configured for the Lambda function or S3 buckets. |
| All tools | Escalation path for operational issues is `[TODO: fill in team contacts]` (from `tool2_tech_docs.py` runbook prompt). |