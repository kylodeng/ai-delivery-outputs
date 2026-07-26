# Underwriting Chatbot

## 1. Project Overview

An AI-powered underwriting assistant that helps insurance underwriters assess customer risk profiles through a conversational chat interface. The system uses a multi-agent LangGraph pipeline to run parallel specialist assessments across finance, health, and life domains, then aggregates them into a structured underwriting report. A suite of five GitHub Actions CI/CD tools provide automated code review, documentation generation, test generation, and UAT facilitation for the repository itself.

---

## 2. Tech Stack

| Component | Technology | Version/Notes |
|---|---|---|
| Backend framework | FastAPI | Python, with SSE streaming |
| Agent orchestration | LangGraph | StateGraph with tool-calling |
| LLM provider (primary) | Anthropic Claude | `claude-haiku-4-5-20251001` (fast), `claude-sonnet-4-20250514` (full) |
| LLM provider (secondary) | Google Gemini | `gemini-3-flash-preview` |
| LLM abstraction | LangChain | `langchain-anthropic`, `langchain-google-genai` |
| Frontend | [TODO: what framework/language is the frontend built in?] | Served on port 8080 |
| Memory / checkpointing | Redis (redis-stack-server) | 7.2.0-v14, via `langgraph.checkpoint.redis` |
| Database | PostgreSQL | 16-alpine; used by frontend (Chainlit session data) |
| Customer data | SQLite (`.db` files) | `customer_profile.db`, `feature_importance.db`, `model_predictions.db`, `application_profile.db` |
| ML model | CatBoostClassifier | v1.0, trained on merged customer dataset |
| CI/CD automation | GitHub Actions | Python 3.12, 5 workflow tools |
| AI workflow scripts | Anthropic Claude (claude-sonnet-4-6) | Used in `.github/scripts/` |
| Email notifications | SendGrid | Via `SENDGRID_API_KEY` |
| Containerisation | Docker Compose | Multi-service stack |

---

## 3. Architecture

The system is composed of four Docker services that communicate over an internal Docker network:

1. **Frontend** (port 8080) — Serves the chat UI. Communicates with the backend over HTTP. Uses PostgreSQL for session/conversation persistence (Chainlit). [TODO: confirm whether the frontend is Chainlit or a custom framework]

2. **Backend** (port 8000) — A FastAPI application that exposes a `/chat` SSE endpoint and a `/health` endpoint. On each chat request it:
   - Builds a LangGraph agent configured with the requested model and mode.
   - Streams events back to the frontend using Server-Sent Events.
   - The agent follows a tool-calling loop: it calls `get_customer_profile` to fetch a customer record from SQLite, then calls `run_underwriting_assessment` which fans out to multiple specialist LLM calls in parallel (capped at 4 concurrent via `asyncio.Semaphore`), and finally calls `customer_lookalike` to find similar customers from a pre-computed similarity dictionary.
   - An aggregator LLM combines specialist outputs into a structured `UnderwritingReport` Pydantic model.

3. **Redis** (port 6379) — Stores LangGraph conversation checkpoints so conversation state persists across turns within a session. [TODO: Redis memory does not persist across serverless backend restarts — see known issues]

4. **PostgreSQL** (port 5432) — Used by the frontend for user/session data.

The `.github/scripts/` tools (Tools 1–5) run in GitHub Actions and interact with the GitHub API and Anthropic API independently of the application runtime.

```
Frontend (8080)
     │  HTTP / SSE
     ▼
Backend FastAPI (8000)
     │
     ├─► LangGraph Agent
     │       ├─► get_customer_profile  ──► SQLite DBs (read-only volume mounts)
     │       ├─► run_underwriting_assessment
     │       │       └─► N specialist LLMs (parallel, Semaphore=4)
     │       │               └─► aggregator LLM → UnderwritingReport
     │       └─► customer_lookalike  ──► customer_similarity_dict.json
     │
     └─► Redis (LangGraph checkpointer)

PostgreSQL ◄── Frontend (session data)
```

---

## 4. Local Development Setup

1. **Clone the repository**

```bash
git clone https://github.com/kylodeng/underwriting_chatbot-main.git
cd underwriting_chatbot-main
```

