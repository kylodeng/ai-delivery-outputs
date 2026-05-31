# Architecture Document — kylodeng/ai-delivery-source

---

## 1. Overview

This repository implements an **AI-assisted software delivery platform** built on GitHub Actions, consisting of two distinct subsystems. The first is a set of five automated CI/CD workflow tools (code review, technical documentation, business documentation, automated test generation, and UAT facilitation) that use Anthropic's Claude LLM to analyse source code and produce artefacts written to a companion output repository (`ai-delivery-outputs`). The second is an AWS-hosted **customer data ingestion pipeline** (the application being reviewed/documented) that ingests raw customer CSV files from an S3 landing bucket, validates and transforms them into Parquet format, and writes results to a processed S3 bucket — triggered automatically by S3 event notifications routed to an AWS Lambda function.

---

## 2. Resources Deployed

| Resource | Type | Cloud Provider | Purpose |
|---|---|---|---|
| `capco-data-landing-{env}` | S3 Bucket | AWS | Receives raw customer CSV files; triggers Lambda on upload |
| `capco-data-processed-{env}` | S3 Bucket | AWS | Stores validated, transformed Parquet output files |
| `data-ingest-{env}` | Lambda Function (Python 3.12) | AWS | Processes CSV files: validates, transforms, writes Parquet |
| `lambda-ingest-role` | IAM Role | AWS | Execution role assumed by the Lambda function |
| `lambda-s3-policy` | IAM Role Policy | AWS | Grants S3 permissions to Lambda (overly broad — see Security) |
| S3 Bucket Notification | Event Notification | AWS | Triggers Lambda on `s3:ObjectCreated:*` for `raw/*.csv` |
| Tool 1 — Code Review | GitHub Actions Workflow | GitHub | PR/scheduled/manual Claude code review; posts PR comments |
| Tool 2 — Tech Docs | GitHub Actions Workflow | GitHub | Generates README, ARCHITECTURE, RUNBOOK on main push/schedule |
| Tool 3 — Business Docs | GitHub Actions Workflow | GitHub | Generates solution overview and gap questionnaire on release tag |
| Tool 4 — Auto Testing | GitHub Actions Workflow | GitHub | Generates or gap-analyses test files on PR/schedule |
| Tool 5 — UAT Facilitation | GitHub Actions Workflow | GitHub | Generates UAT test packs or analyses results on release branch |
| `ai-delivery-outputs` | GitHub Repository | GitHub | Stores all AI-generated artefacts (docs, reports, test files) |
| Claude (`claude-sonnet-4-6`) | External LLM API | Anthropic (SaaS) | Performs all AI analysis and content generation |
| SendGrid | Email API | Twilio/SendGrid (SaaS) | Sends notification emails on workflow completion |

---

## 3. Data Flow

### 3a — Customer Data Pipeline (AWS)

1. An external system or operator uploads a raw customer CSV file to `s3://capco-data-landing-{env}/raw/*.csv`.
2. The S3 bucket notification fires an `s3:ObjectCreated:*` event, invoking the `data-ingest-{env}` Lambda function with the bucket name and object key.
3. The Lambda (`data_pipeline.lambda_handler`) reads the `LANDING_BUCKET` environment variable and the event payload to identify the file.
4. The Lambda calls `get_s3_client()` — **⚠ currently using hardcoded credentials in source** — and downloads the CSV from the landing bucket via `GetObject`.
5. Each row is passed through `validate_customer_record()`, checking for required fields (`customer_id`, `email`, `age`, `country_code`) and basic value constraints.
6. Valid rows are collected into a Pandas DataFrame; failed rows are logged but **not persisted or dead-lettered**.
7. The validated DataFrame is serialised to Parquet and written back to the landing bucket under `processed/` (path mirrors `raw/` prefix), using `to_parquet()` with an `s3://` URI.
8. The Lambda returns a JSON summary `{processed, failed, output_key, timestamp}` to the invoker.

### 3b — AI Delivery Workflows (GitHub Actions)

1. A trigger event fires (PR open, push to main, version tag, release branch creation, schedule, or `workflow_dispatch`).
2. The relevant GitHub Actions workflow checks out the source repository.
3. Python dependencies (`anthropic`, `requests`) are installed on the runner.
4. The tool script reads environment secrets (`ANTHROPIC_API_KEY`, `GH_TOKEN`, `SENDGRID_API_KEY`) and calls `shared.py` utilities.
5. `get_repo_files()` fetches source/IaC file contents from the GitHub API (up to 20 files, capped at 4,000 characters per file for IaC, 6,000 for source).
6. For PR-triggered workflows, `get_pr_diff()` fetches the unified diff (capped at 30,000 characters).
7. Assembled context is sent to the Claude API (`claude-sonnet-4-6`) with a tool-specific system prompt.
8. Claude's response is parsed (JSON extraction with markdown-fence stripping for structured outputs; raw Markdown for document outputs).
9. Artefacts are committed to the `ai-delivery-outputs` repository via the GitHub Contents API (`PUT /repos/.../contents/...`), creating or updating files under structured paths.
10. For Tool 1, a formatted summary comment is posted directly to the source PR via the GitHub Issues API.
11. A notification email is sent via SendGrid to `kylo.deng@capco.com`.
12. An audit log entry is written to the output repository.
13. For Tool 1, the raw JSON review result is uploaded as a GitHub Actions workflow artifact.

