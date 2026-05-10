# Architecture Document — kylodeng/ai-delivery-source

---

## 1. Overview

This repository implements an **AI-assisted software delivery platform** built entirely on GitHub Actions. It consists of two loosely coupled subsystems: (1) a set of five Claude-powered automation tools that run as GitHub Actions workflows to perform code review, technical documentation generation, business documentation generation, automated test generation, and UAT facilitation for any source repository; and (2) a sample AWS data ingestion pipeline (the "source system" being delivered) that ingests customer CSV files from an S3 landing bucket, validates and transforms them, and writes Parquet output to a processed S3 bucket via an AWS Lambda function. All AI-generated artefacts are committed to a separate output repository (`ai-delivery-outputs`) and notification emails are dispatched via SendGrid. The platform is designed to be dropped into any GitHub repository to accelerate delivery workflow automation using Anthropic's Claude Sonnet model.

---

## 2. Resources Deployed

| Resource | Type | Cloud Provider | Purpose |
|---|---|---|---|
| `capco-data-landing-{env}` | S3 Bucket | AWS | Landing zone for raw customer CSV files |
| `capco-data-processed-{env}` | S3 Bucket | AWS | Stores validated, transformed Parquet files |
| `data-ingest-{env}` | Lambda Function (Python 3.12) | AWS | Processes CSV files triggered by S3 object creation events |
| `lambda-ingest-role` | IAM Role | AWS | Execution role for the Lambda function |
| `lambda-s3-policy` | IAM Role Policy | AWS | Grants S3 permissions to Lambda (see Security section) |
| S3 Bucket Notification | S3 Event Notification | AWS | Triggers Lambda on `s3:ObjectCreated:*` for `raw/*.csv` |
| GitHub Actions Runners (`ubuntu-latest`) | CI/CD Compute | GitHub | Executes all five AI workflow tools |
| `ai-delivery-outputs` | GitHub Repository | GitHub | Stores all AI-generated artefacts (docs, test files, reports) |
| Claude Sonnet (`claude-sonnet-4-6`) | External AI API | Anthropic (third-party) | Performs code review, doc generation, test generation, UAT facilitation |
| SendGrid | External Email API | Twilio/SendGrid (third-party) | Delivers notification emails on workflow completion |

---

## 3. Data Flow

### 3A — Data Ingestion Pipeline (AWS)

1. An upstream system or user uploads a customer CSV file to `s3://capco-data-landing-{env}/raw/<filename>.csv`.
2. The S3 bucket notification fires an `s3:ObjectCreated:*` event to the `data-ingest-{env}` Lambda function.
3. Lambda's `lambda_handler` receives the event, extracts the `bucket` and `key` values, and calls `process_csv()`.
4. `process_csv()` instantiates a boto3 S3 client (using **hardcoded credentials** — see Security section) and calls `get_object` to download the CSV into memory as a pandas DataFrame.
5. Each row is passed through `validate_customer_record()`, which checks for required fields (`customer_id`, `email`, `age`, `country_code`), email format, and age range (1–150). Valid rows are collected; invalid rows are logged as failures.
6. The validated DataFrame is written back to S3 as a Parquet file under the `processed/` prefix in the **same landing bucket** (not the processed bucket — see Risks).
7. Lambda returns a JSON response with counts of processed/failed rows, the output key, and a UTC timestamp.

### 3B — AI Delivery Workflows (GitHub Actions)

1. A trigger event fires (PR open, push to main, tag push, branch creation, schedule, or manual dispatch) on the source repository.
2. The relevant GitHub Actions workflow (Tool 1–5) checks out the source repository on an `ubuntu-latest` runner.
3. Python 3.12 is set up and `anthropic` + `requests` packages are installed via pip.
4. `shared.py` reads secrets from environment variables (Anthropic API key, GitHub token, SendGrid key).
5. Source files and/or PR diffs are fetched from the GitHub API using the `GH_TOKEN` bearer token.
6. The relevant tool script constructs a system prompt and user prompt, then calls the Anthropic Claude API (`claude-sonnet-4-6`) via `shared.call_claude()`.
7. Claude's response (review JSON, markdown document, test file, UAT pack, etc.) is parsed and formatted.
8. The output is committed to the `ai-delivery-outputs` repository via the GitHub Contents API (create/update file with base64-encoded content).
9. For Tool 1 (Code Review), a summary comment is also posted directly to the PR via the GitHub Issues Comments API.
10. A SendGrid email notification is sent to `kylo.deng@capco.com` with a summary and link to the output artefact.
11. An audit log entry is written [TODO: audit log destination not fully visible in provided source — `write_audit_entry` referenced but implementation truncated in `shared.py`].

---

## 4. Security Posture

### ✅ What Is Secured

- **GitHub Secrets**: `ANTHROPIC_API_KEY`, `GH_TOKEN`, and `SENDGRID_API_KEY` are stored as GitHub Actions encrypted secrets and injected as environment variables at runtime — not hardcoded in workflow YAML.
- **IAM Trust Policy**: The Lambda execution role correctly restricts `sts:AssumeRole` to the `lambda.amazonaws.com` service principal only.
- **S3 Event Filter**: The Lambda trigger is scoped to `raw/` prefix and `.csv` suffix, reducing the blast radius of accidental triggers.
- **PR comment posting** uses a scoped GitHub token rather than embedding credentials in output.

