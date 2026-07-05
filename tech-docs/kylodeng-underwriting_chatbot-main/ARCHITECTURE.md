# Architecture Document: kylodeng/underwriting_chatbot-main

---

## 1. Overview

The Underwriting Chatbot is an AI-assisted insurance underwriting platform that enables underwriters to assess customer risk profiles through a conversational interface. The backend exposes a FastAPI streaming endpoint that orchestrates a LangGraph-based multi-agent pipeline: a routing agent determines which tools to invoke (customer profile lookup, customer lookalike search, or full risk assessment), and a parallel specialist assessment system fans out across multiple domain categories (finance, health, life, etc.) using Anthropic Claude LLMs before aggregating results into a structured `UnderwritingReport`. The system persists conversation state in Redis and application data in SQLite databases, with a Chainlit-based frontend providing the chat UI. A suite of five GitHub Actions CI/CD workflows layer on AI-powered code review, technical documentation generation, business documentation, automated test generation, and UAT facilitation — all powered by Claude and publishing outputs to a shared `ai-delivery-outputs` repository.

---

## 2. Resources Deployed

| Resource | Type | Cloud Provider | Purpose |
|---|---|---|---|
| `backend` | Docker container (FastAPI) | Self-hosted / Docker Compose | Core API server; LangGraph agent orchestration; SSE streaming |
| `frontend` | Docker container (Chainlit) | Self-hosted / Docker Compose | Chat UI served to underwriters |
| `redis` (redis-stack-server:7.2.0-v14) | Docker container | Self-hosted / Docker Compose | LangGraph conversation state checkpointing |
| `postgres` (postgres:16-alpine) | Docker container | Self-hosted / Docker Compose | Chainlit persistent storage (auth, threads) |
| `customer_profile.db` | SQLite file (read-only mount) | Self-hosted | Customer profile data |
| `feature_importance.db` | SQLite file (read-only mount) | Self-hosted | ML model feature importance scores |
| `model_predictions.db` | SQLite file (read-only mount) | Self-hosted | CatBoost model risk classification predictions |
| `application_profile.db` | SQLite file (read-only mount) | Self-hosted | Insurance application data |
| `postgres_data` | Docker named volume | Self-hosted | PostgreSQL persistent storage |
| Anthropic Claude (claude-sonnet-4-20250514) | External LLM API | Anthropic | Deep/aggregator underwriting assessments |
| Anthropic Claude (claude-haiku-4-5-20251001) | External LLM API | Anthropic | Fast agent routing; specialist assessments |
| Google Gemini (gemini-3-flash-preview) | External LLM API | Google Cloud | Alternative LLM provider (configured, optional) |
| `ai-delivery-outputs` | GitHub Repository | GitHub | Destination for AI-generated docs, test files, UAT packs |
| GitHub Actions runners (ubuntu-latest) | CI/CD compute | GitHub | Five AI workflow tools (code review, docs, testing, UAT) |
| SendGrid | Email API | Twilio/SendGrid | Notification delivery for CI/CD workflow outputs |

---

## 3. Data Flow

### Runtime Chat Flow

1. **User sends message** via the Chainlit frontend (`http://localhost:8080`). Frontend forwards the request as an HTTP POST to `http://backend:8000/chat` with `message`, `session_id`, `model`, `temperature`, and `mode` fields.
2. **FastAPI backend** receives the `ChatRequest` and instantiates a LangGraph agent via `build_agent()`, attaching a Redis-backed `AsyncRedisSaver` checkpointer keyed by `session_id` for conversation continuity.
3. **Agent (routing LLM)** — Claude Haiku by default — receives the user message plus conversation history from Redis. It decides which tool to call by emitting a JSON action block (`tool_call` or `done`).
4. **Tool dispatch** (one at a time, sequential):
   - `get_customer_profile`: Queries `customer_profile.db` SQLite and returns structured customer data.
   - `customer_lookalike`: Reads `backend/tmp/customer_similarity_dict.json` to find similar customer IDs; fetches their profiles from SQLite.
   - `run_underwriting_assessment`: Receives the customer profile string and triggers the specialist pipeline (step 5).
5. **Specialist assessment fan-out**: `_run_underwriting_assessment()` launches parallel async calls (semaphore-limited to 4 concurrent) to the tagged specialist LLM for each domain category (finance, health, life, etc.) defined in `assessment_criterias.json`. Each specialist returns a domain-specific findings block.
6. **Aggregation**: All specialist results are combined and sent to the aggregator LLM (Claude Sonnet with structured output), which produces a typed `UnderwritingReport` Pydantic object containing risk class, findings, follow-up items, and data gaps.
7. **SSE streaming response**: The backend streams Server-Sent Events back to the frontend throughout the process — `tool_start`, `tool_end`, `thinking` (specialist tokens), `response` (agent answer tokens), and `chart` (feature importance/prediction data from SQLite DBs). Charts are deduplicated per `(session_id, field)` pair.
8. **Frontend renders** streaming tokens in real time, displaying the underwriting report, charts, and follow-up items to the underwriter.

### CI/CD Workflow Data Flow

