# Architecture Document — kylodeng/ai-delivery-source

---

## 1. Overview

This repository implements an **AI-powered software delivery accelerator** consisting of two distinct subsystems. The first is a set of five GitHub Actions–based CI/CD workflow tools that leverage Anthropic's Claude AI model to automate code review, technical documentation generation, business documentation generation, automated test generation, and UAT facilitation — all outputs are written to a companion repository (`ai-delivery-outputs`) and notifications are sent via SendGrid email. The second is an AWS-hosted **data ingestion pipeline** (infrastructure defined in Terraform) that accepts raw customer CSV files dropped into an S3 landing bucket, validates and transforms them via an AWS Lambda function, and writes the resulting Parquet files to a processed S3 bucket — this pipeline represents the primary *subject* codebase that the five AI delivery tools operate against.

---

## 2. Resources Deployed

| Resource | Type | Cloud Provider | Purpose |
|---|---|---|---|
| `capco-data-landing-{env}` | S3 Bucket | AWS | Receives raw customer CSV files uploaded to `raw/` prefix |
| `capco-data-processed-{env}` | S3 Bucket | AWS | Stores validated and transformed Parquet output files |
| `data-ingest-{env}` | Lambda Function (Python 3.12) | AWS | Validates, transforms, and moves data from landing to processed bucket |
| `lambda-ingest-role` | IAM Role | AWS | Execution role assumed by the Lambda function |
| `lambda-s3-policy` | IAM Role Policy | AWS | Grants Lambda permissions to access S3 (see Security section) |
| S3 Bucket Notification | Event Trigger | AWS | Fires Lambda on `s3:ObjectCreated:*` for `raw/*.csv` objects |
| GitHub Actions Runner (`ubuntu-latest`) | Ephemeral CI Runner | GitHub (SaaS) | Executes all five AI workflow tools |
| `ai-delivery-outputs` | GitHub Repository | GitHub (SaaS) | Persistent store for all AI-generated documentation, test files, and UAT packs |
| Claude (`claude-sonnet-4-6`) | Managed LLM API | Anthropic (SaaS) | AI inference for all five tools |
| SendGrid | Transactional Email API | Twilio/SendGrid (SaaS) | Delivers notification emails to `kylo.deng@capco.com` |

---

## 3. Data Flow

### 3a — Data Ingestion Pipeline (AWS)

1. An external system or user uploads a `.csv` file to the `capco-data-landing-{env}` S3 bucket under the `raw/` prefix.
2. The S3 bucket notification triggers the `data-ingest-{env}` Lambda function, passing the bucket name and object key in the event payload.
3. Lambda (`lambda_handler`) calls `process_csv()`, which invokes `get_s3_client()` using **hardcoded AWS credentials** in `data_pipeline.py` (see Security section).
4. Lambda downloads the CSV file via `s3.get_object()` and loads it into a Pandas DataFrame.
5. Each row is validated by `validate_customer_record()` — checking required fields, email format, and age range. Valid rows are collected; invalid rows are logged with their error.
6. Valid rows are converted to a Parquet file and written back to the **same landing bucket** under the `processed/` prefix (note: output goes to the landing bucket, not the processed bucket, due to a code bug — see Risks).
7. Lambda returns a JSON response with counts of processed and failed rows, the output key, and a timestamp.

### 3b — AI Delivery Workflows (GitHub Actions)

