# ai-delivery-source

## 1. Project Overview

This repository contains a customer CSV data ingestion pipeline (AWS Lambda + S3) alongside five AI-powered GitHub Actions workflows that automate software delivery tasks — code review, technical documentation, business documentation, test generation, and UAT facilitation. Each workflow calls the Anthropic Claude API to analyse repository source files and IaC, then writes outputs to a companion repository (`ai-delivery-outputs`). The source pipeline itself validates, transforms, and writes customer records from a raw S3 landing bucket to a processed S3 bucket in Parquet format.

---

## 2. Tech Stack

| Component | Technology | Version/Notes |
|---|---|---|
| AI/LLM | Anthropic Claude | Model: `claude-sonnet-4-6` |
| Automation runtime | GitHub Actions | Ubuntu Latest |
| Scripting language | Python | 3.12 |
| Python HTTP client | `requests` | Latest via pip |
| Anthropic SDK | `anthropic` | Latest via pip |
| Data pipeline runtime | AWS Lambda | Python 3.12 runtime |
| Data processing | `pandas` | Latest (used in pipeline) |
| AWS SDK | `boto3` | Latest (used in pipeline) |
| Infrastructure as Code | Terraform (AWS provider) | `~> 5.0` |
| Cloud provider | AWS | Region: `us-east-1` (default) |
| Email notifications | SendGrid | Via REST API |
| Output storage | GitHub repository | `ai-delivery-outputs` |

---

## 3. Architecture

The repository has two distinct concerns that interact via the GitHub Actions platform:

**Data Pipeline (`src/data_pipeline.py` + `infra/main.tf`):**
An S3 event on the `raw/` prefix of the landing bucket triggers the `data-ingest` Lambda function. The Lambda downloads the CSV, validates each row (required fields, email format, age range), and writes valid rows as Parquet to the `processed/` prefix of the same bucket. Invalid rows are logged but not persisted. Terraform in `infra/` provisions both S3 buckets, the Lambda function, its IAM role, and the S3 bucket notification.

**AI Delivery Workflows (`.github/workflows/` + `.github/scripts/`):**
Five GitHub Actions workflows each trigger on different events (PR open, push to main, version tag, release branch creation, or schedule). Each workflow checks out the source repo, installs Python dependencies, and invokes the corresponding script under `.github/scripts/`. The scripts share common utilities via `shared.py` (GitHub API calls, Claude API calls, SendGrid email, output file writing). All AI-generated artefacts (review reports, docs, test files, UAT packs) are written to a separate `ai-delivery-outputs` GitHub repository via the GitHub Contents API. PR review comments are also posted directly to the originating pull request.

