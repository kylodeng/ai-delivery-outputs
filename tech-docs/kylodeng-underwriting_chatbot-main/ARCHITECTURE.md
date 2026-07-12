# Architecture Document: `kylodeng/underwriting_chatbot-main`

---

## 1. Overview

The Underwriting Chatbot is an AI-powered insurance underwriting assistant that enables underwriters to assess customer risk profiles through a conversational interface. The backend orchestrates a multi-agent LLM pipeline (using Anthropic Claude and Google Gemini) that runs specialist underwriting assessments across domains such as finance, health, and life insurance, then aggregates results into a structured `UnderwritingReport`. Customer data is sourced from pre-built SQLite databases and a CatBoost-trained risk classification model. The system is containerised via Docker Compose and exposes a streaming FastAPI backend consumed by a Chainlit-based frontend. A parallel CI/CD pipeline of five GitHub Actions workflows uses Claude to automate code review, technical documentation, business documentation, test generation, and UAT facilitation across the repository lifecycle.

---

## 2. Resources Deployed

| Resource | Type | Cloud Provider | Purpose |
|---|---|---|---|
| `backend` | Docker container (FastAPI, Python) | Local / self-hosted | Streaming chat API, LangGraph agent orchestration, underwriting assessment pipeline |
| `frontend` | Docker container (Chainlit) | Local / self-hosted | Conversational UI for underwriters |
| `redis` (redis-stack-server:7.2.0-v14) | Docker container | Local / self-hosted | LangGraph agent checkpoint/memory store (conversation state per `thread_id`) |
| `postgres` (postgres:16-alpine) | Docker container | Local / self-hosted | Chainlit session and user data persistence |
| `customer_profile.db` | SQLite file (read-only volume mount) | Local / self-hosted | Customer demographic and profile data |
| `feature_importance.db` | SQLite file (read-only volume mount) | Local / self-hosted | CatBoost model feature importance scores |
| `model_predictions.db` | SQLite file (read-only volume mount) | Local / self-hosted | Pre-computed CatBoost risk classification predictions |
| `application_profile.db` | SQLite file (read-only volume mount) | Local / self-hosted | Insurance application metadata |
| `postgres_data` | Docker named volume | Local / self-hosted | Persistent PostgreSQL storage |
| Anthropic Claude (claude-sonnet-4-20250514, claude-haiku-4-5-20251001) | External LLM API | Anthropic (third-party) | Agent reasoning, specialist assessment, aggregation, and all CI/CD AI tooling |
| Google Gemini (gemini-3-flash-preview) | External LLM API | Google Cloud (third-party) | Alternative LLM provider (configured, optional) |
| GitHub Actions runners (ubuntu-latest) | CI/CD compute | GitHub | Automated code review, documentation, testing, UAT workflows |
| `ai-delivery-outputs` (GitHub repo) | External GitHub repository | GitHub | Stores AI-generated artefacts: docs, test files, UAT packs, code review reports |
| SendGrid | External email API | Twilio/SendGrid (third-party) | Notification emails for CI/CD workflow outputs |

---

## 3. Data Flow

### Runtime Chat Flow

1. **User input**: An underwriter types a message in the Chainlit frontend (port 8080). The frontend sends an HTTP POST to `/chat` on the FastAPI backend (port 8000), carrying `message`, `session_id`, `model`, `temperature`, and `mode`.
2. **Agent initialisation**: The backend calls `build_agent()`, which instantiates a LangGraph agent with an Anthropic or Gemini LLM, three tools (`get_customer_profile`, `customer_lookalike`, `run_underwriting_assessment`), and a Redis-backed `AsyncRedisSaver` checkpointer keyed to `thread_id` (the `session_id`).
3. **Conversation state hydration**: LangGraph loads prior conversation turns from Redis (port 6379) using the `thread_id`, enabling multi-turn memory.
4. **LLM reasoning (agent loop)**: The agent LLM (tagged `"agent"`) receives the system prompt (embedding the model card from `model_card.json`) and the user message. It decides which tool to invoke and emits a JSON action blob.
5. **Tool execution — profile lookup**: `get_customer_profile` queries `customer_profile.db` (SQLite, read-only) and related databases for the requested customer ID.
6. **Tool execution — lookalike**: `customer_lookalike` looks up pre-computed similar customer IDs from `customer_similarity_dict.json` (in-memory/file).
7. **Tool execution — underwriting assessment**: `run_underwriting_assessment` receives the customer profile string and fans out up to 4 concurrent async calls (semaphore-gated) to the specialist LLM (`claude-haiku-4-5-20251001`, tagged `"thinking"`), one per assessment category (finance, health, life, etc.), using prompts from `assessment_criterias.json`.
8. **Aggregation**: The aggregator LLM (`claude-haiku-4-5-20251001` with large token budget) receives all specialist outputs and produces a structured `UnderwritingReport` Pydantic object via `.with_structured_output()`.
9. **Streaming response**: The FastAPI endpoint streams Server-Sent Events (SSE) back to the frontend — emitting `tool_start`, `tool_end`, `thinking` (specialist LLM tokens), `response` (agent answer tokens), `chart` (visualisation payloads), and `done` events.
10. **Session persistence**: Conversation checkpoints are written back to Redis; Chainlit user/session metadata is persisted to PostgreSQL.

