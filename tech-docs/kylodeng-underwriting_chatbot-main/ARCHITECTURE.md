# Architecture Document: kylodeng/underwriting_chatbot-main

---

## 1. Overview

The Underwriting Chatbot is an AI-assisted insurance underwriting decision-support platform. It provides a conversational interface for underwriters to query customer profiles, retrieve similar customer records (lookalike analysis), and run multi-specialist AI risk assessments against insurance applications. The backend orchestrates a LangGraph-based agentic pipeline powered by Anthropic Claude (and optionally Google Gemini), fan-out to specialist LLM agents across assessment domains (finance, health, life, KYC, etc.), and aggregates results into a structured `UnderwritingReport`. A Chainlit-based frontend communicates with the FastAPI backend over Server-Sent Events (SSE) for streaming responses. The repository also includes five GitHub Actions AI-delivery workflows (code review, tech docs, business docs, auto-testing, UAT facilitation) that use Claude to automate the software delivery lifecycle.

---

## 2. Resources Deployed

| Resource | Type | Cloud Provider | Purpose |
|---|---|---|---|
| FastAPI backend | Application server (Docker container) | Self-hosted / [TODO: target cloud TBD] | Serves `/chat` and `/health` endpoints; orchestrates LangGraph agent |
| Chainlit frontend | Web application (Docker container) | Self-hosted / [TODO: target cloud TBD] | Conversational UI for underwriters |
| Redis (redis-stack-server 7.2) | In-memory data store (Docker container) | Self-hosted | LangGraph conversation checkpointing / session memory |
| PostgreSQL 16 | Relational database (Docker container) | Self-hosted | Chainlit session/user persistence |
| `customer_profile.db` | SQLite database (bind-mounted, read-only) | Self-hosted | Customer profile data |
| `feature_importance.db` | SQLite database (bind-mounted, read-only) | Self-hosted | ML model feature importance scores |
| `model_predictions.db` | SQLite database (bind-mounted, read-only) | Self-hosted | Pre-computed CatBoost risk classification predictions |
| `application_profile.db` | SQLite database (bind-mounted, read-only) | Self-hosted | Insurance application records |
| `customer_similarity_dict.json` | Static JSON file | Self-hosted | Pre-computed customer lookalike index |
| CatBoostClassifier model | ML model artefact | Self-hosted | Risk classification (Preferred / Standard Plus / Standard / Substandard) |
| Anthropic Claude (claude-sonnet-4-20250514 / claude-haiku-4-5-20251001) | External LLM API | Anthropic (external) | Specialist and aggregator underwriting assessment; CI/CD AI tools |
| Google Gemini (gemini-3-flash-preview) | External LLM API | Google Cloud (external) | Alternative LLM provider (configured but [TODO: verify active use]) |
| GitHub Actions runners | CI/CD compute | GitHub | Five AI-delivery automation workflows |
| SendGrid | External email API | Twilio/SendGrid | Workflow notification emails |
| `ai-delivery-outputs` (GitHub repo) | Output repository | GitHub | Stores AI-generated docs, test files, UAT packs |

---

## 3. Data Flow

### Chat / Assessment Flow

1. **Underwriter** submits a message via the Chainlit frontend (port 8080).
2. Frontend forwards the request (message, session_id, model, mode, temperature) as an HTTP POST to the FastAPI backend at `/chat` (port 8000).
3. Backend instantiates a LangGraph agent (`build_agent`) with Redis-backed conversation checkpointing. The agent uses the session's `thread_id` to load prior conversation state from Redis.
4. The LangGraph agent invokes the configured Anthropic/Gemini LLM to determine which tool to call next, based on the system prompt, skill documentation, and conversation history.
5. **Tool: `get_customer_profile`** — queries `customer_profile.db` and other SQLite databases (bind-mounted, read-only) and returns structured customer data.
6. **Tool: `customer_lookalike`** — looks up pre-computed similar customer IDs from `customer_similarity_dict.json` in memory.
7. **Tool: `run_underwriting_assessment`** — receives the customer profile string and fans out **parallel** specialist LLM calls (up to 4 concurrent via `asyncio.Semaphore`) across assessment categories (finance, health, life, KYC, etc.) loaded from `assessment_criterias.json`. Each specialist call goes to the Anthropic API (claude-haiku for fast, claude-sonnet for deep mode).
8. Specialist results are aggregated by a second LLM call using `structured_llm` (with Pydantic structured output), producing a validated `UnderwritingReport` JSON.
9. The report is rendered and streamed back to the backend as SSE events (`tool_start`, `tool_end`, `response`, `chart` events).
10. FastAPI streams SSE events to the frontend, which renders thinking indicators, tool progress, charts, and the final assessment text in real time.
11. Conversation state (messages) is checkpointed back to Redis after each turn.
12. The ML model predictions from `model_predictions.db` (CatBoost `Risk_Classification`) are available to the agent as supplementary signals alongside the LLM assessment.

### CI/CD AI Workflow Data Flow

