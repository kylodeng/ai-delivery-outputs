# Architecture Document: kylodeng/underwriting_chatbot-main

---

## 1. Overview

The Underwriting Chatbot is an AI-powered insurance underwriting assistant that enables underwriters to assess customer risk profiles through a conversational interface. The system ingests pre-built SQLite databases of customer profiles, financial data, KYC records, and ML model predictions, then orchestrates multiple specialist LLM agents (via LangGraph) to produce structured underwriting reports covering finance, health, life, and other assessment domains. A FastAPI backend streams responses via Server-Sent Events (SSE) to a frontend UI (Chainlit-based), while Redis provides LangGraph conversation checkpointing and PostgreSQL stores chat session state. A parallel GitHub Actions CI/CD pipeline uses Claude to automate code review, documentation, test generation, and UAT facilitation across the repository.

---

## 2. Resources Deployed

| Resource | Type | Cloud Provider | Purpose |
|---|---|---|---|
| `backend` | Docker container (FastAPI) | Local / Docker Compose | Serves `/chat` and `/health` endpoints; orchestrates LangGraph agent |
| `frontend` | Docker container (Chainlit) | Local / Docker Compose | Browser-based chat UI for underwriters |
| `redis` (redis-stack-server:7.2.0-v14) | Docker container | Local / Docker Compose | LangGraph conversation checkpointer (thread memory) |
| `postgres` (postgres:16-alpine) | Docker container | Local / Docker Compose | Chainlit session/user data persistence |
| `customer_profile.db` | SQLite file (read-only volume mount) | Local filesystem | Customer demographic and profile data |
| `feature_importance.db` | SQLite file (read-only volume mount) | Local filesystem | ML model feature importance scores |
| `model_predictions.db` | SQLite file (read-only volume mount) | Local filesystem | CatBoostClassifier risk classification predictions |
| `application_profile.db` | SQLite file (read-only volume mount) | Local filesystem | Insurance application records |
| `postgres_data` | Docker named volume | Local / Docker Compose | Persistent PostgreSQL data |
| Anthropic Claude API (claude-sonnet-4-20250514 / claude-haiku-4-5-20251001) | External API | Anthropic (external) | Specialist and aggregator LLM calls for underwriting assessment |
| Google Gemini API (gemini-3-flash-preview) | External API | Google Cloud (external) | Alternative LLM provider (configured, not default) |
| GitHub Actions Runners (ubuntu-latest) | CI/CD compute | GitHub (external) | Automated code review, doc generation, test generation, UAT |
| SendGrid API | External email service | Twilio/SendGrid (external) | CI/CD notification emails |
| `ai-delivery-outputs` | GitHub repository | GitHub (external) | Output store for AI-generated docs, test files, and reports |

---

## 3. Data Flow

### Chat / Underwriting Assessment Flow

1. **User submits query** via the Chainlit frontend UI (port 8080); the frontend POSTs to `http://backend:8000/chat` with `message`, `session_id`, `model`, `mode`, and `temperature`.
2. **Backend builds agent** via `build_agent()` (LangGraph + LangChain), attaching a Redis checkpointer keyed by `thread_id` (= `session_id`) to maintain conversation history.
3. **Agent (LLM decision loop)** receives the user message plus conversation history from Redis; the LLM decides which tool to invoke (`get_customer_profile`, `customer_lookalike`, or `run_underwriting_assessment`).
4. **`get_customer_profile` tool** queries the read-only SQLite databases (`customer_profile.db`, `application_profile.db`, etc.) and returns structured customer data.
5. **`customer_lookalike` tool** reads `backend/tmp/customer_similarity_dict.json` to find similar customers from the pre-computed similarity index.
6. **`run_underwriting_assessment` tool** fans out to N specialist LLM calls concurrently (up to 4 via `asyncio.Semaphore`), each assessing a domain (finance, health, life, etc.) using prompts from `assessment_criterias.json`. Results are aggregated by a second LLM call using structured output (`UnderwritingReport` Pydantic model).
7. **Backend streams events** back to the frontend via SSE (`EventSourceResponse`): `tool_start`, `tool_end`, `response` (streamed text tokens), `thinking` (specialist reasoning), and `chart` events.
8. **Conversation state** is persisted to Redis after each turn; chart deduplication state is held in the in-process `_charts_sent` set.
9. **PostgreSQL** stores Chainlit session/user data (login, history) accessed directly by the frontend container.

### CI/CD AI Tooling Flow

10. **GitHub Actions triggers** (PR open, push to main, tag, schedule, or manual dispatch) invoke one of five workflow scripts.
11. **Python scripts** (`tool1–5`) call the **GitHub API** to fetch source files or PR diffs, then invoke the **Anthropic Claude API** (`claude-sonnet-4-6`) to generate review findings, documentation, test files, or UAT packs.
12. **Outputs are committed** to the `ai-delivery-outputs` GitHub repository via authenticated GitHub API calls.
13. **SendGrid API** sends notification emails to `kylo.deng@capco.com` with links to generated outputs.

