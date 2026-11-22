# Architecture Document — kylodeng/Insurance-Training-Bot-main

---

## 1. Overview

The Insurance Training Bot is a Retrieval-Augmented Generation (RAG) application designed to train insurance sales agents at Sun Life Hong Kong. It provides two primary modes: a **Teacher mode** (ongoing interactive chat where an AI coach helps agents learn product knowledge, sales techniques, and insurance concepts) and a **Roleplay/Assessment mode** (where the agent practices against a simulated AI customer, followed by an automated accuracy assessment scored across five dimensions). The backend is a FastAPI service that uses LangGraph agents backed by an LLM (routed via OpenRouter or a compatible endpoint), a local vector store (FAISS or Chroma) populated from annotated insurance PDF documents, and a session management layer persisted to a local JSON file. A separate frontend application is deployed alongside the API, both hosted on Azure App Service. The repository also includes five AI-powered CI/CD tooling workflows (code review, tech docs, business docs, auto-testing, and UAT facilitation) that use Anthropic Claude via the GitHub Actions pipeline.

---

## 2. Resources Deployed

| Resource | Type | Cloud Provider | Purpose |
|---|---|---|---|
| `training-bot-api` | Azure App Service (Web App) | Azure | Hosts the FastAPI backend serving chat, ingestion, and session APIs |
| `training-bot-frontend` | Azure App Service (Web App) | Azure | Hosts the frontend UI (Chainlit or Vite-based) |
| Local FAISS / Chroma vector store | File-system index (embedded) | Azure (App Service filesystem) | Stores embedded insurance document chunks for RAG retrieval |
| `data/sessions.json` | File-based session store | Azure (App Service filesystem) | Persists multi-turn conversation sessions across server restarts |
| `data/Insurance-product-info/` | Static file directory | Azure (App Service filesystem) | Stores source PDF documents and `.annot.json` sidecar annotation caches |
| GitHub Actions runners | CI/CD compute (ubuntu-latest) | GitHub | Runs tests, builds, deploys, and AI tooling workflows |
| `ai-delivery-outputs` | GitHub Repository | GitHub | Stores AI-generated artefacts (code review reports, tech docs, test files, UAT packs) |
| OpenRouter API (or compatible) | External LLM gateway | Third-party | Routes LLM inference calls for the training bot agents |
| Anthropic Claude API (`claude-sonnet-4-6`) | External LLM API | Anthropic | Powers AI tooling workflows (code review, docs, testing, UAT) |
| SendGrid | External email service | Twilio/SendGrid | Sends notification emails from AI tooling workflows |
| Voyage AI (inferred) | External embedding API | Voyage AI | Embeds document chunks (referenced in `embed_chunks` rate-limit comments) |
| Health Mutual Group (HMG) | Third-party hospital network data | External | Provides cashless arrangement hospital network data embedded in PDFs |

---

## 3. Data Flow

### 3a. Document Ingestion (one-time / on-demand)

1. An operator calls `POST /ingest` on the FastAPI backend (or runs `core/ingest.py` directly).
2. `ingest_directory()` walks `data/Insurance-product-info/` recursively, finding all `.pdf` files.
3. For each PDF, `load_or_create_annotations()` checks for a `.annot.json` sidecar. If absent, it extracts the first three pages' text, calls the configured LLM (via OpenRouter/Anthropic endpoint) with `_DOC_PROMPT` to get product metadata, then calls `_PAGE_PROMPT` for each page to determine relevance. Results are cached to the sidecar file.
4. `extract_chunks_from_pdf()` uses `pdfplumber` to extract text page by page. Pages flagged as `relevant=false` in annotations are skipped. Remaining text is split into semantic units (headings, bullets, paragraphs) by `split_page_into_units()`, then further split to respect the `max_words` limit.
5. Each chunk dict carries metadata: `document_name`, `product_name`, `doc_type`, `linked_product`, `page_start`, `page_end`, `section_title`, `file_url`, `chunk_id`.
6. `embed_chunks()` sends chunks in batches (default 126) to the configured embedding model (Voyage AI inferred from rate-limit comments) and upserts into the vector store (FAISS or Chroma).
7. The vector store is saved to disk (`store.save()`). On next API startup, `lifespan()` calls `_store.load()` to restore the index.

### 3b. Teacher Mode (interactive training chat)

1. The frontend sends a user message to `POST /chat` (or equivalent streaming endpoint) on the FastAPI backend.
2. `api/main.py` retrieves or creates a `Session` from `sessions.json` (via `get_session()`).
3. `reset_sources()` initialises a fresh per-request `ContextVar` list for source tracking.
4. The `make_teacher_agent()` LangGraph agent is invoked with the conversation history. The agent's system prompt (`TEACHER_SYSTEM`) instructs it to call RAG tools before answering product questions.
5. The agent selects from eight RAG tools in `api/rag_tools.py` (e.g., `search_product`, `compare_plans`, `lookup_exclusions`). Each tool queries the vector store with filtered similarity search and collects source metadata via `_collect_sources()`.
6. Retrieved chunks are formatted with source IDs (S1, S2, …) and returned to the agent.
7. The agent streams its response back via `astream_events`, with inline citation markers (`[[S1]]`).
8. The FastAPI layer streams the response (SSE or chunked HTTP) to the frontend.
9. Source metadata accumulated in the `ContextVar` is sent as a trailing event for the UI to render footnotes/links.

