# ai-delivery-source

## 1. Project Overview

This repository contains a customer data ingestion pipeline deployed on AWS, together with five AI-powered GitHub Actions workflows that automate software delivery tasks. The pipeline reads customer CSV files from S3, validates and transforms them, and writes Parquet output back to S3 via an AWS Lambda function. The five workflows use Anthropic's Claude API to perform automated code review, technical documentation generation, business documentation generation, test generation, and UAT facilitation.

---

## 2. Tech Stack

| Component | Technology | Version/Notes |
|---|---|---|
| Runtime language | Python | 3.12 |
| AI model | Anthropic Claude | `claude-sonnet-4-6` |
| Cloud provider | AWS | us-east-1 (default) |
| Compute | AWS Lambda | python3.12 runtime |
| Storage | AWS S3 | Landing + processed buckets |
| IaC | Terraform | AWS provider `~> 5.0` |
| Data processing | pandas | [TODO: exact version not pinned in requirements] |
| CI/CD | GitHub Actions | ubuntu-latest runners |
| Email notifications | SendGrid | [TODO: exact API version not specified] |
| HTTP client | requests | [TODO: exact version not pinned] |
| Anthropic SDK | anthropic (Python) | [TODO: exact version not pinned] |

---

## 3. Architecture

The repository has two distinct concerns that interact through GitHub Actions:

**Data pipeline:** An S3 event notification on the `capco-data-landing-{env}` bucket triggers the `data-ingest-{env}` Lambda function whenever a `.csv` file is placed under the `raw/` prefix. The Lambda runs `data_pipeline.lambda_handler`, which validates each customer record, converts valid rows to Parquet, and writes the output to the `capco-data-processed-{env}` bucket under the `processed/` prefix. Failed rows are counted and returned in the response but are not persisted.

**AI delivery workflows:** Five GitHub Actions workflows (`.github/workflows/tool[1-5]_*.yml`) each invoke a corresponding Python script (`.github/scripts/tool[1-5]_*.py`). All scripts share common utilities via `.github/scripts/shared.py`, which wraps the GitHub REST API, the Anthropic Messages API, SendGrid email, and output-file writing. Generated artefacts (reports, docs, test files) are committed to a separate repository named `ai-delivery-outputs` (same owner). Notifications are sent via SendGrid email after each run.

```
Source repo                         ai-delivery-outputs repo
┌──────────────────────────┐        ┌──────────────────────────┐
│  .github/workflows/      │        │  code-review/            │
│  tool1..tool5 *.yml      │──────▶ │  tech-docs/              │
│                          │        │  business-docs/          │
│  .github/scripts/        │        │  auto-tests/             │
│  shared.py + tool*.py    │        │  uat/                    │
└──────────────────────────┘        └──────────────────────────┘
         │  GitHub API / Anthropic API / SendGrid
         ▼
┌──────────────────────────┐
│  AWS (via Terraform)     │
│  S3 landing ──▶ Lambda   │
│           └──▶ S3 processed │
└──────────────────────────┘
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
source .venv/bin/activate   # Windows: .venv\Scripts\activate
```

3. **Install Python dependencies**

```bash
pip install anthropic requests pandas boto3 pyarrow
```

