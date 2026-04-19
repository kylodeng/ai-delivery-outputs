# Insurance Training Bot

## 1. Project Overview

Insurance Training Bot is a FastAPI-based AI training system designed to help new insurance agents in Hong Kong master sales skills and product knowledge. It provides two interaction modes: a **Teacher mode** for guided learning via streamed chat, and a **Roleplay/Assessment mode** in which the agent practises with a simulated customer and receives a scored performance review. Product knowledge is grounded in a RAG (Retrieval-Augmented Generation) pipeline built on real insurance product PDFs.

---

## 2. Tech Stack

| Component | Technology | Version/Notes |
|---|---|---|
| Backend API | FastAPI | Python 3.13 (production), 3.12 (CI scripts) |
| LLM (application) | OpenAI-compatible via OpenRouter | Default: `openai/gpt-oss-20b:free`; configurable via `OPENAI_MODEL` |
| LLM (ingestion/annotation) | Anthropic Claude (via OpenAI-compatible wrapper) | Default: `claude-sonnet-4-6` |
| LLM (CI tools) | Anthropic Claude API | `claude-sonnet-4-6` via `anthropic` SDK |
| Agent framework | LangGraph / LangChain | `langchain`, `langchain-openai`, `langchain-core` |
| Vector store | Chroma / local FAISS / Pinecone | Selectable via `core/vector_store.py`; default loaded at startup |
| PDF processing | pdfplumber | — |
| Embeddings | Voyage AI (implied by rate-limit defaults) | Free-tier default; `batch_size=126`, `batch_delay=0` |
| Package manager | uv | astral-sh/setup-uv@v3 |
| HTTP client | httpx | SSL verification disabled (dev/internal) |
| Environment config | python-dotenv | `.env` file |
| CI/CD platform | GitHub Actions | 5 AI-powered workflow tools |
| Deployment target | Azure App Service | Two apps: `training-bot-api`, `training-bot-frontend` |
| Email delivery | SendGrid | CI tools only |
| Session persistence | JSON file (`data/sessions.json`) | Survives server restarts |

---

