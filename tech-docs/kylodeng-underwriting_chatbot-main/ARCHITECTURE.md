# Architecture Document: kylodeng/underwriting_chatbot-main

---

## 1. Overview

The Underwriting Chatbot is an AI-powered insurance underwriting assistant that enables underwriters to assess customer risk profiles through a conversational interface. The system ingests structured customer data from SQLite databases, orchestrates multi-specialist LLM assessments (finance, health, life, KYC, etc.) using Anthropic Claude and Google Gemini models via a LangGraph agent framework, and surfaces a structured `UnderwritingReport` including risk classification, findings, and follow-up items. A Chainlit-based frontend communicates with a FastAPI streaming backend over Server-Sent Events (SSE); Redis provides LangGraph conversation checkpointing and PostgreSQL persists Chainlit session/user data. The repository also includes five GitHub Actions CI/CD pipelines that use Claude to automate code review, technical documentation, business documentation, test generation, and UAT facilitation.

---

## 2. Resources Deployed

| Resource | Type | Cloud Provider | Purpose |
|---|---|---|---|
| `backend` | Docker container (FastAPI + LangGraph) | Self-hosted (Docker Compose) | Core API: streaming chat, agent orchestration, underwriting assessment |
| `frontend` | Docker container (Chainlit) | Self-hosted (Docker Compose) | Conversational UI for underwriters |
| `redis` | Docker container (`redis/redis-stack-server:7.2.0-v14`) | Self-hosted (Docker Compose) | LangGraph conversation checkpointing (session memory) |
| `postgres` | Docker container (`postgres:16-alpine`) | Self-hosted (Docker Compose) | Chainlit session, user, and thread persistence |
| `customer_profile.db` | SQLite file (read-only bind mount) | Self-hosted | Customer demographic and KYC data |
| `feature_importance.db` | SQLite file (read-only bind mount) | Self-hosted | ML model feature importance data |
| `model_predictions.db` | SQLite file (read-only bind mount) | Self-hosted | Pre-computed CatBoost risk classification predictions |
| `application_profile.db` | SQLite file (read-only bind mount) | Self-hosted | Insurance application data |
| `postgres_data` | Docker named volume | Self-hosted | Persistent PostgreSQL storage |
| Anthropic Claude API | External SaaS API | Anthropic | LLM inference (claude-sonnet-4, claude-haiku-4-5) for agent and specialists |
| Google Gemini API | External SaaS API | Google Cloud | Alternative LLM inference (gemini-3-flash-preview) |
| GitHub Actions runners | CI/CD compute (`ubuntu-latest`) | GitHub | Automated code review, doc generation, test generation, UAT |
| `ai-delivery-outputs` (GitHub repo) | External GitHub repository | GitHub | Stores AI-generated documentation, test files, UAT packs |
| SendGrid | External SaaS API | Twilio/SendGrid | Email notifications for CI/CD workflow outputs |

---

## 3. Data Flow

### Runtime (Underwriting Assessment)

1. **User → Frontend (Chainlit):** An underwriter types a question or request (e.g., "Assess customer CUST00000001") into the Chainlit web UI on port 8080.
2. **Frontend → Backend (FastAPI `/chat`):** The frontend POSTs a `ChatRequest` (message, session_id, model, mode, temperature) to the FastAPI backend on port 8000 over the internal Docker network.
3. **Backend → LangGraph Agent:** FastAPI builds a LangGraph agent (`build_agent`) with Redis checkpointing, loads conversation history for the `thread_id` (session_id) from Redis, and streams agent events via `astream_events`.
4. **Agent → Tool: `get_customer_profile`:** The agent determines it needs customer data and calls the `get_customer_profile` tool, which queries the read-only `customer_profile.db` SQLite database (and possibly other `.db` files) mounted into the container.
5. **Agent → Tool: `customer_lookalike`:** Optionally, the agent calls the `customer_lookalike` tool, which references the in-memory `customer_similarity_dict.json` (pre-computed similarity index) to find comparable customers.
6. **Agent → Tool: `run_underwriting_assessment`:** The agent calls the underwriting assessment tool, passing the retrieved customer profile string.
7. **Assessment → Parallel Specialist LLMs:** The assessment module concurrently dispatches (up to 4 parallel, via `asyncio.Semaphore(4)`) individual category assessments (finance, health, life, KYC, etc.) to the specialist LLM (Claude Haiku or configured model) via the Anthropic/Google API. Each specialist receives domain-specific prompts from `assessment_criterias.json`.
8. **Specialist Results → Aggregator LLM:** All specialist category reports are collected and passed to a second (aggregator) Claude LLM call with a large token budget, which produces a structured `UnderwritingReport` Pydantic object via LangChain's `with_structured_output`.
9. **Assessment Result → Agent → Backend:** The structured report is serialised, returned to the agent as a tool result, and the agent formulates its final answer.
10. **Backend → Frontend (SSE):** FastAPI streams events (`tool_start`, `tool_end`, `response`, `chart`) back to the frontend as Server-Sent Events. Chart/visualisation data (feature importance, risk distributions) is buffered and emitted after the text response.
11. **Redis:** At each agent step, LangGraph checkpoints the conversation state to Redis so that follow-up messages within the same `session_id` have full history.
12. **PostgreSQL:** Chainlit reads/writes session, user, and thread metadata to PostgreSQL for UI state persistence.

