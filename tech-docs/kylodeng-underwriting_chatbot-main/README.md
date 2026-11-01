# Underwriting Chatbot

## 1. Project Overview

An AI-powered underwriting assistant that helps insurance underwriters assess customer risk profiles through a conversational chat interface. The system uses LangGraph-orchestrated agents to gather customer information, run multi-specialist underwriting assessments in parallel, and return structured risk classifications. A suite of five GitHub Actions CI/CD tools (code review, tech docs, business docs, auto-testing, and UAT facilitation) are included for AI-assisted delivery workflows.

---

## 2. Tech Stack

| Component | Technology | Version/Notes |
|---|---|---|
| Backend API | FastAPI | Python, SSE streaming via `sse-starlette` |
| Agent Orchestration | LangGraph | StateGraph with custom tool-call loop |
| LLM – Primary | Anthropic Claude Haiku | `claude-haiku-4-5-20251001` (anthropic-fast, default) |
| LLM – Assessment | Anthropic Claude Sonnet | `claude-sonnet-4-20250514` (anthropic) |
| LLM – Alternative | Google Gemini | `gemini-3-flash-preview` |
| Agent Memory / Checkpointing | Redis (LangGraph AsyncRedisSaver) | `redis/redis-stack-server:7.2.0-v14` |
| Frontend | [TODO: what framework/language is the frontend?] | Served on port 8080 |
| Database | SQLite (read-only volumes) | `customer_profile.db`, `feature_importance.db`, `model_predictions.db`, `application_profile.db` |
| Auth/Session DB | PostgreSQL | `postgres:16-alpine`, used by Chainlit |
| Containerisation | Docker Compose | Multi-service |
| AI Delivery Workflows | GitHub Actions + Anthropic Claude | `claude-sonnet-4-6`, Python 3.12 |
| Email Notifications | SendGrid | Via `shared.py` |
| Risk Model Card | CatBoostClassifier | v1.0, pre-trained (not trained at runtime) |

---

## 3. Architecture

```
┌─────────────────────────────────────────────────────┐
│                      Frontend                       │
│           (port 8080, BACKEND_URL → backend)        │
└───────────────────────┬─────────────────────────────┘
                        │ HTTP / SSE
┌───────────────────────▼─────────────────────────────┐
│              FastAPI Backend (port 8000)             │
│  POST /chat → builds LangGraph agent per request    │
│  GET  /health                                        │
│                                                     │
│  ┌─────────────────────────────────────────────┐    │
│  │           LangGraph Agent (agent_with_skills)│    │
│  │  ┌──────────┐  ┌────────────┐  ┌─────────┐ │    │
│  │  │get_customer│ │run_risk_  │  │customer │ │    │
│  │  │_profile   │ │assessment │  │lookalike│ │    │
│  │  └──────────┘  └─────┬─────┘  └─────────┘ │    │
│  └────────────────────── │ ──────────────────┘    │
│                          │                          │
│  ┌───────────────────────▼─────────────────────┐   │
│  │        Parallel Specialist LLM Calls         │   │
│  │  finance | health | life | … (asyncio.gather)│   │
│  │  → structured UnderwritingReport (Pydantic)  │   │
│  └─────────────────────────────────────────────┘   │
└──────┬──────────────────────┬──────────────────────┘
       │                      │
┌──────▼──────┐    ┌──────────▼──────────┐
│    Redis     │    │  SQLite DBs (read-  │
│ (checkpoint/ │    │  only: customer,    │
│  sessions)   │    │  model, application)│
└─────────────┘    └─────────────────────┘
       │
┌──────▼──────┐
│  PostgreSQL  │
│ (Chainlit    │
│  session DB) │
└─────────────┘
```

**Data flow summary:**
1. The user sends a message from the frontend to `POST /chat` on the FastAPI backend.
2. The backend builds a LangGraph agent for the session and streams events back via SSE.
3. The agent (LLM) decides which tools to call sequentially (get customer profile → run assessment → lookalike).
4. The underwriting assessment tool spawns parallel specialist LLM calls (finance, health, life, etc.), each capped at 1 500 output tokens, then aggregates into a structured `UnderwritingReport` via a second LLM call with structured output.
5. Session state (conversation history, tool call results) is checkpointed to Redis using LangGraph's `AsyncRedisSaver`.
6. Customer profile data is read from read-only SQLite databases mounted into the backend container.

---

## 4. Local Development Setup

