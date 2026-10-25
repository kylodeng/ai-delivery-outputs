# Insurance Training Bot

## 1. Project Overview

Insurance Training Bot is an AI-powered training platform for insurance sales agents operating in the Hong Kong market. It provides two interactive modes: a **teacher mode** for guided learning (explaining products, running exercises, and quizzing the agent) and a **roleplay mode** where the agent practises sales conversations against a simulated customer persona. Product knowledge is grounded in a RAG (Retrieval-Augmented Generation) pipeline built on real insurance product PDFs, so answers are always sourced from verified documents.

---

## 2. Tech Stack

| Component | Technology | Version/Notes |
|---|---|---|
| Language | Python | 3.13 (CI), 3.12+ recommended locally |
| Package manager | [uv](https://github.com/astral-sh/uv) | Replaces pip/venv |
| Web framework | FastAPI | — |
| LLM integration | LangChain + LangGraph | `langchain-openai`, `langchain-core` |
| LLM provider | OpenRouter (default) / Anthropic | Configurable via env vars |
| Default model | `openai/gpt-oss-20b:free` | Overridable via `OPENAI_MODEL` |
| Embedding / Vector store | FAISS (local), Chroma, or Pinecone | Selectable via `core/vector_store.py` |
| PDF parsing | pdfplumber | — |
| Frontend | Chainlit UI (dev) / Vite dev server | Port 5173 |
| CI/CD | GitHub Actions | `.github/workflows/` |
| Deployment target | Azure App Service | Two apps: API + Frontend |
| AI DevOps tooling | Claude (`claude-sonnet-4-6`) via Anthropic SDK | 5 workflow tools |
| Email | SendGrid | Used by GitHub Actions workflows only |
| HTTP client | httpx | SSL verification disabled (see Known Issues) |

---

## 3. Architecture

```
┌──────────────────────────────────────────────────────────────┐
│  Client (Chainlit UI / Vite)  :5173                          │
│  — teacher chat  — roleplay chat  — assessment results       │
└───────────────────────┬──────────────────────────────────────┘
                        │ HTTP / SSE streaming
┌───────────────────────▼──────────────────────────────────────┐
│  FastAPI  (api/main.py)  :8000                               │
│  — /ingest   POST  → triggers PDF ingestion pipeline         │
│  — /chat     POST  → teacher agent (streamed)                │
│  — /roleplay POST  → roleplay customer (streamed)            │
│  — /assess   POST  → assessor agent (one-shot)               │
│  — /docs     GET   → static file server for PDFs in /data/   │
│  — /sessions CRUD  → session persistence (sessions.json)    │
└──────┬────────────────────────────┬───────────────────────────┘
       │                            │
┌──────▼──────┐            ┌────────▼────────────────────────┐
│  LangGraph  │            │  core/ RAG library               │
│  Agents     │            │  ingest.py  → annotator → chunker│
│  teacher    │◄──tools───►│  vector_store (FAISS/Chroma/     │
│  assessor   │            │  Pinecone)                        │
└─────────────┘            └─────────────────────────────────-┘
```

**Data flow:**
1. Operator runs `POST /ingest` → `core/ingest.py` walks `data/Insurance-product-info/`, annotates each PDF page via LLM, chunks the text, embeds it, and saves the vector index.
2. At startup, `main.py` loads the saved vector store into `_store` and builds the eight RAG tools (`api/rag_tools.py`).
3. Each user message reaches the FastAPI handler, which creates or retrieves a `Session` (persisted to `data/sessions.json`) and invokes the appropriate LangGraph agent.
4. The **teacher agent** streams responses via `astream_events`; the **assessor agent** runs one-shot via `ainvoke` after a roleplay session ends.
5. Source citations are tracked per-request using a `contextvars.ContextVar` so that concurrent async tasks share the same citation list without race conditions.

**GitHub Actions (5 AI DevOps tools)** run independently against the repository using Claude (`claude-sonnet-4-6`) via `shared.py` and write outputs to a separate `ai-delivery-outputs` repo:

| Tool | Trigger | Output |
|---|---|---|
| Tool 1 — Code Review | PR open/sync, Monday cron, manual | PR comments + report |
| Tool 2 — Tech Docs | Push to main, Sunday cron, manual | README, ARCHITECTURE, RUNBOOK |
| Tool 3 — Business Docs | Version tag push, manual | Solution overview + gap questionnaire |
| Tool 4 — Auto Testing | PR open/sync, Wednesday cron, manual | Generated test files + coverage report |
| Tool 5 — UAT | Release branch creation, manual | UAT test pack CSV or defect report |

---

## 4. Local Development Setup

```bash
# 1. Clone the repository
git clone https://github.com/kylodeng/Insurance-Training-Bot-main.git
cd Insurance-Training-Bot-main
```

```bash
# 2. Install uv (if not already installed)
pip install uv
```

```bash
# 3. Install all dependencies
uv sync
```

```bash
# 4. Copy and populate the environment file
cp .env.example .env
# Edit .env — see Environment Variables section below
```

```bash
# 5. Ingest insurance product PDFs into the vector store
#    PDFs must be placed under data/Insurance-product-info/
uv run python core/ingest.py --pdf-dir data/Insurance-product-info
```

```bash
# 6. Start the FastAPI backend
uv run uvicorn api.main:app --reload --port 8000
```

```bash
# 7. (Optional) Open the Chainlit UI in a separate terminal
#    [TODO: what command starts the Chainlit frontend? No Chainlit entrypoint file was found in the provided files.]
```

> **Note:** The Vite dev server (port 5173) is whitelisted in CORS. [TODO: what command/directory starts the Vite frontend?]

---

## 5. Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `API_KEY` | Yes | `""` | API key for the LLM provider (OpenRouter or Anthropic) |
| `OPENAI_URL_BASE` | No | `https://openrouter.ai/api/v1` | Base URL for the OpenAI-compatible LLM endpoint |
| `OPENAI_MODEL` | No | `openai/gpt-oss-20b:free` | Model name passed to the LLM client |
| `SHOW_TOOL_CALLS` | No | `true` | Log and stream tool call events (`true`/`false`); overridable per session in the UI |
| `ANTHROPIC_API_KEY` | Yes (CI only) | — | Anthropic API key used by GitHub Actions AI tools (Tools 1–5) |
| `GH_TOKEN` | Yes (CI only) | — | GitHub PAT used by Actions scripts to read repos and post comments |
| `SENDGRID_API_KEY` | Yes (CI only) | — | SendGrid key for email notifications from Actions workflows |
| `OUTPUT_REPO` | No (CI only) | `ai-delivery-outputs` | Name of the GitHub repo where Actions write output files |
| `OUTPUT_REPO_OWNER` | No (CI only) | `GITHUB_REPOSITORY_OWNER` | Owner of the output repo |
| `NOTIFY_EMAIL` | No (CI only) | `kylo.deng@capco.com` | Recipient email for workflow notifications |
| `SENDER_EMAIL` | No (CI only) | `kylo.deng@capco.com` | Sender address for SendGrid emails |

---

## 6. Running Tests

```bash
# Run the full test suite
uv run pytest tests/ -v
```

Tests are also run automatically on every push and pull request to `main` via the `Test & Deploy` GitHub Actions workflow (`.github/workflows/deploy.yml`).

> [TODO: What testing framework and test files exist under `tests/`? No test files were present in the provided source listing.]

---

## 7. Deployment

### Prerequisites

- An Azure subscription with two App Service instances named `training-bot-api` and `training-bot-frontend`.
- GitHub repository secrets set:
  - `AZURE_WEBAPP_PUBLISH_PROFILE_API`
  - `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND`

### Automatic (CI/CD)

Every push to `main` that passes tests triggers the `deploy-api` and `deploy-frontend` jobs in `.github/workflows/deploy.yml`:

```
push to main
  → test job (pytest)
      → deploy-api  (azure/webapps-deploy → training-bot-api)
      → deploy-frontend (azure/webapps-deploy → training-bot-frontend)
```

### Manual

```bash
# 1. Export a requirements.txt from the uv lockfile
uv export --no-dev --format requirements-txt -o requirements.txt
```

```bash
# 2. Deploy API manually using the Azure CLI
az webapp deploy \
  --name training-bot-api \
  --resource-group <your-resource-group> \
  --src-path . \
  --type zip
```

> [TODO: What Azure region and resource group are used? No Bicep/Terraform IaC files were found in the provided files.]

### Ingesting documents on the deployed instance

```bash
# Call the ingest endpoint after deployment (PDFs must already be in the data/ directory)
curl -X POST https://<your-app>.azurewebsites.net/ingest
```

> [TODO: Does the `/ingest` endpoint require authentication? No auth middleware was visible in the provided `main.py`.]

---

## 8. Known Issues / TODOs

Extracted from code comments and observable gaps:

| Location | Issue / TODO |
|---|---|
| `api/main.py` | `httpx.Client(verify=False)` and `httpx.AsyncClient(verify=False)` — SSL certificate verification is **disabled** for all LLM API calls. This is a security risk and should be resolved before production use. |
| `api/main.py` | `print(f"SHOW_TOOL_CALLS=...")` — debug `print` statement left in production startup code. |
| `api/agent.py` | File is partially shown; `make_teacher_agent` and `make_assessor_agent` factory functions are referenced in `main.py` but their implementations were not included in the provided files. `from langchain.agents import create_agent` is imported but that symbol does not exist in recent LangChain versions — likely needs `create_react_agent` or a LangGraph equivalent. |
| `core/ingest.py` | Default `OPENAI_URL_BASE` for the annotation LLM is `https://api.anthropic.com/v1` but the model is `claude-sonnet-4-6` — the Anthropic REST API is not OpenAI-compatible without a proxy; this may fail without additional configuration. |
| `.github/scripts/tool2_tech_docs.py` | `build_index` function has a bare `{r` at end of the f-string — the variable `r` is undefined (likely should be `repo`). File appears truncated. |
| `.github/scripts/tool3_business_docs.py` | `build_full_output` return type is `tuple[str, str]` but the View link f-string at end of the file is cut off. |
| `.github/scripts/tool4_auto_testing.py` | Report table row separator `|---|---` is cut off — the markdown table in `build_test_report` is incomplete. |
| `.github/scripts/shared.py` | `send_email`, `email_html`, and `write_audit_entry` are imported by all tool scripts but their implementations are missing from the provided `shared.py` (file is truncated after the email section comment). |
| `api/sessions.py` | `CustomerProfile.describe()` method is truncated in the provided file. |
| `core/chunker.py` | `split_by_words` function body is truncated in the provided file. |
| General | No DR (disaster recovery) or multi-region configuration found. |
| General | No monitoring or alerting configuration (e.g. Application Insights, Sentry) found. |
| General | No authentication/authorisation layer visible on any FastAPI endpoint. |
| `api/agent.py` | Assessor system prompt references `{profile}` and `{conversation}` as format placeholders — [TODO: verify these are correctly substituted before the prompt is sent to the LLM]. |