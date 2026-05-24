# Architecture Document: kylodeng/underwriting_chatbot-main

---

## 1. Overview

The Underwriting Chatbot is an AI-assisted insurance underwriting platform that enables underwriters to assess customer risk profiles through a conversational interface. A FastAPI backend orchestrates a multi-agent LangGraph pipeline that fans out parallel specialist assessments (finance, health, life, KYC, etc.) using Anthropic Claude LLMs, then aggregates results into a structured `UnderwritingReport`. The frontend (Chainlit-based) communicates with the backend over Server-Sent Events (SSE) for streaming responses. Supporting data is served from four read-only SQLite databases (customer profiles, ML model predictions, feature importance, application profiles), a CatBoost ML model provides pre-computed risk classifications, Redis provides LangGraph conversation-state checkpointing, and PostgreSQL persists Chainlit session/chat history. A suite of five GitHub Actions CI/CD workflows use Claude to automate code review, technical documentation, business documentation, test generation, and UAT facilitation.

---

## 2. Resources Deployed

| Resource | Type | Cloud Provider | Purpose |
|---|---|---|---|
| `backend` | Docker container (FastAPI, Python) | Local / self-hosted (docker-compose) | REST + SSE API; hosts LangGraph agent, assessment pipeline |
| `frontend` | Docker container (Chainlit) | Local / self-hosted (docker-compose) | Chat UI; streams responses from backend |
| `redis` | Docker container (redis-stack-server 7.2.0) | Local / self-hosted (docker-compose) | LangGraph conversation-state checkpointing (AsyncRedisSaver) |
| `postgres` | Docker container (PostgreSQL 16 Alpine) | Local / self-hosted (docker-compose) | Chainlit session, user, and chat history persistence |
| `customer_profile.db` | SQLite file (read-only volume mount) | Local / self-hosted | Customer demographic and profile data |
| `feature_importance.db` | SQLite file (read-only volume mount) | Local / self-hosted | CatBoost model feature importance scores |
| `model_predictions.db` | SQLite file (read-only volume mount) | Local / self-hosted | Pre-computed CatBoost risk classification predictions |
| `application_profile.db` | SQLite file (read-only volume mount) | Local / self-hosted | Insurance application metadata |
| `customer_similarity_dict.json` | JSON file (backend tmp) | Local / self-hosted | Pre-computed customer lookalike similarity index |
| Anthropic Claude API (claude-sonnet-4-20250514) | External SaaS LLM | Anthropic | Deep/full assessment aggregator; CI/CD tooling |
| Anthropic Claude API (claude-haiku-4-5-20251001) | External SaaS LLM | Anthropic | Fast specialist assessments; default agent model |
| Google Gemini API (gemini-3-flash-preview) | External SaaS LLM | Google Cloud | Optional alternative LLM provider |
| GitHub Actions runners | CI/CD (ubuntu-latest) | GitHub | Runs five AI delivery automation workflows |
| `ai-delivery-outputs` (GitHub repo) | External GitHub repository | GitHub | Stores generated code review reports, docs, test files, UAT packs |
| SendGrid API | External SaaS email | Twilio/SendGrid | Sends notification emails from CI/CD workflows |
| CatBoost model | ML model (pre-trained, v1.0) | Local / self-hosted | Risk classification (Preferred / Standard / Substandard etc.) |

---

## 3. Data Flow

### Runtime (Chat / Assessment)

1. **User → Frontend (Chainlit):** Underwriter types a question or request (e.g., "Assess customer CUST00000001") into the Chainlit chat UI on port 8080.
2. **Frontend → Backend (`POST /chat`):** Chainlit sends a JSON `ChatRequest` (message, session_id, model, mode, temperature) to the FastAPI backend on port 8000.
3. **Backend → LangGraph Agent:** `build_agent()` constructs a LangGraph graph with Redis checkpointing. The agent LLM (Claude Haiku by default, tagged `"agent"`) receives the system prompt (injected with model card data and skill docs) and the user message.
4. **Agent → Tool: `get_customer_profile`:** Agent invokes the profile tool, which queries `customer_profile.db` (SQLite, read-only volume mount) and returns structured customer metadata.
5. **Agent → Tool: `customer_lookalike`:** (Optional) Agent queries `customer_similarity_dict.json` to find similar historical customers.
6. **Agent → Tool: `run_underwriting_assessment`:** Agent passes the customer profile string to the assessment pipeline.
7. **Assessment Pipeline → Specialist LLMs (parallel):** Up to 4 concurrent async tasks (semaphore-limited) call Claude Haiku (tagged `"thinking"`) for each `ASSESSMENT_CATEGORIES` domain (finance, health, life, KYC, etc.), each using a specific prompt from `assessment_criterias.json`.
8. **Specialist LLMs → Aggregator LLM:** Raw specialist outputs are collected and passed to Claude Sonnet (aggregator) with `structured_output(UnderwritingReport)`, which synthesises them into a validated Pydantic `UnderwritingReport` JSON object.
9. **Assessment Tool → Agent:** The rendered report string is returned to the agent as a `ToolMessage`.
10. **Agent → Backend (SSE stream):** The backend streams events (`tool_start`, `tool_end`, `response`, `chart`, `done`) back to the frontend via `EventSourceResponse` (SSE).
11. **Backend → Redis:** LangGraph persists conversation thread state (keyed by `session_id`) to Redis after each turn for multi-turn memory.
12. **Frontend → PostgreSQL:** Chainlit writes session/chat history to PostgreSQL (via `DATABASE_URL`).
13. **Backend → Frontend:** Final `done` SSE event signals end of response; frontend renders the streamed markdown and any buffered charts.

