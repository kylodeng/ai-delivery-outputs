# ai-delivery-source

## 1. Project Overview

This repository contains a customer CSV data ingestion pipeline that reads files from an S3 landing bucket, validates and transforms them, and writes Parquet output to a processed S3 bucket via an AWS Lambda function. It also hosts five AI-powered GitHub Actions workflows that use Claude (Anthropic) to automate code review, technical documentation, business documentation, test generation, and UAT facilitation across the software delivery lifecycle. All AI-generated outputs are written to a separate GitHub repository (`ai-delivery-outputs`) and optionally emailed via SendGrid.

---

## 2. Tech Stack

| Component | Technology | Version/Notes |
|---|---|---|
| Pipeline language | Python | 3.12 |
| Data processing | pandas | [TODO: confirm version pinned in requirements] |
| AWS SDK | boto3 | [TODO: confirm version pinned in requirements] |
| Cloud runtime | AWS Lambda | python3.12 runtime |
| Storage | AWS S3 | Two buckets: landing (raw CSV) and processed (Parquet) |
| IaC | Terraform | AWS provider ~> 5.0 |
| AI model | Anthropic Claude | `claude-sonnet-4-6` |
| AI SDK | anthropic (Python) | [TODO: confirm version pinned in requirements] |
| HTTP client | requests | [TODO: confirm version pinned in requirements] |
| Email delivery | SendGrid API | v3 (REST) |
| CI/CD | GitHub Actions | ubuntu-latest, Node24 actions |

---

## 3. Architecture

The system has two distinct layers:

**Data Pipeline:** A CSV file dropped into the `raw/` prefix of the S3 landing bucket triggers the `data-ingest` Lambda function via an S3 bucket notification. The Lambda (`data_pipeline.lambda_handler`) downloads the file, validates each row against required fields and business rules, converts valid rows to Parquet, and writes the result to the `raw/` → `processed/` path in the same (or a separate) processed S3 bucket. Failed rows are counted and returned in the response but not currently persisted anywhere.

**AI Delivery Workflows:** Five GitHub Actions workflows (`.github/workflows/tool1_*.yml` through `tool5_*.yml`) each invoke a corresponding Python script under `.github/scripts/`. All scripts share common utilities from `shared.py` (Claude API calls, GitHub API helpers, SendGrid email, audit logging). Each workflow reads source files or PR diffs from the source repository via the GitHub API, sends the content to Claude, and writes structured Markdown or CSV outputs back to a separate `ai-delivery-outputs` repository. PR comments are posted directly via the GitHub Issues API.

```
S3 Landing Bucket (raw/*.csv)
        │  S3 ObjectCreated trigger
        ▼
  AWS Lambda (data_pipeline.py)
        │  validate + transform
        ▼
S3 Processed Bucket (processed/*.parquet)

GitHub Event (PR / push / tag / cron)
        │
        ▼
GitHub Actions Workflow (tool1–5 .yml)
        │  reads files / diff via GitHub API
        ▼
  Python Script (.github/scripts/)
        │  calls Claude API
        ▼
  Claude (claude-sonnet-4-6)
        │  returns structured text / JSON
        ▼
  Output Repo (ai-delivery-outputs)  +  PR comment  +  SendGrid email
```

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

3. **Install dependencies**

```bash
pip install anthropic requests boto3 pandas
```

> [TODO: Is there a `requirements.txt` or `pyproject.toml`? None was found in the provided files — add one to pin versions.]

