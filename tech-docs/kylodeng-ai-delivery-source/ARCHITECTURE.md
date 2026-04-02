# Architecture Document — kylodeng/ai-delivery-source

---

## 1. Overview

This repository implements an **AI-assisted software delivery platform** composed of two loosely coupled systems. The first is a set of five GitHub Actions–driven automation tools that use Anthropic's Claude LLM to perform code review, technical documentation generation, business documentation generation, automated test generation, and UAT facilitation — all triggered by repository events (pull requests, pushes, tags, branch creation) or on a schedule. Outputs are written to a companion GitHub repository (`ai-delivery-outputs`) and notifications are sent via SendGrid email. The second system is an AWS data ingestion pipeline that ingests customer CSV files uploaded to an S3 landing bucket, validates and transforms them via an AWS Lambda function, and writes the results as Parquet files to a processed S3 bucket — with the Lambda triggered automatically by S3 object creation events.

---

## 2. Resources Deployed

| Resource | Type | Cloud Provider | Purpose |
|---|---|---|---|
| `capco-data-landing-{env}` | AWS S3 Bucket | AWS | Receives raw customer CSV files for ingestion |
| `capco-data-processed-{env}` | AWS S3 Bucket | AWS | Stores validated, transformed Parquet output files |
| `data-ingest-{env}` | AWS Lambda Function | AWS | Processes CSV files on S3 trigger; validates and transforms records |
| `lambda-ingest-role` | AWS IAM Role | AWS | Execution role assumed by the Lambda function |
| `lambda-s3-policy` | AWS IAM Role Policy | AWS | Grants S3 permissions to the Lambda IAM role |
| `landing_trigger` | AWS S3 Bucket Notification | AWS | Invokes Lambda on `s3:ObjectCreated:*` for `raw/*.csv` objects |
| GitHub Actions Runners (`ubuntu-latest`) | CI/CD Compute | GitHub (Microsoft Azure) | Ephemeral runners executing all five workflow tools |
| `ai-delivery-outputs` | GitHub Repository | GitHub | Persistent store for all AI-generated output documents |
| Anthropic Claude (`claude-sonnet-4-6`) | External LLM API | Anthropic | Generates code reviews, documentation, tests, UAT packs |
| SendGrid | Email API | Twilio/SendGrid | Delivers notification emails upon workflow completion |

---

## 3. Data Flow

### 3a — AWS Data Pipeline

1. An external process (or user) uploads a CSV file to `s3://capco-data-landing-{env}/raw/`.
2. The S3 bucket notification fires an `s3:ObjectCreated:*` event filtered on prefix `raw/` and suffix `.csv`.
3. AWS invokes the `data-ingest-{env}` Lambda function, passing the bucket name and object key in the event payload.
4. The Lambda function (`data_pipeline.lambda_handler`) calls `process_csv()`, which uses a `boto3` S3 client to download the file.
5. The CSV is parsed into a Pandas DataFrame. Each row is validated against required fields (`customer_id`, `email`, `age`, `country_code`); invalid rows are collected as failures.
6. Valid rows are written as a Parquet file to `s3://capco-data-landing-{env}/processed/{original_key}.parquet` (**⚠ note: written back to the same landing bucket, not the `processed` bucket — see Risks**).
7. The Lambda returns an HTTP-style response with counts of processed and failed rows.

### 3b — AI Delivery Workflows

1. A GitHub event (PR open, push to `main`, tag push, release branch creation, scheduled cron, or manual dispatch) triggers one of five GitHub Actions workflows.
2. The workflow runner installs Python 3.12 and the `anthropic` + `requests` packages.
3. The relevant Python script in `.github/scripts/` reads source and/or IaC files from the source repository via the **GitHub REST API** (using `GH_TOKEN`), or reads the PR diff.
4. The script constructs a prompt and calls the **Anthropic Claude API** (`claude-sonnet-4-6`) with the file content. Entire file contents (up to truncation limits) are transmitted to Anthropic's API.
5. Claude returns structured output (JSON or Markdown) which the script parses.
6. The script writes the output file(s) to the `ai-delivery-outputs` GitHub repository via authenticated `PUT` to the GitHub Contents API.
7. For Tool 1 (Code Review), the script also posts a comment directly on the pull request via the GitHub Issues Comments API.
8. A notification email is dispatched via the **SendGrid API** to `kylo.deng@capco.com`.
9. For Tool 1, the review JSON is also uploaded as a GitHub Actions artifact attached to the workflow run.

---

## 4. Security Posture

### ✅ What is secured

- **GitHub Secrets** — `ANTHROPIC_API_KEY`, `GH_TOKEN`, and `SENDGRID_API_KEY` are stored as GitHub Actions encrypted secrets, not in plain text in workflow files.
- **Lambda IAM Trust Policy** — The Lambda execution role correctly restricts `sts:AssumeRole` to the `lambda.amazonaws.com` service principal only.
- **S3 Event Filter** — The Lambda trigger is scoped to `raw/*.csv` objects, reducing unintended invocations.
- **UAT workflow guard** — Tool 5 has an `if:` condition ensuring it only runs on `release/` branches or explicit manual dispatch.

