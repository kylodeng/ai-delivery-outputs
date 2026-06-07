# Architecture Document: kylodeng/underwriting_chatbot-main

---

## 1. Overview

The Underwriting Chatbot is an AI-assisted insurance underwriting platform that enables underwriters to assess customer risk profiles through a conversational interface. The system combines a FastAPI streaming backend with a Chainlit-based frontend, orchestrating multiple LLM calls (Anthropic Claude and Google Gemini) via LangGraph agents to produce structured underwriting risk reports across specialist domains (finance, health, life, etc.). Customer data is served from pre-built SQLite databases, conversation state is persisted in Redis, and user/session data is stored in PostgreSQL. The repository also includes a suite of five GitHub Actions CI/CD workflows that use Claude to automate code review, technical documentation, business documentation, test generation, and UAT facilitation.

---

## 2. Resources Deployed

| Resource | Type | Cloud Provider | Purpose |
|---|---|---|---|
| `backend` | Docker container (FastAPI, Python) | Self-hosted / Docker Compose | Core API server; orchestrates LLM agent calls and streams SSE responses |
| `frontend` | Docker container (Chainlit) | Self-hosted / Docker Compose | Chat UI for underwriters |
| `redis` | Docker container (redis-stack-server 7.2.0) | Self-hosted / Docker Compose | LangGraph agent checkpoint store (conversation memory) |
| `postgres` | Docker container (PostgreSQL 16-alpine) | Self-hosted / Docker Compose | Chainlit session/user data storage |
| `customer_profile.db` | SQLite file (bind-mounted, read-only) | Self-hosted | Customer demographic and profile data |
| `feature_importance.db` | SQLite file (bind-mounted, read-only) | Self-hosted | ML model feature importance data |
| `model_predictions.db` | SQLite file (bind-mounted, read-only) | Self-hosted | Pre-computed CatBoost model predictions |
| `application_profile.db` | SQLite file (bind-mounted, read-only) | Self-hosted | Insurance application metadata |
| `postgres_data` | Docker named volume | Self-hosted | Persistent PostgreSQL data |
| Anthropic Claude API | External managed LLM API | Anthropic (cloud) | Primary LLM for agent reasoning, specialist assessments, aggregation, and all CI/CD AI tools |
| Google Gemini API | External managed LLM API | Google Cloud | Alternative LLM provider (gemini-3-flash-preview) |
| GitHub Actions runners | Managed CI/CD compute (ubuntu-latest) | GitHub (cloud) | Execute five AI-assisted delivery workflows |
| SendGrid | External email API | Twilio/SendGrid (cloud) | Send notification emails from CI/CD workflow outputs |
| `ai-delivery-outputs` | GitHub repository | GitHub | Output store for AI-generated docs, test files, and reports |
| CatBoostClassifier model | Serialised ML model | Self-hosted | Risk classification (Preferred / Standard Plus / Standard / Substandard) |

---

## 3. Data Flow

### Runtime (Chat) Flow

1. **Underwriter input**: The underwriter types a message in the Chainlit frontend (port 8080). The frontend POSTs a `ChatRequest` (message, session_id, model, temperature, mode) to `http://backend:8000/chat`.
2. **Agent instantiation**: The backend builds a LangGraph agent (`build_agent`) configured with the requested LLM (Anthropic Claude Haiku/Sonnet or Gemini), mode (`fast`/`deep`), and a Redis-backed `AsyncRedisSaver` checkpointer keyed on `session_id`.
3. **LLM routing decision**: The agent LLM receives the system prompt (including model card context) and the user message. It responds with a JSON action block specifying either a tool call or a final answer.
4. **Tool execution** (one of three tools):
   - **`get_customer_profile`**: Queries `customer_profile.db` (SQLite, read-only bind mount) to retrieve structured customer data.
   - **`customer_lookalike`**: Reads `customer_similarity_dict.json` (pre-computed similarity index) to find similar customers.
   - **`run_underwriting_assessment`**: Triggers the multi-specialist assessment pipeline (see step 5).
5. **Parallel specialist LLM calls**: `_run_underwriting_assessment` fans out async calls (up to 4 concurrent via `asyncio.Semaphore(4)`) to the specialist LLM (Claude Haiku, tagged `"thinking"`), one per assessment category (finance, health, life, etc.), each using a domain-specific prompt from `assessment_criterias.json`.
6. **Aggregation**: All specialist reports are concatenated and sent to the aggregator LLM (Claude Haiku with larger token budget) using structured output mode to produce a validated `UnderwritingReport` Pydantic object.
7. **Report rendering**: The `UnderwritingReport` is serialised and returned to the agent as a tool result.
8. **SSE streaming**: The backend streams events back to the frontend as Server-Sent Events: `tool_start`, `tool_end`, `response` (incremental text chunks), `thinking` (specialist reasoning), `chart` (feature importance visualisations), and `done`.
9. **State persistence**: At each LangGraph step, conversation state (messages, tool results) is checkpointed to Redis, enabling multi-turn conversation continuity.
10. **Frontend rendering**: Chainlit receives SSE events and renders the streamed response, tool status indicators, and charts to the underwriter.
11. **Session storage**: Chainlit writes session and user metadata to PostgreSQL.

