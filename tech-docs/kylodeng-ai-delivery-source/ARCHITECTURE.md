# Architecture Document — kylodeng/ai-delivery-source

---

## 1. Overview

The `ai-delivery-source` repository is a dual-purpose system. Its **primary workload** is a serverless AWS data ingestion pipeline that ingests customer CSV files dropped into an S3 landing bucket, validates and transforms them into Parquet format, and writes results to a processed S3 bucket via an AWS Lambda function. Its **secondary capability** is a suite of five AI-powered GitHub Actions workflows that wrap Anthropic's Claude API to automate software delivery tasks — automated code review, technical documentation generation, business documentation generation, AI-assisted test generation, and UAT facilitation — all writing their outputs to a shared `ai-delivery-outputs` GitHub repository and notifying stakeholders via SendGrid email.

---

## 2. Resources Deployed

| Resource | Type | Cloud Provider | Purpose |
|---|---|---|---|
| `capco-data-landing-{env}` | S3 Bucket | AWS | Receives raw customer CSV files; triggers Lambda on upload |
| `capco-data-processed-{env}` | S3 Bucket | AWS | Stores validated, transformed Parquet output files |
| `data-ingest-{env}` | Lambda Function (Python 3.12) | AWS | Core ingestion logic: validate, transform CSV → Parquet |
| `lambda-ingest-role` | IAM Role | AWS | Execution role assumed by Lambda |
| `lambda-s3-policy` | IAM Role Policy | AWS | Grants Lambda access to S3 (⚠️ overly broad — see Security) |
| S3 Bucket Notification (`landing_trigger`) | S3 Event Notification | AWS | Fires Lambda on `s3:ObjectCreated:*` under `raw/*.csv` |
| `ai-delivery-outputs` | GitHub Repository | GitHub | Stores all AI-generated output documents |
| Tool 1 — Code Review | GitHub Actions Workflow | GitHub | Claude-powered PR and repo code review |
| Tool 2 — Tech Docs | GitHub Actions Workflow | GitHub | Claude-generated README, ARCHITECTURE, RUNBOOK docs |
| Tool 3 — Business Docs | GitHub Actions Workflow | GitHub | Claude-generated solution overview and gap questionnaire |
| Tool 4 — Auto Testing | GitHub Actions Workflow | GitHub | Claude-generated test files and coverage gap analysis |
| Tool 5 — UAT Facilitation | GitHub Actions Workflow | GitHub | Claude-generated UAT test packs and defect analysis |
| Anthropic Claude (`claude-sonnet-4-6`) | External AI API | Anthropic | LLM inference for all five workflow tools |
| SendGrid | External Email API | Twilio/SendGrid | Delivery of notification emails to stakeholders |

---

## 3. Data Flow

### AWS Data Pipeline

1. An external producer (ETL job, upstream system, or manual upload) places a `.csv` file under the `raw/` prefix in the `capco-data-landing-{env}` S3 bucket.
2. The S3 bucket notification (`landing_trigger`) fires an `s3:ObjectCreated:*` event to the `data-ingest-{env}` Lambda function.
3. Lambda receives the event, extracts `bucket` and `key` from the payload (falling back to the `LANDING_BUCKET` environment variable), and invokes `process_csv()`.
4. Lambda calls `get_s3_client()` — **currently using hardcoded IAM credentials in source** — and downloads the CSV object via `GetObject`.
5. The CSV is loaded into a Pandas DataFrame. Each row is passed through `validate_customer_record()`, which enforces required fields (`customer_id`, `email`, `age`, `country_code`), email format, and age range (1–150). Invalid rows are collected into a `failed_rows` list (currently logged only; not persisted).
6. Valid rows are written as Parquet to `s3://{landing_bucket}/processed/{original_key_as_parquet}` — **note: output is written back to the landing bucket, not the processed bucket** [TODO: confirm whether `processed/` prefix in the landing bucket or the `capco-data-processed-{env}` bucket is the intended destination].
7. Lambda returns a JSON summary (`processed` count, `failed` count, `output_key`, `timestamp`) with HTTP status 200 on success, 500 on error.

### AI Workflow Pipeline (all five tools follow the same pattern)

1. A GitHub event (PR open, push to main, version tag, branch creation, schedule, or `workflow_dispatch`) triggers the relevant workflow.
2. The GitHub Actions runner checks out the source repository and installs `anthropic` and `requests`.
3. The Python script reads source/IaC files from the repository (via GitHub Contents API using `GH_TOKEN`) and constructs a structured prompt.
4. The prompt is sent to Anthropic's Claude API (`claude-sonnet-4-6`) via `ANTHROPIC_API_KEY`. The response (Markdown, JSON, or delimited text) is parsed.
5. Parsed output is committed to the `ai-delivery-outputs` GitHub repository via the GitHub Contents API (create or update file).
6. A notification email is sent via SendGrid using `SENDGRID_API_KEY` to `kylo.deng@capco.com`.
7. For Tool 1 (Code Review), a summary comment is also posted directly to the originating Pull Request via the GitHub Issues API.
8. JSON artifacts (e.g., review results) are uploaded to the GitHub Actions run as artifacts for auditability.

