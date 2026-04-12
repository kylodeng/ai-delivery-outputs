# Underwriting Chatbot

## 1. Project Overview

This is an AI-powered life insurance underwriting assistant that enables underwriters to assess customer risk profiles through a conversational chat interface. The system uses a multi-agent LangGraph pipeline to orchestrate parallel specialist LLM assessments across domains such as finance, health, and life risk, then aggregates the results into a structured underwriting report. A suite of five GitHub Actions CI/CD tools provides automated code review, technical documentation, business documentation, test generation, and UAT facilitation — all powered by Claude AI.

---

## 2. Tech Stack

| Component | Technology | Version/Notes |
|---|---|---|
| Backend framework | FastAPI | Python, with SSE streaming |
| Frontend | [TODO: what framework is the frontend built with?] | Served on port 8080 |
| Agent orchestration | LangGraph | `StateGraph` with tool-calling nodes |
| LLM — default/fast | Anthropic Claude Haiku | `claude-haiku-4-5-20251001` |
| LLM — standard | Anthropic Claude Sonnet | `claude-sonnet-4-20250514` |
| LLM — alternative | Google Gemini | `gemini-3-flash-preview` |
| LLM — CI tools | Anthropic Claude Sonnet | `claude-sonnet-4-6` (shared.py) |
| Session memory / checkpointing | Redis (redis-stack-server) | 7.2.0-v14, via LangGraph AsyncRedisSaver |
| Database | PostgreSQL | 16-alpine, used by frontend (Chainlit session storage) |
| Customer data | SQLite databases | `customer_profile.db`, `feature_importance.db`, `model_predictions.db`, `application_profile.db` |
| Risk model | CatBoostClassifier | v1.0, trained on merged customer datasets |
| CI/CD automation | GitHub Actions | 5 workflows (code review, tech docs, business docs, auto testing, UAT) |
| Email notifications | SendGrid | Via `SENDGRID_API_KEY` |
| Containerisation | Docker / Docker Compose | Multi-service stack |
| Python version | Python | 3.12 (CI), 3.x (backend) |
| Config | YAML + JSON | `config.yml`, `model_card.json`, `assessment_criterias.json` |

---

## 3. Architecture

The system is composed of four Docker services that interact as follows:

1. **Frontend** (port 8080) — serves the chat UI (built on Chainlit, inferred from PostgreSQL `chainlit` database and `DATABASE_URL`). It connects to the **Backend** over HTTP and uses **PostgreSQL** for persistent session/conversation storage.

2. **Backend** (port 8000) — a FastAPI application that exposes `/health` and `/chat` (Server-Sent Events stream). On each chat request it:
   - Instantiates a LangGraph agent (`build_agent`) configured with the requested model and mode.
   - The agent decides which tools to call: `get_customer_profile` (SQLite lookup), `customer_lookalike` (similarity dictionary lookup), and `run_underwriting_assessment`.
   - `run_underwriting_assessment` fans out up to 4 parallel async calls to specialist LLMs (one per assessment category: finance, health, life, etc.), then aggregates them via a structured-output aggregator LLM into an `UnderwritingReport` Pydantic model.
   - Agent state and conversation history are checkpointed to **Redis** via `AsyncRedisSaver`, keyed by `session_id`.
   - Streaming events (tool start/end, LLM tokens, thinking blocks, charts) are forwarded to the frontend as SSE events.

3. **Redis** (port 6379) — stores LangGraph conversation checkpoints so that multi-turn conversations maintain state. Currently runs as a local container.

4. **PostgreSQL** (port 5432) — stores frontend session data, initialised via `postgres/init.sql`.

The five **GitHub Actions workflows** run independently of the Docker stack. They read source files from the repository, call the Anthropic Claude API, and write outputs (reports, docs, generated tests) to a separate `ai-delivery-outputs` GitHub repository, optionally sending email notifications via SendGrid.

```
User
 │
 ▼
Frontend (Chainlit, :8080)
 │  HTTP + SSE
 ▼
Backend / FastAPI (:8000)
 │
 ├──► Redis (:6379)          [conversation checkpoints]
 ├──► PostgreSQL (:5432)     [session storage, via frontend]
 ├──► SQLite DBs             [customer profiles, predictions]
 └──► Anthropic / Google APIs [LLM calls]
          │
          ├── Agent LLM (Haiku / Sonnet)
          ├── Specialist LLMs × N (parallel, tagged "thinking")
          └── Aggregator LLM → UnderwritingReport
```

---

## 4. Local Development Setup

### Prerequisites