1. A workflow is triggered by a GitHub event (PR open, push to `main`, version tag, release branch creation, or cron schedule) or manual `workflow_dispatch`.
2. The GitHub Actions runner checks out the source repository and installs `anthropic` and `requests` Python dependencies.
3. The relevant tool script (`tool1_`–`tool5_`) calls `shared.py` utilities to fetch source or IaC files from the source repository via the GitHub API (authenticated with `GH_TOKEN`), or fetches the PR diff.
4. The collected file contents are assembled into a prompt and sent to the Anthropic Claude API (`claude-sonnet-4-6`) via the `call_claude()` function using `ANTHROPIC_API_KEY`.
5. Claude returns a structured response (JSON for code review / test gap analysis / UAT analysis; Markdown for documentation).
6. The tool script writes the output file(s) to the `ai-delivery-outputs` GitHub repository via the GitHub Contents API (`write_output_file()`).
7. A notification email is sent via SendGrid (`send_email()`) to `kylo.deng@capco.com` with a summary and link to the output.
8. For Tool 1 (Code Review), a review comment is also posted directly to the PR via the GitHub Issues API (`post_pr_comment()`).
9. For Tool 1, the raw JSON review artifact is uploaded to GitHub Actions Artifacts for the run.

---

## 4. Security Posture

### ✅ What is secured

- **GitHub Secrets**: `ANTHROPIC_API_KEY`, `GH_TOKEN`, and `SENDGRID_API_KEY` are stored as GitHub Actions secrets and injected as environment variables at runtime — not committed in plaintext to workflow YAML.
- **Lambda IAM Trust Policy**: Correctly scoped to `lambda.amazonaws.com` as the only principal that can assume the role.
- **PR Filter on Tool 5 (UAT)**: Workflow conditionally runs only on `release/` branches or manual dispatch, preventing accidental execution.
- **Tool 4 (Auto Testing)**: Explicitly instructs Claude to use mocks/stubs and never make real API calls in generated tests.

### ❌ Security gaps and vulnerabilities

- **CRITICAL — Hardcoded AWS credentials in source code**: `data_pipeline.py` contains a hardcoded `AWS_ACCESS_KEY` and `AWS_SECRET_KEY`. These must be removed immediately and rotated. The code even has a `# TODO: move this to secrets manager` comment acknowledging this.
- **CRITICAL — Hardcoded `DB_PASSWORD` in Terraform**: `infra/main.tf` sets `DB_PASSWORD = "SuperSecret123!"` as a plain-text Lambda environment variable. This should be stored in AWS Secrets Manager or SSM Parameter Store and retrieved at runtime.
- **HIGH — No S3 encryption on the landing bucket**: The `aws_s3_bucket.landing` resource has no `aws_s3_bucket_server_side_encryption_configuration` block. Customer CSV data is stored unencrypted at rest.
- **HIGH — No S3 Public Access Block on either bucket**: Neither bucket has `aws_s3_bucket_public_access_block` configured, meaning buckets could be made public by policy or ACL.
- **HIGH — Overly permissive IAM policy**: The `lambda-s3-policy` grants `s3:*` on `Resource: "*"`, giving Lambda full S3 access to every bucket in the account. This violates the principle of least privilege. It should be scoped to specific bucket ARNs and only the required actions (`s3:GetObject`, `s3:PutObject`, `s3:ListBucket`).
- **HIGH — No encryption on the processed S3 bucket**: `aws_s3_bucket.processed` also lacks encryption configuration.
- **MEDIUM — No S3 versioning**: Neither bucket has versioning enabled; accidental overwrites or deletions cannot be recovered.
- **MEDIUM — No bucket lifecycle policies**: No TTL or transition rules for landing or processed data.
- **MEDIUM — No VPC configuration for Lambda**: Lambda runs in the default public AWS network context with no VPC, subnet, or security group constraints.
- **MEDIUM — GitHub token scope unknown**: [TODO: What permissions does `GH_TOKEN` have? It writes to `ai-delivery-outputs` and reads source repos — confirm it is scoped to only necessary repository permissions and is not a classic PAT with broad org access.]
- **MEDIUM — No input sanitization on `user_stories` workflow input**: Tool 5 passes `inputs.user_stories` directly as an environment variable without sanitization; a malicious value could inject environment content.
- **LOW — No HTTPS enforcement on S3 buckets**: No bucket policy denying `aws:SecureTransport: false` requests.
- **LOW — Claude prompt injection risk**: User-controlled content (PR diffs, repository files, user stories) is passed directly into Claude prompts. A malicious commit could attempt to manipulate Claude's output.
- **LOW — No S3 access logging**: Neither bucket has access logging enabled.

