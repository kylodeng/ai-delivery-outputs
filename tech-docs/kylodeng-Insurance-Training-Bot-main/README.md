# Insurance Training Bot

## 1. Project Overview

Insurance Training Bot is a FastAPI-based AI coaching system designed to help new insurance agents in Hong Kong master sales techniques and product knowledge. It operates in two modes: a **teacher mode** for interactive learning and guided exercises, and a **roleplay mode** where the agent practises pitching to a simulated customer with a randomly generated profile. A RAG (Retrieval-Augmented Generation) pipeline ingests Sun Life insurance product PDFs into a vector store, enabling the AI to answer product-specific questions accurately without hallucinating policy details.

---

## 2. Tech Stack

| Component | Technology | Version/Notes |
|---|---|---|
| Backend framework | FastAPI | Python 3.13 |
| LLM orchestration | LangChain / LangGraph | `langchain-core`, `langchain-openai` |
| LLM provider | OpenRouter (default) or Anthropic | Configurable via `OPENAI_URL_BASE` |
| LLM model | `openai/gpt-oss-20b:free` (default) | Configurable via `OPENAI_MODEL` |
| Embedding / annotation model | `claude-sonnet-4-6` (default ingest) | Configurable via `OPENAI_MODEL` |
| Vector store | Chroma, FAISS, or Pinecone | Selected via `core/vector_store.py` |
| PDF parsing | pdfplumber | — |
| Dependency management | uv | astral-sh/setup-uv@v3 |
| CI/CD | GitHub Actions | Workflows in `.github/workflows/` |
| Deployment target | Azure App Service | Two apps: API + Frontend |
| AI tooling workflows | Anthropic Claude (`claude-sonnet-4-6`) | Code review, docs, UAT, test generation |
| Email notifications | SendGrid | Via `SENDGRID_API_KEY` |
| Session persistence | JSON file (`data/sessions.json`) | Survives server restarts |
| SSL verification | Disabled (`verify=False`) | [TODO: Is this intentional for all environments, or only local dev?] |

---

## 3. Architecture

```
┌─────────────────────────────────────────────────────────┐
│                     Client (Browser / Chainlit UI)       │
└──────────────────────────┬──────────────────────────────┘
                           │ HTTP / SSE streaming
┌──────────────────────────▼──────────────────────────────┐
│                    FastAPI Backend (api/main.py)          │
│  ┌────────────────┐  ┌──────────────┐  ┌─────────────┐  │
│  │  Teacher Agent  │  │Roleplay (LLM)│  │  Assessor   │  │
│  │  (LangGraph)    │  │  Simulation  │  │   Agent     │  │
│  └───────┬────────┘  └──────────────┘  └──────┬──────┘  │
│          │                                     │         │
│  ┌───────▼─────────────────────────────────────▼──────┐  │
│  │               RAG Tools (api/rag_tools.py)          │  │
│  │  list_products · search_product · search_all        │  │
│  │  lookup_hospital_network · compare_plans            │  │
│  │  lookup_exclusions · search_claim_procedure         │  │
│  │  get_current_date                                   │  │
│  └───────────────────────┬─────────────────────────────┘  │
└──────────────────────────┼──────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────┐
│               Vector Store (core/vector_store.py)        │
│          Chroma | FAISS | Pinecone (pluggable)           │
└──────────────────────────▲──────────────────────────────┘
                           │ ingest pipeline
┌──────────────────────────┴──────────────────────────────┐
│          Ingestion Pipeline (core/ingest.py)             │
│  PDFs → annotator (LLM) → chunker (pdfplumber)          │
│        → embed_chunks → vector store                     │
└──────────────────────────────────────────────────────────┘

Supplementary CI/CD tools (.github/scripts/):
  Tool 1: Claude code review on PRs
  Tool 2: Auto-generated tech documentation
  Tool 3: Business documentation on release tags
  Tool 4: Auto test generation
  Tool 5: UAT test pack generation and defect analysis
```

**Key interactions:**

