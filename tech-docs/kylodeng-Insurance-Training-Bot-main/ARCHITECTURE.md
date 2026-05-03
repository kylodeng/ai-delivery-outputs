# Architecture Document — kylodeng/Insurance-Training-Bot

---

## 1. Overview

The Insurance Training Bot is a dual-mode AI-powered training platform designed to help new insurance agents at a Hong Kong-based insurer (Sun Life products) develop product knowledge and sales skills. It consists of a FastAPI backend that orchestrates a LangGraph-based RAG (Retrieval-Augmented Generation) agent, a vector store populated from ingested insurance product PDFs, and a frontend application. The system supports two interaction modes: a **Teacher mode** for ongoing instructional chat about insurance products and sales techniques, and an **Assessor mode** that scores trainee performance after a roleplay session against a synthetically generated customer profile. Both components are deployed as Azure App Service instances via GitHub Actions CI/CD, and the platform is augmented by five autonomous AI delivery workflows (code review, tech docs, business docs, auto testing, and UAT facilitation) that run against the repository itself using Claude (Anthropic) as the intelligence layer.

---

## 2. Resources Deployed

| Resource | Type | Cloud Provider | Purpose |
|---|---|---|---|
| `training-bot-api` | Azure App Service (Web App) | Azure | Hosts the FastAPI backend, LangGraph agents, and RAG pipeline |
| `training-bot-frontend` | Azure App Service (Web App) | Azure | Hosts the frontend UI (Chainlit or Vite-based) |
| Vector Store (local FAISS or ChromaDB) | File-based index on App Service filesystem | Azure | Stores embedded insurance document chunks for RAG retrieval |
| PDF/data static mount (`/docs`) | StaticFiles mount on FastAPI | Azure | Serves raw PDF files and JSON annotations over HTTP |
| `sessions.json` | File on App Service filesystem | Azure | Persists multi-turn session state across server restarts |
| `ai-delivery-outputs` | GitHub Repository | GitHub | Receives AI-generated reports: code reviews, docs, test files, UAT packs |
| GitHub Actions Runners | Ephemeral Ubuntu VMs | GitHub | CI/CD: test, deploy, and run all five AI delivery tools |
| Anthropic Claude API (`claude-sonnet-4-6`) | Managed AI API | Anthropic (external) | Powers AI delivery tools (code review, docs, testing, UAT) |
| OpenRouter / LLM endpoint | REST API (configurable) | External (OpenRouter or Anthropic) | Powers the teacher and assessor agents at runtime |
| Voyage AI (inferred) | Embedding API | External | Embeds document chunks into the vector store (batch_delay hints at Voyage free tier) |
| SendGrid | Email API | Twilio/SendGrid (external) | Sends notification emails from AI delivery workflows |

---

## 3. Data Flow

### 3a — Document Ingestion (offline / one-time)

1. An operator runs `POST /ingest` or executes `core/ingest.py` directly against the `data/Insurance-product-info/` directory.
2. `ingest_directory()` walks all PDF files recursively.
3. For each PDF, `load_or_create_annotations()` checks for a `.annot.json` sidecar file; if absent, it calls the configured LLM (OpenRouter/Anthropic) to classify the document and annotate each page, then writes the sidecar to disk.
4. `extract_chunks_from_pdf()` uses `pdfplumber` to extract text, applies heading/bullet heuristics to split pages into semantic units, and assembles chunk dicts with metadata (product name, page range, file URL).
5. `embed_chunks()` sends chunk batches to the embedding API (Voyage AI inferred) and upserts vectors into the vector store (FAISS, Chroma, or Pinecone depending on environment config).
6. The vector store index is saved to disk (`store.save()`).

### 3b — Teacher Mode (runtime chat)

1. A user opens the frontend (Chainlit UI) and starts or resumes a session.
2. The frontend sends a chat message via HTTP to the FastAPI backend.
3. `main.py` resolves the session from `sessions.json`, constructs the `ChatOpenAI` LLM instance pointed at OpenRouter, and calls `make_teacher_agent()` which wires the LangGraph ReAct agent with eight RAG tools.
4. The agent decides which tool(s) to call (e.g. `search_product`, `compare_plans`, `lookup_exclusions`).
5. Each tool queries the vector store for relevant chunks; `_collect_sources()` accumulates source metadata into a per-request contextvar list.
6. The agent synthesises a response with inline `[[Sn]]` citation markers and streams tokens back to the frontend via `StreamingResponse`.
7. Source metadata collected during the request is appended to the streamed response for the UI to render as footnotes.

### 3c — Roleplay / Assessor Mode

1. The frontend requests a new roleplay session; `generate_profile()` randomly assembles a `CustomerProfile` (name, age, occupation, HK-context financials, personality note).
2. The user engages in a conversation with the **roleplay system prompt** (the backend impersonates the customer profile using the configured LLM).
3. When the session ends, the frontend triggers assessment by calling the assessor endpoint.
4. `make_assessor_agent()` receives the full conversation transcript and customer profile, and uses the same eight RAG tools to **verify** every factual claim made by the trainee against the knowledge base.
5. The assessor returns a structured scoring report across five dimensions.

