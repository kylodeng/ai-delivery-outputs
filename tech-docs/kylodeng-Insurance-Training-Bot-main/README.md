# Insurance Training Bot

## 1. Project Overview

Insurance Training Bot is an AI-powered training platform for new insurance sales agents, specifically tailored to the Hong Kong insurance market. It provides two modes: a **Teacher mode** for interactive learning (explaining products, quizzes, roleplay exercises) and an **Assessor mode** for evaluating a trainee's performance after a roleplay session. The system is backed by a RAG (Retrieval-Augmented Generation) pipeline that ingests real insurance product PDFs so agents can query accurate, cited product information.

---

## 2. Tech Stack

| Component | Technology | Version/Notes |
|---|---|---|
| Backend API | FastAPI | Python, async |
| LLM Orchestration | LangGraph / LangChain | `create_agent`, `astream_events`, `ainvoke` |
| LLM Provider | OpenRouter (default) / Anthropic | Configurable via env; default model `openai/gpt-oss-20b:free` |
| Embeddings / Vector Store | ChromaDB, FAISS, or Pinecone | Selectable via `core/vector_store.py` |
| PDF Ingestion | pdfplumber | Custom chunker + LLM-based annotator |
| Session Persistence | JSON file (`data/sessions.json`) | Survives server restarts |
| Frontend | [TODO: What frontend framework/technology is used? Vite is referenced in CORS config but no frontend source files are present] | Served separately, Vite dev server on port 5173 |
| CI/CD | GitHub Actions | 5 AI-assisted workflow tools |
| AI Workflow Tools | Anthropic Claude (`claude-sonnet-4-6`) | Code review, tech docs, business docs, auto-testing, UAT |
| Email Notifications | SendGrid | Via `shared.py` |
| Deployment | Azure App Service | Two apps: `training-bot-api` and `training-bot-frontend` |
| Package Manager | uv (astral-sh) | Python 3.13 for CI |
| HTTP Client | httpx | SSL verification disabled — see Known Issues |

---

## 3. Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    Client (Browser / Chainlit UI)        │
│                    http://localhost:5173 or :8000        │
└───────────────────────────┬─────────────────────────────┘
                            │ HTTP / SSE (streaming)
┌───────────────────────────▼─────────────────────────────┐
│                    FastAPI Backend (api/main.py)         │
│  ┌──────────────┐  ┌────────────────┐  ┌─────────────┐  │
│  │ Session Mgmt │  │  RAG Tools     │  │ Static /docs│  │
│  │ sessions.py  │  │  rag_tools.py  │  │ (PDF files) │  │
│  └──────────────┘  └───────┬────────┘  └─────────────┘  │
│                            │                             │
│  ┌─────────────────────────▼──────────────────────────┐  │
│  │         LangGraph Agents (api/agent.py)            │  │
│  │   Teacher Agent (streaming)   Assessor Agent       │  │
│  └─────────────────────────┬──────────────────────────┘  │
└────────────────────────────┼────────────────────────────┘
                             │ LangChain LLM calls
                    ┌────────▼─────────┐
                    │  OpenRouter API  │
                    │  (or Anthropic)  │
                    └──────────────────┘

