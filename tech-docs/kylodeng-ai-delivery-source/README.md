# ai-delivery-source

## 1. Project Overview

This repository contains a customer data ingestion pipeline (a Python AWS Lambda function) alongside five Claude AI-powered GitHub Actions workflows that automate software delivery tasks: automated code review, technical documentation generation, business documentation generation, test generation, and UAT facilitation. Each workflow reads source files from this repository, calls the Anthropic Claude API, and writes its outputs to a companion repository (`ai-delivery-outputs`). Notifications are sent via SendGrid email.

---

## 2. Tech Stack

| Component | Technology | Version/Notes |
|---|---|---|
| Pipeline runtime | Python | 3.12 |
| AI model | Anthropic Claude | `claude-sonnet-4-6` |
| Anthropic SDK | `anthropic` (Python) | Latest compatible |
| HTTP client | `requests` (Python) | Latest compatible |
| Data processing | `pandas` | Latest compatible |
| Cloud provider | AWS | us-east-1 (default) |
| Compute | AWS Lambda | Python 3.12 runtime |
| Storage | AWS S3 | Landing + processed buckets |
| IaC | Terraform | AWS provider `~> 5.0` |
| CI/CD | GitHub Actions | ubuntu-latest runners |
| Email notifications | SendGrid | REST API v3 |
| Output storage | GitHub (companion repo) | `ai-delivery-outputs` |

---

## 3. Architecture

The repository has two distinct concerns that interact through GitHub Actions:

**Data pipeline:**  
`src/data_pipeline.py` is deployed as an AWS Lambda function (`data-ingest-<env>`). It is triggered by an S3 `ObjectCreated` event on CSV files uploaded to the `raw/` prefix of the landing bucket (`capco-data-landing-<env>`). The function downloads the file, validates each row, and writes the result as a Parquet file to the processed bucket (`capco-data-processed-<env>`). Infrastructure is defined in `infra/main.tf` (Terraform).

**AI delivery workflows:**  
Five GitHub Actions workflows share a common Python utility module (`.github/scripts/shared.py`) that handles GitHub API calls, Claude API calls, SendGrid email, and audit logging. Each workflow script fetches source files or PR diffs from the GitHub API, sends them to the Claude API, and writes structured Markdown or CSV output files to the `ai-delivery-outputs` companion repository via the GitHub Contents API. Email notifications are dispatched via SendGrid after each run.

```
GitHub Event (PR / push / tag / schedule / manual)
        │
        ▼
GitHub Actions Runner
        │
        ├─► .github/scripts/shared.py  ──► GitHub API  (read source files / post PR comments)
        │                               ──► Anthropic Claude API  (generate content)
        │                               ──► GitHub API  (write to ai-delivery-outputs)
        │                               ──► SendGrid API  (send email notification)
        │
        └─► src/data_pipeline.py (deployed separately as Lambda)
                │
                ├─► S3 landing bucket  (ObjectCreated trigger)
                └─► S3 processed bucket  (Parquet output)
```

---

## 4. Local Development Setup

1. **Clone the repository**

```bash
git clone https://github.com/kylodeng/ai-delivery-source.git
cd ai-delivery-source
```

2. **Create and activate a virtual environment**

```bash
python3.12 -m venv .venv
source .venv/bin/activate
```

3. **Install dependencies**

```bash
pip install anthropic requests pandas boto3
```

