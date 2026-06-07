# Underwriting Chatbot

## 1. Project Overview

This is an AI-powered underwriting assistant that helps insurance underwriters assess customer risk profiles through a conversational chat interface. The backend orchestrates multiple specialist LLM agents (finance, health, life, etc.) to produce structured underwriting reports, while the frontend provides a streaming chat UI. A suite of five GitHub Actions workflows additionally automates code review, documentation generation, test generation, and UAT facilitation using Claude AI.

---

## 2. Tech Stack

| Component | Technology | Version/Notes |
|---|---|---|
| Backend API | FastAPI (Python) | Async, Server-Sent Events (SSE) |
| Agent Orchestration | LangGraph / LangChain | `StateGraph`, `astream_events` v2 |
| Primary LLM (fast) | Anthropic Claude Haiku | `claude-haiku-4-5-20251001` |
| Primary LLM (full) | Anthropic Claude Sonnet | `claude-sonnet-4-20250514` |
| Secondary LLM | Google Gemini | `gemini-3-flash-preview` |
| Risk Classification Model | CatBoostClassifier | v1.0, trained on merged customer dataset |
| Session Memory / Checkpointing | Redis (via LangGraph AsyncRedisSaver) | `redis-stack-server:7.2.0-v14` |
| Frontend Database | PostgreSQL | v16-alpine (Chainlit persistence) |
| Frontend | [TODO: What framework/library powers the frontend? Only a `./frontend` build context is referenced in docker-compose.yml] | Port 8080 |
| Customer Data | SQLite databases | `customer_profile.db`, `feature_importance.db`, `model_predictions.db`, `application_profile.db` |
| Customer Similarity Index | Pre-computed JSON | `backend/tmp/customer_similarity_dict.json` |
| CI/CD & AI Workflows | GitHub Actions | Python 3.12, `anthropic`, `requests` |
| Email Notifications | SendGrid | Via `SENDGRID_API_KEY` |
| Containerisation | Docker Compose | Multi-service |

---

## 3. Architecture

The system is composed of four runtime services (defined in `docker-compose.yml`) and five GitHub Actions automation workflows:

**Runtime services:**

1. **Frontend** (port 8080) — Chat UI that sends user messages to the backend over HTTP and renders streaming responses. It reads/writes session data from PostgreSQL.
2. **Backend** (port 8000) — FastAPI application that receives chat requests and streams responses via Server-Sent Events (SSE). On each request it builds a LangGraph agent configured with the requested model and mode.
3. **Agent layer** — The LangGraph `StateGraph` agent (`agent_with_skills.py`) decides which tools to call sequentially (one at a time). Available tools are: `get_customer_profile`, `customer_lookalike`, and `run_underwriting_assessment`. The assessment tool fans out to multiple specialist LLM calls (finance, health, life, etc.) concurrently (semaphore-limited to 4), then aggregates results into a structured `UnderwritingReport` Pydantic model.
4. **Redis** (port 6379) — Stores LangGraph conversation checkpoints so session history persists across agent invocations within a session.
5. **PostgreSQL** (port 5432) — Used by the frontend (Chainlit) for its own persistence (users, threads).

**GitHub Actions workflows** run on pull requests, merges, tags, or schedules and call Claude (`claude-sonnet-4-6`) via `shared.py` to produce code reviews, README/architecture/runbook docs, business documents, auto-generated tests, and UAT test packs. Outputs are written to a separate `ai-delivery-outputs` repository.

```
User → Frontend (8080)
         │ HTTP POST /chat (SSE)
         ▼
     Backend FastAPI (8000)
         │ builds LangGraph agent
         ▼
     Agent (StateGraph)
      ├─ get_customer_profile   ─→ SQLite DBs
      ├─ customer_lookalike     ─→ customer_similarity_dict.json
      └─ run_underwriting_assessment
              ├─ Specialist LLMs × N (parallel, tagged "thinking")
              └─ Aggregator LLM  → UnderwritingReport (structured JSON)
         │
     Redis (checkpoints / session memory)
     PostgreSQL (frontend session persistence)
```

---

## 4. Local Development Setup

**Prerequisites:** Docker Desktop, Python 3.12, a `.env` file at the repo root (see Environment Variables section).

### With Docker Compose (recommended)

```bash
# 1. Clone the repository
git clone https://github.com/kylodeng/underwriting_chatbot-main.git
cd underwriting_chatbot-main

# 2. Create your .env file at the repo root (see Environment Variables section)
cp .env.example .env   # or create .env manually
# Edit .env and fill in ANTHROPIC_API_KEY, GOOGLE_API_KEY, etc.

# 3. Build and start all services
docker compose up --build

# 4. Access the frontend
open http://localhost:8080

# 5. Access the backend health check
curl http://localhost:8000/health
```

### Backend only (without Docker)

```bash
# 1. Navigate to the backend directory
cd backend

# 2. Create and activate a virtual environment
python3.12 -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# 3. Install dependencies
# [TODO: Is there a requirements.txt or pyproject.toml in the backend directory?]
pip install fastapi uvicorn langchain langgraph langchain-anthropic langchain-google-genai \
    redis pydantic pyyaml python-dotenv sse-starlette anthropic

# 4. Ensure Redis is running locally (or use Docker)
docker run -p 6379:6379 redis/redis-stack-server:7.2.0-v14

# 5. Start the backend
uvicorn main:app --reload --port 8000
```

