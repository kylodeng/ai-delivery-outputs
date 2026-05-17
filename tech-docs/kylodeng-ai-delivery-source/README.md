# ai-delivery-source

## 1. Project Overview

This repository contains a customer CSV data ingestion pipeline that reads files from an S3 landing bucket, validates and transforms records, and writes Parquet output to a processed S3 bucket via an AWS Lambda function. It also hosts five Claude AI-powered GitHub Actions workflows that automate code review, technical documentation, business documentation, test generation, and UAT facilitation across any source repository.

---

## 2. Tech Stack

| Component | Technology | Version/Notes |
|---|---|---|
| Runtime language | Python | 3.12 |
| AI model | Anthropic Claude | `claude-sonnet-4-6` |
| AI SDK | `anthropic` (Python) | Latest compatible with Python 3.12 |
| HTTP client | `requests` | Latest |
| Data processing | `pandas` | Latest |
| Cloud provider | AWS | us-east-1 (default) |
| Compute | AWS Lambda | Python 3.12 runtime |
| Storage | AWS S3 | Landing + processed buckets |
| IaC | Terraform | AWS provider `~> 5.0` |
| CI/CD | GitHub Actions | ubuntu-latest, Node 24 |
| Email notifications | SendGrid API | Via `SENDGRID_API_KEY` |
| Output storage | Separate GitHub repo | `ai-delivery-outputs` |

---

## 3. Architecture

The repository has two distinct layers:

**Data Pipeline:**  
A Python Lambda function (`src/data_pipeline.py`) is triggered by S3 `ObjectCreated` events on the `raw/` prefix of the landing bucket (`capco-data-landing-{env}`). It downloads the CSV, validates each row against required fields and business rules, and writes a Parquet file to the `processed/` prefix of the same bucket (`capco-data-processed-{env}`). Infrastructure is defined in `infra/main.tf` (Terraform) which provisions both S3 buckets, the Lambda function, an IAM role/policy, and the S3 event notification.

**AI Delivery Workflows:**  
Five GitHub Actions workflows (`.github/workflows/tool*.yml`) each invoke a corresponding Python script (`.github/scripts/tool*.py`). All scripts share common utilities in `.github/scripts/shared.py` for calling the Claude API, reading source files from the GitHub API, writing output files to the `ai-delivery-outputs` repository, posting PR comments, and sending email notifications via SendGrid. Generated artefacts (reports, docs, test files, UAT packs) are committed to the `ai-delivery-outputs` repo and optionally emailed to `NOTIFY_EMAIL`.

