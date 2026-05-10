# ai-delivery-source

## 1. Project Overview

This repository contains a customer data ingestion pipeline (an AWS Lambda function that reads CSV files from S3, validates and transforms them to Parquet) alongside five Claude AI-powered GitHub Actions workflows that automate software delivery tasks: code review, technical documentation, business documentation, test generation, and UAT facilitation. Each workflow reads source files or PR diffs, calls the Anthropic Claude API, and writes outputs to a companion repository (`ai-delivery-outputs`). Notifications are sent via SendGrid email.

---

## 2. Tech Stack

| Component | Technology | Version/Notes |
|---|---|---|
| Runtime language | Python | 3.12 |
| AI model | Anthropic Claude | `claude-sonnet-4-6` |
| AI SDK | `anthropic` (Python) | Latest compatible with pip |
| HTTP client | `requests` | Latest |
| Data processing | `pandas` | Latest |
| Cloud SDK | `boto3` | Latest |
| IaC | Terraform (AWS provider) | `~> 5.0` |
| Cloud provider | AWS | us-east-1 (default) |
| CI/CD | GitHub Actions | ubuntu-latest runners |
| Email delivery | SendGrid | REST API v3 |
| Output format | Parquet (processed data), Markdown (docs) | — |

---

## 3. Architecture

The repository has two distinct layers:

**Data Pipeline (`src/data_pipeline.py` + `infra/main.tf`)**
An S3-triggered AWS Lambda function (`data-ingest-{env}`) fires whenever a `.csv` file is uploaded to the `raw/` prefix of the landing bucket (`capco-data-landing-{env}`). The Lambda validates each row (required fields, email format, age range), converts valid rows to Parquet, and writes the result to `capco-data-processed-{env}` under a `processed/` prefix. Failed rows are counted but not persisted anywhere.

**AI Delivery Workflows (`.github/workflows/` + `.github/scripts/`)**
Five independent GitHub Actions workflows each install Python 3.12, run a corresponding script in `.github/scripts/`, and rely on a shared utility module (`shared.py`). `shared.py` provides: Claude API calls, GitHub API helpers (fetch files, fetch PR diffs, write files, post PR comments), SendGrid email sending, and audit logging. All five scripts write their outputs (Markdown reports, test files, CSV test packs) to a separate GitHub repository (`ai-delivery-outputs`) via the GitHub Contents API.

