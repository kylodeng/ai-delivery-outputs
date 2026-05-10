# Underwriting Chatbot

## 1. Project Overview

An AI-powered life insurance underwriting assistant that helps underwriters assess customer risk profiles through a conversational chat interface. The system uses a multi-agent LangGraph pipeline to run parallel specialist assessments across finance, health, and life risk domains, then aggregates the results into a structured underwriting report. A suite of five GitHub Actions workflows provides automated code review, documentation generation, test generation, and UAT facilitation using the Anthropic Claude API.

---

## 2. Tech Stack

| Component | Technology | Version/Notes |
|---|---|---|
| Backend API | FastAPI | Python, SSE streaming via `sse_starlette` |
| Agent Orchestration | LangGraph | `StateGraph` with tool-calling loop |
| LLM — Fast (default) | Anthropic Claude Haiku | `claude-haiku-4-5-20251001` |
| LLM — Full | Anthropic Claude Sonnet | `claude-sonnet-4-20250514` |
| LLM — Alternative | Google Gemini | `gemini-3-flash-preview` |
| Agent Memory / Checkpointing | Redis | `redis/redis-stack-server:7.2.0-v14` via LangGraph `AsyncRedisSaver` |
| Frontend | [TODO: what framework is the frontend built with?] | Served on port 8080 |
| Database | SQLite (read-only) | `customer_profile.db`, `feature_importance.db`, `model_predictions.db`, `application_profile.db` |
| Auth / Session DB | PostgreSQL | `postgres:16-alpine`, used by Chainlit session layer |
| Containerisation | Docker Compose | Multi-service stack |
| CI / AI Tooling | GitHub Actions + Anthropic Claude | 5 automated delivery workflows |
| CI AI Model | Anthropic Claude Sonnet | `claude-sonnet-4-6` (workflows) |
| Email Notifications | SendGrid | Via `SENDGRID_API_KEY` |
| Risk Model Card | CatBoostClassifier | `Underwriting Risk Classification v1.0` |

---

## 3. Architecture

The system is composed of four Docker services that communicate over a shared internal network:

1. **Frontend** (port 8080) — serves the chat UI. It forwards user messages to the Backend over HTTP, receiving streamed responses via Server-Sent Events (SSE).
2. **Backend** (port 8000) — a FastAPI application. On each `/chat` request it builds a LangGraph agent, which decides whether to call tools or produce a final answer. Tool calls include `get_customer_profile` (SQLite lookup), `customer_lookalike` (similarity index from `customer_similarity_dict.json`), and `run_underwriting_assessment`.
3. **Assessment pipeline** — invoked as a tool, this runs up to 4 concurrent specialist LLM calls (finance, health, life, etc.) tagged `"thinking"`, then aggregates the results through a structured-output aggregator LLM into an `UnderwritingReport` Pydantic model, which is rendered and returned.
4. **Redis** (port 6379) — provides LangGraph conversation checkpointing so that multi-turn context is preserved within a session.
5. **PostgreSQL** (port 5432) — stores Chainlit session/user data; initialised via `postgres/init.sql`.

The five GitHub Actions workflows (`.github/workflows/tool1–5`) run independently of the Docker stack, calling the GitHub API and Claude API to deliver automated code review, technical docs, business docs, test generation, and UAT test packs, writing outputs to a separate `ai-delivery-outputs` repository.

```
User ──► Frontend (8080)
             │  HTTP + SSE
             ▼
         Backend (8000) ──► Redis (6379)  [session memory]
             │
             ├──► get_customer_profile    [SQLite DBs]
             ├──► customer_lookalike      [similarity JSON]
             └──► run_underwriting_assessment
                       │
                       ├──► Specialist LLM ×N  (parallel, Semaphore=4)
                       └──► Aggregator LLM  ──► UnderwritingReport JSON
```

---

## 4. Local Development Setup

**Prerequisites:** Docker Desktop, Python 3.12+, an Anthropic API key.

1. **Clone the repository**

```bash
git clone https://github.com/kylodeng/underwriting_chatbot-main.git
cd underwriting_chatbot-main
```

2. **Create the root `.env` file** (used by the backend service and local Python runs)

```bash
cp .env.example .env   # if an example exists, otherwise create manually
```

