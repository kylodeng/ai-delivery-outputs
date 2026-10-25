# Architecture Document: kylodeng/underwriting_chatbot-main

---

## 1. Overview

The Underwriting Chatbot is an AI-assisted insurance underwriting platform that enables underwriters to assess customer risk profiles through a conversational interface. The system ingests structured customer data from SQLite databases (covering customer profiles, financial information, KYC records, and application histories), routes questions through a LangGraph-orchestrated multi-agent pipeline, and produces structured `UnderwritingReport` outputs via parallel specialist LLM calls (finance, health, life, etc.) aggregated by a senior-underwriter LLM. The frontend (port 8080) communicates with a FastAPI backend (port 8000) via Server-Sent Events (SSE) for streaming responses; Redis provides agent memory/checkpointing and PostgreSQL provides Chainlit session persistence. A separate suite of five GitHub Actions CI/CD workflows use Anthropic's Claude API to automate code review, technical documentation, business documentation, test generation, and UAT facilitation.

---

## 2. Resources Deployed

| Resource | Type | Cloud Provider | Purpose |
|---|---|---|---|
| `backend` | Docker container (FastAPI + LangGraph) | Local / self-hosted | Serves `/chat` and `/health` endpoints; runs underwriting agent pipeline |
| `frontend` | Docker container | Local / self-hosted | Web UI for underwriter interaction (port 8080) |
| `redis` (redis-stack-server:7.2.0-v14) | Docker container | Local / self-hosted | LangGraph agent checkpointing / conversation memory |
| `postgres` (postgres:16-alpine) | Docker container | Local / self-hosted | Chainlit session storage |
| `postgres_data` | Docker named volume | Local / self-hosted | Persistent PostgreSQL data |
| `customer_profile.db` | SQLite database (read-only bind mount) | Local / self-hosted | Customer demographic and profile data |
| `feature_importance.db` | SQLite database (read-only bind mount) | Local / self-hosted | CatBoost model feature importance scores |
| `model_predictions.db` | SQLite database (read-only bind mount) | Local / self-hosted | Pre-computed ML risk classification predictions |
| `application_profile.db` | SQLite database (read-only bind mount) | Local / self-hosted | Insurance application metadata |
| `CatBoostClassifier` (model_card.json) | ML model (v1.0, 2024-06-01) | Local / self-hosted | Risk classification (Preferred / Standard Plus / Standard / Substandard) |
| Anthropic Claude Sonnet 4 (`claude-sonnet-4-20250514`) | External LLM API | Anthropic (cloud) | Aggregator LLM for final underwriting report synthesis |
| Anthropic Claude Haiku 4.5 (`claude-haiku-4-5-20251001`) | External LLM API | Anthropic (cloud) | Fast specialist agents and default agent LLM |
| Google Gemini Flash (`gemini-3-flash-preview`) | External LLM API | Google Cloud (cloud) | Alternative LLM provider (configured, usage path unclear) |
| GitHub Actions runners (ubuntu-latest) | CI/CD compute | GitHub (cloud) | Runs five AI delivery workflow tools |
| `ai-delivery-outputs` (GitHub repo) | External GitHub repository | GitHub (cloud) | Stores generated docs, test files, UAT packs, review reports |
| SendGrid | External email API | Twilio/SendGrid (cloud) | Notifies `kylo.deng@capco.com` of workflow outputs |

---

## 3. Data Flow

### Runtime (Chat) Data Flow

1. **User input**: An underwriter submits a natural-language message via the frontend (port 8080); the frontend POSTs a `ChatRequest` (message, session_id, model, mode, temperature) to `POST /chat` on the backend (port 8000).
2. **Agent initialisation**: `build_agent()` in `graph.py` constructs a LangGraph agent backed by the selected LLM (Anthropic Haiku by default), attaches tools (`get_customer_profile`, `run_underwriting_assessment`, `customer_lookalike`), and connects to the `AsyncRedisSaver` checkpointer using the session's `thread_id` from Redis.
3. **Agent reasoning**: The agent LLM (tagged `"agent"`) receives the system prompt (including skill documentation and model card context) and the user message. It reasons about which tool(s) to call, emitting JSON tool-call instructions.
4. **Tool: `get_customer_profile`**: Queries the read-only SQLite databases (`customer_profile.db`, `application_profile.db`, etc.) to retrieve structured customer data and returns it as a profile string.
5. **Tool: `customer_lookalike`**: Looks up pre-computed similar customer IDs from `customer_similarity_dict.json` in `backend/tmp/`.
6. **Tool: `run_underwriting_assessment`**: Receives the customer profile string and fans out **parallel async specialist LLM calls** (up to 4 concurrent via `asyncio.Semaphore(4)`) — one per assessment category (finance, health, life, etc.) — each using Claude Haiku with `specialist_max_tokens=1500`. Results are collected and passed to the **aggregator LLM** (Claude Sonnet) with `aggregator_max_tokens=8000`, which produces a structured `UnderwritingReport` Pydantic object via `with_structured_output`.
7. **Streaming SSE response**: The backend streams events back to the frontend over SSE: `tool_start` events (tool name), `on_chat_model_stream` events (agent text tokens, filtered by `"agent"` tag), `thinking` events (specialist LLM tokens, filtered by `"thinking"` tag), and `chart` events (feature importance data, deduplicated per session/field via `_charts_sent`).
8. **Memory persistence**: After each turn, LangGraph writes the updated conversation state (messages, tool results) to Redis using the `thread_id` as the checkpoint key.
9. **Session persistence**: PostgreSQL (Chainlit schema) stores session metadata; the frontend reads `DATABASE_URL` pointing to the Chainlit Postgres instance.