9. **GitHub event** (PR, push to main, version tag, schedule, or manual dispatch) triggers one of five GitHub Actions workflows.
10. **Workflow script** fetches source files from the triggering repository via GitHub API and passes them to Claude (Sonnet) for analysis.
11. **Claude output** (review JSON, markdown docs, test files, UAT pack) is written to the `ai-delivery-outputs` GitHub repository via GitHub Contents API.
12. **SendGrid** delivers an email notification with links to the generated artefacts to `kylo.deng@capco.com`.

---

## 4. Security Posture

### What Is Secured

- **Secrets management**: All API keys (`ANTHROPIC_API_KEY`, `GH_TOKEN`, `SENDGRID_API_KEY`, `GOOGLE_API_KEY`) are stored as GitHub Actions secrets and injected via environment variables; not hardcoded in source.
- **SQLite databases are mounted read-only** (`ro` flag in `docker-compose.yml`), preventing runtime writes to customer data.
- **Agent system prompt concealment**: The agent is explicitly instructed never to reveal internal system instructions or tool names to end users.
- **Specialist LLM token cap**: `specialist_max_tokens: 1500` prevents runaway output and limits cost exposure per assessment call.
- **Semaphore on parallel LLM calls**: Limits concurrent specialist calls to 4, providing some rate-limit and cost protection.

### Gaps and Weaknesses ⚠️

- **No authentication or authorisation on the FastAPI backend**: `allow_origins=["*"]`, `allow_methods=["*"]`, `allow_headers=["*"]` — the CORS policy is completely open. Any client on the network can call `/chat` with any `session_id`. There is no API key, JWT, OAuth, or IP allowlist protecting the backend endpoint.
- **PostgreSQL credentials are hardcoded in `docker-compose.yml`**: `POSTGRES_USER: chainlit`, `POSTGRES_PASSWORD: chainlit`. These are default/trivial credentials stored in plain text in version control.
- **No encryption at rest**: SQLite databases, the `customer_similarity_dict.json`, and the PostgreSQL `postgres_data` volume are all unencrypted at rest. Customer PII and financial data are stored without encryption.
- **No encryption in transit between containers**: Docker Compose internal networking uses plain HTTP between frontend→backend and backend→Redis/Postgres. There is no TLS on internal service communication.
- **Redis has no authentication**: The Redis container is deployed with no password, no TLS, and port 6379 exposed to the host. Conversation state (which may contain customer data) is unprotected.
- **Port 6379 and 5432 exposed to the host**: Both Redis and PostgreSQL ports are mapped to `0.0.0.0` on the host, making them accessible to any process or network peer that can reach the host.
- **Customer PII in LLM prompts**: Full customer profiles (age, income, medical conditions, nationality, smoker status) are passed as plain text to external Anthropic and Google APIs. No anonymisation or PII redaction is performed before sending data to third-party AI providers.
- **`customer_similarity_dict.json` stored in `backend/tmp/`**: A precomputed similarity index for ~10,000 customers is committed directly to the repository. If the repo is public or the container image is inspected, this data is exposed.
- **`model_card.json` in repository**: Contains model metadata including feature importance weights, which reveals which customer attributes (age, medical conditions, smoker status) are most heavily weighted in risk decisions — potential regulatory/fairness disclosure concern.
- **GitHub Actions `GH_TOKEN` scope unknown**: [TODO: What permissions does the `GH_TOKEN` secret have? If it has `repo` write scope across the organisation, a compromised workflow could exfiltrate or modify other repositories.]
- **No input validation on `session_id`**: The `session_id` field from the chat request is passed directly to Redis as a thread key without sanitisation. Malicious session ID values could potentially cause key collisions or injection issues.
- **`.env` file loaded at runtime**: `load_dotenv()` is called in both `main.py` and `LLMS.py`. If the `.env` file is accidentally committed or left in the Docker build context, secrets are exposed in the image layer.

---

## 5. Environment Variables and Secrets

