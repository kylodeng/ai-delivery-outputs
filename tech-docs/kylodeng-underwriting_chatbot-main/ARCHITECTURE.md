# Architecture Document: kylodeng/underwriting_chatbot-main

---

## 1. Overview

The Underwriting Chatbot is an AI-assisted insurance underwriting platform that enables underwriters to assess customer risk profiles through a conversational interface. A FastAPI backend orchestrates a multi-agent LangGraph pipeline that calls specialist LLM agents (Anthropic Claude, Google Gemini) to evaluate customers across domains such as finance, health, and life risk. Customer data is retrieved from SQLite databases, session memory is persisted in Redis, and conversation history is stored in PostgreSQL. A frontend (Chainlit-based) communicates with the backend via Server-Sent Events (SSE) for streaming responses. The repository also includes five GitHub Actions CI/CD automation tools powered by Claude that provide AI-driven code review, technical documentation, business documentation, test generation, and UAT facilitation.

---

## 2. Resources Deployed

| Resource | Type | Cloud Provider | Purpose |
|---|---|---|---|
| `backend` | Docker container (FastAPI, Python) | Local / self-hosted | REST API + LLM agent orchestration |
| `frontend` | Docker container (Chainlit) | Local / self-hosted | Underwriter chat UI |
| `redis` | Docker container (redis-stack-server 7.2.0-v14) | Local / self-hosted | LangGraph agent checkpoint / session memory |
| `postgres` | Docker container (postgres:16-alpine) | Local / self-hosted | Chainlit conversation history |
| `customer_profile.db` | SQLite file (read-only volume mount) | Local / self-hosted | Customer demographic & profile data |
| `feature_importance.db` | SQLite file (read-only volume mount) | Local / self-hosted | ML model feature importance scores |
| `model_predictions.db` | SQLite file (read-only volume mount) | Local / self-hosted | CatBoost model pre-computed predictions |
| `application_profile.db` | SQLite file (read-only volume mount) | Local / self-hosted | Insurance application data |
| `postgres_data` | Docker named volume | Local / self-hosted | PostgreSQL persistent data storage |
| Anthropic Claude API | External SaaS API | Anthropic | LLM inference (specialist agents, aggregator, CI tools) |
| Google Gemini API | External SaaS API | Google Cloud | Alternative LLM inference |
| GitHub Actions (tool1–5) | CI/CD workflows | GitHub | AI-driven code review, docs, testing, UAT |
| SendGrid API | External SaaS API | Twilio/SendGrid | Email notifications from CI/CD workflows |
| `ai-delivery-outputs` repo | GitHub repository | GitHub | Output artefact storage for CI/CD tool results |

---

## 3. Data Flow

1. **User sends a message** via the Chainlit frontend (HTTP POST to `http://backend:8000/chat`), including `session_id`, `model`, `mode`, and `temperature`.

2. **Backend receives the `ChatRequest`** in `main.py` and calls `build_agent()` which instantiates a LangGraph agent with a Redis-backed `AsyncRedisSaver` checkpointer, loading previous conversation state for the `session_id`.

3. **Agent LLM (Claude Haiku by default)** receives the user message plus conversation history and system prompt. It decides which tool to invoke and returns a JSON tool-call directive.

4. **Tool: `get_customer_profile`** queries the SQLite `customer_profile.db` (mounted read-only) to retrieve structured customer data and returns it to the agent.

5. **Tool: `customer_lookalike`** reads `backend/tmp/customer_similarity_dict.json` to find similar customers by ID for comparative analysis.

6. **Tool: `run_underwriting_assessment`** receives the customer profile string and fans out **parallel async calls** (max concurrency = 4 via `asyncio.Semaphore`) to specialist LLM agents — one per assessment category (finance, health, life, etc.) — each using prompts loaded from `assessment_criterias.json`.

7. **Specialist LLMs** (Claude Haiku, capped at 1,500 output tokens) return their domain assessments. Results are collected and passed to the **aggregator LLM** (Claude Sonnet, up to 8,000 tokens) which uses `structured_output` to produce a validated `UnderwritingReport` Pydantic object.

8. **Report is rendered** via `render_report` and returned as the tool output string back to the agent.

9. **Agent streams the final answer** back to `main.py`, which translates LangGraph `astream_events` into **Server-Sent Events (SSE)** (`tool_start`, `tool_end`, `response`, `chart` event types) consumed by the frontend.

10. **Charts** (feature importance, model predictions) are assembled from the SQLite databases and buffered, then emitted after the text response stream completes.

11. **Session state** is checkpointed to Redis after each turn, enabling multi-turn conversation continuity.

12. **Conversation history** is persisted to PostgreSQL (Chainlit-managed schema, initialised via `postgres/init.sql`).

13. **CI/CD workflows** (GitHub Actions tools 1–5): On PR/push/tag/schedule triggers, Python scripts call the Anthropic Claude API directly (via `shared.py`), write output artefacts to the `ai-delivery-outputs` GitHub repo, post PR comments, and send email notifications via SendGrid.

---

