# Architecture Document: kylodeng/underwriting_chatbot-main

---

## 1. Overview

The Underwriting Chatbot is an AI-assisted insurance underwriting platform that allows underwriters to assess customer risk profiles through a conversational interface. The system ingests pre-computed customer data from SQLite databases, uses a multi-agent LLM architecture (Claude Anthropic and Google Gemini) to run specialist underwriting assessments across domains (finance, health, life, KYC, etc.), aggregates findings into a structured `UnderwritingReport`, and streams results back to the frontend via Server-Sent Events. The repository also includes a suite of five GitHub Actions-powered AI delivery tools (code review, tech docs, business docs, auto-testing, and UAT facilitation) that use the Anthropic Claude API to automate software delivery processes. Conversation state is persisted in Redis, and user/session data is stored in PostgreSQL.

---

## 2. Resources Deployed

| Resource | Type | Cloud Provider | Purpose |
|---|---|---|---|
| `backend` | Docker container (FastAPI) | Local / Docker host | Core API: LLM orchestration, SSE streaming, underwriting assessment |
| `frontend` | Docker container (Chainlit) | Local / Docker host | Chat UI served to underwriters |
| `redis` (redis-stack-server:7.2.0-v14) | Docker container | Local / Docker host | LangGraph conversation checkpointing / session memory |
| `postgres` (postgres:16-alpine) | Docker container | Local / Docker host | Chainlit session/user data persistence |
| `customer_profile.db` | SQLite file (read-only mount) | Local filesystem | Customer demographic and profile data |
| `feature_importance.db` | SQLite file (read-only mount) | Local filesystem | ML model feature importance scores |
| `model_predictions.db` | SQLite file (read-only mount) | Local filesystem | Pre-computed CatBoost model risk predictions |
| `application_profile.db` | SQLite file (read-only mount) | Local filesystem | Insurance application records |
| Anthropic Claude API | External SaaS API | Anthropic (cloud) | LLM inference: specialist assessment + aggregation + CI tools |
| Google Gemini API | External SaaS API | Google Cloud | Alternative LLM provider (gemini-3-flash-preview) |
| GitHub Actions runners | Managed CI/CD compute | GitHub (cloud) | AI delivery tools (code review, docs, testing, UAT) |
| SendGrid API | External SaaS API | Twilio/SendGrid | Email notifications for CI/CD tool outputs |
| `ai-delivery-outputs` (GitHub repo) | External GitHub repository | GitHub | Stores generated docs, test files, UAT packs from CI tools |
| `postgres_data` | Docker named volume | Local Docker host | PostgreSQL data persistence |

---

## 3. Data Flow

### Runtime (Chat) Flow

1. **User sends a message** via the Chainlit frontend (port 8080); the frontend proxies the request as an HTTP POST to `backend:8000/chat` with `message`, `session_id`, `model`, `temperature`, and `mode` fields.
2. **Backend receives the request** and calls `build_agent()`, which instantiates a LangGraph agent with the selected LLM (Claude or Gemini), attaches tools (`get_customer_profile`, `customer_lookalike`, `run_underwriting_assessment`), and wires a Redis-backed async checkpointer for conversation state keyed by `session_id`.
3. **Agent reasons** using the system prompt and conversation history retrieved from Redis; it emits a structured JSON tool-call response if a tool is needed.
4. **Tool: `get_customer_profile`** queries the read-only SQLite `customer_profile.db` (and potentially `application_profile.db`) mounted at `/data/` in the backend container and returns structured customer metadata.
5. **Tool: `customer_lookalike`** reads `backend/tmp/customer_similarity_dict.json` to return a list of similar customer IDs for benchmarking.
6. **Tool: `run_underwriting_assessment`** fans out to parallel specialist LLM calls (up to 4 concurrent via semaphore) — one per assessment category (finance, health, life, KYC, etc.) — each invoked against `assessment_criterias.json` prompts and the customer profile string.
7. **Aggregator LLM call** collects all specialist outputs and produces a structured `UnderwritingReport` Pydantic object via `structured_output`.
8. **Results stream back** to the frontend as Server-Sent Events (SSE): `tool_start`, `tool_end`, streamed response text chunks, and any buffered chart/visualisation events.
9. **Conversation turn is checkpointed** to Redis so subsequent messages in the same session have full history.
10. **PostgreSQL** stores Chainlit-managed session/user data (authentication state, chat history UI layer).

### CI/CD (AI Delivery Tools) Flow

11. **GitHub event triggers** one of five workflow YAML files (PR open, push to main, tag push, branch create, or scheduled cron).
12. **Workflow runner** checks out the source repo, installs `anthropic` and `requests`, and calls the corresponding Python script.
13. **Script** fetches repo files or PR diffs via the GitHub REST API and passes them to the Claude API.
14. **Claude response** (code review JSON, markdown docs, test files, UAT packs) is written to the `ai-delivery-outputs` GitHub repo via the GitHub Contents API.
15. **SendGrid** sends an email notification to `kylo.deng@capco.com` with a link to the output artifact.

