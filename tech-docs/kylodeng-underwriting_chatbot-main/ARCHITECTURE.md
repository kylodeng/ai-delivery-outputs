# Architecture Document — kylodeng/underwriting_chatbot-main

---

## 1. Overview

The Underwriting Chatbot is an AI-assisted insurance underwriting platform that enables underwriters to assess customer risk profiles through a conversational interface. A frontend chat UI communicates with a FastAPI backend, which orchestrates a multi-agent LLM pipeline (built on LangGraph and LangChain) to gather customer data, run parallel specialist assessments across domains (finance, health, life, KYC, etc.), and aggregate results into a structured `UnderwritingReport`. The system integrates a pre-trained CatBoost risk classification model, customer similarity lookups, and four SQLite databases containing customer profiles, application data, model predictions, and feature importance scores. A suite of five GitHub Actions AI-delivery workflows (code review, tech docs, business docs, auto-testing, UAT facilitation) supplements the platform by automating SDLC activities using Anthropic's Claude API.

---

## 2. Resources Deployed

| Resource | Type | Cloud Provider | Purpose |
|---|---|---|---|
| `backend` | Docker container (FastAPI, Python) | Local / self-hosted | Hosts the LLM orchestration layer, REST API, and underwriting assessment engine |
| `frontend` | Docker container | Local / self-hosted | Chat UI served on port 8080; communicates with backend via HTTP |
| `redis` (redis-stack-server:7.2.0-v14) | Docker container | Local / self-hosted | LangGraph checkpoint store for conversation session memory |
| `postgres` (postgres:16-alpine) | Docker container | Local / self-hosted | Chainlit session/user data persistence |
| `customer_profile.db` | SQLite file (read-only volume mount) | Local / self-hosted | Customer demographic and profile data |
| `feature_importance.db` | SQLite file (read-only volume mount) | Local / self-hosted | CatBoost model feature importance scores |
| `model_predictions.db` | SQLite file (read-only volume mount) | Local / self-hosted | Pre-computed CatBoost risk classification predictions |
| `application_profile.db` | SQLite file (read-only volume mount) | Local / self-hosted | Insurance application metadata |
| `customer_similarity_dict.json` | JSON file (in-memory / backend tmp) | Local / self-hosted | Pre-computed customer lookalike similarity index |
| Anthropic Claude API (claude-sonnet-4-20250514 / claude-haiku-4-5-20251001) | External managed API | Anthropic (cloud) | LLM inference for agent reasoning, specialist assessment, and aggregation |
| Google Gemini API (gemini-3-flash-preview) | External managed API | Google Cloud | Alternative LLM provider (configured but [TODO: verify active usage]) |
| GitHub Actions runners (ubuntu-latest) | Managed CI/CD compute | GitHub (cloud) | Execute AI-delivery workflow tools (code review, docs, testing, UAT) |
| `ai-delivery-outputs` (separate GitHub repo) | GitHub repository | GitHub (cloud) | Stores AI-generated artifacts: docs, test files, UAT packs, audit logs |
| SendGrid API | External email service | Twilio/SendGrid (cloud) | Sends notification emails on workflow completion |

---

## 3. Data Flow

### Runtime (Chat / Assessment) Flow

1. **User → Frontend**: Underwriter sends a natural-language message via the chat UI (port 8080).
2. **Frontend → Backend `/chat`**: Frontend POSTs a `ChatRequest` (message, session_id, model, temperature, mode) to the FastAPI backend (port 8000) over HTTP.
3. **Backend → LangGraph Agent**: `build_agent()` constructs a LangGraph agent with Redis-backed checkpointing. The agent retrieves prior conversation state from Redis using `thread_id = session_id`.
4. **Agent → Tool: `get_customer_profile`**: The agent invokes the profile tool, which queries the `customer_profile.db` and `application_profile.db` SQLite databases (read-only mounts) to retrieve structured customer data.
5. **Agent → Tool: `customer_lookalike`**: The agent optionally invokes the lookalike tool, which reads `customer_similarity_dict.json` to return similar customer IDs.
6. **Agent → Tool: `run_underwriting_assessment`**: The agent passes the customer profile string to the assessment tool.
7. **Assessment Engine → Parallel Specialist LLMs**: The assessment tool fans out up to `N` specialist calls (semaphore-limited to 4 concurrent) across assessment categories (finance, health, life, KYC, etc.) using the `claude-haiku-4-5-20251001` model with prompts from `assessment_criterias.json`.
8. **Specialist LLMs → Aggregator LLM**: Individual specialist responses are concatenated and sent to the aggregator (`claude-haiku-4-5-20251001` / `claude-sonnet-4-20250514`) with structured output binding, producing a typed `UnderwritingReport` JSON object.
9. **Assessment Tool → Agent**: The rendered report is returned to the LangGraph agent as a tool result.
10. **Agent → Backend**: The agent emits a final answer; intermediate streaming events (tool start/end, LLM tokens, thinking blocks) are streamed via Server-Sent Events (SSE).
11. **Backend → Frontend**: The FastAPI `/chat` endpoint streams SSE events (`tool_start`, `response`, `chart`, `done`) back to the frontend in real time.
12. **Redis Checkpoint**: After each turn, LangGraph serialises updated conversation state back to Redis for session continuity.

