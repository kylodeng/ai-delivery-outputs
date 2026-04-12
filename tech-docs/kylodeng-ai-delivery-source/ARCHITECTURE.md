# Architecture Document — kylodeng/ai-delivery-source

---

## 1. Overview

This repository implements an AI-powered software delivery automation platform built on GitHub Actions. It combines a live AWS data ingestion pipeline (a Lambda function that ingests customer CSV files from S3, validates and transforms them to Parquet) with five AI-driven developer tooling workflows that leverage the Anthropic Claude API to automate code review, technical documentation, business documentation, test generation, and UAT facilitation. Workflow outputs (reports, docs, test packs) are committed to a companion repository (`ai-delivery-outputs`) and notifications are dispatched via SendGrid email. The system is intended to reduce manual effort across the software delivery lifecycle by integrating AI assistance directly into CI/CD triggers.

---

## 2. Resources Deployed

| Resource | Type | Cloud Provider | Purpose |
|---|---|---|---|
| `capco-data-landing-<env>` | S3 Bucket | AWS | Receives raw customer CSV files for ingestion |
| `capco-data-processed-<env>` | S3 Bucket | AWS | Stores validated, transformed Parquet output files |
| `data-ingest-<env>` | Lambda Function (Python 3.12) | AWS | Processes CSV files triggered by S3 object creation events |
| `lambda-ingest-role` | IAM Role | AWS | Execution role assumed by the Lambda function |
| `lambda-s3-policy` | IAM Role Policy | AWS | Grants S3 permissions to the Lambda role |
| S3 Bucket Notification (`landing_trigger`) | S3 Event Notification | AWS | Triggers Lambda on `s3:ObjectCreated:*` under `raw/*.csv` |
| Tool 1 — Code Review | GitHub Actions Workflow | GitHub | AI-powered PR and repo code review via Claude |
| Tool 2 — Tech Documentation | GitHub Actions Workflow | GitHub | Auto-generates README, architecture doc, and runbook |
| Tool 3 — Business Documentation | GitHub Actions Workflow | GitHub | Auto-generates solution overview and gap questionnaire |
| Tool 4 — Auto Testing | GitHub Actions Workflow | GitHub | Auto-generates test files or performs coverage gap analysis |
| Tool 5 — UAT Facilitation | GitHub Actions Workflow | GitHub | Generates UAT test packs and analyses completed results |
| `ai-delivery-outputs` | GitHub Repository | GitHub | Stores all AI-generated artefacts (docs, reports, test packs) |
| Anthropic Claude (`claude-sonnet-4-6`) | External AI API | Anthropic | LLM inference for all five tooling workflows |
| SendGrid | External Email API | Twilio/SendGrid | Delivers email notifications for all workflow completions |

---

## 3. Data Flow

### A — AWS Data Ingestion Pipeline

1. An upstream system (or operator) uploads a raw customer CSV file to `s3://capco-data-landing-<env>/raw/<filename>.csv`.
2. The S3 bucket notification (`landing_trigger`) fires an `s3:ObjectCreated:*` event filtered to the `raw/` prefix and `.csv` suffix.
3. AWS Lambda invokes `data_pipeline.lambda_handler`, passing the bucket name and object key in the event payload.
4. The Lambda function instantiates a boto3 S3 client using **hardcoded AWS credentials** (see Security Posture) and calls `get_object` to download the CSV into memory.
5. `pandas` reads the CSV body and iterates each row through `validate_customer_record`, checking for required fields (`customer_id`, `email`, `age`, `country_code`), email format, and age range (1–150).
6. Valid rows are accumulated into a result DataFrame; failed rows are collected with error messages.
7. The result DataFrame is serialised to Parquet and written to `s3://capco-data-landing-<env>/processed/<filename>.parquet` (note: output lands in the **same landing bucket**, not the `processed` bucket — see Risks).
8. The Lambda returns a JSON response with counts of processed/failed rows, output key, and timestamp.

