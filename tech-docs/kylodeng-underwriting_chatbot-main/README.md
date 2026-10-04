# Underwriting Chatbot

## 1. Project Overview

An AI-powered underwriting assistant that helps insurance underwriters assess customer risk by gathering customer profiles, running multi-specialist underwriting assessments, and surfacing a structured risk classification report. The backend exposes a streaming chat API that orchestrates LLM-based tool calls (customer lookup, lookalike matching, and parallel specialist risk assessments), while the frontend provides a conversational interface. A suite of five GitHub Actions workflows provide automated code review, documentation generation, test generation, and UAT facilitation for the repository itself.

---

## 2. Tech Stack

| Component | Technology | Version/Notes |
|---|---|---|
| Backend API | FastAPI | Python, with SSE streaming (`sse_starlette`) |
| Agent Framework | LangGraph + LangChain | `StateGraph`, `create_agent` |
| Primary LLM (fast) | Anthropic Claude Haiku | `claude-haiku-4-5-20251001` |
| Primary LLM (standard) | Anthropic Claude Sonnet | `claude-sonnet-4-20250514` |
| Alternative LLM | Google Gemini | `gemini-3-flash-preview` |
| Risk Classification Model | CatBoostClassifier | v1.0, trained on merged customer dataset |
| Session Memory / Checkpointing | Redis | `redis-stack-server:7.2.0-v14`, via `AsyncRedisSaver` |
| Database | PostgreSQL | v16-alpine, used by frontend (Chainlit) |
| Frontend | Chainlit | [TODO: confirm Chainlit version] |
| Containerisation | Docker Compose | Multi-service: redis, postgres, backend, frontend |
| Customer Data | SQLite databases | Mounted read-only into backend container |
| CI/CD & AI Tooling | GitHub Actions + Anthropic Claude | 5 workflow tools (code review, tech docs, business docs, auto-testing, UAT) |
| Config | YAML (`config.yml`) | LLM settings, token budgets |
| Data Validation | Pydantic | `UnderwritingReport`, `AreaOfInterest`, etc. |

---

## 3. Architecture

The system is composed of four Docker services that communicate over a shared Docker network:

1. **Frontend (Chainlit)** receives user messages and forwards them to the **Backend** via HTTP POST to `/chat`. It uses a PostgreSQL database for Chainlit session persistence.
2. **Backend (FastAPI)** exposes a `/chat` endpoint that returns a **Server-Sent Events (SSE)** stream. On each request it builds a LangGraph agent (`build_agent`) configured with the chosen model and mode.
3. **The LangGraph agent** (`agent_with_skills.py` / `graph.py`) drives a reasoning loop:
   - Calls `get_customer_profile` to fetch structured customer data from SQLite.
   - Calls `customer_lookalike` to find similar historical customers.
   - Calls `run_underwriting_assessment` which fans out **parallel async LLM calls** to specialist agents (finance, health, life, etc. — one per assessment category defined in `assessment_criterias.json`), then aggregates results into a structured `UnderwritingReport` via a second LLM call with `with_structured_output`.
4. **Redis** provides conversation checkpointing via `AsyncRedisSaver`, enabling multi-turn sessions. **PostgreSQL** is used exclusively by the frontend for Chainlit's own persistence.
5. **GitHub Actions workflows** run independently of the application, using the Anthropic API directly (via `shared.py`) to perform repository-level tasks: automated code review on PRs, documentation generation on merge, business documentation on releases, test generation, and UAT pack creation.

```
User
 │
 ▼
Frontend (Chainlit :8080)
 │  HTTP POST /chat
 ▼
Backend (FastAPI :8000)  ──────────────────────────────────────────┐
 │  LangGraph Agent loop                                           │
 │                                                                 │
 ├─► get_customer_profile ──► SQLite DBs (read-only volume mounts) │
 ├─► customer_lookalike   ──► customer_similarity_dict.json        │
 └─► run_underwriting_assessment                                   │
       │  asyncio parallel specialist LLM calls (Claude/Gemini)    │
       └─► aggregate ──► UnderwritingReport (structured output)    │
                                                                   │
Backend ◄──── Redis :6379 (session checkpoints) ───────────────────┘
Frontend ◄─── PostgreSQL :5432 (Chainlit session store)

GitHub Actions (CI)
 └─► Claude API (code review / docs / tests / UAT)
     └─► ai-delivery-outputs repo (reports written back)
```

---

## 4. Local Development Setup

### Prerequisites
- Docker and Docker Compose installed
- An Anthropic API key
- (Optional) A Google API key for Gemini models

**Steps:**

1. **Clone the repository**

```bash
git clone https://github.com/kylodeng/underwriting_chatbot-main.git
cd underwriting_chatbot-main
```

2. **Create the root `.env` file** (used by Docker Compose and the backend)

```bash
cat > .env << 'EOF'
ANTHROPIC_API_KEY=your_anthropic_api_key_here
GOOGLE_API_KEY=your_google_api_key_here
EOF
```

3. **Ensure the SQLite database files exist** under `./database/`

```
database/
  customer_profile.db
  feature_importance.db
  model_predictions.db
  application_profile.db
```

[TODO: Where do these database files come from? Are they checked in, generated by a script, or downloaded separately?]

4. **Build and start all services**

```bash
docker compose up --build
```

5. **Verify the backend is healthy**

