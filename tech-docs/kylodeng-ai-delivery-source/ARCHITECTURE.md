# Architecture Document — kylodeng/ai-delivery-source

---

## 1. Overview

This system is a dual-purpose platform combining a **data ingestion pipeline** with an **AI-powered software delivery automation suite**. The data pipeline ingests customer CSV files dropped into an AWS S3 landing bucket, validates and transforms them via an AWS Lambda function, and writes the results as Parquet files to a processed S3 bucket. Layered on top of this is a set of five GitHub Actions workflows that use Anthropic's Claude API to automate software delivery tasks across the repository lifecycle: automated code review on pull requests, technical documentation generation on merges, business documentation generation on releases, AI-generated test suites on PRs, and UAT test pack generation/analysis on release branches. Outputs from all five AI tools are persisted to a separate GitHub repository (`ai-delivery-outputs`) and notifications are dispatched via SendGrid email.

---

## 2. Resources Deployed

| Resource | Type | Cloud Provider | Purpose |
|---|---|---|---|
| `capco-data-landing-{env}` | S3 Bucket | AWS | Receives raw customer CSV files; triggers Lambda on upload to `raw/` prefix |
| `capco-data-processed-{env}` | S3 Bucket | AWS | Stores validated and transformed Parquet files |
| `data-ingest-{env}` | Lambda Function | AWS | Processes CSV files: validates records, transforms to Parquet, writes to processed bucket |
| `lambda-ingest-role` | IAM Role | AWS | Execution role assumed by the Lambda function |
| `lambda-s3-policy` | IAM Role Policy | AWS | Grants S3 permissions to the Lambda execution role (⚠️ overly broad — see Security) |
| S3 Bucket Notification | Event Notification | AWS | Triggers Lambda on `s3:ObjectCreated:*` events under `raw/*.csv` |
| `tool1_code_review` | GitHub Actions Workflow | GitHub | Runs Claude code review on PRs, weekly, or on demand |
| `tool2_tech_docs` | GitHub Actions Workflow | GitHub | Generates README, architecture doc, and runbook on merge to main |
| `tool3_business_docs` | GitHub Actions Workflow | GitHub | Generates business/solution overview docs on version tags or releases |
| `tool4_auto_testing` | GitHub Actions Workflow | GitHub | Generates AI test files or performs coverage gap analysis on PRs |
| `tool5_uat` | GitHub Actions Workflow | GitHub | Generates UAT test packs on release branch creation; analyses completed results |
| `ai-delivery-outputs` | GitHub Repository | GitHub | Stores all AI-generated artefacts (docs, reports, test files) |
| Claude Sonnet (`claude-sonnet-4-6`) | External LLM API | Anthropic | Performs all AI reasoning tasks across the five tools |
| SendGrid | Email API | Twilio/SendGrid | Delivers notification emails on workflow completion |

---

## 3. Data Flow

### Data Pipeline (AWS)

1. An external process or user uploads a customer CSV file to `s3://capco-data-landing-{env}/raw/<filename>.csv`.
2. The S3 bucket notification fires an `s3:ObjectCreated:*` event and invokes the `data-ingest-{env}` Lambda function with the bucket name and object key.
3. Lambda calls `process_csv()`, which instantiates a Boto3 S3 client using **hardcoded AWS credentials** (⚠️ — see Security) and downloads the CSV via `get_object`.
4. Each row is validated against required fields (`customer_id`, `email`, `age`, `country_code`) and business rules (age 1–150, valid email format). Rows passing validation are collected; failing rows are captured with error messages.
5. The validated DataFrame is serialised to Parquet and written back to the same landing bucket under the `processed/` prefix (e.g., `processed/<filename>.parquet`).
6. Lambda returns a JSON summary (`processed`, `failed`, `output_key`, `timestamp`) to the invoker with HTTP status 200 or 500.

### AI Delivery Workflows (GitHub Actions)

7. A GitHub event (PR opened, push to main, version tag, release branch creation, or scheduled cron) triggers one of the five workflow YAML files.
8. The workflow runner checks out the source repository and installs `anthropic` and `requests` Python packages.
9. The relevant `tool*.py` script calls `shared.py::get_repo_files()` or `get_pr_diff()`, which fetches source/IaC file content from the GitHub REST API using `GH_TOKEN`.
10. The script calls `shared.py::call_claude()`, passing a system prompt and user prompt (with file content) to the Anthropic Messages API (`claude-sonnet-4-6`). Responses up to 4,096 tokens are returned.
11. The Claude response (Markdown documents or JSON) is post-processed (JSON extraction, delimiter splitting, CSV building) by the tool script.
12. `shared.py::write_output_file()` commits the generated artefact to `ai-delivery-outputs/<path>` via the GitHub Contents API (create or update with SHA).
13. For Tool 1, `post_pr_comment()` posts a formatted review comment directly to the source PR via the GitHub Issues Comments API.
14. `shared.py::send_email()` dispatches a notification email via the SendGrid API to `kylo.deng@capco.com` with a link to the generated output and a run URL.
15. An audit log entry is written via `write_audit_entry()` [TODO: audit log destination not visible in shared.py — file appears truncated].

