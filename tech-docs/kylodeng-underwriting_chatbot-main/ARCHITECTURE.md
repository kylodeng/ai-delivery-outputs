# Architecture Document: `kylodeng/underwriting_chatbot-main`

---

## 1. Overview

The Underwriting Chatbot is an AI-powered insurance underwriting assistant that enables underwriters to assess customer risk profiles through a conversational interface. The system ingests structured customer data from multiple SQLite databases (customer profiles, financial data, KYC, application profiles, and model predictions), routes queries through a LangGraph-orchestrated multi-agent pipeline backed by Anthropic Claude (primary) and Google Gemini (secondary) LLMs, and produces structured `UnderwritingReport` outputs covering finance, health, life, and KYC assessment categories. A CatBoost ML model pre-scores risk classifications which are surfaced alongside LLM-generated narrative assessments. The frontend (Chainlit-based) communicates with a FastAPI backend over Server-Sent Events (SSE) for streaming responses, with Redis providing LangGraph conversation checkpointing and PostgreSQL storing chat history. The repository also ships a suite of five GitHub Actions–powered AI delivery tools (code review, tech docs, business docs, auto-testing, and UAT facilitation) that use Claude to automate SDLC artefact generation.

---

## 2. Resources Deployed

| Resource | Type | Cloud Provider | Purpose |
|---|---|---|---|
| `backend` | Docker container (FastAPI, Python) | Local / [TODO: target cloud runtime?] | LLM orchestration, SSE streaming, underwriting assessment API |
| `frontend` | Docker container (Chainlit) | Local / [TODO: target cloud runtime?] | Chat UI for underwriters |
| `redis` (redis-stack-server 7.2.0) | Docker container | Local / [TODO: target cloud?] | LangGraph conversation checkpointing (in-memory, ephemeral) |
| `postgres` (postgres:16-alpine) | Docker container | Local / [TODO: target cloud?] | Chainlit chat history persistence |
| `customer_profile.db` | SQLite file (read-only mount) | Local filesystem | Customer demographic and profile data |
| `feature_importance.db` | SQLite file (read-only mount) | Local filesystem | CatBoost model feature importance data |
| `model_predictions.db` | SQLite file (read-only mount) | Local filesystem | Pre-computed CatBoost risk classification predictions |
| `application_profile.db` | SQLite file (read-only mount) | Local filesystem | Insurance application profile data |
| `postgres_data` | Docker named volume | Local filesystem | PostgreSQL data persistence |
| Anthropic Claude API (claude-sonnet-4-20250514, claude-haiku-4-5-20251001) | External API | Anthropic (cloud) | Specialist assessment LLMs and fast agent LLM |
| Google Gemini API (gemini-3-flash-preview) | External API | Google Cloud | Alternative LLM provider (configured, usage optional) |
| GitHub Actions Runners (ubuntu-latest) | Ephemeral CI compute | GitHub (cloud) | AI delivery tool execution (5 workflows) |
| `ai-delivery-outputs` (separate GitHub repo) | GitHub repository | GitHub | Stores generated docs, test files, UAT packs |
| SendGrid | External email API | Twilio/SendGrid (cloud) | Notification emails from CI workflows |

---

## 3. Data Flow

### Runtime Chat Flow

1. **User sends a message** via the Chainlit frontend (port 8080); the frontend POSTs to `http://backend:8000/chat` with `{message, session_id, model, mode, temperature}`.
2. **FastAPI `/chat` endpoint** receives the request and calls `build_agent()`, which initialises a LangGraph agent with Redis-backed `AsyncRedisSaver` checkpointer using the provided `session_id` as `thread_id`.
3. **LangGraph agent (LLM)** — either `claude-haiku-4-5` (fast) or `claude-sonnet-4-20250514` (deep) — processes the message. The agent emits JSON tool-call instructions (`{"action": "tool_call", "tool_name": ..., "tool_args": ...}`).
4. **Tool: `get_customer_profile`** — queries the SQLite `customer_profile.db` (and related DBs mounted read-only at `/data/`) to retrieve structured customer data.
5. **Tool: `customer_lookalike`** — consults `backend/tmp/customer_similarity_dict.json` (pre-computed similarity index) to find comparable historical customer profiles.
6. **Tool: `run_underwriting_assessment`** — fans out to **parallel specialist LLM calls** (up to 4 concurrent via `asyncio.Semaphore(4)`) for each assessment category (finance, health, life, KYC, etc.) using prompts from `assessment_criterias.json`. Results are aggregated by a second LLM call that produces a structured `UnderwritingReport` Pydantic model (JSON).
7. **CatBoost model predictions** are read from `model_predictions.db` and surfaced alongside LLM assessments to provide a quantitative risk classification (`Preferred`, `Standard Plus`, `Standard`, `Substandard`).
8. **Streaming response** is sent back to the frontend as SSE events (`tool_start`, `tool_end`, `response`, `chart`, `done`) over the open HTTP connection.
9. **Redis** stores LangGraph conversation checkpoints per `session_id` so multi-turn context is maintained within a session.
10. **PostgreSQL** stores Chainlit chat history (user/assistant messages) for audit and UX persistence.

