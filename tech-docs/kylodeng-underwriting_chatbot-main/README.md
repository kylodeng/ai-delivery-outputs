# Underwriting Chatbot

## 1. Project Overview

An AI-powered underwriting assistant that helps insurance underwriters assess customer risk profiles through a conversational chat interface. The system uses a multi-agent LangGraph pipeline to run parallel specialist assessments across domains such as finance, health, and life risk, then aggregates the results into a structured underwriting report. A suite of five GitHub Actions–driven AI workflows (code review, technical docs, business docs, auto-testing, and UAT facilitation) are also included to support the software delivery lifecycle.

---

## 2. Tech Stack

| Component | Technology | Version/Notes |
|---|---|---|
| Backend API | FastAPI | Python, streamed via SSE |
| Agent Framework | LangGraph + LangChain | Multi-node stateful graph |
| LLM – Default (fast) | Claude Haiku | `claude-haiku-4-5-20251001` via `langchain-anthropic` |
| LLM – Full (deep) | Claude Sonnet | `claude-sonnet-4-20250514` via `langchain-anthropic` |
| LLM – Alternative | Gemini Flash | `gemini-3-flash-preview` via `langchain-google-genai` |
| Agent Memory / Checkpointing | Redis | `redis/redis-stack-server:7.2.0-v14` via `AsyncRedisSaver` |
| Frontend | Chainlit | Connects to backend over HTTP |
| Database (customer data) | SQLite | `.db` files mounted read-only into backend |
| Database (Chainlit session) | PostgreSQL | `postgres:16-alpine` |
| Containerisation | Docker Compose | `docker-compose.yml` at repo root |
| Risk model metadata | CatBoostClassifier model card | `model_card.json` (feature importances, version 1.0) |
| CI/CD AI Tools | GitHub Actions + Claude Sonnet (`claude-sonnet-4-6`) | 5 automated delivery workflows |
| Email notifications | SendGrid | Via `SENDGRID_API_KEY` |

---

## 3. Architecture

```
┌─────────────┐        HTTP/SSE        ┌──────────────────────────────────────────┐
│   Frontend  │ ─────────────────────► │  FastAPI Backend  (:8000)                │
│  (Chainlit) │                        │                                          │
└─────────────┘                        │  /chat  → build_agent()                  │
                                       │           ↓                              │
                                       │  LangGraph Agent Graph                   │
                                       │    ├─ get_customer_profile (tool)        │
                                       │    ├─ customer_lookalike (tool)          │
                                       │    └─ run_underwriting_assessment (tool) │
                                       │         ↓ parallel specialist LLM calls  │
                                       │    [finance | health | life | ...]        │
                                       │         ↓ aggregator LLM                 │
                                       │    UnderwritingReport (Pydantic)         │
                                       └──────┬───────────────────────────────────┘
                                              │
                              ┌───────────────┼────────────────┐
                              ▼               ▼                ▼
                           Redis           PostgreSQL       SQLite DBs
                        (checkpoints)   (Chainlit sessions) (customer/
                                                             model data)
```

**Data flow summary:**

1. The user sends a message from the Chainlit frontend to the FastAPI `/chat` endpoint.
2. FastAPI streams the response back using Server-Sent Events (SSE).
3. The LangGraph agent decides which tool(s) to call, one at a time.
4. `get_customer_profile` looks up the customer in SQLite databases; `customer_lookalike` returns similar customer IDs from a pre-computed JSON dictionary.
5. `run_underwriting_assessment` fans out parallel async LLM calls to specialist agents (finance, health, life, etc.), capped by a semaphore (concurrency=4).
6. An aggregator LLM consolidates specialist outputs into a structured `UnderwritingReport` Pydantic model.
7. Conversation state (thread checkpoints) is persisted in Redis so sessions survive across turns.
8. Chainlit session data is stored in PostgreSQL.

---

## 4. Local Development Setup

### Prerequisites