### B — AI Tooling Workflows (GitHub Actions)

1. A GitHub event triggers one of the five workflow YAML files (PR open, push to main, version tag, release branch creation, schedule, or `workflow_dispatch`).
2. The Actions runner checks out the source repository with full history (`fetch-depth: 0` for Tool 1).
3. Python 3.12 is configured and `anthropic` + `requests` packages are installed via pip.
4. `shared.py` reads secrets from GitHub Actions environment variables (`ANTHROPIC_API_KEY`, `GH_TOKEN`, `SENDGRID_API_KEY`).
5. The tool script calls `get_repo_files` or `get_pr_diff` via the GitHub REST API (authenticated with `GH_TOKEN`) to fetch source files or PR diffs, truncating content to safe prompt sizes.
6. The assembled prompt is sent to the Anthropic Claude API (`claude-sonnet-4-6`, max 4096 tokens default) via the `anthropic` Python SDK.
7. Claude's response is parsed (JSON extraction with markdown fence stripping for structured tools; raw Markdown for documentation tools).
8. The output artefact (review JSON, Markdown doc, CSV test pack, etc.) is committed to the `ai-delivery-outputs` repository via `write_output_file`, which uses the GitHub Contents API (PUT) to create or update files.
9. An email notification is dispatched via the SendGrid API to `kylo.deng@capco.com` from `noreply@ai-delivery.capco.com` containing a summary and link to the artefact.
10. For Tool 1 (code review), a formatted Markdown comment is also posted directly to the source PR via the GitHub Issues Comments API.
11. An audit log entry is written (via `write_audit_entry` in `shared.py`) to the output repository.
12. The workflow uploads any generated JSON files as GitHub Actions artefacts (Tool 1 only, `if: always()`).

---

## 4. Security Posture

### ✅ What Is Secured

- **GitHub Secrets**: `ANTHROPIC_API_KEY`, `GH_TOKEN`, and `SENDGRID_API_KEY` are stored as GitHub repository secrets and injected as environment variables at runtime; they are not hardcoded in workflow YAML files.
- **Lambda IAM Trust Policy**: The `assume_role_policy` correctly restricts the trust to `lambda.amazonaws.com` only.
- **UAT Workflow Conditional**: Tool 5 only executes on `release/` prefixed branches or manual dispatch, reducing accidental triggering.
- **Prompt size limits**: Source files are truncated (`[:4000]`, `[:6000]`, etc.) before being sent to the Claude API, limiting accidental exfiltration of large secrets embedded in code.

### ❌ Security Gaps — Explicit Callouts

