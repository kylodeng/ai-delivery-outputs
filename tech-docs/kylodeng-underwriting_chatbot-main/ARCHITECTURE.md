# Architecture Document: kylodeng/underwriting_chatbot-main

---

## 1. Overview

The Underwriting Chatbot is an AI-assisted insurance underwriting platform that enables underwriters to assess customer risk profiles through a conversational chat interface. The system ingests pre-built SQLite databases of customer profiles, financial data, KYC records, and ML model predictions, then orchestrates multiple specialist LLM agents (powered by Anthropic Claude and Google Gemini) to produce structured underwriting reports covering finance, health, life, and other risk domains. A FastAPI backend streams results to a frontend over Server-Sent Events (SSE), while Redis provides conversational memory persistence and PostgreSQL stores Chainlit session state. The repository also includes a suite of five AI-powered CI/CD automation tools (code review, tech docs, business docs, auto-testing, and UAT facilitation) that use Claude to generate artefacts and push them to a separate output repository.

---

## 2. Resources Deployed

| Resource | Type | Cloud Provider | Purpose |
|---|---|---|---|
| `backend` | Docker container (FastAPI) | Self-hosted (Docker Compose) | Serves `/chat` SSE endpoint and `/health`; orchestrates LLM agents |
| `frontend` | Docker container | Self-hosted (Docker Compose) | Chainlit-based chat UI served on port 8080 |
| `redis` | Docker container (redis-stack-server 7.2.0-v14) | Self-hosted (Docker Compose) | LangGraph checkpoint store for conversational memory |
| `postgres` | Docker container (postgres:16-alpine) | Self-hosted (Docker Compose) | Chainlit session/user state storage |
| `customer_profile.db` | SQLite file (read-only bind mount) | Self-hosted | Customer demographic and profile data |
| `feature_importance.db` | SQLite file (read-only bind mount) | Self-hosted | ML model feature importance data |
| `model_predictions.db` | SQLite file (read-only bind mount) | Self-hosted | Pre-computed CatBoostClassifier risk predictions |
| `application_profile.db` | SQLite file (read-only bind mount) | Self-hosted | Insurance application profile data |
| `postgres_data` | Docker named volume | Self-hosted | Persistent PostgreSQL data storage |
| `Claude claude-sonnet-4-20250514` | External LLM API | Anthropic | Deep/aggregator underwriting assessments |
| `Claude claude-haiku-4-5-20251001` | External LLM API | Anthropic | Fast/agent-level underwriting assessments |
| `gemini-3-flash-preview` | External LLM API | Google Cloud (Vertex/AI Studio) | Alternative LLM provider (configured, optional) |
| GitHub Actions runners (`ubuntu-latest`) | CI/CD compute | GitHub (Microsoft Azure) | Five AI tooling workflows |
| `ai-delivery-outputs` (separate repo) | GitHub repository | GitHub | Stores generated docs, test files, UAT packs |
| SendGrid | Email API | Twilio/SendGrid | Notification delivery for CI/CD tool outputs |

---

## 3. Data Flow

### Runtime Chat Flow

1. **User input**: An underwriter types a message in the Chainlit frontend (port 8080).
2. **HTTP POST to backend**: The frontend sends a `POST /chat` request to `http://backend:8000` with `message`, `session_id`, `model`, `temperature`, and `mode` fields.
3. **Agent instantiation**: `build_agent()` in `graph.py` constructs a LangGraph agent with the requested model (Anthropic or Gemini) and attaches a Redis-backed `AsyncRedisSaver` checkpointer, keyed by `thread_id` (session ID).
4. **Tool dispatch — profile retrieval**: The agent LLM decides to call `get_customer_profile` tool, which queries `customer_profile.db` (and related SQLite databases) for the requested customer ID.
5. **Tool dispatch — lookalike**: Optionally, the agent calls `customer_lookalike`, which reads `customer_similarity_dict.json` from `backend/tmp/` to return similar customer IDs.
6. **Tool dispatch — underwriting assessment**: The agent calls `run_underwriting_assessment` with the profile string. This triggers `_run_underwriting_assessment()` in `assessment.py`.
7. **Parallel specialist LLM calls**: Up to 4 concurrent async calls (semaphore-limited) are made to the specialist LLM (Claude Haiku by default) with domain-specific system prompts from `assessment_criterias.json` (finance, health, life, etc.).
8. **Aggregation**: All specialist outputs are collected and passed to the aggregator LLM (Claude Sonnet) using `structured_llm` (with Pydantic `UnderwritingReport` schema enforcement), producing a structured JSON report.
9. **SSE streaming**: The backend streams LLM token chunks, tool start/end events, and chart data back to the frontend as Server-Sent Events. The frontend renders the streamed response progressively.
10. **Checkpoint persistence**: LangGraph writes conversation state to Redis after each step, enabling multi-turn conversations within a session.
11. **Chart buffering**: Any chart data emitted by tool calls is buffered and sent after the response text stream completes.

### CI/CD Tooling Flow (Tools 1–5)

