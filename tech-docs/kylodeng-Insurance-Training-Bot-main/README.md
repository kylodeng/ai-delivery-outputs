# Insurance Training Bot

## 1. Project Overview

Insurance Training Bot is an AI-powered training platform for insurance sales agents, built around a Retrieval-Augmented Generation (RAG) pipeline over real insurance product PDFs. It offers two interaction modes: a **Teacher mode** for guided learning, concept explanation, and roleplay practice, and an **Assessor mode** that scores a completed roleplay session across multiple performance dimensions. The backend exposes a FastAPI service; a separate frontend application connects to it for the chat UI.

---

## 2. Tech Stack

| Component | Technology | Version/Notes |
|---|---|---|
| Language | Python | 3.13 (per CI), 3.12 used in workflow jobs |
| Package manager | `uv` (Astral) | `astral-sh/setup-uv@v3` |
| Web framework | FastAPI | — |
| LLM orchestration | LangChain / LangGraph | `langchain-core`, `langchain_openai` |
| LLM provider | OpenRouter (default) | `openai/gpt-oss-20b:free`; configurable via `OPENAI_URL_BASE` / `OPENAI_MODEL` |
| Annotation LLM | Anthropic Claude (via OpenAI-compat) | `claude-sonnet-4-6` |
| Embeddings / vector store | FAISS (local), Chroma, Pinecone | Selectable via `core/vector_store.py` |
| PDF processing | `pdfplumber` | — |
| HTTP client | `httpx` | SSL verification disabled (see Known Issues) |
| AI delivery workflows | Anthropic Claude API | `claude-sonnet-4-6` via `anthropic` SDK |
| Email | SendGrid | — |
| CI/CD | GitHub Actions | — |
| Deployment target | Azure App Service | Two apps: `training-bot-api`, `training-bot-frontend` |
| Config | `python-dotenv` | `.env` file |

---

## 3. Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                        Frontend App                         │
│          (Azure App Service: training-bot-frontend)         │
│   Chainlit UI / Vite dev server → connects to FastAPI       │
└───────────────────────────┬─────────────────────────────────┘
                            │ HTTP (REST + SSE streaming)
┌───────────────────────────▼─────────────────────────────────┐
│                  FastAPI Backend (api/main.py)               │
│          (Azure App Service: training-bot-api)              │
│                                                             │
│  ┌──────────────┐   ┌──────────────┐   ┌─────────────────┐ │
│  │ Teacher Agent│   │Assessor Agent│   │ Roleplay System │ │
│  │ (LangGraph)  │   │ (LangGraph)  │   │   (direct LLM)  │ │
│  └──────┬───────┘   └──────┬───────┘   └────────┬────────┘ │
│         │                  │                     │          │
│  ┌──────▼──────────────────▼─────────────────────▼────────┐ │
│  │                   RAG Tools (api/rag_tools.py)          │ │
│  │  search_product · search_all · compare_plans ·         │ │
│  │  lookup_hospital_network · lookup_exclusions · etc.     │ │
│  └──────────────────────────┬──────────────────────────────┘ │
│                             │                               │
│  ┌──────────────────────────▼──────────────────────────────┐ │
│  │              Vector Store (core/vector_store.py)         │ │
│  │         FAISS (local) · Chroma · Pinecone               │ │
│  └──────────────────────────┬──────────────────────────────┘ │
└───────────────────────────────────────────────────────────────┘
                              │ loaded at startup via POST /ingest
┌─────────────────────────────▼─────────────────────────────────┐
│               Ingestion Pipeline (core/ingest.py)             │
│  PDF → pdfplumber → annotator (LLM) → chunker → embed → save  │
│  Source PDFs: data/Insurance-product-info/**/*.pdf            │
└───────────────────────────────────────────────────────────────┘

