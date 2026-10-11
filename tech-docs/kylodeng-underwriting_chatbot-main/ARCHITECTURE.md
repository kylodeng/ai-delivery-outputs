# Architecture Document: `kylodeng/underwriting_chatbot-main`

---

## 1. Overview

The Underwriting Chatbot is an AI-powered insurance underwriting assistant that enables underwriters to assess customer risk profiles through a conversational chat interface. The system ingests pre-computed customer data from several SQLite databases, retrieves customer profiles and similarity scores, and runs multi-specialist LLM assessments (using Anthropic Claude and optionally Google Gemini) to produce structured `UnderwritingReport` outputs covering health, finance, life, and other domain-specific risk dimensions. A FastAPI backend streams assessment results via Server-Sent Events (SSE) to a frontend chat UI, with Redis providing LangGraph agent conversation checkpointing and PostgreSQL backing the Chainlit frontend session store. A suite of five GitHub Actions CI/CD workflows additionally automate code review, technical documentation, business documentation, test generation, and UAT facilitation — all powered by Anthropic Claude via the Anthropic API.

---

## 2. Resources Deployed

| Resource | Type | Cloud Provider | Purpose |
|---|---|---|---|
| `backend` | Docker container (FastAPI + LangGraph) | Local / [TODO: target cloud platform] | Hosts the underwriting agent, assessment pipeline, and SSE `/chat` endpoint |
| `frontend` | Docker container (Chainlit UI) | Local / [TODO: target cloud platform] | Chat UI served to underwriters |
| `redis` (redis-stack-server 7.2.0) | Docker container | Local / [TODO: target cloud platform] | LangGraph agent conversation checkpointing (session memory) |
| `postgres` (PostgreSQL 16) | Docker container | Local / [TODO: target cloud platform] | Chainlit frontend session/message persistence |
| `customer_profile.db` | SQLite file (read-only volume mount) | Local filesystem | Customer demographic and profile data |
| `feature_importance.db` | SQLite file (read-only volume mount) | Local filesystem | CatBoostClassifier feature importance data |
| `model_predictions.db` | SQLite file (read-only volume mount) | Local filesystem | Pre-computed ML risk classification predictions |
| `application_profile.db` | SQLite file (read-only volume mount) | Local filesystem | Insurance application data |
| `postgres_data` | Docker named volume | Local filesystem | Persistent PostgreSQL storage |
| Anthropic Claude (claude-sonnet-4-20250514) | External API | Anthropic | High-quality/aggregator LLM for structured underwriting report generation |
| Anthropic Claude Haiku (claude-haiku-4-5-20251001) | External API | Anthropic | Fast agent-tier LLM for conversational turns and specialist assessments |
| Google Gemini (gemini-3-flash-preview) | External API | Google Cloud | Optional alternative LLM provider (configured but [TODO: not confirmed in active use]) |
| GitHub Actions runners (ubuntu-latest) | Managed CI/CD | GitHub | Execute all five AI delivery workflow tools |
| `ai-delivery-outputs` | GitHub repository | GitHub | Stores generated docs, test files, UAT packs, and audit logs from CI/CD tools |
| SendGrid | External email API | Twilio/SendGrid | Sends notification emails after each CI/CD tool run |
| CatBoostClassifier model | ML model artefact | Local (`model_card.json`) | Pre-trained risk classification model (not directly served at runtime — predictions pre-computed) |

---

## 3. Data Flow

### Runtime Chat Flow

1. **Underwriter sends a message** via the Chainlit frontend chat UI (port 8080). The frontend forwards the message as a POST request to `http://backend:8000/chat` with `session_id`, `model`, `mode`, `temperature`, and the message text.
2. **FastAPI `/chat` endpoint** receives the request and calls `build_agent()`, which initialises a LangGraph agent with the selected LLM (Anthropic Claude Haiku by default), attaches the Redis-backed `AsyncRedisSaver` checkpointer for session memory, and registers tools: `get_customer_profile`, `customer_lookalike`, and `run_underwriting_assessment`.
3. **LangGraph agent streams events** via `astream_events()`. The agent LLM (tagged `"agent"`) reasons over the conversation history (loaded from Redis by `thread_id`) and determines what action to take.
4. **Tool call — `get_customer_profile`**: The agent calls this tool with a customer ID, which queries `customer_profile.db` and related SQLite databases (mounted read-only) and returns structured customer metadata.
5. **Tool call — `customer_lookalike`**: The agent optionally calls this tool, which looks up `customer_similarity_dict.json` (pre-computed similarity index) to find analogous customer IDs.
6. **Tool call — `run_underwriting_assessment`**: The agent passes the retrieved customer profile string to this tool. The assessment pipeline:
   a. Reads `assessment_criterias.json` to load domain-specific prompts for each `ASSESSMENT_CATEGORIES` area (e.g., finance, health, life).
   b. Spawns up to 4 concurrent specialist LLM calls (Anthropic Claude Haiku, tagged `"thinking"`) via `asyncio.Semaphore(4)`, each assessing a different domain area.
   c. Collects specialist outputs and passes them to the aggregator LLM (Anthropic Claude Sonnet) with structured output binding (`UnderwritingReport` Pydantic model).
   d. Returns the rendered `UnderwritingReport` as a string.
