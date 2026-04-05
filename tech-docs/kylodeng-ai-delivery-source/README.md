# ai-delivery-source

## 1. Project Overview

This repository is the **source application** for a Claude AI-powered delivery automation platform. It contains a customer CSV data ingestion pipeline (deployed as an AWS Lambda function) alongside five GitHub Actions workflows that use the Claude API to automate code review, technical documentation, business documentation, test generation, and UAT facilitation. All AI-generated outputs are written to a companion repository (`ai-delivery-outputs`).

---

## 2. Tech Stack

| Component | Technology | Version/Notes |
|---|---|---|
| AI Model | Anthropic Claude | `claude-sonnet-4-6` |
| Scripting language | Python | 3.12 |
| CI/CD platform | GitHub Actions | ubuntu-latest runners |
| Cloud provider | AWS | us-east-1 (default) |
| Compute | AWS Lambda | Python 3.12 runtime |
| Storage | AWS S3 | Landing + processed buckets |
| IaC | Terraform (HashiCorp AWS provider) | `~> 5.0` |
| Data processing | pandas | [TODO: what version is pinned?] |
| HTTP client | requests | [TODO: what version is pinned?] |
| Anthropic SDK | anthropic (Python) | [TODO: what version is pinned?] |
| Email delivery | SendGrid API | Via `SENDGRID_API_KEY` |
| Output encoding | base64 (stdlib) | Used for GitHub API file writes |

---

## 3. Architecture

The repository has two distinct layers that interact as follows:

**Data pipeline layer:**  
`src/data_pipeline.py` is packaged as `lambda.zip` and deployed as an AWS Lambda function (`data-ingest-{environment}`). It is triggered automatically by S3 `ObjectCreated` events on files uploaded to the `raw/` prefix of the landing bucket (`capco-data-landing-{environment}`). The function downloads each CSV, validates each row, and writes a Parquet file to the processed bucket (`capco-data-processed-{environment}`).

**AI automation layer:**  
Five GitHub Actions workflows (`.github/workflows/tool1–5.yml`) each invoke a corresponding Python script under `.github/scripts/`. All scripts share common utilities from `shared.py` (Claude API calls, GitHub REST API helpers, SendGrid email, audit logging). Claude is called with a structured system prompt; the response is parsed and written back to the `ai-delivery-outputs` repository via the GitHub Contents API. PR comments are posted directly to the source repository where relevant.

```
GitHub Events (PR / push / tag / cron / dispatch)
        │
        ▼
GitHub Actions Workflow (tool1–5.yml)
        │
        ├─► .github/scripts/shared.py  ──► Anthropic Claude API
        │           │
        │           ├─► GitHub REST API  (read source files / write output repo / post PR comments)
        │           └─► SendGrid API     (email notifications)
        │
        └─► ai-delivery-outputs repo    (generated docs, test files, UAT packs)

S3 Landing Bucket (raw/*.csv)
        │  ObjectCreated trigger
        ▼
AWS Lambda (data_pipeline.lambda_handler)
        │
        └─► S3 Processed Bucket (processed/*.parquet)
```

---

## 4. Local Development Setup

> **Prerequisites:** Python 3.12, pip, Terraform ≥ 1.x, AWS CLI configured, a GitHub personal access token, an Anthropic API key, and a SendGrid API key.

**Running the data pipeline locally:**

