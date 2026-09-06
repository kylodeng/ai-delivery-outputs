# Architecture Document — kylodeng/underwriting_chatbot-main

---

## 1. Overview

The Underwriting Chatbot is an AI-assisted insurance underwriting platform that enables underwriters to query customer profiles, retrieve similar customer cohorts (lookalike analysis), and trigger multi-specialist LLM risk assessments against a structured customer database. A FastAPI backend orchestrates a LangGraph/LangChain agent that routes user intents to three tools — customer profile lookup, customer lookalike search, and a parallel multi-domain underwriting assessment engine — then streams results (text, tool events, charts) to a Chainlit-based frontend over Server-Sent Events (SSE). The system additionally runs five CI/CD-integrated AI automation workflows (code review, tech docs, business docs, auto-testing, UAT facilitation) powered by Anthropic Claude via GitHub Actions.

---

## 2. Resources Deployed

| Resource | Type | Cloud Provider | Purpose |
|---|---|---|---|
| `backend` | Docker container (FastAPI) | Local / Self-hosted | LangGraph agent, underwriting assessment API, SSE streaming endpoint |
| `frontend` | Docker container (Chainlit) | Local / Self-hosted | Chat UI served on port 8080 |
| `redis` (redis-stack-server:7.2.0-v14) | Docker container | Local / Self-hosted | LangGraph conversation checkpoint store (session memory) |
| `postgres` (postgres:16-alpine) | Docker container | Local / Self-hosted | Chainlit user session and metadata persistence |
| `customer_profile.db` | SQLite file (read-only mount) | Local / Self-hosted | Customer demographic and KYC data |
| `feature_importance.db` | SQLite file (read-only mount) | Local / Self-hosted | CatBoost model feature importance scores |
| `model_predictions.db` | SQLite file (read-only mount) | Local / Self-hosted | Pre-computed ML risk classification predictions |
| `application_profile.db` | SQLite file (read-only mount) | Local / Self-hosted | Insurance application data |
| `postgres_data` | Docker named volume | Local / Self-hosted | Persistent PostgreSQL data storage |
| Anthropic Claude (claude-haiku-4-5, claude-sonnet-4) | External LLM API | Anthropic (cloud) | Agent reasoning, specialist assessments, aggregation, CI tools |
| Google Gemini (gemini-3-flash-preview) | External LLM API | Google Cloud | Alternative LLM provider (configured, not default) |
| GitHub Actions runners | CI/CD compute | GitHub (cloud) | Five AI workflow automation tools |
| SendGrid | Email API | Twilio/SendGrid (cloud) | Notification delivery for CI workflow outputs |
| `ai-delivery-outputs` | GitHub repository | GitHub | Output store for generated docs, test files, UAT packs |

---

## 3. Data Flow

### Runtime Chat Flow

1. **User submits message** via the Chainlit frontend (port 8080) over HTTP POST.
2. **Frontend routes** the request to `backend:8000/chat` with `session_id`, `model`, `mode`, `temperature`, and message payload.
3. **FastAPI `/chat` endpoint** calls `build_agent()`, which instantiates a LangGraph agent with the selected LLM (Anthropic Haiku or Sonnet, or Gemini), a Redis-backed `AsyncRedisSaver` checkpointer for session memory, and three registered tools.
4. **Agent LLM reasons** over the system prompt + conversation history (loaded from Redis by `thread_id`) and emits a JSON tool-call directive or a final answer.
5. **Tool dispatch — `get_customer_profile`**: queries `customer_profile.db` (SQLite, read-only) and returns structured customer data.
6. **Tool dispatch — `customer_lookalike`**: looks up `customer_similarity_dict.json` (pre-computed similarity index) and returns a list of similar customer IDs.
7. **Tool dispatch — `run_underwriting_assessment`**: receives the customer profile string, fans out **parallel async calls** (semaphore-capped at 4) to a specialist LLM (Claude Haiku, tagged `"thinking"`) for each assessment domain (`finance`, `health`, `life`, etc.) using prompts from `assessment_criterias.json`. Results are collected and passed to an **aggregator LLM** (Claude Haiku with structured output) which produces a typed `UnderwritingReport` Pydantic object. The report is then rendered to a markdown string.
8. **SSE streaming**: All agent events (`on_tool_start`, `on_tool_end`, `on_chat_model_stream`) are streamed back to the frontend as SSE events (`tool_start`, `tool_end`, `thinking`, `response`, `chart`, `done`). Chart data buffered during tool calls is flushed after the text response.
9. **Conversation state** is checkpointed back to Redis after each turn, keyed by `session_id`.
10. **PostgreSQL** stores Chainlit-managed user session metadata (authentication, chat history display) — initialized via `postgres/init.sql` at startup.

### CI/CD AI Tooling Flow

