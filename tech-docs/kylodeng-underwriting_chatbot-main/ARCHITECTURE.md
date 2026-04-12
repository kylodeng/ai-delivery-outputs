# Architecture Document: kylodeng/underwriting_chatbot-main

---

## 1. Overview

The Underwriting Chatbot is an AI-powered insurance underwriting assistant that enables underwriters to assess customer risk profiles through a conversational interface. The system ingests pre-built SQLite databases containing customer profiles, financial data, KYC records, and ML model predictions, then orchestrates a multi-agent LLM pipeline (using Anthropic Claude and optionally Google Gemini) to produce structured underwriting reports covering finance, health, life, and other risk dimensions. A LangGraph-based agent with Redis-backed memory handles conversation state, while a FastAPI backend streams responses via Server-Sent Events (SSE) to a frontend chatbot UI. The repository also ships five GitHub Actions–powered AI tooling workflows (code review, tech docs, business docs, auto-testing, UAT) that use Claude to automate SDLC tasks and publish outputs to a shared `ai-delivery-outputs` repository.

---

## 2. Resources Deployed

| Resource | Type | Cloud Provider | Purpose |
|---|---|---|---|
| `backend` | Docker container (FastAPI + Python) | Self-hosted / Docker Compose | LLM orchestration, agent execution, SSE streaming API |
| `frontend` | Docker container | Self-hosted / Docker Compose | Chainlit-based chat UI served on port 8080 |
| `redis` (redis-stack-server:7.2.0-v14) | Docker container | Self-hosted / Docker Compose | LangGraph conversation checkpoint / session memory |
| `postgres` (postgres:16-alpine) | Docker container | Self-hosted / Docker Compose | Chainlit user/session persistence |
| `customer_profile.db` | SQLite file (read-only volume mount) | Local filesystem | Customer demographic and profile data |
| `feature_importance.db` | SQLite file (read-only volume mount) | Local filesystem | CatBoost model feature importance data |
| `model_predictions.db` | SQLite file (read-only volume mount) | Local filesystem | Pre-computed ML risk classification predictions |
| `application_profile.db` | SQLite file (read-only volume mount) | Local filesystem | Insurance application data |
| `postgres_data` | Docker named volume | Self-hosted | Persistent PostgreSQL storage |
| Anthropic Claude (claude-haiku-4-5, claude-sonnet-4) | External LLM API | Anthropic (SaaS) | Agent reasoning, specialist assessment, aggregation |
| Google Gemini (gemini-3-flash-preview) | External LLM API | Google Cloud (SaaS) | Optional alternative LLM provider |
| GitHub Actions runners (ubuntu-latest) | CI/CD compute | GitHub (SaaS) | Execute AI tooling workflows |
| `ai-delivery-outputs` (separate GitHub repo) | GitHub repository | GitHub (SaaS) | Stores generated docs, test files, review reports |
| SendGrid | Email API | Twilio/SendGrid (SaaS) | Workflow completion notifications |
| CatBoostClassifier model | ML model (serialised) | Local / container | Risk classification (Preferred/Standard/Substandard) |

---

## 3. Data Flow

### Runtime (Chatbot)

1. **User input**: An underwriter types a message in the Chainlit frontend (port 8080); the frontend POSTs `{ message, session_id, model, mode, temperature }` to the backend `/chat` endpoint (port 8000) via internal Docker network.
2. **Agent invocation**: The FastAPI backend calls `build_agent()` which instantiates a LangGraph agent, connecting to Redis (`REDIS_HOST=redis:6379`) to load the prior conversation checkpoint for the given `thread_id` (session_id).
3. **Tool dispatch — customer profile**: The agent LLM (Claude Haiku by default) determines it needs customer data and emits a JSON `tool_call` for `get_customer_profile`, which queries the read-only `customer_profile.db` SQLite database mounted at `/data/`.
4. **Tool dispatch — lookalike**: Optionally the agent calls `customer_lookalike`, which looks up `backend/tmp/customer_similarity_dict.json` to find similar historical customers.
5. **Tool dispatch — risk assessment**: The agent calls `run_underwriting_assessment(profile)`, which fans out **parallel async calls** (semaphore-limited to 4) to the specialist LLM for each `ASSESSMENT_CATEGORIES` domain (finance, health, life, etc.) using prompts from `prompts/assessment_criterias.json`.
6. **Aggregation**: All specialist category results are gathered and passed to the aggregator LLM (`claude-haiku` with structured output) which produces a typed `UnderwritingReport` Pydantic object (risk class, findings, follow-up items, data gaps).
7. **Streaming response**: The backend streams all events (tool start/end, LLM tokens, structured report) back to the frontend as Server-Sent Events (SSE). Chart data is buffered and sent after the text response.
8. **Checkpoint persistence**: LangGraph writes the updated conversation state back to Redis so future turns within the same session can reference prior context.
9. **Database persistence**: Chainlit stores session/user metadata in PostgreSQL (`chainlit` DB, port 5432).

### CI/CD (AI Tooling Workflows)

