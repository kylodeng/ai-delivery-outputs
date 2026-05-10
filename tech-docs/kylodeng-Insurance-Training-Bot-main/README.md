# Insurance Training Bot

## 1. Project Overview

The Insurance Training Bot is a FastAPI-based AI system that helps new insurance agents learn the craft through two modes: an interactive **Teacher mode** (ongoing streamed chat with a RAG-powered coaching agent) and a **Roleplay mode** (simulated customer conversations followed by AI-driven performance assessment). The backend uses a vector store built from insurance product PDFs (Sun Life Hong Kong products, hospital network lists, etc.) to ground all agent responses in accurate product knowledge. A suite of five GitHub Actions AI workflows (code review, tech docs, business docs, auto-testing, and UAT facilitation) is also included, all powered by Anthropic Claude.

---

## 2. Tech Stack

| Component | Technology | Version/Notes |
|---|---|---|
| Backend API | FastAPI | Python async |
| LLM (agent/roleplay) | OpenAI-compatible via OpenRouter | Default: `openai/gpt-oss-20b:free`; configurable |
| LLM (ingest annotation) | Anthropic Claude (via OpenAI-compatible wrapper) | Default: `claude-sonnet-4-6` |
| LLM (GitHub Actions tools) | Anthropic Claude (`claude-sonnet-4-6`) | Direct Anthropic SDK |
| Agent framework | LangChain / LangGraph | `create_agent`, `astream_events` |
| Vector store | Chroma / Local FAISS / Pinecone | Selectable via `core/vector_store.py` |
| Embeddings | [TODO: which embedding model/provider is configured?] | Voyage AI free-tier referenced in comments |
| PDF parsing | pdfplumber | — |
| Package manager | uv (astral-sh) | Python 3.13 (CI), 3.12 (Actions scripts) |
| HTTP client | httpx | SSL verification disabled — see Known Issues |
| Email delivery | SendGrid | GitHub Actions workflows only |
| Deployment target | Azure App Service | Two apps: `training-bot-api`, `training-bot-frontend` |
| CI/CD | GitHub Actions | `deploy.yml` + 5 AI tool workflows |
| Frontend | [TODO: what technology is the frontend? Chainlit is referenced in comments but no frontend source files were provided] | Served separately on Azure App Service |

---

## 3. Architecture

```
┌─────────────────────────────────────────────────────────┐
│                     Client (Frontend)                   │
│          Chainlit UI / Vite dev server :5173            │
└───────────────────────┬─────────────────────────────────┘
                        │ HTTP / SSE (StreamingResponse)
┌───────────────────────▼─────────────────────────────────┐
│               FastAPI Backend  (api/main.py)            │
│                                                         │
│  /ingest  →  core/ingest.py  →  PDF chunker/annotator   │
│  /chat    →  api/agent.py    →  LangGraph Teacher Agent │
│  /roleplay →  api/agent.py   →  LangGraph Assessor      │
│  /docs    →  StaticFiles     →  data/ (PDFs served)     │
│                                                         │
│  api/rag_tools.py  (8 LangChain tools)                  │
│    ↕                                                    │
│  core/vector_store.py  (Chroma / FAISS / Pinecone)      │
│    ↑                                                    │
│  core/ingest.py + core/chunker.py + core/annotator.py   │
│    ↑                                                    │
│  data/Insurance-product-info/  (PDFs + .annot.json)     │
└─────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────┐
│           GitHub Actions AI Delivery Tools              │
│  tool1: Code Review  (PR / cron / manual)               │
│  tool2: Tech Docs    (merge to main / cron)             │
│  tool3: Business Docs (release tag / manual)            │
│  tool4: Auto Testing  (PR / cron / manual)              │
│  tool5: UAT          (release branch / manual)          │
│  All tools → .github/scripts/shared.py → Claude API    │
│           → outputs written to ai-delivery-outputs repo │
│           → PR comments + SendGrid email notifications  │
└─────────────────────────────────────────────────────────┘
```

