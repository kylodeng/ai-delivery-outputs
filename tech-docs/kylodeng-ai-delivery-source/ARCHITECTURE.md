# Architecture Document — kylodeng/ai-delivery-source

---

## 1. Overview

This repository implements an **AI-powered software delivery toolkit** comprising two distinct subsystems. The first is a set of five GitHub Actions–based AI workflows ("tools") that leverage Anthropic's Claude LLM to automate code review, technical documentation generation, business documentation generation, automated test generation, and UAT facilitation across any source repository. The second is a sample AWS data ingestion pipeline (the subject repository's own application) that ingests customer CSV files from an S3 landing bucket, validates and transforms them using a Python Lambda function, and writes the results as Parquet files to a processed S3 bucket. The AI workflows read source and IaC files from the triggering repository, call Claude for analysis, persist outputs to a dedicated `ai-delivery-outputs` GitHub repository, and optionally send email notifications via SendGrid.

---

## 2. Resources Deployed

| Resource | Type | Cloud Provider | Purpose |
|---|---|---|---|
| `capco-data-landing-<env>` | S3 Bucket | AWS | Landing zone for raw customer CSV files |
| `capco-data-processed-<env>` | S3 Bucket | AWS | Stores processed Parquet output files |
| `data-ingest-<env>` | Lambda Function (Python 3.12) | AWS | Validates, transforms, and routes CSV→Parquet |
| `lambda-ingest-role` | IAM Role | AWS | Execution role assumed by the Lambda function |
| `lambda-s3-policy` | IAM Role Policy | AWS | Grants S3 permissions to Lambda (see Security section) |
| S3 Bucket Notification (landing) | S3 Event Trigger | AWS | Fires Lambda on `s3:ObjectCreated:*` under `raw/*.csv` |
| Tool 1 — Code Review | GitHub Actions Workflow | GitHub | AI-driven PR and repo-wide code review |
| Tool 2 — Tech Documentation | GitHub Actions Workflow | GitHub | Auto-generates README, ARCHITECTURE, and RUNBOOK docs |
| Tool 3 — Business Documentation | GitHub Actions Workflow | GitHub | Auto-generates stakeholder-facing solution overview |
| Tool 4 — Auto Testing | GitHub Actions Workflow | GitHub | AI-generates or gap-analyses test suites |
| Tool 5 — UAT Facilitation | GitHub Actions Workflow | GitHub | Generates UAT test packs and analyses completed results |
| `ai-delivery-outputs` | GitHub Repository | GitHub | Centralised store for all AI-generated output artefacts |
| Claude API (`claude-sonnet-4-6`) | External LLM API | Anthropic | Provides AI inference for all five tools |
| SendGrid | Email Delivery API | Twilio/SendGrid | Sends notification emails on workflow completion |

---

## 3. Data Flow

### 3a. AWS Data Ingestion Pipeline

1. An external producer uploads a `.csv` file to `s3://capco-data-landing-<env>/raw/`.
2. The S3 bucket notification triggers the `data-ingest-<env>` Lambda function, passing bucket name and object key in the event payload.
3. `lambda_handler` in `data_pipeline.py` receives the event, resolves the bucket name from the event or the `LANDING_BUCKET` environment variable, and calls `process_csv()`.
4. `process_csv()` instantiates a Boto3 S3 client (using hardcoded credentials — see Security section) and calls `get_object` to download the CSV into memory as a Pandas DataFrame.
5. Each row is passed to `validate_customer_record()`, which checks for required fields (`customer_id`, `email`, `age`, `country_code`) and basic value rules. Valid rows are collected; invalid rows are logged with their error messages.
6. The validated DataFrame is serialised to Parquet and written directly back to `s3://capco-data-landing-<env>/processed/<original-key>.parquet` (note: both landing and processed writes go to the *same* bucket due to the current implementation; see Risks).
7. The Lambda returns a JSON summary (`processed`, `failed`, `output_key`, `timestamp`) with HTTP status 200, or 500 on failure.

### 3b. AI Delivery Workflows

1. A GitHub event (PR open, push to `main`, version tag, release branch creation, or scheduled cron) triggers the relevant workflow.
2. The runner checks out the source repository with full history (`fetch-depth: 0`).
3. Python dependencies (`anthropic`, `requests`) are installed on the ephemeral runner.
4. The appropriate tool script (`tool1_`–`tool5_`) is invoked; it calls `shared.py` helpers to fetch repository file contents or PR diffs via the GitHub REST API (authenticated with `GH_TOKEN`).
5. The fetched content is assembled into a structured prompt and sent to the Anthropic Claude API (`claude-sonnet-4-6`) via the `anthropic` Python SDK.
6. The Claude response (Markdown documents or JSON findings) is parsed and validated.
7. Outputs are committed to the `ai-delivery-outputs` repository via an authenticated GitHub API PUT (`write_output_file()`), under a path structured as `<doc-type>/<owner>-<repo>/`.
8. For Tool 1 (code review), a summary comment is additionally posted directly to the pull request via the GitHub Issues Comments API.
9. An email notification (HTML) is dispatched to `kylo.deng@capco.com` via the SendGrid API.
10. Raw JSON artefacts are uploaded as GitHub Actions run artefacts (Tool 1 only).

