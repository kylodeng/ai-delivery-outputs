# Architecture Document — kylodeng/underwriting_chatbot-main

---

## 1. Overview

The Underwriting Chatbot is an AI-powered insurance underwriting assistant that enables underwriters to assess customer risk profiles through a conversational interface. The system ingests structured customer data from several SQLite databases (customer profile, financial, KYC, application, and ML model predictions), runs multi-specialist LLM assessments in parallel using Anthropic Claude and Google Gemini models, and returns a structured `UnderwritingReport` with risk classification, findings, and follow-up actions. The backend exposes a streaming FastAPI/SSE endpoint consumed by a Chainlit-based frontend. Five GitHub Actions workflows layer on AI-powered DevOps tooling (automated code review, tech docs, business docs, test generation, and UAT facilitation), all powered by Claude via the Anthropic API.

---

## 2. Resources Deployed

| Resource | Type | Cloud Provider | Purpose |
|---|---|---|---|
| `backend` | Docker container (FastAPI) | Self-hosted / Docker Compose | Serves `/chat` SSE endpoint and `/health`; orchestrates LLM agents |
| `frontend` | Docker container (Chainlit) | Self-hosted / Docker Compose | Conversational UI for underwriters |
| `redis` (redis-stack-server 7.2.0) | Docker container | Self-hosted / Docker Compose | LangGraph checkpoint store for agent conversation memory |
| `postgres` (v16-alpine) | Docker container | Self-hosted / Docker Compose | Chainlit session/user persistence |
| `customer_profile.db` | SQLite file (read-only mount) | Self-hosted | Customer demographic and profile data |
| `feature_importance.db` | SQLite file (read-only mount) | Self-hosted | ML model feature importance scores |
| `model_predictions.db` | SQLite file (read-only mount) | Self-hosted | CatBoostClassifier risk predictions |
| `application_profile.db` | SQLite file (read-only mount) | Self-hosted | Insurance application data |
| `postgres_data` | Docker named volume | Self-hosted | Persistent PostgreSQL data |
| Anthropic Claude API | External managed LLM API | Anthropic (cloud) | Specialist and aggregator LLM calls for underwriting assessment and DevOps tooling |
| Google Gemini API | External managed LLM API | Google Cloud | Alternative LLM provider (configured, [TODO: confirm actively used in prod]) |
| SendGrid API | External transactional email service | Twilio/SendGrid (cloud) | Email notifications from CI/CD workflow tools |
| GitHub Actions (5 workflows) | CI/CD compute (ubuntu-latest) | GitHub (cloud) | Automated code review, tech docs, business docs, test generation, UAT |
| `ai-delivery-outputs` (separate GitHub repo) | GitHub repository | GitHub | Stores AI-generated artefacts (docs, test files, UAT packs) |
| CatBoostClassifier model | ML model (pre-trained, embedded) | Self-hosted | Risk classification — `model_card.json` indicates v1.0, deployed 2024-06-01 |

---

## 3. Data Flow

### Chat / Underwriting Assessment Flow

1. **User input** — An underwriter types a message in the Chainlit frontend (port 8080).
2. **HTTP POST to backend** — The frontend sends `POST /chat` with `{message, session_id, model, mode, temperature}` to the FastAPI backend (port 8000).
3. **Agent initialisation** — `build_agent()` constructs a LangGraph agent with the selected LLM (Claude Haiku "fast" or Claude Sonnet "deep"), attaches tools (`get_customer_profile`, `customer_lookalike`, `run_underwriting_assessment`), and loads the Redis checkpointer keyed on `session_id`.
4. **Tool: get_customer_profile** — Agent calls `get_customer_profile` with a customer ID; tool queries `customer_profile.db` (and related SQLite DBs) mounted read-only inside the backend container.
5. **Tool: customer_lookalike** — Agent optionally calls `customer_lookalike`, which reads `customer_similarity_dict.json` from `backend/tmp/` to return similar historical customer IDs.
6. **Tool: run_underwriting_assessment** — Agent passes the retrieved profile string to this tool, which fans out up to 4 concurrent specialist LLM calls (via `asyncio.Semaphore(4)`) to Claude, one per assessment category (finance, health, life, etc.), using prompts from `assessment_criterias.json`.
7. **Aggregation** — Specialist responses are collected and passed to a second (aggregator) Claude call that returns a structured `UnderwritingReport` JSON object via `structured_output`.
8. **Report rendering** — The report is passed to `render_report()` and serialised.
9. **SSE streaming** — The backend streams events (`tool_start`, `tool_end`, `response`, `chart`) back to the frontend via Server-Sent Events. Chart data is buffered and sent after the narrative text.
10. **Frontend display** — Chainlit renders the streaming markdown response and any chart events to the underwriter.
11. **Checkpoint persistence** — At each agent step, LangGraph writes conversation state to Redis for multi-turn continuity.

### CI/CD AI Tooling Flow

