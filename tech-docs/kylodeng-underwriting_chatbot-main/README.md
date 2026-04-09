# Underwriting Chatbot

## 1. Project Overview

An AI-powered underwriting assistant that helps insurance underwriters assess customer risk profiles through a conversational chat interface. The system uses a multi-agent LLM pipeline to run parallel specialist assessments across finance, health, and life domains, then aggregates them into a structured underwriting report with a risk classification. A suite of five GitHub Actions workflows provides automated code review, documentation generation, test generation, and UAT facilitation using Claude AI.

---

## 2. Tech Stack

| Component | Technology | Version/Notes |
|---|---|---|
| Backend framework | FastAPI | Python, with SSE streaming via `sse_starlette` |
| Agent orchestration | LangGraph | `StateGraph`-based agent with tool calling |
| LLM provider (primary) | Anthropic Claude | `claude-haiku-4-5-20251001` (fast), `claude-sonnet-4-20250514` (deep) |
| LLM provider (secondary) | Google Gemini | `gemini-3-flash-preview` |
| LLM integration | LangChain | `langchain-anthropic`, `langchain-google-genai` |
| Session memory / checkpointing | Redis | `redis/redis-stack-server:7.2.0-v14` via `langgraph.checkpoint.redis` |
| Customer profile storage | SQLite (via file mounts) | `customer_profile.db`, `model_predictions.db`, `application_profile.db`, `feature_importance.db` |
| Frontend chat UI | Chainlit | Served on port 8080 |
| Frontend–backend transport | HTTP + Server-Sent Events (SSE) | `/chat` endpoint streams events |
| Auth / session DB | PostgreSQL | `postgres:16-alpine`, used by Chainlit |
| Risk model | CatBoostClassifier | Pre-trained, v1.0, deployed 2024-06-01 |
| Containerisation | Docker Compose | Multi-service: redis, postgres, backend, frontend |
| CI/CD AI tools | Claude (`claude-sonnet-4-6`) + GitHub Actions | 5 workflow tools (code review, tech docs, business docs, auto testing, UAT) |
| Email notifications | SendGrid | Used by CI/CD workflows |
| Python version | Python | 3.12 (CI), [TODO: confirm backend runtime Python version in Dockerfile] |

---

## 3. Architecture

The system is composed of four Docker services that communicate over an internal Docker network:

1. **Frontend (Chainlit, port 8080)** — The user-facing chat interface. It sends user messages to the backend via HTTP POST to `/chat` and receives streaming responses as Server-Sent Events (SSE). It persists session/thread state to PostgreSQL.

2. **Backend (FastAPI, port 8000)** — The core application. On each `/chat` request it:
   - Builds a LangGraph agent (`build_agent`) configured with the requested LLM model and analysis mode.
   - The agent decides whether to call one of three tools: `get_customer_profile` (fetches customer data from SQLite), `customer_lookalike` (finds similar customers from a pre-computed similarity dictionary), or `run_underwriting_assessment` (runs parallel specialist LLM calls).
   - `run_underwriting_assessment` fans out up to 4 concurrent async calls to a specialist LLM (tagged `"thinking"`) — one per assessment category (finance, health, life, etc.) — then aggregates results using a structured-output LLM call into a typed `UnderwritingReport` Pydantic model.
   - The agent LLM (tagged `"agent"`) is streamed back to the client as SSE events (`tool_start`, `tool_end`, `response`, `chart`, etc.).
   - An alternative `agent_with_skills.py` implements the same loop without LangGraph, using a manual JSON-based tool-dispatch loop driven by skill markdown files in `backend/skills/`.

3. **Redis (port 6379)** — Used by LangGraph's `AsyncRedisSaver` to checkpoint agent conversation state, enabling multi-turn sessions keyed by `thread_id` (the `session_id` from the request).

4. **PostgreSQL (port 5432)** — Used by Chainlit for its own session/user data persistence.

Customer data is injected into the backend container as read-only SQLite database file mounts from a `./database/` directory on the host.

Five **GitHub Actions workflows** (`.github/workflows/`) run CI/CD automation using a shared Python library (`.github/scripts/shared.py`) that calls the Anthropic Claude API to perform code review, generate documentation, generate tests, and facilitate UAT. Outputs are written to a separate GitHub repository (`ai-delivery-outputs`).

---

## 4. Local Development Setup

**Prerequisites:** Docker Desktop (or Docker + Docker Compose), Git.

1. **Clone the repository**

```bash
git clone https://github.com/kylodeng/underwriting_chatbot-main.git
cd underwriting_chatbot-main
```

2. **Create the root `.env` file** (used by the backend service via `env_file: .env`)

```bash
cp .env.example .env   # if an example exists, otherwise create manually
```

Populate it with at minimum (see Environment Variables section):

```
ANTHROPIC_API_KEY=sk-ant-...
GOOGLE_API_KEY=AIza...
```

3. **Confirm database files are present** in `./database/`

```
./database/customer_profile.db
./database/feature_importance.db
./database/model_predictions.db
./database/application_profile.db
```

[TODO: How are these SQLite databases seeded or generated? Is there a script or a data generation step?]

4. **Build and start all services**

```bash
docker compose up --build
```

5. **Verify the backend is healthy**