---

## 4. Security Posture

### ✅ What Is Secured

- **GitHub Secrets**: `ANTHROPIC_API_KEY`, `GH_TOKEN`, and `SENDGRID_API_KEY` are stored as GitHub Actions secrets and injected as environment variables; they are not hardcoded in workflow YAML.
- **Lambda IAM Trust Policy**: The `assume_role_policy` is correctly scoped to `lambda.amazonaws.com` only — no cross-account or wildcard principal.
- **S3 Trigger Filter**: The bucket notification is scoped to the `raw/` prefix and `.csv` suffix, reducing trigger noise.
- **Workflow Conditions (Tool 5)**: UAT workflow correctly guards `create` events to `refs/heads/release/*` branches only.
- **PR Number Injection**: Tool 1 reads `pr_number` from the GitHub event context, not from user-supplied free text in the critical code path.

### ❌ Gaps and Issues

| Gap | Severity | Detail |
|---|---|---|
| **Hardcoded AWS credentials in source code** | CRITICAL | `AWS_ACCESS_KEY` and `AWS_SECRET_KEY` are hardcoded strings in `src/data_pipeline.py`. Even if they are example values, this pattern must not reach production. The code's own `# TODO` acknowledges this. |
| **Hardcoded DB password in Lambda environment variable** | CRITICAL | `DB_PASSWORD = "SuperSecret123!"` is set as a plaintext Lambda environment variable in `infra/main.tf`. This value will be stored in the Terraform state file and visible in the AWS console. Must be replaced with AWS Secrets Manager or SSM Parameter Store (SecureString). |
| **S3 landing bucket has no encryption** | HIGH | `aws_s3_bucket.landing` has no `aws_s3_bucket_server_side_encryption_configuration` block. The Terraform code comment explicitly notes "NO encryption". All customer PII CSV data is at rest unencrypted. |
| **S3 landing bucket has no public access block** | HIGH | No `aws_s3_bucket_public_access_block` resource is defined for the landing bucket. The bucket could be made public accidentally. |
| **Overly broad IAM policy — `s3:*` on `*`** | HIGH | `lambda-ingest-role` is granted `s3:*` on `Resource: "*"`, meaning the Lambda can read, write, delete, and modify ACLs on *any* S3 bucket in the account. Should be scoped to `arn:aws:s3:::capco-data-landing-<env>/*` and `arn:aws:s3:::capco-data-processed-<env>/*` with only the required actions (`s3:GetObject`, `s3:PutObject`). |
| **Processed output written to landing bucket** | MEDIUM | `process_csv()` replaces `raw/` with `processed/` in the key but writes back to the same `bucket` variable (the landing bucket). The `aws_s3_bucket.processed` resource is never referenced in code. |
| **No S3 bucket versioning** | MEDIUM | Neither S3 bucket has versioning enabled, so accidental overwrites or deletions are unrecoverable. |
| **No VPC / network isolation for Lambda** | MEDIUM | The Lambda is not deployed inside a VPC; if it ever needs to reach a private database, this will require rework. |
| **`GH_TOKEN` scope unknown** | MEDIUM | The `GH_TOKEN` secret needs write access to the `ai-delivery-outputs` repo and read access to source repos. If this is a Personal Access Token with broad `repo` scope, it represents a significant blast radius. [TODO: confirm token scope and consider using a fine-grained PAT or GitHub App] |
| **No encryption in transit validation** | LOW | Boto3 and the `requests` library default to HTTPS, but this is not explicitly enforced or verified in the code. |
| **No S3 access logging** | LOW | Neither bucket has server access logging enabled; there is no audit trail of object access. |
| **No Lambda resource policy for S3 trigger** | LOW | Terraform does not define an `aws_lambda_permission` resource allowing S3 to invoke the Lambda. The notification may fail or require manual console intervention. [TODO: add `aws_lambda_permission` resource] |
| **No pagination in `get_all_pending_files()`** | LOW | `list_objects_v2` returns a maximum of 1,000 keys. Files beyond that will be silently missed. |

---

## 5. Environment Variables and Secrets