1. GitHub event (PR, push to main, tag, schedule, manual dispatch) triggers the relevant workflow.
2. GitHub Actions runner checks out the source repository.
3. Python scripts call the Anthropic Claude API with repository file contents as context.
4. Generated artefacts (review comments, markdown docs, test files, UAT packs) are written to the `ai-delivery-outputs` GitHub repository via the GitHub API.
5. SendGrid sends email notifications to `kylo.deng@capco.com` with links to outputs.
6. PR review comments are posted back to GitHub via the GitHub Issues/PRs API.

---

## 4. Security Posture

### What Is Secured

- **Secrets management**: All API keys (`ANTHROPIC_API_KEY`, `GOOGLE_API_KEY`, `SENDGRID_API_KEY`, `GH_TOKEN`) are stored as GitHub Actions secrets and injected as environment variables at runtime — not hardcoded in source.
- **Read-only database mounts**: All four SQLite databases are mounted as `:ro` (read-only) in Docker Compose, preventing the backend from modifying source data.
- **Backend health check**: Docker Compose health check prevents the frontend from starting before the backend is healthy.
- **Agent prompt injection guard**: System prompt explicitly instructs the agent never to disclose internal instructions or tool details to users.
- **Structured output validation**: `UnderwritingReport` is validated through Pydantic models before returning to the client, reducing risk of malformed LLM output propagating downstream.

### Security Gaps — Explicit Callouts

- ⚠️ **No authentication or authorisation on the API**: The FastAPI backend exposes `/chat` with `allow_origins=["*"]` (CORS wildcard). Any client that can reach port 8000 can submit queries and receive customer PII. There is no API key, JWT, OAuth, or mTLS on the backend API.
- ⚠️ **No TLS/HTTPS configured**: Docker Compose exposes plain HTTP on ports 8000 and 8080. Sensitive customer data (PII, financial data, medical data) is transmitted unencrypted in transit between frontend and backend, and between client and frontend.
- ⚠️ **Sensitive customer PII sent to external LLM APIs**: Customer profile data (age, income, medical conditions, nationality, employment) is included verbatim in prompts sent to Anthropic and Google APIs. There is no PII redaction or anonymisation layer before external API calls. This is a significant data residency and privacy risk for regulated insurance data.
- ⚠️ **PostgreSQL uses hardcoded default credentials**: `POSTGRES_USER=chainlit`, `POSTGRES_PASSWORD=chainlit` are set directly in `docker-compose.yml` in plaintext. These must be rotated and externalised before any non-local deployment.
- ⚠️ **Redis has no authentication configured**: The Redis container is started with no password, no ACLs, and no TLS. Any process that can reach port 6379 has full read/write access to conversation state (which may contain customer data).
- ⚠️ **SQLite databases contain customer PII at rest without encryption**: The `.db` files are mounted directly from the host filesystem with no encryption at rest.
- ⚠️ **`GH_TOKEN` scope is unknown**: The `GH_TOKEN` used by CI/CD workflows has write access to at least the `ai-delivery-outputs` repo and PR commenting. [TODO: What is the minimum required scope? Is it a fine-grained PAT or a classic token with broad `repo` scope?]
- ⚠️ **No rate limiting on `/chat` endpoint**: The endpoint builds a new LLM agent per request, with no throttling. This creates both cost exposure (unbounded Anthropic API spend) and denial-of-service risk.
- ⚠️ **No input sanitisation on `message` or `profile` fields**: User-supplied `message` content is passed directly into LLM prompts; `profile` strings from tool outputs are also passed directly. Prompt injection attacks are not mitigated.
- ⚠️ **`_charts_sent` is a module-level global set**: In a multi-worker deployment, this in-process set will not be shared across workers, causing chart deduplication to fail. In a single-worker deployment it leaks state across sessions permanently for the process lifetime.
- ⚠️ **No secrets scanning in CI pipeline**: No `trufflehog`, `gitleaks`, or similar tool is configured in the workflows.

---

## 5. Environment Variables and Secrets

| Name | Required | Sensitivity | Where Set |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | **High** — external API key with billing implications | GitHub Actions secret; `.env` file for local/Docker |
| `GOOGLE_API_KEY` | Yes (if Gemini used) | **High** — external API key | `.env` file for local/Docker; [TODO: not present in GitHub secrets list] |
| `SENDGRID_API_KEY` | Yes (for CI notifications) | **High** — email sending capability | GitHub Actions secret |
| `GH_TOKEN` | Yes (for CI workflows) | **High** — GitHub repo write access | GitHub Actions secret |
| `REDIS_HOST` | No | Low | Docker Compose environment; defaults to `localhost` |
| `POSTGRES_USER` | Yes | Medium | Hardcoded in `docker-compose.yml` (`chainlit`) — **must be externalised** |
| `POSTGRES_PASSWORD` | Yes | **High** | Hardcoded in `docker-compose.yml` (`chainlit`) — **must be externalised** |
| `POSTGRES_DB` | No | Low | Hardcoded in `docker-compose.yml` (`chainlit`) |
| `BACKEND_URL` | Yes (frontend) | Low | Docker Compose environment (`http://backend:8000`) |
| `DATABASE_URL` | Yes (frontend) | Medium — contains DB credentials | Docker Compose environment (hardcoded `postgresql+asyncpg://chainlit:chainlit@postgres:5432/chainlit`) |
| `OUTPUT_REPO` | No | Low | GitHub Actions env; defaults to `ai-delivery-outputs` |
| `OUTPUT_REPO_OWNER` | No | Low | GitHub Actions env; derived from `github.repository_owner` |
| `NOTIFY_EMAIL` | No | Low | GitHub Actions env (`kylo.deng@capco.com`) |
| `SENDER_EMAIL` | No | Low | GitHub Actions env (`noreply@ai-delivery.capco.com`) |
| `FORCE_JAVASCRIPT_ACTIONS_TO_NODE24` | No | None | GitHub Actions env |

