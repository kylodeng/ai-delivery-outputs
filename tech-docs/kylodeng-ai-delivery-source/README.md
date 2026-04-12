# ai-delivery-source

## 1. Project Overview

This repository contains a customer data ingestion pipeline deployed as an AWS Lambda function, alongside a suite of five AI-powered GitHub Actions workflows. The workflows use the Anthropic Claude API to automate code review, technical documentation generation, business documentation generation, test generation, and UAT facilitation. Outputs from each workflow are written to a companion repository (`ai-delivery-outputs`) and optionally delivered by email via SendGrid.

---

## 2. Tech Stack

| Component | Technology | Version / Notes |
|---|---|---|
| Runtime language | Python | 3.12 |
| AI model | Anthropic Claude | `claude-sonnet-4-6` |
| CI/CD platform | GitHub Actions | Node 24 runner |
| Cloud provider | AWS | us-east-1 (default) |
| IaC | Terraform | AWS provider `~> 5.0` |
| Compute | AWS Lambda | Python 3.12 runtime |
| Storage | AWS S3 | Landing + processed buckets |
| Data processing | pandas | [TODO: what version is pinned?] |
| Email delivery | SendGrid | Via REST API |
| HTTP client | requests | [TODO: what version is pinned?] |
| AWS SDK | boto3 | [TODO: what version is pinned?] |

---

## 3. Architecture

The system has two distinct layers:

**Data pipeline layer:** A CSV file uploaded to the `raw/` prefix of the S3 landing bucket triggers the `data-ingest` Lambda function via an S3 bucket notification. The Lambda validates each customer record (required fields, email format, age range), transforms valid rows, and writes a Parquet file to the `processed/` prefix of the same bucket. Failed rows are counted and returned in the response body but are not persisted separately.

**AI workflow layer:** Five GitHub Actions workflows share a common Python utility module (`.github/scripts/shared.py`) that wraps the GitHub API, Anthropic Claude API, SendGrid email API, and output-repo file writes. Each workflow fetches source files or PR diffs from this repository, sends them to Claude with a specialist system prompt, and commits the AI-generated artefacts (review reports, markdown docs, test files, UAT packs) to the `ai-delivery-outputs` repository. Workflows are triggered by pull request events, branch/tag pushes, scheduled cron jobs, or manual dispatch.

```
CSV upload
    │
    ▼
S3 landing bucket  ──(s3:ObjectCreated)──►  Lambda (data_pipeline.py)
                                                │
                                    ┌───────────┴───────────┐
                                    ▼                       ▼
                              Valid rows              Failed rows
                                    │                  (logged)
                                    ▼
                         S3 processed bucket
                         (Parquet output)

GitHub event (PR / push / tag / cron / manual)
    │
    ▼
GitHub Actions workflow (tool1–tool5)
    │
    ├──► shared.py ──► GitHub API (fetch files / diff / post PR comment)
    ├──► shared.py ──► Anthropic Claude API (generate artefact)
    ├──► shared.py ──► GitHub API (write to ai-delivery-outputs repo)
    └──► shared.py ──► SendGrid API (email notification)
```

---

## 4. Local Development Setup

1. **Clone the repository**

```bash
git clone https://github.com/kylodeng/ai-delivery-source.git
cd ai-delivery-source
```

2. **Create and activate a Python virtual environment**

```bash
python3.12 -m venv .venv
source .venv/bin/activate
```

3. **Install Python dependencies**

```bash
pip install anthropic requests boto3 pandas pyarrow
```