| Name | Required | Sensitivity | Where Set |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | **High** — LLM API key with usage/billing impact | GitHub Actions secret (`secrets.ANTHROPIC_API_KEY`) |
| `GH_TOKEN` | Yes | **High** — GitHub token with repo read/write access | GitHub Actions secret (`secrets.GH_TOKEN`) |
| `SENDGRID_API_KEY` | Yes | **High** — Email delivery API key | GitHub Actions secret (`secrets.SENDGRID_API_KEY`) |
| `OUTPUT_REPO` | No | Low | Workflow `env` block; defaults to `ai-delivery-outputs` |
| `OUTPUT_REPO_OWNER` | No | Low | Workflow `env` block; defaults to `github.repository_owner` |
| `NOTIFY_EMAIL` | No | Low | Workflow `env` block; hardcoded to `kylo.deng@capco.com` |
| `SENDER_EMAIL` | No | Low | Workflow `env` block; hardcoded to `noreply@ai-delivery.capco.com` |
| `SOURCE_REPO_OWNER` | No | Low | Workflow `env` block; set from `github.repository_owner` |
| `SOURCE_REPO_NAME` | No | Low | Workflow `env` block; set from `github.event.repository.name` |
| `GITHUB_RUN_URL` | No | Low | Workflow `env` block; constructed from GitHub context |
| `REVIEW_MODE` | No | Low | Set at runtime in workflow step; values: `repo` or `pr` |
| `PR_NUMBER` | Conditional | Low | Set at runtime when `REVIEW_MODE=pr` |
| `RELEASE_VERSION` | Conditional | Low | Set at runtime for Tools 3 and 5; from tag or input |
| `PROJECT_NAME` | Conditional | Low | Set at runtime for Tool 3; from input or repo name |
| `UAT_MODE` | Conditional | Low | Set at runtime for Tool 5; values: `generate` or `analyse` |
| `TEST_MODE` | No | Low | Workflow `env` block for Tool 4; values: `generate` or `gap-analysis` |
| `LANDING_BUCKET` | Yes (Lambda) | Low | Lambda environment variable set by Terraform |
| `DB_PASSWORD` | Yes (Lambda) | **Critical** — plaintext database password | ⚠️ Hardcoded in `infra/main.tf` Lambda env block — must move to Secrets Manager |
| `AWS_ACCESS_KEY` | N/A | **Critical** — AWS credential | ⚠️ Hardcoded in `src/data_pipeline.py` — must be removed immediately |
| `AWS_SECRET_KEY` | N/A | **Critical** — AWS credential | ⚠️ Hardcoded in `src/data_pipeline.py` — must be removed immediately |

---

## 6. Dependencies

| Dependency | Type | Purpose | Version / Notes |
|---|---|---|---|
| Anthropic Claude API | External SaaS API | LLM inference for all five tools | Model: `claude-sonnet-4-6`; billed per token |
| SendGrid API | External SaaS API | Transactional email notifications | [TODO: confirm SendGrid account and sender domain verification for `ai-delivery.capco.com`] |
| GitHub REST API (`api.github.com`) | External API | Fetch repo files, post PR comments, write output files | API version `2022-11-28`; requires `GH_TOKEN` |
| `kylodeng/ai-delivery-outputs` | GitHub Repository | Stores all generated documentation artefacts | Must exist and be writable by `GH_TOKEN` |
| `anthropic` Python package | PyPI Library | Anthropic SDK | Installed at runtime via `pip install anthropic` — no pinned version |
| `requests` Python package | PyPI Library | GitHub and SendGrid HTTP calls | Installed at runtime via `pip install requests` — no pinned version |
| `boto3` | PyPI Library | AWS SDK for S3 operations in the data pipeline | [TODO: confirm version; not in a requirements.txt] |
| `pandas` | PyPI Library | CSV parsing and DataFrame operations | [TODO: confirm version; not in a requirements.txt] |
| AWS (`us-east-1`) | Cloud Provider | Lambda, S3, IAM | Terraform AWS provider `~> 5.0` |
| `hashicorp/aws` Terraform provider | IaC Provider | AWS resource provisioning | `~> 5.0` |

---

## 7. Deployment Instructions

### Prerequisites

- Terraform >= 1.0 installed locally
- AWS CLI configured with credentials that have IAM, S3, and Lambda permissions
- A `lambda.zip` artefact containing `data_pipeline.py` and its dependencies, placed in the `infra/` directory
- GitHub repository secrets set: `ANTHROPIC_API_KEY`, `GH_TOKEN`, `SENDGRID_API_KEY`
- The `ai-delivery-outputs` repository must exist under the same GitHub owner

### Deploy AWS Infrastructure

```bash
# Navigate to the IaC directory
cd infra/

# Initialise Terraform and download providers
terraform init

# Review the execution plan — inspect IAM and S3 changes carefully
terraform plan -var="environment=dev"

# Apply infrastructure changes
terraform apply -var="environment=dev"

# To deploy to a different environment (e.g. prod)
terraform apply -var="environment=prod"
```

### Package and Deploy Lambda

```bash
# From the repository root — package the Lambda source
cd src/
pip install boto3 pandas -t ./package/
cp data_pipeline.py ./package/
cd package/
zip -r ../../infra/lambda.zip .

# Re-run Terraform apply to update the Lambda function code
cd ../../infra/
terraform apply -var="environment=dev"
```

### Trigger AI Workflows Manually

```bash
# Tool 1 — Code Review (repo-wide scan)
gh workflow run tool1_code_review.yml -f review_mode=repo

# Tool 1 — Code Review (specific PR)
gh workflow run tool1_code_review.yml -f review_mode=pr -f pr_number=42

# Tool 2 — Tech Documentation
gh workflow run tool2_tech_docs.yml

# Tool 3 — Business Documentation
gh workflow run tool3_business_docs.yml \
  -f project_name="Data Ingestion Pipeline" \
  -f release_version="1.0.0"

# Tool 4 — Auto Testing (generate mode)
gh workflow run tool4_auto_testing.yml -f test_mode=generate

# Tool 4 — Auto Testing (