```bash
# 1. Clone the repository
git clone https://github.com/kylodeng/ai-delivery-source.git
cd ai-delivery-source

# 2. Create and activate a virtual environment
python3.12 -m venv .venv
source .venv/bin/activate

# 3. Install Python dependencies
pip install anthropic requests boto3 pandas pyarrow

# 4. Export required environment variables (see Environment Variables section)
export ANTHROPIC_API_KEY="sk-ant-..."
export GH_TOKEN="ghp_..."
export SENDGRID_API_KEY="SG...."
export OUTPUT_REPO="ai-delivery-outputs"
export OUTPUT_REPO_OWNER="kylodeng"
export NOTIFY_EMAIL="you@example.com"
export SENDER_EMAIL="noreply@example.com"

# 5. (Pipeline only) Export AWS credentials and bucket name
export AWS_ACCESS_KEY_ID="..."
export AWS_SECRET_ACCESS_KEY="..."
export LANDING_BUCKET="capco-data-landing-dev"

# 6. Run a specific AI tool script directly
python .github/scripts/tool1_code_review.py

# 7. Or invoke the Lambda handler locally with a test event
python - <<'EOF'
from src.data_pipeline import lambda_handler
result = lambda_handler({"bucket": "capco-data-landing-dev", "key": "raw/test.csv"}, None)
print(result)
EOF
```

**Provisioning infrastructure locally with Terraform:**

```bash
# 8. Navigate to the infra directory
cd infra

# 9. Initialise Terraform
terraform init

# 10. Preview the plan
terraform plan -var="environment=dev"

# 11. Apply
terraform apply -var="environment=dev"
```

---

## 5. Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | — | API key for the Anthropic Claude API |
| `GH_TOKEN` | Yes | — | GitHub personal access token with repo read/write permissions |
| `SENDGRID_API_KEY` | Yes | — | SendGrid API key for outbound email notifications |
| `OUTPUT_REPO` | No | `ai-delivery-outputs` | Name of the GitHub repository where AI outputs are written |
| `OUTPUT_REPO_OWNER` | No | `GITHUB_REPOSITORY_OWNER` | GitHub owner (org or user) of the output repository |
| `NOTIFY_EMAIL` | No | `kylo.deng@capco.com` | Recipient email address for workflow notifications |
| `SENDER_EMAIL` | No | `kylo.deng@capco.com` | Sender email address used by SendGrid |
| `SOURCE_REPO_OWNER` | No | `github.repository_owner` | Owner of the source repository being analysed (set by workflows) |
| `SOURCE_REPO_NAME` | No | `github.event.repository.name` | Name of the source repository being analysed (set by workflows) |
| `GITHUB_RUN_URL` | No | Constructed by workflow | Full URL of the current Actions run (set by workflows) |
| `LANDING_BUCKET` | Yes (Lambda) | — | S3 bucket name for raw CSV uploads; read from Lambda event or this env var |
| `TEST_MODE` | No | `generate` | Mode for Tool 4: `generate` (new tests) or `gap-analysis` |
| `UAT_MODE` | No | `generate` | Mode for Tool 5: `generate` (test pack) or `analyse` (defect report) |
| `RELEASE_VERSION` | No | `0.1.0` | Release version string used by Tool 3 and Tool 5 |
| `PROJECT_NAME` | No | Repository name | Human-readable project name used in business docs (Tool 3) |
| `USER_STORIES` | No | — | Optional acceptance criteria pasted into Tool 5 UAT generation |
| `UAT_RESULTS_PATH` | No | — | Path in output repo to a completed UAT results CSV (Tool 5 analyse mode) |

> All secrets (`ANTHROPIC_API_KEY`, `GH_TOKEN`, `SENDGRID_API_KEY`) must be stored as **GitHub Actions repository secrets** for the workflows to function.

---

## 6. Running Tests

[TODO: Are there any existing tests in the repository? No test files were found in the provided source.]

The Tool 4 workflow (`tool4_auto_testing.yml`) **generates** test files using Claude and writes them to the `ai-delivery-outputs` repository. To trigger test generation manually:

```bash
# Via GitHub CLI — manual dispatch
gh workflow run "Tool 4 — Auto Testing" \
  --field test_mode=generate

# Or trigger gap analysis mode
gh workflow run "Tool 4 — Auto Testing" \
  --field test_mode=gap-analysis
```

To run any locally generated test file (pytest example):

```bash
# Install pytest
pip install pytest

# Run a generated test file
pytest path/to/generated_test_file.py -v
```

---

## 7. Deployment

