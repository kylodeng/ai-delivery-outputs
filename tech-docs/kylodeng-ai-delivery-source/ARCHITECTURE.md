# Architecture Document — kylodeng/ai-delivery-source

---

## 1. Overview

This repository is a dual-purpose system that combines a **data ingestion pipeline** with an **AI-powered software delivery toolchain**. The data pipeline reads customer CSV files dropped into an AWS S3 landing bucket, validates and transforms them into Parquet format, and writes results to a processed S3 bucket via an AWS Lambda function. Layered on top of this infrastructure is a suite of five GitHub Actions workflows — each backed by a Python script and Anthropic's Claude LLM — that automate software delivery tasks: AI-driven code review, technical documentation generation, business documentation generation, automated test generation, and UAT facilitation. Outputs from the AI tools are written to a separate GitHub repository (`ai-delivery-outputs`) and notified via SendGrid email.

---

## 2. Resources Deployed

| Resource | Type | Cloud Provider | Purpose |
|---|---|---|---|
| `capco-data-landing-<env>` | S3 Bucket | AWS | Receives raw customer CSV files; triggers Lambda on `raw/*.csv` creation |
| `capco-data-processed-<env>` | S3 Bucket | AWS | Stores validated, transformed Parquet output files |
| `data-ingest-<env>` | Lambda Function | AWS | Validates, transforms, and moves CSV data to processed bucket |
| `lambda-ingest-role` | IAM Role | AWS | Execution role for the Lambda function |
| `lambda-s3-policy` | IAM Role Policy | AWS | Grants S3 permissions to the Lambda role (⚠️ overly broad — see Security) |
| S3 Bucket Notification | Event Notification | AWS | Triggers Lambda on `s3:ObjectCreated:*` events in `raw/*.csv` |
| `claude-sonnet-4-6` | LLM API (external) | Anthropic | AI model used by all five workflow tools |
| `ai-delivery-outputs` | GitHub Repository | GitHub | Stores all generated documentation, test files, and audit logs |
| Tool 1 — Code Review | GitHub Actions Workflow | GitHub | Runs on PR open/sync, weekly Monday cron, or manual dispatch |
| Tool 2 — Tech Docs | GitHub Actions Workflow | GitHub | Runs on push to `main`, weekly Sunday cron, or manual dispatch |
| Tool 3 — Business Docs | GitHub Actions Workflow | GitHub | Runs on version tag push or manual dispatch |
| Tool 4 — Auto Testing | GitHub Actions Workflow | GitHub | Runs on PR open/sync (src changes), weekly Wednesday cron, or manual dispatch |
| Tool 5 — UAT Facilitation | GitHub Actions Workflow | GitHub | Runs on `release/*` branch creation or manual dispatch |
| SendGrid | Email API (external) | SendGrid / Twilio | Sends notification emails on workflow completion |

---

## 3. Data Flow

### 3a. Data Ingestion Pipeline (AWS)

1. An upstream process or user uploads a `.csv` file to the `capco-data-landing-<env>` S3 bucket under the `raw/` prefix.
2. The S3 `ObjectCreated` event notification fires and invokes the `data-ingest-<env>` Lambda function, passing the bucket name and object key.
3. Lambda's `lambda_handler` calls `process_csv()`, which uses a `boto3` S3 client (⚠️ with hardcoded credentials — see Security) to call `GetObject` and download the CSV into memory via `pandas`.
4. Each row is passed through `validate_customer_record()`, which checks for required fields (`customer_id`, `email`, `age`, `country_code`), email format, and age range (1–150). Valid rows are accumulated; invalid rows are logged separately.
5. The validated `DataFrame` is serialised to Parquet and written back to the same `capco-data-landing-<env>` bucket (⚠️ not the processed bucket — see Risks) under the `processed/` prefix, with the `.csv` extension replaced by `.parquet`.
6. Lambda returns a JSON response with counts of processed and failed rows, the output key, and a timestamp.

### 3b. AI Delivery Toolchain (GitHub Actions)

1. A GitHub event (PR open, push to `main`, tag creation, branch creation, scheduled cron, or manual `workflow_dispatch`) triggers one of the five workflow YAML files.
2. The runner checks out the source repository and installs Python dependencies (`anthropic`, `requests`).
3. The relevant `tool<N>_*.py` script calls `shared.py::get_repo_files()` or `get_pr_diff()`, which queries the **GitHub REST API** to retrieve source files or PR diffs (up to configurable limits of 15–20 files, 30 000 diff characters).
4. The script constructs a system prompt and user prompt and calls `shared.py::call_claude()`, which sends the payload to the **Anthropic Messages API** (`claude-sonnet-4-6`) and returns the text response.
5. The response is parsed (JSON extraction for structured tools; plain Markdown for docs) and formatted into one or more output files.
6. `shared.py::write_output_file()` calls the **GitHub Contents API** (`PUT /repos/{owner}/{repo}/contents/{path}`) to create or update files in the `ai-delivery-outputs` repository.
7. For Tool 1, a formatted summary comment is also posted directly to the source PR via `post_pr_comment()`.
8. On completion, `send_email()` (via **SendGrid API**) dispatches an HTML notification to `kylo.deng@capco.com`.
9. An audit entry is written to the `ai-delivery-outputs` repo for traceability.
10. For Tool 1, the raw JSON review output is also uploaded as a GitHub Actions artifact (`code-review-<run_id>`).

