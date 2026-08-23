# Underwriting Chatbot

## 1. Project Overview

An AI-powered underwriting assistant that helps insurance underwriters assess customer risk profiles through a conversational chat interface. The system orchestrates multiple specialist LLM agents to evaluate finance, health, and life risk categories in parallel, then aggregates their outputs into a structured `UnderwritingReport`. A suite of five GitHub Actions CI/CD tools (code review, tech docs, business docs, auto-testing, and UAT facilitation) is also included, each powered by Claude AI.

---

## 2. Tech Stack

| Component | Technology | Version/Notes |
|---|---|---|
| Backend API | FastAPI | Python, Server-Sent Events via `sse_starlette` |
| LLM Orchestration | LangGraph + LangChain | `StateGraph`, streaming events v2 |
| Primary LLM (fast) | Anthropic Claude Haiku | `claude-haiku-4-5-20251001` |
| Primary LLM (deep) | Anthropic Claude Sonnet | `claude-sonnet-4-20250514` |
| Secondary LLM | Google Gemini | `gemini-3-flash-preview` |
| Risk Classification Model | CatBoostClassifier | v1.0, trained on merged customer/KYC/application profiles |
| Session Memory / Checkpointing | Redis (LangGraph Redis Saver) | `redis/redis-stack-server:7.2.0-v14` |
| Customer Data | SQLite databases | `customer_profile.db`, `feature_importance.db`, `model_predictions.db`, `application_profile.db` |
| Frontend | [TODO: what framework is the frontend built with?] | Port 8080 |
| Database (Frontend Auth/State) | PostgreSQL | `postgres:16-alpine`, via `asyncpg` |
| Containerisation | Docker Compose | Multi-service stack |
| CI/CD AI Tools | GitHub Actions + Anthropic Claude | `claude-sonnet-4-6`, Python 3.12 |
| Email Notifications | SendGrid | Via `SENDGRID_API_KEY` |
| Environment Config | python-dotenv | `.env` file |

---

## 3. Architecture

The system is composed of four Docker services that interact as follows:

1. **Frontend** (port 8080) presents the chat UI to the underwriter. It communicates with the **Backend** over HTTP, reading streamed responses via Server-Sent Events (SSE).
2. **Backend** (port 8000) is a FastAPI application. On each `/chat` request it builds a LangGraph agent, which:
   - Calls tool `get_customer_profile` to retrieve customer data from SQLite databases.
   - Calls `run_underwriting_assessment` which fans out parallel async calls to specialist LLMs (finance, health, life categories), then aggregates results into a structured `UnderwritingReport` using a second LLM with structured output.
   - Calls `customer_lookalike` to find similar customers.
   - Streams partial text tokens and tool-start/end events back to the frontend as SSE.
3. **Redis** (port 6379) stores LangGraph conversation checkpoints, providing multi-turn session memory.
4. **PostgreSQL** (port 5432) is used by the frontend for auth/state persistence (initialised via `postgres/init.sql`).

Five **GitHub Actions workflows** run independently of the application stack, each invoking Claude via the Anthropic API to perform: automated code review, technical documentation generation, business documentation generation, test generation/gap analysis, and UAT test pack generation/analysis. Results are written to a separate `ai-delivery-outputs` GitHub repository.

```
Underwriter
    │
    ▼
Frontend (8080)
    │ HTTP / SSE
    ▼
Backend FastAPI (8000)
    │
    ├── LangGraph Agent
    │       ├── get_customer_profile ──► SQLite DBs
    │       ├── customer_lookalike   ──► customer_similarity_dict.json
    │       └── run_underwriting_assessment
    │               ├── Specialist LLM (finance) ─┐
    │               ├── Specialist LLM (health)  ─┼─► Aggregator LLM ──► UnderwritingReport
    │               └── Specialist LLM (life)    ─┘
    │
    ├── Redis (6379)  ◄── session checkpoints
    └── PostgreSQL (5432) ◄── frontend state
```

---

## 4. Local Development Setup

**Prerequisites:** Docker, Docker Compose, Python 3.12, Git.

1. **Clone the repository**

```bash
git clone https://github.com/kylodeng/underwriting_chatbot-main.git
cd underwriting_chatbot-main
```

2. **Create the root `.env` file** (used by the backend service via `env_file: .env`):

```bash
cp .env.example .env   # [TODO: confirm whether .env.example exists]
# Then edit .env and fill in the required variables (see Environment Variables section)
```

3. **Ensure SQLite database files are present** under `./database/`:

```
database/
  customer_profile.db
  feature_importance.db
  model_predictions.db
  application_profile.db
```

[TODO: How are these database files generated or seeded? Is there a script to create them?]

4. **Start all services with Docker Compose**

```bash
docker compose up --build
```

5. **Verify the backend is healthy**

```bash
curl http://localhost:8000/health
# Expected: {"status": "ok"}
```

6. **Access the frontend**