10. **Trigger**: A GitHub event (PR open, push to main, tag, schedule, or manual dispatch) fires one of the five workflow YAML files.
11. **Source fetch**: The workflow runner checks out the repository and the Python script calls the GitHub API (`GET /repos/{owner}/{repo}/git/trees/HEAD`) to fetch file contents.
12. **Claude call**: The script sends file content as a prompt to the Anthropic API (`claude-sonnet-4-6`) and receives structured output (JSON or Markdown).
13. **Output commit**: The result is Base64-encoded and written back to the `ai-delivery-outputs` repository via GitHub API (`PUT /repos/{owner}/{repo}/contents/{path}`).
14. **Notification**: SendGrid API is called to email `kylo.deng@capco.com` with an HTML summary and link to the output file.

---

## 4. Security Posture

### What IS secured

- **Secrets managed via GitHub Actions secrets**: `ANTHROPIC_API_KEY`, `GH_TOKEN`, `SENDGRID_API_KEY` are injected as environment secrets, not hardcoded in source.
- **Read-only database mounts**: SQLite databases are mounted with `:ro` flag, preventing the backend container from modifying source data.
- **Agent prompt hardening**: The system prompt explicitly instructs the agent to never reveal internal instructions or tool inventory.
- **SQLite databases not exposed externally**: Database ports are not published; only the backend container accesses them.
- **Semaphore on parallel LLM calls**: Prevents runaway concurrency (capped at 4) reducing risk of API abuse/cost explosion.

### What is NOT secured — Gaps

- ⚠️ **No authentication on the `/chat` endpoint**: `CORSMiddleware` is configured with `allow_origins=["*"]`, `allow_methods=["*"]`, `allow_headers=["*"]`. Any client on any origin can call the API with no API key, JWT, or session token required.
- ⚠️ **PostgreSQL credentials hardcoded in `docker-compose.yml`**: `POSTGRES_USER=chainlit`, `POSTGRES_PASSWORD=chainlit` are plaintext in the Compose file and committed to the repository.
- ⚠️ **Redis has no authentication**: The Redis container is started with no password and no ACL. Any process that can reach port 6379 can read or overwrite conversation checkpoints (including potentially customer PII).
- ⚠️ **No encryption at rest**: SQLite database files containing customer PII (profiles, KYC, financials) are unencrypted on the host filesystem. PostgreSQL data volume is also unencrypted.
- ⚠️ **No encryption in transit between containers**: Internal Docker network traffic (backend↔Redis, backend↔PostgreSQL, frontend↔backend) uses plain TCP with no TLS.
- ⚠️ **Customer PII sent to external LLM APIs**: Customer profile data (age, income, medical conditions, nationality, etc.) is transmitted to Anthropic and Google Gemini APIs. No data residency controls or PII-scrubbing layer is present.
- ⚠️ **`GH_TOKEN` scope unknown**: The `GH_TOKEN` secret is used to write to a separate output repository. If over-scoped (e.g., `repo` full access), a compromised workflow could read/write any repository in the organisation. [TODO: confirm minimum required scopes — likely `contents:write` on `ai-delivery-outputs` only]
- ⚠️ **`customer_similarity_dict.json` committed to repository**: `backend/tmp/customer_similarity_dict.json` contains `CUST0000XXXX` customer IDs in plain text checked into source control.
- ⚠️ **No input sanitisation**: User messages from the frontend are passed directly to the LLM agent without sanitisation, creating prompt injection risk.
- ⚠️ **No rate limiting**: No rate limiting is applied to the `/chat` endpoint, exposing the system to LLM cost abuse.
- ⚠️ **Ports 6379 and 5432 published to host**: Redis and PostgreSQL ports are bound to `0.0.0.0` on the host by default in Docker Compose, making them accessible from outside the host if firewall rules permit.

---

## 5. Environment Variables and Secrets

| Name | Required | Sensitivity | Where set |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | 🔴 High — API billing key | GitHub Actions secret; `.env` file for local dev |
| `GOOGLE_API_KEY` | Yes (if using Gemini) | 🔴 High — API billing key | `.env` file (backend) |
| `GH_TOKEN` | Yes (CI workflows) | 🔴 High — GitHub repo write access | GitHub Actions secret |
| `SENDGRID_API_KEY` | Yes (CI workflows) | 🔴 High — email sending key | GitHub Actions secret |
| `REDIS_HOST` | No | 🟢 Low | `docker-compose.yml` environment; defaults to `localhost` |
| `DATABASE_URL` | Yes (frontend) | 🟡 Medium — contains DB password | `docker-compose.yml` environment (plaintext) |
| `BACKEND_URL` | Yes (frontend) | 🟢 Low | `docker-compose.yml` environment |
| `POSTGRES_USER` | Yes | 🟡 Medium | `docker-compose.yml` (hardcoded: `chainlit`) |
| `POSTGRES_PASSWORD` | Yes | 🔴 High — DB credential | `docker-compose.yml` (hardcoded: `chainlit`) ⚠️ |
| `POSTGRES_DB` | Yes | 🟢 Low | `docker-compose.yml` (hardcoded: `chainlit`) |
| `OUTPUT_REPO` | No (CI) | 🟢 Low | GitHub Actions workflow env (default: `ai-delivery-outputs`) |
| `OUTPUT_REPO_OWNER` | No (CI) | 🟢 Low | GitHub Actions workflow env |
| `NOTIFY_EMAIL` | No (CI) | 🟡 Medium | GitHub Actions workflow env (hardcoded: `kylo.deng@capco.com`) |
| `SENDER_EMAIL` | No (CI) | 🟡 Medium | GitHub Actions workflow env |

