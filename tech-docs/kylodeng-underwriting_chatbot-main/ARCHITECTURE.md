# Architecture Document: kylodeng/underwriting_chatbot-main

---

## 1. Overview

The Underwriting Chatbot is an AI-assisted insurance underwriting platform that enables underwriters to assess customer risk profiles through a conversational interface. The backend exposes a streaming FastAPI service that orchestrates a LangGraph-based multi-agent system: a routing agent dispatches tool calls to retrieve customer profiles, compute customer similarity ("lookalike"), and run full underwriting risk assessments. The assessment pipeline fans out to multiple specialist LLM agents (finance, health, life, KYC, etc.) in parallel, then aggregates their findings into a structured `UnderwritingReport` via a second LLM call with structured output. The frontend is a Chainlit-based chat UI. Supporting the development lifecycle, a suite of five GitHub Actions workflows uses Claude (via the Anthropic API) to automate code review, technical documentation, business documentation, test generation, and UAT facilitation — all writing outputs to a shared `ai-delivery-outputs` repository and notifying via SendGrid email.

---

## 2. Resources Deployed

| Resource | Type | Cloud Provider | Purpose |
|---|---|---|---|
| `backend` | Docker container (FastAPI, Python 3.x) | Local / self-hosted | Serves `/chat` SSE streaming endpoint and `/health`; hosts LangGraph agent |
| `frontend` | Docker container (Chainlit) | Local / self-hosted | Web-based chat UI for underwriters |
| `redis` (redis-stack-server 7.2.0) | Docker container | Local / self-hosted | LangGraph conversation checkpoint store (session memory) |
| `postgres` (postgres:16-alpine) | Docker container | Local / self-hosted | Chainlit persistent storage (user sessions, chat history) |
| `customer_profile.db` | SQLite file (read-only volume mount) | Local / self-hosted | Customer profile data |
| `feature_importance.db` | SQLite file (read-only volume mount) | Local / self-hosted | ML model feature importance data |
| `model_predictions.db` | SQLite file (read-only volume mount) | Local / self-hosted | CatBoost model risk classification predictions |
| `application_profile.db` | SQLite file (read-only volume mount) | Local / self-hosted | Insurance application profile data |
| `postgres_data` | Docker named volume | Local / self-hosted | Persistent Postgres data across restarts |
| Anthropic Claude API (claude-sonnet-4, claude-haiku-4-5) | External managed LLM API | Anthropic (cloud) | Powers all agent LLM calls and CI/CD AI tools |
| Google Gemini API (gemini-3-flash-preview) | External managed LLM API | Google Cloud | Configured as optional/alternative LLM provider |
| GitHub Actions runners (ubuntu-latest) | CI/CD compute | GitHub (cloud) | Executes all five AI delivery workflow tools |
| `ai-delivery-outputs` (GitHub repo) | External GitHub repository | GitHub | Receives AI-generated docs, test files, UAT packs from CI workflows |
| SendGrid API | External email service | Twilio/SendGrid (cloud) | Sends notification emails on CI workflow completion |

---

## 3. Data Flow

### Runtime (Chat) Flow

1. **User input**: An underwriter types a message in the Chainlit frontend (port 8080). The frontend forwards the message via HTTP POST to `http://backend:8000/chat` with `session_id`, `model`, `mode`, and `temperature` parameters.
2. **Agent initialisation**: The backend's `build_agent()` constructs a LangGraph agent for each request, loading the system prompt (including model card context) and configuring the LLM (Claude Haiku by default, Claude Sonnet for deep mode, or Gemini).
3. **Checkpoint retrieval**: LangGraph retrieves prior conversation state from Redis (keyed by `session_id`) via `AsyncRedisSaver`, enabling multi-turn memory.
4. **Agent routing**: The agent LLM decides which tool to call based on the user message and conversation history. In `agent_with_skills.py`, the agent emits structured JSON `{"action": "tool_call", ...}` responses to invoke tools one at a time.
5. **Tool: `get_customer_profile`**: Queries the SQLite `customer_profile.db` (and related databases) to retrieve structured customer data. Returns the profile as a string to the agent.
6. **Tool: `customer_lookalike`**: Reads `customer_similarity_dict.json` (pre-computed similarity index) to return a list of similar customer IDs.
7. **Tool: `run_underwriting_assessment`**: Receives the customer profile string. Fans out `asyncio` coroutines (semaphore-limited to 4 concurrent) to invoke the specialist LLM for each assessment category (finance, health, life, KYC, etc.) defined in `assessment_criterias.json`, using the configured mode (`fast` or `deep`).
8. **Specialist LLM calls**: Each specialist call uses Claude Haiku (fast) with a `max_tokens` cap of 1,500. Responses are collected as category-labelled text blocks.
9. **Aggregation**: All specialist reports are passed to the aggregator LLM (Claude Haiku, `max_tokens` 8,000) with structured output enforced via `UnderwritingReport` Pydantic schema, producing a typed risk classification result.
10. **Report rendering**: The `render_report` module formats the `UnderwritingReport` into markdown/HTML for streaming to the frontend.
11. **SSE streaming**: The backend streams events (`tool_start`, `tool_end`, `response`, chart data) as Server-Sent Events back to the frontend, which renders them progressively.
12. **Checkpoint write**: LangGraph writes the updated conversation state back to Redis for the next turn.