**Infrastructure (Terraform):**

```bash
cd infra

# Initialise providers
terraform init

# Deploy to dev
terraform apply -var="environment=dev"

# Deploy to production
terraform apply -var="environment=prod"

# Destroy an environment
terraform destroy -var="environment=dev"
```

**Lambda function packaging:**

```bash
# Package the Lambda deployment archive
zip lambda.zip src/data_pipeline.py

# Deploy via Terraform (lambda.zip must exist before apply)
terraform apply -var="environment=dev"
```

**GitHub Actions workflows** are deployed automatically when pushed to the repository. Workflows trigger on the following events:

| Workflow | Trigger |
|---|---|
| Tool 1 — Code Review | PR opened/synchronised; Monday 08:00 UTC cron; manual dispatch |
| Tool 2 — Tech Documentation | Push to `main` (non-doc files); Sunday 06:00 UTC cron; manual dispatch |
| Tool 3 — Business Documentation | Push of a `v*` tag; manual dispatch (with `project_name` and `release_version` inputs) |
| Tool 4 — Auto Testing | PR opened/synchronised on `src/**` or script files; Wednesday 07:00 UTC cron; manual dispatch |
| Tool 5 — UAT Facilitation | Creation of a `release/*` branch; manual dispatch (with `uat_mode` and `release_version` inputs) |

---

## 8. Known Issues / TODOs

The following issues are extracted directly from code comments:

| Location | Issue |
|---|---|
| `src/data_pipeline.py` line 10–11 | **CRITICAL — Hardcoded AWS credentials.** `AWS_ACCESS_KEY` and `AWS_SECRET_KEY` are hardcoded in source. Comment: `# TODO: move this to secrets manager` |
| `src/data_pipeline.py` `get_all_pending_files()` | S3 `list_objects_v2` result is **not paginated**; will silently miss files beyond the first page (1,000 objects) |
| `src/data_pipeline.py` `process_csv()` | No error handling if the downloaded CSV is malformed (comment: `# No error handling if CSV is malformed`) |
| `src/data_pipeline.py` `lambda_handler()` | Bare `except Exception` swallows all errors with no re-raise (comment: `# bare except swallows all errors`) |
| `infra/main.tf` `aws_s3_bucket.landing` | **Landing S3 bucket has no server-side encryption and no public access block configured** (comment: `# S3 landing bucket - NO encryption, NO public access block`) |
| `infra/main.tf` `aws_s3_bucket.landing` | Missing resource tags (comment: `# TODO: add tags`) |
| `infra/main.tf` `aws_iam_role_policy.lambda_policy` | **IAM policy grants `s3:*` on `Resource: "*"` — overly permissive** (comment: `# Overly permissive policy - full S3 access`) |
| `infra/main.tf` `aws_lambda_function.ingest` | **`DB_PASSWORD` hardcoded as a Lambda environment variable** (comment: `# Hardcoded secret - should use SSM or Secrets Manager`) |
| `shared.py` | `send_email`, `email_html`, and `write_audit_entry` functions are imported by all tool scripts but their implementations are not present in the provided source files — [TODO: are these functions defined elsewhere in shared.py?] |
| `tool2_tech_docs.py` | `build_index` function references variable `r` which appears to be a typo for `repo` — file is truncated |
| `tool4_auto_testing.py` | `build_test_pack_csv` in `tool5_uat.py` and `build_test_report` in `tool4_auto_testing.py` are truncated in source |
| All workflows | `NOTIFY_EMAIL` and `SENDER_EMAIL` are hardcoded to `kylo.deng@capco.com` / `noreply@ai-delivery.capco.com` in workflow env blocks — should be parameterised |
| `infra/main.tf` | No CloudWatch logging, monitoring, or alerting configured for the Lambda function |
| `infra/main.tf` | No disaster recovery, multi-region, or S3 versioning configured |
| `infra/main.tf` | Terraform state backend is not configured (no `backend` block) — [TODO: where is remote state stored?] |