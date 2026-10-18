# Underwriting Chatbot

## 1. Project Overview

This is an AI-powered underwriting assistant chatbot that helps insurance underwriters assess customer risk profiles. The system uses large language models (Claude and Gemini) orchestrated via LangGraph to run multi-specialist underwriting assessments, retrieve customer profiles, and identify similar customers. A streaming chat API backend serves a frontend UI, with results delivered in real time via Server-Sent Events (SSE).

---

## 2. Tech Stack

| Component | Technology | Version/Notes |
|---|---|---|
| Backend API | FastAPI | Python, port 8000 |
| Agent Orchestration | LangGraph | Custom StateGraph agent |
| Primary LLM | Anthropic Claude | `claude-sonnet-4-20250514` (deep), `claude-haiku-4-5-20251001` (fast) |
| Secondary LLM | Google Gemini | `gemini-3-flash-preview` |
| Frontend | [TODO: what framework is the frontend built with?] | Port 8080 |
| Cache / Checkpointer | Redis Stack Server | 7.2.0-v14, port 6379 |
| Database | PostgreSQL | 16-alpine, port 5432 |
| Customer Data | SQLite | Three `.db` files mounted read-only |
| Risk Model | CatBoostClassifier | v1.0, trained on merged customer dataset |
| Streaming | SSE (Server-Sent Events) | via `sse-starlette` |
| CI/CD AI Tools | GitHub Actions + Anthropic Claude | 5 automated workflow tools |
| Containerisation | Docker Compose | Multi-service |
| Secret management | Environment variables / `.env` file | |

---

## 3. Architecture

The system is composed of four Docker services that interact as follows:

1. **Frontend** (port 8080) sends user chat messages to the **Backend** API over HTTP.
2. **Backend** (FastAPI, port 8000) receives each `POST /chat` request and builds a LangGraph agent on the fly, configured with the requested model, temperature, and assessment mode.
3. The **LangGraph agent** (`agent_with_skills.py`) operates a ReAct-style loop:
   - It calls **tools** one at a time: `get_customer_profile` (SQLite lookup), `customer_lookalike` (similarity dictionary lookup), and `run_underwriting_assessment`.
   - `run_underwriting_assessment` fans out up to 4 concurrent async calls to **specialist LLMs** (one per assessment category: finance, health, life, etc.), then aggregates results via a structured-output aggregator LLM into a typed `UnderwritingReport` Pydantic model.
4. Agent conversation checkpoints are stored in **Redis** (port 6379), enabling multi-turn session memory.
5. **PostgreSQL** (port 5432) backs the frontend's Chainlit session/user data.
6. The backend streams responses to the frontend as **Server-Sent Events**, emitting `tool_start`, `tool_end`, `response`, and `chart` event types.
7. Five **GitHub Actions workflows** (`.github/workflows/tool1–5`) run Claude-powered automation: code review, tech doc generation, business doc generation, test generation, and UAT facilitation. Outputs are written to a separate `ai-delivery-outputs` repository.

```
User
 │
 ▼
Frontend (port 8080)
 │  HTTP POST /chat
 ▼
Backend / FastAPI (port 8000)
 │
 ├─► LangGraph Agent
 │      │
 │      ├─► get_customer_profile  ──► SQLite DBs (read-only)
 │      ├─► customer_lookalike    ──► customer_similarity_dict.json
 │      └─► run_underwriting_assessment
 │               │
 │               ├─► Specialist LLM × N (parallel, tag: "thinking")
 │               └─► Aggregator LLM  (structured output → UnderwritingReport)
 │
 ├─► Redis (session checkpoints, port 6379)
 └─► SSE stream → Frontend

PostgreSQL (port 5432) ◄── Frontend (Chainlit session data)
```

---

## 4. Local Development Setup

**Prerequisites:** Docker Desktop, Python 3.12+, and a `.env` file in the repo root (see Environment Variables below).

1. **Clone the repository**
   ```bash
   git clone https://github.com/kylodeng/underwriting_chatbot-main.git
   cd underwriting_chatbot-main
   ```

2. **Create the root `.env` file**
   ```bash
   cp .env.example .env   # [TODO: confirm whether .env.example exists]
   # Edit .env and fill in required values (see Environment Variables section)
   ```

3. **Start all services with Docker Compose**
   ```bash
   docker-compose up --build
   ```

4. **Verify the backend is healthy**
   ```bash
   curl http://localhost:8000/health
   # Expected: {"status": "ok"}
   ```

5. **Access the frontend**
   ```
   http://localhost:8080
   ```

6. **(Optional) Run the backend locally without Docker**
   ```bash
   cd backend
   pip install -r requirements.txt   # [TODO: confirm requirements.txt location]
   uvicorn main:app --reload --port 8000
   ```

