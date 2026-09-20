# Architecture Document: kylodeng/underwriting_chatbot-main

---

## 1. Overview

The Underwriting Chatbot is an AI-powered insurance underwriting assistant that enables underwriters to assess customer risk profiles through a conversational interface. The system ingests structured customer data from SQLite databases (customer profiles, financial data, KYC, application profiles, and ML model predictions), runs multi-specialist LLM-based underwriting assessments via a LangGraph agent orchestration layer, and streams results back to a Chainlit-based frontend. A parallel CI/CD automation suite uses Claude (Anthropic) to continuously generate code reviews, technical documentation, business documentation, automated tests, and UAT test packs on every pull request, merge, or release event. The system runs locally via Docker Compose and has no declared cloud infrastructure.

---

## 2. Resources Deployed

| Resource | Type | Cloud Provider | Purpose |
|---|---|---|---|
| `backend` | Docker container (FastAPI, Python 3.x) | Local / [TODO: target cloud?] | Hosts the LangGraph agent, LLM orchestration, assessment engine, and SSE streaming API |
| `frontend` | Docker container (Chainlit) | Local / [TODO: target cloud?] | Chat UI for underwriters to interact with the agent |
| `redis` (redis-stack-server:7.2.0-v14) | Docker container | Local | LangGraph conversation checkpoint store (session memory) |
| `postgres` (postgres:16-alpine) | Docker container | Local | Chainlit session/user state persistence |
| `customer_profile.db` | SQLite file (read-only mount) | Local filesystem | Customer demographic and profile data |
| `feature_importance.db` | SQLite file (read-only mount) | Local filesystem | ML model feature importance scores |
| `model_predictions.db` | SQLite file (read-only mount) | Local filesystem | CatBoostClassifier risk classification predictions |
| `application_profile.db` | SQLite file (read-only mount) | Local filesystem | Insurance application data |
| `postgres_data` | Docker named volume | Local | Persistent PostgreSQL data storage |
| Anthropic Claude API (claude-sonnet-4-20250514, claude-haiku-4-5-20251001) | External SaaS API | Anthropic | Primary LLM for agent, specialist assessment, and aggregation |
| Google Gemini API (gemini-3-flash-preview) | External SaaS API | Google Cloud | Alternative LLM provider (configured but [TODO: verify active use]) |
| GitHub Actions runners (ubuntu-latest) | Ephemeral CI compute | GitHub (Microsoft Azure) | Runs all five AI delivery workflow tools |
| `ai-delivery-outputs` (external GitHub repo) | GitHub repository | GitHub | Stores generated docs, test files, and UAT packs |
| SendGrid | External SaaS API | Twilio/SendGrid | Email notifications for CI tool outputs |
| CatBoostClassifier model | Serialised ML model | Local | Risk classification (Preferred / Standard Plus / Standard / Substandard) |

---

## 3. Data Flow

### Runtime (Chat) Flow

1. **User input**: An underwriter types a message in the Chainlit frontend (port 8080). The frontend sends an HTTP POST to `backend:8000/chat` with `message`, `session_id`, `model`, `temperature`, and `mode` fields.
2. **Agent invocation**: The FastAPI backend calls `build_agent()`, which initialises a LangGraph agent with the selected LLM (Anthropic or Gemini), attaches tools (`get_customer_profile`, `run_underwriting_assessment`, `customer_lookalike`), and retrieves conversation history from Redis using the `session_id` as the checkpoint thread ID.
3. **Tool dispatch — profile lookup**: The agent LLM emits a JSON tool call for `get_customer_profile`. The tool queries `customer_profile.db` (SQLite, read-only) and returns structured customer metadata.
4. **Tool dispatch — lookalike**: Optionally, the agent calls `customer_lookalike`, which queries `customer_similarity_dict.json` (pre-computed similarity index) to find comparable customers.
5. **Tool dispatch — underwriting assessment**: The agent calls `run_underwriting_assessment(profile)`. This launches parallel async specialist LLM calls (up to 4 concurrent, controlled by `asyncio.Semaphore(4)`) — one per assessment category (finance, health, life, etc.) — each using the `anthropic-fast` (Claude Haiku) model with prompts from `assessment_criterias.json`.
6. **Assessment aggregation**: Specialist outputs are passed to an aggregator LLM (Claude Sonnet) with structured output enforcement via `UnderwritingReport` Pydantic schema, producing a typed JSON report (risk class, snapshot, findings, follow-up items, data gaps).
7. **Report rendering**: The structured report is rendered and returned as a tool result back to the agent.
8. **SSE streaming**: The FastAPI backend streams all intermediate events (tool start/end, LLM token chunks, thinking events, chart data) to the frontend as Server-Sent Events. The frontend renders these progressively.
9. **Checkpoint persistence**: LangGraph writes conversation state to Redis after each agent step, enabling multi-turn memory within a session.
10. **Chainlit session persistence**: Frontend session metadata (users, threads) is persisted to PostgreSQL.

### CI/CD Flow (GitHub Actions)

