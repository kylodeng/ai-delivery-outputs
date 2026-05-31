# Underwriting Chatbot

## 1. Project Overview

An AI-powered life insurance underwriting assistant that helps underwriters assess customers by gathering profile information and running multi-specialist risk assessments. The system uses a LangGraph-based agent backed by Anthropic Claude (and optionally Google Gemini) to orchestrate tool calls across finance, health, and life risk domains, then aggregates the results into a structured `UnderwritingReport`. A suite of five GitHub Actions CI/CD tools (code review, tech docs, business docs, auto-testing, and UAT facilitation) are also included, each powered by Claude via the Anthropic API.

---

## 2. Tech Stack

| Component | Technology | Version/Notes |
|---|---|---|
| Backend API | FastAPI | Python, SSE streaming via `sse-starlette` |
| Agent Framework | LangGraph + LangChain | `StateGraph`, `create_agent` |
| Primary LLM | Anthropic Claude (Haiku) | `claude-haiku-4-5-20251001` (fast/default) |
| Secondary LLM | Anthropic Claude (Sonnet) | `claude-sonnet-4-20250514` (deep mode) |
| Optional LLM | Google Gemini | `gemini-3-flash-preview` via `langchain-google-genai` |
| Risk Model | CatBoostClassifier | v1.0, model card in `backend/model_card.json` |
| Conversation Memory | Redis | `redis/redis-stack-server:7.2.0-v14`, via `langgraph.checkpoint.redis` |
| Application Database | PostgreSQL | `postgres:16-alpine`, used by frontend (Chainlit) |
| Customer Data | SQLite | `.db` files mounted read-only into backend container |
| Frontend | Chainlit | Port 8080, connects to backend at port 8000 |
| CI/CD AI Tools | GitHub Actions + Anthropic Claude | `claude-sonnet-4-6`, Python 3.12 |
| Email Notifications | SendGrid | Via `SENDGRID_API_KEY` |
| Containerisation | Docker Compose | Multi-service: redis, postgres, backend, frontend |

---

## 3. Architecture

```
┌─────────────────────────────────────────────────────────┐
│                      Frontend (Chainlit)                 │
│  Port 8080 — chat UI, connects to PostgreSQL for        │
│  session persistence, proxies chat to backend           │
└─────────────────────┬───────────────────────────────────┘
                      │ HTTP / SSE
┌─────────────────────▼───────────────────────────────────┐
│               Backend (FastAPI) — Port 8000              │
│                                                          │
│  POST /chat  ──► LangGraph Agent (agent_with_skills.py) │
│                        │                                 │
│              ┌─────────┼──────────────┐                 │
│              ▼         ▼              ▼                  │
│     get_customer   customer_      run_underwriting_      │
│       _profile    lookalike        assessment            │
│        (SQLite)   (similarity       │                    │
│                    dict JSON)       │                    │
│                          ┌──────────▼──────────┐        │
│                          │  Specialist LLMs     │        │
│                          │  (finance, health,   │        │
│                          │   life, …) parallel  │        │
│                          │  ► Aggregator LLM    │        │
│                          │  ► UnderwritingReport│        │
│                          └─────────────────────┘        │
│                                                          │
│  Conversation checkpoints ──► Redis (port 6379)         │
│  Customer data ──► SQLite DBs (read-only volume mounts) │
└─────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────┐
│            GitHub Actions CI/CD (5 AI Tools)            │
│  Tool 1: Code Review  (PR trigger / weekly cron)        │
│  Tool 2: Tech Docs    (push to main / weekly cron)      │
│  Tool 3: Business Docs (release tag / manual)           │
│  Tool 4: Auto Testing  (PR trigger / weekly cron)       │
│  Tool 5: UAT           (release branch / manual)        │
│  All tools ──► Anthropic Claude API ──► ai-delivery-    │
│                                         outputs repo    │
└─────────────────────────────────────────────────────────┘
```

The **backend** receives a chat message via `POST /chat`, builds a LangGraph agent for the session, and streams Server-Sent Events back to the frontend. The agent decides which tools to call (customer profile lookup, lookalike search, or full risk assessment). The risk assessment fans out to multiple specialist LLMs in parallel (capped at 4 concurrent by a semaphore), then an aggregator LLM synthesises a structured `UnderwritingReport`. Conversation state is checkpointed to **Redis** keyed by `session_id`. Customer data is served from **SQLite** databases mounted read-only into the backend container. The **frontend** (Chainlit) stores its own session/user data in **PostgreSQL**.

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

2. **Create the root `.env` file**

```bash
cp .env.example .env   # if an example exists, otherwise create manually
# Then fill in the required values — see Environment Variables section
```

3. **Build and start all services**

```bash
docker compose up --build
```

4. **Verify the backend is healthy**

```bash
curl http://localhost:8000/health
# Expected: {"status": "ok"}
```

5. **Access the frontend**