### ❌ Gaps and Explicit Security Failures

| Gap | Severity | Detail |
|---|---|---|
| **Hardcoded AWS credentials in source code** | CRITICAL | `src/data_pipeline.py` lines 10–11 contain `AWS_ACCESS_KEY = "AKIAIOSFODNN7EXAMPLE"` and `AWS_SECRET_KEY = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"`. Even if these are example keys, the pattern is dangerous and must be removed. Credentials should be sourced from the Lambda execution role (instance profile) — no explicit credentials needed. |
| **Hardcoded DB password in Terraform** | CRITICAL | `infra/main.tf` Lambda environment variable `DB_PASSWORD = "SuperSecret123!"` is hardcoded in plaintext IaC. Must be replaced with an AWS SSM Parameter Store or Secrets Manager reference. |
| **S3 landing bucket has no encryption** | HIGH | `aws_s3_bucket.landing` has no `aws_s3_bucket_server_side_encryption_configuration` resource. Customer PII (email, age, country) is stored unencrypted at rest. |
| **S3 landing bucket has no public access block** | HIGH | No `aws_s3_bucket_public_access_block` resource is attached to the landing bucket. The bucket could be made public accidentally. |
| **IAM policy grants `s3:*` on `*`** | HIGH | `lambda_policy` allows all S3 actions on all resources. Lambda should only need `s3:GetObject` on the landing bucket and `s3:PutObject` on the processed bucket. This violates the principle of least privilege. |
| **Processed Parquet written to landing bucket, not processed bucket** | HIGH | `data_pipeline.py:process_csv()` writes output to `s3://{bucket}/{output_key}` where `bucket` is the landing bucket passed in the event. The `aws_s3_bucket.processed` resource is provisioned but never used in application code. |
| **No encryption on processed S3 bucket** | HIGH | `aws_s3_bucket.processed` also lacks server-side encryption configuration. |
| **No S3 bucket versioning** | MEDIUM | Neither bucket has versioning enabled, meaning accidental overwrites or deletions are unrecoverable. |
| **No S3 bucket logging** | MEDIUM | No access logging is configured on either bucket. |
| **No VPC / network isolation for Lambda** | MEDIUM | Lambda runs in the default AWS network context with no VPC attachment, security groups, or private subnet configuration. |
| **GH_TOKEN scope unknown** | MEDIUM | The `GH_TOKEN` secret is used to read source repos, write to `ai-delivery-outputs`, and post PR comments. The required scopes (`repo`, `contents:write`) are broad. [TODO: confirm whether a fine-grained PAT is used or a classic token with full repo scope] |
| **No S3 bucket tags** | LOW | `aws_s3_bucket.landing` has a `# TODO: add tags` comment. Both buckets are untagged, preventing cost allocation and resource management. |
| **No input sanitisation in Lambda handler** | LOW | `lambda_handler` reads `event["key"]` without validating the key format before passing to `process_csv`. A malformed key could cause unexpected behaviour. |
| **Bare `except Exception` in lambda_handler** | LOW | Swallows all error types and returns a 500. Specific exception handling should be implemented. |
| **No S3 pagination in `get_all_pending_files`** | LOW | `list_objects_v2` returns at most 1,000 keys. For buckets with more files, some will be silently skipped. |

---

## 5. Environment Variables and Secrets

| Name | Required | Sensitivity | Where Set |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | **High** — API key for paid LLM service | GitHub Actions Secret (`secrets.ANTHROPIC_API_KEY`) |
| `GH_TOKEN` | Yes | **High** — GitHub Personal Access Token with repo write access | GitHub Actions Secret (`secrets.GH_TOKEN`) |
| `SENDGRID_API_KEY` | Yes | **High** — SendGrid email API key | GitHub Actions Secret (`secrets.SENDGRID_API_KEY`) |
| `OUTPUT_REPO` | No | Low | Workflow `env` block, defaults to `ai-delivery-outputs` |
| `OUTPUT_REPO_OWNER` | No | Low | Workflow `env` block, set to `github.repository_owner` |
| `NOTIFY_EMAIL` | No | Low | Workflow `env` block, hardcoded to `kylo.deng@capco.com` |
| `SENDER_EMAIL` | No | Low | Workflow `env` block, hardcoded to `noreply@ai-delivery.capco.com` |
| `SOURCE_REPO_OWNER` | No | Low | Workflow `env` block, set to `github.repository_owner` |
| `SOURCE_REPO_NAME` | No | Low | Workflow `env` block, set to `github.event.repository.name` |
| `GITHUB_RUN_URL` | No | Low | Workflow `env` block, constructed from GitHub context |
| `REVIEW_MODE` | Conditional | Low | Set dynamically in workflow step (`pr` or `repo`) |
| `PR_NUMBER` | Conditional | Low | Set dynamically in workflow step when `REVIEW_MODE=pr` |
| `TEST_MODE` | No | Low | Workflow `env` block for Tool 4, defaults to `generate` |
| `RELEASE_VERSION` | Conditional | Low | Set dynamically in Tool 3 / Tool 5 workflows |
| `PROJECT_NAME` | Conditional | Low | Set dynamically in Tool 3 workflow |
| `UAT_MODE` | Conditional | Low | Set dynamically in Tool 5 workflow |
| `USER_STORIES` | No | Low | Set from workflow_dispatch input in Tool 5 |
| `UAT_RESULTS_PATH` | Conditional | Low | Set from workflow_dispatch input in Tool 5 (analyse mode) |
| `LANDING_BUCKET` | Yes (Lambda) | Low | Lambda environment variable set in Terraform |
| `DB_PASSWORD` | Yes (Lambda) | **CRITICAL** — plaintext secret | ⚠️ Hardcoded in `infra/main.tf` — must be moved to Secrets Manager |
| `AWS_ACCESS_KEY` | N/A | **CRITICAL** — AWS credential | ⚠️ Hardcoded in `src/data_pipeline.py` — must be removed entirely; use IAM role |
| `AWS_SECRET_KEY` | N/A | **CRITICAL** — AWS credential | ⚠️ Hardcoded in `src/data_pipeline.py` — must be removed entirely; use IAM role |

