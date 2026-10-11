# Underwriting Chatbot

## 1. Project Overview

This project is an AI-powered underwriting assistant chatbot that helps insurance underwriters assess customer risk profiles. It uses large language models (Claude and Gemini) orchestrated via LangGraph to run multi-specialist underwriting assessments, retrieve customer profiles, and identify similar customers. The system exposes a streaming FastAPI backend and a frontend chat interface, with a suite of GitHub Actions workflows for automated code review, documentation generation, and UAT facilitation.

---

## 2. Tech Stack

| Component | Technology | Version/Notes |
|---|---|---|
| Backend API | FastAPI | Python, with SSE streaming |
| Agent Orchestration | LangGraph | Multi-node state graph |
| LLM (primary) | Anthropic Claude | `claude-haiku-4-5-20251001` (fast), `claude-sonnet-4-20250514` (deep) |
| LLM (alternative) | Google Gemini | `gemini-3-flash-preview` |
| Memory / Checkpointing | Redis | `redis/redis-stack-server:7.2.0-v14` via LangGraph AsyncRedisSaver |
| Chat UI persistence | PostgreSQL | `postgres:16-alpine`, used by frontend (Chainlit) |
| Frontend | Chainlit | [TODO: confirm exact Chainlit version] |
| Risk Classification Model | CatBoostClassifier | Version 1.0, deployed 2024-06-01 |
| Customer Data | SQLite databases | `customer_profile.db`, `feature_importance.db`, `model_predictions.db`, `application_profile.db` |
| Customer Similarity | Pre-computed JSON | `backend/tmp/customer_similarity_dict.json` |
| Containerisation | Docker Compose | Multi-service orchestration |
| CI/CD & AI Workflows | GitHub Actions | Python 3.12, 5 automated tools |
| AI Workflow LLM | Anthropic Claude | `claude-sonnet-4-6` (shared.py) |
| Email notifications | SendGrid | Via `SENDGRID_API_KEY` |

---

## 3. Architecture

The system is composed of four runtime services managed by Docker Compose:

1. **Frontend** (Chainlit, port 8080): The chat UI that end users interact with. It connects to the backend over HTTP and stores conversation history in PostgreSQL.
2. **Backend** (FastAPI, port 8000): Receives chat messages via `POST /chat` and streams responses using Server-Sent Events (SSE). On each request it instantiates a LangGraph agent (`build_agent`), which is configured with a model and mode (`fast`/`deep`). The agent graph uses an `AsyncRedisSaver` checkpointer so conversation state persists across turns within a session.
3. **Redis** (port 6379): Provides the LangGraph checkpoint store for per-session agent memory.
4. **PostgreSQL** (port 5432): Stores Chainlit frontend session and message data.

The **agent** uses three tools:
- `get_customer_profile` – retrieves a customer record from the SQLite databases.
- `customer_lookalike` – looks up pre-computed similar customers from `customer_similarity_dict.json`.
- `run_underwriting_assessment` – spawns parallel specialist LLM calls (finance, health, life, etc.) then aggregates their outputs into a structured `UnderwritingReport` via a second LLM call with structured output.

The **GitHub Actions** CI layer contains five Python-based tools (`tool1`–`tool5`) that use the Claude API to perform automated code review, technical documentation generation, business documentation, test generation, and UAT facilitation. Their outputs are written to a separate `ai-delivery-outputs` GitHub repository.

```
┌─────────────┐        HTTP/SSE        ┌──────────────────────────────────────────┐
│  Frontend   │ ──────────────────────▶│  Backend (FastAPI)                       │
│  (Chainlit) │                        │  ┌─────────────────────────────────────┐ │
│  port 8080  │                        │  │  LangGraph Agent                    │ │
└──────┬──────┘                        │  │  ┌──────────────┐  ┌─────────────┐ │ │
       │ PostgreSQL                    │  │  │ get_profile  │  │  lookalike  │ │ │
       ▼                               │  │  └──────────────┘  └─────────────┘ │ │
┌─────────────┐                        │  │  ┌─────────────────────────────┐   │ │
│  PostgreSQL │                        │  │  │ run_underwriting_assessment  │   │ │
│  port 5432  │                        │  │  │  (parallel specialist LLMs)  │   │ │
└─────────────┘                        │  │  └─────────────────────────────┘   │ │
                                       │  └──────────────┬──────────────────────┘ │
                                       │                 │                         │
                                       └─────────────────┼─────────────────────────┘
                                                         │
                              ┌──────────────────────────┼───────────────┐
                              │                          │               │
                         ┌────▼────┐              ┌──────▼──────┐  ┌────▼──────┐
                         │  Redis  │              │  SQLite DBs │  │ Claude /  │
                         │ port    │              │  (customer  │  │ Gemini    │
                         │  6379   │              │   data)     │  │ APIs      │
                         └─────────┘              └─────────────┘  └───────────┘
```

---

## 4. Local Development Setup

### Prerequisites
- Docker and Docker Compose installed
- An Anthropic API key
- (Optional) A Google API key for Gemini

**Steps:**

1. **Clone the repository**

```bash
git clone https://github.com/kylodeng/underwriting_chatbot-main.git
cd underwriting_chatbot-main
```

2. **Create the environment file**

```bash
cp .env.example .env
```

[TODO: confirm whether `.env.example` exists in the repo]

