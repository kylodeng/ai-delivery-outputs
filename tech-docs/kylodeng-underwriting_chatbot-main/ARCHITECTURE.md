# Architecture Document: kylodeng/underwriting_chatbot-main

---

## 1. Overview

The Underwriting Chatbot is an AI-powered insurance underwriting assistant that enables underwriters to assess customer risk profiles through a conversational interface. The system ingests pre-computed customer data from multiple SQLite databases, runs parallel multi-specialist LLM assessments (using Anthropic Claude as the primary model and Google Gemini as an alternative), and produces structured underwriting reports covering finance, health, life, KYC, and other risk domains. A React/Chainlit-based frontend communicates with a FastAPI streaming backend over Server-Sent Events (SSE); Redis provides conversational memory via LangGraph checkpointing, and PostgreSQL persists Chainlit session/user data. A suite of five GitHub Actions CI/CD workflows automate code review, technical documentation, business documentation, test generation, and UAT facilitation — all powered by Claude via the Anthropic API.

---

## 2. Resources Deployed

| Resource | Type | Cloud Provider | Purpose |
|---|---|---|---|
| `backend` | Docker container (FastAPI, Python 3.x) | Self-hosted / Docker Compose | Serves `/chat` SSE endpoint and `/health`; orchestrates LangGraph agent and LLM calls |
| `frontend` | Docker container (Chainlit/React) | Self-hosted / Docker Compose | Conversational UI for underwriters |
| `redis` (redis-stack-server:7.2.0-v14) | Docker container | Self-hosted / Docker Compose | LangGraph conversation checkpointing / session memory |
| `postgres` (postgres:16-alpine) | Docker container | Self-hosted / Docker Compose | Chainlit session/user persistence |
| `customer_profile.db` | SQLite file (read-only volume mount) | Self-hosted | Customer demographic and profile data |
| `feature_importance.db` | SQLite file (read-only volume mount) | Self-hosted | CatBoostClassifier feature importance scores |
| `model_predictions.db` | SQLite file (read-only volume mount) | Self-hosted | Pre-computed ML model predictions |
| `application_profile.db` | SQLite file (read-only volume mount) | Self-hosted | Insurance application data |
| `postgres_data` | Docker named volume | Self-hosted | PostgreSQL data persistence |
| Anthropic Claude API (claude-sonnet-4-20250514, claude-haiku-4-5-20251001) | External SaaS API | Anthropic | Primary LLM for chat, specialist assessment, aggregation, and all CI workflows |
| Google Gemini API (gemini-3-flash-preview) | External SaaS API | Google Cloud | Alternative LLM provider (partially configured) |
| GitHub Actions runners (ubuntu-latest) | Managed CI/CD | GitHub | Executes all five automation workflow tools |
| `ai-delivery-outputs` (GitHub repo) | External GitHub repository | GitHub | Stores generated docs, test files, UAT packs, and code review reports |
| SendGrid | External SaaS API | Twilio/SendGrid | Email notifications for CI workflow outputs |
| CatBoostClassifier model | Serialised ML model | Self-hosted | Pre-trained risk classification model (referenced in model_card.json) |

---

## 3. Data Flow

### Primary Chat Flow

1. **User Input**: An underwriter types a message in the Chainlit frontend (port 8080). The frontend sends a POST request to `http://backend:8000/chat` with `{message, temperature, session_id, model, mode}`.

2. **Agent Initialisation**: The FastAPI backend (`main.py`) calls `build_agent()`, which initialises a LangGraph agent with Redis-backed checkpointing (`AsyncRedisSaver`) keyed by `session_id`. Prior conversation turns are loaded from Redis.

3. **Agent Reasoning (Tool Selection)**: The LangGraph agent sends the user message plus conversation history to the Claude LLM (via `langchain_anthropic`). The LLM responds with a JSON tool-call directive (e.g., `{"action": "tool_call", "tool_name": "get_customer_profile", ...}`).

4. **Tool: `get_customer_profile`**: The agent invokes the profile tool, which queries the `customer_profile.db` SQLite database (mounted read-only at `/data/customer_profile.db`) and returns structured customer data.

5. **Tool: `customer_lookalike`**: Optionally invoked; reads `backend/tmp/customer_similarity_dict.json` to find similar historical customers by ID.

6. **Tool: `run_underwriting_assessment`**: The agent invokes the assessment tool with the customer profile string. Inside `assessment.py`:
   - Up to 4 concurrent specialist LLM calls (semaphore-limited) are dispatched via `asyncio`, each targeting a domain category (finance, health, life, KYC, etc.) using prompts from `assessment_criterias.json`.
   - Specialist LLM: `claude-haiku-4-5-20251001` (anthropic-fast), max 1,500 tokens per specialist, tagged `"thinking"`.
   - Results are aggregated by a second LLM call using `claude-haiku-4-5-20251001`, max 8,000 tokens, with structured output enforced via Pydantic (`UnderwritingReport`).

7. **Streaming Response**: The backend streams SSE events back to the frontend: `tool_start`, `tool_end`, `response` (text chunks), `chart` (feature importance visualisations), and `done`. The frontend renders these progressively.

