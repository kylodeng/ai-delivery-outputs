# Architecture Document: kylodeng/underwriting_chatbot-main

---

## 1. Overview

The Underwriting Chatbot is an AI-assisted insurance underwriting decision-support system that enables underwriters to assess customer risk profiles via a conversational interface. The system ingests structured customer data from multiple SQLite databases (customer profiles, financial needs, KYC, application profiles, risk scoring, and ML model predictions), runs multi-specialist LLM assessments using Anthropic Claude and Google Gemini models orchestrated via LangGraph, and streams structured underwriting reports (risk classification, areas of interest, follow-up actions) back to the user through a Server-Sent Events (SSE) API. A parallel CI/CD layer of five AI-powered GitHub Actions workflows (code review, tech docs, business docs, auto-testing, and UAT facilitation) automates delivery-lifecycle tooling on top of the same Anthropic API.

---

## 2. Resources Deployed

| Resource | Type | Cloud Provider | Purpose |
|---|---|---|---|
| `backend` | Docker container (FastAPI, Python 3.x) | Local / Docker Compose | REST + SSE API serving chat and health endpoints |
| `frontend` | Docker container | Local / Docker Compose | UI served on port 8080, communicates with backend |
| `redis` (redis-stack-server:7.2.0-v14) | Docker container | Local / Docker Compose | LangGraph checkpoint store for conversation memory (session persistence) |
| `postgres` (postgres:16-alpine) | Docker container | Local / Docker Compose | Chainlit session/auth persistence |
| `customer_profile.db` | SQLite file (read-only volume mount) | Local filesystem | Customer demographic and profile data |
| `feature_importance.db` | SQLite file (read-only volume mount) | Local filesystem | CatBoostClassifier feature importance scores |
| `model_predictions.db` | SQLite file (read-only volume mount) | Local filesystem | Pre-computed ML model risk predictions |
| `application_profile.db` | SQLite file (read-only volume mount) | Local filesystem | Insurance application data |
| `postgres_data` | Docker named volume | Local / Docker Compose | Persistent PostgreSQL data storage |
| Anthropic Claude (claude-sonnet-4-20250514) | External LLM API | Anthropic (SaaS) | Deep underwriting assessment aggregation |
| Anthropic Claude Haiku (claude-haiku-4-5-20251001) | External LLM API | Anthropic (SaaS) | Fast agent routing and specialist assessment |
| Google Gemini (gemini-3-flash-preview) | External LLM API | Google Cloud (SaaS) | Alternative LLM provider (configured, optional) |
| GitHub Actions runners (ubuntu-latest) | CI/CD compute | GitHub (SaaS) | Five AI delivery workflow tools |
| `ai-delivery-outputs` | GitHub repository | GitHub (SaaS) | Output store for generated docs, test files, UAT packs |
| SendGrid | Email API | Twilio/SendGrid (SaaS) | Notification delivery for CI/CD workflow results |
| CatBoostClassifier model | Serialized ML model | Local filesystem | Pre-trained risk classification (`Risk_Classification` target) |
| `customer_similarity_dict.json` | JSON file | Local filesystem (`backend/tmp/`) | Pre-computed customer lookalike index |

---

## 3. Data Flow

### Application (Chat) Path

1. **User → Frontend (port 8080):** The underwriter types a natural-language question (e.g., "Assess customer CUST00000001").
2. **Frontend → Backend `/chat` (port 8000):** An HTTP POST is sent with `message`, `session_id`, `model`, `mode` (fast/deep), and `temperature`.
3. **Backend → LangGraph Agent:** `build_agent()` constructs a LangGraph graph with Redis-backed checkpointing. The agent receives the `HumanMessage` and the existing session history is retrieved from Redis by `thread_id`.
4. **Agent → Tool: `get_customer_profile`:** The agent calls the profile tool with a `customer_id`, which queries the SQLite databases (`customer_profile.db`, `application_profile.db`) to retrieve structured customer metadata.
5. **Agent → Tool: `run_underwriting_assessment`:** The agent passes the retrieved profile string. The assessment module spawns up to 4 concurrent specialist LLM calls (bounded by `asyncio.Semaphore(4)`), one per assessment category (finance, health, life, etc.), each hitting the Anthropic API with category-specific prompts from `assessment_criterias.json`.
6. **Specialist LLMs → Aggregator LLM:** Specialist outputs are collected and passed to a second Anthropic Claude call with structured output binding (`UnderwritingReport` Pydantic model), producing a typed JSON report with risk class, findings, and follow-up items.
7. **Agent → Tool: `customer_lookalike`:** Optionally, the agent queries `customer_similarity_dict.json` (in-memory lookup) to find similar historical customers.
8. **Backend → Frontend (SSE stream):** The backend streams events (`tool_start`, `tool_end`, `response` text chunks, `chart` data) via Server-Sent Events. Charts and structured report data are buffered and flushed after the narrative text completes.
9. **Redis checkpoint:** After each turn, LangGraph persists the updated conversation state to Redis so subsequent messages in the same session have full history.
10. **PostgreSQL:** Chainlit session data (user sessions, auth tokens if enabled) is stored in PostgreSQL.