### CI/CD AI Tooling Flow

1. GitHub event (PR, push to main, tag, schedule, or manual dispatch) triggers one of five GitHub Actions workflows.
2. The workflow runner checks out the source repository, installs `anthropic` and `requests`, and runs the corresponding Python script.
3. The script reads source/IaC files from the GitHub API (via `GH_TOKEN`), constructs prompts, and calls `claude-sonnet-4-6` via the Anthropic API (`ANTHROPIC_API_KEY`).
4. Output artefacts (markdown docs, JSON reports, test files, UAT packs) are committed to the `ai-delivery-outputs` GitHub repository via the GitHub Contents API.
5. For PR reviews, a comment is posted directly to the pull request.
6. A notification email is sent via SendGrid (`SENDGRID_API_KEY`) to `kylo.deng@capco.com`.
7. An audit log entry is written (destination [TODO: confirm audit log storage location — local file or output repo]).

---

## 4. Security Posture

### Secured

- **API keys stored as GitHub Secrets**: `ANTHROPIC_API_KEY`, `GH_TOKEN`, `SENDGRID_API_KEY` are injected at workflow runtime and not hardcoded in source.
- **SQLite databases mounted read-only** (`ro` flag in Docker Compose volumes): prevents write access from the backend container.
- **Redis not exposed externally** in production intent (port 6379 is published to host in `docker-compose.yml`, but internal service-to-service communication uses the `redis` hostname).
- **PostgreSQL data in named Docker volume**: persists across restarts, not bind-mounted to an arbitrary host path.
- **Structured output validation**: `UnderwritingReport` uses Pydantic models with `Literal` constraints on risk classifications.

### Not Secured / Gaps

- ⚠️ **CORS is fully open**: `allow_origins=["*"]`, `allow_methods=["*"]`, `allow_headers=["*"]` in `main.py`. Any origin can call the `/chat` endpoint. No authentication or authorisation is implemented on the FastAPI backend.
- ⚠️ **No HTTPS/TLS**: All inter-service communication (frontend→backend, backend→Redis, backend→Postgres) is unencrypted plaintext within Docker Compose. There is no TLS termination layer (no nginx, no cert configuration).
- ⚠️ **Redis has no authentication**: The `redis-stack-server` container is launched with no password (`requirepass` not set). Conversation state (including customer PII from tool results) is stored unencrypted in Redis.
- ⚠️ **PostgreSQL credentials are hardcoded** in `docker-compose.yml` (`POSTGRES_USER: chainlit`, `POSTGRES_PASSWORD: chainlit`). These are trivially guessable default credentials.
- ⚠️ **No encryption at rest**: SQLite databases, Redis data, and PostgreSQL data are stored unencrypted on the Docker host filesystem. Customer PII and financial data are in scope.
- ⚠️ **Customer PII sent to external LLM APIs**: Customer profiles (age, income, medical conditions, nationality, financial data) are transmitted to Anthropic's API and optionally Google's API. No data residency controls, PII masking, or anonymisation is applied before transmission.
- ⚠️ **`customer_similarity_dict.json` stored in `backend/tmp/`**: A pre-computed similarity index containing thousands of customer IDs is committed directly to the repository.
- ⚠️ **`GH_TOKEN` scope is unknown**: The token is used to read source repos, write to `ai-delivery-outputs`, and post PR comments. [TODO: confirm the token has minimum required scopes — `repo` (scoped) rather than full `repo` or `admin`].
- ⚠️ **No input sanitisation on `session_id`**: The `session_id` from the chat request is passed directly as `thread_id` to Redis without validation. A malicious client could potentially target another user's session.
- ⚠️ **No rate limiting**: The `/chat` endpoint has no rate limiting, authentication, or request throttling. Each request builds a new agent and streams from the LLM API.
- ⚠️ **`.env` file dependency**: The backend loads secrets from a `.env` file at startup (`load_dotenv`). If the `.env` is committed or left on disk, secrets are exposed. No `.env.example` or secret management system (e.g., Vault, AWS Secrets Manager) is evident.
- ⚠️ **Ports 6379 and 5432 published to host**: Both Redis and PostgreSQL ports are bound to `0.0.0.0` on the host by default in Docker Compose, making them accessible from outside the Docker network if the host is networked.

---

## 5. Environment Variables and Secrets

