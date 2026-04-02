# ai-delivery-source

## 1. Project Overview

This repository contains a customer data ingestion pipeline (an AWS Lambda function triggered by S3 uploads) alongside five AI-powered GitHub Actions workflows that automate software delivery tasks: automated code review, technical documentation generation, business documentation generation, test generation, and UAT facilitation. Each workflow calls the Anthropic Claude API to produce structured outputs (markdown reports, test files, CSV test packs) which are written to a companion output repository. The pipeline itself processes customer CSV files from S3, validates and transforms them, and writes Parquet output to a processed S3 bucket.

---

## 2. Tech Stack

| Component | Technology | Version/Notes |
|---|---|---|
| Cloud provider | AWS | us-east-1 (default) |
| Compute | AWS Lambda | Python 3.12 runtime |
| Storage | AWS S3 | Two buckets: landing + processed |
| IaC | Terraform | AWS provider ~> 5.0 |
| Data processing | pandas | [TODO: exact version not pinned in any requirements file] |
| AI model | Anthropic Claude | `claude-sonnet-4-6` |
| Anthropic SDK | `anthropic` Python library | [TODO: version not pinned] |
| HTTP client | `requests` | [TODO: version not pinned] |
| Email notifications | SendGrid API | REST v3 |
| CI/CD | GitHub Actions | ubuntu-latest runners |
| Python (workflows) | Python | 3.12 |

---

## 3. Architecture

The repository has two distinct concerns that share a common trigger (GitHub Actions):

**Data pipeline:**  
CSV files are uploaded to the `capco-data-landing-{environment}` S3 bucket under the `raw/` prefix. An S3 event notification triggers the `data-ingest-{environment}` Lambda function (`data_pipeline.lambda_handler`). The Lambda validates each row against required fields and business rules, then writes a Parquet file to the same bucket under the `processed/` prefix. Both buckets are provisioned by Terraform in `infra/main.tf`.

**AI delivery workflows:**  
Five GitHub Actions workflows (`.github/workflows/tool*.yml`) each invoke a corresponding Python script (`.github/scripts/tool*.py`). All scripts import shared utilities from `.github/scripts/shared.py`, which provides:
- a Claude API client (`call_claude`)
- GitHub API helpers to read source files and PR diffs (`get_repo_files`, `get_pr_diff`)
- a file writer that commits outputs to a separate `ai-delivery-outputs` repository (`write_output_file`)
- PR comment posting (`post_pr_comment`)
- SendGrid email notifications (`send_email`)

Outputs (markdown reports, test files, CSVs) are written to the `ai-delivery-outputs` repository and optionally emailed to `NOTIFY_EMAIL`.

```
Source repo (this repo)
        │
        ├── PR / push / tag / schedule / manual dispatch
        │          │
        │   GitHub Actions runner
        │          │
        │   .github/scripts/tool*.py
        │          │
        │   ┌──────┴───────┐
        │   Claude API   GitHub API
        │                   │
        │           ai-delivery-outputs repo
        │                   │
        │              SendGrid email → NOTIFY_EMAIL
        │
S3 landing bucket (raw/*.csv)
        │
   Lambda (data_pipeline.py)
        │
S3 landing bucket (processed/*.parquet)
```

---

## 4. Local Development Setup

```bash
# 1. Clone the repository
git clone https://github.com/kylodeng/ai-delivery-source.git
cd ai-delivery-source
```

```bash
# 2. Create and activate a Python virtual environment
python3.12 -m venv .venv
source .venv/bin/activate
```

```bash
# 3. Install Python dependencies
pip install anthropic requests pandas boto3 pyarrow
```

> [TODO: Is there a requirements.txt or pyproject.toml? None was found in the provided files.]

```bash
# 4. Export required environment variables (see Environment Variables section)
export ANTHROPIC_API_KEY="your-anthropic-key"
export GH_TOKEN="your-github-personal-access-token"
export SENDGRID_API_KEY="your-sendgrid-key"
export OUTPUT_REPO="ai-delivery-outputs"
export OUTPUT_REPO_OWNER="kylodeng"
export NOTIFY_EMAIL="kylo.deng@capco.com"
export SENDER_EMAIL="noreply@ai-delivery.capco.com"
```

