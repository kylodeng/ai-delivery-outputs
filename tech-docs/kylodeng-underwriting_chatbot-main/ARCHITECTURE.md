# Architecture Document: kylodeng/underwriting_chatbot-main

---

## 1. Overview

The Underwriting Chatbot is an AI-assisted insurance underwriting platform that enables underwriters to assess customer risk profiles through a conversational chat interface. The system ingests structured customer data from multiple SQLite databases, runs parallel specialist LLM assessments across finance, health, life, and other underwriting domains, and aggregates results into a structured `UnderwritingReport` with a risk classification (Preferred → Substandard). A FastAPI backend streams responses via Server-Sent Events (SSE) to a frontend UI, with Redis providing LangGraph agent conversation state/checkpointing and PostgreSQL persisting Chainlit session data. The repository also includes five GitHub Actions-powered AI delivery tools (code review, tech docs, business docs, auto-testing, and UAT facilitation) that use Anthropic Claude to automate SDLC artefact generation.

---

## 2. Resources Deployed

| Resource | Type | Cloud Provider | Purpose |
|---|---|---|---|
| `backend` | Docker container (FastAPI + LangGraph) | Local / self-hosted (Docker Compose) | Serves `/chat` SSE endpoint; orchestrates LLM agent and underwriting assessment tools |
| `frontend` | Docker container | Local / self-hosted (Docker Compose) | Serves the chat UI (Chainlit-compatible) on port 8080 |
| `redis` (redis-stack-server:7.2.0-v14) | Docker container | Local / self-hosted (Docker Compose) | LangGraph `AsyncRedisSaver` checkpointer — stores agent conversation state per `thread_id` |
| `postgres` (postgres:16-alpine) | Docker container | Local / self-hosted (Docker Compose) | Chainlit session/user data persistence |
| `customer_profile.db` | SQLite file (read-only mount) | Local filesystem | Customer demographic and profile data |
| `feature_importance.db` | SQLite file (read-only mount) | Local filesystem | CatBoost model feature importance scores |
| `model_predictions.db` | SQLite file (read-only mount) | Local filesystem | Pre-computed ML model risk predictions |
| `application_profile.db` | SQLite file (read-only mount) | Local filesystem | Insurance application data |
| `postgres_data` | Docker named volume | Local / self-hosted | PostgreSQL data persistence across restarts |
| Anthropic Claude (claude-sonnet-4-20250514) | External API | Anthropic (cloud) | Full-depth underwriting assessment aggregation |
| Anthropic Claude Haiku (claude-haiku-4-5-20251001) | External API | Anthropic (cloud) | Fast agent routing and specialist assessments |
| Google Gemini (gemini-3-flash-preview) | External API | Google Cloud (cloud) | Alternative LLM provider (configured but marked `azure`/`openai` stubs as `None`) |
| CatBoostClassifier model | Static artefact (model_card.json) | Local filesystem | Pre-trained risk classification model metadata |
| GitHub Actions runners (ubuntu-latest) | CI/CD compute | GitHub (cloud) | AI delivery tools (Tools 1–5) |
| `ai-delivery-outputs` (external repo) | GitHub repository | GitHub (cloud) | Output destination for generated docs, test files, UAT packs |
| SendGrid | Email API | Twilio/SendGrid (cloud) | Notification emails from AI delivery tools |

---

## 3. Data Flow

### Chat / Underwriting Assessment Flow

1. **User sends message** via the frontend (port 8080). The frontend forwards an HTTP POST to `backend:8000/chat` with `{message, session_id, model, temperature, mode}`.
2. **FastAPI `/chat` handler** calls `build_agent()`, which instantiates a LangGraph agent with the chosen LLM and tools (`get_customer_profile`, `run_underwriting_assessment`, `customer_lookalike`), wired to an `AsyncRedisSaver` checkpointer using the `session_id` as `thread_id`.
3. **Agent streams events** via `astream_events()`. The LLM decides which tool to call based on the user's question and the system prompt.
4. **`get_customer_profile` tool** queries `customer_profile.db` (SQLite, read-only mount at `/data/`) to retrieve structured customer data including demographics, financial, and KYC fields.
5. **`customer_lookalike` tool** queries `customer_similarity_dict.json` (pre-computed similarity index) to return similar customer IDs from the 10,000-customer dataset.
6. **`run_underwriting_assessment` tool** receives the customer profile string and fans out **parallel async specialist LLM calls** (up to 4 concurrent via `asyncio.Semaphore(4)`) — one per assessment category (finance, health, life, etc.) — each using `claude-haiku-4-5-20251001` with prompts from `assessment_criterias.json`.
7. **Specialist responses are aggregated** by a second LLM call (`claude-sonnet-4-20250514`) using `with_structured_output(UnderwritingReport)`, producing a validated Pydantic `UnderwritingReport` JSON.
8. **Assessment result is rendered** via `render_report()` into a human-readable format and returned to the agent as a tool response.
9. **Agent composes a final answer** and streams it back to the FastAPI endpoint as SSE events (`tool_start`, `tool_end`, `response`, chart data).
10. **Frontend receives SSE stream** and renders thinking steps, tool activity, and the final underwriting report in real time.
11. **Conversation state** (messages, tool calls, history) is checkpointed to Redis after each agent step, enabling multi-turn conversations.
12. **Charts/visualisations** (feature importance, etc.) are buffered server-side and flushed after the main response text to avoid interleaving.

