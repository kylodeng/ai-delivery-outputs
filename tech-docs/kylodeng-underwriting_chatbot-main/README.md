# Underwriting Chatbot

## 1. Project Overview

An AI-powered insurance underwriting assistant that helps underwriters assess customer risk profiles through a conversational chat interface. The backend orchestrates multiple specialist LLM agents to evaluate finance, health, and life risk categories in parallel, then aggregates results into a structured `UnderwritingReport`. A suite of five GitHub Actions–based CI/CD tools (code review, tech docs, business docs, auto-testing, and UAT facilitation) automates delivery workflows using the Anthropic Claude API.

---

## 2. Tech Stack

| Component | Technology | Version/Notes |
|---|---|---|
| Backend framework | FastAPI | Python, with SSE streaming |
| Agent orchestration | LangGraph | `StateGraph`-based multi-node agent |
| LLM – default (fast) | Anthropic Claude Haiku | `claude-haiku-4-5-20251001` |
| LLM – deep assessment | Anthropic Claude Sonnet | `claude-sonnet-4-20250514` |
| LLM – alternative | Google Gemini | `gemini-3-flash-preview` |
| Frontend | [TODO: what framework/language is the frontend written in?] | Served on port 8080 |
| Chat UI | [TODO: confirm if Chainlit is the frontend framework] | PostgreSQL-backed sessions |
| Session memory / checkpointing | Redis (via LangGraph `AsyncRedisSaver`) | `redis-stack-server:7.2.0-v14` |
| Relational store | PostgreSQL | `postgres:16-alpine` |
| Customer data | SQLite databases | `customer_profile.db`, `feature_importance.db`, `model_predictions.db`, `application_profile.db` |
| Risk model | CatBoostClassifier | v1.0, trained on merged customer dataset |
| CI/CD automation | GitHub Actions | 5 workflow tools |
| AI for CI workflows | Anthropic Claude Sonnet | `claude-sonnet-4-6` (shared.py) |
| Email notifications | SendGrid | Via `SENDGRID_API_KEY` |
| Containerisation | Docker Compose | Multi-service |
| Python version | Python | 3.12 (Actions), [TODO: confirm backend Dockerfile base image] |

---

## 3. Architecture

The system is composed of four runtime services orchestrated by Docker Compose:

1. **Frontend** (port 8080) — serves the chat UI and communicates with the backend over HTTP. It uses PostgreSQL for session persistence (configured with a `DATABASE_URL` pointing to the `postgres` service).
2. **Backend** (port 8000) — a FastAPI application that exposes a `/chat` endpoint returning Server-Sent Events (SSE). On each request it builds a LangGraph agent (`build_agent`) configured with three tools: `get_customer_profile`, `customer_lookalike`, and `run_underwriting_assessment`. The agent streams token-by-token responses back to the frontend.
3. **Redis** (port 6379) — used by LangGraph's `AsyncRedisSaver` checkpointer to persist conversation thread state across turns within a session.
4. **PostgreSQL** (port 5432) — stores frontend session/chat history data.

The **underwriting assessment** tool fans out concurrent specialist LLM calls (capped at 4 via `asyncio.Semaphore`) for each assessment category (finance, health, life, etc.), then aggregates their outputs into a structured `UnderwritingReport` Pydantic model via a second "aggregator" LLM call with `with_structured_output`.

The **GitHub Actions CI/CD layer** is independent of the runtime services. Five workflows invoke Python scripts under `.github/scripts/` that call the Anthropic API directly and write outputs to a separate `ai-delivery-outputs` repository.

```
┌──────────────┐     HTTP/SSE      ┌──────────────────────────────┐
│   Frontend   │◄─────────────────►│  Backend (FastAPI)           │
│  (port 8080) │                   │  - LangGraph agent           │
└──────────────┘                   │  - Specialist LLMs (parallel)│
       │                           │  - Aggregator LLM            │
       │ PostgreSQL                └──────────┬───────────────────┘
       ▼                                      │ Redis checkpointer
┌──────────────┐                   ┌──────────▼───────────┐
│  PostgreSQL  │                   │       Redis          │
│  (sessions)  │                   │  (thread state)      │
└──────────────┘                   └──────────────────────┘
```

---

## 4. Local Development Setup

1. **Clone the repository**

```bash
git clone https://github.com/kylodeng/underwriting_chatbot-main.git
cd underwriting_chatbot-main
```

