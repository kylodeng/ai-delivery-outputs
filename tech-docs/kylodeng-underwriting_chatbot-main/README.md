# Underwriting Chatbot

## 1. Project Overview

An AI-powered underwriting assistant that helps insurance underwriters assess customer risk profiles through a conversational chat interface. The system uses a multi-agent LLM pipeline to run parallel specialist assessments across domains (finance, health, life, etc.) and aggregates them into a structured underwriting report with a risk classification. A suite of five GitHub Actions workflows provides automated code review, documentation generation, test generation, and UAT facilitation powered by Claude AI.

---

## 2. Tech Stack

| Component | Technology | Version/Notes |
|---|---|---|
| Backend API | FastAPI | Python, SSE streaming via `sse_starlette` |
| Agent Framework | LangGraph + LangChain | `StateGraph`, tool calling, streaming events |
| Primary LLM (fast) | Anthropic Claude Haiku | `claude-haiku-4-5-20251001` |
| Primary LLM (deep) | Anthropic Claude Sonnet | `claude-sonnet-4-20250514` |
| Alternative LLM | Google Gemini | `gemini-3-flash-preview` |
| Agent Memory / Checkpointing | Redis via LangGraph `AsyncRedisSaver` | Redis Stack 7.2.0 |
| Frontend | Chainlit | Connects to backend via `BACKEND_URL` |
| Frontend DB | PostgreSQL | 16-alpine, used by Chainlit session/auth |
| Customer Data | SQLite databases | `customer_profile.db`, `feature_importance.db`, `model_predictions.db`, `application_profile.db` |
| Customer Similarity | Pre-computed JSON | `backend/tmp/customer_similarity_dict.json` |
| Risk Model Card | CatBoostClassifier (metadata only) | `model_card.json` v1.0, trained on merged customer datasets |
| Containerisation | Docker Compose | Multi-service: redis, postgres, backend, frontend |
| CI/CD & AI Tooling | GitHub Actions | 5 workflows using Claude `claude-sonnet-4-6` via Anthropic API |
| Email Notifications | SendGrid | Via `SENDGRID_API_KEY` |
| Config | YAML | `backend/config.yml` |
| Environment Management | python-dotenv | `.env` file at repo root and `backend/.env` |

---

## 3. Architecture

The system is composed of four Docker services that interact as follows:

1. **Frontend (Chainlit)** receives user messages and streams responses. It communicates with the **Backend** over HTTP (`BACKEND_URL`) and persists session/auth data to **PostgreSQL**.
2. **Backend (FastAPI)** exposes a `/chat` SSE endpoint. On each request it builds a LangGraph agent (`build_agent`) configured with a model, temperature, and assessment mode. The agent uses **Redis** (via `AsyncRedisSaver`) as a persistent checkpoint store so conversation history survives across turns within a session.
3. **LangGraph Agent** orchestrates three tools: `get_customer_profile` (reads SQLite DBs), `customer_lookalike` (reads pre-computed similarity JSON), and `run_underwriting_assessment`. The assessment tool fans out parallel async calls to a **specialist LLM** (tagged `"thinking"`) for each assessment category (finance, health, life, etc.), then aggregates results with a **structured output LLM** to produce a typed `UnderwritingReport` Pydantic model.
4. **Skill documentation** (`.md` files under `backend/skills/`) is loaded at agent build time and injected into the system prompt, providing the LLM with exact parameter names and tool usage guidance.
5. **GitHub Actions** workflows run independently of the application. They call the GitHub API and Anthropic API directly (not through the backend) to perform code review, generate documentation, generate tests, produce business docs, and facilitate UAT.

```
User
 │
 ▼
Frontend (Chainlit :8080)
 │  HTTP + SSE
 ▼
Backend (FastAPI :8000)
 │                        ┌─────────────────────────────────┐
 ├─ LangGraph Agent ──────► get_customer_profile (SQLite DBs)│
 │       │                ├─────────────────────────────────┤
 │       │                ► customer_lookalike (JSON)        │
 │       │                ├─────────────────────────────────┤
 │       │                ► run_underwriting_assessment      │
 │       │                │   ├─ Specialist LLMs (parallel) │
 │       │                │   └─ Aggregator LLM (structured)│
 │       │                └─────────────────────────────────┘
 │       │
 │       └─ Redis :6379 (checkpointing / memory)
 │
 └─ PostgreSQL :5432 (Chainlit session/auth)
```

---

## 4. Local Development Setup

**Prerequisites:** Docker and Docker Compose installed. Anthropic API key (and optionally Google API key) available.

1. **Clone the repository**

```bash
git clone https://github.com/kylodeng/underwriting_chatbot-main.git
cd underwriting_chatbot-main
```

