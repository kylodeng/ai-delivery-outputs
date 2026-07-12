# Underwriting Chatbot

## 1. Project Overview

An AI-powered underwriting assistant that helps insurance underwriters assess customer risk profiles through a conversational chat interface. The system uses a multi-agent LLM pipeline to run parallel specialist assessments across finance, health, and life insurance domains, then aggregates results into a structured underwriting report. A suite of five GitHub Actions workflows provides automated code review, documentation generation, test generation, and UAT facilitation powered by the Claude API.

---

## 2. Tech Stack

| Component | Technology | Version/Notes |
|---|---|---|
| Backend API | FastAPI | Python, server-sent events (SSE) |
| Frontend | [TODO: what framework/language is the frontend built in?] | Served on port 8080 |
| LLM Orchestration | LangGraph + LangChain | Agent graph with streaming |
| Primary LLM (fast) | Anthropic Claude Haiku | `claude-haiku-4-5-20251001` |
| Primary LLM (deep) | Anthropic Claude Sonnet | `claude-sonnet-4-20250514` |
| Secondary LLM | Google Gemini | `gemini-3-flash-preview` |
| Session Memory | Redis (with LangGraph checkpointer) | `redis/redis-stack-server:7.2.0-v14` |
| Frontend DB | PostgreSQL | `postgres:16-alpine` |
| Customer Data | SQLite databases | Mounted as read-only volumes |
| Risk Model | CatBoostClassifier | `model_card.json` v1.0, deployed 2024-06-01 |
| Containerisation | Docker Compose | Multi-service |
| CI/CD | GitHub Actions | 5 automated AI delivery workflows |
| CI LLM | Anthropic Claude Sonnet | `claude-sonnet-4-6` (workflows only) |
| Email (CI) | SendGrid | Workflow notifications |

---

## 3. Architecture

The system is composed of four Docker services that interact as follows:

1. **Frontend** (port 8080) accepts user messages in a chat UI and streams responses from the **Backend** over HTTP using server-sent events (SSE).
2. **Backend** (port 8000, FastAPI) receives each chat message, builds a LangGraph agent, and streams tool call and LLM events back to the frontend. The agent has access to three tools:
   - `get_customer_profile` — retrieves a customer record from SQLite databases mounted at `/data/`.
   - `customer_lookalike` — returns a list of similar customers from a precomputed similarity dictionary (`customer_similarity_dict.json`).
   - `run_underwriting_assessment` — fans out parallel specialist LLM calls (finance, health, life, and other assessment categories defined in `assessment_criterias.json`), then aggregates results via a structured-output LLM into an `UnderwritingReport` Pydantic model.
3. **Redis** (port 6379) provides LangGraph conversation checkpointing so session history is preserved within a running instance.
4. **PostgreSQL** (port 5432) is used by the frontend (Chainlit schema, based on `POSTGRES_USER=chainlit`).

The five GitHub Actions workflows (`.github/workflows/tool*.yml`) run independently of the Docker stack, calling the Claude API directly via Python scripts in `.github/scripts/` to perform code review, documentation generation, business documentation, test generation, and UAT facilitation on the repository itself.

---

## 4. Local Development Setup

1. **Clone the repository**

```bash
git clone https://github.com/kylodeng/underwriting_chatbot-main.git
cd underwriting_chatbot-main
```