### ❌ Security gaps and issues

- **🔴 CRITICAL — Hardcoded AWS credentials in source code**: `src/data_pipeline.py` contains `AWS_ACCESS_KEY = "AKIAIOSFODNN7EXAMPLE"` and `AWS_SECRET_KEY = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"`. Even though these appear to be example values, **committing credential patterns to source control is a critical security violation**. The code comment acknowledges this (`# TODO: move this to secrets manager`). These must be removed immediately and replaced with the Lambda execution role's ambient IAM credentials (i.e., remove the explicit key parameters from `boto3.client()`).
- **🔴 CRITICAL — Hardcoded database password in Terraform**: `infra/main.tf` sets `DB_PASSWORD = "SuperSecret123!"` as a plain-text Lambda environment variable. This value is visible in Terraform state, the AWS Console, and any logs. Must be replaced with an AWS Secrets Manager or SSM Parameter Store reference.
- **🔴 CRITICAL — S3 landing bucket has no server-side encryption**: The `aws_s3_bucket.landing` resource has no `aws_s3_bucket_server_side_encryption_configuration` block. Customer PII (email addresses, ages, country codes) is stored in plaintext at rest.
- **🟠 HIGH — S3 landing bucket has no public access block**: There is no `aws_s3_bucket_public_access_block` resource for the landing bucket. It is not confirmed private.
- **🟠 HIGH — Overly broad IAM policy**: `lambda-s3-policy` grants `s3:*` on `Resource: "*"` — full S3 control across all buckets in the account. This should be scoped to only the specific actions (`s3:GetObject`, `s3:PutObject`, `s3:ListBucket`) on only the two named bucket ARNs.
- **🟠 HIGH — Source code transmitted to third-party LLM**: All five tools send raw source code, IaC files, and PR diffs to Anthropic's API. Depending on data classification, this may violate data handling policies, especially if proprietary or customer-linked code is present.
- **🟡 MEDIUM — No S3 bucket versioning or MFA delete**: Neither bucket has versioning enabled; accidental deletion or overwrite of data is unrecoverable.
- **🟡 MEDIUM — No S3 access logging**: Neither bucket has server access logging configured; there is no audit trail for data access.
- **🟡 MEDIUM — No Lambda VPC configuration**: The Lambda runs outside a VPC. If the processed data store is a VPC-resident database, network isolation is absent.
- **🟡 MEDIUM — No Lambda resource-based policy shown**: There is no `aws_lambda_permission` resource granting S3 the right to invoke the Lambda. [TODO: confirm whether this is defined elsewhere or if the trigger will fail.]
- **🟡 MEDIUM — GH_TOKEN scope unknown**: The `GH_TOKEN` secret is used to read source repos and write to `ai-delivery-outputs`. The minimum required scopes (`repo` read on source, `contents: write` on output repo) should be documented and enforced via a fine-grained PAT. [TODO: confirm token type and scopes.]
- **🟢 LOW — No S3 lifecycle policies**: Data in both buckets will grow indefinitely with no expiry or tiering rules.

---

## 5. Environment Variables and Secrets

| Name | Required | Sensitivity | Where set |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | 🔴 High — paid API key | GitHub Actions Secret |
| `GH_TOKEN` | Yes | 🔴 High — GitHub PAT with repo access | GitHub Actions Secret |
| `SENDGRID_API_KEY` | Yes | 🔴 High — email sending credential | GitHub Actions Secret |
| `OUTPUT_REPO` | No | Low | Workflow `env:` block (default: `ai-delivery-outputs`) |
| `OUTPUT_REPO_OWNER` | No | Low | Workflow `env:` block (derived from `github.repository_owner`) |
| `NOTIFY_EMAIL` | No | Low | Workflow `env:` block (`kylo.deng@capco.com`) |
| `SENDER_EMAIL` | No | Low | Workflow `env:` block (`noreply@ai-delivery.capco.com`) |
| `SOURCE_REPO_OWNER` | No | Low | Workflow `env:` block (derived from `github.repository_owner`) |
| `SOURCE_REPO_NAME` | No | Low | Workflow `env:` block (derived from `github.event.repository.name`) |
| `GITHUB_RUN_URL` | No | Low | Workflow `env:` block (derived from `github.*` context) |
| `REVIEW_MODE` | No (Tool 1 only) | Low | Set dynamically in workflow step |
| `PR_NUMBER` | No (Tool 1 only) | Low | Set dynamically in workflow step |
| `TEST_MODE` | No (Tool 4 only) | Low | Workflow `env:` block / dispatch input |
| `UAT_MODE` | No (Tool 5 only) | Low | Set dynamically in workflow step |
| `RELEASE_VERSION` | No (Tools 3, 5) | Low | Set dynamically in workflow step |
| `PROJECT_NAME` | No (Tool 3 only) | Low | Set dynamically in workflow step |
| `LANDING_BUCKET` | Yes (Lambda) | Low | Terraform Lambda environment variable |
| `DB_PASSWORD` | Yes (Lambda) | 🔴 **CRITICAL — hardcoded plaintext** | Terraform Lambda environment variable — **must migrate to Secrets Manager** |
| `AWS_ACCESS_KEY` | N/A | 🔴 **CRITICAL — hardcoded in source** | `src/data_pipeline.py` — **must be removed** |
| `AWS_SECRET_KEY` | N/A | 🔴 **CRITICAL — hardcoded in source** | `src/data_pipeline.py` — **must be removed** |