---

## 4. Security Posture

### What Is Secured

- **SQLite databases are mounted read-only** (`:ro` flag in Docker Compose volumes) — prevents backend from modifying source data.
- **API keys** (Anthropic, Google, SendGrid) are passed via environment variables / `.env` file and GitHub Actions secrets — not hardcoded in source.
- **GitHub Actions secrets** (`ANTHROPIC_API_KEY`, `GH_TOKEN`, `SENDGRID_API_KEY`) are used for CI/CD pipelines.
- **System prompt confidentiality**: the agent explicitly refuses to reveal internal instructions or tool names to end users.
- **LLM output token caps**: specialist LLMs capped at 1,500 tokens, aggregator at 8,000, to limit runaway generation costs.

### Security Gaps and Issues

- **❌ CORS is fully open**: `allow_origins=["*"]`, `allow_methods=["*"]`, `allow_headers=["*"]` on the FastAPI backend. Any origin can POST to `/chat` — **critical gap for any non-localhost deployment**.
- **❌ No authentication or authorization** on the `/chat` or `/health` endpoints. Any client with network access can submit queries and consume LLM credits.
- **❌ PostgreSQL credentials are hardcoded** in `docker-compose.yml`: `POSTGRES_USER: chainlit`, `POSTGRES_PASSWORD: chainlit`. These are default/weak credentials inappropriate for any non-local environment.
- **❌ Redis has no authentication** configured. Port 6379 is exposed on `0.0.0.0` without a password, ACL, or TLS.
- **❌ No encryption at rest** for SQLite databases, Redis data, or PostgreSQL `postgres_data` volume.
- **❌ No TLS/HTTPS** configured for any service in the Docker Compose stack. All traffic (frontend↔backend, backend↔Redis, backend↔PostgreSQL) is unencrypted in transit within the Docker network.
- **❌ Ports 6379 (Redis) and 5432 (PostgreSQL) are exposed to the host** — these should not be publicly accessible in any deployed environment.
- **❌ `_charts_sent` deduplication state is in-process memory** — shared state across concurrent requests in a multi-worker deployment would cause race conditions.
- **❌ `GH_TOKEN` scope is unknown** — [TODO: verify that the GitHub token used in CI/CD has the minimum required scopes (repo read + write to `ai-delivery-outputs` only) and is not an org-level admin token].
- **⚠️ LLM prompt injection risk**: user messages are passed directly to LLM without sanitisation. A malicious user could attempt to override the system prompt.
- **⚠️ No input validation** beyond Pydantic model type checking on the `/chat` endpoint — `session_id`, `message`, and `mode` are not sanitised.
- **⚠️ `model_card.json` and `customer_similarity_dict.json` are committed to the repository** — these may contain references to real customer IDs (`CUST00000001` etc.); confirm these are synthetic test data only.

---

## 5. Environment Variables and Secrets

| Name | Required | Sensitivity | Where Set |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | 🔴 High — billable API key | `.env` file (backend); GitHub Actions secret |
| `GOOGLE_API_KEY` | Yes (if using Gemini) | 🔴 High — billable API key | `.env` file (backend) |
| `REDIS_HOST` | No (defaults to `localhost`) | 🟡 Medium | `docker-compose.yml` environment; `.env` |
| `DATABASE_URL` | Yes (frontend) | 🔴 High — contains DB credentials | `docker-compose.yml` environment |
| `BACKEND_URL` | Yes (frontend) | 🟢 Low | `docker-compose.yml` environment |
| `POSTGRES_USER` | Yes | 🔴 High | `docker-compose.yml` (hardcoded: `chainlit`) |
| `POSTGRES_PASSWORD` | Yes | 🔴 High | `docker-compose.yml` (hardcoded: `chainlit`) |
| `POSTGRES_DB` | Yes | 🟢 Low | `docker-compose.yml` (hardcoded: `chainlit`) |
| `GH_TOKEN` | Yes (CI/CD) | 🔴 High — GitHub API access | GitHub Actions secret |
| `SENDGRID_API_KEY` | Yes (CI/CD) | 🔴 High — email service key | GitHub Actions secret |
| `OUTPUT_REPO` | No (default: `ai-delivery-outputs`) | 🟢 Low | GitHub Actions workflow env |
| `OUTPUT_REPO_OWNER` | No (default: repo owner) | 🟢 Low | GitHub Actions workflow env |
| `NOTIFY_EMAIL` | No (default: `kylo.deng@capco.com`) | 🟡 Medium — PII | GitHub Actions workflow env (hardcoded) |
| `SENDER_EMAIL` | No (default: `noreply@ai-delivery.capco.com`) | 🟢 Low | GitHub Actions workflow env (hardcoded) |
| `REVIEW_MODE` | No (CI/CD runtime) | 🟢 Low | Set dynamically in workflow step |
| `PR_NUMBER` | No (CI/CD runtime) | 🟢 Low | Set dynamically in workflow step |
| `RELEASE_VERSION` | No (CI/CD runtime) | 🟢 Low | Set dynamically in workflow step |
| `PROJECT_NAME` | No (CI/CD runtime) | 🟢 Low | Set dynamically in workflow step |
| `TEST_MODE` | No (default: `generate`) | 🟢 Low | GitHub Actions workflow env |
| `UAT_MODE` | No (CI/CD runtime) | 🟢 Low | Set dynamically in workflow step |