## 3. Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                      Client / Chainlit UI                    │
│                  (http://localhost:5173 or :8000)            │
└─────────────────────────┬───────────────────────────────────┘
                          │ HTTP / SSE (StreamingResponse)
┌─────────────────────────▼───────────────────────────────────┐
│                   FastAPI Backend  (api/main.py)             │
│                                                              │
│  ┌──────────────┐   ┌──────────────────┐  ┌─────────────┐  │
│  │ Teacher Agent │   │ Roleplay/Customer │  │  Assessor   │  │
│  │ (LangGraph)  │   │ (direct LLM call) │  │   Agent     │  │
│  └──────┬───────┘   └──────────────────┘  └──────┬──────┘  │
│         │                                          │         │
│  ┌──────▼──────────────────────────────────────────▼──────┐ │
│  │                  RAG Tools (api/rag_tools.py)           │ │
│  │  search_product · search_all · lookup_hospital_network  │ │
│  │  compare_plans · lookup_exclusions · search_claim_...   │ │
│  └──────────────────────┬──────────────────────────────────┘ │
└─────────────────────────┼───────────────────────────────────┘
                          │
┌─────────────────────────▼───────────────────────────────────┐
│                 Core RAG Library (core/)                      │
│                                                              │
│  vector_store.py  ← ChromaStore / LocalFAISSStore /          │
│                      PineconeStore                           │
│  ingest.py        ← PDF walk → annotate → chunk → embed      │
│  chunker.py       ← pdfplumber + heuristic text splitting    │
│  annotator.py     ← LLM-based doc/page annotation            │
│                      (cached as .annot.json sidecars)        │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│           GitHub Actions CI/CD (.github/workflows/)          │
│                                                              │
│  Tool 1: Claude code review  → PR comments + report         │
│  Tool 2: Tech docs generation → README/ARCH/RUNBOOK          │
│  Tool 3: Business docs        → Solution overview            │
│  Tool 4: Auto test generation → pytest/jest stubs            │
│  Tool 5: UAT facilitation     → test pack CSV + defect report│
│                                                              │
│  shared.py: GitHub API · Claude API · SendGrid · audit log   │
└─────────────────────────────────────────────────────────────┘
```

**Data flow (teacher mode):**
1. User sends a message to `POST /chat` (or Chainlit equivalent).
2. FastAPI calls `make_teacher_agent`, passing the shared `_llm` and pre-built RAG tools.
3. The LangGraph agent decides which RAG tool to invoke (e.g. `search_product`).
4. The tool queries the vector store and returns ranked chunks with source metadata.
5. Sources are collected per-request via a `contextvars.ContextVar` (async-safe).
6. The agent streams a response with inline citation markers back to the client.

**Ingestion flow (one-time / on-demand):**
1. `POST /ingest` (or `python core/ingest.py`) walks `data/` for PDFs.
2. Each PDF is annotated by an LLM (doc-level + per-page); results cached as `.annot.json`.
3. Relevant pages are chunked by `core/chunker.py` using heuristic heading/bullet detection.
4. Chunks are embedded in batches and stored in the configured vector store.

---

## 4. Local Development Setup

### Prerequisites
- Python 3.13
- [uv](https://github.com/astral-sh/uv) package manager

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
cp .env.example .env
# Edit .env — see Environment Variables table below
```

4. **Ingest insurance product PDFs into the vector store**

```bash
uv run python core/ingest.py --pdf-dir data/Insurance-product-info --verbose
```

5. **Start the FastAPI backend**

```bash
uv run uvicorn api.main:app --reload --port 8000
```

6. **Access the API**

```
http://localhost:8000
http://localhost:8000/docs   ← Swagger UI
```

> **Note:** The Chainlit UI (if present) runs on port 5173 by default. [TODO: confirm how to start the frontend — no frontend source files were found in the provided file listing]

---

## 5. Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `API_KEY` | Yes | `""` | API key for the OpenAI-compatible LLM provider (e.g. OpenRouter key) |
| `OPENAI_URL_BASE` | No | `https://openrouter.ai/api/v1` | Base URL for the LLM API |
| `OPENAI_MODEL` | No | `openai/gpt-oss-20b:free` | Model name for teacher/assessor/roleplay agents |
| `SHOW_TOOL_CALLS` | No | `true` | Log and stream tool call events by default (`true`/`false`) |
| `ANTHROPIC_API_KEY` | Yes (CI only) | — | Anthropic API key used by all 5 GitHub Actions CI tools |
| `GH_TOKEN` | Yes (CI only) | — | GitHub personal access token for CI scripts to read repos and post comments |
| `SENDGRID_API_KEY` | Yes (CI only) | — | SendGrid API key for CI email notifications |
| `OUTPUT_REPO` | No (CI only) | `ai-delivery-outputs` | GitHub repo name where CI tools write their output files |
| `OUTPUT_REPO_OWNER` | No (CI only) | `GITHUB_REPOSITORY_OWNER` | GitHub org/user that owns the output repo |
| `NOTIFY_EMAIL` | No (CI only) | `kylo.deng@capco.com` | Recipient email for CI tool notifications |
| `SENDER_EMAIL` | No (CI only) | `kylo.deng@capco.com` | Sender address for CI tool emails |

> **Note:** `AZURE_WEBAPP_PUBLISH_PROFILE_API` and `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND` must be set as GitHub repository secrets for deployment (see Deployment section).

---

## 6. Running Tests

Tests are executed with pytest via uv:

```bash
uv run pytest tests/ -v
```

The CI pipeline runs this automatically on every push and pull request to `main` before any deployment step proceeds (see `.github/workflows/deploy.yml`).

> [TODO: What test files exist under `tests/`? No test files were present in the provided file listing — confirm test coverage and whether fixtures or a test database are required.]

---

## 7. Deployment

### Prerequisites
- Azure App Service apps named `training-bot-api` and `training-bot-frontend` must exist.
- GitHub repository secrets must be configured:
  - `AZURE_WEBAPP_PUBLISH_PROFILE_API`
  - `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND`

### Automatic deployment (CI/CD)

Deployment is triggered automatically on every push to `main` after tests pass. The workflow (`.github/workflows/deploy.yml`) performs the following steps:

1. Runs the test suite (`uv run pytest tests/ -v`).
2. Exports a `requirements.txt` from the uv lock file (no dev dependencies).
3. Deploys the API to Azure App Service (`training-bot-api`).
4. Deploys the frontend to Azure App Service (`training-bot-frontend`).

### Manual ingestion (run once or when PDFs change)

```bash
# From the project root, after configuring .env
uv run python core/ingest.py --pdf-dir data/Insurance-product-info --verbose
```

This produces a persisted vector store that the FastAPI app loads at startup.

### Triggering the vector store ingest via the API

```bash
curl -X POST http://localhost:8000/ingest
```

> [TODO: Confirm whether `POST /ingest` accepts authentication, a request body, or additional parameters — the endpoint is referenced in `main.py` comments but its full signature is not shown in the provided files.]

### CI tool workflows (manual dispatch)

Each AI delivery tool can be triggered manually from the GitHub Actions UI:

| Workflow | Trigger | Purpose |
|---|---|---|
| Tool 1 — Code Review | PR open/sync, Monday 08:00 UTC, manual | Claude code review on PR diff or full repo |
| Tool 2 — Tech Docs | Push to `main`, Sunday 06:00 UTC, manual | Generate README, ARCHITECTURE, RUNBOOK |
| Tool 3 — Business Docs | Version tag (`v*`), manual | Generate solution overview and gap questionnaire |
| Tool 4 — Auto Testing | PR open/sync on source files, Wednesday 07:00 UTC, manual | Generate test stubs or coverage gap analysis |
| Tool 5 — UAT | `release/*` branch creation, manual | Generate UAT test pack or analyse completed results CSV |

---

## 8. Known Issues / TODOs

The following are extracted directly from code comments:

- **`api/main.py`** — SSL verification is explicitly disabled for both the sync and async httpx clients (`verify=False`). This is noted as a development/internal configuration and must be addressed before production exposure.

  ```python
  http_client=httpx.Client(verify=False),
  http_async_client=httpx.AsyncClient(verify=False),
  ```

- **`api/agent.py`** — The `make_teacher_agent` and `make_assessor_agent` factory functions are referenced in `main.py` but the file provided only contains the system prompt strings and a stub `from langchain.agents import create_agent` import. [TODO: Confirm the full agent factory implementation — the provided file appears truncated.]

- **`api/rag_tools.py`** — The `_collect_sources` function body appears truncated in the provided source; the return statement is missing. [TODO: Verify the complete implementation.]

- **`core/chunker.py`** — The `split_by_words` function is truncated (`# Hard f...`). [TODO: Confirm the complete fallback implementation.]

- **`core/annotator.py`** — Contains a comment `# custom annotation logic` indicating the post-LLM annotation processing may be incomplete or project-specific logic was omitted.

- **`core/ingest.py`** — The default `OPENAI_URL_BASE` used for annotation LLM is `https://api.anthropic.com/v1`, but Anthropic's native API is not OpenAI-compatible at that path without a proxy. [TODO: Confirm whether an OpenAI-compatible proxy is in front of Anthropic, or whether this should point to the same OpenRouter URL used by the main app.]

- **`api/sessions.py`** — Sessions are persisted to `data/sessions.json`. No concurrency control (e.g. file locking) is implemented; concurrent writes on multi-worker deployments could corrupt the file.

- **`.github/scripts/shared.py`** — The `send_email` and `write_audit_entry` functions are referenced throughout the CI scripts but their implementations are truncated at the end of the shown file content. [TODO: Confirm these functions are fully implemented in the actual repository.]

- **`tool2_tech_docs.py`** — The `build_index` function references an undefined variable `r` (`{owner}/{r`...) — this appears to be a typo for `repo` in the truncated file.

- **All CI tools** — Escalation path contacts are not configured: `[TODO: fill in team contacts]` appears in the runbook generation system prompt.

- **`tool3_business_docs.py` / all tools** — Stakeholder names, go-live dates, and success metrics are intentionally left as `[TODO]` placeholders to be filled by human reviewers after AI generation.

- **Voyage AI rate limits** — The ingestion pipeline's `embed_chunks` default was previously `batch_size=20, batch_delay=22s` (free tier). It has been updated to `batch_size=126, batch_delay=0` in the current code, which implies a paid Voyage AI account is now expected. [TODO: Confirm which Voyage AI tier is in use and document the API key configuration.]