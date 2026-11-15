# Architecture Document: kylodeng/underwriting_chatbot-main

---

## 1. Overview

The Underwriting Chatbot is an AI-powered insurance underwriting assistant that enables underwriters to assess customer risk profiles through a conversational interface. The system ingests structured customer data from multiple SQLite databases (customer profiles, financial data, KYC, application history, model predictions), queries a pre-trained CatBoost risk classification model, and runs parallel specialist LLM assessments across domains (finance, health, life, etc.) before aggregating results into a structured `UnderwritingReport`. A LangGraph-based agent orchestrates tool calls — customer profile lookup, similarity search, and full underwriting assessment — and streams results to a Chainlit-powered frontend via Server-Sent Events. Five GitHub Actions CI/CD workflows additionally automate AI-assisted code review, technical documentation, business documentation, test generation, and UAT facilitation using Anthropic Claude.

---

## 2. Resources Deployed

| Resource | Type | Cloud Provider | Purpose |
|---|---|---|---|
| `backend` | Docker container (FastAPI + LangGraph) | Local / [TODO: target cloud platform?] | Hosts the chat API, agent orchestration, and underwriting assessment engine |
| `frontend` | Docker container (Chainlit) | Local / [TODO: target cloud platform?] | Provides the conversational UI for underwriters |
| `redis` | Docker container (redis-stack-server:7.2.0-v14) | Local | LangGraph conversation state checkpointing (agent memory) |
| `postgres` | Docker container (postgres:16-alpine) | Local | Chainlit session/user data persistence |
| `customer_profile.db` | SQLite file (read-only volume mount) | Local filesystem | Customer demographic and profile data |
| `feature_importance.db` | SQLite file (read-only volume mount) | Local filesystem | CatBoost model feature importance scores |
| `model_predictions.db` | SQLite file (read-only volume mount) | Local filesystem | Pre-computed risk classification predictions |
| `application_profile.db` | SQLite file (read-only volume mount) | Local filesystem | Insurance application history and metadata |
| `postgres_data` | Docker named volume | Local | Persistent PostgreSQL storage |
| `ai-delivery-outputs` | GitHub repository | GitHub | Destination repo for all AI-generated documentation artifacts |
| Anthropic Claude (claude-sonnet-4-20250514) | External LLM API | Anthropic | Deep underwriting assessments, aggregation, CI/CD doc generation |
| Anthropic Claude (claude-haiku-4-5-20251001) | External LLM API | Anthropic | Fast agent responses, default LLM for chatbot interaction |
| Google Gemini (gemini-3-flash-preview) | External LLM API | Google Cloud | Alternative LLM provider (configured but [TODO: verify active usage]) |
| SendGrid | External email API | Twilio/SendGrid | Notification emails from CI/CD workflows |
| CatBoostClassifier model | ML model artifact | Local filesystem | Risk classification (Preferred / Standard Plus / Standard / Substandard) |

---

## 3. Data Flow

### Chat / Underwriting Assessment Flow

1. **User input**: An underwriter types a message in the Chainlit frontend (port 8080). The frontend sends a POST request to `http://backend:8000/chat` with `message`, `session_id`, `model`, `temperature`, and `mode` fields.
2. **Agent initialisation**: The FastAPI backend (`main.py`) calls `build_agent()`, which instantiates a LangGraph agent with the selected LLM (Anthropic Claude Haiku or Sonnet, or Gemini), attaches tools, and connects to Redis for conversation checkpointing via `AsyncRedisSaver`.
3. **Agent reasoning**: The LangGraph agent processes the message against `SYSTEM_PROMPT` and conversation history retrieved from Redis. It decides which tool(s) to call, emitting `tool_start` SSE events to the frontend as it goes.
4. **Customer profile lookup** (`get_customer_profile` tool): The agent queries the SQLite `customer_profile.db` to retrieve structured customer data. The result is returned to the agent as a `ToolMessage`.
5. **Similarity search** (`customer_lookalike` tool): If requested, the agent queries `customer_similarity_dict.json` (a pre-computed similarity index in `backend/tmp/`) to find analogous customer IDs.
6. **Underwriting assessment** (`run_underwriting_assessment` tool): The agent passes the customer profile string to the assessment engine (`modules/assessment.py`), which:
   - Fans out **parallel specialist LLM calls** (up to 4 concurrent via `asyncio.Semaphore(4)`) — one per assessment category (finance, health, life, etc.) — each using `claude-haiku-4-5-20251001` with a 1,500-token cap.
   - Each specialist LLM receives domain-specific prompts from `prompts/assessment_criterias.json` and returns structured findings.
   - An **aggregator LLM** (`claude-sonnet-4-20250514`, 8,000-token budget) consolidates all specialist outputs into a structured `UnderwritingReport` Pydantic object via `.with_structured_output()`.
