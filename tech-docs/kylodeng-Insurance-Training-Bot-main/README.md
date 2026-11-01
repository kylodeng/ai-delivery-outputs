# Insurance Training Bot

## 1. Project Overview

Insurance Training Bot is an AI-powered training platform designed to help new insurance agents in Hong Kong master sales techniques and product knowledge. It provides two modes: a **Teacher mode** for interactive coaching and concept explanation, and a **Roleplay/Assessment mode** where the agent practices with a simulated customer and receives structured performance feedback. The system uses Retrieval-Augmented Generation (RAG) over a corpus of real insurance product PDFs to ensure all product-specific answers are grounded in verified documents.

---

## 2. Tech Stack

| Component | Technology | Version/Notes |
|---|---|---|
| Backend framework | FastAPI | Python async |
| LLM orchestration | LangChain / LangGraph | `create_agent`, `astream_events`, `ainvoke` |
| LLM provider | OpenRouter (default) or Anthropic | Configurable via `OPENAI_URL_BASE` |
| Default model | `openai/gpt-oss-20b:free` | Overridable via `OPENAI_MODEL` |
| CI/CD AI tools model | `claude-sonnet-4-6` | Used in GitHub Action scripts |
| Embeddings / Vector store | Chroma, FAISS, or Pinecone | Selected via `core/vector_store.py` |
| PDF parsing | pdfplumber | Chunker in `core/chunker.py` |
| Document annotation | LLM-based (same model as main) | Cached to `.annot.json` sidecar files |
| HTTP client | httpx | SSL verification disabled (see Known Issues) |
| Dependency management | uv | `uv sync`, `uv export` |
| Python version | 3.13 (CI), 3.12 (GH Action scripts) | |
| Deployment target | Azure App Service | Two apps: API + Frontend |
| CI/CD | GitHub Actions | 7 workflows |
| AI DevOps tools | Anthropic Claude API (`claude-sonnet-4-6`) | Code review, docs, testing, UAT |
| Email notifications | SendGrid | Via `SENDGRID_API_KEY` |
| Session persistence | JSON file (`data/sessions.json`) | Survives server restarts |
| Insurance data | Sun Life Hong Kong PDFs | Stored under `data/Insurance-product-info/` |

---

## 3. Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        Client (Browser / Chainlit UI)           │
│                    Vite dev server — localhost:5173              │
└────────────────────────────┬────────────────────────────────────┘
                             │ HTTP / SSE (StreamingResponse)
