# Architecture Document: kylodeng/underwriting_chatbot-main

---

## 1. Overview

The Underwriting Chatbot is an AI-powered insurance underwriting assistant that enables underwriters to assess customer risk profiles through a conversational interface. The system ingests structured customer data from multiple SQLite databases (customer profiles, financial data, KYC records, application profiles, and ML model predictions), runs parallel specialist LLM assessments across domains such as finance, health, and life risk, and aggregates results into a structured `UnderwritingReport`. A FastAPI backend exposes a streaming Server-Sent Events (SSE) chat endpoint consumed by a frontend UI, with Redis used for LangGraph conversation checkpointing and PostgreSQL for Chainlit session persistence. The repository also includes five GitHub Actions–based AI delivery tools (code review, tech docs, business docs, auto-testing, and UAT facilitation) powered by Anthropic's Claude API.

---

## 2. Resources Deployed

| Resource | Type | Cloud Provider | Purpose |
|---|---|---|---|
| `backend` | Docker container (FastAPI) | Self-hosted / Docker Compose | Core API server; hosts LangGraph agent, assessment pipeline, SSE streaming endpoint |
| `frontend` | Docker container | Self-hosted / Docker Compose | User-facing chat UI (Chainlit-based) |
| `redis` | Docker container (`redis/redis-stack-server:7.2.0-v14`) | Self-hosted / Docker Compose | LangGraph conversation checkpointing (in-memory, ephemeral) |
| `postgres` | Docker container (`postgres:16-alpine`) | Self-hosted / Docker Compose | Chainlit session/user data persistence |
| `customer_profile.db` | SQLite database (read-only volume) | Self-hosted | Customer demographic and profile data |
| `feature_importance.db` | SQLite database (read-only volume) | Self-hosted | CatBoost model feature importance data |
| `model_predictions.db` | SQLite database (read-only volume) | Self-hosted | Pre-computed ML risk classification predictions |
| `application_profile.db` | SQLite database (read-only volume) | Self-hosted | Insurance application data |
| `postgres_data` | Docker named volume | Self-hosted | Persistent PostgreSQL storage |
| Anthropic Claude API | External SaaS API | Anthropic | LLM inference for agent reasoning and specialist assessments |
| Google Gemini API | External SaaS API | Google Cloud | Alternative LLM provider (configured but usage unclear) |
| GitHub Actions runners | CI/CD compute (`ubuntu-latest`) | GitHub | Five AI delivery workflow tools |
| `ai-delivery-outputs` | GitHub repository | GitHub | Stores generated docs, test reports, UAT packs from AI tools |
| SendGrid | External SaaS API | Twilio/SendGrid | Email notifications from AI delivery tools |

---

## 3. Data Flow

### Chat / Assessment Flow

1. **User sends message** via the frontend UI (HTTP POST to `http://backend:8000/chat`) with `session_id`, `model`, `mode`, `temperature`, and `message` fields.
2. **FastAPI backend** receives the `ChatRequest`, builds a LangGraph agent (`build_agent`) with the selected model and tools, and streams events via SSE (`EventSourceResponse`).
3. **LangGraph agent** (backed by `AsyncRedisSaver`) loads conversation history from **Redis** keyed by `session_id`, then invokes the LLM (Claude or Gemini) with the system prompt and conversation history.
4. **Agent decides tool calls** by returning JSON with `action: "tool_call"`. Available tools are:
   - `get_customer_profile` — queries SQLite `customer_profile.db`
   - `customer_lookalike` — reads `backend/tmp/customer_similarity_dict.json` to find similar customers
   - `run_underwriting_assessment` — triggers the parallel specialist assessment pipeline
5. **Underwriting assessment** (`_run_underwriting_assessment`): for each domain category (finance, health, life, etc.) a specialist LLM call is made **concurrently** (bounded by `asyncio.Semaphore(4)`), reading prompt criteria from `prompts/assessment_criterias.json`.
6. **Aggregator LLM** receives all specialist reports and produces a structured `UnderwritingReport` Pydantic model via `structured_output`.
7. **Rendered report** is returned to the agent as a tool result; the agent formats a final answer.
8. **SSE events** stream back to the frontend: `tool_start`, `tool_end`, `response` (text chunks), `chart` (feature importance/lookalike data), and `report` (full JSON report).
9. **Conversation state** is checkpointed back to Redis after each turn.
10. **Frontend** renders streamed text, charts, and the structured underwriting report in the UI.

### AI Delivery Tools Flow (GitHub Actions)

1. A trigger event (PR open, push to `main`, release tag, schedule) fires a GitHub Actions workflow.
2. The workflow checks out the source repo and runs a Python script (e.g., `tool1_code_review.py`).
3. The script fetches repo files or PR diffs via the **GitHub API** using `GH_TOKEN`.
4. Content is sent to **Claude API** (`claude-sonnet-4-6`) for analysis.
5. Results are written to the **`ai-delivery-outputs`** GitHub repository via the GitHub Contents API.
6. A **SendGrid** email notification is sent to `kylo.deng@capco.com`.
7. For code review, a PR comment is also posted via the GitHub Issues API.