### CI/CD (AI Delivery Tools) Flow

1. A GitHub event (PR open, push to main, tag, schedule, or manual dispatch) triggers one of the five workflow YAML files.
2. The workflow checks out the source repo and installs `anthropic` and `requests`.
3. The corresponding Python script (`tool1_` through `tool5_`) fetches source files or PR diffs from the GitHub API using `GH_TOKEN`.
4. File content is passed to Claude (via `ANTHROPIC_API_KEY`) with a tool-specific system prompt.
5. The Claude response (review JSON, markdown docs, test files, UAT pack) is written to the `ai-delivery-outputs` GitHub repository via the GitHub Contents API.
6. A notification email is sent via SendGrid (`SENDGRID_API_KEY`) to `kylo.deng@capco.com`.
7. For code review (Tool 1), a comment is also posted back to the originating PR.

---

## 4. Security Posture

### Secured

- **Secrets management (CI/CD)**: All API keys (`ANTHROPIC_API_KEY`, `GH_TOKEN`, `SENDGRID_API_KEY`) are stored as GitHub Actions secrets and injected at runtime — not hardcoded in workflow files.
- **SQLite databases are mounted read-only** (`ro` flag in `docker-compose.yml`), preventing the backend from modifying source data.
- **System prompt confidentiality**: The agent is explicitly instructed never to reveal its system prompt or tool list to users.
- **Specialist LLM token caps**: `specialist_max_tokens: 1500` and `aggregator_max_tokens: 8000` prevent runaway cost from verbose LLM output.
- **Asyncio semaphore**: Limits concurrent specialist LLM calls to 4, providing a basic rate-control mechanism.

### Not Secured / Gaps

- **❌ No authentication or authorisation on the `/chat` endpoint**: `CORSMiddleware` is configured with `allow_origins=["*"]`, `allow_methods=["*"]`, `allow_headers=["*"]` — the API is fully open. Any client with network access can query sensitive customer underwriting data.
- **❌ No HTTPS/TLS**: There is no TLS termination configured in `docker-compose.yml`. All traffic (including sensitive customer PII and LLM responses) travels over unencrypted HTTP between containers and potentially to clients.
- **❌ Hardcoded database credentials**: Postgres uses `POSTGRES_USER=chainlit`, `POSTGRES_PASSWORD=chainlit`, `POSTGRES_DB=chainlit` hardcoded in `docker-compose.yml`. These are not sourced from secrets.
- **❌ Redis has no authentication**: The Redis container (`redis-stack-server`) is started with no password, no ACL, and port 6379 exposed to the host. Conversation checkpoints (which may contain customer PII) are unprotected.
- **❌ Customer PII in Redis**: LangGraph stores full conversation state (including customer profiles with medical conditions, financial data, etc.) in Redis without encryption at rest.
- **❌ SQLite databases contain sensitive customer data with no encryption at rest**: `customer_profile.db`, `model_predictions.db`, `application_profile.db` are plain SQLite files. No encryption at rest is applied.
- **❌ No input validation or prompt injection protection**: The `/chat` endpoint accepts free-form `message` strings and forwards them directly to the LLM agent. There is no sanitisation against prompt injection attacks.
- **❌ `_charts_sent` is a module-level in-memory set**: In a multi-worker deployment, this state is not shared across processes, creating potential for chart deduplication failures. More critically, it grows unboundedly — a memory leak under load.
- **❌ Overly broad CI/CD token**: `GH_TOKEN` is used to read source repos, write to `ai-delivery-outputs`, and post PR comments. There is no evidence of it being scoped to minimum required permissions (e.g., `contents: write` only on the output repo). [TODO: confirm token scopes and apply least privilege]
- **❌ `customer_similarity_dict.json` is committed to the repository** under `backend/tmp/` — this contains mappings of all customer IDs and their similar peers, which constitutes sensitive customer relationship data.
- **❌ No audit logging of LLM queries**: Customer profiles and risk assessment results sent to Anthropic's API are not logged locally for compliance/audit purposes. Data residency and processing agreements with Anthropic are [TODO: confirm].
- **❌ Gemini model name appears incorrect**: `gemini-3-flash-preview` does not correspond to a known Google model identifier as of the knowledge cutoff. This may be a placeholder or typo. [TODO: verify correct model name]

---

## 5. Environment Variables and Secrets