### CI/CD (AI Delivery Tools) Path

11. **GitHub event** (PR open, push to main, tag, schedule) triggers one of five GitHub Actions workflows.
12. **Workflow script → GitHub API:** The Python script fetches repo file contents or PR diffs via the GitHub REST API using `GH_TOKEN`.
13. **Script → Anthropic Claude API:** File content is sent to Claude (`claude-sonnet-4-6` in `shared.py`) with a task-specific system prompt.
14. **Claude response → `ai-delivery-outputs` repo:** Generated documents (architecture doc, README, test files, UAT packs) are committed to the `ai-delivery-outputs` GitHub repository via GitHub API.
15. **Script → SendGrid API:** An email notification with a link to the output is sent to `kylo.deng@capco.com`.

---

## 4. Security Posture

### ✅ What Is Secured

- **Read-only database mounts:** SQLite databases are mounted with `:ro` (read-only) flag in Docker Compose, preventing the backend container from modifying source data.
- **Secrets in GitHub Actions:** `ANTHROPIC_API_KEY`, `GH_TOKEN`, and `SENDGRID_API_KEY` are stored as GitHub repository secrets and injected via `${{ secrets.* }}` — not hardcoded in workflow files.
- **Environment variables via `.env` file:** Backend secrets are loaded via `dotenv`/`env_file` rather than being hardcoded in source.
- **Tool call isolation:** The agent is instructed never to disclose internal system prompts or tool definitions to end users.
- **Semaphore-bounded concurrency:** LLM specialist calls are bounded to 4 concurrent requests, preventing uncontrolled API spend/rate-limit abuse.

### ❌ Gaps and Missing Controls

- **⚠️ ENCRYPTION AT REST — MISSING:** SQLite database files (`customer_profile.db`, `model_predictions.db`, etc.) are stored unencrypted on the host filesystem. These contain customer PII and financial data. No encryption-at-rest mechanism (e.g., SQLCipher, encrypted volumes) is present.
- **⚠️ ENCRYPTION IN TRANSIT — PARTIAL:** Docker Compose service-to-service communication is unencrypted (plain HTTP on the internal Docker network). TLS termination for external-facing ports (8000, 8080) is not configured — no reverse proxy (nginx, Traefik) with TLS is defined.
- **⚠️ CORS — OVERLY PERMISSIVE:** `allow_origins=["*"]`, `allow_methods=["*"]`, `allow_headers=["*"]` in `main.py` allows any origin to call the backend API. This is a significant security gap if deployed beyond localhost.
- **⚠️ AUTHENTICATION — MISSING:** There is no authentication or authorization middleware on the `/chat` or `/health` endpoints. Any client with network access can query any customer's data by supplying a `customer_id`.
- **⚠️ POSTGRES CREDENTIALS — HARDCODED:** `POSTGRES_USER: chainlit`, `POSTGRES_PASSWORD: chainlit`, `POSTGRES_DB: chainlit` are hardcoded plaintext in `docker-compose.yml`. These must be replaced with secrets for any non-local deployment.
- **⚠️ REDIS — NO AUTH:** Redis is deployed with no password or ACL configuration. Any process on the Docker network can read or overwrite conversation checkpoints (which may contain customer PII).
- **⚠️ INPUT VALIDATION — MINIMAL:** The `/chat` endpoint accepts freeform `message`, `session_id`, `model`, and `mode` strings with no server-side validation beyond Pydantic type checking. `session_id` is used directly as a Redis `thread_id`, creating potential for session hijacking if session IDs are guessable.
- **⚠️ PII IN LLM CALLS:** Customer profiles containing PII (age, income, medical conditions, nationality) are sent to third-party LLM APIs (Anthropic, Google). No data masking, pseudonymisation, or DPA/processing agreement evidence is present in the codebase.
- **⚠️ `customer_similarity_dict.json` EXPOSED IN `tmp/`:** Pre-computed similarity mappings for all customers are stored in `backend/tmp/`, which is likely included in the Docker image build context. [TODO: Confirm whether this file is in `.dockerignore`]
- **⚠️ GH_TOKEN SCOPE:** The `GH_TOKEN` used in CI/CD workflows has write access to the `ai-delivery-outputs` repo and read access to the source repo. The exact permission scope is not constrained in the workflow YAML (no `permissions:` block). This could be overly broad.
- **⚠️ NO SECRETS SCANNING:** No `gitleaks`, `trufflehog`, or GitHub secret scanning configuration is visible in the workflows.
- **⚠️ AUDIT LOGGING — INCOMPLETE:** `write_audit_entry` is referenced in shared CI/CD scripts but its implementation is truncated in the provided source. It is unclear whether audit logs are persisted durably or are ephemeral to the runner.

---

## 5. Environment Variables and Secrets