8. **Conversation Persistence**: Each turn's messages (including tool calls/results) are checkpointed to Redis via LangGraph's `AsyncRedisSaver`.

9. **PostgreSQL**: Chainlit session metadata (users, threads) is persisted to the `chainlit` PostgreSQL database.

### CI/CD Automation Flow (GitHub Actions)

10. **Trigger**: A PR, push to `main`, scheduled cron, version tag, or manual dispatch triggers one of five workflows.

11. **Claude Analysis**: The workflow script fetches repo files or PR diffs via the GitHub REST API, sends them to `claude-sonnet-4-6` (hardcoded in `shared.py`), and receives structured output (JSON or Markdown).

12. **Output Storage**: Generated artefacts (review JSON, docs, test files, UAT packs) are committed to the `ai-delivery-outputs` GitHub repository via the GitHub API.

13. **Notification**: SendGrid sends an email to `kylo.deng@capco.com` with links to the generated output.

---

## 4. Security Posture

### What IS Secured

- **API keys in CI**: `ANTHROPIC_API_KEY`, `GH_TOKEN`, `SENDGRID_API_KEY`, and `GOOGLE_API_KEY` are stored as GitHub Actions secrets and injected at runtime — not hardcoded in workflow YAML.
- **SQLite databases mounted read-only**: All four SQLite databases are mounted with `:ro` in Docker Compose, preventing write access from the backend container.
- **Prompt injection guard**: The system prompt explicitly instructs the agent never to reveal internal instructions or tool details.
- **Semaphore limiting**: Concurrent LLM calls are capped at 4 to prevent runaway resource consumption.
- **Token caps**: Specialist LLM responses are hard-capped at 1,500 tokens; aggregator at 8,000 tokens.

### What IS NOT Secured (Gaps)

- **⚠️ CORS is fully open**: `allow_origins=["*"]`, `allow_methods=["*"]`, `allow_headers=["*"]` in `main.py`. Any origin can call the `/chat` and `/health` endpoints. There is no authentication or authorisation on the API.
- **⚠️ No authentication on `/chat`**: Any client that can reach port 8000 can submit chat requests with arbitrary `session_id`, `model`, and `mode` parameters — including model selection, which could route to more expensive models.
- **⚠️ PostgreSQL credentials are hardcoded** in `docker-compose.yml`: `POSTGRES_USER=chainlit`, `POSTGRES_PASSWORD=chainlit`. These are trivially guessable and should be secrets.
- **⚠️ Redis has no authentication**: Redis port 6379 is exposed with no password, ACLs, or TLS. All conversation history (which may contain PII and underwriting data) is stored unencrypted.
- **⚠️ No encryption at rest**: SQLite databases, Redis data, and PostgreSQL data are stored on plain Docker volumes with no encryption. Customer PII (age, income, medical conditions, nationality) and model predictions are unprotected at rest.
- **⚠️ No encryption in transit** between internal Docker services (backend↔redis, backend↔postgres, frontend↔backend): plain TCP, no TLS within the Docker network.
- **⚠️ Sensitive data in `tmp/` directory**: `backend/tmp/customer_similarity_dict.json` contains a pre-computed similarity mapping for ~10,000 customer IDs committed to the repository.
- **⚠️ `.env` file loaded from disk**: `main.py` calls `load_dotenv()` — if the `.env` file is included in the Docker image build context, secrets may be baked into the image.
- **⚠️ No input sanitisation**: User messages are passed directly to the LLM without length limits or content filtering beyond the system prompt instruction.
- **⚠️ `GH_TOKEN` scope unknown**: The GitHub token used in CI workflows has unknown scope. If it has `repo` write access across the organisation, it could be misused. [TODO: confirm minimum required scopes for GH_TOKEN]
- **⚠️ No rate limiting**: The `/chat` endpoint has no rate limiting, making it susceptible to abuse and runaway LLM cost.
- **⚠️ `customer_similarity_dict.json` in source control**: Contains mappings of real (or realistic) customer IDs — this should not be committed to a repository.
- **⚠️ Ports 6379 and 5432 exposed to host**: Redis and PostgreSQL are bound to `0.0.0.0` on the Docker host, not restricted to the internal Docker network.

---

## 5. Environment Variables and Secrets