7. **Streaming response**: The FastAPI handler yields SSE events (`tool_start`, `tool_end`, `response`, chart data) back to the frontend as the agent streams its final answer. Chart/visualization data buffered during tool execution is flushed after the text response.
8. **Session state persisted**: LangGraph writes updated conversation checkpoints back to Redis for the given `thread_id`, enabling multi-turn context.
9. **Frontend renders** the streamed response including structured report sections and any chart visualizations.

### CI/CD AI Tooling Flow

10. **GitHub event triggers** one of the five workflow YAML files (PR open, push to main, version tag, release branch creation, or schedule).
11. **GitHub Actions runner** checks out the source repo, installs `anthropic` and `requests`, and runs the corresponding Python script from `.github/scripts/`.
12. **Script reads source/IaC files** from the repository via the GitHub API (using `GH_TOKEN`).
13. **Script calls Anthropic Claude** (claude-sonnet-4-6 in `shared.py`) with a structured system prompt.
14. **Output is written** to the `ai-delivery-outputs` GitHub repository via the GitHub API (`GH_TOKEN` with write access).
15. **Notification email sent** via SendGrid API to `kylo.deng@capco.com`.

---

## 4. Security Posture

### What Is Secured

- **API keys stored as GitHub Actions secrets**: `ANTHROPIC_API_KEY`, `GH_TOKEN`, `SENDGRID_API_KEY` are not hardcoded and are injected at runtime via `${{ secrets.* }}`.
- **SQLite databases mounted read-only** (`ro` flag in Docker Compose volumes), preventing the backend container from modifying source data.
- **Backend health check** prevents the frontend from routing traffic to an unhealthy backend.
- **Tool system prompt hardening**: The agent system prompt explicitly instructs the LLM never to reveal internal instructions or tool names to users.
- **Assessment token caps**: `specialist_max_tokens: 1500` and `aggregator_max_tokens: 8000` cap runaway LLM output costs.

### Security Gaps and Concerns

- **⚠️ No authentication or authorisation on the `/chat` endpoint**: `CORSMiddleware` is configured with `allow_origins=["*"]`, `allow_methods=["*"]`, `allow_headers=["*"]`. Any client on any origin can call the backend with any session ID. There is no API key, JWT, OAuth, or session validation.
- **⚠️ No HTTPS/TLS configured**: All inter-service communication (frontend → backend, backend → Redis, backend → Postgres) runs over plaintext HTTP/TCP within Docker's network. There is no TLS termination configured anywhere in the IaC.
- **⚠️ Hardcoded PostgreSQL credentials**: `POSTGRES_USER: chainlit`, `POSTGRES_PASSWORD: chainlit`, `POSTGRES_DB: chainlit` are hardcoded in `docker-compose.yml` and the `DATABASE_URL` environment variable. These are weak, shared, default credentials.
- **⚠️ Redis has no authentication**: The Redis container (`redis-stack-server`) is started with no password, no ACL, and no TLS. Any process that can reach port 6379 can read or overwrite all conversation checkpoints.
- **⚠️ SQLite databases contain what appears to be real or realistic customer PII**: Files like `customer_profile.db` and `application_profile.db` are stored as plain files on the local filesystem with no encryption at rest.
- **⚠️ `customer_similarity_dict.json` stored in `backend/tmp/`**: Sensitive customer ID mappings committed directly to the repository.
- **⚠️ No secrets scanning or SAST in CI**: None of the five GitHub Actions workflows run tools like `gitleaks`, `trufflehog`, `bandit`, or `semgrep` to detect accidentally committed secrets or insecure code patterns.
- **⚠️ `GH_TOKEN` scope is unknown**: The token used to write to `ai-delivery-outputs` and post PR comments is not scoped — it may have broad repository or organisation-level write access. [TODO: confirm minimum required scopes and restrict accordingly]
- **⚠️ No network isolation between containers**: Docker Compose places all services on the default bridge network with no explicit network segmentation. Redis and Postgres are exposed to any container in the stack.
- **⚠️ Ports 6379 (Redis) and 5432 (Postgres) are published to the host**: Both are accessible from outside the Docker network, not just from peer containers.
- **⚠️ Prompt injection risk**: Customer profile data retrieved from databases is interpolated directly into LLM prompts without sanitisation.
- **⚠️ `BACKEND_URL` uses internal Docker hostname**: If deployed beyond single-host Docker Compose, this environment variable would need reconfiguration — no service mesh or discovery mechanism is present.

---

## 5. Environment Variables and Secrets

