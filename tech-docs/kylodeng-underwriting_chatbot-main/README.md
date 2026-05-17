# Underwriting Chatbot

## 1. Project Overview

An AI-powered underwriting assistant that helps insurance underwriters assess customer risk profiles through a conversational chat interface. The system uses a multi-agent LLM pipeline to run parallel specialist assessments across finance, health, and life domains before aggregating them into a structured underwriting report. A suite of GitHub Actions workflows provides automated code review, documentation generation, test generation, and UAT facilitation using Claude AI.

---

## 2. Tech Stack

| Component | Technology | Version/Notes |
|---|---|---|
| Backend API | FastAPI | Python, served on port 8000 |
| Agent Framework | LangGraph | StateGraph-based agent with tool calling |
| LLM Orchestration | LangChain | `langchain-core`, `langchain-anthropic`, `langchain-google-genai` |
| Primary LLM (fast) | Anthropic Claude Haiku | `claude-haiku-4-5-20251001` |
| Primary LLM (full) | Anthropic Claude Sonnet | `claude-sonnet-4-20250514` |
| Secondary LLM | Google Gemini | `gemini-3-flash-preview` |
| Streaming | SSE (Server-Sent Events) | `sse-starlette` |
| Agent Memory / Checkpointing | Redis | `redis/redis-stack-server:7.2.0-v14`, port 6379 |
| Frontend | [TODO: what framework is the frontend built with?] | Served on port 8080 |
| Database | SQLite (read-only) | `customer_profile.db`, `feature_importance.db`, `model_predictions.db`, `application_profile.db` |
| Auth / Session DB | PostgreSQL | `postgres:16-alpine`, used by Chainlit layer |
| Containerisation | Docker Compose | Multi-service: redis, postgres, backend, frontend |
| Risk Classification Model | CatBoostClassifier | v1.0, trained on merged customer dataset |
| CI/CD AI Tools | Anthropic Claude Sonnet | `claude-sonnet-4-6` via GitHub Actions |
| CI/CD Notifications | SendGrid | Email delivery |
| Python (runtime) | Python | 3.12 (CI), [TODO: confirm backend container Python version] |

---

## 3. Architecture

The system is composed of four Docker services that interact as follows:

1. **Frontend** (port 8080) receives user messages and streams responses. It communicates with the **Backend** over HTTP, using the `BACKEND_URL` environment variable. [TODO: confirm whether the frontend is Chainlit or a custom UI]
2. **Backend** (port 8000) exposes a FastAPI application with a `/chat` POST endpoint and a `/health` GET endpoint. On each chat request it builds a LangGraph agent, which orchestrates tool calls sequentially.
3. The **agent** (`agent_with_skills.py`) uses a tagged LLM (`anthropic-fast` by default) to decide which tool to call. Available tools are:
   - `get_customer_profile` — retrieves a customer record from the SQLite databases.
   - `customer_lookalike` — finds similar customers using a precomputed similarity dictionary (`customer_similarity_dict.json`).
   - `run_underwriting_assessment` — spawns up to 4 concurrent specialist LLM calls (finance, health, life, etc.) capped by a semaphore, then aggregates results into a structured `UnderwritingReport` Pydantic model.
4. **Redis** (port 6379) stores LangGraph conversation checkpoints so conversation history persists within a session. **Note:** Redis is currently in-container and memory does not persist across backend restarts (see Known Issues).
5. **PostgreSQL** (port 5432) is used by the frontend/Chainlit layer for session and user data.
6. The **GitHub Actions workflows** (`.github/workflows/`) run five AI delivery tools against the repository on triggers such as PRs, merges, tags, and cron schedules. All tools call Claude via the shared `shared.py` utility and write outputs to a separate `ai-delivery-outputs` repository.

```
User
  │
  ▼
Frontend (8080)
  │  HTTP / SSE
  ▼
Backend / FastAPI (8000)
  │
  ├─► LangGraph Agent (claude-haiku / claude-sonnet)
  │       │
  │       ├─► get_customer_profile  ──► SQLite DBs
  │       ├─► customer_lookalike    ──► similarity_dict.json
  │       └─► run_underwriting_assessment
  │               │
  │               ├─► Specialist LLM × N (parallel, semaphore=4)
  │               └─► Aggregator LLM ──► UnderwritingReport (JSON)
  │
  ├─► Redis (6379)  — conversation checkpoints
  └─► PostgreSQL (5432) — frontend session data
```

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

2. **Create the `.env` file** at the repository root (copy and fill in the values from the table below):

```bash
cp .env.example .env   # if an example file exists, otherwise create manually
```

3. **Build and start all services**

```bash
docker compose up --build
```

4. **Verify the backend is healthy**

```bash
curl http://localhost:8000/health
# Expected: {"status": "ok"}
```

5. **Open the frontend**

