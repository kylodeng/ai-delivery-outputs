# Architecture Document — kylodeng/ai-delivery-source

---

## 1. Overview

This repository implements an **AI-assisted software delivery pipeline** built on GitHub Actions. It combines two distinct concerns: (1) a set of five AI-powered developer tooling workflows — code review, technical documentation, business documentation, automated test generation, and UAT facilitation — each driven by Anthropic Claude (`claude-sonnet-4-6`) and delivering outputs to a companion GitHub repository (`ai-delivery-outputs`) with email notifications via SendGrid; and (2) a sample AWS data ingestion workload consisting of a Lambda function that reads raw CSV files from an S3 landing bucket, validates and transforms customer records, and writes Parquet output to a processed S3 bucket, provisioned via Terraform. The two concerns are co-located: the data pipeline (`src/data_pipeline.py` + `infra/main.tf`) serves as the **subject** repository that the five AI tools operate against.

---

## 2. Resources Deployed

| Resource | Type | Cloud Provider | Purpose |
|---|---|---|---|
| `capco-data-landing-<env>` | S3 Bucket | AWS | Receives raw customer CSV files under the `raw/` prefix |
| `capco-data-processed-<env>` | S3 Bucket | AWS | Stores validated, transformed Parquet output |
| `data-ingest-<env>` | Lambda Function (Python 3.12) | AWS | Validates and transforms CSV → Parquet on S3 object creation |
| `lambda-ingest-role` | IAM Role | AWS | Execution identity for the Lambda function |
| `lambda-s3-policy` | IAM Role Policy | AWS | Grants S3 permissions to the Lambda role (see Security) |
| S3 Bucket Notification (`landing_trigger`) | S3 Event Notification | AWS | Triggers Lambda on `s3:ObjectCreated:*` for `raw/*.csv` |
| `ai-delivery-outputs` | GitHub Repository | GitHub | Stores all AI-generated documents and test artefacts |
| Tool 1 — Code Review | GitHub Actions Workflow | GitHub | AI code review on PRs, weekly, or on demand |
| Tool 2 — Tech Docs | GitHub Actions Workflow | GitHub | AI-generated README, architecture doc, and runbook |
| Tool 3 — Business Docs | GitHub Actions Workflow | GitHub | AI-generated solution overview and gap questionnaire |
| Tool 4 — Auto Testing | GitHub Actions Workflow | GitHub | AI-generated test files and coverage gap analysis |
| Tool 5 — UAT Facilitation | GitHub Actions Workflow | GitHub | AI-generated UAT test packs and defect report analysis |
| Anthropic Claude (`claude-sonnet-4-6`) | External LLM API | Anthropic (third-party) | Powers all five AI tools |
| SendGrid | Transactional Email API | Twilio/SendGrid (third-party) | Delivers notification emails after each tool run |

---

## 3. Data Flow

### 3a — AWS Data Ingestion Pipeline

1. An external producer (system or user) uploads a `.csv` file to `s3://capco-data-landing-<env>/raw/`.
2. The S3 bucket notification fires an `s3:ObjectCreated:*` event filtered to the `raw/` prefix and `.csv` suffix.
3. AWS invokes the `data-ingest-<env>` Lambda function, passing the bucket name and object key in the event payload.
4. `lambda_handler` in `data_pipeline.py` calls `process_csv(bucket, key)`.
5. The Lambda uses a hardcoded AWS key pair (see Security) to instantiate a Boto3 S3 client and calls `get_object` to download the CSV into memory.
6. `pandas` reads the CSV body; each row is passed to `validate_customer_record`, which checks for required fields, email format, and age range (1–150).
7. Valid rows accumulate in `valid_rows`; failed rows (with error messages) accumulate in `failed_rows`.
8. The validated DataFrame is serialised to Parquet and written back to the **same landing bucket** under the `processed/` prefix (e.g. `raw/foo.csv` → `processed/foo.parquet`).
9. The Lambda returns a summary `{processed, failed, output_key, timestamp}` with HTTP 200, or HTTP 500 on unhandled exception.

### 3b — AI Delivery Workflows

1. A GitHub event (PR open, push to `main`, version tag, release branch creation, or `workflow_dispatch`) triggers one of the five workflow YAML files.
2. The runner checks out the source repository and installs `anthropic` and `requests` via pip.
3. `shared.py` reads credentials from GitHub Actions secrets (`ANTHROPIC_API_KEY`, `GH_TOKEN`, `SENDGRID_API_KEY`) into environment variables.
4. The tool-specific script calls `get_repo_files()` or `get_pr_diff()` — fetching source and IaC file contents from GitHub REST API using `GH_TOKEN`.
5. File content is assembled into a prompt and sent to Anthropic Claude (`claude-sonnet-4-6`) via `call_claude()`.
6. Claude returns structured output (JSON or Markdown depending on the tool).
7. The script calls `write_output_file()`, which uses the GitHub Contents API to commit the generated document or test file into the `ai-delivery-outputs` repository.
8. For Tool 1, a formatted Markdown comment is also posted back to the originating pull request via `post_pr_comment()`.
9. `send_email()` dispatches a notification to `kylo.deng@capco.com` via SendGrid from `noreply@ai-delivery.capco.com`.
10. An audit entry is written (via `write_audit_entry()`) and the raw JSON/Markdown artifact is uploaded to GitHub Actions artifact storage (`upload-artifact@v4`).

