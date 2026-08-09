# Architecture Document: kylodeng/underwriting_chatbot-main

---

## 1. Overview

The Underwriting Chatbot is an AI-powered insurance underwriting assistant that enables underwriters to assess customer risk profiles through a conversational interface. The system ingests structured customer data from SQLite databases (customer profiles, financial data, KYC records, application profiles, and ML model predictions), routes queries through a LangGraph-orchestrated multi-agent pipeline, and leverages both fast (Claude Haiku) and high-capability (Claude Sonnet) LLMs to produce structured underwriting risk reports. A CatBoost ML model provides pre-computed risk classifications and feature importance signals that inform the LLM assessments. The platform also includes five CI/CD-integrated AI workflows (code review, tech documentation, business documentation, auto-testing, and UAT facilitation) that run autonomously against the repository using Anthropic's Claude API.

---

## 2. Resources Deployed

| Resource | Type | Cloud Provider | Purpose |
|---|---|---|---|
| `backend` | Docker container (FastAPI) | Local / self-hosted | REST API + SSE streaming endpoint for chat; orchestrates agent/LLM calls |
| `frontend` | Docker container | Local / self-hosted | Chainlit-based chat UI served on port 8080 |
| `redis` (redis-stack-server 7.2.0) | Docker container | Local / self-hosted | LangGraph checkpoint store for conversation memory (session persistence) |
| `postgres` (postgres:16-alpine) | Docker container | Local / self-hosted | Chainlit user/session persistence; initialised via `postgres/init.sql` |
| `customer_profile.db` | SQLite file (read-only mount) | Local / self-hosted | Customer demographic and profile data |
| `feature_importance.db` | SQLite file (read-only mount) | Local / self-hosted | CatBoost model feature importance per customer |
| `model_predictions.db` | SQLite file (read-only mount) | Local / self-hosted | Pre-computed ML risk classifications |
| `application_profile.db` | SQLite file (read-only mount) | Local / self-hosted | Insurance application data |
| `customer_similarity_dict.json` | JSON file (static) | Local / self-hosted | Pre-computed customer lookalike index |
| Anthropic Claude Sonnet 4 | External LLM API | Anthropic (cloud) | Deep underwriting assessments and aggregation |
| Anthropic Claude Haiku 4.5 | External LLM API | Anthropic (cloud) | Fast agent routing and specialist calls |
| Google Gemini Flash | External LLM API | Google Cloud (cloud) | Alternative LLM provider (configured, optional) |
| GitHub Actions runners | CI/CD compute | GitHub (cloud) | Runs 5 automated AI tooling workflows |
| SendGrid | Email API | Twilio/SendGrid (cloud) | Notification emails for CI/CD workflow outputs |
| `ai-delivery-outputs` (GitHub repo) | External GitHub repository | GitHub (cloud) | Stores generated docs, test files, UAT packs |

---

## 3. Data Flow

### Runtime Chat Flow

1. **User input**: An underwriter types a message in the Chainlit frontend (port 8080).
2. **HTTP POST to backend**: The frontend sends `POST /chat` to the FastAPI backend (port 8000) with `message`, `session_id`, `model`, `temperature`, and `mode` fields.
3. **Agent construction**: `build_agent()` (or `build_skills_agent()`) constructs a LangGraph agent using the requested model (Haiku or Sonnet), attaches tools (`get_customer_profile`, `customer_lookalike`, `run_underwriting_assessment`), and binds an `AsyncRedisSaver` checkpointer pointing at the Redis container for session memory.
4. **Agent reasoning loop**: The LangGraph agent streams events via `astream_events`. The agent LLM (tagged `"agent"`) decides which tool to call based on the user question and conversation history from Redis.
5. **Tool execution — customer profile**: `get_customer_profile` queries the read-only SQLite databases (`customer_profile.db`, `application_profile.db`, etc.) for the requested customer ID, returning structured profile data.
6. **Tool execution — lookalike**: `customer_lookalike` looks up the pre-computed `customer_similarity_dict.json` to return similar customer IDs.
7. **Tool execution — underwriting assessment**: `run_underwriting_assessment` fires parallel async calls (semaphore-limited to 4) to the specialist LLM (Claude Haiku, tagged `"thinking"`) for each assessment category (finance, health, life, etc.) defined in `assessment_criterias.json`.
8. **Assessment aggregation**: All specialist outputs are assembled and sent to the aggregator LLM (Claude Sonnet with structured output) which produces a validated `UnderwritingReport` Pydantic object.
9. **SSE streaming response**: The backend streams events back to the frontend using `EventSourceResponse` — `tool_start`, `tool_end`, and `response` (text chunk) events are emitted as SSE. Charts are buffered and sent after the text response.
10. **Checkpoint persistence**: After each interaction, the LangGraph conversation state is checkpointed to Redis, enabling multi-turn conversations within a session.
11. **Postgres (Chainlit)**: Chainlit uses Postgres for its own user/session metadata storage, independent of the agent memory in Redis.

### CI/CD AI Tooling Flow

