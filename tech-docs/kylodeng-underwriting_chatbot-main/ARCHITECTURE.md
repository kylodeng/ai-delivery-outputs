# Architecture Document: kylodeng/underwriting_chatbot-main

---

## 1. Overview

The Underwriting Chatbot is an AI-assisted insurance underwriting platform that enables underwriters to assess customer risk profiles through a conversational chat interface. The system ingests pre-computed customer data from SQLite databases, orchestrates multi-specialist LLM assessments (using Anthropic Claude and Google Gemini models) via a LangGraph agent graph, and returns structured underwriting reports covering finance, health, life, and other risk domains. A FastAPI backend streams responses over Server-Sent Events (SSE) to a frontend UI, with Redis used for LangGraph checkpoint/session memory and PostgreSQL used for Chainlit session persistence. The repository also ships five GitHub Actions–powered AI delivery tools (code review, tech docs, business docs, auto-testing, and UAT facilitation) that themselves call Claude via Anthropic's API.

---

## 2. Resources Deployed

| Resource | Type | Cloud Provider | Purpose |
|---|---|---|---|
| `backend` | Docker container (FastAPI, Python 3.x) | Local / self-hosted | REST + SSE API; hosts LangGraph agent and underwriting assessment logic |
| `frontend` | Docker container | Local / self-hosted | Chat UI (Chainlit-based); communicates with backend over HTTP |
| `redis` | Docker container (`redis/redis-stack-server:7.2.0-v14`) | Local / self-hosted | LangGraph agent conversation checkpoint/memory store |
| `postgres` | Docker container (`postgres:16-alpine`) | Local / self-hosted | Chainlit session/message persistence |
| `customer_profile.db` | SQLite file (read-only bind mount) | Local / self-hosted | Customer demographic and profile data |
| `feature_importance.db` | SQLite file (read-only bind mount) | Local / self-hosted | CatBoost model feature importance scores |
| `model_predictions.db` | SQLite file (read-only bind mount) | Local / self-hosted | Pre-computed CatBoost risk classification predictions |
| `application_profile.db` | SQLite file (read-only bind mount) | Local / self-hosted | Insurance application data |
| `postgres_data` | Docker named volume | Local / self-hosted | Persistent PostgreSQL data storage |
| Anthropic Claude API (claude-sonnet-4, claude-haiku-4-5) | External SaaS API | Anthropic | LLM for agent reasoning, specialist assessment, and CI tools |
| Google Gemini API (gemini-3-flash-preview) | External SaaS API | Google Cloud | Alternative LLM provider (configured but [TODO: verify if actively used]) |
| GitHub Actions runners (`ubuntu-latest`) | CI/CD compute | GitHub | Executes five AI delivery workflow tools |
| SendGrid API | External SaaS API | Twilio/SendGrid | Email notifications from CI/CD workflows |
| `ai-delivery-outputs` GitHub repo | External GitHub repository | GitHub | Stores generated docs, test files, UAT packs from CI workflows |

---

## 3. Data Flow

### Runtime (Chat) Flow

1. **User sends a message** via the frontend chat UI (port 8080). The frontend POSTs `{message, session_id, model, mode, temperature}` to `http://backend:8000/chat`.
2. **Backend receives the request** and calls `build_agent()`, which instantiates a LangGraph agent with the selected LLM, tools, and a Redis-backed `AsyncRedisSaver` checkpointer keyed by `session_id`.
3. **Agent reasons** using the system prompt and conversation history retrieved from Redis. It emits a JSON tool-call directive (`{"action": "tool_call", "tool_name": "...", "tool_args": {...}}`).
4. **Tool: `get_customer_profile`** — queries `customer_profile.db` (SQLite, read-only) and returns the customer's structured profile fields.
5. **Tool: `customer_lookalike`** — reads `customer_similarity_dict.json` (pre-computed) and returns a list of similar customer IDs from the same SQLite database.
6. **Tool: `run_underwriting_assessment`** — receives the customer profile string and fans out **parallel async LLM calls** (up to 4 concurrent via `asyncio.Semaphore`) to specialist agents, one per assessment category (finance, health, life, etc.), using prompts from `assessment_criterias.json`.
7. **Specialist LLMs** (claude-haiku-4-5, capped at 1,500 output tokens) return per-domain findings (Q1–Q13 style assessments).
8. **Aggregator LLM** (claude-sonnet-4, up to 8,000 output tokens) receives all specialist reports and uses `structured_output` to produce a validated `UnderwritingReport` Pydantic object (risk class, top drivers, follow-up items, data gaps, etc.).
9. **Backend streams the response** token-by-token as SSE events (`tool_start`, `tool_end`, `response`, `chart`, `report`, `done`) back to the frontend.
10. **Redis checkpoint is updated** with the new conversation turn so subsequent messages have full history.
11. **Frontend renders** the streamed text, any chart events, and the structured underwriting report card.