- On startup, `main.py` loads the vector store from disk and initialises RAG tools.
- The FastAPI server mounts the `data/` directory at `/docs` so PDFs can be linked directly in responses.
- Teacher and Assessor agents are LangGraph agents that call RAG tools to answer product questions, with per-request source tracking via Python `contextvars` (async-safe).
- Sessions (conversation history + customer profiles) are persisted to `data/sessions.json`.
- Five GitHub Actions workflows invoke Claude (`claude-sonnet-4-6`) for automated code review, documentation, test generation, and UAT facilitation.

---

## 4. Local Development Setup

### Prerequisites

- Python 3.13
- [`uv`](https://github.com/astral-sh/uv) package manager

### Steps

1. **Clone the repository**

```bash
git clone https://github.com/kylodeng/Insurance-Training-Bot-main.git
cd Insurance-Training-Bot-main
```

2. **Install dependencies**

```bash
uv sync
```

3. **Create a `.env` file** in the project root (see [Environment Variables](#5-environment-variables)):

```bash
cp .env.example .env   # if provided, otherwise create manually
```

4. **Ingest insurance product PDFs into the vector store**

Place PDF files under `data/Insurance-product-info/` (subdirectories are supported), then run:

```bash
uv run python -m core.ingest --pdf-dir data/Insurance-product-info
```

[TODO: What is the exact module entrypoint / CLI command for ingest? `core/ingest.py` has an `if __name__ == "__main__"` block but the argument definition is truncated in the source.]

Alternatively, trigger ingestion via the API after the server starts:

```bash
curl -X POST http://localhost:8000/ingest
```

5. **Start the FastAPI backend**

```bash
uv run uvicorn api.main:app --reload --port 8000
```

6. **Access the API**

```
http://localhost:8000
```

Served PDFs are available at:

```
http://localhost:8000/docs/<relative-path-to-pdf>
```

[TODO: How is the frontend/Chainlit UI started separately? The workflow deploys a `training-bot-frontend` Azure App Service, but no frontend start command is visible in the source files.]

---

## 5. Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `API_KEY` | Yes | `""` | API key for the LLM provider (OpenRouter or Anthropic) |
| `OPENAI_URL_BASE` | No | `https://openrouter.ai/api/v1` | Base URL for the LLM API |
| `OPENAI_MODEL` | No | `openai/gpt-oss-20b:free` | LLM model name used by the backend |
| `SHOW_TOOL_CALLS` | No | `true` | Stream/log tool call events by default (`true`/`false`) |
| `ANTHROPIC_API_KEY` | Yes (CI only) | — | Anthropic API key used by `.github/scripts/` CI tools |
| `GH_TOKEN` | Yes (CI only) | — | GitHub token for CI scripts to read repos and post PR comments |
| `SENDGRID_API_KEY` | Yes (CI only) | — | SendGrid API key for email notifications from CI tools |
| `OUTPUT_REPO` | No (CI only) | `ai-delivery-outputs` | GitHub repo name where CI tool outputs are written |
| `OUTPUT_REPO_OWNER` | No (CI only) | `GITHUB_REPOSITORY_OWNER` | GitHub owner of the output repo |
| `NOTIFY_EMAIL` | No (CI only) | `kylo.deng@capco.com` | Recipient email for CI notification emails |
| `SENDER_EMAIL` | No (CI only) | `kylo.deng@capco.com` | Sender email for CI notification emails |

> **Note:** The backend disables SSL certificate verification (`verify=False`) on all HTTP clients. [TODO: Confirm whether this should be gated to local/dev environments only.]

---

## 6. Running Tests

Tests are located in the `tests/` directory and use **pytest**.

```bash
# Run all tests
uv run pytest tests/ -v
```

The CI pipeline (`deploy.yml`) runs this automatically on every push and pull request to `main`.

[TODO: Are there any test fixtures or environment variables required to run tests locally? No test files were visible in the provided source.]

---

## 7. Deployment

### CI/CD Pipeline (GitHub Actions)

Deployment is fully automated via `.github/workflows/deploy.yml` on every push to `main` (after tests pass).

**Required GitHub Secrets:**

| Secret | Used for |
|---|---|
| `AZURE_WEBAPP_PUBLISH_PROFILE_API` | Deploy `api/` to Azure App Service `training-bot-api` |
| `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND` | Deploy frontend to Azure App Service `training-bot-frontend` |
| `ANTHROPIC_API_KEY` | Claude-based CI tooling workflows |
| `GH_TOKEN` | CI tooling scripts (GitHub API access) |
| `SENDGRID_API_KEY` | CI tooling email notifications |

**Pipeline steps:**

1. Push to `main` triggers the `Test & Deploy` workflow.
2. Tests run (`uv run pytest tests/ -v`).
3. On success, `uv export` generates a `requirements.txt`.
4. The API is deployed to Azure App Service `training-bot-api`.
5. The frontend is deployed to Azure App Service `training-bot-frontend`.

### Manual Deployment

1. **Generate `requirements.txt`**

```bash
uv export --no-dev --format requirements-txt -o requirements.txt
```

2. **Deploy API to Azure** (requires Azure CLI and publish profile)

```bash
az webapp deployment source config-zip \
  --resource-group <resource-group> \
  --name training-bot-api \
  --src <zip-of-repo>
```

[TODO: What is the exact Azure resource group name and region? Not specified in source files.]

3. **Trigger PDF ingestion after first deploy**

```bash
curl -X POST https://<api-app-url>/ingest
```

### AI Tooling Workflows

The five supplementary GitHub Actions workflows can be triggered manually:

```
# Tool 1 — Code Review (manual)
gh workflow run tool1_code_review.yml -f review_mode=repo

# Tool 2 — Tech Documentation (manual)
gh workflow run tool2_tech_docs.yml

# Tool 3 — Business Documentation (manual)
gh workflow run tool3_business_docs.yml -f project_name="Insurance Training Bot" -f release_version="1.0.0"

# Tool 4 — Auto Testing (manual)
gh workflow run tool4_auto_testing.yml -f test_mode=generate

# Tool 5 — UAT Test Pack (manual)
gh workflow run tool5_uat.yml -f uat_mode=generate -f release_version="1.0.0"
```

---

## 8. Known Issues / TODOs

Extracted from code comments and source inspection:

| Location | Issue / TODO |
|---|---|
| `api/main.py` | SSL verification is disabled (`verify=False`) on all `httpx` clients — should be gated to non-production environments only |
| `api/main.py` | `SHOW_TOOL_CALLS` print statement has a bug: `os.getenv("SHOW_TOOL_CALLS"," ").lower()== "true"` uses a space as default, so it always prints `False` |
| `api/agent.py` | `make_teacher_agent` and `make_assessor_agent` are imported in `main.py` but `agent.py` only shows a stub using `create_agent` — the full agent factory implementations are not shown |
| `api/agent.py` | Assessor system prompt is truncated in source; full tool list for assessor mode is incomplete |
| `core/ingest.py` | CLI `argparse` block is truncated; exact `--pdf-dir` default path is unknown |
| `core/chunker.py` | `split_by_words` function body is truncated |
| `core/annotator.py` | Custom annotation logic comment (`# custom annotation logic`) suggests incomplete or placeholder implementation |
| `.github/scripts/shared.py` | `send_email`, `email_html`, and `write_audit_entry` functions are referenced throughout CI scripts but their implementations are truncated/missing from `shared.py` |
| `.github/scripts/tool2_tech_docs.py` | `build_index` function truncated — variable `r` appears to be a typo/incomplete reference to `repo` |
| `.github/scripts/tool1_code_review.py` | `review_pr` function truncated after building the PR comment |
| `api/sessions.py` | `CustomerProfile.describe()` method is truncated |
| `api/rag_tools.py` | `_collect_sources` function return statement is truncated |
| General | No DR (Disaster Recovery) configuration or multi-region setup evident |
| General | No monitoring or alerting configuration visible (Application Insights, etc.) |
| General | Hardcoded email addresses (`kylo.deng@capco.com`) in workflow environment variables — should be moved to secrets or configurable inputs |