---

## 4. Security Posture

### ✅ What Is Secured

- GitHub Actions secrets (`ANTHROPIC_API_KEY`, `GH_TOKEN`, `SENDGRID_API_KEY`) are stored as encrypted GitHub repository secrets and injected as environment variables at runtime — not hardcoded in workflow YAML.
- Tool 5 UAT workflow includes a conditional guard (`if:`) to prevent execution on non-release branches when triggered by `create` events.
- Lambda function uses an IAM role (`sts:AssumeRole`) rather than embedding user credentials in the function configuration... however, the source code undermines this (see below).
- S3 bucket notifications use a `filter_prefix` and `filter_suffix` to scope Lambda triggers to `raw/*.csv` only.

### ❌ Gaps and Vulnerabilities

| Gap | Detail |
|---|---|
| **Hardcoded AWS credentials in source code** | `src/data_pipeline.py` lines 10–11 contain a literal `AWS_ACCESS_KEY` and `AWS_SECRET_KEY`. These are example-format keys but the pattern is a critical security flaw. The `# TODO` comment acknowledges this but it is unresolved. |
| **Hardcoded DB password in Lambda IaC** | `infra/main.tf` sets `DB_PASSWORD = "SuperSecret123!"` as a Lambda environment variable in plaintext. This will appear in AWS Console, CloudTrail, and Terraform state. Must be replaced with AWS SSM Parameter Store or Secrets Manager reference. |
| **S3 landing bucket has no encryption** | `aws_s3_bucket.landing` has no `aws_s3_bucket_server_side_encryption_configuration` block. Data at rest is unencrypted. The in-code comment explicitly flags this. The processed bucket also lacks encryption configuration. |
| **S3 landing bucket has no public access block** | `aws_s3_bucket.landing` has no `aws_s3_bucket_public_access_block` resource. Without this, the bucket could be made public via ACLs or bucket policy. The in-code comment explicitly flags this. |
| **Overly broad IAM policy** | `lambda_policy` grants `s3:*` on `Resource: "*"` — full S3 access across all buckets in the account. Should be scoped to `s3:GetObject` on the landing bucket ARN and `s3:PutObject` on the processed bucket ARN at minimum. |
| **No S3 bucket versioning** | Neither bucket has versioning enabled. Accidental overwrites or deletions are unrecoverable. |
| **No S3 access logging** | Neither bucket has server access logging configured. No audit trail of who accessed or uploaded data. |
| **No VPC / network isolation for Lambda** | Lambda is not configured within a VPC. It communicates with S3 over the public internet without a VPC endpoint. |
| **No Lambda resource-based policy shown** | There is no `aws_lambda_permission` resource granting S3 permission to invoke the Lambda. This may cause the trigger to fail silently. [TODO: verify Lambda invoke permission exists] |
| **GH_TOKEN scope unknown** | [TODO: confirm what scopes are granted to the GH_TOKEN secret — it needs `repo` access to both the source and output repos; overly broad tokens (e.g., `admin:org`) would be a risk] |
| **No encryption in transit enforcement** | No S3 bucket policy denying HTTP (non-HTTPS) access is configured. |
| **No Terraform state backend configured** | `infra/main.tf` has no `backend` block. Terraform state is local only, meaning no state locking, no remote collaboration, and state may contain the plaintext `DB_PASSWORD`. |
| **No input sanitisation in Lambda handler** | `lambda_handler` reads `event["key"]` without validation. A malformed event could cause an unhandled `KeyError`. |
| **S3 pagination not implemented** | `get_all_pending_files()` uses `list_objects_v2` without handling the `NextContinuationToken`. Buckets with >1,000 objects will silently return incomplete results. |

---

## 5. Environment Variables and Secrets

