# Architecture Document: kylodeng/underwriting_chatbot-main

---

## 1. Overview

The Underwriting Chatbot is an AI-assisted insurance underwriting platform that enables underwriters to assess customer risk profiles through a conversational interface. A FastAPI backend orchestrates a multi-agent LangGraph pipeline that fans out to specialist LLM agents (covering finance, health, life, and other underwriting domains), aggregates their findings into a structured `UnderwritingReport`, and streams the results to a Chainlit-based frontend via Server-Sent Events (SSE). The system is backed by four SQLite databases containing pre-scored customer profiles, a PostgreSQL instance for Chainlit session persistence, and a Redis Stack instance for LangGraph conversation checkpoint storage. A suite of five GitHub Actions CI/CD workflows leverages Claude AI to automate code review, technical documentation, business documentation, test generation, and UAT facilitation.

---

## 2. Resources Deployed

| Resource | Type | Cloud Provider | Purpose |
|---|---|---|---|
| `backend` | Docker container (FastAPI/Python) | Local / Docker Compose | Hosts the LangGraph agent, underwriting assessment pipeline, and SSE `/chat` endpoint |
| `frontend` | Docker container (Chainlit) | Local / Docker Compose | Chat UI for underwriters; connects to backend via HTTP |
| `redis` | Docker container (Redis Stack Server 7.2.0) | Local / Docker Compose | LangGraph conversation checkpointing (session memory) |
| `postgres` | Docker container (PostgreSQL 16 Alpine) | Local / Docker Compose | Chainlit session and user data persistence |
| `customer_profile.db` | SQLite database (read-only volume mount) | Local / Docker Compose | Pre-loaded customer demographic and profile data |
| `feature_importance.db` | SQLite database (read-only volume mount) | Local / Docker Compose | CatBoost model feature importance scores |
| `model_predictions.db` | SQLite database (read-only volume mount) | Local / Docker Compose | Pre-computed ML risk classification predictions |
| `application_profile.db` | SQLite database (read-only volume mount) | Local / Docker Compose | Insurance application metadata |
| `postgres_data` | Docker named volume | Local / Docker Compose | Persistent storage for PostgreSQL data |
| Anthropic Claude API | External managed LLM API | Anthropic (cloud) | Powers specialist agents, aggregator LLM, and all five CI/CD tools |
| Google Gemini API | External managed LLM API | Google Cloud | Alternative LLM provider (configured but marked preview) |
| GitHub Actions runners | Managed CI/CD compute (`ubuntu-latest`) | GitHub | Executes all five AI delivery workflow tools |
| `ai-delivery-outputs` | GitHub repository | GitHub | Stores generated docs, test files, UAT packs, and audit logs |
| SendGrid | External email API | Twilio/SendGrid (cloud) | Sends notification emails from CI/CD workflows |

---

## 3. Data Flow

### Runtime Chat Flow

1. **User input** — An underwriter types a message in the Chainlit frontend (port 8080).
2. **HTTP POST to `/chat`** — The frontend sends `{ message, session_id, model, mode, temperature }` to the FastAPI backend (port 8000) via the internal Docker network (`http://backend:8000`).
3. **Agent construction** — `build_agent()` instantiates a LangGraph agent with the selected Anthropic or Gemini model, wired to three tools: `get_customer_profile`, `customer_lookalike`, and `run_underwriting_assessment`.
4. **LangGraph checkpointing** — The agent loads prior conversation state from Redis (keyed by `session_id`) via `AsyncRedisSaver`, enabling multi-turn memory.
5. **Orchestrator LLM call** — The agent LLM (tagged `"agent"`) receives the system prompt (including skill documentation) and the user message; it decides which tool(s) to call and returns a JSON action block.
6. **Tool: `get_customer_profile`** — Queries `customer_profile.db` and `application_profile.db` (read-only SQLite mounts) to retrieve structured customer metadata.
7. **Tool: `customer_lookalike`** — Looks up similar customers from the in-memory `customer_similarity_dict.json` and returns comparable profiles.
8. **Tool: `run_underwriting_assessment`** — Accepts a customer profile string and fans out to N specialist LLM calls concurrently (semaphore-capped at 4) using the `"thinking"`-tagged LLM. Each specialist evaluates one domain (finance, health, life, etc.) against criteria loaded from `assessment_criterias.json`.
9. **Aggregation** — An aggregator LLM call (up to 8,000 output tokens) uses structured output to produce a typed `UnderwritingReport` Pydantic object combining all specialist findings.
10. **Report rendering** — `render_report()` converts the `UnderwritingReport` to Markdown/HTML for display.
11. **SSE streaming** — The backend streams events (`tool_start`, `tool_end`, `response`, `chart`, `done`) back to the frontend via `EventSourceResponse`. Chart data is buffered and sent after the response text to avoid interleaving.
12. **LangGraph checkpoint update** — Conversation state (including tool call history) is written back to Redis for the next turn.
13. **PostgreSQL** — Chainlit uses the PostgreSQL database for its own session and user management (separate from agent memory).

