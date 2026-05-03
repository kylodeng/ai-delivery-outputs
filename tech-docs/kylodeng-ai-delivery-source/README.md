# ai-delivery-source

## 1. Project Overview

This repository is a suite of five AI-powered GitHub Actions workflows that automate common software delivery tasks — code review, technical documentation, business documentation, test generation, and UAT facilitation — using Anthropic's Claude API. Each workflow reads source files or pull request diffs from the repository, calls Claude to produce structured output, and writes results to a companion output repository (`ai-delivery-outputs`). The repository also contains a sample AWS data ingestion pipeline (an S3-triggered Lambda) that serves as the subject for these automation tools.

---

## 2. Tech Stack

| Component | Technology | Version/Notes |
|---|---|---|
| CI/CD platform | GitHub Actions | ubuntu-latest runners |
| AI model | Anthropic Claude | `claude-sonnet-4-6` |
| Python runtime | Python | 3.12 |
| Python SDK (Anthropic) | `anthropic` | Latest (pip install) |
| HTTP client | `requests` | Latest (pip install) |
| Email delivery | SendGrid API | REST via `requests` |
| Data pipeline runtime | AWS Lambda | Python 3.12 |
| Data processing | `pandas` | Latest (pip install) |
| Cloud storage | AWS S3 | via `boto3` |
| Infrastructure as Code | Terraform | AWS provider `~> 5.0` |
| Output storage | GitHub repository | `ai-delivery-outputs` (separate repo) |

---

## 3. Architecture

The repository has two distinct layers:

**AI Delivery Automation (`.github/`):** Five GitHub Actions workflows each trigger a corresponding Python script under `.github/scripts/`. All scripts share a common library (`shared.py`) that wraps the Anthropic Claude API, GitHub REST API, and SendGrid email API. Workflows are triggered by pull request events, pushes to `main`, version tags, branch creation, or scheduled cron jobs. Each script fetches source files or PR diffs from the source repository via the GitHub API, submits them to Claude for analysis, then writes Markdown or JSON output files to a separate `ai-delivery-outputs` GitHub repository and optionally posts comments to pull requests and sends email notifications. An audit log entry is also written for each run.

**Sample Data Pipeline (`src/` + `infra/`):** A Python Lambda function (`data_pipeline.py`) is triggered by S3 `ObjectCreated` events on the landing bucket. It downloads CSV files from the `raw/` prefix, validates each row, converts valid records to Parquet, and writes them to the `processed/` prefix of the same bucket. The accompanying Terraform in `infra/main.tf` provisions both S3 buckets, the Lambda function, an IAM role, and the S3 event notification.

```
Pull Request / Push / Tag / Schedule
          │
          ▼
  GitHub Actions Workflow (.yml)
          │
          ▼
  Python Script (.github/scripts/tool*.py)
          │
  ┌───────┴────────┐
  │                │
  ▼                ▼
GitHub API     Anthropic Claude API
(fetch files,  (claude-sonnet-4-6)
 post comments)     │
                    ▼
             Output written to
             ai-delivery-outputs repo
             + SendGrid email sent
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
pip install anthropic requests pandas boto3
```