2. **Create the root `.env` file** (see [Environment Variables](#5-environment-variables))

```bash
cp .env.example .env   # if provided, otherwise create manually
```

3. **Ensure SQLite database files are present** under `./database/`

```
database/customer_profile.db
database/feature_importance.db
database/model_predictions.db
database/application_profile.db
```

[TODO: How are these database files obtained or generated? Is there a seed/migration script?]

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
pip install -r requirements.txt   # [TODO: confirm requirements.txt filename]
uvicorn main:app --reload --port 8000
```

---

## 5. Environment Variables

The backend reads from a `.env` file at the repo root (loaded via `python-dotenv`). The GitHub Actions workflows read the same keys from GitHub repository secrets.

| Variable | Required | Default | Description |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | — | API key for Anthropic Claude (backend LLM calls) |
| `GOOGLE_API_KEY` | Yes (if using Gemini) | — | API key for Google Generative AI |
| `REDIS_HOST` | No | `localhost` | Redis hostname (set to `redis` inside Docker Compose) |
| `GH_TOKEN` | Yes (CI only) | — | GitHub personal access token for workflow scripts |
| `SENDGRID_API_KEY` | Yes (CI only) | — | SendGrid API key for workflow email notifications |
| `OUTPUT_REPO` | No (CI only) | `ai-delivery-outputs` | GitHub repo name where CI tool outputs are written |
| `OUTPUT_REPO_OWNER` | No (CI only) | `GITHUB_REPOSITORY_OWNER` | Owner of the output repo |
| `NOTIFY_EMAIL` | No (CI only) | `kylo.deng@capco.com` | Recipient address for workflow notification emails |
| `SENDER_EMAIL` | No (CI only) | `kylo.deng@capco.com` | Sender address for workflow notification emails |

[TODO: Are there any additional environment variables required by the frontend service (e.g. auth tokens, feature flags)?]

---

## 6. Running Tests

[TODO: Are there any existing tests in the repository (e.g. under a `tests/` directory)? No test files were found in the provided source.]

The repository includes a **GitHub Actions workflow (Tool 4)** that auto-generates tests using Claude:

- Triggered automatically on pull requests that modify `src/**`, `*.py`, `*.js`, or `*.ts` files.
- Can be triggered manually via **Actions → Tool 4 — Auto Testing → Run workflow**, with modes `generate` (create new test files) or `gap-analysis` (analyse coverage gaps).
- Generated test files are written to the configured output repository.

To run the test generation script locally:

```bash
pip install anthropic requests
export ANTHROPIC_API_KEY=<your-key>
export GH_TOKEN=<your-token>
export OUTPUT_REPO_OWNER=<your-github-username>
python .github/scripts/tool4_auto_testing.py
```

---

## 7. Deployment

### Docker Compose (local / single-host)

```bash
# Build and start all services in detached mode
docker compose up --build -d

# View logs
docker compose logs -f

# Stop all services
docker compose down
```

### GitHub Actions CI Workflows

Five automated workflows are available under `.github/workflows/`. They require the following secrets to be configured in the GitHub repository settings (`Settings → Secrets and variables → Actions`):

- `ANTHROPIC_API_KEY`
- `GH_TOKEN`
- `SENDGRID_API_KEY`

| Workflow | Trigger | Purpose |
|---|---|---|
| Tool 1 — Code Review | PR open/sync, Monday 08:00 UTC, manual | Claude reviews PR diffs and posts comments |
| Tool 2 — Tech Docs | Push to `main`, Sunday 06:00 UTC, manual | Generates README, architecture doc, and runbook |
| Tool 3 — Business Docs | Version tag push (`v*`), manual | Generates solution overview and gap questionnaire |
| Tool 4 — Auto Testing | PR open/sync on source files, Wednesday 07:00 UTC, manual | Generates test files or analyses coverage gaps |
| Tool 5 — UAT | Release branch creation (`release/*`), manual | Generates UAT test pack or analyses completed results |

To trigger a workflow manually:

```
GitHub UI → Actions → <Workflow Name> → Run workflow
```

[TODO: Is there a cloud deployment target (e.g. Azure, AWS, GCP) beyond Docker Compose? No IaC files (Terraform, Bicep) were found in the provided source.]

---

## 8. Known Issues / TODOs

Extracted directly from code comments:

| Location | Issue / TODO |
|---|---|
| `backend/agent/graph.py` | **TODO:** Migrate Redis to an external service (e.g. Azure Cache for Redis, dedicated Redis container) so that memory persists across serverless backend instances. Currently, conversation history is lost if the backend container restarts. |
| `backend/modules/LLMS.py` | **TODO:** Add more LLM providers. `azure` and `openai` entries in the model mapper are present but set to `None` (unconfigured). |
| `backend/agent/agent_with_skills.py` | The `agent_with_skills.py` file defines a second agent implementation separate from `graph.py`. [TODO: Which agent implementation is the one actually used by `main.py`? `graph.py` is imported by `main.py`, but the relationship between the two agent files is unclear.] |
| `backend/main.py` | A `lifespan` context manager is defined but commented out. [TODO: What was the intended lifespan initialisation logic?] |
| `backend/modules/assessment.py` | Specialist LLM token output is capped at `specialist_max_tokens: 1500` in `config.yml` specifically because earlier runs were hitting ~2772 tokens of output. |
| General | No disaster recovery (DR) configuration is present. Redis and PostgreSQL data would be lost if volumes are deleted. |
| General | `CORS allow_origins=["*"]` is set in `backend/main.py` — overly permissive for a production deployment. |