---

## 4. Security Posture

### ✅ What Is Secured

- **GitHub Actions secrets**: `ANTHROPIC_API_KEY`, `GH_TOKEN`, and `SENDGRID_API_KEY` are stored as GitHub encrypted repository secrets and injected at runtime; they are not hardcoded in workflow YAML.
- **Lambda assume-role policy**: Correctly scoped to `lambda.amazonaws.com` as the trusted principal only.
- **S3 trigger filter**: Lambda is only triggered by `raw/*.csv` objects, reducing the attack surface of the trigger.
- **GitHub API versioning**: Requests include `X-GitHub-Api-Version: 2022-11-28` and Bearer token auth headers.
- **LLM input truncation**: File contents sent to Claude are truncated (4,000–30,000 chars) to limit prompt injection blast radius.

### ❌ Security Gaps and Findings

- **🚨 CRITICAL — Hardcoded AWS credentials in source code**: `src/data_pipeline.py` contains literal `AWS_ACCESS_KEY` (`AKIAIOSFODNN7EXAMPLE`) and `AWS_SECRET_KEY` values. Even if these are example values, the pattern is present in production code. These must be removed immediately and replaced with the Lambda execution role (no explicit credentials needed inside Lambda).
- **🚨 CRITICAL — Hardcoded DB password in Terraform**: `infra/main.tf` sets `DB_PASSWORD = "SuperSecret123!"` as a plaintext Lambda environment variable. This will appear in the AWS Console and CloudTrail logs. Must be replaced with AWS Secrets Manager or SSM Parameter Store lookup.
- **🔴 HIGH — S3 landing bucket has no encryption**: The `aws_s3_bucket.landing` resource has no `aws_s3_bucket_server_side_encryption_configuration` block. Customer PII data (email, age, country) is stored unencrypted at rest. The comment in the Terraform explicitly notes this omission.
- **🔴 HIGH — S3 landing bucket has no public access block**: No `aws_s3_bucket_public_access_block` resource is attached to the landing bucket. By default this leaves it at account-level defaults, which may allow public access if account-level block is not configured separately.
- **🔴 HIGH — Overly broad IAM policy**: `lambda-s3-policy` grants `s3:*` on `Resource: "*"`. This allows the Lambda to read, write, delete, and modify ACLs on **every S3 bucket in the account**. Should be scoped to `["s3:GetObject","s3:PutObject"]` on the two specific bucket ARNs only.
- **🔴 HIGH — Processed Parquet written back to the landing bucket**: `process_csv()` writes output to `s3://{bucket}/{output_key}` where `bucket` is the **landing** bucket, not the separately provisioned `capco-data-processed-{env}` bucket. The `processed` bucket defined in Terraform appears unused.
- **🟡 MEDIUM — No S3 encryption on processed bucket**: `aws_s3_bucket.processed` also lacks an encryption configuration block.
- **🟡 MEDIUM — No S3 versioning on either bucket**: Accidental overwrites or deletions of customer data cannot be recovered.
- **🟡 MEDIUM — No Lambda Dead Letter Queue (DLQ)**: Failed Lambda invocations are silently dropped. There is no SQS DLQ or SNS topic configured for failed events.
- **🟡 MEDIUM — No pagination in `get_all_pending_files()`**: `list_objects_v2` returns a maximum of 1,000 keys. Buckets with more than 1,000 CSV files will have unprocessed files silently ignored.
- **🟡 MEDIUM — `GH_TOKEN` scope unknown**: The token used to write to `ai-delivery-outputs` and post PR comments must have `repo` scope. If it has `public_repo` only or broader org-level access, this is either insufficient or over-privileged. [TODO: document the minimum required scopes for `GH_TOKEN`]
- **🟡 MEDIUM — No input sanitisation on `user_stories` workflow input**: Tool 5 passes raw `user_stories` text directly into environment variables and then into Claude prompts. Long or adversarially crafted inputs could cause prompt injection or environment variable truncation issues.
- **🟢 LOW — No resource tagging**: The `landing` bucket has a `# TODO: add tags` comment; neither bucket has cost-allocation or environment tags, making billing attribution and governance difficult.
- **🟢 LOW — Email sender domain unverified**: `noreply@ai-delivery.capco.com` is used as the SendGrid sender. [TODO: confirm this domain is verified in SendGrid; unverified domains cause email delivery failures and SPF/DKIM failures.]

---

## 5. Environment Variables and Secrets

