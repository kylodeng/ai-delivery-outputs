# ai-delivery-source

## 1. Project Overview

This repository contains a customer data ingestion pipeline (an AWS Lambda function that reads CSV files from S3, validates and transforms them to Parquet) alongside five Claude AI-powered GitHub Actions workflows that automate code review, technical documentation, business documentation, test generation, and UAT facilitation across any source repository. All AI-generated outputs are written to a separate `ai-delivery-outputs` repository and optionally emailed via SendGrid.

---

## 2. Tech Stack

| Component | Technology | Version/Notes |
|---|---|---|
| AI model | Anthropic Claude | `claude-sonnet-4-6` |
| Workflow orchestration | GitHub Actions | ubuntu-latest runners |
| Python runtime | Python | 3.12 |
| Anthropic SDK | `anthropic` (PyPI) | [TODO: exact pinned version not specified] |
| HTTP client | `requests` (PyPI) | [TODO: exact pinned version not specified] |
| Data pipeline runtime | AWS Lambda | Python 3.12 |
| Data ingestion | AWS S3 + pandas | [TODO: pandas version not pinned] |
| Infrastructure as Code | Terraform | AWS provider `~> 5.0` |
| Email notifications | SendGrid API | [TODO: version not specified] |
| Output storage | GitHub repository (`ai-delivery-outputs`) | Via GitHub Contents API |

---

## 3. Architecture

The repository has two distinct layers:

**Data pipeline layer:** `src/data_pipeline.py` implements an AWS Lambda handler. When a `.csv` file is uploaded to the `raw/` prefix of the S3 landing bucket, an S3 event notification triggers the Lambda. The Lambda validates each customer record, converts valid rows to Parquet, and writes the result to the `processed/` prefix of the same bucket. Infrastructure for both buckets, the Lambda function, and IAM roles is defined in `infra/main.tf`.

**AI delivery automation layer:** Five GitHub Actions workflows (`.github/workflows/tool1_*.yml` through `tool5_*.yml`) each invoke a corresponding Python script in `.github/scripts/`. All scripts share common utilities from `shared.py` (Claude API calls, GitHub API helpers, SendGrid email, audit logging). Each tool reads source files and/or PR diffs from the triggering repository, calls the Claude API, and writes structured Markdown or CSV outputs back to a separate `ai-delivery-outputs` GitHub repository via the GitHub Contents API. Notifications are sent via SendGrid email.

```
Source Repo (this repo)
        │
        ├── PR / push / tag / cron event
        │
        ▼
GitHub Actions Workflow
        │
        ├── shared.py ──► GitHub API (read source files / PR diff)
        │
        ├── shared.py ──► Anthropic Claude API (generate content)
        │
        ├── shared.py ──► GitHub API (write to ai-delivery-outputs repo)
        │
        └── shared.py ──► SendGrid API (email notification)

infra/main.tf ──► AWS (S3 landing bucket → Lambda → S3 processed bucket)
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
    pip install anthropic requests pandas boto3 pyarrow
    ```

