# Underwriting Chatbot

## 1. Project Overview

An AI-powered underwriting assistant that helps insurance underwriters assess customer risk profiles through a conversational chat interface. The system uses a multi-agent LLM pipeline to run parallel specialist assessments across domains (finance, health, life, etc.) and aggregates results into a structured underwriting report with risk classification. It also provides customer lookalike matching and integrates five GitHub Actions–based CI/CD tools for automated code review, documentation generation, test generation, and UAT facilitation.

---

## 2. Tech Stack

| Component | Technology | Version/Notes |
|---|---|---|
| Backend framework | FastAPI | Python, with SSE streaming |
| Agent orchestration | LangGraph | `StateGraph`-based agent loop |
| LLM provider (primary) | Anthropic Claude | `claude-sonnet-4-20250514` (deep), `claude-haiku-4-5-20251001` (fast) |
| LLM provider (secondary) | Google Gemini | `gemini-3-flash-preview` |
| LLM abstraction | LangChain | `langchain-anthropic`, `langchain-google-genai` |
| Session memory / checkpointing | Redis | `redis/redis-stack-server:7.2.0-v14` via `langgraph.checkpoint.redis` |
| Frontend | [TODO: what framework is the frontend built with?] | Served on port 8080 |
| Database | SQLite (read-only) | `customer_profile.db`, `feature_importance.db`, `model_predictions.db`, `application_profile.db` |
| Auth / persistence (frontend) | PostgreSQL | `postgres:16-alpine` |
| Risk classification model | CatBoostClassifier | Version 1.0, trained on merged customer dataset |
| CI/CD AI tools | Anthropic Claude (`claude-sonnet-4-6`) | Via GitHub Actions + `anthropic` Python SDK |
| Email notifications | SendGrid | Via `SENDGRID_API_KEY` |
| Containerisation | Docker Compose | Multi-service |

---

## 3. Architecture

The system is composed of four Docker services that interact as follows:

1. **Frontend** (port 8080) — serves the chat UI. Communicates with the backend over HTTP, using Server-Sent Events (SSE) for streaming responses. Stores chat session data in PostgreSQL.

2. **Backend** (port 8000) — a FastAPI application. On each `/chat` request it builds a LangGraph agent (`build_agent`) configured with three tools: `get_customer_profile`, `customer_lookalike`, and `run_underwriting_assessment`. The agent LLM decides which tools to call and in what order. Streaming events (tool start/end, model tokens) are forwarded to the frontend as SSE events.

3. **Assessment pipeline** — when `run_underwriting_assessment` is called, the backend fans out async calls to a specialist LLM for each assessment category (finance, health, life, etc.) with a concurrency semaphore of 4. Results are aggregated by a second LLM call that produces a structured `UnderwritingReport` Pydantic object, which is then rendered and returned.

4. **Redis** (port 6379) — used by LangGraph's `AsyncRedisSaver` checkpointer to persist conversation state across turns within a session.

5. **PostgreSQL** (port 5432) — used by the frontend (Chainlit, based on the DB credentials) for user/session persistence.

6. **GitHub Actions workflows** (`.github/`) — five independent AI-powered tools (code review, tech docs, business docs, auto-testing, UAT) that call Claude via the Anthropic API and write outputs to a separate `ai-delivery-outputs` GitHub repository.

```
User → Frontend (8080) → Backend FastAPI (8000) → LangGraph Agent
                                                        ├── get_customer_profile  → SQLite DBs
                                                        ├── customer_lookalike    → customer_similarity_dict.json
                                                        └── run_underwriting_assessment
                                                                ├── Specialist LLMs (×N, async, semaphore=4)
                                                                └── Aggregator LLM → UnderwritingReport
Backend ←→ Redis (session checkpoints)
Frontend ←→ PostgreSQL (session/user data)
```

---

## 4. Local Development Setup

1. **Clone the repository**

```bash
git clone https://github.com/kylodeng/underwriting_chatbot-main.git
cd underwriting_chatbot-main
```

2. **Create the environment file**

```bash
cp .env.example .env   # [TODO: confirm whether .env.example exists in the repo]
```