3. **Populate `.env` with required secrets** (see [Environment Variables](#5-environment-variables))

```bash
# Example minimal .env
ANTHROPIC_API_KEY=sk-ant-...
GOOGLE_API_KEY=...        # optional, only needed for Gemini model
REDIS_HOST=redis
```

4. **Ensure the SQLite database files are present**

The backend expects the following files to be mounted:

```
database/customer_profile.db
database/feature_importance.db
database/model_predictions.db
database/application_profile.db
```

[TODO: confirm how these databases are provisioned — are they committed to the repo, generated by a script, or downloaded separately?]

5. **Build and start all services**

```bash
docker compose up --build
```

6. **Verify the backend is healthy**

```bash
curl http://localhost:8000/health
# Expected: {"status": "ok"}
```

7. **Open the chat UI**

Navigate to [http://localhost:8080](http://localhost:8080) in your browser.

---

### Running the backend standalone (without Docker)

1. **Install Python dependencies**

```bash
cd backend
pip install -r requirements.txt
```

[TODO: confirm `requirements.txt` exists and its exact path]

2. **Start Redis locally** (or point `REDIS_HOST` at an existing instance)

```bash
docker run -p 6379:6379 redis/redis-stack-server:7.2.0-v14
```

3. **Start the FastAPI server**

```bash
cd backend
uvicorn main:app --reload --port 8000
```

---

## 5. Environment Variables

### Backend (`.env` at repo root, loaded by `docker-compose.yml` and `backend/modules/LLMS.py`)

| Variable | Required | Default | Description |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | — | Anthropic API key for Claude models |
| `GOOGLE_API_KEY` | No | — | Google API key for Gemini models; required only if using `gemini` model |
| `REDIS_HOST` | No | `localhost` | Hostname of the Redis instance (use `redis` inside Docker Compose) |

### GitHub Actions workflows (repository secrets)

| Variable | Required | Default | Description |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | — | Claude API key used by all five AI workflow tools |
| `GH_TOKEN` | Yes | — | GitHub Personal Access Token with repo read/write permissions for output repo |
| `SENDGRID_API_KEY` | Yes | — | SendGrid API key for email notifications |
| `OUTPUT_REPO` | No | `ai-delivery-outputs` | Name of the GitHub repo where tool outputs are written |
| `OUTPUT_REPO_OWNER` | No | `github.repository_owner` | GitHub org/user that owns the output repo |
| `NOTIFY_EMAIL` | No | `kylo.deng@capco.com` | Recipient email address for notifications |
| `SENDER_EMAIL` | No | `kylo.deng@capco.com` | Sender email address for notifications |

---

## 6. Running Tests

[TODO: no test files or test runner configuration were found in the provided source files — are tests located in a directory not included here?]

The repository includes a GitHub Actions tool (`tool4_auto_testing.py`) that **generates** test files using Claude. To trigger it:

- **On PR open/sync** targeting `src/**`, `*.py`, `*.js`, or `*.ts` files — runs automatically.
- **Manual dispatch** via GitHub Actions → Tool 4 — Auto Testing:

```
Mode: generate   # generate new test stubs
Mode: gap-analysis  # analyse coverage gaps against existing tests
```

To run any manually generated test files locally (Python example):

```bash
cd backend
pip install pytest
pytest tests/
```

[TODO: confirm test output directory and whether pytest is listed as a dependency]

---

## 7. Deployment

### Local / development — Docker Compose

```bash
docker compose up --build -d
```

To stop:

```bash
docker compose down
```

### GitHub Actions CI workflows

The five automated tools are triggered as follows:

| Tool | Trigger |
|---|---|
| Tool 1 — Code Review | PR open/sync, Monday 08:00 UTC cron, manual dispatch |
| Tool 2 — Tech Documentation | Push to `main` (non-doc files), Sunday 06:00 UTC cron, manual dispatch |
| Tool 3 — Business Documentation | Push of a `v*` tag, manual dispatch with `project_name` and `release_version` inputs |
| Tool 4 — Auto Testing | PR open/sync on source files, Wednesday 07:00 UTC cron, manual dispatch |
| Tool 5 — UAT Facilitation | Creation of a `release/*` branch, manual dispatch |

All workflows require the following repository secrets to be set (see [Environment Variables](#5-environment-variables)):
`ANTHROPIC_API_KEY`, `GH_TOKEN`, `SENDGRID_API_KEY`

### Production deployment

[TODO: no IaC files (Terraform, Bicep, Helm, etc.) were found — how is the production environment provisioned and where is it hosted?]

[TODO: is there a container registry push step, and if so which registry is used?]

---

## 8. Known Issues / TODOs

The following items were extracted directly from code comments:

| Location | Issue / TODO |
|---|---|
| `backend/agent/graph.py` | **TODO:** Migrate Redis to an external service (e.g. Azure Cache for Redis, dedicated Redis container) so that memory persists across serverless backend instances |
| `backend/modules/LLMS.py` | **TODO:** Add more LLM providers (currently `azure` and `openai` entries exist in the mapper but are set to `None` and will raise `ValueError` if selected) |
| `backend/agent/agent_with_skills.py` | `agent_with_skills.py` and `graph.py` appear to define two separate agent-building approaches; it is unclear which is used in production |
| `backend/config.yml` | `specialist_max_tokens: 1500` — comment notes this was added because specialist LLMs were previously "hitting 2772" tokens; may need tuning |
| GitHub Actions `shared.py` | `send_email`, `email_html`, and `write_audit_entry` functions are imported by all tool scripts but their implementations are truncated in the provided files — completeness of `shared.py` should be verified |
| General | No IaC (Terraform/Bicep) files are present — production infrastructure provisioning is undocumented |
| General | No `requirements.txt` was provided — dependency management for the backend is undocumented |
| General | The `postgres/init.sql` file referenced in `docker-compose.yml` is not present in the provided files |
| General | The `model_card.json` is loaded in `prompts.py` but the `MODEL_CARD` variable is not used anywhere visible — purpose unclear |
| `backend/modules/LLMS.py` | Model name `gemini-3-flash-preview` does not correspond to a known released Gemini model name as of mid-2025; verify this is correct |