4. **Export required environment variables** (see [Environment Variables](#5-environment-variables) section below)

    ```bash
    export ANTHROPIC_API_KEY="your-anthropic-key"
    export GH_TOKEN="your-github-pat"
    export SENDGRID_API_KEY="your-sendgrid-key"
    export OUTPUT_REPO="ai-delivery-outputs"
    export OUTPUT_REPO_OWNER="kylodeng"
    export NOTIFY_EMAIL="you@example.com"
    export SENDER_EMAIL="noreply@example.com"
    ```

5. **Run an individual tool script directly** (example: code review in repo mode)

    ```bash
    export REVIEW_MODE=repo
    export SOURCE_REPO_OWNER=kylodeng
    export SOURCE_REPO_NAME=ai-delivery-source
    export GITHUB_RUN_URL="https://github.com/kylodeng/ai-delivery-source/actions/runs/local"
    python .github/scripts/tool1_code_review.py
    ```

6. **Initialise and apply Terraform infrastructure** (requires AWS credentials)

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
| `SENDGRID_API_KEY` | Yes | — | SendGrid API key for outbound email notifications |
| `OUTPUT_REPO` | No | `ai-delivery-outputs` | Name of the GitHub repository where AI outputs are written |
| `OUTPUT_REPO_OWNER` | No | Value of `GITHUB_REPOSITORY_OWNER` | GitHub owner/org of the output repository |
| `NOTIFY_EMAIL` | No | `kylo.deng@capco.com` | Recipient email address for notifications |
| `SENDER_EMAIL` | No | `kylo.deng@capco.com` | Sender email address used by SendGrid |
| `SOURCE_REPO_OWNER` | Yes (at runtime) | Set by workflow | GitHub owner of the source repository being analysed |
| `SOURCE_REPO_NAME` | Yes (at runtime) | Set by workflow | Name of the source repository being analysed |
| `REVIEW_MODE` | Tool 1 only | `repo` | `pr` to review a specific PR, `repo` to review all files |
| `PR_NUMBER` | Tool 1 (pr mode) | — | Pull request number to review |
| `RELEASE_VERSION` | Tools 3 & 5 | — | Version string e.g. `1.0.0`, set from git tag or workflow input |
| `PROJECT_NAME` | Tool 3 | Repository name | Human-readable project/solution name |
| `TEST_MODE` | Tool 4 | `generate` | `generate` to create tests, `gap-analysis` to analyse coverage |
| `UAT_MODE` | Tool 5 | `generate` | `generate` to create test pack, `analyse` to process results CSV |
| `USER_STORIES` | Tool 5 (optional) | — | Acceptance criteria / user stories pasted as plain text |
| `UAT_RESULTS_PATH` | Tool 5 (analyse mode) | — | Path in output repo to the completed UAT results CSV |
| `GITHUB_RUN_URL` | No | Set by workflow | URL of the GitHub Actions run, appended to generated reports |
| `LANDING_BUCKET` | Lambda only | Set via Terraform env var | Name of the S3 landing bucket (set in Lambda environment by Terraform) |

---

## 6. Running Tests

[TODO: Are there any existing tests for the scripts in `.github/scripts/` or `src/data_pipeline.py`? No test files were found in the provided repository contents.]

Tool 4 (`tool4_auto_testing.py`) will **generate** tests for source files automatically when triggered. To invoke it locally:

```bash
export TEST_MODE=generate
export SOURCE_REPO_OWNER=kylodeng
export SOURCE_REPO_NAME=ai-delivery-source
python .github/scripts/tool4_auto_testing.py
```

To run a coverage gap analysis instead:

```bash
export TEST_MODE=gap-analysis
python .github/scripts/tool4_auto_testing.py
```

---

## 7. Deployment

### GitHub Actions (AI workflows)

No deployment step is required. Workflows are active as soon as the repository secrets are configured:

1. Navigate to **Settings → Secrets and variables → Actions** in the GitHub repository.
2. Add the following repository secrets:
   - `ANTHROPIC_API_KEY`
   - `GH_TOKEN`
   - `SENDGRID_API_KEY`
3. Ensure the `ai-delivery-outputs` repository exists under the same owner and that `GH_TOKEN` has write access to it.
4. Workflows trigger automatically on their configured events (see table below) or can be run manually via **Actions → workflow → Run workflow**.

| Workflow | Automatic triggers |
|---|---|
| Tool 1 — Code Review | PR opened/synchronised/reopened; every Monday 08:00 UTC |
| Tool 2 — Tech Documentation | Push to `main` (non-docs files); every Sunday 06:00 UTC |
| Tool 3 — Business Documentation | Push of a `v*` tag; manual dispatch |
| Tool 4 — Auto Testing | PR opened/synchronised on `src/**` or `*.py/js/ts`; every Wednesday 07:00 UTC |
| Tool 5 — UAT Facilitation | Creation of a `release/*` branch; manual dispatch |

### AWS Infrastructure (Terraform)

```bash
cd infra
terraform init
terraform plan -var="environment=prod" -var="aws_region=us-east-1"
terraform apply -var="environment=prod" -var="aws_region=us-east-1"
```

### Lambda Deployment

[TODO: How is `lambda.zip` built and uploaded? The Terraform resource references `filename = "lambda.zip"` but no build script or CI step for packaging the Lambda is present in the provided files.]

---

## 8. Known Issues / TODOs

The following issues were extracted directly from code comments:

### Security (Critical)

- **`src/data_pipeline.py` line 9–10:** AWS credentials (`AWS_ACCESS_KEY`, `AWS_SECRET_KEY`) are hardcoded in source code.
  > `# TODO: move this to secrets manager`

- **`infra/main.tf` Lambda environment block:** Database password is hardcoded in the Terraform Lambda environment variable.
  > `# Hardcoded secret - should use SSM or Secrets Manager`
  > `DB_PASSWORD = "SuperSecret123!"`

### Infrastructure gaps

- **`infra/main.tf` S3 landing bucket:** No server-side encryption configured and no public access block enabled.
  > `# S3 landing bucket - NO encryption, NO public access block`

- **`infra/main.tf` IAM policy:** Lambda execution role is granted `s3:*` on `*` (all S3 actions on all resources).
  > `# Overly permissive policy - full S3 access`

- **`infra/main.tf` S3 buckets:** No resource tags are applied to either bucket.
  > `# TODO: add tags`

### Code quality

- **`src/data_pipeline.py` `get_all_pending_files`:** S3 `list_objects_v2` call has no pagination — will silently truncate results beyond 1,000 objects.
  > `# SQL injection not applicable here but no pagination implemented`

- **`src/data_pipeline.py` `lambda_handler`:** Bare `except Exception` in the handler swallows all error types without re-raising.
  > `# bare except swallows all errors`

- **`src/data_pipeline.py` `process_csv`:** No error handling if the downloaded CSV is malformed.
  > `# No error handling if CSV is malformed`

### Documentation gaps

- `shared.py`: `send_email`, `email_html`, and `write_audit_entry` functions are referenced by all five tool scripts but their implementations were truncated in the provided files. [TODO: Are these functions fully implemented in `shared.py`?]
- `tool2_tech_docs.py`: `build_index` function is truncated mid-line. [TODO: Is this function complete in the actual repository?]
- `tool4_auto_testing.py` and `tool5_uat.py`: `build_test_report` and `build_test_pack_csv` functions are truncated. [TODO: Are these complete in the actual repository?]