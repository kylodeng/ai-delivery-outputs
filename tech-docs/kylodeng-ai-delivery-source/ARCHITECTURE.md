# Architecture Document — kylodeng/ai-delivery-source

---

## 1. Overview

This system is a dual-purpose platform combining an **AI-assisted software delivery toolkit** with a **cloud data ingestion pipeline**. The delivery toolkit consists of five GitHub Actions–driven workflows that leverage Anthropic's Claude API to automate code review, technical documentation generation, business documentation generation, test generation, and UAT facilitation across software repositories. Results are written to a dedicated output GitHub repository (`ai-delivery-outputs`) and notifications are dispatched via SendGrid. In parallel, an AWS-hosted data pipeline (defined in Terraform) ingests customer CSV files dropped into an S3 landing bucket, validates and transforms them via an AWS Lambda function, and writes the output as Parquet files to a processed S3 bucket.

---

## 2. Resources Deployed

| Resource | Type | Cloud Provider | Purpose |
|---|---|---|---|
| `capco-data-landing-{env}` | S3 Bucket | AWS | Receives raw customer CSV files |
| `capco-data-processed-{env}` | S3 Bucket | AWS | Stores validated, transformed Parquet files |
| `data-ingest-{env}` | Lambda Function (Python 3.12) | AWS | Validates and transforms CSV → Parquet on S3 event trigger |
| `lambda-ingest-role` | IAM Role | AWS | Execution role assumed by Lambda |
| `lambda-s3-policy` | IAM Role Policy | AWS | Grants Lambda permissions on S3 |
| S3 Bucket Notification | S3 Event Trigger | AWS | Fires Lambda on `s3:ObjectCreated:*` under `raw/*.csv` |
| `tool1_code_review.yml` | GitHub Actions Workflow | GitHub | AI code review on PRs and scheduled runs |
| `tool2_tech_docs.yml` | GitHub Actions Workflow | GitHub | AI technical documentation generation |
| `tool3_business_docs.yml` | GitHub Actions Workflow | GitHub | AI business documentation generation |
| `tool4_auto_testing.yml` | GitHub Actions Workflow | GitHub | AI test generation and coverage gap analysis |
| `tool5_uat.yml` | GitHub Actions Workflow | GitHub | AI UAT test pack generation and result analysis |
| `ai-delivery-outputs` | GitHub Repository | GitHub | Stores all AI-generated output artefacts |
| Claude (`claude-sonnet-4-6`) | LLM API | Anthropic (external) | Performs all AI inference tasks |
| SendGrid | Email API | Twilio/SendGrid (external) | Notification delivery |

---

## 3. Data Flow

### 3a — Data Ingestion Pipeline (AWS)

1. An external process or upstream system drops a `.csv` file into the `capco-data-landing-{env}` S3 bucket under the `raw/` prefix.
2. S3 fires an `ObjectCreated` event notification to the `data-ingest-{env}` Lambda function.
3. Lambda retrieves the file using a hardcoded AWS access key pair embedded in `data_pipeline.py` (⚠️ see Security Posture).
4. The Lambda reads the CSV into a Pandas DataFrame and iterates over each row, calling `validate_customer_record()` to check for required fields (`customer_id`, `email`, `age`, `country_code`), valid email format, and age range (1–150).
5. Valid rows are collected; invalid rows are logged with their error reason.
6. The validated DataFrame is serialised to Parquet and written back to the same landing bucket under the `processed/` prefix, with the key path mirroring the original (e.g. `raw/foo.csv` → `processed/foo.parquet`). Note: output is written to the **landing** bucket, not the processed bucket — [TODO: confirm whether `processed` bucket is intentionally unused in this path].
7. Lambda returns a JSON response with counts of processed and failed rows, the output key, and a UTC timestamp.

### 3b — AI Delivery Toolkit (GitHub Actions)

1. A trigger event fires the relevant workflow (PR open/sync, push to `main`, version tag push, release branch creation, scheduled cron, or manual `workflow_dispatch`).
2. The GitHub Actions runner checks out the source repository at the relevant ref.
3. Python dependencies (`anthropic`, `requests`) are installed on the ephemeral runner.
4. The relevant tool script (`tool1–5`) calls `shared.py::get_repo_files()` or `get_pr_diff()`, fetching source and IaC file contents from GitHub's REST API using the `GH_TOKEN`.
5. The collected file content is assembled into a prompt and sent to the Anthropic API (`claude-sonnet-4-6`) via `shared.py::call_claude()`.
6. Claude's response (JSON or Markdown) is parsed and formatted.
7. Depending on the tool: a PR comment is posted back to the source repository (Tool 1); a Markdown file is committed to the `ai-delivery-outputs` repository via `shared.py::write_output_file()` (Tools 1–5).
8. A SendGrid email notification is dispatched to `kylo.deng@capco.com` with a summary and a link to the output artefact.
9. An audit log entry is written to the output repository.

---

## 4. Security Posture

### ✅ What is secured