- Docker and Docker Compose installed
- An `.env` file at the repo root (see [Environment Variables](#5-environment-variables))

### Steps

1. **Clone the repository**

```bash
git clone https://github.com/kylodeng/underwriting_chatbot-main.git
cd underwriting_chatbot-main
```

2. **Create the environment file**

```bash
cp .env.example .env   # if an example exists, otherwise create .env manually
```

Populate `.env` with at minimum `ANTHROPIC_API_KEY` and `GOOGLE_API_KEY` (see the table below).

3. **Start all services via Docker Compose**

```bash
docker compose up --build
```

This will start:
- `redis` on port `6379`
- `postgres` on port `5432`
- `backend` (FastAPI) on port `8000`
- `frontend` (Chainlit) on port `8080`

4. **Verify the backend is healthy**

```bash
curl http://localhost:8000/health
# Expected: {"status": "ok"}
```

5. **Open the chat UI**

Navigate to [http://localhost:8080](http://localhost:8080) in your browser.

6. **(Optional) Run the backend directly without Docker**

```bash
cd backend
pip install -r requirements.txt   # [TODO: confirm requirements file name/location]
uvicorn main:app --reload --port 8000
```

> **Note:** You must have Redis running locally on port `6379` and set `REDIS_HOST=localhost` (the default) when running outside Docker.

---

## 5. Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | — | API key for Anthropic Claude models |
| `GOOGLE_API_KEY` | Yes (if using Gemini) | — | API key for Google Generative AI (Gemini) |
| `REDIS_HOST` | No | `localhost` | Hostname of the Redis server |
| `GH_TOKEN` | Yes (CI workflows only) | — | GitHub Personal Access Token for workflow scripts |
| `SENDGRID_API_KEY` | Yes (CI workflows only) | — | SendGrid API key for email notifications |
| `OUTPUT_REPO` | No (CI workflows only) | `ai-delivery-outputs` | GitHub repo name where AI workflow outputs are written |
| `OUTPUT_REPO_OWNER` | No (CI workflows only) | `GITHUB_REPOSITORY_OWNER` | Owner of the output repo |
| `NOTIFY_EMAIL` | No (CI workflows only) | `kylo.deng@capco.com` | Email address to receive workflow notifications |
| `SENDER_EMAIL` | No (CI workflows only) | `kylo.deng@capco.com` | Sender address for workflow notification emails |

> The backend reads its `.env` file from `backend/.env` (loaded via `python-dotenv`). The Docker Compose `env_file: .env` directive loads the root `.env` into the backend container.

---

## 6. Running Tests

[TODO: Are there any existing test files in the repository? No test files were found in the provided source tree. The `tool4_auto_testing.py` CI workflow is designed to *generate* tests via Claude, but no hand-written or generated test suite was found in the repository.]

To trigger AI-generated test generation for a PR:

```bash
# Open or push to a pull request that modifies src/, *.py, *.js, or *.ts files.
# The "Tool 4 — Auto Testing" GitHub Actions workflow will run automatically.
```

To run gap analysis manually via GitHub Actions:

1. Go to **Actions → Tool 4 — Auto Testing → Run workflow**
2. Select mode: `gap-analysis`

---

## 7. Deployment

### Local / Development

```bash
docker compose up --build
```

### Production

[TODO: Is there an IaC definition (Terraform, Bicep, etc.) for production infrastructure? None was found in the provided files.]

[TODO: What is the target cloud provider and deployment platform (e.g. Azure Container Apps, AWS ECS, Kubernetes)?]

The application is fully containerised. To deploy to any container platform:

1. **Build and push images**

```bash
docker build -t <registry>/underwriting-backend:latest ./backend
docker build -t <registry>/underwriting-frontend:latest ./frontend
docker push <registry>/underwriting-backend:latest
docker push <registry>/underwriting-frontend:latest
```

2. **Provision dependencies**
   - Redis (see known issue below — currently in-container, should be an external managed service)
   - PostgreSQL (for Chainlit session storage)
   - SQLite database files: `customer_profile.db`, `feature_importance.db`, `model_predictions.db`, `application_profile.db` must be available and mounted read-only into the backend container at `/data/`

3. **Set all required environment variables** (see table above) on the target platform.

4. **Apply the `docker-compose.yml`** as a reference for port mappings, volume mounts, health checks, and service dependencies.

### GitHub Actions AI Delivery Workflows

The five CI/CD AI tools require the following repository secrets to be set:

```
ANTHROPIC_API_KEY
GH_TOKEN
SENDGRID_API_KEY
```

| Workflow | Trigger |
|---|---|
| Tool 1 — Code Review | PR open/sync, Monday 08:00 UTC cron, manual dispatch |
| Tool 2 — Tech Documentation | Push to `main`, Sunday 06:00 UTC cron, manual dispatch |
| Tool 3 — Business Documentation | Version tag push (`v*`), manual dispatch |
| Tool 4 — Auto Testing | PR open/sync on source files, Wednesday 07:00 UTC cron, manual dispatch |
| Tool 5 — UAT Facilitation | `release/*` branch creation, manual dispatch |

---

## 8. Known Issues / TODOs

| Location | Issue / TODO |
|---|---|
| `backend/agent/graph.py` | **TODO:** Migrate Redis to an external managed service (e.g. Azure Cache for Redis, dedicated Redis container) so that memory persists across serverless backend instances. Currently Redis runs as a sidecar container and state is lost on restart. |
| `backend/modules/LLMS.py` | **TODO:** Add more LLM providers. `azure` and `openai` entries exist in the model mapper but are set to `None` and will raise `ValueError` if selected. |
| `backend/modules/LLMS.py` | The Gemini model string `gemini-3-flash-preview` may not match the current Google API model identifier — verify against the Google AI SDK. |
| `backend/agent/agent_with_skills.py` | `agent_with_skills.py` and `agent/graph.py` both define agents; it is unclear which is used by `main.py` at runtime. `main.py` imports from `agent.graph`. |
| `docker-compose.yml` | PostgreSQL credentials (`chainlit`/`chainlit`) are hardcoded — these should be injected via environment variables or secrets in production. |
| `backend/tmp/customer_similarity_dict.json` | Similarity data is stored as a static JSON file in `tmp/` — [TODO: Is this pre-computed offline or should it be regenerated? What is the source dataset?] |
| `.github/scripts/shared.py` | `send_email`, `email_html`, and `write_audit_entry` are imported by all tool scripts but their implementations are not present in the provided files — the file appears truncated. |
| `backend/agent/graph.py` | Imports `from langchain.agents import create_agent` — verify this is the correct import path for the installed LangChain version. |
| General | No test suite found in the repository. CI test generation is AI-assisted (Tool 4) but no baseline tests exist. |
| General | No IaC (Terraform/Bicep/CloudFormation) found — production infrastructure provisioning is undocumented. |