> [TODO: Confirm whether a `.env` file template (`env.example`) exists and what additional variables are expected by the backend at runtime beyond `ANTHROPIC_API_KEY` and `GOOGLE_API_KEY`]

---

## 6. Dependencies

| Dependency | Type | Purpose | Version / Notes |
|---|---|---|---|
| Anthropic Claude (claude-haiku-4-5-20251001, claude-sonnet-4-20250514, claude-sonnet-4-6) | External SaaS API | Primary LLM for agent, specialist assessment, aggregation, and CI tooling | Paid API |
| Google Gemini (gemini-3-flash-preview) | External SaaS API | Optional alternative LLM provider | Paid API; model name appears to be non-standard [TODO: verify correct model ID] |
| SendGrid | External SaaS API | Email notification delivery from CI workflows | Paid API |
| GitHub API (api.github.com) | External SaaS API | File fetching, PR comments, output repo writes | Uses PAT (`GH_TOKEN`) |
| LangChain / LangGraph | Python library | Agent orchestration, graph state management, tool wrapping | `langchain-core`, `langchain-anthropic`, `langchain-google-genai`, `langgraph` |
| `langgraph-checkpoint-redis` | Python library | Redis-backed conversation state persistence | `langgraph.checkpoint.redis.aio` |
| FastAPI + uvicorn | Python library | Backend HTTP server and SSE streaming | |
| Chainlit | Python/Node library | Frontend chat UI framework | Version [TODO: check `frontend/` build files] |
| CatBoost | ML library / model artefact | Pre-trained risk classification model | v1.0, trained 2024-06-01 |
| Redis Stack Server | Docker image | Conversation checkpoint store | `redis/redis-stack-server:7.2.0-v14` |
| PostgreSQL | Docker image | Chainlit session/user persistence | `postgres:16-alpine` |
| `sse-starlette` | Python library | Server-Sent Events streaming | |
| `pydantic` | Python library | Structured output models | |
| `python-dotenv` | Python library | `.env` file loading | |
| `ai-delivery-outputs` | Separate GitHub repo | Stores all CI-generated artefacts (docs, tests, reports) | Must exist in same GitHub org/owner |

---

## 7. Deployment Instructions

### Prerequisites

- Docker and Docker Compose v2 installed
- An `.env` file in the repository root with at minimum:

```bash
ANTHROPIC_API_KEY=sk-ant-...
GOOGLE_API_KEY=...          # Only if using Gemini model
```

### Local / Docker Compose Deployment

```bash
# 1. Clone the repository
git clone https://github.com/kylodeng/underwriting_chatbot-main.git
cd underwriting_chatbot-main

# 2. Create the .env file
cp .env.example .env          # [TODO: confirm .env.example exists]
# Edit .env and populate ANTHROPIC_API_KEY (and GOOGLE_API_KEY if needed)

# 3. Ensure SQLite database files are present
ls database/
# Expected: customer_profile.db  feature_importance.db  model_predictions.db  application_profile.db
# [TODO: document how to obtain/regenerate these database files]

# 4. Build and start all services
docker compose up --build -d

# 5. Verify health
curl http://localhost:8000/health
# Expected: {"status":"ok"}

# 6. Access the chatbot UI
open http://localhost:8080

# 7. View logs
docker compose logs -f backend
docker compose logs -f frontend

# 8. Stop all services
docker compose down

# 9. Stop and remove persistent volumes (full reset)
docker compose down -v
```

### CI/CD Workflows (GitHub Actions)

```bash
# Workflows are automatically triggered. For manual dispatch:

# Tool 1 — Code Review (repo-wide)
# GitHub UI: Actions → "Tool 1 — Code Review" → Run workflow → mode=repo

# Tool 2 — Tech Documentation
# Triggered automatically on push to main, or:
# GitHub UI: Actions → "Tool 2 — Tech Documentation" → Run workflow

# Tool 3 — Business Documentation
# Triggered on version tag:
git tag v1.0.0
git push origin v1.0.0

# Tool 4 — Auto Testing
# Triggered on PR open/sync to src/** or *.py files, or manual dispatch

# Tool 5 — UAT Facilitation
# Triggered on release branch creation:
git checkout -b release/1.0.0
git push origin release/1.0.0
```

### Required GitHub Secrets (set in repo Settings → Secrets → Actions)

```
ANTHROPIC_API_KEY   — Anthropic API key
GH_TOKEN            — GitHub PAT with write access to ai-delivery-outputs repo
SENDGRID_API_KEY    — SendGrid API key for email notifications
```

---

## 8. Risks and TODOs

### Extracted from Code

| Location | Issue |
|---|---|
| `backend/agent/graph