4. **Set required environment variables** (see [Environment Variables](#5-environment-variables) section)

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
export GH_TOKEN="ghp_..."
export SENDGRID_API_KEY="SG...."
export OUTPUT_REPO="ai-delivery-outputs"
export OUTPUT_REPO_OWNER="kylodeng"
export NOTIFY_EMAIL="your@email.com"
export SENDER_EMAIL="your@email.com"
```

5. **Run a workflow script manually** (example: code review tool in repo mode)

```bash
export REVIEW_MODE=repo
export SOURCE_REPO_OWNER=kylodeng
export SOURCE_REPO_NAME=ai-delivery-source
export GITHUB_RUN_URL=http://localhost/run/1
python .github/scripts/tool1_code_review.py
```

6. **Initialise Terraform (for infrastructure changes)**

```bash
cd infra
terraform init
terraform plan -var="environment=dev"
```

---

## 5. Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | — | Anthropic API key for Claude calls |
| `GH_TOKEN` | Yes | — | GitHub personal access token with repo read/write permissions |
| `SENDGRID_API_KEY` | Yes | — | SendGrid API key for email notifications |
| `OUTPUT_REPO` | No | `ai-delivery-outputs` | Name of the GitHub repo where AI outputs are written |
| `OUTPUT_REPO_OWNER` | No | Value of `GITHUB_REPOSITORY_OWNER` | GitHub owner/org of the output repo |
| `NOTIFY_EMAIL` | No | `kylo.deng@capco.com` | Recipient email address for notifications |
| `SENDER_EMAIL` | No | `kylo.deng@capco.com` | Sender email address used with SendGrid |
| `SOURCE_REPO_OWNER` | Yes (workflows) | `github.repository_owner` | Owner of the source repo being analysed |
| `SOURCE_REPO_NAME` | Yes (workflows) | `github.event.repository.name` | Name of the source repo being analysed |
| `GITHUB_RUN_URL` | No | — | URL of the current Actions run (set automatically in CI) |
| `REVIEW_MODE` | No (Tool 1) | `repo` | Code review mode: `repo` or `pr` |
| `PR_NUMBER` | Conditional (Tool 1) | — | PR number to review when `REVIEW_MODE=pr` |
| `TEST_MODE` | No (Tool 4) | `generate` | Test tool mode: `generate` or `gap-analysis` |
| `UAT_MODE` | No (Tool 5) | `generate` | UAT tool mode: `generate` or `analyse` |
| `RELEASE_VERSION` | No (Tools 3, 5) | `0.1.0` | Version string used in generated documents |
| `PROJECT_NAME` | No (Tool 3) | Repository name | Human-readable project name for business docs |
| `USER_STORIES` | No (Tool 5) | — | Acceptance criteria pasted in for UAT pack generation |
| `UAT_RESULTS_PATH` | No (Tool 5, analyse mode) | — | Path in output repo to completed UAT results CSV |
| `LANDING_BUCKET` | Yes (Lambda) | — | S3 bucket name for raw CSV files (set in Lambda environment via Terraform) |

> **Security note:** `AWS_ACCESS_KEY` and `AWS_SECRET_KEY` are currently hardcoded in `src/data_pipeline.py`. These must be moved to AWS Secrets Manager or IAM role-based authentication before production use (see [Known Issues](#8-known-issues--todos)).

---

## 6. Running Tests

> [TODO: No test files or test runner configuration were found in the provided repository. The Tool 4 workflow generates tests and writes them to the `ai-delivery-outputs` repo — are generated tests intended to be copied back and run here?]

To trigger AI-generated test creation for the source files via GitHub Actions:

1. Open a pull request against `main` that modifies files under `src/` — Tool 4 will run automatically.
2. Or trigger manually via the Actions tab:

```
Workflow: "Tool 4 — Auto Testing"
Inputs:
  test_mode: generate   # or gap-analysis
```

Generated test files are written to the `ai-delivery-outputs` repository under a path corresponding to this repo.

---

## 7. Deployment

### Infrastructure (Terraform)

```bash
cd infra
terraform init
terraform plan -var="environment=dev" -var="aws_region=us-east-1"
terraform apply -var="environment=dev" -var="aws_region=us-east-1"
```

Terraform will create:
- S3 landing bucket: `capco-data-landing-<environment>`
- S3 processed bucket: `capco-data-processed-<environment>`
- Lambda function: `data-ingest-<environment>` (requires a pre-built `lambda.zip` in the `infra/` directory)
- IAM role and policy for Lambda
- S3 bucket notification to trigger Lambda on `raw/*.csv` uploads

> [TODO: How is `lambda.zip` built and where should it be placed before running `terraform apply`? No build script was found.]

### Lambda Deployment Package

> [TODO: No Makefile, build script, or CI step for packaging the Lambda zip was found. Provide build instructions.]

### GitHub Actions Workflows

The five AI delivery workflows run automatically on GitHub-hosted runners. No manual deployment is needed beyond configuring the required repository secrets:

1. In your GitHub repository, go to **Settings → Secrets and variables → Actions**.
2. Add the following repository secrets:

```
ANTHROPIC_API_KEY
GH_TOKEN
SENDGRID_API_KEY
```

3. Ensure the `ai-delivery-outputs` repository exists under the same GitHub owner and that `GH_TOKEN` has write access to it.

---

## 8. Known Issues / TODOs

The following issues were extracted directly from code comments:

| Location | Issue |
|---|---|
| `src/data_pipeline.py` line 8–9 | **CRITICAL — hardcoded AWS credentials** (`AWS_ACCESS_KEY`, `AWS_SECRET_KEY`). Comment: `# TODO: move this to secrets manager` |
| `src/data_pipeline.py` `get_all_pending_files()` | S3 `list_objects_v2` has no pagination — will silently truncate results beyond 1,000 objects |
| `src/data_pipeline.py` `process_csv()` | No error handling if the CSV file is malformed (comment in code) |
| `src/data_pipeline.py` `lambda_handler()` | Bare `except Exception` swallows all errors — comment: `# bare except swallows all errors` |
| `infra/main.tf` `aws_s3_bucket.landing` | Landing S3 bucket has **no server-side encryption** and **no public access block** configured |
| `infra/main.tf` `aws_iam_role_policy.lambda_policy` | **Overly permissive IAM policy** — grants `s3:*` on `Resource: "*"` (all S3 buckets, all actions) |
| `infra/main.tf` `aws_lambda_function.ingest` | **Hardcoded secret** in Lambda environment variable: `DB_PASSWORD = "SuperSecret123!"` — should use SSM Parameter Store or Secrets Manager |
| `infra/main.tf` `aws_s3_bucket.landing` | Comment: `# TODO: add tags` — no resource tags defined on the landing bucket |
| `infra/main.tf` | No CloudWatch logging, monitoring, or alerting configured for the Lambda function |
| `infra/main.tf` | No disaster recovery or multi-region setup |
| `infra/main.tf` | No S3 bucket versioning configured on either bucket |
| `.github/scripts/shared.py` | `send_email`, `email_html`, and `write_audit_entry` functions are imported by all tool scripts but their implementations are not present in the provided `shared.py` — file appears truncated |
| `.github/scripts/tool2_tech_docs.py` | `build_index` function appears truncated — references undefined variable `r` |
| `.github/scripts/tool4_auto_testing.py` | `build_test_report` function appears truncated |
| `.github/scripts/tool5_uat.py` | `build_test_pack_csv` function signature appears truncated |
| General | No `requirements.txt` or dependency lockfile found — dependency versions are unpinned |