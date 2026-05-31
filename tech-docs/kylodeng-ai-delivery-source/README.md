# ai-delivery-source

## 1. Project Overview

This repository contains a customer data ingestion pipeline (an AWS Lambda function triggered by S3 uploads) alongside five AI-powered GitHub Actions workflows that use Claude to automate code review, technical documentation, business documentation, test generation, and UAT facilitation. All AI-generated outputs are written to a companion repository (`ai-delivery-outputs`) and optionally delivered by email via SendGrid.

---

## 2. Tech Stack

| Component | Technology | Version/Notes |
|---|---|---|
| Runtime language | Python | 3.12 |
| AI model | Anthropic Claude | `claude-sonnet-4-6` |
| CI/CD platform | GitHub Actions | Node 24 runners |
| Data pipeline compute | AWS Lambda | Python 3.12 runtime |
| Data pipeline storage | AWS S3 | Two buckets: landing + processed |
| IaC | Terraform (HashiCorp AWS provider) | `~> 5.0` |
| Data processing | pandas | [TODO: exact version not pinned in any requirements file] |
| Email delivery | SendGrid | [TODO: exact SDK version not pinned] |
| GitHub/Claude API client | `anthropic`, `requests` | Installed via `pip install anthropic requests` |
| Output format | Parquet (processed), CSV (raw input) | via pandas |

---

## 3. Architecture

The repository has two distinct layers that interact as follows:

**Data pipeline layer:**
1. A CSV file lands in the `capco-data-landing-{env}/raw/` S3 prefix.
2. An S3 event notification triggers the `data-ingest-{env}` Lambda function.
3. `data_pipeline.lambda_handler` downloads the CSV, validates each row (required fields, email format, age range), and writes valid rows as Parquet to `capco-data-processed-{env}/processed/`.
4. Validation failures are collected and returned in the Lambda response but not persisted separately.

**AI automation layer (GitHub Actions):**
Five independent workflows each invoke a corresponding Python script under `.github/scripts/`. All scripts share `shared.py`, which provides:
- A Claude API wrapper (`call_claude`)
- GitHub API helpers (fetch repo files, fetch PR diffs, write files, post PR comments)
- SendGrid email delivery and audit logging helpers

Each workflow writes its outputs (markdown reports, test files, CSV packs) to the separate `ai-delivery-outputs` repository via the GitHub Contents API, and optionally sends an email notification to `NOTIFY_EMAIL`.

```
Source repo (this repo)
  │
  ├── .github/workflows/tool[1-5].yml   ← trigger conditions
  │         │
  │         └── .github/scripts/tool[1-5].py
  │                   │
  │                   ├── shared.py ──► Anthropic Claude API
  │                   │             ──► GitHub API (read source, write outputs)
  │                   │             ──► SendGrid (email notification)
  │                   └────────────► ai-delivery-outputs repo
  │
  └── src/data_pipeline.py ──► AWS Lambda ──► S3 (landing → processed)
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
pip install anthropic requests boto3 pandas pyarrow
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

5. **Run a script manually (example: code review tool in repo mode)**

```bash
export REVIEW_MODE=repo
export SOURCE_REPO_OWNER=kylodeng
export SOURCE_REPO_NAME=ai-delivery-source
export GITHUB_RUN_URL="https://github.com/kylodeng/ai-delivery-source/actions"
python .github/scripts/tool1_code_review.py
```

6. **(Optional) Initialise Terraform for the data pipeline infrastructure**

```bash
cd infra
terraform init
terraform plan
```

---

## 5. Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | — | Anthropic API key for Claude calls |
| `GH_TOKEN` | Yes | — | GitHub Personal Access Token with repo read/write permissions |
| `SENDGRID_API_KEY` | Yes | — | SendGrid API key for email notifications |
| `OUTPUT_REPO` | No | `ai-delivery-outputs` | Name of the GitHub repository where AI outputs are written |
| `OUTPUT_REPO_OWNER` | No | `GITHUB_REPOSITORY_OWNER` env value | GitHub owner/org of the output repository |
| `NOTIFY_EMAIL` | No | `kylo.deng@capco.com` | Recipient email address for delivery notifications |
| `SENDER_EMAIL` | No | `kylo.deng@capco.com` | Sender email address used in SendGrid requests |
| `SOURCE_REPO_OWNER` | Yes (in workflow) | `github.repository_owner` | Owner of the source repository being analysed |
| `SOURCE_REPO_NAME` | Yes (in workflow) | `github.event.repository.name` | Name of the source repository being analysed |
| `GITHUB_RUN_URL` | Yes (in workflow) | Constructed by Actions | URL of the current Actions run, included in reports |
| `REVIEW_MODE` | No (tool 1) | `repo` | `pr` or `repo` — controls what tool 1 analyses |
| `PR_NUMBER` | Conditional (tool 1) | — | PR number to review; required when `REVIEW_MODE=pr` |
| `TEST_MODE` | No (tool 4) | `generate` | `generate` or `gap-analysis` |
| `UAT_MODE` | No (tool 5) | `generate` | `generate` or `analyse` |
| `RELEASE_VERSION` | Yes (tools 3 & 5) | — | Version string e.g. `1.0.0`, used in document filenames and headings |
| `PROJECT_NAME` | Yes (tool 3) | — | Human-readable project name for business documents |
| `USER_STORIES` | No (tool 5) | — | Pasted acceptance criteria / user stories for UAT pack generation |
| `UAT_RESULTS_PATH` | Conditional (tool 5) | — | Path in `OUTPUT_REPO` to completed UAT results CSV; required when `UAT_MODE=analyse` |
| `LANDING_BUCKET` | Yes (Lambda) | — | Name of the S3 landing bucket; read by `lambda_handler` when not passed in the event |

---

## 6. Running Tests

[TODO: Are there any existing tests in this repository, or is all test generation handled by Tool 4 writing outputs to ai-delivery-outputs?]

To trigger AI-generated test generation for source files, use Tool 4 via GitHub Actions:

```bash
# Via GitHub CLI — manually dispatch the workflow
gh workflow run tool4_auto_testing.yml \
  -f test_mode=generate