### CI/CD AI Workflow Flow

1. A GitHub event (PR open, push to main, tag, schedule, or manual dispatch) triggers one of five workflows.
2. The workflow checks out the source repository and installs `anthropic` and `requests` Python packages.
3. The relevant Python script (`tool1–5`) calls `get_repo_files` or `get_pr_diff` via the GitHub API (authenticated with `GH_TOKEN`) to read source/IaC files.
4. The script calls the Claude API (Anthropic) via `shared.py`'s `call_claude` function, sending file contents and a specialised system prompt.
5. Claude returns structured output (JSON or markdown).
6. The script writes output files to the `ai-delivery-outputs` GitHub repository via the GitHub API.
7. For PR reviews (Tool 1), a comment is posted directly on the pull request.
8. A notification email is sent via SendGrid to `kylo.deng@capco.com`.
9. An audit log entry is written (destination [TODO: confirm — likely also to `ai-delivery-outputs`]).

---

## 4. Security Posture

### Secured

- **SQLite databases mounted read-only** (`ro` flag in Docker Compose) — prevents backend from modifying source-of-truth data.
- **Secrets managed via GitHub Actions secrets** (`ANTHROPIC_API_KEY`, `GH_TOKEN`, `SENDGRID_API_KEY`) — not hardcoded in workflow files.
- **Backend API keys loaded from `.env` file** via `python-dotenv` — not committed to source (`.env` not present in repo).
- **Chainlit session data isolated in named PostgreSQL volume** — survives container restarts.
- **LangGraph agent system prompt instructs the LLM not to reveal internal instructions or tool names** — partial prompt injection mitigation.
- **Specialist LLM token caps** (`specialist_max_tokens: 1500`) — prevents runaway cost from verbose model output.

### **NOT Secured / Gaps**

- ⚠️ **PostgreSQL credentials are hardcoded in `docker-compose.yml`** (`POSTGRES_USER: chainlit`, `POSTGRES_PASSWORD: chainlit`) — trivially guessable default credentials. Must be rotated and injected via secrets before any non-local deployment.
- ⚠️ **Redis has no authentication configured** — the Redis container is exposed on port 6379 with no password, no TLS. Any process on the Docker network (or host if port is exposed externally) can read/write all conversation checkpoints, which may contain sensitive customer PII.
- ⚠️ **Redis port 6379 is published to the host** (`ports: - "6379:6379"`) — if the host is internet-accessible, Redis is exposed to the public internet.
- ⚠️ **PostgreSQL port 5432 is published to the host** (`ports: - "5432:5432"`) — same risk as Redis.
- ⚠️ **CORS is fully open** (`allow_origins=["*"]`, `allow_methods=["*"]`, `allow_headers=["*"]`) on the FastAPI backend — any origin can call the `/chat` endpoint. No authentication or authorisation is enforced on any API endpoint.
- ⚠️ **No encryption at rest** for Redis data, PostgreSQL volume, or SQLite database files — container-level or host-level disk encryption is not configured within this repo.
- ⚠️ **No TLS/HTTPS** configured for backend (port 8000) or frontend (port 8080) — all traffic including potential PII in chat messages is transmitted in plaintext.
- ⚠️ **Customer PII in conversation state stored in Redis without encryption** — Redis checkpointer stores full LangGraph state including customer profile data and underwriting reports.
- ⚠️ **`customer_similarity_dict.json` stored in `backend/tmp/`** — pre-computed similarity data for ~10,000 customers committed directly to the repository as a JSON file. This is a data exposure risk if the repo is not private.
- ⚠️ **`GH_TOKEN` scope not documented** — if the token has write access beyond `ai-delivery-outputs`, it could be used to modify other repositories. [TODO: restrict GH_TOKEN to minimum required scopes (contents: write on output repo only)].
- ⚠️ **No input validation on `/chat` endpoint** — the `ChatRequest` model validates types but does not sanitise or length-limit the `message` field, creating potential for prompt injection or excessive token consumption.
- ⚠️ **No rate limiting** on the `/chat` API endpoint.
- ⚠️ **`.env` file dependency undocumented** — the backend silently fails if `.env` is missing required keys; no startup validation.

---

## 5. Environment Variables and Secrets