### 3c. Roleplay and Assessment Mode

1. The frontend requests a new roleplay session. `generate_profile()` randomly assembles a `CustomerProfile` from HK-contextualised name/occupation/income pools.
2. The roleplay LLM (using `_ROLEPLAY_SYSTEM` prompt) is instantiated with the profile injected.
3. The trainee agent and the simulated customer exchange messages; both sides are stored in the session's message history.
4. When the session ends, `make_assessor_agent()` is invoked with the full conversation and the original `CustomerProfile` as context.
5. The assessor agent uses the same RAG tools to verify every factual claim made by the trainee against the vector store.
6. An assessment report (scored across five dimensions) is returned to the frontend and stored in the session.

### 3d. CI/CD and AI Tooling

1. A `push` to `main` triggers `deploy.yml`: tests run via `pytest`, then `uv export` generates `requirements.txt`, and `azure/webapps-deploy@v3` deploys both `training-bot-api` and `training-bot-frontend` using publish profiles stored as GitHub secrets.
2. PRs and scheduled crons trigger AI tools (Tools 1–5): each workflow checks out the repo, calls the relevant Python script in `.github/scripts/`, which reads repo files via the GitHub API, calls Claude (`claude-sonnet-4-6`) via Anthropic API, and writes output artefacts to the `ai-delivery-outputs` GitHub repo. SendGrid sends notification emails to `kylo.deng@capco.com`.

---

## 4. Security Posture

### Secured

- **GitHub Secrets**: All sensitive credentials (`AZURE_WEBAPP_PUBLISH_PROFILE_API`, `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND`, `ANTHROPIC_API_KEY`, `GH_TOKEN`, `SENDGRID_API_KEY`) are stored as GitHub Actions secrets, not hardcoded in source.
- **CI gate**: Deployment only proceeds after tests pass (`needs: test`) and only on `push` to `main`, not on PRs.
- **LLM API key handling**: `API_KEY` for the LLM is wrapped in Pydantic `SecretStr` before being passed to `ChatOpenAI`, preventing accidental logging.
- **SendGrid**: API key injected via environment variable, not hardcoded.

### Not Secured / Gaps

- **⚠️ TLS verification disabled**: `httpx.Client(verify=False)` and `httpx.AsyncClient(verify=False)` are used for all LLM API calls in `api/main.py` and `core/ingest.py`. This disables SSL certificate validation, exposing all LLM traffic to man-in-the-middle attacks. This must be fixed before production use.
- **⚠️ No API authentication on FastAPI endpoints**: There is no authentication middleware visible on the FastAPI app. Any caller with network access to `training-bot-api` can invoke `/ingest`, `/chat`, or session management endpoints without credentials.
- **⚠️ CORS is overly permissive**: `allow_methods=["*"]` and `allow_headers=["*"]` are set. Origins include `localhost` addresses — these should be tightened to the production frontend origin only before deployment.
- **⚠️ Static file serving with no access control**: `app.mount("/docs", StaticFiles(...))` serves the entire `data/` directory (including raw PDFs with potentially sensitive insurance product data) over HTTP with no authentication.
- **⚠️ File-based session store is unencrypted**: `data/sessions.json` persists conversation history (including customer profiles and roleplay transcripts) to plaintext JSON on the App Service filesystem. No encryption at rest is applied at the application level; whether Azure App Service filesystem encryption is enabled is [TODO: verify Azure App Service storage encryption settings].
- **⚠️ No secrets scanning**: No `gitleaks`, `trufflehog`, or similar tool is configured in CI to prevent accidental secret commits.
- **⚠️ `GH_TOKEN` scope unknown**: The `GH_TOKEN` used by AI tools has write access to the `ai-delivery-outputs` repo. The minimum required scopes are not documented. [TODO: audit GH_TOKEN permissions and enforce least-privilege].
- **⚠️ Annotation LLM calls are unauthenticated to self-signed or proxy endpoints**: `OPENAI_URL_BASE` defaults to `https://api.anthropic.com/v1` but `verify=False` removes the trust anchor.
- **⚠️ No rate limiting or abuse protection** on any FastAPI endpoint.
- **⚠️ No input sanitisation** visible before user messages are passed to LangGraph agents.
- **⚠️ Sensitive email address hardcoded**: `NOTIFY_EMAIL: kylo.deng@capco.com` and `SENDER_EMAIL: noreply@ai-delivery.capco.com` are hardcoded in workflow env blocks (not secrets). This is low sensitivity but should be parameterised.

---

## 5. Environment Variables and Secrets