---

## 6. Dependencies

| Dependency | Type | Purpose | Notes |
|---|---|---|---|
| **Anthropic Claude API** (`claude-sonnet-4-6`) | External SaaS API | Core AI inference for all five tools | Paid API; rate limits and token costs apply. No retry logic observed in `shared.py`. |
| **SendGrid API** | External SaaS API | Email notification delivery | [TODO: confirm SendGrid account/domain ownership for `noreply@ai-delivery.capco.com`] |
| **GitHub API** (`api.github.com`) | External SaaS API | Read source files, post PR comments, write output files | Uses `GH_TOKEN`; API rate limits apply (5,000 req/hr for authenticated requests) |
| **`ai-delivery-outputs`** | Sibling GitHub Repository | Stores all AI-generated artefacts | Must exist and be writable by `GH_TOKEN` before workflows run |
| **`anthropic` Python package** | PyPI Library | Python SDK for Claude API | Installed at runtime via pip; no version pinned — [TODO: pin version in requirements.txt] |
| **`requests` Python package** | PyPI Library | HTTP calls to GitHub and SendGrid APIs | Installed at runtime via pip; no version pinned |
| **`pandas`** | PyPI Library | CSV parsing and DataFrame operations in pipeline | Used in `data_pipeline.py`; not listed in any requirements file |
| **`boto3`** | PyPI Library | AWS SDK for S3 operations in pipeline | Used in `data_pipeline.py`; not listed in any requirements file |
| **AWS** (`us-east-1`) | Cloud Provider | Hosts S3 buckets and Lambda function | Single region only — no multi-region DR |
| **Terraform `~> 5.0` AWS Provider** | IaC Runtime | Provisions AWS infrastructure | Requires Terraform CLI and AWS credentials at deploy time |

---

## 7. Deployment Instructions

### Prerequisites

- AWS CLI configured with credentials that have permissions to create S3, Lambda, and IAM resources
- Terraform >= 1.0 installed
- A Lambda deployment package (`lambda.zip`) containing `data_pipeline.py` and its dependencies
- The `ai-delivery-outputs` GitHub repository created and accessible by `GH_TOKEN`
- GitHub Actions secrets configured: `ANTHROPIC_API_KEY`, `GH_TOKEN`, `SENDGRID_API_KEY`

### Step 1 — Build Lambda Deployment Package

```bash
pip install boto3 pandas pyarrow --target ./package
cp src/data_pipeline.py ./package/
cd package && zip -r ../lambda.zip . && cd ..
```

### Step 2 — Deploy AWS Infrastructure

```bash
cd infra/
terraform init
terraform plan -var="environment=dev" -var="aws_region=us-east-1"
terraform apply -var="environment=dev" -var="aws_region=us-east-1"
```

### Step 3 — Deploy to a Different Environment (e.g., prod)

```bash
terraform apply -var="environment=prod" -var="aws_region=us-east-1"
```

### Step 4 — Configure GitHub Actions Secrets

```bash
# Using GitHub CLI
gh secret set ANTHROPIC_API_KEY --body "<your-anthropic-key>"
gh secret set GH_TOKEN --body "<your-github-pat>"
gh secret set SENDGRID_API_KEY --body "<your-sendgrid-key>"
```

### Step 5 — Trigger AI Workflows Manually

```bash
# Tool 1: Code Review (full repo scan)
gh workflow run tool1_code_review.yml -f review_mode=repo

# Tool 2: Tech Documentation
gh workflow run tool2_tech_docs.yml

# Tool 3: Business Documentation
gh workflow run tool3_business_docs.yml \
  -f project_name="Data Ingestion Pipeline" \
  -f release_version="1.0.0"

# Tool 4: Auto Testing (generate mode)
gh workflow run