```bash
curl http://localhost:8000/health
# Expected: {"status": "ok"}
```

6. **Open the frontend** in your browser

```
http://localhost:8080
```

7. **(Optional) Run the backend locally without Docker** — create a `backend/.env` file and install dependencies:

```bash
cd backend
pip install -r requirements.txt   # [TODO: confirm requirements.txt filename/location]
uvicorn main:app --reload --port 8000
```

---

## 5. Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | — | API key for Anthropic Claude models |
| `GOOGLE_API_KEY` | Yes (if using Gemini) | — | API key for Google Gemini models |
| `REDIS_HOST` | No | `localhost` | Hostname for Redis instance (set to `redis` in Docker Compose) |
| `BACKEND_URL` | No (frontend) | `http://backend:8000` | URL the frontend uses to reach the backend |
| `DATABASE_URL` | No (frontend) | `postgresql+asyncpg://chainlit:chainlit@postgres:5432/chainlit` | PostgreSQL connection string for Chainlit |

**GitHub Actions secrets** (required for CI workflows):

| Variable | Required | Default | Description |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | — | Claude API key for all five AI delivery tools |
| `GH_TOKEN` | Yes | — | GitHub token with repo read/write access (for PR comments and output repo writes) |
| `SENDGRID_API_KEY` | Yes | — | SendGrid key for email notifications |
| `OUTPUT_REPO` | No | `ai-delivery-outputs` | Repository name where generated docs/reports are written |
| `OUTPUT_REPO_OWNER` | No | `${{ github.repository_owner }}` | Owner of the output repository |
| `NOTIFY_EMAIL` | No | `kylo.deng@capco.com` | Email address for delivery notifications |
| `SENDER_EMAIL` | No | `noreply@ai-delivery.capco.com` | Sender address for delivery notifications |

---

## 6. Running Tests

[TODO: Are there existing test files in the repository? No test files were found in the provided source. The CI pipeline includes Tool 4 (auto-testing) which generates tests via Claude and writes them to the output repo, but no runnable test suite is committed to this repository.]

To trigger AI-generated test generation via GitHub Actions:

```
# Via GitHub UI: Actions → "Tool 4 — Auto Testing" → Run workflow
# Select mode: "generate" (create new tests) or "gap-analysis" (analyse coverage)
```

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

### Stopping and removing volumes (resets PostgreSQL data)

```bash
docker compose down -v
```

### Production deployment

[TODO: Is there a cloud deployment target (e.g. Azure, AWS, GCP)? No IaC files (.tf, .bicep) were found in the provided source. Docker Compose is the only deployment mechanism evidenced.]

[TODO: Is there a container registry push step? No CI workflow for building/pushing Docker images was found.]

### GitHub Actions CI workflows

The five AI delivery workflows run automatically but can also be triggered manually:

| Workflow | Trigger | Manual dispatch available |
|---|---|---|
| Tool 1 — Code Review | PR open/sync; Monday 08:00 UTC | Yes |
| Tool 2 — Tech Documentation | Push to `main`; Sunday 06:00 UTC | Yes |
| Tool 3 — Business Documentation | Version tag push (`v*`) | Yes |
| Tool 4 — Auto Testing | PR on `src/**`, `*.py`, `*.js`, `*.ts`; Wednesday 07:00 UTC | Yes |
| Tool 5 — UAT Facilitation | `release/*` branch creation | Yes |

Required repository secrets for all workflows: `ANTHROPIC_API_KEY`, `GH_TOKEN`, `SENDGRID_API_KEY`.

---

## 8. Known Issues / TODOs

Extracted directly from code comments:

| Location | Issue / TODO |
|---|---|
| `backend/agent/graph.py` | **TODO:** Migrate Redis to an external service (e.g. Azure Cache for Redis, dedicated Redis container) so that memory persists across serverless backend instances. |
| `backend/modules/LLMS.py` | **TODO:** Add more LLM providers (`azure` and `openai` entries exist in the mapper but are set to `None` — not yet configured). |
| `backend/main.py` | The `lifespan` context manager for FastAPI is commented out — application lifecycle management is incomplete. |
| `backend/main.py` | `_charts_sent` is an in-memory set: chart deduplication state is lost on restart and not shared across multiple backend instances. |
| CI scripts (`shared.py`) | `send_email`, `email_html`, and `write_audit_entry` functions are imported by all tool scripts but their implementations are not shown in the provided source — these may be incomplete or truncated. |
| `tool1_code_review.py` | Script is truncated in the repository — the `review_pr` function comment block is cut off. |
| `tool2_tech_docs.py` | `build_index` function is truncated — the f-string referencing `owner` and `repo` is incomplete. |
| `tool4_auto_testing.py` | `build_test_report` function is truncated. |
| `tool5_uat.py` | `build_test_pack_csv` function is truncated. |
| `backend/agent/agent_with_skills.py` | `agent` function is truncated — the `elif action == "done"` branch is incomplete. |
| Assessment framework | `assessment_criterias.json` has both `"deep"` and `"fast"` modes; only `"deep"` categories are fully shown — `"fast"` mode criteria are truncated. |
| Deployment | No IaC (Terraform/Bicep) found — no documented path to cloud deployment. |
| Monitoring | No metrics, alerting, or observability configuration found in the codebase. |
| Authentication | No authentication or authorisation layer is evidenced on the `/chat` endpoint (`allow_origins=["*"]` in CORS config). |