---

## 5. Environment Variables and Secrets

| Name | Required | Sensitivity | Where set |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | **Secret** — API key for Claude | GitHub Actions Secret → env |
| `GH_TOKEN` | Yes | **Secret** — GitHub PAT for API access and writes to output repo | GitHub Actions Secret → env |
| `SENDGRID_API_KEY` | Yes | **Secret** — SendGrid transactional email key | GitHub Actions Secret → env |
| `OUTPUT_REPO` | No | Low | Workflow `env` block (hardcoded: `ai-delivery-outputs`) |
| `OUTPUT_REPO_OWNER` | No | Low | Workflow `env` block (derived from `github.repository_owner`) |
| `NOTIFY_EMAIL` | No | Low | Workflow `env` block (hardcoded: `kylo.deng@capco.com`) |
| `SENDER_EMAIL` | No | Low | Workflow `env` block (hardcoded: `noreply@ai-delivery.capco.com`) |
| `SOURCE_REPO_OWNER` | No | Low | Workflow `env` block (derived from `github.repository_owner`) |
| `SOURCE_REPO_NAME` | No | Low | Workflow `env` block (derived from event context) |
| `GITHUB_RUN_URL` | No | Low | Workflow `env` block (derived from GitHub context) |
| `REVIEW_MODE` | No (Tool 1) | Low | Set at runtime by workflow step |
| `PR_NUMBER` | No (Tool 1) | Low | Set at runtime by workflow step |
| `RELEASE_VERSION` | No (Tools 3, 5) | Low | Set at runtime by workflow step |
| `PROJECT_NAME` | No (Tool 3) | Low | Set at runtime by workflow step or input |
| `TEST_MODE` | No (Tool 4) | Low | Workflow `env` block (derived from input or default `generate`) |
| `UAT_MODE` | No (Tool 5) | Low | Set at runtime by workflow step |
| `USER_STORIES` | No (Tool 5) | Low-Medium | Set at runtime from `workflow_dispatch` input |
| `UAT_RESULTS_PATH` | No (Tool 5) | Low | Set at runtime from `workflow_dispatch` input |
| `LANDING_BUCKET` | Yes (Lambda) | Low | Terraform Lambda environment variable |
| `DB_PASSWORD` | Yes (Lambda) | **CRITICAL — Secret** | **⚠️ Hardcoded in `infra/main.tf`** — must be moved to Secrets Manager |
| `AWS_ACCESS_KEY` | — | **CRITICAL — Secret** | **⚠️ Hardcoded in `src/data_pipeline.py`** — must be removed immediately |
| `AWS_SECRET_KEY` | — | **CRITICAL — Secret** | **⚠️ Hardcoded in `src/data_pipeline.py`** — must be removed immediately |

---

## 6. Dependencies