```
Source Repo (this repo)
        │
        ├── GitHub Actions Workflow triggered
        │         │
        │         ▼
        │   .github/scripts/tool*.py
        │         │
        │    ┌────┴────────────────────┐
        │    │                         │
        │    ▼                         ▼
        │  Claude API            GitHub API
        │  (analysis)        (read source files,
        │                    write output files,
        │                    post PR comments)
        │                         │
        │                         ▼
        │                  ai-delivery-outputs repo
        │                         │
        │                         ▼
        │                  SendGrid (email notification)
        │
        └── S3 Event → Lambda (data_pipeline.py)
                             │
                    Validate + transform CSV
                             │
                    Write Parquet → S3 processed/
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

4. **Set required environment variables** (see [Environment Variables](#5-environment-variables) below)

```bash
export ANTHROPIC_API_KEY="your-anthropic-api-key"
export GH_TOKEN="your-github-pat"
export SENDGRID_API_KEY="your-sendgrid-api-key"
export OUTPUT_REPO="ai-delivery-outputs"
export OUTPUT_REPO_OWNER="your-github-username"
export NOTIFY_EMAIL="you@example.com"
export SENDER_EMAIL="noreply@example.com"
```

5. **Run a script locally (example: tool 1 code review in repo mode)**

```bash
export REVIEW_MODE=repo
export SOURCE_REPO_OWNER=kylodeng
export SOURCE_REPO_NAME=ai-delivery-source
export GITHUB_RUN_URL="http://localhost"
python .github/scripts/tool1_code_review.py
```

6. **Provision infrastructure with Terraform**

```bash
cd infra
terraform init
terraform plan -var="environment=dev"
terraform apply -var="environment=dev"
```

---

## 5. Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | — | API key for Anthropic Claude |
| `GH_TOKEN` | Yes | — | GitHub Personal Access Token with repo read/write permissions |
| `SENDGRID_API_KEY` | Yes | — | SendGrid API key for email notifications |
| `OUTPUT_REPO` | No | `ai-delivery-outputs` | Name of the GitHub repo where AI-generated artefacts are written |
| `OUTPUT_REPO_OWNER` | No | `GITHUB_REPOSITORY_OWNER` | GitHub owner/org of the output repo |
| `NOTIFY_EMAIL` | No | `kylo.deng@capco.com` | Recipient email for workflow notifications |
| `SENDER_EMAIL` | No | `kylo.deng@capco.com` | Sender email address used by SendGrid |
| `SOURCE_REPO_OWNER` | Yes (workflows) | `github.repository_owner` | Owner of the source repository being analysed |
| `SOURCE_REPO_NAME` | Yes (workflows) | `github.event.repository.name` | Name of the source repository being analysed |
| `GITHUB_RUN_URL` | No | Set by Actions | URL of the current Actions run, included in reports |
| `LANDING_BUCKET` | Yes (Lambda) | — | Name of the S3 landing bucket; passed via Lambda environment variable |
| `REVIEW_MODE` | No (Tool 1) | `repo` | `pr` or `repo`; controls whether tool 1 reviews a PR diff or full repo |
| `PR_NUMBER` | No (Tool 1) | — | PR number to review when `REVIEW_MODE=pr` |
| `RELEASE_VERSION` | No (Tools 3, 5) | `0.1.0` | Version string used in business docs and UAT packs |
| `PROJECT_NAME` | No (Tool 3) | Repository name | Human-readable project name for business docs |
| `TEST_MODE` | No (Tool 4) | `generate` | `generate` (create tests) or `gap-analysis` (analyse coverage) |
| `UAT_MODE` | No (Tool 5) | `generate` | `generate` (create test pack) or `analyse` (process completed results) |
| `USER_STORIES` | No (Tool 5) | — | Acceptance criteria / user stories pasted inline for UAT pack generation |
| `UAT_RESULTS_PATH` | No (Tool 5) | — | Path in output repo to completed UAT results CSV for analysis mode |

---

## 6. Running Tests

[TODO: Are there any existing tests in this repository, or is test generation handled entirely by Tool 4 writing output to `ai-delivery-outputs`?]

To generate tests for the data pipeline using Tool 4 locally:

```bash
export TEST_MODE=generate
export SOURCE_REPO_OWNER=kylodeng
export SOURCE_REPO_NAME=ai-delivery-source
export GITHUB_RUN_URL="http://localhost"
python .github/scripts/tool4_auto_testing.py
```

To run a coverage gap analysis:

```bash
export TEST_MODE=gap-analysis
python .github/scripts/tool4_auto_testing.py
```

Generated test files are written to the `ai-delivery-outputs` repository under a path derived from the source repo name.

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

To destroy:

```bash
terraform destroy -var="environment=dev"
```

### Lambda Deployment

[TODO: How is `lambda.zip` built and uploaded? Is there a build script or CI step that packages `src/data_pipeline.py`?]

The Terraform config references `filename = "lambda.zip"` in `infra/main.tf`. Before running `terraform apply`, package the Lambda manually:

```bash
pip install pandas boto3 -t package/
cp src/data_pipeline.py package/
cd package && zip -r ../lambda.zip . && cd ..
mv lambda.zip infra/lambda.zip
```

### GitHub Actions Workflows

The five workflows run automatically based on their triggers. They can also be triggered manually from the **Actions** tab in GitHub. Required secrets must be set in the repository's **Settings → Secrets and variables → Actions**:

- `ANTHROPIC_API_KEY`
- `GH_TOKEN`
- `SENDGRID_API_KEY`

The `ai-delivery-outputs` repository must exist under the same GitHub owner and the `GH_TOKEN` must have write access to it.

---

## 8. Known Issues / TODOs

The following issues are extracted directly from code comments:

| Location | Issue |
|---|---|
| `src/data_pipeline.py` line 10–11 | AWS credentials (`AWS_ACCESS_KEY`, `AWS_SECRET_KEY`) are hardcoded — must be moved to AWS Secrets Manager |
| `src/data_pipeline.py` `get_all_pending_files()` | S3 `list_objects_v2` has no pagination; will silently miss files beyond the first 1000 |
| `src/data_pipeline.py` `process_csv()` | No error handling if the CSV is malformed |
| `src/data_pipeline.py` `lambda_handler()` | Bare `except Exception` swallows all errors |
| `infra/main.tf` S3 landing bucket | No server-side encryption configured |
| `infra/main.tf` S3 landing bucket | No `aws_s3_bucket_public_access_block` resource — public access not explicitly blocked |
| `infra/main.tf` S3 landing bucket | Missing resource tags (TODO comment in file) |
| `infra/main.tf` Lambda environment | `DB_PASSWORD` is hardcoded in plain text — should use AWS SSM Parameter Store or Secrets Manager |
| `infra/main.tf` IAM policy | Lambda role has `s3:*` on `*` (all S3 resources) — overly permissive; should be scoped to specific bucket ARNs |
| `tool2_tech_docs.py` `build_index()` | Code is truncated — function body appears incomplete in the source |
| `tool3_business_docs.py` `build_full_output()` | Function body is truncated in the source |
| `tool4_auto_testing.py` `build_test_report()` | Function body is truncated in the source |
| `tool5_uat.py` `build_test_pack_csv()` | Function body is truncated in the source |
| All workflows | `send_email`, `email_html`, and `write_audit_entry` are imported from `shared.py` but their implementations are not present in the provided source (truncated) |