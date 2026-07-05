# Underwriting Chatbot

## 1. Project Overview

An AI-powered underwriting assistant that helps insurance underwriters assess customer risk by gathering customer profile information and running multi-specialist underwriting assessments. The system uses a LangGraph-based agent backend with a streaming chat API, coordinating multiple LLM specialists to evaluate finance, health, and life insurance risk factors in parallel. It also includes five GitHub Actions–based AI delivery workflows (code review, tech docs, business docs, auto testing, and UAT facilitation) powered by Claude.

---

## 2. Tech Stack

| Component | Technology | Version/Notes |
|---|---|---|
| Backend API | FastAPI | Python, SSE streaming via `sse-starlette` |
| Agent Framework | LangGraph | StateGraph-based agent with tool calls |
| LLM – Sonnet | Anthropic Claude `claude-sonnet-4-20250514` | Used as aggregator LLM |
| LLM – Haiku (fast) | Anthropic Claude `claude-haiku-4-5-20251001` | Default specialist/agent LLM |
| LLM – Gemini | Google Gemini `gemini-3-flash-preview` | Optional provider |
| Agent Memory | Redis via `langgraph-checkpoint-redis` | Redis Stack 7.2.0 |
| Frontend | [TODO: What framework/technology is the frontend built with?] | Served on port 8080 |
| Database | SQLite (`.db` files) | Customer profile, feature importance, model predictions, application profile |
| Chat UI persistence | PostgreSQL 16 | Chainlit session store |
| Containerisation | Docker Compose | Multi-service stack |
| CI/CD & AI Workflows | GitHub Actions | Python 3.12, five Claude-powered tools |
| AI Workflow LLM | Anthropic `claude-sonnet-4-6` | Used by `.github/scripts/` tools |
| Risk Classification Model | CatBoostClassifier | v1.0, trained on merged customer datasets |
| Config | YAML (`config.yml`) | LLM defaults, token budgets |
| Env management | `python-dotenv` | `.env` file loaded at runtime |

---

## 3. Architecture

The system is composed of four Docker services that interact as follows:

- **Frontend** (port 8080) sends chat messages to the **Backend** (port 8000) via HTTP POST to `/chat`, receiving a Server-Sent Events stream in return.
- **Backend** builds a LangGraph `StateGraph` agent per request. The agent decides whether to call one of three tools: `get_customer_profile` (fetches customer data from SQLite DBs), `customer_lookalike` (finds similar customers via a pre-computed similarity dictionary), or `run_underwriting_assessment` (runs parallel specialist LLM calls).
- `run_underwriting_assessment` fans out up to 4 concurrent async calls to a **specialist LLM** (tagged `"thinking"`) — one per assessment category (finance, health, life, etc.) — then an **aggregator LLM** combines results into a structured `UnderwritingReport` Pydantic object.
- **Redis** (port 6379, Redis Stack) is used by LangGraph's `AsyncRedisSaver` checkpointer to persist conversation thread state across turns within a session.
- **PostgreSQL** (port 5432) stores Chainlit UI session/user data, initialised via `postgres/init.sql`.
- Five **GitHub Actions workflows** run separately against the repository and write outputs to a separate `ai-delivery-outputs` GitHub repository.

```
User → Frontend (8080) → Backend FastAPI (8000) → LangGraph Agent
                                                        ├── get_customer_profile   → SQLite DBs
                                                        ├── customer_lookalike     → similarity JSON
                                                        └── run_underwriting_assessment
                                                                ├── Specialist LLM × N (parallel, sem=4)
                                                                └── Aggregator LLM → UnderwritingReport
                                    Redis (6379) ← LangGraph checkpointer
                                    PostgreSQL (5432) ← Chainlit session store
```

---

## 4. Local Development Setup

1. **Clone the repository**

    ```bash
    git clone https://github.com/kylodeng/underwriting_chatbot-main.git
    cd underwriting_chatbot-main
    ```

