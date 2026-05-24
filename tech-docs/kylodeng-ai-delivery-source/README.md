# ai-delivery-source

## 1. Project Overview

This repository contains a customer CSV data ingestion pipeline that reads files from an S3 landing bucket, validates and transforms them to Parquet, and writes results to a processed S3 bucket via an AWS Lambda function. It also ships five AI-powered GitHub Actions workflows that use Claude (Anthropic) to automate code review, technical documentation, business documentation, test generation, and UAT facilitation across any source repository. All AI-generated outputs are written to a companion repository (`ai-delivery-outputs`) and notifications are sent via SendGrid.

---

## 2. Tech Stack

| Component | Technology | Version/Notes |
|---|---|---|
| Pipeline runtime | Python | 3.12 |
| AI model | Anthropic Claude | `claude-sonnet-4-6` |
| Anthropic SDK | `anthropic` (Python) | Latest compatible with Claude Sonnet 4 |
| HTTP client | `requests` | pip-installed |
| Data processing | `pandas` | pip-installed |
| Cloud runtime | AWS Lambda | Python 3.12 runtime |
| Object storage | AWS S3 | Landing + processed buckets |
| IaC | Terraform (AWS provider) | `~> 5.0` |
| CI/CD | GitHub Actions | `ubuntu-latest`, Node 24 |
| Email notifications | SendGrid | Via `SENDGRID_API_KEY` |
| Output storage | GitHub repository | `ai-delivery-outputs` |

---

## 3. Architecture

The repository contains two distinct systems that share CI/CD infrastructure:

**Data Pipeline:**  
A CSV file uploaded to the `raw/` prefix of the S3 landing bucket (`capco-data-landing-<env>`) triggers a Lambda function (`data-ingest-<env>`) via an S3 bucket notification. The Lambda downloads the file, validates each row (required fields, email format, age range), and writes a Parquet file to `processed/` in the same bucket. The Lambda's IAM role grants it access to S3.

**AI Delivery Workflows:**  
Five GitHub Actions workflows (Tools 1–5) run on `ubuntu-latest`. Each workflow installs Python 3.12 with the `anthropic` and `requests` packages, then executes a corresponding script under `.github/scripts/`. All scripts share utilities from `shared.py` (Claude API calls, GitHub API helpers, SendGrid email, audit logging). Scripts read source files or PR diffs from the triggering repository via the GitHub API, call Claude, and write generated artefacts (markdown reports, test files, UAT packs) as committed files to the `ai-delivery-outputs` repository. PR comments are posted back to the source repo where applicable.

```
GitHub Event
    │
    ▼
GitHub Actions Workflow (tool1–5.yml)
    │
    ├── .github/scripts/shared.py ──► GitHub API (read source files / post PR comments)
    │                              ──► Anthropic Claude API (generate content)
    │                              ──► GitHub API (write to ai-delivery-outputs repo)
    │                              ──► SendGrid (email notification)
    │
S3 ObjectCreated event
    │
    ▼
Lambda (data_pipeline.lambda_handler)
    │
    ├── S3 GET  (capco-data-landing-<env>/raw/*.csv)
    ├── validate + transform (pandas)
    └── S3 PUT  (capco-data-landing-<env>/processed/*.parquet)
```

---

## 4. Local Development Setup

### Data Pipeline

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

3. **Install Python dependencies**

```bash
pip install boto3 pandas anthropic requests
```