Open your browser at [http://localhost:8080](http://localhost:8080)

6. **(Optional) Run the backend locally without Docker**

```bash
cd backend
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt  # [TODO: confirm requirements.txt exists or identify the correct dependency file]
uvicorn main:app --reload --port 8000
```

> **Note:** When running outside Docker you must have Redis accessible at `localhost:6379` and set `REDIS_HOST=localhost`.

---

## 5. Environment Variables

The backend reads from a `.env` file at `backend/.env` (loaded by `python-dotenv`). The root `.env` is passed as `env_file` to the backend Docker container.

| Variable | Required | Default | Description |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | — | Anthropic API key for Claude LLM calls |
| `GOOGLE_API_KEY` | No | — | Google API key for Gemini model; only required if using the `gemini` model provider |
| `REDIS_HOST` | No | `localhost` | Hostname of the Redis instance (set to `redis` in Docker Compose) |
| `GH_TOKEN` | Yes (CI only) | — | GitHub personal access token for GitHub Actions AI tools |
| `SENDGRID_API_KEY` | Yes (CI only) | — | SendGrid API key used by GitHub Actions tools for email notifications |
| `OUTPUT_REPO` | No (CI only) | `ai-delivery-outputs` | Target GitHub repo name where CI tool outputs are written |
| `OUTPUT_REPO_OWNER` | No (CI only) | `GITHUB_REPOSITORY_OWNER` | Owner of the output repo |
| `NOTIFY_EMAIL` | No (CI only) | `kylo.deng@capco.com` | Email address for CI tool notifications |
| `SENDER_EMAIL` | No (CI only) | `kylo.deng@capco.com` | Sender address for CI tool emails |

[TODO: Are there any additional environment variables required by the frontend Chainlit service (e.g. authentication keys)?]

[TODO: Is there a `DATABASE_URL` or SQLite path variable required by the backend for the customer profile databases?]

---

## 6. Running Tests

[TODO: No test files or test runner configuration were found in the provided source files. How are tests run for this project?]

The repository includes a GitHub Actions workflow (Tool 4) that auto-generates tests using Claude and writes them to the `ai-delivery-outputs` repository. To trigger it manually:

```bash
# Via GitHub CLI
gh workflow run "Tool 4 — Auto Testing" --field test_mode=generate

# Or via gap analysis mode
gh workflow run "Tool 4 — Auto Testing" --field test_mode=gap-analysis
```

---

## 7. Deployment

### Local / Development (Docker Compose)

```bash
docker compose up --build -d
```

```bash
# View logs
docker compose logs -f backend

# Stop all services
docker compose down

# Stop and remove volumes
docker compose down -v
```

### GitHub Actions CI/CD Tools

All five AI delivery tools are triggered automatically or can be run manually:

```bash
# Tool 1 — Code Review (runs automatically on PR open/sync; also manual)
gh workflow run "Tool 1 — Code Review" --field review_mode=repo

# Tool 2 — Tech Documentation (runs automatically on push to main; also manual)
gh workflow run "Tool 2 — Tech Documentation"

# Tool 3 — Business Documentation (runs automatically on version tag push)
git tag v1.0.0 && git push origin v1.0.0
# Or manually:
gh workflow run "Tool 3 — Business Documentation" \
  --field project_name="Underwriting Chatbot" \
  --field release_version="1.0.0"

# Tool 4 — Auto Testing (runs automatically on PR; also manual)
gh workflow run "Tool 4 — Auto Testing" --field test_mode=generate

# Tool 5 — UAT (runs automatically on release branch creation; also manual)
git checkout -b release/1.0.0 && git push origin release/1.0.0
# Or manually:
gh workflow run "Tool 5 — UAT Facilitation" \
  --field uat_mode=generate \
  --field release_version="1.0.0"
```

### Required GitHub Secrets

The following secrets must be set in the repository's GitHub Actions secrets:

```
ANTHROPIC_API_KEY
GH_TOKEN
SENDGRID_API_KEY
```

### Production Deployment

[TODO: Is there a Kubernetes manifest, Helm chart, cloud IaC (Terraform/Bicep), or cloud-specific deployment process for production? None was found in the provided files.]

[TODO: The `graph.py` comment flags that Redis must be migrated to an external service (e.g. Azure Cache for Redis) for production use so that memory persists across serverless backend instances.]

---

## 8. Known Issues / TODOs

Extracted from code comments:

| Location | Issue / TODO |
|---|---|
| `backend/agent/graph.py` | **Redis persistence**: Redis must be migrated to an external service (e.g. Azure Cache for Redis, dedicated Redis container) so that memory persists across serverless backend instances |
| `backend/modules/LLMS.py` | `azure` and `openai` model providers are defined in the mapper but not implemented (`None`) |
| `backend/modules/LLMS.py` | Comment: `# TODO: add more providers here` |
| `backend/main.py` | A `lifespan` async context manager for app startup/shutdown is commented out; `_agent` global initialisation is incomplete |
| `backend/main.py` | `_charts_sent` set is module-level and will grow unboundedly in long-running processes (no eviction) |
| `backend/agent/agent_with_skills.py` | The `"azure"` and `"openai"` model options in `LLMS` are not yet implemented |
| `backend/prompts/assessment_criterias.json` | Assessment criteria content is truncated in the provided files — completeness of all specialist prompt categories (finance, health, life, etc.) is not fully verifiable |
| `backend/tmp/customer_similarity_dict.json` | Similarity data is stored as a static JSON file in `tmp/` — [TODO: should this be served from a database or computed dynamically?] |
| GitHub Actions scripts | `shared.py` is truncated — `send_email`, `email_html`, and `write_audit_entry` functions are referenced by all five tools but their implementations are not visible in the provided files |
| GitHub Actions scripts | `tool1_code_review.py`, `tool2_tech_docs.py`, `tool3_business_docs.py`, `tool4_auto_testing.py`, `tool5_uat.py` are all truncated — completeness of the main entry-point functions cannot be confirmed |