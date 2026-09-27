# Underwriting Chatbot

## 1. Project Overview

An AI-powered life insurance underwriting assistant that helps underwriters assess customer risk by gathering customer profiles, running multi-specialist risk assessments, and presenting structured underwriting reports via a chat interface. The backend exposes a streaming FastAPI service backed by LangGraph agents and multiple LLM providers; the frontend is a separate service that communicates with the backend over HTTP. A suite of GitHub Actions workflows provides automated code review, documentation generation, test generation, and UAT facilitation powered by the Anthropic Claude API.

---

## 2. Tech Stack

| Component | Technology | Version/Notes |
|---|---|---|
| Backend framework | FastAPI | With `sse-starlette` for streaming |
| Agent orchestration | LangGraph | `StateGraph` with custom `AgentState` |
| LLM – primary (fast) | Anthropic Claude Haiku | `claude-haiku-4-5-20251001` |
| LLM – primary (deep) | Anthropic Claude Sonnet | `claude-sonnet-4-20250514` |
| LLM – alternative | Google Gemini | `gemini-3-flash-preview` |
| LangChain integrations | `langchain-anthropic`, `langchain-google-genai` | Via `langchain_core` |
| Memory / checkpointing | Redis (LangGraph AsyncRedisSaver) | `redis-stack-server:7.2.0-v14` |
| Database | PostgreSQL | `postgres:16-alpine`, used by frontend (Chainlit) |
| Frontend | Chainlit | Served on port 8080 |
| Containerisation | Docker / Docker Compose | Multi-service compose file |
| CI/CD AI tools | Anthropic Claude Sonnet | `claude-sonnet-4-6` via GitHub Actions |
| CI/CD scripting | Python | 3.12 |
| Email notifications | SendGrid | Via API key |
| Customer similarity | Pre-computed JSON lookup | `backend/tmp/customer_similarity_dict.json` |
| Risk model card | CatBoostClassifier | v1.0, trained on merged customer datasets |
| Config | YAML | `backend/config.yml` |
| Structured output | Pydantic v2 | `BaseModel` for `UnderwritingReport` |

---

## 3. Architecture

```
┌─────────────┐        HTTP/SSE         ┌──────────────────────────────┐
│  Frontend   │◄───────────────────────►│  Backend (FastAPI :8000)     │
│ (Chainlit   │                         │                              │
│  :8080)     │                         │  POST /chat → EventSource    │
└─────────────┘                         │                              │
      │                                 │  LangGraph Agent             │
      │ PostgreSQL                      │  ├── get_customer_profile    │
      ▼                                 │  ├── customer_lookalike      │
┌─────────────┐                         │  └── run_underwriting_       │
│  PostgreSQL │                         │      assessment              │
│  (:5432)    │                         │       ├── N specialist LLMs  │
└─────────────┘                         │       │   (parallel, sem=4)  │
                                        │       └── aggregator LLM     │
                                        │           → UnderwritingReport│
                                        └──────────┬───────────────────┘
                                                   │
                                    ┌──────────────▼──────────────┐
                                    │  Redis (:6379)              │
                                    │  LangGraph checkpoint store │
                                    └─────────────────────────────┘
                                                   │
                                    ┌──────────────▼──────────────┐
                                    │  SQLite databases (read-only)│
                                    │  - customer_profile.db       │
                                    │  - feature_importance.db     │
                                    │  - model_predictions.db      │
                                    │  - application_profile.db    │
                                    └─────────────────────────────┘
```

**Request flow:**
1. The user sends a message via the Chainlit frontend.
2. The frontend POSTs to `POST /chat` on the FastAPI backend and opens a Server-Sent Events stream.
3. The backend instantiates a LangGraph agent (model and mode are per-request) with Redis-backed checkpointing for session memory.
4. The agent decides which tools to call (one at a time): `get_customer_profile`, `customer_lookalike`, or `run_underwriting_assessment`.
5. `run_underwriting_assessment` fans out to up to 4 concurrent specialist LLM calls (semaphore-limited), then aggregates results into a structured `UnderwritingReport` via a second LLM call with Pydantic structured output.
6. LLM tokens are streamed back to the client as SSE events; tool start/end events are also streamed so the UI can show progress.
7. GitHub Actions workflows run independently and write outputs (code reviews, docs, test files) to a separate `ai-delivery-outputs` repository.

---

## 4. Local Development Setup

**Prerequisites:** Docker Desktop, Python 3.12, a `.env` file at the repo root (see Environment Variables section).

```bash
# 1. Clone the repository
git clone https://github.com/kylodeng/underwriting_chatbot-main.git
cd underwriting_chatbot-main
```

```bash
# 2. Create the root .env file with required secrets (see Environment Variables section)
cp .env.example .env   # if an example file exists, otherwise create manually
```

```bash
# 3. Start all services (Redis, PostgreSQL, backend, frontend)
docker compose up --build
```

```bash
# 4. Verify the backend is healthy
curl http://localhost:8000/health
# Expected: {"status": "ok"}
```

```bash
# 5. Open the frontend
open http://localhost:8080
```

**Running the backend locally without Docker (for development):**