---

## 4. Security Posture

### ✅ What Is Secured

- **GitHub Secrets** — `ANTHROPIC_API_KEY`, `GH_TOKEN`, and `SENDGRID_API_KEY` are stored as GitHub Actions secrets and injected as environment variables; they do not appear in workflow logs.
- **Lambda execution role** — correctly scoped to `lambda.amazonaws.com` as the trust principal; not publicly assumable.
- **S3 event filter** — Lambda is only triggered for `raw/*.csv` objects, reducing unnecessary invocations.
- **Output repo separation** — AI-generated outputs are written to a separate repository (`ai-delivery-outputs`), isolating them from source code.

### ❌ Gaps and Findings

| Gap | Severity | Detail |
|---|---|---|
| **Hardcoded AWS credentials in source code** | CRITICAL | `AWS_ACCESS_KEY = "AKIAIOSFODNN7EXAMPLE"` and `AWS_SECRET_KEY = "..."` are hardcoded in `src/data_pipeline.py`. Even though these appear to be example keys, the pattern is dangerous and must be replaced with IAM role-based authentication (Lambda execution role already exists — use it). |
| **Hardcoded DB password in Terraform** | CRITICAL | `DB_PASSWORD = "SuperSecret123!"` is set as a plaintext Lambda environment variable in `infra/main.tf`. Must be moved to AWS Secrets Manager or SSM Parameter Store. |
| **S3 landing bucket has no encryption** | HIGH | `aws_s3_bucket.landing` has no `aws_s3_bucket_server_side_encryption_configuration` resource. Customer PII (email, age) is stored unencrypted at rest. |
| **S3 landing bucket has no public access block** | HIGH | No `aws_s3_bucket_public_access_block` resource is attached to the landing bucket. The bucket could be made public accidentally. |
| **Overly broad IAM policy — `s3:*` on `*`** | HIGH | `lambda-s3-policy` grants `s3:*` on `Resource: "*"`. Lambda should only need `s3:GetObject` on the landing bucket and `s3:PutObject` on the processed bucket. |
| **No encryption on processed bucket** | MEDIUM | `aws_s3_bucket.processed` also lacks an explicit SSE configuration (relies on AWS default, which may or may not be enabled depending on account settings — should be explicit). |
| **No S3 bucket versioning** | MEDIUM | Neither bucket has versioning enabled. Accidental overwrites or deletions are unrecoverable. |
| **No S3 access logging** | MEDIUM | Neither bucket has server access logging configured. No audit trail for who accessed customer data. |
| **No VPC / network isolation for Lambda** | MEDIUM | Lambda runs in the default AWS network with no VPC configuration. If the downstream database is VPC-hosted, this is also a connectivity gap. |
| **Failed rows are not persisted** | MEDIUM | Invalid/failed CSV rows are only logged. There is no dead-letter queue, error bucket, or alerting for validation failures. |
| **No pagination on `list_objects_v2`** | LOW | `get_all_pending_files()` does not paginate — buckets with >1,000 objects will silently return incomplete results. |
| **`GH_TOKEN` scope unknown** | LOW | [TODO: What permissions does the `GH_TOKEN` secret have? It should be scoped to `contents:write` and `pull_requests:write` on the output repo only, not a broad personal access token.] |
| **No Lambda resource policy shown** | LOW | The Terraform does not include an `aws_lambda_permission` to allow S3 to invoke Lambda. This may cause the trigger to fail silently. |
| **No secrets scanning / pre-commit hooks** | LOW | Hardcoded credentials could have been caught before commit with tools like `git-secrets` or `truffleHog`. None are configured. |

---

## 5. Environment Variables and Secrets