## 4. Security Posture

### ✅ Secured

- **SQLite databases are mounted read-only** in Docker (`ro` flag), preventing backend writes to source data.
- **Secrets managed via GitHub Actions Secrets** (`ANTHROPIC_API_KEY`, `GH_TOKEN`, `SENDGRID_API_KEY`) — not hardcoded in workflow YAML.
- **Application secrets via `.env` file** (`env_file: .env` in Docker Compose) — not in source control (assuming `.env` is gitignored).
- **LLM prompt injection guardrail**: System prompts explicitly instruct the agent not to reveal internal instructions or tool configurations.
- **Specialist LLM output capped** at 1,500 tokens to prevent runaway token consumption.
- **Agent tool-call deduplication** via history inspection to prevent repeated calls.

### ❌ Not Secured / Gaps

- **⚠️ CORS is fully open**: `allow_origins=["*"]`, `allow_methods=["*"]`, `allow_headers=["*"]` in `main.py`. Any origin can call the backend API. This is a significant security gap for a system handling insurance underwriting (PII-sensitive) data.
- **⚠️ No authentication or authorisation** on the `/chat` endpoint. Any client that can reach port 8000 can query customer data and run assessments.
- **⚠️ No HTTPS/TLS configured** in Docker Compose. All traffic between frontend, backend, Redis, and PostgreSQL is unencrypted in transit.
- **⚠️ PostgreSQL credentials are hardcoded** in `docker-compose.yml` (`POSTGRES_USER: chainlit`, `POSTGRES_PASSWORD: chainlit`). These are weak, default credentials.
- **⚠️ Redis has no authentication configured** — no `requirepass` or ACL rules. Any process on the Docker network can read/write agent session checkpoints.
- **⚠️ Encryption at rest: not configured** for Redis, PostgreSQL, or SQLite databases. Customer PII and underwriting assessments are stored unencrypted.
- **⚠️ `customer_similarity_dict.json` is stored in `backend/tmp/`** — committed to the repository. This file contains relationships between ~10,000 customer IDs, which may constitute sensitive data.
- **⚠️ `GH_TOKEN` scope is unknown** — [TODO: confirm this is a fine-grained PAT scoped to minimum required permissions, not a classic token with broad repo/org access].
- **⚠️ No input sanitisation** visible on the `message` field in `ChatRequest` before it is passed to LLM agents.
- **⚠️ No rate limiting** on the `/chat` API endpoint — susceptible to abuse and excessive LLM API cost.
- **⚠️ `_charts_sent` is a module-level in-memory set** — shared across all sessions in the same process, which could cause cross-session data leakage in a multi-user deployment.
- **⚠️ `model_card.json` and `assessment_criterias.json` committed to repository** — these reveal internal model architecture, feature weights, and assessment logic.

---

## 5. Environment Variables and Secrets

| Name | Required | Sensitivity | Where Set |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | 🔴 Secret | `.env` file (backend); GitHub Actions Secret |
| `GOOGLE_API_KEY` | Yes (if using Gemini) | 🔴 Secret | `.env` file (backend) |
| `GH_TOKEN` | Yes (CI tools) | 🔴 Secret | GitHub Actions Secret |
| `SENDGRID_API_KEY` | Yes (CI tools) | 🔴 Secret | GitHub Actions Secret |
| `REDIS_HOST` | Yes | 🟡 Internal | `docker-compose.yml` environment / `.env` |
| `DATABASE_URL` | Yes | 🔴 Secret | `docker-compose.yml` frontend environment (plaintext) |
| `POSTGRES_USER` | Yes | 🟡 Internal | `docker-compose.yml` (hardcoded: `chainlit`) |
| `POSTGRES_PASSWORD` | Yes | 🔴 Secret | `docker-compose.yml` (hardcoded: `chainlit`) ⚠️ |
| `POSTGRES_DB` | Yes | 🟢 Low | `docker-compose.yml` (hardcoded: `chainlit`) |
| `BACKEND_URL` | Yes | 🟢 Low | `docker-compose.yml` frontend environment |
| `OUTPUT_REPO` | No | 🟢 Low | GitHub Actions workflow env (default: `ai-delivery-outputs`) |
| `OUTPUT_REPO_OWNER` | No | 🟢 Low | GitHub Actions workflow env |
| `NOTIFY_EMAIL` | No | 🟡 Internal | GitHub Actions workflow env (hardcoded: `kylo.deng@capco.com`) |
| `SENDER_EMAIL` | No | 🟡 Internal | GitHub Actions workflow env (hardcoded: `noreply@ai-delivery.capco.com`) |
| `REVIEW_MODE` | No | 🟢 Low | GitHub Actions (runtime, set by workflow step) |
| `PR_NUMBER` | No | 🟢 Low | GitHub Actions (runtime, set by workflow step) |
| `RELEASE_VERSION` | No | 🟢 Low | GitHub Actions (runtime, set by workflow step) |
| `PROJECT_NAME` | No | 🟢 Low | GitHub Actions (runtime, set by workflow step) |
| `TEST_MODE` | No | 🟢 Low | GitHub Actions workflow env |
| `UAT_MODE` | No | 🟢 Low | GitHub Actions (runtime, set by workflow step) |

