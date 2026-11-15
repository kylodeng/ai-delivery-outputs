# Architecture Document — kylodeng/Insurance-Training-Bot-main

---

## 1. Overview

The Insurance Training Bot is a Retrieval-Augmented Generation (RAG) application designed to train insurance sales agents at Sun Life Hong Kong. It provides two operational modes: a **Teacher mode** that delivers interactive coaching sessions (covering product knowledge, discovery questioning, and sales technique) via a streaming chat interface, and a **Roleplay/Assessor mode** that simulates realistic customer conversations and then scores the trainee's performance across five dimensions. The backend ingests insurance product PDFs (brochures, hospital network lists, policy documents) into a vector store, which is queried at runtime by a LangGraph agent equipped with eight purpose-built RAG tools. A FastAPI backend serves the agent over HTTP/SSE, and a separate Chainlit-based frontend consumes it. Both components are deployed as Azure App Service instances via GitHub Actions CI/CD, with AI-assisted developer tooling (code review, documentation generation, test generation, UAT facilitation) layered on top using the Anthropic Claude API.

---

## 2. Resources Deployed

| Resource | Type | Cloud Provider | Purpose |
|---|---|---|---|
| `training-bot-api` | Azure App Service | Azure | Hosts the FastAPI backend (agent, RAG tools, ingestion endpoints) |
| `training-bot-frontend` | Azure App Service | Azure | Hosts the Chainlit (or Vite) frontend UI |
| Vector Store (ChromaDB / FAISS / Pinecone) | Embedded / Managed DB | Local file or Pinecone (SaaS) | Stores embedded PDF chunks for semantic retrieval |
| `data/sessions.json` | Local flat-file persistence | Azure App Service filesystem | Persists multi-turn conversation sessions across server restarts |
| GitHub Actions Runners | CI/CD compute (ubuntu-latest) | GitHub | Runs tests, deploys both App Services, runs AI tooling workflows |
| `ai-delivery-outputs` | GitHub Repository | GitHub | Receives auto-generated code reviews, tech docs, test files, UAT packs |
| OpenRouter / LLM endpoint | External API | OpenRouter (or configurable) | Serves the chat LLM for teacher/assessor/roleplay agents |
| Anthropic Claude API | External API | Anthropic | Powers the five developer-tooling workflows (code review, docs, tests, UAT) |
| Voyage AI (inferred) | External API | Voyage AI | Embedding model for PDF chunk ingestion (rate-limit hints in code) |
| SendGrid | External SaaS | Twilio SendGrid | Sends email notifications from AI tooling workflows |

---

## 3. Data Flow

### Ingestion Path (offline / on-demand)

1. An operator places insurance PDF files under `data/Insurance-product-info/`.
2. `POST /ingest` is called on the FastAPI backend (or `python core/ingest.py` is run directly).
3. `ingest_directory()` walks the directory; for each PDF, `load_or_create_annotations()` calls the configured LLM (via `OPENAI_URL_BASE`) to produce a `.annot.json` sidecar file describing the document and each page (product name, doc type, relevance flags).
4. Relevant pages are passed to `extract_chunks_from_pdf()`, which uses `pdfplumber` to extract text, heuristically splits it into semantic units (headings, bullets, paragraphs), and assembles chunk dicts with metadata (product name, page range, file URL).
5. `embed_chunks()` sends chunks in batches to the embedding model (Voyage AI inferred from rate-limit code) via the configured `BaseVectorStore` implementation (Chroma, FAISS, or Pinecone).
6. The vector store index is saved to disk (or upserted to Pinecone). Annotation JSON sidecars are written alongside each PDF.

### Teacher / Chat Path (runtime)

1. A trainee opens the frontend (Chainlit UI on `training-bot-frontend` App Service).
2. The frontend POSTs a session creation request and chat messages to `training-bot-api`.
3. FastAPI loads the vector store from disk on startup via `lifespan()`.
4. For each user message, the FastAPI handler constructs a LangGraph teacher agent with the eight RAG tools and the shared `ChatOpenAI` LLM instance, then calls `astream_events()`.
5. The agent reasons over the message; when product knowledge is needed, it invokes one or more RAG tools (e.g. `search_product`, `lookup_exclusions`).
6. Each RAG tool calls `store.similarity_search()`, collects source metadata via `_collect_sources()`, and returns formatted context strings with inline source IDs (S1, S2, …).
7. The LLM generates a response with `[[Sn]]` citation markers; the response is streamed back to the frontend as Server-Sent Events.
8. Tool call events and source metadata are optionally surfaced in the UI (controlled by `SHOW_TOOL_CALLS`).

### Roleplay / Assessment Path (runtime)

1. A session is put into `roleplay` mode; `generate_profile()` randomly assembles a `CustomerProfile` from HK-context persona pools.
2. The roleplay system prompt is injected into a `ChatOpenAI` instance; the LLM acts as the simulated customer throughout the conversation.
3. When the roleplay ends, the FastAPI handler invokes the assessor agent (`ainvoke`) with the full conversation transcript and customer profile.
4. The assessor uses the same eight RAG tools to fact-check every product claim made by the trainee, then returns a structured assessment JSON.
5. The assessment is returned to the frontend as a final response.

