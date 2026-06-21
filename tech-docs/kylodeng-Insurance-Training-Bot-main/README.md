# Insurance Training Bot

## 1. Project Overview

Insurance Training Bot is an AI-powered training platform for insurance sales agents in Hong Kong. It provides two modes: a **teacher mode** for interactive learning (product knowledge, discovery questioning, sales techniques) and a **roleplay/assessment mode** where trainees practice conversations with simulated customers and receive structured performance feedback. The system is backed by a RAG (Retrieval-Augmented Generation) pipeline that ingests Sun Life insurance product PDFs and exposes them to LangGraph agents via eight specialised tools.

---

## 2. Tech Stack

| Component | Technology | Version/Notes |
|---|---|---|
| Backend API | FastAPI | Python 3.13 |
| LLM Provider | OpenRouter (default) / Anthropic Claude | `openai/gpt-oss-20b:free` default; `claude-sonnet-4-6` used in CI tools |
| Agent Framework | LangGraph / LangChain | `langchain`, `langchain-openai`, `langchain-core` |
| Vector Store | ChromaDB / local FAISS / Pinecone | Selectable via `core/vector_store.py`; default auto-detected |
| Embeddings | [TODO: which embedding model/provider is configured?] | Voyage AI mentioned in comments (free-tier rate limits noted) |
| PDF Processing | pdfplumber | Used in `core/chunker.py` |
| Dependency Manager | uv (Astral) | `uv sync`, `uv export` |
| CI/CD | GitHub Actions | 5 AI-delivery workflows + deploy workflow |
| Deployment | Azure App Service | Two apps: `training-bot-api`, `training-bot-frontend` |
| AI Delivery Tools | Anthropic Claude (`claude-sonnet-4-6`) | Code review, tech docs, business docs, auto testing, UAT |
| Email Notifications | SendGrid | Used in CI workflow scripts |
| Frontend | [TODO: what frontend framework is used — Vite is referenced in CORS config but no frontend source files provided] | Vite dev server on port 5173 |
| UI (alternative) | Chainlit | Mentioned in CORS config and comments |
| Session Persistence | JSON file (`data/sessions.json`) | Survives server restarts |

---

## 3. Architecture

```
┌─────────────────────────────────────────────────────────┐
│                      Clients                            │
│   Chainlit UI (port 8000)  │  Vite Frontend (port 5173) │
└──────────────┬──────────────────────────────────────────┘
               │ HTTP / SSE (streaming)
┌──────────────▼──────────────────────────────────────────┐
│               FastAPI Backend  (api/main.py)            │
│  • /ingest  — trigger PDF ingestion                     │
│  • /chat    — teacher mode (streamed via SSE)           │
│  • /roleplay — customer simulation                      │
│  • /assess  — post-roleplay scoring                     │
│  • /docs    — static file serving for PDFs              │
│  Sessions persisted to data/sessions.json               │
└──────┬──────────────────────────┬───────────────────────┘
       │                          │
┌──────▼──────┐          ┌────────▼────────────┐
│ LangGraph   │          │  core RAG library   │
│ Agents      │◄────────►│  (core/)            │
│ (api/agent) │  tools   │  • annotator.py     │
│             │          │  • chunker.py       │
│  Teacher    │          │  • ingest.py        │
│  Assessor   │          │  • vector_store.py  │
└──────┬──────┘          └────────┬────────────┘
       │                          │
┌──────▼──────┐          ┌────────▼────────────┐
│  LLM API    │          │  Vector Store       │
│ (OpenRouter │          │  Chroma/FAISS/      │
│  /Anthropic)│          │  Pinecone           │
└─────────────┘          └─────────────────────┘

┌─────────────────────────────────────────────────────────┐
│            GitHub Actions CI/CD (.github/workflows/)    │
│  tool1: Claude code review on PRs                       │
│  tool2: Tech doc generation on merge to main            │
│  tool3: Business doc generation on release tags         │
│  tool4: Auto test generation on PRs                     │
│  tool5: UAT test pack generation on release branches    │
│  deploy: pytest → Azure App Service (API + Frontend)    │
└─────────────────────────────────────────────────────────┘
```

**Key interactions:**
- On startup, FastAPI loads the vector store from disk and warns if it is empty (run `POST /ingest` first).
- The teacher agent streams responses via `astream_events`; the assessor agent runs one-shot via `ainvoke`.
- Both agents share eight RAG tools (`api/rag_tools.py`) that query the vector store and track inline citation sources per request using `contextvars` (async-safe).
- PDF ingestion (`core/ingest.py`) annotates each document once with an LLM, caches annotations to `.annot.json` sidecar files, then chunks and embeds into the vector store.
- Sessions are stored as JSON on disk; each session carries a `Mode` (`teacher` or `roleplay`) and an optional `CustomerProfile` generated randomly from Hong Kong-context personas.

---

## 4. Local Development Setup

1. **Clone the repository**

```bash
git clone https://github.com/kylodeng/Insurance-Training-Bot-main.git
cd Insurance-Training-Bot-main
```

2. **Install uv** (if not already installed)

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

3. **Install Python dependencies**

```bash
uv sync
```