┌───────────────────────────────────────────────────────────────┐
│            GitHub Actions AI Delivery Workflows               │
│  Tool 1: Claude code review (on PR)                          │
│  Tool 2: Tech doc generation (on push to main)               │
│  Tool 3: Business doc generation (on release tag)            │
│  Tool 4: Auto test generation (on PR / Wednesday cron)       │
│  Tool 5: UAT test pack / defect analysis (on release branch) │
│  All tools → write to ai-delivery-outputs repo + SendGrid    │
└───────────────────────────────────────────────────────────────┘
```

**Key interactions:**

1. At startup FastAPI loads the pre-built vector store from disk (`/data`). If none exists, it warns and waits for `POST /ingest`.
2. The Teacher agent receives user messages, calls RAG tools to retrieve product facts, then streams an annotated response with inline source citations back to the frontend.
3. The Assessor agent is invoked once after a roleplay session ends; it uses the same RAG tools to verify factual claims made by the trainee agent and returns a structured assessment.
4. Sessions (conversation history, customer profiles, mode) are persisted to `data/sessions.json` and survive server restarts.
5. PDF files are served statically under `/docs` so the frontend can link directly to source pages.

---

## 4. Local Development Setup

### Prerequisites

- Python 3.13
- [`uv`](https://docs.astral.sh/uv/) package manager
- Access to an OpenRouter API key (or any OpenAI-compatible endpoint)

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

3. **Copy and configure environment variables**

```bash
cp .env.example .env   # if provided, otherwise create .env manually
```

Edit `.env` with the values described in the [Environment Variables](#5-environment-variables) section.

4. **Place insurance PDF documents** into the data directory

```
data/Insurance-product-info/
```

5. **Ingest PDFs into the vector store**

```bash
uv run python -m core.ingest --pdf-dir data/Insurance-product-info
```

Alternatively, after starting the server, call:

```bash
curl -X POST http://localhost:8000/ingest
```

6. **Start the FastAPI backend**

```bash
uv run uvicorn api.main:app --reload --port 8000
```

7. **Verify the server is running**

```bash
curl http://localhost:8000/docs
```

The interactive API docs will be available at `http://localhost:8000/docs`.

> **Note:** The frontend application build/start commands are [TODO: not evidenced in the provided files — confirm the frontend framework and start command].

---

## 5. Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `API_KEY` | Yes | `""` | API key for the LLM provider (OpenRouter or Anthropic) |
| `OPENAI_URL_BASE` | No | `https://openrouter.ai/api/v1` | Base URL for the OpenAI-compatible LLM API |
| `OPENAI_MODEL` | No | `openai/gpt-oss-20b:free` | Model name for the main chat LLM |
| `SHOW_TOOL_CALLS` | No | `true` | Stream tool call events to the UI by default (`true`/`false`) |
| `ANTHROPIC_API_KEY` | Yes (CI workflows) | — | Anthropic API key used by the GitHub Actions AI delivery tools |
| `GH_TOKEN` | Yes (CI workflows) | — | GitHub personal access token for the AI delivery workflow scripts |
| `SENDGRID_API_KEY` | Yes (CI workflows) | — | SendGrid API key for email notifications from CI workflows |
| `OUTPUT_REPO` | No (CI workflows) | `ai-delivery-outputs` | GitHub repo name where AI tool outputs are written |
| `OUTPUT_REPO_OWNER` | No (CI workflows) | `GITHUB_REPOSITORY_OWNER` | GitHub owner of the output repo |
| `NOTIFY_EMAIL` | No (CI workflows) | `kylo.deng@capco.com` | Recipient email for workflow notifications |
| `SENDER_EMAIL` | No (CI workflows) | `kylo.deng@capco.com` | Sender email for workflow notifications |

> **Note:** CI workflow secrets (`ANTHROPIC_API_KEY`, `GH_TOKEN`, `SENDGRID_API_KEY`, `AZURE_WEBAPP_PUBLISH_PROFILE_API`, `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND`) must be configured in GitHub repository Settings → Secrets and Variables → Actions.

---

## 6. Running Tests

Tests are located in the `tests/` directory. The CI pipeline runs them with `pytest`:

