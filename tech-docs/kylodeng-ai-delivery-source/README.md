# AI Delivery Source

A GitHub-hosted repository that demonstrates a suite of five Claude AI-powered automation tools integrated into a CI/CD pipeline. Each tool handles a different phase of the software delivery lifecycle — code review, technical documentation, business documentation, automated test generation, and UAT facilitation — all driven by GitHub Actions workflows. The repository also contains a sample AWS data ingestion pipeline (`src/data_pipeline.py`) that serves as the subject for these AI tools.

---

## Tech Stack

| Component | Technology | Version/Notes |
|---|---|---|
| AI Model | Anthropic Claude | `claude-sonnet-4-6` |
| Python SDK | `anthropic` | Latest (pip install) |
| CI/CD | GitHub Actions | Ubuntu-latest runners, Node24 |
| Python Runtime | Python | 3.12 |
| HTTP Client | `requests` | Latest (pip install) |
| Email Delivery | SendGrid API | Via REST |
| IaC | Terraform (AWS provider) | `~> 5.0` |
| Cloud Provider | AWS | us-east-1 (default) |
| Data Processing | `pandas` | Latest |
| AWS SDK | `boto3` | Latest |
| Source Repo Output | GitHub API | v2022-11-28 |

---

## Architecture

The repository is structured around five independent GitHub Actions workflows, each calling a corresponding Python script in `.github/scripts/`. All five scripts share common utilities from `shared.py` (GitHub API access, Claude API calls, SendGrid email, and audit logging).

```
┌─────────────────────────────────────────────────────────┐
│                   GitHub Actions Triggers                │
│  PR open/sync │ push to main │ release tag │ cron │ manual│
└──────┬────────┴──────┬───────┴──────┬──────┴──┬───┴──────┘
       │               │              │          │
       ▼               ▼              ▼          ▼
  tool1_code_     tool2_tech_    tool3_biz_  tool4_auto_   tool5_uat.py
  review.py       docs.py        docs.py     testing.py
       │               │              │          │              │
       └───────────────┴──────────────┴──────────┴──────────────┘
                                │
                          shared.py
                    ┌───────────┼───────────┐
                    ▼           ▼           ▼
             GitHub API    Claude API   SendGrid API
                    │
                    ▼
          ai-delivery-outputs repo
          (generated docs, reports,
           test files written here)
```

- **Tool 1 (Code Review):** Triggered on PR open/sync or weekly cron. Fetches the PR diff via the GitHub API, sends it to Claude, and posts findings as a PR comment. Also writes a report to the output repo.
- **Tool 2 (Tech Docs):** Triggered on push to `main` or weekly cron. Reads source and IaC files, generates README, architecture, and runbook documents, and writes them to the output repo.
- **Tool 3 (Business Docs):** Triggered on version tags or manual dispatch. Produces a non-technical Solution Overview Document and a Gap Questionnaire. Writes both to the output repo.
- **Tool 4 (Auto Testing):** Triggered on PR open/sync against source files or weekly cron. Generates test files (in the correct framework for the detected language) and performs coverage gap analysis. Writes results to the output repo.
- **Tool 5 (UAT Facilitation):** Triggered on `release/` branch creation or manual dispatch. In `generate` mode, produces a UAT test pack as CSV and markdown. In `analyse` mode, reads a completed results CSV and outputs a defect report with a go/no-go recommendation.

The **sample application** (`src/data_pipeline.py` + `infra/main.tf`) is a Lambda-triggered S3 data ingestion pipeline that reads customer CSV files from a landing bucket, validates and transforms them to Parquet, and writes to a processed bucket.

---

## Local Development Setup

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

3. **Install dependencies**

```bash
pip install anthropic requests pandas boto3
```