| Name | Required | Sensitivity | Where set |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | 🔴 High (API billing key) | GitHub Actions secret; `.env` file (backend container) |
| `GH_TOKEN` | Yes (CI/CD tools only) | 🔴 High (GitHub repo access) | GitHub Actions secret |
| `SENDGRID_API_KEY` | Yes (CI/CD tools only) | 🔴 High (email API key) | GitHub Actions secret |
| `GOOGLE_API_KEY` | Conditional (if Gemini used) | 🔴 High (GCP API billing key) | `.env` file (backend container) |
| `REDIS_HOST` | No (defaults to `localhost`) | 🟢 Low | Docker Compose `environment` block |
| `POSTGRES_USER` | Yes | 🟡 Medium | Hardcoded in `docker-compose.yml` |
| `POSTGRES_PASSWORD` | Yes | 🔴 High (DB credential) | Hardcoded in `docker-compose.yml` — **must be moved to secret** |
| `POSTGRES_DB` | Yes | 🟢 Low | Hardcoded in `docker-compose.yml` |
| `DATABASE_URL` | Yes (frontend) | 🔴 High (includes password) | Docker Compose `environment` block (plaintext) |
| `BACKEND_URL` | Yes (frontend) | 🟢 Low | Docker Compose `environment` block |
| `OUTPUT_REPO` | No (defaults to `ai-delivery-outputs`) | 🟢 Low | GitHub Actions `env` block |
| `OUTPUT_REPO_OWNER` | No (defaults to repo owner) | 🟢 Low | GitHub Actions `env` block |
| `NOTIFY_EMAIL` | No (defaults to `kylo.deng@capco.com`) | 🟡 Medium (PII) | GitHub Actions `env` block (hardcoded) |
| `SENDER_EMAIL` | No | 🟢 Low | GitHub Actions `env` block (hardcoded) |
| `REVIEW_MODE` | No (CI/CD, set at runtime) | 🟢 Low | Set dynamically in workflow step |
| `PR_NUMBER` | No (CI/CD, set at runtime) | 🟢 Low | Set dynamically in workflow step |
| `RELEASE_VERSION` | No (CI/CD, set at runtime) | 🟢 Low | Set dynamically in workflow step |
| `PROJECT_NAME` | No (CI/CD, set at runtime) | 🟢 Low | Set dynamically in workflow step |
| `TEST_MODE` | No (defaults to `generate`) | 🟢 Low | GitHub Actions `env` block |
| `UAT_MODE` | No (CI/CD, set at runtime) | 🟢 Low | Set dynamically in workflow step |

---

## 6. Dependencies

### External APIs and Services

| Dependency | Type | Used By | Notes |
|---|---|---|---|
| Anthropic Claude API (claude-sonnet-4-20250514, claude-haiku-4-5-20251001) | External LLM API | Backend assessment pipeline; CI/CD scripts | Primary LLM provider; billing risk if uncapped |
| Google Gemini API (gemini-3-flash-preview) | External LLM API | Backend LLMS module | Configured but [TODO: confirm whether actively used in production paths] |
| SendGrid API | External email service | All 5 CI/CD workflow scripts | Sends completion notification emails |
| GitHub API (api.github.com) | External REST API | All 5 CI/CD workflow scripts | Reads source repo files, writes to output repo, posts PR comments |

### Python Libraries (Backend)

- `langchain-core`, `langchain-anthropic`, `langchain-google-genai` — LLM abstraction layer
- `langgraph` — agent graph orchestration and checkpointing
- `fastapi`, `uvicorn` — API server
- `sse-starlette` — Server-Sent Events streaming
- `pydantic` — structured output validation
- `redis` (async) — LangGraph Redis checkpointer
- `python-dotenv` — local `.env` loading
- `pyyaml` — config file parsing
- `anthropic` (direct SDK) — used in CI/CD scripts

### Other Repositories

- `{github_owner}/ai-delivery-outputs` — **required sibling repository** where all CI/CD tool outputs (docs, test files, UAT packs) are written. Must exist and be accessible by `GH_TOKEN` before workflows run.

### Data Files (Local, not cloud-managed)

- `database/customer_profile.db` — customer demographics
- `database/feature_importance.db` — ML feature importance
- `database/model_predictions.db` — pre-computed risk predictions
- `database/application_profile.db` — insurance application records
- `backend/tmp/customer_similarity_dict.json` — pre-computed customer similarity index
- `backend/model_card.json` — CatBoostClassifier metadata
- `postgres/init.sql` — PostgreSQL schema initialisation script [TODO: confirm this file exists and is committed]

---

## 7. Deployment Instructions

### Prerequisites

- Docker and Docker Compose v2 installed
- A `.env` file in the repository root (and/or `backend/.env`) containing at minimum:

```bash
ANTHROPIC_API_KEY=sk-ant-...
GOOGLE_API_KEY=AIza...        # only if Gemini is used
```

- The four SQLite database files present under `./database/`:
  - `customer_profile.db`
  - `feature_importance.db`
  - `model_predictions.db`
  - `application_profile.db`

### Local Deployment

```bash
# 1. Clone the repository
git clone https://github.com/kylodeng/underwriting_chatbot-main.git
cd underwriting_chatbot-main

# 2. Create the .env file with required secrets
cat > .env <<EOF
ANTHROPIC_API_KEY=sk-ant-<your-key>
GOOGLE_API_KEY=<your-gcp-key>
EOF

# 3. Build and start all services
docker compose up --build

# 4. Verify backend health