### Session Persistence

6. All sessions (messages, profiles, mode, title) are serialised to `data/sessions.json` on write and loaded on server startup.

### CI/CD Path

7. A push to `main` triggers `deploy.yml`: tests run with `pytest`, then both App Services are deployed via `azure/webapps-deploy@v3` using publish profiles stored as GitHub secrets.
8. AI tooling workflows (Tools 1–5) run on PRs, schedules, or tags; they read source code via GitHub API, call Anthropic Claude, and write outputs to the `ai-delivery-outputs` repo. Email notifications are sent via SendGrid.

---

## 4. Security Posture

### What is secured

- **Secrets in CI/CD**: `AZURE_WEBAPP_PUBLISH_PROFILE_API`, `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND`, `ANTHROPIC_API_KEY`, `GH_TOKEN`, `SENDGRID_API_KEY` are stored as GitHub Actions secrets — not hardcoded.
- **Dependency pinning**: `uv` with a lockfile is used for the main application, reducing supply-chain risk relative to unpinned `pip install`.
- **CORS restriction**: The FastAPI CORS middleware restricts allowed origins to known local dev origins only. Production App Service origins are **not listed** (see gap below).
- **Streaming isolation**: Per-request source tracking uses Python `contextvars`, which is async-safe and prevents cross-request data leakage.

### Gaps and concerns — **explicitly flagged**

- 🔴 **TLS certificate verification disabled**: Both `httpx.Client(verify=False)` and `httpx.AsyncClient(verify=False)` are set on the LLM HTTP clients in `api/main.py` and `core/ingest.py`. This disables TLS certificate validation on all outbound LLM/embedding API calls, exposing the application to man-in-the-middle attacks. **This must be fixed before production use.**
- 🔴 **No API authentication on FastAPI endpoints**: There is no authentication middleware, API key check, or OAuth on any `/ingest`, `/chat`, or `/sessions` endpoints. Any user who can reach `training-bot-api` can invoke the agent, ingest documents, or read all sessions.
- 🔴 **Sessions stored in plaintext flat file**: `data/sessions.json` on the App Service filesystem contains full conversation transcripts and customer profiles. There is no encryption at rest for this file.
- 🔴 **`API_KEY` defaults to empty string**: `os.getenv("API_KEY", "")` — if the environment variable is not set, the LLM client is initialised with an empty API key. The application will silently start and fail only at first LLM call.
- 🟠 **CORS allows all methods and headers**: `allow_methods=["*"]` and `allow_headers=["*"]` are overly permissive. Should be restricted to the specific verbs and headers actually used.
- 🟠 **CORS does not include production App Service URLs**: The `allow_origins` list only contains `localhost` entries. The deployed frontend at `training-bot-frontend.azurewebsites.net` is not listed, which will break cross-origin requests in production — unless the frontend and API are on the same origin.
- 🟠 **`GH_TOKEN` scope unknown**: `GH_TOKEN` is used by AI tooling workflows to read source repos and write to `ai-delivery-outputs`. The required scope is not documented. If it carries `repo` (full) scope it is overly broad. [TODO: Confirm GH_TOKEN scope and restrict to minimum required]
- 🟠 **PDF files served unauthenticated**: `app.mount("/docs", StaticFiles(directory=str(_DATA_DIR)))` serves the entire `data/` directory over HTTP with no authentication. This includes insurance product PDFs and potentially `sessions.json`.
- 🟡 **No rate limiting**: No rate limiting is applied to any API endpoint. The LLM and embedding APIs are metered; abuse could incur significant cost.
- 🟡 **Annotation LLM uses `OPENAI_URL_BASE` defaulting to `api.anthropic.com`**: The annotator in `core/ingest.py` constructs a `ChatOpenAI` client pointing at `https://api.anthropic.com/v1` — a non-standard base URL for the OpenAI client library. This may work via the Anthropic OpenAI-compatibility layer but is fragile.
- 🟡 **No secrets scanning or SAST in CI**: None of the five workflow tools run static analysis for secrets or vulnerabilities as a gate on the `deploy` workflow.
- 🟡 **`SHOW_TOOL_CALLS` has a print-statement bug**: `print(f"SHOW_TOOL_CALLS={os.getenv("SHOW_TOOL_CALLS"," ").lower()== "true"}")` — nested quotes will cause a `SyntaxError` in Python < 3.12; in 3.13 it is valid but the logic is inverted (prints the boolean of the comparison, not the env var value).

---

## 5. Environment Variables and Secrets

