# Architecture Document: kylodeng/underwriting_chatbot-main

---

## 1. Overview

The Underwriting Chatbot is an AI-assisted insurance underwriting platform that enables underwriters to assess customer risk profiles through a conversational interface. The system ingests pre-computed customer data from SQLite databases, runs multi-specialist LLM assessments (finance, health, life) in parallel using Anthropic Claude and Google Gemini models, and returns a structured `UnderwritingReport` with risk classification, findings, and follow-up actions. A LangGraph-based agent orchestrates tool calls (customer profile lookup, lookalike analysis, risk assessment), streams responses via Server-Sent Events from a FastAPI backend to a Chainlit frontend, and persists conversation state in Redis. A suite of five GitHub Actions CI/CD workflows use Claude (via Anthropic API) to automate code review, technical documentation, business documentation, test generation, and UAT facilitation.

---

## 2. Resources Deployed

| Resource | Type | Cloud Provider | Purpose |
|---|---|---|---|
| `backend` | Docker container (FastAPI) | Local / Docker Compose | Hosts the chat API, LangGraph agent, and underwriting assessment engine |
| `frontend` | Docker container (Chainlit) | Local / Docker Compose | Provides the conversational UI for underwriters |
| `redis` | Docker container (`redis-stack-server:7.2.0-v14`) | Local / Docker Compose | LangGraph conversation checkpoint store (session memory) |
| `postgres` | Docker container (`postgres:16-alpine`) | Local / Docker Compose | Chainlit user/session persistence |
| `customer_profile.db` | SQLite file (read-only mount) | Local filesystem | Customer profile data |
| `feature_importance.db` | SQLite file (read-only mount) | Local filesystem | ML model feature importance data |
| `model_predictions.db` | SQLite file (read-only mount) | Local filesystem | Pre-computed CatBoost risk classification predictions |
| `application_profile.db` | SQLite file (read-only mount) | Local filesystem | Insurance application data |
| `customer_similarity_dict.json` | JSON file | Local filesystem | Pre-computed customer lookalike similarity index |
| Anthropic Claude API | External AI API | Anthropic (cloud) | LLM inference for underwriting assessments and CI/CD tools |
| Google Gemini API | External AI API | Google Cloud | Alternative LLM provider (gemini-3-flash-preview) |
| GitHub Actions runners | CI/CD compute (`ubuntu-latest`) | GitHub (cloud) | Automated code review, docs, testing, UAT workflows |
| `ai-delivery-outputs` (separate repo) | GitHub repository | GitHub | Stores AI-generated artifacts (docs, test files, UAT packs) |
| SendGrid | Email API | Twilio/SendGrid (cloud) | Notification emails for CI/CD workflow completions |

---

## 3. Data Flow

### Runtime Chat Flow

1. **User input**: An underwriter types a message in the Chainlit frontend (port 8080).
2. **HTTP POST to backend**: The frontend sends a `POST /chat` request to the FastAPI backend (port 8000) with `{ message, session_id, model, mode, temperature }`.
3. **Agent invocation**: The backend calls `build_agent()` which instantiates a LangGraph agent backed by an Anthropic Claude or Gemini LLM, with Redis as the conversation checkpointer.
4. **Agent reasoning**: The agent LLM evaluates the message against its system prompt and skill documentation. It decides whether to call a tool or produce a final answer, emitting JSON action payloads.
5. **Tool dispatch — `get_customer_profile`**: If required, the agent calls `get_customer_profile`, which queries `customer_profile.db` (and related SQLite databases) by customer ID and returns structured profile data.
6. **Tool dispatch — `customer_lookalike`**: The agent may call `customer_lookalike`, which looks up pre-computed similar customer IDs from `customer_similarity_dict.json`.
7. **Tool dispatch — `run_underwriting_assessment`**: The agent calls `run_underwriting_assessment(profile)`, which fans out **parallel async LLM calls** (up to 4 concurrent via `asyncio.Semaphore`) — one specialist LLM call per assessment category (finance, health, life, etc.) using prompts from `assessment_criterias.json`.
8. **Aggregation**: Specialist outputs are collected and passed to an aggregator LLM configured with `structured_output(UnderwritingReport)`, which produces a validated Pydantic model containing risk class, findings, top drivers, and follow-up items.
9. **SSE streaming**: The backend streams the response back to the frontend as Server-Sent Events (SSE), with distinct event types: `tool_start`, `tool_end`, `response` (text chunks), `thinking` (specialist reasoning), and `chart` (visualisation payloads).
10. **Redis checkpoint**: LangGraph saves the conversation turn to Redis keyed by `session_id`, enabling multi-turn continuity.
11. **PostgreSQL persistence**: Chainlit records session/user metadata to PostgreSQL.

### CI/CD AI Tooling Flow