### CI/CD (GitHub Actions Workflows)

14. **Trigger (PR / push / schedule / tag):** One of the five workflows fires (code review, tech docs, business docs, auto-testing, UAT).
15. **Workflow → Source repo:** Checks out source code; fetches files or PR diffs via GitHub REST API.
16. **Workflow → Anthropic Claude API:** `shared.py` calls `claude-sonnet-4-6` with a structured system prompt and file content.
17. **Claude → Workflow:** Returns generated review JSON, markdown docs, test files, or UAT pack.
18. **Workflow → `ai-delivery-outputs` repo:** Pushes generated artefacts (via GitHub API `PUT /contents`) to the output repository.
19. **Workflow → SendGrid:** Sends notification email (with HTML summary and output link) to `kylo.deng@capco.com`.
20. **Workflow → PR comment:** For code review mode, posts a formatted markdown comment directly on the pull request.

---

## 4. Security Posture

### ✅ What Is Secured

- **SQLite databases are mounted read-only** (`ro` flag in docker-compose volumes), preventing runtime writes to source data.
- **API keys are stored as GitHub Actions secrets** (`ANTHROPIC_API_KEY`, `GH_TOKEN`, `SENDGRID_API_KEY`) and not hardcoded in workflow YAML.
- **System prompts instruct the agent not to reveal internal instructions or tooling** to end users.
- **Redis is not exposed externally** — only accessible within the Docker bridge network (no external port binding needed beyond `6379` which *is* exposed — see gaps).
- **Specialist LLM outputs are token-capped** (`specialist_max_tokens: 1500`) to limit runaway LLM cost and prevent prompt injection amplification.
- **Pydantic models enforce structured output** from the aggregator LLM, reducing injection into downstream data.

### ❌ Gaps and Missing Controls

- **⚠️ CORS is fully open:** `allow_origins=["*"]`, `allow_methods=["*"]`, `allow_headers=["*"]` — any origin can call the backend API. This is a critical gap if the backend is ever exposed beyond localhost.
- **⚠️ No authentication or authorisation on `/chat` or `/health`:** Any client with network access can submit chat requests. There is no API key, JWT, OAuth, or session token on backend endpoints.
- **⚠️ PostgreSQL credentials are hardcoded in `docker-compose.yml`** (`POSTGRES_USER: chainlit`, `POSTGRES_PASSWORD: chainlit`). These are default/weak credentials committed to source.
- **⚠️ Redis has no authentication configured** — the Redis container is started with no password. Port 6379 is also published to the host (`0.0.0.0:6379`), making it accessible from outside the Docker network.
- **⚠️ No encryption at rest** for SQLite databases, Redis, or PostgreSQL. Sensitive insurance customer PII (age, income, medical conditions, nationality) in `customer_profile.db` is unencrypted on disk.
- **⚠️ No TLS/HTTPS** configured for backend (port 8000) or frontend (port 8080). All data including LLM responses and customer profiles transit in plaintext within the Docker network.
- **⚠️ `customer_similarity_dict.json` is stored in `backend/tmp/`** — a temporary directory with no access controls, containing customer ID relationships.
- **⚠️ `GH_TOKEN` scope is unknown** — if this token has broad repo write permissions it could be used to push to any repository the owner controls. [TODO: confirm GH_TOKEN is scoped to minimum required permissions (contents:write on ai-delivery-outputs only)]
- **⚠️ No input sanitisation** on the `message` field in `ChatRequest` before it is passed to LLMs — prompt injection risk.
- **⚠️ `.env` file is loaded at runtime** by `dotenv` — if this file contains production secrets and is committed to source, it represents a secret exposure risk. [TODO: confirm `.env` is in `.gitignore`]
- **⚠️ No rate limiting** on the `/chat` endpoint — susceptible to abuse/cost amplification via LLM API calls.
- **⚠️ `model_card.json` and assessment criteria prompts are committed to the repository** — internal risk model logic and prompt engineering are publicly visible.
- **⚠️ No audit logging** of which underwriter accessed which customer profile at runtime (only CI/CD audit entries exist in `shared.py`).

---

## 5. Environment Variables and Secrets