┌─────────────────────────────────────────────────────────┐
│              Vector Store (core/)                        │
│  ChromaStore | LocalFAISSStore | PineconeStore           │
│  Populated via POST /ingest → core/ingest.py             │
│  ← PDF files in data/Insurance-product-info/             │
└─────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────┐
│              GitHub Actions CI/CD                        │
│  Tool 1: Claude Code Review (on PR)                      │
│  Tool 2: Tech Docs generation (on push to main)          │
│  Tool 3: Business Docs (on release tag)                  │
│  Tool 4: Auto Test generation (on PR / schedule)         │
│  Tool 5: UAT facilitation (on release branch)            │
│  → deploy-api + deploy-frontend to Azure App Service     │
└─────────────────────────────────────────────────────────┘
```

**Data flow summary:**
1. Insurance product PDFs are placed in `data/Insurance-product-info/`.
2. `POST /ingest` triggers `core/ingest.py`, which annotates pages via LLM, chunks text, embeds, and saves to the vector store.
3. On user chat requests, the FastAPI backend routes to either the Teacher or Assessor LangGraph agent.
4. Agents call RAG tools (`api/rag_tools.py`) to retrieve relevant chunks with source citations before answering.
5. Teacher agent responses are streamed via SSE; Assessor agent is invoked once after roleplay ends.
6. Sessions (including message history and customer profiles) are persisted to `data/sessions.json`.

---

## 4. Local Development Setup

1. **Clone the repository**

```bash
git clone https://github.com/kylodeng/Insurance-Training-Bot-main.git
cd Insurance-Training-Bot-main
```

2. **Install uv (Python package manager)**

```bash
pip install uv
```

3. **Install Python dependencies**

```bash
uv sync
```

4. **Copy and configure environment variables**

```bash
cp .env.example .env
# Edit .env — see Environment Variables section below
```

5. **Add insurance product PDFs**

```bash
# Place PDF files under:
mkdir -p data/Insurance-product-info
# Copy your PDF files into this directory (subdirectories are supported)
```

6. **Ingest PDFs into the vector store**

```bash
uv run python -m core.ingest --pdf-dir data/Insurance-product-info --verbose
# OR via the API after starting the server:
# curl -X POST http://localhost:8000/ingest
```

7. **Start the FastAPI backend**

```bash
uv run uvicorn api.main:app --reload --port 8000
```

8. **Access the API**

```
http://localhost:8000
API docs: http://localhost:8000/docs
PDF files served at: http://localhost:8000/docs/<filename>
```

9. **Start the frontend** (if applicable)

```bash
# [TODO: What command starts the frontend? Only a Vite dev server on port 5173 is referenced in CORS config]
```

---

## 5. Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `API_KEY` | Yes | `""` | API key for the LLM provider (OpenRouter or Anthropic) |
| `OPENAI_URL_BASE` | No | `https://openrouter.ai/api/v1` | Base URL for the LLM API endpoint |
| `OPENAI_MODEL` | No | `openai/gpt-oss-20b:free` | LLM model name to use for chat and annotation |
| `SHOW_TOOL_CALLS` | No | `true` | Log and stream tool call events (`true`/`false`) |
| `ANTHROPIC_API_KEY` | Yes (CI only) | — | Anthropic API key used by GitHub Actions AI tools |
| `GH_TOKEN` | Yes (CI only) | — | GitHub personal access token for Actions workflows |
| `SENDGRID_API_KEY` | Yes (CI only) | — | SendGrid API key for email notifications from CI workflows |
| `OUTPUT_REPO` | No (CI only) | `ai-delivery-outputs` | GitHub repo name where CI tool outputs are written |
| `OUTPUT_REPO_OWNER` | No (CI only) | `GITHUB_REPOSITORY_OWNER` | Owner of the output repo |
| `NOTIFY_EMAIL` | No (CI only) | `kylo.deng@capco.com` | Recipient email for CI notification emails |
| `SENDER_EMAIL` | No (CI only) | `kylo.deng@capco.com` | Sender address for CI notification emails |

> **Note:** `OPENAI_URL_BASE` and `OPENAI_MODEL` are also used by `core/ingest.py` for LLM-based PDF annotation. Set them to your Anthropic endpoint and model if using Claude directly (e.g. `https://api.anthropic.com/v1` and `claude-sonnet-4-6`).

---

## 6. Running Tests

Tests are located in the `tests/` directory. Run them with:

```bash
uv run pytest tests/ -v
```

The CI pipeline (`deploy.yml`) runs this command automatically on every push and pull request to `main` before deployment.

[TODO: Are there any test fixtures, conftest.py files, or required environment variables needed to run the tests locally?]

---

## 7. Deployment

### Automatic Deployment (GitHub Actions)

Deployment is triggered automatically on every push to `main` (after tests pass):

1. **API** → deployed to Azure App Service app named `training-bot-api`
2. **Frontend** → deployed to Azure App Service app named `training-bot-frontend`

Required GitHub repository secrets:

```
AZURE_WEBAPP_PUBLISH_PROFILE_API       # Publish profile for training-bot-api
AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND  # Publish profile for training-bot-frontend
ANTHROPIC_API_KEY
GH_TOKEN
SENDGRID_API_KEY
```