### CI/CD AI-Delivery Flow

1. A GitHub event (PR, push to main, tag, schedule, or `workflow_dispatch`) triggers one of the five workflow YAML files.
2. The workflow runner checks out the source repository and installs `anthropic` and `requests`.
3. The Python script reads source/IaC files from the repository via the GitHub API (authenticated with `GH_TOKEN`).
4. The script calls the Anthropic Claude API (`claude-sonnet-4-6` in `shared.py`) with a structured system prompt.
5. Claude returns generated content (review JSON, markdown docs, test files, UAT packs).
6. The script writes output files to the `ai-delivery-outputs` GitHub repository via the GitHub Contents API.
7. For PR reviews, the script posts a comment directly on the pull request.
8. SendGrid sends a notification email to `kylo.deng@capco.com` with a summary and link.
9. Artifacts (JSON reports) are uploaded to the GitHub Actions run as downloadable artifacts.

---

## 4. Security Posture

### What Is Secured

- **SQLite databases are mounted read-only** (`ro` flag in `docker-compose.yml`), preventing write access from the backend container.
- **API keys and secrets are stored in GitHub Actions Secrets** (`ANTHROPIC_API_KEY`, `GH_TOKEN`, `SENDGRID_API_KEY`) and injected as environment variables — not hardcoded in source.
- **System prompt confidentiality**: The agent is instructed never to reveal internal prompts or tool details to end users.
- **Semaphore-limited concurrency**: Specialist LLM calls are bounded at 4 concurrent requests, preventing runaway API consumption.
- **Assessment token caps**: `specialist_max_tokens: 1500` and `aggregator_max_tokens: 8000` limit runaway LLM output.

### Security Gaps — Explicit Callouts

- ❌ **No authentication or authorisation on the `/chat` API endpoint.** Any process that can reach port 8000 can query the system. There is no API key, OAuth, JWT, or mTLS protecting the backend.
- ❌ **CORS is fully open** (`allow_origins=["*"]`, `allow_methods=["*"]`, `allow_headers=["*"]`). This allows any origin to call the backend API — a significant risk if the backend is ever network-accessible beyond localhost.
- ❌ **PostgreSQL credentials are hardcoded in `docker-compose.yml`** (`POSTGRES_USER: chainlit`, `POSTGRES_PASSWORD: chainlit`). These are trivially guessable default credentials. No secrets management is used.
- ❌ **Redis has no authentication configured.** The Redis container exposes port 6379 with no password or ACL. Any process on the Docker network (or host if port is exposed) can read/write conversation checkpoint data, which may contain sensitive customer PII.
- ❌ **Redis port 6379 is published to the host** (`ports: - "6379:6379"`), making it accessible from outside the Docker network.
- ❌ **PostgreSQL port 5432 is published to the host** (`ports: - "5432:5432"`), same risk.
- ❌ **No encryption at rest** for SQLite databases, Redis data, or PostgreSQL data. Customer PII and underwriting data are stored unencrypted on disk.
- ❌ **No TLS/HTTPS** between frontend and backend, or between the client and frontend. All traffic is plaintext HTTP.
- ❌ **`GH_TOKEN` scope is unknown** — [TODO: verify the token has least-privilege scope (e.g., `repo` write only to `ai-delivery-outputs`, not organisation-wide admin)]. Overly broad tokens could allow repo deletion or secret exfiltration.
- ❌ **Customer similarity data (`customer_similarity_dict.json`) is stored in `backend/tmp/`** as a plaintext JSON file committed to the repository, potentially exposing customer IDs in version history.
- ❌ **No input sanitisation or prompt injection defence** on user messages before they are forwarded to the LLM.
- ❌ **No rate limiting** on the `/chat` endpoint — the API is open to abuse.
- ❌ **`load_dotenv` in production code** (`main.py`, `LLMS.py`) — `.env` files should not be the credential mechanism in containerised production deployments.
- ⚠️ **`GOOGLE_API_KEY`** is referenced in `LLMS.py` but does not appear in `docker-compose.yml` env or documented secrets — [TODO: confirm whether Gemini is in active use and where the key is set].

---

## 5. Environment Variables and Secrets