11. **Trigger**: A PR open/sync, push to `main`, version tag push, or scheduled cron triggers one of five GitHub Actions workflows.
12. **Code fetch**: The workflow checks out the source repo and installs `anthropic` and `requests` Python packages.
13. **LLM processing**: A Python script calls `shared.py` utilities, fetches repo files or PR diffs via the GitHub API, and sends them to Claude (claude-sonnet-4-6) for analysis.
14. **Output storage**: Generated artefacts (review JSON, markdown docs, test files, UAT packs) are committed to the `ai-delivery-outputs` GitHub repository via the GitHub Contents API.
15. **Notification**: SendGrid sends an email to `kylo.deng@capco.com` with a summary and link to the output.
16. **Audit logging**: Each run writes an audit entry (tool, timestamp, run URL) alongside the artefacts.

---

## 4. Security Posture

### What Is Secured

- **SQLite databases are mounted read-only** (`ro` flag in `docker-compose.yml`) — the backend cannot modify source data.
- **API keys are stored as GitHub Actions secrets** (`ANTHROPIC_API_KEY`, `GH_TOKEN`, `SENDGRID_API_KEY`) and are not hardcoded in workflow files.
- **System prompt confidentiality**: The agent is instructed never to reveal internal instructions or tool names to users.
- **Assessment token caps**: `specialist_max_tokens: 1500` and `aggregator_max_tokens: 8000` limit runaway LLM output and reduce prompt-injection blast radius.
- **Concurrency limiting**: `asyncio.Semaphore(4)` prevents unbounded parallel LLM calls from exhausting API rate limits.

### Gaps and Weaknesses

- **❌ No authentication or authorisation on the backend API**: `POST /chat` is publicly accessible with `allow_origins=["*"]` (CORS wildcard). Any client with network access can invoke the underwriting engine with arbitrary inputs.
- **❌ No TLS/HTTPS configured**: All inter-service communication (frontend → backend, backend → Redis, backend → PostgreSQL) runs over plain HTTP/TCP with no encryption in transit declared in `docker-compose.yml`.
- **❌ PostgreSQL credentials are hardcoded in plaintext**: `POSTGRES_USER: chainlit`, `POSTGRES_PASSWORD: chainlit`, `POSTGRES_DB: chainlit` are committed in `docker-compose.yml` — trivially discoverable.
- **❌ Redis has no authentication**: The Redis container is exposed on port 6379 with no password, ACL, or TLS. Conversation checkpoints (potentially containing PII) are stored unencrypted.
- **❌ Redis port 6379 is exposed to the host**: Any process on the host machine can read or write agent conversation state.
- **❌ PostgreSQL port 5432 is exposed to the host**: Same concern as Redis.
- **❌ No encryption at rest**: SQLite databases, PostgreSQL volume (`postgres_data`), and Redis data are stored unencrypted on the host filesystem.
- **❌ PII in conversation checkpoints**: Customer profiles containing sensitive underwriting data (age, income, medical conditions, nationality, smoker status) flow through Redis with no field-level encryption.
- **❌ `customer_similarity_dict.json` stored in `backend/tmp/`**: A pre-computed index of customer IDs is committed to the repository — scope of PII exposure should be reviewed.
- **❌ IAM not applicable (no cloud deployment) but GH_TOKEN scope is unknown**: [TODO: What permissions does `GH_TOKEN` have? It writes to `ai-delivery-outputs` and posts PR comments — confirm it is scoped to minimum required permissions and not an organisation-wide admin token.]
- **❌ No input validation on `/chat` endpoint**: The `message` field is passed directly to the LLM with no sanitisation — prompt injection risk.
- **❌ No rate limiting on the API**: Unlimited requests per session/IP.
- **❌ `model_card.json` committed to repo**: Contains feature names and importance scores for the risk classification model — assess whether this constitutes model IP leakage.
- **❌ CI scripts use `max_files=20` cap but no content filtering**: Sensitive file contents (including `.env` patterns) could be sent to the Anthropic API during code review.

---

## 5. Environment Variables and Secrets

| Name | Required | Sensitivity | Where Set |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | **Critical** — grants full Anthropic API access | GitHub Actions secret; `.env` file for backend |
| `GH_TOKEN` | Yes | **High** — GitHub API write access | GitHub Actions secret |
| `SENDGRID_API_KEY` | Yes | **High** — email sending capability | GitHub Actions secret |
| `GOOGLE_API_KEY` | Yes (if Gemini used) | **High** — Google Generative AI access | `.env` file for backend |
| `REDIS_HOST` | Yes | Low | `docker-compose.yml` environment block; defaults to `localhost` |
| `DATABASE_URL` | Yes | **Medium** — contains DB credentials | `docker-compose.yml` (hardcoded `chainlit:chainlit`) |
| `BACKEND_URL` | Yes | Low | `docker-compose.yml` (hardcoded `http://backend:8000`) |
| `POSTGRES_USER` | Yes | **Medium** | `docker-compose.yml` (hardcoded `chainlit`) |
| `POSTGRES_PASSWORD` | Yes | **High** | `docker-compose.yml` (hardcoded `chainlit`) — ❌ not a secret |
| `POSTGRES_DB` | Yes | Low | `docker-compose.yml` (hardcoded `chainlit`) |
| `OUTPUT_REPO` | No | Low | GitHub Actions env (default: `ai-delivery-outputs`) |
| `OUTPUT_REPO_OWNER` | No | Low | GitHub Actions env (derived from `github.repository_owner`) |
| `NOTIFY_EMAIL` | No | Low | GitHub Actions env (hardcoded `kylo.deng@capco.com`) |
| `SENDER_EMAIL` | No | Low | GitHub Actions env (hardcoded `noreply@ai-delivery.capco.com`) |
| `SOURCE_REPO_OWNER` | No | Low | GitHub Actions env |
| `SOURCE_REPO_NAME` | No | Low | GitHub Actions env |
| `GITHUB_RUN_URL` | No | Low | GitHub Actions env |