---

## 6. Dependencies

| Dependency | Type | Purpose | Notes |
|---|---|---|---|
| Anthropic API (`api.anthropic.com`) | External SaaS API | LLM inference for all 5 tools | Model: `claude-sonnet-4-6`; all source code is transmitted |
| SendGrid API (`api.sendgrid.com`) | External SaaS API | Email notification delivery | Sender domain `ai-delivery.capco.com` must be verified in SendGrid |
| GitHub REST API (`api.github.com`) | External API | Read source files, write outputs, post PR comments | Requires `GH_TOKEN` PAT |
| `ai-delivery-outputs` repo (`kylodeng/ai-delivery-outputs`) | Sibling GitHub Repository | Persistent store for all generated documents | Must exist and be writable by `GH_TOKEN` before workflows run |
| `hashicorp/aws` Terraform provider `~> 5.0` | IaC Provider | AWS resource provisioning | Requires AWS credentials at `terraform apply` time |
| Python `anthropic` package | PyPI | Claude API client | Installed at runtime in GitHub Actions |
| Python `requests` package | PyPI | GitHub and SendGrid HTTP calls | Installed at runtime in GitHub Actions |
| Python `boto3` package | PyPI (Lambda runtime) | AWS S3 operations in pipeline | Available in Lambda Python 3.12 runtime |
| Python `pandas` package | PyPI (Lambda runtime) | CSV parsing and Parquet writing | [TODO: confirm `pandas` is packaged in `lambda.zip` — it is not in the default Lambda runtime] |
| `actions/checkout@v4` | GitHub Action | Source code checkout | |
| `actions/setup-python@v5` | GitHub Action | Python 3.12 runtime setup | |
| `actions/upload-artifact@v4` | GitHub Action | Artifact upload (Tool 1) | |

---

## 7. Deployment Instructions

### Prerequisites

- AWS CLI configured with credentials that have sufficient IAM permissions
- Terraform >= 1.0 installed
- `lambda.zip` built and present in the `infra/` directory
- The `ai-delivery-outputs` GitHub repository created and accessible by `GH_TOKEN`
- GitHub Actions secrets set: `ANTHROPIC_API_KEY`, `GH_TOKEN`, `SENDGRID_API_KEY`

### AWS Infrastructure (Terraform)

```bash
# 1. Navigate to the infra directory
cd infra/

# 2. Initialise Terraform
terraform init

# 3. Preview the deployment plan
terraform plan -var="environment=dev" -var="aws_region=us-east-1"

# 4. Apply infrastructure
terraform apply -var="environment=dev" -var="aws_region=us-east-1"

# 5. Note outputs
# landing_bucket  = "capco-data-landing-dev"
# processed_bucket = "capco-data-processed-dev"
```

```bash
# To deploy to production
terraform apply -var="environment=prod" -var="aws_region=us-east-1"
```

> **⚠ WARNING:** Do not apply the current Terraform configuration to production without first removing the hardcoded `DB_PASSWORD` from `main.tf` and adding S3 encryption and public access blocks.

### GitHub Actions Workflows

The five tools are deployed automatically as part of the repository. No additional deployment step is required beyond ensuring secrets are configured.

To trigger manually:

```bash
# Tool 1 — Code Review (manual, full repo scan)
gh workflow run tool1_code_review.yml \
  -f review_mode=repo

# Tool 1 — Code Review (manual, specific PR)
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

# Tool 4 — Auto Testing (gap analysis)
gh workflow run tool4_auto_testing.yml \
  -f test_mode=gap-analysis

# Tool 5 — UAT (generate test pack)
gh workflow run tool5_uat.yml \
  -f uat_mode=generate \
  -f release_version="1.0.0"

# Tool 5 — UAT (analyse results)
gh workflow run tool5_uat.yml \
  -f uat_mode=analyse \
  -f release_version="1.0.0" \
  -f uat_results_path="uat/owner-repo/v1.0.0/UAT_RESULTS_SHEET.csv"
```

### Lambda Deployment (manual update)

```bash
# Rebuild and redeploy Lambda package
zip -r infra/lambda.zip src/