1. A GitHub event (PR open, push to main, version tag, schedule) triggers one of the five workflow YAML files.
2. The workflow runner checks out the source repo and installs `anthropic` + `requests`.
3. The Python script fetches source files via the GitHub REST API (using `GH_TOKEN`).
4. The script calls the Anthropic Claude API (`claude-sonnet-4-6` in `shared.py`) with structured system prompts.
5. The Claude response is parsed and written as a file to the `ai-delivery-outputs` repo via the GitHub Contents API.
6. SendGrid sends an email notification to `kylo.deng@capco.com`.
7. For PR-triggered reviews (Tool 1), a comment is also posted back to the pull request.

---

## 4. Security Posture

### Secured

- **SQLite databases are mounted read-only** (`ro` flag in `docker-compose.yml`) — prevents backend from modifying source data.
- **API keys managed as GitHub Actions secrets** — `ANTHROPIC_API_KEY`, `GH_TOKEN`, `SENDGRID_API_KEY` are not hardcoded in workflow YAML.
- **System prompt confidentiality** — The agent is explicitly instructed never to reveal internal system prompts or tool names to users.
- **Semaphore-limited concurrency** — `asyncio.Semaphore(4)` prevents unbounded parallel LLM calls per assessment.
- **Assessment token caps** — `specialist_max_tokens: 1500` and `aggregator_max_tokens: 8000` in `config.yml` limit runaway LLM output and cost exposure.

### Not Secured / Gaps

- **⚠️ CORS wildcard** — `allow_origins=["*"]` in `main.py` means any origin can call the `/chat` endpoint. No authentication or API key is required to invoke the backend. This is a **critical gap** in a system handling sensitive insurance applicant data.
- **⚠️ No authentication on `/chat` or `/health`** — There is no JWT, OAuth, session token, or API key validation on any FastAPI endpoint. Any host that can reach port 8000 can submit underwriting queries.
- **⚠️ PostgreSQL credentials hardcoded in `docker-compose.yml`** — `POSTGRES_USER: chainlit`, `POSTGRES_PASSWORD: chainlit` are plaintext defaults. These must be rotated before any non-local deployment.
- **⚠️ Redis has no password / TLS** — Redis is exposed on port 6379 with no authentication configured. The TODO comment in `graph.py` acknowledges this is a known gap.
- **⚠️ No encryption at rest** — SQLite database files are plain files on the host filesystem. PostgreSQL data volume is unencrypted. No disk-level encryption is configured.
- **⚠️ No encryption in transit (internal)** — Inter-container communication (backend→Redis, backend→Postgres, frontend→backend) uses plain HTTP/TCP. No TLS is configured for internal Docker network traffic.
- **⚠️ Sensitive PII in `backend/tmp/`** — `customer_similarity_dict.json` is a static file baked into the repository containing thousands of customer IDs. This should not be in version control.
- **⚠️ `GH_TOKEN` scope unknown** — The GitHub token used by CI scripts has access to write to `ai-delivery-outputs` repo and post PR comments. The exact scopes granted are [TODO: confirm minimum required scopes — repo, contents:write, pull-requests:write].
- **⚠️ No input sanitisation** — User messages from the frontend are passed directly to LLM prompts. No sanitisation or length limiting is applied before LLM invocation.
- **⚠️ `_charts_sent` is a global in-process set** — Session state is tracked in a module-level Python set, which will not survive process restarts and is not thread/process safe if multiple workers are running.
- **⚠️ `model_card.json` in repo** — The model card lists internal feature names and feature importance weights. Leaking this could aid adversarial input crafting.

---

## 5. Environment Variables and Secrets

| Name | Required | Sensitivity | Where Set |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | **High** — LLM API billing key | GitHub Actions secret; `.env` file for backend container |
| `GOOGLE_API_KEY` | Yes (if Gemini used) | **High** — GCP API billing key | `.env` file for backend container |
| `GH_TOKEN` | Yes (CI only) | **High** — GitHub PAT with repo write | GitHub Actions secret |
| `SENDGRID_API_KEY` | Yes (CI only) | **High** — email sending credential | GitHub Actions secret |
| `REDIS_HOST` | No | Low | `docker-compose.yml` environment block (default: `localhost`) |
| `POSTGRES_USER` | Yes | Medium | `docker-compose.yml` hardcoded as `chainlit` ⚠️ |
| `POSTGRES_PASSWORD` | Yes | **High** | `docker-compose.yml` hardcoded as `chainlit` ⚠️ |
| `POSTGRES_DB` | No | Low | `docker-compose.yml` hardcoded as `chainlit` |
| `DATABASE_URL` | Yes (frontend) | **High** | `docker-compose.yml` environment block (contains password) |
| `BACKEND_URL` | Yes (frontend) | Low | `docker-compose.yml` environment block |
| `OUTPUT_REPO` | No (CI) | Low | GitHub Actions env (default: `ai-delivery-outputs`) |
| `OUTPUT_REPO_OWNER` | No (CI) | Low | GitHub Actions env (derived from `github.repository_owner`) |
| `NOTIFY_EMAIL` | No (CI) | Low | GitHub Actions env hardcoded as `kylo.deng@capco.com` |
| `SENDER_EMAIL` | No (CI) | Low | GitHub Actions env hardcoded as `noreply@ai-delivery.capco.com` |
| `REVIEW_MODE` | No (CI) | Low | Set at runtime by workflow step |
| `PR_NUMBER` | No (CI) | Low | Set at runtime by workflow step |
| `RELEASE_VERSION` | No (CI) | Low | Set at runtime by workflow step |
| `UAT_MODE` | No (CI) | Low | Set at runtime by workflow step |
| `TEST_MODE` | No (CI) | Low | Set at runtime by workflow step |