---

## 4. Security Posture

### ✅ What Is Secured

- GitHub Actions secrets (`ANTHROPIC_API_KEY`, `GH_TOKEN`, `SENDGRID_API_KEY`) are stored as encrypted GitHub repository secrets and injected as environment variables at runtime — not hardcoded in workflow YAML values.
- Tool 5 UAT workflow includes a branch filter (`startsWith(github.ref, 'refs/heads/release/')`) to prevent accidental triggers on arbitrary branch creation.
- PR diff content is truncated at 30 000 characters before being sent to the external Claude API, limiting accidental data exfiltration volume.
- The Lambda IAM role uses a correctly scoped trust policy (only `lambda.amazonaws.com` may assume it).

### ❌ Gaps and Issues (Critical)

- **⛔ CRITICAL — Hardcoded AWS credentials in source code:** `src/data_pipeline.py` contains a literal `AWS_ACCESS_KEY` and `AWS_SECRET_KEY` (lines 12–13). These are hardcoded example-format keys but the pattern is production-dangerous. They must be removed immediately; Lambda should rely solely on its IAM execution role.
- **⛔ CRITICAL — Hardcoded database password in IaC:** `infra/main.tf` sets `DB_PASSWORD = "SuperSecret123!"` as a plaintext Lambda environment variable. This value is visible in the AWS Console, Terraform state, and any CI logs. Must be replaced with an AWS Secrets Manager or SSM Parameter Store reference.
- **⛔ HIGH — S3 landing bucket has no encryption:** `aws_s3_bucket.landing` in `main.tf` has no `aws_s3_bucket_server_side_encryption_configuration` block and no `aws_s3_bucket_public_access_block` resource. Customer PII (emails, ages) lands unencrypted and could be publicly accessible.
- **⛔ HIGH — S3 processed bucket has no encryption or public access block:** Same issue as landing bucket; Parquet files containing validated customer records are unencrypted at rest.
- **⛔ HIGH — Overly broad IAM policy:** `lambda-s3-policy` grants `s3:*` on `Resource: "*"`, giving the Lambda function full S3 control over every bucket in the account. It should be scoped to `s3:GetObject` on the landing bucket ARN and `s3:PutObject` on the processed bucket ARN only.
- **⛔ HIGH — Parquet output written back to landing bucket:** `process_csv()` builds the output key using `key.replace("raw/", "processed/")` and writes to the same `bucket` variable (the landing bucket), not `capco-data-processed-<env>`. This is almost certainly a bug and means the processed bucket is never written to.
- **⛔ MEDIUM — No S3 bucket versioning or lifecycle policies:** Neither bucket has versioning enabled; accidental overwrites of raw or processed data are unrecoverable.
- **⛔ MEDIUM — No S3 public access block on either bucket:** Neither bucket explicitly blocks public access. Without account-level SCPs, these buckets could be made public.
- **⛔ MEDIUM — Source code (including hardcoded secrets) is sent to Anthropic's API:** `get_repo_files()` fetches `.py` files — including `data_pipeline.py` which contains hardcoded keys — and sends them verbatim to a third-party LLM. This constitutes a data exfiltration risk.
- **⛔ MEDIUM — No Lambda VPC configuration:** The Lambda function has no VPC attachment, meaning all S3 API calls traverse the public internet rather than a VPC endpoint.
- **⛔ MEDIUM — No pagination in `get_all_pending_files()`:** `list_objects_v2` returns at most 1 000 keys per call. Buckets with more than 1 000 raw files will silently process an incomplete set.
- **⛔ LOW — No resource tags:** The `aws_s3_bucket.landing` resource has a `# TODO: add tags` comment; neither bucket nor the Lambda function has cost allocation, environment, or owner tags.
- **⛔ LOW — `GH_TOKEN` scope is unknown:** [TODO: What permissions does the `GH_TOKEN` secret have? If it is a Personal Access Token with `repo` scope, it grants write access to all repositories the owner controls. A fine-grained PAT scoped to `ai-delivery-outputs` (contents: write) should be used instead.]

---

## 5. Environment Variables and Secrets

