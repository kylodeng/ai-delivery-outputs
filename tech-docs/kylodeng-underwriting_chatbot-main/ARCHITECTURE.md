# Architecture Document: kylodeng/underwriting_chatbot-main

---

## 1. Overview

The Underwriting Chatbot is an AI-assisted insurance underwriting platform that enables underwriters to assess customer risk profiles through a conversational interface. The system combines a FastAPI streaming backend with a frontend chat UI, orchestrating multiple LLM calls (Anthropic Claude, Google Gemini) via LangGraph agents to produce structured underwriting reports. Specialist agents evaluate distinct risk domains (finance, health, life, KYC, etc.) in parallel, with an aggregator LLM synthesising their outputs into a typed `UnderwritingReport`. Supporting infrastructure includes a Redis instance (for LangGraph agent memory/checkpointing), a PostgreSQL database (Chainlit session persistence), and several pre-built SQLite databases containing customer profiles, ML model predictions, and feature importance scores from an offline-trained CatBoost risk classifier. Five GitHub Actions CI/CD workflows leverage Claude (via Anthropic API) to provide automated code review, technical documentation generation, business documentation generation, test generation, and UAT facilitation.

---

## 2. Resources Deployed

| Resource | Type | Cloud Provider | Purpose |
|---|---|---|---|
| `backend` | Docker container (FastAPI) | Self-hosted / Docker Compose | Core API: agent orchestration, LLM calls, SSE streaming |
| `frontend` | Docker container | Self-hosted / Docker Compose | Chat UI (Chainlit-based) served on port 8080 |
| `redis` | Docker container (`redis-stack-server:7.2.0-v14`) | Self-hosted / Docker Compose | LangGraph checkpoint store for agent conversation memory |
| `postgres` | Docker container (`postgres:16-alpine`) | Self-hosted / Docker Compose | Chainlit session/user data persistence |
| `customer_profile.db` | SQLite file (read-only volume mount) | Self-hosted | Customer demographic and profile data |
| `feature_importance.db` | SQLite file (read-only volume mount) | Self-hosted | CatBoost model feature importance scores |
| `model_predictions.db` | SQLite file (read-only volume mount) | Self-hosted | Pre-computed ML risk classification predictions |
| `application_profile.db` | SQLite file (read-only volume mount) | Self-hosted | Insurance application profile data |
| `postgres_data` | Docker named volume | Self-hosted | Persistent PostgreSQL data storage |
| `claude-haiku-4-5-20251001` | External LLM API | Anthropic | Fast/default agent LLM and CI tool calls |
| `claude-sonnet-4-20250514` | External LLM API | Anthropic | Deep specialist assessment LLM |
| `gemini-3-flash-preview` | External LLM API | Google Cloud | Alternative LLM provider (configured, usage conditional) |
| GitHub Actions runners (`ubuntu-latest`) | CI/CD compute | GitHub | Five automated AI-powered delivery workflows |
| `ai-delivery-outputs` | GitHub repository | GitHub | Output store for AI-generated docs, reports, test files |
| SendGrid | Email API | Twilio/SendGrid | Notification delivery for CI workflow outputs |

---

## 3. Data Flow

### 3a. Runtime Chat Flow

1. **User** submits a message via the frontend chat UI (port 8080); the frontend forwards the request as an HTTP POST to `http://backend:8000/chat` with `{message, session_id, model, mode, temperature}`.
2. The **FastAPI backend** (`main.py`) calls `build_agent()`, which instantiates a LangGraph agent backed by `AsyncRedisSaver` (Redis on port 6379) — the agent loads any prior conversation state for the `thread_id` (session ID).
3. The agent's **orchestration LLM** (Claude Haiku, tagged `"agent"`) receives the user message plus conversation history and decides which tool to call first, responding with a structured JSON action.
4. If the agent calls `get_customer_profile`, the **tools module** queries the SQLite `customer_profile.db` and returns structured customer data.
5. If the agent calls `customer_lookalike`, the pre-computed `customer_similarity_dict.json` is queried to return similar customer IDs.
6. If the agent calls `run_underwriting_assessment`, the **assessment module** fans out parallel async calls (semaphore-limited to 4) to the **specialist LLM** (Claude Haiku with `"thinking"` tag) — one call per assessment category (finance, health, life, KYC, etc.) using prompts from `assessment_criterias.json`.
7. Specialist LLM responses are collected and passed to an **aggregator LLM** (Claude Haiku/Sonnet with structured output), which synthesises them into a typed `UnderwritingReport` Pydantic model, optionally querying `model_predictions.db` and `feature_importance.db` for ML-derived signals.
8. The backend streams all intermediate events (tool start/end, LLM token chunks, charts) back to the frontend as **Server-Sent Events (SSE)**, with chart payloads buffered and flushed after the text response.
9. LangGraph writes the updated conversation state back to **Redis** via `AsyncRedisSaver`.
10. The **frontend** renders streaming tokens, tool progress indicators, and chart components in real time.

### 3b. CI/CD Workflow Data Flow