---

## 4. Security Posture

### What IS secured

- **Secrets management**: All API keys (`ANTHROPIC_API_KEY`, `GH_TOKEN`, `SENDGRID_API_KEY`, `GOOGLE_API_KEY`) are stored as GitHub Actions secrets and injected as environment variables — not hardcoded in source.
- **Read-only SQLite mounts**: Database files are mounted `:ro` in Docker Compose, preventing the backend from writing to customer data.
- **Redis used only for ephemeral session state**: No sensitive PII is explicitly stored long-term in Redis.
- **Agent prompt injection guard**: The system prompt explicitly instructs the agent never to reveal internal instructions or tool names to the user.
- **Semaphore on LLM fan-out**: Limits concurrent specialist calls to 4, providing basic rate-limiting against runaway LLM spend.

### What is NOT secured / gaps

- **⚠️ No encryption at rest**: SQLite databases (`customer_profile.db`, `model_predictions.db`, etc.) containing customer PII and risk data are stored as plain files with no encryption. **This is a critical gap for a financial services application.**
- **⚠️ No encryption in transit within Docker network**: Inter-container communication (backend↔Redis, backend↔PostgreSQL, frontend↔backend) uses plain HTTP/TCP with no TLS. Redis on port 6379 has no authentication configured.
- **⚠️ Hardcoded PostgreSQL credentials**: `POSTGRES_USER: chainlit` / `POSTGRES_PASSWORD: chainlit` are set in plain text in `docker-compose.yml`. These must be rotated and moved to secrets before any non-local deployment.
- **⚠️ CORS wildcard**: `allow_origins=["*"]` in FastAPI middleware allows any origin to call the backend API. This is unsafe for any internet-facing deployment.
- **⚠️ No authentication on the `/chat` endpoint**: Any client that can reach port 8000 can send chat requests with arbitrary `session_id` values, potentially hijacking other users' sessions.
- **⚠️ No authentication on the `/health` endpoint**: Minor, but exposes service availability externally.
- **⚠️ Customer PII sent to third-party LLM APIs**: Customer profiles (age, income, medical conditions, nationality, smoker status) are sent to Anthropic and Google Gemini APIs. There is no evidence of data masking, anonymisation, or a Data Processing Agreement (DPA) check in the code.
- **⚠️ `customer_similarity_dict.json` in `backend/tmp/`**: A pre-computed similarity lookup containing thousands of customer IDs is committed directly to the repository. If the repo is public or the image is shared, this is a data exposure risk.
- **⚠️ No secrets scanning in CI**: None of the five GitHub Actions workflows run secret-scanning or SAST tooling.
- **⚠️ `GH_TOKEN` scope unknown**: The `GH_TOKEN` secret used by CI tools is used to write to the `ai-delivery-outputs` repo and post PR comments. If this is a PAT with broad repo scope, it should be replaced with a fine-grained token scoped to the minimum required permissions.
- **⚠️ No network segmentation**: All containers share a single default Docker bridge network with no firewall rules between services.
- **⚠️ No input validation on `session_id`**: The `session_id` field from the chat request is passed directly to the Redis checkpointer as a thread ID without sanitisation.

---

## 5. Environment Variables and Secrets

| Name | Required | Sensitivity | Where set |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | **High** — billable API key | GitHub Actions secret; `.env` file for backend |
| `GOOGLE_API_KEY` | Yes (if Gemini used) | **High** — billable API key | `.env` file for backend |
| `GH_TOKEN` | Yes (CI tools) | **High** — GitHub repo write access | GitHub Actions secret |
| `SENDGRID_API_KEY` | Yes (CI tools) | **High** — email send access | GitHub Actions secret |
| `REDIS_HOST` | No | Low | Docker Compose `environment` block (defaults to `localhost`) |
| `POSTGRES_USER` | Yes | Medium | Docker Compose `environment` (hardcoded: `chainlit`) |
| `POSTGRES_PASSWORD` | Yes | **High** | Docker Compose `environment` (hardcoded: `chainlit` — **must be changed**) |
| `POSTGRES_DB` | Yes | Low | Docker Compose `environment` (hardcoded: `chainlit`) |
| `DATABASE_URL` | Yes (frontend) | Medium | Docker Compose `environment` (constructed from hardcoded credentials) |
| `BACKEND_URL` | Yes (frontend) | Low | Docker Compose `environment` (`http://backend:8000`) |
| `OUTPUT_REPO` | No (CI) | Low | GitHub Actions `env` block (default: `ai-delivery-outputs`) |
| `OUTPUT_REPO_OWNER` | No (CI) | Low | GitHub Actions `env` block (derived from `github.repository_owner`) |
| `NOTIFY_EMAIL` | No (CI) | Low | GitHub Actions `env` block (`kylo.deng@capco.com`) |
| `SENDER_EMAIL` | No (CI) | Low | GitHub Actions `env` block (`noreply@ai-delivery.capco.com`) |
| `REVIEW_MODE` | No (CI Tool 1) | Low | Set dynamically in workflow step |
| `PR_NUMBER` | No (CI Tool 1) | Low | Set dynamically in workflow step |
| `RELEASE_VERSION` | No (CI Tools 3/5) | Low | Set dynamically in workflow step |
| `PROJECT_NAME` | No (CI Tool 3) | Low | Set dynamically in workflow step |
| `TEST_MODE` | No (CI Tool 4) | Low | GitHub Actions `env` block |
| `UAT_MODE` | No (CI Tool 5) | Low | Set dynamically in workflow step |