---

## 4. Security Posture

### ✅ What is secured

- **GitHub Actions secrets**: `ANTHROPIC_API_KEY`, `GH_TOKEN`, and `SENDGRID_API_KEY` are stored as encrypted GitHub Actions secrets and injected as environment variables — not hardcoded in workflow YAML.
- **Lambda assume-role trust policy**: correctly scoped to `lambda.amazonaws.com` service principal only.
- **S3 event filter**: Lambda is only triggered for `raw/*.csv` objects, reducing unnecessary invocations.
- **Input validation in pipeline**: `validate_customer_record` enforces required fields, email format, and age bounds before processing.

### ❌ Gaps and explicit failures

- **CRITICAL — Hardcoded AWS credentials in source code**: `src/data_pipeline.py` lines 13–14 contain a hardcoded `AWS_ACCESS_KEY` and `AWS_SECRET_KEY`. These must be rotated immediately and replaced with IAM role-based authentication (Lambda already has an execution role — remove the explicit credential instantiation entirely).
- **CRITICAL — Hardcoded DB password in Terraform**: `infra/main.tf` sets `DB_PASSWORD = "SuperSecret123!"` as a plaintext Lambda environment variable. This must be moved to AWS Secrets Manager or SSM Parameter Store (SecureString) with IAM-gated access.
- **HIGH — S3 landing bucket has no encryption**: `aws_s3_bucket.landing` has no `aws_s3_bucket_server_side_encryption_configuration` block. Customer PII (email, age, country code) is stored in plaintext. The processed bucket also lacks an explicit encryption configuration.
- **HIGH — S3 landing bucket has no public access block**: No `aws_s3_bucket_public_access_block` resource is attached to the landing bucket. Without it, the bucket could be inadvertently made public via ACL or bucket policy.
- **HIGH — IAM policy is overly broad**: `lambda-s3-policy` grants `s3:*` on `Resource: "*"` — full S3 control across all buckets in the account. This should be restricted to `s3:GetObject` on `capco-data-landing-<env>/raw/*` and `s3:PutObject` on `capco-data-landing-<env>/processed/*` at minimum.
- **HIGH — Source code sent to third-party LLM**: All repository source files, including IaC with embedded secrets, are transmitted to the Anthropic API. If the `DB_PASSWORD` or AWS keys are present in any file fetched by `get_repo_files()`, they are sent to Anthropic. Data residency and confidentiality obligations must be reviewed.
- **MEDIUM — No S3 bucket versioning or object lock**: No protection against accidental deletion or overwrite of landing/processed data.
- **MEDIUM — No VPC binding for Lambda**: The Lambda function runs in the default VPC context with no private subnet or security group constraints.
- **MEDIUM — No encryption in transit enforcement**: Neither S3 bucket has a bucket policy enforcing `aws:SecureTransport`. HTTP access to the buckets is not explicitly denied.
- **MEDIUM — `GH_TOKEN` scope unknown**: The token is used to read source repos AND write to `ai-delivery-outputs`. [TODO: confirm this is a fine-grained PAT scoped to only those two repositories with minimum required permissions, not a classic token with broad `repo` scope.]
- **LOW — No S3 access logging**: Neither bucket has server access logging enabled, making forensic investigation of data access impossible.
- **LOW — No CloudTrail or Lambda monitoring configured in IaC**: There is no `aws_cloudtrail` or `aws_cloudwatch_log_group` resource defined.
- **LOW — No pagination in `get_all_pending_files()`**: `list_objects_v2` returns a maximum of 1,000 objects. Files beyond this limit are silently ignored.

---

## 5. Environment Variables and Secrets

| Name | Required | Sensitivity | Where set |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | **High** — LLM API key | GitHub Actions secret |
| `GH_TOKEN` | Yes | **High** — GitHub PAT with repo read/write | GitHub Actions secret |
| `SENDGRID_API_KEY` | Yes | **High** — email sending key | GitHub Actions secret |
| `OUTPUT_REPO` | No | Low | Workflow `env` block (default: `ai-delivery-outputs`) |
| `OUTPUT_REPO_OWNER` | No | Low | Workflow `env` block (defaults to `github.repository_owner`) |
| `NOTIFY_EMAIL` | No | Low | Workflow `env` block (`kylo.deng@capco.com`) |
| `SENDER_EMAIL` | No | Low | Workflow `env` block (`noreply@ai-delivery.capco.com`) |
| `SOURCE_REPO_OWNER` | No | Low | Workflow `env` block |
| `SOURCE_REPO_NAME` | No | Low | Workflow `env` block |
| `GITHUB_RUN_URL` | No | Low | Workflow `env` block (constructed from `github.*` context) |
| `TEST_MODE` | No | Low | Workflow `env` block (Tool 4 only; default: `generate`) |
| `LANDING_BUCKET` | Yes (Lambda) | Low | Terraform Lambda environment variable |
| `DB_PASSWORD` | Yes (Lambda) | **Critical** — database credential | ⚠️ Hardcoded in `infra/main.tf` — must move to Secrets Manager |
| `AWS_ACCESS_KEY` _(in code)_ | N/A | **Critical** — AWS IAM key | ⚠️ Hardcoded in `src/data_pipeline.py` — must be removed |
| `AWS_SECRET_KEY` _(in code)_ | N/A | **Critical** — AWS IAM secret | ⚠️ Hardcoded in `src/data_pipeline.py` — must be removed |