4. **Set required environment variables** (see [Environment Variables](#environment-variables) section)

```bash
export ANTHROPIC_API_KEY="your-anthropic-key"
export GH_TOKEN="your-github-pat"
export SENDGRID_API_KEY="your-sendgrid-key"
export OUTPUT_REPO="ai-delivery-outputs"
export OUTPUT_REPO_OWNER="your-github-username"
export NOTIFY_EMAIL="you@example.com"
export SENDER_EMAIL="noreply@yourdomain.com"
```

5. **Run a tool script directly** (example: tech docs generation)

```bash
export SOURCE_REPO_OWNER="kylodeng"
export SOURCE_REPO_NAME="ai-delivery-source"
export GITHUB_RUN_URL="https://github.com/kylodeng/ai-delivery-source/actions/runs/local"
python .github/scripts/tool2_tech_docs.py
```

6. **(Optional) Run the data pipeline locally against real AWS credentials**

```bash
export AWS_ACCESS_KEY_ID="your-access-key"
export AWS_SECRET_ACCESS_KEY="your-secret-key"
export LANDING_BUCKET="capco-data-landing-dev"
python -c "from src.data_pipeline import lambda_handler; lambda_handler({'bucket': 'capco-data-landing-dev', 'key': 'raw/sample.csv'}, None)"
```

> **Note:** The `src/data_pipeline.py` file currently contains hardcoded AWS credentials (see [Known Issues](#known-issues--todos)). Do not commit real credentials.

7. **(Optional) Initialise Terraform**

```bash
cd infra
terraform init
terraform plan
```

---

## Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | — | API key for Anthropic Claude |
| `GH_TOKEN` | Yes | — | GitHub Personal Access Token with repo read/write scope |
| `SENDGRID_API_KEY` | Yes | — | SendGrid API key for email notifications |
| `OUTPUT_REPO` | No | `ai-delivery-outputs` | Name of the GitHub repo where generated outputs are written |
| `OUTPUT_REPO_OWNER` | No | `GITHUB_REPOSITORY_OWNER` | GitHub owner/org of the output repo |
| `NOTIFY_EMAIL` | No | `kylo.deng@capco.com` | Recipient email address for notifications |
| `SENDER_EMAIL` | No | `kylo.deng@capco.com` | Sender email address used with SendGrid |
| `SOURCE_REPO_OWNER` | Yes (workflows) | `github.repository_owner` | Owner of the source repo being analysed |
| `SOURCE_REPO_NAME` | Yes (workflows) | `github.event.repository.name` | Name of the source repo being analysed |
| `GITHUB_RUN_URL` | Yes (workflows) | Constructed by Actions | URL of the current Actions run, included in outputs |
| `LANDING_BUCKET` | No | — | S3 bucket name for the data pipeline Lambda (`src/data_pipeline.py`) |
| `REVIEW_MODE` | No | `repo` | Set by workflow: `pr` or `repo` — controls Tool 1 review scope |
| `PR_NUMBER` | No | — | PR number to review (Tool 1, `REVIEW_MODE=pr`) |
| `TEST_MODE` | No | `generate` | Tool 4 mode: `generate` new tests or `gap-analysis` |
| `UAT_MODE` | No | `generate` | Tool 5 mode: `generate` test pack or `analyse` results |
| `RELEASE_VERSION` | No | — | Release version string used by Tool 3 and Tool 5 |
| `PROJECT_NAME` | No | Repository name | Human-readable project name used in Tool 3 output |
| `USER_STORIES` | No | — | Optional acceptance criteria pasted into Tool 5 `generate` mode |
| `UAT_RESULTS_PATH` | No | — | Path in output repo to completed UAT results CSV (Tool 5 `analyse` mode) |

All secrets (`ANTHROPIC_API_KEY`, `GH_TOKEN`, `SENDGRID_API_KEY`) must be configured as **GitHub Actions repository secrets**.

---

## Running Tests

[TODO: Are there existing tests for the `.github/scripts/` tooling, or only the AI-generated tests produced by Tool 4?]

Tool 4 (`tool4_auto_testing.py`) auto-generates test files for source code in the repository and writes them to the `ai-delivery-outputs` repo. To trigger it manually:

```bash
# Via GitHub Actions UI — go to Actions > Tool 4 — Auto Testing > Run workflow
# Select mode: generate or gap-analysis
```

To run any locally generated test file (Python example):

```bash
pip install pytest
pytest path/to/generated_test_file.py -v
```

---

## Deployment

### Infrastructure (Terraform)

```bash
cd infra
terraform init
terraform plan -var="environment=dev" -var="aws_region=us-east-1"
terraform apply -var="environment=dev" -var="aws_region=us-east-1"
```

This provisions:
- S3 landing bucket (`capco-data-landing-<env>`)
- S3 processed bucket (`capco-data-processed-<env>`)
- Lambda function `data-ingest-<env>` (Python 3.12, 30s timeout)
- IAM role and policy for Lambda
- S3 bucket notification to trigger Lambda on `raw/*.csv` object creation

> ⚠️ See [Known Issues](#known-issues--todos) for security problems in the current IaC that must be fixed before production deployment.

### Lambda Deployment Package

[TODO: How is `lambda.zip` built and uploaded? No build script is present in the repository.]

### GitHub Actions Workflows

The five workflows run automatically based on their configured triggers. They require no manual deployment — only the repository secrets listed in [Environment Variables](#environment-variables) must be set in the GitHub repository settings.

To trigger any workflow manually:

```
GitHub UI → Actions → <Workflow Name> → Run workflow
```

---

## Known Issues / TODOs

The following issues are extracted directly from code comments:

### Security (High / Critical)

- **Hardcoded AWS credentials in `src/data_pipeline.py`:**
  ```python
  # TODO: move this to secrets manager
  AWS_ACCESS_KEY = "AKIAIOSFODNN7EXAMPLE"
  AWS_SECRET_KEY = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
  ```
  These example credentials are committed to source. Real credentials must never be stored here; use AWS Secrets Manager or IAM roles.

- **Hardcoded DB password in `infra/main.tf`:**
  ```hcl
  # Hardcoded secret - should use SSM or Secrets Manager
  DB_PASSWORD = "SuperSecret123!"
  ```

- **S3 landing bucket has no encryption and no public access block (`infra/main.tf`):**
  ```hcl
  # S3 landing bucket - NO encryption, NO public access block
  ```

- **Overly permissive IAM policy (`infra/main.tf`):** Lambda role is granted `s3:*` on `Resource: "*"` — full S3 access across all buckets.

### Missing Features / Code Quality

- **No pagination in `get_all_pending_files` (`src/data_pipeline.py`):** `list_objects_v2` will silently truncate results at 1,000 objects.

- **Bare `except` in `lambda_handler` (`src/data_pipeline.py`):** Catches all exceptions including `SystemExit` and `KeyboardInterrupt`; comment notes `# bare except swallows all errors`.

- **No error handling for malformed CSV (`src/data_pipeline.py`):** Commented as `# No error handling if CSV is malformed`.

- **Missing resource tags (`infra/main.tf`):**
  ```hcl
  # TODO: add tags
  ```

- **`send_email`, `email_html`, and `write_audit_entry` functions** are imported by all tool scripts from `shared.py` but their implementations are not present in the provided source files. [TODO: Are these functions defined in a portion of `shared.py` not included in the repository snapshot?]

- **`tool2_tech_docs.py` contains a truncated f-string** (`f"# Tech Documentation Index — {owner}/{r`) — the variable reference is cut off. [TODO: Is this a transcription truncation or a bug in the source file?]

- **`tool4_auto_testing.py` and `tool5_uat.py`** contain truncated function definitions (`build_test_pack_csv(scenarios: list[d`) — implementation details are missing from the provided files.