| Name | Required | Sensitivity | Where set |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | 🔴 Secret | GitHub Secrets (CI/CD); `.env` file (backend runtime) |
| `GOOGLE_API_KEY` | Yes (if Gemini used) | 🔴 Secret | `.env` file (backend runtime) |
| `GH_TOKEN` | Yes (CI/CD) | 🔴 Secret | GitHub Secrets |
| `SENDGRID_API_KEY` | Yes (CI/CD notifications) | 🔴 Secret | GitHub Secrets |
| `REDIS_HOST` | No | 🟢 Low | `docker-compose.yml` environment block (value: `redis`) |
| `POSTGRES_USER` | Yes | 🟡 Medium | `docker-compose.yml` (hardcoded: `chainlit`) |
| `POSTGRES_PASSWORD` | Yes | 🔴 Secret | `docker-compose.yml` (hardcoded: `chainlit`) — **insecure** |
| `POSTGRES_DB` | Yes | 🟢 Low | `docker-compose.yml` (hardcoded: `chainlit`) |
| `DATABASE_URL` | Yes (frontend) | 🟡 Medium | `docker-compose.yml` environment block (contains credentials) |
| `BACKEND_URL` | Yes (frontend) | 🟢 Low | `docker-compose.yml` (value: `http://backend:8000`) |
| `OUTPUT_REPO` | No | 🟢 Low | GitHub Actions env (default: `ai-delivery-outputs`) |
| `OUTPUT_REPO_OWNER` | No | 🟢 Low | GitHub Actions env (derived from `github.repository_owner`) |
| `NOTIFY_EMAIL` | No | 🟢 Low | GitHub Actions env (hardcoded: `kylo.deng@capco.com`) |
| `SENDER_EMAIL` | No | 🟢 Low | GitHub Actions env (hardcoded: `noreply@ai-delivery.capco.com`) |
| `REVIEW_MODE` | No | 🟢 Low | Set dynamically in CI/CD workflow steps |
| `PR_NUMBER` | No | 🟢 Low | Set dynamically in CI/CD workflow steps |
| `RELEASE_VERSION` | No | 🟢 Low | Set dynamically in CI/CD workflow steps |
| `PROJECT_NAME` | No | 🟢 Low | Set dynamically in CI/CD workflow steps |
| `UAT_MODE` | No | 🟢 Low | Set dynamically in CI/CD workflow steps |
| `TEST_MODE` | No | 🟢 Low | Set dynamically in CI/CD workflow steps |

---

## 6. Dependencies

| Dependency | Type | Purpose | Notes |
|---|---|---|---|
| Anthropic API (Claude) | External SaaS API | LLM inference for agent reasoning, specialist assessment, aggregation, all CI/CD tools | Models: `claude-sonnet-4-20250514`, `claude-haiku-4-5-20251001`, `claude-sonnet-4-6` |
| Google Generative AI (Gemini) | External SaaS API | Alternative LLM provider | `gemini-3-flash-preview`; configured but [TODO: confirm if actively used in production] |
| LangGraph / LangChain | Python library | Agent graph orchestration, tool calling, streaming | `langgraph`, `langchain-core`, `langchain-anthropic`, `langchain-google-genai` |
| Redis (redis-stack-server) | Infrastructure | LangGraph conversation checkpointing | In-container; [TODO: migrate to managed Redis for production per `graph.py` TODO comment] |
| PostgreSQL | Infrastructure | Chainlit session/user persistence | In-container |
| Chainlit | Python framework | Frontend conversational UI | Version [TODO: not specified in provided files] |
| FastAPI + sse-starlette | Python framework | Backend HTTP/SSE API | |
| CatBoost (pre-trained model) | ML model | Risk classification | Model outputs stored in `model_predictions.db`; model itself [TODO: confirm if `.cbm` file included or only predictions] |
| `ai-delivery-outputs` (GitHub repo) | External GitHub repository | Stores all CI/CD generated artefacts | Must exist and be writable by `GH_TOKEN` |
| SendGrid | External SaaS API | Email notifications from CI/CD workflows | |
| GitHub API | External API | PR diffs, file content, PR comments (CI/CD scripts) | |
| `pydantic` | Python library | Structured output validation for `UnderwritingReport` | |
| `python-dotenv` | Python library | `.env` file loading in backend | |

---

## 7. Deployment Instructions

### Prerequisites

- Docker and Docker Compose v2+ installed on the host
- A `.env` file in the repository root (or `./backend/.env`) containing at minimum:

```bash
ANTHROPIC_API_KEY=sk-ant-...
GOOGLE_API_KEY=AIza...        # required if using Gemini
```

- PostgreSQL init script at `./postgres/init.sql` (referenced in `docker-compose.yml`)
- SQLite database files present at:
  - `./database/customer_profile.db`
  - `./database/feature_importance.db`
  - `./database/model_predictions.db`
  - `./database/application_profile.db`

### Start all services

```bash
docker compose up --build -d
```

### Verify health

```bash
# Backend health check
curl http://localhost:8000/health

# Check all containers are running
docker compose ps

# Tail logs
docker compose logs -f backend
docker compose logs -f frontend
```

### Access the application

- **Frontend (Chainlit UI):** `http://localhost:8080`
- **Backend API:** `http://localhost:8000`
- **Redis:** `localhost:6379`
- **PostgreSQL:** `localhost:5432`

### Stop and clean up

```bash
# Stop containers (preserve volumes)
docker compose down

# Stop and remove volumes (destructive — deletes PostgreSQL data)
docker compose down -v
```

### CI/CD Workflows (GitHub Actions)

Workflows