7. **(Optional) Run Redis locally without Docker**
   ```bash
   docker run -p 6379:6379 redis/redis-stack-server:7.2.0-v14
   ```

---

## 5. Environment Variables

The backend reads from a `.env` file at `backend/.env` (loaded via `python-dotenv`). The root `.env` file is passed to the Docker Compose `backend` service via `env_file: .env`.

| Variable | Required | Default | Description |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | — | Anthropic Claude API key (used by backend LLMs and all 5 CI tools) |
| `GOOGLE_API_KEY` | Yes | — | Google API key for Gemini LLM |
| `REDIS_HOST` | No | `localhost` | Hostname of the Redis instance |
| `GH_TOKEN` | Yes (CI only) | — | GitHub personal access token for CI workflow scripts |
| `SENDGRID_API_KEY` | Yes (CI only) | — | SendGrid API key for CI email notifications |
| `OUTPUT_REPO` | No (CI only) | `ai-delivery-outputs` | GitHub repo name where CI tool outputs are written |
| `OUTPUT_REPO_OWNER` | No (CI only) | `GITHUB_REPOSITORY_OWNER` | GitHub org/user owning the output repo |
| `NOTIFY_EMAIL` | No (CI only) | `kylo.deng@capco.com` | Email address for CI notification delivery |
| `SENDER_EMAIL` | No (CI only) | `kylo.deng@capco.com` | Sender address for CI emails |

PostgreSQL credentials are hardcoded in `docker-compose.yml` (`chainlit`/`chainlit`). [TODO: should these be moved to environment variables?]

---

## 6. Running Tests

[TODO: No test files or test runner configuration were found in the provided source files. What test framework and test location should be used?]

The CI pipeline includes **Tool 4 (Auto Testing)** which uses Claude to auto-generate pytest/jest test files and writes them to the `ai-delivery-outputs` repository. To trigger it manually:

```bash
# Via GitHub Actions UI: navigate to Actions → "Tool 4 — Auto Testing" → Run workflow
# Select mode: "generate" (create new tests) or "gap-analysis" (analyse coverage gaps)
```

---

## 7. Deployment

### Local / Development

```bash
docker-compose up --build
```

### CI/CD Workflows

The repository contains five GitHub Actions workflows that run automatically:

| Workflow | Trigger | Purpose |
|---|---|---|
| Tool 1 — Code Review | PR open/sync, Monday 08:00 UTC, manual | Claude reviews diffs, posts PR comments |
| Tool 2 — Tech Docs | Push to `main`, Sunday 06:00 UTC, manual | Generates README, architecture doc, runbook |
| Tool 3 — Business Docs | Version tag push (`v*`), manual | Generates solution overview and gap questionnaire |
| Tool 4 — Auto Testing | PR open/sync on `src/**`, Wednesday 07:00 UTC, manual | Generates or gap-analyses test files |
| Tool 5 — UAT | `release/*` branch creation, manual | Generates UAT test pack or analyses completed CSV results |

Required GitHub repository secrets for all CI workflows:

```
ANTHROPIC_API_KEY
GH_TOKEN
SENDGRID_API_KEY
```

### Production Deployment

[TODO: No IaC files (Terraform, Bicep, Kubernetes manifests) were found. How is this deployed to production? What cloud provider is targeted?]

---

## 8. Known Issues / TODOs

Extracted directly from code comments:

| Location | Issue / TODO |
|---|---|
| `backend/agent/graph.py` | **TODO:** Migrate Redis to an external service (e.g. Azure Cache for Redis, dedicated Redis container) so that memory persists across serverless backend instances. |
| `backend/modules/LLMS.py` | **TODO:** Add more LLM providers (`azure` and `openai` entries exist in the mapper but are set to `None` and not implemented). |
| `backend/main.py` | Lifespan handler (`asynccontextmanager`) is commented out — global `_agent` lifecycle management is not implemented; agents are rebuilt on every request. |
| `backend/main.py` | `_charts_sent` is a module-level in-process set; it will not work correctly across multiple backend instances or server restarts. |
| `docker-compose.yml` | PostgreSQL credentials (`chainlit`/`chainlit`) are hardcoded — should be moved to environment variables for production. |
| `.github/scripts/tool5_uat.py` | Escalation path section in generated runbooks always emits `[TODO: fill in team contacts]` — no team contact data is available. |
| `backend/modules/LLMS.py` | `gemini-3-flash-preview` model name — [TODO: verify this is the correct/current Gemini model identifier]. |
| General | No Terraform, Bicep, or other IaC files found — [TODO: how is the production environment provisioned?] |
| General | No `requirements.txt` or `pyproject.toml` found in provided files — [TODO: confirm Python dependency management approach]. |