- **GitHub Actions secrets** — `ANTHROPIC_API_KEY`, `GH_TOKEN`, and `SENDGRID_API_KEY` are stored as encrypted GitHub Actions secrets and injected as environment variables; they are not hardcoded in workflow files.
- **Lambda IAM trust policy** — correctly scoped to `lambda.amazonaws.com` service principal only.
- **S3 event trigger** — filtered to `raw/*.csv` prefix/suffix, limiting Lambda invocation surface.
- **Workflow conditions** — Tool 5 (UAT) correctly gates execution to `release/` branches or manual dispatch, preventing unintended runs.
- **PR diff truncation** — `get_pr_diff()` caps content at 30,000 characters, reducing prompt-injection blast radius.

### ❌ Gaps and issues — explicit call-outs

| Issue | Severity | Detail |
|---|---|---|
| **Hardcoded AWS credentials in source code** | CRITICAL | `AWS_ACCESS_KEY = "AKIAIOSFODNN7EXAMPLE"` and `AWS_SECRET_KEY` are hardcoded in `src/data_pipeline.py`. These must be rotated immediately and moved to IAM instance roles or AWS Secrets Manager. The `TODO` comment acknowledges this but has not been acted on. |
| **Hardcoded DB password in Terraform** | CRITICAL | `DB_PASSWORD = "SuperSecret123!"` is set as a plaintext Lambda environment variable in `infra/main.tf`. This is visible in Terraform state, AWS Console, and any process that calls `GetFunctionConfiguration`. Must be moved to AWS Secrets Manager or SSM Parameter Store (SecureString). |
| **S3 landing bucket has no encryption** | HIGH | `aws_s3_bucket.landing` has no `aws_s3_bucket_server_side_encryption_configuration` resource attached. Customer PII data (email, age, country) lands unencrypted at rest. The Terraform comment explicitly notes this. |
| **S3 landing bucket has no public access block** | HIGH | No `aws_s3_bucket_public_access_block` resource is attached to the landing bucket. Without this, a misconfigured bucket ACL or policy could expose customer data publicly. |
| **Overly broad IAM policy — `s3:*` on `*`** | HIGH | `aws_iam_role_policy.lambda_policy` grants `s3:*` on `Resource: "*"`. Lambda should only need `s3:GetObject` on the landing bucket ARN and `s3:PutObject` on the processed bucket ARN. This grants Lambda the ability to delete, replicate, or list all S3 buckets in the account. |
| **Lambda reads credentials from environment instead of IAM role** | HIGH | `data_pipeline.py` explicitly passes `aws_access_key_id` and `aws_secret_access_key` to `boto3.client()`, bypassing the IAM execution role entirely. The Lambda IAM role exists but is not used by the application code. |
| **S3 processed bucket has no encryption** | MEDIUM | `aws_s3_bucket.processed` also lacks an encryption configuration block. |
| **No S3 bucket versioning** | MEDIUM | Neither S3 bucket has versioning enabled, making accidental overwrites or deletions unrecoverable. |
| **No S3 bucket logging** | MEDIUM | No access logging is configured on either bucket. There is no audit trail for data access. |
| **No Lambda dead-letter queue (DLQ)** | MEDIUM | If Lambda fails to process a file, the event is silently dropped after retries. No SQS DLQ or SNS alert is configured. |
| **Bare `except Exception` in Lambda handler** | MEDIUM | `lambda_handler` catches all exceptions and returns a 500, swallowing stack traces and preventing proper alerting integration. |
| **No S3 bucket tagging** | LOW | `aws_s3_bucket.landing` has a `# TODO: add tags` comment. Missing tags impede cost allocation, compliance, and resource tracking. |
| **GH_TOKEN scope unknown** | MEDIUM | The `GH_TOKEN` secret is used to read source repos and write to `ai-delivery-outputs`. The required scopes (`repo`, `contents:write`) are not documented. An overly broad PAT (e.g. `repo:admin`) would be a significant risk. [TODO: verify GH_TOKEN is a fine-grained PAT scoped to minimum required repositories and permissions.] |
| **Source code sent to third-party LLM** | MEDIUM | All repository source code (up to 20 files × 4,000 chars each) is transmitted to Anthropic's API. Data residency, retention, and confidentiality policies with Anthropic have not been documented here. [TODO: confirm Anthropic data processing agreement is in place for Capco.] |
| **No input sanitisation on workflow_dispatch inputs** | LOW | User-supplied inputs (`pr_number`, `project_name`, `release_version`, `user_stories`) are passed directly into shell `echo` commands and Python scripts without sanitisation. Shell injection risk is low in GitHub Actions but should be reviewed. |

---

## 5. Environment Variables and Secrets