```
PR / push / schedule / tag / branch creation
        │
        ▼
GitHub Actions workflow (tool1–5 .yml)
        │
        ▼
Python script (.github/scripts/tool*.py)
        │  uses
        ▼
shared.py ──► Anthropic Claude API (claude-sonnet-4-6)
        │
        ├──► GitHub API ──► ai-delivery-outputs repo (reports/docs/tests)
        ├──► GitHub API ──► source repo PR comments (tool 1)
        └──► SendGrid API ──► email notification
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

3. **Install Python dependencies**

```bash
pip install anthropic requests pandas boto3
```

4. **Set required environment variables** (see [Environment Variables](#5-environment-variables) section)

```bash
export ANTHROPIC_API_KEY="your-anthropic-key"
export GH_TOKEN="your-github-pat"
export SENDGRID_API_KEY="your-sendgrid-key"
export OUTPUT_REPO="ai-delivery-outputs"
export OUTPUT_REPO_OWNER="kylodeng"
export NOTIFY_EMAIL="you@example.com"
export SENDER_EMAIL="noreply@example.com"
```

5. **Set source repo context** (required by workflow scripts)

```bash
export SOURCE_REPO_OWNER="kylodeng"
export SOURCE_REPO_NAME="ai-delivery-source"
export GITHUB_RUN_URL="https://github.com/kylodeng/ai-delivery-source/actions/runs/0"
```

6. **Run a script directly** (example: tech docs generation)

```bash
python .github/scripts/tool2_tech_docs.py
```

7. **(Optional) For the data pipeline only — set up AWS credentials**

```bash
export AWS_ACCESS_KEY_ID="your-aws-key"
export AWS_SECRET_ACCESS_KEY="your-aws-secret"
export AWS_DEFAULT_REGION="us-east-1"
```

> ⚠️ The current source code contains hardcoded AWS credentials in `src/data_pipeline.py`. Do not use real credentials there. See [Known Issues](#8-known-issues--todos).

---

## 5. Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | — | Anthropic API key for Claude calls |
| `GH_TOKEN` | Yes | — | GitHub Personal Access Token (needs `repo` scope for reading source repo and writing to output repo) |
| `SENDGRID_API_KEY` | Yes | — | SendGrid API key for email notifications |
| `OUTPUT_REPO` | No | `ai-delivery-outputs` | Name of the GitHub repo where generated outputs are written |
| `OUTPUT_REPO_OWNER` | No | Value of `GITHUB_REPOSITORY_OWNER` | GitHub owner/org of the output repo |
| `NOTIFY_EMAIL` | No | `kylo.deng@capco.com` | Recipient address for email notifications |
| `SENDER_EMAIL` | No | `kylo.deng@capco.com` | Sender address used in SendGrid emails |
| `SOURCE_REPO_OWNER` | Yes (in workflows) | `github.repository_owner` | Owner of the source repository being analysed |
| `SOURCE_REPO_NAME` | Yes (in workflows) | `github.event.repository.name` | Name of the source repository being analysed |
| `GITHUB_RUN_URL` | No | — | URL of the current Actions run, included in outputs |
| `REVIEW_MODE` | No | `repo` | Tool 1 only: `pr` to review a specific PR, `repo` for full repo scan |
| `PR_NUMBER` | No | — | Tool 1 only: PR number to review when `REVIEW_MODE=pr` |
| `TEST_MODE` | No | `generate` | Tool 4 only: `generate` new tests or `gap-analysis` of existing tests |
| `UAT_MODE` | No | `generate` | Tool 5 only: `generate` test pack or `analyse` completed results CSV |
| `RELEASE_VERSION` | No | — | Tool 3 & 5: version string e.g. `1.0.0` |
| `PROJECT_NAME` | No | Repository name | Tool 3: human-readable project name for business docs |
| `USER_STORIES` | No | — | Tool 5: acceptance criteria / user stories pasted as text |
| `UAT_RESULTS_PATH` | No | — | Tool 5 (analyse mode): path in output repo to completed results CSV |
| `LANDING_BUCKET` | No | From `event.bucket` | Lambda only: S3 landing bucket name |

---

## 6. Running Tests

[TODO: Are there any existing tests in this repository? No test files were found in the provided source.]

The **Tool 4 Auto Testing** workflow can generate tests for this repository automatically:

```bash
# Trigger manually via GitHub Actions UI (workflow_dispatch)
# Or run the script locally after setting environment variables:
python .github/scripts/tool4_auto_testing.py
```

Generated test files are written to the `ai-delivery-outputs` repository, not run in this repository's CI pipeline.

[TODO: Is there a `requirements.txt` or `pyproject.toml` that pins dependency versions for reproducible test runs?]

---

## 7. Deployment

### Infrastructure (Terraform)

1. **Install Terraform** (version compatible with AWS provider `~> 5.0`)

2. **Configure AWS credentials**

```bash
export AWS_ACCESS_KEY_ID="your-key"
export AWS_SECRET_ACCESS_KEY="your-secret"
export AWS_DEFAULT_REGION="us-east-1"
```

3. **Initialise Terraform**

```bash
cd infra
terraform init
```

4. **Review the plan**

```bash
terraform plan -var="environment=dev"
```

5. **Apply**

```bash
terraform apply -var="environment=dev"
```

6. **Package and deploy the Lambda function**

```bash
cd src
zip ../infra/lambda.zip data_pipeline.py
cd ../infra
terraform apply -var="environment=dev"
```

> The `aws_lambda_function` resource expects `lambda.zip` to exist in the `infra/` directory at apply time.

### GitHub Actions Workflows

No deployment step is required for the workflows themselves. They activate automatically once repository secrets are configured:

1. Go to **Settings → Secrets and variables → Actions** in the GitHub repository.
2. Add the following repository secrets: `ANTHROPIC_API_KEY`, `GH_TOKEN`, `SENDGRID_API_KEY`.
3. Workflows trigger automatically on their configured events (PR open, push to `main`, version tags, schedule, etc.) or can be run manually via **Actions → [workflow name] → Run workflow**.

[TODO: Is there a Terraform backend configured for remote state? None is defined in `infra/main.tf`.]

---

## 8. Known Issues / TODOs

The following issues were extracted directly from code comments:

| Location | Severity | Issue |
|---|---|---|
| `src/data_pipeline.py:12–13` | 🔴 CRITICAL | AWS credentials (`AWS_ACCESS_KEY` and `AWS_SECRET_KEY`) are hardcoded in source. Comment says: *"TODO: move this to secrets manager"* |
| `infra/main.tf` — `aws_lambda_function` | 🔴 CRITICAL | `DB_PASSWORD` is hardcoded as `"SuperSecret123!"` in Lambda environment variables. Comment: *"Hardcoded secret - should use SSM or Secrets Manager"* |
| `infra/main.tf` — `aws_iam_role_policy` | 🔴 HIGH | IAM policy grants `s3:*` on `Resource: "*"` — overly permissive full S3 access |
| `infra/main.tf` — `aws_s3_bucket.landing` | 🟠 HIGH | Landing S3 bucket has no server-side encryption and no public access block configured. Comment: *"NO encryption, NO public access block"* |
| `infra/main.tf` — `aws_s3_bucket.landing` | 🟡 MEDIUM | No resource tags defined. Comment: *"TODO: add tags"* |
| `src/data_pipeline.py` — `get_all_pending_files` | 🟡 MEDIUM | S3 `list_objects_v2` call has no pagination — will silently miss files beyond the first 1,000 results |
| `src/data_pipeline.py` — `process_csv` | 🟡 MEDIUM | No error handling if the downloaded CSV is malformed (e.g. encoding errors, wrong delimiter) |
| `src/data_pipeline.py` — `lambda_handler` | 🟡 MEDIUM | Top-level `except Exception` swallows all error types — comment reads *"bare except swallows all errors"* |
| `infra/main.tf` | 🟠 HIGH | No Terraform remote state backend configured — state is local only |
| `infra/main.tf` | 🟠 HIGH | No DR strategy, no multi-region setup, no CloudWatch monitoring or alerting configured |
| `shared.py` / all tools | 🟡 MEDIUM | `send_email`, `email_html`, and `write_audit_entry` are imported in all tool scripts but their implementations are not present in the provided files |
| All workflows | 🟡 LOW | `NOTIFY_EMAIL` and `SENDER_EMAIL` are hardcoded to `kylo.deng@capco.com` / `noreply@ai-delivery.capco.com` in workflow `env` blocks rather than being parameterised |