> [TODO: Confirm whether a `.env.example` file exists for onboarding — none was found in the provided files.]
> [TODO: `DATABASE_URL` is set in plaintext in `docker-compose.yml` frontend environment block with embedded credentials — should use Docker secrets or environment injection.]

---

## 6. Dependencies

| Dependency | Type | Purpose | Notes |
|---|---|---|---|
| **Anthropic Claude API** | External SaaS | LLM inference for underwriting agents and all 5 CI/CD tools | Models: `claude-sonnet-4-20250514`, `claude-haiku-4-5-20251001`, `claude-sonnet-4-6` |
| **Google Gemini API** | External SaaS | Alternative LLM inference | Model: `gemini-3-flash-preview`; [TODO: verify model name is valid — `gemini-3-flash-preview` does not match known GCP model IDs] |
| **SendGrid API** | External SaaS | Email notifications from CI/CD automation tools | Used in all 5 GitHub Actions workflows |
| **GitHub API** (`api.github.com`) | External SaaS | PR comments, file reads, artefact writes in `ai-delivery-outputs` repo | Accessed via `GH_TOKEN` |
| **`ai-delivery-outputs` repo** | GitHub repository (separate) | Output storage for CI/CD tool artefacts (docs, test reports, UAT packs) | Must exist under `OUTPUT_REPO_OWNER` account |
| **Redis Stack Server 7.2.0** | Infrastructure | LangGraph `AsyncRedisSaver` conversation checkpointing | [TODO: migrate to managed service for production per `graph.py` TODO comment] |
| **PostgreSQL 16** | Infrastructure | Chainlit conversation persistence | |
| **LangChain / LangGraph** | Python library | Agent graph construction, tool orchestration, streaming | Core orchestration framework |
| **Chainlit** | Python library / framework | Frontend chat UI and PostgreSQL session management | |
| **CatBoost** (pre-trained model) | ML model | Risk classification (`model_predictions.db`) | Model v1.0, trained 2024-06-01; not retrained in this repo |
| **`langchain_google_genai`** | Python library | Google Gemini LangChain integration | |
| **`langchain_anthropic`** | Python library | Anthropic Claude LangChain integration | |
| **`sse_starlette`** | Python library | Server-Sent Events streaming from FastAPI | |
| **`pydantic`** | Python library | Request/response validation, `UnderwritingReport` schema | |

---

## 7. Deployment Instructions

### Prerequisites

- Docker and Docker Compose v2+ installed
- A `.env` file in the repository root containing at minimum:

```bash
ANTHROPIC_API_KEY=sk-ant-...
GOOGLE_API_KEY=...        # Required if using Gemini model
```

- SQLite database files present at:
  - `./database/customer_profile.db`
  - `./database/feature_importance.db`
  - `./database/model_predictions.db`
  - `./database/application_profile.db`

### Local Deployment

```bash
# Clone the repository
git clone https://github.com/kylodeng/underwriting_chatbot-main.git
cd underwriting_chatbot-main

# Create and populate .env file
cp .env.example .env   # [TODO: confirm .env.example exists]
# Edit .env and set ANTHROPIC_API_KEY, GOOGLE_API_KEY

# Build and start all services
docker compose up --build

# Verify services are healthy
docker compose ps
curl http://localhost:8000/health

# Access the frontend
open http://localhost:8080
```

### Service Ports

| Service | Host Port | Container Port |
|---|---|---|
| Backend (FastAPI) | `8000` | `8000` |
| Frontend (Chainlit) | `8080` | `8080` |
| Redis | `6379` | `6379` |
| PostgreSQL | `5432` | `5432` |

### Stopping Services

```bash
docker compose down

# To also remove persistent volumes (destroys PostgreSQL data)
docker compose down -v
```

### GitHub Actions CI/CD Tools Setup

```bash
# Required repository secrets (set in GitHub → Settings → Secrets → Actions):
# ANTHROPIC_API_KEY   — Anthropic API key
# GH_TOKEN            — GitHub PAT with repo read/write access
# SENDGRID_API_KEY    — SendGrid API key

# Tool 1 (Code Review) — triggers automatically on PR open/sync
# Tool 2 (Tech Docs)   — triggers automatically on push to main
# Tool 3 (Business Docs) — triggers on version tag push
git tag v1.0.0 && git push origin v1.0.0

# Tool 4 (Auto Testing) — triggers on PR with src/** changes
# Tool 5 (UAT)          — triggers on release branch creation
git checkout -b release/1.0.0 && git push origin release/1.0.0

# Manual dispatch for any tool via GitHub Actions UI → workflow → Run workflow
```

---

## 8. Risks and TODOs

### Extracted from Code

| Location | Risk / TODO |
|---|---|
| `backend/agent/graph.py:1` | **TODO (in code):** Migrate Redis to external managed service (e.g. Azure Cache for Redis) so session memory persists across serverless backend rest