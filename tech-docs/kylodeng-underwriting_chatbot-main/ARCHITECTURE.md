# Architecture Document: kylodeng/underwriting_chatbot-main

---

## 1. Overview

The Underwriting Chatbot is an AI-assisted insurance underwriting platform that enables underwriters to assess customer risk profiles through a conversational interface. A frontend chat UI communicates with a FastAPI backend that orchestrates a multi-agent LangGraph pipeline: a routing agent interprets underwriter queries, invokes tools to fetch customer data from local SQLite databases, runs parallel specialist LLM assessments across risk domains (finance, health, life, KYC, etc.), and aggregates the results into a structured `UnderwritingReport`. The system is augmented by five GitHub Actions CI/CD AI tooling workflows (code review, tech docs, business docs, auto testing, UAT facilitation) that use Claude via the Anthropic API to automate delivery-quality gates and documentation. The platform targets life insurance underwriting use cases and uses a pre-trained CatBoost classifier model alongside LLM-generated assessments.

---

## 2. Resources Deployed

| Resource | Type | Cloud Provider | Purpose |
|---|---|---|---|
| FastAPI Backend | Application Service (Docker container) | Local / [TODO: cloud host?] | Serves `/chat` SSE endpoint and `/health`; orchestrates agent pipeline |
| Chainlit Frontend | Web Application (Docker container) | Local / [TODO: cloud host?] | Underwriter chat UI |
| Redis Stack Server 7.2 | In-memory data store (Docker container) | Local / [TODO: cloud host?] | LangGraph conversation checkpoint persistence across turns |
| PostgreSQL 16 | Relational database (Docker container) | Local / [TODO: cloud host?] | Chainlit session/user data storage |
| `customer_profile.db` | SQLite file (read-only volume mount) | Local filesystem | Customer profile records |
| `feature_importance.db` | SQLite file (read-only volume mount) | Local filesystem | ML model feature importance data |
| `model_predictions.db` | SQLite file (read-only volume mount) | Local filesystem | CatBoost model prediction outputs |
| `application_profile.db` | SQLite file (read-only volume mount) | Local filesystem | Insurance application profiles |
| `customer_similarity_dict.json` | JSON file (backend/tmp) | Local filesystem | Pre-computed customer lookalike similarity index |
| CatBoost Classifier | ML Model (v1.0, deployed 2024-06-01) | Local / in-process | Risk classification (`Preferred`/`Standard Plus`/`Standard`/`Substandard`) |
| Anthropic Claude (claude-sonnet-4-20250514) | External LLM API | Anthropic Cloud | Deep specialist assessment aggregation |
| Anthropic Claude Haiku (claude-haiku-4-5-20251001) | External LLM API | Anthropic Cloud | Fast agent routing and specialist assessments |
| Google Gemini (gemini-3-flash-preview) | External LLM API | Google Cloud | Alternative LLM provider (configured, availability unconfirmed) |
| GitHub Actions Runners (ubuntu-latest) | CI/CD Compute | GitHub Cloud | AI tooling workflows (code review, docs, testing, UAT) |
| `ai-delivery-outputs` (external repo) | GitHub Repository | GitHub Cloud | Stores generated docs, test files, audit logs from CI workflows |
| SendGrid | Email delivery API | Twilio/SendGrid Cloud | Notification emails for CI workflow completions |

---

## 3. Data Flow

### Runtime Chat Flow

1. **Underwriter** types a message in the Chainlit **frontend** (port 8080); the frontend POSTs to `http://backend:8000/chat` with `{message, session_id, model, mode, temperature}`.
2. **FastAPI backend** (`/chat`) builds a LangGraph agent via `build_agent()`, configuring it with the requested LLM (`anthropic-fast` by default) and a Redis-backed `AsyncRedisSaver` checkpointer keyed on `session_id`.
3. The **routing agent** LLM receives a system prompt (loaded from `prompts.py` + model card JSON) plus conversation history retrieved from **Redis**, then produces either a tool-call JSON or a final answer.
4. If the agent calls **`get_customer_profile`**, the tool queries the local **SQLite databases** (`customer_profile.db`, `application_profile.db`) mounted read-only into the backend container and returns structured profile data.
5. If the agent calls **`customer_lookalike`**, the tool reads `backend/tmp/customer_similarity_dict.json` and returns a list of similar customer IDs.
6. If the agent calls **`run_underwriting_assessment`**, the assessment module spawns up to **4 concurrent specialist LLM calls** (semaphore-controlled) to the **Anthropic API**, each evaluating a different risk domain (finance, health, life, KYC, etc.) using prompts from `assessment_criterias.json`.
7. Specialist LLM responses are collected and passed to an **aggregator LLM call** (also Anthropic), which uses structured output (`with_structured_output`) to produce a validated `UnderwritingReport` Pydantic model.
8. The backend streams **Server-Sent Events (SSE)** back to the frontend: `tool_start`, `tool_end`, `response` (streamed text chunks), and buffered `chart` events for visualisations.
9. The **CatBoost model** predictions stored in `model_predictions.db` are referenced during assessment; the model itself appears to run offline (predictions pre-computed).
10. Conversation state is **checkpointed to Redis** after each turn so context persists across subsequent messages in the same session.
11. Frontend renders the streamed response and any chart payloads to the underwriter.