### CI/CD Flow (GitHub Actions)

12. **Trigger events** (PR open, push to main, tag, schedule, `workflow_dispatch`) fire one of five workflow YAML files.
13. **Workflow runner** checks out the source repo and installs `anthropic` + `requests`.
14. **Python script** fetches repo files or PR diffs via the **GitHub REST API** (authenticated with `GH_TOKEN`).
15. **Claude API** (`claude-sonnet-4-6` in `shared.py`) processes the code and returns structured output (JSON reviews, Markdown docs, test files, UAT packs).
16. **Output** is committed to the `ai-delivery-outputs` GitHub repo via GitHub REST API (`PUT /repos/.../contents/...`).
17. **SendGrid** sends an email notification to `kylo.deng@capco.com` with a link to the generated artefact.
18. **Audit log entry** is written (destination not fully visible in provided code — [TODO: confirm audit log sink]).

---

## 4. Security Posture

### What Is Secured

- **SQLite databases are mounted read-only** (`ro` flag in `docker-compose.yml`) — prevents backend from writing to source data.
- **Secrets managed via GitHub Actions Secrets** (`ANTHROPIC_API_KEY`, `GH_TOKEN`, `SENDGRID_API_KEY`) — not hardcoded in workflow YAML.
- **Backend env vars loaded from `.env` file** via `python-dotenv` — not committed (assumed; `.env` not present in repo).
- **Agent system prompt** explicitly instructs the LLM never to reveal internal instructions or tool names.
- **Specialist LLM output capped** at 1,500 tokens to prevent runaway generation costs.

### What Is NOT Secured — Gaps