### AI Delivery Tools Flow (GitHub Actions)

1. A trigger event (PR open, push to main, version tag, schedule, or `workflow_dispatch`) fires a GitHub Actions workflow.
2. The workflow checks out the source repo and installs `anthropic` and `requests`.
3. `shared.py` fetches repo files or PR diffs from the GitHub API using `GH_TOKEN`.
4. Files/diffs are sent to Anthropic Claude (`claude-sonnet-4-6`) via the Anthropic API.
5. Claude's response (review JSON, markdown docs, test files, UAT packs) is written to the `ai-delivery-outputs` GitHub repo via the GitHub Contents API.
6. A SendGrid email notification is dispatched to `kylo.deng@capco.com`.
7. Artefacts (JSON review files) are uploaded as GitHub Actions workflow artefacts.

---

## 4. Security Posture

### What Is Secured

- **Secrets management**: API keys (`ANTHROPIC_API_KEY`, `GH_TOKEN`, `SENDGRID_API_KEY`, `GOOGLE_API_KEY`) are stored as GitHub Actions secrets and injected via environment variables — not hardcoded in source.
- **Read-only database mounts**: All four SQLite databases are mounted with `:ro` (read-only) in Docker Compose, preventing write access from the backend container.
- **System prompt protection**: The agent system prompt explicitly instructs the LLM never to reveal internal instructions or tool definitions to users.
- **Tool concurrency control**: `asyncio.Semaphore(4)` limits parallel LLM calls, providing basic rate-limiting protection against runaway API spend.
- **Structured output validation**: `UnderwritingReport` is a Pydantic model with `Literal` constraints on risk class, preventing LLM hallucination of invalid values from entering the report.

### Security Gaps — Explicit Call-outs

- **⚠️ CORS is completely open**: `allow_origins=["*"]`, `allow_methods=["*"]`, `allow_headers=["*"]` on the FastAPI backend. Any origin can POST to `/chat`. This is a critical gap for any non-local deployment.
- **⚠️ No authentication or authorisation on `/chat`**: The API endpoint has no API keys, JWT tokens, OAuth, or session validation. Any client with network access can query the underwriting system and retrieve customer PII.
- **⚠️ PostgreSQL uses hardcoded default credentials**: `POSTGRES_USER: chainlit`, `POSTGRES_PASSWORD: chainlit` are hardcoded in `docker-compose.yml`. These are not pulled from secrets.
- **⚠️ Redis has no authentication**: The Redis container exposes port 6379 with no password (`requirepass` not configured). Agent conversation history (potentially containing PII) is stored unprotected.
- **⚠️ No encryption at rest**: SQLite databases containing customer PII (profile, application, predictions) are plain unencrypted files on the host filesystem. No volume encryption is configured.
- **⚠️ No encryption in transit between containers**: Internal Docker network communication (backend↔redis, backend↔postgres, frontend↔backend) uses plain HTTP/TCP — no TLS.
- **⚠️ Customer PII sent to external LLM APIs**: Customer profile strings (age, income, medical conditions, nationality, smoker status, etc.) are sent verbatim to Anthropic and Google APIs. There is no data masking or anonymisation layer. [TODO: Confirm data processing agreements with Anthropic and Google for insurance PII/PHI]
- **⚠️ `customer_similarity_dict.json` stored in `backend/tmp/`**: This file maps customer IDs to similar customers and appears to be committed to the repository — potentially exposing customer ID patterns.
- **⚠️ No input sanitisation on `profile` parameter**: The `run_underwriting_assessment` tool accepts a free-text `profile` string from the agent with no sanitisation before embedding in LLM prompts (prompt injection risk).
- **⚠️ `GH_TOKEN` scope unknown**: The GitHub token used by AI delivery tools has unspecified permissions. If it has write access to all repos, a compromised workflow could exfiltrate or modify code. [TODO: Restrict GH_TOKEN to minimum required scopes — contents:write on `ai-delivery-outputs` only]
- **⚠️ No secrets scanning**: No `git-secrets`, `trufflehog`, or similar tool is configured in the CI pipeline.
- **⚠️ `_charts_sent` is a global in-process set**: This session tracking mechanism will not work correctly under multiple backend replicas and leaks session metadata in memory.

---

## 5. Environment Variables and Secrets