> [TODO: Confirm whether a `.env` file exists at the repo root and what additional variables it contains beyond what is visible in docker-compose.yml]

---

## 6. Dependencies

| Dependency | Type | Purpose | Version/Notes |
|---|---|---|---|
| Anthropic Claude API | External SaaS | LLM inference for underwriting assessment and all 5 CI tools | `claude-sonnet-4-20250514`, `claude-haiku-4-5-20251001` |
| Google Gemini API | External SaaS | Alternative LLM provider | `gemini-3-flash-preview` (stub — `azure` and `openai` are `None`) |
| SendGrid API | External SaaS | Email notification delivery from CI tools | [TODO: confirm API version/plan] |
| GitHub REST API v2022-11-28 | External SaaS | Repo file fetching, PR comments, output file writing | Used by all 5 CI tool scripts |
| `ai-delivery-outputs` (GitHub repo) | External repository | Stores all generated CI tool outputs | Must exist under same GitHub owner |
| LangChain / LangGraph | Python library | Agent orchestration, graph state machine, tool binding | [TODO: confirm exact pinned versions from requirements.txt/pyproject.toml] |
| `langchain-anthropic` | Python library | Anthropic model integration for LangChain | [TODO: version] |
| `langchain-google-genai` | Python library | Google Gemini integration for LangChain | [TODO: version] |
| `langgraph-checkpoint-redis` | Python library | Redis-backed async checkpointer for LangGraph | [TODO: version] |
| FastAPI | Python library | Backend HTTP/SSE server | [TODO: version] |
| `sse-starlette` | Python library | Server-Sent Events support for FastAPI | [TODO: version] |
| Chainlit | Python library / frontend | Chat UI framework | [TODO: version] |
| CatBoost | ML framework | Pre-trained `Risk_Classification` model | v1.0, trained offline — predictions stored in SQLite |
| `anthropic` (PyPI) | Python library | Direct Claude API client used in CI scripts | [TODO: version] |
| Redis Stack Server | Docker image | Session checkpointing | `7.2.0-v14` |
| PostgreSQL | Docker image | Chainlit session persistence | `16-alpine` |
| `python-dotenv` | Python library | `.env` file loading | [TODO: version] |
| `pydantic` | Python library | Structured output models (`UnderwritingReport`) | [TODO: version] |

---

## 7. Deployment Instructions

### Prerequisites

- Docker and Docker Compose installed
- A `.env` file in the repo root containing at minimum:

```bash
ANTHROPIC_API_KEY=<your-anthropic-api-key>
GOOGLE_API_KEY=<your-google-api-key>
```

- The four SQLite database files present under `./database/`:
  - `customer_profile.db`
  - `feature_importance.db`
  - `model_predictions.db`
  - `application_profile.db`

### Start all services

```bash
# Clone the repository
git clone https://github.com/kylodeng/underwriting_chatbot-main.git
cd underwriting_chatbot-main

# Create and populate .env file
cp .env.example .env   # [TODO: confirm .env.example exists]
# Edit .env with your API keys

# Build and start all containers
docker compose up --build

# Or run in detached mode
docker compose up --build -d
```

### Verify deployment

```bash
# Check backend health
curl http://localhost:8000/health
# Expected: {"status": "ok"}

# Check all containers are running
docker compose ps

# View logs
docker compose logs -f backend
docker compose logs -f frontend
```

### Access the application

- **Frontend (Chainlit UI)**: http://localhost:8080
- **Backend API**: http://localhost:8000
- **Redis**: localhost:6379
- **PostgreSQL**: localhost:5432

### Stop services

```bash
docker compose down

# To also remove persistent volumes (WARNING: deletes PostgreSQL data)
docker compose down -v
```

### GitHub Actions CI Tools setup

```bash
# Add the following secrets to your GitHub repository:
# Settings > Secrets and variables > Actions > New repository secret

ANTHROPIC_API_KEY=<your-anthropic-api-key>
GH_TOKEN=<github-pat-with-repo-read-write-access>
SENDGRID_API_KEY=<your-sendgrid-api-key>

# Ensure the ai-delivery-outputs repository exists under the same GitHub owner
```

### Trigger CI tools manually

```bash
# Via GitHub CLI
gh workflow run tool1_code_review.yml -f review_mode=repo
gh workflow run tool2_tech_docs.yml
gh workflow run tool3_business_docs.yml -f project_name="Underwriting Chatbot" -f release_version="1.0.0"
gh workflow run tool4_auto_testing.yml -f test_mode=generate
gh workflow run tool5_uat.