---

## 6. Dependencies

| Dependency | Type | Purpose | Notes |
|---|---|---|---|
| Anthropic Claude API (`claude-sonnet-4-6`) | External SaaS API | LLM inference for all five tools | Requires `ANTHROPIC_API_KEY`; source code is transmitted — check data processing agreement |
| SendGrid / Twilio Email API | External SaaS API | Transactional email notifications | Requires `SENDGRID_API_KEY`; sender domain `ai-delivery.capco.com` must be verified in SendGrid |
| GitHub REST API (`api.github.com`) | External SaaS API | Read source repo files, post PR comments, write to output repo | Requires `GH_TOKEN` |
| `ai-delivery-outputs` (companion repo) | GitHub Repository | Stores all generated documents and artefacts | Must exist under the same owner; [TODO: confirm branch protection and access controls on this repo] |
| `anthropic` Python SDK | PyPI package | Python client for Claude API | Installed at runtime via `pip install anthropic` — no pinned version |
| `requests` Python library | PyPI package | HTTP calls to GitHub and SendGrid APIs | Installed at runtime — no pinned version |
| `boto3` | PyPI package (Lambda layer or bundled) | AWS SDK for S3 access in `data_pipeline.py` | [TODO: confirm whether boto3 is bundled in `lambda.zip` or provided by the Lambda runtime] |
| `pandas` | PyPI package | CSV parsing and DataFrame operations | [TODO: confirm included in `lambda.zip`; pandas + dependencies add ~50MB] |
| `pyarrow` / `fastparquet` | PyPI package (transitive) | Required by `pandas.to_parquet()` | [TODO: confirm Parquet engine is included in `lambda.zip`] |
| AWS Lambda runtime (Python 3.12) | AWS managed | Executes `data_pipeline.lambda_handler` | Built-in boto3 may differ from the version expected by the code |
| HashiCorp Terraform (`~> 5.0` AWS provider) | IaC toolchain | Provisions AWS infrastructure | Must be installed locally or in CI for deployment |

---

## 7. Deployment Instructions

### Prerequisites

```bash
# Install Terraform (>= 1.5 recommended for AWS provider ~> 5.0)
# Install Python 3.12
# Configure AWS credentials (do NOT use hardcoded keys — use IAM role or aws configure)
aws configure   # or set AWS_PROFILE / assume a role

# Package the Lambda deployment artifact
cd src
pip install boto3 pandas pyarrow -t ./package
cp data_pipeline.py ./package/
cd package && zip -r ../../infra/lambda.zip . && cd ../..
```

### Deploy AWS infrastructure

```bash
cd infra

terraform init

terraform plan \
  -var="environment=dev" \
  -var="aws_region=us-east-1"

terraform apply \
  -var="environment=dev" \
  -var="aws_region=us-east-1"
```

### Configure GitHub Actions secrets

Navigate to **Settings → Secrets and variables → Actions** in the `ai-delivery-source` repository and set:

```
ANTHROPIC_API_KEY   = <your Anthropic API key>
GH_TOKEN            = <GitHub PAT with contents:read+write on source and output repos>
SENDGRID_API_KEY    = <your SendGrid API key>
```

### Trigger workflows manually

```bash
# Tool 1 — Code Review (repo-wide scan)
gh workflow run tool1_code_review.yml \
  -f review_mode=repo

# Tool 1 — Code Review (specific PR)
gh workflow run tool1_code_review.yml \
  -f review_mode=pr \
  -f pr_number=42

# Tool 2 — Tech Documentation
gh workflow run tool2_tech_docs.yml

# Tool 3 — Business Documentation
gh workflow run tool3_business_docs.yml \
  -f project_name="Data Ingestion Pipeline" \
  -f release_version="1.0.0"

# Tool 4 — Auto Testing
gh workflow run tool4_auto_testing.yml \
  -f test_mode=generate

# Tool 5 — UAT Facilitation (generate test pack)
gh workflow run tool5_uat.yml \
  -f uat_mode=generate \
  -f release_version="1.0.0"

# Tool 5 — UAT Facilitation (analyse results)
gh workflow run tool5_uat.yml \
  -f uat_mode=analyse \
  -f release_version="1.0.0" \
  -f uat_results_path="uat/owner-repo/v1.0.0/UAT_RESULTS_SHEET.csv"
```

### Destroy infrastructure

```bash
cd infra
terraform destroy \
  -var="environment=dev" \
  -