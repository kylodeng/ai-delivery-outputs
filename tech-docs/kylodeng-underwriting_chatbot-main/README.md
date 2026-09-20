# Underwriting Chatbot

## 1. Project Overview

An AI-powered underwriting assistant that helps insurance underwriters assess customer risk profiles through a conversational chat interface. The system uses large language models (Anthropic Claude and Google Gemini) to run multi-specialist underwriting assessments across finance, health, and life insurance dimensions, then aggregates findings into a structured risk classification. A suite of GitHub Actions workflows provides automated code review, documentation generation, test generation, and UAT facilitation on top of the application.

---

## 2. Tech Stack

| Component | Technology | Version/Notes |
|---|---|---|
| Backend API | FastAPI | Python, SSE streaming via `sse-starlette` |
| Agent Framework | LangGraph + LangChain | Multi-step tool-calling agent with state graph |
| Primary LLM | Anthropic Claude Haiku | `claude-haiku-4-5-20251001` (fast/default) |
| Secondary LLM | Anthropic Claude Sonnet | `claude-sonnet-4-20250514` (deep mode) |
| Tertiary LLM | Google Gemini | `gemini-3-flash-preview` (optional provider) |
| Frontend | [TODO: What framework/technology is the frontend built with?] | Served on port 8080 |
| Agent Memory / Checkpointing | Redis | `redis/redis-stack-server:7.2.0-v14` |
| Chat History DB | PostgreSQL | `postgres:16-alpine`, via Chainlit schema |
| Customer Data | SQLite (`.db` files) | `customer_profile.db`, `feature_importance.db`, `model_predictions.db`, `application_profile.db` |
| Risk Model | CatBoostClassifier | v1.0, trained on merged customer/KYC/application datasets |
| CI/CD & AI Tooling | GitHub Actions | 5 automated workflows (code review, docs, testing, UAT) |
| AI Workflow LLM | Anthropic Claude Sonnet | `claude-sonnet-4-6` (used by GitHub Actions scripts) |
| Email Notifications | SendGrid | Via `SENDGRID_API_KEY` |
| Containerisation | Docker / Docker Compose | All services defined in `docker-compose.yml` |

---

## 3. Architecture

The system is composed of four Docker services that interact as follows:

1. **Frontend** (port 8080) — serves the chat UI. It communicates with the **Backend** over HTTP via `BACKEND_URL`. [TODO: Confirm whether the frontend is Chainlit, a custom React app, or another framework.]

2. **Backend** (port 8000) — a FastAPI application that exposes a `/chat` endpoint (Server-Sent Events stream) and a `/health` endpoint. On each chat request it:
   - Instantiates a LangGraph agent (`build_agent`) configured with the requested model and mode.
   - The agent uses three tools: `get_customer_profile`, `customer_lookalike`, and `run_underwriting_assessment`.
   - `run_underwriting_assessment` fans out async calls to specialist LLM agents (finance, health, life, etc.) in parallel (semaphore-limited to 4), then aggregates results via a structured-output LLM call into an `UnderwritingReport` Pydantic model.
   - Agent conversation state is checkpointed to **Redis** using `AsyncRedisSaver`, keyed by `session_id`.

3. **Redis** (port 6379) — `redis-stack-server` used exclusively as the LangGraph conversation checkpoint store.

4. **PostgreSQL** (port 5432) — stores Chainlit chat history. Initialised by `postgres/init.sql`. [TODO: Confirm whether Chainlit is the frontend or a middleware component.]

Customer data is served from read-only mounted SQLite database files (`./database/*.db`) into the backend container. A pre-computed customer similarity dictionary (`backend/tmp/customer_similarity_dict.json`) is used by the `customer_lookalike` tool.

The `.github/scripts/` directory contains five standalone Python scripts, each triggered by a corresponding GitHub Actions workflow, that use the Anthropic API independently of the application to perform code review, documentation generation, business documentation, test generation, and UAT facilitation.

---

## 4. Local Development Setup

### Prerequisites