Navigate to [http://localhost:8080](http://localhost:8080) in your browser.

6. **(Optional) Run the backend locally without Docker** for faster iteration:

```bash
cd backend
pip install -r requirements.txt   # [TODO: confirm requirements file name/location]
uvicorn main:app --reload --port 8000
```

> **Note:** You must still have Redis running (e.g. via `docker compose up redis`) because the agent checkpointer connects to it.

---

## 5. Environment Variables

The backend reads from an `.env` file loaded by `python-dotenv`. The GitHub Actions workflows read from repository secrets.

### Backend / Runtime

| Variable | Required | Default | Description |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | — | API key for Anthropic Claude (used by the agent and assessment LLMs) |
| `GOOGLE_API_KEY` | Yes (if using Gemini) | — | API key for Google Generative AI (Gemini model) |
| `REDIS_HOST` | No | `localhost` | Hostname of the Redis instance for LangGraph checkpointing |

### GitHub Actions Workflows

| Variable | Required | Default | Description |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | — | API key for Claude (used by all 5 AI delivery tools) |
| `GH_TOKEN` | Yes | — | GitHub Personal Access Token with repo read/write scope |
| `SENDGRID_API_KEY` | Yes | — | SendGrid API key for email notifications |
| `OUTPUT_REPO` | No | `ai-delivery-outputs` | Name of the GitHub repo where generated docs/reports are written |
| `OUTPUT_REPO_OWNER` | No | `github.repository_owner` | GitHub username/org owning the output repo |
| `NOTIFY_EMAIL` | No | `kylo.deng@capco.com` | Recipient email for workflow notifications |
| `SENDER_EMAIL` | No | `kylo.deng@capco.com` | Sender address for SendGrid emails |

### Docker Compose (PostgreSQL — hardcoded in `docker-compose.yml`)

| Variable | Required | Default | Description |
|---|---|---|---|
| `POSTGRES_USER` | — | `chainlit` | PostgreSQL username |
| `POSTGRES_PASSWORD` | — | `chainlit` | PostgreSQL password |
| `POSTGRES_DB` | — | `chainlit` | PostgreSQL database name |
| `DATABASE_URL` | — | `postgresql+asyncpg://chainlit:chainlit@postgres:5432/chainlit` | Full connection string passed to the frontend service |

---

## 6. Running Tests

[TODO: Are there existing test files in the repository? No test files were found in the provided source listing. The CI tool `tool4_auto_testing.py` generates tests but does not appear to run them automatically.]

To trigger AI-generated test generation via GitHub Actions:

```
# Via GitHub UI: Actions → "Tool 4 — Auto Testing" → Run workflow → Mode: generate
```

To trigger a coverage gap analysis:

```
# Via GitHub UI: Actions → "Tool 4 — Auto Testing" → Run workflow → Mode: gap-analysis
```

Generated test files are written to the `ai-delivery-outputs` repository under `auto-tests/<owner>-<repo>/`.

---

## 7. Deployment

### Local / Development

```bash
docker compose up --build
```

### Stopping services

```bash
docker compose down
```

### Stopping and removing volumes (full reset)

```bash
docker compose down -v
```

### Production Deployment

[TODO: Is there a Kubernetes manifest, Helm chart, Terraform, or Bicep configuration for production deployment? None was found in the provided files.]

### GitHub Actions — AI Delivery Workflows

The five automation workflows are triggered automatically but can also be run manually:

| Workflow | Trigger | Manual dispatch |
|---|---|---|
| Tool 1 — Code Review | PR open/sync, Monday 08:00 UTC | `workflow_dispatch` (mode: `repo` or `pr`) |
| Tool 2 — Tech Documentation | Push to `main`, Sunday 06:00 UTC | `workflow_dispatch` |
| Tool 3 — Business Documentation | Push of `v*` tag | `workflow_dispatch` (project name + version) |
| Tool 4 — Auto Testing | PR open/sync on `src/**`, Wednesday 07:00 UTC | `workflow_dispatch` (mode: `generate` or `gap-analysis`) |
| Tool 5 — UAT Facilitation | Creation of `release/*` branch | `workflow_dispatch` (mode: `generate` or `analyse`) |

To trigger Tool 3 via a version tag:

```bash
git tag v1.0.0
git push origin v1.0.0
```

To trigger Tool 5 via a release branch:

```bash
git checkout -b release/1.0.0
git push origin release/1.0.0
```

---

## 8. Known Issues / TODOs

The following are extracted directly from code comments:

- **`backend/agent/graph.py`**: Redis is running as a local container. Memory does not persist across serverless backend restarts. TODO: migrate Redis to an external managed service (e.g. Azure Cache for Redis, dedicated Redis container) so that checkpoint data persists.

- **`backend/modules/LLMS.py`**: `azure` and `openai` model providers are listed in the model mapper but are set to `None` and are not implemented. TODO: add more providers.

- **`backend/main.py`**: The `lifespan` context manager for FastAPI application startup/shutdown is commented out. TODO: determine if lifespan logic is needed.

- **`backend/agent/prompts.py`**: The `MODEL_CARD` is loaded from `model_card.json` but is not used within `prompts.py`. [TODO: confirm intended use of model card in the prompt.]

- **`.github/scripts/tool2_tech_docs.py`**: The `build_index` function contains a syntax error in the truncated source (`{r` — incomplete f-string). [TODO: verify the full function is correct in the repository.]

- **`backend/prompts/assessment_criterias.json`**: Assessment criteria prompts for modes `fast` and `deep` are defined, but the `fast` mode criteria content was truncated in the provided files. [TODO: confirm `fast` mode criteria are complete.]

- **GitHub Actions workflows**: All five workflows reference `send_email`, `email_html`, and `write_audit_entry` functions imported from `shared.py`, but the implementations of those functions were not present in the provided `shared.py` source (file was truncated). [TODO: confirm these functions are fully implemented.]

- **`backend/agent/prompts.py`**: `SYSTEM_PROMPT` does not incorporate the loaded `MODEL_CARD` data. [TODO: is the model card intended to be injected into the system prompt?]