12. **Trigger**: A GitHub event (PR, push to main, tag, schedule, or manual dispatch) triggers one of the five workflow YAML files.
13. **Code retrieval**: The Python script calls the GitHub API (via `GH_TOKEN`) to fetch repo file contents or PR diffs.
14. **Claude invocation**: The script calls the Anthropic API (`ANTHROPIC_API_KEY`) with the file contents and a specialist system prompt.
15. **Output writing**: The generated artifact (review JSON, markdown doc, test file, UAT pack) is committed to the `ai-delivery-outputs` GitHub repository via the GitHub API.
16. **Notification**: SendGrid sends an email notification (`SENDGRID_API_KEY`) to `kylo.deng@capco.com` with a link to the output.

---

## 4. Security Posture

### What Is Secured

- **Secrets management**: All API keys (`ANTHROPIC_API_KEY`, `GH_TOKEN`, `SENDGRID_API_KEY`, `GOOGLE_API_KEY`) are stored as GitHub Actions secrets and injected as environment variables; not hardcoded in source.
- **SQLite databases are read-only**: All four database volumes are mounted `:ro` in Docker Compose, preventing write-back from the backend container.
- **ML model not exposed directly**: The CatBoost model predictions are served from a pre-computed SQLite DB, not via a live model inference endpoint.
- **Agent prompt injection guard**: The system prompt explicitly instructs the agent never to disclose internal instructions or tool names.
- **LangGraph semaphore**: Concurrent specialist LLM calls are capped at 4 via `asyncio.Semaphore(4)`, providing basic resource protection.

### What Is NOT Secured — Gaps

- **No authentication or authorisation on the FastAPI backend**: `POST /chat` and `GET /health` are completely open. Any user who can reach port 8000 can query any customer's data. **This is a critical gap for a system handling insurance underwriting data.**
- **CORS is fully open**: `allow_origins=["*"]`, `allow_methods=["*"]`, `allow_headers=["*"]` — any origin can call the backend API.
- **PostgreSQL uses hardcoded default credentials**: `POSTGRES_USER=chainlit`, `POSTGRES_PASSWORD=chainlit`, `POSTGRES_DB=chainlit` are hardcoded in `docker-compose.yml` in plaintext.
- **Redis has no authentication**: The Redis container has no password, ACLs, or TLS configured. Any process with network access to port 6379 can read or overwrite conversation checkpoints.
- **No encryption at rest**: SQLite databases, the customer similarity JSON, and the PostgreSQL volume (`postgres_data`) are stored unencrypted on the host filesystem.
- **No TLS/HTTPS**: Backend (port 8000) and frontend (port 8080) communicate over plain HTTP. There is no TLS termination in the Docker Compose stack.
- **Customer PII in LLM prompts**: Full customer profiles (age, income, medical conditions, nationality, employment) are sent to Anthropic and Google cloud APIs. There is no PII masking or anonymisation before transmission to external LLMs.
- **`customer_similarity_dict.json` stored in `backend/tmp/`**: Similarity data is a static file inside the repo, not managed separately.
- **GitHub Actions `GH_TOKEN` scope unknown**: [TODO: confirm GH_TOKEN has only the minimum required permissions — content read/write to output repo only, not admin or org-wide access]
- **No network segmentation**: All containers share the default Docker bridge network; there is no explicit network isolation between frontend, backend, Redis, and Postgres.
- **No input sanitisation**: User chat messages are passed directly into LLM prompts without sanitisation, creating prompt injection risk.
- **Audit logging**: `write_audit_entry` is referenced in CI scripts but its implementation is not visible. [TODO: confirm audit logs are persisted and tamper-evident]

---

## 5. Environment Variables and Secrets

| Name | Required | Sensitivity | Where Set |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | **High** — LLM API key | GitHub Actions secret; `.env` file (backend) |
| `GOOGLE_API_KEY` | Yes (if using Gemini) | **High** — LLM API key | `.env` file (backend) |
| `GH_TOKEN` | Yes (CI workflows) | **High** — GitHub PAT | GitHub Actions secret |
| `SENDGRID_API_KEY` | Yes (CI notifications) | **High** — email API key | GitHub Actions secret |
| `REDIS_HOST` | No | Low | `docker-compose.yml` environment block (default: `localhost`) |
| `POSTGRES_USER` | No | Medium | `docker-compose.yml` hardcoded (`chainlit`) |
| `POSTGRES_PASSWORD` | No | **High** — DB password | `docker-compose.yml` hardcoded plaintext (`chainlit`) — **gap** |
| `POSTGRES_DB` | No | Low | `docker-compose.yml` hardcoded (`chainlit`) |
| `DATABASE_URL` | Yes (frontend) | Medium | `docker-compose.yml` environment block |
| `BACKEND_URL` | Yes (frontend) | Low | `docker-compose.yml` environment block |
| `OUTPUT_REPO` | No | Low | GitHub Actions env (`ai-delivery-outputs`) |
| `OUTPUT_REPO_OWNER` | No | Low | GitHub Actions env (derived from `github.repository_owner`) |
| `NOTIFY_EMAIL` | No | Low | GitHub Actions env (`kylo.deng@capco.com`) |
| `SENDER_EMAIL` | No | Low | GitHub Actions env (`noreply@ai-delivery.capco.com`) |