| Name | Required | Sensitivity | Where Set |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | 🔴 High — billable API key | GitHub Actions secret; `.env` file for backend |
| `GOOGLE_API_KEY` | Yes (if using Gemini) | 🔴 High — billable API key | `.env` file for backend |
| `GH_TOKEN` | Yes (CI only) | 🔴 High — GitHub repo access | GitHub Actions secret |
| `SENDGRID_API_KEY` | Yes (CI only) | 🔴 High — email sending | GitHub Actions secret |
| `REDIS_HOST` | No | 🟢 Low | Docker Compose `environment`; defaults to `localhost` |
| `DATABASE_URL` | Yes (frontend) | 🟡 Medium — DB credentials in URL | Docker Compose `environment` (hardcoded) |
| `BACKEND_URL` | Yes (frontend) | 🟢 Low | Docker Compose `environment` |
| `POSTGRES_USER` | Yes | 🟡 Medium | Docker Compose `environment` (hardcoded as `chainlit`) |
| `POSTGRES_PASSWORD` | Yes | 🔴 High | Docker Compose `environment` (hardcoded as `chainlit`) ⚠️ |
| `POSTGRES_DB` | Yes | 🟢 Low | Docker Compose `environment` (hardcoded as `chainlit`) |
| `OUTPUT_REPO` | No (CI) | 🟢 Low | GitHub Actions `env` block; defaults to `ai-delivery-outputs` |
| `OUTPUT_REPO_OWNER` | No (CI) | 🟢 Low | GitHub Actions `env` block |
| `NOTIFY_EMAIL` | No (CI) | 🟢 Low | GitHub Actions `env` block (hardcoded: `kylo.deng@capco.com`) |
| `SENDER_EMAIL` | No (CI) | 🟢 Low | GitHub Actions `env` block (hardcoded: `noreply@ai-delivery.capco.com`) |
| `REVIEW_MODE` | No (CI Tool 1) | 🟢 Low | Set dynamically in workflow step |
| `PR_NUMBER` | No (CI Tool 1) | 🟢 Low | Set dynamically in workflow step |
| `RELEASE_VERSION` | No (CI Tool 3/5) | 🟢 Low | Set dynamically in workflow step |
| `PROJECT_NAME` | No (CI Tool 3) | 🟢 Low | Set dynamically in workflow step |
| `TEST_MODE` | No (CI Tool 4) | 🟢 Low | Set dynamically in workflow step |
| `UAT_MODE` | No (CI Tool 5) | 🟢 Low | Set dynamically in workflow step |

---

## 6. Dependencies

| Dependency | Type | Used By | Notes |
|---|---|---|---|
| Anthropic API (`api.anthropic.com`) | External SaaS | Backend (LLM inference), all CI workflows | Primary LLM provider; models: `claude-sonnet-4-20250514`, `claude-haiku-4-5-20251001`, `claude-sonnet-4-6` (CI) |
| Google Generative AI API | External SaaS | Backend | Secondary LLM provider (Gemini); `gemini-3-flash-preview` — model name appears non-standard [TODO: verify correct Gemini model identifier] |
| LangChain / LangGraph | Python library | Backend | Agent orchestration, tool execution, streaming |
| LangChain Anthropic (`langchain_anthropic`) | Python library | Backend | Anthropic integration for LangChain |
| LangChain Google GenAI (`langchain_google_genai`) | Python library | Backend | Google Gemini integration |
| Redis Stack Server 7.2.0 | Infrastructure | Backend | LangGraph conversation checkpointing |
| PostgreSQL 16 | Infrastructure | Frontend (Chainlit) | Session/user data persistence |
| Chainlit | Python/Frontend framework | Frontend | Conversational UI framework |
| FastAPI + sse-starlette | Python library | Backend | HTTP API and SSE streaming |
| Pydantic | Python library | Backend | Structured LLM output validation (`UnderwritingReport`) |
| CatBoostClassifier (pre-trained) | ML model artefact | Backend (via SQLite DBs) | Risk classification — model stored as predictions in `model_predictions.db`; training data not in repo |
| SendGrid (`api.sendgrid.com`) | External SaaS | CI workflows | Email notifications |
| GitHub REST API (`api.github.com`) | External API | CI workflows | Fetching repo files, PR diffs, posting comments, writing to output repo |
| `ai-delivery-outputs` (GitHub repo) | External repo (`kylodeng/ai-delivery-outputs`) | CI workflows | Storage for all generated documentation, test files, UAT packs |
| `anthropic` Python SDK | Python library | CI workflows | Direct Anthropic API calls in GitHub Actions |

---

## 7. Deployment Instructions

### Prerequisites

- Docker and Docker Compose v2+ installed
- `.env` file created in the project root with required secrets (see Section 5)
- SQLite database files present under `./database/`

### Local / Single-Host Deployment

```bash
# 1. Clone the repository
git clone https://github.com/kylodeng/underwriting_chatbot-main.git
cd underwriting_chatbot-main

# 2. Create the .env file with required secrets
cat > .env << EOF
ANTHROPIC_API_KEY=<your-anthropic-api-key>
GOOGLE_API_KEY=<your-google-api-key>
EOF

# 3. Ensure database files exist
ls ./database/
# Expected: customer_profile.db  feature_importance.db  model_predictions.db  application_profile.db

# 4. Build and start all services
docker compose up --build -d

# 5. Verify health
curl http://localhost:8000/health
# Expected: {"status": "ok"}

# 6. Access the frontend
open http://localhost:8080
```

### Service Logs

```bash
# All services
docker compose logs -f

# Individual service
docker compose logs -f backend
docker compose logs -f frontend
docker compose logs -f redis
docker compose logs -f postgres
```

### Stopping and Cleanup

```bash
# Stop services (preserves volumes)
docker compose down

# Stop and remove volumes (destroys