### CI/CD AI Tooling Flow

1. A GitHub event (PR open, push to main, version tag, schedule, or manual dispatch) triggers one of five **GitHub Actions workflows**.
2. The workflow checks out the source repo, installs `anthropic` and `requests`, then runs the corresponding Python script from `.github/scripts/`.
3. The script calls `get_repo_files()` or `get_pr_diff()` via the **GitHub REST API** (authenticated with `GH_TOKEN`) to retrieve source or diff content.
4. Content is sent to **Claude** (via `ANTHROPIC_API_KEY`) with a structured prompt; the response is parsed and validated.
5. Output documents or JSON artifacts are written to the **`ai-delivery-outputs`** GitHub repository via the GitHub Contents API.
6. A notification email is sent via **SendGrid** to `kylo.deng@capco.com`.
7. For code review workflows, a **PR comment** is also posted back to the source PR.

---

## 4. Security Posture

### What Is Secured

- **SQLite databases** are mounted **read-only** (`ro`) into the backend container, preventing writes from a compromised backend process.
- **GitHub secrets** (`ANTHROPIC_API_KEY`, `GH_TOKEN`, `SENDGRID_API_KEY`) are stored in GitHub Actions secrets and injected as environment variables — not hardcoded in workflow files.
- **Redis checkpointer** is internal to the Docker network (not exposed externally beyond the mapped port 6379).
- **PostgreSQL** data is persisted in a named Docker volume (`postgres_data`), surviving container restarts.
- **Backend health check** is defined, preventing the frontend from receiving requests before the backend is ready.

### Security Gaps — Explicit Callouts

- 🔴 **CORS is fully open**: `allow_origins=["*"]`, `allow_methods=["*"]`, `allow_headers=["*"]` — any origin can call the `/chat` endpoint. There is **no authentication or authorization** on the FastAPI backend. Any user with network access can submit chat requests.
- 🔴 **No encryption at rest**: SQLite files (`customer_profile.db`, etc.) containing customer PII and insurance data are stored as unencrypted files on the host filesystem. No encryption is configured.
- 🔴 **No encryption in transit (internal)**: Docker Compose service-to-service communication (frontend→backend, backend→Redis, backend→Postgres) uses plain HTTP/TCP with no TLS. Redis has no password configured.
- 🔴 **PostgreSQL credentials are hardcoded** in `docker-compose.yml` (`POSTGRES_USER: chainlit`, `POSTGRES_PASSWORD: chainlit`). These are trivially guessable.
- 🔴 **Redis has no authentication**: The Redis Stack container has no `requirepass` configuration or ACL rules. Any process on the Docker network can read/write all LangGraph checkpoints.
- 🔴 **Sensitive customer data sent to third-party LLMs**: Customer PII (age, income, medical conditions, nationality, smoker status) is included in prompts sent to the Anthropic API and potentially Google Gemini. There is no evidence of data masking or anonymisation before transmission.
- 🔴 **`customer_similarity_dict.json` is stored in `backend/tmp/`**: A pre-computed index of customer similarity relationships is committed to the repository. This may expose customer ID relationships.
- 🟡 **`GH_TOKEN` scope is unknown**: The GitHub token used by CI workflows writes to an external repo and reads PR diffs. [TODO: confirm the token has minimum required scopes — it should be `repo`-scoped only for the output repo, not admin or org-wide.]
- 🟡 **No input validation on `/chat`**: The `message` field is passed directly to the LLM with no sanitisation. Prompt injection attacks are a risk.
- 🟡 **`FORCE_JAVASCRIPT_ACTIONS_TO_NODE24: true`**: This forces all JS actions to Node 24, which may cause unexpected behaviour with pinned action versions.
- 🟡 **No rate limiting** on the `/chat` endpoint.
- 🟡 **Assessment prompt content in `assessment_criterias.json`** is committed to the repository, exposing underwriting logic to anyone with repo access.
- 🟡 **Google Gemini model name** (`gemini-3-flash-preview`) appears incorrect or experimental — [TODO: verify this is a valid, stable model identifier].

---

## 5. Environment Variables and Secrets