| Name | Required | Sensitivity | Where Set |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | 🔴 High — billable API key | GitHub Actions secret; `.env` file for backend |
| `GOOGLE_API_KEY` | Yes (if using Gemini) | 🔴 High — billable API key | `.env` file for backend |
| `GH_TOKEN` | Yes (CI workflows) | 🔴 High — GitHub repo access | GitHub Actions secret |
| `SENDGRID_API_KEY` | Yes (CI workflows) | 🔴 High — email sending capability | GitHub Actions secret |
| `POSTGRES_USER` | Yes | 🟡 Medium | `docker-compose.yml` (hardcoded: `chainlit`) |
| `POSTGRES_PASSWORD` | Yes | 🔴 High — database credential | `docker-compose.yml` (hardcoded: `chainlit`) ⚠️ |
| `POSTGRES_DB` | Yes | 🟢 Low | `docker-compose.yml` (hardcoded: `chainlit`) |
| `REDIS_HOST` | No | 🟢 Low | `docker-compose.yml` environment; defaults to `localhost` |
| `DATABASE_URL` | Yes (frontend) | 🟡 Medium — contains credentials | `docker-compose.yml` (hardcoded with password) ⚠️ |
| `BACKEND_URL` | Yes (frontend) | 🟢 Low | `docker-compose.yml` |
| `OUTPUT_REPO` | No (CI) | 🟢 Low | GitHub Actions workflow env (default: `ai-delivery-outputs`) |
| `OUTPUT_REPO_OWNER` | No (CI) | 🟢 Low | GitHub Actions workflow env |
| `NOTIFY_EMAIL` | No (CI) | 🟡 Medium — PII | GitHub Actions workflow env (hardcoded: `kylo.deng@capco.com`) |
| `SENDER_EMAIL` | No (CI) | 🟢 Low | GitHub Actions workflow env |
| `REVIEW_MODE` | No (CI Tool 1) | 🟢 Low | Set dynamically in workflow |
| `PR_NUMBER` | No (CI Tool 1) | 🟢 Low | Set dynamically in workflow |
| `RELEASE_VERSION` | No (CI Tools 3/5) | 🟢 Low | Set dynamically in workflow |
| `PROJECT_NAME` | No (CI Tools 3/5) | 🟢 Low | Set dynamically in workflow |
| `TEST_MODE` | No (CI Tool 4) | 🟢 Low | GitHub Actions workflow env |
| `UAT_MODE` | No (CI Tool 5) | 🟢 Low | Set dynamically in workflow |
| `USER_STORIES` | No (CI Tool 5) | 🟢 Low | Workflow dispatch input |
| `UAT_RESULTS_PATH` | No (CI Tool 5) | 🟢 Low | Workflow dispatch input |

[TODO: Are `ANTHROPIC_API_KEY` and `GOOGLE_API_KEY` the same secret used for both CI and runtime backend, or separate credentials with scoped permissions?]

---

## 6. Dependencies

| Dependency | Type | Purpose | Notes |
|---|---|---|---|
| Anthropic Claude API (claude-sonnet-4-20250514, claude-haiku-4-5-20251001) | External LLM API | Agent routing, specialist assessment, aggregation, all CI AI tools | Billable per token; no fallback configured |
| Google Gemini API (gemini-3-flash-preview) | External LLM API | Alternative LLM provider | Configured but appears optional; `azure` and `openai` stubs present but `None` |
| LangGraph / LangChain | Python library | Agent graph orchestration, tool binding, streaming | Core runtime dependency |
| Redis (redis-stack-server:7.2.0-v14) | Infrastructure | LangGraph `AsyncRedisSaver` conversation checkpointing | Local Docker container; see TODO in `graph.py` re: external service |
| PostgreSQL 16 | Infrastructure | Chainlit persistence (threads, auth) | Local Docker container |
| Chainlit | Frontend framework | Chat UI | [TODO: Exact version not visible in provided files] |
| FastAPI + SSE-Starlette | Python library | Backend API server with streaming | |
| CatBoost (pre-trained model) | ML model | Risk classification (`model_predictions.db`) | Model v1.0, deployed 2024-06-01; not retrained at runtime |
| SendGrid API | External email API | CI workflow notification emails | Used only in GitHub Actions workflows |
| GitHub API (api.github.com) | External API | CI workflow: reading repo files, writing outputs, posting PR comments | Requires `GH_TOKEN` with repo permissions |
| `ai-delivery-outputs` (GitHub repo) | External repository | Destination for all CI-generated artefacts | Must exist and `GH_TOKEN` must have write access |
| `kylodeng/ai-delivery-outputs` | Sibling GitHub repo | Output storage for all 5 CI tools | Separate repo from main application |
| Pydantic v2 | Python library | Structured output validation (`UnderwritingReport`) | |
| `python-dotenv` | Python library | `.env` file loading in backend | |

---

## 7. Deployment Instructions

### Prerequisites
- Docker and Docker Compose v2+ installed
- `.env` file created in the project root with required secrets (see Section 5)
- SQLite database files present in `./database/` directory:
  - `customer_profile.db`
  - `feature_importance.db`
  - `model_predictions.db`
  - `application_profile.db`
- `postgres/init.sql` present for database initialisation

### Local Deployment

```bash
# 1. Clone the repository
git clone https://github.com/kylodeng/underwriting_chatbot-main.git
cd underwriting_chatbot-main

# 2. Create the .env file with required secrets
cat > .env << 'EOF'
ANTHROPIC_API_KEY=your_anthropic_api_key_here
GOOGLE_API_KEY=your_google_api_key_here
EOF

# 3. Build and start all services
docker compose up --build -d

# 4. Verify services are healthy
docker compose ps
docker compose logs backend --tail=50

# 5. Confirm backend health check passes
curl http://localhost:8000/health

# 6. Access the frontend
open http://localhost:8080
```

### Service URLs (Local)
- **Frontend (Chainlit UI)**: `http://localhost:8080`
- **Backend API**: `http://localhost:8000`
- **Backend health check**: `http://localhost:8000/health`
- **Redis**: `localhost:6379`
- **PostgreSQL**: `localhost:5432`

### Stopping Services

```bash
docker compose down

# To also remove persistent volumes (WARNING: destroys PostgreSQL data)
docker compose down -v
```

### CI