| Name | Required | Sensitivity | Where Set |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | **High** — LLM API key | GitHub Actions Secret; `.env` file for local backend |
| `GH_TOKEN` | Yes (CI/CD) | **High** — GitHub PAT with repo write access | GitHub Actions Secret |
| `SENDGRID_API_KEY` | Yes (CI/CD) | **High** — email API key | GitHub Actions Secret |
| `GOOGLE_API_KEY` | Conditional | **High** — GCP API key | `.env` file; [TODO: not documented in docker-compose or CI secrets] |
| `REDIS_HOST` | No | Low | `docker-compose.yml` environment block (`redis`) |
| `POSTGRES_USER` | Yes | Medium | Hardcoded in `docker-compose.yml` (`chainlit`) — **should use secret** |
| `POSTGRES_PASSWORD` | Yes | **High** | Hardcoded in `docker-compose.yml` (`chainlit`) — **must be rotated and secretised** |
| `POSTGRES_DB` | No | Low | Hardcoded in `docker-compose.yml` (`chainlit`) |
| `DATABASE_URL` | Yes (frontend) | Medium | `docker-compose.yml` environment block (contains credentials inline) |
| `BACKEND_URL` | Yes (frontend) | Low | `docker-compose.yml` environment block |
| `OUTPUT_REPO` | No | Low | GitHub Actions env (`ai-delivery-outputs`) |
| `OUTPUT_REPO_OWNER` | No | Low | GitHub Actions env (`github.repository_owner`) |
| `NOTIFY_EMAIL` | No | Low | GitHub Actions env (`kylo.deng@capco.com`) |
| `SENDER_EMAIL` | No | Low | GitHub Actions env (`noreply@ai-delivery.capco.com`) |
| `REVIEW_MODE` | No | Low | Set dynamically in CI workflow step |
| `PR_NUMBER` | No | Low | Set dynamically in CI workflow step |
| `RELEASE_VERSION` | No | Low | Set dynamically in CI workflow step |
| `PROJECT_NAME` | No | Low | Set dynamically in CI workflow step |
| `UAT_MODE` | No | Low | Set dynamically in CI workflow step |
| `TEST_MODE` | No | Low | Set dynamically in CI workflow step |

---

## 6. Dependencies

| Dependency | Type | Purpose | Notes |
|---|---|---|---|
| Anthropic Claude API | External API | Core LLM inference for chat agent, specialist assessment, aggregation, and all CI/CD AI tools | Models: `claude-sonnet-4-20250514`, `claude-haiku-4-5-20251001`, `claude-sonnet-4-6` |
| Google Gemini API | External API | Alternative LLM provider | `gemini-3-flash-preview`; [TODO: confirm whether actively used in production paths] |
| SendGrid API | External email service | Workflow completion notifications | Used in all 5 CI/CD tools |
| GitHub API (`api.github.com`) | External API | Reading source files, writing output files, posting PR comments | Authenticated via `GH_TOKEN` |
| `ai-delivery-outputs` (GitHub repo) | External repository | Storage for AI-generated docs, test files, UAT packs, audit logs | Must exist under same owner; [TODO: confirm repo creation and branch protection] |
| LangChain / LangGraph | Python library | Agent orchestration, tool calling, streaming | Core runtime framework |
| LangChain Anthropic | Python library | Anthropic LLM integration | `langchain_anthropic` |
| LangChain Google GenAI | Python library | Google Gemini integration | `langchain_google_genai` |
| LangGraph Redis checkpointer (`AsyncRedisSaver`) | Python library | Persistent conversation state | Requires Redis 7.x with RedisStack |
| CatBoost model | Local ML model file | Risk classification (`Risk_Classification` target) | Pre-trained; model file path [TODO: not explicitly shown — confirm location in container] |
| Redis Stack Server 7.2.0 | Docker image | Session memory / LangGraph checkpointing | |
| PostgreSQL 16 | Docker image | Chainlit session persistence | |
| FastAPI + SSE-Starlette | Python library | REST API and streaming event source | |
| Pydantic | Python library | Structured output validation (`UnderwritingReport`) | |
| `anthropic` (direct SDK) | Python library | Used in CI/CD scripts (`shared.py`) independently of LangChain | |

---

## 7. Deployment Instructions

### Prerequisites

- Docker and Docker Compose v2 installed
- `.env` file created in the project root containing at minimum:

```bash
ANTHROPIC_API_KEY=sk-ant-...
GOOGLE_API_KEY=...          # if Gemini is used
```

### Local Deployment

```bash
# 1. Clone the repository
git clone https://github.com/kylodeng/underwriting_chatbot-main.git
cd underwriting_chatbot-main

# 2. Create the environment file
cp .env.example .env        # [TODO: confirm .env.example exists]
# Edit .env and populate ANTHROPIC_API_KEY and GOOGLE_API_KEY

# 3. Ensure SQLite databases exist in ./database/
# [TODO: document how to obtain or generate the four .db files]
# Required files:
#   ./database/customer_profile.db
#   ./database/feature_importance.db
#   ./database/model_predictions.db
#   ./database/application_profile.db

# 4. Build and start all services
docker compose up --build

# 5. Verify backend health
curl http://localhost:8000/health
# Expected: {"status": "ok"}

# 6. Access the frontend
open http://localhost:8080
```

### Stopping and Cleanup

```bash
# Stop all services
docker compose down

# Stop and remove volumes (WARNING: deletes PostgreSQL data)
docker compose down -v
```

### Triggering CI/CD AI-Delivery Workflows

```bash
# Tool 1 — Manual code review of entire repo
gh workflow run tool1_code_review.yml \
  -f review_mode=repo

# Tool 1 — Review a specific PR
gh workflow run tool1_code_review.yml \
  -f review_mode=pr \
  -f pr_number=42

# Tool 2 — Generate tech documentation (also triggers on push to main)
gh