- Docker and Docker Compose installed
- An `.env` file in the repo root (see [Environment Variables](#5-environment-variables))
- SQLite database files present under `./database/`

### Steps

1. **Clone the repository**

```bash
git clone https://github.com/kylodeng/underwriting_chatbot-main.git
cd underwriting_chatbot-main
```

2. **Create the environment file**

```bash
cp .env.example .env   # or create .env manually — see Environment Variables section
```

3. **Start all services**

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

6. **(Optional) Run the backend locally without Docker**

```bash
cd backend
pip install -r requirements.txt   # [TODO: confirm requirements.txt filename and location]
uvicorn main:app --reload --port 8000
```

> [TODO: Is there a `requirements.txt` or `pyproject.toml` in `backend/`? The Dockerfiles are not included in the provided files.]

---

## 5. Environment Variables

The backend reads from a `.env` file at `backend/.env` (loaded via `python-dotenv`). The Docker Compose stack reads from `.env` at the repo root via `env_file: .env`.

| Variable | Required | Default | Description |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | — | API key for Anthropic Claude (used by backend LLMs and all CI scripts) |
| `GOOGLE_API_KEY` | Yes (if using Gemini) | — | API key for Google Generative AI (Gemini models) |
| `REDIS_HOST` | No | `localhost` | Hostname of the Redis server; set to `redis` in Docker Compose |
| `GH_TOKEN` | Yes (CI only) | — | GitHub personal access token used by GitHub Actions workflows to read repos and write to `ai-delivery-outputs` |
| `SENDGRID_API_KEY` | Yes (CI only) | — | SendGrid API key for email notifications from CI workflows |
| `OUTPUT_REPO` | No (CI only) | `ai-delivery-outputs` | Name of the GitHub repo where CI tool outputs are written |
| `OUTPUT_REPO_OWNER` | No (CI only) | `GITHUB_REPOSITORY_OWNER` | Owner of the output repo |
| `NOTIFY_EMAIL` | No (CI only) | `kylo.deng@capco.com` | Recipient email address for CI notifications |
| `SENDER_EMAIL` | No (CI only) | `kylo.deng@capco.com` | Sender email address for CI notifications |
| `DATABASE_URL` | Yes (frontend) | — | PostgreSQL connection string for Chainlit session storage, e.g. `postgresql+asyncpg://chainlit:chainlit@postgres:5432/chainlit` |
| `BACKEND_URL` | Yes (frontend) | — | URL the frontend uses to reach the backend, e.g. `http://backend:8000` |

> [TODO: Are there additional variables required by `modules/tools.py` (e.g. database paths for SQLite files)?]

---

## 6. Running Tests

> [TODO: No test files or test runner configuration were found in the provided source files. Are there existing tests under a `tests/` directory?]

The repository includes a **GitHub Actions workflow (Tool 4)** that automatically generates pytest test files using Claude AI. Generated tests are written to the `ai-delivery-outputs` repository and are not run in-place.

To trigger test generation manually:

1. Go to **Actions → Tool 4 — Auto Testing** in the GitHub UI.
2. Select **Run workflow**, choose mode `generate` or `gap-analysis`, and click **Run workflow**.

To run any generated tests locally once retrieved:

```bash
cd backend
pip install pytest
pytest tests/   # [TODO: confirm test directory path]
```

---

## 7. Deployment

### Local / Development — Docker Compose

```bash
docker compose up --build -d
```

To stop:

```bash
docker compose down
```

To stop and remove volumes:

```bash
docker compose down -v
```

### Production

> [TODO: No Infrastructure-as-Code files (Terraform, Bicep, Helm charts, Kubernetes manifests) were found in the provided files. How is this application deployed to production?]

> [TODO: Is there a cloud target (Azure, AWS, GCP)? The `LLMS.py` file has an `"azure": None` placeholder suggesting Azure is a future target.]

### GitHub Actions CI Workflows

All five tools are triggered automatically or can be run manually:

| Workflow | Trigger | Manual dispatch |
|---|---|---|
| Tool 1 — Code Review | PR open/sync, Monday 08:00 UTC | Yes — choose `repo` or `pr` mode |
| Tool 2 — Tech Docs | Push to `main`, Sunday 06:00 UTC | Yes |
| Tool 3 — Business Docs | Push of `v*` tag, | Yes — provide `project_name` and `release_version` |
| Tool 4 — Auto Testing | PR open/sync on source files, Wednesday 07:00 UTC | Yes — choose `generate` or `gap-analysis` |
| Tool 5 — UAT | `release/*` branch creation | Yes — choose `generate` or `analyse` mode |

Required GitHub repository secrets for CI workflows:

```
ANTHROPIC_API_KEY
GH_TOKEN
SENDGRID_API_KEY
```

---

## 8. Known Issues / TODOs

The following are extracted directly from code comments and configuration:

| Location | Issue / TODO |
|---|---|
| `backend/agent/graph.py` | `# TODO: migrate Redis to an external service (e.g. Azure Cache for Redis, dedicated Redis container) so that memory persists across serverless backend instances.` |
| `backend/modules/LLMS.py` | `# TODO: add more providers here` — `"azure": None` and `"openai": None` are defined but not implemented |
| `backend/main.py` | Lifespan context manager (`@asynccontextmanager async def lifespan`) is commented out — application lifecycle management is incomplete |
| `backend/main.py` | `_charts_sent` deduplication set is process-local; will not work correctly across multiple backend instances or restarts |
| `backend/agent/agent_with_skills.py` | Agent builds a new LLM and reloads all skill files on every invocation — no caching |
| `docker-compose.yml` | Redis runs as a local container with no persistence volume; conversation checkpoints will be lost on Redis restart |
| `.github/scripts/shared.py` | `send_email`, `email_html`, and `write_audit_entry` functions are imported by all tool scripts but their implementations are truncated in the provided files — [TODO: confirm these functions are fully implemented in `shared.py`] |
| `tool2_tech_docs.py` | `build_index` function is truncated — references `{r` (incomplete f-string) |
| `tool4_auto_testing.py` | `build_test_report` function is truncated |
| `tool5_uat.py` | `parse_scenarios` and `build_test_pack_csv` functions are truncated |
| General | No DR (disaster recovery) or multi-region strategy evident |
| General | No monitoring or alerting configuration found |
| General | CORS is configured as `allow_origins=["*"]` — should be restricted in production |