4. **Create a `.env` file** in the project root (see [Environment Variables](#5-environment-variables) below)

```bash
cp .env.example .env   # if example exists, otherwise create manually
```

5. **Ingest insurance product PDFs** — place PDFs under `data/Insurance-product-info/` then run:

```bash
uv run python -m core.ingest --pdf-dir data/Insurance-product-info
```

   Or via the API after starting the server:

```bash
curl -X POST http://localhost:8000/ingest
```

6. **Start the FastAPI backend**

```bash
uv run uvicorn api.main:app --reload --port 8000
```

7. **Access the API**

```
http://localhost:8000        # API root
http://localhost:8000/docs   # Swagger UI
```

8. **(Optional) Start the Vite frontend** — [TODO: confirm frontend directory and start command; `npm run dev` assumed based on Vite reference in CORS config]

---

## 5. Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `API_KEY` | Yes | `""` | API key for the LLM provider (OpenRouter or Anthropic) |
| `OPENAI_URL_BASE` | No | `https://openrouter.ai/api/v1` | Base URL for the LLM API endpoint |
| `OPENAI_MODEL` | No | `openai/gpt-oss-20b:free` | Model name to use for inference |
| `SHOW_TOOL_CALLS` | No | `true` | Log and stream tool call events; overridable per-session in the UI |
| `ANTHROPIC_API_KEY` | Yes (CI only) | — | Anthropic API key used by the five GitHub Actions AI-delivery tools |
| `GH_TOKEN` | Yes (CI only) | — | GitHub personal access token used by CI scripts to read repos and post PR comments |
| `SENDGRID_API_KEY` | Yes (CI only) | — | SendGrid API key for email notifications from CI workflows |
| `OUTPUT_REPO` | No (CI only) | `ai-delivery-outputs` | GitHub repo name where CI tool outputs (reports, docs) are written |
| `OUTPUT_REPO_OWNER` | No (CI only) | `GITHUB_REPOSITORY_OWNER` | Owner of the output repo |
| `NOTIFY_EMAIL` | No (CI only) | `kylo.deng@capco.com` | Recipient email for CI notifications |
| `SENDER_EMAIL` | No (CI only) | `kylo.deng@capco.com` | Sender email for CI notifications |

> **Note:** `AZURE_WEBAPP_PUBLISH_PROFILE_API` and `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND` must be set as GitHub Actions secrets for deployment (see [Deployment](#7-deployment)).

---

## 6. Running Tests

```bash
uv run pytest tests/ -v
```

The `test` job in `.github/workflows/deploy.yml` runs this command automatically on every push and pull request targeting `main`.

> [TODO: what test files exist under `tests/`? No test source files were provided.]

---

## 7. Deployment

Deployment is handled automatically by `.github/workflows/deploy.yml` on every push to `main` (after tests pass). To deploy manually:

1. **Export `requirements.txt` from the lockfile**

```bash
uv export --no-dev --format requirements-txt -o requirements.txt
```

2. **Deploy the API to Azure App Service**

The workflow uses `azure/webapps-deploy@v3` with app name `training-bot-api`. Ensure the GitHub secret `AZURE_WEBAPP_PUBLISH_PROFILE_API` is set with the publish profile downloaded from the Azure portal.

```bash
# The GitHub Actions workflow handles this step automatically.
# For manual deployment via Azure CLI:
az webapp deploy --resource-group <rg> --name training-bot-api --src-path .
```

3. **Deploy the Frontend to Azure App Service**

App name `training-bot-frontend`, secret `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND`.

```bash
az webapp deploy --resource-group <rg> --name training-bot-frontend --src-path .
```

> [TODO: are the API and frontend deployed from the same repository root, or are there separate build steps for the frontend?]

**CI/CD AI delivery tools** (tools 1–5) run on their own triggers and write outputs to the `ai-delivery-outputs` GitHub repository. No manual deployment steps are needed for these.

---

## 8. Known Issues / TODOs

Extracted from code comments:

| Location | Issue / TODO |
|---|---|
| `api/main.py` | SSL verification is disabled (`verify=False`) on both `httpx.Client` and `httpx.AsyncClient` — not suitable for production without a valid certificate chain |
| `api/main.py` | `SHOW_TOOL_CALLS` print statement contains a syntax quirk: `print(f"SHOW_TOOL_CALLS={os.getenv("SHOW_TOOL_CALLS"," ").lower()== "true"}")` — nested quotes may cause issues on some Python versions |
| `api/agent.py` | `from langchain.agents import create_agent` is imported but the rest of the agent factory functions (`make_teacher_agent`, `make_assessor_agent`) are not shown — [TODO: confirm agent construction pattern] |
| `core/ingest.py` | Default `batch_size=20` and `batch_delay=22s` comments reference Voyage AI free-tier 3 RPM limit — with a paid account raise `batch_size` to 128 and set `batch_delay=0` |
| `core/annotator.py` | Comment `# custom annotation logic` suggests the annotator has placeholder/incomplete logic |
| `core/chunker.py` | `split_by_words` function is truncated in the provided source — implementation may be incomplete |
| `tool2_tech_docs.py` | `build_index` function references an undefined variable `r` (likely a typo for `repo`) |
| `tool1_code_review.py` | `review_pr` function's PR comment body is truncated — `_Auto-generated by AI` string is not closed |
| `.github/scripts/shared.py` | `send_email`, `email_html`, and `write_audit_entry` functions are referenced by all five tool scripts but their implementations are not present in the truncated `shared.py` file |
| All CI workflows | `NOTIFY_EMAIL` and `SENDER_EMAIL` are hardcoded to `kylo.deng@capco.com` — should be parameterised for reuse |
| `api/sessions.py` | Sessions are persisted to a local JSON file (`data/sessions.json`) — this will not scale horizontally across multiple Azure App Service instances |
| `tool5_uat.py` | `SYSTEM_ANALYSE` and escalation sections reference `[TESTER: verify this]` placeholders that testers must manually complete |
| Various agent system prompts | Escalation path in runbook template is `[TODO: fill in team contacts]` |