| Dependency | Type | Purpose | Notes |
|---|---|---|---|
| Anthropic Claude API (`claude-sonnet-4-6`) | External SaaS API | AI inference for all five workflow tools | Billed per token; no rate-limit handling in code |
| GitHub API (`api.github.com`) | External SaaS API | Read source files, write to output repo, post PR comments | Requires `GH_TOKEN` |
| SendGrid API | External SaaS API | Send notification emails | Requires `SENDGRID_API_KEY`; `send_email()` referenced in scripts but implementation truncated in `shared.py` |
| `ai-delivery-outputs` (GitHub repo) | Sibling repository | Stores all AI-generated artefacts | Must exist and be writable by `GH_TOKEN` |
| AWS S3 | AWS Managed Service | Landing and processed data storage | Two buckets provisioned via Terraform |
| AWS Lambda | AWS Managed Service | Serverless compute for data pipeline | Python 3.12 runtime |
| AWS IAM | AWS Managed Service | Execution role for Lambda | |
| `anthropic` (PyPI) | Python library | Claude API client | Installed at runtime via `pip install anthropic` |
| `requests` (PyPI) | Python library | HTTP client for GitHub and SendGrid APIs | Installed at runtime via `pip install requests` |
| `boto3` (PyPI) | Python library | AWS SDK for S3 access in Lambda | [TODO: Is boto3 bundled in `lambda.zip` or provided by the Lambda runtime? Python 3.12 Lambda runtime includes boto3 by default] |
| `pandas` (PyPI) | Python library | CSV parsing and Parquet conversion in Lambda | [TODO: Is pandas included in `lambda.zip`? It is not a Lambda built-in and must be packaged] |
| `pyarrow` or `fastparquet` (PyPI) | Python library | Required by pandas for `.to_parquet()` | [TODO: Confirm which Parquet engine is bundled in `lambda.zip`] |

---

## 7. Deployment Instructions

### 7a — AWS Infrastructure (Terraform)

```bash
# Prerequisites: AWS CLI configured with credentials for the target account
# and Terraform >= 1.x installed

cd infra/

# Initialise Terraform (downloads AWS provider ~5.x)
terraform init

# Review the execution plan
terraform plan -var="environment=dev"

# Apply (deploys S3 buckets, Lambda, IAM role, and S3 event trigger)
terraform apply -var="environment=dev"

# For production deployment
terraform apply -var="environment=prod"

# To destroy all resources
terraform destroy -var="environment=dev"
```

> **⚠️ Pre-deployment requirement**: The Lambda function requires a deployment package at `infra/lambda.zip`. [TODO: Document how `lambda.zip` is built — is there a build script that packages `src/data_pipeline.py` with pandas and pyarrow?]

```bash
# Example lambda.zip build (assumed — not present in repo):
pip install pandas pyarrow -t package/
cp src/data_pipeline.py package/
cd package && zip -r ../infra/lambda.zip . && cd ..
```

### 7b — GitHub Actions Workflows

No deployment steps are required for the GitHub Actions workflows themselves — they are automatically registered by GitHub upon being committed to `.github/workflows/`. However, the following secrets **must** be configured in the repository settings before any workflow will succeed:

```
# Navigate to: GitHub Repo → Settings → Secrets and variables → Actions → New repository secret

ANTHROPIC_API_KEY   = <your Anthropic API key>
GH_TOKEN            = <GitHub PAT with repo read/write scope for both repos>
SENDGRID_API_KEY    = <your SendGrid API key>
```

The `ai-delivery-outputs` repository must also exist under the same GitHub owner before workflows run:

```bash
# Create the output repo if it doesn't exist (via GitHub CLI)
gh repo create kylodeng/ai-delivery-outputs --private
```

Workflows can also be triggered manually via the GitHub Actions UI:

```
# GitHub UI: Actions tab → Select workflow → Run workflow → Fill inputs
```

---

## 8. Risks and TODOs

### 🔴 Critical Risks

| Risk | Detail |
|---|---|
| **Hardcoded AWS credentials in `data_pipeline.py`** | `AWS_ACCESS_KEY` and `AWS_SECRET_KEY` are committed to source. These are example values but the pattern is production-dangerous. Credentials must be removed; Lambda should use its IAM execution role (`boto3.client('s3')` with no explicit credentials). |
| **Hardcoded `DB_PASSWORD` in Terraform** | `"SuperSecret123!"` is committed to IaC. Must be replaced with a reference to AWS Secrets Manager or SSM Parameter Store. The code has a comment acknowledging this. |
| **No S3 encryption at rest** | Customer PII (CSV files containing `customer_id`, `email`, `age`, `country_code`) is stored unencrypted. This is likely a compliance violation (GDPR, PCI-DSS depending on context). |

###