> **⚠️ Note:** `NOTIFY_EMAIL` is hardcoded to a personal email address (`kylo.deng@capco.com`) in all five workflow YAML files — this should be parameterised or moved to a repository variable.

---

## 6. Dependencies

### External Services and APIs

| Dependency | Type | Usage | Notes |
|---|---|---|---|
| Anthropic Claude API | External LLM API | Core underwriting assessment (specialist + aggregator); CI/CD code review, docs, tests | Models: `claude-sonnet-4-20250514`, `claude-haiku-4-5-20251001`, `claude-sonnet-4-6` (CI/CD scripts use a different model name from config — potential inconsistency) |
| Google Gemini API | External LLM API | Alternative LLM provider | Model `gemini-3-flash-preview` configured but not default; [TODO: verify this model name is valid — Gemini naming convention suggests it may be incorrect] |
| SendGrid API | External email service | CI/CD notification delivery | Used in all 5 GitHub Actions tools |
| GitHub API (`api.github.com`) | External REST API | PR diffs, file fetching, output commits, PR comments | Used by all CI/CD scripts in `shared.py` |
| `ai-delivery-outputs` (GitHub repo) | External GitHub repository | Storage for AI-generated docs, tests, UAT packs, code reviews | Must exist and be writable by `GH_TOKEN` |

### Python Package Dependencies

| Package | Used In | Purpose |
|---|---|---|
| `anthropic` | CI/CD scripts | Direct Anthropic API client |
| `langchain-anthropic` | Backend | LangChain wrapper for Claude |
| `langchain-google-genai` | Backend | LangChain wrapper for Gemini |
| `langgraph` | Backend | Agent graph orchestration |
| `langchain-core` | Backend | LangChain base primitives |
| `fastapi` | Backend | REST API framework |
| `sse-starlette` | Backend | Server-Sent Events streaming |
| `pydantic` | Backend | Data validation and structured output |
| `redis` (asyncio) | Backend | Redis async client for checkpointing |
| `python-dotenv` | Backend | `.env` file loading |
| `pyyaml` | Backend | `config.yml` parsing |
| `requests` | CI/CD scripts | GitHub and SendGrid HTTP calls |
| `catboost` | [TODO: verify] | ML model inference (model card references CatBoostClassifier but no inference code found in provided files) |

### Other Repositories

- **`kylodeng/ai-delivery-outputs`** (inferred): Output repository for CI/CD tool artefacts — must exist prior to first workflow run.

---

## 7. Deployment Instructions

### Prerequisites

- Docker and Docker Compose v2+ installed
- An `.env` file at the repository root containing at minimum:

```bash
ANTHROPIC_API_KEY=sk-ant-...
GOOGLE_API_KEY=AIza...        # required if using Gemini model
```

### Local Development Deployment

```bash
# 1. Clone the repository
git clone https://github.com/kylodeng/underwriting_chatbot-main.git
cd underwriting_chatbot-main

# 2. Create the .env file
cat > .env <<EOF
ANTHROPIC_API_KEY=sk-ant-your-key-here
GOOGLE_API_KEY=your-google-key-here
EOF

# 3. Build and start all services
docker compose up --build

# 4. Verify backend health
curl http://localhost:8000/health
# Expected: {"status": "ok"}

# 5. Access the frontend
open http://localhost:8080
```

### Service Ports

| Service | Host Port | Container Port |
|---|---|---|
| Backend (FastAPI) | 8000 | 8000 |
| Frontend (Chainlit) | 8080 | 8080 |
| Redis | 6379 | 6379 |
| PostgreSQL | 5432 | 5432 |

### Stopping Services

```bash
docker compose down

# To also remove persistent volumes (destructive — deletes PostgreSQL data)
docker compose down -v
```

### CI/CD Workflow Triggers

```bash
# Trigger code review manually via GitHub CLI
gh workflow run tool1_code_review.yml \
  -f review_mode=repo

# Trigger business documentation for a release
gh workflow run tool3_business_docs.yml \
  -f project_name="Underwriting Chatbot" \
  -f release_version="1.0.0"

# Or push a version tag to auto-trigger business docs
git tag v1.0.0 && git push origin v1.0.0

# Trigger UAT pack generation
gh workflow run tool5_uat.yml \
  -f uat_mode=generate \
  -f release_version="1.0.0"
```

### Required GitHub Secrets (for CI/CD)

```
ANTHROPIC_API_KEY   — Anthropic API key
GH_TOKEN            — GitHub PAT with repo read + write to ai-delivery-outputs
SENDGRID_API_KEY    — SendGrid API key