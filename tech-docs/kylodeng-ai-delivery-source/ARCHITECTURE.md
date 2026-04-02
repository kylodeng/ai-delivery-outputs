# Architecture Document — kylodeng/ai-delivery-source

---

## 1. Overview

This system is a dual-purpose platform combining a **data ingestion pipeline** deployed on AWS with an **AI-powered software delivery toolkit** implemented as GitHub Actions workflows. The data pipeline ingests customer CSV files dropped into an S3 landing bucket, validates and transforms them into Parquet format, and writes results to a processed S3 bucket via an AWS Lambda function. The AI delivery toolkit wraps Anthropic's Claude API to automate five software delivery activities across the same source repository: automated code review (Tool 1), technical documentation generation (Tool 2), business documentation generation (Tool 3), automated test generation and coverage gap analysis (Tool 4), and UAT test pack generation and results analysis (Tool 5). Outputs from all five tools are written to a companion GitHub repository (`ai-delivery-outputs`) and notified to stakeholders via SendGrid email.

---

## 2. Resources Deployed

| Resource | Type | Cloud Provider | Purpose |
|---|---|---|---|
| `capco-data-landing-{env}` | S3 Bucket | AWS | Landing zone for raw customer CSV files |
| `capco-data-processed-{env}` | S3 Bucket | AWS | Stores validated, transformed Parquet files |
| `data-ingest-{env}` | Lambda Function (Python 3.12) | AWS | Processes CSVs on S3 `ObjectCreated` trigger |
| `lambda-ingest-role` | IAM Role | AWS | Execution role for the Lambda function |
| `lambda-s3-policy` | IAM Role Policy | AWS | Grants S3 permissions to Lambda |
| S3 Bucket Notification | Event Notification | AWS | Triggers Lambda on `raw/*.csv` object creation |
| GitHub Actions Runner | Managed CI/CD (ubuntu-latest) | GitHub | Executes all five AI delivery tools |
| `ai-delivery-outputs` | GitHub Repository | GitHub | Stores all AI-generated documents and reports |
| Anthropic Claude API (`claude-sonnet-4-6`) | External API | Anthropic | LLM inference for all five tools |
| SendGrid | External Email API | Twilio/SendGrid | Delivery of notification emails to stakeholders |

---

## 3. Data Flow

### 3a — Data Ingestion Pipeline

1. An external process (or user) uploads a CSV file to `s3://capco-data-landing-{env}/raw/*.csv`.
2. The S3 bucket notification fires an `s3:ObjectCreated:*` event filtered to the `raw/` prefix and `.csv` suffix.
3. AWS Lambda (`data-ingest-{env}`) is invoked with the bucket and key in the event payload.
4. `lambda_handler` calls `process_csv()`, which uses a hardcoded AWS key pair to instantiate a Boto3 S3 client and downloads the CSV object into memory via `get_object`.
5. The CSV is parsed into a Pandas DataFrame; each row is validated by `validate_customer_record` (checking required fields, email format, and age range).
6. Valid rows are collected into a result DataFrame; invalid rows are captured with their error messages.
7. The result DataFrame is serialised as Parquet and written back to the **same landing bucket** under the `processed/` prefix (key pattern: `raw/` → `processed/`, `.csv` → `.parquet`).
8. The Lambda returns a JSON summary (`processed`, `failed`, `output_key`, `timestamp`).

### 3b — AI Delivery Toolkit

1. A GitHub event (PR open, push to main, version tag, branch creation, or schedule) triggers one of the five workflow YAML files.
2. The runner checks out the source repository and installs `anthropic` and `requests`.
3. The relevant Python tool script calls `shared.get_repo_files()` or `shared.get_pr_diff()` to fetch source code and IaC from GitHub via the REST API using `GH_TOKEN`.
4. The assembled code context is sent to Anthropic's Claude API (`claude-sonnet-4-6`) via `shared.call_claude()`.
5. Claude returns structured output (Markdown documents or JSON reports).
6. `shared.write_output_file()` commits the output file to `ai-delivery-outputs` via the GitHub Contents API.
7. For code review (Tool 1), a formatted summary is also posted as a PR comment via `shared.post_pr_comment()`.
8. `shared.send_email()` dispatches an HTML notification to `kylo.deng@capco.com` via the SendGrid API.
9. An audit entry is written (via `write_audit_entry()`) and the raw JSON artifact is uploaded to GitHub Actions artifacts.

---

## 4. Security Posture

### ✅ What is secured

- API keys (`ANTHROPIC_API_KEY`, `GH_TOKEN`, `SENDGRID_API_KEY`) are stored as GitHub Actions Secrets and injected as environment variables — not hardcoded in workflow YAML.
- Lambda IAM role uses a scoped assume-role trust policy limited to `lambda.amazonaws.com`.
- S3 event trigger is filtered to `raw/` prefix and `.csv` suffix, limiting the Lambda invocation surface.
- GitHub Actions jobs use pinned action versions (`actions/checkout@v4`, etc.).

### ❌ Gaps and explicit failures