4. **Set required environment variables** (see [Environment Variables](#5-environment-variables) section below)

```bash
export ANTHROPIC_API_KEY="your-anthropic-api-key"
export GH_TOKEN="your-github-personal-access-token"
export SENDGRID_API_KEY="your-sendgrid-api-key"
export OUTPUT_REPO="ai-delivery-outputs"
export OUTPUT_REPO_OWNER="your-github-username"
export NOTIFY_EMAIL="you@example.com"
export SENDER_EMAIL="noreply@example.com"
```

5. **Run a script manually** (example: code review in repo mode)

```bash
export REVIEW_MODE=repo
export SOURCE_REPO_OWNER=kylodeng
export SOURCE_REPO_NAME=ai-delivery-source
export GITHUB_RUN_URL="http://localhost"
python .github/scripts/tool1_code_review.py
```

6. **For Terraform (infrastructure only)**

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
| `OUTPUT_REPO` | No | `ai-delivery-outputs` | Name of the GitHub repository where outputs are written |
| `OUTPUT_REPO_OWNER` | No | `GITHUB_REPOSITORY_OWNER` env value | GitHub owner (user or org) of the output repository |
| `NOTIFY_EMAIL` | No | `kylo.deng@capco.com` | Recipient email address for notifications |
| `SENDER_EMAIL` | No | `kylo.deng@capco.com` | Sender email address used in SendGrid |
| `SOURCE_REPO_OWNER` | Yes (scripts) | Set by workflow | GitHub owner of the source repository being analysed |
| `SOURCE_REPO_NAME` | Yes (scripts) | Set by workflow | Name of the source repository being analysed |
| `REVIEW_MODE` | No | `repo` | For Tool 1: `pr` or `repo` |
| `PR_NUMBER` | Conditional | — | Required when `REVIEW_MODE=pr` |
| `TEST_MODE` | No | `generate` | For Tool 4: `generate` or `gap-analysis` |
| `UAT_MODE` | No | `generate` | For Tool 5: `generate` or `analyse` |
| `RELEASE_VERSION` | No | — | Version string used by Tools 3 and 5 |
| `PROJECT_NAME` | No | — | Human-readable project name used by Tool 3 |
| `USER_STORIES` | No | — | Acceptance criteria pasted in for Tool 5 generate mode |
| `UAT_RESULTS_PATH` | No | — | Path in output repo to completed UAT CSV for Tool 5 analyse mode |
| `LANDING_BUCKET` | No | — | S3 bucket name for the Lambda data pipeline (runtime env var) |
| `GITHUB_RUN_URL` | No | Set by workflow | URL of the current Actions run, used in output links |

---

## 6. Running Tests

[TODO: Are there any existing tests for the scripts in `.github/scripts/` or `src/data_pipeline.py`? No test files were found in the provided repository contents.]

To generate tests using Tool 4 via GitHub Actions, open a pull request against `main` touching any file under `src/` or a `.py`/`.js`/`.ts` file, or trigger manually:

```
GitHub Actions → Tool 4 — Auto Testing → Run workflow
  mode: generate
```

To run a coverage gap analysis:

```
GitHub Actions → Tool 4 — Auto Testing → Run workflow
  mode: gap-analysis
```

---

## 7. Deployment

### Infrastructure (Terraform)

```bash
cd infra
terraform init
terraform plan -var="environment=dev"
terraform apply -var="environment=dev"
```

To deploy to a different environment:

```bash
terraform apply -var="environment=prod"
```

Terraform outputs after apply:

```
landing_bucket  = "capco-data-landing-dev"
processed_bucket = "capco-data-processed-dev"
```

### Lambda Deployment

[TODO: How is `lambda.zip` built and published? No build script for the Lambda artifact was found in the repository.]

The Terraform configuration expects a file named `lambda.zip` at the path referenced by `aws_lambda_function.ingest.filename`. You must build and place this file before running `terraform apply`:

```bash
# Example — exact packaging steps not specified in repository
zip lambda.zip src/data_pipeline.py
```

### GitHub Actions Workflows

The workflows deploy automatically based on their triggers. To trigger manually, use the GitHub Actions UI:

```
Repository → Actions → Select workflow → Run workflow
```

All five workflows require the following GitHub repository secrets to be configured:

```
ANTHROPIC_API_KEY
GH_TOKEN
SENDGRID_API_KEY
```

---

## 8. Known Issues / TODOs

The following issues are extracted directly from comments in the source code:

| Location | Issue |
|---|---|
| `src/data_pipeline.py` line 10–11 | AWS credentials (`AWS_ACCESS_KEY`, `AWS_SECRET_KEY`) are hardcoded. **TODO: move to AWS Secrets Manager.** |
| `src/data_pipeline.py` `get_all_pending_files()` | S3 `list_objects_v2` has no pagination — will silently truncate results beyond 1,000 objects. |
| `src/data_pipeline.py` `lambda_handler()` | Bare `except Exception` in the handler swallows all error detail. |
| `src/data_pipeline.py` `process_csv()` | No error handling if the CSV file is malformed. |
| `infra/main.tf` `aws_s3_bucket.landing` | Landing S3 bucket has **no server-side encryption** and **no public access block** configured. |
| `infra/main.tf` `aws_s3_bucket.landing` | **TODO: add resource tags.** |
| `infra/main.tf` `aws_lambda_function.ingest` | `DB_PASSWORD` is hardcoded as a Lambda environment variable. **Should use SSM Parameter Store or Secrets Manager.** |
| `infra/main.tf` `aws_iam_role_policy.lambda_policy` | IAM policy grants `s3:*` on `*` — **overly permissive**. Should be scoped to specific buckets and actions. |
| All workflows | `send_email` and `write_audit_entry` are imported in tool scripts but the implementations are not present in the truncated `shared.py` provided. [TODO: Confirm these functions exist in the full `shared.py`.] |
| `tool2_tech_docs.py` `build_index()` | Source is truncated mid-function — index build logic is incomplete in the provided files. |
| `tool4_auto_testing.py` `build_test_report()` | Source is truncated mid-function. |
| `tool5_uat.py` `build_test_pack_csv()` | Source is truncated mid-function. |