| Name | Required | Sensitivity | Where Set |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | **High** — LLM API key; incurs cost | GitHub Actions secret; `.env` file (backend) |
| `GOOGLE_API_KEY` | Yes (if Gemini used) | **High** — GCP API key | `.env` file (backend) |
| `GH_TOKEN` | Yes (CI/CD) | **High** — GitHub PAT with repo access | GitHub Actions secret |
| `SENDGRID_API_KEY` | Yes (CI/CD) | **High** — email sending API key | GitHub Actions secret |
| `REDIS_HOST` | No | Low | `docker-compose.yml` environment; defaults to `localhost` |
| `POSTGRES_USER` | Yes | **Medium** — DB credential | `docker-compose.yml` (hardcoded: `chainlit`) ⚠️ |
| `POSTGRES_PASSWORD` | Yes | **High** — DB credential | `docker-compose.yml` (hardcoded: `chainlit`) ⚠️ |
| `POSTGRES_DB` | Yes | Low | `docker-compose.yml` (hardcoded: `chainlit`) |
| `DATABASE_URL` | Yes (frontend) | **Medium** — includes DB password | `docker-compose.yml` environment |
| `BACKEND_URL` | Yes (frontend) | Low | `docker-compose.yml` environment |
| `OUTPUT_REPO` | No | Low | GitHub Actions env; defaults to `ai-delivery-outputs` |
| `OUTPUT_REPO_OWNER` | No | Low | GitHub Actions env; derived from `github.repository_owner` |
| `NOTIFY_EMAIL` | No | Low | GitHub Actions env (hardcoded: `kylo.deng@capco.com`) |
| `SENDER_EMAIL` | No | Low | GitHub Actions env (hardcoded: `noreply@ai-delivery.capco.com`) |
| `REVIEW_MODE` | No | Low | Set dynamically in workflow step |
| `PR_NUMBER` | No | Low | Set dynamically in workflow step |
| `RELEASE_VERSION` | No | Low | Set dynamically in workflow step |
| `PROJECT_NAME` | No | Low | Set dynamically in workflow step |
| `UAT_MODE` | No | Low | Set dynamically in workflow step |
| `TEST_MODE` | No | Low | Set dynamically in workflow step |

---

## 6. Dependencies

| Dependency | Type | Purpose | Version/Notes |
|---|---|---|---|
| Anthropic Claude API | External SaaS | LLM for assessments and CI/CD tools | `claude-sonnet-4-20250514`, `claude-haiku-4-5-20251001`, `claude-sonnet-4-6` (shared.py) |
| Google Gemini API | External SaaS | Optional alternative LLM | `gemini-3-flash-preview` |
| SendGrid API | External SaaS | Email notifications from CI/CD | v3 REST API |
| GitHub REST API | External SaaS | Read source files, write output repo, post PR comments | `api.github.com` v2022-11-28 |
| `ai-delivery-outputs` (GitHub repo) | External repo (same owner) | Storage for all generated artefacts | Must exist and be writable by `GH_TOKEN` |
| LangChain / LangGraph | Python library | Agent orchestration, tool calling, graph state | `langchain-core`, `langgraph` |
| `langchain-anthropic` | Python library | Claude LLM wrapper | — |
| `langchain-google-genai` | Python library | Gemini LLM wrapper | — |
| FastAPI | Python library | Backend REST + SSE server | — |
| `sse-starlette` | Python library | SSE streaming for `/chat` | — |
| Chainlit | Python library / framework | Chat UI frontend | — |
| `redis-stack-server` | Docker image | Redis with JSON/Search modules for LangGraph checkpointing | `7.2.0-v14` |
| PostgreSQL | Docker image | Chainlit session storage | `16-alpine` |
| CatBoost | ML framework | Pre-trained risk classification model | v1.0, `model_predictions.db` |
| `asyncpg` | Python library | Async PostgreSQL driver for Chainlit | — |
| `pydantic` | Python library | Structured LLM output validation | — |
| `python-dotenv` | Python library | `.env` file loading | — |
| `anthropic` (SDK) | Python library | Direct Anthropic API calls in CI/CD scripts | — |
| `requests` | Python library | GitHub and SendGrid HTTP calls in CI/CD scripts | — |

---

## 7. Deployment Instructions

### Prerequisites
- Docker and Docker Compose v2 installed
- An `.env` file in the project root (and/or `backend/.env`) with required secrets (see section 5)
- The four SQLite database files present under `./database/`

### Local Deployment (docker-compose)

```bash
# 1. Clone the repository
git clone https://github.com/kylodeng/underwriting_chatbot-main.git
cd underwriting_chatbot-main

# 2. Create the root .env file with required secrets
cat > .env << EOF
ANTHROPIC_API_KEY=<your-anthropic-api-key>
GOOGLE_API_KEY=<your-google-api-key>
EOF

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

# To also remove persisted PostgreSQL data volume:
docker compose down -v
```

### Rebuilding After Code Changes

```bash
docker compose up --build --force-recreate
```

### GitHub Actions CI/CD Setup

```bash
# In the GitHub repository settings, add these secrets:
# Settings