4. **Set required environment variables** (see [Environment Variables](#5-environment-variables))

```bash
export LANDING_BUCKET=capco-data-landing-dev
export ANTHROPIC_API_KEY=your_key_here
export GH_TOKEN=your_github_pat_here
export SENDGRID_API_KEY=your_sendgrid_key_here
```

5. **Invoke the pipeline locally against a real or mocked S3 bucket**

```bash
python -c "
from src.data_pipeline import process_csv
result = process_csv('capco-data-landing-dev', 'raw/sample.csv')
print(result)
"
```

### AI Delivery Scripts

6. **Run a script directly** (e.g. tech docs generator)

```bash
export OUTPUT_REPO=ai-delivery-outputs
export OUTPUT_REPO_OWNER=kylodeng
export SOURCE_REPO_OWNER=kylodeng
export SOURCE_REPO_NAME=ai-delivery-source
export NOTIFY_EMAIL=you@example.com
export SENDER_EMAIL=you@example.com

python .github/scripts/tool2_tech_docs.py
```

---

## 5. Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | — | Anthropic API key used to call Claude |
| `GH_TOKEN` | Yes | — | GitHub Personal Access Token with repo read/write permissions |
| `SENDGRID_API_KEY` | Yes | — | SendGrid API key for email notifications |
| `OUTPUT_REPO` | No | `ai-delivery-outputs` | Name of the GitHub repo where AI outputs are written |
| `OUTPUT_REPO_OWNER` | No | `GITHUB_REPOSITORY_OWNER` env value | GitHub owner/org of the output repository |
| `NOTIFY_EMAIL` | No | `kylo.deng@capco.com` | Recipient email address for workflow notifications |
| `SENDER_EMAIL` | No | `kylo.deng@capco.com` | Sender email address used with SendGrid |
| `SOURCE_REPO_OWNER` | Yes (workflows) | `github.repository_owner` | Owner of the source repository being analysed |
| `SOURCE_REPO_NAME` | Yes (workflows) | `github.event.repository.name` | Name of the source repository being analysed |
| `LANDING_BUCKET` | Yes (Lambda) | — | S3 bucket name for raw CSV ingestion |
| `REVIEW_MODE` | No (Tool 1) | `repo` | Code review mode: `pr` or `repo` |
| `PR_NUMBER` | No (Tool 1) | — | Pull request number when `REVIEW_MODE=pr` |
| `RELEASE_VERSION` | No (Tools 3 & 5) | `0.1.0` | Version string attached to generated documents |
| `PROJECT_NAME` | No (Tool 3) | repository name | Human-readable project/solution name for business docs |
| `TEST_MODE` | No (Tool 4) | `generate` | `generate` new tests or run `gap-analysis` |
| `UAT_MODE` | No (Tool 5) | `generate` | `generate` a UAT test pack or `analyse` completed results |
| `USER_STORIES` | No (Tool 5) | — | Optional acceptance criteria/user stories pasted as text |
| `UAT_RESULTS_PATH` | No (Tool 5) | — | Path in output repo to completed UAT results CSV (analyse mode) |
| `GITHUB_RUN_URL` | No | set by Actions | URL of the current Actions run, appended to generated reports |

> **Note:** In GitHub Actions all secrets (`ANTHROPIC_API_KEY`, `GH_TOKEN`, `SENDGRID_API_KEY`) must be configured in the repository's **Settings → Secrets and variables → Actions**.

---

## 6. Running Tests

[TODO: Are there existing test files in this repository, or does Tool 4 generate them into `ai-delivery-outputs` only?]

To generate tests for the pipeline using Tool 4 locally:

```bash
export TEST_MODE=generate
python .github/scripts/tool4_auto_testing.py
```

To run a coverage gap analysis:

```bash
export TEST_MODE=gap-analysis
python .github/scripts/tool4_auto_testing.py
```

Generated test files are written to the `ai-delivery-outputs` repository under a path derived from the source repo name. To execute any generated pytest tests locally:

```bash
pip install pytest pytest-mock
pytest <path-to-generated-test-file> -v
```

---

## 7. Deployment

### Infrastructure (Terraform)

1. **Ensure AWS credentials are configured**

```bash
export AWS_ACCESS_KEY_ID=your_access_key
export AWS_SECRET_ACCESS_KEY=your_secret_key
export AWS_DEFAULT_REGION=us-east-1
```

2. **Initialise Terraform**

```bash
cd infra
terraform init
```

3. **Review the plan**

```bash
terraform plan -var="environment=dev"
```

4. **Apply**

```bash
terraform apply -var="environment=dev"
```

5. **Package and deploy the Lambda function** [TODO: Is there a build script that produces `lambda.zip`? The Terraform references `filename = "lambda.zip"` but no packaging step is defined in the repository]

```bash
# Example — confirm actual packaging steps
zip lambda.zip src/data_pipeline.py
terraform apply -var="environment=dev"
```

### GitHub Actions Workflows

The five workflows deploy automatically based on their triggers. To trigger manually via the GitHub UI:

- Go to **Actions** → select the desired workflow → **Run workflow**
- Provide any required inputs (e.g. `project_name`, `release_version`, `uat_mode`)

To trigger via the GitHub CLI:

```bash
# Example: manually trigger Tool 3 — Business Documentation
gh workflow run tool3_business_docs.yml \
  -f project_name="Data Ingestion Pipeline" \
  -f release_version="1.0.0"
```

---

## 8. Known Issues / TODOs

The following are extracted directly from code comments:

| Location | Issue |
|---|---|
| `src/data_pipeline.py:12–13` | **CRITICAL — SECURITY:** AWS credentials (`AWS_ACCESS_KEY`, `AWS_SECRET_KEY`) are hardcoded. Comment says: *"move this to secrets manager"* |
| `src/data_pipeline.py` | S3 listing in `get_all_pending_files` has no pagination; will silently truncate results beyond the default 1,000-object page |
| `src/data_pipeline.py` | `process_csv`: no error handling if the downloaded CSV is malformed (empty file, bad encoding, etc.) |
| `src/data_pipeline.py` | `lambda_handler` uses a broad `except Exception` that swallows all error detail |
| `infra/main.tf:20–21` | **SECURITY:** S3 landing bucket has no server-side encryption and no public access block configured |
| `infra/main.tf:24` | S3 processed bucket has no encryption configured |
| `infra/main.tf:38–39` | **SECURITY:** Lambda environment variable `DB_PASSWORD` is hardcoded as `"SuperSecret123!"`. Comment: *"should use SSM or Secrets Manager"* |
| `infra/main.tf:50–60` | **SECURITY:** IAM policy grants `s3:*` on `*` (all S3 actions on all resources) — overly permissive |
| `infra/main.tf` | S3 buckets and Lambda function have no resource tags. Comment: *"TODO: add tags"* |
| `infra/main.tf` | No DR configuration, no CloudWatch monitoring or alerting defined |
| `.github/scripts/tool2_tech_docs.py` | `build_index` function contains a truncated f-string (`{r` at end of file excerpt) — possible bug in source |
| All workflows | No disaster recovery or multi-region failover for the data pipeline |
| `tool5_uat.py` | `build_test_pack_csv` function definition is truncated in the available source |