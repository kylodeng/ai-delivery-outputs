# Architecture Document — kylodeng/ai-delivery-source

---

## 1. Overview

This system is a dual-purpose platform combining a **data ingestion pipeline** (infrastructure + application code) with an **AI-assisted software delivery toolkit**. The data pipeline ingests customer CSV files uploaded to an AWS S3 landing bucket, validates and transforms them via an AWS Lambda function, and writes the results as Parquet files to a processed S3 bucket. Layered on top of this is a suite of five GitHub Actions–based AI tools powered by Anthropic's Claude API: automated code review, technical documentation generation, business documentation generation, AI-generated test suites, and UAT facilitation. Outputs from all five AI tools are committed to a companion GitHub repository (`ai-delivery-outputs`) and notifications are delivered via SendGrid email.

---

## 2. Resources Deployed

| Resource | Type | Cloud Provider | Purpose |
|---|---|---|---|
| `capco-data-landing-{env}` | S3 Bucket | AWS | Receives raw customer CSV files uploaded externally |
| `capco-data-processed-{env}` | S3 Bucket | AWS | Stores validated, transformed Parquet output files |
| `data-ingest-{env}` | Lambda Function (Python 3.12) | AWS | Validates and transforms CSVs triggered by S3 events |
| `lambda-ingest-role` | IAM Role | AWS | Execution role assumed by the Lambda function |
| `lambda-s3-policy` | IAM Role Policy | AWS | Grants S3 permissions to the Lambda role |
| S3 Bucket Notification | S3 Event Trigger | AWS | Fires Lambda on `s3:ObjectCreated:*` for `raw/*.csv` |
| `ai-delivery-outputs` | GitHub Repository | GitHub | Stores all AI-generated documents and test artefacts |
| Tool 1 — Code Review | GitHub Actions Workflow | GitHub | Runs Claude code reviews on PRs and weekly |
| Tool 2 — Tech Docs | GitHub Actions Workflow | GitHub | Generates README, architecture doc, runbook on merge to main |
| Tool 3 — Business Docs | GitHub Actions Workflow | GitHub | Generates solution overview and gap questionnaire on release tag |
| Tool 4 — Auto Testing | GitHub Actions Workflow | GitHub | Generates or analyses test coverage on PR or schedule |
| Tool 5 — UAT | GitHub Actions Workflow | GitHub | Generates UAT test packs or analyses UAT results on release branches |
| Claude (`claude-sonnet-4-6`) | LLM API | Anthropic (external) | Powers all five AI delivery tools |
| SendGrid | Email API | Twilio/SendGrid (external) | Delivers notification emails for tool outputs |

---

## 3. Data Flow

### Data Pipeline

1. An external actor (person, system, or upstream pipeline) uploads a `.csv` file to the `capco-data-landing-{env}` S3 bucket under the `raw/` prefix.
2. The S3 bucket notification fires an `s3:ObjectCreated:*` event to the `data-ingest-{env}` Lambda function.
3. Lambda reads `LANDING_BUCKET` from its environment variables and the object key from the event payload.
4. `data_pipeline.py` calls `get_s3_client()` — **⚠️ using hardcoded AWS credentials** (see Security section) — and downloads the CSV via `get_object`.
5. Each row is validated by `validate_customer_record()` against required fields (`customer_id`, `email`, `age`, `country_code`), email format, and age range.
6. Valid rows are collected into a DataFrame; failed rows are captured with their error messages.
7. The validated DataFrame is written as a Parquet file to the `raw/` → `processed/` key path (`.csv` → `.parquet`) in the same landing bucket.
   - **⚠️ Note:** Output is written back to the *landing* bucket, not the *processed* bucket — the `aws_s3_bucket.processed` resource in Terraform appears to be unused by the Lambda.
8. Lambda returns a JSON summary `{processed, failed, output_key, timestamp}` with HTTP status 200 or 500.

### AI Delivery Toolchain

9. A GitHub event (PR open, push to `main`, version tag, release branch creation, or schedule) triggers one of the five workflow YAML files.
10. The workflow checks out the source repository and installs `anthropic` and `requests` Python dependencies.
11. The corresponding tool script (`tool1_` through `tool5_`) calls `shared.get_repo_files()` or `shared.get_pr_diff()`, fetching source and IaC files from GitHub via the REST API using `GH_TOKEN`.
12. File content (truncated to 3,000–6,000 characters per file) is assembled into a prompt and sent to the Claude API (`claude-sonnet-4-6`) via `shared.call_claude()`.
13. Claude's response (JSON, Markdown, CSV, or structured text depending on the tool) is parsed and formatted by the tool script.
14. Formatted output is committed to the `ai-delivery-outputs` repository via `shared.write_output_file()` (GitHub Contents API PUT).
15. Tool 1 additionally posts a review comment directly on the PR via `shared.post_pr_comment()`.
16. A notification email is sent to `kylo.deng@capco.com` via SendGrid with a summary and link to the output.
17. An audit log entry is written to the `ai-delivery-outputs` repository.
18. For Tool 1, a JSON artefact is uploaded to the GitHub Actions run via `actions/upload-artifact`.