```bash
# 5. (Pipeline only) Configure AWS credentials
export AWS_ACCESS_KEY_ID="your-access-key"
export AWS_SECRET_ACCESS_KEY="your-secret-key"
export AWS_DEFAULT_REGION="us-east-1"
```

> ⚠️ **Warning:** `src/data_pipeline.py` currently has hardcoded AWS credentials. These must be removed and replaced with environment variables or IAM roles before any real use (see Known Issues).

```bash
# 6. Run a workflow script manually (example: code review on a repo)
export SOURCE_REPO_OWNER="kylodeng"
export SOURCE_REPO_NAME="ai-delivery-source"
export REVIEW_MODE="repo"
export GITHUB_RUN_URL="http://localhost"
python .github/scripts/tool1_code_review.py
```

---

## 5. Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | — | API key for Anthropic Claude |
| `GH_TOKEN` | Yes | — | GitHub Personal Access Token with repo read/write permissions |
| `SENDGRID_API_KEY` | Yes | — | SendGrid API key for email notifications |
| `OUTPUT_REPO` | No | `ai-delivery-outputs` | Name of the GitHub repo where AI outputs are written |
| `OUTPUT_REPO_OWNER` | No | Value of `GITHUB_REPOSITORY_OWNER` | GitHub owner/org of the output repo |
| `NOTIFY_EMAIL` | No | `kylo.deng@capco.com` | Recipient email for notifications |
| `SENDER_EMAIL` | No | `kylo.deng@capco.com` | Sender email address (must be verified in SendGrid) |
| `SOURCE_REPO_OWNER` | Yes (workflows) | `github.repository_owner` | Owner of the source repo being analysed |
| `SOURCE_REPO_NAME` | Yes (workflows) | `github.event.repository.name` | Name of the source repo being analysed |
| `GITHUB_RUN_URL` | No | — | URL of the current Actions run, used in reports |
| `LANDING_BUCKET` | Yes (Lambda) | — | Name of the S3 bucket where raw CSV files are uploaded |
| `REVIEW_MODE` | No | `repo` | Tool 1 only: `pr` or `repo` |
| `PR_NUMBER` | No | — | Tool 1 only: PR number when `REVIEW_MODE=pr` |
| `TEST_MODE` | No | `generate` | Tool 4 only: `generate` or `gap-analysis` |
| `UAT_MODE` | No | `generate` | Tool 5 only: `generate` or `analyse` |
| `RELEASE_VERSION` | No | — | Tool 3 & 5: version string e.g. `1.0.0` |
| `PROJECT_NAME` | No | repo name | Tool 3: human-readable project name |
| `USER_STORIES` | No | — | Tool 5 (generate mode): acceptance criteria text |
| `UAT_RESULTS_PATH` | No | — | Tool 5 (analyse mode): path in output repo to completed results CSV |

---

## 6. Running Tests

[TODO: No test files or test runner configuration were found in the provided repository files. Tool 4 (`tool4_auto_testing.py`) is designed to *generate* tests for this codebase — have those generated tests been committed?]

To generate tests using Tool 4 locally:

```bash
export TEST_MODE="generate"
python .github/scripts/tool4_auto_testing.py
```

To run a coverage gap analysis:

```bash
export TEST_MODE="gap-analysis"
python .github/scripts/tool4_auto_testing.py
```

---

## 7. Deployment

### Infrastructure (Terraform)

```bash
# 1. Navigate to the infra directory
cd infra
```

```bash
# 2. Initialise Terraform
terraform init
```

```bash
# 3. Review the plan
terraform plan -var="environment=dev"
```

```bash
# 4. Apply
terraform apply -var="environment=dev"
```

```bash
# 5. To deploy to a different environment
terraform apply -var="environment=prod"
```

> [TODO: Is there a remote state backend configured? None is defined in `infra/main.tf`.]

### Lambda deployment