### CI/CD Data Flow (GitHub Actions)

10. **Trigger**: A PR, push, tag, scheduled cron, or manual dispatch fires one of the five workflow YAML files.
11. **Code ingestion**: The relevant Python script (`tool1`–`tool5`) fetches source files or diffs from the source GitHub repo via the GitHub REST API using `GH_TOKEN`.
12. **Claude processing**: Files/diffs are sent to Anthropic Claude (claude-sonnet-4-6 in `shared.py`) for review, doc generation, test generation, or UAT pack creation.
13. **Output persistence**: Generated artefacts (markdown docs, test files, JSON reports, CSV UAT packs) are written to the `ai-delivery-outputs` GitHub repository via the GitHub Contents API.
14. **Notification**: SendGrid sends an email notification to `kylo.deng@capco.com` with a link to the generated output.
15. **PR comment** (Tool 1 only): A review comment is posted back to the originating pull request via the GitHub Issues Comments API.

---

## 4. Security Posture

### ✅ What Is Secured

- **SQLite databases mounted read-only** (`ro` flag in `docker-compose.yml`): prevents the backend container from modifying source data.
- **Secrets managed via GitHub Actions Secrets**: `ANTHROPIC_API_KEY`, `GH_TOKEN`, `SENDGRID_API_KEY` are not hardcoded in workflow YAMLs and are injected as environment variables at runtime.
- **System prompt confidentiality**: The agent is explicitly instructed never to reveal internal system instructions or tool names to end users.
- **Structured output enforcement**: `UnderwritingReport` uses Pydantic `Literal` types for `risk_class`, reducing prompt-injection risk on the classification output.
- **Semaphore on parallel LLM calls**: `asyncio.Semaphore(4)` limits concurrent outbound API calls, providing some protection against runaway costs.

### ❌ Gaps and Missing Controls

- **No encryption at rest**: PostgreSQL data volume (`postgres_data`) and all four SQLite databases are stored unencrypted on the host filesystem. Customer PII and financial data are exposed if the host is compromised.
- **No encryption in transit (internal)**: Docker internal network traffic between `frontend`, `backend`, `redis`, and `postgres` uses plain HTTP/TCP — no TLS on inter-service communication.
- **Redis has no authentication**: The `redis` service in `docker-compose.yml` exposes port 6379 with no password, no ACL, and no `requirepass` configuration. Conversation memory (including customer PII surfaced by tools) is accessible to anyone who can reach that port.
- **Hardcoded PostgreSQL credentials**: `POSTGRES_USER=chainlit`, `POSTGRES_PASSWORD=chainlit`, and `DATABASE_URL=postgresql+asyncpg://chainlit:chainlit@postgres:5432/chainlit` are plaintext in `docker-compose.yml` and the frontend environment block — not sourced from secrets.
- **CORS policy is fully open**: `allow_origins=["*"]`, `allow_methods=["*"]`, `allow_headers=["*"]` in `main.py` — the backend API accepts requests from any origin.
- **No authentication or authorisation on the API**: The `/chat` endpoint has no authentication middleware. Any client that can reach port 8000 can submit queries and receive customer PII.
- **`GH_TOKEN` scope unknown**: [TODO: What permissions does this PAT have? If it has `repo` write scope across the organisation it is overly broad — use a fine-grained token scoped to only the `ai-delivery-outputs` repo.]
- **Customer similarity data in `backend/tmp/`**: `customer_similarity_dict.json` contains real customer IDs and is committed to the repository. This is a data leakage risk if the repo is ever made public.
- **No input sanitisation**: User messages are passed directly to the LLM without sanitisation, creating prompt-injection risk.
- **Anthropic and Google API keys loaded from `.env`**: The `.env` file is referenced by `env_file: .env` in `docker-compose.yml`. If `.env` is committed to the repository, keys are leaked. [TODO: Confirm `.env` is in `.gitignore`.]
- **No secrets scanning**: No `gitleaks`, `truffleHog`, or GitHub secret scanning workflow is configured.
- **`azure` and `openai` model providers configured as `None`**: If a caller passes `model="azure"` or `model="openai"`, the backend raises an unhandled `ValueError` rather than a graceful 400 response.

---

## 5. Environment Variables and Secrets