### CI/CD Data Flow (GitHub Actions)

1. A trigger (PR, push to main, tag, schedule, or manual dispatch) fires one of the five workflow YAML files.
2. The workflow checks out the source repository and installs `anthropic` and `requests` Python packages.
3. The relevant Python script (`tool1_` through `tool5_`) calls `get_repo_files` or `get_pr_diff` via the GitHub REST API using `GH_TOKEN`.
4. File contents are passed as context to Claude (via `ANTHROPIC_API_KEY`) to generate reviews, documentation, tests, or UAT packs.
5. Outputs are written to the `ai-delivery-outputs` GitHub repository via `write_output_file` (GitHub Contents API with `GH_TOKEN`).
6. For PR workflows, review comments are posted directly to the PR via `post_pr_comment`.
7. SendGrid is called to email results to `kylo.deng@capco.com`.

---

## 4. Security Posture

### Secured

- **Secrets managed via GitHub Actions Secrets** (`ANTHROPIC_API_KEY`, `GH_TOKEN`, `SENDGRID_API_KEY`): Not hardcoded in workflow YAMLs; injected at runtime.
- **SQLite databases mounted read-only** (`:ro` flag in Docker Compose): Prevents the backend container from modifying source-of-truth data.
- **System prompt confidentiality enforced in agent:** Both agent implementations instruct the LLM to never reveal internal system instructions or tool details to end users.
- **Semaphore limiting parallel LLM calls** (`asyncio.Semaphore(4)`): Provides basic rate-limiting on outbound API calls.
- **Pydantic models for structured output:** `UnderwritingReport` and related models enforce schema validation on LLM outputs before use.

### Not Secured / Gaps

- **⚠️ PostgreSQL credentials hardcoded in `docker-compose.yml`:** `POSTGRES_USER=chainlit`, `POSTGRES_PASSWORD=chainlit`, `POSTGRES_DB=chainlit` are plaintext defaults. No secrets management used.
- **⚠️ Redis has no authentication configured:** The Redis container (`redis/redis-stack-server`) is started with no password, no TLS, and is exposed on `0.0.0.0:6379`. Any process on the host can read/write conversation checkpoints, which may contain PII.
- **⚠️ Redis port 6379 exposed to host:** Should be internal-only on the Docker network.
- **⚠️ PostgreSQL port 5432 exposed to host:** Should be internal-only if no direct external access is needed.
- **⚠️ CORS is fully open (`allow_origins=["*"]`):** The FastAPI backend accepts requests from any origin. This is inappropriate for a production system handling sensitive underwriting data.
- **⚠️ No authentication or authorisation on the `/chat` API endpoint:** Any caller with network access to port 8000 can submit queries. There is no JWT, API key, or OAuth enforcement visible in the source.
- **⚠️ No HTTPS/TLS termination in Docker Compose:** All traffic between services and to the host is unencrypted. No reverse proxy (nginx, Traefik) with TLS is configured.
- **⚠️ Customer PII in SQLite databases is unencrypted at rest:** The `.db` files are plain SQLite with no encryption layer (e.g., SQLCipher).
- **⚠️ `customer_similarity_dict.json` stored in `backend/tmp/`:** Pre-computed customer similarity data (linking customer IDs) is stored as a plain JSON file committed to the repository — potential PII exposure in source control.
- **⚠️ `GH_TOKEN` scope unknown:** [TODO: What permissions does the GH_TOKEN have? If it is a classic PAT with `repo` scope it has write access to all repositories owned by the user — this should be a fine-grained token scoped only to `ai-delivery-outputs`.]
- **⚠️ No input sanitisation visible before passing user messages to LLM:** Prompt injection risk — user-supplied messages in `ChatRequest.message` are passed directly to the LLM without sanitisation.
- **⚠️ No rate limiting on `/chat` endpoint:** The API is vulnerable to abuse/cost amplification via unlimited LLM calls.
- **⚠️ `_charts_sent` is a module-level in-memory set:** Not thread-safe across multiple workers and lost on restart; not a security issue but a correctness gap.
- **⚠️ `.env` file loaded at runtime:** Secrets are loaded from a `.env` file in the container via `python-dotenv`. If this file is committed or leaked it exposes all API keys.

---

## 5. Environment Variables and Secrets