| Gap | Location | Severity |
|---|---|---|
| **AWS access keys hardcoded in source code** | `src/data_pipeline.py` lines 11–12 | 🔴 CRITICAL |
| **DB password hardcoded in Terraform** | `infra/main.tf` Lambda environment variable `DB_PASSWORD = "SuperSecret123!"` | 🔴 CRITICAL |
| **S3 landing bucket has no server-side encryption** | `infra/main.tf` `aws_s3_bucket.landing` — no `aws_s3_bucket_server_side_encryption_configuration` resource | 🔴 CRITICAL |
| **S3 landing bucket has no public access block** | `infra/main.tf` — no `aws_s3_bucket_public_access_block` resource for landing bucket | 🔴 CRITICAL |
| **S3 processed bucket has no server-side encryption** | `infra/main.tf` `aws_s3_bucket.processed` | 🔴 CRITICAL |
| **IAM policy grants `s3:*` on `*`** — full S3 access across all buckets in the account | `infra/main.tf` `aws_iam_role_policy.lambda_policy` | 🔴 CRITICAL |
| **Processed Parquet files written to landing bucket**, not to `capco-data-processed-{env}` | `src/data_pipeline.py` `process_csv()` — output path remains in same bucket | 🟠 HIGH |
| **S3 listing has no pagination** — `list_objects_v2` returns max 1,000 objects | `src/data_pipeline.py` `get_all_pending_files()` | 🟠 HIGH |
| **No encryption in transit enforcement** — no S3 bucket policy requiring `aws:SecureTransport` | `infra/main.tf` | 🟠 HIGH |
| **No VPC configuration for Lambda** — function runs in default AWS network with public internet access | `infra/main.tf` | 🟠 HIGH |
| **Bare `except Exception` in Lambda handler** — all errors silently swallowed | `src/data_pipeline.py` `lambda_handler()` | 🟡 MEDIUM |
| **No S3 bucket versioning** — no recovery from accidental overwrites or deletes | `infra/main.tf` | 🟡 MEDIUM |
| **No resource tagging** on any AWS resource | `infra/main.tf` — TODO comment present | 🟡 MEDIUM |
| **No CloudWatch log group or retention policy** for Lambda | `infra/main.tf` | 🟡 MEDIUM |
| **`GH_TOKEN` scope unknown** | `shared.py` — used for repo read, PR comments, and writing to output repo; minimum required scopes not documented | [TODO: verify token has only `repo` scope on required repos] |
| **Email sender domain `noreply@ai-delivery.capco.com` not verified in SendGrid** | `shared.py` `SENDER_EMAIL` | [TODO: confirm SPF/DKIM records] |

---

## 5. Environment Variables and Secrets

| Name | Required | Sensitivity | Where set |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | 🔴 Secret | GitHub Actions Secret |
| `GH_TOKEN` | Yes | 🔴 Secret | GitHub Actions Secret |
| `SENDGRID_API_KEY` | Yes | 🔴 Secret | GitHub Actions Secret |
| `OUTPUT_REPO` | No | Low | Workflow `env` block (hardcoded: `ai-delivery-outputs`) |
| `OUTPUT_REPO_OWNER` | No | Low | Workflow `env` block (derived from `github.repository_owner`) |
| `NOTIFY_EMAIL` | No | Low | Workflow `env` block (hardcoded: `kylo.deng@capco.com`) |
| `SENDER_EMAIL` | No | Low | Workflow `env` block (hardcoded: `noreply@ai-delivery.capco.com`) |
| `SOURCE_REPO_OWNER` | No | Low | Workflow `env` block (derived from `github.repository_owner`) |
| `SOURCE_REPO_NAME` | No | Low | Workflow `env` block (derived from `github.event.repository.name`) |
| `GITHUB_RUN_URL` | No | Low | Workflow `env` block (derived from GitHub context) |
| `TEST_MODE` | No (Tool 4 only) | Low | Workflow `env` block / workflow dispatch input |
| `UAT_MODE` | No (Tool 5 only) | Low | Set at runtime in workflow step |
| `RELEASE_VERSION` | No (Tools 3, 5) | Low | Set at runtime in workflow step |
| `PROJECT_NAME` | No (Tool 3 only) | Low | Set at runtime in workflow step |
| `USER_STORIES` | No (Tool 5 only) | Low | Workflow dispatch input |
| `UAT_RESULTS_PATH` | No (Tool 5 only) | Low | Workflow dispatch input |
| `LANDING_BUCKET` | Yes (Lambda) | Low | Terraform Lambda environment variable |
| `DB_PASSWORD` | Yes (Lambda) | 🔴 Secret — **CRITICAL: hardcoded in Terraform** | Terraform Lambda environment variable — must be moved to AWS Secrets Manager or SSM Parameter Store |
| `AWS_ACCESS_KEY` | N/A | 🔴 Secret — **CRITICAL: hardcoded in source** | `src/data_pipeline.py` — must be removed; use Lambda execution role instead |
| `AWS_SECRET_KEY` | N/A | 🔴 Secret — **CRITICAL: hardcoded in source** | `src/data_pipeline.py` — must be removed; use Lambda execution role instead |

---

## 6. Dependencies

| Dependency | Type | Version / Notes |
|---|---|---|
| Anthropic Claude API | External API | Model: `claude-sonnet-4-6`; Python SDK `anthropic` |
| GitHub REST API | External API | `api.github.com`; version `2022-11-28` |
| SendGrid API | External Email API | `api.sendgrid.com`; Python `requests` library (no official SDK used) |
| `ai-delivery-outputs` | Companion GitHub Repository | Must exist and be writable by `GH_TOKEN`; owned by same GitHub org/user |
| AWS S3 | Cloud Service | `us-east-1`; two buckets managed by Terraform |
| AWS Lambda | Cloud Service | Python 3.12 runtime; `lambda.zip` must be present at `terraform apply` time |
| `lambda.zip` | Build Artifact | [TODO: how is this artifact built and where is the CI/CD pipeline that produces it?] |
| `pandas` | Python Library | Used in `data_pipeline.py`; not present in Lambda layer or `requirements.txt` — [TODO: confirm packaging] |
| `pyarrow` / `fastparquet` | Python Library | Required by pandas `to_parquet()`; not explicitly listed — [TODO: confirm packaging] |
| `boto3` | Python Library | Available in Lambda runtime by default (AWS managed) |
| GitHub Actions | CI/CD Platform | `ubuntu-latest` runners; `actions/checkout@v4`, `actions/setup-python@v5`, `actions/upload-artifact@v4` |

---

## 7. Deployment Instructions

### Prerequisites

- AWS CLI configured with credentials that have permissions to create S3, Lambda, and IAM resources in `us-east-1`
- Terraform ≥ 1.0 installed
- `lambda.zip` build artifact present in `infra/` directory [TODO: document how to build this]
- GitHub repository secrets configured: `ANTHROPIC_API_KEY`, `GH_TOKEN`, `SENDGRID_API_KEY`
- Companion repository `ai-delivery-outputs` created and accessible by `GH_TOKEN`

### AWS Infrastructure (Terraform)

```bash
# Navigate to the Terraform directory
cd infra/

# Initialise Terraform
terraform init

# Preview the planned changes
terraform plan -var="environment=dev" -var="aws_region=us-east-1"

# Apply infrastructure
terraform apply -var="environment=dev" -var="aws_region=us-east-1"

# For production
terraform apply -var="environment=prod" -var="aws_region=us-east-1"
```

### GitHub Actions Workflows

Workflows are triggered automatically based on repository events. To trigger manually:

```bash
# Tool 1 — Code Review (manual, repo-wide scan)
gh workflow run tool1_code_review.yml \
  -f review_mode=repo

# Tool 1 — Code Review (manual, specific PR)
gh workflow run tool1_code_review.yml \
  -f review_mode=pr \
  -f pr_number=42

# Tool 2 — Tech Documentation
gh workflow run tool2_tech_docs.yml

# Tool 3 — Business Documentation
gh workflow run tool3_business_docs.yml \
  -f project_name="Data Ingestion Pipeline" \
  -f release_version="1.0.0"

# Tool 4 — Auto Testing (generate mode)
gh workflow run tool4_auto_testing.yml \
  -f test_mode=generate

# Tool 4 — Auto Testing (gap analysis mode)
gh workflow run tool4_auto_testing.yml \
  -f test_mode=gap-analysis

# Tool 5 — UAT (generate test pack)
gh workflow run tool5_uat.yml \
  -f uat_mode=generate \
  -f release_version="1.0.0" \
  -f user_stories="As a customer I want..."

# Tool 5 — UAT (analyse results)
gh workflow run tool5_uat.yml \
  -f uat_mode=analyse \
  -f release_version="1.0.0" \
  -f uat_results_path="uat/owner-repo/v1.0.0/UAT_RESULTS_SHEET.csv"
```

### Tear Down

```bash
cd infra/
terraform destroy -var="environment=dev"
```

---

## 8. Risks and TODOs

### 🔴 Critical — Address Immediately

| Risk | Evidence | Recommended Action |
|---|---|---|
| AWS access keys hardcoded in source code | `src/data_pipeline.py` lines 11–12, comment: `# TODO: move this to secrets manager` | Remove keys; Lambda should use its execution role (`lambda-ingest-role`) via instance metadata — no explicit credentials needed |
| DB password hardcoded in Terraform state and Lambda environment | `infra/main.tf` comment: `# Hardcoded secret - should use SSM or Secrets Manager` | Migrate to `aws_ssm_parameter` (SecureString) or `aws_secretsmanager_secret` and reference via Lambda environment or SDK call |
| Overly permissive IAM policy (`s3:*` on `*`) | `infra/main.tf` comment: `# Overly permissive policy - full S3 access` | Restrict to `s3:GetObject`, `s3:PutObject`, `s3:ListBucket` on the two specific bucket ARNs |
| No S3 encryption on either bucket | `infra/main.