> **Note**: The `.env` file loaded by `backend/main.py` and `backend/modules/LLMS.py` is not present in the repository. [TODO: Document all required `.env` keys and provide a `.env.example`.]

---

## 6. Dependencies

| Dependency | Type | Purpose | Notes |
|---|---|---|---|
| Anthropic Claude API | External SaaS | Primary LLM (agent, specialists, aggregator, CI tools) | Models: claude-sonnet-4-20250514, claude-haiku-4-5-20251001, claude-sonnet-4-6 (CI scripts use a different model name than backend — inconsistency flagged below) |
| Google Generative AI (Gemini) | External SaaS | Alternative LLM provider | gemini-3-flash-preview; configured but [TODO: verify this model name is valid and actively used] |
| LangGraph / LangChain | Python library | Agent orchestration, tool calling, graph execution | `langgraph`, `langchain-core`, `langchain-anthropic`, `langchain-google-genai` |
| Redis Stack Server 7.2.0 | Infrastructure | LangGraph conversation checkpointing | `langgraph.checkpoint.redis.aio.AsyncRedisSaver` |
| PostgreSQL 16 | Infrastructure | Chainlit session persistence | |
| Chainlit | Python/UI framework | Frontend chat interface | Port 8080 |
| FastAPI + SSE-Starlette | Python framework | Backend REST + streaming API | Port 8000 |
| CatBoostClassifier | ML model (serialised) | Risk classification | [TODO: Where is the serialised model file? Only `model_card.json` and `model_predictions.db` are visible — confirm model artifact location] |
| SendGrid | External SaaS | Email notifications from CI tools | |
| GitHub API (api.github.com) | External SaaS | CI: repo file fetch, PR comments, output repo writes | |
| `ai-delivery-outputs` (GitHub repo) | External repo | Stores all CI-generated artefacts | Must exist under same owner; [TODO: confirm repo exists and GH_TOKEN has write access] |
| `pydantic` | Python library | Structured output validation for `UnderwritingReport` | |
| `python-dotenv` | Python library | `.env` loading in backend | |

---

## 7. Deployment Instructions

### Prerequisites

- Docker and Docker Compose installed
- A `.env` file in the `backend/` directory with at minimum:
  ```
  ANTHROPIC_API_KEY=<your-key>
  GOOGLE_API_KEY=<your-key>   # if using Gemini
  ```
- SQLite database files present under `./database/`:
  - `customer_profile.db`
  - `feature_importance.db`
  - `model_predictions.db`
  - `application_profile.db`

### Start All Services

```bash
# Clone the repository
git clone https://github.com/kylodeng/underwriting_chatbot-main.git
cd underwriting_chatbot-main

# Create backend environment file
cp .env.example backend/.env   # [TODO: .env.example does not exist — create it]
# Edit backend/.env with actual API keys

# Build and start all services
docker compose up --build

# To run in detached mode
docker compose up --build -d

# View logs
docker compose logs -f

# View logs for a specific service
docker compose logs -f backend
```

### Access the Application

```bash
# Frontend (Chainlit chat UI)
open http://localhost:8080

# Backend health check
curl http://localhost:8000/health

# Backend chat API (example)
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "Assess customer CUST00000001", "temperature": 0.3, "session_id": "test", "model": "anthropic-fast", "mode": "fast"}'
```

### Stop Services

```bash
docker compose down

# To also remove volumes (destroys PostgreSQL data)
docker compose down -v
```

### Trigger CI Workflows Manually

```bash
# Tool 1: Code Review (repo-wide)
gh workflow run tool1_code_review.yml -f review_mode=repo

# Tool 2: Tech Documentation
gh workflow run tool2_tech_docs.yml

# Tool 3: Business Documentation
gh workflow run tool3_business_docs.yml -f project_name="Underwriting Chatbot" -f release_version="1.0.0"

# Tool 4: Auto Testing (generate mode)
gh workflow run tool4_auto_testing.yml -f test_mode=generate

# Tool 5: UAT (generate mode)
gh workflow run tool5_uat.yml -f uat_mode=generate -f release_version="1.0.0"
```

---

## 8. Risks and TODOs

### Code-