| Name | Required | Sensitivity | Where set |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | 🔴 High (API key, billed) | GitHub Actions Secret; `.env` file for backend |
| `GOOGLE_API_KEY` | Yes (if Gemini used) | 🔴 High (API key, billed) | `.env` file for backend |
| `GH_TOKEN` | Yes (CI/CD) | 🔴 High (GitHub access token) | GitHub Actions Secret |
| `SENDGRID_API_KEY` | Yes (CI/CD notifications) | 🔴 High (API key) | GitHub Actions Secret |
| `REDIS_HOST` | No | 🟢 Low | Docker Compose `environment`; defaults to `localhost` |
| `DATABASE_URL` | Yes (frontend) | 🟡 Medium (DB credentials in URL) | Docker Compose `environment` (hardcoded) |
| `BACKEND_URL` | Yes (frontend) | 🟢 Low | Docker Compose `environment` (hardcoded) |
| `POSTGRES_USER` | Yes | 🟡 Medium | Docker Compose `environment` (hardcoded: `chainlit`) |
| `POSTGRES_PASSWORD` | Yes | 🔴 High | Docker Compose `environment` (hardcoded: `chainlit`) — **not a secret** |
| `POSTGRES_DB` | Yes | 🟢 Low | Docker Compose `environment` (hardcoded: `chainlit`) |
| `OUTPUT_REPO` | No (CI/CD) | 🟢 Low | GitHub Actions `env` (hardcoded: `ai-delivery-outputs`) |
| `OUTPUT_REPO_OWNER` | No (CI/CD) | 🟢 Low | GitHub Actions `env` (derived from `github.repository_owner`) |
| `NOTIFY_EMAIL` | No (CI/CD) | 🟢 Low | GitHub Actions `env` (hardcoded: `kylo.deng@capco.com`) |
| `SENDER_EMAIL` | No (CI/CD) | 🟢 Low | GitHub Actions `env` (hardcoded) |
| `REVIEW_MODE` | No (CI/CD) | 🟢 Low | Set dynamically in workflow step |
| `PR_NUMBER` | No (CI/CD) | 🟢 Low | Set dynamically in workflow step |
| `RELEASE_VERSION` | No (CI/CD) | 🟢 Low | Set dynamically in workflow step |
| `TEST_MODE` | No (CI/CD) | 🟢 Low | GitHub Actions `env` |
| `UAT_MODE` | No (CI/CD) | 🟢 Low | Set dynamically in workflow step |

> [TODO: Confirm the full list of variables in the `.env` file — it is referenced by `env_file: .env` in docker-compose but not included in the repository.]

---

## 6. Dependencies

| Dependency | Type | Used By | Purpose |
|---|---|---|---|
| Anthropic Claude API (`claude-sonnet-4-20250514`, `claude-haiku-4-5-20251001`) | External SaaS | Backend, GitHub Actions | LLM inference for agent, specialists, aggregator, and all CI/CD tools |
| Google Gemini API (`gemini-3-flash-preview`) | External SaaS | Backend | Alternative LLM provider (configured but `azure` and `openai` stubs are `None`) |
| LangGraph / LangChain | Python library | Backend | Agent orchestration, tool calling, streaming, structured output |
| Chainlit | Python library / Docker | Frontend | Conversational UI framework |
| FastAPI + `sse_starlette` | Python library | Backend | REST API and SSE streaming |
| Redis Stack (`redis/redis-stack-server:7.2.0-v14`) | Infrastructure | Backend | LangGraph async checkpoint storage |
| PostgreSQL 16 | Infrastructure | Frontend (Chainlit) | Session/thread/user persistence |
| CatBoost (pre-trained model) | ML model artifact | Backend (`model_predictions.db`) | Pre-computed risk classification scores (model not trained at runtime) |
| SendGrid | External SaaS | GitHub Actions | Email delivery for workflow notifications |
| GitHub REST API (`api.github.com`) | External API | GitHub Actions | Fetch repo files, PR diffs, post comments, write output files |
| `ai-delivery-outputs` (GitHub repo) | External GitHub repo (`kylodeng/ai-delivery-outputs`) | GitHub Actions | Stores all AI-generated artefacts |
| `python-dotenv` | Python library | Backend | `.env` file loading |
| `pydantic` | Python library | Backend | Schema validation for LLM outputs and API request models |
| `anthropic` (Python SDK) | Python library | GitHub Actions scripts | Direct Claude API calls in CI/CD |
| `requests` | Python library | GitHub Actions scripts | GitHub and SendGrid HTTP calls |

---

## 7. Deployment Instructions

### Prerequisites

- Docker and Docker Compose v2+ installed
- A `.env` file in the repository root containing at minimum `ANTHROPIC_API_KEY` and `GOOGLE_API_KEY`
- PostgreSQL init script at `./postgres/init.sql` (referenced in Docker Compose)

### Local Deployment

```bash
# 1. Clone the repository
git clone https://github.com/kylodeng/underwriting_chatbot-main.git
cd underwriting_chatbot-main

# 2. Create the .env file with required secrets
cat > .env << 'EOF'
ANTHROPIC_API_KEY=sk-ant-...
GOOGLE_API_KEY=AIza...
EOF

# 3. Build and start all services
docker compose up --build -d

# 4. Verify all containers are healthy
docker compose ps

# 5. Check backend health
curl http://localhost:8000/health

# 6. Access the frontend
# Open http://localhost:8080 in a browser
```

### Stopping and Teardown

```bash
# Stop services (preserves volumes)
docker compose down

# Stop and remove all volumes (DESTROYS PostgreSQL data)
docker compose down -v
```

### CI/CD Workflows (GitHub Actions)

The