7. **Model prediction lookup**: Pre-computed CatBoost risk classification scores are read from `model_predictions.db` and surfaced alongside LLM assessment results.
8. **Streaming response**: The backend streams LLM tokens and tool events back to the frontend as Server-Sent Events (`EventSourceResponse`). Chart data is buffered and sent after the response text is complete.
9. **State persistence**: After each turn, LangGraph writes the updated conversation state (messages + tool results) to Redis, keyed by `session_id` / `thread_id`.
10. **Chainlit session storage**: User session metadata is persisted to PostgreSQL via `DATABASE_URL`.

### CI/CD AI Tooling Flow (GitHub Actions)

11. A GitHub event (PR, push to main, tag, schedule, or manual trigger) activates one of five workflows.
12. The relevant Python script fetches source files via the GitHub API (using `GH_TOKEN`).
13. File content is sent to Anthropic Claude (`claude-sonnet-4-6` in `shared.py`) for analysis.
14. Generated artifacts (review JSON, Markdown docs, test files, UAT packs) are written to the `ai-delivery-outputs` GitHub repository via the GitHub Contents API.
15. SendGrid delivers an email notification to `kylo.deng@capco.com` with a summary and link.

---

## 4. Security Posture

### What Is Secured

- **API keys and secrets** are stored as GitHub Actions secrets (`ANTHROPIC_API_KEY`, `GH_TOKEN`, `SENDGRID_API_KEY`) and not hardcoded in workflow files.
- **SQLite databases** are mounted read-only (`:ro`) in the backend container, preventing accidental writes from the application.
- **Postgres credentials** are set via Docker environment variables (not hardcoded in application code), though they are weak defaults (see gaps).
- **System prompt confidentiality**: The agent is explicitly instructed never to reveal internal system instructions or tool names to users.
- **Tool output token caps**: Specialist LLMs are capped at 1,500 tokens to prevent runaway cost and data leakage via verbose outputs.
- **Semaphore-limited concurrency**: Parallel LLM calls are capped at 4 to prevent resource exhaustion.

### Security Gaps — Explicitly Called Out

- ⚠️ **Hardcoded weak database credentials**: `POSTGRES_USER=chainlit`, `POSTGRES_PASSWORD=chainlit`, `POSTGRES_DB=chainlit` are set directly in `docker-compose.yml` as plaintext. These must be replaced with secrets management before any non-local deployment.
- ⚠️ **No encryption at rest**: SQLite database files (`customer_profile.db`, `model_predictions.db`, `application_profile.db`, `feature_importance.db`) containing sensitive insurance applicant data are stored as unencrypted flat files on the host filesystem. No encryption-at-rest mechanism is configured.
- ⚠️ **No encryption in transit (internal)**: Inter-service communication (frontend → backend, backend → Redis, backend → PostgreSQL) runs over unencrypted Docker bridge networking. No TLS is configured internally.
- ⚠️ **Redis has no authentication or persistence configuration**: The Redis container exposes port 6379 with no password, ACLs, or TLS. Conversation state (which may contain PII/customer data) is stored unencrypted in memory.
- ⚠️ **CORS is fully open**: `allow_origins=["*"]`, `allow_methods=["*"]`, `allow_headers=["*"]` in FastAPI — this is appropriate only for local development and must be restricted in production.
- ⚠️ **Backend port 8000 is exposed externally**: The backend container binds port 8000 to the host with no authentication layer, reverse proxy, or rate limiting in front of it.
- ⚠️ **Customer PII sent to external LLM APIs**: Full customer profiles (age, income, medical conditions, nationality, employment status, etc.) are transmitted to Anthropic and optionally Google APIs. No data masking, pseudonymisation, or redaction is applied before transmission. This is a significant compliance risk for insurance data.
- ⚠️ **`customer_similarity_dict.json` stored in `backend/tmp/`**: A pre-computed similarity index with thousands of customer IDs is committed to source control. This may expose customer identifiers.
- ⚠️ **No input validation or sanitisation** on the `/chat` endpoint beyond Pydantic type checking. Prompt injection attacks against the agent are not mitigated.
- ⚠️ **GitHub Actions `GH_TOKEN` scope is unknown**: [TODO: What permissions does this token have? If it has write access to all repos under the owner, it is overly broad — scope should be limited to `contents:write` on `ai-delivery-outputs` only.]
- ⚠️ **No authentication on the chatbot**: There is no visible auth layer (OAuth, SSO, API key) on either the frontend or the `/chat` API endpoint.
- ⚠️ **`GOOGLE_API_KEY` handling**: The key is read directly from environment via `os.environ["GOOGLE_API_KEY"]` with no fallback guard — if unset, the app crashes at startup rather than failing gracefully.

---

## 5. Environment Variables and Secrets