2. **Create the root `.env` file** (see [Environment Variables](#5-environment-variables) section)

```bash
cp .env.example .env   # if provided, otherwise create manually
```

3. **Populate `.env`** with at minimum:

```env
ANTHROPIC_API_KEY=sk-ant-...
GOOGLE_API_KEY=...          # required if using gemini model
REDIS_HOST=redis            # set to "localhost" for local-only backend dev
```

4. **Ensure SQLite database files exist** under `./database/`:

```
database/
  customer_profile.db
  feature_importance.db
  model_predictions.db
  application_profile.db
```

[TODO: Where do these database files come from — are they checked in, generated by a script, or provided separately?]

5. **Build and start all services**

```bash
docker compose up --build
```

6. **Verify the backend is healthy**

```bash
curl http://localhost:8000/health
# Expected: {"status": "ok"}
```

7. **Open the frontend**

Navigate to [http://localhost:8080](http://localhost:8080) in your browser.

---

**Running the backend locally without Docker** (for faster iteration):

```bash
cd backend
pip install -r requirements.txt   # [TODO: confirm requirements.txt exists]
uvicorn main:app --reload --port 8000
```

Redis must be reachable. Start it separately if needed:

```bash
docker compose up redis
```

---

## 5. Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | — | Anthropic API key for Claude LLMs (backend and CI scripts) |
| `GOOGLE_API_KEY` | Conditional | — | Required when `model` is set to `"gemini"` |
| `REDIS_HOST` | No | `localhost` | Redis hostname; set to `redis` when running inside Docker Compose |
| `GH_TOKEN` | Yes (CI only) | — | GitHub personal access token used by GitHub Actions workflows |
| `SENDGRID_API_KEY` | Yes (CI only) | — | SendGrid API key for email notifications from CI workflows |
| `OUTPUT_REPO` | No (CI only) | `ai-delivery-outputs` | GitHub repo name where CI tool outputs are written |
| `OUTPUT_REPO_OWNER` | No (CI only) | `GITHUB_REPOSITORY_OWNER` | Owner of the output repo |
| `NOTIFY_EMAIL` | No (CI only) | `kylo.deng@capco.com` | Recipient email for CI notifications |
| `SENDER_EMAIL` | No (CI only) | `kylo.deng@capco.com` | Sender address for CI notification emails |

PostgreSQL credentials for the Chainlit frontend are hardcoded in `docker-compose.yml`:

| Variable | Value | Description |
|---|---|---|
| `POSTGRES_USER` | `chainlit` | PostgreSQL username |
| `POSTGRES_PASSWORD` | `chainlit` | PostgreSQL password |
| `POSTGRES_DB` | `chainlit` | PostgreSQL database name |
| `DATABASE_URL` | `postgresql+asyncpg://chainlit:chainlit@postgres:5432/chainlit` | Full DSN passed to the frontend container |

---

## 6. Running Tests

[TODO: Are there existing test files in this repository? No test files were found in the provided source. The CI pipeline (Tool 4) can auto-generate tests via GitHub Actions.]

To trigger AI-generated test generation via GitHub Actions:

1. Open a pull request against the repository, or
2. Navigate to **Actions → Tool 4 — Auto Testing → Run workflow** and select mode `generate` or `gap-analysis`.

Generated test files are written to the `ai-delivery-outputs` repository.

---

## 7. Deployment

### Local / Development — Docker Compose

```bash
docker compose up --build -d
```

To stop:

```bash
docker compose down
```

To rebuild a single service after code changes:

```bash
docker compose up --build backend -d
```

### GitHub Actions — AI Delivery Workflows

Five workflows run automatically or on demand. All require the following repository secrets configured in **Settings → Secrets and variables → Actions**:

| Secret | Purpose |
|---|---|
| `ANTHROPIC_API_KEY` | Claude API access |
| `GH_TOKEN` | Read source repo + write output repo |
| `SENDGRID_API_KEY` | Email notifications |

| Workflow | Trigger | Description |
|---|---|---|
| Tool 1 — Code Review | PR open/sync, Monday 08:00 UTC, manual | Claude reviews PR diff or full repo and posts comments |
| Tool 2 — Tech Docs | Push to `main`, Sunday 06:00 UTC, manual | Generates README, ARCHITECTURE, and RUNBOOK docs |
| Tool 3 — Business Docs | Version tag push (`v*`), manual | Generates solution overview and gap questionnaire |
| Tool 4 — Auto Testing | PR open/sync on source files, Wednesday 07:00 UTC, manual | Generates test files or gap analysis report |
| Tool 5 — UAT | `release/*` branch creation, manual | Generates UAT test pack CSV or analyses completed results |

To manually trigger any workflow:

1. Go to **Actions** tab in GitHub.
2. Select the desired workflow.
3. Click **Run workflow** and fill in the required inputs.

### Production Deployment

[TODO: Is there a Kubernetes manifest, Helm chart, Terraform config, or cloud-specific IaC for production deployment? None was found in the provided files.]

---

## 8. Known Issues / TODOs

Extracted directly from code comments:

| Location | Issue / TODO |
|---|---|
| `backend/agent/graph.py` | **TODO:** Migrate Redis to an external service (e.g. Azure Cache for Redis, dedicated Redis container) so that memory persists across serverless backend instances. |
| `backend/modules/LLMS.py` | **TODO:** Add more LLM providers. `azure` and `openai` entries exist in the model mapper but are `None` (unconfigured). |
| `backend/config.yml` | `specialist_max_tokens: 1500` — comment notes this caps runaway specialist output (others were hitting 2,772 tokens). |
| `docker-compose.yml` | PostgreSQL credentials (`chainlit`/`chainlit`) are hardcoded — not suitable for production without secrets management. |
| CI shared scripts | `send_email`, `email_html`, and `write_audit_entry` are imported in all tool scripts but their implementations are absent from the provided `shared.py` (file appears truncated). |
| `backend/agent/agent_with_skills.py` | `_SKILLS_DIR` points to `backend/skills/*.md` — skills directory contents not provided; missing files will cause a silent empty prompt injection. |
| General | No test files were found in the repository. The auto-test CI workflow can generate them but none exist at source. |
| General | No IaC files (Terraform, Bicep, CloudFormation) were found. Production infrastructure provisioning is undocumented. |