- **🔴 CRITICAL — Hardcoded AWS credentials in source code**: `src/data_pipeline.py` contains a hardcoded `AWS_ACCESS_KEY` (`AKIAIOSFODNN7EXAMPLE`) and `AWS_SECRET_KEY`. Even though these appear to be example keys, the pattern is dangerous, these values are committed to the repository and will be picked up by secret scanners. The Lambda already receives an IAM execution role; the boto3 client should use the role's ambient credentials (instantiate `boto3.client('s3')` with no explicit credentials).
- **🔴 CRITICAL — Hardcoded DB password in Terraform**: `infra/main.tf` sets `DB_PASSWORD = "SuperSecret123!"` as a plaintext Lambda environment variable. This is visible in the AWS Console, CloudTrail logs, and Terraform state. Must be migrated to AWS Secrets Manager or SSM Parameter Store (SecureString).
- **🔴 HIGH — S3 landing bucket has no encryption**: `aws_s3_bucket.landing` has no `aws_s3_bucket_server_side_encryption_configuration` block and no `aws_s3_bucket_public_access_block`. Customer PII (emails, ages, country codes) is stored unencrypted. Both SSE-S3 (minimum) and a public access block resource must be added.
- **🔴 HIGH — S3 processed bucket has no encryption**: `aws_s3_bucket.processed` also lacks any encryption or public access block configuration.
- **🔴 HIGH — Overly broad IAM policy**: `lambda-s3-policy` grants `s3:*` on `Resource: "*"`. This gives the Lambda full S3 control across the entire AWS account. The policy should be scoped to `s3:GetObject` on the landing bucket and `s3:PutObject` on the processed bucket ARNs only.
- **🔴 HIGH — Lambda environment variable contains plaintext secret**: The `DB_PASSWORD` is visible in plaintext in Terraform state and the AWS Lambda console. Even if Secrets Manager is not used, at minimum the variable should be marked sensitive in Terraform.
- **🟡 MEDIUM — No S3 bucket versioning or object lock**: There is no versioning on either S3 bucket, meaning accidental overwrites or deletions of processed data are unrecoverable.
- **🟡 MEDIUM — No VPC configuration for Lambda**: The Lambda function has no `vpc_config` block, meaning it runs in the AWS-managed public network. For a pipeline handling customer PII, the function should run in a private VPC subnet with a NAT gateway or VPC endpoints for S3 access.
- **🟡 MEDIUM — GH_TOKEN scope unknown**: The `GH_TOKEN` secret is used to read source repos and write to `ai-delivery-outputs`. The required scopes (`repo`, `contents:write`) are not documented. A fine-grained PAT scoped to specific repositories should be used and its permissions documented.
- **🟡 MEDIUM — No pagination in `get_all_pending_files`**: `list_objects_v2` returns a maximum of 1,000 objects. Files beyond that limit are silently dropped.
- **🟡 MEDIUM — Bare `except Exception` in `lambda_handler`**: All exceptions are caught and swallowed, returning a 500. This prevents Lambda retry behaviour from working correctly for transient errors (e.g. S3 throttling).
- **🟡 MEDIUM — Source file content sent to external AI API**: Up to 4,000–6,000 characters of potentially sensitive source code (including the hardcoded credentials above) are transmitted to Anthropic's API. Data residency and retention policies of the Anthropic API should be reviewed for compliance.
- **🟢 LOW — No resource tags on S3 buckets**: The `# TODO: add tags` comment is present in `main.tf`. Without tags, cost allocation, compliance scanning, and data classification are impaired.

---

## 5. Environment Variables and Secrets

| Name | Required | Sensitivity | Where Set |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | 🔴 High — API key for billed AI service | GitHub Actions Secret |
| `GH_TOKEN` | Yes | 🔴 High — GitHub PAT with repo read/write | GitHub Actions Secret |
| `SENDGRID_API_KEY` | Yes | 🔴 High — API key for email dispatch | GitHub Actions Secret |
| `OUTPUT_REPO` | No | 🟢 Low | Workflow `env` block (hardcoded `ai-delivery-outputs`) |
| `OUTPUT_REPO_OWNER` | No | 🟢 Low | Workflow `env` block (`github.repository_owner`) |
| `NOTIFY_EMAIL` | No | 🟡 Medium — PII (recipient email) | Workflow `env` block (hardcoded `kylo.deng@capco.com`) |
| `SENDER_EMAIL` | No | 🟢 Low | Workflow `env` block (hardcoded `noreply@ai-delivery.capco.com`) |
| `SOURCE_REPO_OWNER` | No | 🟢 Low | Workflow `env` block |
| `SOURCE_REPO_NAME` | No | 🟢 Low | Workflow `env` block |
| `GITHUB_RUN_URL` | No | 🟢 Low | Workflow `env` block (constructed from `github.*` context) |
| `REVIEW_MODE` | No | 🟢 Low | Set at runtime by workflow step (`pr` or `repo`) |
| `PR_NUMBER` | No | 🟢 Low | Set at runtime by workflow step |
| `TEST_MODE` | No | 🟢 Low | Workflow `env` block (`generate` or `gap-analysis`) |
| `RELEASE_VERSION` | No | 🟢 Low | Set at runtime by workflow step |
| `PROJECT_NAME` | No | 🟢 Low | Set at runtime by workflow step |
| `UAT_MODE` | No | 🟢 Low | Set at runtime by workflow step |
| `USER_STORIES` | No | 🟡 Medium — may contain business requirements | Set at runtime from `workflow_dispatch` input |
| `UAT_RESULTS_PATH` | No | 🟢 Low | Set at runtime from `workflow_dispatch` input |
| `LANDING_BUCKET` | Yes (Lambda) | 🟢 Low | Lambda environment variable via Terraform |
| `DB_PASSWORD` | Yes (Lambda) | 🔴 **CRITICAL — plaintext secret** | Lambda environment variable via Terraform — **must move to Secrets Manager** |
| `AWS_ACCESS_KEY` *(hardcoded)* | N/A | 🔴 **CRITICAL — hardcoded in source** | `src/data_pipeline.py` — **must be removed immediately** |
| `AWS_SECRET_KEY` *(hardcoded)* | N/A | 🔴 **CRITICAL — hardcoded in source** | `src/data_pipeline.py` — **must be removed immediately** |