| Name | Required | Sensitivity | Where Set |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | 🔴 High — API key for paid LLM service | GitHub Actions secret; `.env` file for local backend |
| `GH_TOKEN` | Yes | 🔴 High — GitHub PAT with repo write access | GitHub Actions secret |
| `SENDGRID_API_KEY` | Yes | 🔴 High — Email service API key | GitHub Actions secret |
| `GOOGLE_API_KEY` | Yes (if using Gemini) | 🔴 High — GCP API key | `.env` file for local backend |
| `REDIS_HOST` | No | 🟢 Low | `docker-compose.yml` env; defaults to `localhost` |
| `POSTGRES_USER` | Yes | 🟡 Medium | `docker-compose.yml` (hardcoded: `chainlit`) |
| `POSTGRES_PASSWORD` | Yes | 🔴 High — hardcoded in compose file | `docker-compose.yml` (hardcoded: `chainlit`) |
| `POSTGRES_DB` | No | 🟢 Low | `docker-compose.yml` (hardcoded: `chainlit`) |
| `DATABASE_URL` | Yes | 🟡 Medium | `docker-compose.yml` frontend environment |
| `BACKEND_URL` | Yes | 🟢 Low | `docker-compose.yml` frontend environment |
| `OUTPUT_REPO` | No | 🟢 Low | GitHub Actions env (default: `ai-delivery-outputs`) |
| `OUTPUT_REPO_OWNER` | No | 🟢 Low | GitHub Actions env (derived from `github.repository_owner`) |
| `NOTIFY_EMAIL` | No | 🟢 Low | GitHub Actions env (hardcoded: `kylo.deng@capco.com`) |
| `SENDER_EMAIL` | No | 🟢 Low | GitHub Actions env (hardcoded: `noreply@ai-delivery.capco.com`) |
| `TEST_MODE` | No | 🟢 Low | GitHub Actions env (default: `generate`) |

> [TODO: Confirm whether a `.env` file template exists for local development — it is referenced by `docker-compose.yml` (`env_file: .env`) but not present in the repo.]

---

## 6. Dependencies

| Dependency | Type | Used By | Notes |
|---|---|---|---|
| Anthropic API (Claude Sonnet 4, Claude Haiku 4.5) | External LLM API | Backend assessment engine; all 5 CI workflows | Primary LLM provider; paid per-token |
| Google Gemini API (gemini-3-flash-preview) | External LLM API | Backend (`LLMS.py`) | Configured but [TODO: verify model name is valid] |
| LangGraph | Python library | Backend agent pipeline | Provides `StateGraph`, `AsyncRedisSaver` checkpointing |
| LangChain | Python library | Backend | Tool wrapping, agent creation, LLM adapters |
| Redis Stack Server 7.2 | Infrastructure | Backend | LangGraph conversation state persistence |
| PostgreSQL 16 | Infrastructure | Frontend (Chainlit) | Session and user data |
| Chainlit | Python/UI framework | Frontend | Chat UI rendering, session management |
| FastAPI + sse-starlette | Python library | Backend | HTTP server, SSE streaming |
| Pydantic | Python library | Backend | `UnderwritingReport` model validation |
| CatBoost (pre-trained) | ML model | Backend (`model_predictions.db`) | Risk classification; predictions appear pre-computed |
| SQLite databases (4 files) | Data files | Backend | Customer, application, feature importance, predictions |
| `customer_similarity_dict.json` | Data file | Backend (`customer_lookalike` tool) | Pre-computed similarity index |
| `assessment_criterias.json` | Configuration | Backend assessment | Specialist LLM prompt templates |
| `model_card.json` | Configuration | Backend agent prompts | Model metadata injected into system prompt |
| GitHub REST API (api.github.com) | External API | All 5 CI workflow scripts | Reads source files, posts PR comments, writes to output repo |
| SendGrid API | External email API | All 5 CI workflow scripts | Workflow completion notifications |
| `ai-delivery-outputs` (separate GitHub repo) | External GitHub repo | All 5 CI workflow scripts | Stores generated documentation and artifacts |
| `anthropic` Python package | PyPI | CI scripts | Claude API client |
| `requests` Python package | PyPI | CI scripts | GitHub and SendGrid API calls |
| `python-dotenv` | PyPI | Backend | `.env` file loading |
| `langchain-google-genai` | PyPI | Backend | Google Gemini adapter |
| `langchain-anthropic` | PyPI | Backend | Anthropic Claude adapter |
| `PyYAML` | PyPI | Backend | `config.yml` parsing |

---

## 7. Deployment Instructions

### Prerequisites

- Docker and Docker Compose installed
- `.env` file created at repo root (see Environment Variables section)
- SQLite database files present in `./database/` directory

### Local Deployment

```bash
# 1. Clone the repository
git clone https://github.com/kylodeng/underwriting_chatbot-main.git
cd underwriting_chatbot-main

# 2. Create .env file with required secrets
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
# Open http://localhost:8080 in your browser
```

### Individual Service Commands

```bash
# Start services in detached mode
docker compose up -d

# View logs for a specific service
docker compose logs -f backend
docker compose logs -f frontend

# Restart a single service
docker compose restart backend

# Stop all services
docker compose down

# Stop and remove volumes (WARNING: destroys PostgreSQL data)
docker compose down -v

# Rebuild a specific service after code changes
docker compose up --build backend
```

### CI/CD Workflows (GitHub Actions)

```bash
# Trigger code review manually (repo mode)
gh workflow run tool1_code_review.yml -f review_mode=repo

# Trigger code review for a specific PR
gh workflow run tool1_code_review.yml -f review