1. A GitHub event (PR, push to main, tag, schedule, or manual dispatch) triggers the relevant workflow.
2. The workflow checks out the source repository and installs `anthropic` and `requests`.
3. The Python script reads source/IaC files from the GitHub API (via `shared.py:get_repo_files`) or fetches a PR diff.
4. File contents are assembled into prompts and sent to Claude (claude-sonnet-4-6) via the Anthropic API.
5. Claude's response (JSON or Markdown) is parsed and written as a file to the `ai-delivery-outputs` repository via GitHub API (`write_output_file`).
6. A notification email is sent via SendGrid to `kylo.deng@capco.com`.
7. For PR reviews (Tool 1), a comment is also posted on the PR via GitHub API.

---

## 4. Security Posture

### What Is Secured

- **SQLite databases are mounted read-only** (`ro` flag in Docker Compose volumes) — prevents the backend from modifying source data.
- **Secrets managed via GitHub Actions secrets** (`ANTHROPIC_API_KEY`, `GH_TOKEN`, `SENDGRID_API_KEY`) — not hardcoded in workflow files.
- **Environment variables via `.env` file** for the backend container — not baked into the image.
- **LLM prompt injection mitigation**: System prompts explicitly instruct agents not to reveal internal instructions or tool details.
- **Semaphore limiting** on parallel LLM calls (max 4 concurrent) — prevents runaway API consumption.
- **Token caps** on specialist (1,500) and aggregator (8,000) LLMs — limits runaway cost/output.

### Gaps and Concerns

- ⚠️ **No encryption at rest**: SQLite databases are plain files on the host filesystem with no encryption. These contain PII (customer profiles, KYC data, financial data).
- ⚠️ **No encryption in transit between containers**: Docker Compose internal networking uses plain HTTP between `frontend → backend` and `backend → redis/postgres`. No TLS on internal links.
- ⚠️ **PostgreSQL credentials are hardcoded** in `docker-compose.yml` (`chainlit`/`chainlit`) — these must be rotated and injected via secrets in any non-local environment.
- ⚠️ **Redis has no authentication or TLS** configured — any process on the Docker network can read/write conversation checkpoints, which may contain PII.
- ⚠️ **CORS is fully open** (`allow_origins=["*"]`, `allow_methods=["*"]`, `allow_headers=["*"]`) in `main.py` — this is acceptable for local dev but must be restricted in production.
- ⚠️ **`GH_TOKEN` scope unknown** [TODO: verify minimum required scopes — `repo` write to `ai-delivery-outputs` plus PR comment rights on source repo; confirm no org-wide write access].
- ⚠️ **`customer_similarity_dict.json` stored in `backend/tmp/`** — this file contains customer ID mappings and is committed to the repository. If the repository is public, this is a data exposure risk.
- ⚠️ **No authentication on the `/chat` endpoint** — any client that can reach port 8000 can submit queries. There is no API key, JWT, or session validation at the FastAPI layer.
- ⚠️ **No rate limiting** on `/chat` — susceptible to abuse/cost amplification via LLM API calls.
- ⚠️ **`agent.graph` imports `create_agent` from `langchain.agents`** — this is a legacy import path; behaviour may differ from LangGraph's `StateGraph`. [TODO: confirm correct import and agent behaviour].
- ⚠️ **Gemini model name `gemini-3-flash-preview`** does not exist as of knowledge cutoff — likely a misconfiguration that would fail at runtime.
- ℹ️ **No IAM roles to audit** — this is a self-hosted Docker deployment with no cloud IAM. If deployed to a cloud VM, the VM's instance role should be reviewed.

---

## 5. Environment Variables and Secrets

| Name | Required | Sensitivity | Where Set |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | 🔴 High — billable API key | GitHub Actions secret; `.env` file for backend |
| `GOOGLE_API_KEY` | Yes (if Gemini used) | 🔴 High — billable API key | `.env` file for backend |
| `GH_TOKEN` | Yes (CI/CD tools) | 🔴 High — GitHub repo write access | GitHub Actions secret |
| `SENDGRID_API_KEY` | Yes (CI/CD tools) | 🔴 High — email sending capability | GitHub Actions secret |
| `REDIS_HOST` | No | 🟢 Low | Docker Compose `environment`; defaults to `localhost` |
| `POSTGRES_USER` | Yes | 🟡 Medium | Hardcoded in `docker-compose.yml` as `chainlit` |
| `POSTGRES_PASSWORD` | Yes | 🔴 High | Hardcoded in `docker-compose.yml` as `chainlit` — **must be moved to secret** |
| `POSTGRES_DB` | No | 🟢 Low | Hardcoded in `docker-compose.yml` as `chainlit` |
| `DATABASE_URL` | Yes (frontend) | 🟡 Medium | Docker Compose `environment` — contains plaintext password |
| `BACKEND_URL` | Yes (frontend) | 🟢 Low | Docker Compose `environment` |
| `OUTPUT_REPO` | No (CI/CD) | 🟢 Low | Workflow `env`; defaults to `ai-delivery-outputs` |
| `OUTPUT_REPO_OWNER` | No (CI/CD) | 🟢 Low | Workflow `env`; derived from `github.repository_owner` |
| `NOTIFY_EMAIL` | No (CI/CD) | 🟢 Low | Hardcoded in workflow `env` as `kylo.deng@capco.com` |
| `SENDER_EMAIL` | No (CI/CD) | 🟢 Low | Hardcoded in workflow `env` as `noreply@ai-delivery.capco.com` |
| `REVIEW_MODE` | No (Tool 1) | 🟢 Low | Set dynamically in workflow step |
| `PR_NUMBER` | No (Tool 1) | 🟢 Low | Set dynamically in workflow step |
| `RELEASE_VERSION` | No (Tools 3, 5) | 🟢 Low | Set dynamically in workflow step |
| `PROJECT_NAME` | No (Tool 3) | 🟢 Low | Set dynamically in workflow step |
| `TEST_MODE` | No (Tool 4) | 🟢 Low | Workflow `env`; defaults to `generate` |
| `UAT_MODE` | No (Tool 5) | 🟢 Low | Set dynamically in workflow step |
| `UAT_RESULTS_PATH` | No (Tool 5) | 🟢 Low | Set dynamically from workflow input |