---

## 4. Security Posture

### ✅ What is secured

- GitHub Actions secrets (`ANTHROPIC_API_KEY`, `GH_TOKEN`, `SENDGRID_API_KEY`) are stored in GitHub Secrets and injected as environment variables — not hardcoded in workflow YAML.
- Tool 5 UAT workflow has a branch guard (`startsWith(github.ref, 'refs/heads/release/')`) to prevent spurious `create` event triggers.
- Lambda is invoked only via S3 event notification, not exposed directly to the internet.
- S3 event notification is filtered to `raw/*.csv` prefix/suffix, limiting Lambda trigger surface.

### ❌ Gaps and explicit security failures

| Issue | Severity | Detail |
|---|---|---|
| **Hardcoded AWS credentials in source code** | CRITICAL | `src/data_pipeline.py` lines 9–10 contain a literal `AWS_ACCESS_KEY` and `AWS_SECRET_KEY`. These will be committed to version history and are likely already leaked. Must be rotated immediately and replaced with the Lambda execution role (remove `get_s3_client()` credential parameters entirely). |
| **Hardcoded `DB_PASSWORD` in Terraform** | CRITICAL | `infra/main.tf` Lambda environment variable `DB_PASSWORD = "SuperSecret123!"` is plaintext in IaC. Must be moved to AWS Secrets Manager or SSM Parameter Store with a `SecretString` reference. |
| **Overly broad IAM policy (`s3:*` on `*`)** | HIGH | `aws_iam_role_policy.lambda_policy` grants full S3 access to every bucket in the account. Scope must be restricted to `s3:GetObject` on the landing bucket ARN and `s3:PutObject` on the processed bucket ARN at minimum. |
| **S3 landing bucket has no encryption** | HIGH | `aws_s3_bucket.landing` has no `aws_s3_bucket_server_side_encryption_configuration` block. The bucket comment in Terraform even notes this explicitly. Customer PII (email, age, country code) is stored unencrypted at rest. |
| **S3 landing bucket has no public access block** | HIGH | No `aws_s3_bucket_public_access_block` resource is defined for the landing bucket. The bucket could be made public accidentally. |
| **S3 processed bucket has no encryption or access controls** | HIGH | Same issue as landing bucket — no SSE configuration, no public access block defined. |
| **No S3 bucket versioning or object lock** | MEDIUM | Neither bucket has versioning enabled, meaning accidental overwrites or deletes cannot be recovered. |
| **No VPC / network isolation for Lambda** | MEDIUM | Lambda runs in the default AWS network context with no VPC configuration, security groups, or private subnet placement. |
| **No Lambda dead-letter queue (DLQ)** | MEDIUM | Failed Lambda invocations are silently dropped. No DLQ or `on_failure` destination is configured. |
| **S3 list pagination not implemented** | MEDIUM | `get_all_pending_files()` calls `list_objects_v2` without handling `NextContinuationToken` — buckets with >1,000 objects will silently drop files. |
| **Bare `except Exception` in Lambda handler** | LOW | `lambda_handler` catches all exceptions generically, masking unexpected failure modes. |
| **No CSV input sanitisation beyond field presence** | LOW | There is no check for CSV injection characters or excessively large files that could cause memory exhaustion. |
| **`GH_TOKEN` scope** | [TODO: What permissions does the PAT/token have? It writes to a separate output repo — verify it is scoped to only that repo and has no `admin` or `delete` permissions.] | — |
| **No SAST or dependency scanning** | LOW | No `dependabot`, Snyk, or pip-audit runs are configured in any workflow. |
| **No Terraform remote state** | MEDIUM | `main.tf` has no `backend` block — state is local only, no locking, no team sharing, no encryption of state file. |

---

## 5. Environment Variables and Secrets

