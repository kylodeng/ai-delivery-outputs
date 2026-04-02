# Architecture Document — `kylodeng/ai-delivery-source`

---

## 1. Overview

This system is a **dual-purpose platform** combining a cloud data ingestion pipeline with an AI-assisted software delivery toolchain. The data pipeline component ingests customer CSV files uploaded to an AWS S3 landing bucket, validates and transforms them via an AWS Lambda function, and writes the results as Parquet files to a processed S3 bucket. Layered on top of this is a suite of five GitHub Actions workflows — each powered by Anthropic's Claude AI — that automate software delivery activities across the repository lifecycle: automated code review on pull requests, technical and business documentation generation on merges and releases, AI-generated test suites on PR changes, and UAT test pack generation and analysis on release branch creation. Outputs from all five tools are committed to a dedicated `ai-delivery-outputs` GitHub repository and notifications are dispatched via SendGrid email.

---

## 2. Resources Deployed

| Resource | Type | Cloud Provider | Purpose |
|---|---|---|---|
| `capco-data-landing-{env}` | S3 Bucket | AWS | Raw CSV file landing zone; S3 event trigger source |
| `capco-data-processed-{env}` | S3 Bucket | AWS | Stores validated, transformed Parquet output files |
| `data-ingest-{env}` | Lambda Function (Python 3.12) | AWS | Core ingestion handler; validates and transforms CSVs to Parquet |
| `lambda-ingest-role` | IAM Role | AWS | Execution role assumed by the Lambda function |
| `lambda-s3-policy` | IAM Role Policy | AWS | Grants S3 permissions to the Lambda role (overly broad — see §4) |
| S3 Bucket Notification | S3 Event Notification | AWS | Triggers Lambda on `s3:ObjectCreated:*` for `raw/*.csv` |
| Tool 1 — Code Review | GitHub Actions Workflow | GitHub | AI code review on PRs, weekly repo scans, manual dispatch |
| Tool 2 — Tech Docs | GitHub Actions Workflow | GitHub | Generates README, ARCHITECTURE.md, RUNBOOK.md on merge to main |
| Tool 3 — Business Docs | GitHub Actions Workflow | GitHub | Generates solution overview and gap questionnaire on version tags |
| Tool 4 — Auto Testing | GitHub Actions Workflow | GitHub | Generates or gap-analyses test suites on PR changes |
| Tool 5 — UAT Facilitation | GitHub Actions Workflow | GitHub | Generates UAT test packs or analyses completed UAT results on release branches |
| `ai-delivery-outputs` | GitHub Repository | GitHub | Centralised output store for all AI-generated artefacts |
| Claude Sonnet (`claude-sonnet-4-6`) | LLM API | Anthropic (external) | AI inference for all five tools |
| SendGrid | Email API | SendGrid (external) | Delivery of notification emails for all tool completions |

---

## 3. Data Flow

### 3a — Data Ingestion Pipeline

1. An external process or operator uploads a `.csv` file to the `capco-data-landing-{env}` S3 bucket under the `raw/` prefix.
2. S3 fires an `ObjectCreated` event notification filtered to `raw/*.csv`.
3. AWS Lambda invokes `data-ingest-{env}` (`data_pipeline.lambda_handler`), passing the bucket name and object key in the event payload.
4. The Lambda function instantiates a `boto3` S3 client (using **hardcoded credentials** — see §4) and calls `get_object` to download the CSV into memory as a Pandas DataFrame.
5. Each row is validated via `validate_customer_record`: required field presence, basic email format (`@` check), and age range (1–150) are enforced. Valid and invalid rows are separated.
6. Valid rows are converted to a Parquet file and written back to the **same landing bucket** under the `processed/` prefix (the key `raw/X.csv` becomes `processed/X.parquet`).
7. The Lambda returns a JSON summary of processed/failed row counts and the output key. Errors are logged and a 500 response is returned.