```bash
curl http://localhost:8000/health
# Expected: {"status": "ok"}
```

6. **Open the chat UI**

Navigate to [http://localhost:8080](http://localhost:8080) in your browser.

7. **(Optional) Run the backend locally without Docker** for faster iteration:

```bash
cd backend
pip install -r requirements.txt   # [TODO: confirm requirements file name/location]
uvicorn main:app --reload --port 8000
```

---

## 5. Environment Variables

Variables consumed by the **backend** application (loaded from `.env` in the repo root or `backend/.env`):

| Variable | Required | Default | Description |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | — | Anthropic API key for Claude LLM calls |
| `GOOGLE_API_KEY` | Yes (if using Gemini) | — | Google API key for Gemini model |
| `REDIS_HOST` | No | `localhost` | Redis hostname; set to `redis` when running via Docker Compose |

Variables consumed by the **GitHub Actions CI/CD workflows** (stored as GitHub repository secrets/variables):

| Variable | Required | Default | Description |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | — | Claude API key for all five AI delivery tools |
| `GH_TOKEN` | Yes | — | GitHub Personal Access Token for reading repos and writing to `ai-delivery-outputs` |
| `SENDGRID_API_KEY` | Yes | — | SendGrid API key for email notifications |
| `OUTPUT_REPO` | No | `ai-delivery-outputs` | GitHub repository name where generated docs/tests are written |
| `OUTPUT_REPO_OWNER` | No | `$GITHUB_REPOSITORY_OWNER` | GitHub owner of the output repo |
| `NOTIFY_EMAIL` | No | `kylo.deng@capco.com` | Email address to receive workflow notifications |
| `SENDER_EMAIL` | No | `kylo.deng@capco.com` | Sender address for outbound emails |

[TODO: Are there any additional environment variables required by the frontend Chainlit service beyond `BACKEND_URL` and `DATABASE_URL`?]

---

## 6. Running Tests

[TODO: No test files or test runner configuration (e.g. `pytest.ini`, `pyproject.toml`, `jest.config.js`) were found in the provided source files. How are tests run locally?]

The repository includes a GitHub Actions workflow (`tool4_auto_testing.yml`) that uses Claude AI to **auto-generate** test files for source code and/or perform coverage gap analysis. This can be triggered:

- Automatically on pull requests that modify `src/**`, `*.py`, `*.js`, or `*.ts` files.
- On a schedule every Wednesday at 07:00 UTC.
- Manually via GitHub Actions UI, choosing `generate` (write new test files) or `gap-analysis` (report on coverage gaps).

Generated test files are written to the `ai-delivery-outputs` repository.

---

## 7. Deployment

### Local / Development

Start all services with Docker Compose:

```bash
docker compose up --build -d
```

Stop all services:

```bash
docker compose down
```

Stop and remove volumes (resets PostgreSQL and Redis data):

```bash
docker compose down -v
```

### CI/CD Workflows (GitHub Actions)

The five AI delivery workflows trigger automatically based on git events or can be run manually from the GitHub Actions UI:

| Workflow | Trigger | Manual dispatch |
|---|---|---|
| Tool 1 — Code Review | PR open/sync; Monday 08:00 UTC | `review_mode`: `repo` or `pr` |
| Tool 2 — Tech Docs | Push to `main`; Sunday 06:00 UTC | Yes |
| Tool 3 — Business Docs | Push of `v*` tag; | `project_name`, `release_version` |
| Tool 4 — Auto Testing | PR open/sync on source files; Wednesday 07:00 UTC | `test_mode`: `generate` or `gap-analysis` |
| Tool 5 — UAT Facilitation | `release/*` branch creation | `uat_mode`, `release_version`, optional stories/results |

Required GitHub secrets must be configured in the repository settings before any workflow can succeed: `ANTHROPIC_API_KEY`, `GH_TOKEN`, `SENDGRID_API_KEY`.

[TODO: Is there a cloud deployment target (e.g. Azure, AWS, GCP) beyond Docker Compose? No IaC files (`.tf`, `.bicep`) were found in the provided sources.]

---

## 8. Known Issues / TODOs

Extracted directly from code comments:

| Location | Issue / TODO |
|---|---|
| `backend/agent/graph.py` | `# TODO: migrate Redis to an external service (e.g. Azure Cache for Redis, dedicated Redis container) so that memory persists across serverless backend instances.` |
| `backend/modules/LLMS.py` | `# TODO: add more providers here` — `azure` and `openai` entries in the model mapper are present but set to `None` (unconfigured). |
| `backend/main.py` | Lifespan context manager (`@asynccontextmanager async def lifespan`) is commented out; the agent is rebuilt on every request rather than being initialised once at startup. |
| `backend/main.py` | `_charts_sent` set is in-process memory only; will not deduplicate across multiple backend instances or restarts. |
| `backend/modules/assessment.py` | Specialist LLM token cap comment: `# caps runaway specialist output (others was hitting 2772)` — `specialist_max_tokens` is set to 1500. |
| General | No IaC files found — there is no automated cloud infrastructure deployment. |
| General | No `requirements.txt` or `pyproject.toml` was provided; exact Python dependency versions are not pinned in the supplied files. |
| General | SQLite database files in `./database/` are required at runtime but no seeding or generation script is visible in the provided sources. |