---

## 4. Security Posture

### ✅ What Is Secured

- **API keys in GitHub Actions** are stored as GitHub repository secrets (`ANTHROPIC_API_KEY`, `GH_TOKEN`, `SENDGRID_API_KEY`) and injected as environment variables — not hardcoded in workflow files.
- **SQLite databases are mounted read-only** (`ro`) in the backend container, preventing accidental writes.
- **Semaphore on concurrent LLM calls** (`asyncio.Semaphore(4)`) prevents runaway API spend from unbounded parallelism.
- **Model card stored in-repo** provides audit trail for the CatBoost classifier used in predictions.
- **Specialist LLM token cap** (`specialist_max_tokens: 1500`) limits runaway output costs.
- **System prompt instructs the agent** never to reveal internal instructions or tool inventory to users.

### ❌ Gaps and Missing Controls

- **PostgreSQL credentials are hardcoded** in `docker-compose.yml` (`POSTGRES_USER: chainlit`, `POSTGRES_PASSWORD: chainlit`). These must be replaced with secrets before any non-local deployment.
- **Redis has no authentication configured** — the Redis container is exposed on port `6379` with no password, `requirepass`, or TLS. Conversation history is unencrypted in transit and at rest.
- **No encryption at rest** for Redis, SQLite databases, or PostgreSQL named volume. Sensitive customer PII and financial data in SQLite files are unencrypted.
- **CORS is fully open** (`allow_origins=["*"]`, `allow_methods=["*"]`, `allow_headers=["*"]`) in `main.py`. This is acceptable for local dev but is a critical misconfiguration in production.
- **No authentication or authorization on the `/chat` endpoint** — any caller can query any `session_id` or submit arbitrary messages.
- **`GH_TOKEN` scope is unknown** — if it has write access to the entire GitHub organization, it is overly broad. [TODO: restrict to minimum required scopes — `contents:write` on `ai-delivery-outputs` and `pull-requests:write` on source repos only.]
- **Customer similarity dict is stored in `backend/tmp/`** as a plain JSON file, unencrypted, and contains customer IDs in plaintext.
- **No secrets scanning** configured in the repository (no `.gitleaks`, `trufflehog`, or GitHub secret scanning policy visible).
- **No input validation/sanitization** on the `message` field in `ChatRequest` beyond Pydantic type checking — prompt injection is a risk.
- **No rate limiting** on the `/chat` endpoint.
- **`GOOGLE_API_KEY` loaded from `.env`** directly via `dotenv` in `LLMS.py` — if `.env` is accidentally committed, the key is exposed. Verify `.env` is in `.gitignore`. [TODO: confirm `.env` is gitignored.]
- **`_charts_sent` is a module-level in-process set** — it does not survive restarts and will diverge across multiple backend replicas.
- **No TLS/HTTPS** configured at the Docker Compose level — all traffic between frontend and backend is plaintext HTTP.
- **Shared AI delivery tools use a hardcoded `MODEL = "claude-sonnet-4-6"` in `shared.py`** which differs from the model IDs in `config.yml`, suggesting a version mismatch or copy-paste error.

---

## 5. Environment Variables and Secrets

| Name | Required | Sensitivity | Where Set |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | 🔴 High — billable API key | GitHub Actions secret; `.env` file for backend |
| `GH_TOKEN` | Yes (CI tools) | 🔴 High — GitHub API access | GitHub Actions secret |
| `SENDGRID_API_KEY` | Yes (CI tools) | 🔴 High — email sending capability | GitHub Actions secret |
| `GOOGLE_API_KEY` | Yes (if Gemini used) | 🔴 High — billable API key | `.env` file for backend |
| `REDIS_HOST` | No | 🟢 Low | `docker-compose.yml` environment; defaults to `localhost` |
| `DATABASE_URL` | Yes (frontend) | 🟡 Medium — DB credentials | `docker-compose.yml` environment (hardcoded) |
| `BACKEND_URL` | Yes (frontend) | 🟢 Low | `docker-compose.yml` environment |
| `POSTGRES_USER` | Yes | 🟡 Medium | `docker-compose.yml` (hardcoded `chainlit`) |
| `POSTGRES_PASSWORD` | Yes | 🔴 High | `docker-compose.yml` (hardcoded `chainlit` — **must be changed**) |
| `POSTGRES_DB` | Yes | 🟢 Low | `docker-compose.yml` (hardcoded `chainlit`) |
| `OUTPUT_REPO` | No (CI tools) | 🟢 Low | GitHub Actions workflow env; defaults to `ai-delivery-outputs` |
| `OUTPUT_REPO_OWNER` | No (CI tools) | 🟢 Low | GitHub Actions workflow env |
| `NOTIFY_EMAIL` | No (CI tools) | 🟡 Medium | GitHub Actions workflow env (hardcoded `kylo.deng@capco.com`) |
| `SENDER_EMAIL` | No (CI tools) | 🟢 Low | GitHub Actions workflow env |
| `REVIEW_MODE` | No (CI tools) | 🟢 Low | Set dynamically in workflow step |
| `PR_NUMBER` | No (CI tools) | 🟢 Low | Set dynamically in workflow step |
| `RELEASE_VERSION` | No (CI tools) | 🟢 Low | Set dynamically in workflow step |
| `PROJECT_NAME` | No (CI tools) | 🟢 Low | Set dynamically in workflow step |
| `TEST_MODE` | No (CI tools) | 🟢 Low | GitHub Actions workflow env |
| `UAT_MODE` | No (CI tools) | 🟢 Low | Set dynamically in workflow step |