> **[TODO: Confirm whether a `.env` file is committed or gitignored — if committed, all secrets are exposed in the repository.]**

---

## 6. Dependencies

| Dependency | Type | Purpose | Notes |
|---|---|---|---|
| Anthropic Claude API (`claude-sonnet-4-20250514`, `claude-haiku-4-5-20251001`) | External SaaS API | Specialist LLM assessment + aggregation; CI tooling | Billing risk; no fallback if API is down |
| Google Gemini API (`gemini-3-flash-preview`) | External SaaS API | Alternative LLM provider | Configured in `LLMS.py` and `config.yml`; [TODO: confirm if actively used in production or only dev] |
| LangChain / LangGraph | Python library | Agent orchestration, tool binding, streaming | Core runtime dependency |
| `redis-stack-server:7.2.0-v14` | Docker image | LangGraph conversation checkpointing | No auth configured |
| `postgres:16-alpine` | Docker image | Chainlit session persistence | Default credentials in use |
| Chainlit | Python framework (frontend container) | Conversational UI | [TODO: confirm Chainlit version pinned in frontend Dockerfile] |
| SendGrid API | External SaaS | Email delivery for CI workflow notifications | Used only in GitHub Actions |
| GitHub REST API (`api.github.com`) | External API | File fetch, PR comments, output repo writes in CI | Requires `GH_TOKEN` |
| `ai-delivery-outputs` (separate GitHub repo, same owner) | External GitHub repo | Storage of all AI-generated CI artefacts | Must exist and be writable by `GH_TOKEN` |
| CatBoostClassifier (pre-trained) | ML model | Risk classification predictions in `model_predictions.db` | Static, trained 2024-06-01 — no retraining pipeline visible |
| SQLite databases (4 files) | Local file dependencies | Customer, application, KYC, model data | Loaded from `./database/` directory on host at compose time |
| `backend/tmp/customer_similarity_dict.json` | Local file | Customer lookalike mapping | Static file in repo — contains PII-adjacent data |
| `backend/prompts/assessment_criterias.json` | Local file | LLM assessment prompts per category and mode | Sensitive IP — system prompts |

---

## 7. Deployment Instructions

### Prerequisites

- Docker and Docker Compose installed
- A `.env` file in the project root with at minimum:

```bash
ANTHROPIC_API_KEY=sk-ant-...
GOOGLE_API_KEY=AIza...
```

- SQLite database files present in `./database/`:
  - `customer_profile.db`
  - `feature_importance.db`
  - `model_predictions.db`
  - `application_profile.db`

### Local / Docker Compose Deployment

```bash
# 1. Clone the repository
git clone https://github.com/kylodeng/underwriting_chatbot-main.git
cd underwriting_chatbot-main

# 2. Create the .env file (DO NOT commit this file)
cp .env.example .env   # [TODO: confirm .env.example exists]
# Edit .env with real API keys

# 3. Build and start all services
docker compose up --build -d

# 4. Verify backend health
curl http://localhost:8000/health
# Expected: {"status": "ok"}

# 5. Access the frontend
open http://localhost:8080

# 6. View logs
docker compose logs -f backend
docker compose logs -f frontend

# 7. Stop all services
docker compose down

# 8. Stop and remove volumes (destructive — loses PostgreSQL data)
docker compose down -v
```

### CI/CD Workflow Triggers

```bash
# Tool 1 — Code Review: triggers automatically on PR open/sync
# Manual trigger via GitHub Actions UI: workflow_dispatch on tool1_code_review.yml

# Tool 2 — Tech Docs: triggers on push to main (non-docs files)
git push origin main

# Tool 3 — Business Docs: triggers on version tag
git tag v1.0.0
git push origin v1.0.0

# Tool 4 — Auto Testing: triggers on PR touching src/**
# Or manually via workflow_dispatch

# Tool 5 — UAT: triggers on release branch creation
git checkout -b release/1.0.0
git push origin release/1.0.0
```

### Required GitHub Repository Secrets

```
ANTHROPIC_API_KEY   — Anthropic API key
GH_TOKEN            — GitHub PAT (needs: repo, contents:write, pull-requests:write)
SENDGRID_API_KEY    — SendGrid API key
```

---

## 8. Risks and TODOs

### Extracted from Code Comments

| Location | Issue |
|---|---|
| `backend/agent/graph.py` line 1 | **TODO**: