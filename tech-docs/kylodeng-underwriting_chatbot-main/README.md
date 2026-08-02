# Underwriting Chatbot

## 1. Project Overview

An AI-powered underwriting assistant that helps insurance underwriters assess customer risk profiles through a conversational chat interface. The backend orchestrates multiple specialist LLM agents to evaluate finance, health, and life insurance risk categories in parallel, aggregating their outputs into a structured `UnderwritingReport`. A suite of five GitHub Actions workflows provides automated code review, technical documentation generation, business documentation, test generation, and UAT facilitation — all powered by Claude AI.

---

## 2. Tech Stack

| Component | Technology | Version/Notes |
|---|---|---|
| Backend API | FastAPI | Python, SSE streaming via `sse_starlette` |
| Agent Orchestration | LangGraph | `StateGraph` with custom tool-calling loop |
| Primary LLM (fast) | Anthropic Claude Haiku | `claude-haiku-4-5-20251001` |
| Primary LLM (full) | Anthropic Claude Sonnet | `claude-sonnet-4-20250514` |
| Alternative LLM | Google Gemini | `gemini-3-flash-preview` |
| Risk Classification Model | CatBoostClassifier | v1.0, trained on merged customer datasets |
| Frontend | [TODO: what framework/language is the frontend built with?] | Served on port 8080 |
| Session Memory / Checkpointing | Redis (LangGraph checkpoint) | `redis-stack-server:7.2.0-v14` |
| Relational Database | PostgreSQL | `postgres:16-alpine`, used by Chainlit |
| Customer Data | SQLite databases | `customer_profile.db`, `feature_importance.db`, `model_predictions.db`, `application_profile.db` |
| Containerisation | Docker / Docker Compose | Multi-service compose stack |
| CI/CD & AI Tooling | GitHub Actions | 5 automated workflow tools |
| AI Workflow Scripts | Python + Anthropic SDK | `claude-sonnet-4-6` for GH Actions tools |
| Email Notifications | SendGrid | Via `SENDGRID_API_KEY` |
| Environment Config | python-dotenv | `.env` file at repo root and `backend/.env` |

---

## 3. Architecture

The system consists of four Docker Compose services that interact as follows:

1. **Frontend** (port 8080) accepts user messages and sends them to the **Backend** API over HTTP. It receives streaming responses via Server-Sent Events (SSE).
2. **Backend** (port 8000, FastAPI) receives chat requests and builds an agent on each request using LangGraph. The agent decides which tools to call:
   - `get_customer_profile` — looks up customer data from SQLite databases.
   - `customer_lookalike` — finds similar customers from a precomputed similarity dictionary (`customer_similarity_dict.json`).
   - `run_underwriting_assessment` — fans out to multiple specialist LLM calls in parallel (capped at 4 concurrent via `asyncio.Semaphore`), one per assessment category (finance, health, life, etc.), then aggregates results through a second structured LLM call into a `UnderwritingReport` Pydantic model.
3. **Redis** (port 6379) stores LangGraph conversation checkpoints, enabling session memory across turns within a running instance. The `REDIS_HOST` environment variable connects the backend to this service.
4. **PostgreSQL** (port 5432) is used by the frontend (Chainlit-style schema) for persistent storage. Initialised via `postgres/init.sql`.

Five GitHub Actions workflows run `.github/scripts/tool*.py` scripts against the repository using Claude AI to produce code reviews, technical docs, business docs, generated tests, and UAT packs, writing outputs to a separate `ai-delivery-outputs` repository.

---

## 4. Local Development Setup

1. **Clone the repository**

```bash
git clone https://github.com/kylodeng/underwriting_chatbot-main.git
cd underwriting_chatbot-main
```

2. **Create the root `.env` file** (see [Environment Variables](#5-environment-variables) section below)

```bash
cp .env.example .env   # if template exists, otherwise create manually
```

3. **Ensure SQLite database files are present** in the `./database/` directory:

```
database/customer_profile.db
database/feature_importance.db
database/model_predictions.db
database/application_profile.db
```

[TODO: how are these database files generated or seeded? Is there a script to create them?]

4. **Build and start all services with Docker Compose**

```bash
docker compose up --build
```

5. **Verify the backend is healthy**

```bash
curl http://localhost:8000/health
# Expected: {"status": "ok"}
```

6. **Access the frontend** at [http://localhost:8080](http://localhost:8080)

7. **(Optional) Run the backend locally without Docker** for development:

```bash
cd backend
pip install -r requirements.txt   # [TODO: confirm requirements.txt filename/location]
uvicorn main:app --reload --port 8000
```

---

## 5. Environment Variables

The backend reads from a `.env` file (loaded via `python-dotenv`). The GitHub Actions workflows read from repository secrets.

### Backend / Application

| Variable | Required | Default | Description |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | — | API key for Anthropic Claude models |
| `GOOGLE_API_KEY` | Yes (if using Gemini) | — | API key for Google Gemini models |
| `REDIS_HOST` | No | `localhost` | Hostname of the Redis service for LangGraph checkpointing |

### GitHub Actions Workflows

| Variable | Required | Default | Description |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | — | Claude API key for all five AI tools |
| `GH_TOKEN` | Yes | — | GitHub personal access token for reading repos and writing to output repo |
| `SENDGRID_API_KEY` | Yes | — | SendGrid API key for email notifications |
| `OUTPUT_REPO` | No | `ai-delivery-outputs` | Name of the GitHub repo where generated docs/reports are written |
| `OUTPUT_REPO_OWNER` | No | `GITHUB_REPOSITORY_OWNER` | Owner of the output repo |
| `NOTIFY_EMAIL` | No | `kylo.deng@capco.com` | Recipient email for workflow notifications |
| `SENDER_EMAIL` | No | `kylo.deng@capco.com` | Sender address for email notifications |

---

## 6. Running Tests

[TODO: Are there existing test files in this repository? No test files were found in the provided source. Is there a `tests/` directory or a `pytest.ini` / `pyproject.toml` defining test configuration?]

The repository includes **Tool 4 — Auto Testing**, a GitHub Actions workflow that uses Claude AI to *generate* test files for the codebase. To trigger it manually:

1. Go to **Actions → Tool 4 — Auto Testing** in the GitHub UI.
2. Select **Run workflow** and choose mode `generate` or `gap-analysis`.

The workflow also runs automatically on:
- Pull requests that modify `src/**`, `*.py`, `*.js`, or `*.ts` files.
- Every Wednesday at 07:00 UTC.

Generated test files are written to the `ai-delivery-outputs` repository.

---

## 7. Deployment

### Local / Development

Use Docker Compose as described in the setup steps:

```bash
docker compose up --build
```

To stop and remove containers:

```bash
docker compose down
```

To stop and also remove the PostgreSQL volume:

```bash
docker compose down -v
```

### GitHub Actions Workflows

The five AI delivery tools are deployed as GitHub Actions and require the following secrets to be configured in the repository (**Settings → Secrets and variables → Actions**):

- `ANTHROPIC_API_KEY`
- `GH_TOKEN`
- `SENDGRID_API_KEY`

| Workflow | Trigger |
|---|---|
| Tool 1 — Code Review | PR open/sync, every Monday 08:00 UTC, manual dispatch |
| Tool 2 — Tech Documentation | Push to `main`, every Sunday 06:00 UTC, manual dispatch |
| Tool 3 — Business Documentation | Push of `v*` tag, manual dispatch |
| Tool 4 — Auto Testing | PR open/sync (on source file changes), every Wednesday 07:00 UTC, manual dispatch |
| Tool 5 — UAT Facilitation | Creation of a `release/*` branch, manual dispatch |

To trigger Tool 3 for a release:

```bash
git tag v1.0.0
git push origin v1.0.0
```

[TODO: Is there any cloud infrastructure (IaC) such as Terraform or Bicep for deploying the backend/frontend to a cloud provider? No `.tf` or `.bicep` files were found in the provided sources.]

---

## 8. Known Issues / TODOs

Extracted from code comments:

| Location | Issue / TODO |
|---|---|
| `backend/agent/graph.py` | **TODO:** Migrate Redis to an external service (e.g. Azure Cache for Redis, dedicated Redis container) so that memory persists across serverless backend instances. Currently Redis state is lost if the backend container restarts. |
| `backend/modules/LLMS.py` | **TODO:** Add more LLM providers. `azure` and `openai` entries exist in the model mapper but are set to `None` and will raise `ValueError` if selected. |
| `backend/main.py` | A `lifespan` async context manager is commented out — the `_agent` global initialisation path is incomplete. |
| `backend/agent/agent_with_skills.py` | Two agent implementations exist (`agent_with_skills.py` using a custom LangGraph `StateGraph` and `graph.py` using `create_agent`). It is unclear which is the active production path. [TODO: Which agent implementation is used by `main.py` at runtime?] |
| `docker-compose.yml` | The `postgres/init.sql` file is mounted but not provided in the listed sources. [TODO: What schema does `init.sql` define?] |
| General | No DR (disaster recovery) strategy, no cloud monitoring or alerting configuration is evident from the source files. |
| GitHub Actions scripts | `send_email`, `email_html`, and `write_audit_entry` are imported in all tool scripts from `shared.py` but the implementations of those functions are not present in the provided `shared.py` source (file appears truncated). |