> **Note:** The `capco-data-processed` bucket declared in Terraform is not used by the Lambda; the Lambda writes back to `LANDING_BUCKET`. [TODO: Confirm whether `capco-data-processed` is intended to be the output target and whether the Lambda environment variable should point there instead.]

### 3b — AI Delivery Toolchain

1. A GitHub event (PR open, push to main, version tag, release branch creation, or scheduled cron) triggers one of the five GitHub Actions workflows.
2. The runner checks out the source repository and installs `anthropic` and `requests` Python packages.
3. The relevant tool script reads source and/or IaC files from the repository via the **GitHub REST API** (`GET /repos/{owner}/{repo}/git/trees/HEAD?recursive=1`), fetching up to 20 files filtered by extension.
4. For PR-scoped tools (Tool 1, Tool 4), the unified diff is fetched via `GET /repos/{owner}/{repo}/pulls/{pr_number}` with `Accept: application/vnd.github.diff`.
5. File content is assembled into a structured prompt and sent to the **Anthropic Claude API** (`claude-sonnet-4-6`, up to 4,096 output tokens per call).
6. Claude's response (Markdown or JSON depending on tool) is parsed and formatted into one or more output documents.
7. Output documents are committed to the `ai-delivery-outputs` repository via `PUT /repos/{owner}/{repo}/contents/{path}` (creating or updating files with SHA-based conflict resolution).
8. For Tool 1, a formatted review comment is also posted directly to the source PR via `POST /repos/{owner}/{repo}/issues/{pr_number}/comments`.
9. A notification email is sent via the **SendGrid API** to `kylo.deng@capco.com` with a link to the output artefact and a summary of the run.
10. JSON artefacts (Tool 1) are also uploaded as GitHub Actions run artifacts via `actions/upload-artifact@v4`.

---

## 4. Security Posture

### ✅ What is secured

- GitHub Actions secrets (`ANTHROPIC_API_KEY`, `GH_TOKEN`, `SENDGRID_API_KEY`) are stored as encrypted GitHub repository secrets, not hardcoded in workflow YAML files.
- Lambda IAM role uses a correctly scoped trust policy, limiting assumption to `lambda.amazonaws.com` only.
- Tool 4 explicitly requires mocks for all external services (S3, databases, APIs) in generated tests — no real credentials used in test generation.
- GitHub Actions workflows use pinned action versions (`actions/checkout@v4`, `actions/setup-python@v5`, `actions/upload-artifact@v4`).
- UAT Tool 5 is conditionally gated to run only on `refs/heads/release/*` branches or manual dispatch, preventing unintended triggering.

### ❌ Gaps and vulnerabilities — explicit call-outs