11. **GitHub event** (PR, push to main, tag, schedule, or manual dispatch) triggers one of five GitHub Actions workflows.
12. **Workflow runner** checks out the source repo, installs `anthropic` and `requests`, and calls the corresponding Python script (e.g., `tool1_code_review.py`).
13. **Script reads** source/IaC files from the GitHub API (via `GH_TOKEN`) or from the diff of a PR.
14. **Claude API** (`claude-sonnet-4-6` in `shared.py`) processes the files and returns structured output (JSON for code review/testing/UAT analysis, Markdown for docs).
15. **Output is committed** to the `ai-delivery-outputs` repository via the GitHub Contents API.
16. **SendGrid** sends an email notification to `kylo.deng@capco.com` with a link to the generated artifact.

---

## 4. Security Posture

### ✅ What Is Secured

- **SQLite databases mounted read-only** (`ro` flag in docker-compose) — backend cannot write to production data files.
- **Secrets managed via GitHub Actions secrets** (`ANTHROPIC_API_KEY`, `GH_TOKEN`, `SENDGRID_API_KEY`) — not hardcoded in workflow YAML.
- **Backend `.env` file** used via `env_file` directive in docker-compose — secrets not embedded in the image.
- **System prompt confidentiality**: agent is instructed never to reveal its internal instructions or tool list to the user.
- **Semaphore on parallel LLM calls** — prevents unbounded concurrent external API calls (capped at 4).

### ❌ Gaps and Concerns

- **PostgreSQL uses hardcoded credentials** (`POSTGRES_USER: chainlit`, `POSTGRES_PASSWORD: chainlit`) in plain text in `docker-compose.yml`. These must be rotated and injected as secrets before any non-local deployment.
- **Redis has no authentication** — the Redis container exposes port 6379 with no password, ACL, or TLS configured. Any process on the Docker network (or host, since the port is bound to `0.0.0.0:6379`) can read/write conversation checkpoints.
- **Redis conversation data is unencrypted at rest and in transit** — session memory including customer PII flowing through the agent is stored in Redis without encryption.
- **PostgreSQL port 5432 is publicly bound** (`"5432:5432"`) — should be removed or restricted to localhost in any non-local environment.
- **Redis port 6379 is publicly bound** (`"6379:6379"`) — same issue.
- **CORS is fully open**: `allow_origins=["*"]`, `allow_methods=["*"]`, `allow_headers=["*"]` — accepts requests from any origin. This must be restricted to the frontend origin before production deployment.
- **No authentication on the `/chat` endpoint** — any client that can reach port 8000 can query any customer's data by `session_id`.
- **`customer_similarity_dict.json` is committed to the repository** — contains mappings of 10,000+ customer IDs in plaintext in the source tree.
- **No encryption of SQLite databases at rest** — customer PII, financial data, and risk scores are stored in unencrypted SQLite files.
- **`GH_TOKEN` scope is unknown** — [TODO: confirm GH_TOKEN has minimum required scopes (contents:write on output repo only); if it has broad repo access it is overly permissive].
- **CI scripts fetch up to 20 source files and send them to Anthropic's API** — customer data in source files (e.g., `backend/tmp/customer_similarity_dict.json`) may be included and transmitted to a third-party API.
- **No rate limiting or input validation** on the `/chat` endpoint beyond Pydantic model parsing.
- **No secrets scanning** in CI pipelines — no step checks for accidental secret commits.

---

## 5. Environment Variables and Secrets

| Name | Required | Sensitivity | Where Set |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | **High** — LLM API key | GitHub Actions secret; backend `.env` file |
| `GH_TOKEN` | Yes | **High** — GitHub PAT with repo write access | GitHub Actions secret |
| `SENDGRID_API_KEY` | Yes | **High** — email API key | GitHub Actions secret |
| `GOOGLE_API_KEY` | Yes (if Gemini used) | **High** — GCP API key | Backend `.env` file |
| `REDIS_HOST` | No | Low | `docker-compose.yml` environment block (default: `localhost`) |
| `POSTGRES_USER` | Yes | Medium | Hardcoded in `docker-compose.yml` (`chainlit`) ⚠️ |
| `POSTGRES_PASSWORD` | Yes | **High** | Hardcoded in `docker-compose.yml` (`chainlit`) ⚠️ |
| `POSTGRES_DB` | Yes | Low | Hardcoded in `docker-compose.yml` (`chainlit`) |
| `DATABASE_URL` | Yes | Medium | `docker-compose.yml` frontend environment block |
| `BACKEND_URL` | Yes | Low | `docker-compose.yml` frontend environment block |
| `OUTPUT_REPO` | No | Low | GitHub Actions env (default: `ai-delivery-outputs`) |
| `OUTPUT_REPO_OWNER` | No | Low | GitHub Actions env (inferred from `github.repository_owner`) |
| `NOTIFY_EMAIL` | No | Low | GitHub Actions env (`kylo.deng@capco.com`) |
| `SENDER_EMAIL` | No | Low | GitHub Actions env (`noreply@ai-delivery.capco.com`) |
| `REVIEW_MODE` | No | Low | Set at runtime by CI workflow step |
| `PR_NUMBER` | No | Low | Set at runtime by CI workflow step |
| `RELEASE_VERSION` | No | Low | Set at runtime by CI workflow step |
| `PROJECT_NAME` | No | Low | Set at runtime by CI workflow step |
| `UAT_MODE` | No | Low | Set at runtime by CI workflow step |
| `TEST_MODE` | No | Low | Set at runtime by CI workflow step |