| Name | Required | Sensitivity | Where set |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | **Critical** — paid API key | GitHub Actions secret; `.env` file for backend |
| `GH_TOKEN` | Yes | **High** — GitHub PAT with repo write access | GitHub Actions secret |
| `SENDGRID_API_KEY` | Yes | **High** — email sending capability | GitHub Actions secret |
| `GOOGLE_API_KEY` | Yes (if Gemini used) | **High** — paid API key | `.env` file for backend |
| `REDIS_HOST` | No | Low | Docker Compose `environment` block (defaults to `localhost`) |
| `DATABASE_URL` | Yes (frontend) | **Medium** — contains DB credentials | Docker Compose `environment` block (plaintext) |
| `BACKEND_URL` | Yes (frontend) | Low | Docker Compose `environment` block |
| `POSTGRES_USER` | Yes | **Medium** | Docker Compose `environment` block (hardcoded: `chainlit`) |
| `POSTGRES_PASSWORD` | Yes | **High** | Docker Compose `environment` block (**hardcoded**: `chainlit`) |
| `POSTGRES_DB` | Yes | Low | Docker Compose `environment` block (hardcoded: `chainlit`) |
| `OUTPUT_REPO` | No | Low | GitHub Actions `env` block (default: `ai-delivery-outputs`) |
| `OUTPUT_REPO_OWNER` | No | Low | GitHub Actions `env` block (derived from `github.repository_owner`) |
| `NOTIFY_EMAIL` | No | Low | GitHub Actions `env` block (hardcoded: `kylo.deng@capco.com`) |
| `SENDER_EMAIL` | No | Low | GitHub Actions `env` block (hardcoded: `noreply@ai-delivery.capco.com`) |
| `GITHUB_RUN_URL` | No | Low | GitHub Actions `env` block (auto-constructed) |
| `FORCE_JAVASCRIPT_ACTIONS_TO_NODE24` | No | None | GitHub Actions `env` block |

> [TODO: `GOOGLE_API_KEY` is referenced in `LLMS.py` but not documented in docker-compose.yml or any `.env.example` — confirm whether it must be present even when `gemini` model is not selected (it is instantiated eagerly in the `LLMS` class constructor, so it will fail at startup if not set)]

---

## 6. Dependencies

| Dependency | Type | Purpose | Version/Notes |
|---|---|---|---|
| Anthropic API | External cloud API | LLM inference (claude-sonnet-4, claude-haiku-4-5) | Models: `claude-sonnet-4-20250514`, `claude-haiku-4-5-20251001` |
| Google Gemini API | External cloud API | Alternative LLM provider | Model: `gemini-3-flash-preview` (configured but usage unclear) |
| SendGrid API | External cloud API | Email notification delivery from CI tools | Used in all 5 GitHub Actions tools |
| GitHub API (api.github.com) | External cloud API | Reading repo files, PR diffs, writing output files, posting PR comments | Used by all 5 GitHub Actions tools via `GH_TOKEN` |
| `ai-delivery-outputs` (separate GitHub repo) | External GitHub repo | Destination for all AI-generated artefacts (docs, tests, UAT packs) | Must exist under same owner as source repo |
| `langchain` / `langchain-core` | Python library | Agent framework, tool wrappers, message types | [TODO: pin exact version] |
| `langgraph` | Python library | Stateful agent graph with checkpointing | Uses `AsyncRedisSaver` |
| `langchain-anthropic` | Python library | Anthropic LLM integration | [TODO: pin exact version] |
| `langchain-google-genai` | Python library | Google Gemini LLM integration | [TODO: pin exact version] |
| `fastapi` | Python library | HTTP API server | [TODO: pin exact version] |
| `sse-starlette` | Python library | Server-Sent Events streaming | [TODO: pin exact version] |
| `pydantic` | Python library | Data validation for `UnderwritingReport` | [TODO: pin exact version] |
| `redis-stack-server:7.2.0-v14` | Docker image | Redis with vector search (stack image used but vector search not evidenced) | Pinned version |
| `postgres:16-alpine` | Docker image | PostgreSQL database | Pinned major version |
| `anthropic` (pip) | Python library | Direct Anthropic API client for GitHub Actions scripts | Used in shared.py |
| `catboost` (inferred) | ML library | Risk classification model (CatBoostClassifier referenced in model_card.json) | [TODO: confirm if model is loaded at runtime or predictions are pre-computed in SQLite] |
| `./postgres/init.sql` | Local file | PostgreSQL schema initialisation | [TODO: file not provided — confirm schema] |

---

## 7. Deployment Instructions

### Prerequisites

- Docker and Docker Compose v2+ installed
- A `.env` file in the project root (or `backend/` directory) with required secrets

### Step 1: Create the `.env` file

```bash
cat > .env << 'EOF'
ANTHROPIC_API_KEY=sk-ant-...
GOOGLE_API_KEY=AIza...
# Add any other required variables
EOF
```

### Step 2: Build and start all services

```bash
docker compose up --build -d
```

### Step 3: Verify services are healthy

```bash
docker compose ps
# backend should show status: healthy
curl http://localhost:8000/health
# Expected: {"status": "ok"}
```

### Step 4: Access the frontend

```
http://localhost:8080
```

### Step 5: View logs

```bash
docker compose logs -f backend
docker compose logs -f frontend
```

### Step 6: Stop all services

```bash
docker compose down
```

### Step 7: Stop and remove volumes (full reset)

```bash
docker compose down -v
```

### Triggering AI Delivery Tools (GitHub Actions)

```bash
# Tool 1 — Code Review (