**Prerequisites:** Docker Desktop, Python 3.12, a `.env` file (see [Environment Variables](#5-environment-variables)).

1. **Clone the repository**

```bash
git clone https://github.com/kylodeng/underwriting_chatbot-main.git
cd underwriting_chatbot-main
```

2. **Create a `.env` file** in the repo root (see the Environment Variables section below for required keys)

```bash
cp .env.example .env   # if an example exists, otherwise create manually
# Edit .env and fill in ANTHROPIC_API_KEY, GOOGLE_API_KEY, etc.
```

3. **Start all services with Docker Compose**

```bash
docker compose up --build
```

This starts: Redis, PostgreSQL, the FastAPI backend (port 8000), and the frontend (port 8080).

4. **Verify the backend is healthy**

```bash
curl http://localhost:8000/health
# Expected: {"status": "ok"}
```

5. **Access the frontend**

Open [http://localhost:8080](http://localhost:8080) in your browser.

6. **(Optional) Run the backend locally without Docker** — install Python dependencies first:

```bash
cd backend
pip install -r requirements.txt   # [TODO: confirm requirements.txt filename and location]
uvicorn main:app --reload --port 8000
```

---

## 5. Environment Variables

The backend reads from a `.env` file located at the repo root (loaded by `dotenv` in `main.py` and `LLMS.py`).

| Variable | Required | Default | Description |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | — | API key for Anthropic Claude (Haiku and Sonnet models) |
| `GOOGLE_API_KEY` | Yes (if using Gemini) | — | API key for Google Generative AI (Gemini) |
| `REDIS_HOST` | No | `localhost` | Hostname of the Redis instance for LangGraph checkpointing |
| `GH_TOKEN` | Yes (CI workflows only) | — | GitHub token for CI delivery workflow scripts |
| `SENDGRID_API_KEY` | Yes (CI workflows only) | — | SendGrid API key for email notifications from CI scripts |
| `OUTPUT_REPO` | No (CI workflows only) | `ai-delivery-outputs` | GitHub repo name where CI tool outputs are written |
| `OUTPUT_REPO_OWNER` | No (CI workflows only) | `GITHUB_REPOSITORY_OWNER` | Owner of the output GitHub repo |
| `NOTIFY_EMAIL` | No (CI workflows only) | `kylo.deng@capco.com` | Email address for CI workflow notifications |
| `SENDER_EMAIL` | No (CI workflows only) | `kylo.deng@capco.com` | Sender address for CI workflow emails |

> **Note:** PostgreSQL credentials (`chainlit`/`chainlit`) are hardcoded in `docker-compose.yml`. [TODO: should these be moved to `.env` for security?]

---

## 6. Running Tests

[TODO: Are there existing test files in the repository? No test files were found in the provided source. The CI pipeline includes Tool 4 (Auto Testing) which generates tests via Claude, but no test runner configuration (e.g. `pytest.ini`, `pyproject.toml`) was found.]

To run any auto-generated tests produced by the CI pipeline:

```bash
cd backend
pip install pytest
pytest
```

To trigger the auto-test generation workflow manually:

1. Go to **Actions → Tool 4 — Auto Testing** in GitHub.
2. Select **Run workflow** and choose mode `generate` or `gap-analysis`.

---

## 7. Deployment

### Local / Development

```bash
docker compose up --build
```

### CI/CD Workflows (GitHub Actions)

Five automated workflows are defined in `.github/workflows/`. They require the following **GitHub repository secrets** to be set:

| Secret | Purpose |
|---|---|
| `ANTHROPIC_API_KEY` | Claude API access |
| `GH_TOKEN` | GitHub API write access (to post comments, write output files) |
| `SENDGRID_API_KEY` | Email delivery |

| Workflow | Trigger | What it does |
|---|---|---|
| Tool 1 — Code Review | PR open/sync, Monday 08:00 UTC, manual | Posts Claude code review as PR comment; writes report to output repo |
| Tool 2 — Tech Docs | Push to `main`, Sunday 06:00 UTC, manual | Generates README, architecture doc, and runbook to output repo |
| Tool 3 — Business Docs | Version tag (`v*`), manual | Generates solution overview + gap questionnaire |
| Tool 4 — Auto Testing | PR open/sync on source files, Wednesday 07:00 UTC, manual | Generates or gap-analyses test files |
| Tool 5 — UAT | `release/*` branch creation, manual | Generates UAT test pack or analyses completed results CSV |

All CI scripts write outputs to a separate GitHub repository named `ai-delivery-outputs` under the same owner.

### Production Deployment

[TODO: Is there Kubernetes, Helm, Terraform, or other IaC for production deployment? No IaC files were found in the provided source.]

---

## 8. Known Issues / TODOs

Extracted from code comments:

| Location | Issue / TODO |
|---|---|
| `backend/agent/graph.py` | **TODO:** Migrate Redis to an external service (e.g. Azure Cache for Redis, dedicated Redis container) so that memory persists across serverless backend instances. |
| `backend/modules/LLMS.py` | **TODO:** Add more LLM providers. `azure` and `openai` entries in `model_mapper` are currently `None` and will raise `ValueError` if selected. |
| `backend/main.py` | `lifespan` async context manager is commented out — application lifecycle management is incomplete. |
| `backend/main.py` | `_charts_sent` set is never cleared between server restarts, which could cause charts not to re-render after a server restart within the same process. |
| `docker-compose.yml` | PostgreSQL credentials (`chainlit`/`chainlit`) are hardcoded; should be moved to environment variables/secrets. |
| `.github/scripts/tool5_uat.py` | Escalation path in generated runbooks always produces `[TODO: fill in team contacts]`. |
| `backend/model_card.json` | `High_Risk_Occupation` has a global feature importance of `0.0` — this field may be unused or improperly encoded in the training data. |
| General | No test files are present in the repository; test coverage is 0% until CI Tool 4 generates them. |
| General | No IaC (Terraform, Bicep, etc.) found — production deployment procedure is undocumented. |