4. **Set required environment variables** (see [Environment Variables](#5-environment-variables) section)

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
export GH_TOKEN="ghp_..."
export SENDGRID_API_KEY="SG...."
export OUTPUT_REPO="ai-delivery-outputs"
export OUTPUT_REPO_OWNER="kylodeng"
export NOTIFY_EMAIL="your@email.com"
export SENDER_EMAIL="noreply@yourdomain.com"
```

5. **Initialise Terraform** (requires AWS credentials configured separately)

```bash
cd infra
terraform init
```

6. **Run a workflow script manually** (example: code review in repo mode)

```bash
export REVIEW_MODE=repo
export SOURCE_REPO_OWNER=kylodeng
export SOURCE_REPO_NAME=ai-delivery-source
export GITHUB_RUN_URL=http://localhost/run/1
python .github/scripts/tool1_code_review.py
```

---

## 5. Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | — | Anthropic API key for Claude calls |
| `GH_TOKEN` | Yes | — | GitHub personal access token with repo read/write permissions |
| `SENDGRID_API_KEY` | Yes | — | SendGrid API key for email notifications |
| `OUTPUT_REPO` | No | `ai-delivery-outputs` | Name of the GitHub repository where generated artefacts are written |
| `OUTPUT_REPO_OWNER` | No | Value of `GITHUB_REPOSITORY_OWNER` | GitHub owner/org of the output repository |
| `NOTIFY_EMAIL` | No | `kylo.deng@capco.com` | Recipient email address for notifications |
| `SENDER_EMAIL` | No | `kylo.deng@capco.com` | Sender email address used by SendGrid |
| `SOURCE_REPO_OWNER` | Yes (workflows) | `${{ github.repository_owner }}` | Owner of the source repository being analysed |
| `SOURCE_REPO_NAME` | Yes (workflows) | `${{ github.event.repository.name }}` | Name of the source repository being analysed |
| `GITHUB_RUN_URL` | Yes (workflows) | Set by Actions runner | Full URL of the current Actions run, included in reports |
| `REVIEW_MODE` | No | `repo` (cron), `pr` (PR event) | Tool 1 only: `pr` reviews a single PR diff; `repo` reviews repository files |
| `PR_NUMBER` | Conditional | — | Tool 1 only: PR number to review when `REVIEW_MODE=pr` |
| `RELEASE_VERSION` | Yes (tool 3, 5) | Tag name (push event) | Version string used in generated documents |
| `PROJECT_NAME` | Yes (tool 3) | Repository name (push event) | Human-readable project name for business docs |
| `TEST_MODE` | No | `generate` | Tool 4 only: `generate` creates new tests; `gap-analysis` analyses coverage gaps |
| `UAT_MODE` | No | `generate` | Tool 5 only: `generate` creates test pack; `analyse` processes completed results CSV |
| `USER_STORIES` | No | — | Tool 5 only: acceptance criteria / user stories pasted as text |
| `UAT_RESULTS_PATH` | Conditional | — | Tool 5 analyse mode: path in output repo to completed results CSV |
| `LANDING_BUCKET` | Yes (Lambda) | From event payload | S3 bucket name where raw CSV files are landed |

---

## 6. Running Tests

[TODO: Are there any existing tests in this repository? No test files were found in the provided source.]

To generate tests using Tool 4, trigger the workflow manually:

```bash
# Via GitHub CLI
gh workflow run tool4_auto_testing.yml \
  -f test_mode=generate
```

Or locally:

```bash
export TEST_MODE=generate
export SOURCE_REPO_OWNER=kylodeng
export SOURCE_REPO_NAME=ai-delivery-source
export GITHUB_RUN_URL=http://localhost/run/1
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

Package the Lambda before applying if `lambda.zip` does not exist:

```bash
cd src
zip ../infra/lambda.zip data_pipeline.py
cd ..
```

To deploy to a different environment:

```bash
terraform apply -var="environment=prod"
```

### GitHub Actions Workflows

Workflows run automatically based on their triggers. To run any workflow manually via the GitHub CLI:

```bash
# Tool 1 – Code Review (repo scan)
gh workflow run tool1_code_review.yml -f review_mode=repo

# Tool 1 – Code Review (single PR)
gh workflow run tool1_code_review.yml -f review_mode=pr -f pr_number=42

# Tool 2 – Tech Documentation
gh workflow run tool2_tech_docs.yml

# Tool 3 – Business Documentation
gh workflow run tool3_business_docs.yml \
  -f project_name="Data Ingestion Pipeline" \
  -f release_version="1.0.0"

# Tool 4 – Auto Testing
gh workflow run tool4_auto_testing.yml -f test_mode=generate

# Tool 5 – UAT (generate test pack)
gh workflow run tool5_uat.yml \
  -f uat_mode=generate \
  -f release_version="1.0.0"

# Tool 5 – UAT (analyse completed results)
gh workflow run tool5_uat.yml \
  -f uat_mode=analyse \
  -f release_version="1.0.0" \
  -f uat_results_path="uat/kylodeng-ai-delivery-source/v1.0.0/UAT_RESULTS_SHEET.csv"
```

The following secrets must be configured in the repository (`Settings → Secrets and variables → Actions`):

```
ANTHROPIC_API_KEY
GH_TOKEN
SENDGRID_API_KEY
```

---

## 8. Known Issues / TODOs

The following issues were extracted directly from code comments:

| Location | Severity | Issue |
|---|---|---|
| `src/data_pipeline.py` line 10–11 | **CRITICAL** | AWS access key and secret key are hardcoded in source code. Comment states: `# TODO: move this to secrets manager` |
| `infra/main.tf` (Lambda env vars) | **CRITICAL** | `DB_PASSWORD` is hardcoded as a plain-text string in the Lambda environment variable block. Comment states: `# Hardcoded secret - should use SSM or Secrets Manager` |
| `infra/main.tf` (`aws_s3_bucket.landing`) | **HIGH** | Landing S3 bucket has no server-side encryption and no public access block configured. Comment states: `# S3 landing bucket - NO encryption, NO public access block` |
| `infra/main.tf` (`aws_iam_role_policy`) | **HIGH** | Lambda IAM policy grants `s3:*` on `*` (all S3 actions on all resources). Comment states: `# Overly permissive policy - full S3 access` |
| `infra/main.tf` (`aws_s3_bucket.landing`) | **LOW** | Resource tags are missing. Comment states: `# TODO: add tags` |
| `src/data_pipeline.py` (`get_all_pending_files`) | **MEDIUM** | S3 `list_objects_v2` call has no pagination — results will be truncated at 1,000 objects. Comment states: `# SQL injection not applicable here but no pagination implemented` |
| `src/data_pipeline.py` (`lambda_handler`) | **MEDIUM** | Top-level exception handler uses a bare `except` which swallows all error types. Comment states: `# bare except swallows all errors` |
| `src/data_pipeline.py` (`process_csv`) | **LOW** | No error handling if the downloaded CSV is malformed (e.g. encoding errors, empty file). Comment states: `# No error handling if CSV is malformed` |
| `.github/scripts/shared.py` | **LOW** | `send_email`, `email_html`, and `write_audit_entry` functions are imported by all tool scripts but their implementations are not present in the provided files. [TODO: Are these functions defined elsewhere in `shared.py` or in a separate file not included here?] |
| General | — | No `requirements.txt` or `pyproject.toml` is present — dependency versions are unpinned. [TODO: Should a requirements file be added?] |
| General | — | No disaster recovery, cross-region replication, or monitoring/alerting configuration is defined in the Terraform. [TODO: Are these required for production?] |