### 3d — AI Delivery Workflows (CI/CD meta-layer)

1. GitHub Actions triggers one of five workflow files on push, PR, schedule, or manual dispatch.
2. The workflow script (`tool1` through `tool5`) fetches repo files or PR diffs via the GitHub REST API using `GH_TOKEN`.
3. The script calls the Anthropic Claude API (`claude-sonnet-4-6`) with a specialist system prompt and the code/diff payload.
4. Claude returns structured output (JSON or Markdown).
5. The script writes the output to the `ai-delivery-outputs` repository via the GitHub Contents API.
6. For PR-triggered tools, a comment is posted back to the PR.
7. SendGrid sends a notification email to `kylo.deng@capco.com` with a summary and link.
8. An audit entry is written (destination [TODO: see below]).

---

## 4. Security Posture

### ✅ What is secured

- **Secrets managed via GitHub Actions secrets**: `AZURE_WEBAPP_PUBLISH_PROFILE_API`, `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND`, `ANTHROPIC_API_KEY`, `GH_TOKEN`, `SENDGRID_API_KEY` are stored as encrypted GitHub secrets and not hardcoded in source.
- **CI/CD gating**: Deployment jobs require the `test` job to pass first (`needs: test`), and only fire on pushes to `main` (not PRs), reducing the risk of deploying broken code.
- **Per-request source isolation**: `contextvars.ContextVar` is used to isolate RAG source tracking per async request, preventing cross-request data leakage in the async FastAPI runtime.
- **Session persistence scoped to server**: Sessions are stored locally in `sessions.json` — no external database exposure.

### ❌ Gaps and concerns

- **TLS/SSL verification explicitly disabled**: `httpx.Client(verify=False)` and `httpx.AsyncClient(verify=False)` are hardcoded in `main.py` and `core/ingest.py`. This disables certificate verification for all LLM API calls, making the application vulnerable to man-in-the-middle attacks. **This must be fixed before any production use.**
- **No API authentication on FastAPI endpoints**: There is no authentication middleware, API key validation, or OAuth on the FastAPI routes. Any user who can reach `training-bot-api.azurewebsites.net` can invoke `/ingest`, read all sessions, or stream LLM responses. [TODO: Add Azure AD authentication or at minimum an API key header check.]
- **CORS policy is overly broad**: `allow_methods=["*"]` and `allow_headers=["*"]` are set. While origins are restricted, this still allows any HTTP method and header from those origins.
- **Sessions stored in plain text on the filesystem**: `sessions.json` contains conversation transcripts and customer profiles unencrypted on the App Service filesystem. If the App Service is compromised or logs are exported, this data is exposed. [TODO: Encrypt at rest or use Azure Cosmos DB / Azure SQL with encryption enabled.]
- **Static file serving of PDFs with no access control**: `app.mount("/docs", StaticFiles(...))` exposes all insurance PDFs and annotation JSON files publicly over HTTP with no authentication. These may contain proprietary or sensitive insurance product documents.
- **No encryption-at-rest configuration visible in IaC**: There is no Bicep, Terraform, or ARM template in the repository. App Service, storage, and any other Azure resources were presumably created manually or via the Azure portal. Encryption at rest and in transit cannot be verified. [TODO: Add IaC (Bicep or Terraform) defining all Azure resources with encryption settings explicitly configured.]
- **`GH_TOKEN` scope unknown**: The `GH_TOKEN` used by AI delivery tools writes to the `ai-delivery-outputs` repo and posts PR comments. If this is a PAT with broad `repo` scope rather than a fine-grained token scoped to specific repositories, it represents an overly broad IAM grant. [TODO: Replace with a fine-grained GitHub PAT scoped to `ai-delivery-outputs` (write) and the source repo (read + PR comment).]
- **No secrets scanning or SAST in pipeline**: The `deploy.yml` workflow only runs pytest. There is no `gitleaks`, `trivy`, `bandit`, or similar tool to catch accidentally committed secrets or insecure code patterns.
- **Publish profiles stored as flat secrets**: Azure App Service publish profiles contain deployment credentials. If these GitHub secrets are exfiltrated, an attacker gains deployment access to both App Services. [TODO: Prefer OIDC federated identity (azure/login with `creds`) over publish profiles.]
- **AI delivery tools install packages without pinned versions**: `pip install anthropic requests` with no version pins in the workflow YAML could result in supply-chain risk from a malicious package update.

---

## 5. Environment Variables and Secrets