- **No encryption at rest**: Redis is deployed with no password, no TLS, and no persistence encryption. Any data in Redis (conversation checkpoints containing customer PII) is unencrypted.
- **No encryption in transit (internal)**: All Docker internal service communication (`backend ↔ redis`, `backend ↔ postgres`, `frontend ↔ backend`) is plain HTTP/TCP with no TLS.
- **PostgreSQL uses hardcoded default credentials** (`chainlit`/`chainlit`) in `docker-compose.yml` — these are committed to the repository in plaintext.
- **CORS is fully open**: `allow_origins=["*"]`, `allow_methods=["*"]`, `allow_headers=["*"]` — the backend API accepts requests from any origin.
- **No authentication on the `/chat` endpoint**: Any client that can reach port 8000 can query any `session_id` and any customer profile.
- **No API rate limiting or abuse protection** on the backend FastAPI app.
- **Redis port 6379 is exposed on `0.0.0.0`** via `ports: - "6379:6379"` — accessible from the host and potentially the network.
- **PostgreSQL port 5432 is exposed on `0.0.0.0`** — same risk as Redis.
- **Customer PII flows through LLM APIs**: Customer profiles (age, income, medical conditions, nationality, etc.) are sent to Anthropic's and Google's external APIs. No data anonymisation or redaction is applied before transmission.
- **`customer_similarity_dict.json` is committed to the repository** in `backend/tmp/` — contains ~10,000 customer ID mappings.
- **`GH_TOKEN` scope is unknown** — [TODO: verify token is scoped to minimum permissions; if it's a classic PAT with `repo` scope, it is overly broad].
- **No secrets scanning** configured in the repository (no `.gitleaks` or similar).
- **Model card (`model_card.json`) and assessment criteria (`assessment_criterias.json`)** are committed in plaintext — exposes internal underwriting logic.
- **No input sanitisation** on `message`, `session_id`, or `model` fields in `ChatRequest` before they are passed to agent/LLM.

---

## 5. Environment Variables and Secrets

| Name | Required | Sensitivity | Where Set |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | 🔴 High (API key) | GitHub Actions Secret; backend `.env` file |
| `GOOGLE_API_KEY` | Yes (if Gemini used) | 🔴 High (API key) | Backend `.env` file |
| `GH_TOKEN` | Yes (CI workflows) | 🔴 High (GitHub PAT) | GitHub Actions Secret |
| `SENDGRID_API_KEY` | Yes (CI workflows) | 🔴 High (API key) | GitHub Actions Secret |
| `REDIS_HOST` | No | 🟢 Low | `docker-compose.yml` environment block; defaults to `localhost` |
| `DATABASE_URL` | Yes (frontend) | 🟡 Medium (DB creds in URL) | `docker-compose.yml` environment block (plaintext: `postgresql+asyncpg://chainlit:chainlit@postgres:5432/chainlit`) |
| `BACKEND_URL` | Yes (frontend) | 🟢 Low | `docker-compose.yml` environment block |
| `POSTGRES_USER` | Yes | 🟡 Medium | `docker-compose.yml` environment block (plaintext: `chainlit`) |
| `POSTGRES_PASSWORD` | Yes | 🔴 High | `docker-compose.yml` environment block (**hardcoded plaintext**: `chainlit`) |
| `POSTGRES_DB` | Yes | 🟢 Low | `docker-compose.yml` environment block |
| `OUTPUT_REPO` | No (CI) | 🟢 Low | GitHub Actions workflow `env` block; defaults to `ai-delivery-outputs` |
| `OUTPUT_REPO_OWNER` | No (CI) | 🟢 Low | GitHub Actions workflow `env` block |
| `NOTIFY_EMAIL` | No (CI) | 🟡 Medium (PII) | GitHub Actions workflow `env` block (hardcoded: `kylo.deng@capco.com`) |
| `SENDER_EMAIL` | No (CI) | 🟢 Low | GitHub Actions workflow `env` block |
| `GITHUB_RUN_URL` | No (CI) | 🟢 Low | GitHub Actions workflow `env` block |

---

## 6. Dependencies

| Dependency | Type | Purpose | Notes |
|---|---|---|---|
| Anthropic API (`claude-sonnet-4-20250514`, `claude-haiku-4-5-20251001`) | External SaaS | Primary LLM for agent reasoning and specialist assessments | Used in both backend and CI scripts |
| Google Generative AI (`gemini-3-flash-preview`) | External SaaS | Alternative LLM provider | Configured in `LLMS.py`; [TODO: confirm if actively called in production] |
| LangChain / LangGraph | Python library | Agent orchestration, tool calling, streaming | Core backend framework |
| Redis Stack (`redis/redis-stack-server:7.2.0-v14`) | Self-hosted container | LangGraph `AsyncRedisSaver` checkpoint store | Comment in `graph.py` notes TODO to migrate to managed service |
| PostgreSQL 16 | Self-hosted container | Chainlit session persistence | Hardcoded credentials |
| Chainlit | Python library (frontend) | Chat UI framework | [TODO: confirm version] |
| FastAPI + uvicorn | Python library | Backend REST/SSE API server | |
| `sse-starlette` | Python library | SSE streaming support for FastAPI | |
| SendGrid API | External SaaS | Email notification delivery from CI workflows | |
| GitHub REST API (`api.github.com`) | External SaaS | CI scripts: fetch repo files, PR diffs, write output files, post PR comments | Authenticated via `GH_TOKEN` |
| `ai-delivery-outputs` GitHub repo | External repo (same org) | Stores generated documentation, test files, UAT packs | Must exist before CI workflows run |
| CatBoostClassifier (pre-trained) | Pre-computed artefacts | Risk classification — predictions stored in `model_predictions.db` | Model trained offline; not retrained at runtime |
| SQLite databases (4 files) | Local file artefacts | Customer, application, feature importance, predictions data | Bind-mounted read-only into backend container |
| `customer_similarity_dict.json` | Local file artefact | Pre-computed customer lookalike mappings | Committed to repo in `backend/tmp/` |

---

## 7. Deployment Instructions

### Prerequisites

- Docker and Docker Compose v2 installed
- A `.env` file in the repo root (or `backend/` directory) with at minimum:

```bash
ANTHROPIC_API_KEY=sk-ant-...
GOOGLE_API_KEY=AIza...          # if using Gemini
```

- SQLite database files present at:
  - `./database/customer_profile.db`
  - `./database/feature_importance.db`
  - `./database/model_predictions.db`
  - `./database/application_profile.db`

- PostgreSQL init script present at `./postgres/init.sql`

### Start all services

```bash
# Clone the repository
git clone https://github.com/kylodeng/underwriting_chatbot-main.git
cd underwriting_chatbot-main

# Create the .env file
cp .env.example .env          # [TODO: confirm .env.example exists]
# Edit .env and populate secrets

# Build and start all containers
docker compose up --build

# Verify backend health
curl http://localhost:8000/health
# Expected: {"status": "ok"}
```

### Access the application

```bash
# Frontend chat UI
open http://localhost:8080

# Backend API (direct)
open http://localhost:8000
```

### Tear down

```bash
docker compose down

# To also remove the postgres volume (destructive)
docker compose down -v
```

### GitHub Actions CI tools setup

```bash
# Required GitHub Secrets (set in repo Settings > Secrets and variables > Actions):
# ANTHROPIC_API_KEY
# GH_TOKEN          — PAT with read access to source repo and write access to ai-delivery-outputs
# SENDGRID_API_KEY

# Trigger code review manually
gh workflow run tool1_code_review.yml -f review_mode=repo

# Trigger tech docs generation
gh workflow run tool2_tech_docs.yml

# Trigger business docs (manual)
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

| Location | Issue |
|---|---|
| `backend/agent/graph.py` line 1 | **TODO (in code)**: Migrate Redis to an external managed service (e.g. Azure Cache for Redis) so memory persists across serverless backend instances. |
| `backend/modules/LLMS.py` | **TODO (in code)**: Add more LLM providers. `azure` and `openai` entries are `None` — calling them will raise `ValueError`. |

### Security Risks

- **🔴