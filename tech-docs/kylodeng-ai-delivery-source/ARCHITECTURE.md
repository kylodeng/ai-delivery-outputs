# Architecture Document — kylodeng/ai-delivery-source

---

## 1. Overview

This system is an AI-powered software delivery automation platform built on GitHub Actions and AWS. It consists of two loosely coupled layers: (1) a set of five Claude-powered GitHub Actions workflows that automate code review, technical documentation, business documentation, test generation, and UAT facilitation for any source repository; and (2) an AWS data ingestion pipeline (Lambda + S3) that serves as the primary *target* workload those AI tools analyse. The AI tooling reads source and IaC files from the source repository, calls Anthropic's Claude API to generate structured outputs, writes artifacts to a separate `ai-delivery-outputs` GitHub repository, posts results as PR comments, and sends email notifications via SendGrid. The underlying data pipeline ingests customer CSV files from a landing S3 bucket, validates and transforms them, and writes Parquet output to a processed S3 bucket.

---

## 2. Resources Deployed

| Resource | Type | Cloud Provider | Purpose |
|---|---|---|---|
| `capco-data-landing-{env}` | S3 Bucket | AWS | Landing zone for raw customer CSV files |
| `capco-data-processed-{env}` | S3 Bucket | AWS | Stores transformed Parquet output files |
| `data-ingest-{env}` | Lambda Function (Python 3.12) | AWS | Validates and transforms CSV → Parquet on S3 trigger |
| `lambda-ingest-role` | IAM Role | AWS | Execution role assumed by the Lambda function |
| `lambda-s3-policy` | IAM Role Policy | AWS | Grants S3 permissions to the Lambda role |
| S3 Bucket Notification (`landing_trigger`) | S3 Event Notification | AWS | Triggers Lambda on `s3:ObjectCreated:*` for `raw/*.csv` |
| Tool 1 — Code Review | GitHub Actions Workflow | GitHub | AI-powered PR and repo code review via Claude |
| Tool 2 — Tech Documentation | GitHub Actions Workflow | GitHub | Generates README, architecture doc, and runbook |
| Tool 3 — Business Documentation | GitHub Actions Workflow | GitHub | Generates business solution overview and gap questionnaire |
| Tool 4 — Auto Testing | GitHub Actions Workflow | GitHub | Generates or gap-analyses test files via Claude |
| Tool 5 — UAT Facilitation | GitHub Actions Workflow | GitHub | Generates UAT test packs or analyses completed UAT results |
| `ai-delivery-outputs` | GitHub Repository | GitHub | Stores all AI-generated artifacts (docs, tests, reports) |
| Claude API (`claude-sonnet-4-6`) | External AI API | Anthropic | LLM inference for all five tools |
| SendGrid | Email API | Twilio/SendGrid | Sends notification emails on workflow completion |

---

## 3. Data Flow

### Data Pipeline (AWS)

1. An upstream process deposits a CSV file under the `raw/` prefix in the `capco-data-landing-{env}` S3 bucket.
2. The S3 event notification fires `s3:ObjectCreated:*` for any `.csv` file matching the `raw/` prefix filter.
3. AWS invokes the `data-ingest-{env}` Lambda function, passing the bucket name and object key in the event payload.
4. The Lambda function (`data_pipeline.lambda_handler`) calls `process_csv()`, which downloads the object from S3 using a hardcoded IAM key pair (see Security section).
5. The CSV is read into a pandas DataFrame. Each row is validated against required fields (`customer_id`, `email`, `age`, `country_code`); invalid rows are collected as failures.
6. The valid rows are written as Parquet back to the **same** landing bucket under the `processed/` prefix (e.g., `raw/file.csv` → `processed/file.parquet`). ⚠️ The output is not written to the `capco-data-processed-{env}` bucket despite that bucket being declared; the pipeline uses `s3://{landing_bucket}/{output_key}`.
7. The Lambda returns a JSON summary `{processed, failed, output_key, timestamp}` with HTTP status 200/500.

### AI Tooling (GitHub Actions)

8. A triggering event (PR open, push to main, version tag, release branch creation, schedule, or manual dispatch) starts the relevant workflow.
9. The workflow checks out the source repository and installs `anthropic` and `requests` via pip.
10. The Python script reads source files and/or IaC files from the GitHub REST API (`/repos/{owner}/{repo}/git/trees/HEAD`) using `GH_TOKEN`.
11. For PR-mode tools (Tool 1, Tool 4), the script fetches the PR unified diff via the GitHub API.
12. The script constructs a prompt and calls the Anthropic Claude API (`claude-sonnet-4-6`) with a system prompt and user content.
13. Claude's response (JSON or Markdown) is parsed and validated by the script.
14. Artifacts are written to the `ai-delivery-outputs` repository via GitHub Contents API (`PUT /repos/{owner}/{repo}/contents/{path}`).
15. For Tool 1, a review comment is posted directly to the source PR via the GitHub Issues Comments API.
16. A notification email is sent via the SendGrid API to `kylo.deng@capco.com`.
17. An audit log entry is written to the output repository.
18. For Tool 1, raw JSON artifacts are also uploaded as GitHub Actions run artifacts.

