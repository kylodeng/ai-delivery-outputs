# Underwriting Chatbot

## 1. Project Overview

An AI-powered underwriting assistant that helps insurance underwriters assess customer risk profiles through a conversational chat interface. The system orchestrates multiple specialist LLM agents to perform parallel assessments across finance, health, life, and other underwriting domains, then aggregates results into a structured risk classification report. A suite of five GitHub Actions workflows provide automated code review, documentation generation, test generation, and UAT facilitation using Claude AI.

---

## 2. Tech Stack

| Component | Technology | Version/Notes |
|---|---|---|
| Backend API | FastAPI | Python, SSE streaming via `sse_starlette` |
| Agent Framework | LangGraph + LangChain | Custom `StateGraph` with tool-calling loop |
| Primary LLM (fast) | Anthropic Claude Haiku | `claude-haiku-4-5-20251001` |
| Primary LLM (deep) | Anthropic Claude Sonnet | `claude-sonnet-4-20250514` |
| Alternative LLM | Google Gemini | `gemini-3-flash-preview` |
| Risk Model | CatBoostClassifier | v1.0, trained on merged customer datasets |
| Session Memory | Redis | `redis/redis-stack-server:7.2.0-v14` |
| Frontend DB | PostgreSQL | `postgres:16-alpine`, used by Chainlit |
| Frontend | Chainlit | [TODO: confirm Chainlit version] |
| Customer Data | SQLite | `.db` files mounted read-only into backend |
| Containerisation | Docker Compose | Multi-service: redis, postgres, backend, frontend |
| CI/CD | GitHub Actions | 5 AI-powered delivery workflows |
| CI LLM | Anthropic Claude Sonnet | `claude-sonnet-4-6` (workflows only) |
| Email | SendGrid | Notification delivery from workflows |

---

## 3. Architecture

The system is composed of four Docker services that interact as follows:

1. **Frontend** (Chainlit, port 8080) receives underwriter messages and streams them to the **Backend** via HTTP POST to `/chat`, consuming Server-Sent Events (SSE) for streaming responses.
2. **Backend** (FastAPI, port 8000) builds a LangGraph agent per request. The agent uses a tool-calling loop: it calls `get_customer_profile` to fetch data from SQLite databases, `customer_lookalike` to find similar customers, and `run_underwriting_assessment` to trigger parallel specialist LLM calls.
3. **Assessment** runs up to 4 concurrent specialist LLM calls (finance, health, life, and other categories defined in `assessment_criterias.json`), each capped at 1,500 tokens. An aggregator LLM (8,000 token budget) then synthesises results into a structured `UnderwritingReport` Pydantic model, which is rendered and streamed back.
4. **Redis** (port 6379) stores LangGraph checkpoint state, enabling conversation memory across turns within a session. **PostgreSQL** (port 5432) is used by the Chainlit frontend for its own persistence.
5. Five **GitHub Actions workflows** run independently of the application, using Claude AI to automate code review, technical documentation, business documentation, test generation, and UAT facilitation. Outputs are written to a separate GitHub repository (`ai-delivery-outputs`).

```
Underwriter
    │
    ▼
[Frontend / Chainlit :8080]
    │  HTTP + SSE
    ▼
[Backend / FastAPI :8000]
    │
    ├──► LangGraph Agent (tool-calling loop)
    │        ├──► get_customer_profile    ──► SQLite DBs
    │        ├──► customer_lookalike      ──► customer_similarity_dict.json
    │        └──► run_underwriting_assessment
    │                  ├──► Specialist LLMs (×N, concurrent, tagged "thinking")
    │                  └──► Aggregator LLM  (structured output → UnderwritingReport)
    │
    ├──► Redis :6379  (LangGraph checkpointer / session memory)
    └──► PostgreSQL :5432  (Chainlit persistence)
```

---

## 4. Local Development Setup