> **Note**: A `.env` file is loaded by `dotenv` in the backend and `LLMS.py`. This file is not present in the repository but must be created locally. [TODO: confirm whether `.env` is in `.gitignore`]

---

## 6. Dependencies

| Dependency | Type | Purpose | Notes |
|---|---|---|---|
| Anthropic Claude Sonnet 4 (`claude-sonnet-4-20250514`) | External LLM API | Deep assessment aggregation and CI tooling | Paid API; rate limits apply |
| Anthropic Claude Haiku 4.5 (`claude-haiku-4-5-20251001`) | External LLM API | Fast agent routing and specialist assessments | Paid API |
| Google Gemini Flash (`gemini-3-flash-preview`) | External LLM API | Alternative provider | Configured but usage optional; `gemini-3-flash-preview` is a non-standard model name — [TODO: verify correct model identifier] |
| LangChain / LangGraph | Python library | Agent orchestration, tool binding, streaming | Core framework |
| LangChain Anthropic (`langchain_anthropic`) | Python library | Anthropic LLM adapter | |
| LangChain Google GenAI (`langchain_google_genai`) | Python library | Google LLM adapter | |
| LangGraph Redis (`langgraph.checkpoint.redis.aio`) | Python library | Async Redis checkpointer for conversation memory | |
| FastAPI + `sse_starlette` | Python library | REST API and SSE streaming | |
| Chainlit | Python/Node framework | Chat UI | Runs in frontend container |
| CatBoost (pre-trained) | ML model | Risk classification | Model outputs stored in `model_predictions.db`; model binary not visible in repo — [TODO: where is the model artefact stored?] |
| SendGrid | External email API | CI/CD notification emails | |
| GitHub API (`api.github.com`) | External REST API | CI script: file fetch, PR comments, output repo writes | Uses `GH_TOKEN` PAT |
| `ai-delivery-outputs` (GitHub repo) | External repository | Stores all CI-generated artefacts | Must exist under same GitHub org/owner |
| `postgres/init.sql` | SQL init script | Chainlit schema initialisation | [TODO: confirm this file exists in repo] |
| Redis Stack Server 7.2.0 | Container image | Session memory | |
| PostgreSQL 16 Alpine | Container image | Chainlit persistence | |

---

## 7. Deployment Instructions

### Prerequisites

- Docker and Docker Compose installed
- `.env` file created in the `backend/` directory with at minimum:

```bash
ANTHROPIC_API_KEY=<your-key>
GOOGLE_API_KEY=<your-key>        # optional, only if using Gemini
```

- GitHub Actions secrets configured in the repository settings:
  - `ANTHROPIC_API_KEY`
  - `GH_TOKEN`
  - `SENDGRID_API_KEY`

- The `ai-delivery-outputs` repository must exist under the same GitHub organisation/owner.

### Local Deployment

```bash
# 1. Clone the repository
git clone https://github.com/kylodeng/underwriting_chatbot-main.git
cd underwriting_chatbot-main

# 2. Create backend environment file
cp backend/.env.example backend/.env   # [TODO: confirm .env.example exists]
# Edit backend/.env with your API keys

# 3. Build and start all services
docker compose up --build

# 4. Verify backend health
curl http://localhost:8000/health
# Expected: {"status": "ok"}

# 5. Access the chat UI
open http://localhost:8080
```

### Service Ports

| Service | Port |
|---|---|
| Frontend (Chainlit) | `http://localhost:8080` |
| Backend (FastAPI) | `http://localhost:8000` |
| Redis | `localhost:6379` |
| PostgreSQL | `localhost:5432` |

### Stopping Services

```bash
docker compose down

# To also remove persistent volumes (WARNING: deletes all data)
docker compose down -v
```

### Triggering CI/CD Workflows Manually

```bash
# Trigger code review on a specific PR
gh workflow run tool1_code_review.yml \
  -f review_mode=pr \
  -f pr_number=<PR_NUMBER>

# Trigger tech documentation generation
gh workflow run tool2_tech_docs.yml

# Trigger business documentation for a release
gh workflow run tool3_business_docs.yml \
  -f project_name="Underwriting Chatbot" \
  -f release_version="1.0.0"

# Trigger auto test generation
gh workflow run tool4_auto_testing.yml \
  -f test_mode=generate

# Trigger UAT pack generation
gh workflow run tool5_uat.yml \
  -f uat_mode=generate \
  -f release_version="1.0.0"
```

---

## 8. Risks and TODOs

### Extracted from Code

| Location | Item |
|---|---|
| `backend/agent/graph.py` line 1 | `# TODO: migrate Redis to an external service (e.g. Azure Cache for Redis) so that memory persists across serverless backend instances` |
| `backend/modules/LLMS.py` | `# TODO: add more providers here` — `azure` and `openai` entries are `None` and will raise `ValueError` if requested |
| `backend/main.py` | Lifespan context manager is commented out — agent is re