2. **Create the root `.env` file** (see [Environment Variables](#5-environment-variables) section)

    ```bash
    cp .env.example .env   # if provided, otherwise create manually
    ```

3. **Ensure the SQLite database files are present** under `./database/`:

    ```
    database/customer_profile.db
    database/feature_importance.db
    database/model_predictions.db
    database/application_profile.db
    ```

    [TODO: How are these database files generated or seeded? Is there a script or data pipeline?]

4. **Ensure the customer similarity file is present**:

    ```
    backend/tmp/customer_similarity_dict.json
    ```

    [TODO: How is `customer_similarity_dict.json` generated? Is there a script to rebuild it?]

5. **Build and start all services with Docker Compose**

    ```bash
    docker compose up --build
    ```

6. **Verify the backend is healthy**

    ```bash
    curl http://localhost:8000/health
    # Expected: {"status": "ok"}
    ```

7. **Access the frontend** at `http://localhost:8080`

8. **(Optional) Run the backend locally without Docker** — from the `backend/` directory:

    ```bash
    cd backend
    pip install -r requirements.txt   # [TODO: confirm requirements filename]
    uvicorn main:app --reload --port 8000
    ```

---

## 5. Environment Variables

The root `.env` file is loaded by the backend container via `env_file: .env` in `docker-compose.yml` and also by `python-dotenv` at runtime.

| Variable | Required | Default | Description |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | — | Anthropic API key for Claude models (Sonnet and Haiku) |
| `GOOGLE_API_KEY` | No | — | Google API key for Gemini model; required only if using the `gemini` model provider |
| `REDIS_HOST` | No | `localhost` | Redis hostname; set to `redis` automatically in Docker Compose |
| `ANTHROPIC_API_KEY` *(GH Actions)* | Yes | — | Same Anthropic key stored as a GitHub Actions secret (`secrets.ANTHROPIC_API_KEY`) |
| `GH_TOKEN` | Yes (GH Actions) | — | GitHub personal access token for the AI delivery workflow scripts |
| `SENDGRID_API_KEY` | Yes (GH Actions) | — | SendGrid API key for email notifications from AI delivery workflows |
| `OUTPUT_REPO` | No (GH Actions) | `ai-delivery-outputs` | Name of the GitHub repo where AI workflow outputs are written |
| `OUTPUT_REPO_OWNER` | No (GH Actions) | `GITHUB_REPOSITORY_OWNER` | Owner of the output repo |
| `NOTIFY_EMAIL` | No (GH Actions) | `kylo.deng@capco.com` | Recipient email for AI workflow notifications |
| `SENDER_EMAIL` | No (GH Actions) | `kylo.deng@capco.com` / `noreply@ai-delivery.capco.com` | Sender email address for notifications |

[TODO: Are there any additional environment variables required by the frontend service (beyond `BACKEND_URL` and `DATABASE_URL` which are set in docker-compose.yml)?]

---

## 6. Running Tests

[TODO: Are there any existing test files in this repository? No test files were found in the provided source tree.]

The GitHub Actions **Tool 4 — Auto Testing** workflow (`.github/workflows/tool4_auto_testing.yml`) can generate tests automatically using Claude. It can be triggered:

- On PRs that modify `src/**`, `*.py`, `*.js`, or `*.ts`
- On a weekly schedule (Wednesdays at 07:00 UTC)
- Manually via workflow dispatch with mode `generate` or `gap-analysis`

To trigger manually from the GitHub UI:

1. Go to **Actions → Tool 4 — Auto Testing**
2. Click **Run workflow**
3. Select mode: `generate` (create new tests) or `gap-analysis` (identify coverage gaps)

Generated test files are written to the `ai-delivery-outputs` repository.

---

## 7. Deployment

### Local / Docker Compose

```bash
docker compose up --build -d
```

To stop:

```bash
docker compose down
```

To view logs:

```bash
docker compose logs -f backend
```

### GitHub Actions AI Delivery Workflows

Five automated workflows are available in `.github/workflows/`. Required GitHub Actions secrets must be configured in the repository settings:

| Secret | Description |
|---|---|
| `ANTHROPIC_API_KEY` | Anthropic API key |
| `GH_TOKEN` | GitHub PAT with repo write access to `ai-delivery-outputs` |
| `SENDGRID_API_KEY` | SendGrid API key for email notifications |

**Tool 1 – Code Review**: Runs automatically on PR open/sync or every Monday at 08:00 UTC. Posts review comments on the PR and writes a report to `ai-delivery-outputs`.

**Tool 2 – Tech Documentation**: Runs automatically on push to `main` (excluding docs/markdown changes) or every Sunday at 06:00 UTC. Generates README, architecture doc, and runbook.

**Tool 3 – Business Documentation**: Runs on version tag push (e.g. `v1.0.0`) or manually via workflow dispatch with `project_name` and `release_version` inputs.

**Tool 4 – Auto Testing**: Runs on PR open/sync for source file changes or every Wednesday at 07:00 UTC. Can also be triggered manually.

**Tool 5 – UAT Facilitation**: Runs when a `release/*` branch is created, or manually via workflow dispatch. Supports two modes: `generate` (create test pack) and `analyse` (process completed results CSV).

[TODO: Is there a cloud infrastructure deployment target (e.g. Azure, AWS, GCP)? No IaC files (Terraform, Bicep, ARM) were found in the provided source tree.]

---

## 8. Known Issues / TODOs

Extracted from code comments:

| Location | Issue / TODO |
|---|---|
| `backend/agent/graph.py` | **TODO**: Migrate Redis to an external service (e.g. Azure Cache for Redis, dedicated Redis container) so that memory persists across serverless backend instances. |
| `backend/modules/LLMS.py` | **TODO**: Add more LLM providers. `azure` and `openai` entries are present in the model mapper but are set to `None` (not implemented). |
| `backend/main.py` | The `lifespan` async context manager for the FastAPI app is commented out — application lifecycle management is not currently wired up. |
| `backend/main.py` | `_charts_sent` is an in-process set; it will not persist or share state across multiple backend instances or restarts. |
| `backend/agent/agent_with_skills.py` | The `agent_with_skills.py` and `graph.py` appear to be two separate agent implementations. [TODO: Which is the active/production agent used by `main.py`?] |
| `.github/scripts/shared.py` | `send_email`, `email_html`, and `write_audit_entry` functions are referenced by tool scripts but their implementations are truncated in the provided files — [TODO: confirm these are fully implemented in the actual file]. |
| `tool5_uat.py` | Escalation path in generated runbooks is left as `[TODO: fill in team contacts]`. |
| General | No DR (disaster recovery) or monitoring/alerting configuration found in the repository. |
| General | No `requirements.txt` or `pyproject.toml` was included in the provided files — [TODO: confirm the Python dependency manifest filename and location for the backend]. |