---

## 5. Environment Variables

Place these in a `.env` file at the repo root. The backend service loads it via `python-dotenv`.

| Variable | Required | Default | Description |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | — | API key for Anthropic Claude (all LLM calls and GitHub Actions workflows) |
| `GOOGLE_API_KEY` | Yes (if using Gemini) | — | API key for Google Generative AI (Gemini model) |
| `REDIS_HOST` | No | `localhost` | Redis hostname; set to `redis` when running in Docker Compose |
| `GH_TOKEN` | Yes (GitHub Actions only) | — | GitHub personal access token for Actions workflows (read/write repo, post PR comments) |
| `SENDGRID_API_KEY` | Yes (GitHub Actions only) | — | SendGrid API key for email notifications from Actions workflows |
| `OUTPUT_REPO` | No (GitHub Actions only) | `ai-delivery-outputs` | Name of the GitHub repository where Actions workflow outputs are written |
| `OUTPUT_REPO_OWNER` | No (GitHub Actions only) | `GITHUB_REPOSITORY_OWNER` | Owner of the output repository |
| `NOTIFY_EMAIL` | No (GitHub Actions only) | `kylo.deng@capco.com` | Recipient email for workflow notifications |
| `SENDER_EMAIL` | No (GitHub Actions only) | `kylo.deng@capco.com` | Sender address for workflow emails |

---

## 6. Running Tests

[TODO: Are there any existing test files in the repository (e.g. `tests/` directory, `pytest.ini`, or `conftest.py`)?]

The repository includes a GitHub Actions workflow (Tool 4) that auto-generates tests using Claude AI. To trigger it:

```bash
# Via GitHub CLI — manual dispatch to generate tests
gh workflow run "Tool 4 — Auto Testing" --field test_mode=generate

# Via GitHub CLI — run coverage gap analysis
gh workflow run "Tool 4 — Auto Testing" --field test_mode=gap-analysis
```

This workflow fires automatically on pull requests that modify `src/**`, `*.py`, `*.js`, or `*.ts` files, and on a weekly schedule (Wednesdays 07:00 UTC). Generated test files are written to the `ai-delivery-outputs` repository.

---

## 7. Deployment

### Local / development

```bash
docker compose up --build
```

### Stopping services

```bash
docker compose down

# To also remove persistent volumes (PostgreSQL data)
docker compose down -v
```

### GitHub Actions workflows

All five automation tools run on GitHub Actions and require the following repository secrets to be configured:

```
ANTHROPIC_API_KEY
GH_TOKEN
SENDGRID_API_KEY
```

| Workflow | Trigger | Purpose |
|---|---|---|
| Tool 1 — Code Review | PR open/sync, Monday 08:00 UTC, manual | AI code review posted as PR comment |
| Tool 2 — Tech Docs | Push to `main`, Sunday 06:00 UTC, manual | Generates README, ARCHITECTURE, RUNBOOK |
| Tool 3 — Business Docs | Version tag (`v*`), manual | Generates business solution overview + gap questionnaire |
| Tool 4 — Auto Testing | PR open/sync on source files, Wednesday 07:00 UTC, manual | Generates or analyses test files |
| Tool 5 — UAT | Release branch creation, manual | Generates UAT test pack or analyses completed results CSV |

To trigger a workflow manually via GitHub CLI:

```bash
# Example: trigger business docs for a release
gh workflow run "Tool 3 — Business Documentation" \
  --field project_name="Underwriting Chatbot" \
  --field release_version="1.0.0"
```

[TODO: Is there a production deployment target (e.g. Azure Container Apps, Kubernetes, AWS ECS)? No IaC files (.tf, .bicep) were found in the repository.]

---

## 8. Known Issues / TODOs

Extracted directly from code comments:

| Location | Issue / TODO |
|---|---|
| `backend/agent/graph.py` | **Redis persistence**: `# TODO: migrate Redis to an external service (e.g. Azure Cache for Redis, dedicated Redis container) so that memory persists across serverless backend instances.` |
| `backend/modules/LLMS.py` | **Additional providers**: `# TODO: add more providers here` — `azure` and `openai` entries exist in the model mapper but are set to `None` (unconfigured). |
| `backend/config.yml` | `specialist_max_tokens: 1500` — comment notes this caps runaway specialist output (previous runs were hitting 2772 tokens). |
| `docker-compose.yml` | `main.py` contains a commented-out `lifespan` context manager — application startup/shutdown lifecycle is not currently implemented. |
| `backend/main.py` | `_charts_sent` set is module-level and in-memory — chart deduplication state will be lost on process restart and is not shared across multiple backend instances. |
| GitHub Actions scripts | `send_email`, `email_html`, and `write_audit_entry` are imported in workflow scripts but their implementations are truncated in the provided `shared.py` — [TODO: confirm these functions are fully implemented in the actual file]. |
| `backend/modules/LLMS.py` | Model name `gemini-3-flash-preview` — [TODO: verify this is the correct/current Google model identifier]. |
| General | No `requirements.txt` or `pyproject.toml` was found in the provided files — [TODO: confirm dependency management approach for the backend]. |
| General | No IaC files (Terraform, Bicep, etc.) present — [TODO: what is the production infrastructure and how is it provisioned?]. |