12. **GitHub event triggers** a workflow (PR open, push to main, release tag, schedule, or manual dispatch).
13. **Python script** (`tool1–5`) calls `get_repo_files()` or `get_pr_diff()` via the GitHub REST API using `GH_TOKEN`.
14. **Claude API call** (`call_claude()` in `shared.py`) sends the code context and a structured system prompt to `claude-sonnet-4-6`.
15. **Output written** to the `ai-delivery-outputs` GitHub repository via `write_output_file()` (GitHub Contents API PUT).
16. **PR comment posted** (tool 1 only) via GitHub Issues API.
17. **Email notification** sent via SendGrid API to `kylo.deng@capco.com`.

---

## 4. Security Posture

### What Is Secured

- **API secrets** (Anthropic, Google, SendGrid, GitHub tokens) are stored as GitHub Actions secrets and injected as environment variables — not hardcoded in source.
- **Database files are mounted read-only** (`ro`) in the backend container, preventing write access from a compromised backend process.
- **Redis is not exposed publicly** — only accessible within the Docker Compose internal network.
- **PostgreSQL is not exposed publicly** — only accessible within the Docker Compose internal network.
- **CORS is configured** on the FastAPI backend — though currently set to `allow_origins=["*"]` (see gaps).

### Security Gaps ⚠️

- **`allow_origins=["*"]`** — CORS is fully open. Any origin can make requests to the backend API. This must be restricted to the frontend's actual origin before any non-local deployment.
- **No authentication or authorisation** — The `/chat` endpoint has no auth middleware. Any client with network access to port 8000 can query the underwriting system. [TODO: Is this intended to be intranet-only? What auth mechanism is planned — OAuth, API key, mTLS?]
- **PostgreSQL credentials are hardcoded** in `docker-compose.yml` (`chainlit`/`chainlit`). These are default/example credentials and must be rotated and injected via secrets in any non-local environment.
- **Redis has no authentication** — The Redis instance runs with no password and no ACL. Anyone on the Docker network can read/write conversation checkpoints.
- **No TLS/HTTPS** — Neither the backend (port 8000) nor the frontend (port 8080) are configured with TLS. All traffic including potential PII (customer financial/medical data) is transmitted in plaintext.
- **No encryption at rest** — SQLite database files are mounted from the local filesystem with no encryption. Customer PII and financial/medical data is stored unencrypted.
- **Customer PII in LLM prompts** — Customer financial data, medical conditions, and personally identifiable information (age, nationality, income, smoker status) are sent directly to the Anthropic and Google cloud APIs. There is no data masking or anonymisation pipeline. [TODO: Has a Data Processing Agreement been signed with Anthropic and Google? Is this compliant with applicable regulations (GDPR, etc.)?]
- **`GH_TOKEN` scope is unknown** — The GitHub token used in CI/CD scripts has write access to `ai-delivery-outputs` and read access to the source repo. [TODO: What is the minimum required scope? Is it a fine-grained PAT or a classic token with broad permissions?]
- **CI/CD scripts use `os.environ["KEY"]` (hard fail)** — Missing any of `ANTHROPIC_API_KEY`, `GH_TOKEN`, or `SENDGRID_API_KEY` will crash the workflow with an unhandled `KeyError`, which may leak environment variable names in logs.
- **`customer_similarity_dict.json` is committed to source** — This file contains mappings of 10,000+ customer IDs and their similarity relationships, which is sensitive operational data committed to the repository.
- **No secrets scanning** in CI/CD pipelines (no `trufflehog`, `gitleaks`, or GitHub secret scanning configuration found).
- **`model_predictions.db` and `customer_profile.db` are committed/mounted from the repo** — [TODO: Are these real customer records or synthetic? If real, this is a significant data governance issue.]

---

## 5. Environment Variables and Secrets

| Name | Required | Sensitivity | Where Set |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | **High** — LLM API key | GitHub Actions secret; `.env` file for backend |
| `GOOGLE_API_KEY` | Yes (if using Gemini) | **High** — GCP API key | `.env` file for backend |
| `GH_TOKEN` | Yes (CI/CD) | **High** — GitHub PAT with repo write access | GitHub Actions secret |
| `SENDGRID_API_KEY` | Yes (CI/CD) | **High** — Email service API key | GitHub Actions secret |
| `REDIS_HOST` | No | Low | `docker-compose.yml` environment block (`redis`) |
| `DATABASE_URL` | Yes (frontend) | **Medium** — PostgreSQL connection string with credentials | `docker-compose.yml` environment block |
| `BACKEND_URL` | Yes (frontend) | Low | `docker-compose.yml` environment block (`http://backend:8000`) |
| `POSTGRES_USER` | Yes | **Medium** | `docker-compose.yml` (hardcoded: `chainlit`) ⚠️ |
| `POSTGRES_PASSWORD` | Yes | **High** | `docker-compose.yml` (hardcoded: `chainlit`) ⚠️ |
| `POSTGRES_DB` | Yes | Low | `docker-compose.yml` (hardcoded: `chainlit`) |
| `OUTPUT_REPO` | No (CI/CD) | Low | GitHub Actions env (`ai-delivery-outputs`) |
| `OUTPUT_REPO_OWNER` | No (CI/CD) | Low | GitHub Actions env (derived from `github.repository_owner`) |
| `NOTIFY_EMAIL` | No (CI/CD) | Low | GitHub Actions env (hardcoded: `kylo.deng@capco.com`) |
| `SENDER_EMAIL` | No (CI/CD) | Low | GitHub Actions env (hardcoded: `noreply@ai-delivery.capco.com`) |