2. **Create the root `.env` file** (see [Environment Variables](#5-environment-variables) below)

```bash
cp .env.example .env   # if provided, otherwise create manually
```

3. **Ensure the SQLite database files are present** under `./database/`

```
database/
  customer_profile.db
  feature_importance.db
  model_predictions.db
  application_profile.db
```

[TODO: how are these database files generated or obtained? Is there a seed script?]

4. **Start all services with Docker Compose**

```bash
docker compose up --build
```

5. **Verify the backend is healthy**

```bash
curl http://localhost:8000/health
# Expected: {"status": "ok"}
```

6. **Open the frontend**

Navigate to [http://localhost:8080](http://localhost:8080) in your browser.

7. **(Optional) Run the backend locally without Docker** — install Python dependencies and start FastAPI directly

```bash
cd backend
pip install -r requirements.txt   # [TODO: confirm requirements.txt exists]
uvicorn main:app --reload --port 8000
```

---

## 5. Environment Variables

The backend reads from a `.env` file at the repo root (mounted via `env_file: .env` in `docker-compose.yml`). The GitHub Actions workflows read secrets from the repository's GitHub Secrets store.

### Application environment variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | — | API key for Anthropic Claude (used by backend LLMs and GitHub Actions scripts) |
| `GOOGLE_API_KEY` | Yes (if using Gemini) | — | API key for Google Generative AI (Gemini model) |
| `REDIS_HOST` | No | `localhost` | Hostname of the Redis instance; set to `redis` in Docker Compose |

### GitHub Actions environment variables / secrets

| Variable | Required | Default | Description |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | — | Anthropic API key for CI workflow scripts |
| `GH_TOKEN` | Yes | — | GitHub personal access token with repo read/write access |
| `SENDGRID_API_KEY` | Yes | — | SendGrid API key for email notifications |
| `OUTPUT_REPO` | No | `ai-delivery-outputs` | Repository name where generated docs/reports are written |
| `OUTPUT_REPO_OWNER` | No | `github.repository_owner` | Owner of the output repository |
| `NOTIFY_EMAIL` | No | `kylo.deng@capco.com` | Recipient email for workflow notifications |
| `SENDER_EMAIL` | No | `kylo.deng@capco.com` | Sender email address |

### Docker Compose service environment variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `POSTGRES_USER` | Yes | `chainlit` | PostgreSQL username |
| `POSTGRES_PASSWORD` | Yes | `chainlit` | PostgreSQL password |
| `POSTGRES_DB` | Yes | `chainlit` | PostgreSQL database name |
| `BACKEND_URL` | Yes | `http://backend:8000` | URL the frontend uses to reach the backend |
| `DATABASE_URL` | Yes | `postgresql+asyncpg://chainlit:chainlit@postgres:5432/chainlit` | PostgreSQL DSN for the frontend |

---

## 6. Running Tests

[TODO: No test files or test runner configuration were found in the provided source files. Does a `tests/` directory exist? What test framework is used?]

The repository includes a GitHub Actions workflow (Tool 4) that auto-generates test files using Claude and writes them to the `ai-delivery-outputs` repository. To trigger it manually:

1. Go to **Actions → Tool 4 — Auto Testing** in the GitHub UI.
2. Click **Run workflow** and select mode `generate` or `gap-analysis`.

To run any generated tests locally once retrieved:

```bash
# Python (pytest assumed)
pip install pytest
pytest tests/
```

---

## 7. Deployment

### Local / development — Docker Compose

```bash
docker compose up --build -d
```

```bash
# Tear down
docker compose down
```

### Updating a running stack after code changes

```bash
docker compose up --build -d --force-recreate backend
```

### GitHub Actions CI/CD workflows

Five automated tools run on the repository. They can be triggered manually from the GitHub Actions tab or fire automatically on the events listed below:

| Workflow | File | Auto-trigger |
|---|---|---|
| Tool 1 — Code Review | `tool1_code_review.yml` | PR open/sync/reopen; Monday 08:00 UTC cron |
| Tool 2 — Tech Documentation | `tool2_tech_docs.yml` | Push to `main` (non-doc files); Sunday 06:00 UTC cron |
| Tool 3 — Business Documentation | `tool3_business_docs.yml` | Push of a `v*` tag |
| Tool 4 — Auto Testing | `tool4_auto_testing.yml` | PR touching `src/**`, `*.py`, `*.js`, `*.ts`; Wednesday 07:00 UTC cron |
| Tool 5 — UAT Facilitation | `tool5_uat.yml` | Creation of a `release/*` branch |

All workflows require the following GitHub repository secrets to be set:

```
ANTHROPIC_API_KEY
GH_TOKEN
SENDGRID_API_KEY
```

### Infrastructure / IaC

[TODO: No Terraform, Bicep, or other IaC files were found in the provided source files. Is there a cloud deployment configuration?]

---

## 8. Known Issues / TODOs

The following items are extracted directly from code comments:

- **Redis persistence** (`backend/agent/graph.py`): Redis is currently running as a local container. Memory does not persist across serverless backend restarts. The TODO is to migrate to an external managed service (e.g. Azure Cache for Redis or a dedicated Redis container with a persistent volume).

- **Additional LLM providers** (`backend/modules/LLMS.py`): The `azure` and `openai` model providers are listed in the model mapper but are set to `None` and not yet implemented.

- **Agent LLM providers** (`backend/modules/LLMS.py`): Code comment `# TODO: add more providers here`.

- **Lifespan handler** (`backend/main.py`): The FastAPI `lifespan` context manager is commented out. [TODO: what initialisation was intended here?]

- **Tool 1 — Code Review** (`tool1_code_review.py`): The comment body for PR review is truncated in the source — the auto-generated footer string appears to be cut off.

- **Tool 2 — Tech Docs** (`tool2_tech_docs.py`): The `build_index` function's f-string references `{r` which appears to be a truncation — the variable `repo` is likely intended.

- **Tool 3 — Business Docs** (`tool3_business_docs.py`): The gap questionnaire example references a truncated `[View` link — the full URL template appears cut off in the source.

- **Tool 4 — Auto Testing** (`tool4_auto_testing.py`): The `build_test_report` markdown table row separator appears truncated (`|---|-`).

- **Escalation path** (`tool2_tech_docs.py` RUNBOOK system prompt): Runbook template contains `# 6. Escalation path [TODO: fill in team contacts]`.

- **Frontend framework**: [TODO: what framework/language is the frontend built in? No frontend source files were provided.]

- **Database seeding**: [TODO: how are the SQLite `.db` files under `./database/` generated? Is there a data pipeline or seed script?]

- **Requirements file**: [TODO: confirm that `backend/requirements.txt` exists and is up to date.]