```

To run a gap analysis against existing tests:

```bash
gh workflow run tool4_auto_testing.yml \
  -f test_mode=gap-analysis
```

Generated test files are written to the `ai-delivery-outputs` repository, not executed in CI automatically. [TODO: Is there a step to run the generated tests and report results back?]

---

## 7. Deployment

### Infrastructure (Terraform)

```bash
cd infra
terraform init
terraform plan -var="environment=dev" -var="aws_region=us-east-1"
terraform apply -var="environment=dev" -var="aws_region=us-east-1"
```

To deploy to a different environment:

```bash
terraform apply -var="environment=prod" -var="aws_region=us-east-1"
```

Terraform outputs after apply:

| Output | Description |
|---|---|
| `landing_bucket` | Name of the S3 landing bucket |
| `processed_bucket` | Name of the S3 processed bucket |

### Lambda function packaging

[TODO: How is `lambda.zip` built and uploaded? The Terraform resource references `filename = "lambda.zip"` but there is no build script in the repository.]

### GitHub Actions workflows

The five workflows run automatically based on their triggers. To invoke any workflow manually:

```bash
# Example: generate business docs for a named release
gh workflow run tool3_business_docs.yml \
  -f project_name="Data Ingestion Pipeline" \
  -f release_version="1.0.0"

# Example: generate UAT test pack for a release version
gh workflow run tool5_uat.yml \
  -f uat_mode=generate \
  -f release_version="1.0.0" \
  -f user_stories="As a data engineer I want..."

# Example: analyse completed UAT results
gh workflow run tool5_uat.yml \
  -f uat_mode=analyse \
  -f release_version="1.0.0" \
  -f uat_results_path="uat/kylodeng-ai-delivery-source/v1.0.0/UAT_RESULTS_SHEET.csv"
```

All required secrets (`ANTHROPIC_API_KEY`, `GH_TOKEN`, `SENDGRID_API_KEY`) must be set in the repository's **Settings → Secrets and variables → Actions** before any workflow will succeed.

---

## 8. Known Issues / TODOs

The following issues are extracted directly from code comments:

| Location | Severity | Issue |
|---|---|---|
| `src/data_pipeline.py` line 12–13 | **CRITICAL** | `AWS_ACCESS_KEY` and `AWS_SECRET_KEY` are hardcoded in source code. Comment says: *"TODO: move this to secrets manager"* |
| `infra/main.tf` — `aws_lambda_function.ingest` | **CRITICAL** | `DB_PASSWORD = "SuperSecret123!"` is hardcoded as a Lambda environment variable. Should use SSM Parameter Store or Secrets Manager |
| `infra/main.tf` — `aws_s3_bucket.landing` | **HIGH** | Landing S3 bucket has no server-side encryption and no public access block configured |
| `infra/main.tf` — `aws_iam_role_policy.lambda_policy` | **HIGH** | IAM policy grants `s3:*` on `Resource: "*"` — overly permissive; should be scoped to specific bucket ARNs |
| `infra/main.tf` — `aws_s3_bucket.landing` | **LOW** | *"TODO: add tags"* — resource tagging is absent |
| `src/data_pipeline.py` — `get_all_pending_files` | **MEDIUM** | S3 `list_objects_v2` call has no pagination; files beyond the first page (1,000 objects) will be silently dropped |
| `src/data_pipeline.py` — `process_csv` | **MEDIUM** | No error handling if the downloaded CSV is malformed (e.g. encoding errors, truncated file) |
| `src/data_pipeline.py` — `lambda_handler` | **LOW** | The `except Exception` block swallows all errors; specific exception types should be caught separately |
| `infra/main.tf` | **N/A** | No disaster recovery, no multi-region setup, no CloudWatch monitoring or alerting configured |
| `.github/scripts/tool2_tech_docs.py` | **N/A** | `build_index` function contains a truncated variable reference (`{r` instead of `{repo}`) — likely a copy/paste bug |
| All workflows | **N/A** | `shared.py` imports `send_email`, `email_html`, and `write_audit_entry` but these functions are not present in the provided `shared.py` file — implementations may be missing or truncated |