1. A trigger event (PR, push to main, tag, schedule, or `workflow_dispatch`) fires one of the five GitHub Actions workflows.
2. The workflow runner checks out the source repo and runs a Python script from `.github/scripts/`.
3. The script calls `get_repo_files()` or `get_pr_diff()` via the **GitHub REST API** (authenticated with `GH_TOKEN`) to fetch source code.
4. The script calls the **Anthropic API** (Claude Sonnet via `shared.py`) with the source code as context.
5. Claude's response (JSON or Markdown) is written to the **`ai-delivery-outputs`** GitHub repository via authenticated `PUT` to the GitHub Contents API.
6. An email notification is sent via the **SendGrid API** to `kylo.deng@capco.com`.
7. For code review workflows, a PR comment is posted back to the source repository via the GitHub Issues API.

---

## 4. Security Posture

### ✅ What Is Secured

- **Secrets management**: All API keys (`ANTHROPIC_API_KEY`, `GH_TOKEN`, `SENDGRID_API_KEY`, `GOOGLE_API_KEY`) are stored as GitHub Actions secrets and injected at runtime; not hardcoded in source.
- **SQLite databases are read-only**: Volume mounts for all four SQLite databases use the `:ro` flag, preventing runtime writes to sensitive customer data.
- **Agent prompt injection mitigation**: The system prompt explicitly instructs the agent never to disclose internal instructions or tool names.
- **Assessment tool guard**: `run_underwriting_assessment` docstring enforces that `get_customer_profile` must be called first, providing a soft data-flow guard.
- **LangGraph checkpointing**: Conversation state is persisted in Redis (internal Docker network), not exposed externally.

### ❌ Security Gaps and Risks

- **PostgreSQL credentials are hardcoded** in `docker-compose.yml` (`POSTGRES_USER: chainlit` / `POSTGRES_PASSWORD: chainlit`). These are plaintext default credentials with no secret injection — **critical gap for any non-local deployment**.
- **Redis has no authentication**: The Redis container has no password, ACL, or TLS configured. Any process on the Docker network can read/write all agent conversation state, including customer PII.
- **No encryption at rest**: SQLite databases containing customer profiles and ML predictions are plain files with no encryption. PostgreSQL volume (`postgres_data`) is also unencrypted.
- **No encryption in transit (internal)**: Inter-container communication (frontend→backend, backend→Redis, backend→PostgreSQL) is unencrypted HTTP/plain TCP within the Docker network.
- **CORS is fully open**: `allow_origins=["*"]`, `allow_methods=["*"]`, `allow_headers=["*"]` — the API accepts requests from any origin, exposing it to CSRF-style attacks if deployed beyond localhost.
- **No authentication on the `/chat` endpoint**: Any client with network access can invoke the chat API, triggering potentially expensive LLM calls.
- **`GH_TOKEN` scope unknown**: [TODO: What permissions does the GH_TOKEN hold? If it has write access to all repos for the owner, it is overly broad — should be scoped to `ai-delivery-outputs` only.]
- **Customer PII in LLM prompts**: Full customer profiles (age, income, medical conditions, nationality, employment) are passed as plaintext to external Anthropic and Google APIs. No anonymisation or data minimisation is applied before transmission.
- **No input validation** on `ChatRequest` beyond Pydantic type checks — `session_id` is used directly as a Redis key with no sanitisation.
- **`customer_similarity_dict.json` stored in `backend/tmp/`**: Sensitive customer similarity data (10,000 customer IDs with their nearest neighbours) is stored in an unprotected temporary directory tracked in the repository.

---

## 5. Environment Variables and Secrets

| Name | Required | Sensitivity | Where Set |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | 🔴 High — billable API key | GitHub Actions secret; `.env` file for backend |
| `GOOGLE_API_KEY` | Yes (if using Gemini) | 🔴 High — billable API key | `.env` file for backend |
| `GH_TOKEN` | Yes (CI workflows) | 🔴 High — GitHub repo access | GitHub Actions secret |
| `SENDGRID_API_KEY` | Yes (CI workflows) | 🔴 High — email sending | GitHub Actions secret |
| `REDIS_HOST` | No | 🟢 Low | Docker Compose environment; defaults to `localhost` |
| `DATABASE_URL` | Yes (frontend) | 🟡 Medium — DB credentials | Docker Compose environment (hardcoded plaintext) |
| `BACKEND_URL` | Yes (frontend) | 🟢 Low | Docker Compose environment |
| `POSTGRES_USER` | Yes | 🟡 Medium | Docker Compose environment (hardcoded: `chainlit`) |
| `POSTGRES_PASSWORD` | Yes | 🔴 High — DB password | Docker Compose environment (**hardcoded plaintext: `chainlit`**) |
| `POSTGRES_DB` | Yes | 🟢 Low | Docker Compose environment (hardcoded: `chainlit`) |
| `OUTPUT_REPO` | No (CI) | 🟢 Low | GitHub Actions env (hardcoded: `ai-delivery-outputs`) |
| `OUTPUT_REPO_OWNER` | No (CI) | 🟢 Low | GitHub Actions env (derived from `github.repository_owner`) |
| `NOTIFY_EMAIL` | No (CI) | 🟢 Low | GitHub Actions env (hardcoded: `kylo.deng@capco.com`) |
| `SENDER_EMAIL` | No (CI) | 🟢 Low | GitHub Actions env (hardcoded: `noreply@ai-delivery.capco.com`) |