> [TODO: Confirm whether a `.env` file is committed to the repository or is generated at deploy time. If committed, it must be audited for secrets.]

---

## 6. Dependencies

### External Services and APIs

| Dependency | Type | Purpose | Notes |
|---|---|---|---|
| Anthropic API | External LLM API | Specialist and aggregator underwriting assessments; all 5 CI/CD AI tools | Models: `claude-sonnet-4-20250514`, `claude-haiku-4-5-20251001`; billing exposure |
| Google Generative AI (Gemini) | External LLM API | Alternative LLM provider | `gemini-3-flash-preview`; [TODO: verify if actively used in production paths] |
| SendGrid | External email API | CI/CD workflow notification emails | Used by all 5 GitHub Actions tools |
| GitHub API (`api.github.com`) | External REST API | CI/CD: fetch repo files, post PR comments, write output files | Requires `GH_TOKEN` |

### Python Libraries (Backend)

| Library | Purpose |
|---|---|
| `fastapi` | HTTP API server |
| `sse-starlette` | Server-Sent Events streaming |
| `langchain` / `langchain-core` | Agent orchestration, tool definitions |
| `langgraph` | Stateful agent graph with Redis checkpointing |
| `langchain-anthropic` | Anthropic Claude integration |
| `langchain-google-genai` | Google Gemini integration |
| `redis` (asyncio) | LangGraph checkpoint storage |
| `pydantic` | Structured output validation |
| `python-dotenv` | Local `.env` loading |
| `pyyaml` | `config.yml` parsing |
| `anthropic` | Direct Anthropic SDK (used in CI scripts) |

### Other Repositories

| Repository | Purpose |
|---|---|
| `{owner}/ai-delivery-outputs` | Output store for AI-generated docs, test files, UAT packs from CI/CD workflows |

---

## 7. Deployment Instructions

### Prerequisites

- Docker and Docker Compose v2 installed
- A `.env` file in the project root containing at minimum:

```bash
ANTHROPIC_API_KEY=sk-ant-...
GOOGLE_API_KEY=AIza...        # if using Gemini
```

### Local / Docker Compose Deployment

```bash
# 1. Clone the repository
git clone https://github.com/kylodeng/underwriting_chatbot-main.git
cd underwriting_chatbot-main

# 2. Create the .env file
cp .env.example .env          # [TODO: confirm .env.example exists]
# Edit .env and populate ANTHROPIC_API_KEY, GOOGLE_API_KEY

# 3. Ensure SQLite database files exist in ./database/
ls ./database/
# Expected: customer_profile.db  feature_importance.db  model_predictions.db  application_profile.db

# 4. Build and start all services
docker compose up --build

# 5. Verify backend health
curl http://localhost:8000/health
# Expected: {"status": "ok"}

# 6. Access the frontend
open http://localhost:8080
```

### Stopping Services

```bash
docker compose down
# To remove persistent PostgreSQL volume:
docker compose down -v
```

### GitHub Actions CI/CD Workflows

Workflows trigger automatically on their configured events. To trigger manually:

```bash
# Tool 1 — Code Review (manual, repo mode)
gh workflow run tool1_code_review.yml -f review_mode=repo

# Tool 2 — Tech Documentation
gh workflow run tool2_tech_docs.yml

# Tool 3 — Business Documentation
gh workflow run tool3_business_docs.yml -f project_name="Underwriting Chatbot" -f release_version="1.0.0"

# Tool 4 — Auto Testing
gh workflow run tool4_auto_testing.yml -f test_mode=generate

# Tool 5 — UAT Facilitation
gh workflow run tool5_uat.yml -f uat_mode=generate -f release_version="1.0.0"
```

> Required GitHub repository secrets to configure before workflows will succeed:
> - `ANTHROPIC_API_KEY`
> - `GH_TOKEN`
> - `SENDGRID_API_KEY`

---

## 8. Risks and TODOs

### Extracted from Code

| Source | Risk / TODO |
|---|---|
| `backend/agent/graph.py` line 1 | `# TODO: migrate Redis to an external service (e.g. Azure Cache for Redis, dedicated Redis container) so that memory persists across serverless backend instances.` — conversation history is lost if the Redis container restarts |
| `backend/modules/LLMS.py` | `# TODO: add more providers here` — Azure OpenAI and plain