---

## 6. Dependencies

### External Services and APIs

| Dependency | Type | Purpose | Notes |
|---|---|---|---|
| Anthropic Claude API | SaaS LLM | Agent reasoning, specialist assessments, AI delivery tools | Models: `claude-sonnet-4-20250514`, `claude-haiku-4-5-20251001`, `claude-sonnet-4-6` (version mismatch — see Risks) |
| Google Gemini API | SaaS LLM | Alternative LLM provider | Model: `gemini-3-flash-preview`; configured but [TODO: confirm actively used] |
| GitHub API (`api.github.com`) | REST API | File fetching, PR comments, output repo writes | Used by all 5 CI tools |
| SendGrid API | SaaS Email | Notification emails on tool completion | Sender: `noreply@ai-delivery.capco.com` |

### Key Python Libraries

| Library | Purpose |
|---|---|
| `fastapi` | REST API framework |
| `langgraph` | Agent graph orchestration and state management |
| `langchain-anthropic` | Anthropic LLM integration |
| `langchain-google-genai` | Google Gemini LLM integration |
| `langchain-core` | LangChain abstractions |
| `redis` (asyncio) | LangGraph checkpoint backend |
| `pydantic` | Data validation and structured output models |
| `sse-starlette` | Server-Sent Events streaming |
| `anthropic` | Direct Anthropic SDK (used in CI tools) |
| `catboost` | [TODO: confirm if runtime inference is performed or only pre-computed predictions are read] |
| `yaml` | Config file parsing |

### Other Repositories

| Repository | Relationship |
|---|---|
| `{owner}/ai-delivery-outputs` | Output repo — CI tools write generated docs, reports, and UAT packs here |

---

## 7. Deployment Instructions

### Prerequisites

- Docker and Docker Compose installed
- A `.env` file in the repo root containing at minimum:

```bash
ANTHROPIC_API_KEY=sk-ant-...
GOOGLE_API_KEY=AIza...
```

### Local Deployment (Docker Compose)

```bash
# 1. Clone the repository
git clone https://github.com/kylodeng/underwriting_chatbot-main.git
cd underwriting_chatbot-main

# 2. Create the .env file
cp .env.example .env   # if template exists, otherwise create manually
# Edit .env and populate ANTHROPIC_API_KEY, GOOGLE_API_KEY

# 3. Build and start all services
docker compose up --build

# 4. Verify backend health
curl http://localhost:8000/health
# Expected: {"status": "ok"}

# 5. Access the frontend
open http://localhost:8080
```

### Services and Ports

| Service | Host Port | Purpose |
|---|---|---|
| `backend` | `8000` | FastAPI chat API |
| `frontend` | `8080` | Chat UI |
| `redis` | `6379` | Redis (local access only needed for debugging) |
| `postgres` | `5432` | PostgreSQL (local access only needed for debugging) |

### Stopping Services

```bash
docker compose down          # stop and remove containers
docker compose down -v       # also remove named volumes (destroys PostgreSQL data)
```

### Triggering CI/AI Delivery Tools Manually

```bash
# Tool 1: Code Review (repo-wide)
gh workflow run tool1_code_review.yml -f review_mode=repo

# Tool 2: Tech Documentation
gh workflow run tool2_tech_docs.yml

# Tool 3: Business Documentation
gh workflow run tool3_business_docs.yml -f project_name="Underwriting Chatbot" -f release_version="1.0.0"

# Tool 4: Auto Testing
gh workflow run tool4_auto_testing.yml -f test_mode=generate

# Tool 5: UAT Facilitation
gh workflow run tool5_uat.yml -f uat_mode=generate -f release_version="1.0.0"
```

---

## 8. Risks and TODOs

### Extracted from Code

| Risk / TODO | Severity | Source |
|---|---|---|
| `# TODO: migrate Redis to an external service (e.g. Azure Cache for Redis)` — Redis is ephemeral; conversation history is lost on container restart | 🔴 High | `backend/agent/graph.py` |
| `# TODO: add more providers here` — `azure` and `openai` entries in `LLMS.model_mapper` return `None` and will raise `ValueError` at runtime | 🟡 Medium | `backend/modules/LLMS.py` |
| Model name mismatch: `shared.py` hardcodes `claude-sonnet-4-6`, `config.yml` specifies `claude-sonnet-4-20250514` and `claude-haiku-4-5-20251001`, `LLMS.py` hardc