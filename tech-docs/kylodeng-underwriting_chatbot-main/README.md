# Underwriting Chatbot

## 1. Project Overview

An AI-powered underwriting assistant that helps insurance underwriters assess customer risk profiles through a conversational chat interface. The system uses a multi-agent LangGraph pipeline to run specialist LLM assessments across finance, health, and life insurance dimensions, then aggregates them into a structured underwriting report. A suite of five GitHub Actions CI/CD tools (code review, tech docs, business docs, auto-testing, and UAT facilitation) powered by Claude AI are also included to automate delivery workflows.

---

## 2. Tech Stack

| Component | Technology | Version/Notes |
|---|---|---|
| Backend framework | FastAPI | Python, with SSE streaming |
| Agent orchestration | LangGraph | `StateGraph` with custom tool routing |
| LLM (default/fast) | Claude Haiku | `claude-haiku-4-5-20251001` via `langchain-anthropic` |
| LLM (full/deep) | Claude Sonnet | `claude-sonnet-4-20250514` via `langchain-anthropic` |
| LLM (alternative) | Google Gemini | `gemini-3-flash-preview` via `langchain-google-genai` |
| Session memory / checkpointing | Redis | `redis-stack-server:7.2.0-v14` via LangGraph `AsyncRedisSaver` |
| Frontend | Chainlit | Connects to backend at `BACKEND_URL` |
| Database | SQLite (read-only) | `customer_profile.db`, `feature_importance.db`, `model_predictions.db`, `application_profile.db` |
| Auth / user persistence | PostgreSQL | `postgres:16-alpine`, used by Chainlit |
| Containerisation | Docker Compose | Multi-service: redis, postgres, backend, frontend |
| CI/CD AI tools | Anthropic Claude (`claude-sonnet-4-6`) | 5 GitHub Actions workflows |
| CI/CD notifications | SendGrid | Email delivery of reports |
| ML model card | CatBoostClassifier | `Underwriting Risk Classification` v1.0, trained on merged customer/KYC/financial data |

---

## 3. Architecture

The system is composed of four Docker services that interact as follows:

1. **Frontend (Chainlit)** receives underwriter messages and streams responses from the **Backend** over HTTP (SSE). It uses **PostgreSQL** for user session and chat history persistence.
2. **Backend (FastAPI)** exposes a `/chat` SSE endpoint. On each request it builds a LangGraph agent (`build_agent`) configured with the requested model and assessment mode (`fast` or `deep`).
3. **Agent** (`agent_with_skills.py` / `graph.py`) routes between three tools:
   - `get_customer_profile` — retrieves customer data from SQLite databases.
   - `customer_lookalike` — looks up similar customers from a pre-computed similarity dictionary (`customer_similarity_dict.json`).
   - `run_underwriting_assessment` — fans out to multiple specialist LLM calls (finance, health, life, etc.) concurrently (semaphore-limited to 4), then aggregates into a structured `UnderwritingReport` Pydantic model.
4. **Redis** stores LangGraph conversation checkpoints so that multi-turn chat state is maintained per `session_id`.
5. **GitHub Actions** workflows invoke five Python scripts (`.github/scripts/`) that call Claude directly via the Anthropic API to perform code review, generate technical/business documentation, auto-generate tests, and facilitate UAT — outputting results to a separate `ai-delivery-outputs` GitHub repository.

```
Underwriter
    │  HTTP (SSE)
    ▼
Frontend (Chainlit :8080)
    │  HTTP (SSE)  ──────────────────── PostgreSQL :5432
    ▼
Backend (FastAPI :8000)
    │
    ▼
LangGraph Agent
    ├── get_customer_profile  ──────── SQLite DBs (read-only volumes)
    ├── customer_lookalike  ─────────── similarity dict (JSON)
    └── run_underwriting_assessment
            ├── Specialist LLMs (×N, semaphore=4)  ── Anthropic / Gemini API
            └── Aggregator LLM  ── structured output → UnderwritingReport
    │
    ▼
Redis :6379  (LangGraph checkpointer — session memory)
```

---

## 4. Local Development Setup