4. **Set required environment variables** (see [Environment Variables](#5-environment-variables) section)

```bash
export ANTHROPIC_API_KEY="your-anthropic-api-key"
export GH_TOKEN="your-github-pat"
export SENDGRID_API_KEY="your-sendgrid-key"
export OUTPUT_REPO_OWNER="your-github-username"
export OUTPUT_REPO="ai-delivery-outputs"
```

5. **Initialise Terraform** (for infrastructure changes)

```bash
cd infra
terraform init
```

6. **Run a workflow script manually** (example: tech docs tool)

```bash
export SOURCE_REPO_OWNER="kylodeng"
export SOURCE_REPO_NAME="ai-delivery-source"
export GITHUB_RUN_URL="https://github.com/kylodeng/ai-delivery-source/actions/runs/local"
python .github/scripts/tool2_tech_docs.py
```

---

## 5. Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | — | API key for Anthropic Claude |
| `GH_TOKEN` | Yes | — | GitHub personal access token with repo read/write permissions |
| `SENDGRID_API_KEY` | Yes | — | SendGrid API key for email delivery |
| `OUTPUT_REPO` | No | `ai-delivery-outputs` | Name of the GitHub repo where generated artefacts are written |
| `OUTPUT_REPO_OWNER` | No | `GITHUB_REPOSITORY_OWNER` | GitHub owner (user or org) of the output repo |
| `NOTIFY_EMAIL` | No | `kylo.deng@capco.com` | Recipient address for email notifications |
| `SENDER_EMAIL` | No | `kylo.deng@capco.com` | Sender address used by SendGrid |
| `SOURCE_REPO_OWNER` | Yes (workflows) | `github.repository_owner` | Owner of the source repository being analysed |
| `SOURCE_REPO_NAME` | Yes (workflows) | `github.event.repository.name` | Name of the source repository being analysed |
| `GITHUB_RUN_URL` | No | Set by Actions runner | URL of the current workflow run, included in outputs |
| `LANDING_BUCKET` | Yes (Lambda) | — | S3 bucket name for raw CSV uploads; set as Lambda environment variable via Terraform |
| `REVIEW_MODE` | No | `repo` | Code review mode: `pr` or `repo` (Tool 1 only) |
| `PR_NUMBER` | No | — | Pull request number to review (Tool 1, `pr` mode only) |
| `TEST_MODE` | No | `generate` | Test tool mode: `generate` or `gap-analysis` (Tool 4 only) |
| `UAT_MODE` | No | `generate` | UAT tool mode: `generate` or `analyse` (Tool 5 only) |
| `RELEASE_VERSION` | No | — | Version string used when generating business docs or UAT packs |
| `PROJECT_NAME` | No | Repository name | Human-readable project name for business docs |
| `USER_STORIES` | No | — | Acceptance criteria pasted inline for UAT pack generation |
| `UAT_RESULTS_PATH` | No | — | Path in output repo to a completed UAT results CSV (Tool 5 analyse mode) |

---

## 6. Running Tests

[TODO: Are there any existing tests in this repository? No test files were found in the provided source.]

The AI workflow **Tool 4** auto-generates test files for source code in this repository and commits them to the `ai-delivery-outputs` repo. To trigger test generation manually:

```bash
# Via GitHub Actions UI — navigate to:
# Actions → "Tool 4 — Auto Testing" → Run workflow → Mode: generate

# Or locally (requires env vars set):
export TEST_MODE="generate"
python .github/scripts/tool4_auto_testing.py
```

To run a coverage gap analysis:

```bash
export TEST_MODE="gap-analysis"
python .github/scripts/tool4_auto_testing.py
```

---

## 7. Deployment

### Infrastructure (Terraform)

```bash
cd infra

# Initialise providers
terraform init

# Preview changes
terraform plan -var="environment=dev"

# Apply to dev
terraform apply -var="environment=dev"

# Apply to a different environment
terraform apply -var="environment=prod" -var="aws_region=eu-west-1"
```

Terraform will create:
- S3 landing bucket (`capco-data-landing-<environment>`)
- S3 processed bucket (`capco-data-processed-<environment>`)
- Lambda function (`data-ingest-<environment>`) from `lambda.zip`
- IAM role and policy for the Lambda
- S3 bucket notification to trigger the Lambda on `raw/*.csv` uploads

> **Note:** `lambda.zip` must exist before running `terraform apply`. [TODO: How is `lambda.zip` built and where is the packaging step documented?]

### Application (Lambda packaging)

[TODO: There is no build script or Makefile for packaging the Lambda zip. How should `lambda.zip` be assembled from `src/data_pipeline.py`?]

### GitHub Actions workflows

All five workflows are deployed automatically when this repository is pushed to GitHub. Required secrets must be configured in the repository settings before any workflow will succeed:

```
Settings → Secrets and variables → Actions → New repository secret
```

Secrets to add: `ANTHROPIC_API_KEY`, `GH_TOKEN`, `SENDGRID_API_KEY`

---

## 8. Known Issues / TODOs

The following issues are extracted directly from code comments:

### Security (Critical)

| Location | Issue |
|---|---|
| `src/data_pipeline.py` lines 10–11 | Hardcoded AWS access key and secret key — **must be moved to AWS Secrets Manager** |
| `infra/main.tf` Lambda environment block | `DB_PASSWORD` hardcoded as `"SuperSecret123!"` in Terraform — should use SSM Parameter Store or Secrets Manager |
| `infra/main.tf` `aws_iam_role_policy` | IAM policy grants `s3:*` on `"*"` — overly permissive; should be scoped to specific bucket ARNs and required actions only |
| `infra/main.tf` `aws_s3_bucket.landing` | S3 landing bucket has **no server-side encryption** and **no public access block** configured |

### Infrastructure

| Location | Issue |
|---|---|
| `infra/main.tf` `aws_s3_bucket.landing` | No resource tags defined — `# TODO: add tags` |
| `infra/main.tf` | No S3 encryption resource for either bucket |
| `infra/main.tf` | No CloudWatch log group or monitoring/alerting configured |
| `infra/main.tf` | No disaster recovery or multi-region configuration |

### Application

| Location | Issue |
|---|---|
| `src/data_pipeline.py` `get_all_pending_files` | S3 `list_objects_v2` result is not paginated — will silently miss files if there are more than 1,000 objects |
| `src/data_pipeline.py` `process_csv` | No error handling if the downloaded CSV is malformed (comment: `# No error handling if CSV is malformed`) |
| `src/data_pipeline.py` `lambda_handler` | Bare `except Exception` swallows all errors without re-raising — comment: `# bare except swallows all errors` |
| `src/data_pipeline.py` `process_csv` | Failed rows are counted but not written anywhere persistent |

### Shared scripts

| Location | Issue |
|---|---|
| `.github/scripts/shared.py` | `send_email`, `email_html`, and `write_audit_entry` functions are imported by all five tools but their implementations are not included in the provided files — [TODO: Are these functions defined elsewhere in `shared.py`?] |
| `.github/scripts/tool1_code_review.py` | Script body appears truncated — the `review_pr` function and main entrypoint after the comment block are cut off |
| `.github/scripts/tool2_tech_docs.py` | `build_index` function is truncated mid-string |
| `.github/scripts/tool4_auto_testing.py` | `build_test_report` function is truncated |
| `.github/scripts/tool5_uat.py` | `build_test_pack_csv` function is truncated |