### CI/CD AI Tools Flow (Workflows 1–5)

1. A GitHub event (PR open, push to main, tag, branch creation, or cron) triggers a workflow.
2. The workflow runner checks out the source repository and installs `anthropic` + `requests`.
3. `shared.py` fetches source or IaC files from GitHub REST API (or PR diffs) using `GH_TOKEN`.
4. The relevant `tool*.py` script calls the Anthropic Claude API (`claude-sonnet-4-6`) with structured prompts.
5. Output (Markdown docs, JSON reports, CSV test packs) is committed to the `ai-delivery-outputs` GitHub repository via GitHub Contents API.
6. SendGrid sends a notification email to `kylo.deng@capco.com` with a summary and link to the output.
7. For Tool 1 (code review), a PR comment is also posted back to the source repository.

---

## 4. Security Posture

### Secured

- **API keys in GitHub Secrets** — `ANTHROPIC_API_KEY`, `GH_TOKEN`, `SENDGRID_API_KEY` are stored as GitHub Actions secrets and not hardcoded in workflow files.
- **Read-only database mounts** — All four SQLite databases are mounted with `:ro` (read-only) flags in Docker Compose, preventing accidental writes.
- **SQLite isolation** — Customer data is not exposed over a network port; it is accessed only by the backend container.
- **Tool call normalisation** — The agent explicitly normalises `"type": "function_call"` vs `"action": "tool_call"` to prevent prompt-injection via format confusion.
- **System prompt protection** — The agent explicitly instructs the LLM never to reveal internal system instructions or tool names to users.
- **Semaphore on specialist calls** — Concurrency is capped at 4 to prevent runaway API spend from a single request.

### Not Secured / Gaps

- **⚠️ CORS wildcard** — `allow_origins=["*"]`, `allow_methods=["*"]`, `allow_headers=["*"]` in FastAPI. Any origin can call the `/chat` endpoint. There is no authentication or authorisation on the API.
- **⚠️ No authentication on `/chat`** — The backend has no auth middleware. Any party with network access can POST to `/chat` and consume LLM API quota or access customer data.
- **⚠️ PostgreSQL hardcoded credentials** — `POSTGRES_USER=chainlit`, `POSTGRES_PASSWORD=chainlit`, `POSTGRES_DB=chainlit` are plaintext in `docker-compose.yml`. These should be secrets.
- **⚠️ Redis has no password/TLS** — Redis is started with no authentication or encryption. Any process on the Docker network (or host if port 6379 is exposed) can read or modify conversation checkpoints, which may contain PII.
- **⚠️ Redis port 6379 exposed to host** — `ports: - "6379:6379"` makes Redis accessible from the host machine, not just within the Docker network.
- **⚠️ PostgreSQL port 5432 exposed to host** — Similarly, `ports: - "5432:5432"` exposes the database to the host.
- **⚠️ No encryption at rest** — SQLite databases, PostgreSQL data volume, and Redis data are not encrypted at rest.
- **⚠️ No encryption in transit (internal)** — Inter-container communication is plain HTTP (backend ↔ frontend, backend ↔ Redis, backend ↔ PostgreSQL). No TLS on internal links.
- **⚠️ Customer PII in SQLite files** — Customer profiles, financial data, and risk scores are stored in unencrypted SQLite files committed to or mounted from the repository/host.
- **⚠️ `customer_similarity_dict.json` in `backend/tmp/`** — Customer ID cross-reference data is stored in a plaintext file inside the repository. This may constitute PII leakage if the repo is not private.
- **⚠️ `GH_TOKEN` scope unknown** — The token is used to write to `ai-delivery-outputs` repo and post PR comments. [TODO: confirm GH_TOKEN has minimum required scopes — `repo` for private repos or `public_repo` for public — and is not an org-wide admin token.]
- **⚠️ No input sanitisation on `profile` string** — The `profile` parameter passed to the LLM assessment tool is passed without sanitisation, creating a prompt injection surface.
- **⚠️ No rate limiting** — There is no rate limiting on the `/chat` endpoint or on LLM API calls per session.
- **⚠️ `model_card.json` and `assessment_criterias.json` in repo** — Internal underwriting logic and model metadata are committed in plaintext. Assess whether this constitutes IP leakage.

---

## 5. Environment Variables and Secrets