> **⚠️ Note**: A `.env` file is expected at `./backend/.env` (and possibly repo root) for local development. This file is not present in the repo (correctly); however there is no `.env.example` to guide setup. [TODO: Add `.env.example`.]

---

## 6. Dependencies

| Dependency | Type | Purpose | Notes |
|---|---|---|---|
| Anthropic API (`claude-haiku-4-5-20251001`, `claude-sonnet-4-20250514`) | External SaaS API | Core LLM for agent, specialists, aggregator, and all 5 CI tools | Billable; no fallback configured |
| Google Generative AI (`gemini-3-flash-preview`) | External SaaS API | Alternative LLM provider | Configured but [TODO: confirm active usage paths] |
| LangGraph / LangChain | Python library | Agent graph orchestration, tool calling, streaming | `langgraph`, `langchain-core`, `langchain-anthropic`, `langchain-google-genai` |
| Redis Stack (`redis/redis-stack-server:7.2.0`) | Containerised service | LangGraph `AsyncRedisSaver` conversation checkpointing | No persistence configured beyond container restart |
| PostgreSQL 16 (`postgres:16-alpine`) | Containerised service | Chainlit frontend session/user storage | Init SQL at `./postgres/init.sql` |
| Chainlit | Python framework | Frontend chat UI rendering and session management | [TODO: confirm Chainlit version] |
| FastAPI + `sse-starlette` | Python framework | Backend HTTP API and SSE streaming | |
| CatBoost (offline) | ML model (pre-trained) | Risk classification — predictions stored in `model_predictions.db` | Model not retrained at runtime; version `1.0`, trained `2024-06-01` |
| SendGrid API | External SaaS API | Email notifications from CI workflows | Used only in GitHub Actions, not in runtime app |
| GitHub REST API (`api.github.com`) | External API | Source code fetch and output writing in CI workflows | Authenticated via `GH_TOKEN` |
| `ai-delivery-outputs` (GitHub repo) | External GitHub repository | Storage for AI-generated docs, test files, UAT packs | Must be pre-created and accessible to `GH_TOKEN` |
| `pydantic` | Python library | Structured output validation (`UnderwritingReport`) | |
| `python-dotenv` | Python library | `.env` loading | |
| `anthropic` (direct SDK) | Python library | Used in CI scripts (`shared.py`) independent of LangChain | |

---

## 7. Deployment Instructions

### Prerequisites
- Docker and Docker Compose v2+ installed
- `.env` file created at `./backend/.env` with required secrets (see Section 5)
- `ai-delivery-outputs` GitHub repository created and `GH_TOKEN` granted write access to it

### Local / Docker Compose Deployment

```bash
# 1. Clone the repository
git clone https://github.com/kylodeng/underwriting_chatbot-main.git
cd underwriting_chatbot-main

# 2. Create the backend environment file
cat > backend/.env << EOF
ANTHROPIC_API_KEY=sk-ant-...
GOOGLE_API_KEY=AIza...
EOF

# 3. Build and start all services
docker compose up --build -d

# 4. Verify services are healthy
docker compose ps
docker compose logs backend --follow

# 5. Confirm backend health endpoint
curl http://localhost:8000/health
# Expected: {"status": "ok"}

# 6. Access the chat UI
open http://localhost:8080
```

### Stopping and Cleanup

```bash
# Stop all services (preserve volumes)
docker compose down

# Stop and remove all volumes (destructive — clears PostgreSQL data)
docker compose down -v
```

### GitHub Actions CI Secrets Setup

```bash
# Set required secrets in GitHub (requires GitHub CLI)
gh secret set ANTHROPIC_API_KEY --body "sk-ant-..."
gh secret set GH_TOKEN --body "ghp_..."
gh secret set SENDGRID_API_KEY --body "SG...."
```

### Triggering CI Workflows Manually

```bash
# Trigger code review on full repo
gh workflow run tool1_code_review.yml -f review_mode=repo

# Trigger tech documentation generation
gh workflow run tool2_tech_docs.yml

# Trigger business documentation for a release
gh workflow run tool3_business_docs.yml \
  -f project_name="Underwriting Chatbot" \
  -f release_version="1.0.0"

# Trigger test generation
gh workflow run tool4_auto_testing.yml -f test_mode=generate

# Trigger UAT pack generation
gh workflow run tool5_uat.yml \
  -f uat_mode=generate \
  -f release_version="1.0.0"
```

---

## 8. Risks and TODOs

### Extracted from Code

| Source | Risk / TODO |
|---|---|
| `backend/agent/graph.py` line 1 | `# TODO: migrate Redis to an external service (e.g. Azure Cache for Redis)` — current in-container Redis loses all conversation memory on container restart |
| `backend/modules/LLMS.py` | `# TODO: add more providers here` — Azure OpenAI and OpenAI providers are stubbed as `None`; calling them raises `ValueError` at runtime |
| `backend/main.py` | `lifespan` context manager is commented out —