| Gap | Severity | Detail |
|---|---|---|
| **Hardcoded AWS credentials in source code** | CRITICAL | `AWS_ACCESS_KEY = "AKIAIOSFODNN7EXAMPLE"` and `AWS_SECRET_KEY` are hardcoded in `src/data_pipeline.py`. These must be removed immediately and rotated. The Lambda's IAM execution role should provide credentials via the standard SDK credential chain. |
| **Hardcoded DB password in Terraform** | CRITICAL | `DB_PASSWORD = "SuperSecret123!"` is set as a plain-text Lambda environment variable in `infra/main.tf`. This is stored in plaintext in Terraform state and in the Lambda configuration. Must be replaced with AWS Secrets Manager or SSM Parameter Store. |
| **S3 landing bucket has no encryption** | HIGH | `aws_s3_bucket.landing` has no `aws_s3_bucket_server_side_encryption_configuration` resource. Data at rest in the landing zone is unencrypted. The processed bucket is also missing explicit encryption configuration. |
| **No S3 public access block on either bucket** | HIGH | Neither S3 bucket has an `aws_s3_bucket_public_access_block` resource. Buckets could be made public accidentally or by policy misconfiguration. |
| **Overly broad IAM policy — `s3:*` on `*`** | HIGH | `lambda-s3-policy` grants `s3:*` on `Resource: "*"`. This gives the Lambda full S3 access across the entire AWS account, not just the two pipeline buckets. Should be scoped to `arn:aws:s3:::capco-data-landing-{env}/*` and `arn:aws:s3:::capco-data-processed-{env}/*` with only required actions (`s3:GetObject`, `s3:PutObject`, `s3:ListBucket`). |
| **No S3 bucket versioning** | MEDIUM | Neither bucket has versioning enabled. Accidental deletion or overwrite of data cannot be recovered. |
| **No S3 bucket lifecycle policy** | MEDIUM | No lifecycle rules are defined. Data accumulates indefinitely in both buckets with no cost control or retention enforcement. |
| **No VPC configuration for Lambda** | MEDIUM | The Lambda function runs outside a VPC. If the downstream database (implied by `DB_PASSWORD`) is inside a VPC, this is a connectivity and security gap. |
| **No Lambda encryption key (KMS)** | MEDIUM | Lambda environment variables (including the hardcoded DB password) are not encrypted with a customer-managed KMS key. |
| **Bare `except` in Lambda handler** | MEDIUM | `lambda_handler` catches all exceptions generically, which masks error types and complicates debugging and alerting. |
| **No input validation on S3 key in Lambda** | MEDIUM | `lambda_handler` uses `event["key"]` directly with no sanitisation or path traversal check. |
| **Email sender domain not verified** | LOW | `SENDER_EMAIL: noreply@ai-delivery.capco.com` — it is unknown whether this domain/sender is verified in SendGrid. [TODO: Confirm SendGrid sender identity verification status.] |
| **No Terraform remote state backend** | LOW | `infra/main.tf` has no `backend` block. State is stored locally, preventing team collaboration and risking state loss or exposure of secrets in plaintext local state files. |
| **No Terraform state encryption** | LOW | Related to above: without a remote backend (e.g. S3 + DynamoDB with SSE), the Terraform state file containing the plaintext `DB_PASSWORD` is unprotected. |
| **No resource tags** | LOW | `# TODO: add tags` comment on the landing bucket; no tags on any resources. This prevents cost allocation, compliance tracking, and automated governance. |

---

## 5. Environment Variables and Secrets

| Name | Required | Sensitivity | Where set |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | 🔴 High — LLM API key | GitHub Actions secret |
| `GH_TOKEN` | Yes | 🔴 High — GitHub PAT with repo write access | GitHub Actions secret |
| `SENDGRID_API_KEY` | Yes | 🔴 High — email service API key | GitHub Actions secret |
| `OUTPUT_REPO` | No | Low | Workflow `env` block (hardcoded: `ai-delivery-outputs`) |
| `OUTPUT_REPO_OWNER` | No | Low | Workflow `env` block (resolved from `github.repository_owner`) |
| `NOTIFY_EMAIL` | No | Low | Workflow `env` block (hardcoded: `kylo.deng@capco.com`) |
| `SENDER_EMAIL` | No | Low | Workflow `env` block (hardcoded: `noreply@ai-delivery.capco.com`) |
| `SOURCE_REPO_OWNER` | No | Low | Workflow `env` block (resolved from `github.repository_owner`) |
| `SOURCE_REPO_NAME` | No | Low | Workflow `env` block (resolved from `github.event.repository.name`) |
| `GITHUB_RUN_URL` | No | Low | Workflow `env` block (resolved from GitHub context) |
| `TEST_MODE` | No | Low | Workflow `env` block (Tool 4 only; default: `generate`) |
| `LANDING_BUCKET` | Yes (Lambda) | Low | Lambda environment variable (set by Terraform) |
| `DB_PASSWORD` | Yes (Lambda) | 🔴 **CRITICAL — hardcoded plaintext** | Lambda environment variable in `main.tf` — **must be replaced with Secrets Manager** |
| `AWS_ACCESS_KEY` | N/A — remove | 🔴 **CRITICAL — hardcoded in source** | `src/data_pipeline.py` — **must be removed immediately** |
| `AWS_SECRET_KEY` | N/A — remove | 🔴 **CRITICAL — hardcoded in source** | `src/data_pipeline.py` — **must be removed immediately** |
| `aws_region` | No | Low | Terraform variable (default: `us-east-1`) |
| `environment` | No | Low | Terraform variable (default: `dev`) |