2. **Create the root `.env` file** (see [Environment Variables](#5-environment-variables) section)

```bash
cp .env.example .env   # if an example exists, otherwise create manually
```

3. **Populate `.env`** with at minimum:

```bash
ANTHROPIC_API_KEY=sk-ant-...
GOOGLE_API_KEY=...
```

4. **Install backend Python dependencies** (for local development without Docker)

```bash
cd backend
pip install -r requirements.txt   # [TODO: confirm requirements.txt filename and location]
```

5. **Start all services with Docker Compose**

```bash
docker-compose up --build
```

6. **Verify the backend is healthy**

```bash
curl http://localhost:8000/health
# Expected: {"status": "ok"}
```

7. **Open the frontend**

```
http://localhost:8080
```

---

## 5. Environment Variables

### Backend / Runtime (`.env` at repo root)

| Variable | Required | Default | Description |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | — | API key for Anthropic Claude models |
| `GOOGLE_API_KEY` | Yes (if using Gemini) | — | API key for Google Gemini models |
| `REDIS_HOST` | No | `localhost` | Hostname of the Redis service |

### GitHub Actions Secrets / Workflow Environment

| Variable | Required | Default | Description |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | — | Anthropic API key used by all five CI tools |
| `GH_TOKEN` | Yes | — | GitHub token for reading source repos and writing to output repo |
| `SENDGRID_API_KEY` | Yes | — | SendGrid API key for email notifications |
| `OUTPUT_REPO` | No | `ai-delivery-outputs` | Repository name where generated docs/tests are written |
| `OUTPUT_REPO_OWNER` | No | `GITHUB_REPOSITORY_OWNER` | Owner of the output repository |
| `NOTIFY_EMAIL` | No | `kylo.deng@capco.com` | Recipient email for notifications |
| `SENDER_EMAIL` | No | `kylo.deng@capco.com` | Sender address for notifications |

---

## 6. Running Tests

[TODO: Are there existing test files in this repository? No test files were found in the provided source tree.]

The repository includes an **AI-powered test generation workflow** (Tool 4) that can generate pytest/jest test files automatically:

```bash
# Trigger manually via GitHub Actions
gh workflow run "Tool 4 — Auto Testing" --field test_mode=generate
```

To run a coverage gap analysis against existing tests:

```bash
gh workflow run "Tool 4 — Auto Testing" --field test_mode=gap-analysis
```

---

## 7. Deployment

### Local / Development

```bash
docker-compose up --build
```

### Stopping services

```bash
docker-compose down
```

### Stopping and removing persistent volumes

```bash
docker-compose down -v
```

### GitHub Actions Workflows

The five CI/CD tools are triggered automatically or manually:

| Tool | Trigger | Manual trigger |
|---|---|---|
| Tool 1 – Code Review | PR open/sync, Monday 08:00 UTC | `gh workflow run "Tool 1 — Code Review"` |
| Tool 2 – Tech Docs | Push to `main`, Sunday 06:00 UTC | `gh workflow run "Tool 2 — Tech Documentation"` |
| Tool 3 – Business Docs | Push of `v*` tag | `gh workflow run "Tool 3 — Business Documentation" --field project_name=... --field release_version=...` |
| Tool 4 – Auto Testing | PR open/sync on `src/**`, Wednesday 07:00 UTC | `gh workflow run "Tool 4 — Auto Testing" --field test_mode=generate` |
| Tool 5 – UAT | Creation of `release/*` branch | `gh workflow run "Tool 5 — UAT Facilitation" --field uat_mode=generate --field release_version=1.0.0` |

Required GitHub repository secrets that must be configured before any workflow runs:

```
ANTHROPIC_API_KEY
GH_TOKEN
SENDGRID_API_KEY
```

[TODO: Is there any Terraform/Bicep/Kubernetes IaC for cloud deployment of the runtime services? No IaC files were found in the provided source tree.]

---

## 8. Known Issues / TODOs

Extracted from code comments:

| Location | Issue / TODO |
|---|---|
| `backend/agent/graph.py` | `# TODO: migrate Redis to an external service (e.g. Azure Cache for Redis, dedicated Redis container) so that memory persists across serverless backend instances.` |
| `backend/modules/LLMS.py` | `# TODO: add more providers here` — `azure` and `openai` entries are `None` (not implemented) |
| `backend/main.py` | Commented-out `lifespan` context manager — application lifespan management is incomplete |
| `backend/main.py` | `_charts_sent` set is module-level and not cleared between deploys; will grow unboundedly in a long-running process |
| `docker-compose.yml` | SQLite database files are mounted read-only from a local `./database/` directory — no database initialisation or migration tooling is evident |
| All GitHub Actions workflow docs | `send_email`, `email_html`, and `write_audit_entry` are imported in scripts but their implementations are truncated in the provided `shared.py` — [TODO: confirm these functions are fully implemented] |
| `.github/scripts/tool2_tech_docs.py` | `build_index` function is truncated (`{r` — likely a bug in the source file) |
| `.github/scripts/tool4_auto_testing.py` | `build_test_pack_csv` function is truncated |
| `.github/scripts/tool5_uat.py` | `parse_scenarios` / `build_test_pack_csv` functions are truncated |
| `backend/agent/agent_with_skills.py` | `TOOLS["run_risk_assessment"]` calls `_run_underwriting_assessment("fast")` at import time — this eagerly initialises the tool; relationship between `agent_with_skills.py` and `graph.py` (two different agent implementations) is unclear |
| General | No DR (Disaster Recovery) strategy or multi-region deployment configuration is evident |
| General | No monitoring or alerting configuration (e.g. Prometheus, Datadog) is evident |