| Name | Required | Sensitivity | Where Set |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | 🔴 High — API billing key | GitHub Actions repository secret |
| `GH_TOKEN` | Yes | 🔴 High — GitHub repo write access | GitHub Actions repository secret |
| `SENDGRID_API_KEY` | Yes | 🔴 High — Email delivery key | GitHub Actions repository secret |
| `OUTPUT_REPO` | No | Low | Workflow `env` block (default: `ai-delivery-outputs`) |
| `OUTPUT_REPO_OWNER` | No | Low | Workflow `env` block (defaults to `github.repository_owner`) |
| `NOTIFY_EMAIL` | No | Low | Workflow `env` block (hardcoded: `kylo.deng@capco.com`) |
| `SENDER_EMAIL` | No | Low | Workflow `env` block (hardcoded: `noreply@ai-delivery.capco.com`) |
| `SOURCE_REPO_OWNER` | No | Low | Workflow `env` block (from `github.repository_owner`) |
| `SOURCE_REPO_NAME` | No | Low | Workflow `env` block (from `github.event.repository.name`) |
| `GITHUB_RUN_URL` | No | Low | Workflow `env` block (constructed from `github.*` context) |
| `REVIEW_MODE` | No | Low | Set dynamically within workflow step |
| `PR_NUMBER` | No | Low | Set dynamically within workflow step |
| `RELEASE_VERSION` | No | Low | Set dynamically within workflow step or `workflow_dispatch` input |
| `PROJECT_NAME` | No | Low | Set dynamically within workflow step or `workflow_dispatch` input |
| `TEST_MODE` | No | Low | Workflow `env` block (default: `generate`) |
| `UAT_MODE` | No | Low | Set dynamically within workflow step |
| `USER_STORIES` | No | Low | Set from `workflow_dispatch` input |
| `UAT_RESULTS_PATH` | No | Low | Set from `workflow_dispatch` input |
| `LANDING_BUCKET` | Yes (Lambda) | Low | Lambda environment variable (set via Terraform) |
| `DB_PASSWORD` | Yes (Lambda) | 🔴 **CRITICAL — plaintext secret** | Lambda environment variable (hardcoded in `main.tf`) |
| `AWS_ACCESS_KEY` *(in code)* | — | 🔴 **CRITICAL — hardcoded credential** | Hardcoded in `src/data_pipeline.py` — **must be removed** |
| `AWS_SECRET_KEY` *(in code)* | — | 🔴 **CRITICAL — hardcoded credential** | Hardcoded in `src/data_pipeline.py` — **must be removed** |

---

## 6. Dependencies

| Dependency | Type | Purpose | Notes |
|---|---|---|---|
| Anthropic Claude API (`claude-sonnet-4-6`) | External SaaS API | All AI analysis and document generation | Requires `ANTHROPIC_API_KEY`; subject to rate limits and token quotas |
| SendGrid API | External SaaS API | Email notifications on workflow completion | Requires verified sender domain; `SENDER_EMAIL` must be configured in SendGrid |
| GitHub API (`api.github.com`) | External SaaS API | Reading source files, posting PR comments, writing output artefacts | Requires `GH_TOKEN` with `repo` scope |
| `ai-delivery-outputs` (sibling repo) | GitHub Repository | Stores all generated artefacts (docs, test files, UAT packs, audit logs) | Must exist before workflows run; `GH_TOKEN` must have write access |
| `anthropic` (PyPI) | Python package | Claude API client | Installed at runtime via `pip install anthropic` |
| `requests` (PyPI) | Python package | GitHub and SendGrid HTTP calls | Installed at runtime via `pip install requests` |
| `boto3` (PyPI) | Python package | AWS S3 access in data pipeline | [TODO: not listed in any `requirements.txt` — confirm it is available in Lambda runtime or packaged in `lambda.zip`] |
| `pandas` (PyPI) | Python package | CSV parsing and Parquet serialisation | [TODO: not listed in any `requirements.txt` — must be included in `lambda.zip` as it is not in the Lambda base runtime] |
| `pyarrow` or `fastparquet` | Python package | Backend for `pandas.to_parquet()` | [TODO: must be bundled in `lambda.zip`; not referenced anywhere in the repo] |
| `hashicorp/aws` Terraform provider `~> 5.0` | IaC provider | Provisions AWS infrastructure | Requires AWS credentials available to the Terraform executor |
| GitHub Actions (`ubuntu-latest`) | CI runner | Executes all five tool workflows | Pinned to `ubuntu-latest`; not pinned to a specific Ubuntu version |

---

## 7. Deployment Instructions

### Prerequisites

- AWS CLI configured with credentials that have IAM, S3, and Lambda permissions
- Terraform ≥ 1.5 installed
- The `lambda.zip` artifact built and placed at `infra/lambda.zip`
- GitHub repository secrets set: `ANTHROPIC_API_KEY`, `GH_TOKEN`, `SENDGRID_API_KEY`
- A companion repository named `ai-delivery-outputs` created under the same GitHub owner

### Deploy AWS Infrastructure

```bash
# 1. Navigate to the infra directory
cd infra

# 2. Initialise Terraform
terraform init

# 3. Preview changes
terraform plan -var="environment=dev" -var="aws_region=us-east-1"

# 4. Apply infrastructure
terraform apply -var="environment=dev" -var="aws_region=us-east-1"

# 5. Note the outputs
# landing_bucket  = "capco-data-landing-dev"
# processed_bucket = "capco-data-processed-dev"
```

### Build and Deploy Lambda Package

```bash
# From repo root — build the zip (pandas and pyarrow must be included)
# [TODO: no build