### CI/CD AI Tool Flow (GitHub Actions)

1. A trigger event (PR, push, tag, schedule, or `workflow_dispatch`) fires one of the five workflows.
2. The workflow runner checks out the source repo and installs `anthropic` + `requests`.
3. The Python script fetches source/IaC files from the GitHub API (using `GH_TOKEN`) and sends them to Claude (`claude-sonnet-4-6`) via the Anthropic API.
4. Claude returns generated artefacts (review JSON, markdown docs, test files, UAT packs).
5. Artefacts are committed to the `ai-delivery-outputs` GitHub repo via the GitHub Contents API.
6. A notification email is dispatched via SendGrid to `kylo.deng@capco.com`.
7. For code review workflows, a comment is also posted to the originating PR.

---

## 4. Security Posture

### ✅ What is secured

- **SQLite databases mounted read-only** (`ro` flag in `docker-compose.yml`) — prevents backend from writing to source-of-truth data files.
- **Secrets managed via GitHub Actions secrets** — `ANTHROPIC_API_KEY`, `GH_TOKEN`, `SENDGRID_API_KEY` are not hardcoded in workflow YAML.
- **ANTHROPIC_API_KEY and GOOGLE_API_KEY** are loaded from a `.env` file at runtime (not committed, loaded via `python-dotenv`).
- **LLM system prompt** instructs the agent to never disclose internal instructions or tool details to users.
- **Specialist LLM token cap** (`specialist_max_tokens: 1500`) limits runaway cost/output.

### ❌ Gaps and risks

- **No encryption at rest for SQLite databases** — `.db` files are plain files on the Docker host filesystem with no encryption. If the host is compromised, all customer PII and financial data is exposed in plaintext.
- **No encryption at rest for PostgreSQL** — the `postgres_data` Docker volume is unencrypted. No `ssl` mode configured in the connection string (`postgresql+asyncpg://chainlit:chainlit@postgres:5432/chainlit`).
- **Hardcoded PostgreSQL credentials** — `POSTGRES_USER: chainlit`, `POSTGRES_PASSWORD: chainlit` are committed in plain text in `docker-compose.yml`. These are weak, default credentials.
- **CORS configured as fully open** — `allow_origins=["*"]`, `allow_methods=["*"]`, `allow_headers=["*"]` in FastAPI. Any origin can call the backend API.
- **No authentication on `/chat` or `/health` endpoints** — the API is unauthenticated. Any actor with network access can query customer data and trigger LLM assessments.
- **Redis has no authentication** — the Redis container is deployed with no password (`requirepass` not set). Any process on the Docker network can read/write conversation checkpoints.
- **`GH_TOKEN` scope unknown** — [TODO: what permissions does this PAT have? If repo-scoped write, it can write to any repo owned by the account. Principle of least privilege not verified.]
- **Customer similarity dictionary stored as a plain JSON file** (`backend/tmp/customer_similarity_dict.json`) committed to the repository — exposes customer ID relationships in source control.
- **No network segmentation** — all Docker services share a default bridge network; any container can reach any other container on any port.
- **No TLS between frontend and backend** — traffic travels over plain HTTP (`http://backend:8000`).
- **No secrets scanning or SAST** in CI pipelines beyond Claude-based code review.
- **`.env` file dependency** — if `.env` is accidentally committed, all secrets are exposed. No `.gitignore` verification is visible in the provided files.
- **`GOOGLE_API_KEY`** is referenced in `LLMS.py` but not listed as a GitHub Actions secret — [TODO: is Gemini used in production? Where is this key managed?]

---

## 5. Environment Variables and Secrets