---

## 6. Dependencies

### External Services and APIs

| Dependency | Type | Purpose | Notes |
|---|---|---|---|
| Anthropic API | REST API | LLM inference (Claude Haiku, Sonnet) | Primary LLM for both runtime and CI tooling |
| Google Generative AI API | REST API | LLM inference (Gemini flash) | Configured as alternative; `GOOGLE_API_KEY` required |
| SendGrid API | REST API | Email notifications from CI workflows | Used by all 5 GitHub Actions tools |
| GitHub API (api.github.com) | REST API | File fetching, PR comments, output repo writes | Requires `GH_TOKEN` PAT |

### Key Python Libraries

| Library | Version Pinned | Purpose |
|---|---|---|
| `langchain` / `langchain-core` | No | Agent framework, tool abstraction |
| `langchain-anthropic` | No | Anthropic LLM integration |
| `langchain-google-genai` | No | Google Gemini integration |
| `langgraph` | No | Stateful agent graph with Redis checkpointing |
| `fastapi` | No | REST API and SSE streaming server |
| `chainlit` | No | Chat frontend framework |
| `pydantic` | No | Data modeling and structured LLM output |
| `anthropic` | No | Direct Anthropic SDK (used in CI scripts) |
| `redis` (asyncio) | No | Agent session memory backend |
| `asyncpg` | No | Async PostgreSQL driver |
| `python-dotenv` | No | Environment variable loading |
| `pyyaml` | No | Config file parsing |
| `sse-starlette` | No | SSE response streaming |

### Other Repositories

| Repository | Relationship | Purpose |
|---|---|---|
| `{owner}/ai-delivery-outputs` | Output target | Receives generated docs, test files, UAT packs from CI workflows |

---

## 7. Deployment Instructions

### Prerequisites

- Docker and Docker Compose v2 installed
- `.env` file in the repo root containing at minimum:
  ```
  ANTHROPIC_API_KEY=<your-key>
  GOOGLE_API_KEY=<your-key>   # only if using Gemini
  ```
- SQLite database files present under `./database/`:
  - `customer_profile.db`
  - `feature_importance.db`
  - `model_predictions.db`
  - `application_profile.db`

### Local Deployment

```bash
# Clone the repository
git clone https://github.com/kylodeng/underwriting_chatbot-main.git
cd underwriting_chatbot-main

# Create the .env file
cp .env.example .env   # [TODO: confirm .env.example exists]
# Edit .env and populate ANTHROPIC_API_KEY and GOOGLE_API_KEY

# Build and start all services
docker compose up --build

# Verify backend health
curl http://localhost:8000/health

# Access the chat UI
open http://localhost:8080
```

### Stopping and Cleanup

```bash
# Stop all services
docker compose down

# Stop and remove volumes (WARNING: deletes PostgreSQL data)
docker compose down -v
```

### GitHub Actions CI Tools

The five AI workflow tools require these secrets set in the repository's GitHub Actions settings:

```
Settings → Secrets and variables → Actions → New repository secret

ANTHROPIC_API_KEY   = <your Anthropic key>
GH_TOKEN            = <PAT with contents:write on ai-delivery-outputs>
SENDGRID_API_KEY    = <your SendGrid key>
```

Trigger manually:

```bash
# Trigger code review on a PR
gh workflow run tool1_code_review.yml -f review_mode=pr -f pr_number=<PR_NUMBER>

# Trigger tech doc generation
gh workflow run tool2_tech_docs.yml

# Trigger business doc generation
gh workflow run tool3_business_docs.yml -f project_name="Underwriting Chatbot" -f release_version="1.0.0"

# Trigger test generation
gh workflow run tool4_auto_testing.yml -f test_mode=generate

# Trigger UAT pack generation
gh workflow run tool5_uat.yml -f uat_mode=generate -f release_version="1.0.0"
```

---

## 8. Risks and TODOs

### Extracted from Code

| Location | Item |
|---|---|
| `backend/agent/graph.py` line 1 | `# TODO: migrate Redis to an external service (e.g. Azure Cache for Redis)` — current Redis is ephemeral; session memory lost if container restarts |
| `backend/modules/LLMS.py` | `# TODO: add more providers here` — Azure OpenAI and OpenAI are stubbed as `None`; calling them raises `ValueError` at runtime |
| `backend/main.py` | Commented-out `lifespan` context manager — agent lifecycle management is incomplete; a new agent instance is built per request |

### Missing Disaster Recovery and Operational Concerns

| Risk | Severity | Detail |
|---|---|---|
| **No DR or backup strategy** | High | No database backup, no Redis persistence configuration (RDB/AOF), no cross-region replication. Single-node SQLite files are the source of truth with no documented backup procedure. |
| **No monitoring or alerting** | High | No APM, no log aggregation, no uptime checks, no LL