---

## 6. Dependencies

### External Services / APIs

| Dependency | Purpose | Notes |
|---|---|---|
| **Anthropic API** | LLM inference (Claude Haiku, Claude Sonnet) | Billable; requires `ANTHROPIC_API_KEY` |
| **Google Generative AI API** | LLM inference (Gemini) | Billable; requires `GOOGLE_API_KEY`; model name `gemini-3-flash-preview` appears invalid — [TODO: verify correct model name] |
| **SendGrid API** | Email notifications from CI/CD tools | Requires `SENDGRID_API_KEY`; sender domain `ai-delivery.capco.com` must be verified |
| **GitHub API** | File reads, PR comments, output repo writes | Requires `GH_TOKEN` with appropriate scopes |

### Python Libraries (Backend)

| Library | Purpose |
|---|---|
| `fastapi` | REST API framework |
| `sse-starlette` | Server-Sent Events streaming |
| `langchain` / `langchain-core` | Agent orchestration, tool definitions |
| `langchain-anthropic` | Anthropic LLM integration |
| `langchain-google-genai` | Google Gemini LLM integration |
| `langgraph` | Stateful agent graph execution |
| `redis` (asyncio) | Redis checkpoint client |
| `pydantic` | Data validation and structured output |
| `pyyaml` | Config file parsing |
| `python-dotenv` | `.env` file loading |
| `catboost` | [TODO: confirm if CatBoost is loaded at runtime or only used offline for pre-computing `model_predictions.db`] |

### Python Libraries (CI/CD Scripts)

| Library | Purpose |
|---|---|
| `anthropic` | Direct Claude API calls |
| `requests` | GitHub API and SendGrid API calls |

### Other Repositories

| Repo | Owner | Purpose |
|---|---|---|
| `ai-delivery-outputs` | `kylodeng` (same org) | Receives generated docs, test files, UAT packs from all five CI/CD tools |

---

## 7. Deployment Instructions

### Prerequisites

- Docker and Docker Compose installed
- A `.env` file in the repository root with at minimum:

```bash
ANTHROPIC_API_KEY=sk-ant-...
GOOGLE_API_KEY=AIza...        # Only if using Gemini
```

- SQLite database files present under `./database/`:
  - `customer_profile.db`
  - `feature_importance.db`
  - `model_predictions.db`
  - `application_profile.db`

### Local Deployment

```bash
# 1. Clone the repository
git clone https://github.com/kylodeng/underwriting_chatbot-main.git
cd underwriting_chatbot-main

# 2. Create the .env file
cp .env.example .env          # [TODO: confirm .env.example exists]
# Edit .env and populate ANTHROPIC_API_KEY (and GOOGLE_API_KEY if needed)

# 3. Build and start all services
docker compose up --build

# 4. Verify backend health
curl http://localhost:8000/health
# Expected: {"status": "ok"}

# 5. Access the frontend
open http://localhost:8080
```

### Stopping / Teardown

```bash
# Stop containers but retain volumes
docker compose down

# Stop containers AND delete persistent data (postgres_data volume)
docker compose down -v
```

### Database Initialisation

PostgreSQL is auto-initialised on first run via:

```bash
./postgres/init.sql   # [TODO: confirm this file exists and document its schema]
```

### CI/CD Tooling Setup

For GitHub Actions workflows to function, the following repository secrets must be set in the source repository settings:

```
Settings → Secrets and variables → Actions → New repository secret

ANTHROPIC_API_KEY   = <Anthropic API key>
GH_TOKEN            = <GitHub PAT with repo write on ai-delivery-outputs>
SENDGRID_API_KEY    = <SendGrid API key>
```

---

## 8. Risks and TODOs

### Extracted from Code

| Location |