| Name | Required | Sensitivity | Where Set |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | 🔴 High — LLM API key with billing implications | GitHub Actions secret |
| `GH_TOKEN` | Yes | 🔴 High — grants read/write access to GitHub repos | GitHub Actions secret |
| `SENDGRID_API_KEY` | Yes | 🔴 High — grants email sending capability | GitHub Actions secret |
| `OUTPUT_REPO` | No | 🟢 Low | Hardcoded in workflow env (`ai-delivery-outputs`) |
| `OUTPUT_REPO_OWNER` | No | 🟢 Low | Derived from `github.repository_owner` |
| `NOTIFY_EMAIL` | No | 🟡 Medium — PII (email address) | Hardcoded in workflow env (`kylo.deng@capco.com`) |
| `SENDER_EMAIL` | No | 🟢 Low | Hardcoded in workflow env |
| `SOURCE_REPO_OWNER` | No | 🟢 Low | Derived from `github.repository_owner` |
| `SOURCE_REPO_NAME` | No | 🟢 Low | Derived from `github.event.repository.name` |
| `GITHUB_RUN_URL` | No | 🟢 Low | Derived from GitHub context |
| `LANDING_BUCKET` | Yes (Lambda) | 🟢 Low | Lambda environment variable (set via Terraform) |
| `DB_PASSWORD` | Yes (Lambda) | 🔴 High — database credential | ⚠️ Hardcoded plaintext in `infra/main.tf` — must be moved to Secrets Manager |
| `AWS_ACCESS_KEY` | — | 🔴 Critical — AWS IAM credential | ⚠️ Hardcoded in `src/data_pipeline.py` — must be removed immediately |
| `AWS_SECRET_KEY` | — | 🔴 Critical — AWS IAM credential | ⚠️ Hardcoded in `src/data_pipeline.py` — must be removed immediately |
| `TEST_MODE` | No | 🟢 Low | Workflow env, defaults to `generate` |
| `REVIEW_MODE` | No | 🟢 Low | Set dynamically in workflow step |
| `PR_NUMBER` | No | 🟢 Low | Set dynamically in workflow step |
| `RELEASE_VERSION` | No | 🟢 Low | Set dynamically in workflow step |
| `PROJECT_NAME` | No | 🟢 Low | Set dynamically in workflow step |
| `UAT_MODE` | No | 🟢 Low | Set dynamically in workflow step |
| `USER_STORIES` | No | 🟢 Low | Passed as workflow dispatch input |
| `UAT_RESULTS_PATH` | No | 🟢 Low | Passed as workflow dispatch input |

---

## 6. Dependencies

| Dependency | Type | Used By | Notes |
|---|---|---|---|
| **Anthropic Claude API** (`claude-sonnet-4-6`) | External SaaS API | All 5 GitHub Actions tools | Requires `ANTHROPIC_API_KEY`; subject to rate limits, token costs, and model availability. No fallback model configured. |
| **GitHub REST API** (`api.github.com`) | External SaaS API | All 5 GitHub Actions tools via `shared.py` | Used for reading repo files, fetching PR diffs, writing output files, posting PR comments. Requires `GH_TOKEN`. API version pinned to `2022-11-28`. |
| **SendGrid API** | External SaaS API | All 5 GitHub Actions tools via `shared.py` | Used for email notifications. Requires `SENDGRID_API_KEY`. [TODO: `send_email()` implementation not visible — shared.py appears truncated] |
| **`ai-delivery-outputs`** | GitHub Repository | All 5 tools | Separate repo owned by `OUTPUT_REPO_OWNER` used as the artefact store for all generated documents. Must exist and be writable by `GH_TOKEN` before workflows run. |
| **`anthropic` Python package** | PyPI library | All 5 tools | Installed at runtime via `pip install anthropic`. No version pinned — breaking changes possible. |
| **`requests` Python package** | PyPI library | All 5 tools | Installed at runtime via `pip install requests`. No version pinned. |
| **`boto3`** | PyPI library | `src/data_pipeline.py` | AWS SDK for Python. [TODO: not installed via requirements.txt or Dockerfile — must be available in Lambda runtime or layer] |
| **`pandas`** | PyPI library | `src/data_pipeline.py` | CSV/DataFrame processing. [TODO: not present in Lambda runtime by default — requires Lambda layer or container image] |
| **`pyarrow` / `fastparquet`** | PyPI library | `src/data_pipeline.py` (via `to_parquet`) | Required by pandas for Parquet output. [TODO: must be included in Lambda deployment package] |
| **AWS S3** | Cloud Service | `src/data_pipeline.py`, `infra/main.tf` | Landing and processed data storage. |
| **AWS Lambda** | Cloud Service | `infra/main.tf` | Serverless compute for pipeline execution. |
| **AWS IAM** | Cloud Service | `infra/main.tf` | Role and policy for Lambda execution. |

---

## 7. Deployment Instructions

### Prerequisites

- Terraform >= 1.0 installed locally
- AWS CLI configured with credentials for the target account
- `lambda.zip` built from `src/data_pipeline.py` and its dependencies (`boto3`, `pandas`, `pyarrow`) — [TODO: no build script provided; manual packaging required]
- GitHub repository secrets set: `ANTHROPIC_API_KEY`, `GH_TOKEN`, `SENDGRID_API_KEY`
- The `ai-delivery-outputs` repository must exist under the same GitHub owner

### Package the Lambda

```bash
# Create deployment package (adjust paths as needed)
pip install boto3 pandas pyarrow -t ./package
cp src/data_pipeline.py ./package/
cd package && zip -r ../lambda.zip . && cd ..
# Move zip to infra directory
mv lambda.zip infra/lambda.zip
```

### Deploy AWS Infrastructure

```bash
cd infra

# Initialise Terraform (local state — no backend configured)
terraform init

# Review the plan
terraform plan -var="environment=dev" -var="aws_region=us-east-1"

# Apply
terraform apply -var="environment=dev" -var="aws_region=us-east-1"

# To deploy to a different environment
terraform apply -var="environment=prod" -var="aws_region=us-east-1"
```

### Trigger AI Workflows Manually

```bash
# Tool 1: Code Review (repo-wide)
gh workflow run tool1_code_review.yml \
  -f review_mode