Open [http://localhost:8080](http://localhost:8080) in your browser.

7. **(Optional) Run the backend directly for development** (outside Docker):

```bash
cd backend
pip install -r requirements.txt   # [TODO: confirm requirements.txt exists]
uvicorn main:app --reload --port 8000
```

---

## 5. Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | — | Anthropic API key for Claude LLMs (backend + CI tools) |
| `GOOGLE_API_KEY` | Yes (if using Gemini) | — | Google API key for Gemini models |
| `REDIS_HOST` | No | `localhost` | Redis hostname; set to `redis` when running via Docker Compose |
| `GH_TOKEN` | Yes (CI only) | — | GitHub personal access token for CI workflow scripts |
| `SENDGRID_API_KEY` | Yes (CI only) | — | SendGrid API key for email notifications from CI tools |
| `OUTPUT_REPO` | No (CI only) | `ai-delivery-outputs` | GitHub repo name where CI tool outputs are written |
| `OUTPUT_REPO_OWNER` | No (CI only) | `GITHUB_REPOSITORY_OWNER` | Owner of the output repo |
| `NOTIFY_EMAIL` | No (CI only) | `kylo.deng@capco.com` | Recipient email for CI tool notifications |
| `SENDER_EMAIL` | No (CI only) | `kylo.deng@capco.com` | Sender email for CI tool notifications |
| `BACKEND_URL` | No (frontend) | `http://backend:8000` | URL the frontend uses to reach the backend |
| `DATABASE_URL` | No (frontend) | `postgresql+asyncpg://chainlit:chainlit@postgres:5432/chainlit` | PostgreSQL connection string for the frontend |
| `POSTGRES_USER` | No | `chainlit` | PostgreSQL username (set in docker-compose.yml) |
| `POSTGRES_PASSWORD` | No | `chainlit` | PostgreSQL password (set in docker-compose.yml) |
| `POSTGRES_DB` | No | `chainlit` | PostgreSQL database name (set in docker-compose.yml) |

---

## 6. Running Tests

[TODO: Are there any existing test files in the repository? No test files were found in the provided source. The CI pipeline (Tool 4) auto-generates tests via Claude and writes them to the `ai-delivery-outputs` repo — what framework do those generated tests use for this project?]

To trigger AI-generated test generation manually via GitHub Actions:

1. Go to **Actions → Tool 4 — Auto Testing** in the GitHub UI.
2. Click **Run workflow**, select mode `generate` or `gap-analysis`, and run.
3. Generated test files are written to the `ai-delivery-outputs` repository.

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

### GitHub Actions CI Tools

The five AI delivery tools run automatically based on their triggers, or can be dispatched manually:

| Tool | Workflow File | Auto-trigger |
|---|---|---|
| Code Review | `tool1_code_review.yml` | PR open/sync; Monday 08:00 UTC |
| Tech Documentation | `tool2_tech_docs.yml` | Push to `main`; Sunday 06:00 UTC |
| Business Documentation | `tool3_business_docs.yml` | Push of `v*` tag |
| Auto Testing | `tool4_auto_testing.yml` | PR open/sync on `.py/.js/.ts`; Wednesday 07:00 UTC |
| UAT Facilitation | `tool5_uat.yml` | Creation of `release/*` branch |

Required GitHub repository secrets for CI tools:

```
ANTHROPIC_API_KEY
GH_TOKEN
SENDGRID_API_KEY
```

To trigger manually:
1. Go to **Actions** in the GitHub UI.
2. Select the desired workflow.
3. Click **Run workflow** and fill in any required inputs.

[TODO: Is there a cloud deployment target (e.g. Azure, AWS, GCP)? No IaC files (Terraform, Bicep, etc.) were found in the provided sources.]

---

## 8. Known Issues / TODOs

Extracted from code comments and prompts:

| Location | Issue / TODO |
|---|---|
| `backend/agent/graph.py` | `# TODO: migrate Redis to an external service (e.g. Azure Cache for Redis, dedicated Redis container) so that memory persists across serverless backend instances.` |
| `backend/modules/LLMS.py` | `# TODO: add more providers here` — `azure` and `openai` entries in `model_mapper` are set to `None` and will raise `ValueError` if selected. |
| `backend/main.py` | `lifespan` function is commented out — application lifecycle management (startup/shutdown hooks) is not wired up. |
| `backend/agent/agent_with_skills.py` | `agent_with_skills.py` defines its own `TOOLS` dict and graph independently of `agent/graph.py`; it is unclear which agent implementation is actually used by `main.py` at runtime. [TODO: Which agent module (`agent_with_skills.py` or `graph.py`) is the active one?] |
| `docker-compose.yml` | PostgreSQL is initialised from `./postgres/init.sql` — [TODO: confirm this file exists in the repo]. |
| `backend/modules/LLMS.py` | `gemini-3-flash-preview` is referenced as the Gemini model; this appears to be a preview/unreleased model identifier — [TODO: verify this model name is correct and available]. |
| CI Tool 3 (`tool3_business_docs.py`) | Stakeholder names, dates, and go-live milestones are always emitted as `[TODO]` — a human must complete them after generation. |
| CI Tool 5 (`tool5_uat.yml`) | Escalation path in generated runbooks is always `[TODO: fill in team contacts]`. |
| General | No test files were found in the repository; test coverage is 0% until Tool 4 generates them. |