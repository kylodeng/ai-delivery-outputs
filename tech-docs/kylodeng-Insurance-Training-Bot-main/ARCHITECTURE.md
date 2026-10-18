# Architecture Document — kylodeng/Insurance-Training-Bot

---

## 1. Overview

The Insurance Training Bot is a RAG-powered (Retrieval-Augmented Generation) AI training platform designed to help insurance agents learn product knowledge and practice sales skills. It consists of two deployed components: a FastAPI backend that hosts a LangGraph agent capable of both "teacher mode" (interactive coaching) and "assessor mode" (post-roleplay evaluation), and a frontend UI (implied Chainlit/Vite). The backend ingests Sun Life Hong Kong insurance product PDFs into a vector store (ChromaDB, FAISS, or Pinecone), exposes RAG tools to the LLM agent, and streams responses to the client. A suite of five AI-powered GitHub Actions workflows (code review, tech docs, business docs, auto-testing, UAT) augments the SDLC using Anthropic Claude.

---

## 2. Resources Deployed

| Resource | Type | Cloud Provider | Purpose |
|---|---|---|---|
| `training-bot-api` | Azure App Service (Web App) | Azure | Hosts the FastAPI backend (LangGraph agent, RAG tools, PDF ingest endpoint) |
| `training-bot-frontend` | Azure App Service (Web App) | Azure | Hosts the frontend UI (Chainlit or Vite-based) |
| Vector Store (ChromaDB / FAISS / Pinecone) | Embedded or managed DB | Local / Azure / Pinecone (runtime-configured) | Stores document embeddings for RAG retrieval |
| GitHub Actions Runners | CI/CD Compute (ubuntu-latest) | GitHub | Run tests, generate requirements, and deploy both App Services |
| `ai-delivery-outputs` | GitHub Repository | GitHub | Stores AI-generated documentation, test reports, UAT packs, and audit logs |
| SendGrid | Email delivery service | Twilio/SendGrid (SaaS) | Sends notification emails after AI workflow runs |
| Anthropic Claude (`claude-sonnet-4-6`) | LLM API | Anthropic (SaaS) | Powers code review, tech docs, business docs, auto-testing, UAT workflows |
| OpenRouter / OpenAI-compatible endpoint | LLM API | OpenRouter (SaaS) or configurable | Powers the runtime insurance training agent (teacher + assessor) |
| Voyage AI (implied) | Embedding API | Voyage AI (SaaS) | Embeds PDF chunks for vector store ingestion (referenced in `embed_chunks` rate-limit comment) |

---

## 3. Data Flow

### 3.1 Document Ingestion (Offline / Admin)

1. An operator runs `POST /ingest` (or the `core/ingest.py` CLI) pointing at the `data/Insurance-product-info/` directory.
2. `ingest_directory()` walks the directory recursively, finding all PDF files.
3. For each PDF, `load_or_create_annotations()` checks for a cached `.annot.json` sidecar file; if absent, it calls the configured LLM (Anthropic via OpenRouter) to extract document-level metadata (product name, doc type, summary) and page-level relevance flags.
4. Relevant pages are chunked by `extract_chunks_from_pdf()` using heuristic heading/bullet/paragraph splitting with a configurable `max_words` cap (~280 words/chunk).
5. Chunks (with metadata: product name, page range, section title, file URL) are embedded in batches via Voyage AI embeddings and stored in the vector store (`ChromaStore` / `LocalFAISSStore` / `PineconeStore`).
6. The vector store index is saved to disk (`store.save()`).

### 3.2 Training Session (Runtime — Teacher Mode)

1. User opens a session in the frontend UI; the frontend calls `POST /sessions` on the FastAPI backend to create a session (stored in `data/sessions.json`).
2. User sends a message; the frontend POSTs to the streaming API endpoint on `training-bot-api`.
3. The FastAPI handler resets the per-request source tracker (`reset_sources()`) and invokes the LangGraph teacher agent via `astream_events`.
4. The teacher agent (using `claude-sonnet-4-6` or the configured OpenRouter model) decides which RAG tool to call: `search_product`, `search_all`, `compare_plans`, `lookup_exclusions`, `lookup_hospital_network`, `search_claim_procedure`, `list_products`, or `get_current_date`.
5. Each tool call queries the vector store for top-K similar chunks; metadata (document name, page, file URL) is collected into the per-request source list via `_collect_sources()`.
6. The agent synthesises a response with inline citation markers (`[[S1]]`, `[[S2]]`, …) referencing retrieved source documents.
7. The response is streamed back to the frontend via `StreamingResponse`; source metadata is appended at the end of the stream.
8. PDFs are served directly to the client over HTTP via the `/docs` static mount on the FastAPI app.
9. Session state (conversation history, profile) is persisted to `data/sessions.json`.

### 3.3 Roleplay & Assessment Mode