---

## 4. Security Posture

### ✅ What Is Secured

- **GitHub Actions secrets**: `ANTHROPIC_API_KEY`, `GH_TOKEN`, and `SENDGRID_API_KEY` are stored as GitHub encrypted secrets and injected as environment variables at runtime — they are not hardcoded in workflow YAML.
- **Lambda IAM trust policy**: Correctly scoped to `lambda.amazonaws.com` principal only.
- **S3 event trigger filtering**: Scoped to `raw/` prefix and `.csv` suffix, reducing unnecessary Lambda invocations.
- **GitHub Actions runner isolation**: Each job runs on a fresh `ubuntu-latest` ephemeral runner.

### ❌ Security Gaps and Risks

- **🔴 CRITICAL — Hardcoded AWS credentials in source code**: `src/data_pipeline.py` contains a plaintext `AWS_ACCESS_KEY` and `AWS_SECRET_KEY` (lines 12–13). Even though these appear to be example values, this pattern is dangerous and the code comment acknowledges it. These must be removed immediately and replaced with the Lambda execution role (IAM instance profile) or AWS Secrets Manager / SSM Parameter Store.
- **🔴 CRITICAL — Hardcoded database password in Terraform**: `infra/main.tf` sets `DB_PASSWORD = "SuperSecret123!"` as a plaintext Lambda environment variable. This is visible in Terraform state, the AWS console, and CloudTrail. Must be replaced with an SSM SecureString or Secrets Manager reference.
- **🔴 HIGH — Overly permissive IAM policy**: `lambda-s3-policy` grants `s3:*` on `Resource: "*"` — full S3 access to every bucket in the account. This violates the principle of least privilege. The policy should be scoped to `s3:GetObject` on the landing bucket ARN and `s3:PutObject` on the processed bucket ARN.
- **🔴 HIGH — S3 landing bucket has no encryption**: `aws_s3_bucket.landing` has no `aws_s3_bucket_server_side_encryption_configuration` resource. Customer data (PII: email, age, country) is stored unencrypted at rest.
- **🔴 HIGH — S3 landing bucket has no public access block**: No `aws_s3_bucket_public_access_block` resource is attached to the landing bucket. The bucket could be made public accidentally.
- **🟡 MEDIUM — S3 processed bucket has no encryption**: Same issue as the landing bucket — no SSE configuration.
- **🟡 MEDIUM — No S3 bucket versioning**: Neither bucket has versioning enabled, making accidental deletion or overwrite unrecoverable.
- **🟡 MEDIUM — No VPC / network isolation for Lambda**: The Lambda function runs in the default VPC (or no VPC) with no private subnet or security group constraints.
- **🟡 MEDIUM — No S3 access logging**: Neither bucket has server access logging enabled, making forensic investigation of data access impossible.
- **🟡 MEDIUM — GH_TOKEN scope unknown**: The `GH_TOKEN` secret is used to read source repos AND write to `ai-delivery-outputs`. The minimum required scopes (`repo` or `contents:write` + `pull_requests:write`) are not documented. If the token has org-wide write access, this is a significant blast radius.
- **🟡 MEDIUM — Claude receives full source file contents**: Raw source code including any secrets present in files is transmitted to Anthropic's external API. Any secrets accidentally left in source files would be exfiltrated.
- **🟠 LOW — No Lambda resource policy for S3 invocation**: There is no `aws_lambda_permission` resource granting S3 permission to invoke the Lambda. The notification will fail at runtime without it.
- **🟠 LOW — No pagination in `get_all_pending_files()`**: `list_objects_v2` is called without handling the `NextContinuationToken`, silently dropping files beyond the 1,000-object limit.
- **🟠 LOW — Bare except in lambda_handler**: `except Exception` swallows all error types, making debugging difficult and potentially masking security-relevant errors.

---

## 5. Environment Variables and Secrets