| Name | Required | Sensitivity | Where Set |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | 🔴 High — paid API key | GitHub Actions Secret |
| `GH_TOKEN` | Yes | 🔴 High — GitHub API access | GitHub Actions Secret |
| `SENDGRID_API_KEY` | Yes | 🔴 High — email sending capability | GitHub Actions Secret |
| `OUTPUT_REPO` | No | 🟢 Low | Workflow `env` block (hardcoded: `ai-delivery-outputs`) |
| `OUTPUT_REPO_OWNER` | No | 🟢 Low | Workflow `env` block (derived from `github.repository_owner`) |
| `NOTIFY_EMAIL` | No | 🟡 Medium — PII (email address) | Workflow `env` block (hardcoded: `kylo.deng@capco.com`) |
| `SENDER_EMAIL` | No | 🟢 Low | Workflow `env` block (hardcoded: `noreply@ai-delivery.capco.com`) |
| `SOURCE_REPO_OWNER` | No | 🟢 Low | Workflow `env` block (derived from GitHub context) |
| `SOURCE_REPO_NAME` | No | 🟢 Low | Workflow `env` block (derived from GitHub context) |
| `GITHUB_RUN_URL` | No | 🟢 Low | Workflow `env` block (derived from GitHub context) |
| `TEST_MODE` | No (Tool 4 only) | 🟢 Low | Workflow `env` block / `workflow_dispatch` input |
| `REVIEW_MODE` | No (Tool 1 only) | 🟢 Low | Set at runtime by workflow step |
| `PR_NUMBER` | No (Tool 1 only) | 🟢 Low | Set at runtime by workflow step |
| `RELEASE_VERSION` | No (Tools 3, 5) | 🟢 Low | Set at runtime from tag or input |
| `PROJECT_NAME` | No (Tool 3 only) | 🟢 Low | Set at runtime from tag or input |
| `UAT_MODE` | No (Tool 5 only) | 🟢 Low | Set at runtime by workflow step |
| `UAT_RESULTS_PATH` | No (Tool 5 only) | 🟢 Low | `workflow_dispatch` input |
| `USER_STORIES` | No (Tool 5 only) | 🟡 Medium — may contain business requirements | `workflow_dispatch` input |
| `LANDING_BUCKET` | Yes (Lambda) | 🟢 Low | Lambda environment variable (set by Terraform) |
| `DB_PASSWORD` | Yes (Lambda) | 🔴 **CRITICAL — plaintext secret** | Lambda environment variable (hardcoded in Terraform — must be moved to Secrets Manager) |
| `AWS_ACCESS_KEY` | ⚠️ Should not exist | 🔴 **CRITICAL — hardcoded credential** | `src/data_pipeline.py` source file — must be removed |
| `AWS_SECRET_KEY` | ⚠️ Should not exist | 🔴 **CRITICAL — hardcoded credential** | `src/data_pipeline.py` source file — must be removed |

---

## 6. Dependencies

| Dependency | Type | Purpose | Notes |
|---|---|---|---|
| **Anthropic Claude API** (`claude-sonnet-4-6`) | External SaaS API | LLM inference for all five AI tools | Paid API; rate limits apply; model version is pinned by name string only — no version pinning guarantee |
| **SendGrid API** | External SaaS API | Transactional email notifications | [TODO: confirm `noreply@ai-delivery.capco.com` sender domain is verified in SendGrid] |
| **GitHub API** (`api.github.com`) | External SaaS API | Reading source files, writing output files, posting PR comments | Requires `GH_TOKEN` with appropriate scopes |
| **`ai-delivery-outputs`** | GitHub Repository (same owner) | Stores all AI-generated documentation and reports | Must exist and be accessible to `GH_TOKEN` before workflows run |
| **AWS S3** | Cloud Service | Landing and processed data storage | `us-east-1` region; accessed by Lambda and `data_pipeline.py` |
| **AWS Lambda** | Cloud Service | Serverless compute for data ingestion | Python 3.12 runtime; triggered by S3 events |
| **`anthropic` (PyPI)** | Python Package | Claude API client | Installed at runtime via `pip install anthropic` — no version pinned |
| **`requests` (PyPI)** | Python Package | HTTP calls to GitHub and SendGrid APIs | Installed at runtime — no version pinned |
| **`boto3` (PyPI)** | Python Package | AWS SDK for S3 operations in Lambda | Provided by Lambda runtime; version determined by AWS |
| **`pandas` (PyPI)** | Python Package | CSV parsing and DataFrame operations | [TODO: not in any requirements file — must be included in `lambda.zip`] |
| **`pyarrow` / `fastparquet`** (implicit) | Python Package | Required by `pandas.to_parquet()` | [TODO: not explicitly listed — must be bundled in `lambda.zip`] |

---

## 7. Deployment Instructions

### Prerequisites

- AWS CLI configured with credentials for the target account
- Terraform >= 1.0 installed
- Python 3.12 installed locally
- GitHub repository secrets set: `ANTHROPIC_API_KEY`, `GH_TOKEN`, `SENDGRID_API_KEY`
- The `ai-delivery-outputs` GitHub repository must already exist under the same owner

### Step 1 — Package the Lambda deployment artifact

```bash
# From repo root
pip install pandas pyarrow boto3 -t lambda_package/
cp src/data_pipeline.py lambda_package/
cd lambda_package && zip -r ../lambda.zip . && cd ..
```

> ⚠️ **Before packaging:** Remove hardcoded `AWS_ACCESS_KEY` and `AWS_SECRET_KEY` from `src/data_pipeline.py` and replace `get_s3_client()` with a no-argument `boto3.client('s3')` call that uses the Lambda execution role.

### Step 2 — Deploy infrastructure

```bash
cd infra
terraform init
terraform plan -var="environment=dev" -var="aws_region=us-east-1"
terraform apply -var="environment=dev" -var="aws_region=us-east-1"
```

> ⚠️ **Before applying:** Remove the hardcoded `DB_PASSWORD` from `main.tf` and replace with an SSM or Secrets Manager reference.

### Step 3 — Add missing Lambda permission (not in Terraform — manual workaround)

```bash
# [TODO: add aws_lambda_permission resource to main.tf instead]
aws lambda add-permission \
  --function-name data-ingest-dev \
  --statement-id s3-invoke \
  --action lambda:InvokeFunction \
  --principal s3.amazonaws.com \
  --source-arn arn:aws:s3:::capco-data-landing-dev \
  --region us-east-1
```

### Step 4 —