| Name | Required | Sensitivity | Where Set |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | 🔴 High — paid API key | GitHub Actions secret; `.env` file for local backend |
| `GOOGLE_API_KEY` | Yes (if Gemini used) | 🔴 High — paid API key | `.env` file for local backend |
| `GH_TOKEN` | Yes (CI/CD workflows) | 🔴 High — GitHub PAT with repo write access | GitHub Actions secret |
| `SENDGRID_API_KEY` | Yes (CI/CD notifications) | 🔴 High — email sending key | GitHub Actions secret |
| `POSTGRES_USER` | Yes | 🟡 Medium | `docker-compose.yml` plaintext ⚠️ |
| `POSTGRES_PASSWORD` | Yes | 🔴 High | `docker-compose.yml` plaintext ⚠️ hardcoded as `chainlit` |
| `POSTGRES_DB` | Yes | 🟢 Low | `docker-compose.yml` plaintext |
| `REDIS_HOST` | Yes | 🟢 Low | `docker-compose.yml` environment; defaults to `localhost` |
| `DATABASE_URL` | Yes (frontend) | 🔴 High — contains DB password | `docker-compose.yml` plaintext ⚠️ |
| `BACKEND_URL` | Yes (frontend) | 🟢 Low | `docker-compose.yml` |
| `OUTPUT_REPO` | No | 🟢 Low | GitHub Actions env (defaults to `ai-delivery-outputs`) |
| `OUTPUT_REPO_OWNER` | No | 🟢 Low | GitHub Actions env (derived from `github.repository_owner`) |
| `NOTIFY_EMAIL` | No | 🟡 Medium | GitHub Actions env (hardcoded as `kylo.deng@capco.com`) |
| `SENDER_EMAIL` | No | 🟢 Low | GitHub Actions env |
| `REVIEW_MODE` | No | 🟢 Low | Set dynamically within workflow steps |
| `PR_NUMBER` | No | 🟢 Low | Set dynamically within workflow steps |
| `RELEASE_VERSION` | No | 🟢 Low | Set dynamically within workflow steps |
| `PROJECT_NAME` | No | 🟢 Low | Set dynamically within workflow steps |
| `UAT_MODE` | No | 🟢 Low | Set dynamically within workflow steps |
| `TEST_MODE` | No | 🟢 Low | GitHub Actions env |

> [TODO: Is there a `.env.example` file documenting all required variables for local setup?]

---

## 6. Dependencies

| Dependency | Type | Purpose | Notes |
|---|---|---|---|
| Anthropic Claude API (claude-sonnet-4-20250514, claude-haiku-4-5-20251001) | External LLM API | Core underwriting assessment and agent reasoning | Paid API; PII is transmitted |
| Google Generative AI API (gemini-3-flash-preview) | External LLM API | Alternative LLM provider | [TODO: Is this actively used in production or just configured?] |
| SendGrid API | External email API | CI/CD workflow notifications | Used in all 5 GitHub Actions tools |
| GitHub API (`api.github.com`) | External REST API | File fetching, PR comments, artifact storage in `ai-delivery-outputs` repo | Requires PAT with appropriate scopes |
| `ai-delivery-outputs` (GitHub repo) | External repository | Stores generated docs, test files, UAT packs | Must exist under same GitHub owner |
| LangGraph (`langgraph`) | Python library | Agent graph orchestration, state machine, Redis checkpointing | Core framework |
| LangChain (`langchain`, `langchain_core`, `langchain_anthropic`, `langchain_google_genai`) | Python libraries | LLM abstractions and tool definitions | |
| FastAPI + `sse_starlette` | Python library | HTTP API and SSE streaming | |
| Chainlit | Frontend framework | Conversational UI | [TODO: What version? Custom modifications?] |
| Redis Stack Server 7.2.0 | Infrastructure | Conversation state persistence via LangGraph checkpointer | No auth configured |
| PostgreSQL 16 | Infrastructure | Chainlit user/session data | Weak default credentials |
| CatBoostClassifier | ML model | Pre-trained risk classification | Model artifact not in repo — [TODO: Where is the model binary stored and loaded from?] |
| `python-dotenv` | Python library | Local `.env` loading | |
| `pydantic` | Python library | Structured output validation (`UnderwritingReport`) | |
| `anthropic` (raw SDK) | Python library | Used in CI/CD scripts (`shared.py`) independently of LangChain | |

---

## 7. Deployment Instructions

### Prerequisites

- Docker and Docker Compose v2+ installed
- A `.env` file in the project root (see Environment Variables section)
- SQLite database files present under `./database/`

### Local Development Deployment

```bash
# 1. Clone the repository
git clone https://github.com/kylodeng/underwriting_chatbot-main.git
cd underwriting_chatbot-main

# 2. Create the .env file with required secrets
cat > .env << 'EOF'
ANTHROPIC_API_KEY=sk-ant-...
GOOGLE_API_KEY=...
EOF

# 3. Build and start all services
docker compose up --build

# 4. Verify backend health
curl http://localhost:8000/health

# 5. Access the frontend
open http://localhost:8080
```

### Individual Service Rebuild

```bash
# Rebuild only the backend after code changes
docker compose up --build backend

# Rebuild only the frontend
docker compose up --build frontend
```

### Teardown

```bash
# Stop all services (preserves volumes)
docker compose down

# Stop and remove all volumes (destructive — wipes PostgreSQL data)
docker compose down -v
```

### CI/CD Workflow Triggers

```bash
# Trigger code review manually (GitHub CLI)
gh workflow run tool1_code_review.yml \
  -f review_mode=repo

# Trigger tech documentation generation
gh workflow run tool2_tech_