| Name | Required | Sensitivity | Where set |
|---|---|---|---|
| `API_KEY` | Yes | 🔴 High — LLM provider key | App Service environment / `.env` file |
| `OPENAI_URL_BASE` | No | Low | App Service environment / `.env` file (default: `https://openrouter.ai/api/v1`) |
| `OPENAI_MODEL` | No | Low | App Service environment / `.env` file (default: `openai/gpt-oss-20b:free`) |
| `SHOW_TOOL_CALLS` | No | Low | App Service environment / `.env` file (default: `true`) |
| `ANTHROPIC_API_KEY` | Yes (AI tooling workflows) | 🔴 High | GitHub Actions secret |
| `GH_TOKEN` | Yes (AI tooling workflows) | 🔴 High — repo write access | GitHub Actions secret |
| `SENDGRID_API_KEY` | Yes (AI tooling workflows) | 🔴 High | GitHub Actions secret |
| `AZURE_WEBAPP_PUBLISH_PROFILE_API` | Yes (deploy) | 🔴 High — Azure credentials | GitHub Actions secret |
| `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND` | Yes (deploy) | 🔴 High — Azure credentials | GitHub Actions secret |
| `OUTPUT_REPO` | No | Low | GitHub Actions env (default: `ai-delivery-outputs`) |
| `OUTPUT_REPO_OWNER` | No | Low | GitHub Actions env (default: repo owner) |
| `NOTIFY_EMAIL` | No | Low | GitHub Actions env (hardcoded: `kylo.deng@capco.com`) |
| `SENDER_EMAIL` | No | Low | GitHub Actions env (hardcoded: `noreply@ai-delivery.capco.com`) |
| `VECTOR_STORE_TYPE` | [TODO: confirm name] | Low | App Service environment (Chroma/FAISS/Pinecone selection) |
| `PINECONE_API_KEY` | Conditional | 🔴 High — if Pinecone is used | App Service environment / `.env` file |

---

## 6. Dependencies

### External Services and APIs

| Dependency | Purpose | Notes |
|---|---|---|
| OpenRouter (`openrouter.ai/api/v1`) | Default LLM endpoint for teacher/assessor/roleplay agents | Configurable via `OPENAI_URL_BASE`; supports any OpenAI-compatible endpoint |
| Anthropic Claude API (`claude-sonnet-4-6`) | AI tooling workflows (code review, docs, tests, UAT) and optionally annotation LLM | Used via `anthropic` Python SDK in workflows; also referenced as annotation model |
| Voyage AI (inferred) | PDF chunk embedding | Inferred from rate-limit code (`batch_delay`, 3 RPM free tier reference); not explicitly named in provided source |
| Pinecone | Optional vector store backend | `PineconeStore` class exists in `core/vector_store.py`; requires `PINECONE_API_KEY` |
| SendGrid | Email notifications from AI tooling workflows | Used in `shared.py` `send_email()` |
| Azure App Service | Application hosting (API + frontend) | Two separate App Service instances |
| GitHub API (`api.github.com`) | AI tooling: read source files, post PR comments, write to output repo | Used in `shared.py` with `GH_TOKEN` |
| `pdfplumber` | PDF text extraction during ingestion | Python library dependency |
| LangChain / LangGraph | Agent orchestration, tool execution, LLM client abstraction | Core runtime framework |
| FastAPI | HTTP API framework | Backend web framework |
| Chainlit | Frontend chat UI | [TODO: confirm Chainlit version and whether a separate frontend repo exists] |
| httpx | Async HTTP client for LLM calls | TLS verification disabled — see Security Posture |

### Other Repositories

| Repo | Relationship |
|---|---|
| `{owner}/ai-delivery-outputs` | Output repository — receives auto-generated docs, test files, UAT packs, code review reports from AI tooling workflows |
| [TODO: Is there a separate frontend repo?] | The `deploy-frontend` job deploys `training-bot-frontend` but no frontend source directory is visible in the provided files |

---

## 7. Deployment Instructions

### Prerequisites

- Python 3.13 installed (3.12 for tooling workflows)
- [`uv`](https://github.com/astral-sh/uv) installed (`pip install uv` or `brew install uv`)
- Azure CLI authenticated with access to the target subscription
- Azure App Services `training-bot-api` and `training-bot-frontend` already provisioned
- GitHub repository secrets configured (see Section 5)

### Local Development

```bash
# 1. Clone the repository
git clone https://github.com/kylodeng/Insurance-Training-Bot-main.git
cd Insurance-Training-Bot-main

# 2. Install dependencies
uv sync

# 3. Copy and populate environment variables
cp .env.example .env   # [TODO: confirm .env.example exists]
# Edit .env: set API_KEY, OPENAI_URL_BASE, OPENAI_MODEL

# 4. Ingest PDF documents into the vector store
uv run python core/ingest.py --pdf-dir data/Insurance-product-info --verbose

# 5. Start the FastAPI backend
uv run uvicorn api.main:app --reload --port 8000

# 6. Start the frontend (separate terminal)
# [TODO: confirm frontend start command — likely `chainlit run` or `npm run dev`]
```

### Running Tests

```bash
uv run pytest tests/ -v
```

### Production Deployment (GitHub Actions — automatic)

```bash
# Deployment is triggered automatically on push to main
git push origin main

# The deploy.yml workflow will:
# 1. Run pytest (must pass)
# 2. Generate requirements