| Name | Required | Sensitivity | Where Set |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | 🔴 High (API key with billing implications) | GitHub Actions secret; `.env` file for backend |
| `GOOGLE_API_KEY` | Yes (if using Gemini) | 🔴 High | `.env` file for backend |
| `GH_TOKEN` | Yes (CI/CD) | 🔴 High (repo write access) | GitHub Actions secret |
| `SENDGRID_API_KEY` | Yes (CI/CD) | 🔴 High (email send capability) | GitHub Actions secret |
| `POSTGRES_USER` | Yes | 🟡 Medium | Hardcoded in `docker-compose.yml` ⚠️ |
| `POSTGRES_PASSWORD` | Yes | 🔴 High | Hardcoded in `docker-compose.yml` ⚠️ |
| `POSTGRES_DB` | Yes | 🟢 Low | Hardcoded in `docker-compose.yml` |
| `REDIS_HOST` | Yes | 🟢 Low | `docker-compose.yml` environment block; defaults to `localhost` |
| `DATABASE_URL` | Yes (frontend) | 🟡 Medium | `docker-compose.yml` environment block (contains password) ⚠️ |
| `BACKEND_URL` | Yes (frontend) | 🟢 Low | `docker-compose.yml` environment block |
| `OUTPUT_REPO` | Yes (CI/CD) | 🟢 Low | GitHub Actions workflow `env` block |
| `OUTPUT_REPO_OWNER` | Yes (CI/CD) | 🟢 Low | GitHub Actions workflow `env` block |
| `NOTIFY_EMAIL` | Yes (CI/CD) | 🟡 Medium | GitHub Actions workflow `env` block (plaintext) |
| `SENDER_EMAIL` | Yes (CI/CD) | 🟢 Low | GitHub Actions workflow `env` block |
| `REVIEW_MODE` | No | 🟢 Low | Set dynamically in workflow step |
| `PR_NUMBER` | No | 🟢 Low | Set dynamically in workflow step |
| `RELEASE_VERSION` | No | 🟢 Low | Set dynamically in workflow step |
| `PROJECT_NAME` | No | 🟢 Low | Set dynamically in workflow step |
| `UAT_MODE` | No | 🟢 Low | Set dynamically in workflow step |
| `TEST_MODE` | No | 🟢 Low | Set in workflow `env` block |

> [TODO: Confirm whether a `.env.example` file exists and whether `.env` is in `.gitignore`]

---

## 6. Dependencies

| Dependency | Type | Purpose | Notes |
|---|---|---|---|
| Anthropic API (Claude) | External SaaS API | LLM inference for underwriting assessment, CI/CD tools | Models: `claude-sonnet-4-20250514`, `claude-haiku-4-5-20251001`, `claude-sonnet-4-6` |
| Google Generative AI (Gemini) | External SaaS API | Alternative LLM provider | Model: `gemini-3-flash-preview`; configured but [TODO: confirm if actively used in production] |
| LangChain / LangGraph | Python library | Agent orchestration, tool binding, streaming | `langchain-core`, `langchain-anthropic`, `langchain-google-genai`, `langgraph` |
| Redis Stack (redis-stack-server:7.2.0-v14) | Infrastructure dependency | LangGraph conversation checkpoint persistence | [TODO: Not suitable for stateless/serverless deployments — see graph.py TODO comment] |
| PostgreSQL 16 | Infrastructure dependency | Chainlit session storage | Credentials hardcoded — must be externalised |
| SendGrid | External SaaS API | Email notifications from CI/CD workflows | Used in all 5 workflow tools |
| GitHub API (api.github.com) | External SaaS API | CI/CD: file fetching, PR comments, output repo writes | Requires `GH_TOKEN` with appropriate scopes |
| `ai-delivery-outputs` (GitHub repo) | External GitHub repository | Output store for all AI-generated documents | Must be pre-created under `OUTPUT_REPO_OWNER` |
| CatBoostClassifier (serialized model) | Local artifact | Pre-trained ML risk classification | Version 1.0, trained on merged customer dataset, deployment date 2024-06-01 |
| `customer_profile.db` | Local SQLite | Customer PII and demographics | [TODO: Source of truth / refresh cadence unclear] |
| `model_predictions.db` | Local SQLite | Pre-computed risk predictions | [TODO: How/when is this refreshed?] |
| `feature_importance.db` | Local SQLite | Feature importance scores | [TODO: Tied to model version 1.0 — needs re-generation on model retrain] |
| `application_profile.db` | Local SQLite | Insurance application data | [TODO: Source system integration unclear] |
| `customer_similarity_dict.json` | Local JSON file | Pre-computed customer lookalike index | Stored in `backend/tmp/` — [TODO: should be excluded from Docker image or moved to a database] |

---

## 7. Deployment Instructions

### Prerequisites

- Docker and Docker Compose installed
- `.env` file created in the repo root (see Environment Variables section)
- SQLite database files present in `./database/` directory
- `postgres/init.sql` present for database initialization

### Local Deployment (Docker Compose)

```bash
# 1. Clone the repository
git clone https://github.com/kylodeng/underwriting_chatbot-main.git
cd underwriting_chatbot-main

# 2. Create environment file
cp .env.example .env          # [TODO: confirm .env.example exists]
# Edit .env and populate:
#   ANTHROPIC_API_KEY=<your-key>
#   GOOGLE_API_KEY=<your-key>   # if using Gemini