```bash
# 6. Install backend dependencies
cd backend
pip install -r requirements.txt   # [TODO: confirm requirements.txt filename and location]
```

```bash
# 7. Ensure Redis is running (via Docker or local install)
docker run -p 6379:6379 redis/redis-stack-server:7.2.0-v14
```

```bash
# 8. Start the FastAPI backend
uvicorn main:app --reload --port 8000
```

---

## 5. Environment Variables

The backend reads a `.env` file located at `backend/.env` (loaded via `python-dotenv`). The root `.env` is used by Docker Compose for all services.

| Variable | Required | Default | Description |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | — | Anthropic API key for Claude models |
| `GOOGLE_API_KEY` | Yes (if using Gemini) | — | Google API key for Gemini models |
| `REDIS_HOST` | No | `localhost` | Redis hostname (set to `redis` inside Docker Compose) |
| `GH_TOKEN` | Yes (CI only) | — | GitHub personal access token for Actions scripts |
| `SENDGRID_API_KEY` | Yes (CI only) | — | SendGrid API key for email notifications |
| `OUTPUT_REPO` | No (CI only) | `ai-delivery-outputs` | GitHub repo name where CI tool outputs are written |
| `OUTPUT_REPO_OWNER` | No (CI only) | `GITHUB_REPOSITORY_OWNER` | Owner of the output repo |
| `NOTIFY_EMAIL` | No (CI only) | `kylo.deng@capco.com` | Recipient address for CI email notifications |
| `SENDER_EMAIL` | No (CI only) | `kylo.deng@capco.com` | Sender address for CI email notifications |

> **PostgreSQL** credentials are hardcoded in `docker-compose.yml` (`chainlit`/`chainlit`). [TODO: should these be moved to environment variables?]

---

## 6. Running Tests

[TODO: No test files or test runner configuration were found in the provided source files. What test framework and test directory are used?]

The CI pipeline includes an automated test-generation workflow (Tool 4) that uses Claude to generate pytest/jest tests and writes them to the `ai-delivery-outputs` repository. To trigger it manually:

1. Go to **Actions → Tool 4 — Auto Testing** in GitHub.
2. Click **Run workflow**, choose `generate` or `gap-analysis` mode.

---

## 7. Deployment

### Local / development

```bash
docker compose up --build
```

### GitHub Actions CI workflows

Five automated workflows run against this repository. Required GitHub Actions secrets must be set in **Settings → Secrets and variables → Actions**:

- `ANTHROPIC_API_KEY`
- `GH_TOKEN` (needs write access to the `ai-delivery-outputs` repo)
- `SENDGRID_API_KEY`

| Workflow | Trigger | Purpose |
|---|---|---|
| Tool 1 — Code Review | PR open/sync, Monday 08:00 UTC, manual | Claude reviews PR diff, posts comments |
| Tool 2 — Tech Docs | Push to `main`, Sunday 06:00 UTC, manual | Generates README, architecture doc, runbook |
| Tool 3 — Business Docs | Version tag push (`v*`), manual | Generates solution overview and gap questionnaire |
| Tool 4 — Auto Testing | PR open/sync on source files, Wednesday 07:00 UTC, manual | Generates test files or coverage gap analysis |
| Tool 5 — UAT | `release/*` branch creation, manual | Generates UAT test pack or analyses completed results CSV |

**Manual dispatch example (Tool 3):**

```
GitHub UI → Actions → Tool 3 — Business Documentation → Run workflow
  project_name: "Underwriting Chatbot"
  release_version: "1.0.0"
```

**Tagging a release (triggers Tool 3 automatically):**

```bash
git tag v1.0.0
git push origin v1.0.0
```

> [TODO: Is there a Kubernetes, cloud (Azure/AWS/GCP), or other production deployment target? No IaC files (Terraform, Bicep, etc.) were found in the provided files.]

---

## 8. Known Issues / TODOs

Extracted from code comments:

| Location | Issue / TODO |
|---|---|
| `backend/agent/graph.py` | **TODO:** Migrate Redis to an external service (e.g. Azure Cache for Redis, dedicated Redis container) so that memory persists across serverless backend instances. |
| `backend/modules/LLMS.py` | **TODO:** Add more LLM providers. `azure` and `openai` entries in the model mapper are `None` (not yet implemented). |
| `backend/main.py` | The `lifespan` async context manager is commented out; its intended purpose is unclear. |
| `backend/agent/agent_with_skills.py` | The `agent_with_skills.py` and `graph.py` appear to define two separate agent implementations. [TODO: Which is the canonical agent used in production?] |
| `docker-compose.yml` | PostgreSQL credentials (`chainlit`/`chainlit`) are hardcoded. Should be moved to environment variables for production. |
| `backend/modules/assessment.py` | Specialist LLM semaphore is hard-coded to 4 concurrent calls. |
| CI scripts (`shared.py`) | `send_email`, `email_html`, and `write_audit_entry` functions are imported in tool scripts but their implementations are truncated in the provided files. [TODO: Confirm these are fully implemented in `shared.py`.] |
| `tool2_tech_docs.py` | `build_index` function is truncated (variable `r` referenced but undefined in the visible snippet). |