┌────────────────────────────▼────────────────────────────────────┐
│                     FastAPI Backend  (api/main.py)              │
│  ┌──────────────┐  ┌────────────────┐  ┌──────────────────────┐ │
│  │ Teacher Agent│  │ Assessor Agent │  │  Roleplay Endpoint   │ │
│  │ (streaming)  │  │ (one-shot)     │  │  (LLM as customer)   │ │
│  └──────┬───────┘  └───────┬────────┘  └──────────────────────┘ │
│         │                  │                                     │
│  ┌──────▼──────────────────▼────────────────────────────────┐   │
│  │              RAG Tools  (api/rag_tools.py)               │   │
│  │  search_product · search_all · compare_plans             │   │
│  │  lookup_hospital_network · lookup_exclusions             │   │
│  │  search_claim_procedure · list_products · get_date       │   │
│  └──────────────────────────┬───────────────────────────────┘   │
│                             │                                   │
│  ┌──────────────────────────▼───────────────────────────────┐   │
│  │           Vector Store  (core/vector_store.py)           │   │
│  │      ChromaStore | LocalFAISSStore | PineconeStore       │   │
│  └──────────────────────────┬───────────────────────────────┘   │
│                             │                                   │
│  ┌──────────────────────────▼───────────────────────────────┐   │
│  │        Ingestion Pipeline  (core/ingest.py)              │   │
│  │  PDF → Annotator (LLM) → Chunker → Embedder → Store     │   │
│  └──────────────────────────────────────────────────────────┘   │
│                                                                 │
│  Static files: /docs/* → data/  (PDFs served over HTTP)        │
│  Sessions: data/sessions.json  (persisted across restarts)      │
└─────────────────────────────────────────────────────────────────┘

GitHub Actions (5 AI DevOps tools — .github/scripts/ + .github/workflows/)
  Tool 1: PR code review → PR comment + output repo report
  Tool 2: Tech documentation generation → output repo
  Tool 3: Business documentation → output repo (on release tag)
  Tool 4: Auto test generation / gap analysis → output repo
  Tool 5: UAT test pack generation / defect analysis → output repo
```

**Data flow summary:**
1. Insurance PDFs in `data/Insurance-product-info/` are ingested via `POST /ingest` (or `python core/ingest.py`), annotated by the LLM, chunked, and embedded into the vector store.
2. The FastAPI server loads the vector store on startup.
3. When a user sends a message, the appropriate LangGraph agent (Teacher or Assessor) decides which RAG tools to call, retrieves document chunks, and streams a response back with inline source citations.
4. Session state (conversation history, customer profile, mode) is stored in `data/sessions.json`.

---

## 4. Local Development Setup

1. **Clone the repository**
   ```bash
   git clone https://github.com/kylodeng/Insurance-Training-Bot-main.git
   cd Insurance-Training-Bot-main
   ```

2. **Install `uv` (Python package manager)**
   ```bash
   pip install uv
   ```

3. **Install Python dependencies**
   ```bash
   uv sync
   ```

4. **Create a `.env` file** in the project root (see [Environment Variables](#5-environment-variables) below):
   ```bash
   cp .env.example .env   # if provided, otherwise create manually
   ```

5. **Ingest insurance PDFs into the vector store**
   ```bash
   uv run python core/ingest.py
   ```
   PDFs must be placed under `data/Insurance-product-info/` before running this step. The script will annotate each PDF with the LLM and cache results to `.annot.json` sidecar files.

6. **Start the FastAPI backend**
   ```bash
   uv run uvicorn api.main:app --reload --port 8000
   ```

7. **Access the API / UI**
   - API docs: [http://localhost:8000/docs](http://localhost:8000/docs)
   - Static insurance PDFs: [http://localhost:8000/docs/](http://localhost:8000/docs/)
   - [TODO: How is the frontend/Chainlit UI started separately? Is there a `chainlit run` command or a Vite dev server?]

---

## 5. Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `API_KEY` | Yes | `""` | API key for the LLM provider (OpenRouter or Anthropic) |
| `OPENAI_URL_BASE` | No | `https://openrouter.ai/api/v1` | Base URL for the LLM API endpoint |
| `OPENAI_MODEL` | No | `openai/gpt-oss-20b:free` | LLM model name for inference and annotation |
| `SHOW_TOOL_CALLS` | No | `true` | Log and stream tool call events; overridable per-session in the UI |
| `ANTHROPIC_API_KEY` | Yes (CI only) | — | Anthropic API key for GitHub Actions AI tools (`claude-sonnet-4-6`) |
| `GH_TOKEN` | Yes (CI only) | — | GitHub personal access token for Actions scripts |
| `SENDGRID_API_KEY` | Yes (CI only) | — | SendGrid key for email notifications from Actions |
| `OUTPUT_REPO` | No (CI only) | `ai-delivery-outputs` | GitHub repo name where CI tool outputs are written |
| `OUTPUT_REPO_OWNER` | No (CI only) | `GITHUB_REPOSITORY_OWNER` | Owner of the output repo |
| `NOTIFY_EMAIL` | No (CI only) | `kylo.deng@capco.com` | Recipient email for CI tool notifications |
| `SENDER_EMAIL` | No (CI only) | `kylo.deng@capco.com` | Sender email for CI tool notifications |

> **Note:** `API_KEY`, `OPENAI_URL_BASE`, and `OPENAI_MODEL` are also used by `core/ingest.py` during the PDF annotation phase (see `_build_ingest_llm()` in `core/ingest.py`).

---

## 6. Running Tests

Tests are located in the `tests/` directory. The CI pipeline uses `uv run pytest`.

```bash
# Install dependencies first
uv sync

# Run all tests with verbose output
uv run pytest tests/ -v
```

[TODO: What test framework and test files exist in `tests/`? Are there any pytest fixtures or markers to be aware of?]

---

## 7. Deployment

### Automated Deployment (GitHub Actions)

Deployment is triggered automatically on every push to `main` after tests pass. The workflow is defined in `.github/workflows/deploy.yml`.

Two Azure App Service apps are deployed in parallel:

- **API**: app name `training-bot-api`
- **Frontend**: app name `training-bot-frontend`

Required GitHub secrets:
- `AZURE_WEBAPP_PUBLISH_PROFILE_API` — publish profile for the API App Service
- `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND` — publish profile for the Frontend App Service

The workflow generates a `requirements.txt` from the lockfile before deploying:

```bash
uv export --no-dev --format requirements-txt -o requirements.txt
```

### Manual Deployment Steps

1. **Generate `requirements.txt`**
   ```bash
   uv export --no-dev --format requirements-txt -o requirements.txt
   ```

2. **Deploy API to Azure App Service**
   ```bash
   az webapp deploy --name training-bot-api --resource-group <YOUR_RG> \
     --src-path . --type zip
   ```

3. **Deploy Frontend to Azure App Service**
   ```bash
   az webapp deploy --name training-bot-frontend --resource-group <YOUR_RG> \
     --src-path . --type zip
   ```

   [TODO: What is the Azure resource group name? Are there any App Service configuration settings (e.g., startup command, app settings) that must be set in the portal or via CLI?]

### Ingesting Data on the Server

After deployment, run the ingestion endpoint to populate the vector store:

```bash
curl -X POST https://<your-api-host>/ingest
```

Or trigger it via the API docs at `https://<your-api-host>/docs`.

---

## 8. Known Issues / TODOs

Extracted from code comments:

| Location | Issue / TODO |
|---|---|
| `api/main.py` | `httpx.Client(verify=False)` and `httpx.AsyncClient(verify=False)` — SSL certificate verification is disabled for all LLM API calls. This is a security risk and should be addressed before production use. |
| `api/main.py` | `SHOW_TOOL_CALLS` env var parsing has a bug: `print(f"SHOW_TOOL_CALLS={os.getenv("SHOW_TOOL_CALLS"," ").lower()== "true"}")` uses nested double-quotes (invalid Python 3.11 and earlier; incidentally valid in 3.12+ but unintentional). |
| `api/agent.py` | `ASSESSOR_SYSTEM` prompt is truncated in the provided source — the tool list and assessment rubric are incomplete. |
| `core/annotator.py` | Comment `# custom annotation logic` suggests the `annotate_document` function is incomplete or has placeholder logic. |
| `core/chunker.py` | `split_by_words` function body is truncated — hardcoded word-split fallback may be incomplete. |
| `core/ingest.py` | `argparse` block at the bottom of the file is truncated — CLI `--pdf-dir` argument default path resolution may be incomplete. |
| `.github/scripts/tool2_tech_docs.py` | `build_index` function references `{r` (likely `{repo}`) — apparent f-string truncation/bug. |
| `.github/scripts/shared.py` | `send_email`, `email_html`, and `write_audit_entry` functions are referenced by all tool scripts but their implementations are truncated/missing from the provided source. |
| `.github/scripts/tool1_code_review.py` | Auto-generated PR comment template is truncated mid-string. |
| `api/rag_tools.py` | `_collect_sources` function body is truncated — the return statement is missing from the provided source. |
| `api/sessions.py` | `CustomerProfile.describe()` method is truncated. |
| General | No disaster recovery (DR) configuration is evident — single Azure region deployment with no failover. |
| General | No monitoring or alerting configuration is present in the codebase (no Application Insights, no health check endpoint documented). |
| General | [TODO: What is the target go-live date and are there any hard deadlines?] |
| General | [TODO: What is the startup command configured on the Azure App Service for both the API and frontend apps?] |