| Name | Required | Sensitivity | Where set |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | **High** — API key for paid LLM service | GitHub Actions Secret |
| `GH_TOKEN` | Yes | **High** — GitHub PAT with repo write access | GitHub Actions Secret |
| `SENDGRID_API_KEY` | Yes | **High** — email service API key | GitHub Actions Secret |
| `OUTPUT_REPO` | No | Low | Workflow `env` block (hardcoded: `ai-delivery-outputs`) |
| `OUTPUT_REPO_OWNER` | No | Low | Workflow `env` (resolved from `github.repository_owner`) |
| `NOTIFY_EMAIL` | No | Low | Workflow `env` (hardcoded: `kylo.deng@capco.com`) |
| `SENDER_EMAIL` | No | Low | Workflow `env` (hardcoded: `noreply@ai-delivery.capco.com`) |
| `SOURCE_REPO_OWNER` | No | Low | Workflow `env` (resolved from `github.repository_owner`) |
| `SOURCE_REPO_NAME` | No | Low | Workflow `env` (resolved from `github.event.repository.name`) |
| `GITHUB_RUN_URL` | No | Low | Workflow `env` (constructed from GitHub context) |
| `REVIEW_MODE` | No | Low | Set dynamically in workflow step |
| `PR_NUMBER` | No | Low | Set dynamically in workflow step |
| `RELEASE_VERSION` | No | Low | Set dynamically in workflow step |
| `PROJECT_NAME` | No | Low | Set dynamically in workflow step |
| `UAT_MODE` | No | Low | Set dynamically in workflow step |
| `USER_STORIES` | No | Low–Medium (may contain business requirements) | Workflow dispatch input, set in step |
| `UAT_RESULTS_PATH` | No | Low | Workflow dispatch input, set in step |
| `TEST_MODE` | No | Low | Workflow `env` (defaults to `generate`) |
| `LANDING_BUCKET` | Yes (Lambda) | Low | Lambda environment variable (set by Terraform) |
| `DB_PASSWORD` | Yes (Lambda) | **CRITICAL** — plaintext secret | **⚠️ Hardcoded in `infra/main.tf`** — must move to Secrets Manager |
| `AWS_ACCESS_KEY` | — | **CRITICAL** — AWS credential | **⚠️ Hardcoded in `src/data_pipeline.py`** — must be removed immediately |
| `AWS_SECRET_KEY` | — | **CRITICAL** — AWS credential | **⚠️ Hardcoded in `src/data_pipeline.py`** — must be removed immediately |

---

## 6. Dependencies

| Dependency | Type | Purpose | Notes |
|---|---|---|---|
| Anthropic Claude API (`claude-sonnet-4-6`) | External paid API | LLM inference for all five AI tools | Rate limits and costs apply; no retry logic observed in `shared.py` |
| SendGrid API | External paid API | Email delivery for tool output notifications | [TODO: Is the sender domain `ai-delivery.capco.com` verified in SendGrid?] |
| GitHub REST API (`api.github.com`) | External API | Fetching repo files, PR diffs, posting comments, writing output files | Requires `GH_TOKEN` PAT |
| `ai-delivery-outputs` (companion repo) | GitHub Repository | Stores all generated documents, test files, UAT packs, and audit logs | Must exist and be writable by `GH_TOKEN` before workflows run |
| `anthropic` (PyPI) | Python library | Python SDK for Claude API | Installed at runtime via `pip install anthropic` — no pinned version |
| `requests` (PyPI) | Python library | HTTP calls to GitHub and SendGrid APIs | Installed at runtime — no pinned version |
| `boto3` (PyPI) | Python library | AWS SDK used by `data_pipeline.py` | Not installed in Lambda layer via Terraform — assumed bundled in `lambda.zip` |
| `pandas` (PyPI) | Python library | CSV parsing and DataFrame operations in `data_pipeline.py` | Must be bundled in `lambda.zip` |
| `pyarrow` / `fastparquet` | Python library | Required by `pandas.to_parquet()` | Must be bundled in `lambda.zip`; not explicitly declared anywhere |
| GitHub Actions (`ubuntu-latest`) | CI runner | Executes all five workflow jobs | Runner image version is floating |
| `actions/checkout@v4` | GitHub Action | Checks out source repository | Pinned to major version only |
| `actions/setup-python@v5` | GitHub Action | Installs Python 3.12 | Pinned to major version only |
| `actions/upload-artifact@v4` | GitHub Action | Uploads code review JSON artefact | Tool 1 only |

---

## 7. Deployment Instructions

### Prerequisites

- AWS CLI configured with credentials that have sufficient IAM permissions to create S3 buckets, Lambda functions, and IAM roles.
- Terraform >= 1.0 installed.
- Python 3.12 installed locally.
- GitHub repository secrets configured: `ANTHROPIC_API_KEY`, `GH_TOKEN`, `SENDGRID_API_KEY`.
- `ai-delivery-outputs` GitHub repository created and accessible to `GH_TOKEN`.
- `lambda.zip` built and present at `infra/lambda.zip` before applying Terraform.

### Build the Lambda deployment package

```bash
# From repo root
pip install boto3 pandas pyarrow -t lambda_package/
cp src/data_pipeline.py lambda_package/
cd lambda_package && zip -r ../infra/lambda.zip . && cd ..
```

### Deploy infrastructure

```bash
cd infra

# Initialise Terraform (local state — no remote backend configured)
terraform init

# Review planned changes
terraform plan -var="environment=dev"

# Apply
terraform apply -var="environment=dev"
```

> **⚠️ Before applying:** Remove the hardcoded `DB_PASSWORD` from `main.tf` and replace with an SSM/Secrets Manager reference. Rotate the hardcoded credentials in `src/data_pipeline.py` before building `lambda.zip`.

### Trigger AI delivery tools manually

```bash
# Tool 1 — Code Review (repo-wide)
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

# Tool 4 — Auto Testing (generate mode)
gh workflow run tool4_auto_testing.yml