| Name | Required | Sensitivity | Where set |
|---|---|---|---|
| `API_KEY` | Yes | 🔴 High — LLM API key | App Service environment / `.env` file |
| `OPENAI_URL_BASE` | No | Low | App Service environment / `.env` file (default: `https://openrouter.ai/api/v1`) |
| `OPENAI_MODEL` | No | Low | App Service environment / `.env` file (default: `openai/gpt-oss-20b:free`) |
| `SHOW_TOOL_CALLS` | No | Low | App Service environment / `.env` file (default: `true`) |
| `ANTHROPIC_API_KEY` | Yes (AI tools) | 🔴 High — Anthropic API key | GitHub Actions secret |
| `GH_TOKEN` | Yes (AI tools) | 🔴 High — GitHub PAT | GitHub Actions secret |
| `SENDGRID_API_KEY` | Yes (AI tools) | 🔴 High — email relay key | GitHub Actions secret |
| `AZURE_WEBAPP_PUBLISH_PROFILE_API` | Yes (deploy) | 🔴 High — Azure deploy credential | GitHub Actions secret |
| `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND` | Yes (deploy) | 🔴 High — Azure deploy deploy credential | GitHub Actions secret |
| `OUTPUT_REPO` | No | Low | GitHub Actions env (default: `ai-delivery-outputs`) |
| `OUTPUT_REPO_OWNER` | No | Low | GitHub Actions env (derived from `github.repository_owner`) |
| `NOTIFY_EMAIL` | No | Low | GitHub Actions env (hardcoded: `kylo.deng@capco.com`) |
| `SENDER_EMAIL` | No | Low | GitHub Actions env (hardcoded: `noreply@ai-delivery.capco.com`) |
| `VECTOR_STORE_TYPE` | No | Low | App Service environment [TODO: confirm env var name — inferred from `get_vector_store()`] |

---

## 6. Dependencies

| Dependency | Type | Purpose | Notes |
|---|---|---|---|
| Anthropic Claude (`claude-sonnet-4-6`) | External AI API | Powers all five AI delivery tools (code review, docs, testing, UAT) | Paid API; key in GitHub secret |
| OpenRouter (default) or any OpenAI-compatible endpoint | External AI API | Powers teacher and assessor agents at runtime | Configurable via `OPENAI_URL_BASE`; TLS verify disabled |
| Voyage AI (inferred) | External embedding API | Embeds document chunks for vector store | Inferred from `batch_delay` free-tier rate-limit comments; [TODO: confirm embedding provider] |
| LangChain / LangGraph | Python library | Agent orchestration, tool binding, message formatting | Core framework dependency |
| FastAPI | Python library | REST API framework for backend | |
| pdfplumber | Python library | PDF text extraction during ingestion | |
| Chroma / FAISS / Pinecone | Vector store backends | Document similarity search | Store type selectable at runtime |
| SendGrid | External email API | Notification emails from AI delivery workflows | |
| GitHub REST API (`api.github.com`) | External API | Read repo files, post PR comments, write output repo | Used by all five AI delivery scripts |
| `ai-delivery-outputs` (sibling repo) | GitHub repository | Stores all generated reports and documents | Must exist and be accessible with `GH_TOKEN` |
| Chainlit (inferred) | Frontend framework | Chat UI served on the frontend App Service | [TODO: confirm — referenced in CORS config and agent streaming] |
| httpx | Python library | Async HTTP client for LLM API calls | TLS verification disabled |
| uv | Python build tool | Dependency management and requirements export | Used in CI/CD |
| pytest | Test framework | Unit and integration tests | Run in `test` job |

---

## 7. Deployment Instructions

### Prerequisites

1. Two Azure App Services must exist: `training-bot-api` and `training-bot-frontend`.
2. Both publish profiles must be downloaded from the Azure Portal and stored as GitHub secrets `AZURE_WEBAPP_PUBLISH_PROFILE_API` and `AZURE_WEBAPP_PUBLISH_PROFILE_FRONTEND`.
3. The following GitHub secrets must be set on the repository: `ANTHROPIC_API_KEY`, `GH_TOKEN`, `SENDGRID_API_KEY`.
4. The sibling repository `<owner>/ai-delivery-outputs` must exist.

### Automated deployment (via GitHub Actions)

Deployment is triggered automatically on every push to `main` after tests pass:

```bash
git checkout main
git push origin main
# GitHub Actions will run: test → deploy-api + deploy-frontend (parallel)
```

### Manual local setup and ingestion

```bash
# 1. Install uv
pip install uv

# 2. Install all dependencies
uv sync

# 3. Copy and configure environment
cp .env.example .env   # [TODO: confirm .env.example exists]
# Edit .env and set:
#   API_KEY=<your-openrouter-or-anthropic-key>
#   OPENAI_URL_BASE=https://openrouter.ai/api/v1
#   OPENAI_MODEL=openai/gpt-oss-20b:free

# 4. Ingest PDF documents into the vector store
uv run python -m core.ingest --pdf-dir data/Insurance-product-info --verbose

# 5. Start the API server
uv run uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload

# 6. Run tests
uv run pytest tests/ -v
```

### Generate requirements.txt for Azure deployment

```bash
uv export --no-dev --format requirements-txt -o requirements.txt
```

### Trigger AI delivery tools manually

```bash
# Trigger code review via GitHub CLI
gh workflow run tool1_code_review.yml \
  --field review_mode=repo

# Trigger tech documentation regeneration
gh workflow run tool2_tech_docs.yml

# Trigger business documentation for a release
gh workflow run tool3_business_docs.yml \
  --field project_name="Insurance Training Bot" \
  --field release_version="1.0.0"
```

---

## 8. Risks and TODOs

### 🔴 Critical Risks

| Risk | Detail |
|---|---|
| **TLS verification disabled** | `verify=False