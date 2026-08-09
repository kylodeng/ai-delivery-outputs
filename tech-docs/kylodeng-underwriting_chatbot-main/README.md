# Underwriting Chatbot

## 1. Project Overview

An AI-powered underwriting assistant that helps insurance underwriters assess customer risk profiles through a conversational chat interface. The backend orchestrates multiple specialist LLM agents (covering finance, health, life, and other risk domains) that run in parallel and are aggregated into a structured `UnderwritingReport`. A CI/CD pipeline of five GitHub Actions workflows provides automated code review, documentation generation, test generation, and UAT facilitation as auxiliary tooling for the repository.

---

## 2. Tech Stack

| Component | Technology | Version/Notes |
|---|---|---|
| Backend framework | FastAPI | with SSE streaming via `sse-starlette` |
| Agent orchestration | LangGraph | `StateGraph` with tool-call routing |
| LLM provider (primary) | Anthropic Claude | `claude-haiku-4-5-20251001` (fast), `claude-sonnet-4-20250514` (deep) |
| LLM provider (secondary) | Google Gemini | `gemini-3-flash-preview` |
| LLM abstraction | LangChain | `langchain-anthropic`, `langchain-google-genai` |
| Memory / checkpointing | Redis | `redis/redis-stack-server:7.2.0-v14` via `langgraph.checkpoint.redis` |
| Frontend | [TODO: what framework/technology is the frontend built with?] | Port 8080 |
| Database (customer data) | SQLite | `.db` files mounted read-only into backend container |
| Database (chat persistence) | PostgreSQL | `postgres:16-alpine`, used by frontend/Chainlit |
| Containerisation | Docker Compose | Three services: redis, postgres, backend, frontend |
| CI/CD AI tooling | GitHub Actions + Anthropic Claude | `claude-sonnet-4-6` via `anthropic` Python SDK |
| CI email notifications | SendGrid | Via `SENDGRID_API_KEY` |
| Risk model metadata | CatBoostClassifier | `model_card.json`, version 1.0, trained 2024-06-01 |
| Config | YAML | `backend/config.yml` |
| Python version | Python | 3.12 (CI), [TODO: confirm local dev Python version requirement] |

---

## 3. Architecture

The system consists of four Docker services that communicate over an internal Docker network:

1. **Frontend** (port 8080) — serves the chat UI. It connects to the **Backend** via `BACKEND_URL` and persists chat sessions to **PostgreSQL** via `DATABASE_URL`. [TODO: confirm whether the frontend is Chainlit or another framework — the PostgreSQL database is named `chainlit` and the init SQL is present, but the frontend build context is not shown.]

2. **Backend** (port 8000) — a FastAPI application that exposes a `/chat` SSE endpoint and a `/health` endpoint. On each chat request it:
   - Builds a LangGraph agent (`build_agent`) configured with three tools: `get_customer_profile`, `run_underwriting_assessment`, and `customer_lookalike`.
   - Streams events back to the frontend using Server-Sent Events.
   - The `run_underwriting_assessment` tool fans out to multiple specialist LLM calls (one per `ASSESSMENT_CATEGORIES` domain, capped at 4 concurrent via `asyncio.Semaphore`), then aggregates results with a structured-output LLM call to produce an `UnderwritingReport` Pydantic model.
   - Agent conversation state (checkpoint) is persisted in **Redis** via `AsyncRedisSaver`.

3. **Redis** (port 6379) — provides LangGraph checkpoint storage so conversation memory survives within a session. The code notes that memory does **not** persist across serverless/ephemeral backend instances (see Known Issues).

4. **PostgreSQL** (port 5432) — stores frontend session/chat data. Initialised via `postgres/init.sql` on first start.

Customer profile data is stored in SQLite `.db` files on the host, mounted read-only into the backend container. A pre-computed customer similarity dictionary (`backend/tmp/customer_similarity_dict.json`) is used by the `customer_lookalike` tool.