1. The frontend calls a session endpoint to switch mode to `roleplay` and generates a `CustomerProfile` (random HK persona via `generate_profile()`).
2. User messages are processed by the `_ROLEPLAY_SYSTEM` prompt, with the LLM playing the customer persona.
3. When roleplay ends, the frontend triggers assessment: the full conversation is passed to the assessor agent via `ainvoke`.
4. The assessor agent uses the same RAG tools to fact-check every product claim the trainee agent made, then scores performance across five dimensions.
5. Assessment results are returned synchronously and displayed to the user.

### 3.4 CI/CD Pipeline (GitHub Actions)

1. Developer pushes to `main` or opens a PR.
2. `deploy.yml` runs tests via `uv run pytest tests/ -v`.
3. On merge to `main`, `uv export` generates `requirements.txt`; `azure/webapps-deploy@v3` deploys both App Services using publish profiles stored as GitHub secrets.
4. AI workflow tools (Tools 1–5) run on schedule or trigger, call Anthropic Claude with repo content, and write outputs to the `ai-delivery-outputs` repo; SendGrid sends notification emails.

---

## 4. Security Posture

### Secured

- **GitHub Secrets**: All sensitive credentials (`AZURE_WEBAPP_PUBLISH_PROFILE_API`, `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND`, `ANTHROPIC_API_KEY`, `GH_TOKEN`, `SENDGRID_API_KEY`) are stored as GitHub Actions secrets, not hardcoded in source.
- **Dependency pinning**: `uv` is used for reproducible dependency resolution with lockfiles.
- **PR gate**: Deployment is blocked if tests fail.
- **Per-request source isolation**: `contextvars.ContextVar` is used for async-safe, per-request RAG source tracking — prevents source list bleed between concurrent requests.

### Not Secured / Gaps

- **⚠️ TLS verification disabled**: `httpx.Client(verify=False)` and `httpx.AsyncClient(verify=False)` are hardcoded in `api/main.py` and `core/ingest.py`. This disables certificate validation for all LLM API calls, making the application vulnerable to man-in-the-middle attacks. **This must be removed before production use.**
- **⚠️ API key passed as plain environment variable**: `_API_KEY = os.getenv("API_KEY", "")` with a default empty string means the app will start silently with no authentication if the env var is unset. No startup validation.
- **⚠️ No authentication on FastAPI endpoints**: There is no middleware enforcing authentication or authorisation on any API route. Any client that can reach `training-bot-api` can access all endpoints, including `/ingest` (which could trigger expensive embedding operations).
- **⚠️ Session data stored in plaintext JSON**: `data/sessions.json` contains full conversation history and customer profiles. This file is persisted on the App Service file system with no encryption at rest.
- **⚠️ CORS is overly permissive**: `allow_methods=["*"]` and `allow_headers=["*"]` are set. Origins are limited to localhost dev URLs and localhost:8000 only — [TODO: confirm the production frontend origin is added and localhost origins are removed in prod].
- **⚠️ Static file mount exposes entire data directory**: `app.mount("/docs", StaticFiles(directory=str(_DATA_DIR)))` serves all files under `data/` publicly, including `sessions.json` which contains conversation history. This is a data exposure risk.
- **⚠️ No rate limiting**: No rate limiting on API endpoints; the `/ingest` endpoint in particular could be abused to trigger repeated expensive LLM/embedding calls.
- **⚠️ GH_TOKEN scope unknown**: The `GH_TOKEN` secret used by AI tools has write access to `ai-delivery-outputs` and read access to source repos. [TODO: confirm token is scoped to minimum required permissions — ideally `contents:write` on the output repo only.]
- **⚠️ No encryption at rest for vector store**: The ChromaDB/FAISS index is stored on the local filesystem of the App Service with no documented encryption configuration.
- **⚠️ Sender email domain unverified in code**: `SENDER_EMAIL: noreply@ai-delivery.capco.com` — [TODO: confirm this domain is verified in SendGrid to prevent spoofing/rejection].
- **No secrets scanning**: No `gitleaks` or similar tool in CI to prevent accidental secret commits.
- **No SAST/dependency vulnerability scanning**: No `bandit`, `safety`, or Dependabot configured.

---

## 5. Environment Variables and Secrets

| Name | Required | Sensitivity | Where Set |
|---|---|---|---|
| `API_KEY` | Yes | 🔴 High — LLM API key (OpenRouter/Anthropic) | App Service environment config / `.env` file locally |
| `OPENAI_URL_BASE` | No | 🟡 Medium — LLM endpoint URL | App Service environment config / `.env` |
| `OPENAI_MODEL` | No | 🟢 Low | App Service environment config / `.env` |
| `SHOW_TOOL_CALLS` | No | 🟢 Low | App Service environment config / `.env` |
| `ANTHROPIC_API_KEY` | Yes (AI tools) | 🔴 High — Anthropic API key | GitHub Actions Secret |
| `GH_TOKEN` | Yes (AI tools) | 🔴 High — GitHub PAT with repo write access | GitHub Actions Secret |
| `SENDGRID_API_KEY` | Yes (AI tools) | 🔴 High — SendGrid API key | GitHub Actions Secret |
| `AZURE_WEBAPP_PUBLISH_PROFILE_API` | Yes (deploy) | 🔴 High — Azure deploy credential | GitHub Actions Secret |
| `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND` | Yes (deploy) | 🔴 High — Azure deploy credential | GitHub Actions Secret |
| `OUTPUT_REPO` | No | 🟢 Low — output repo name | GitHub Actions env (hardcoded default: `ai-delivery-outputs`) |
| `OUTPUT_REPO_OWNER` | No | 🟢 Low | GitHub Actions env (derived from `github.repository_owner`) |
| `NOTIFY_EMAIL` | No | 🟡 Medium — PII (email address) | GitHub Actions env (hardcoded: `kylo.deng@capco.com`) |
| `SENDER_EMAIL` | No | 🟡 Medium | GitHub Actions env (hardcoded: `noreply@ai-delivery.capco.com`) |
| `VECTOR_STORE_TYPE` | No | 🟢 Low | App Service environment config / `.env` — [TODO: confirm env var name] |
| `PINECONE_API_KEY` | Conditional | 🔴 High — if PineconeStore is used | App Service environment config / `.env` — [TODO: confirm] |

