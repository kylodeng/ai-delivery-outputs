# Underwriting Chatbot

## 1. Project Overview

An AI-powered underwriting assistant that helps insurance underwriters assess customer risk profiles through a conversational chat interface. The system uses a multi-agent LangGraph pipeline to run parallel specialist assessments across categories such as finance, health, and life risk, then aggregates the results into a structured underwriting report. A suite of five GitHub Actions CI workflows (powered by Claude AI) automates code review, technical documentation, business documentation, test generation, and UAT facilitation for the repository itself.

---

## 2. Tech Stack

| Component | Technology | Version/Notes |
|---|---|---|
| Backend framework | FastAPI | Python, with SSE streaming |
| Agent orchestration | LangGraph | `StateGraph` with custom agent loop |
| LLM provider (primary) | Anthropic Claude | `claude-haiku-4-5-20251001` (fast), `claude-sonnet-4-20250514` (deep) |
| LLM provider (secondary) | Google Gemini | `gemini-3-flash-preview` |
| LangChain integrations | `langchain-anthropic`, `langchain-google-genai` | Used via `LLMS` wrapper class |
| Structured output | Pydantic v2 | `UnderwritingReport`, `AreaOfInterest`, etc. |
| Session memory / checkpointing | Redis | `redis-stack-server:7.2.0-v14` via `AsyncRedisSaver` |
| Frontend | [TODO: what framework is used in `./frontend`?] | Served on port 8080 |
| Database | SQLite (read-only) | `customer_profile.db`, `feature_importance.db`, `model_predictions.db`, `application_profile.db` |
| Auth / user persistence DB | PostgreSQL | `postgres:16-alpine`, used by Chainlit |
| Container orchestration | Docker Compose | `docker-compose.yml` |
| CI/CD | GitHub Actions | 5 automated workflows |
| AI workflow automation | Anthropic Claude (CI) | `claude-sonnet-4-6` via `shared.py` |
| Email notifications | SendGrid | Used in CI workflows |
| Risk model (offline) | CatBoostClassifier | Version 1.0, trained on merged customer dataset |

---

## 3. Architecture

The system is composed of four Docker services that interact as follows:

1. **Frontend** (port 8080) — the chat UI, built on Chainlit [TODO: confirm frontend framework]. It connects to the **Backend** over HTTP and persists user/session data in **PostgreSQL**.
2. **Backend** (port 8000) — a FastAPI application that exposes a `/chat` SSE endpoint. On each request it builds a LangGraph agent (`build_agent`) configured with three tools: `get_customer_profile`, `customer_lookalike`, and `run_underwriting_assessment`. The agent uses **Redis** (`AsyncRedisSaver`) for conversation checkpointing across turns.
3. **Redis** (port 6379) — `redis-stack-server` used exclusively for LangGraph conversation memory. The `graph.py` comment notes this should be migrated to an external managed service for serverless deployments.
4. **PostgreSQL** (port 5432) — stores Chainlit session/auth data, initialised from `./postgres/init.sql`.

The **assessment pipeline** (`assessment.py`) works as follows: when `run_underwriting_assessment` is called, it fans out up to 4 concurrent async calls (via `asyncio.Semaphore(4)`) to a specialist LLM for each assessment category (finance, health, life, etc.). The specialist results are then aggregated by a second LLM call that uses `structured_output` to produce a validated `UnderwritingReport` Pydantic object. The report is rendered and streamed back to the frontend as SSE events.

The **CI tooling** (`.github/scripts/`) runs in GitHub Actions and calls the Anthropic API directly via `shared.py` to perform code review, generate documentation, generate tests, and facilitate UAT — all independent of the running application.

---

## 4. Local Development Setup

1. **Clone the repository**

```bash
git clone https://github.com/kylodeng/underwriting_chatbot-main.git
cd underwriting_chatbot-main
```