| Name | Required | Sensitivity | Where Set |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | **Critical** — paid API key | GitHub Actions secret; `.env` file for backend |
| `GH_TOKEN` | Yes (CI/CD) | **High** — GitHub API access token | GitHub Actions secret |
| `SENDGRID_API_KEY` | Yes (CI/CD) | **High** — email sending key | GitHub Actions secret |
| `GOOGLE_API_KEY` | Yes (if Gemini used) | **High** — paid API key | `.env` file for backend |
| `REDIS_HOST` | No | Low | `docker-compose.yml` environment; defaults to `localhost` |
| `DATABASE_URL` | Yes (frontend) | **Medium** — DB credentials in URL | `docker-compose.yml` environment (hardcoded credentials) |
| `BACKEND_URL` | Yes (frontend) | Low | `docker-compose.yml` environment |
| `POSTGRES_USER` | Yes (postgres) | **Medium** | `docker-compose.yml` environment (hardcoded: `chainlit`) |
| `POSTGRES_PASSWORD` | Yes (postgres) | **High** | `docker-compose.yml` environment (**hardcoded: `chainlit`** — must be moved to secret) |
| `POSTGRES_DB` | Yes (postgres) | Low | `docker-compose.yml` environment (hardcoded: `chainlit`) |
| `OUTPUT_REPO` | No (CI/CD) | Low | GitHub Actions workflow env; defaults to `ai-delivery-outputs` |
| `OUTPUT_REPO_OWNER` | No (CI/CD) | Low | GitHub Actions workflow env; derived from `github.repository_owner` |
| `NOTIFY_EMAIL` | No (CI/CD) | Low | GitHub Actions workflow env (hardcoded: `kylo.deng@capco.com`) |
| `SENDER_EMAIL` | No (CI/CD) | Low | GitHub Actions workflow env (hardcoded: `noreply@ai-delivery.capco.com`) |
| `SOURCE_REPO_OWNER` | No (CI/CD) | Low | GitHub Actions workflow env |
| `SOURCE_REPO_NAME` | No (CI/CD) | Low | GitHub Actions workflow env |
| `GITHUB_RUN_URL` | No (CI/CD) | Low | GitHub Actions workflow env |
| `TEST_MODE` | No (Tool 4) | Low | GitHub Actions workflow env; defaults to `generate` |
| `REVIEW_MODE` | No (Tool 1) | Low | Set at runtime within workflow step |
| `PR_NUMBER` | No (Tool 1) | Low | Set at runtime within workflow step |
| `RELEASE_VERSION` | No (Tools 3, 5) | Low | Set at runtime within workflow step |
| `PROJECT_NAME` | No (Tool 3) | Low | Set at runtime within workflow step |
| `UAT_MODE` | No (Tool 5) | Low | Set at runtime within workflow step |
| `UAT_RESULTS_PATH` | No (Tool 5) | Low | Set at runtime within workflow step |
| `USER_STORIES` | No (Tool 5) | Low | Set at runtime within workflow step |

> **[TODO: confirm]** Where exactly is the backend `.env` file generated/sourced? It is referenced in `docker-compose.yml` (`env_file: .env`) and in `load_dotenv()` calls, but no `.env.example` or secret management solution is documented.

---

## 6. Dependencies

| Dependency | Type | Purpose |
|---|---|---|
| Anthropic API (Claude Sonnet 4, Claude Haiku 4.5) | External managed API | All LLM inference: agent routing, specialist assessments, aggregation, CI/CD tools |
| Google Generative AI (Gemini) | External managed API | Optional/alternative LLM provider (appears misconfigured — see risks) |
| LangChain / LangGraph | Python library (open source) | Agent state graph, tool dispatch, streaming event system |
| Chainlit | Python framework | Frontend chat UI and session management |
| FastAPI + SSE-Starlette | Python framework | Backend HTTP/SSE API server |
| Redis Stack (redis-stack-server 7.2.0) | Docker container | LangGraph checkpoint persistence (conversation memory) |
| PostgreSQL 16 | Docker container | Chainlit user/session data persistence |
| CatBoost (pre-trained model, not live) | ML library | Risk classification model — predictions are pre-computed in `model_predictions.db`; the model card and feature importance are served as reference data |
| SendGrid API | External managed API | Email notifications from CI/CD workflow completions |
| GitHub API (api.github.com) | External managed API | CI/CD: fetching source files, PR diffs, writing output files, posting PR comments |
| `ai-delivery-outputs` (GitHub repo, `kylodeng/ai-delivery-outputs`) | External GitHub repository | Destination for all AI-generated documentation, test files, and UAT packs |
| `pydantic` | Python library | Structured output validation for `UnderwritingReport` |
| `langchain-anthropic`, `langchain-google-genai` | Python libraries | LangChain provider integrations |
| `python-dotenv` | Python library | Loading `.env` files in backend |
| `redis.asyncio` | Python library | Async Redis client for LangGraph checkpointer |

---

## 7. Deployment Instructions

### Prerequisites

- Docker and Docker Compose installed
- `.env` file created in the repository root with at minimum:

```bash
ANTHROPIC_API_KEY=sk-ant-...
GOOGLE_API_KEY=...        # Only required if using Gemini model
```

- SQLite database files present under `./database/`:
  - `customer_profile.db`
  - `feature_importance.db`
  - `model_predictions.db`
  - `application_profile.db`

### Start All Services

```bash
# Build and start all containers (Redis, Postgres, Backend, Frontend)
docker compose up --build

# Or run in detached mode
docker compose up --build -d

# Follow backend logs
docker compose logs -f backend

# Follow all logs
docker compose logs -f
```

### Verify Deployment