---

## 6. Dependencies

| Dependency | Type | Purpose | Notes |
|---|---|---|---|
| Anthropic Claude (`claude-sonnet-4-6`) | External LLM API | AI workflow tools (code review, docs, testing, UAT) | Paid API; key required |
| OpenRouter (or compatible OpenAI endpoint) | External LLM API | Runtime insurance training agent | Configurable via `OPENAI_URL_BASE`; default is OpenRouter |
| Voyage AI | External Embedding API | PDF chunk embedding for vector store | Implied by rate-limit comment (`3 RPM free tier`); [TODO: confirm embedding provider env var] |
| LangChain / LangGraph | Python library | Agent orchestration, tool binding, streaming | Core agent framework |
| LangChain-OpenAI | Python library | `ChatOpenAI` wrapper for LLM calls | Used for both agent and annotation LLM |
| FastAPI | Python library | REST API framework | Backend web server |
| Pydantic | Python library | Request/response validation | Used throughout FastAPI layer |
| pdfplumber | Python library | PDF text extraction | Used in `core/chunker.py` |
| ChromaDB / FAISS / Pinecone | Vector database | Embedding storage and retrieval | Runtime-selectable via `core/vector_store.py` |
| SendGrid | External email API | Notification emails from AI workflows | Requires verified sender domain |
| GitHub API (`api.github.com`) | External API | Reading repo files, posting PR comments, writing output files | Used by all 5 AI tool scripts |
| `ai-delivery-outputs` | External GitHub Repo | Stores all AI-generated artefacts (docs, test reports, UAT packs) | Must exist under the same owner; `GH_TOKEN` needs write access |
| `astral-sh/uv` | Build tool | Python dependency management and packaging | Used in all CI workflows |
| Azure App Service | Cloud PaaS | Hosting both API and frontend applications | Deployed via `azure/webapps-deploy@v3` |
| httpx | Python library | Async HTTP client for LLM calls | Currently configured with `verify=False` — security risk |

---

## 7. Deployment Instructions

### Prerequisites

- Azure CLI installed and authenticated, or Azure publish profiles downloaded
- Python 3.13 (for local dev) / 3.12 (for AI tool scripts)
- `uv` installed: `pip install uv`
- GitHub repository secrets configured (see Section 5)
- Azure App Services `training-bot-api` and `training-bot-frontend` created in Azure portal

### Local Development

```bash
# Clone the repository
git clone https://github.com/kylodeng/Insurance-Training-Bot-main
cd Insurance-Training-Bot-main

# Install dependencies
uv sync

# Copy and configure environment variables
cp .env.example .env   # [TODO: confirm .env.example exists]
# Edit .env with API_KEY, OPENAI_URL_BASE, OPENAI_MODEL, etc.

# Ingest PDF documents into vector store
uv run python core/ingest.py --pdf-dir data/Insurance-product-info --verbose

# Run the API backend
uv run uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload

# Run the frontend (separate terminal)
# [TODO: confirm frontend start command — Chainlit or Vite]
uv run chainlit run frontend/app.py   # if Chainlit
# OR
npm run dev                            # if Vite
```

### Run Tests

```bash
uv run pytest tests/ -v
```

### Production Deployment (Automated via GitHub Actions)

```bash
# Trigger deployment by pushing to main
git push origin main

# The deploy.yml workflow will:
# 1. Run tests
# 2. Generate requirements.txt via: uv export --no-dev --format requirements-txt -o requirements.txt
# 3. Deploy API to Azure App Service (training-bot-api)
# 4. Deploy Frontend to Azure App Service (training-bot-frontend)
```

### Manual Deployment (if needed)

```bash
# Generate requirements
uv export --no-dev --format requirements-txt -o requirements.txt

# Deploy API manually using Azure CLI
az webapp deploy --resource-group <rg-name> \
  --name training-bot-api \
  --src-path . \
  --type zip

# Deploy Frontend manually
az webapp deploy --resource-group <rg-name> \
  --name training-bot-frontend \
  --src-path . \
  --type zip
```

###