- Docker and Docker Compose installed
- An `.env` file at the repository root (see [Environment Variables](#5-environment-variables))

### Steps

1. **Clone the repository**

```bash
git clone https://github.com/kylodeng/underwriting_chatbot-main.git
cd underwriting_chatbot-main
```

2. **Create the root `.env` file**

```bash
cp .env.example .env  # if an example exists, otherwise create manually
```

Populate it with at minimum `ANTHROPIC_API_KEY` and `GOOGLE_API_KEY` (see the Environment Variables table below).

3. **Ensure the SQLite database files are present**

```bash
ls ./database/
# Expected: customer_profile.db  feature_importance.db  model_predictions.db  application_profile.db
```

[TODO: How are these database files obtained or generated? Is there a seed script?]

4. **Build and start all services**

```bash
docker compose up --build
```

5. **Verify the backend is healthy**

```bash
curl http://localhost:8000/health
# Expected: {"status": "ok"}
```

6. **Open the frontend**

Navigate to `http://localhost:8080` in your browser.

### Running the backend directly (without Docker)

1. **Install Python dependencies**

```bash
cd backend
pip install -r requirements.txt  # [TODO: confirm requirements file name/location]
```

2. **Ensure Redis is running** (required for agent checkpointing)

```bash
docker compose up redis -d
```

3. **Start the backend**

```bash
cd backend
uvicorn main:app --reload --port 8000
```

---

## 5. Environment Variables

The backend reads from a `.env` file at `backend/.env` (also loaded from the repo root by `docker-compose.yml` via `env_file: .env`).

| Variable | Required | Default | Description |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | — | API key for Anthropic Claude (used by both backend and GitHub Actions scripts) |
| `GOOGLE_API_KEY` | Yes (if using Gemini) | — | API key for Google Generative AI (Gemini models) |
| `REDIS_HOST` | No | `localhost` | Hostname of the Redis server; set to `redis` when running via Docker Compose |
| `GH_TOKEN` | Yes (CI only) | — | GitHub Personal Access Token used by GitHub Actions scripts to read repos and post PR comments |
| `SENDGRID_API_KEY` | Yes (CI only) | — | SendGrid API key for email notifications from GitHub Actions workflows |
| `OUTPUT_REPO` | No (CI only) | `ai-delivery-outputs` | GitHub repo name where CI workflow outputs (docs, reports) are written |
| `OUTPUT_REPO_OWNER` | No (CI only) | `GITHUB_REPOSITORY_OWNER` | GitHub org/user that owns `OUTPUT_REPO` |
| `NOTIFY_EMAIL` | No (CI only) | `kylo.deng@capco.com` | Recipient email for CI workflow notifications |
| `SENDER_EMAIL` | No (CI only) | `kylo.deng@capco.com` | Sender email for CI workflow notifications |

[TODO: Are there any additional environment variables required by the frontend service (beyond `BACKEND_URL` and `DATABASE_URL`)?]

---

## 6. Running Tests

[TODO: Are there existing test files in the repository? No test files were present in the provided source listing. The GitHub Actions Tool 4 workflow (`tool4_auto_testing.yml`) generates tests automatically via Claude, but no pre-existing test suite was found.]

The CI-generated tests are written to the `ai-delivery-outputs` repository and are not executed automatically within the application's own CI pipeline.

To trigger AI-generated test generation manually:

1. Go to **Actions** → **Tool 4 — Auto Testing** in the GitHub UI.
2. Select **Run workflow** and choose mode `generate` or `gap-analysis`.

---

## 7. Deployment

### Local / Development

Use Docker Compose as described in [Local Development Setup](#4-local-development-setup):

```bash
docker compose up --build -d
```

To stop all services:

```bash
docker compose down
```

To stop and remove volumes (wipes PostgreSQL data):

```bash
docker compose down -v
```

### Production

[TODO: Is there any IaC (Terraform, Bicep, etc.) for cloud deployment? No IaC files were found in the provided source listing.]

[TODO: What is the target cloud platform (Azure, AWS, GCP)?]

[TODO: Is there a container registry push step or a Kubernetes/App Service deployment pipeline?]

**Note from code comments:** Redis is currently running as a local Docker container. The `graph.py` file contains an explicit TODO to migrate Redis to an external managed service (e.g. Azure Cache for Redis) so that agent conversation memory persists across serverless backend instances.

---

## 8. Known Issues / TODOs

The following are extracted directly from code comments and configuration files:

| Location | Issue / TODO |
|---|---|
| `backend/agent/graph.py` | **TODO:** Migrate Redis to an external service (e.g. Azure Cache for Redis, dedicated Redis container) so that memory persists across serverless backend instances. |
| `backend/modules/LLMS.py` | **TODO:** Add more LLM providers. The `azure` and `openai` entries in the model mapper are defined but set to `None` (not implemented). |
| `backend/main.py` | Commented-out `lifespan` context manager — application lifespan hooks are not currently wired up. |
| `backend/config.yml` | `specialist_max_tokens: 1500` — explicitly capped because specialist agents were previously producing runaway verbose output (observed at 2772 tokens). |
| GitHub Actions scripts | `send_email`, `email_html`, and `write_audit_entry` are imported in all five tool scripts but their implementations are truncated in the provided `shared.py` — it is unclear whether these functions are fully implemented. |
| `backend/agent/agent_with_skills.py` | Two agent implementations exist (`agent_with_skills.py` and `graph.py`/`build_agent`). `main.py` uses `build_agent` from `graph.py`; the relationship and intended use of `agent_with_skills.py` is unclear. |
| Assessment modes | Two assessment modes exist (`fast` and `deep`) with separate prompt criteria in `assessment_criterias.json`, but only `fast` mode is used as the default in `config.yml` and tool definitions. |