**Prerequisites:** Docker, Docker Compose, Python 3.12, a `.env` file (see [Environment Variables](#5-environment-variables)).

1. **Clone the repository**
   ```bash
   git clone https://github.com/kylodeng/underwriting_chatbot-main.git
   cd underwriting_chatbot-main
   ```

2. **Create the root `.env` file** (referenced by `docker-compose.yml` and `backend/main.py`)
   ```bash
   cp .env.example .env   # if an example exists, otherwise create manually
   # Then populate with required values — see Environment Variables section
   ```

3. **Start all services with Docker Compose**
   ```bash
   docker compose up --build
   ```

4. **Verify the backend is healthy**
   ```bash
   curl http://localhost:8000/health
   # Expected: {"status": "ok"}
   ```

5. **Access the frontend**
   Open `http://localhost:8080` in your browser.

6. **(Optional) Run the backend locally without Docker** — requires Redis running separately
   ```bash
   cd backend
   pip install -r requirements.txt   # [TODO: confirm requirements.txt filename/location]
   uvicorn main:app --reload --port 8000
   ```

---

## 5. Environment Variables

The root `.env` file is loaded by Docker Compose (`env_file: .env`) and by the backend (`load_dotenv`). GitHub Actions workflows read secrets from the repository's GitHub Secrets.

| Variable | Required | Default | Description |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | — | Anthropic API key for Claude LLM calls (backend + CI workflows) |
| `GOOGLE_API_KEY` | Yes (if using Gemini) | — | Google API key for Gemini model access |
| `GH_TOKEN` | Yes (CI only) | — | GitHub PAT used by workflow scripts to read repos and write outputs |
| `SENDGRID_API_KEY` | Yes (CI only) | — | SendGrid API key for workflow email notifications |
| `REDIS_HOST` | No | `localhost` | Redis hostname; set to `redis` inside Docker Compose |
| `OUTPUT_REPO` | No (CI only) | `ai-delivery-outputs` | GitHub repo name where workflow outputs are written |
| `OUTPUT_REPO_OWNER` | No (CI only) | `GITHUB_REPOSITORY_OWNER` | Owner of the output repo |
| `NOTIFY_EMAIL` | No (CI only) | `kylo.deng@capco.com` | Recipient email for workflow notifications |
| `SENDER_EMAIL` | No (CI only) | `kylo.deng@capco.com` | Sender address for workflow emails |

> **Note:** `DATABASE_URL` for the frontend is hardcoded in `docker-compose.yml` as `postgresql+asyncpg://chainlit:chainlit@postgres:5432/chainlit`. PostgreSQL credentials (`POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`) are also set directly in `docker-compose.yml`.

---

## 6. Running Tests

[TODO: Are there any existing test files in the repository? No test directory or test runner configuration was found in the provided files.]

The CI workflow (`tool4_auto_testing.yml`) can auto-generate pytest test files for Python source files and write them to the `ai-delivery-outputs` repository. To trigger it manually:

1. Go to **Actions → Tool 4 — Auto Testing** in GitHub.
2. Select `workflow_dispatch` and choose mode `generate` or `gap-analysis`.
3. Generated test files will be committed to the output repo.

---

## 7. Deployment

### Local / Development
```bash
docker compose up --build
```

### Production
[TODO: Is there a Kubernetes manifest, Helm chart, or cloud IaC (Terraform/Bicep) for production deployment? None was found in the provided files.]

### GitHub Actions Workflows

All five workflows can be triggered manually from **Actions** in GitHub, in addition to their automatic triggers:

| Workflow | Automatic Trigger | Manual Trigger |
|---|---|---|
| Tool 1 — Code Review | PR open/sync; Monday 08:00 UTC | `workflow_dispatch` (mode: `repo` or `pr`) |
| Tool 2 — Tech Docs | Push to `main`; Sunday 06:00 UTC | `workflow_dispatch` |
| Tool 3 — Business Docs | Push of `v*` tag | `workflow_dispatch` (project name + version) |
| Tool 4 — Auto Testing | PR open/sync on `src/**`, `*.py`, `*.js`, `*.ts`; Wednesday 07:00 UTC | `workflow_dispatch` (mode: `generate` or `gap-analysis`) |
| Tool 5 — UAT | Creation of `release/*` branch | `workflow_dispatch` (mode: `generate` or `analyse`) |

Required GitHub Secrets for all workflows: `ANTHROPIC_API_KEY`, `GH_TOKEN`, `SENDGRID_API_KEY`.

---

## 8. Known Issues / TODOs

Extracted directly from code comments:

- **Redis persistence** (`backend/agent/graph.py`): Redis is currently run as a local container. It should be migrated to an external managed service (e.g. Azure Cache for Redis, dedicated Redis container) so that memory persists across serverless backend instances.
- **Additional LLM providers** (`backend/modules/LLMS.py`): `azure` and `openai` providers are defined in the model mapper but set to `None` — they are not yet implemented.
- **LLM provider expansion** (`backend/modules/LLMS.py`): Comment `# TODO: add more providers here` indicates planned additions.
- **Frontend lifespan handler** (`backend/main.py`): The `lifespan` async context manager is commented out — agent initialisation lifecycle is not managed at startup.
- **Escalation contacts** (CI runbooks generated by Tool 2): Runbook template includes `[TODO: fill in team contacts]` for escalation path.
- **Postgres `init.sql`** (`docker-compose.yml`): References `./postgres/init.sql` — [TODO: confirm this file exists in the repository].
- **Backend `requirements.txt`**: [TODO: confirm the name and location of the Python dependency file for the backend].
- **Production IaC**: No Terraform, Bicep, or Kubernetes manifests were found — [TODO: is there a separate infrastructure repository?].
- **Model card deployment**: `model_card.json` references a `CatBoostClassifier` with `deployment_date: 2024-06-01` — [TODO: where is the trained model artifact stored and how is it loaded at runtime?].