4. **Set required environment variables** (see [Environment Variables](#5-environment-variables) below)

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
export GH_TOKEN="ghp_..."
export SENDGRID_API_KEY="SG...."
export OUTPUT_REPO_OWNER="your-github-org-or-username"
```

5. **Run a workflow script manually** (example: code review in repo mode)

```bash
export REVIEW_MODE=repo
export SOURCE_REPO_OWNER=kylodeng
export SOURCE_REPO_NAME=ai-delivery-source
export GITHUB_RUN_URL=http://localhost
python .github/scripts/tool1_code_review.py
```

6. **(Optional) Initialise Terraform for infrastructure**

```bash
cd infra
terraform init
terraform plan -var="environment=dev"
```

---

## 5. Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | — | Anthropic API key used to call the Claude model |
| `GH_TOKEN` | Yes | — | GitHub personal access token; needs `repo` scope to read source files, post PR comments, and write to the output repo |
| `SENDGRID_API_KEY` | Yes | — | SendGrid API key for sending notification emails |
| `OUTPUT_REPO` | No | `ai-delivery-outputs` | Name of the companion GitHub repository where generated docs and reports are written |
| `OUTPUT_REPO_OWNER` | No | Value of `GITHUB_REPOSITORY_OWNER` | GitHub owner (org or user) of the output repository |
| `NOTIFY_EMAIL` | No | `kylo.deng@capco.com` | Recipient address for workflow notification emails |
| `SENDER_EMAIL` | No | `noreply@ai-delivery.capco.com` | From address for notification emails (must be verified in SendGrid) |
| `SOURCE_REPO_OWNER` | Yes (workflows) | `${{ github.repository_owner }}` | Owner of the source repository being analysed |
| `SOURCE_REPO_NAME` | Yes (workflows) | `${{ github.event.repository.name }}` | Name of the source repository being analysed |
| `GITHUB_RUN_URL` | No | Set by Actions runner | URL of the current Actions run, included in reports |
| `LANDING_BUCKET` | Yes (Lambda) | — | Name of the S3 landing bucket; used by `lambda_handler` when not supplied in the event |
| `REVIEW_MODE` | No | `repo` (schedule), `pr` (PR event) | Code review mode: `repo` for full repo scan, `pr` for PR diff review |
| `PR_NUMBER` | Conditional | — | PR number; required when `REVIEW_MODE=pr` |
| `TEST_MODE` | No | `generate` | Auto-testing mode: `generate` (create new tests) or `gap-analysis` |
| `UAT_MODE` | No | `generate` | UAT mode: `generate` (create test pack) or `analyse` (process completed results CSV) |
| `RELEASE_VERSION` | No | Derived from tag/branch | Version string used in business docs and UAT tools |
| `PROJECT_NAME` | No | Repository name | Human-readable project name used in business documentation |
| `USER_STORIES` | No | — | Optional acceptance criteria pasted directly into the UAT workflow dispatch |
| `UAT_RESULTS_PATH` | Conditional | — | Path in the output repo to the completed UAT results CSV; required for `UAT_MODE=analyse` |

> **Note:** In GitHub Actions all secrets (`ANTHROPIC_API_KEY`, `GH_TOKEN`, `SENDGRID_API_KEY`) must be configured as repository secrets. All other variables are set as workflow `env` entries and can be overridden.

---

## 6. Running Tests

[TODO: Are there any existing tests in this repository, or is test generation handled exclusively by Tool 4 writing output to `ai-delivery-outputs`?]

To run Tool 4 locally to generate tests for `src/data_pipeline.py`:

```bash
export REVIEW_MODE=repo
export SOURCE_REPO_OWNER=kylodeng
export SOURCE_REPO_NAME=ai-delivery-source
export TEST_MODE=generate
export GITHUB_RUN_URL=http://localhost
python .github/scripts/tool4_auto_testing.py
```

The generated test files will be written to the `ai-delivery-outputs` repository under a path derived from the source repo name.

To run a coverage gap analysis instead:

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
terraform plan  -var="environment=dev" -var="aws_region=us-east-1"
terraform apply -var="environment=dev" -var="aws_region=us-east-1"
```

Terraform will create:
- S3 landing bucket (`capco-data-landing-<environment>`)
- S3 processed bucket (`capco-data-processed-<environment>`)
- Lambda function (`data-ingest-<environment>`) from a local `lambda.zip`
- IAM role and policy for the Lambda
- S3 bucket notification to trigger the Lambda on `raw/*.csv` uploads

### Packaging the Lambda

[TODO: What is the exact command to produce `lambda.zip`? Is there a Makefile or build script?]

As a starting point:

```bash
pip install pandas boto3 --target ./package
cp src/data_pipeline.py ./package/
cd package && zip -r ../lambda.zip . && cd ..
```

Then re-run `terraform apply`.

### GitHub Actions Workflows

The five workflows run automatically on their configured triggers. They can also be triggered manually from the **Actions** tab in GitHub using **workflow_dispatch**.

| Workflow | Trigger |
|---|---|
| Tool 1 — Code Review | PR opened/synchronised; Monday 08:00 UTC; manual |
| Tool 2 — Tech Docs | Push to `main` (non-doc files); Sunday 06:00 UTC; manual |
| Tool 3 — Business Docs | Version tag push (`v*`); manual |
| Tool 4 — Auto Testing | PR opened/synchronised on `src/**`; Wednesday 07:00 UTC; manual |
| Tool 5 — UAT Facilitation | `release/*` branch creation; manual |

Required repository secrets to configure before workflows will succeed:

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
| `src/data_pipeline.py` line 10–11 | 🔴 CRITICAL | AWS access key and secret key are hardcoded in source. Comment says: *"TODO: move this to secrets manager"* |
| `infra/main.tf` — `aws_s3_bucket.landing` | 🔴 CRITICAL | Landing S3 bucket has **no server-side encryption** and **no public access block** configured |
| `infra/main.tf` — `aws_iam_role_policy.lambda_policy` | 🔴 CRITICAL | Lambda IAM policy grants `s3:*` on `*` — overly broad, full S3 access across all buckets |
| `infra/main.tf` — `aws_lambda_function.ingest` environment variables | 🔴 CRITICAL | `DB_PASSWORD` is hardcoded as `"SuperSecret123!"` in the Terraform resource. Comment says: *"should use SSM or Secrets Manager"* |
| `infra/main.tf` — `aws_s3_bucket.landing` | 🟡 MEDIUM | Comment says: *"TODO: add tags"* — resource tags are missing from the landing bucket |
| `src/data_pipeline.py` — `get_all_pending_files` | 🟡 MEDIUM | S3 `list_objects_v2` result is not paginated; will silently miss files beyond the first 1,000 |
| `src/data_pipeline.py` — `lambda_handler` | 🟡 MEDIUM | Bare `except Exception` in the handler swallows all errors without re-raising |
| `src/data_pipeline.py` — `process_csv` | 🟢 LOW | No error handling if the downloaded CSV is malformed (noted in a code comment) |
| `.github/scripts/shared.py` — `send_email` | 🟢 LOW | Function body is truncated in the source file; implementation may be incomplete |
| `.github/scripts/tool1_code_review.py` — `build_report_md` | 🟢 LOW | Markdown template is truncated in the source file |
| `.github/scripts/tool2_tech_docs.py` — `build_index` | 🟢 LOW | Function body is truncated; references undefined variable `r` instead of `repo` |
| General | — | No disaster recovery (DR) configuration present in the Terraform |
| General | — | No monitoring or alerting resources (CloudWatch alarms, log groups) defined in the IaC |