The Lambda function references `filename = "lambda.zip"` in `infra/main.tf`. Build this before running `terraform apply`:

```bash
# Package the Lambda function
zip lambda.zip src/data_pipeline.py
mv lambda.zip infra/lambda.zip
```

> [TODO: Are there additional dependencies (pandas, boto3) that need to be included in the Lambda zip, or are they provided via a Lambda layer?]

### AI workflow deployment

The five GitHub Actions workflows are deployed automatically when pushed to the repository. Ensure the following secrets are set in the repository's **Settings → Secrets and variables → Actions**:

| Secret name | Description |
|---|---|
| `ANTHROPIC_API_KEY` | Anthropic Claude API key |
| `GH_TOKEN` | GitHub token with write access to the output repo |
| `SENDGRID_API_KEY` | SendGrid API key |

The workflows will then trigger according to their configured schedules and events:

| Workflow | Automatic triggers |
|---|---|
| Tool 1 — Code Review | PR open/sync/reopen; every Monday 08:00 UTC |
| Tool 2 — Tech Documentation | Push to `main` (non-docs paths); every Sunday 06:00 UTC |
| Tool 3 — Business Documentation | Push of a `v*` tag |
| Tool 4 — Auto Testing | PR open/sync on `src/**`, `*.py`, `*.js`, `*.ts`; every Wednesday 07:00 UTC |
| Tool 5 — UAT Facilitation | Creation of a `release/*` branch |

---

## 8. Known Issues / TODOs

The following issues are extracted directly from code comments:

### 🔴 Security — Critical

- **Hardcoded AWS credentials** in `src/data_pipeline.py`:
  ```python
  # TODO: move this to secrets manager
  AWS_ACCESS_KEY = "AKIAIOSFODNN7EXAMPLE"
  AWS_SECRET_KEY = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
  ```
- **Hardcoded database password** in `infra/main.tf` Lambda environment variable:
  ```hcl
  # Hardcoded secret - should use SSM or Secrets Manager
  DB_PASSWORD = "SuperSecret123!"
  ```
- **Overly permissive IAM policy** in `infra/main.tf` — grants `s3:*` on `Resource: "*"` to the Lambda role.

### 🟠 Infrastructure — High

- **S3 landing bucket has no encryption and no public access block** (`infra/main.tf`):
  ```hcl
  # S3 landing bucket - NO encryption, NO public access block
  ```
- **Missing resource tags** on the S3 landing bucket:
  ```hcl
  # TODO: add tags
  ```
- **No remote Terraform state backend** configured.

### 🟡 Code quality — Medium

- **No pagination** on `list_objects_v2` in `get_all_pending_files` — will silently miss files beyond the first 1,000:
  ```python
  # SQL injection not applicable here but no pagination implemented
  ```
- **Bare `except` clause** in `lambda_handler` swallows all errors:
  ```python
  # bare except swallows all errors
  ```
- **No error handling for malformed CSVs** in `process_csv`:
  ```python
  # No error handling if CSV is malformed
  ```

### 🟢 General — Low / Open questions

- [TODO: Is there a `requirements.txt` or `pyproject.toml`? No dependency file was found.]
- [TODO: Are pandas/pyarrow bundled in the Lambda deployment package or provided via a Lambda layer?]
- [TODO: Is there a remote Terraform state backend, and if so where is it configured?]
- [TODO: What is the target go-live environment beyond `dev`?]
- [TODO: No test files were found — have generated tests been committed to the repo?]
- [TODO: The `send_email` function body in `shared.py` is truncated in the provided files — confirm it is complete in the repository.]
- [TODO: `tool2_tech_docs.py` contains a truncated `build_index` function (`{r` at end of file) — confirm this is complete in the repository.]
- [TODO: `tool3_business_docs.py` contains a truncated `build_full_output` function — confirm this is complete in the repository.]
- [TODO: `tool4_auto_testing.py` `build_test_report` function appears truncated — confirm this is complete in the repository.]
- [TODO: `tool5_uat.py` `build_test_pack_csv` function appears truncated — confirm this is complete in the repository.]