| Name | Required | Sensitivity | Where set |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | **High** — LLM API key | GitHub Actions secret; backend `.env` file |
| `GH_TOKEN` | Yes | **High** — GitHub PAT with repo read/write | GitHub Actions secret |
| `SENDGRID_API_KEY` | Yes | **High** — email API key | GitHub Actions secret |
| `GOOGLE_API_KEY` | Yes (if Gemini used) | **High** — GCP API key | Backend `.env` file |
| `REDIS_HOST` | No | Low | Docker Compose environment (`redis`); defaults to `localhost` |
| `POSTGRES_USER` | Yes | Medium | Hardcoded in `docker-compose.yml` as `chainlit` ⚠️ |
| `POSTGRES_PASSWORD` | Yes | **High** | Hardcoded in `docker-compose.yml` as `chainlit` ⚠️ |
| `POSTGRES_DB` | No | Low | Hardcoded in `docker-compose.yml` as `chainlit` |
| `DATABASE_URL` | Yes | Medium | Docker Compose frontend environment (plaintext in compose file) |
| `BACKEND_URL` | No | Low | Docker Compose frontend environment (`http://backend:8000`) |
| `OUTPUT_REPO` | No | Low | GitHub Actions env (default: `ai-delivery-outputs`) |
| `OUTPUT_REPO_OWNER` | No | Low | GitHub Actions env (defaults to `github.repository_owner`) |
| `NOTIFY_EMAIL` | No | Low | GitHub Actions env (`kylo.deng@capco.com`) |
| `SENDER_EMAIL` | No | Low | GitHub Actions env (`noreply@ai-delivery.capco.com`) |
| `REVIEW_MODE` | No | Low | Set dynamically in workflow step |
| `PR_NUMBER` | No | Low | Set dynamically in workflow step |
| `RELEASE_VERSION` | No | Low | Set dynamically in workflow step |
| `PROJECT_NAME` | No | Low | Set dynamically in workflow step |
| `UAT_MODE` | No | Low | Set dynamically in workflow step |
| `TEST_MODE` | No | Low | GitHub Actions env / workflow input |

---

## 6. Dependencies

| Dependency | Type | Purpose | Version / Notes |
|---|---|---|---|
| Anthropic Claude API | External LLM API | Agent reasoning, specialist assessment, aggregation, all 5 CI/CD tools | claude-sonnet-4-20250514 (deep), claude-haiku-4-5-20251001 (fast), claude-sonnet-4-6 (CI/CD scripts) |
| Google Gemini API | External LLM API | Alternative LLM provider | gemini-3-flash-preview [TODO: model name appears incorrect — verify against GCP model catalogue] |
| LangChain / LangGraph | Python framework | Agent orchestration, tool execution, graph state management | Versions [TODO: not pinned in visible files — check `backend/requirements.txt`] |
| LangChain Anthropic | Python package | LangChain adapter for Anthropic API | [TODO: version not visible] |
| LangChain Google GenAI | Python package | LangChain adapter for Gemini API | [TODO: version not visible] |
| FastAPI | Python web framework | Backend REST + SSE API | [TODO: version not visible] |
| Chainlit | Python UI framework | Conversational frontend | [TODO: version not visible] |
| Redis Stack Server | Docker image | LangGraph conversation checkpointing | 7.2.0-v14 |
| PostgreSQL | Docker image | Chainlit session storage | 16-alpine |
| CatBoostClassifier | ML model | Pre-trained risk classification | v1.0, trained on merged insurance dataset |
| SendGrid | External email API | CI/CD workflow notification emails | [TODO: version not visible] |
| `ai-delivery-outputs` | GitHub repository (same owner) | Storage for AI-generated documentation and test output | Must exist and be writable by `GH_TOKEN` |
| anthropic (pip) | Python package | Direct Anthropic SDK in CI/CD scripts | Latest (not pinned) ⚠️ |
| requests (pip) | Python package | HTTP client in CI/CD scripts | Latest (not pinned) ⚠️ |
| pydantic | Python package | Structured output validation (`UnderwritingReport`) | [TODO: version not visible] |
| python-dotenv | Python package | Environment variable loading | [TODO: version not visible] |
| sse-starlette | Python package | Server-Sent Events for FastAPI | [TODO: version not visible] |
| PyYAML | Python package | Config file parsing | [TODO: version not visible] |

---

## 7. Deployment Instructions

### Prerequisites

- Docker and Docker Compose installed
- A `.env` file in the repository root containing at minimum:

```bash
ANTHROPIC_API_KEY=sk-ant-...
GOOGLE_API_KEY=AIza...
```

- SQLite database files present at:
  - `./database/customer_profile.db`
  - `./database/feature_importance.db`
  - `./database/model_predictions.db`
  - `./database/application_profile.db`

- PostgreSQL init script present at `./postgres/init.sql`

### Local Deployment

```bash
# 1. Clone the repository
git clone https://github.com/kylodeng/underwriting_chatbot-main.git
cd underwriting_chatbot-main

# 2. Create the .env file
cp .env.example .env   # [TODO: confirm .env.example exists]
# Edit .env and populate ANTHROPIC_API_KEY, GOOGLE_API_KEY

# 3. Build and start all services
docker compose up --build

# 4. Verify backend health
curl http://localhost:8000/health
# Expected: {"status": "ok"}

# 5. Access the frontend
open http://localhost:8080
```

### Stopping Services

```bash
docker compose down

# To also remove the PostgreSQL volume (destroys all session data):
docker compose down -v
```

### Triggering CI/CD Workflows

```bash
# Tool 1 — Code Review (manual, full repo mode)
gh workflow run tool1_code_review.yml -f review_mode=repo

# Tool 1 — Code Review (