| Name | Required | Sensitivity | Where set |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | 🔴 High — billable API key | GitHub Actions secret; backend `.env` file |
| `GH_TOKEN` | Yes | 🔴 High — GitHub PAT with repo write access | GitHub Actions secret |
| `SENDGRID_API_KEY` | Yes (CI workflows) | 🔴 High — email sending capability | GitHub Actions secret |
| `GOOGLE_API_KEY` | Conditional | 🔴 High — billable API key | Backend `.env` file only [TODO: not in GH secrets] |
| `POSTGRES_USER` | Yes | 🟡 Medium | Hardcoded in `docker-compose.yml` (`chainlit`) |
| `POSTGRES_PASSWORD` | Yes | 🔴 High — DB password | Hardcoded in `docker-compose.yml` (`chainlit`) — **must be moved to secrets** |
| `POSTGRES_DB` | Yes | 🟢 Low | Hardcoded in `docker-compose.yml` (`chainlit`) |
| `DATABASE_URL` | Yes (frontend) | 🟡 Medium — contains password | Set in `docker-compose.yml` environment block (plaintext) |
| `REDIS_HOST` | Yes | 🟢 Low | Set in `docker-compose.yml` environment (`redis`) |
| `OUTPUT_REPO` | Yes (CI) | 🟢 Low | Hardcoded in workflow env (`ai-delivery-outputs`) |
| `OUTPUT_REPO_OWNER` | Yes (CI) | 🟢 Low | Derived from `github.repository_owner` |
| `NOTIFY_EMAIL` | Yes (CI) | 🟢 Low | Hardcoded in workflow env (`kylo.deng@capco.com`) |
| `SENDER_EMAIL` | Yes (CI) | 🟢 Low | Hardcoded in workflow env |
| `SOURCE_REPO_OWNER` | Yes (CI) | 🟢 Low | Derived from GitHub context |
| `SOURCE_REPO_NAME` | Yes (CI) | 🟢 Low | Derived from GitHub context |
| `GITHUB_RUN_URL` | Yes (CI) | 🟢 Low | Derived from GitHub context |
| `REVIEW_MODE` | Conditional (CI) | 🟢 Low | Set dynamically in workflow step |
| `PR_NUMBER` | Conditional (CI) | 🟢 Low | Set dynamically in workflow step |
| `RELEASE_VERSION` | Conditional (CI) | 🟢 Low | Set dynamically in workflow step |
| `PROJECT_NAME` | Conditional (CI) | 🟢 Low | Set dynamically in workflow step |
| `TEST_MODE` | Conditional (CI) | 🟢 Low | Set from `workflow_dispatch` input |
| `UAT_MODE` | Conditional (CI) | 🟢 Low | Set dynamically in workflow step |
| `UAT_RESULTS_PATH` | Conditional (CI) | 🟢 Low | Set from `workflow_dispatch` input |

---

## 6. Dependencies

| Dependency | Type | Purpose | Notes |
|---|---|---|---|
| Anthropic Claude API | External LLM API | Primary AI backbone for underwriting assessment and all CI tools | claude-sonnet-4-20250514, claude-haiku-4-5-20251001, claude-sonnet-4-6 (three different model IDs in use — inconsistency flagged) |
| Google Gemini API | External LLM API | Alternative LLM provider | gemini-3-flash-preview; [TODO: model name appears non-standard — verify correct Gemini model ID] |
| SendGrid API | External email API | CI workflow result notifications | Used in all 5 AI delivery tools |
| GitHub API (api.github.com) | External VCS API | File fetching, PR comments, output repo writes | Requires `GH_TOKEN` PAT |
| `ai-delivery-outputs` (GitHub repo) | Sibling GitHub repository | Stores all AI-generated artefacts (docs, tests, UAT packs) | Must exist and be writable by `GH_TOKEN` |
| LangChain / LangGraph | Python framework | Agent orchestration, tool registration, graph state management | Core runtime dependency |
| LangChain-Anthropic | Python package | Anthropic LLM integration | `ChatAnthropic` wrapper |
| LangChain-Google-GenAI | Python package | Google Gemini LLM integration | `ChatGoogleGenerativeAI` wrapper |
| CatBoost | ML library | Pre-trained risk classification model | Model pre-trained externally; predictions stored in SQLite |
| Redis (redis-stack-server 7.2.0) | Cache / message broker | LangGraph `AsyncRedisSaver` checkpointing | [TODO: migrate to managed service for production per code comment in `graph.py`] |
| PostgreSQL 16 | Relational database | Chainlit chat history | Initialised via `postgres/init.sql` |
| Chainlit | Frontend framework | Chat UI | [TODO: version not specified in provided files] |
| FastAPI | Python web framework | Backend REST/SSE API | |
| `sse-starlette` | Python package | SSE streaming support | |
| `python-dotenv` | Python package | `.env` file loading | |
| `pydantic` | Python package | Structured output validation (`UnderwritingReport`) | |
| `anthropic` (raw SDK) | Python package | Used by CI scripts (`shared.py`) independently of LangChain | |

---

## 7. Deployment Instructions

### Prerequisites
- Docker and Docker Compose installed
- `.env` file created in the repo root with required secrets (see Section 5)
- SQLite database files present under `./database/`

### Local Deployment

```bash
# 1. Clone the repository
git clone https://github.com/kylodeng/underwriting_chatbot-main.git
cd underwriting_chatbot-main

# 2. Create the .env file (minimum required variables)
cat > .env << EOF
ANTHROPIC_API_KEY=your_anthropic_api_key_here
GOOGLE_API_KEY=your_google_api_key_here
EOF

# 3. Build and start all services
docker compose up --build

# 4. Verify backend health
curl http://localhost:8000/health
# Expected: {"status": "ok"}

# 5. Access the frontend
# Open http://localhost:8080 in a browser
```

### Stopping the stack

```bash
docker compose down
# To also remove the PostgreSQL volume:
docker compose down -v
```

### Triggering CI/CD AI Tools Manually

```bash
# Tool 1 — Code Review (full repo scan)
gh workflow run tool1_code_review.yml \
  -f review_mode=repo

# Tool 1 — Code Review (specific PR)
gh workflow run tool1_code_review.yml \
  -f review_mode=pr \
  -f pr_number=42

# Tool 2 — Tech Documentation
gh workflow run tool2_tech_docs.yml

# Tool 3 — Business Documentation
gh workflow run tool3_business_