### Manual / First-Time Deployment

1. **Export dependencies**

```bash
uv export --no-dev --format requirements-txt -o requirements.txt
```

2. **Deploy API to Azure App Service**

```bash
# Via Azure CLI:
az webapp deploy --resource-group <rg> --name training-bot-api --src-path .
```

3. **Ingest PDFs on the deployed instance**

```bash
curl -X POST https://training-bot-api.azurewebsites.net/ingest
```

4. **Configure environment variables in Azure**

```bash
az webapp config appsettings set \
  --resource-group <rg> \
  --name training-bot-api \
  --settings API_KEY="..." OPENAI_URL_BASE="..." OPENAI_MODEL="..."
```

[TODO: What startup command is configured for the Azure App Service? (e.g. `uvicorn api.main:app --host 0.0.0.0 --port 8000`)]

### AI Workflow Tools (GitHub Actions)

Five AI-assisted tools run as GitHub Actions workflows:

| Tool | Trigger | Purpose |
|---|---|---|
| Tool 1 — Code Review | PR open/sync, weekly Monday 08:00 UTC, manual | Claude reviews PR diff, posts comments |
| Tool 2 — Tech Docs | Push to main, weekly Sunday 06:00 UTC, manual | Generates README, ARCHITECTURE, RUNBOOK |
| Tool 3 — Business Docs | Release tag (`v*`), manual dispatch | Generates solution overview + gap questionnaire |
| Tool 4 — Auto Testing | PR on `src/**` / `*.py` / `*.js` / `*.ts`, weekly Wednesday 07:00 UTC, manual | Generates test files or coverage gap analysis |
| Tool 5 — UAT | Release branch creation (`release/*`), manual dispatch | Generates UAT test pack or analyses completed results CSV |

---

## 8. Known Issues / TODOs

Extracted from code comments:

- **SSL verification disabled** — `httpx.Client(verify=False)` and `httpx.AsyncClient(verify=False)` are used in `api/main.py` and `core/ingest.py`. This suppresses SSL certificate verification and is a security risk in production.

- **`shared.py` is truncated** — The `send_email`, `email_html`, and `write_audit_entry` functions referenced throughout all tool scripts are missing from the provided `shared.py` source. [TODO: Are these functions defined elsewhere, or is the file incomplete?]

- **`tool2_tech_docs.py` has a syntax error** — The `build_index` function ends with `f"# Tech Documentation Index — {owner}/{r` — the string is cut off. [TODO: Complete the `build_index` function.]

- **`tool1_code_review.py` comment block truncated** — The PR comment f-string ends abruptly with `_Auto-generated by AI`. [TODO: Complete the comment template.]

- **`api/agent.py` is incomplete** — The file shows `from langchain.agents import create_agent` and two system prompt strings but the actual `make_teacher_agent` and `make_assessor_agent` factory functions are not shown. The `ASSESSOR_SYSTEM` prompt is also truncated. [TODO: Confirm agent factory implementations.]

- **Annotation custom logic placeholder** — `core/annotator.py` contains the comment `# custom annotation logic` with no implementation shown after the initial JSON parse.

- **`core/chunker.py` truncated** — `split_by_words` function is cut off. [TODO: Confirm the hard word-split fallback implementation.]

- **`core/ingest.py` truncated** — The `argparse` block at the bottom is cut off. [TODO: Confirm CLI argument definitions for `--pdf-dir`.]

- **Session storage is file-based** — Sessions are persisted to `data/sessions.json` with no locking mechanism, which may cause race conditions under concurrent load.

- **Escalation path not defined** — The RUNBOOK template generated by Tool 2 includes `[TODO: fill in team contacts]` for the escalation path section.

- **Hardcoded email addresses** — `NOTIFY_EMAIL` and `SENDER_EMAIL` default to `kylo.deng@capco.com` in both `shared.py` and CI workflow env blocks. These should be parameterised for other deployments.

- **Rate-limit defaults for embedding** — A comment in `core/ingest.py` notes that the default `batch_delay=22s` is set for Voyage AI free-tier (3 RPM). With a paid account, `batch_size` can be raised to 128 and `batch_delay` set to 0.