| Name | Required | Sensitivity | Where set |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | 🔴 High — billable API key | GitHub Actions secret |
| `GH_TOKEN` | Yes | 🔴 High — GitHub PAT with repo write access | GitHub Actions secret |
| `SENDGRID_API_KEY` | Yes | 🔴 High — billable email API key | GitHub Actions secret |
| `OUTPUT_REPO` | No | 🟢 Low | Hardcoded in workflow env (`ai-delivery-outputs`) |
| `OUTPUT_REPO_OWNER` | No | 🟢 Low | Derived from `github.repository_owner` |
| `NOTIFY_EMAIL` | No | 🟡 Medium — PII (personal email) | Hardcoded in workflow env (`kylo.deng@capco.com`) |
| `SENDER_EMAIL` | No | 🟢 Low | Hardcoded in workflow env |
| `SOURCE_REPO_OWNER` | No | 🟢 Low | Derived from `github.repository_owner` |
| `SOURCE_REPO_NAME` | No | 🟢 Low | Derived from `github.event.repository.name` |
| `GITHUB_RUN_URL` | No | 🟢 Low | Derived from GitHub context |
| `REVIEW_MODE` | No | 🟢 Low | Set at runtime by workflow step |
| `PR_NUMBER` | No | 🟢 Low | Set at runtime by workflow step |
| `TEST_MODE` | No | 🟢 Low | Set at runtime (`generate` default) |
| `UAT_MODE` | No | 🟢 Low | Set at runtime |
| `RELEASE_VERSION` | No | 🟢 Low | Set at runtime from tag or input |
| `PROJECT_NAME` | No | 🟢 Low | Set at runtime from input |
| `USER_STORIES` | No | 🟡 Medium — may contain business requirements | Set at runtime from workflow_dispatch input |
| `UAT_RESULTS_PATH` | No | 🟢 Low | Set at runtime from workflow_dispatch input |
| `LANDING_BUCKET` | Yes (Lambda) | 🟢 Low | Lambda environment variable (set by Terraform) |
| `DB_PASSWORD` | Yes (Lambda) | 🔴 CRITICAL — plaintext secret | Lambda environment variable (hardcoded in Terraform ⚠️) |
| `AWS_ACCESS_KEY` | — | 🔴 CRITICAL — AWS credential | Hardcoded in `data_pipeline.py` ⚠️ |
| `AWS_SECRET_KEY` | — | 🔴 CRITICAL — AWS credential | Hardcoded in `data_pipeline.py` ⚠️ |

---

## 6. Dependencies

| Dependency | Type | Purpose | Notes |
|---|---|---|---|
| **Anthropic API** (`claude-sonnet-4-6`) | External SaaS API | All AI inference (review, docs, tests, UAT) | Requires `ANTHROPIC_API_KEY`; billable per token; [TODO: confirm Capco DPA with Anthropic] |
| **SendGrid API** | External SaaS API | Email notification delivery | Requires `SENDGRID_API_KEY`; sender domain `ai-delivery.capco.com` must be verified in SendGrid |
| **GitHub REST API** (`api.github.com`) | External API | Read source repo files/diffs; write to output repo | Requires `GH_TOKEN` PAT |
| **`kylodeng/ai-delivery-outputs`** | GitHub Repository | Stores all generated Markdown artefacts and audit logs | Must exist and be writable by `GH_TOKEN` before workflows run |
| **AWS S3** | AWS Service | Landing and processed data storage | `us-east-1`; no cross-region replication configured |
| **AWS Lambda** | AWS Service | Serverless compute for data pipeline | Python 3.12 runtime; requires `lambda.zip` deployment package |
| **`anthropic` (PyPI)** | Python Package | Anthropic SDK | Installed at workflow runtime via `pip install anthropic` |
| **`requests` (PyPI)** | Python Package | HTTP calls to GitHub and SendGrid APIs | Installed at workflow runtime |
| **`boto3` (PyPI)** | Python Package | AWS SDK for S3 access in Lambda | Must be bundled in `lambda.zip` or provided via Lambda layer |
| **`pandas` (PyPI)** | Python Package | CSV parsing and Parquet serialisation | Must be bundled in `lambda.zip` or provided via Lambda layer |
| **`pyarrow` or `fastparquet`** | Python Package | Parquet write support for pandas | Must be bundled in `lambda.zip`; [TODO: confirm which engine is used] |

---

## 7. Deployment Instructions

### Prerequisites

```bash
# Install Terraform >= 1.x
terraform -version

# Configure AWS credentials (do NOT use hardcoded keys — use a profile or IAM role)
aws configure --profile capco-dev

# Ensure lambda.zip exists containing data_pipeline.py and dependencies
pip install boto3 pandas pyarrow --target ./lambda_package
cp src/data_pipeline.py ./lambda_package/
cd lambda_package && zip -r ../lambda.zip . && cd ..
```

### Deploy AWS Infrastructure

```bash
cd infra

# Initialise Terraform
terraform init

# Review planned changes
terraform plan -var="environment=dev" -var="aws_region=us-east-1"

# Apply infrastructure
terraform apply -var="environment=dev" -var="aws_region=us-east-1"

# Confirm outputs
terraform output
```

### Configure GitHub Actions Secrets

Navigate to **Repository Settings → Secrets and Variables → Actions** and add:

```
ANTHROPIC_API_KEY   = <your Anthropic API key>
GH_TOKEN            = <GitHub PAT with contents:read/write on source + output repos>
SENDGRID_API_KEY    = <your SendGrid API key>
```

### Trigger Workflows Manually

```bash
# Tool 1: Code Review (repo-wide scan)
gh workflow run tool1_code_review.yml \
  -f review_mode=repo

# Tool 1: Code Review (specific PR)
gh workflow run tool1_code_review