**Prerequisites:** Docker Desktop, Python 3.12, a `.env` file in the repo root (see [Environment Variables](#5-environment-variables)).

1. **Clone the repository**

```bash
git clone https://github.com/kylodeng/underwriting_chatbot-main.git
cd underwriting_chatbot-main
```

2. **Create the root `.env` file** with required secrets (see Section 5).

```bash
cp .env.example .env   # [TODO: confirm whether .env.example exists in repo]
# then edit .env with your actual keys
```

3. **Build and start all services**

```bash
docker compose up --build
```

4. **Verify the backend is healthy**

```bash
curl http://localhost:8000/health
# expected: {"status": "ok"}
```

5. **Access the frontend**

Open [http://localhost:8080](http://localhost:8080) in your browser.

6. **(Optional) Run the backend locally without Docker** for faster iteration:

```bash
cd backend
pip install -r requirements.txt   # [TODO: confirm requirements.txt location]
uvicorn main:app --reload --port 8000
```

---

## 5. Environment Variables

The backend reads from a `.env` file mounted via `env_file: .env` in `docker-compose.yml`. The GitHub Actions workflows read from GitHub Secrets.

### Backend / Docker

| Variable | Required | Default | Description |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | — | Anthropic API key for Claude models |
| `GOOGLE_API_KEY` | Yes (if using Gemini) | — | Google API key for Gemini models |
| `REDIS_HOST` | No | `localhost` | Redis hostname; set to `redis` automatically by Docker Compose |

### GitHub Actions Workflows

| Variable | Required | Default | Description |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | — | Claude API key for all five AI delivery tools |
| `GH_TOKEN` | Yes | — | GitHub Personal Access Token for reading repos and writing to output repo |
| `SENDGRID_API_KEY` | Yes | — | SendGrid API key for email notifications |
| `OUTPUT_REPO` | No | `ai-delivery-outputs` | Name of the GitHub repo where generated docs/reports are written |
| `OUTPUT_REPO_OWNER` | No | `GITHUB_REPOSITORY_OWNER` | Owner of the output repo |
| `NOTIFY_EMAIL` | No | `kylo.deng@capco.com` | Recipient email for notifications |
| `SENDER_EMAIL` | No | `kylo.deng@capco.com` | Sender email address |

---

## 6. Running Tests

[TODO: Are there any existing test files in the repository (e.g. a `tests/` directory)?]

The repository includes an AI-powered test generation workflow (Tool 4) that automatically generates pytest test files for source files on PR open or on a Wednesday 07:00 UTC schedule:

```bash
# Trigger manually via GitHub Actions UI → "Tool 4 — Auto Testing" → Run workflow
# Or push a change to src/**,  *.py, *.js, or *.ts to trigger on PR
```

Generated test files are written to the `ai-delivery-outputs` repository.

To run any locally generated tests (pytest assumed for Python):

```bash
cd backend
pip install pytest
pytest
```

---

## 7. Deployment

### Docker Compose (local / single-host)

```bash
docker compose up --build -d
```

To stop:

```bash
docker compose down
```

To rebuild after code changes:

```bash
docker compose up --build --force-recreate
```

### GitHub Actions CI/CD Workflows

All five workflows are defined in `.github/workflows/` and require the GitHub Secrets listed in Section 5 to be configured on the repository.

| Workflow | Trigger | Purpose |
|---|---|---|
| Tool 1 — Code Review | PR open/sync, Monday 08:00 UTC, manual | Claude reviews PR diff, posts comments |
| Tool 2 — Tech Docs | Push to `main`, Sunday 06:00 UTC, manual | Generates README, ARCHITECTURE, RUNBOOK |
| Tool 3 — Business Docs | Push of `v*` tag, manual | Generates solution overview and gap questionnaire |
| Tool 4 — Auto Testing | PR open/sync on source files, Wednesday 07:00 UTC, manual | Generates pytest/jest test files |
| Tool 5 — UAT | Release branch creation, manual | Generates UAT test pack or analyses completed results |

To manually trigger any workflow:

```bash
# Via GitHub CLI
gh workflow run "Tool 2 — Tech Documentation" --repo kylodeng/underwriting_chatbot-main
```

[TODO: Is there a Kubernetes, Azure, or other cloud deployment configuration beyond Docker Compose?]

---

## 8. Known Issues / TODOs

Extracted from code comments:

| Location | Issue / TODO |
|---|---|
| `backend/agent/graph.py` | **TODO:** Migrate Redis to an external service (e.g. Azure Cache for Redis, dedicated Redis container) so that memory persists across serverless backend instances. |
| `backend/modules/LLMS.py` | **TODO:** Add more LLM providers (the `azure` and `openai` entries in `model_mapper` are currently `None` and will raise `ValueError` if selected). |
| `backend/main.py` | The `lifespan` context manager is commented out — application lifecycle management is not currently wired up. |
| `backend/main.py` | `_charts_sent` set is in-process memory only — will not survive restarts or work correctly across multiple backend instances. |
| `.github/scripts/shared.py` | `send_email`, `email_html`, and `write_audit_entry` functions are imported throughout the CI scripts but their implementations are truncated in the provided files — [TODO: confirm these are fully implemented in `shared.py`]. |
| `backend/modules/assessment.py` | Assessment runs with a concurrency semaphore of 4 — this value is hardcoded and not configurable via `config.yml`. |
| GitHub Actions workflows | `NOTIFY_EMAIL` and `SENDER_EMAIL` are hardcoded to `kylo.deng@capco.com` in workflow env blocks, overriding the `shared.py` defaults. Update to team distribution addresses before production use. |
| `tool2_tech_docs.py` | Minor code truncation: `f"# Tech Documentation Index — {owner}/{r` — the index builder function appears cut off in the source. |
| `tool3_business_docs.py` | Stakeholder names, go-live dates, and success metrics are always `[TODO]` — must be filled in manually after generation. |
| `tool5_uat.py` | UAT escalation path section always emits `[TODO: fill in team contacts]` — no escalation contacts are defined. |