| Name | Required | Sensitivity | Where Set |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | 🔴 High — API key with billing implications | GitHub Actions Secret; backend `.env` file |
| `GH_TOKEN` | Yes | 🔴 High — GitHub PAT with repo write access | GitHub Actions Secret |
| `SENDGRID_API_KEY` | Yes | 🔴 High — Email sending API key | GitHub Actions Secret |
| `GOOGLE_API_KEY` | Yes (if Gemini used) | 🔴 High — GCP API key | Backend `.env` file |
| `POSTGRES_USER` | Yes | 🟡 Medium | Hardcoded in `docker-compose.yml` (`chainlit`) |
| `POSTGRES_PASSWORD` | Yes | 🔴 High — **Currently hardcoded** | Hardcoded in `docker-compose.yml` (`chainlit`) |
| `POSTGRES_DB` | Yes | 🟢 Low | Hardcoded in `docker-compose.yml` (`chainlit`) |
| `DATABASE_URL` | Yes | 🔴 High — Contains credentials | Hardcoded in `docker-compose.yml` frontend environment |
| `REDIS_HOST` | No | 🟢 Low | `docker-compose.yml` environment (`redis`); defaults to `localhost` |
| `OUTPUT_REPO` | No | 🟢 Low | GitHub Actions env (`ai-delivery-outputs`) |
| `OUTPUT_REPO_OWNER` | No | 🟢 Low | GitHub Actions env (derived from `github.repository_owner`) |
| `NOTIFY_EMAIL` | No | 🟡 Medium — PII (email address) | Hardcoded in workflow YAMLs (`kylo.deng@capco.com`) |
| `SENDER_EMAIL` | No | 🟢 Low | Hardcoded in workflow YAMLs (`noreply@ai-delivery.capco.com`) |
| `REVIEW_MODE` | No | 🟢 Low | Set dynamically in Tool 1 workflow step |
| `PR_NUMBER` | No | 🟢 Low | Set dynamically in Tool 1 workflow step |
| `RELEASE_VERSION` | No | 🟢 Low | Set dynamically in Tool 3 / Tool 5 workflow steps |
| `PROJECT_NAME` | No | 🟢 Low | Set dynamically in Tool 3 workflow step |
| `TEST_MODE` | No | 🟢 Low | GitHub Actions env (defaults to `generate`) |
| `UAT_MODE` | No | 🟢 Low | Set dynamically in Tool 5 workflow step |
| `UAT_RESULTS_PATH` | No | 🟢 Low | Set dynamically in Tool 5 workflow step |
| `FORCE_JAVASCRIPT_ACTIONS_TO_NODE24` | No | 🟢 Low | All workflow YAMLs |
| `GITHUB_RUN_URL` | No | 🟢 Low | All workflow YAMLs (derived from `github` context) |

---

## 6. Dependencies

| Dependency | Type | Purpose | Version / Notes |
|---|---|---|---|
| Anthropic API (`api.anthropic.com`) | External LLM API | Claude Sonnet 4, Claude Haiku 4.5 for underwriting assessment and all CI/CD AI tools | `claude-sonnet-4-20250514`, `claude-haiku-4-5-20251001`, `claude-sonnet-4-6` (in `shared.py`) |
| Google Generative AI API | External LLM API | Gemini Flash as alternative model provider | `gemini-3-flash-preview` — [TODO: confirm this model name is valid/released] |
| SendGrid API (`api.sendgrid.com`) | External email API | Notification emails on workflow completion | [TODO: confirm sender domain `ai-delivery.capco.com` is verified in SendGrid] |
| GitHub REST API (`api.github.com`) | External VCS/CI API | Fetching repo files, posting PR comments, writing output files | `2022-11-28` API version |
| `ai-delivery-outputs` GitHub repo | External repository | Stores all AI-generated artefacts (docs, tests, UAT packs, review reports) | Must exist under same GitHub owner; [TODO: confirm repo exists and GH_TOKEN has write access] |
| LangChain / LangGraph | Python library | Agent orchestration, tool execution, streaming | `langgraph`, `langchain-core`, `langchain-anthropic`, `langchain-google-genai` |
| Redis Stack Server | Infrastructure | LangGraph conversation checkpointing | `redis/redis-stack-server:7.2.0-v14` |
| PostgreSQL | Infrastructure | Chainlit session persistence | `postgres:16-alpine` |
| FastAPI + SSE-Starlette | Python library | Backend HTTP and streaming API | |
| CatBoostClassifier (serialised model) | ML artefact | Risk classification — [TODO: Where is the model binary? Only `model_card.json` is present in repo; actual serialised model file location unknown] | v1.0, trained 2024-06-01 |
| `customer_profile.db`, `feature_importance.db`, `model_predictions.db`, `application_profile.db` | SQLite databases | Customer and application data | [TODO: How are these databases populated/refreshed? No ETL pipeline is visible in this repo] |
| `customer_similarity_dict.json` | Static JSON file | Pre-computed customer lookalike index | Located at `backend/tmp/` — [TODO: How is this regenerated when new customers are added?] |
| `assessment_criterias.json` | Static JSON file | LLM prompts for each specialist assessment category | Located at `backend/prompts/` |
| `anthropic` (pip) | Python library | Used in CI/CD scripts (`shared.py`) separately from L