> ⚠️ `POSTGRES_USER` and `POSTGRES_PASSWORD` are hardcoded plaintext in `docker-compose.yml`. These must be moved to a `.env` file or secrets manager before any non-local deployment.

> [TODO: Is there a `.env` file template/example (`.env.example`) in the repo? The backend uses `load_dotenv()` but no `.env.example` is provided in the shared files.]

---

## 6. Dependencies

### External Services & APIs

| Dependency | Type | Used By | Notes |
|---|---|---|---|
| Anthropic Claude API (`claude-sonnet-4-20250514`, `claude-haiku-4-5-20251001`, `claude-sonnet-4-6`) | External AI API | Backend assessment engine, all CI/CD tools | Primary LLM provider; requires `ANTHROPIC_API_KEY` |
| Google Gemini API (`gemini-3-flash-preview`) | External AI API | Backend (optional provider) | Requires `GOOGLE_API_KEY`; model name appears non-standard — [TODO: verify `gemini-3-flash-preview` is a valid model ID] |
| SendGrid API | External Email API | All CI/CD workflows | Notification on workflow completion; requires `SENDGRID_API_KEY` |
| GitHub REST API (`api.github.com`) | External API | All CI/CD scripts | Source code fetch, PR comments, output repo writes; requires `GH_TOKEN` |

### Python Libraries (Backend)

| Library | Purpose |
|---|---|
| `fastapi` | REST API framework |
| `langchain`, `langchain-core`, `langchain-anthropic`, `langchain-google-genai` | LLM orchestration |
| `langgraph` | Agent state graph and conversation checkpointing |
| `langgraph-checkpoint-redis` | Redis-backed LangGraph checkpointer |
| `redis` (asyncio) | Redis client |
| `pydantic` | Data validation and structured LLM output |
| `sse-starlette` | Server-Sent Events streaming |
| `catboost` | ML model for risk classification (implied by `model_card.json`) |
| `pyyaml` | Config file parsing |
| `python-dotenv` | Local `.env` loading |
| `chainlit` | Frontend chat UI framework |
| `asyncpg` | Async PostgreSQL driver (for Chainlit) |

### Python Libraries (CI/CD Scripts)

| Library | Purpose |
|---|---|
| `anthropic` | Claude API client for CI/CD tools |
| `requests` | GitHub API and HTTP calls |

### Other Repositories

| Repo | Relationship | Notes |
|---|---|---|
| `{owner}/ai-delivery-outputs` | Write-only output store | CI/CD workflows push generated docs, test files, and UAT packs here |

---

## 7. Deployment Instructions

### Prerequisites

- Docker and Docker Compose installed
- A `.env` file in the project root (and/or `backend/.env`) with the required secrets

**Create `.env` file:**
```bash
cat > .env << EOF
ANTHROPIC_API_KEY=your_anthropic_api_key
GOOGLE_API_KEY=your_google_api_key
EOF
```

### Local Deployment (Docker Compose)

```bash
# Clone the repository
git clone https://github.com/kylodeng/underwriting_chatbot-main.git
cd underwriting_chatbot-main

# Build and start all services (Redis, PostgreSQL, Backend, Frontend)
docker compose up --build

# Run in detached mode
docker compose up --build -d

# Check service health
curl http://localhost:8000/health

# View logs
docker compose logs -f backend
docker compose logs -f frontend

# Stop all services
docker compose down

# Stop and remove volumes (WARNING: deletes PostgreSQL data)
docker compose down -v
```

### Service Endpoints

```
Frontend (Chainlit UI):  http://localhost:8080
Backend (FastAPI):       http://localhost:8000
Backend Health Check:    http://localhost:8000/health
Redis:                   localhost:6379
PostgreSQL:              localhost:5432
```

### CI/CD Workflows (GitHub Actions)

```bash
# Tool 1 — Code Review (runs automatically on PR, or manually)
# Navigate to: Actions > "Tool 1 — Code Review" > Run workflow
# Select mode: repo (full repo review) or pr (specific PR number)

# Tool 2 — Tech Documentation (runs automatically on push to main)
# Or manually: Actions > "Tool 2 — Tech Documentation" > Run workflow

# Tool 3 — Business Documentation (runs on version tag push)
git tag v1.0.0
git push origin v1.0.0
# Or manually via Actions UI with project_name and release_version inputs

# Tool 4 — Auto Testing (runs automatically on PR with src/** changes)
# Or manually: Actions > "Tool 4 — Auto Testing" > Run workflow
# Select mode: generate or gap-analysis

# Tool 5 — UAT Facilitation (runs on release/* branch creation)
git checkout -b release/1.0.0
git push origin release/1.0.0
# Or manually via Actions UI
```

### Required GitHub Secrets (for CI/CD)

```bash
# Set via GitHub UI: Settings > Secrets and variables > Actions > New repository secret
ANTHROPIC_API_KEY   # Anthropic Claude API key
GH_TOKEN            