---

## 6. Dependencies

| Dependency | Type | Purpose | Notes |
|---|---|---|---|
| Anthropic API (`claude-sonnet-4-6`) | External SaaS API | LLM inference for all five tools | Billed per token; no fallback model configured |
| SendGrid API | External SaaS API | Transactional email notifications | [TODO: confirm SendGrid account ownership and domain verification for `ai-delivery.capco.com`] |
| GitHub API v3 (REST) | External API | Fetch repo files, PR diffs, post comments, write output files | Requires PAT with `repo` scope |
| `ai-delivery-outputs` (GitHub repo) | Companion Repository | Stores all generated artefacts | Must exist and be accessible to `GH_TOKEN` before workflows run |
| `kylodeng/ai-delivery-source` | This Repository | Source code and IaC under analysis | — |
| `anthropic` (PyPI) | Python Package | Anthropic Python SDK | Installed at runtime via `pip install anthropic` — no pinned version |
| `requests` (PyPI) | Python Package | HTTP calls to GitHub and SendGrid APIs | Installed at runtime — no pinned version |
| `boto3` | Python Package | AWS SDK for S3 operations in Lambda | Provided by Lambda runtime; version not pinned |
| `pandas` | Python Package | CSV parsing and Parquet writing in Lambda | Not declared in a `requirements.txt` — must be packaged in `lambda.zip` |
| AWS (us-east-1) | Cloud Provider | Hosts Lambda, S3 buckets | Single region — no DR/multi-region |
| Terraform (`~> 5.0` AWS provider) | IaC Tool | Provisions AWS resources | State backend not configured (no `backend` block — defaults to local state) |

---

## 7. Deployment Instructions

### Prerequisites

- AWS CLI configured with credentials that have permissions to create IAM roles, Lambda functions, and S3 buckets.
- Terraform >= 1.5 installed.
- Python 3.12 installed locally.
- `lambda.zip` built and present in the `infra/` directory before applying Terraform.

### Build Lambda Package

```bash
# From repo root
pip install pandas boto3 -t lambda_package/
cp src/data_pipeline.py lambda_package/
cd lambda_package && zip -r ../infra/lambda.zip . && cd ..
```

### Deploy Infrastructure

```bash
cd infra

# Initialise Terraform (WARNING: no remote backend configured — state is local)
terraform init

# Preview changes
terraform plan -var="environment=dev" -var="aws_region=us-east-1"

# Apply
terraform apply -var="environment=dev" -var="aws_region=us-east-1"
```

### Configure GitHub Actions Secrets

In the repository **Settings → Secrets and variables → Actions**, create the following secrets:

```
ANTHROPIC_API_KEY   = <your