| Name | Required | Sensitivity | Where Set |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | 🔴 High — API key with billing implications | GitHub Actions secret |
| `GH_TOKEN` | Yes | 🔴 High — GitHub PAT with repo read/write | GitHub Actions secret |
| `SENDGRID_API_KEY` | Yes | 🔴 High — Email sending API key | GitHub Actions secret |
| `OUTPUT_REPO` | No | Low | Workflow `env` block (hardcoded: `ai-delivery-outputs`) |
| `OUTPUT_REPO_OWNER` | No | Low | Workflow `env` block (`github.repository_owner`) |
| `NOTIFY_EMAIL` | No | Low | Workflow `env` block (hardcoded: `kylo.deng@capco.com`) |
| `SENDER_EMAIL` | No | Low | Workflow `env` block (hardcoded: `noreply@ai-delivery.capco.com`) |
| `SOURCE_REPO_OWNER` | No | Low | Workflow `env` block |
| `SOURCE_REPO_NAME` | No | Low | Workflow `env` block |
| `GITHUB_RUN_URL` | No | Low | Workflow `env` block |
| `REVIEW_MODE` | No | Low | Set dynamically in workflow step |
| `PR_NUMBER` | No | Low | Set dynamically in workflow step |
| `TEST_MODE` | No | Low | Workflow `env` block / input |
| `UAT_MODE` | No | Low | Set dynamically in workflow step |
| `RELEASE_VERSION` | No | Low | Set dynamically from tag or input |
| `PROJECT_NAME` | No | Low | Set dynamically from tag or input |
| `USER_STORIES` | No | Low | Workflow dispatch input |
| `UAT_RESULTS_PATH` | No | Low | Workflow dispatch input |
| `LANDING_BUCKET` | Yes (Lambda) | Low | Terraform Lambda environment variable |
| `DB_PASSWORD` | Yes (Lambda) | 🔴 **CRITICAL — Plaintext secret** | Terraform Lambda environment variable — **must be moved to Secrets Manager** |
| `AWS_ACCESS_KEY` *(in source)* | — | 🔴 **CRITICAL — Hardcoded credential** | `src/data_pipeline.py` — **must be removed** |
| `AWS_SECRET_KEY` *(in source)* | — | 🔴 **CRITICAL — Hardcoded credential** | `src/data_pipeline.py` — **must be removed** |
| `aws_region` | No | Low | Terraform variable (default: `us-east-1`) |
| `environment` | No | Low | Terraform variable (default: `dev`) |

---

## 6. Dependencies

| Dependency | Type | Purpose | Notes |
|---|---|---|---|
| Anthropic Claude API (`claude-sonnet-4-6`) | External SaaS API | LLM inference for all five tools | Paid API; rate limits apply; data is sent externally |
| SendGrid API | External SaaS API | Email notifications on workflow completion | Requires verified sender domain `ai-delivery.capco.com` |
| GitHub REST API v3 | External API | Read source files, post PR comments, write output artifacts | Requires PAT (`GH_TOKEN`) with appropriate scopes |
| `ai-delivery-outputs` GitHub repo | External GitHub Repository | Stores all generated documentation, tests, and reports | Must exist and be writable by `GH_TOKEN` before workflows run |
| `anthropic` (PyPI) | Python Package | Claude API client | Installed at runtime via pip |
| `requests` (PyPI) | Python Package | HTTP calls to GitHub and SendGrid APIs | Installed at runtime via pip |
| `boto3` | Python Package | AWS SDK for S3 operations in data pipeline | Must be available in Lambda runtime (Python 3.12 includes it) |
| `pandas` | Python Package | CSV parsing and DataFrame transformation | **Not bundled in Lambda runtime** — must be in `lambda.zip` |
| `pyarrow` / `fastparquet` | Python Package | Parquet serialisation via `to_parquet()` | **Not bundled in Lambda runtime** — must be in `lambda.zip` |
| AWS S3 | AWS Service | Landing and processed data storage | `us-east-1`, environment-suffixed bucket names |
| AWS Lambda | AWS Service | Serverless compute for data pipeline | Python 3.12 runtime |
| GitHub Actions | CI/CD Platform | Workflow orchestration for all five tools | `ubuntu-latest` runners |

---

## 7. Deployment Instructions

### Prerequisites

```bash
# Required tools
terraform >= 1.5
aws-cli >= 2.x (configured with credentials for target account)
# GitHub: create secrets ANTHROPIC_API_KEY, GH_TOKEN, SENDGRID_API_KEY in repo settings
# GitHub: ensure 'ai-delivery-outputs' repository exists and GH_TOKEN has write access to it
```

### AWS Infrastructure

```bash
# 1. Package the Lambda deployment artifact
#    pandas and pyarrow must be included — they are not in the Lambda runtime
pip install pandas pyarrow boto3 --target ./lambda_package
cp src/data_pipeline.py ./lambda_package/
cd lambda_package && zip -r ../lambda.zip . && cd ..

# 2. Initialise Terraform
cd infra
terraform init

# 3. Plan — review output carefully before applying
terraform plan -var="environment=dev" -var="aws_region=us-east-1"

# 4. Apply
terraform apply -var="environment=dev" -var="aws_region=us-east-1"

# 5. Note outputs
terraform output landing_bucket
terraform output processed_bucket
```

### GitHub Actions Workflows

```bash
# Workflows are triggered automatically. To trigger manually:

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
gh workflow run tool4_auto_testing.yml \
  -f test_mode=generate

# Tool 4 — Auto Testing (gap analysis mode)
gh workflow run tool4_auto_testing.yml \
  -f test_mode=gap-analysis

# Tool 5 — UAT (generate test pack)
gh workflow run tool5_uat.yml \
  -f uat_mode=generate \
  -f release_version="1.0.0" \
  -f user_stories="As a user I want..."

# Tool 5 — UAT (analyse results)
gh workflow run tool5_uat.yml \
  