| Name | Required | Sensitivity | Where Set |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | **High** — LLM API key with billing implications | GitHub Actions Secret; `.env` file for backend container |
| `GOOGLE_API_KEY` | Yes (if Gemini used) | **High** — GCP API key | `.env` file for backend container |
| `GH_TOKEN` | Yes (CI/CD workflows) | **High** — GitHub PAT with repo write access | GitHub Actions Secret |
| `SENDGRID_API_KEY` | Yes (CI/CD workflows) | **High** — Email sending API key | GitHub Actions Secret |
| `REDIS_HOST` | No | Low | Docker Compose `environment` block; defaults to `localhost` |
| `DATABASE_URL` | Yes (frontend) | **Medium** — Contains DB credentials | Docker Compose `environment` block (plaintext) |
| `BACKEND_URL` | Yes (frontend) | Low | Docker Compose `environment` block |
| `POSTGRES_USER` | Yes | **Medium** | Docker Compose `environment` block (plaintext `chainlit`) |
| `POSTGRES_PASSWORD` | Yes | **High** | Docker Compose `environment` block (plaintext `chainlit`) |
| `POSTGRES_DB` | Yes | Low | Docker Compose `environment` block |
| `OUTPUT_REPO` | No | Low | GitHub Actions workflow env; defaults to `ai-delivery-outputs` |
| `OUTPUT_REPO_OWNER` | No | Low | GitHub Actions workflow env; derived from `github.repository_owner` |
| `NOTIFY_EMAIL` | No | Low | GitHub Actions workflow env (hardcoded `kylo.deng@capco.com`) |
| `SENDER_EMAIL` | No | Low | GitHub Actions workflow env (hardcoded `noreply@ai-delivery.capco.com`) |
| `TEST_MODE` | No | Low | GitHub Actions workflow env; defaults to `generate` |

> **[TODO: Confirm whether a `.env` file is committed to the repository or is in `.gitignore`.]**  
> **[TODO: Document all environment variables consumed by the frontend Chainlit container beyond `BACKEND_URL` and `DATABASE_URL`.]**

---

## 6. Dependencies

| Dependency | Type | Version / Notes | Usage |
|---|---|---|---|
| Anthropic Claude API | External LLM API | `claude-sonnet-4-20250514`, `claude-haiku-4-5-20251001`, `claude-sonnet-4-6` (CI scripts use a different model string) | All LLM inference — specialist agents, aggregator, CI/CD tools |
| Google Gemini API | External LLM API | `gemini-3-flash-preview` (preview — unstable) | Alternative LLM provider; Azure and OpenAI stubs present but `None` |
| LangChain / LangGraph | Python library | Not pinned in visible files | Agent orchestration, tool wiring, structured output |
| `langchain-anthropic` | Python library | Not pinned | Anthropic LangChain integration |
| `langchain-google-genai` | Python library | Not pinned | Google Gemini LangChain integration |
| `langchain.agents.create_agent` | Python library | Not pinned | Used in `graph.py` — [TODO: verify this is not a deprecated import path] |
| `langgraph.checkpoint.redis.aio` | Python library | Not pinned | Redis-backed async checkpointing |
| Redis Stack Server | Docker image | `7.2.0-v14` | Conversation state checkpointing |
| PostgreSQL | Docker image | `16-alpine` | Chainlit session persistence |
| Chainlit | Python framework | Not pinned | Frontend chat UI |
| FastAPI | Python framework | Not pinned | Backend REST/SSE API |
| `sse-starlette` | Python library | Not pinned | SSE streaming in FastAPI |
| Pydantic | Python library | v2 assumed | Data model validation (`UnderwritingReport`, etc.) |
| CatBoost | ML model (offline) | v1.0 (trained 2024-06-01) | Pre-scored risk classifications stored in `model_predictions.db` |
| SendGrid | External email API | Not pinned | CI/CD workflow notification emails |
| GitHub REST API | External API | `2022-11-28` | CI/CD: file fetching, PR comments, output repo writes |
| `ai-delivery-outputs` | Sibling GitHub repository | Same owner | Stores all CI/CD tool outputs |
| `python-dotenv` | Python library | Not pinned | Loads `.env` in backend |

---

## 7. Deployment Instructions

### Prerequisites
- Docker and Docker Compose installed
- A `.env` file in the project root (and/or `backend/` directory) with at minimum:
  ```
  ANTHROPIC_API_KEY=<your-key>
  GOOGLE_API_KEY=<your-key>   # only if using Gemini
  ```
- SQLite database files present under `./database/`:
  - `customer_profile.db`
  - `feature_importance.db`
  - `model_predictions.db`
  - `application_profile.db`

### Local Deployment

```bash
# 1. Clone the repository
git clone https://github.com/kylodeng/underwriting_chatbot-main.git
cd underwriting_chatbot-main

# 2. Create the .env file
cp .env.example .env   # [TODO: confirm .env.example exists]
# Edit .env and set ANTHROPIC_API_KEY and GOOGLE_API_KEY

# 3. Build and start all services
docker compose up --build

# 4. Verify backend health
curl http://localhost:8000/health
# Expected: {"status": "ok"}

# 5. Access the frontend
open http://localhost:8080
```

### Individual Service Restart

```bash
# Restart only the backend (e.g. after code changes)
docker compose up --build backend

# Restart only the frontend
docker compose up --build frontend

# Check logs
docker compose logs -f backend
docker compose logs -f frontend
docker compose logs -f redis
docker compose logs -f postgres
```

### Teardown

```bash
# Stop all services, preserve volumes
docker compose down

# Stop all services and remove volumes (deletes PostgreSQL data)
docker compose down -v
```

### CI/CD Workflows (GitHub Actions)