- **Ingestion**: PDFs under `data/Insurance-product-info/` are processed by `core/ingest.py`. Each PDF is annotated once (LLM call via `core/annotator.py`) and the annotation is cached to a `.annot.json` sidecar file. Pages marked `relevant: false` are filtered before chunking. Chunks are embedded and stored in the vector store.
- **RAG tools**: `api/rag_tools.py` exposes 8 LangChain tools to the LangGraph agents. Source tracking uses `contextvars` to collect citation metadata across async tool calls within a single request.
- **Sessions**: `api/sessions.py` persists multi-turn session state (including randomly generated Hong Kong customer profiles for roleplay) to `data/sessions.json`.
- **Streaming**: Teacher-mode responses are streamed via `astream_events`; the FastAPI endpoint returns a `StreamingResponse`.

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

4. **Create a `.env` file** in the project root with the required variables (see [Environment Variables](#5-environment-variables) section):
   ```bash
   cp .env.example .env   # if provided, otherwise create manually
   ```
   Minimum required:
   ```env
   API_KEY=<your-openrouter-or-compatible-api-key>
   OPENAI_URL_BASE=https://openrouter.ai/api/v1
   OPENAI_MODEL=openai/gpt-oss-20b:free
   ```

5. **Place insurance product PDFs** in `data/Insurance-product-info/` (subdirectories are supported).

6. **Ingest PDFs into the vector store**
   ```bash
   uv run python -m core.ingest
   ```
   Or via the API endpoint after starting the server:
   ```bash
   curl -X POST http://localhost:8000/ingest
   ```

7. **Start the FastAPI development server**
   ```bash
   uv run uvicorn api.main:app --reload --port 8000
   ```

8. **Access the API**
   - API docs: [http://localhost:8000/docs](http://localhost:8000/docs)
   - Static PDF files: [http://localhost:8000/docs/](http://localhost:8000/docs/)

9. **[TODO: How is the frontend started locally? Is it a separate Chainlit process, a Vite app, or both?]**

---

## 5. Environment Variables

### Application (`api/main.py`, `core/ingest.py`)

| Variable | Required | Default | Description |
|---|---|---|---|
| `API_KEY` | Yes | `""` | API key for the LLM provider (OpenRouter or compatible) |
| `OPENAI_URL_BASE` | No | `https://openrouter.ai/api/v1` | Base URL for the OpenAI-compatible LLM endpoint |
| `OPENAI_MODEL` | No | `openai/gpt-oss-20b:free` | Model identifier for chat/agent calls |
| `SHOW_TOOL_CALLS` | No | `true` | Log and stream tool call events by default; overridable per-session in UI |

### GitHub Actions Workflows (`.github/scripts/shared.py`)

| Variable | Required | Default | Description |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | — | Anthropic API key for Claude (all 5 tools) |
| `GH_TOKEN` | Yes | — | GitHub token with repo read/write access |
| `SENDGRID_API_KEY` | Yes | — | SendGrid API key for email notifications |
| `OUTPUT_REPO` | No | `ai-delivery-outputs` | Name of the GitHub repo where tool outputs are written |
| `OUTPUT_REPO_OWNER` | No | `GITHUB_REPOSITORY_OWNER` | Owner of the output repo |
| `NOTIFY_EMAIL` | No | `kylo.deng@capco.com` | Recipient email for notifications |
| `SENDER_EMAIL` | No | `kylo.deng@capco.com` | Sender email address for SendGrid |

### Azure Deployment (GitHub Actions secrets)

| Variable | Required | Default | Description |
|---|---|---|---|
| `AZURE_WEBAPP_PUBLISH_PROFILE_API` | Yes (deploy) | — | Azure publish profile for `training-bot-api` App Service |
| `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND` | Yes (deploy) | — | Azure publish profile for `training-bot-frontend` App Service |

---

## 6. Running Tests

Tests are located in the `tests/` directory. The CI pipeline uses `uv` and `pytest`.

```bash
# Run all tests with verbose output
uv run pytest tests/ -v
```

```bash
# Run a specific test file
uv run pytest tests/test_chunker.py -v
```

> [TODO: Are there integration tests that require a running vector store or live API keys? Are any tests marked to skip without credentials?]

---

## 7. Deployment

### Automatic Deployment (CI/CD)

Pushes to `main` trigger the `Test & Deploy` workflow (`.github/workflows/deploy.yml`), which:
1. Runs `pytest` tests
2. On success, exports a `requirements.txt` via `uv export`
3. Deploys to two Azure App Services: `training-bot-api` and `training-bot-frontend`

The required GitHub secrets must be configured in the repository settings:
- `AZURE_WEBAPP_PUBLISH_PROFILE_API`
- `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND`

### Manual Steps

1. **Generate `requirements.txt`** (for Azure or any non-uv environment):
   ```bash
   uv export --no-dev --format requirements-txt -o requirements.txt
   ```

2. **Ingest PDFs on the deployed instance** — after first deploy, call the ingest endpoint:
   ```bash
   curl -X POST https://<your-api-app>.azurewebsites.net/ingest
   ```
   Or run the ingest script directly if you have shell access:
   ```bash
   python -m core.ingest --pdf-dir data/Insurance-product-info
   ```

3. **[TODO: Are application environment variables (`API_KEY`, `OPENAI_URL_BASE`, etc.) set via Azure App Service Configuration or a Key Vault reference? There is no IaC (Terraform/Bicep) in the repository for the App Service resources themselves.]**

### GitHub Actions AI Tools

The five AI delivery tools are triggered automatically (see workflow triggers) or manually via **Actions → workflow → Run workflow**. Required secrets for all tools:

```
ANTHROPIC_API_KEY
GH_TOKEN
SENDGRID_API_KEY
```

The output repository (`ai-delivery-outputs`) must exist and the `GH_TOKEN` must have write access to it.

---

## 8. Known Issues / TODOs

Extracted from code comments and source files:

| Location | Issue / TODO |
|---|---|
| `api/main.py` | SSL certificate verification is **disabled** for all httpx clients (`verify=False`). This is a security risk in production. |
| `api/main.py` | `print(f"SHOW_TOOL_CALLS=...")` debug statement left in production code. |
| `core/ingest.py` | Default `OPENAI_URL_BASE` in `_build_ingest_llm()` points to `https://api.anthropic.com/v1` but the model env var defaults to `claude-sonnet-4-6` — may conflict with the main app's OpenRouter configuration. |
| `core/ingest.py` | Default `batch_delay=0` in `embed_chunks()` — code comment notes that previous default was 22 s to stay under Voyage AI free-tier 3 RPM limit; setting to 0 may cause rate-limit errors with free-tier embedding accounts. |
| `api/agent.py` | `ASSESSOR_SYSTEM` prompt is truncated in the provided source — the full system prompt content is not complete in the file extract. |
| `api/rag_tools.py` | Source file is truncated — `_collect_sources` function body is incomplete. |
| `core/chunker.py` | `split_by_words` function is truncated in the provided source. |
| `.github/scripts/tool2_tech_docs.py` | `build_index` function references `{r` (appears to be a truncated f-string variable — likely a bug or truncation artefact). |
| `.github/scripts/tool4_auto_testing.py` | `build_test_report` table formatting is truncated. |
| `.github/scripts/tool5_uat.py` | `build_test_pack_csv` function signature is truncated (`list[d` is incomplete). |
| `agent.py` | Uses `from langchain.agents import create_agent` — `create_agent` is not a standard LangChain export; this may cause an import error. [TODO: Verify correct import — likely `create_react_agent` or a LangGraph equivalent.] |
| All GitHub Actions scripts | `send_email`, `email_html`, and `write_audit_entry` are imported from `shared.py` but those functions are not present in the truncated `shared.py` source provided — confirm they exist in the full file. |
| `api/sessions.py` | `CustomerProfile.describe()` method is truncated. |
| Deployment | No IaC (Terraform/Bicep) found in the repository for provisioning the Azure App Service resources. |
| Deployment | No Dockerfile or container configuration found — [TODO: Is this deployed as a ZIP deploy or container?] |
| Monitoring | No monitoring, alerting, or health-check endpoints are evidenced in the source. |
| Disaster Recovery | No DR configuration evidenced — single-region Azure deployment. |