**GitHub Actions CI tools** (`tool1`–`tool5`) run independently in GitHub-hosted runners. They call the Anthropic API directly (using `claude-sonnet-4-6`) and write outputs (reports, docs, test files) to a separate `ai-delivery-outputs` repository via the GitHub API.

---

## 4. Local Development Setup

```bash
# 1. Clone the repository
git clone https://github.com/kylodeng/underwriting_chatbot-main.git
cd underwriting_chatbot-main
```

```bash
# 2. Create a root-level .env file with required secrets (see Environment Variables section)
cp .env.example .env   # if an example file exists, otherwise create manually
# Edit .env and fill in all required values
```

```bash
# 3. Ensure the SQLite database files are present in ./database/
#    (customer_profile.db, feature_importance.db, model_predictions.db, application_profile.db)
# [TODO: how are these database files obtained or generated?]
```

```bash
# 4. Build and start all services
docker compose up --build
```

```bash
# 5. Verify the backend is healthy
curl http://localhost:8000/health
# Expected: {"status": "ok"}
```

```bash
# 6. Open the frontend
open http://localhost:8080
```

**Backend-only development (without Docker):**

```bash
# 7. Create a Python virtual environment
cd backend
python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
```

```bash
# 8. Install dependencies
# [TODO: a requirements.txt or pyproject.toml was not present in the provided files — confirm dependency file name]
pip install fastapi sse-starlette langchain langchain-anthropic langchain-google-genai \
    langgraph redis pydantic pyyaml python-dotenv
```

```bash
# 9. Ensure Redis is running (required for agent checkpointing)
docker compose up redis -d
```

```bash
# 10. Start the backend
cd backend
uvicorn main:app --reload --port 8000
```

---

## 5. Environment Variables

The backend reads from a `.env` file located at the project root (loaded by `python-dotenv`).

| Variable | Required | Default | Description |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | — | Anthropic API key for Claude LLM calls in the backend agent and assessment modules |
| `GOOGLE_API_KEY` | Yes (if using Gemini) | — | Google API key for Gemini model provider |
| `REDIS_HOST` | No | `localhost` | Hostname of the Redis server (set to `redis` inside Docker Compose) |
| `ANTHROPIC_API_KEY` | Yes (CI) | — | Anthropic API key used by GitHub Actions workflow scripts (same key, set as a repo secret) |
| `GH_TOKEN` | Yes (CI) | — | GitHub personal access token used by CI scripts to read source repos and write to `ai-delivery-outputs` repo |
| `SENDGRID_API_KEY` | Yes (CI) | — | SendGrid API key for email notifications from CI workflows |
| `OUTPUT_REPO` | No (CI) | `ai-delivery-outputs` | Name of the GitHub repo where CI tool outputs are written |
| `OUTPUT_REPO_OWNER` | No (CI) | `GITHUB_REPOSITORY_OWNER` | GitHub owner/org for the output repo |
| `NOTIFY_EMAIL` | No (CI) | `kylo.deng@capco.com` | Recipient email address for CI workflow notifications |
| `SENDER_EMAIL` | No (CI) | `kylo.deng@capco.com` | Sender email address for CI workflow notifications |

PostgreSQL credentials are hardcoded in `docker-compose.yml` for local development:

| Variable | Value |
|---|---|
| `POSTGRES_USER` | `chainlit` |
| `POSTGRES_PASSWORD` | `chainlit` |
| `POSTGRES_DB` | `chainlit` |

[TODO: are there additional environment variables required by the frontend service (e.g. API keys, feature flags)?]

---

## 6. Running Tests

[TODO: no test files or test runner configuration were present in the provided source files. How are tests run for this project?]

The repository includes a GitHub Actions workflow (`tool4_auto_testing.yml`) that uses Claude to **generate** test files automatically on pull requests and on a weekly schedule (Wednesdays 07:00 UTC). Generated tests are written to the `ai-delivery-outputs` repository, not committed back to this repo.

To trigger test generation manually:

1. Go to **Actions → Tool 4 — Auto Testing** in the GitHub UI.
2. Select **Run workflow**.
3. Choose mode: `generate` (new test files) or `gap-analysis` (coverage gap report).

---

## 7. Deployment

### Local / Development

```bash
# Start all services
docker compose up --build -d

# View logs
docker compose logs -f backend

# Stop all services
docker compose down

# Stop and remove volumes (resets PostgreSQL data)
docker compose down -v
```

### GitHub Actions CI Workflows

Five automated workflows run against this repository. They are configured via secrets set in **Repository Settings → Secrets and variables → Actions**. Required secrets:

| Secret | Used by |
|---|---|
| `ANTHROPIC_API_KEY` | All five tools |
| `GH_TOKEN` | All five tools |
| `SENDGRID_API_KEY` | All five tools |

| Workflow | Trigger | Purpose |
|---|---|---|
| Tool 1 — Code Review | PR open/sync, Monday 08:00 UTC, manual | Claude reviews PR diffs and posts comments |
| Tool 2 — Tech Docs | Push to `main`, Sunday 06:00 UTC, manual | Generates README, ARCHITECTURE, RUNBOOK to output repo |
| Tool 3 — Business Docs | Version tag push (`v*`), manual | Generates solution overview and gap questionnaire |
| Tool 4 — Auto Testing | PR open/sync on `src/**`, Wednesday 07:00 UTC, manual | Generates or gap-analyses test files |
| Tool 5 — UAT | `release/*` branch creation, manual | Generates UAT test pack or analyses completed UAT results CSV |

### Cloud / Production Deployment

[TODO: no IaC files (Terraform, Bicep, ARM, CloudFormation) were present in the provided files. How is this application deployed to a cloud environment?]

---

## 8. Known Issues / TODOs

Extracted from code comments:

| Location | Issue / TODO |
|---|---|
| `backend/agent/graph.py` | **TODO:** Migrate Redis to an external service (e.g. Azure Cache for Redis, dedicated Redis container) so that memory persists across serverless backend instances. Currently, agent memory is lost if the backend container restarts. |
| `backend/modules/LLMS.py` | **TODO:** Add more LLM providers. The `azure` and `openai` entries in `model_mapper` are present but set to `None` and will raise a `ValueError` if selected. |
| `backend/main.py` | The `lifespan` context manager is commented out — application startup/shutdown lifecycle hooks are not active. |
| `backend/agent/agent_with_skills.py` | `agent_with_skills.py` defines a second agent implementation (`build_skills_agent`) using a custom JSON tool-call loop. It is unclear whether this or `graph.py`'s `build_agent` is the one used in production — `main.py` imports from `agent.graph`. [TODO: is `agent_with_skills.py` the intended agent implementation, or is it a work-in-progress replacement?] |
| `.github/scripts/shared.py` | `send_email`, `email_html`, and `write_audit_entry` functions are imported by all tool scripts but their implementations are truncated in the provided files. [TODO: confirm these functions are fully implemented in `shared.py`.] |
| `backend/tmp/customer_similarity_dict.json` | Customer similarity data is stored as a static JSON file in `backend/tmp/`. [TODO: how is this file generated/updated, and should it be committed to the repository?] |
| `backend/prompts/assessment_criterias.json` | Assessment criteria prompts exist for `deep` mode. `fast` mode is referenced in `config.yml` and `agent_with_skills.py` but [TODO: confirm `fast` mode criteria are defined in `assessment_criterias.json`]. |
| `.github/scripts/tool2_tech_docs.py` | Script is truncated in the file (`{r` at end of `build_index` function). |
| `.github/scripts/tool4_auto_testing.py` | Script is truncated. |
| `.github/scripts/tool5_uat.py` | Script is truncated. |
| General | No `requirements.txt`, `pyproject.toml`, or `Dockerfile` contents were provided for the backend. [TODO: confirm the Python dependency manifest file name and location.] |