Populate `.env` with at minimum (see [Environment Variables](#5-environment-variables) below):

```env
ANTHROPIC_API_KEY=sk-ant-...
GOOGLE_API_KEY=...
REDIS_HOST=localhost
```

3. **Start all services with Docker Compose**

```bash
docker compose up --build
```

This starts Redis, PostgreSQL, the backend, and the frontend. On first run the PostgreSQL `init.sql` script is executed automatically.

4. **Verify the backend is healthy**

```bash
curl http://localhost:8000/health
# Expected: {"status":"ok"}
```

5. **Access the frontend**

Open [http://localhost:8080](http://localhost:8080) in your browser.

6. **(Optional) Run the backend locally outside Docker** for faster iteration:

```bash
cd backend
pip install -r requirements.txt   # [TODO: confirm requirements file name/location]
uvicorn main:app --reload --port 8000
```

---

## 5. Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | — | Anthropic API key for Claude LLM calls |
| `GOOGLE_API_KEY` | Yes (if using Gemini) | — | Google API key for Gemini model |
| `REDIS_HOST` | No | `localhost` | Hostname of the Redis service used for LangGraph checkpointing |
| `GH_TOKEN` | Yes (CI only) | — | GitHub personal access token for workflow scripts |
| `SENDGRID_API_KEY` | Yes (CI only) | — | SendGrid API key for email notifications from CI workflows |
| `OUTPUT_REPO` | No (CI only) | `ai-delivery-outputs` | GitHub repo name where CI workflow outputs are written |
| `OUTPUT_REPO_OWNER` | No (CI only) | `GITHUB_REPOSITORY_OWNER` | GitHub owner of the output repo |
| `NOTIFY_EMAIL` | No (CI only) | `kylo.deng@capco.com` | Recipient address for CI email notifications |
| `SENDER_EMAIL` | No (CI only) | `kylo.deng@capco.com` | Sender address for CI email notifications |

PostgreSQL credentials are hardcoded in `docker-compose.yml` (`chainlit`/`chainlit`) and are **not** intended to be production credentials.

[TODO: Are there any additional environment variables required by the frontend service?]

---

## 6. Running Tests

[TODO: No test files were found in the provided source files. What is the test framework and where are tests located?]

The project includes a GitHub Actions workflow (`tool4_auto_testing.yml`) that uses Claude to auto-generate pytest/jest tests and perform coverage gap analysis. To trigger it manually:

1. Go to **Actions → Tool 4 — Auto Testing** in the GitHub UI.
2. Select **Run workflow** and choose mode `generate` or `gap-analysis`.

To run any generated tests locally (once generated and placed in the repo):

```bash
# Python (pytest assumed)
cd backend
pip install pytest
pytest
```

---

## 7. Deployment

### Local / development

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

### CI Workflows

All five GitHub Actions workflows are defined in `.github/workflows/` and require the following repository secrets to be set:

| Secret | Purpose |
|---|---|
| `ANTHROPIC_API_KEY` | Claude API access |
| `GH_TOKEN` | GitHub API access (write to output repo) |
| `SENDGRID_API_KEY` | Email notifications |

| Workflow | Trigger |
|---|---|
| Tool 1 — Code Review | PR open/sync, every Monday 08:00 UTC, manual dispatch |
| Tool 2 — Tech Docs | Push to `main`, every Sunday 06:00 UTC, manual dispatch |
| Tool 3 — Business Docs | Push of `v*` tag, manual dispatch |
| Tool 4 — Auto Testing | PR open/sync on `src/**` or `*.py/.js/.ts`, every Wednesday 07:00 UTC, manual dispatch |
| Tool 5 — UAT | `release/*` branch creation, manual dispatch |

[TODO: Is there a cloud infrastructure deployment (e.g. Kubernetes, Azure, AWS) beyond Docker Compose? No IaC files (.tf, .bicep) were found in the provided files.]

---

## 8. Known Issues / TODOs

Extracted from code comments:

- **`backend/agent/graph.py`** — `# TODO: migrate Redis to an external service (e.g. Azure Cache for Redis, dedicated Redis container) so that memory persists across serverless backend instances.`
- **`backend/modules/LLMS.py`** — `# TODO: add more providers here` — the `azure` and `openai` entries in the model mapper are currently `None` and will raise a `ValueError` if selected.
- **`backend/main.py`** — The `lifespan` context manager for the FastAPI app is commented out; the agent is rebuilt on every request rather than being shared across requests.
- **`backend/main.py`** — `_charts_sent` is a module-level in-process set; it will not be shared across multiple backend instances and will not be cleared between deployments.
- **`docker-compose.yml`** — PostgreSQL credentials (`chainlit`/`chainlit`) are hardcoded and should be moved to environment variables or secrets before production use.
- **`LLMS.py` / `config.yml`** — `gemini-3-flash-preview` is listed as the Gemini model identifier; this should be verified against the current Google Generative AI SDK model names.
- **CI workflows** — `NOTIFY_EMAIL` and `SENDER_EMAIL` default to `kylo.deng@capco.com`; these should be overridden via repository variables for use outside the original project context.
- **General** — No disaster recovery, monitoring, or alerting configuration is present in the provided files. [TODO: What observability stack (metrics, logs, traces) is intended for production?]