Edit `.env` and populate the required variables (see [Environment Variables](#5-environment-variables) below).

3. **Ensure the SQLite database files are present**

```bash
ls database/
# Expected: customer_profile.db  feature_importance.db  model_predictions.db  application_profile.db
```

[TODO: how are these database files generated or obtained? Are they checked in or built from a script?]

4. **Build and start all services**

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

7. **(Optional) Run the backend locally without Docker**

```bash
cd backend
pip install -r requirements.txt   # [TODO: confirm requirements.txt exists]
uvicorn main:app --reload --port 8000
```

---

## 5. Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | — | Anthropic API key for Claude LLM calls (backend and GitHub Actions) |
| `GOOGLE_API_KEY` | Yes (if using Gemini) | — | Google API key for Gemini model access |
| `REDIS_HOST` | No | `localhost` | Hostname of the Redis instance for LangGraph checkpointing |
| `GH_TOKEN` | Yes (GitHub Actions) | — | GitHub personal access token for Actions workflows to read repos and write to output repo |
| `SENDGRID_API_KEY` | Yes (GitHub Actions) | — | SendGrid API key for email notifications from CI/CD tools |
| `OUTPUT_REPO` | No (GitHub Actions) | `ai-delivery-outputs` | Name of the GitHub repo where AI tool outputs are written |
| `OUTPUT_REPO_OWNER` | No (GitHub Actions) | `GITHUB_REPOSITORY_OWNER` | Owner of the output GitHub repo |
| `NOTIFY_EMAIL` | No (GitHub Actions) | `kylo.deng@capco.com` | Recipient email address for CI/CD tool notifications |
| `SENDER_EMAIL` | No (GitHub Actions) | `kylo.deng@capco.com` | Sender email address used by SendGrid |
| `BACKEND_URL` | No | `http://backend:8000` | URL the frontend uses to reach the backend (set in docker-compose) |
| `DATABASE_URL` | No | `postgresql+asyncpg://chainlit:chainlit@postgres:5432/chainlit` | PostgreSQL connection string for the frontend |

> **Note:** PostgreSQL credentials (`POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`) are hardcoded in `docker-compose.yml` as `chainlit`/`chainlit`/`chainlit`. Change these for any non-local deployment.

---

## 6. Running Tests

[TODO: No test files or test runner configuration were found in the provided source files. How are tests run for this project?]

The repository includes a GitHub Actions workflow (Tool 4 — Auto Testing, `.github/workflows/tool4_auto_testing.yml`) that uses Claude to **generate** test files automatically. It triggers on PRs that touch `src/**`, `*.py`, `*.js`, or `*.ts` files, or on a weekly Wednesday schedule. It can also be run manually with modes `generate` or `gap-analysis`.

To trigger it manually via GitHub Actions:

1. Go to **Actions → Tool 4 — Auto Testing → Run workflow**
2. Select mode: `generate` or `gap-analysis`

---

## 7. Deployment

### Local / Development

```bash
docker compose up --build
```

### Production

[TODO: Is there any cloud infrastructure (Terraform, Bicep, Kubernetes manifests) for production deployment? None was found in the provided files.]

Key considerations before deploying to production:

- Replace hardcoded PostgreSQL credentials in `docker-compose.yml`
- Migrate Redis to an external managed service (e.g. Azure Cache for Redis) — see known issues below
- Set all secrets via a secrets manager rather than a plain `.env` file
- The backend `CORSMiddleware` currently allows all origins (`allow_origins=["*"]`) — restrict this for production

### GitHub Actions CI/CD Tools

The five AI delivery tools are configured as GitHub Actions workflows. Required repository secrets to set in **Settings → Secrets and variables → Actions**:

```
ANTHROPIC_API_KEY
GH_TOKEN
SENDGRID_API_KEY
```

Workflows and their triggers:

| Workflow | Trigger |
|---|---|
| Tool 1 — Code Review | PR open/sync, Monday 08:00 UTC, manual |
| Tool 2 — Tech Docs | Push to `main`, Sunday 06:00 UTC, manual |
| Tool 3 — Business Docs | Version tag (`v*`), manual |
| Tool 4 — Auto Testing | PR (src files changed), Wednesday 07:00 UTC, manual |
| Tool 5 — UAT | `release/*` branch creation, manual |

---

## 8. Known Issues / TODOs

Extracted from code comments:

- **Redis persistence across serverless instances** (`backend/agent/graph.py`):
  > `# TODO: migrate Redis to an external service (e.g. Azure Cache for Redis, dedicated Redis container) so that memory persists across serverless backend instances.`

- **Additional LLM providers not yet configured** (`backend/modules/LLMS.py`):
  > `# TODO: add more providers here` — `azure` and `openai` entries in the model mapper are set to `None` and will raise a `ValueError` if requested.

- **`lifespan` handler commented out** (`backend/main.py`):
  > The `@asynccontextmanager async def lifespan` block is commented out; application startup/shutdown lifecycle is not currently managed.

- **Agent implementation split** — there are two agent implementations present (`backend/agent/agent_with_skills.py` using a manual LangGraph `StateGraph` loop, and `backend/agent/graph.py` using `langchain.agents.create_agent`). It is unclear which is active in production. [TODO: which agent implementation is used at runtime?]

- **`send_email` and `write_audit_entry` referenced but not shown** — `shared.py` imports are referenced in the GitHub Actions scripts but the implementations were truncated in the provided files. [TODO: confirm `send_email` and `write_audit_entry` are fully implemented in `shared.py`.]

- **Frontend framework unknown** — the `frontend/` service is built via Docker but no frontend source files were provided. [TODO: what framework/language is the frontend written in?]

- **Database files origin unknown** — four SQLite `.db` files are mounted read-only into the backend container. [TODO: how are these files generated? Is there a data pipeline or seed script?]

- **`postgres/init.sql` not provided** — referenced in `docker-compose.yml` as the PostgreSQL initialisation script. [TODO: what schema does `init.sql` create?]