---

## 6. Dependencies

| Dependency | Type | Purpose | Notes |
|---|---|---|---|
| **Anthropic Claude API** (`claude-sonnet-4-6`) | External LLM API | AI inference for all five delivery tools | Requires `ANTHROPIC_API_KEY`; model name `claude-sonnet-4-6` is hardcoded in `shared.py` |
| **SendGrid API** | External email API | Notification emails on tool completion | Requires `SENDGRID_API_KEY`; sender domain must be verified |
| **GitHub REST API** (`api.github.com`) | External API | File fetching, PR comments, output repo commits | Requires `GH_TOKEN` with `repo` scope (read source + write output repo) |
| **`ai-delivery-outputs`** | Sibling GitHub repository | Stores all AI-generated artefacts (docs, test files, reports, UAT packs) | Must exist under the same owner; `GH_TOKEN` must have write access |
| **AWS S3** | Cloud service | Landing zone and processed output storage | Two buckets per environment |
| **AWS Lambda** | Cloud service | Serverless compute for data ingestion | Python 3.12 runtime; triggered by S3 events |
| **AWS IAM** | Cloud service | Lambda execution permissions | `lambda-ingest-role` and `lambda-s3-policy` |
| **`anthropic` Python SDK** | PyPI package | Claude API client | Installed at runtime in GitHub Actions runners (`pip install anthropic`) |
| **`requests` Python SDK** | PyPI package | GitHub and SendGrid HTTP calls | Installed at runtime in GitHub Actions runners |
| **`boto3`** | PyPI package | AWS SDK for S3 operations in Lambda | [TODO: Confirm `boto3` and `pandas` are included in `lambda.zip` or a Lambda layer] |
| **`pandas`** | PyPI package | CSV reading and DataFrame operations in Lambda | [TODO: Confirm included in Lambda deployment package] |

---

## 7. Deployment Instructions

### Prerequisites

```bash
# AWS CLI configured with sufficient permissions
aws configure

# Terraform installed (>= 1.5 recommended for AWS provider ~> 5.0)
terraform version

# lambda.zip must be built before applying — package src/ and dependencies
pip install boto3 pandas --target ./package
cp src/data_pipeline.py ./package/
cd package && zip -r ../lambda.zip . && cd ..
```

### Infrastructure deployment (AWS)

```bash
cd infra

# Initialise Terraform (add a backend block before this step in production)
terraform init

# Review the execution plan
terraform plan -var="environment=dev" -var="aws_region=us-east-1"

# Apply infrastructure
terraform apply -var="environment=dev" -var="aws_region=us-east-1"

# Confirm outputs
terraform output landing_bucket
terraform output processed_bucket
```

### GitHub Actions (AI Toolchain)

```bash
# Set required secrets in the source repository
gh secret set ANTHROPIC_API_KEY --body "<your-anthropic-key>"
gh secret set GH_TOKEN          --body "<your-github-pat>"
gh secret set SENDGRID_API_KEY  --body "<your-sendgrid-key>"

# Ensure the output repository exists
gh repo create <owner>/ai-delivery-outputs --private
```

### Trigger workflows manually

```bash
# Tool 1 — Code Review (full repo scan)
gh workflow run tool1_code_review.yml \
  -f review_mode=repo

# Tool 1 — Code Review (specific PR)
gh workflow run tool