| Name | Required | Sensitivity | Where Set |
|---|---|---|---|
| `API_KEY` | Yes | **High** — LLM gateway API key | App Service env / `.env` file locally |
| `OPENAI_URL_BASE` | No | Low | App Service env / `.env`; defaults to `https://openrouter.ai/api/v1` |
| `OPENAI_MODEL` | No | Low | App Service env / `.env`; defaults to `openai/gpt-oss-20b:free` |
| `SHOW_TOOL_CALLS` | No | Low | App Service env / `.env`; defaults to `true` |
| `ANTHROPIC_API_KEY` | Yes (CI tools) | **High** — Anthropic Claude API key | GitHub Actions Secret |
| `GH_TOKEN` | Yes (CI tools) | **High** — GitHub Personal Access Token with repo write access | GitHub Actions Secret |
| `SENDGRID_API_KEY` | Yes (CI tools) | **High** — SendGrid email API key | GitHub Actions Secret |
| `AZURE_WEBAPP_PUBLISH_PROFILE_API` | Yes (deploy) | **High** — Azure deployment credential | GitHub Actions Secret |
| `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND` | Yes (deploy) | **High** — Azure deployment credential | GitHub Actions Secret |
| `OUTPUT_REPO` | No (CI tools) | Low | GitHub Actions workflow env; defaults to `ai-delivery-outputs` |
| `OUTPUT_REPO_OWNER` | No (CI tools) | Low | GitHub Actions workflow env; derived from `github.repository_owner` |
| `NOTIFY_EMAIL` | No (CI tools) | Low | Hardcoded in workflow env block (`kylo.deng@capco.com`) |
| `SENDER_EMAIL` | No (CI tools) | Low | Hardcoded in workflow env block (`noreply@ai-delivery.capco.com`) |
| `VECTOR_STORE_TYPE` | [TODO] | Low | Not observed in provided files — unclear how Chroma vs FAISS is selected |
| `VOYAGE_API_KEY` | [TODO] | **High** — embedding API key | Not observed in provided files; required if Voyage AI is used |

---

## 6. Dependencies

| Dependency | Type | Purpose | Notes |
|---|---|---|---|
| **OpenRouter** (`https://openrouter.ai/api/v1`) | External LLM gateway API | Routes inference for teacher/assessor/roleplay agents | Default; configurable via `OPENAI_URL_BASE` |
| **Anthropic Claude** (`claude-sonnet-4-6`) | External LLM API | Powers all five CI/CD AI tooling workflows; also used as annotation LLM in `core/ingest.py` default | Requires `ANTHROPIC_API_KEY` |
| **Voyage AI** (inferred) | External embedding API | Embeds document chunks into the vector store | Inferred from rate-limit comments in `embed_chunks`; API key management [TODO: confirm] |
| **LangChain / LangGraph** | Python library | Agent orchestration, tool calling, streaming | Core runtime dependency |
| **FastAPI** | Python library | Backend REST/streaming API framework | |
| **pdfplumber** | Python library | PDF text extraction | |
| **FAISS or Chroma** | Local vector store library | Similarity search over embedded chunks | Backend selected via `get_vector_store()` — selection mechanism [TODO: document] |
| **SendGrid** | External email API | Sends notification emails from AI tools | Requires `SENDGRID_API_KEY` |
| **GitHub API** (`api.github.com`) | External REST API | AI tools read source files, post PR comments, write output artefacts | Requires `GH_TOKEN` |
| **`ai-delivery-outputs`** (same GitHub org) | Sibling GitHub repository | Stores all AI-generated artefacts (reports, docs, test files) | Must exist before tools run; write access via `GH_TOKEN` |
| **Azure App Service** | Cloud PaaS | Hosts API and frontend | Configured via publish profiles |
| **Health Mutual Group (HMG)** | Third-party data provider | Hospital cashless network data embedded in PDFs | No live API integration; data is static in PDFs |
| **Sun Life Hong Kong** | Data source | Insurance product PDFs (brochures, policy docs, supplementary) | Embedded as static files; no live API |
| **uv** (`astral-sh/setup-uv`) | Build tool | Python dependency management and packaging | Used in CI for `uv sync`, `uv export` |

---

## 7. Deployment Instructions

### Prerequisites

- Azure CLI authenticated, with the two App Services (`training-bot-api`, `training-bot-frontend`) already provisioned.
- GitHub repository secrets configured (see Section 5).
- The `ai-delivery-outputs` GitHub repository exists under the same owner.

### Automated Deployment (CI/CD — recommended)

```bash
# Push to main branch — triggers test + deploy pipeline automatically
git push origin main
```

The `deploy.yml` workflow will:
1. Run `uv sync` and `uv run pytest tests/ -v`
2. On success, run `uv export --no-dev --format requirements-txt -o requirements.txt`
3. Deploy to `training-bot-api` using `AZURE_WEBAPP_PUBLISH_PROFILE_API`
4. Deploy to `training-bot-frontend` using `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND`

### Manual Document Ingestion

```bash
# Install dependencies locally
uv sync

# Run ingestion against the data directory
uv run python core/ingest.py --pdf-dir data/Insurance-product-info --