```
Source Repo (this repo)
       │
       ├── PR / push / tag / schedule event
       │
       ▼
GitHub Actions Workflow
       │
       ├── .github/scripts/shared.py  ──►  Anthropic Claude API
       │                                        │
       │                              AI-generated content
       │                                        │
       ├──────────────────────────────►  ai-delivery-outputs repo
       │                                 (README, ARCHITECTURE,
       │                                  RUNBOOK, test files,
       │                                  UAT packs, defect reports)
       │
       ├──────────────────────────────►  PR comments (Tool 1)
       │
       └──────────────────────────────►  SendGrid email notification
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
pip install anthropic requests boto3 pandas
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

5. **Run a workflow script manually** (example: code review tool in repo mode)

```bash
export REVIEW_MODE=repo
export SOURCE_REPO_OWNER=kylodeng
export SOURCE_REPO_NAME=ai-delivery-source
export GITHUB_RUN_URL="https://github.com/kylodeng/ai-delivery-source/actions"
python .github/scripts/tool1_code_review.py
```

6. **Initialise and apply Terraform infrastructure** (see [Deployment](#7-deployment) section)

```bash
cd infra
terraform init
terraform plan
terraform apply
```

---

## 5. Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | — | API key for the Anthropic Claude API |
| `GH_TOKEN` | Yes | — | GitHub Personal Access Token with repo read/write permissions |
| `SENDGRID_API_KEY` | Yes | — | SendGrid API key for email notifications |
| `OUTPUT_REPO` | No | `ai-delivery-outputs` | Name of the GitHub repo where AI outputs are written |
| `OUTPUT_REPO_OWNER` | No | Value of `GITHUB_REPOSITORY_OWNER` | GitHub owner/org of the output repository |
| `NOTIFY_EMAIL` | No | `kylo.deng@capco.com` | Email address to receive workflow notifications |
| `SENDER_EMAIL` | No | `kylo.deng@capco.com` | From address used for SendGrid emails |
| `SOURCE_REPO_OWNER` | Yes (workflows) | `github.repository_owner` | Owner of the source repo being analysed |
| `SOURCE_REPO_NAME` | Yes (workflows) | `github.event.repository.name` | Name of the source repo being analysed |
| `GITHUB_RUN_URL` | No | Constructed by Actions | URL of the current Actions run, included in reports |
| `LANDING_BUCKET` | Yes (Lambda) | — | Name of the S3 landing bucket (set via Lambda env var in Terraform) |
| `REVIEW_MODE` | No | `repo` | `pr` or `repo` — controls Tool 1 review scope |
| `PR_NUMBER` | No | — | PR number to review when `REVIEW_MODE=pr` |
| `RELEASE_VERSION` | No | `0.1.0` | Version string used by Tool 3 and Tool 5 |
| `PROJECT_NAME` | No | Repository name | Human-readable project name for business docs (Tool 3) |
| `TEST_MODE` | No | `generate` | `generate` or `gap-analysis` — controls Tool 4 behaviour |
| `UAT_MODE` | No | `generate` | `generate` or `analyse` — controls Tool 5 behaviour |
| `USER_STORIES` | No | — | Acceptance criteria pasted inline for Tool 5 UAT generation |
| `UAT_RESULTS_PATH` | No | — | Path in output repo to completed UAT CSV for Tool 5 analyse mode |

---

## 6. Running Tests

[TODO: Are there any existing tests in this repository, or is test generation entirely delegated to Tool 4?]

To trigger AI-generated test creation via Tool 4, either open a pull request that touches `src/**`, `*.py`, `*.js`, or `*.ts`, or run manually:

```bash
# Via GitHub CLI
gh workflow run "Tool 4 — Auto Testing" \
  --field test_mode=generate

# Or run the script directly (with env vars set as above)
export TEST_MODE=generate
export SOURCE_REPO_OWNER=kylodeng
export SOURCE_REPO_NAME=ai-delivery-source
python .github/scripts/tool4_auto_testing.py
```

Generated test files are written to the `ai-delivery-outputs` repository, not executed in CI automatically.

[TODO: Is there a step that actually executes the generated tests, or are they only written as artefacts for human review?]

---

## 7. Deployment

### Infrastructure (Terraform)

```bash
cd infra

# Initialise providers
terraform init

# Preview changes
terraform plan -var="environment=dev" -var="aws_region=us-east-1"

# Apply
terraform apply -var="environment=dev" -var="aws_region=us-east-1"
```

Terraform will create:
- S3 landing bucket: `capco-data-landing-<environment>`
- S3 processed bucket: `capco-data-processed-<environment>`
- Lambda function: `data-ingest-<environment>` (expects a `lambda.zip` deployment package)
- IAM role and policy for the Lambda
- S3 bucket notification to trigger the Lambda on `raw/*.csv` uploads

**Before applying**, package the Lambda deployment artefact:

```bash
cd src
zip ../infra/lambda.zip data_pipeline.py
```

[TODO: Are additional Python packages (boto3, pandas) expected to be bundled into lambda.zip via a Lambda layer or included directly in the zip?]

### GitHub Actions Workflows

Workflows run automatically on their configured triggers. Required secrets must be set in the repository's **Settings → Secrets and variables → Actions**:

| Secret name | Maps to variable |
|---|---|
| `ANTHROPIC_API_KEY` | `ANTHROPIC_API_KEY` |
| `GH_TOKEN` | `GH_TOKEN` |
| `SENDGRID_API_KEY` | `SENDGRID_API_KEY` |

To trigger workflows manually via GitHub CLI:

```bash
# Tool 1 – Code review on a specific PR
gh workflow run "Tool 1 — Code Review" \
  --field review_mode=pr \
  --field pr_number=42

# Tool 2 – Tech documentation
gh workflow run "Tool 2 — Tech Documentation"

# Tool 3 – Business documentation
gh workflow run "Tool 3 — Business Documentation" \
  --field project_name="Data Ingestion Pipeline" \
  --field release_version="1.0.0"

# Tool 4 – Auto test generation
gh workflow run "Tool 4 — Auto Testing" \
  --field test_mode=generate

# Tool 5 – UAT test pack generation
gh workflow run "Tool 5 — UAT Facilitation" \
  --field uat_mode=generate \
  --field release_version="1.0.0"

# Tool 5 – Analyse completed UAT results
gh workflow run "Tool 5 — UAT Facilitation" \
  --field uat_mode=analyse \
  --field release_version="1.0.0" \
  --field uat_results_path="uat/kylodeng-ai-delivery-source/v1.0.0/UAT_RESULTS_SHEET.csv"
```

---

## 8. Known Issues / TODOs

The following issues were extracted directly from code comments:

### Security (Critical)

- **`src/data_pipeline.py` line 9–10** — AWS credentials are hardcoded as plaintext constants (`AWS_ACCESS_KEY`, `AWS_SECRET_KEY`). Comment reads: `# TODO: move this to secrets manager`
- **`infra/main.tf` Lambda environment block** — `DB_PASSWORD` is hardcoded as `"SuperSecret123!"` in the Lambda environment variables. Comment reads: `# Hardcoded secret - should use SSM or Secrets Manager`
- **`infra/main.tf` `aws_s3_bucket.landing`** — The landing S3 bucket has no server-side encryption and no public access block configured. Comment reads: `# S3 landing bucket - NO encryption, NO public access block`
- **`infra/main.tf` `aws_iam_role_policy.lambda_policy`** — The Lambda IAM policy grants `s3:*` on `Resource: "*"` (full S3 access to all buckets). Comment reads: `# Overly permissive policy - full S3 access`

### Infrastructure

- **`infra/main.tf` `aws_s3_bucket.landing`** — Resource tags are missing. Comment reads: `# TODO: add tags`
- **`infra/main.tf`** — No Terraform remote state backend is configured. [TODO: Should state be stored in S3 with DynamoDB locking?]

### Application

- **`src/data_pipeline.py` `get_all_pending_files`** — S3 `list_objects_v2` result is not paginated; calls returning more than 1,000 objects will silently drop files. Comment reads: `# SQL injection not applicable here but no pagination implemented`
- **`src/data_pipeline.py` `process_csv`** — No error handling for malformed CSV input. Comment reads: `# No error handling if CSV is malformed`
- **`src/data_pipeline.py` `lambda_handler`** — The `except Exception` clause catches all errors without re-raising, which suppresses unexpected failures silently.

### Workflows / Scripts

- **`shared.py`** — `send_email`, `email_html`, and `write_audit_entry` functions are imported by all tool scripts but their implementations are truncated in the provided files. [TODO: Are these functions fully implemented in the actual repository?]
- **`tool2_tech_docs.py` `build_index`** — The function body is truncated in the provided files; the variable `r` appears to be a typo for `repo`. [TODO: Confirm the full implementation of `build_index`.]
- **`tool4_auto_testing.py` `build_test_report`** — The function body is truncated in the provided files. [TODO: Confirm complete implementation.]
- **`tool5_uat.py` `build_test_pack_csv`** — The function signature and body are truncated in the provided files. [TODO: Confirm complete implementation.]
- All workflows — No DR, monitoring, or alerting configuration is present anywhere in the repository.
- All workflows — Escalation contacts are not defined. [TODO: Who is the on-call owner for pipeline failures?]