2. **Create the root `.env` file** (see [Environment Variables](#5-environment-variables) below)

```bash
cp .env.example .env   # or create .env manually
```

3. **Ensure the SQLite database files are present** under `./database/`

```
database/
  customer_profile.db
  feature_importance.db
  model_predictions.db
  application_profile.db
```

[TODO: How are these database files generated or seeded? Is there a script?]

4. **Build and start all services with Docker Compose**

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
pip install -r requirements.txt   # [TODO: confirm requirements file name]
uvicorn main:app --reload --port 8000
```

---

## 5. Environment Variables

All variables are loaded from the root `.env` file (mounted into the `backend` container via `env_file: .env`) and from `backend/.env` for direct local runs.

| Variable | Required | Default | Description |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | — | Anthropic API key for Claude LLM calls (backend and CI) |
| `GOOGLE_API_KEY` | Yes (if using Gemini) | — | Google API key for Gemini model |
| `REDIS_HOST` | No | `localhost` | Redis hostname; set to `redis` automatically in Docker Compose |
| `GH_TOKEN` | Yes (CI only) | — | GitHub personal access token used by CI workflow scripts |
| `SENDGRID_API_KEY` | Yes (CI only) | — | SendGrid API key for CI email notifications |
| `OUTPUT_REPO` | No (CI only) | `ai-delivery-outputs` | GitHub repo name where CI tool outputs are written |
| `OUTPUT_REPO_OWNER` | No (CI only) | `GITHUB_REPOSITORY_OWNER` | Owner of the output repo |
| `NOTIFY_EMAIL` | No (CI only) | `kylo.deng@capco.com` | Recipient email for CI notifications |
| `SENDER_EMAIL` | No (CI only) | `kylo.deng@capco.com` | Sender email for CI notifications |

[TODO: Are there any additional variables required by the frontend service (e.g. auth secrets)?]

---

## 6. Running Tests

[TODO: No test files were found in the provided source. Is there a `tests/` directory? What test command should be used?]

The repository includes a GitHub Actions workflow (Tool 4 — Auto Testing, `.github/workflows/tool4_auto_testing.yml`) that uses Claude AI to **generate** pytest test files and perform coverage gap analysis. This workflow can be triggered manually:

1. Go to **Actions → Tool 4 — Auto Testing** in GitHub.
2. Click **Run workflow**.
3. Select mode: `generate` (create new tests) or `gap-analysis` (analyse existing coverage).

Generated test files are written to the `ai-delivery-outputs` repository.

To run any existing Python tests locally:

```bash
cd backend
pytest
```

---

## 7. Deployment

### Docker Compose (local / single-host)

```bash
docker compose up --build -d
```

To tear down:

```bash
docker compose down -v
```

### CI/CD Workflows

The following GitHub Actions workflows run automatically:

| Workflow | Trigger | Description |
|---|---|---|
| Tool 1 — Code Review | PR open/sync, every Monday 08:00 UTC, manual | Claude AI reviews PR diff and posts comments |
| Tool 2 — Tech Documentation | Push to `main`, every Sunday 06:00 UTC, manual | Generates README, ARCHITECTURE, and RUNBOOK |
| Tool 3 — Business Documentation | Push of `v*` tag, manual | Generates solution overview and gap questionnaire |
| Tool 4 — Auto Testing | PR open/sync on source files, every Wednesday 07:00 UTC, manual | Generates or analyses test coverage |
| Tool 5 — UAT Facilitation | `release/*` branch creation, manual | Generates UAT test pack or analyses completed results |

All workflows require the following secrets to be configured in the repository:

```
ANTHROPIC_API_KEY
GH_TOKEN
SENDGRID_API_KEY
```

### Triggering a Business Documentation release manually

```bash
git tag v1.0.0
git push origin v1.0.0
```

### Triggering UAT pack generation via branch

```bash
git checkout -b release/1.0.0
git push origin release/1.0.0
```

[TODO: Is there any cloud infrastructure (IaC) such as Terraform or Bicep for deploying this to Azure/AWS/GCP? No IaC files were found in the provided sources.]

---

## 8. Known Issues / TODOs

Extracted directly from code comments:

| Location | Issue / TODO |
|---|---|
| `backend/agent/graph.py` | **TODO:** Migrate Redis to an external service (e.g. Azure Cache for Redis, dedicated Redis container) so that memory persists across serverless backend instances |
| `backend/modules/LLMS.py` | **TODO:** Add more LLM providers — `azure` and `openai` entries exist in the model mapper but are set to `None` (not implemented) |
| `backend/agent/agent_with_skills.py` | Relationship between `agent_with_skills.py` and `graph.py` is unclear — both define agent-building functions; it is not evident which is used in production [TODO: clarify which agent entrypoint is active] |
| `backend/main.py` | Lifespan context manager (`@asynccontextmanager`) is commented out |
| `backend/main.py` | `_agent` is rebuilt on every request inside `generate()` rather than being initialised once — this may have performance implications |
| `backend/main.py` | `_extract_text` and chart buffering logic is truncated in the provided source [TODO: confirm complete streaming implementation] |
| CI scripts (`shared.py`) | `send_email`, `email_html`, and `write_audit_entry` functions are referenced by all five tool scripts but their implementations are truncated in the provided source |
| `backend/tmp/customer_similarity_dict.json` | Similarity data is stored as a static JSON file in `tmp/` — [TODO: should this be moved to a database or generated dynamically?] |
| `docker-compose.yml` | PostgreSQL credentials (`chainlit`/`chainlit`) are hardcoded — should be moved to secrets/environment variables for any non-local deployment |
| `backend/config.yml` | `specialist_max_tokens: 1500` — comment notes previous runs were hitting 2772 tokens; cap may truncate some specialist assessments |