| Name | Required | Sensitivity | Where Set |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | 🔴 High — billable API key | GitHub Actions Secret |
| `GH_TOKEN` | Yes | 🔴 High — repository write access | GitHub Actions Secret |
| `SENDGRID_API_KEY` | Yes | 🔴 High — email sending capability | GitHub Actions Secret |
| `OUTPUT_REPO` | No | 🟢 Low | Workflow `env` block (hardcoded: `ai-delivery-outputs`) |
| `OUTPUT_REPO_OWNER` | No | 🟢 Low | Workflow `env` block (derived from `github.repository_owner`) |
| `NOTIFY_EMAIL` | No | 🟡 Medium — PII (email address) | Workflow `env` block (hardcoded: `kylo.deng@capco.com`) |
| `SENDER_EMAIL` | No | 🟢 Low | Workflow `env` block (hardcoded: `noreply@ai-delivery.capco.com`) |
| `SOURCE_REPO_OWNER` | No | 🟢 Low | Workflow `env` block (derived from `github.repository_owner`) |
| `SOURCE_REPO_NAME` | No | 🟢 Low | Workflow `env` block (derived from `github.event.repository.name`) |
| `GITHUB_RUN_URL` | No | 🟢 Low | Workflow `env` block (derived from GitHub context) |
| `REVIEW_MODE` | No | 🟢 Low | Set at runtime by workflow step (`pr` or `repo`) |
| `PR_NUMBER` | No | 🟢 Low | Set at runtime by workflow step |
| `TEST_MODE` | No | 🟢 Low | Workflow `env` block (default: `generate`) |
| `RELEASE_VERSION` | No | 🟢 Low | Set at runtime by workflow step |
| `PROJECT_NAME` | No | 🟢 Low | Set at runtime by workflow step |
| `UAT_MODE` | No | 🟢 Low | Set at runtime by workflow step |
| `UAT_RESULTS_PATH` | No | 🟢 Low | Set at runtime from `workflow_dispatch` input |
| `USER_STORIES` | No | 🟡 Medium — may contain business requirements | Set at runtime from `workflow_dispatch` input |
| `LANDING_BUCKET` | Yes (Lambda) | 🟢 Low | Lambda environment variable via Terraform |
| `DB_PASSWORD` | Yes (Lambda) | 🔴 CRITICAL — plaintext secret | ⚠️ Hardcoded in `main.tf` Lambda env vars — must move to Secrets Manager |
| `AWS_ACCESS_KEY` *(in code)* | N/A | 🔴 CRITICAL — hardcoded credential | ⚠️ Hardcoded in `src/data_pipeline.py` — must be removed |
| `AWS_SECRET_KEY` *(in code)* | N/A | 🔴 CRITICAL — hardcoded credential | ⚠️ Hardcoded in `src/data_pipeline.py` — must be removed |

---

## 6. Dependencies

| Dependency | Type | Purpose | Notes |
|---|---|---|---|
| Anthropic Claude API (`claude-sonnet-4-6`) | External SaaS API | LLM inference for all five AI tools | Billable per token; no fallback/retry logic observed |
| GitHub REST API (`api.github.com`) | External API | Fetch repo files, PR diffs, write output files, post PR comments | Requires `GH_TOKEN` with appropriate scopes |
| SendGrid API | External SaaS API | Send HTML notification emails on workflow completion | Requires `SENDGRID_API_KEY`; sender domain `ai-delivery.capco.com` must be verified |
| `ai-delivery-outputs` repository | Sibling GitHub repo | Stores all AI-generated documentation, test files, and audit logs | Must exist and `GH_TOKEN` must have write access |
| AWS S3 | AWS Service | Landing and processed data storage | Two buckets per environment |
| AWS Lambda | AWS Service | Serverless compute for data ingestion | Python 3.12 runtime |
| AWS IAM | AWS Service | Execution role and permissions for Lambda | |
| `anthropic` Python package | PyPI library | Anthropic API client | Installed at runtime via `pip install anthropic` |
| `requests` Python package | PyPI library | HTTP client for GitHub and SendGrid APIs | Installed at runtime via `pip install requests` |
| `boto3` Python package | PyPI library | AWS SDK for S3 access in Lambda | [TODO: Is `boto3` bundled in the Lambda deployment package `lambda.zip`, or is it assumed present in the runtime?] |
| `pandas` Python package | PyPI library | CSV parsing and DataFrame manipulation in Lambda | [TODO: Is `pandas` bundled in `lambda.zip`? It is not a Lambda built-in and must be included as a layer or in the zip.] |

---

## 7. Deployment Instructions

### Prerequisites

- AWS CLI configured with credentials that have permissions to create S3 buckets, Lambda functions, and IAM roles
- Terraform >= 1.x installed
- A `lambda.zip` file built from `src/data_pipeline.py` with all Python dependencies included
- GitHub repository secrets set: `ANTHROPIC_API_KEY`, `GH_TOKEN`, `SENDGRID_API_KEY`
- A GitHub repository named `ai-delivery-outputs` accessible by the `GH_TOKEN`

### Infrastructure Deployment (AWS)

```bash
# 1. Build the Lambda deployment package
cd src
pip install boto3 pandas -t ./package
cp data_pipeline.py ./package/
cd package && zip -r ../../infra/lambda.zip . && cd ../..

# 2. Initialise Terraform
cd infra
terraform init

# 3. Review the plan
terraform plan -var="environment=dev" -var="aws_region=us-east-1"

# 4. Apply (deploy to dev)
terraform apply -var="environment=dev" -var="aws_region=us-east-1"

# 5. For production
terraform apply -var="environment=prod" -var="aws_region=us-east-1"
```

### GitHub Actions Workflows

The five workflows are deployed automatically as part of the repository. No additional deployment