```bash
uv run pytest tests/ -v
```

To run tests without `uv`:

```bash
pytest tests/ -v
```

> **Note:** [TODO: Are there any test fixtures or environment variables required to run tests locally? The test suite content was not included in the provided files.]

---

## 7. Deployment

### CI/CD (GitHub Actions — automatic)

Deployment is triggered automatically on every push to `main` after tests pass:

1. Tests run in the `test` job.
2. On success, `deploy-api` and `deploy-frontend` jobs run in parallel.
3. Each job:
   - Generates a `requirements.txt` from the `uv` lockfile:
     ```bash
     uv export --no-dev --format requirements-txt -o requirements.txt
     ```
   - Deploys to Azure App Service using the `azure/webapps-deploy@v3` action and the corresponding publish profile secret.

| App Service name | Secret required |
|---|---|
| `training-bot-api` | `AZURE_WEBAPP_PUBLISH_PROFILE_API` |
| `training-bot-frontend` | `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND` |

### Manual deployment

1. **Export dependencies**

```bash
uv export --no-dev --format requirements-txt -o requirements.txt
```

2. **Deploy API to Azure App Service** (requires Azure CLI)

```bash
az webapp up --name training-bot-api --resource-group <your-rg> --runtime "PYTHON:3.13"
```

3. **Set environment variables on Azure**

```bash
az webapp config appsettings set \
  --name training-bot-api \
  --resource-group <your-rg> \
  --settings API_KEY="..." OPENAI_URL_BASE="..." OPENAI_MODEL="..."
```

4. **Trigger ingestion** after deployment

```bash
curl -X POST https://training-bot-api.azurewebsites.net/ingest
```

> **Note:** [TODO: What is the startup command configured on Azure App Service? e.g. `uvicorn api.main:app --host 0.0.0.0 --port 8000`]

---

## 8. Known Issues / TODOs

The following are extracted from code comments and structural gaps in the provided files:

| Location | Issue / TODO |
|---|---|
| `api/main.py` | `http_client=httpx.Client(verify=False)` — SSL certificate verification is **disabled** for all LLM API calls. This is a security risk and should be resolved before production use. |
| `api/main.py` | `SHOW_TOOL_CALLS` default is `true`; the print statement uses a potentially inconsistent comparison: `os.getenv("SHOW_TOOL_CALLS"," ").lower()== "true"` (note leading space in default). |
| `api/agent.py` | File is truncated — the `ASSESSOR_SYSTEM` prompt and `make_teacher_agent` / `make_assessor_agent` factory functions are cut off. |
| `api/rag_tools.py` | File is truncated — the `_collect_sources` function body is cut off; full tool definitions (`search_product`, `search_all`, etc.) are not shown. |
| `.github/scripts/tool2_tech_docs.py` | File is truncated — `build_index` function references undefined variable `r` instead of `repo`. |
| `.github/scripts/tool4_auto_testing.py` | File is truncated — `build_test_report` table is cut off. |
| `.github/scripts/tool5_uat.py` | File is truncated — `build_test_pack_csv` function signature is cut off. |
| `.github/scripts/shared.py` | File is truncated — `send_email`, `email_html`, and `write_audit_entry` functions are referenced throughout but their implementations are cut off. |
| `core/annotator.py` | Comment `# custom annotation logic` suggests the `annotate_document` function body is incomplete. |
| `core/chunker.py` | `split_by_words` function is truncated. |
| `core/ingest.py` | `--pdf-dir` argparse default path is truncated. |
| `api/sessions.py` | `CustomerProfile.describe()` method is truncated. |
| All AI workflow tools | Escalation path contacts are `[TODO: fill in team contacts]` (from `tool2_tech_docs.py` runbook template). |
| General | No disaster recovery or monitoring configuration is evidenced in the codebase. |
| General | The frontend application's framework, source location, and local start command are not evidenced in the provided files — [TODO: confirm frontend technology and dev start command]. |